# backend.py
import os
import platform
import subprocess
import time

try:
    import psutil
    PSUTIL = True
except Exception:
    PSUTIL = False

IS_WINDOWS = platform.system() == "Windows"

# Cache for process list to avoid frequent heavy operations
_process_cache = {}
_cache_timestamp = 0
CACHE_DURATION = 1.0  # Cache for 1 second

def get_cpu_percent(interval=0.3):  # Reduced interval
    """
    Return overall CPU usage percent.
    """
    if PSUTIL:
        return psutil.cpu_percent(interval=interval)
    return 0.0

def get_ram_info():
    """
    Returns dict: { 'total': bytes, 'used': bytes, 'percent': 0-100 }
    """
    if PSUTIL:
        mem = psutil.virtual_memory()
        return {"total": mem.total, "used": mem.used, "percent": mem.percent}

    if IS_WINDOWS:
        try:
            out = subprocess.check_output(
                ["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize", "/Value"],
                universal_newlines=True, stderr=subprocess.DEVNULL
            )
            parts = {}
            for line in out.strip().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    parts[k.strip()] = v.strip()
            free_kb = int(parts.get("FreePhysicalMemory", "0"))
            total_kb = int(parts.get("TotalVisibleMemorySize", "0"))
            used_kb = total_kb - free_kb
            total = total_kb * 1024
            used = used_kb * 1024
            percent = int((used_kb / total_kb) * 100) if total_kb else 0
            return {"total": total, "used": used, "percent": percent}
        except Exception:
            return {"total": 0, "used": 0, "percent": 0}
    return {"total": 0, "used": 0, "percent": 0}

def list_processes(max_count=150, apps_only=True):  # Reduced default max count. apps_only=True returns only GUI applications on Windows
    """
    Return a list of processes with caching for performance.
    """
    global _process_cache, _cache_timestamp
    
    current_time = time.time()
    # Return cached result if still valid
    if (current_time - _cache_timestamp) < CACHE_DURATION and _process_cache.get('max_count') == max_count and _process_cache.get('apps_only') == apps_only:
        return _process_cache.get('processes', [])
    
    procs = []
    visible_app_pids = None
    # If caller wants applications only and we're on Windows, collect visible window PIDs
    if apps_only and IS_WINDOWS:
        visible_app_pids = _get_visible_window_pids()
    if PSUTIL:
        try:
            # Get processes with minimal info first
            processes = list(psutil.process_iter(['pid', 'name']))
            
            # Sample CPU usage more efficiently
            for p in processes[:100]:  # Only sample top processes for CPU
                try:
                    p.cpu_percent(interval=None)
                except Exception:
                    pass
            
            time.sleep(0.05)  # Reduced sleep time
            
            # Collect process info
            for p in processes:
                try:
                    info = p.as_dict(attrs=['pid', 'name', 'memory_info', 'cpu_percent', 'exe'])
                    pid = info.get('pid', 0)
                    # If apps_only is requested, skip processes that don't own a top-level visible window
                    if apps_only and visible_app_pids is not None and pid not in visible_app_pids:
                        continue
                    name = info.get('name') or ""
                    mem = info.get('memory_info')
                    mem_mb = (mem.rss / (1024.0 ** 2)) if mem else 0.0
                    cpu = info.get('cpu_percent') or 0.0
                    
                    procs.append({
                        'pid': pid,
                        'name': name,
                        'exe': info.get('exe') or "",
                        'memory_mb': round(mem_mb, 1),
                        'cpu_percent': round(cpu, 1)
                    })
                    
                    # Early exit if we have enough processes
                    if max_count and len(procs) >= max_count:
                        break
                        
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            # Sort by CPU desc then memory
            procs.sort(key=lambda x: (x['cpu_percent'], x['memory_mb']), reverse=True)
            
        except Exception as e:
            print(f"Error getting processes with psutil: {e}")
            procs = _get_processes_fallback(max_count, apps_only=apps_only)
    
    else:
        procs = _get_processes_fallback(max_count, apps_only=apps_only)
    
    # Cache the result
    _process_cache = {
        'processes': procs,
        'max_count': max_count,
        'apps_only': apps_only,
        'timestamp': current_time
    }
    _cache_timestamp = current_time
    
    return procs


def _get_visible_window_pids():
    """
    Returns a set of PIDs that own a visible top-level window (Windows only).
    Uses Win32 EnumWindows + IsWindowVisible + GetWindowTextLength to filter.
    If anything fails, returns an empty set.
    """
    pids = set()
    if not IS_WINDOWS:
        return pids

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        IsWindowVisible = user32.IsWindowVisible
        GetWindowTextLengthW = user32.GetWindowTextLengthW

        def _foreach(hwnd, lParam):
            try:
                if IsWindowVisible(hwnd):
                    length = GetWindowTextLengthW(hwnd)
                    if length > 0:
                        pid = wintypes.DWORD()
                        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        pids.add(pid.value)
            except Exception:
                pass
            return True

        EnumWindows(EnumWindowsProc(_foreach), 0)
    except Exception:
        # If any of this fails, return empty set so we fall back to returning all processes
        return set()

    return pids

def _get_processes_fallback(max_count=None, apps_only=False):
    """Fallback method for getting processes without psutil"""
    procs = []
    visible_app_pids = None
    if apps_only and IS_WINDOWS:
        visible_app_pids = _get_visible_window_pids()
    
    if IS_WINDOWS:
        try:
            out = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH"], 
                universal_newlines=True, 
                stderr=subprocess.DEVNULL,
                timeout=2  # Add timeout
            )
            for line in out.splitlines()[:max_count] if max_count else out.splitlines():
                parts = [p.strip().strip('"') for p in line.split('","')]
                if len(parts) >= 2:
                    name = parts[0]
                    pid_str = parts[1]
                    try:
                        pid = int(pid_str)
                    except Exception:
                        continue
                    # If apps_only requested, skip PIDs that aren't owning a visible window
                    if apps_only and visible_app_pids is not None and pid not in visible_app_pids:
                        continue
                    mem_str = parts[-1]
                    mem_mb = 0.0
                    try:
                        mem_num = mem_str.replace('K', '').replace(',', '').replace('k', '').strip()
                        mem_k = float(mem_num)
                        mem_mb = mem_k / 1024.0
                    except Exception:
                        mem_mb = 0.0
                    procs.append({
                        'pid': pid, 
                        'name': name, 
                        'memory_mb': round(mem_mb, 1), 
                        'cpu_percent': 0.0
                    })
        except Exception:
            pass
    
    return procs

def kill_process(pid):
    """
    Try to terminate the process.
    """
    if PSUTIL:
        try:
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(timeout=2)  # Reduced timeout
                return True
            except psutil.TimeoutExpired:
                p.kill()
                p.wait(timeout=2)
                return True
        except Exception:
            return False

    if IS_WINDOWS:
        try:
            rc = subprocess.call(
                ["taskkill", "/PID", str(pid), "/F"], 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL,
                timeout=3
            )
            return rc == 0
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 15)  # SIGTERM
            return True
        except Exception:
            return False