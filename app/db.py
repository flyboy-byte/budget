import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH


def connect(db_path=DB_PATH) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI runs sync dependencies (like get_db) in a worker
    # thread but async dependencies (like verify_csrf_token) on the event loop thread.
    # A single request's dependency chain can cross threads even though the connection
    # is only ever used sequentially within that one request, never concurrently.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_connection(db_path=DB_PATH):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
