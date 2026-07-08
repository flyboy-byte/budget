import sqlite3

import pytest

from migrations.runner import MIGRATIONS_DIR


@pytest.fixture(autouse=True)
def _reset_ratelimit_state():
    """app.ratelimit is process-global in-memory state; without this, login-rate-limit
    tests would leak failed-attempt counts across tests in the same pytest run."""
    from app import ratelimit

    ratelimit._attempts.clear()
    yield
    ratelimit._attempts.clear()


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(migration.read_text())
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (migration.stem,))
    conn.commit()

    yield conn
    conn.close()


@pytest.fixture
def user_id(db):
    cur = db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        ("alice", "hash"),
    )
    db.commit()
    return cur.lastrowid
