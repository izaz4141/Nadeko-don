import json, threading
from sqlite3 import connect, Row

from utils.constants import CONFIG_DIRECTORY, SQL_BUILD

DB_PATH = f"{CONFIG_DIRECTORY}/nandeshiko-database.db"
DB_LOCK = threading.Lock()

cxn = connect(DB_PATH, check_same_thread=False)
cxn.row_factory = Row
cur = cxn.cursor()

def with_commit(func):
    def inner(*args, **kwargs):
        func(*args, **kwargs)
        commit()

    return inner

@with_commit
def build():
    scriptexec(SQL_BUILD)

def commit():
    cxn.commit()

# def autosave(sched):
#     sched.add_job(commit, CronTrigger(second=29))

def close():
    cxn.close()

def field(command, *values):
    cur.execute(command, tuple(values))
    fetch = cur.fetchone()
    if fetch is not None:
        return fetch[0]

def record(command, *values):
    cur.execute(command, tuple(values))
    return cur.fetchone()

def records(command, *values):
    cur.execute(command, tuple(values))
    return cur.fetchall()

def column(command, *values):
    cur.execute(command, tuple(values))
    return [item[0] for item in cur.fetchall()]

def execute(command, *values):
    cur.execute(command, tuple(values))

def multiexec(command,valueset):
    cur.executemany(command, valueset)

def scriptexec(script):
    cur.executescript(script)


def _row_to_dict(row: Row) -> dict:
    """Converts a SQLite row object to a dictionary, handling type conversions for the new schema."""
    task_dict = dict(row)
    # Convert JSON strings back to Python objects
    task_dict['items'] = json.loads(task_dict['items'])
    task_dict['timer'] = json.loads(task_dict['timer'])
    task_dict['metadata'] = json.loads(task_dict['metadata'])
    return task_dict