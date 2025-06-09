import os
import time
import requests
import subprocess
import re # Added for ffmpeg output parsing
from collections import deque
from urllib.parse import urlparse
from PySide6.QtCore import (
    QThread, QObject, Signal, QMutex,
    QMutexLocker, Slot, QTimer, QWaitCondition
)
from PySide6.QtWidgets import QSystemTrayIcon
from PySide6.QtGui import QIcon

# --- Mocking external utilities for standalone execution ---
# In a real application, these would be imported from your project structure
# from utils.ffmpeg import combine_video_audio
# from utils.helpers import *

def combine_video_audio(video_path, audio_path):
    """Mocks combining video and audio files."""
    print(f"MOCK: Combining video {video_path} and audio {audio_path}")
    if os.path.exists(video_path): os.remove(video_path)
    if os.path.exists(audio_path): os.remove(audio_path)
    combined_path = video_path.replace(".video", "")
    with open(combined_path, 'w') as f:
        f.write("Combined content. MOCK_FILE_SIZE: 1000000") # Add mock size for testing
    print(f"MOCK: Combined file created at {combined_path}")
    # Update total size for mock combined file
    return os.path.getsize(combined_path)


def is_m3u8_url(url):
    """Checks if a URL is an M3U8 HLS stream."""
    return '.m3u8' in url.lower() # Case-insensitive check

def extract_size_info(line):
    """Extracts size and unit from FFmpeg output lines."""
    # This regex is specifically for 'size=   1234kB' format
    m = re.search(r'size=\s*(\d+\.?\d*)\s*(kB|MB|GB)', line, re.IGNORECASE)
    if m:
        return float(m.group(1)), m.group(2)
    raise ValueError("Size info not found in line.")

# Mock logger and constants for standalone testing
class MockLogger:
    def sendToLog(self, message, level='INFO'):
        print(f"LOG [{level}]: {message}")

logger = MockLogger()

class MockVersion:
    def __init__(self):
        self.version_str = "1.0.0"
VERSION = MockVersion()

# --- Utility for file pre-allocation ---
def pre_allocate_file(filepath, size):
    """
    Pre-allocates a file to a given size. Creates parent directories if they don't exist.
    Uses `posix_fallocate` on POSIX systems for efficiency, falls back to sparse file creation.
    """
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'wb') as f:
            if hasattr(os, 'posix_fallocate'):
                os.posix_fallocate(f.fileno(), 0, size)
            else:
                f.seek(size - 1)
                f.write(b'\0')
        logger.sendToLog(f"File '{filepath}' pre-allocated to {size} bytes.", 'INFO')
    except Exception as e:
        logger.sendToLog(f"Error pre-allocating file {filepath}: {e}. Creating empty file instead.", 'ERROR')
        # If pre-allocation fails (e.g., no space, or permission), still ensure file exists
        open(filepath, 'wb').close()

# --- Required for retry logic in requests ---
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class DownloadPartWorker(QThread):
    """
    QThread subclass responsible for downloading a specific part (byte range) of a file.
    Communicates progress and status back to the main DownloadTask via signals.
    """
    part_progress = Signal(int, int)  # part_index, downloaded_bytes_in_part
    part_finished = Signal(int)       # part_index
    part_error = Signal(int, str)     # part_index, error_message

    def __init__(self, task_ref, worker_id, url, file_path, session_config):
        """
        Initializes the download worker for a specific part.

        Args:
            task_ref (DownloadTask): Reference to the parent DownloadTask instance.
            worker_id (int): A unique identifier for this worker thread within the pool.
            url (str): The URL of the file to download.
            file_path (str): The full path to the target file on disk.
            session_config (dict): Configuration for the requests session (timeout, retry, etc.).
        """
        super().__init__()
        self.task_ref = task_ref
        self.worker_id = worker_id # Unique ID for this worker in the pool
        self.url = url
        self.file_path = file_path
        
        # Session configuration
        self.check_certificate = session_config.get('check_certificate', True)
        self.timeout = session_config.get('timeout', 30)
        self.retry = session_config.get('retry', 5)
        self.retry_wait = session_config.get('retry_wait', 5)
        self.python_request_chunk_size = session_config.get('python_request_chunk_size', 256) # In bytes
        self.max_speed_per_thread = session_config.get('max_speed_per_thread', 0) # In bytes/sec
        
        self._session = None

    def run(self):
        """
        Main execution loop for the download worker.
        Continuously requests parts from the DownloadTask until all parts are done or canceled.
        """
        # Initialize requests session for this thread
        self._session = requests.Session()
        
        # Apply retry strategy to the session
        retry_strategy = Retry(
            total=self.retry,
            backoff_factor=self.retry_wait,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET"] # Only GET for downloads
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        
        # Set common session headers (User-Agent)
        self._session.headers.update({'User-Agent': 'PersepolisDM/' + str(VERSION.version_str)})

        while True: # Loop to continuously process parts
            # Check for global task cancellation early
            if self.task_ref._canceled:
                logger.sendToLog(f"Worker {self.worker_id} detected task canceled. Exiting.", 'DEBUG')
                break

            # Request a new part index from the DownloadTask (thread-safe)
            # This method blocks if no part is available but the task is not yet complete.
            part_data = self.task_ref._request_part_for_thread(self.worker_id)
            
            if part_data is None: # No more parts to download or task is finished/canceled
                logger.sendToLog(f"Worker {self.worker_id} - No more parts or task ended. Exiting.", 'DEBUG')
                break

            part_idx, start_byte, end_byte_inclusive, initial_downloaded_in_part = part_data
            
            # Calculate the current write offset for this part, considering partial downloads
            current_write_offset = start_byte + initial_downloaded_in_part
            current_part_total_len = (end_byte_inclusive - start_byte + 1)
            
            # If the part is already fully downloaded, skip it
            if initial_downloaded_in_part >= current_part_total_len:
                logger.sendToLog(f"Worker {self.worker_id} skipping part {part_idx} as it's already complete.", 'INFO')
                self.part_finished.emit(part_idx)
                continue # Request next part

            logger.sendToLog(f"Worker {self.worker_id} assigned part {part_idx}. Range: {current_write_offset}-{end_byte_inclusive}", 'INFO')

            try:
                headers = {'Range': f"bytes={current_write_offset}-{end_byte_inclusive}"}
                
                response = self._session.get(
                    self.url,
                    headers=headers,
                    stream=True, # Important for large downloads
                    timeout=self.timeout,
                    verify=self.check_certificate
                )
                response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

                # If server didn't honor range request (sent 200 OK instead of 206 Partial Content)
                # and we were trying to resume an existing part, this indicates an issue.
                # For simplicity, if 200, assume full file sent from beginning,
                # but for multi-part download, this implies a problem with server's range support.
                # In a real scenario, this might trigger fallback to single-threaded or re-evaluate.
                if response.status_code == 200 and (current_write_offset != start_byte):
                    logger.sendToLog(f"Worker {self.worker_id} received 200 OK for range request for part {part_idx}. Server may not support ranges fully, or this is the only part. Restarting part from {start_byte}.", 'WARNING')
                    current_write_offset = start_byte # Reset to start of original part if server ignored range
                    initial_downloaded_in_part = 0 # No bytes downloaded for this part if server ignored range

                # Open the file in binary read/write mode for seeking
                with open(self.file_path, "r+b") as fp:
                    fp.seek(current_write_offset) # Seek to the correct position for this part
                    
                    bytes_downloaded_for_this_segment = 0 # Track bytes for this worker in this specific chunk sequence
                    for chunk in response.iter_content(chunk_size=self.python_request_chunk_size):
                        if not chunk:
                            continue # Empty chunk, continue

                        # Acquire mutex for pause/cancel checks
                        with self.task_ref.download_mutex:
                            # Check for cancellation within the chunk loop
                            if self.task_ref._canceled:
                                logger.sendToLog(f"Worker {self.worker_id} detected cancel during chunk write for part {part_idx}. Exiting.", 'DEBUG')
                                self.part_error.emit(part_idx, "Download canceled during chunk write.")
                                return # Exit this run()
                            # Check for pause and wait if paused
                            while self.task_ref._paused:
                                self.task_ref.parts_condition.wait(self.task_ref.download_mutex)
                        
                        # Verify if this worker is still assigned to this part
                        # (Important if a part was re-queued due to error and picked by another worker)
                        with self.task_ref.download_mutex:
                            if self.task_ref.part_info[part_idx]['thread_id'] != self.worker_id:
                                logger.sendToLog(f"Worker {self.worker_id} part {part_idx} taken by another thread. Exiting current chunk loop.", 'DEBUG')
                                # Signal current thread's premature exit, without marking part as error (as it's re-assigned)
                                return # Exit this run()
                                
                        
                        fp.write(chunk)
                        chunk_len = len(chunk)
                        bytes_downloaded_for_this_segment += chunk_len
                        
                        # Emit progress for this specific part, updated from initial downloaded bytes
                        self.part_progress.emit(part_idx, initial_downloaded_in_part + bytes_downloaded_for_this_segment)

                        # Apply speed limiting for this individual thread
                        if self.max_speed_per_thread > 0:
                            delay = chunk_len / self.max_speed_per_thread
                            time.sleep(delay)
                        
                        # Check if this part of the download is complete (written beyond its end byte)
                        if (start_byte + initial_downloaded_in_part + bytes_downloaded_for_this_segment) > end_byte_inclusive:
                            break # This part is finished

                # Mark part as finished only if its expected range has been fully downloaded
                if (start_byte + initial_downloaded_in_part + bytes_downloaded_for_this_segment) > end_byte_inclusive:
                    self.part_finished.emit(part_idx)
                else:
                    # If loop finished but part not fully downloaded, something is wrong, retry it.
                    raise requests.exceptions.RequestException(f"Part {part_idx} not fully downloaded. Expected: {current_part_total_len}, Actual: {initial_downloaded_in_part + bytes_downloaded_for_this_segment}")

            except requests.exceptions.RequestException as e:
                logger.sendToLog(f"Worker {self.worker_id} encountered HTTP error for part {part_idx}: {e}", 'ERROR')
                # Signal error to DownloadTask, which will manage retries/reassignment
                self.part_error.emit(part_idx, str(e))
                # This worker will now try to pick up a new part from the queue
            except Exception as e:
                logger.sendToLog(f"Worker {self.worker_id} encountered unexpected error for part {part_idx}: {e}", 'CRITICAL')
                self.part_error.emit(part_idx, str(e))
                # This worker will now try to pick up a new part from the queue

        logger.sendToLog(f"DownloadPartWorker {self.worker_id} finished its job.", 'INFO')


class DownloadTask(QObject):
    """
    Manages a single download task, supporting multi-threaded HTTP/S downloads
    and single-threaded HLS/YTDL downloads.
    """
    status_changed = Signal(str) # Emits the current status of the download (e.g., "Downloading", "Paused", "Completed")
    finished = Signal(str)       # Emits the basename of the file when download completes successfully
    error_occurred = Signal(str, str) # Emits basename and error message when an error occurs
    
    # Internal signals for part workers to communicate with DownloadTask
    _part_progress_signal = Signal(int, int)
    _part_finished_signal = Signal(int)
    _part_error_signal = Signal(int, str)


    def __init__(self, items, max_speed=0, configs=None):
        """
        Initializes a DownloadTask.

        Args:
            items (dict): Dictionary containing download details (urls, path, type, connections).
            max_speed (int): Global maximum download speed for this task (bytes/sec). 0 means unlimited.
            configs (dict, optional): Global application configurations (timeout, retry, etc.).
        """
        super().__init__()
        self.items = items
        self.urls = items['urls']
        self.save_path = items['path']
        self.basename = os.path.basename(items['path'])
        self.max_speed = max_speed # Total speed limit for all connections
        self.status = "Queued"
        self.downloaded = 0 # Total downloaded bytes across all parts/threads
        self.total_size = 0 # Total file size (retrieved from HTTP header if available)
        self.speed = 0 # Overall download speed (calculated from history)
        self.history = deque(maxlen=60) # Stores (timestamp, downloaded_bytes) for speed calculation
        self._paused = False
        self._canceled = False
        self.mutex = QMutex() # Main mutex for task state (pause/cancel/status)
        self.condition = QWaitCondition() # Main condition for pause/resume
        self.ytdl = items['type'] == 'ytdl'
        self.start_time = None
        
        # Multi-threading specific attributes
        self.max_connections = max(1, items.get('connections', 1)) # User-defined concurrent connections, min 1
        self.accept_ranges_support = False # Does the server support byte range requests?
        self.part_info = [] # List of dictionaries: {'start', 'end', 'downloaded', 'status', 'retries', 'thread_id'}
        self.active_part_workers = [] # List of DownloadPartWorker instances actively running
        self.parts_completed_count = 0 # Count of parts that have finished successfully
        self.download_mutex = QMutex() # Mutex for managing part info and overall downloaded size (separate from self.mutex for pause/cancel)
        self.parts_condition = QWaitCondition() # Condition for part workers to get new parts / for main task to wait for completion
        self.is_multithreaded = False # Flag indicating if multi-threading is active for this download
        
        # Configuration for requests session (from main_window.config, if passed)
        self.configs = configs if configs is not None else {}
        self.check_certificate = self.configs.get('dont_check_certificate', 'no') == 'no' # Inverts 'dont-check' to 'check'
        self.timeout = int(self.configs.get('timeout', 30))
        self.retry = int(self.configs.get('max_tries', 5))
        self.retry_wait = int(self.configs.get('retry_wait', 5))
        self.python_request_chunk_size = int(self.configs.get('chunk_size', 256)) * 1024 # Convert KiB to bytes


        # Connect internal signals from part workers to slots in DownloadTask
        self._part_progress_signal.connect(self._on_part_progress)
        self._part_finished_signal.connect(self._on_part_finished)
        self._part_error_signal.connect(self._on_part_error)

    def start_download(self):
        """Sets the task status to Downloading."""
        with QMutexLocker(self.mutex):
            self._set_status("Downloading")

    def pause(self):
        """Pauses the download if it is currently active."""
        with QMutexLocker(self.mutex):
            if self.status == "Downloading":
                self._paused = True
                self._set_status("Paused")
                # Workers will wait on their own condition checks after seeing _paused flag

    def resume(self):
        """Resumes a paused download."""
        with QMutexLocker(self.mutex):
            if self.status == "Paused":
                self._paused = False
                self._set_status("Downloading")
                self.condition.wakeAll() # Wake main task condition
                self.parts_condition.wakeAll() # Wake part workers if they were waiting

    def cancel(self):
        """Cancels the download."""
        with QMutexLocker(self.mutex):
            if not self._canceled:
                self._canceled = True
                self._set_status("Canceled")
                self.condition.wakeAll() # Wake main task if it's waiting
                self.parts_condition.wakeAll() # Wake up all part workers to check for cancel

    def set_max_speed(self, max_speed):
        """Sets the maximum download speed for this task."""
        with QMutexLocker(self.mutex):
            self.max_speed = max_speed
            # Distribute speed limit to active part workers if multi-threaded
            if self.is_multithreaded and self.max_connections > 0:
                speed_per_thread = self.max_speed / self.max_connections if self.max_connections > 0 else 0
                for worker in self.active_part_workers:
                    worker.max_speed_per_thread = speed_per_thread
            # For single-threaded, the _limit_speed method in this class applies the total limit

    def download(self):
        """
        Main download logic. Determines download type (YTDL, HLS, HTTP)
        and initiates the appropriate download process (single-threaded or multi-threaded).
        """
        self.start_time = time.time()
        
        try:
            if self.ytdl:
                # Handle YTDL downloads (typically external tool, e.g., yt-dlp)
                for i, url in enumerate(self.urls):
                    if self._canceled: return
                    save_path = self._get_save_path(i)
                    self._download_url(url, save_path) # This calls ffmpeg for YTDL combined files
                if self.urls: # If there was at least one URL, update total size if combined
                    self.total_size = combine_video_audio(f"{self.save_path}.video", f"{self.save_path}.audio")
            elif is_m3u8_url(self.urls[0]): # Assuming single URL for HLS
                if self._canceled: return
                self._download_url(self.urls[0], self.save_path) # HLS uses ffmpeg directly
                self.total_size = self.downloaded # FFmpeg output usually updates self.downloaded, so use that as total if not known
            else: # Standard HTTP/S download, potentially multi-threaded
                # Step 1: Get file metadata (size, range support)
                if not self._get_file_metadata(self.urls[0]): # Assuming single URL for standard HTTP download
                    return # Error already handled and emitted inside _get_file_metadata
                
                if self._canceled: return # Check for cancellation after metadata fetch
                
                # Step 2: Prepare the file on disk and initialize part information
                self._prepare_file()
                
                if self._canceled: return # Check for cancellation after file preparation

                self._set_status("Downloading") # Set status once actual download starts

                # Step 3: Start download based on multi-threading capability
                if self.is_multithreaded:
                    logger.sendToLog(f"Starting multi-threaded download for {self.basename}.", 'INFO')
                    self._start_multithreaded_download()
                    
                    # Wait for all parts to complete or for an unrecoverable error
                    with QMutexLocker(self.download_mutex):
                        while self.parts_completed_count < len(self.part_info) and not self._canceled and self.status != "ERROR":
                            # If all remaining non-complete parts are stuck in 'failed' status after max retries
                            # then terminate the download as a failure.
                            if all(p['status'] == 'failed' for p in self.part_info if p['status'] != 'complete'):
                                logger.sendToLog("All remaining parts failed after max retries. Terminating download.", 'ERROR')
                                self._handle_error("All remaining parts failed to download after multiple retries.")
                                return # Exit if multi-part download fails critically
                            self.parts_condition.wait(self.download_mutex) # Wait for a part status change

                else: # Single-threaded HTTP download
                    logger.sendToLog(f"Starting single-threaded download for {self.basename}.", 'INFO')
                    self._download_single_http_file(self.urls[0], self.save_path)
                    
            if self._canceled:
                self._set_status("Canceled")
                logger.sendToLog(f"Download for {self.basename} was canceled.", 'INFO')
                return # Exit if canceled during download stages

            # Final success handling
            self._handle_success()
                    
        except Exception as e:
            self._handle_error(e)
        finally:
            self._cleanup_active_workers() # Ensure all worker threads are stopped

    def _set_status(self, status):
        """Updates the download status and emits the status_changed signal."""
        # Use main mutex to protect status changes
        with QMutexLocker(self.mutex):
            if self.status != status: # Only emit if status actually changes
                self.status = status
                self.status_changed.emit(status)

    def _get_save_path(self, index):
        """Determines the save path for YTDL video/audio parts."""
        if self.ytdl:
            file_type = 'video' if index == 0 else 'audio'
            return f"{self.save_path}.{file_type}"
        return self.save_path

    def _handle_success(self):
        """Handles successful completion of the download."""
        with QMutexLocker(self.mutex):
            # Only mark success if not canceled or already in an error state
            if not self._canceled and self.status not in ["ERROR", "Canceled"]:
                self._set_status("Completed")
                self.finished.emit(self.basename)
                logger.sendToLog(f"Download for {self.basename} completed successfully.", 'INFO')

    def _handle_error(self, exception):
        """Handles errors during the download, sets status, and emits error signal."""
        with QMutexLocker(self.mutex):
            # Only set error status if not explicitly canceled by user
            if not self._canceled and self.status != "ERROR":
                self._set_status("ERROR")
                error_msg = str(exception)
                self.error_occurred.emit(self.basename, error_msg)
                logger.sendToLog(f"Download for {self.basename} failed: {error_msg}", 'ERROR')
            # Important: Re-raise the exception so the DownloadWorker also catches it and signals completion
            raise exception

    def _cleanup_active_workers(self):
        """Ensures all DownloadPartWorker threads are terminated."""
        with QMutexLocker(self.download_mutex): # Use download_mutex for worker list
            for worker in self.active_part_workers:
                worker.quit() # Request thread to terminate
                worker.wait(5000) # Wait up to 5 seconds for the thread to exit gracefully
                logger.sendToLog(f"Worker {worker.worker_id} cleanup complete.", 'DEBUG')
            self.active_part_workers.clear()


    # --- Multi-threading specific methods for DownloadTask orchestration ---

    def _get_file_metadata(self, url):
        """
        Performs a HEAD request to get Content-Length and Accept-Ranges from the server.
        Initializes a temporary requests session for this purpose.
        """
        temp_session = requests.Session()
        # Apply retry strategy for initial HEAD request
        retry_strategy = Retry(
            total=self.retry,
            backoff_factor=self.retry_wait,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        temp_session.mount("http://", adapter)
        temp_session.mount("https://", adapter)
        temp_session.headers.update({'User-Agent': 'PersepolisDM/' + str(VERSION.version_str)})

        try:
            response = temp_session.head(url, allow_redirects=True, timeout=self.timeout, verify=self.check_certificate)
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

            self.total_size = int(response.headers.get('content-length', 0))
            self.accept_ranges_support = response.headers.get('accept-ranges', '').lower() == 'bytes'
            logger.sendToLog(f"File metadata for {self.basename}: Size={self.total_size} bytes, Accept-Ranges={self.accept_ranges_support}", 'INFO')
            return True
        except requests.exceptions.RequestException as e:
            logger.sendToLog(f"Failed to get file metadata for {self.basename}: {e}", 'ERROR')
            self.total_size = 0 # Indicate size unknown
            self.accept_ranges_support = False # Assume no range support if cannot connect/head
            self.error_occurred.emit(self.basename, f"Network/HTTP error getting file info: {str(e)}")
            return False
        except ValueError: # content-length header not a valid integer
            logger.sendToLog(f"Content-Length header invalid for {self.basename}. Proceeding without known total size.", 'WARNING')
            self.total_size = 0 # Cannot determine size
            self.accept_ranges_support = False # Assume no range support if size is unknown for multithreading
            return True # Not a critical error, can proceed as single-file download if possible (appends)
        except Exception as e:
            logger.sendToLog(f"Unexpected error getting file metadata for {self.basename}: {e}", 'ERROR')
            self.error_occurred.emit(self.basename, f"Unexpected error getting file info: {str(e)}")
            return False


    def _prepare_file(self):
        """
        Prepares the file on disk (creates directory, pre-allocates size)
        and initializes part information for multi-threading if applicable.
        Handles existing partial files for resume.
        """
        # Create download directory if it doesn't exist
        os.makedirs(os.path.dirname(self.save_path), exist_ok=True)
        
        existing_file_size = self._get_existing_size(self.save_path)
        
        # Decide if we can resume based on total_size and existing_file_size
        can_resume_full_file = (self.total_size > 0 and existing_file_size > 0 and existing_file_size < self.total_size)
        
        if self.total_size > 0: # Only pre-allocate if we know the total size
            if not can_resume_full_file or existing_file_size > self.total_size:
                logger.sendToLog(f"Creating/resetting file {self.save_path} for download (total size known).", 'INFO')
                pre_allocate_file(self.save_path, self.total_size)
                self.downloaded = 0 # Reset overall downloaded count
                existing_file_size = 0 # No existing data to consider for individual parts for new download
            else:
                logger.sendToLog(f"Resuming overall download for {self.basename}. Existing size: {existing_file_size} bytes.", 'INFO')
                self.downloaded = existing_file_size # Set overall downloaded to existing size
        else: # Total size unknown (e.g., from server not providing Content-Length), just ensure file exists for append mode
            if not os.path.exists(self.save_path):
                open(self.save_path, 'wb').close() # Create empty file
            self.downloaded = existing_file_size # For unknown size, start with existing size

        # Initialize part information if multi-threading is enabled
        if self.accept_ranges_support and self.max_connections > 1 and self.total_size > 0:
            self.is_multithreaded = True
            # Determine number of parts. Minimum part size to avoid too many small requests.
            min_part_size = 1024 * 1024 # 1 MiB minimum per part
            # Calculate ideal number of parts based on total size and min part size
            num_parts_by_size = max(1, (self.total_size + min_part_size - 1) // min_part_size)
            # Use the lesser of user-defined connections or parts dictated by min_part_size
            self.num_parts = min(self.max_connections, num_parts_by_size) 
            
            # Ensure at least one part for very small files
            if self.num_parts == 0: self.num_parts = 1

            part_size_base = self.total_size // self.num_parts
            
            temp_part_info = []
            for i in range(self.num_parts):
                start = i * part_size_base
                end = (i + 1) * part_size_base - 1
                if i == self.num_parts - 1: # Last part gets all remaining bytes
                    end = self.total_size - 1
                
                part_downloaded = 0
                part_status = 'pending'
                
                # Check if this part is already covered by the existing file
                if existing_file_size > 0 and (end + 1) <= existing_file_size:
                    part_downloaded = (end - start + 1)
                    part_status = 'complete'
                    logger.sendToLog(f"Part {i} ({start}-{end}) already complete from existing file.", 'INFO')
                elif existing_file_size > 0 and start < existing_file_size: # Partially downloaded part
                    part_downloaded = min(existing_file_size - start, end - start + 1)
                    part_status = 'pending' # Still needs to be downloaded, but offset applied
                    logger.sendToLog(f"Part {i} ({start}-{end}) partially downloaded ({part_downloaded} bytes) from existing file.", 'INFO')

                temp_part_info.append({
                    'start': start,
                    'end': end,
                    'downloaded': part_downloaded, # Bytes downloaded for this specific part
                    'status': part_status, # 'pending', 'downloading', 'complete', 'failed'
                    'retries': 0,
                    'thread_id': None # ID of the worker thread currently downloading this part
                })

            self.part_info = temp_part_info
            self.parts_completed_count = sum(1 for p in self.part_info if p['status'] == 'complete')
            logger.sendToLog(f"Initialized {self.num_parts} parts for multi-threaded download. Already completed: {self.parts_completed_count} parts.", 'INFO')

        else:
            self.is_multithreaded = False
            logger.sendToLog(f"Multi-threading not enabled for {self.basename}. Proceeding with single-threaded download.", 'INFO')


    def _start_multithreaded_download(self):
        """
        Spawns DownloadPartWorker threads to download file parts concurrently.
        """
        logger.sendToLog(f"Initiating {self.max_connections} DownloadPartWorker threads for {self.basename}.", 'INFO')
        # Distribute the overall speed limit among the concurrent connections
        speed_per_thread = self.max_speed / self.max_connections if self.max_connections > 0 else 0
        
        # Ensure minimum chunk size for requests, converting from KiB to bytes
        python_request_chunk_size_bytes = self.python_request_chunk_size

        # Configuration for each worker's requests session
        worker_session_config = {
            'check_certificate': self.check_certificate,
            'timeout': self.timeout,
            'retry': self.retry,
            'retry_wait': self.retry_wait,
            'python_request_chunk_size': python_request_chunk_size_bytes,
            'max_speed_per_thread': speed_per_thread # Bytes per second per thread
        }
        
        # Start the pool of worker threads.
        for i in range(self.max_connections):
            worker = DownloadPartWorker(self, i, self.urls[0], self.save_path, worker_session_config)
            # Connect the worker's signals to DownloadTask's slots
            worker.part_progress.connect(self._part_progress_signal)
            worker.part_finished.connect(self._part_finished_signal)
            worker.part_error.connect(self._part_error_signal)
            
            self.active_part_workers.append(worker)
            worker.start() # Start the QThread

    @Slot(int, int)
    def _on_part_progress(self, part_index, downloaded_in_part):
        """
        Slot to receive progress updates from a single part worker.
        Aggregates progress to update the overall download status.
        """
        with QMutexLocker(self.download_mutex): # Protect shared download progress data
            # Update individual part's downloaded bytes
            if 0 <= part_index < len(self.part_info):
                # Ensure we only update if the new value is greater (prevents race conditions with old signals)
                if downloaded_in_part > self.part_info[part_index]['downloaded']:
                    self.part_info[part_index]['downloaded'] = downloaded_in_part
            
            # Recalculate total downloaded bytes across all parts
            old_downloaded = self.downloaded
            self.downloaded = sum(p['downloaded'] for p in self.part_info)
            
            # Update history for overall speed calculation if total downloaded changed
            if self.downloaded != old_downloaded:
                self.history.append((time.time(), self.downloaded))
            
            # Emit overall status change to update UI
            self.status_changed.emit(self.status) # UI needs to re-read progress values

    @Slot(int)
    def _on_part_finished(self, part_index):
        """
        Slot to handle a part worker completing its assigned part.
        Updates part status and wakes up waiting threads if all parts are done.
        """
        with QMutexLocker(self.download_mutex): # Protect shared part_info data
            if 0 <= part_index < len(self.part_info) and self.part_info[part_index]['status'] != 'complete':
                self.part_info[part_index]['status'] = 'complete'
                self.parts_completed_count += 1
                logger.sendToLog(f"Part {part_index} completed. Total completed: {self.parts_completed_count}/{len(self.part_info)} for {self.basename}", 'INFO')
                self.parts_condition.wakeAll() # Wake up main task if it's waiting for completion, or other workers for new parts

    @Slot(int, str)
    def _on_part_error(self, part_index, error_message):
        """
        Slot to handle an error from a part worker.
        Increments retry count for the part. If max retries reached, marks part as failed.
        Otherwise, re-queues the part for another worker to pick up.
        """
        with QMutexLocker(self.download_mutex): # Protect shared part_info data
            if 0 <= part_index < len(self.part_info):
                self.part_info[part_index]['retries'] += 1
                logger.sendToLog(f"Part {part_index} error: {error_message}. Retries: {self.part_info[part_index]['retries']}/{self.retry}", 'WARNING')
                
                if self.part_info[part_index]['retries'] >= self.retry:
                    self.part_info[part_index]['status'] = 'failed' # Mark as permanently failed
                    logger.sendToLog(f"Part {part_index} failed after max retries for {self.basename}.", 'ERROR')
                    self.parts_condition.wakeAll() # Potentially signal overall failure to main task
                else:
                    self.part_info[part_index]['status'] = 'pending' # Make it available for re-assignment
                    self.part_info[part_index]['thread_id'] = None # Clear previous thread assignment
                    self.parts_condition.wakeAll() # Wake up other workers to potentially pick up this part

    def _request_part_for_thread(self, worker_id):
        """
        Thread-safe method for a DownloadPartWorker to request the next available part.
        Workers call this to get a new segment to download.

        Args:
            worker_id (int): The unique ID of the worker requesting a part.

        Returns:
            tuple: (part_index, start_byte, end_byte_inclusive, downloaded_in_part) or None if no parts available.
        """
        with QMutexLocker(self.download_mutex):
            # Check for global task cancellation first
            if self._canceled or self.status == "ERROR":
                logger.sendToLog(f"Worker {worker_id} - Task {self.basename} is canceled or in error state. No part provided.", 'DEBUG')
                return None
            
            # Find a 'pending' or 'failed' part that needs to be downloaded
            for i, part in enumerate(self.part_info):
                if part['status'] in ['pending', 'failed'] and part['retries'] < self.retry:
                    part['status'] = 'downloading'
                    part['thread_id'] = worker_id # Assign this worker to the part
                    logger.sendToLog(f"Worker {worker_id} assigned part {i}.", 'DEBUG')
                    return (i, part['start'], part['end'], part['downloaded'])
            
            # If all parts are 'complete' or 'downloading' (and not all are completed yet),
            # this worker should wait until a part becomes 'pending' again (e.g., after an error).
            # If all parts are truly complete or permanently failed, return None.
            if self.parts_completed_count >= len(self.part_info):
                logger.sendToLog(f"Worker {worker_id} - All parts completed for {self.basename}.", 'DEBUG')
                return None # All parts finished
            
            # No part immediately available. Worker's `run` loop should handle waiting.
            logger.sendToLog(f"Worker {worker_id} - No pending/failed part available for {self.basename}. Will try again.", 'DEBUG')
            return None 

    # --- Original methods adapted ---
    def _download_url(self, url, save_path):
        """
        Unified download method. For HLS, it triggers ffmpeg.
        For HTTP, it's bypassed if multithreading is enabled, otherwise handles single HTTP.
        """
        if is_m3u8_url(url):
            self._download_hls_stream(url, save_path)
        else:
            # This branch is effectively for YTDL now, as standard HTTP is handled
            # by _download_single_http_file or _start_multithreaded_download.
            # YTDL sometimes requires just fetching video/audio parts and then combining.
            # The original code structure suggests _download_url is called in a loop for YTDL parts.
            # So, keep this as a proxy for either HLS (ffmpeg) or potentially direct HTTP for YTDL *parts* if not multithreaded.
            self._download_single_http_file(url, save_path) # Fallback for YTDL direct link or HTTP without multi-thread

    def _download_single_http_file(self, url, save_path):
        """
        Handles single-threaded HTTP downloads, including resume for the entire file.
        Used when server doesn't support ranges or multi-threading is not desired.
        """
        existing_size = self._get_existing_size(save_path)
        
        # If total_size was known and existing_size is less, use Range header for resume
        headers = {}
        mode = 'wb' # Default to overwrite
        if self.total_size > 0 and existing_size > 0 and existing_size < self.total_size:
            headers = {"Range": f"bytes={existing_size}-"}
            mode = 'ab' # Append to existing file
            with QMutexLocker(self.download_mutex):
                self.downloaded = existing_size # Set initial downloaded for single thread
            logger.sendToLog(f"Single-thread resume: starting from {existing_size} bytes for {self.basename}.", 'INFO')
        elif existing_size > 0 and self.total_size == 0: # Unknown total size, but existing file. Assume append.
             mode = 'ab'
             with QMutexLocker(self.download_mutex):
                 self.downloaded = existing_size
             logger.sendToLog(f"Single-thread unknown size resume: starting from {existing_size} bytes for {self.basename}.", 'INFO')


        # Create requests session for this single-threaded download with retry logic
        single_session = requests.Session()
        retry_strategy = Retry(
            total=self.retry,
            backoff_factor=self.retry_wait,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        single_session.mount("http://", adapter)
        single_session.mount("https://", adapter)
        single_session.headers.update({'User-Agent': 'PersepolisDM/' + str(VERSION.version_str)})


        try:
            with single_session.get(url, headers=headers, stream=True, timeout=self.timeout, verify=self.check_certificate) as response:
                response.raise_for_status()
                
                # If server sent full content (200 OK) but we asked for partial (206)
                if response.status_code == 200 and existing_size > 0 and headers.get('Range'):
                    logger.sendToLog(f"Server ignored Range header for {self.basename}. Restarting download from beginning.", 'WARNING')
                    mode = 'wb' # Overwrite
                    with QMutexLocker(self.download_mutex):
                        self.downloaded = 0 # Reset downloaded count
                
                # Update total size if it was unknown (e.g., from _get_file_metadata for a non-seekable resource)
                if self.total_size == 0 and 'content-length' in response.headers:
                    self.total_size = int(response.headers.get('content-length'))
                    logger.sendToLog(f"Updated total size for single download: {self.total_size} bytes", 'INFO')

                with open(save_path, mode) as file:
                    for chunk in response.iter_content(chunk_size=self.python_request_chunk_size):
                        if not chunk: continue
                        
                        # Check pause/cancel using the main task mutex
                        if self._handle_pause_cancel():
                            logger.sendToLog(f"Single download for {self.basename} paused or canceled.", 'INFO')
                            return
                            
                        file.write(chunk)
                        self._update_download_progress(len(chunk)) # Updates self.downloaded and history
                        
                        if self.max_speed > 0:
                            self._limit_speed(len(chunk)) # Applies overall speed limit

        except requests.exceptions.RequestException as e:
            raise Exception(f"Single file download network error for {self.basename}: {e}")
        except Exception as e:
            raise Exception(f"Unexpected error in single file download for {self.basename}: {e}")

    def _get_existing_size(self, save_path):
        """Gets current size of file on disk."""
        if os.path.exists(save_path):
            return os.path.getsize(save_path)
        return 0

    def _handle_pause_cancel(self):
        """
        Handles pause and cancel states for single-threaded or ffmpeg downloads.
        Blocks execution if paused, returns True if canceled.
        """
        with QMutexLocker(self.mutex): # Use the main task mutex for pause/cancel
            while self._paused or self._canceled:
                if self._canceled:
                    return True # Signal that it was canceled
                self.condition.wait(self.mutex) # Wait until unpaused or canceled
            return False # Not canceled, proceed

    def _update_download_progress(self, chunk_size):
        """
        Updates total downloaded for single-threaded downloads.
        For multi-threaded, progress is aggregated by _on_part_progress.
        """
        with QMutexLocker(self.download_mutex): # Use download_mutex for overall progress
            self.downloaded += chunk_size
            self.history.append((time.time(), self.downloaded))
        self.status_changed.emit(self.status) # Signal UI for progress update

    def _limit_speed(self, chunk_size):
        """Applies overall speed limit for single-threaded downloads."""
        if self.max_speed > 0:
            delay = chunk_size / self.max_speed
            time.sleep(delay)

    # --- FFmpeg related methods (unchanged for multi-threading logic) ---
    def _download_hls_stream(self, url, save_path):
        """Downloads HLS streams using FFmpeg."""
        logger.sendToLog(f"Downloading HLS stream with FFmpeg: {url} to {save_path}", 'INFO')
        if os.path.exists(save_path):
            os.remove(save_path) # Overwrite existing partial HLS file if any
        
        ext = os.path.splitext(save_path)[1][1:]  # Get extension without dot
        cmd = self._build_ffmpeg_command(url, save_path, ext)
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # Redirect stderr to stdout to capture all output
            universal_newlines=True, # Decode stdout/stderr as text
            text=True, # Python 3.7+ equivalent of universal_newlines=True
            bufsize=1 # Line-buffered output
        )
        
        try:
            self._monitor_ffmpeg_process(process, save_path)
            # FFmpeg doesn't reliably report total size upfront, so use accumulated downloaded as total
            if not self._canceled and self.status != "ERROR":
                self.total_size = self.downloaded 
                logger.sendToLog(f"HLS stream download finished for {self.basename}. Final estimated total size: {self.total_size} bytes.", 'INFO')
        except Exception as e:
            logger.sendToLog(f"FFmpeg HLS download failed for {self.basename}: {e}", 'ERROR')
            raise e
        finally:
            self._cleanup_ffmpeg_process(process, save_path)

    def _build_ffmpeg_command(self, url, save_path, ext):
        """Constructs the FFmpeg command based on URL and target extension."""
        cmd = [
            'ffmpeg',
            '-i', url,
            '-c', 'copy', # Copy audio/video streams without re-encoding
            '-f', ext,    # Output format
            save_path     # Output file path
        ]
        if ext == 'mp4': # Specific optimization for MP4 containers
            cmd.extend(['-bsf:a', 'aac_adtstoasc'])
        return cmd

    def _monitor_ffmpeg_process(self, process, save_path):
        """Monitors FFmpeg output for progress and handles pause/cancel."""
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None: # No more output and process has exited
                break
                
            if line:
                self._process_ffmpeg_output(line)
                
            # Check for pause/cancel periodically
            if self._handle_pause_cancel():
                logger.sendToLog(f"FFmpeg process for {self.basename} terminated due to pause/cancel request.", 'INFO')
                process.terminate() # Terminate the FFmpeg process
                return # Exit monitoring loop early
                
        self._check_ffmpeg_exit_code(process, save_path) # Check FFmpeg's exit status

    def _process_ffmpeg_output(self, line):
        """Parses FFmpeg output line to extract download size and update progress."""
        line = line.strip()
        try:
            size_val_bytes = 0
            # Look for common 'size=XXXkB' or 'size=XXXMB' patterns
            size_match = re.search(r'size=\s*(\d+\.?\d*)\s*(kB|MB|GB)', line, re.IGNORECASE)
            if size_match:
                size, unit = float(size_match.group(1)), size_match.group(2)
                size_val_bytes = self._convert_to_bytes(size, unit)
            # More general capture for any bytes value if above regex fails
            elif "bytes=" in line:
                 bytes_match = re.search(r'bytes=(\d+)', line)
                 if bytes_match:
                     size_val_bytes = int(bytes_match.group(1))

            if size_val_bytes > 0:
                with QMutexLocker(self.download_mutex): # Protect overall download progress update
                    if size_val_bytes > self.downloaded: # Only update if new size is larger
                        self.downloaded = size_val_bytes
                        self.history.append((time.time(), size_val_bytes))
                    self.status_changed.emit(self.status) # Signal UI for progress update
        except (ValueError, TypeError, AttributeError) as e:
            logger.sendToLog(f"Could not parse FFmpeg output line for size: '{line}' - Error: {e}", 'DEBUG')
            pass # Ignore lines that don't contain parsable size info or cause conversion errors

    def _convert_to_bytes(self, size, unit):
        """Converts a given size and unit string (e.g., '10.5MB') to bytes."""
        unit = unit.lower()
        multipliers = {
            'gb': 1024**3, 'gib': 1024**3,
            'mb': 1024**2, 'mib': 1024**2,
            'kb': 1024,    'kib': 1024,
            'b': 1
        }
        return float(size) * multipliers.get(unit, 1)

    def _check_ffmpeg_exit_code(self, process, save_path):
        """Checks the exit code of the FFmpeg process and raises an exception if it failed."""
        retcode = process.wait()
        if retcode != 0:
            raise Exception(f"FFmpeg failed with exit code {retcode} for {self.basename}")

    def _cleanup_ffmpeg_process(self, process, save_path):
        """Ensures the FFmpeg process is terminated and cleans up partial files if canceled."""
        if process.poll() is None: # If process is still running
            process.terminate() # Terminate it
            
        if self._canceled and os.path.exists(save_path):
            logger.sendToLog(f"Removing partially downloaded HLS file {save_path} due to cancellation.", 'INFO')
            os.remove(save_path)


class DownloadWorker(QThread):
    """
    A QThread wrapper for a DownloadTask instance.
    Runs the DownloadTask's main `download()` method in a separate thread.
    """
    def __init__(self, task):
        """
        Initializes the worker with a DownloadTask.

        Args:
            task (DownloadTask): The download task instance to run.
        """
        super().__init__()
        self.task = task

    def run(self):
        """
        Executes the DownloadTask's main download logic.
        Catches any unhandled exceptions from the task.
        """
        try:
            self.task.download()
        except Exception as e:
            # The task's _handle_error should have already emitted the signal.
            # This just ensures the QThread stops gracefully and logs any unexpected errors.
            logger.sendToLog(f"DownloadWorker for {self.task.basename} caught unhandled exception: {e}", 'CRITICAL')


class DownloadManager(QObject):
    """
    Manages a queue of DownloadTask instances, controlling their concurrency
    and overall download flow.
    """
    queue_updated = Signal() # Emitted when the download queue or active tasks change

    def __init__(self, parent):
        """
        Initializes the DownloadManager.

        Args:
            parent (QObject): The parent QObject (e.g., the main application window).
        """
        super().__init__()
        self.tasks = deque() # Queue of DownloadTask objects
        self.active_tasks = {} # Dictionary of active DownloadWorker threads {id(task): worker_thread}
        self.mutex = QMutex() # Mutex for managing tasks and active_tasks lists
        self.main_window = parent # Reference to the main application window
        
        # Get global configuration from parent or provide defaults
        self.config = getattr(parent, 'config', {}) 
        self.max_speed = self.config.get('max_speed', 0) # Global overall max speed (bytes/sec)
        self.max_workers = self.config.get('concurrency', 3) # Max concurrent downloads (tasks)

    def add_download(self, items):
        """
        Adds a new download task to the queue.

        Args:
            items (dict): Dictionary containing details for the new download task.
        """
        # Pass the global configs to the DownloadTask so it has access to them
        task = DownloadTask(items, self.max_speed, self.config)
        with QMutexLocker(self.mutex):
            self.tasks.append(task)
        self.queue_updated.emit() # Notify UI about the new task
        self.process_queue() # Try to start downloads if capacity is available

    def process_queue(self):
        """
        Manages the download queue, starting new tasks if capacity allows.
        Also cleans up completed/errored/canceled tasks.
        """
        with QMutexLocker(self.mutex):
            # Clean up finished/error/canceled tasks from the queue
            self.tasks = deque([t for t in t in self.tasks if t.status not in ["Completed", "ERROR", "Canceled"]])

            # Start new tasks if there's capacity and tasks in queue
            while self.tasks and len(self.active_tasks) < self.max_workers:
                task = self._find_next_queued_task()
                if not task: # No more queued tasks to start
                    break
                    
                self._start_task(task)

    def _find_next_queued_task(self):
        """Finds the next 'Queued' task in the queue."""
        for task in self.tasks:
            if task.status == "Queued":
                return task
        return None

    def _start_task(self, task):
        """Starts a DownloadTask by assigning it to a DownloadWorker thread."""
        worker = DownloadWorker(task)
        # Connect DownloadTask signals to DownloadManager's slots for overall management
        task.status_changed.connect(self._handle_task_update)
        task.finished.connect(self._handle_task_completion)
        task.error_occurred.connect(self._handle_task_error)
        
        task.start_download() # Set task status to "Downloading"
        self.active_tasks[id(task)] = worker # Keep track of active workers
        worker.start() # Start the QThread that runs DownloadTask.download()
        self.queue_updated.emit() # Notify UI about new active task

    def _handle_task_update(self):
        """Slot to handle status updates from any DownloadTask."""
        self.queue_updated.emit() # Re-emit to signal UI update

    @Slot(str)
    def _handle_task_completion(self, filename):
        """Slot for when a DownloadTask completes successfully."""
        logger.sendToLog(f"Download task '{filename}' completed successfully.", 'INFO')
        self._cleanup_task_worker(filename) # Clean up the worker thread associated with this task
        self._show_tray_notification(
            "Download Complete",
            f"{filename} downloaded successfully!",
            QSystemTrayIcon.MessageIcon.Information
        )
        self.queue_updated.emit() # Update UI one last time

    @Slot(str, str)
    def _handle_task_error(self, filename, error):
        """Slot for when a DownloadTask encounters an error."""
        logger.sendToLog(f"Download task '{filename}' error: {error}", 'ERROR')
        self._cleanup_task_worker(filename) # Clean up the worker thread
        self._show_tray_notification(
            "Download Failed",
            f"{filename} failed to download! Error: {error}",
            QSystemTrayIcon.MessageIcon.Critical
        )
        self.queue_updated.emit() # Update UI one last time


    def _cleanup_task_worker(self, filename):
        """
        Cleans up the DownloadWorker thread associated with a finished (completed/error/canceled) task.
        """
        with QMutexLocker(self.mutex): # Protect active_tasks dictionary
            # Find the task's worker by basename (could be more robust with task ID)
            task_to_remove_id = None
            for task_id, worker_obj in self.active_tasks.items():
                if worker_obj.task.basename == filename:
                    task_to_remove_id = task_id
                    # Ensure worker thread is stopped gracefully
                    worker_obj.quit()
                    worker_obj.wait(5000) # Wait up to 5 seconds for the thread to exit
                    break
            
            if task_to_remove_id is not None:
                del self.active_tasks[task_to_remove_id]
                logger.sendToLog(f"Cleaned up worker for task '{filename}'.", 'INFO')
            else:
                logger.sendToLog(f"Worker for task '{filename}' not found in active tasks during cleanup.", 'WARNING')

        # After cleanup, try to process the queue again for next available tasks
        self.process_queue()


    def _show_tray_notification(self, title, message, icon):
        """Shows a system tray notification."""
        # Check if sys_tray icon object exists and is valid before trying to use it
        if hasattr(self.main_window, 'sys_tray') and self.main_window.sys_tray:
            self.main_window.sys_tray.showMessage(
                title,
                message,
                icon,
                2000 # Display duration in ms
            )
        else:
            logger.sendToLog(f"System tray icon not available. Skipping notification: '{title}' - '{message}'", 'INFO')

    def pause_download(self, task):
        """Pauses a specific download task."""
        task.pause()
        self.queue_updated.emit()

    def resume_download(self, task):
        """Resumes a specific download task."""
        task.resume()
        self.queue_updated.emit()

    def cancel_download(self, task):
        """Cancels a specific download task."""
        task.cancel() # Signal the task to cancel internally
        
        with QMutexLocker(self.mutex):
            # Also remove it from the main queue if it was pending and not yet started
            if task in self.tasks:
                self.tasks.remove(task)
        
        # Explicitly clean up its worker thread if it was active
        self._cleanup_task_worker(task.basename)
        self.queue_updated.emit() # Notify UI of the change
        self.process_queue() # Try to start next download if capacity is now free

    def set_max_speed(self, task, max_speed):
        """Sets the maximum download speed for a specific task."""
        task.set_max_speed(max_speed)
        self.queue_updated.emit()

    def set_concurrency(self, max_workers):
        """Sets the maximum number of concurrent downloads managed by the manager."""
        with QMutexLocker(self.mutex):
            self.max_workers = max_workers
        self.process_queue() # Re-process queue if concurrency limit changed

    def shutdown(self):
        """Initiates a graceful shutdown of all active download tasks."""
        logger.sendToLog("DownloadManager shutting down. Attempting to cancel all active downloads.", 'INFO')
        with QMutexLocker(self.mutex):
            # Iterate over a copy of active_tasks values as the original dict will be modified during cleanup
            for worker in list(self.active_tasks.values()):
                worker.task.cancel() # Signal the task to cancel
                worker.quit() # Request the worker thread to terminate
                worker.wait(2000) # Give some time for threads to gracefully exit
            self.active_tasks.clear() # Clear the dictionary of active tasks
            self.tasks.clear() # Clear the pending task queue
        self.queue_updated.emit() # Emit update for final UI state