from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QDialog,
    QSpinBox, QDoubleSpinBox, QFileDialog,
    QMessageBox
)

from utils.updater import UpdateWorker

import os

class ConfigPopup(QDialog):
    finished = Signal()
    speed_changed = Signal()
    workers_changed = Signal()
    def __init__(self, parent):
        super(ConfigPopup, self).__init__(parent)
        self.setWindowTitle("Nadeko~don: Configuration Popup")
        self.main_window = parent
        self.saveDir = parent.config['save_path']
        self.ytCookies = parent.config['yt_cookies']
        self.updater_thread = None
        self.init_ui()
        self.fetch_version(True)

    def init_ui(self):
        self.setMinimumSize(600, 250)
        main_layout = QVBoxLayout()

        port_section = QHBoxLayout()
        port_label = QLabel("Localhost Port")
        port_label.setToolTip("Port used to connect to NadeCon (Extension)")
        port_section.addWidget(port_label)
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(self.main_window.config['port'])
        port_section.addWidget(self.port_input)
        main_layout.addLayout(port_section)

        saveDir_section = QHBoxLayout()
        saveDir_label = QLabel("Download Folder")
        saveDir_label.setToolTip("Location for downloaded files")
        saveDir_section.addWidget(saveDir_label)
        self.saveDir_input = QPushButton("Change")
        self.saveDir_input.clicked.connect(lambda: self.handle_changeDir('save_path'))
        saveDir_section.addWidget(self.saveDir_input)
        main_layout.addLayout(saveDir_section)

        speed_section = QHBoxLayout()
        speed_label = QLabel("Speed Limit (KB)")
        speed_label.setToolTip("Maximum global download speed in kilobytes")
        speed_section.addWidget(speed_label)
        self.speed_input = QDoubleSpinBox()
        self.speed_input.setRange(0,999999999)
        self.speed_input.setValue(self.main_window.config['max_speed'])
        self.speed_input.setSingleStep(0.5)
        self.speed_input.setDecimals(2)
        speed_section.addWidget(self.speed_input)
        main_layout.addLayout(speed_section)

        concurrency_section = QHBoxLayout()
        concurrency_label = QLabel("Concurrency")
        concurrency_label.setToolTip("Number of download split (can make downloads faster)")
        concurrency_section.addWidget(concurrency_label)
        self.concurrency_input = QSpinBox()
        self.concurrency_input.setRange(1,64)
        self.concurrency_input.setValue(self.main_window.config['concurrency'])
        concurrency_section.addWidget(self.concurrency_input)
        main_layout.addLayout(concurrency_section)

        sim_downloads_section = QHBoxLayout()
        sim_downloads_label = QLabel("Simultaneous Download")
        sim_downloads_label.setToolTip("Number of maximum downloads")
        sim_downloads_section.addWidget(sim_downloads_label)
        self.sim_downloads_input = QSpinBox()
        self.sim_downloads_input.setRange(1,64)
        self.sim_downloads_input.setValue(self.main_window.config['max_workers'])
        sim_downloads_section.addWidget(self.sim_downloads_input)
        main_layout.addLayout(sim_downloads_section)

        ytCookies_section = QHBoxLayout()
        ytCookies_label = QLabel("YT Cookie File")
        ytCookies_label.setToolTip("Path to youtube cookie text file used for yt-dlp")
        ytCookies_section.addWidget(ytCookies_label)
        self.ytCookies_input = QPushButton("Browse")
        self.ytCookies_input.clicked.connect(lambda: self.handle_changeDir('yt_cookies'))
        ytCookies_section.addWidget(self.ytCookies_input)
        main_layout.addLayout(ytCookies_section)

        version_section = QHBoxLayout()
        version_label = QLabel("Version")
        version_label.setToolTip("Current application version")
        version_section.addWidget(version_label)
        self.curVersion_label = QLabel("Fetching version...")
        version_section.addWidget(self.curVersion_label)
        self.checkVer_button = QPushButton("Check")
        self.checkVer_button.setToolTip("Check for new version")
        self.checkVer_button.clicked.connect(self.fetch_version)
        version_section.addWidget(self.checkVer_button)
        main_layout.addLayout(version_section)

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.handle_finished)
        main_layout.addWidget(ok_button)

        self.setLayout(main_layout)

    def fetch_version(self, init=False):
        self.curVersion_label.setText("Fetching version...")

        if self.updater_thread and self.updater_thread.isRunning():
            self.updater_thread.stop()
            self.updater_thread.wait(1000)
        self.updater_thread = UpdateWorker(self.main_window)
        if init:
            self.updater_thread.updateDetails.connect(self.init_updater)
        else:
            self.updater_thread.updateDetails.connect(self.handle_updater)
        self.updater_thread.start()

    def init_updater(self, details:list):
        updates, message, download_url = details
        self.curVersion_label.setText(message)

    def handle_updater(self, details:list):
        updates, message, download_url = details
        self.curVersion_label.setText(message)
        if updates and download_url:

            confirm = QMessageBox(self)
            confirm.setText(message)
            confirm.setInformativeText("Do you want to update the app?")
            confirm.setIcon(QMessageBox.Question)

            update_button = confirm.addButton("Update", QMessageBox.AcceptRole)
            cancel_button = confirm.addButton("Cancel", QMessageBox.RejectRole)
            confirm.setDefaultButton(update_button)

            confirm.exec()
            if confirm.clickedButton() == update_button:
                if self.updater_thread and self.updater_thread.isRunning():
                    self.updater_thread.stop()
                    self.updater_thread.wait(1000)
                self.updater_thread = UpdateWorker(self.main_window, download_url)
                self.updater_thread.updateDetails.connect(self.handle_updater)
                self.updater_thread.start()


    def handle_changeDir(self, tipe):
        if tipe in ['save_path']:
            directory = QFileDialog.getExistingDirectory(
                    self,
                    "Select Directory",
                    self.main_window.config[tipe]
            )
            if directory:
                self.saveDir = directory
        if tipe in ['yt_cookies']:
            filee, _ = QFileDialog.getOpenFileName(
                self,
                "Select File",
                os.path.expanduser("~/"),
                "All Files (*)"
            )
            self.ytCookies = filee

    def handle_finished(self):
        self.main_window.config['port'] = self.port_input.value()
        self.main_window.config['save_path'] = self.saveDir
        self.main_window.config['yt_cookies'] = self.ytCookies
        self.main_window.config['concurrency'] = self.concurrency_input.value()

        max_speed = self.speed_input.value()
        prev_speed = self.main_window.config['max_speed']
        self.main_window.config['max_speed'] = max_speed

        max_workers = self.sim_downloads_input.value()
        prev_workers = self.main_window.config['max_workers']
        self.main_window.config['max_workers'] = max_workers

        self.main_window.update_config()

        if max_speed != prev_speed:
            self.speed_changed.emit()
        if max_workers != prev_workers:
            self.workers_changed.emit()

        self.finished.emit()

        self.close()
