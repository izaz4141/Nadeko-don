from os.path import expanduser

CONFIG_DIRECTORY = expanduser("~/.config/Nadeko~don")

DEFAULT_CONFIG = {
    'port': 12345,
    'save_path': expanduser("~/Downloads"),
    'max_speed': 0,
    'max_workers': 3,
    'concurrency': 8,
    'yt_cookies': ""
}

SQL_BUILD = """
CREATE TABLE IF NOT EXISTS downloads (
    id TEXT PRIMARY KEY,
    start_time REAL,
    downloaded INTEGER,
    total_size INTEGER,
    items TEXT NOT NULL,
    timer TEXT,
    metadata TEXT,
    status TEXT
);
"""
