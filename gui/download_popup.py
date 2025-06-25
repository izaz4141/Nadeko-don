from PySide6.QtCore import Qt, Slot, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QDialog, QLineEdit,
    QFrame, QFileDialog, QMessageBox
)

from utils.helpers import (
    get_url_info,
    is_urlDownloadable, format_bytes
)
from network.ydl_thread import YTDL_Thread

import requests, os


class DownloadPopup(QDialog):
    finished = Signal()
    def __init__(self, parent, url):
        super(DownloadPopup, self).__init__(parent)
        self.setWindowTitle("Nadeko~don: Download Popup")
        self.main_window = parent
        self.url = url
        self.thumbnail_thread = None
        self.ytdl = False # Flag to track if the source is YTDL
        self.ytdl_thread = None
        self.info = None # Stores the YTDL info dictionary
        self.download_request = None # Stores the prepared download request for DownloadManager

        self.init_ui()
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self.urlInfo = InfoFetcher(url)
        self.urlInfo.infoReady.connect(self.get_urlInfo)
        self.urlInfo.start()

    def init_ui(self):
        main_layout = QVBoxLayout()
        sub_layout = QHBoxLayout()

        # Thumbnail display
        self.thumbnail_frame = QFrame()
        self.thumbnail_frame.setVisible(False)
        self.thumbnail_frame.setFrameShape(QFrame.Shape.StyledPanel)
        thumbnail_layout = QVBoxLayout(self.thumbnail_frame)
        self.thumbnail_label = QLabel("Loading...")
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

        self.filesize = QLabel("Loading...")
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
        """Opens a dialog to change the default download directory."""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Download Directory",
            self.main_window.config['save_path']
        )
        if directory:
            self.main_window.config['save_path'] = directory
            self.main_window.update_config() # Save the updated config
        self.saveDir_button.setText(self.main_window.config['save_path'])

    @Slot(dict)
    def handle_ytdl_video_info(self, info):
        """
        Handles the video information received from YTDL_Thread.
        Populates thumbnail, filename, and stream selection dropdowns.
        """
        self.info = info # Store the full info dictionary

        # Load thumbnail if available, otherwise display message
        if self.info.get('thumbnail'):
            self.load_thumbnail(self.info['thumbnail'])
        else:
            self.thumbnail_label.setText("Thumbnail not available")

        # Clear existing items in combo boxes
        self.audio_select.clear()
        self.video_select.clear()

        formats = self.info.get('formats', [])

        # Filter and sort video formats
        self.video_formats = []
        for f in reversed(formats):
            if f.get('vcodec') not in ['audio only', 'none'] and f.get('height'):
                resolution = f.get('height')
                vcodec = f.get('vcodec', '?')
                video_ext = f.get('video_ext', '') or f.get('ext', '')
                filesize = f.get('filesize') or f.get('filesize_approx') or 0
                fid = f.get('format_id')
                self.video_formats.append([
                    f.get('url'),
                    f"{resolution}p-{vcodec}-{video_ext}-{format_bytes(filesize)}",
                    filesize, fid
                ])

        # Filter and sort audio formats
        self.audio_formats = []
        for f in reversed(formats):
            if f.get('acodec') and f.get('vcodec') in ['audio only', 'none']:
                acodec = f.get('acodec', '?')
                audio_ext = f.get('audio_ext', '') or f.get('ext', '')
                filesize = f.get('filesize') or f.get('filesize_approx') or 0
                fid = f.get('format_id')
                self.audio_formats.append([
                    f.get('url'),
                    f"{acodec}-{audio_ext}-{format_bytes(filesize)}",
                    filesize, fid
                ])

        # Add "None" options for user flexibility
        self.video_formats.append([None, "None (No video)", 0, None])
        self.audio_formats.append([None, "None (No audio)", 0, None])

        for url, desc, _, _ in self.video_formats:
            self.video_select.addItem(desc, url)
        for url, desc, _, _ in self.audio_formats:
            self.audio_select.addItem(desc, url)

        # Set initial filename based on title and a default video extension
        # Use a default extension if no video format is initially available
        default_ext = self.video_formats[0][1].split('-')[2] if self.video_formats and self.video_formats[0][1] != "None (No video)" else (self.info.get('ext') or 'mp4')
        self.save_input.setText(f"{self.info.get('title', 'untitled')}.{default_ext}")

        self.video_select.setEnabled(True)
        self.audio_select.setEnabled(True)
        self.download_button.setEnabled(True)

        self.video_select.currentIndexChanged.connect(self.handle_selectUpdates)
        self.audio_select.currentIndexChanged.connect(self.handle_selectUpdates)

        # Trigger initial update to show correct size and filename
        self.handle_selectUpdates()

    @Slot()
    def handle_selectUpdates(self):
        """Updates the displayed filesize and suggested filename based on selected streams."""
        selected_video_url = self.video_select.currentData()
        selected_audio_url = self.audio_select.currentData()

        video_size = self.video_formats[self.video_select.currentIndex()][2] if selected_video_url else 0
        audio_size = self.audio_formats[self.audio_select.currentIndex()][2] if selected_audio_url else 0

        total_size = video_size + audio_size

        if total_size > 0:
            self.filesize.setText(format_bytes(total_size))
        else:
            self.filesize.setText('Unknown Sizes')

        # Determine the primary extension for the filename
        current_ext = ""
        if selected_video_url:
            current_ext = self.video_formats[self.video_select.currentIndex()][1].split('-')[2]
        elif selected_audio_url:
            current_ext = self.audio_formats[self.audio_select.currentIndex()][1].split('-')[1]
        else:
            # If neither video nor audio is selected, disable download button
            self.download_button.setDisabled(True)
            self.filesize.setText('No stream selected')
            return

        # Enable download button if at least one stream is selected
        self.download_button.setEnabled(True)

        # Update filename, preserving the base title
        base_title = self.info.get('title', 'untitled') if self.ytdl else os.path.splitext(self.save_input.text())[0]
        self.save_input.setText(f"{base_title}.{current_ext}")

    def show_overwrite_dialog(self, file_path):
        """
        Shows a dialog asking the user whether to resume, overwrite, or cancel a download.
        Returns "resume", "overwrite", or "cancel".
        """
        msg_box = QMessageBox(self)
        msg_box.setText(f"File '{os.path.basename(file_path)}' already exists.")
        msg_box.setInformativeText("Do you want to resume the download, overwrite it, or cancel?")
        msg_box.setIcon(QMessageBox.Question)

        resume_button = msg_box.addButton("Resume", QMessageBox.AcceptRole)
        overwrite_button = msg_box.addButton("Overwrite", QMessageBox.DestructiveRole)
        cancel_button = msg_box.addButton("Cancel", QMessageBox.RejectRole)

        msg_box.setDefaultButton(resume_button)

        msg_box.exec()

        if msg_box.clickedButton() == resume_button:
            return "resume"
        elif msg_box.clickedButton() == overwrite_button:
            return "overwrite"
        elif msg_box.clickedButton() == cancel_button:
            return "cancel"
        return "cancel" # Default to cancel

    def load_thumbnail(self, url):
        """Load thumbnail from URL using a separate thread."""
        if self.thumbnail_thread and self.thumbnail_thread.isRunning():
            self.thumbnail_thread.quit()
            self.thumbnail_thread.wait(1000)
            self.thumbnail_thread.deleteLater()

        self.thumbnail_thread = ThumbnailLoader(url)
        self.thumbnail_thread.thumbnail_loaded.connect(self.handle_thumbnail_loaded)
        self.thumbnail_thread.load_failed.connect(self.handle_thumbnail_error)
        self.thumbnail_thread.start()
        self.thumbnail_frame.setVisible(True) # Make frame visible once loading starts

    @Slot(QPixmap)
    def handle_thumbnail_loaded(self, pixmap):
        """Handle successful thumbnail loading."""
        scaled_pixmap = pixmap.scaled(
            self.thumbnail_label.width(),
            self.thumbnail_label.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.thumbnail_label.setPixmap(scaled_pixmap)
        self.thumbnail_thread.quit()
        self.thumbnail_thread.wait(1000)
        self.thumbnail_thread.deleteLater()

    @Slot(str)
    def handle_thumbnail_error(self, message):
        """Handle thumbnail loading errors."""
        self.thumbnail_label.setText(f"Error loading thumbnail: {message}")
        self.thumbnail_thread.quit()
        self.thumbnail_thread.wait(1000)
        self.thumbnail_thread.deleteLater()

    @Slot(bool, int, str)
    def get_urlInfo(self, downloadable, size, filename):
        """
        Receives information about the URL from InfoFetcher thread.
        Decides whether to treat it as a direct download or a YTDL source.
        """
        self.urlInfo.quit()
        self.urlInfo.wait(1000)
        self.urlInfo.deleteLater()

        if not downloadable:
            # It's not a simple downloadable file, try YTDL
            if self.ytdl_thread and self.ytdl_thread.isRunning():
                self.ytdl_thread.stop()
                self.ytdl_thread.wait(1000)
                self.ytdl_thread.deleteLater()

            self.ytdl_thread = YTDL_Thread(self.main_window.config, self.url)
            self.ytdl_thread.info_ready.connect(self.handle_ytdl_video_info)
            self.ytdl_thread.error.connect(self.handle_error)
            self.ytdl_thread.start()

            # Show YTDL specific UI elements
            self.thumbnail_frame.setVisible(True)
            self.videoWidget.setVisible(True)
            self.audioWidget.setVisible(True)
            self.ytdl = True # Set flag indicating this is a YTDL source
        else:
            # It's a direct downloadable file
            self.download_request = {
                'type': 'single',
                'items': [
                    {
                        'url': self.url,
                        'path': f"{self.main_window.config['save_path']}/{filename}",
                        'filesize': size
                    }
                ]
            }
            if size != 0:
                self.filesize.setText(format_bytes(size))
            else:
                self.filesize.setText('Unknown Sizes')
            self.save_input.setText(filename)
            self.download_button.setEnabled(True)

    def handle_error(self, err):
        self.main_window.logger.error(err)
        QMessageBox.warning(self, "Error",
            err
        )
        self.close()

    @Slot()
    def handle_download(self):
        """
        Prepares the final download request based on user selections and
        initiates the download via DownloadManager, handling overwrite/resume.
        """
        full_save_path = f"{self.main_window.config['save_path']}/{self.save_input.text()}"

        if self.ytdl:
            selected_video_url = self.video_select.currentData()
            selected_audio_url = self.audio_select.currentData()

            # # Format id of selected streams for yt-dlp download fallback
            video_id = self.video_formats[self.video_select.currentIndex()][3] if selected_video_url else None
            audio_id = self.audio_formats[self.audio_select.currentIndex()][3] if selected_audio_url else None
            video_size = self.video_formats[self.video_select.currentIndex()][2] if selected_video_url else 0
            audio_size = self.audio_formats[self.audio_select.currentIndex()][2] if selected_audio_url else 0

            urls_to_download = []
            formats_id = []
            temp_path = []
            total_size = []
            if selected_video_url:
                urls_to_download.append(selected_video_url)
                formats_id.append(video_id)
                temp_path.append('tmpv')
                total_size.append(video_size)
            if selected_audio_url:
                urls_to_download.append(selected_audio_url)
                formats_id.append(audio_id)
                temp_path.append('tmpa')
                total_size.append(audio_size)

            if not urls_to_download:
                QMessageBox.warning(self, "No Stream Selected", "Please select at least one video or audio stream to download.")
                return


            self.download_request = {
                'type': 'ytdl',
                'items': [
                    {
                        'url': urls_to_download[i],
                        'original_url': self.url,
                        'format_id': formats_id[i],
                        'path': f"{full_save_path}.{temp_path[i]}" if len(urls_to_download) > 1 else full_save_path,
                        'filesize': total_size[i]
                    } for i in range(len(urls_to_download))
                ]
            }

            if sum(total_size) > 0:
                self.filesize.setText(format_bytes(sum(total_size)))
            else:
                self.filesize.setText('Unknown Sizes')

        elif self.download_request['type'] == 'single':
            self.download_request['items'][0]['path'] = full_save_path


        # Handle file existence and user choice (resume/overwrite)
        if os.path.exists(full_save_path):
            action = self.show_overwrite_dialog(full_save_path)

            if action == "overwrite":
                try:
                    print(f"Overwriting existing file: {full_save_path}")
                    os.remove(full_save_path) # Delete the existing file if user chose to overwrite
                    if os.path.exists(f"{full_save_path}.metadata"):
                        os.remove(f"{full_save_path}.metadata")
                except OSError as e:
                    QMessageBox.critical(self, "File Error", f"Could not remove existing file: {e}")
                    return # Stop if file cannot be removed
            elif action == "cancel":
                return # User cancelled, do nothing
            # If action is "resume", proceed normally; DownloadTask will handle resume logic

        # Proceed with adding download if not cancelled or if overwrite was successful
        if self.download_request:
            self.main_window.download_manager.add_download(self.download_request)
            self.finished.emit()
            self.close()


class InfoFetcher(QThread):
    """
    A QThread to fetch initial URL information (downloadable, size, filename)
    without blocking the UI.
    """
    infoReady = Signal(bool, int, str)

    def __init__(self, url):
        super().__init__()
        self.url = url
        self.setObjectName("InfoFetcher")

    def run(self):
        size = 0
        downloadable = False
        filename = 'unknown' # Default filename

        try:
            if is_urlDownloadable(self.url):
                size, filename = get_url_info(self.url)
                downloadable = True
        except Exception as e:
            print(f"Error fetching URL info for {self.url}: {e}")
            # Keep downloadable as False and size/filename as defaults on error
            pass

        self.infoReady.emit(downloadable, size, filename)

class ThumbnailLoader(QThread):
    """
    A QThread to download a thumbnail image from a URL without blocking the UI.
    """
    thumbnail_loaded = Signal(QPixmap)
    load_failed = Signal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url
        self.setObjectName("ThumbnailLoader")

    def run(self):
        try:
            response = requests.get(self.url, stream=True, timeout=10)
            response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

            img_data = response.content
            pixmap = QPixmap()
            pixmap.loadFromData(img_data)

            if not pixmap.isNull():
                self.thumbnail_loaded.emit(pixmap)
            else:
                self.load_failed.emit("Invalid image data received from URL.")
        except requests.exceptions.RequestException as e:
            self.load_failed.emit(f"Network error loading thumbnail: {e}")
        except Exception as e:
            self.load_failed.emit(f"Error loading thumbnail: {str(e)}")
