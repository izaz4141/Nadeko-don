from PySide6.QtWidgets import ( QApplication, QFileDialog, QToolBar,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QDialog, QStyle,
    QLineEdit, QPushButton, QComboBox, QTextEdit, QLabel, QListWidget,
    QProgressBar, QSplitter, QFrame, QSystemTrayIcon, QMenu, QMessageBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QSizePolicy
)
from PySide6.QtCore import Qt, Slot, QThread, QTimer, QSize, QSettings
from PySide6.QtGui import QAction, QIcon, QPixmap, QBrush, QColor

from network.server_thread import ServerThread
from network.download_thread import DownloadManager, DownloadTask
from gui.download_popup import DownloadPopup # Assuming this now refers to the fixed version
from gui.config_popup import ConfigPopup
from utils.helpers import *
from utils.constants import *

import os, json, logging, time
from logging.handlers import RotatingFileHandler

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Nadeko~don")
        self.setGeometry(100, 100, 800, 500)
        self.config_dir = os.path.expanduser("~/.config/Nadeko~don")
        self.setup_config()
        self.init_ui()
        self.setup_system_tray()
        self.show()

    def init_ui(self):
        # Central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        toolbar = QToolBar("Main Toolbar")
        toolbar.setIconSize(QSize(32, 32))
        self.addToolBar(toolbar)
        self.play_action = QAction(
            QIcon(self.style().standardIcon(QStyle.SP_MediaPlay)), 
            "Play", self
        )
        self.play_action.setCheckable(True)
        self.play_action.setEnabled(False)
        self.play_action.triggered.connect(self.handle_play_trigger)
        toolbar.addAction(self.play_action)
        self.stop_action = QAction(
            QIcon(self.style().standardIcon(QStyle.SP_MediaStop)), 
            "Stop", self
        )
        self.stop_action.setEnabled(False)
        self.stop_action.triggered.connect(self.handle_stop)
        toolbar.addAction(self.stop_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        toolbar.addWidget(spacer)

        config_action = QAction(
            QIcon(resource_path("assets/settings.svg")), 
            "Settings", self
        )
        config_action.triggered.connect(self.handle_config)
        toolbar.addAction(config_action)


        # URL input
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter URL...")
        url_layout.addWidget(self.url_input)
        layout.addLayout(url_layout)

        self.download_button = QPushButton("Download")
        self.download_button.setMaximumWidth(120)
        self.download_button.clicked.connect(self.handle_download)
        url_layout.addWidget(self.download_button)

        ## Create download table
        self.download_table = QTableWidget()
        self.download_table.setColumnCount(4)
        self.download_table.setHorizontalHeaderLabels(["Filename", "Size", "Status", "Speed"])
        self.download_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.download_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.download_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.download_table.selectionModel().selectionChanged.connect(self.handle_updateTableClicked)
        
        # Configure header
        table_header = self.download_table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.Stretch)
        table_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        
        # Initial call to update table is no longer needed here as the download manager will send initial data
        # self.update_download_table() 
        layout.addWidget(self.download_table)

        # Status bar
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")

    @Slot(list) # The slot now expects a list of dictionaries
    def update_download_table(self, tasks_data):
        """
        Updates the download table with the provided list of task data.
        This method is a slot connected to DownloadManager.queue_updated signal.
        """
        self.download_table.setRowCount(len(tasks_data))
        
        for row, task in enumerate(tasks_data): # Iterate directly over the received data
            # Basename column
            name_item = QTableWidgetItem(task['basename'])
            
            # Size column (show progress if downloading)
            if task['status'] not in ["Queued", "Completed"]:
                size_text = f"{format_bytes(task['downloaded'])} / {format_bytes(task['total_size'])}"
            else:
                size_text = format_bytes(task['total_size'])
            size_item = QTableWidgetItem(size_text)
            size_item.setTextAlignment(Qt.AlignCenter)
            
            # Status column
            status_item = QTableWidgetItem(task['status'])
            
            # Apply color coding based on status
            if task['status'] == "Downloading":
                status_item.setForeground(QBrush(QColor(Qt.green)))
            elif task['status'] == "Completed":
                status_item.setForeground(QBrush(QColor(Qt.cyan)))
            elif task['status'] == "Queued":
                status_item.setForeground(QBrush(QColor(Qt.darkGray)))
            elif task['status'] == "Paused":
                status_item.setForeground(QBrush(QColor(Qt.darkYellow)))
            else:
                status_item.setForeground(QBrush(QColor(Qt.red)))
            
            # Speed column
            speed_item = QTableWidgetItem(f"{format_bytes(get_speed(task['history']))}/s")
            speed_item.setTextAlignment(Qt.AlignCenter)
            
            # Add items to table
            self.download_table.setItem(row, 0, name_item)
            self.download_table.setItem(row, 1, size_item)
            self.download_table.setItem(row, 2, status_item)
            self.download_table.setItem(row, 3, speed_item)

        self.handle_updateTableClicked(self.download_table.selectedItems())

    def setup_config(self):
        try:
            # Load config, or create with defaults if missing/corrupted
            with open(f"{self.config_dir}/config.json", "r") as f:
                self.config = json.load(f)
            # Ensure all default keys exist in loaded config
            with open(f"{self.config_dir}/config.json", "w") as f:
                check_default(self.config, DEFAULT_CONFIG)
                json.dump(self.config, f, indent=4)
        except Exception:
            # If any error reading/parsing config, create a fresh default config
            os.makedirs(self.config_dir, exist_ok=True)
            self.config = DEFAULT_CONFIG
            with open(f"{self.config_dir}/config.json", "w") as f:
                json.dump(self.config, f, indent=4)

        # Set up logging for errors
        self.logger = logging.getLogger("Nadeko~don")
        self.logger.setLevel(logging.ERROR)
        file_handler = RotatingFileHandler(
            filename=f'{self.config_dir}/errors.log',
            maxBytes=5 * 1024 ** 2, # 5 MB
            backupCount=1, 
            mode='a', 
            encoding='utf-8',
        )
        file_handler.setLevel(logging.ERROR)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        # Setup server thread for receiving URLs
        self.server = ServerThread(self.config['port'])
        self.server.url_received.connect(self.handle_received_url)
        self.server.start()

        # Setup download manager in a separate QThread
        self.download_manager = DownloadManager(self)
        # Connect to the update_download_table slot, which now expects list data
        self.download_manager.queue_updated.connect(self.update_download_table) 
        self.download_manager_thread = QThread()
        self.download_manager_thread.setObjectName("DownloadManagerThread")
        self.download_manager.moveToThread(self.download_manager_thread)
        self.download_manager_thread.start()

        # Timer to periodically refresh the download table UI
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(lambda: self.download_manager._update_table()) # Trigger _update_table which emits queue_updated
        self.refresh_timer.start(500)  # Update UI every 500ms

    def setup_system_tray(self):
        self.sys_tray = QSystemTrayIcon(self)
        self.sys_tray.setIcon(QIcon(resource_path('assets/nadeko-don.png')))
        self.sys_tray.setToolTip("Nadeko~don\nA GUI for YT-DLP")

        tray_menu = QMenu()
        show_action = tray_menu.addAction("Show")
        quit_action = tray_menu.addAction("Quit")

        show_action.triggered.connect(self.show)
        quit_action.triggered.connect(self.quit_app)

        self.sys_tray.setContextMenu(tray_menu)
        self.sys_tray.show()

    def update_config(self):
        """Saves the current configuration to the config file."""
        os.makedirs(self.config_dir, exist_ok=True)
        with open(f"{self.config_dir}/config.json", "w") as f:
                json.dump(self.config, f, indent=4)


    def handle_received_url(self, url):
        """Slot to handle URLs received from the server thread (e.g., browser extension)."""
        self.url_input.setText(url)
        self.sys_tray.showMessage(
            "Nadeko~don",
            f"URL received from extension: {url}",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )
        self.handle_download() # Automatically trigger download process

    def handle_download(self):
        """Initiates the download process by showing the DownloadPopup."""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Input Error", "Please enter a valid URL")
            return

        self.popup = DownloadPopup(self, url)
        # Note: The download button enabling is handled by the popup itself based on info fetched

    def handle_error(self, message):
        """Central error handler for issues encountered during download or info fetching."""
        self.download_button.setEnabled(True) # Re-enable main download button on error
        self.logger.error(message) # Log the error

    def change_directory(self):
        """Allows the user to change the default download directory."""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Download Directory",
            self.config['save_path']
        )
        if directory:
            self.config['save_path'] = directory
            self.dir_label.setText(directory)
            self.update_config()

    def handle_updateTableClicked(self, selected, deselected=None):
        """
        Handles selection changes in the download table.
        Enables/disables play/stop actions based on selected task status.
        """
        try:
            row = selected.indexes()[0].row() # From selectionChanged Signal
        except Exception:
            try:
                row = selected[0].row() # From update_download_table
            except Exception as e:
                row = None

        if isinstance(row, int):
            task = self.download_manager.tasks[row] 
            # Set play/pause action's checked state based on task status
            self.play_action.setChecked(task.status in ["Paused", "Completed", "Canceled", "ERROR"])
            # Update the icon based on the new checked state
            self.handle_play_toggle(self.play_action.isChecked())
            
            # Enable/disable play/stop actions based on task status
            if task.status in ["Canceled", "Completed", "ERROR"]:
                self.play_action.setEnabled(False)
                self.stop_action.setEnabled(False)
            else:
                self.play_action.setEnabled(True)
                self.stop_action.setEnabled(True)
        else:
            # If no row is selected, disable both actions
            self.play_action.setEnabled(False)
            self.stop_action.setEnabled(False)

    def handle_stop(self):
        """Cancels the currently selected download task."""
        # Ensure a task is selected before proceeding
        if not self.download_table.selectedItems():
            return
        row = self.download_table.selectedItems()[0].row()
        task = self.download_manager.tasks[row] 
        self.download_manager.cancel_download(task)

    def handle_play_toggle(self, checked):
        """Visually updates the play/pause icon based on the 'checked' state."""
        if checked: # If checked (meaning 'play' state, for resuming or starting)
            self.play_action.setIcon(
                self.style().standardIcon(QStyle.SP_MediaPlay)
            )
        else: # If unchecked (meaning 'pause' state, for active downloading)
            self.play_action.setIcon(
                self.style().standardIcon(QStyle.SP_MediaPause)
            )

    def handle_play_trigger(self, checked):
        """
        Triggers pause/resume action for the selected download task.
        'checked' state indicates if the button is now in 'play' (checked) or 'pause' (unchecked) mode.
        """
        # Ensure a task is selected before proceeding
        if not self.download_table.selectedItems():
            return
        row = self.download_table.selectedItems()[0].row()
        task = self.download_manager.tasks[row]

        if checked: # Button is now 'Play' (checked), so it means user wants to PAUSE
            self.download_manager.pause_download(task)
            self.play_action.setIcon( # Update icon to 'Play' as it's now paused
                self.style().standardIcon(QStyle.SP_MediaPlay)
            )
        else: # Button is now 'Pause' (unchecked), so it means user wants to RESUME
            self.download_manager.resume_download(task)
            self.play_action.setIcon( # Update icon to 'Pause' as it's now downloading
                self.style().standardIcon(QStyle.SP_MediaPause)
            )

    def handle_config(self):
        """Opens the configuration popup window."""
        self.cpopup = ConfigPopup(self)
        self.cpopup.speed_changed.connect(self.download_manager.set_max_speed)
        self.cpopup.workers_changed.connect(self.download_manager.set_max_workers)
        self.cpopup.show()

    def closeEvent(self, event):
        """
        Overrides the default close event. If system tray is available,
        hides the window to the tray; otherwise, quits the application.
        """
        if QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore() # Ignore the close event to prevent actual closing
            self.hide() # Hide the window
            self.sys_tray.showMessage(
                "Nadeko~don",
                "Application is running in system tray",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
        else:
            self.quit_app() # If no tray, just quit
            event.accept()

    def quit_app(self):
        """Performs a graceful shutdown of all threads and the application."""
        self.server.stop() # Stop the server thread
        self.download_manager.shutdown() # Tell download manager to stop all tasks
        self.download_manager_thread.exit() # Exit the download manager's thread event loop
        self.download_manager_thread.wait(2000) # Wait for thread to finish (optional timeout)
        self.sys_tray.hide() # Hide system tray icon
        QApplication.quit() # Quit the application
