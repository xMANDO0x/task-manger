# main.py
import sys
from PyQt6.QtWidgets import QApplication
from gui import ModernTaskManager

def main():
    app = QApplication(sys.argv)
    win = ModernTaskManager()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()