import sys, os
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from gui.main_window import MainWindow
from utils.helpers import resource_path

APP_NAME = "Nadeko~don"
__version__ = "2.2.0"


if __name__ == "__main__":
    if hasattr(sys, '_MEIPASS'):
        plugin_path = os.path.join(sys._MEIPASS, 'plugins')
        os.environ['QT_PLUGIN_PATH'] = plugin_path
        os.environ['QT_LOGGING_RULES'] = "qt.qpa.wayland.*.warning=false"

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(resource_path("assets/nadeko-don.png")))

    socket = QLocalSocket()
    socket.connectToServer(APP_NAME)
    if socket.waitForConnected(500):
        QMessageBox.warning(None, "Application Already Running",
                            "Another instance of this application is already running.\n"
                            "Please check your system tray or taskbar.")
        sys.exit(0) # Exit if another instance is already running
    else:
        server = QLocalServer()
        if not server.listen(APP_NAME):
            if sys.platform != "win32":
                QMessageBox.information(None, "Cleanup",
                                        "Found a stale server instance. Attempting to clean up and restart.")
                try:
                    QLocalServer.removeServer(APP_NAME)
                    # Try listening again after removal
                    if not server.listen(APP_NAME):
                        QMessageBox.critical(None, "Error",
                                             "Could not restart the application server after cleanup. "
                                             "Please ensure no other instance is running and try again.")
                        sys.exit(1)
                except Exception as e:
                    QMessageBox.critical(None, "Error",
                                         f"Failed to remove stale server: {e}\n"
                                         "Please restart your computer or manually delete the server file if on Linux/macOS.")
                    sys.exit(1)
            else:
                # Generic error if not a known stale server issue or on Windows
                QMessageBox.critical(None, "Error",
                                     "Could not start the application server. "
                                     "Please ensure no other instance is running and try again. Error: " +
                                     server.errorString())
                sys.exit(1)
        # Make sure the server cleans up when the app exits
        app.aboutToQuit.connect(server.close)

    # Ensure single instance
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(None, "Warning", "System tray not available")

    try:
        window = MainWindow()
        window.show()
    except Exception as e:
        QMessageBox.critical(None, "Initialization Error",
                             f"Failed to start the main application window: {e}")
        sys.exit(1)


    sys.exit(app.exec())
