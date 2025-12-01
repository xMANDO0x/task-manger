# gui.py
import sys
import platform
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem,
                             QLineEdit, QLabel, QFrame, QMessageBox, QTabWidget,
                             QProgressBar, QHeaderView, QSplitter, QGridLayout,
                             QToolBar, QStatusBar, QSystemTrayIcon, QMenu, QDialog,
                             QDialogButtonBox, QFormLayout, QSpinBox, QComboBox)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSize, QThread, pyqtSlot
from PyQt6.QtCore import QThreadPool, QRunnable
from PyQt6.QtGui import QFont, QIcon, QPalette, QColor, QAction, QLinearGradient, QBrush, QPixmap, QPainter, QPen, QImage
import psutil
import ctypes
from ctypes import wintypes
from backend import get_cpu_percent, get_ram_info, list_processes, kill_process
from scheduler import CPUScheduler, SchedulingAlgorithm
import platform

# small helper
IS_WINDOWS = platform.system() == "Windows"

class ProcessUpdateThread(QThread):
    """Thread for updating process data to prevent GUI freezing"""
    process_data_ready = pyqtSignal(list)
    system_data_ready = pyqtSignal(dict)
    
    def __init__(self):
        super().__init__()
        self.running = True
        # allow main thread to request an immediate update
        self.force_update = False
        
    def run(self):
        while self.running:
            try:
                # Update system info more frequently
                cpu_percent = get_cpu_percent()
                ram_info = get_ram_info()
                system_data = {
                    'cpu_percent': cpu_percent,
                    'ram_info': ram_info
                }
                self.system_data_ready.emit(system_data)
                
                # Update process list less frequently
                processes = list_processes(max_count=150, apps_only=getattr(self, 'apps_only', True))  # Reduced from 200
                self.process_data_ready.emit(processes)
                
                # Update processes every ~3 seconds, system info every ~1.5 seconds
                # Sleep in short increments so we can respond quickly to stop or force_update
                slept = 0
                while slept < 3000 and self.running:
                    if self.force_update:
                        # immediate short-circuit to refresh again
                        self.force_update = False
                        break
                    # update system info halfway through the cycle
                    if slept == 0 or slept == 1500:
                        cpu_percent = get_cpu_percent()
                        ram_info = get_ram_info()
                        system_data = {
                            'cpu_percent': cpu_percent,
                            'ram_info': ram_info
                        }
                        self.system_data_ready.emit(system_data)
                    self.msleep(200)
                    slept += 200
                        
            except Exception as e:
                print(f"Error in update thread: {e}")
                self.msleep(2000)
                
    def stop(self):
        self.running = False
        # Ask the thread to finish and wait a short time
        self.force_update = False
        self.quit()
        self.wait(2000)  # Wait up to 2 seconds for thread to finish

class ProcessKillDialog(QDialog):
    def __init__(self, pid, name, parent=None):
        super().__init__(parent)
        self.pid = pid
        self.name = name
        self.initUI()
        
    def initUI(self):
        self.setWindowTitle("Confirm Process Termination")
        self.setFixedSize(400, 200)
        
        layout = QVBoxLayout()
        
        # Warning icon and message
        warning_layout = QHBoxLayout()
        warning_icon = QLabel("⚠️")
        warning_icon.setStyleSheet("font-size: 24px;")
        warning_text = QLabel(
            f"<b>Are you sure you want to terminate this process?</b><br><br>"
            f"<b>PID:</b> {self.pid}<br>"
            f"<b>Name:</b> {self.name}<br><br>"
            f"<i>Terminating system processes may cause system instability.</i>"
        )
        warning_text.setWordWrap(True)
        
        warning_layout.addWidget(warning_icon)
        warning_layout.addWidget(warning_text, 1)
        layout.addLayout(warning_layout)
        
        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | 
                                    QDialogButtonBox.StandardButton.No)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        
        layout.addWidget(button_box)
        self.setLayout(layout)


class KillWorker(QRunnable):
    """Worker to perform process termination off the GUI thread.

    Emits results by calling a callback passed in constructor.
    """
    def __init__(self, pid, name, callback):
        super().__init__()
        self.pid = pid
        self.name = name
        self.callback = callback  # function(success: bool, msg: str, pid: int)

    def run(self):
        import subprocess, platform, os, ctypes
        try:
            # Try backend kill first (psutil preferred)
            from backend import kill_process
            ok = False
            try:
                ok = kill_process(self.pid)
            except Exception:
                ok = False

            if ok:
                self.callback(True, "killed", self.pid)
                return

            # Try taskkill on Windows
            if platform.system() == 'Windows':
                try:
                    proc = subprocess.run(["taskkill", "/PID", str(self.pid), "/F", "/T"], capture_output=True, text=True, timeout=5)
                    if proc.returncode == 0:
                        self.callback(True, "taskkill", self.pid)
                        return
                    stderr = (proc.stderr or proc.stdout or "").strip()
                    low = stderr.lower()
                    if "access is denied" in low or "could not be terminated" in low:
                        # Try to launch elevated taskkill without blocking (ShellExecuteW)
                        try:
                            params = f"/PID {self.pid} /F /T"
                            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", "taskkill", params, None, 1)
                            if int(ret) > 32:
                                self.callback(True, "elevation_launched", self.pid)
                                return
                        except Exception:
                            pass
                    # final failure
                    self.callback(False, stderr or "taskkill failed", self.pid)
                    return
                except Exception as e:
                    self.callback(False, str(e), self.pid)
                    return
            else:
                # Unix-like fallback
                import signal
                try:
                    os.kill(self.pid, signal.SIGTERM)
                    self.callback(True, "sigterm", self.pid)
                    return
                except Exception as e:
                    self.callback(False, str(e), self.pid)
                    return

        except Exception as e:
            try:
                self.callback(False, str(e), self.pid)
            except Exception:
                pass

class ModernTaskManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.process_data = []
        self.update_thread = None
        self.is_updating = False
        # cache icons by executable path to avoid repeated loads
        # Limit cache size to prevent memory issues (keep last 200 icons)
        self._icon_cache = {}
        self._icon_cache_max_size = 200
        # Initialize CPU scheduler with default algorithm
        self.cpu_scheduler = CPUScheduler(SchedulingAlgorithm.ROUND_ROBIN)
        self.initUI()
        self.start_updates()

    def _get_icon_pixmap(self, exe_path, size=64):
        """Return a QPixmap for the given executable path, using cache.
        
        Extracts high-quality colored icons from Windows executables at maximum resolution (256x256).
        Then scales down to requested size using smooth transformation for best quality.
        Uses multiple methods to ensure accurate color representation.

        Args:
            exe_path: Path to executable file
            size: Desired display size (default 64 for better quality)
            
        Returns:
            QPixmap with the icon, or None on failure
        """
        if not exe_path:
            return None

        # Cache per (exe_path, size) to avoid redundant extractions
        cache_key = f"{exe_path}|{size}"
        cached = self._icon_cache.get(cache_key)
        if cached is not None:
            return cached.copy() if cached else None

        pix = None
        # Always extract at highest available resolution (256x256 JUMBO) for best quality
        # Then scale down to requested size - this preserves maximum detail and sharpness
        # Windows supports up to 256x256 (JUMBO) icons, which we always request
        extraction_size = 256  # Always use highest resolution available (JUMBO)
        # Only attempt Windows shell extraction on Windows
        if IS_WINDOWS and exe_path:
            try:
                # Try SHGetImageList -> ImageList_GetIcon for high-res colored icons
                try:
                    shell32 = ctypes.windll.shell32
                    comctl32 = ctypes.windll.comctl32

                    # SHFILEINFO struct
                    class SHFILEINFO(ctypes.Structure):
                        _fields_ = [
                            ("hIcon", wintypes.HICON),
                            ("iIcon", ctypes.c_int),
                            ("dwAttributes", wintypes.DWORD),
                            ("szDisplayName", wintypes.WCHAR * 260),
                            ("szTypeName", wintypes.WCHAR * 80),
                        ]

                    SHGFI_SYSICONINDEX = 0x00004000
                    SHGFI_USEFILEATTRIBUTES = 0x00000010
                    SHGFI_ICON = 0x000000100
                    SHGFI_LARGEICON = 0x000000000
                    SHGFI_SMALLICON = 0x000000001

                    shfi = SHFILEINFO()
                    # Use FILE attribute fallback so we can query exe paths reliably
                    res = shell32.SHGetFileInfoW(ctypes.c_wchar_p(exe_path), 0, ctypes.byref(shfi), ctypes.sizeof(shfi), SHGFI_SYSICONINDEX)
                    index = int(shfi.iIcon) if res else -1

                    # Helper to parse GUID string to struct
                    class GUID(ctypes.Structure):
                        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

                    def _guid_from_str(guid_str):
                        import uuid
                        u = uuid.UUID(guid_str)
                        data = u.bytes_le
                        return GUID.from_buffer_copy(data)

                    IID_IImageList = _guid_from_str("46EB5926-582E-4017-9FDF-E8998DAA0950")

                    # Map desired size -> SHIL index
                    SHIL_SMALL = 0
                    SHIL_LARGE = 1
                    SHIL_EXTRALARGE = 2
                    SHIL_JUMBO = 4

                    # Always use JUMBO (256x256) for highest quality
                    # We'll scale down to display size after extraction
                    himl = ctypes.c_void_p()
                    want_shil = SHIL_JUMBO  # Always use 256x256 for maximum resolution
                    # SHGetImageList prototype: HRESULT SHGetImageList(int, REFIID, void**)
                    try:
                        SHGetImageList = shell32.SHGetImageList
                        SHGetImageList.argtypes = [ctypes.c_int, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
                        SHGetImageList.restype = ctypes.c_long
                        hr = SHGetImageList(want_shil, ctypes.byref(IID_IImageList), ctypes.byref(himl))
                    except Exception:
                        himl = ctypes.c_void_p()

                    if himl and himl.value and index >= 0:
                        ILD_TRANSPARENT = 0x00000001
                        try:
                            hicon = comctl32.ImageList_GetIcon(himl, index, ILD_TRANSPARENT)
                            if hicon:
                                try:
                                    # Prefer QtWinExtras if available
                                    try:
                                        import importlib
                                        qtwin_mod = importlib.import_module('PyQt6.QtWinExtras')
                                        # Use QtWinExtras for best color preservation at highest resolution
                                        pix = qtwin_mod.QtWin.fromHICON(int(hicon)).pixmap(extraction_size, extraction_size) if hasattr(qtwin_mod, 'QtWin') else None
                                    except Exception:
                                        pix = None

                                    if not pix or pix.isNull():
                                        # Use custom conversion for accurate color preservation at highest resolution
                                        pix = self._hicon_to_qpixmap(int(hicon), extraction_size)
                                    
                                    # Always scale down from highest resolution to display size for best quality
                                    if pix and not pix.isNull() and pix.size() != QSize(size, size):
                                        # Use smooth transformation for best quality when scaling down
                                        pix = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                                finally:
                                    try:
                                        ctypes.windll.user32.DestroyIcon(hicon)
                                    except Exception:
                                        pass
                        except Exception:
                            pix = None

                except Exception:
                    pix = None

                # If shell-based imagelist failed, try ExtractIconEx as a fallback
                if (not pix or pix.isNull() if hasattr(pix, 'isNull') else not pix):
                    try:
                        shell32 = ctypes.windll.shell32
                        hi_large = ctypes.c_void_p()
                        hi_small = ctypes.c_void_p()
                        res = shell32.ExtractIconExW(ctypes.c_wchar_p(exe_path), 0, ctypes.byref(hi_large), ctypes.byref(hi_small), 1)
                        hicon = None
                        if res and getattr(hi_large, 'value', None):
                            hicon = hi_large.value
                        if res and getattr(hi_small, 'value', None):
                            hicon = hi_small.value

                        if hicon:
                            try:
                                try:
                                    import importlib
                                    qtwin_mod = importlib.import_module('PyQt6.QtWinExtras')
                                    # Extract at highest resolution for best quality
                                    pix = qtwin_mod.QtWin.fromHICON(int(hicon)).pixmap(extraction_size, extraction_size) if hasattr(qtwin_mod, 'QtWin') else None
                                except Exception:
                                    pix = None

                                if not pix or (hasattr(pix, 'isNull') and pix.isNull()):
                                    # Use custom conversion for accurate color preservation at highest resolution
                                    pix = self._hicon_to_qpixmap(int(hicon), extraction_size)
                                
                                # Always scale down from highest resolution to display size
                                if pix and not pix.isNull() and pix.size() != QSize(size, size):
                                    # Use smooth transformation for best quality when scaling down
                                    pix = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                            finally:
                                try:
                                    ctypes.windll.user32.DestroyIcon(hicon)
                                except Exception:
                                    pass
                    except Exception:
                        pix = None

            except Exception:
                pix = None

        # Last-resort: try QIcon from exe (may still give colored icon but lower resolution)
        if (not pix or (hasattr(pix, 'isNull') and pix.isNull())):
            try:
                icon = QIcon(exe_path)
                # Request highest available size for best quality
                # QIcon may not always support 256x256, so try highest sizes first
                # Try sizes from largest to smallest to get best quality available
                for try_size in [extraction_size, 128, 96, 64, 48, 32]:
                    temp_pix = icon.pixmap(try_size, try_size)
                    if temp_pix and not temp_pix.isNull():
                        # Found a valid icon at this size
                        pix = temp_pix
                        # Scale to requested size if needed
                        if pix.size() != QSize(size, size):
                            # Use smooth transformation for best quality when scaling
                            pix = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        break
            except Exception:
                pix = None

        # Normalize falsy pix to None and cache the result
        if pix and not pix.isNull():
            # Limit cache size to prevent memory issues
            if len(self._icon_cache) >= self._icon_cache_max_size:
                # Remove oldest entries (simple FIFO - remove first key)
                keys_to_remove = list(self._icon_cache.keys())[:len(self._icon_cache) - self._icon_cache_max_size + 1]
                for key in keys_to_remove:
                    del self._icon_cache[key]
            # Cache the icon for future use
            self._icon_cache[cache_key] = pix.copy()
            return pix
        # If we couldn't extract a real icon, create a small placeholder (initials)
        # This ensures every process has some visual representation
        try:
            # derive initials from filename or exe_path
            name = exe_path.split("\\")[-1] if \
                (exe_path and "\\" in exe_path) else exe_path
            if not name:
                name = "?"
            # pick 1-2 letters
            base = name.split('.')[0]
            initials = (base[:2]).upper()

            pixmap = QPixmap(size, size)
            pixmap.fill(QColor(0, 0, 0, 0))

            painter = QPainter(pixmap)
            try:
                # background color from hash to vary per app
                h = (abs(hash(exe_path)) % 360) if exe_path else 200
                bg = QColor.fromHsl(h, 200, 120)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
                pen = QPen(QColor(0, 0, 0, 30))
                painter.setPen(pen)
                painter.setBrush(bg)
                painter.drawEllipse(1, 1, size - 2, size - 2)

                # draw initials with better text rendering
                font = QFont("Segoe UI", max(8, size // 2))
                font.setBold(True)
                painter.setFont(font)
                painter.setPen(QColor(255, 255, 255))
                rect = pixmap.rect()
                painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), initials)
            finally:
                painter.end()

            # Cache the placeholder as well
            if len(self._icon_cache) >= self._icon_cache_max_size:
                keys_to_remove = list(self._icon_cache.keys())[:len(self._icon_cache) - self._icon_cache_max_size + 1]
                for key in keys_to_remove:
                    del self._icon_cache[key]
            self._icon_cache[cache_key] = pixmap
            return pixmap
        except Exception as e:
            # Log error but don't crash - return None instead
            print(f"Error creating placeholder icon for {exe_path}: {e}")
            self._icon_cache[cache_key] = None
            return None

    def _hicon_to_qpixmap(self, hicon, size=24):
        """Convert a Win32 HICON handle to a QPixmap using GDI calls.

        Returns QPixmap or None on failure.
        """
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32

            # Create DCs and bitmap
            hdc_screen = user32.GetDC(0)
            memdc = gdi32.CreateCompatibleDC(hdc_screen)
            hbm = gdi32.CreateCompatibleBitmap(hdc_screen, size, size)
            old = gdi32.SelectObject(memdc, hbm)

            # Draw the icon into the memory DC
            DI_NORMAL = 0x0003
            user32.DrawIconEx(memdc, 0, 0, wintypes.HICON(hicon), size, size, 0, 0, DI_NORMAL)

            # Prepare BITMAPINFO for 32bpp top-down DIB
            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ('biSize', wintypes.DWORD),
                    ('biWidth', wintypes.LONG),
                    ('biHeight', wintypes.LONG),
                    ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD),
                    ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD),
                    ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG),
                    ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD),
                ]

            class BITMAPINFO(ctypes.Structure):
                _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wintypes.DWORD * 3)]

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = size
            bmi.bmiHeader.biHeight = -size  # top-down
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0  # BI_RGB

            buf_size = size * size * 4
            buf = (ctypes.c_ubyte * buf_size)()

            res = gdi32.GetDIBits(memdc, hbm, 0, size, ctypes.byref(buf), ctypes.byref(bmi), 0)

            # Cleanup GDI objects
            gdi32.SelectObject(memdc, old)
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(memdc)
            user32.ReleaseDC(0, hdc_screen)

            if not res:
                return None

            # Create QImage from raw BGRA bytes
            # GetDIBits with BI_RGB returns BGRA (Blue, Green, Red, Alpha) format
            # On Windows (little-endian), QImage.Format_ARGB32 interprets pixels as:
            # Byte 0: Blue, Byte 1: Green, Byte 2: Red, Byte 3: Alpha
            # This matches BGRA format perfectly, so we can use it directly
            # This preserves the true colors of the application icons
            byte_data = bytes(buf)
            qimg = QImage(byte_data, size, size, size * 4, QImage.Format.Format_ARGB32)
            # Verify the image is valid
            if qimg.isNull():
                return None
            # Convert to pixmap - this preserves the original icon colors
            return QPixmap.fromImage(qimg)
        except Exception:
            try:
                # best-effort cleanup if something failed earlier
                pass
            except Exception:
                pass
            return None
        
    def initUI(self):
        self.setWindowTitle("Professional Task Manager")
        self.setGeometry(200, 100, 1200, 800)
        self.setMinimumSize(1000, 600)
        
        # Set dark theme
        self.set_dark_theme()
        
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Create toolbar
        self.create_toolbar()
        
        # Create status bar
        self.create_status_bar()
        
        # Create main content
        self.create_main_content(main_layout)
        
        # Apply styles
        self.apply_styles()
        
    def set_dark_theme(self):
        dark_palette = QPalette()
        dark_palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
        dark_palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        dark_palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
        dark_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
        dark_palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
        dark_palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
        dark_palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        dark_palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
        dark_palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        dark_palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
        dark_palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
        dark_palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
        dark_palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
        QApplication.setPalette(dark_palette)
        
    def create_toolbar(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(toolbar)
        
        # Refresh action
        refresh_act = QAction("🔄 Refresh", self)
        refresh_act.setShortcut("F5")
        refresh_act.triggered.connect(self.manual_refresh)
        toolbar.addAction(refresh_act)
        
        toolbar.addSeparator()
        
        # End task action
        end_task_act = QAction("⏹️ End Task", self)
        end_task_act.setShortcut("Delete")
        end_task_act.triggered.connect(self.end_selected_task)
        toolbar.addAction(end_task_act)
        
        # Pause updates action
        self.pause_act = QAction("⏸️ Pause Updates", self)
        self.pause_act.setShortcut("Ctrl+P")
        self.pause_act.setCheckable(True)
        self.pause_act.triggered.connect(self.toggle_updates)
        toolbar.addAction(self.pause_act)
        
        toolbar.addSeparator()
        
        # Search box
        toolbar.addWidget(QLabel("Search:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter processes...")
        self.search_box.setFixedWidth(200)
        self.search_box.textChanged.connect(self.filter_processes)
        toolbar.addWidget(self.search_box)
        # Apps-only toggle
        self.apps_only_act = QAction("Applications only", self)
        self.apps_only_act.setCheckable(True)
        self.apps_only_act.setChecked(True)
        self.apps_only_act.triggered.connect(self.on_apps_only_toggled)
        toolbar.addAction(self.apps_only_act)
        
        toolbar.addSeparator()
        
        # CPU Scheduling Algorithm selector
        toolbar.addWidget(QLabel("CPU Scheduling:"))
        self.scheduling_combo = QComboBox()
        self.scheduling_combo.addItem("First Come First Served", SchedulingAlgorithm.FCFS)
        self.scheduling_combo.addItem("Shortest Job First", SchedulingAlgorithm.SJF)
        self.scheduling_combo.addItem("Priority", SchedulingAlgorithm.PRIORITY)
        self.scheduling_combo.addItem("Round Robin", SchedulingAlgorithm.ROUND_ROBIN)
        self.scheduling_combo.addItem("Multilevel Queue Scheduling", SchedulingAlgorithm.MULTILEVEL_QUEUE)
        self.scheduling_combo.setCurrentIndex(3)  # Default to Round Robin
        self.scheduling_combo.currentIndexChanged.connect(self.on_scheduling_changed)
        self.scheduling_combo.setFixedWidth(220)
        toolbar.addWidget(self.scheduling_combo)
        
    def create_status_bar(self):
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        
        # CPU usage
        self.cpu_label = QLabel("CPU: 0%")
        status_bar.addPermanentWidget(self.cpu_label)
        
        # RAM usage
        self.ram_label = QLabel("RAM: 0%")
        status_bar.addPermanentWidget(self.ram_label)
        
        # Process count
        self.process_count_label = QLabel("Processes: 0")
        status_bar.addPermanentWidget(self.process_count_label)
        
        # Update status
        self.update_status_label = QLabel("🟢 Updating")
        status_bar.addWidget(self.update_status_label)
        
    def create_main_content(self, parent_layout):
        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # System monitoring panel (top)
        system_panel = self.create_system_panel()
        splitter.addWidget(system_panel)
        
        # Processes panel (bottom)
        processes_panel = self.create_processes_panel()
        splitter.addWidget(processes_panel)
        
        # Set splitter proportions
        splitter.setSizes([200, 600])
        
        parent_layout.addWidget(splitter)
        
    def create_system_panel(self):
        panel = QFrame()
        panel.setObjectName("systemPanel")
        panel.setStyleSheet("""
            #systemPanel {
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #2c3e50, stop: 1 #34495e);
                border-bottom: 2px solid #1a252f;
            }
        """)
        
        layout = QGridLayout(panel)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(20)
        
        # CPU Usage
        cpu_group = self.create_metric_group("💻 CPU Usage", "0%")
        self.cpu_progress = cpu_group['progress']
        self.cpu_value = cpu_group['value']
        layout.addWidget(cpu_group['widget'], 0, 0)
        
        # Memory Usage
        mem_group = self.create_metric_group("🧠 Memory Usage", "0%")
        self.mem_progress = mem_group['progress']
        self.mem_value = mem_group['value']
        layout.addWidget(mem_group['widget'], 0, 1)
        
        # Memory Details
        mem_details_group = self.create_details_group("📊 Memory Details")
        self.mem_used_label = QLabel("Used: 0 GB")
        self.mem_available_label = QLabel("Available: 0 GB")
        self.mem_total_label = QLabel("Total: 0 GB")
        
        mem_details_layout = mem_details_group['layout']
        mem_details_layout.addWidget(self.mem_used_label)
        mem_details_layout.addWidget(self.mem_available_label)
        mem_details_layout.addWidget(self.mem_total_label)
        layout.addWidget(mem_details_group['widget'], 0, 2)
        
        # System Info
        sys_info_group = self.create_details_group("🖥️ System Information")
        self.os_label = QLabel(f"OS: {platform.system()} {platform.release()}")
        self.arch_label = QLabel(f"Arch: {platform.architecture()[0]}")
        self.processor_label = QLabel(f"Processor: {platform.processor()[:30]}...")
        
        sys_info_layout = sys_info_group['layout']
        sys_info_layout.addWidget(self.os_label)
        sys_info_layout.addWidget(self.arch_label)
        sys_info_layout.addWidget(self.processor_label)
        layout.addWidget(sys_info_group['widget'], 0, 3)
        
        # CPU Scheduling Info
        scheduling_group = self.create_details_group("⚙️ CPU Scheduling")
        self.scheduling_label = QLabel("Algorithm: Round Robin")
        self.scheduling_desc_label = QLabel("Processes execute in time slices")
        self.scheduling_desc_label.setWordWrap(True)
        self.scheduling_desc_label.setStyleSheet("color: #bdc3c7; font-size: 10px;")
        
        scheduling_layout = scheduling_group['layout']
        scheduling_layout.addWidget(self.scheduling_label)
        scheduling_layout.addWidget(self.scheduling_desc_label)
        layout.addWidget(scheduling_group['widget'], 0, 4)
        
        return panel
        
    def create_metric_group(self, title, initial_value):
        widget = QFrame()
        widget.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                border: 1px solid #34495e;
            }
        """)
        
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(15, 10, 15, 10)
        
        # Title
        title_label = QLabel(title)
        title_label.setStyleSheet("""
            QLabel {
                color: #ecf0f1;
                font-size: 12px;
                font-weight: bold;
            }
        """)
        
        # Value
        value_label = QLabel(initial_value)
        value_label.setStyleSheet("""
            QLabel {
                color: #3498db;
                font-size: 18px;
                font-weight: bold;
            }
        """)
        
        # Progress bar
        progress_bar = QProgressBar()
        progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #34495e;
                border-radius: 4px;
                text-align: center;
                background: #2c3e50;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #3498db, stop: 1 #2980b9);
                border-radius: 3px;
            }
        """)
        progress_bar.setMaximum(100)
        
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(progress_bar)
        
        return {
            'widget': widget,
            'value': value_label,
            'progress': progress_bar
        }
        
    def create_details_group(self, title):
        widget = QFrame()
        widget.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                border: 1px solid #34495e;
            }
        """)
        
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(15, 10, 15, 10)
        
        # Title
        title_label = QLabel(title)
        title_label.setStyleSheet("""
            QLabel {
                color: #ecf0f1;
                font-size: 12px;
                font-weight: bold;
                margin-bottom: 5px;
            }
        """)
        
        # Content layout
        content_layout = QVBoxLayout()
        content_layout.setSpacing(3)
        
        layout.addWidget(title_label)
        layout.addLayout(content_layout)
        
        return {
            'widget': widget,
            'layout': content_layout
        }
        
    def create_processes_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Processes table: first column will show application icon (replaces PID)
        self.process_table = QTableWidget()
        self.process_table.setColumnCount(7)
        self.process_table.setHorizontalHeaderLabels(["", "#", "Name", "Arrival Time", "Priority", "CPU %", "Memory (MB)"])
        
        # Table styling
        self.process_table.setStyleSheet("""
            QTableWidget {
                background: #2c3e50;
                alternate-background-color: #34495e;
                gridline-color: #1a252f;
                border: none;
                outline: none;
            }
            QTableWidget::item {
                padding: 5px;
                border-bottom: 1px solid #1a252f;
                color: #ecf0f1;
            }
            QTableWidget::item:selected {
                background: #3498db;
                color: white;
            }
            QHeaderView::section {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 #34495e, stop: 1 #2c3e50);
                color: #ecf0f1;
                padding: 8px;
                border: none;
                border-right: 1px solid #1a252f;
                font-weight: bold;
            }
        """)
        
        # Table configuration
        self.process_table.setAlternatingRowColors(True)
        self.process_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.process_table.setSortingEnabled(True)
        self.process_table.setShowGrid(False)
        
        # Column widths
        header = self.process_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # App icon
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Process #
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # Name
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Arrival Time
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Priority
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)  # CPU
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)  # Memory
        
        self.process_table.sortByColumn(5, Qt.SortOrder.DescendingOrder)  # Sort by CPU initially (column 5)

        # Request larger icons for better fidelity and adjust row height accordingly
        # Use 64px display size to show higher resolution icons with more detail
        self.icon_size = 64
        self.process_table.setIconSize(QSize(self.icon_size, self.icon_size))
        try:
            self.process_table.verticalHeader().setDefaultSectionSize(self.icon_size + 12)
        except Exception:
            pass

        layout.addWidget(self.process_table)
        
        return panel
        
    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background: #2c3e50;
            }
            QToolBar {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 #34495e, stop: 1 #2c3e50);
                border: none;
                border-bottom: 1px solid #1a252f;
                spacing: 5px;
                padding: 5px;
            }
            QToolButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 5px 10px;
                color: #ecf0f1;
            }
            QToolButton:hover {
                background: rgba(52, 152, 219, 0.3);
                border: 1px solid #3498db;
            }
            QLineEdit {
                background: #34495e;
                border: 1px solid #1a252f;
                border-radius: 4px;
                padding: 5px;
                color: #ecf0f1;
            }
            QLineEdit:focus {
                border: 1px solid #3498db;
            }
            QStatusBar {
                background: #2c3e50;
                color: #bdc3c7;
                border-top: 1px solid #1a252f;
            }
        """)
        
    def start_updates(self):
        # Start the update thread
        if self.update_thread and self.update_thread.isRunning():
            return
        self.update_thread = ProcessUpdateThread()
        self.update_thread.process_data_ready.connect(self.update_process_list)
        self.update_thread.system_data_ready.connect(self.update_system_info)
        self.update_thread.start()
        # thread pool for background tasks (kill worker etc.)
        self._thread_pool = QThreadPool.globalInstance()
        
    @pyqtSlot(dict)
    def update_system_info(self, system_data):
        """Update system information (called from thread)"""
        if self.is_updating:
            return
            
        try:
            self.is_updating = True
            
            # CPU usage
            cpu_percent = system_data['cpu_percent']
            self.cpu_progress.setValue(int(cpu_percent))
            self.cpu_value.setText(f"{cpu_percent:.1f}%")
            self.cpu_label.setText(f"CPU: {cpu_percent:.1f}%")
            
            # RAM usage
            ram_info = system_data['ram_info']
            ram_percent = ram_info['percent']
            ram_used_gb = ram_info['used'] / (1024 ** 3)
            ram_total_gb = ram_info['total'] / (1024 ** 3)
            ram_available_gb = (ram_info['total'] - ram_info['used']) / (1024 ** 3)
            
            self.mem_progress.setValue(int(ram_percent))
            self.mem_value.setText(f"{ram_percent:.1f}%")
            self.ram_label.setText(f"RAM: {ram_percent:.1f}%")
            
            # Memory details
            self.mem_used_label.setText(f"Used: {ram_used_gb:.1f} GB")
            self.mem_available_label.setText(f"Available: {ram_available_gb:.1f} GB")
            self.mem_total_label.setText(f"Total: {ram_total_gb:.1f} GB")
            
        finally:
            self.is_updating = False
            
    @pyqtSlot(list)
    def update_process_list(self, processes):
        """Update process list (called from thread)"""
        if self.is_updating:
            return
            
        try:
            self.is_updating = True
            # Apply CPU scheduling algorithm to sort processes
            scheduled_processes = self.cpu_scheduler.schedule_processes(processes)
            self.process_data = scheduled_processes
            # Store current selection PID (so we can re-select after update)
            current_pid = None
            try:
                cur_item = self.process_table.currentItem()
                if cur_item:
                    sel_row = cur_item.row()
                    pid_item = self.process_table.item(sel_row, 0)
                    if pid_item:
                        try:
                            current_pid = int(pid_item.text())
                        except Exception:
                            current_pid = None
            except Exception:
                current_pid = None

            # Disable sorting and UI updates during batch update for better performance
            self.process_table.setSortingEnabled(False)
            self.process_table.setUpdatesEnabled(False)
            
            # Batch update row count once
            current_row_count = self.process_table.rowCount()
            target_row_count = len(scheduled_processes)
            
            if current_row_count != target_row_count:
                self.process_table.setRowCount(target_row_count)

            for row, process in enumerate(scheduled_processes):
                # Skip if row is out of bounds (shouldn't happen but safety check)
                if row >= target_row_count:
                    continue
                    
                # PID
                # App icon (replaces PID column)
                exe_path = process.get('exe') or ""
                icon_widget = self.process_table.cellWidget(row, 0)
                # get pixmap (cached) using helper - use actual icon size for quality
                pix = None
                try:
                    icon_size = getattr(self, 'icon_size', 64)
                    pix = self._get_icon_pixmap(exe_path, size=icon_size)
                except Exception:
                    pix = None

                if not icon_widget:
                    lbl = QLabel()
                    icon_size = getattr(self, 'icon_size', 64)
                    lbl.setFixedSize(icon_size, icon_size)
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    lbl.setScaledContents(True)
                    if pix:
                        lbl.setPixmap(pix)
                    self.process_table.setCellWidget(row, 0, lbl)
                    icon_widget = lbl
                else:
                    # update existing widget pixmap
                    try:
                        if pix:
                            icon_widget.setPixmap(pix)
                    except Exception:
                        pass
                # set tooltip to show PID and exe path
                try:
                    if icon_widget:
                        tt = f"{process.get('name','')} (PID: {process.get('pid')})"
                        if exe_path:
                            tt += "\n" + exe_path
                        icon_widget.setToolTip(tt)
                except Exception:
                    pass
                
                # Process Number
                proc_num_item = self.process_table.item(row, 1)
                proc_num_text = f"P{process.get('process_number', row + 1)}"
                if not proc_num_item:
                    proc_num_item = QTableWidgetItem(proc_num_text)
                    self.process_table.setItem(row, 1, proc_num_item)
                else:
                    proc_num_item.setText(proc_num_text)
                proc_num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                
                # Name (store PID in UserRole for re-selection)
                name_item = self.process_table.item(row, 2)
                if not name_item:
                    name_item = QTableWidgetItem(process['name'])
                    name_item.setData(Qt.ItemDataRole.UserRole, int(process.get('pid', 0)))
                    self.process_table.setItem(row, 2, name_item)
                else:
                    name_item.setText(process['name'])
                    try:
                        name_item.setData(Qt.ItemDataRole.UserRole, int(process.get('pid', 0)))
                    except Exception:
                        pass
                
                # Arrival Time
                arrival_item = self.process_table.item(row, 3)
                arrival_time = process.get('arrival_time', 0)
                # Format as seconds with 2 decimal places
                arrival_text = f"{arrival_time:.2f}s"
                if not arrival_item:
                    arrival_item = QTableWidgetItem(arrival_text)
                    self.process_table.setItem(row, 3, arrival_item)
                else:
                    arrival_item.setText(arrival_text)
                arrival_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                
                # Priority
                priority_item = self.process_table.item(row, 4)
                priority = process.get('priority', 50)
                priority_text = str(priority)
                if not priority_item:
                    priority_item = QTableWidgetItem(priority_text)
                    self.process_table.setItem(row, 4, priority_item)
                else:
                    priority_item.setText(priority_text)
                priority_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                
                # CPU
                cpu_item = self.process_table.item(row, 5)
                cpu_text = f"{process['cpu_percent']:.1f}"
                if not cpu_item:
                    cpu_item = QTableWidgetItem(cpu_text)
                    self.process_table.setItem(row, 5, cpu_item)
                else:
                    cpu_item.setText(cpu_text)
                cpu_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                
                # Memory
                mem_item = self.process_table.item(row, 6)
                mem_text = f"{process['memory_mb']:.1f}"
                if not mem_item:
                    mem_item = QTableWidgetItem(mem_text)
                    self.process_table.setItem(row, 6, mem_item)
                else:
                    mem_item.setText(mem_text)
                mem_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                
                # Restore selection
                if current_pid and process['pid'] == current_pid:
                    self.process_table.selectRow(row)
            
            # Re-enable sorting and UI updates - this will trigger a repaint
            self.process_table.setUpdatesEnabled(True)
            self.process_table.setSortingEnabled(True)
            
            # Force a repaint to show updated icons smoothly
            self.process_table.viewport().update()
            
            # Update process count
            self.process_count_label.setText(f"Processes: {len(scheduled_processes)}")
            
        finally:
            self.is_updating = False
        
    def filter_processes(self):
        search_text = self.search_box.text().lower()
        
        for row in range(self.process_table.rowCount()):
            name_item = self.process_table.item(row, 2)  # Name is now column 2
            proc_num_item = self.process_table.item(row, 1)  # Process number is column 1
            
            if name_item:
                name = name_item.text().lower()
                proc_num = proc_num_item.text().lower() if proc_num_item else ""
                
                # Show row if search text matches name or process number
                matches = search_text in name or search_text in proc_num
                self.process_table.setRowHidden(row, not matches)
                
    def manual_refresh(self):
        """Manual refresh triggered by user"""
        if self.update_thread and self.update_thread.isRunning():
            # Request the background thread to perform an immediate update
            self.statusBar().showMessage("Refreshing...", 2000)
            try:
                # set the apps_only flag on the thread and request force update
                self.update_thread.apps_only = bool(self.apps_only_act.isChecked())
                self.update_thread.force_update = True
            except Exception:
                pass
        else:
            self.refresh_data()
            
    def refresh_data(self):
        """Fallback refresh method"""
        try:
            cpu_percent = get_cpu_percent()
            ram_info = get_ram_info()
            apps_only = True
            try:
                apps_only = bool(self.apps_only_act.isChecked())
            except Exception:
                apps_only = True
            processes = list_processes(max_count=150, apps_only=apps_only)
            
            self.update_system_info({
                'cpu_percent': cpu_percent,
                'ram_info': ram_info
            })
            self.update_process_list(processes)
            
            self.statusBar().showMessage("Data refreshed", 2000)
        except Exception as e:
            self.statusBar().showMessage(f"Refresh error: {str(e)}", 3000)
            
    def toggle_updates(self):
        """Toggle automatic updates on/off"""
        if self.pause_act.isChecked():
            if self.update_thread:
                self.update_thread.stop()
            self.update_status_label.setText("🔴 Updates Paused")
            self.statusBar().showMessage("Updates paused", 2000)
        else:
            self.start_updates()
            self.update_status_label.setText("🟢 Updating")
            self.statusBar().showMessage("Updates resumed", 2000)
            
    def end_selected_task(self):
        current_row = self.process_table.currentRow()
        if current_row < 0:
            QMessageBox.information(self, "No Selection", "Please select a process to terminate.")
            return
            
        # Column 0 is now an icon widget, not an item
        # PID is stored in the UserRole of the name item (column 2)
        name_item = self.process_table.item(current_row, 2)
        if not name_item:
            QMessageBox.warning(self, "Selection error", "Selected row is invalid.")
            return

        # Get PID from UserRole data (stored during process list update)
        try:
            pid = name_item.data(Qt.ItemDataRole.UserRole)
            if pid is None:
                # Fallback: try to get from process_data if available
                if current_row < len(self.process_data):
                    pid = self.process_data[current_row].get('pid')
                if pid is None:
                    raise ValueError("PID not found")
            pid = int(pid)
        except (ValueError, TypeError, AttributeError) as e:
            QMessageBox.warning(self, "Invalid PID", f"Could not get PID from selected row: {str(e)}")
            return

        name = name_item.text()

        # Confirm with a simple QMessageBox to avoid cross-version dialog return issues
        reply = QMessageBox.question(self, "Confirm Termination",
                                     f"Terminate process {name} (PID: {pid})?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Offload termination to background worker to avoid blocking the GUI
        self.statusBar().showMessage("Terminating process...", 2000)

        def _kill_callback(success, msg, pid_ret):
            try:
                if success:
                    if msg == 'elevation_launched':
                        QMessageBox.information(self, "Elevation launched",
                                                "An elevated prompt was launched; if you approved it, the process should be terminated.\nWait a moment and refresh.")
                    else:
                        self.statusBar().showMessage(f"Process {name} (PID: {pid}) terminated ({msg})", 3000)
                else:
                    QMessageBox.warning(self, "Termination failed", f"{msg}")
            finally:
                QTimer.singleShot(1000, self.manual_refresh)

        try:
            worker = KillWorker(pid, name, _kill_callback)
            try:
                # submit to pool
                self._thread_pool.start(worker)
            except Exception:
                # fallback: run in a simple thread
                import threading
                t = threading.Thread(target=worker.run, daemon=True)
                t.start()
        except Exception as e:
            QMessageBox.warning(self, "Termination error", f"Failed to start termination worker: {e}")
    
    def on_apps_only_toggled(self, checked):
        """Handler when user toggles Applications-only mode."""
        try:
            if self.update_thread and self.update_thread.isRunning():
                self.update_thread.apps_only = bool(checked)
                self.update_thread.force_update = True
            else:
                # immediate refresh
                self.manual_refresh()
        except Exception:
            pass
    
    def on_scheduling_changed(self, index):
        """Handler when user changes CPU scheduling algorithm."""
        try:
            algorithm = self.scheduling_combo.itemData(index)
            if algorithm:
                self.cpu_scheduler.set_algorithm(algorithm)
                # Update scheduling display
                self.scheduling_label.setText(f"Algorithm: {self.cpu_scheduler.get_algorithm_name()}")
                self.scheduling_desc_label.setText(self.cpu_scheduler.get_algorithm_description())
                # Trigger refresh to apply new scheduling
                if self.update_thread and self.update_thread.isRunning():
                    self.update_thread.force_update = True
                else:
                    self.manual_refresh()
        except Exception as e:
            print(f"Error changing scheduling algorithm: {e}")
        
    def closeEvent(self, event):
        """Handle application close"""
        # Stop update thread
        if self.update_thread and self.update_thread.isRunning():
            self.update_thread.stop()
            
        reply = QMessageBox.question(self, "Confirm Exit", 
                                   "Are you sure you want to exit Task Manager?",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            event.accept()
        else:
            event.ignore()

def main():
    app = QApplication(sys.argv)
    
    # Set application font
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    
    window = ModernTaskManager()
    window.show()
    
    sys.exit(app.exec())

if __name__ == '__main__':
    main()