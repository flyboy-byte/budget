import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.db import get_connection
from migrations.runner import MIGRATIONS_DIR


@pytest.fixture
def real_db(tmp_path):
    db_path = tmp_path / "budget.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(migration.read_text())
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (migration.stem,))
    conn.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'logan', 'x')")
    conn.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (1, 'Checking', 'checking', 10000)"
    )
    conn.commit()
    conn.close()
    return db_path


def run_script(module: str, args: list[str], db_path: Path):
    import os

    env = {**os.environ, "BUDGET_DB_PATH": str(db_path)}
    repo_root = Path(__file__).resolve().parent.parent
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )


def test_backup_script_writes_json_file(real_db, tmp_path, monkeypatch):
    import scripts.backup as backup_module

    monkeypatch.setattr(backup_module, "BACKUP_DIR", tmp_path / "backups")

    with get_connection(real_db) as conn:
        path = backup_module.backup_user(conn, "logan", 1, keep=None)

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["tables"]["accounts"][0]["name"] == "Checking"


def test_backup_script_keep_prunes_old_backups(real_db, tmp_path, monkeypatch):
    import time

    import scripts.backup as backup_module

    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup_module, "BACKUP_DIR", backup_dir)

    with get_connection(real_db) as conn:
        for _ in range(5):
            backup_module.backup_user(conn, "logan", 1, keep=2)
            time.sleep(1.1)  # filenames are second-precision; ensure each call gets a distinct one

    remaining = sorted(backup_dir.glob("budget-backup-logan-*.json"))
    assert len(remaining) == 2


def test_backup_cli_runs_for_all_users(real_db):
    # scripts.backup writes under the real repo's data/backups/ (BASE_DIR-relative);
    # clean up the file this test creates so it doesn't linger in the working tree.
    from app.config import BASE_DIR

    backup_dir = BASE_DIR / "data" / "backups"
    before = set(backup_dir.glob("budget-backup-logan-*.json")) if backup_dir.exists() else set()

    result = run_script("scripts.backup", [], real_db)
    assert result.returncode == 0, result.stderr
    assert "Backed up 1 user" in result.stdout

    after = set(backup_dir.glob("budget-backup-logan-*.json"))
    for path in after - before:
        path.unlink()


def test_backup_cli_unknown_user_fails(real_db):
    result = run_script("scripts.backup", ["--user", "nobody"], real_db)
    assert result.returncode == 1


def test_reset_password_script_updates_hash_and_revokes_sessions(real_db):
    conn = sqlite3.connect(real_db)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at, csrf_secret) VALUES ('sess1', 1, '2099-01-01', 'x')"
    )
    conn.commit()
    old_hash = conn.execute("SELECT password_hash FROM users WHERE id = 1").fetchone()["password_hash"]
    conn.close()

    result = run_script("scripts.reset_password", ["logan", "newpassword123"], real_db)
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(real_db)
    conn.row_factory = sqlite3.Row
    new_hash = conn.execute("SELECT password_hash FROM users WHERE id = 1").fetchone()["password_hash"]
    assert new_hash != old_hash
    assert conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 0
    conn.close()


def test_reset_password_script_unknown_user_fails(real_db):
    result = run_script("scripts.reset_password", ["nobody", "x"], real_db)
    assert result.returncode == 1
