from PySide6.QtCore import QObject, Signal

import json, threading
from sqlite3 import connect, Row

from utils.constants import CONFIG_DIRECTORY, SQL_BUILD

DB_PATH = f"{CONFIG_DIRECTORY}/nandeshiko-database.db"
DB_LOCK = threading.Lock()

def build(cxn, cur):
    scriptexec(cur, SQL_BUILD)
    commit(cxn)

def commit(cxn):
    cxn.commit()

def close(cxn):
    cxn.close()

def field(cur, command, *values):
    cur.execute(command, tuple(values))
    fetch = cur.fetchone()
    if fetch is not None:
        return fetch[0]

def record(cur, command, *values):
    cur.execute(command, tuple(values))
    return cur.fetchone()

def records(cur, command, *values):
    cur.execute(command, tuple(values))
    return cur.fetchall()

def column(cur, command, *values):
    cur.execute(command, tuple(values))
    return [item[0] for item in cur.fetchall()]

def execute(cur, command, *values):
    cur.execute(command, tuple(values))

def multiexec(cur, command,valueset):
    cur.executemany(command, valueset)

def scriptexec(cur, script):
    cur.executescript(script)


def row_to_dict(row: Row) -> dict:
    """Converts a SQLite row object to a dictionary, handling type conversions for the new schema."""
    task_dict = dict(row)
    task_items = task_dict.items()
    for key, value in task_items:
        if isinstance(value, str): # Handle JSON
            try:
                jeson = json.loads(value)
                task_dict[key] = jeson
            except Exception:
                pass
    return task_dict

class DownloadDB(QObject):

    def __init__(self, parent):
        super().__init__()
        self.cxn = connect(DB_PATH, check_same_thread=False)
        self.cxn.row_factory = Row
        self.cur = self.cxn.cursor()
        self.db_lock = threading.Lock()
        self.main_window = parent

        with self.db_lock:
            build(self.cxn, self.cur)

        self.main_window.threadReady.connect(self.connect_signal)

    def insert_or_update(self, task_data:dict):
        sql = """
            INSERT INTO downloads (
                id, start_time, downloaded, total_size, items, timer, metadata, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                start_time=excluded.start_time,
                downloaded=excluded.downloaded,
                total_size=excluded.total_size,
                items=excluded.items,
                timer=excluded.timer,
                metadata=excluded.metadata,
                status=excluded.status
        """
        try:
            with self.db_lock:
                execute(self.cur, sql, 
                    task_data['id'],
                    task_data['start_time'],
                    task_data['downloaded'],
                    task_data['total_size'],
                    json.dumps(task_data['items']), # Store dict as JSON string
                    json.dumps(task_data['timer']), # Store timer dict as JSON string
                    json.dumps(task_data['metadata']),
                    task_data['status']
                )
                commit(self.cxn)
        except Exception as e:
            self.main_window.logger.error(f"[DownloadDB] {str(e)}")

    def delete(self, task_id):
        try:
            with self.db_lock:
                execute(self.cur, "DELETE FROM downloads WHERE id = ?", task_id)
                commit(self.cxn)
        except Exception as e:
            self.main_window.logger.error(f"[DownloadDB] {str(e)}")

    def fetchone(self, column, task_id):
        data = field(self.cur, "SELECT metadata from downloads WHERE id = ?", task_id)
        return data

    def fetchall(self):
        datas = records(self.cur, "SELECT * from downloads")
        return datas

    def connect_signal(self):
        self.main_window.download_manager.db_update.connect(self.insert_or_update)
        self.main_window.download_manager.db_delete.connect(self.delete)
