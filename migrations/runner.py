"""Applies numbered .sql files in this directory that aren't yet in schema_migrations.

Usage: python -m migrations.runner [db_path]
"""
import sqlite3
import sys
from pathlib import Path

from app.config import DB_PATH

MIGRATIONS_DIR = Path(__file__).resolve().parent


def pending_migrations(conn: sqlite3.Connection) -> list[Path]:
    applied = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchall()
    }
    applied_versions: set[str] = set()
    if applied:
        applied_versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    all_migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return [m for m in all_migrations if m.stem not in applied_versions]


def apply_migrations(db_path=DB_PATH) -> list[str]:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    applied = []
    try:
        for migration in pending_migrations(conn):
            sql = migration.read_text()
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (migration.stem,),
            )
            conn.commit()
            applied.append(migration.stem)
    finally:
        conn.close()
    return applied


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    applied = apply_migrations(target)
    if applied:
        print(f"Applied: {', '.join(applied)}")
    else:
        print("No pending migrations.")
