import sys, argparse
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from gui.main_window import MainWindow

APP_NAME = "Nadeko~don"
__version__ = "2.0.0"

def parse_arguments():
    """Parses command-line arguments for the application."""
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=f"{APP_NAME} - Version {__version__}"
    )
    parser.add_argument(
        '-v', '--version',
        action='version',
        version=f'%(prog)s {__version__}',
        help="Show application's version number and exit."
    )
    # You can add more arguments here if needed in the future
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()


    if hasattr(sys, '_MEIPASS'):
        plugin_path = os.path.join(sys._MEIPASS, 'plugins')
        os.environ['QT_PLUGIN_PATH'] = plugin_path

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)

    socket = QLocalSocket()
    socket.connectToServer(APP_NAME)
    if socket.waitForConnected(5000):
        QMessageBox.warning(None, "Application Already Running",
                            "Another instance of this application is already running.\n"
                            "Please check your system tray or taskbar.")
        sys.exit(0) # Exit if another instance is already running
    else:
        server = QLocalServer()
        if not server.listen(APP_NAME):
            # Fallback if listen fails (e.g., server name already in use by a zombie process)
            QMessageBox.critical(None, "Error",
                                 "Could not start the application server. "
                                 "Please ensure no other instance is running and try again.")
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
