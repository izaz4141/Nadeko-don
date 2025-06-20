from PySide6.QtCore import QThread, Signal
import json, yt_dlp

class YTDL_Thread(QThread):
    progress_updated = Signal(dict)
    download_complete = Signal(bool, str)
    info_ready = Signal(dict)
    error = Signal(str)

    def __init__(self, config, url, format_code=None, save_path=None):
        super().__init__()
        self.setObjectName("YDLThread")
        self.config = config
        self.url = url
        self.path = save_path or config['save_path']
        self.format_code = format_code
        self.running = True
        self.ytdl_opts = {
            'dump_single_json': True,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'paths': {'home': self.path},
            'progress_hooks': [self.ytdl_hook]
        }
        if config['yt_cookies'] != "":
            self.ytdl_opts['cookiefile'] = config['yt_cookies']

    def run(self):
        try:
            # Get video info
            if not self.format_code:
                with yt_dlp.YoutubeDL(self.ytdl_opts) as ydl:
                    info = ydl.extract_info(self.url, download=False)
                    info = ydl.sanitize_info(info)
                self.info_ready.emit(info)
                return

            self.ytdl_opts['format'] = self.format_code
            self.ytdl_opts['dump_single_json'] = False
            with yt_dlp.YoutubeDL(self.ytdl_opts) as ydl:
                ydl.download(self.url)
            if self.running:
                self.download_complete.emit("Download complete!")

        except Exception as e:
            self.error.emit(str(e))

    def ytdl_hook(self, d):
        """Progress hook called by yt-dlp during download"""
        if not self.running:
            raise yt_dlp.DownloadError("Download cancelled")

        if d['status'] == 'downloading':
            self.progress_updated.emit(d)

    def stop(self):
        self.running = False
