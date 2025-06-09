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
from gui.download_popup import DownloadPopup
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
        
        self.update_download_table()
        layout.addWidget(self.download_table)

        # Info text display



        # Download directory
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Download Directory:"))
        self.dir_label = QLabel(self.config['save_path'])
        dir_layout.addWidget(self.dir_label)
        self.dir_button = QPushButton("Change")
        self.dir_button.clicked.connect(self.change_directory)
        dir_layout.addWidget(self.dir_button)
        layout.addLayout(dir_layout)


        # Status bar
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")

    def update_download_table(self):
        tasks = self.download_manager.tasks
        self.download_table.setRowCount(len(tasks))
        
        for row, task in enumerate(tasks):
            # Basename column
            name_item = QTableWidgetItem(task.basename)
            
            # Size column (show progress if downloading)
            if task.status not in ["Queued", "Completed"]:
                size_text = f"{format_bytes(task.downloaded)} / {format_bytes(task.total_size)}"
            else:
                size_text = format_bytes(task.total_size)
            size_item = QTableWidgetItem(size_text)
            size_item.setTextAlignment(Qt.AlignCenter)
            
            # Status column
            status_item = QTableWidgetItem(task.status)
            
            # Apply color coding based on status
            if task.status == "Downloading":
                status_item.setForeground(QBrush(QColor(Qt.green)))
            elif task.status == "Completed":
                status_item.setForeground(QBrush(QColor(Qt.cyan)))
            elif task.status == "Queued":
                status_item.setForeground(QBrush(QColor(Qt.darkGray)))
            elif task.status == "Paused":
                status_item.setForeground(QBrush(QColor(Qt.darkYellow)))
            else:
                status_item.setForeground(QBrush(QColor(Qt.red)))
            
            # Speed column
            speed_item = QTableWidgetItem(f"{format_bytes(get_speed(task.history))}/s")
            speed_item.setTextAlignment(Qt.AlignCenter)
            
            # Add items to table
            self.download_table.setItem(row, 0, name_item)
            self.download_table.setItem(row, 1, size_item)
            self.download_table.setItem(row, 2, status_item)
            self.download_table.setItem(row, 3, speed_item)

    def setup_config(self):
        try:
            with open(f"{self.config_dir}/config.json", "r") as f:
                self.config = json.load(f)
            with open(f"{self.config_dir}/config.json", "w") as f:
                check_default(self.config, DEFAULT_CONFIG)
                json.dump(self.config, f, indent=4)
        except Exception:
            os.makedirs(self.config_dir, exist_ok=True)
            self.config = DEFAULT_CONFIG
            with open(f"{self.config_dir}/config.json", "w") as f:
                json.dump(self.config, f, indent=4)

        self.logger = logging.getLogger("Nadeko~don")
        self.logger.setLevel(logging.ERROR)
        file_handler = RotatingFileHandler(
            filename=f'{self.config_dir}/errors.log',
            maxBytes=5 * 1024 ** 2,
            backupCount=1, 
            mode='a', 
            encoding='utf-8',
        )
        file_handler.setLevel(logging.ERROR)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        self.server = ServerThread(self.config['port'])
        self.server.url_received.connect(self.handle_received_url)
        self.server.start()

        self.download_manager = DownloadManager(self)
        self.download_manager.queue_updated.connect(self.update_download_table)
        self.download_manager_thread = QThread()
        self.download_manager.moveToThread(self.download_manager_thread)
        self.download_manager_thread.start()

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.update_download_table)
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
        os.makedirs(self.config_dir, exist_ok=True)
        with open(f"{self.config_dir}/config.json", "w") as f:
                json.dump(self.config, f, indent=4)


    def handle_received_url(self, url):
        self.url_input.setText(url)
        self.sys_tray.showMessage(
            "Nadeko~don",
            f"URL received from extension: {url}",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )
        self.handle_download()

    def handle_download(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Input Error", "Please enter a valid URL")
            return

        self.popup = DownloadPopup(self, url)
        self.popup.thumbnail_label.setText("Loading...")

        self.download_button.setEnabled(True)

    def handle_error(self, message):
        self.download_button.setEnabled(True)
        self.logger.error(message)

    def change_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Download Directory",
            self.config['save_path']
        )
        if directory:
            self.config['save_path'] = directory
            self.dir_label.setText(directory)
            self.update_config()

    def handle_updateTableClicked(self, selected, deselected):
        if selected:
            row = selected.indexes()[0].row()
            task = self.download_manager.tasks[row]
            self.play_action.setChecked(task.status not in ["Downloading", "Queued"])
            self.handle_play_toggle(task.status not in ["Downloading", "Queued"])
            
            if task.status in ["Canceled", "Completed"]:
                self.play_action.setEnabled(False)
                self.stop_action.setEnabled(False)
            else:
                self.play_action.setEnabled(True)
                self.stop_action.setEnabled(True)
        else:
            self.play_action.setEnabled(False)
            self.stop_action.setEnabled(False)

    def handle_stop(self):
        task = self.download_manager.tasks[self.download_table.selectedItems()[0].row()] 
        task.cancel()
        # Add your functionality here

    def handle_play_toggle(self, checked):
        if checked:
            self.play_action.setIcon(
                self.style().standardIcon(QStyle.SP_MediaPlay)
            )
        else:
            self.play_action.setIcon(
                self.style().standardIcon(QStyle.SP_MediaPause)
            )

    def handle_play_trigger(self, checked):
        task = self.download_manager.tasks[self.download_table.selectedItems()[0].row()]
        if checked:
            task.pause()
            self.play_action.setIcon(
                self.style().standardIcon(QStyle.SP_MediaPlay)
            )
        else:
            task.resume()
            self.play_action.setIcon(
                self.style().standardIcon(QStyle.SP_MediaPause)
            )

    def handle_config(self):
        self.cpopup = ConfigPopup(self)
        self.cpopup.speed_changed.connect(self.download_manager.set_max_speed)
        self.cpopup.workers_changed.connect(self.download_manager.set_max_workers)
        self.cpopup.show()

    def handle_download_complete(self, success, message):
        if success:
            self.sys_tray.showMessage(
                "Nadeko~don",
                "Download completed successfully!",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
        else:
            self.sys_tray.showMessage(
                "Nadeko~don",
                "Download failed!",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )

    def closeEvent(self, event):
        if QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
            self.sys_tray.showMessage(
                "Nadeko~don",
                "Application is running in system tray",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
        else:
            self.quit_app()
            event.accept()

    def quit_app(self):
        self.server.stop()
        self.download_manager.shutdown()
        self.download_manager_thread.exit()
        self.sys_tray.hide()
        QApplication.quit()
