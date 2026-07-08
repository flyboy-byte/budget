import subprocess
import sqlite3
import sys
from pathlib import Path

import pytest

from migrations.runner import MIGRATIONS_DIR


@pytest.fixture
def real_db(tmp_path):
    db_path = tmp_path / "budget.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(migration.read_text())
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (migration.stem,))
    conn.execute(
        """INSERT INTO users (id, username, password_hash, totp_secret_encrypted, totp_enabled, auth_mode)
           VALUES (1, 'logan', 'x', X'deadbeef', 1, 'totp')"""
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at, csrf_secret) VALUES ('sess1', 1, '2099-01-01', 'x')"
    )
    conn.commit()
    conn.close()
    return db_path


def run_script(args: list[str], db_path: Path):
    import os

    env = {**os.environ, "BUDGET_DB_PATH": str(db_path)}
    repo_root = Path(__file__).resolve().parent.parent
    return subprocess.run(
        [sys.executable, "-m", "scripts.reset_totp", *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )


def test_reset_totp_clears_state_reverts_mode_and_revokes_sessions(real_db):
    result = run_script(["logan"], real_db)
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(real_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT totp_secret_encrypted, totp_enabled, auth_mode FROM users WHERE id = 1"
    ).fetchone()
    assert row["totp_secret_encrypted"] is None
    assert row["totp_enabled"] == 0
    assert row["auth_mode"] == "password"
    assert conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 0
    conn.close()


def test_reset_totp_unknown_user_fails(real_db):
    result = run_script(["nobody"], real_db)
    assert result.returncode == 1
