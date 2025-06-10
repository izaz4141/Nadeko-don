from PySide6.QtCore import Qt, Slot, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QDialog, QLineEdit,
    QFrame, QFileDialog, QMessageBox
)

from utils.helpers import (
    format_durasi, get_url_info, 
    is_urlDownloadable, format_bytes
)
from network.ydl_thread import YTDL_Thread

import requests, os


class DownloadPopup(QDialog):
    def __init__(self, parent, url):
        super(DownloadPopup, self).__init__(parent)
        self.setWindowTitle("Nadeko~don: Download Popup")
        self.main_window = parent
        self.url = url
        self.thumbnail_thread = None
        self.ytdl = False
        self.ytdl_thread = None
        self.init_ui()
        self.urlInfo = FetchInfo(url)
        self.urlInfo.infoReady.connect(self.get_urlInfo)
        self.urlInfo.start()
        self.show()

    def init_ui(self):
        main_layout = QVBoxLayout()
        sub_layout = QHBoxLayout()

        # Thumbnail display
        self.thumbnail_frame = QFrame()
        self.thumbnail_frame.setVisible(False)
        self.thumbnail_frame.setFrameShape(QFrame.Shape.StyledPanel)
        thumbnail_layout = QVBoxLayout(self.thumbnail_frame)
        self.thumbnail_label = QLabel("")
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setMinimumWidth(240)
        thumbnail_layout.addWidget(self.thumbnail_label)
        sub_layout.addWidget(self.thumbnail_frame)

        side_layout = QVBoxLayout()

        self.saveDir_button = QPushButton(self.main_window.config['save_path'])
        self.saveDir_button.clicked.connect(self.handle_changeDir)
        side_layout.addWidget(self.saveDir_button)

        save_edit = QHBoxLayout()
        self.save_input = QLineEdit()
        self.save_input.setMinimumWidth(400)
        self.save_input.setPlaceholderText("Enter FileName...")
        save_edit.addWidget(self.save_input)
        side_layout.addLayout(save_edit)

        self.filesize = QLabel("")
        side_layout.addWidget(self.filesize)


        # Video Stream
        self.videoWidget = QWidget()
        self.videoWidget.setVisible(False)
        video_stream = QHBoxLayout()
        video_label = QLabel("Video:")
        video_label.setMaximumWidth(90)
        video_stream.addWidget(video_label)
        self.video_select = QComboBox()
        self.video_select.setEnabled(False)
        video_stream.addWidget(self.video_select)
        self.videoWidget.setLayout(video_stream)
        side_layout.addWidget(self.videoWidget)

        # Audio Stream
        self.audioWidget = QWidget()
        self.audioWidget.setVisible(False)
        audio_stream = QHBoxLayout()
        audio_label = QLabel("Audio:")
        audio_label.setMaximumWidth(90)
        audio_stream.addWidget(audio_label)
        self.audio_select = QComboBox()
        self.audio_select.setEnabled(False)
        audio_stream.addWidget(self.audio_select)
        self.audioWidget.setLayout(audio_stream)
        side_layout.addWidget(self.audioWidget)


        sub_layout.addLayout(side_layout)
        main_layout.addLayout(sub_layout)

        self.download_button = QPushButton("Download")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.handle_download)
        main_layout.addWidget(self.download_button)

        self.setLayout(main_layout)

    def handle_changeDir(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Download Directory",
            self.main_window.config['save_path']
        )
        if directory:
            self.main_window.config['save_path'] = directory
            self.main_window.update_config()
        self.saveDir_button.setText(self.main_window.config['save_path'])

    def handle_ytdl_video_info(self, info):
        self.info = info

        self.load_thumbnail(self.info.get('thumbnail')) or self.thumbnail_label.setText("Thumbnail not available")

        # Populate quality options
        self.audio_select.clear()
        self.video_select.clear()
        formats = self.info.get('formats', [])
        # Filter and sort formats
        self.video_formats = [
            [f.get('url'),
             f"{f.get('resolution').split('x')[1]+'p' or '?'}-"
             f"{f.get('vcodec') or '?'}-"
             f"{f.get('video_ext') or '?'}-"
             f"{format_bytes(f.get('filesize') or f.get('filesize_approx') or 0)}",
              (f.get('filesize') or f.get('filesize_approx') or 0)
            ]
            for f in reversed(formats)
            if not f.get('vcodec') in ['audio only', 'none']
        ]

        self.audio_formats = [
            [f.get('url'),
             f"{f.get('acodec', '?')}-"
             f"{f.get('audio_ext', '?')}-"
             f"{format_bytes(f.get('filesize') or f.get('filesize_approx') or 0)}",
             (f.get('filesize') or f.get('filesize_approx') or 0) 
            ]
            for f in reversed(formats)
            if f.get('resolution') == 'audio only'
        ]

        # Add options to combo box
        self.video_formats.append([None, "None", 0])
        self.audio_formats.append([None, "None", 0])
        for url, desc, _ in self.video_formats:
            self.video_select.addItem(desc, url)
        for url, desc, _ in self.audio_formats:
            self.audio_select.addItem(desc, url)

        self.save_input.setText(f"{self.info['title']}.{self.video_formats[0][1].split('-')[2]}")

        self.video_select.setEnabled(True)
        self.audio_select.setEnabled(True)
        self.download_button.setEnabled(True)

        self.video_select.currentIndexChanged.connect(self.handle_selectUpdates)
        self.audio_select.currentIndexChanged.connect(self.handle_selectUpdates)

    def handle_selectUpdates(self):
        if self.video_select.currentData():
            ext = self.video_formats[self.video_select.currentIndex()][1].split('-')[2]
            self.download_button.setEnabled(True)
        elif self.audio_select.currentData():
            ext = self.audio_formats[self.audio_select.currentIndex()][1].split('-')[1]
            self.download_button.setEnabled(True)
        else:
            ext = ""
            self.download_button.setDisabled(True)

        self.save_input.setText(f"{self.info['title']}.{ext}")
        size = (self.video_formats[self.video_select.currentIndex()][2] 
                + self.audio_formats[self.audio_select.currentIndex()][2])
        if size: 
            self.filesize.setText(format_bytes(size))
        else:
            self.filesize.setText('Unknown Sizes')

    def show_overwrite_dialog(self):
        msg_box = QMessageBox()
        msg_box.setText("Existing Files Detected!")
        msg_box.setIcon(QMessageBox.Question)
        
        # Add custom buttons
        resume_button = msg_box.addButton("Resume", QMessageBox.AcceptRole)
        overwrite_button = msg_box.addButton("Overwrite", QMessageBox.ActionRole)
        cancel_button = msg_box.addButton("Cancel", QMessageBox.RejectRole)
        
        # Show the dialog and wait for user input
        msg_box.exec()
        
        # Check which button was clicked
        if msg_box.clickedButton() == resume_button:
            return True
        elif msg_box.clickedButton() == overwrite_button:
            os.path.remove(self.items['path'])
            return True
        elif msg_box.clickedButton() == cancel_button:
            return False

    def load_thumbnail(self, url):
        """Load thumbnail from URL using a separate thread"""
        # Cancel any existing thumbnail thread
        if self.thumbnail_thread and self.thumbnail_thread.isRunning():
            self.thumbnail_thread.quit()
            self.thumbnail_thread.wait(1000)

        # Start new thread
        self.thumbnail_thread = ThumbnailLoader(url)
        self.thumbnail_thread.thumbnail_loaded.connect(self.handle_thumbnail_loaded)
        self.thumbnail_thread.load_failed.connect(self.handle_thumbnail_error)
        self.thumbnail_thread.start()

    def handle_thumbnail_loaded(self, pixmap):
        """Handle successful thumbnail loading"""
        # Scale pixmap to fit label while maintaining aspect ratio
        scaled_pixmap = pixmap.scaled(
            self.thumbnail_label.width(),
            self.thumbnail_label.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.thumbnail_label.setPixmap(scaled_pixmap)

    def handle_thumbnail_error(self, message):
        """Handle thumbnail loading errors"""
        self.thumbnail_label.setText(f"Error: {message}")

    def get_urlInfo(self, downloadable, size, filename):
        if not downloadable:
            # Cancel any existing download thread
            if self.ytdl_thread and self.ytdl_thread.isRunning():
                self.ytdl_thread.stop()
                self.ytdl_thread.wait(1000)

            self.ytdl_thread = YTDL_Thread(self.main_window, self.url)
            self.ytdl_thread.info_ready.connect(self.handle_ytdl_video_info)
            self.ytdl_thread.error.connect(self.main_window.handle_error)
            self.ytdl_thread.start()
            self.thumbnail_frame.setVisible(True)
            self.videoWidget.setVisible(True)
            self.audioWidget.setVisible(True)
            self.ytdl = True
        else:
            self.items = {
                'type': 'single',
                'urls': [self.url],
                'filesize': size
            }
            if size != 0: 
                self.filesize.setText(format_bytes(size))
            else:
                self.filesize.setText('Unknown Sizes')
            self.save_input.setText(filename)
            self.download_button.setEnabled(True)
    
    def handle_download(self):
        if self.ytdl:
            if self.video_select.currentData() is not None and self.audio_select.currentData() is not None:
                self.items = {
                    'urls': [self.video_select.currentData(), self.audio_select.currentData()],
                    'type': 'ytdl'
                }
            else:
                self.items = {
                    'urls': [self.video_select.currentData() or self.audio_select.currentData()],
                    'type': 'single'
                    }
            size = (self.video_formats[self.video_select.currentIndex()][2] 
                    + self.audio_formats[self.audio_select.currentIndex()][2])
            self.items['filesize'] = size
            if size != 0: 
                self.filesize.setText(format_bytes(size))
            else:
                self.filesize.setText('Unknown Sizes')
        self.items['path'] = f"{self.main_window.config['save_path']}/{self.save_input.text()}"
        if os.path.exists(self.items['path']):
            overwrite = self.show_overwrite_dialog()
            if overwrite:
                self.main_window.download_manager.add_download(self.items)
                self.close()
            else:
                pass
        else:
            self.main_window.download_manager.add_download(self.items)
            self.close()


class FetchInfo(QThread):
    infoReady = Signal(bool, int, str)
    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        size = 0
        downloadable = False
        filename, ext = 'unknown', 'unknown'
        if is_urlDownloadable(self.url):
            size, filename = get_url_info(self.url)
            downloadable = True
        self.infoReady.emit(downloadable, size, filename)

class ThumbnailLoader(QThread):
    thumbnail_loaded = Signal(QPixmap)
    load_failed = Signal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            response = requests.get(self.url, stream=True, timeout=10)
            if response.status_code == 200:
                # Load image data
                img_data = response.content

                # Create QPixmap from image data
                pixmap = QPixmap()
                pixmap.loadFromData(img_data)

                if not pixmap.isNull():
                    self.thumbnail_loaded.emit(pixmap)
                else:
                    self.load_failed.emit("Invalid image data")
            else:
                self.load_failed.emit(f"HTTP Error: {response.status_code}")
        except Exception as e:
            self.load_failed.emit(f"Error: {str(e)}")