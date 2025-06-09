import re, requests, os, sys
from urllib.parse import urlparse, unquote
from pathlib import Path

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and PyInstaller """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = Path(sys._MEIPASS)
    else:
        # Get the directory of the main script (main.py)
        if hasattr(sys, 'frozen'):  # Handle other bundlers if needed
            base_path = Path(os.path.dirname(sys.executable))
        else:
            # Use the directory of the main script (resolves to project root)
            base_path = Path(sys.argv[0]).resolve().parent
    
    return str(base_path / relative_path)

def format_durasi(durasi: int) -> str:
    durasi = int(durasi)
    donat = durasi
    if durasi >= 3600:
        jam = durasi // 3600
        donat = durasi % 3600
    if donat >= 60:
        menit = donat // 60
        detik = donat % 60
    else:
        menit = 0
        detik = donat

    if durasi >= 3600:
        return f"{jam}:{menit:02}:{detik:02}"
    else:
        return f"{menit}:{detik:02}"

def format_bytes(size_bytes):
    """Convert bytes to human-readable format"""
    if size_bytes <= 0:
        return "0 B"
    units = ('B', 'KB', 'MB', 'GB')
    unit_idx = 0
    
    while size_bytes >= 1024 and unit_idx < len(units)-1:
        size_bytes /= 1024.0
        unit_idx += 1
        
    return f"{size_bytes:.2f} {units[unit_idx]}" if unit_idx > 0 else f"{size_bytes} {units[unit_idx]}"

def get_speed(history):
    """Calculate average speed over last 30 seconds"""
    if len(history) < 2:
        return 0  # Not enough data
        
    # Get the oldest and most recent data points
    oldest_time, oldest_downloaded = history[0]
    newest_time, newest_downloaded = history[-1]
    
    time_diff = newest_time - oldest_time
    
    if time_diff <= 0:
        return 0
        
    data_diff = newest_downloaded - oldest_downloaded
    return data_diff / time_diff

def is_m3u8_url(url):
    """Check if URL points to an M3U8 stream"""
    parsed = urlparse(url)
    path = parsed.path.lower()
    return path.endswith('.m3u8') or path.endswith('.m3u')

def check_default(config:dict, default:dict):
    """Check the config and add any missing values"""
    for key, default_value in default.items():
        if key not in config.keys():
            config[key] = default_value
        else:
            current_value = config[key]
            if isinstance(default_value, dict) and isinstance(current_value, dict):
                check_default(current_value, default_value)

def extract_size_info(s):
    """Extract Size Info from FFMPEG output"""
    match = re.search(r'size=\s*([0-9]*\.?[0-9]+|[0-9]+\.?[0-9]*)([a-zA-Z]+)', s)
    if match:
        return (match.group(1), match.group(2))
    return (None, None)

def get_url_info(url):
    try:
        response = requests.head(url, allow_redirects=True, timeout=10)
        response.raise_for_status()  # Raise exception for HTTP errors
        
        content_length = response.headers.get('Content-Length') or 0
        ext = response.headers.get('Content-Type').split('/')[1] or ''
        content_disp = response.headers.get('Content-Disposition', '')
        match = re.search(r'filename\s*=\s*"?(.*?)"?($|;|\s)', content_disp)
        if match:
            filename = os.path.basename(unquote(match.group(1)).strip())
        else:
            filename = os.path.basename(unquote(urlparse(url).path).rstrip('/')) or 'unknown'
        cext = os.path.splitext(filename)[1]
        if cext == '':
            filename = f"{filename}.{ext}"
        # if ext != 'unknown':
        #     filename = f"{os.path.splitext(filename)[0]}.{ext}"
            
        return int(content_length), filename
    except Exception as e:
        raise e
        return None

def is_urlDownloadable(url):
    try:
        # Send HEAD request to get headers only
        response = requests.head(url, allow_redirects=True, timeout=10)
        response.raise_for_status()  # Raise error for HTTP status >= 400
        
        headers = response.headers
        
        # Check Content-Disposition for explicit download flag
        content_disp = headers.get('Content-Disposition', '').lower()
        if 'attachment' in content_disp:
            return True
        
        # Analyze Content-Type for non-webpage content
        content_type = headers.get('Content-Type', '').lower()
        
        # Common webpage types (likely to be displayed in browser)
        webpage_types = {
            'text/html',
            'application/xhtml+xml',
            'text/xml',
            'application/xml'
        }
        
        # Check if Content-Type indicates a webpage
        return not any(
            content_type.startswith(web_type) 
            for web_type in webpage_types
        )
    
    except requests.exceptions.RequestException:
        return False

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
    except Exception as e:
        # If pre-allocation fails (e.g., no space, or permission), still ensure file exists
        open(filepath, 'wb').close()