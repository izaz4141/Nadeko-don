from PySide6.QtCore import (
    QThread, QObject, Signal, QMutex, 
    QMutexLocker, Slot, QWaitCondition
)
from PySide6.QtWidgets import QSystemTrayIcon
from PySide6.QtGui import QIcon

from utils.ffmpeg import combine_video_audio
from utils.helpers import *

import os, time, requests, subprocess, threading, math, json
from collections import deque
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class DownloadTask(QObject):
    status_changed = Signal(str)
    finished = Signal(str)
    error_occurred = Signal(str, str)

    def __init__(self, items, max_speed=0, max_connections=8):
        super().__init__()
        self.items = items
        self.urls = items['urls']
        self.save_path = items['path']
        self.basename = os.path.basename(items['path'])
        self.max_speed = max_speed * 1024 # In KiloBytes
        self.max_connections = max_connections
        self.status = "Queued"
        self.downloaded = 0
        self.total_size = 0
        self.speed = 0
        self.history = deque(maxlen=60)
        self._paused = False
        self._canceled = False
        self.mutex = QMutex()
        self.condition = QWaitCondition()
        self.ytdl = items['type'] == 'ytdl'
        self.start_time = None

        # Multithreading support
        self.part_threads = []
        self.part_info = []
        self.part_lock = threading.Lock()
        self.part_cancel_event = threading.Event()
        self.part_pause_event = threading.Event()
        self.part_pause_condition = threading.Condition()
        self.progress_lock = threading.Lock()
        self.completed_parts = 0
        self.session = self.create_session(["GET", "HEAD"])

    def start_download(self):
        with QMutexLocker(self.mutex):
            self._set_status("Downloading")
            self._paused = False
            self._canceled = False

    def pause(self):
        with QMutexLocker(self.mutex):
            if self.status == "Downloading":
                self._paused = True
                self._set_status("Paused")
                self.part_pause_event.set()

    def resume(self):
        with QMutexLocker(self.mutex):
            if self.status == "Paused":
                self._paused = False
                self._set_status("Downloading")
                self.condition.wakeAll()
                self.part_pause_event.clear()
                with self.part_pause_condition:
                    self.part_pause_condition.notify_all()

    def cancel(self):
        with QMutexLocker(self.mutex):
            if not self._canceled:
                self._canceled = True
                self._set_status("Canceled")
                self.condition.wakeAll()
                self.part_cancel_event.set()
                with self.part_pause_condition:
                    self.part_pause_condition.notify_all()

    def set_max_speed(self, max_speed):
        with QMutexLocker(self.mutex):
            self.max_speed = max_speed * 1024

    def create_session(self, methods: list):
        session = requests.Session()
        retry_strategy = retry_strategy = Retry(
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
        self.start_time = time.time()
        try:
            for i, url in enumerate(self.urls):
                if self._canceled:
                    return
                    
                save_path = self._get_save_path(i)
                self._download_url(url, save_path)
                
            self.total_size = self.downloaded
            
            if self.ytdl:
                self._combine_ytdl_files()
                
            self._handle_success()
                    
        except Exception as e:
            self._handle_error(e)

    def _set_status(self, status):
        self.status = status
        self.status_changed.emit(status)

    def _get_save_path(self, index):
        if self.ytdl:
            file_type = 'video' if index == 0 else 'audio'
            return f"{self.save_path}.{file_type}"
        return self.save_path

    def _combine_ytdl_files(self):
        video_path = f"{self.save_path}.video"
        audio_path = f"{self.save_path}.audio"
        if os.path.exists(video_path) and os.path.exists(audio_path):
            combine_video_audio(video_path, audio_path, self.save_path)

    def _handle_success(self):
        with QMutexLocker(self.mutex):
            if not self._canceled:
                self._set_status("Completed")
                self.finished.emit(self.basename)

    def _handle_error(self, exception):
        with QMutexLocker(self.mutex):
            if not self._canceled:
                self._set_status("ERROR")
                self.error_occurred.emit(self.basename, str(exception))
        raise exception

    def _download_url(self, url, save_path):
        if is_m3u8_url(url):
            self._download_hls_stream(url, save_path)
        else:
            self._download_http_file(url, save_path)

    def _download_http_file(self, url, save_path):
        self._get_url_metadata(url)
        self._prepare_file(save_path)
        if self.is_multithreaded:
            self._download_multiThreads(url, save_path)
        else:
            self._download_single_http(url, save_path)


    def _download_single_http(self, url, save_path):
        existing_size = self._get_existing_size(save_path)
        if existing_size > 0 and self.accept_ranges_support:
            if existing_size < self.total_size:
                headers = {"Range": f"bytes={existing_size}-"} 
            else:
                os.remove(save_path)
                self.downloaded = 0
                headers = {}
        else:
            headers = {}
        with self.session.get(url, headers=headers, stream=True, timeout=30) as response:
            response.raise_for_status()
            mode = 'ab' if headers != {} else 'wb'
            with open(save_path, mode) as file:
                for chunk in response.iter_content(chunk_size=1024*256):
                    if not chunk:
                        continue
                        
                    with QMutexLocker(self.mutex):
                        while self._paused or self._canceled:
                            if self._canceled:
                                return
                            self.condition.wait(self.mutex)
                    start_time = time.time()
                    file.write(chunk)
                    chunk_duration = time.time() - start_time
                    self._update_download_progress(len(chunk))
                    
                    if self.max_speed > 0:
                        self._limit_speed(len(chunk), chunk_duration)

    def _get_existing_size(self, save_path):
        if os.path.exists(save_path):
            return os.path.getsize(save_path)
        return 0

    def _get_url_metadata(self, url):
        try:
            response = self.session.head(url, allow_redirects=True, timeout=30)
            response.raise_for_status()
            self.total_size = int(response.headers.get('content-length', 0))
            self.accept_ranges_support = response.headers.get('accept-ranges', '').lower() == 'bytes'
        except Exception as e:
            raise e

    def _prepare_file(self, save_path):
        self.current_save_path = save_path
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        existing_file_size = self._get_existing_size(save_path)
        
        # Load existing metadata if available
        metadata = self._load_metadata(save_path)
        
        # Determine if we can resume
        can_resume = (self.total_size > 0 and 
                      existing_file_size > 0 and 
                      existing_file_size < self.total_size)
        
        if self.total_size > 0:
            if ((not can_resume or existing_file_size > self.total_size)
                 and self.max_connections > 1):
                pre_allocate_file(save_path, self.total_size)
                self.downloaded = 0
                existing_file_size = 0
                # Clear any existing metadata
                self._delete_metadata(save_path)
            else:
                # For resume, use existing file size
                self.downloaded = existing_file_size
        else:
            if not os.path.exists(save_path):
                open(save_path, 'wb').close()
            self.downloaded = existing_file_size

        # Initialize multi-threading if supported
        min_part_size = 1024 * 1024  # 1 MiB
        if (self.accept_ranges_support and 
            self.max_connections > 1 and 
            self.total_size > min_part_size * self.max_connections):
            
            self.is_multithreaded = True
            self.num_parts = min(self.max_connections, 
                                math.ceil(self.total_size / min_part_size))
            part_size = self.total_size // self.num_parts
            self.part_info = []
            self.completed_parts = 0

            # Try to load part info from metadata
            if metadata and 'parts' in metadata:
                self.part_info = metadata['parts']
                self.completed_parts = sum(1 for p in self.part_info if p['status'] == 'complete')
                self.downloaded = sum(p['downloaded'] for p in self.part_info)
            else:
                # Create new part info
                for i in range(self.num_parts):
                    start = i * part_size
                    end = (i + 1) * part_size - 1 if i < self.num_parts - 1 else self.total_size - 1
                    
                    part_downloaded = 0
                    if existing_file_size > start:
                        # Only count existing data that falls within this part
                        part_downloaded = min(existing_file_size - start, end - start + 1)
                    
                    self.part_info.append({
                        'start': start,
                        'end': end,
                        'downloaded': part_downloaded,
                        'status': 'complete' if part_downloaded == (end - start + 1) else 'pending',
                        'retries': 0
                    })
                # Save initial metadata
                self._save_metadata(save_path)
        else:
            self.is_multithreaded = False
            # Clean up any leftover metadata
            self._delete_metadata(save_path)

    def _save_metadata(self, save_path):
        """Save download metadata to a file for resuming"""
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
                json.dump(data, f)
        except Exception as e:
            print(f"Failed to save metadata: {e}")

    def _load_metadata(self, save_path):
        """Load download metadata from file if available"""
        metadata_path = f"{save_path}.metadata"
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r') as f:
                    return json.load(f)
            except Exception:
                # Corrupted metadata, delete it
                self._delete_metadata(save_path)
        return None

    def _delete_metadata(self, save_path):
        """Delete metadata file"""
        metadata_path = f"{save_path}.metadata"
        if os.path.exists(metadata_path):
            try:
                os.remove(metadata_path)
            except Exception:
                pass
    
    def _download_multiThreads(self, url, save_path):
        self.part_threads = []
        self.part_cancel_event.clear()
        self.part_pause_event.clear()
        
        # Create all threads for incomplete parts only
        for part_index in range(len(self.part_info)):
            part = self.part_info[part_index]
            if part['status'] != 'complete':  # Only create threads for incomplete parts
                thread = threading.Thread(
                    target=self._download_part,
                    args=(url, save_path, part_index)
                )
                thread.daemon = True
                self.part_threads.append(thread)
        
        # Start all threads
        for thread in self.part_threads:
            thread.start()
        
        last_save_time = time.time()
        save_interval = 5  # Save every 5 seconds
        
        while self.part_threads:
            time.sleep(1)
            
            current_time = time.time()
            if current_time - last_save_time >= save_interval:
                self._save_metadata(save_path)
                last_save_time = current_time
            
            # Check for completed threads
            for i in range(len(self.part_threads) - 1, -1, -1):
                if not self.part_threads[i].is_alive():
                    self.part_threads[i].join()  # Clean up thread resources
                    del self.part_threads[i]

            if self.part_pause_event.is_set():
                self._save_metadata(save_path)
                with self.part_pause_condition:
                    while self.part_pause_event.is_set() and not self.part_cancel_event.is_set():
                        self.part_pause_condition.wait()
                
            # Check if any parts failed
            failed_parts = [p for p in self.part_info if p['status'] == 'failed']
            if failed_parts:
                raise Exception(f"{len(failed_parts)} part(s) failed to download")
            
            if self.part_cancel_event.is_set():
                self._save_metadata(save_path)
                break

        
        self._save_metadata(save_path)
        
        
        # Clean up metadata only on successful completion
        if not failed_parts and not self.part_cancel_event.is_set():
            self._delete_metadata(save_path)

    def _download_part(self, url, save_path, part_index):
        part = self.part_info[part_index]

        if part['status'] == 'complete':
            return
        
        start = part['start'] + part['downloaded']
        end = part['end']
        
        # Skip if already completed
        if start > end:
            with self.part_lock:
                part['status'] = 'complete'
                self.completed_parts += 1
            return

        headers = {'Range': f'bytes={start}-{end}'}
        
        try:
            with self.session.get(url, headers=headers, stream=True, timeout=30) as response:
                response.raise_for_status()
                if response.status_code != 206:
                    raise Exception("Server does not support partial content")
                
                # CRITICAL FIX: Always open in r+b mode and seek to position
                with open(save_path, 'r+b') as f:
                    f.seek(start)
                    
                    for chunk in response.iter_content(chunk_size=1024*256):
                        if not chunk:
                            continue
                            
                        # Handle pause/cancel
                        if self.part_cancel_event.is_set():
                            return
                            
                        # Handle pause
                        if self.part_pause_event.is_set():
                            with self.part_pause_condition:
                                while self.part_pause_event.is_set() and not self.part_cancel_event.is_set():
                                    self.part_pause_condition.wait()
                                if self.part_cancel_event.is_set():
                                    return
                        chunk_len = len(chunk)

                        start_time = time.time()
                        f.write(chunk)
                        chunk_duration = time.time() - start_time
                        
                        # Update progress
                        with self.progress_lock:
                            self.downloaded += chunk_len
                            self.history.append((time.time(), self.downloaded))
                        
                        # Update part info
                        with self.part_lock:
                            part['downloaded'] += chunk_len
                        
                        # Speed limiting
                        if self.max_speed > 0:
                            self._limit_speed(chunk_len, chunk_duration,
                                 self.max_speed/len(self.part_threads))
            
            # Mark part as completed
            with self.part_lock:
                part['status'] = 'complete'
                self.completed_parts += 1
                
        except Exception as e:
            with self.part_lock:
                part['status'] = 'failed'
                part['error'] = str(e)
            self.cancel()

    def _update_download_progress(self, chunk_size):
        with QMutexLocker(self.mutex):
            self.downloaded += chunk_size
        self.history.append((time.time(), self.downloaded))

    def _limit_speed(self, chunk_size, chunk_duration, max_speed=None):
        max_speed = max_speed or self.max_speed
        desired_time = chunk_size / max_speed
        if chunk_duration < desired_time:
            time.sleep(desired_time - chunk_duration)

    def _download_hls_stream(self, url, save_path):
        if os.path.exists(save_path):
            os.remove(save_path)
        ext = os.path.splitext(self.save_path)[1].split('.')[1]
        cmd = self._build_ffmpeg_command(url, save_path, ext)
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            text=True
        )
        
        try:
            self._monitor_ffmpeg_process(process, save_path)
        finally:
            self._cleanup_ffmpeg_process(process, save_path)

    def _build_ffmpeg_command(self, url, save_path, ext):
        cmd = [
            'ffmpeg',
            '-i', url,
            '-c', 'copy',
            '-f', ext,
            save_path
        ]
        if ext == 'mp4':
            cmd.extend(['-bsf:a', 'aac_adtstoasc'])
        return cmd

    def _monitor_ffmpeg_process(self, process, save_path):
        while True:
            line = process.stdout.readline()
            print(line)
            if not line and process.poll() is not None:
                break
                
            if line:
                self._process_ffmpeg_output(line)
                
        self._check_ffmpeg_exit_code(process, save_path)

    def _process_ffmpeg_output(self, line):
        line = line.strip()
        try:
            size, unit = extract_size_info(line)
            if size and unit:
                size_val = self._convert_to_bytes(size, unit)
                with QMutexLocker(self.mutex):
                    self.downloaded = size_val
                    self.history.append((time.time(), size_val))
        except (ValueError, TypeError):
            pass

    def _convert_to_bytes(self, size, unit):
        unit = unit.lower()
        multipliers = {
            'gb': 1024**3, 'gib': 1024**3,
            'mb': 1024**2, 'mib': 1024**2,
            'kb': 1024,    'kib': 1024,
            'b': 1
        }
        return float(size) * multipliers.get(unit, 1)

    def _check_ffmpeg_exit_code(self, process, save_path):
        retcode = process.wait()
        if retcode != 0:
            raise Exception(f"FFmpeg failed with code {retcode}")

    def _cleanup_ffmpeg_process(self, process, save_path):
        if process.poll() is None:
            process.terminate()
            
        if self._canceled and os.path.exists(save_path):
            os.remove(save_path)


class DownloadWorker(QThread):
    def __init__(self, task):
        super().__init__()
        self.task = task

    def run(self):
        self.task.download()


class DownloadManager(QObject):
    queue_updated = Signal()

    def __init__(self, parent):
        super().__init__()
        self.tasks = deque()
        self.active_tasks = {}
        self.mutex = QMutex()
        self.main_window = parent
        self.max_speed = parent.config['max_speed']
        self.max_workers = parent.config['max_workers']

    def add_download(self, items):
        task = DownloadTask(items, self.max_speed, self.main_window.config['concurrency'])
        with QMutexLocker(self.mutex):
            self.tasks.append(task)
        self.queue_updated.emit()
        self.process_queue()

    def process_queue(self):
        with QMutexLocker(self.mutex):
            while self.tasks and len(self.active_tasks) < self.max_workers:
                task = self._find_next_queued_task()
                if not task:
                    break
                    
                self._start_task(task)

    def _find_next_queued_task(self):
        for task in self.tasks:
            if task.status == "Queued":
                return task
        return None

    def _start_task(self, task):
        worker = DownloadWorker(task)
        task.status_changed.connect(self._handle_task_update)
        task.finished.connect(self._handle_task_completion)
        task.error_occurred.connect(self._handle_task_error)
        
        task.start_download()
        self.active_tasks[id(task)] = worker
        worker.start()
        self.queue_updated.emit()

    def _handle_task_update(self):
        self.queue_updated.emit()

    def _handle_task_completion(self, filename):
        self._cleanup_task(filename)
        self._show_tray_notification(
            "Download Complete",
            f"{filename} downloaded successfully!",
            QSystemTrayIcon.MessageIcon.Information
        )
        self.queue_updated.emit()
        self.process_queue()

    def _handle_task_error(self, filename, error):
        self._cleanup_task(filename)
        self._show_tray_notification(
            "Download Failed",
            f"{filename} failed to download!",
            QSystemTrayIcon.MessageIcon.Critical
        )
        self.queue_updated.emit()
        self.process_queue()

    def _cleanup_task(self, filename):
        with QMutexLocker(self.mutex):
            task_id = next((tid for tid, t in self.active_tasks.items() 
                           if t.task.basename == filename), None)
            if task_id:
                del self.active_tasks[task_id]
        self.process_queue()

    def _show_tray_notification(self, title, message, icon):
        self.main_window.sys_tray.showMessage(
            title,
            message,
            icon,
            2000
        )

    def pause_download(self, task):
        for task in self.tasks:
            task.pause()

    def resume_download(self, task):
        for task in self.tasks:
            task.resume()

    def cancel_download(self, task):
        for task in self.tasks:
            task.cancel()
            with QMutexLocker(self.mutex):
                task_id = id(task)
                if task_id in self.active_tasks:
                    worker = self.active_tasks[task_id]
                    worker.quit()
                    worker.wait(1000)
                    if task_id in self.active_tasks:
                        del self.active_tasks[task_id]
                elif task in self.tasks:
                    self.tasks.remove(task)
        self.queue_updated.emit()
        self.process_queue()

    def set_max_speed(self):
        for task in self.tasks:
            task.set_max_speed(self.main_window.config['max_speed'])
    
    def set_max_workers(self):
        self.max_workers = self.parent.config['max_workers']
        self.process_queue()

    def shutdown(self):
        with QMutexLocker(self.mutex):
            for worker in self.active_tasks.values():
                worker.quit()
                worker.wait(1000)
            self.active_tasks.clear()
            self.tasks.clear()