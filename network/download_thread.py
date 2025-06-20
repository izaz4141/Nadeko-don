from PySide6.QtCore import (
    QThread, QObject, Signal, QMutex,
    QMutexLocker, Slot, QWaitCondition
)
from PySide6.QtWidgets import QSystemTrayIcon, QMessageBox

from utils.ffmpeg import combine_video_audio, check_ffmpeg_in_path
from utils.helpers import is_m3u8_url, extract_size_info, pre_allocate_file
from utils.timer import ProgressTimer

import os, time, requests, subprocess, threading, math, json
from collections import deque
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class DownloadTask(QObject):
    """
    Manages a single download task, supporting HTTP multi-part downloads,
    HLS streams via FFmpeg, and YTDL-style video/audio combining.
    It encapsulates the state and logic for a single download.
    """
    status_changed = Signal(str)
    finished = Signal(QObject)
    error_occurred = Signal(QObject, str)

    def __init__(self, items, max_connections=8):
        """
        Initializes a DownloadTask.

        Args:
            items (dict): A dictionary containing download parameters:
                          - 'urls': A list of URLs to download (single URL for HTTP,
                                    video/audio pair for YTDL).
                          - 'path': The desired save path for the downloaded file.
                          - 'type': 'ytdl' or 'http'.
            max_connections (int): Maximum concurrent connections for multi-part downloads.
        """
        super().__init__()
        self.items = items
        self.urls = items['urls']
        self.save_path = items['path']
        self.basename = os.path.basename(items['path'])
        self.max_connections = max_connections

        self.status = "Queued"
        self.downloaded = 0
        self.total_size = 0
        self.speed = 0
        self.timer = ProgressTimer()
        self.history = deque(maxlen=60)

        self._paused = False
        self._canceled = False

        # Mutex for protecting shared state within this DownloadTask instance.
        # This state is accessed by the worker thread (running self.download) and the
        # GUI thread (via calls from DownloadManager like pause/resume/cancel or UI queries).
        self.mutex = QMutex()
        # Used with self.mutex for pause/resume functionality.
        self.condition = QWaitCondition()

        self.ytdl = items['type'] == 'ytdl'
        self.start_time = None

        self._current_allowed_speed_bps = float('inf')

        self.part_threads = []
        self.part_info = []
        # Mutex for protecting self.part_info list when multiple internal
        # part-downloading threads access it in _download_multiThreads.
        self.part_lock = threading.Lock()
        self.part_cancel_event = threading.Event()
        self.part_pause_event = threading.Event()
        self.part_pause_condition = threading.Condition()
        # Mutex for protecting self.downloaded and self.history
        # when multiple internal part-downloading threads update it.
        self.progress_lock = threading.Lock()
        self.completed_parts = 0

        self.session = self.create_session(["GET", "HEAD"])

    @Slot(float)
    def _update_allowed_speed(self, new_speed_bps: float):
        """
        Slot to receive and update the task's individual allowed download speed.
        Called by the DownloadManager (GUI thread) for global speed distribution.
        Requires mutex to protect _current_allowed_speed_bps from concurrent access.
        """
        with QMutexLocker(self.mutex):
            self._current_allowed_speed_bps = new_speed_bps

    def start_download(self):
        """
        Sets the task status to 'Downloading' and clears control flags.
        Accessed by DownloadManager (GUI thread). Requires mutex.
        """
        with QMutexLocker(self.mutex):
            self._set_status("Downloading")
            self._paused = False
            self._canceled = False

    def pause(self):
        """
        Pauses the download task.
        Accessed by DownloadManager (GUI thread). Requires mutex.
        """
        with QMutexLocker(self.mutex):
            if self.status == "Downloading":
                self._paused = True
                self._set_status("Paused")
                self.part_pause_event.set()
                with self.part_pause_condition:
                    self.part_pause_condition.notify_all()
                self.timer.pause()

    def resume(self):
        """
        Resumes a paused download task.
        Accessed by DownloadManager (GUI thread). Requires mutex.
        """
        with QMutexLocker(self.mutex):
            if self.status in ["Paused", "Queued"]:
                self._paused = False
                self._set_status("Downloading")
                self.condition.wakeAll()
                self.part_pause_event.clear()
                with self.part_pause_condition:
                    self.part_pause_condition.notify_all()
                self.timer.resume()

    def cancel(self):
        """
        Cancels the download task.
        Accessed by DownloadManager (GUI thread) and internal download parts. Requires mutex.
        """
        with QMutexLocker(self.mutex):
            if not self._canceled:
                self._canceled = True
                self._set_status("Canceled")
                self.condition.wakeAll()
                self.part_cancel_event.set()
                with self.part_pause_condition:
                    self.part_pause_condition.notify_all()
                self.timer.stop()

    def create_session(self, methods: list):
        """
        Creates a requests.Session with a retry strategy for robust HTTP requests.
        """
        session = requests.Session()
        retry_strategy = Retry(
            total=5,
            backoff_factor=10,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=methods
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def download(self):
        """
        Main download logic for the task. This method is run in a separate thread.
        Handles YTDL combining, HLS streaming, and multi-part HTTP downloads.
        """
        self.timer.start()
        try:
            for i, url in enumerate(self.urls):
                # Mutex needed here to safely read _canceled flag, which can be set by GUI thread.
                with QMutexLocker(self.mutex):
                    if self._canceled:
                        return

                save_path_for_url = self._get_save_path(i)
                self._download_url(url, save_path_for_url)

            if self.ytdl:
                self._combine_ytdl_files()

            self.total_size = self._get_existing_size(self.save_path)

            self._handle_success()

        except Exception as e:
            print(f"[{time.time():.2f}] Task '{self.basename}': Unhandled error in download(): {e}")
            self._handle_error(e)

    def _set_status(self, status):
        """
        Updates the task's status and emits the status_changed signal.
        Accessed by both worker and GUI threads.
        """
        self.status = status
        self.status_changed.emit(status)

    def _get_save_path(self, index):
        """
        Determines the specific save path for a given URL index.
        For YTDL type downloads, it appends '.video' or '.audio' extensions.
        """
        if self.ytdl:
            file_type = 'video' if index == 0 else 'audio'
            return f"{self.save_path}.{file_type}"
        return self.save_path

    def _combine_ytdl_files(self):
        """
        Combines downloaded video and audio files (for YTDL tasks).
        This is an internal, sequential operation within the worker thread.
        """
        video_path = f"{self.save_path}.video"
        audio_path = f"{self.save_path}.audio"
        if os.path.exists(video_path) and os.path.exists(audio_path):
            try:
                combine_video_audio(video_path, audio_path, self.save_path)
                os.remove(video_path)
                os.remove(audio_path)
            except Exception as e:
                raise Exception(f"Failed to combine YTDL video and audio: {e}")
        elif os.path.exists(video_path) and not os.path.exists(audio_path):
            os.rename(video_path, self.save_path)
        elif os.path.exists(audio_path) and not os.path.exists(video_path):
            os.rename(audio_path, self.save_path)


    def _handle_success(self):
        """
        Sets task status to 'Completed' and emits the finished signal.
        Accessed by worker thread, modifies shared state (status). Requires mutex.
        """
        with QMutexLocker(self.mutex):
            if not self._canceled:
                self.timer.stop()
                self._set_status("Completed")
                self.finished.emit(self)

    def _handle_error(self, exception):
        """
        Sets task status to 'ERROR' and emits the error_occurred signal.
        Accessed by worker thread, modifies shared state (status). Requires mutex.
        """
        with QMutexLocker(self.mutex):
            self.timer.stop()
            self._set_status("ERROR")
            self.error_occurred.emit(self, str(exception))

    def _download_url(self, url, save_path):
        """
        Dispatches to the appropriate download method based on whether the URL
        is an HLS (M3U8) stream or a regular HTTP file.
        """
        if is_m3u8_url(url):
            if check_ffmpeg_in_path():
                self._download_hls_stream(url, save_path)
            else:
                Exception("Error: 'ffmpeg' executable not found in system PATH. Please install FFmpeg or add it to your PATH.")
        else:
            self._download_http_file(url, save_path)

    def _download_http_file(self, url, save_path):
        """
        Handles HTTP file downloads. It first retrieves metadata, then prepares the file,
        and finally dispatches to either multi-threaded or single-threaded download logic.
        """
        self._get_url_metadata(url)
        self._prepare_file(save_path)

        if self.status == "Completed":
            return

        if self.is_multithreaded:
            self._download_multiThreads(url, save_path)
        else:
            self._download_single_http(url, save_path)

    def _download_single_http(self, url, save_path):
        """
        Downloads a file using a single HTTP connection, supporting resume functionality.
        Contains mutex for checking shared control flags (_paused, _canceled) and updating progress.
        """
        existing_size = self._get_existing_size(save_path)
        headers = {}
        mode = 'wb'

        if self.accept_ranges_support:
            if existing_size > 0 and existing_size < self.total_size:
                headers = {"Range": f"bytes={existing_size}-"}
                mode = 'ab'
            elif existing_size >= self.total_size and self.total_size > 0:
                self.downloaded = existing_size
                self._set_status("Completed")
                return
            elif existing_size > 0 and self.total_size == 0:
                # If total_size is 0 but we have an existing size, try to resume
                # assuming the server supports ranges for streaming downloads.
                headers = {"Range": f"bytes={existing_size}-"}
                mode = 'ab'

        try:
            with self.session.get(url, headers=headers, stream=True, timeout=30) as response:
                response.raise_for_status()

                # If server sends full content (200) despite a range request, start fresh.
                if response.status_code == 200 and headers:
                    mode = 'wb'
                    self.downloaded = 0

                with open(save_path, mode) as file:
                    for chunk in response.iter_content(chunk_size=1024*256):
                        if not chunk:
                            continue

                        # Mutex needed to safely read _paused and _canceled flags
                        # and for condition.wait (which releases mutex while waiting).
                        with QMutexLocker(self.mutex):
                            while self._paused or self._canceled:
                                if self._canceled:
                                    return
                                self.condition.wait(self.mutex) # Releases mutex while waiting.

                        chunk_len = len(chunk)
                        start_write_time = time.time()
                        file.write(chunk)
                        chunk_duration = time.time() - start_write_time

                        self._update_download_progress(chunk_len)

                        if self._current_allowed_speed_bps > 0:
                            self._limit_speed(chunk_len, chunk_duration, self._current_allowed_speed_bps)
        except requests.exceptions.RequestException as e:
            raise Exception(f"HTTP download failed: {e}")
        except OSError as e:
            raise Exception(f"File writing error during HTTP download: {e}")

    def _get_existing_size(self, save_path):
        """Returns the size of an existing file at save_path, or 0 if it doesn't exist."""
        size = os.path.getsize(save_path) if os.path.exists(save_path) else 0
        return size

    def _get_url_metadata(self, url):
        """
        Performs a HEAD request to get file size and check for 'Accept-Ranges' support.
        If metadata retrieval fails, it defaults to unknown size and no range support.
        """
        try:
            response = self.session.head(url, allow_redirects=True, timeout=30)
            response.raise_for_status()
            self.total_size = int(response.headers.get('content-length', 0))
            self.accept_ranges_support = response.headers.get('accept-ranges', '').lower() == 'bytes'
        except Exception as e:
            self.total_size = 0
            self.accept_ranges_support = False
            print(f"[{time.time():.2f}] Task '{self.basename}': Warning: Failed to get URL metadata, assuming 0 total size and no range support: {e}")

    def _prepare_file(self, save_path):
        """
        Prepares the target file for download. This involves creating directories,
        handling existing files (especially for forced overwrites), loading/saving
        metadata for resumed multi-part downloads, and pre-allocating file space.
        It also determines if the download should be multi-threaded or single-threaded.
        """
        self.current_save_path = save_path
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        existing_file_size_on_disk = self._get_existing_size(save_path)

        metadata = self._load_metadata(save_path)

        self.is_multithreaded = False
        self.downloaded = 0

        min_part_size = 1024 * 1024 # 1 MB minimum size for a part

        # Condition for enabling multi-threaded download:
        # 1. Server must support byte-range requests.
        # 2. Maximum allowed connections must be greater than 1.
        # 3. Total file size must be known and large enough for at least one part (>= min_part_size).
        if (self.accept_ranges_support and
            self.max_connections > 1 and
            self.total_size >= min_part_size):

            self.is_multithreaded = True

            # Determine the number of parts:
            # - Ensure at least 1 part.
            # - Calculate parts based on total_size / min_part_size, rounded up.
            # - Cap the number of parts at the maximum allowed connections.
            ideal_parts_by_size = max(1, math.ceil(self.total_size / min_part_size))
            self.num_parts = min(self.max_connections, ideal_parts_by_size)

            if metadata and 'parts' in metadata and metadata.get('total_size') == self.total_size:
                # Resume multi-threaded download from existing metadata
                self.part_info = metadata['parts']
                self.completed_parts = sum(1 for p in self.part_info if p['status'] == 'complete')
                self.downloaded = sum(p['downloaded'] for p in self.part_info if p['status'] != 'failed')

                if existing_file_size_on_disk != self.total_size:
                    # Re-allocate if file size on disk doesn't match expected total size
                    pre_allocate_file(save_path, self.total_size)
            else:
                # Start new multi-threaded download
                pre_allocate_file(save_path, self.total_size)
                self.downloaded = 0
                self.part_info = []
                self.completed_parts = 0
                part_size = self.total_size // self.num_parts # Integer division for base part size

                for i in range(self.num_parts):
                    start = i * part_size
                    end = (i + 1) * part_size - 1
                    if i == self.num_parts - 1: # Ensure the last part covers till the very end of the file
                        end = self.total_size - 1

                    self.part_info.append({
                        'start': start, 'end': end, 'downloaded': 0, 'status': 'pending', 'retries': 0
                    })
                self._save_metadata(save_path)
        else:
            # Fallback to single-threaded download if multi-threading conditions are not met:
            # (e.g., no range support, max_connections <= 1, total_size unknown, or total_size too small).
            self.is_multithreaded = False
            if self.total_size > 0 and existing_file_size_on_disk > 0 and \
               existing_file_size_on_disk < self.total_size and self.accept_ranges_support:
                # Resume single-threaded download if file is partially downloaded and ranges are supported
                self.downloaded = existing_file_size_on_disk
            elif existing_file_size_on_disk > 0 and self.total_size == 0 and self.accept_ranges_support:
                # Resume single-threaded download for unknown total size (e.g., live stream with range support)
                self.downloaded = existing_file_size_on_disk
            else:
                # If file exists and is larger than expected, clear it.
                if os.path.exists(save_path) and (self.total_size > 0 and existing_file_size_on_disk > self.total_size):
                    try:
                        os.remove(save_path)
                    except OSError as e:
                        raise Exception(f"Failed to clear existing file {save_path} for single-threaded download: {e}")

                # Create a new empty file if it doesn't exist
                if not os.path.exists(save_path):
                    open(save_path, 'wb').close()
                self.downloaded = 0

            self._delete_metadata(save_path)

        # If the file is already fully downloaded (e.g., from a previous run), mark as completed.
        if self.total_size > 0 and self.downloaded >= self.total_size:
            self._set_status("Completed")
            self._delete_metadata(save_path) # Clean up metadata for completed downloads
            return

    def _save_metadata(self, save_path):
        """Saves download metadata (especially part info for multi-threaded) to a file for resuming."""
        metadata_path = f"{save_path}.metadata"
        data = {
            'total_size': self.total_size,
            'num_parts': len(self.part_info),
            'parts': self.part_info,
            'downloaded': self.downloaded,
            'timestamp': time.time()
        }
        try:
            with open(metadata_path, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"[{time.time():.2f}] Task '{self.basename}': Failed to save metadata for {self.basename}: {e}")

    def _load_metadata(self, save_path):
        """Loads download metadata from file if available, handling potential corruption."""
        metadata_path = f"{save_path}.metadata"
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
                    return metadata
            except (json.JSONDecodeError, OSError) as e:
                print(f"[{time.time():.2f}] Task '{self.basename}': Error loading metadata for {self.basename}, deleting corrupted file: {e}")
                self._delete_metadata(save_path)
        return None

    def _delete_metadata(self, save_path):
        """Deletes the metadata file associated with a download."""
        metadata_path = f"{save_path}.metadata"
        if os.path.exists(metadata_path):
            try:
                os.remove(metadata_path)
            except OSError as e:
                print(f"[{time.time():.2f}] Task '{self.basename}': Failed to delete metadata file for {self.basename}: {e}")

    def _download_multiThreads(self, url, save_path):
        """
        Manages the multi-threaded download process, spawning and monitoring individual
        part threads. It handles pausing, resuming, and cancellation of the overall download.
        """
        self.part_threads = []
        self.part_cancel_event.clear()
        self.part_pause_event.clear()

        # Pre-allocation is handled in _prepare_file, ensuring it's done only if total_size is known.
        if self.total_size > 0 and (not os.path.exists(save_path) or os.path.getsize(save_path) != self.total_size):
            pre_allocate_file(save_path, self.total_size)

        # Create and start a thread for each part that isn't already complete or failed.
        for part_index in range(len(self.part_info)):
            part = self.part_info[part_index]
            with self.part_lock: # Protects part_info modification during iteration
                if part['status'] != 'complete' and part['status'] != 'failed':
                    thread = threading.Thread(
                        target=self._download_part,
                        args=(url, save_path, part_index)
                    )
                    thread.daemon = True # Allow main program to exit even if threads are running
                    self.part_threads.append(thread)

        for thread in self.part_threads:
            thread.start()

        last_save_time = time.time()
        save_interval = 5 # Interval for saving metadata during multi-part download

        # Main loop to monitor part threads and handle global pause/cancel events.
        while True:
            time.sleep(0.1) # Small delay to avoid busy-waiting

            if self.part_cancel_event.is_set() or self._canceled: # Check for cancellation
                break

            if self.part_pause_event.is_set() or self._paused: # Check for pause
                self._save_metadata(save_path) # Save state before pausing
                with self.part_pause_condition:
                    # Wait, releasing the mutex, until resumed or canceled.
                    while (self.part_pause_event.is_set() or self._paused) and not (self.part_cancel_event.is_set() or self._canceled):
                        self.part_pause_condition.wait()
                if self.part_cancel_event.is_set() or self._canceled: # Check cancellation again after waking up
                    break

            current_time = time.time()
            if current_time - last_save_time >= save_interval:
                self._save_metadata(save_path) # Periodically save progress
                last_save_time = current_time

            failed_parts = [p for p in self.part_info if p['status'] == 'failed']
            if failed_parts:
                print(f"[{time.time():.2f}] Task '{self.basename}': Detected {len(failed_parts)} failed parts.")
                raise Exception(f"{len(failed_parts)} part(s) failed to download. Errors: {'; '.join(p.get('error', 'Unknown') for p in failed_parts)}")

            # Filter out completed/exited threads
            self.part_threads = [t for t in self.part_threads if t.is_alive()]
            if not self.part_threads: # All parts completed or exited
                break

        # Ensure all threads are joined (or timed out) before exiting.
        for thread in self.part_threads:
            if thread.is_alive():
                thread.join(timeout=1)

        self._save_metadata(save_path) # Final save of metadata

        failed_parts = [p for p in self.part_info if p['status'] == 'failed']
        if not failed_parts and not (self.part_cancel_event.is_set() or self._canceled):
            self._delete_metadata(save_path) # Clean up metadata if successful

    def _download_part(self, url, save_path, part_index):
        """
        Downloads a specific byte range (part) of the file in a separate thread.
        It uses `part_lock` for part-specific info and `progress_lock` for global downloaded bytes.
        Checks for global cancellation and pause flags.
        """
        part = self.part_info[part_index]

        with self.part_lock: # Protects access to this part's status
            if part['status'] == 'complete':
                return

        start_byte_offset = part['start'] + part['downloaded']
        end_byte_offset = part['end']

        # Construct Range header for partial content request.
        headers = {'Range': f'bytes={start_byte_offset}-{end_byte_offset}'}

        try:
            with self.session.get(url, headers=headers, stream=True, timeout=30) as response:
                response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

                # Check if server responded with full content (200) despite a range request.
                if response.status_code == 200 and 'Range' in headers:
                    raise Exception(f"Server returned full content (200) instead of partial (206) for range request on part {part_index}.")
                # Ensure the response is 206 Partial Content.
                elif response.status_code != 206:
                    raise Exception(f"Server response {response.status_code} not expected for part {part_index}. Expected 206.")

                with open(save_path, 'r+b') as f:
                    f.seek(start_byte_offset) # Seek to the correct position in the file

                    for chunk in response.iter_content(chunk_size=1024*256):
                        if not chunk:
                            continue

                        if self.part_cancel_event.is_set() or self._canceled:
                            return

                        if self.part_pause_event.is_set() or self._paused:
                            with self.part_pause_condition:
                                # Wait until resumed or canceled.
                                while (self.part_pause_event.is_set() or self._paused) and not (self.part_cancel_event.is_set() or self._canceled):
                                    self.part_pause_condition.wait()
                            if self.part_cancel_event.is_set() or self._canceled:
                                return

                        chunk_len = len(chunk)
                        start_write_time = time.time()
                        f.write(chunk)
                        chunk_duration = time.time() - start_write_time

                        # Protect self.downloaded and self.history with progress_lock.
                        with self.progress_lock:
                            self.downloaded += chunk_len
                            self.history.append((time.time(), self.downloaded))

                        # Protect part['downloaded'] with part_lock.
                        with self.part_lock:
                            part['downloaded'] += chunk_len

                        # Apply speed limit per thread.
                        if self._current_allowed_speed_bps > 0 and len(self.part_threads) > 0:
                            per_thread_limit = self._current_allowed_speed_bps / len(self.part_threads)
                            self._limit_speed(chunk_len, chunk_duration, per_thread_limit)

            with self.part_lock: # Update part status to complete.
                part['status'] = 'complete'
                self.completed_parts += 1

        except Exception as e:
            with self.part_lock: # Mark part as failed and store error.
                part['status'] = 'failed'
                part['error'] = str(e)
            self.cancel() # Propagate cancellation to the main task.
            print(f"[{time.time():.2f}] Task '{self.basename}': Part {part_index} failed: {e}")

    def _update_download_progress(self, chunk_size):
        """
        Updates the global downloaded bytes count and adds current progress to history.
        Used for single-threaded/HLS downloads. Requires mutex for `self.downloaded`.
        """
        with QMutexLocker(self.mutex):
            self.downloaded += chunk_size
        self.history.append((time.time(), self.downloaded))

    def _limit_speed(self, chunk_size, chunk_duration, max_speed_bps: float):
        """
        Limits the download speed by pausing execution if data is transferred too fast.
        Calculates the necessary sleep time to match the `max_speed_bps`.
        """
        if max_speed_bps <= 0 or max_speed_bps == float('inf'):
            return

        desired_time = chunk_size / max_speed_bps
        if chunk_duration < desired_time:
            sleep_time = desired_time - chunk_duration
            time.sleep(max(0, sleep_time))


    def _download_hls_stream(self, url, save_path):
        """
        Downloads an HLS (M3U8) stream using an external FFmpeg process.
        Monitors FFmpeg's output for progress and handles pause/cancel events.
        """
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except OSError as e:
                raise Exception(f"Failed to clear existing file for HLS download: {e}")

        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        ext = os.path.splitext(self.save_path)[1].lstrip('.')
        if not ext:
            ext = 'mp4'
            print(f"[{time.time():.2f}] Task '{self.basename}': Warning: No file extension found for HLS URL, defaulting to .{ext}")

        cmd = self._build_ffmpeg_command(url, save_path, ext)

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # Redirect stderr to stdout to capture FFmpeg progress
            universal_newlines=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )

        try:
            self._monitor_ffmpeg_process(process, save_path)
        except Exception as e:
            print(f"[{time.time():.2f}] Task '{self.basename}': HLS download failed during monitoring: {e}")
            self._cleanup_ffmpeg_process(process, save_path)
            raise Exception(f"HLS download failed during monitoring: {e}")
        finally:
            self._cleanup_ffmpeg_process(process, save_path)

    def _build_ffmpeg_command(self, url, save_path, ext):
        """Constructs the FFmpeg command for HLS download based on URL and desired output path/extension."""
        cmd = [
            'ffmpeg',
            '-i', url,
            '-c', 'copy', # Copy audio/video streams without re-encoding
            '-f', ext,    # Force output format
            save_path
        ]
        if ext == 'mp4':
            # Specific bitstream filter for AAC audio when outputting to MP4.
            cmd.extend(['-bsf:a', 'aac_adtstoasc'])
        return cmd

    def _monitor_ffmpeg_process(self, process, save_path):
        """
        Monitors FFmpeg's stdout/stderr output to extract progress information.
        It also checks for global pause/cancel flags and manages the FFmpeg process state.
        """
        while True:
            # Mutex needed to safely read _canceled and _paused flags, and for condition.wait.
            with QMutexLocker(self.mutex):
                if self._canceled:
                    process.terminate() # Terminate FFmpeg process on cancellation
                    return
                if self._paused:
                    self.condition.wait(self.mutex) # Wait while paused, releasing mutex
                    if self._canceled: # Check cancellation again after waking up
                        process.terminate()
                        return

            line = process.stdout.readline()
            if not line and process.poll() is not None: # Process exited and no more output
                break

            if line:
                self._process_ffmpeg_output(line) # Parse FFmpeg output for progress

        self._check_ffmpeg_exit_code(process) # Check FFmpeg's final exit code

    def _process_ffmpeg_output(self, line):
        """Parses a line of FFmpeg output to extract downloaded size and update progress."""
        line = line.strip()
        try:
            size, unit = extract_size_info(line)
            if size and unit:
                size_val = self._convert_to_bytes(size, unit)
                self._update_download_progress(size_val)
        except (ValueError, TypeError):
            # Ignore lines that don't contain recognizable size info
            pass

    def _convert_to_bytes(self, size, unit):
        """Converts a size value with its unit (e.g., 'MB', 'GB') to bytes."""
        unit = unit.lower()
        multipliers = {
            'gb': 1024**3, 'gib': 1024**3,
            'mb': 1024**2, 'mib': 1024**2,
            'kb': 1024,    'kib': 1024,
            'b': 1
        }
        return float(size) * multipliers.get(unit, 1)

    def _check_ffmpeg_exit_code(self, process):
        """
        Checks FFmpeg's final exit code. If non-zero and not due to cancellation,
        it raises an exception indicating an FFmpeg failure.
        """
        retcode = process.wait()
        if retcode != 0:
            with QMutexLocker(self.mutex):
                if self._canceled:
                    print(f"[{time.time():.2f}] Task '{self.basename}': FFmpeg process for {self.basename} was terminated by user (retcode {retcode}).")
                    return
            raise Exception(f"FFmpeg failed with code {retcode} for {self.basename}")

    def _cleanup_ffmpeg_process(self, process, save_path):
        """
        Ensures the FFmpeg process is terminated.
        If the download was canceled, it also attempts to remove the partially downloaded file.
        """
        if process.poll() is None: # Check if process is still running
            try:
                process.terminate() # Request graceful termination
                process.wait(timeout=5) # Wait for a short period
                if process.poll() is None:
                    process.kill() # Force kill if still running
                    print(f"[{time.time():.2f}] Task '{self.basename}': Force killed FFmpeg process.")
            except OSError as e:
                print(f"[{time.time():.2f}] Task '{self.basename}': Error terminating FFmpeg process for {self.basename}: {e}")

        with QMutexLocker(self.mutex):
            if self._canceled and os.path.exists(save_path):
                try:
                    os.remove(save_path) # Remove partial file on cancellation
                    print(f"[{time.time():.2f}] Task '{self.basename}': Removed canceled HLS file {save_path}.")
                except OSError as e:
                    print(f"[{time.time():.2f}] Task '{self.basename}': Failed to remove canceled HLS file {save_path}: {e}")


class DownloadWorker(QThread):
    """
    A simple QThread subclass designed to run a DownloadTask in a separate thread.
    This offloads the heavy downloading work from the main GUI thread.
    """
    def __init__(self, task: DownloadTask):
        """Initializes the worker with a DownloadTask instance."""
        super().__init__()
        self.task = task
        self.setObjectName("DownloadWorker")

    def run(self):
        """
        Executes the DownloadTask's main download method.
        The DownloadTask itself handles its internal state, signals, and error reporting.
        """
        self.task.download()


class DownloadManager(QObject):
    """
    Manages a queue of DownloadTask objects, starting them up to a maximum
    number of concurrent workers. It handles task completion and error events
    and distributes global speed limits among active tasks.
    """
    # Signal emitted when the queue state changes, carrying a snapshot of task data.
    queue_updated = Signal(list)
    task_finished = Signal(DownloadTask)

    def __init__(self, parent):
        """Initializes the DownloadManager with a reference to the main application window (parent)."""
        super().__init__()
        self.tasks = deque() # Queue of DownloadTask objects
        self.active_tasks = {} # Dictionary of currently active workers, indexed by task ID
        self.paused_tasks = {} # Dictionary of currently paused workers, indexed by task ID
        # This mutex protects `self.tasks` and `self.active_tasks` from concurrent access
        # by multiple threads (e.g., when adding tasks from GUI, or handling task completion).
        self.mutex = QMutex()
        self.main_window = parent # Reference to the main application window

        with QMutexLocker(self.mutex):
            self._recalculate_and_distribute_speed_limits()

    def _get_current_tasks_data(self):
        """Gathers a snapshot of data for all tasks, for emission via signal. Assumes mutex is held."""
        tasks_data = []
        for task in self.tasks:
            tasks_data.append({
                'basename': task.basename,
                'downloaded': task.downloaded,
                'total_size': task.total_size,
                'path': task.save_path,
                'status': task.status,
                'timer': task.timer,
                'history': list(task.history) # Send a copy of history
            })
        return tasks_data

    def add_download(self, download_request: dict):
        """
        Adds one or more download tasks based on the provided request structure.
        The request specifies the type ('single', 'batch', 'ytdl') and relevant items.
        """
        request_type = download_request.get('type')
        request_items_list = download_request.get('items')

        if not request_type or not isinstance(request_items_list, list) or not request_items_list:
            raise ValueError("Invalid download request structure. 'type' and non-empty 'items' list are required.")

        if request_type == 'single' or request_type == 'ytdl':
            if len(request_items_list) != 1:
                raise ValueError(f"'{request_type}' type expects exactly one item in 'items' list.")

            item_data = request_items_list[0]
            urls_for_task = item_data.get('url')
            if not isinstance(urls_for_task, list):
                urls_for_task = [urls_for_task]

            task_items = {
                'urls': urls_for_task,
                'path': item_data.get('path'),
                'type': request_type,
                'config': self.main_window.config
            }
            if request_type == 'ytdl':
                task_items['original_url'] = item_data['original_url']
                task_items['format_id'] = item_data['format_id']

            task = DownloadTask(task_items, self.main_window.config.get('concurrency', 8))
            with QMutexLocker(self.mutex):
                self.tasks.append(task)

        elif request_type == 'batch':
            for i, item_data in enumerate(request_items_list):
                urls_for_task = item_data.get('url')
                if not isinstance(urls_for_task, list):
                    urls_for_task = [urls_for_task]

                specified_save_path = item_data.get('path')

                task_items = {
                    'urls': urls_for_task,
                    'path': specified_save_path,
                    'type': 'batch',
                    'config': self.main_window.config
                }

                task = DownloadTask(task_items, self.main_window.config.get('concurrency', 8))
                with QMutexLocker(self.mutex):
                    self.tasks.append(task)
        else:
            raise ValueError(f"Unknown download request type: '{request_type}'. Expected 'single', 'batch', or 'ytdl'.")

        # Emit a snapshot of the updated queue
        with QMutexLocker(self.mutex):
            self.queue_updated.emit(self._get_current_tasks_data())
        self.process_queue() # Attempt to start new downloads

    @Slot()
    def process_queue(self):
        """
        Processes the download queue, starting new tasks if worker slots are available.
        This method should be called whenever a task completes or new tasks are added.
        It acquires `self.mutex` to safely manage the task queue and active workers.
        """
        with QMutexLocker(self.mutex): # Acquire mutex for operations on shared task lists
            current_max_workers = self.main_window.config.get('max_workers', 3)
            # Iterate over a copy of the deque to avoid issues if `tasks` is modified during iteration
            for task in list(self.tasks):
                if task.status == "Queued" and len(self.active_tasks) < current_max_workers:
                    self._start_task(task)
                elif len(self.active_tasks) >= current_max_workers:
                    break # Max workers reached, stop processing for now


    def _recalculate_and_distribute_speed_limits(self):
        """
        Calculates the per-task speed limit based on the global max_speed setting and
        the number of currently active tasks. This limit is then distributed to each active task.
        This method assumes `self.mutex` is already held by the caller.
        """
        num_active_tasks = len(self.active_tasks)
        global_max_speed_kbps = self.main_window.config.get('max_speed', 0)

        if global_max_speed_kbps > 0 and num_active_tasks > 0:
            allowed_speed_per_task_bps = (global_max_speed_kbps * 1024) / num_active_tasks
        else:
            allowed_speed_per_task_bps = float('inf') # Unlimited speed

        for worker in self.active_tasks.values():
            worker.task._update_allowed_speed(allowed_speed_per_task_bps)


    def _start_task(self, task: DownloadTask):
        """
        Starts a given DownloadTask by creating a DownloadWorker (QThread) and
        connecting its signals to appropriate slots in the DownloadManager.
        This method is designed to be called from within a `QMutexLocker` context.
        """
        task_id = id(task)

        if task_id in self.paused_tasks:
            worker = self.paused_tasks.pop(task_id)
            task.resume()
        else:
            worker = DownloadWorker(task)
            # Connect task signals to manager slots for lifecycle management and UI updates.
            task.status_changed.connect(self._handle_task_update)
            task.finished.connect(self._handle_task_completion)
            task.error_occurred.connect(self._handle_task_error)

            task.start_download() # Change task's internal status to 'Downloading'

            try:
                worker.start() # Start the worker thread
            except Exception as e:
                # Handle cases where thread creation/start itself fails
                print(f"[{time.time():.2f}] DownloadManager ERROR: Failed to start worker thread for '{task.basename}': {e}")
                if id(task) in self.active_tasks:
                    del self.active_tasks[id(task)] # Clean up if failed to start

                self._recalculate_and_distribute_speed_limits() # Re-distribute speed limits
                self.queue_updated.emit(self._get_current_tasks_data())

        self.active_tasks[id(task)] = worker
        self._recalculate_and_distribute_speed_limits()
        self.queue_updated.emit(self._get_current_tasks_data())

    @Slot()
    def _update_table(self):
        # Emit a snapshot of the updated queue
        with QMutexLocker(self.mutex):
            self.queue_updated.emit(self._get_current_tasks_data())

    @Slot()
    def _handle_task_update(self):
        """
        Slot to handle `status_changed` signals from DownloadTasks.
        It's executed on the GUI thread.
        """
        # Emit a snapshot of the updated queue
        with QMutexLocker(self.mutex):
            self.queue_updated.emit(self._get_current_tasks_data())
            self._recalculate_and_distribute_speed_limits()


    @Slot(QObject)
    def _handle_task_completion(self, task: QObject):
        """
        Slot to handle a DownloadTask completing successfully.
        This slot is executed on the GUI thread in response to a worker signal.
        It cleans up the task and notifies the user via system tray.
        """
        # Ensure the received object is indeed a DownloadTask instance.
        if not isinstance(task, DownloadTask):
            self.main_window.logger.warning(f"DownloadManager: Warning: _handle_task_completion received non-DownloadTask object: {type(task)}")
            return

        self._cleanup_task(task) # Clean up task from manager's lists
        self._show_tray_notification(
            "Download Complete",
            f"{task.basename} downloaded successfully!",
            QSystemTrayIcon.MessageIcon.Information
        )
        with QMutexLocker(self.mutex):
            self.queue_updated.emit(self._get_current_tasks_data())
            self.task_finished.emit(task)
        self.process_queue() # Try to start next queued download


    @Slot(QObject, str)
    def _handle_task_error(self, task: QObject, error_message: str):
        """
        Slot to handle a DownloadTask reporting an error.
        This slot is executed on the GUI thread in response to a worker signal.
        It cleans up the task and notifies the user about the error.
        """
        if not isinstance(task, DownloadTask):
            self.main_window.logger.warning(f"DownloadManager: _handle_task_error received non-DownloadTask object: {type(task)}")
            return

        self.main_window.logger.error(f"DownloadManager: Task '{task.basename}' reported an error: {error_message}")
        self._cleanup_task(task) # Clean up task from manager's lists
        self._show_tray_notification(
            "Download Failed",
            f"{task.basename} failed to download!",
            QSystemTrayIcon.MessageIcon.Critical
        )

        with QMutexLocker(self.mutex):
            self.queue_updated.emit(self._get_current_tasks_data())
        self.process_queue() # Try to start next queued download


    def _cleanup_task(self, task: DownloadTask):
        """
        Centralized method to clean up a DownloadTask.
        Removes the task from the `active_tasks` dictionary.
        It ensures thread safety by acquiring `self.mutex`.
        """
        with QMutexLocker(self.mutex): # Acquire mutex as this modifies shared state
            task_id = id(task)
            if task_id in self.active_tasks:
                worker = self.active_tasks[task_id]
                if worker.isRunning():
                    worker.wait()
                    worker.deleteLater()
                del self.active_tasks[task_id]
            elif task_id in self.paused_tasks:
                worker = self.paused_tasks[task_id]
                if worker.isRunning():
                    worker.wait()
                    worker.deleteLater()
                del self.paused_tasks[task_id]


    def _show_tray_notification(self, title: str, message: str, icon: QSystemTrayIcon.MessageIcon):
        """
        Displays a system tray notification to the user.
        If a system tray icon is not available, it prints to console as a fallback.
        """
        if hasattr(self.main_window, 'sys_tray') and self.main_window.sys_tray:
            self.main_window.sys_tray.showMessage(
                title,
                message,
                icon,
                2000 # Duration in milliseconds
            )
        else:
            print(f"[{time.time():.2f}] Notification: {title} - {message}")

    def pause_download(self, task: DownloadTask):
        """
        Pauses a specific download task.
        Acquires `self.mutex` to ensure consistent state and recalculate speed limits.
        """
        if task:
            with QMutexLocker(self.mutex):
                task_id = id(task)
                task.pause() # Delegate pause logic to the task itself
                if task_id in self.active_tasks: # Ensure it's active before trying to remove
                    worker = self.active_tasks.pop(task_id)
                    self.paused_tasks[task_id] = worker
                # Emit a snapshot of the updated queue
                self.queue_updated.emit(self._get_current_tasks_data())
            self.process_queue()


    def resume_download(self, task: DownloadTask):
        """
        Resumes a specific download task that was previously paused.
        Acquires `self.mutex` to ensure consistent state and recalculate speed limits.
        """
        if task:
            with QMutexLocker(self.mutex):
                task._set_status("Queued")
                self.queue_updated.emit(self._get_current_tasks_data())
            self.process_queue()


    def cancel_download(self, task: DownloadTask):
        """
        Cancels a specific download task and cleans up its resources.
        It delegates the cancellation to the task itself, then cleans up manager's state.
        """
        if task:
            task.cancel() # Delegate cancellation to the task (which uses its own mutex)
            self._cleanup_task(task) # Clean up from DownloadManager's lists
            # Emit a snapshot of the updated queue after cleanup
            with QMutexLocker(self.mutex):
                self.queue_updated.emit(self._get_current_tasks_data())
            self.process_queue() # Trigger queue processing to start next task if slot available

    def remove_download(self, task: DownloadTask):
        """
        Removes a specific download task and cleans up its resources.
        It cancels the task first then cleans up the resources.
        """
        if not task:
            return
        if not task._canceled:
            task.cancel()
        self._cleanup_task(task)
        with QMutexLocker(self.mutex):
            self.tasks.remove(task)

    def set_max_speed(self):
        """
        Updates the maximum global download speed. This triggers a recalculation
        and redistribution of speed limits among all active download tasks.
        """
        with QMutexLocker(self.mutex):
            self._recalculate_and_distribute_speed_limits()
            # Emit a snapshot of the updated queue after speed changes, as it might affect displayed speed.
            self.queue_updated.emit(self._get_current_tasks_data())


    def set_max_workers(self):
        """
        Updates the maximum number of concurrent workers allowed. This triggers
        the `process_queue` method to potentially start more queued tasks if slots become available.
        """
        self.process_queue()
            # Emit a snapshot of the updated queue after worker changes, as it might affect active tasks.
        with QMutexLocker(self.mutex):
            self.queue_updated.emit(self._get_current_tasks_data())

    def shutdown(self):
        """
        Gracefully shuts down the download manager. It attempts to cancel all active tasks
        and clears all task queues.
        """
        with QMutexLocker(self.mutex):
            for task_id, worker in list(self.active_tasks.items()):
                task = worker.task
                task.cancel() # Request each active task to cancel
                worker.wait() # Wait for the worker thread to finish
                worker.deleteLater()
                if task_id in self.active_tasks:
                    del self.active_tasks[task_id] # Remove from active tasks
            for task_id, worker in list(self.paused_tasks.items()):
                task = worker.task
                task.cancel() # Request each active task to cancel
                worker.wait() # Wait for the worker thread to finish
                worker.deleteLater()
                if task_id in self.paused_tasks:
                    del self.paused_tasks[task_id] # Remove from active tasks
            self.active_tasks.clear() # Clear any remaining active tasks
            self.paused_tasks.clear() # Clear any remaining active tasks
            self.tasks.clear() # Clear the main task queue
            # Emit a snapshot of the updated queue after shutdown
            self.queue_updated.emit(self._get_current_tasks_data())
