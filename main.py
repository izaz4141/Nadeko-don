import sys
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox
from gui.main_window import MainWindow

VERSION = "1.1.0"

if __name__ == "__main__":
    if hasattr(sys, '_MEIPASS'):
        plugin_path = os.path.join(sys._MEIPASS, 'PySide6', 'plugins')
        os.environ['QT_PLUGIN_PATH'] = plugin_path

    app = QApplication([])
    # Ensure single instance
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(None, "Warning", "System tray not available")

    window = MainWindow()
    sys.exit(app.exec())
