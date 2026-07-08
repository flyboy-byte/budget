import subprocess
import sqlite3
import sys
from pathlib import Path

from migrations.runner import MIGRATIONS_DIR


def _seeded_db(tmp_path: Path) -> Path:
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
        [sys.executable, "-m", "scripts.digest", *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )


def test_cli_unknown_user_fails(tmp_path):
    db_path = _seeded_db(tmp_path)
    result = run_script(["--user", "nobody"], db_path)
    assert result.returncode == 1


def test_cli_skips_user_with_no_digest_email(tmp_path):
    db_path = _seeded_db(tmp_path)
    result = run_script([], db_path)
    assert result.returncode == 0, result.stderr
    assert "skipped (no digest email set)" in result.stdout
    assert "push: none" in result.stdout
    assert "Processed 1 user(s)" in result.stdout


def test_cli_reports_bill_push_for_bill_due_today(tmp_path):
    db_path = _seeded_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) "
        "VALUES (1, 'Rent', 'housing', 120000, date('now'))"
    )
    conn.commit()
    conn.close()

    result = run_script([], db_path)
    assert result.returncode == 0, result.stderr
    assert "push: bill" in result.stdout
