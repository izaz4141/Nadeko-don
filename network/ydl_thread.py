from PySide6.QtCore import QThread, Signal
import os, json, subprocess, yt_dlp

class YTDL_Thread(QThread):
    progress_updated = Signal(int)
    download_complete = Signal(bool, str)
    info_ready = Signal(dict)
    error = Signal(str)

    def __init__(self, main_window, url, format_code=None):
        super().__init__()
        self.main_window = main_window
        self.url = url
        self.download_dir = main_window.config['save_path']
        self.format_code = format_code
        self.running = True
        self.ytdl_opts = {
            'dump_single_json': True,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'paths': {'home': self.download_dir},
            'progress_hooks': [self.ytdl_hook]
        }
        if main_window.config['yt_cookies'] != "":
            self.ytdl_opts['cookiefile'] = main_window.config['yt_cookies']

    def run(self):
        try:
            # Get video info
            if not self.format_code:
                with yt_dlp.YoutubeDL(self.ytdl_opts) as ydl:
                    info = ydl.extract_info(self.url, download=False)
                    info = ydl.sanitize_info(info)
                # with open("info.log", "w") as out_file:
                #     json.dump(info, out_file, indent=4)
                self.info_ready.emit(info)
                return

            self.ytdl_opts['format'] = self.format_code
            self.ytdl_opts['dump_single_json'] = False
            with yt_dlp.YoutubeDL(self.ytdl_opts) as ydl:
                download = ydl.download(self.url)
            if self.running:
                self.download_complete.emit("Download complete!")

        except Exception as e:
            self.error.emit(str(e))

    def ytdl_hook(self, d):
        """Progress hook called by yt-dlp during download"""
        if not self.running:
            raise yt_dlp.DownloadError("Download cancelled")
            
        if d['status'] == 'downloading':
            if d.get('total_bytes'):
                progress = d['downloaded_bytes'] / d['total_bytes'] * 100
            elif d.get('total_bytes_estimate'):
                progress = d['downloaded_bytes'] / d['total_bytes_estimate'] * 100
            else:
                progress = 0
                
            self.progress_updated.emit(int(progress))

    def stop(self):
        self.running = False

