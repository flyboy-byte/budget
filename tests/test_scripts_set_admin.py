import sqlite3
import subprocess
import sys
from pathlib import Path

from migrations.runner import MIGRATIONS_DIR


def _fresh_db(tmp_path) -> Path:
    db_path = tmp_path / "budget.db"
    conn = sqlite3.connect(db_path)
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(migration.read_text())
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (migration.stem,))
    conn.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'logan', 'x')")
    conn.commit()
    conn.close()
    return db_path


def run_script(args, db_path: Path):
    import os

    env = {**os.environ, "BUDGET_DB_PATH": str(db_path)}
    repo_root = Path(__file__).resolve().parent.parent
    return subprocess.run(
        [sys.executable, "-m", "scripts.set_admin", *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )


def test_set_admin_on(tmp_path):
    db_path = _fresh_db(tmp_path)
    result = run_script(["logan", "on"], db_path)
    assert result.returncode == 0, result.stderr
    assert "is now an admin" in result.stdout

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT is_admin FROM users WHERE username = 'logan'").fetchone()[0] == 1
    conn.close()


def test_set_admin_off(tmp_path):
    db_path = _fresh_db(tmp_path)
    run_script(["logan", "on"], db_path)
    result = run_script(["logan", "off"], db_path)
    assert result.returncode == 0, result.stderr
    assert "is now not an admin" in result.stdout

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT is_admin FROM users WHERE username = 'logan'").fetchone()[0] == 0
    conn.close()


def test_set_admin_unknown_user_fails(tmp_path):
    db_path = _fresh_db(tmp_path)
    result = run_script(["nobody", "on"], db_path)
    assert result.returncode == 1


def test_set_admin_invalid_action_fails(tmp_path):
    db_path = _fresh_db(tmp_path)
    result = run_script(["logan", "maybe"], db_path)
    assert result.returncode == 1
