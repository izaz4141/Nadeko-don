from PySide6.QtCore import Qt, Slot, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QDialog, QLineEdit,
    QSpinBox, QDoubleSpinBox, QFileDialog
)

import os

class ConfigPopup(QDialog):
    speed_changed = Signal()
    workers_changed = Signal()
    def __init__(self, parent):
        super(ConfigPopup, self).__init__(parent)
        self.setWindowTitle("Nadeko~don: Configuration Popup")
        self.main_window = parent
        self.saveDir = parent.config['save_path']
        self.ytCookies = parent.config['yt_cookies']
        self.init_ui()

    def init_ui(self):
        self.setMinimumSize(600, 250)
        main_layout = QVBoxLayout()

        port_section = QHBoxLayout()
        port_section.addWidget(QLabel("Port:"))
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(self.main_window.config['port'])
        port_section.addWidget(self.port_input)
        main_layout.addLayout(port_section)

        saveDir_section = QHBoxLayout()
        saveDir_section.addWidget(QLabel("Download Folder:"))
        self.saveDir_input = QPushButton("Change")
        self.saveDir_input.clicked.connect(lambda: self.handle_changeDir('save_path'))
        saveDir_section.addWidget(self.saveDir_input)
        main_layout.addLayout(saveDir_section)

        speed_section = QHBoxLayout()
        speed_section.addWidget(QLabel("Speed Limit:"))
        self.speed_input = QDoubleSpinBox()
        self.speed_input.setRange(0,999999999)
        self.speed_input.setValue(self.main_window.config['max_speed'])
        self.speed_input.setSingleStep(0.5)
        self.speed_input.setDecimals(2)
        speed_section.addWidget(self.speed_input)
        main_layout.addLayout(speed_section)

        concurrency_section = QHBoxLayout()
        concurrency_section.addWidget(QLabel("Concurrency:"))
        self.concurrency_input = QSpinBox()
        self.concurrency_input.setRange(1,64)
        self.concurrency_input.setValue(self.main_window.config['concurrency'])
        concurrency_section.addWidget(self.concurrency_input)
        main_layout.addLayout(concurrency_section)

        sim_downloads_section = QHBoxLayout()
        sim_downloads_section.addWidget(QLabel("Simultaneous Download:"))
        self.sim_downloads_input = QSpinBox()
        self.sim_downloads_input.setRange(1,64)
        self.sim_downloads_input.setValue(self.main_window.config['max_workers'])
        sim_downloads_section.addWidget(self.sim_downloads_input)
        main_layout.addLayout(sim_downloads_section)

        ytCookies_section = QHBoxLayout()
        ytCookies_section.addWidget(QLabel("YT Cookies:"))
        self.ytCookies_input = QPushButton("Browse")
        self.ytCookies_input.clicked.connect(lambda: self.handle_changeDir('yt_cookies'))
        ytCookies_section.addWidget(self.ytCookies_input)
        main_layout.addLayout(ytCookies_section)

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.handle_finished)
        main_layout.addWidget(ok_button)

        self.setLayout(main_layout)

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

        self.close()