"""Write a full JSON backup for one or all active users to disk. Cron-friendly.

Usage:
    python -m scripts.backup                # all active users
    python -m scripts.backup --user logan    # just one user
    python -m scripts.backup --keep 14       # prune older backups per user (default: keep all)
"""
import argparse
import sys
from pathlib import Path

from app.config import BASE_DIR, DB_PATH
from app.db import get_connection
from app.services import export

BACKUP_DIR = BASE_DIR / "data" / "backups"


def backup_user(conn, username: str, user_id: int, keep: int | None) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = export.export_json_backup(conn, user_id)
    filename = export.backup_filename(username)
    path = BACKUP_DIR / filename
    path.write_bytes(export.to_json_bytes(backup))

    if keep is not None:
        existing = sorted(BACKUP_DIR.glob(f"budget-backup-{username}-*.json"))
        for old_path in existing[:-keep]:
            old_path.unlink()

    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="username to back up (default: all active users)")
    parser.add_argument("--keep", type=int, default=None, help="keep only the N most recent backups per user")
    args = parser.parse_args()

    with get_connection(DB_PATH) as conn:
        if args.user:
            row = conn.execute(
                "SELECT id, username FROM users WHERE username = ? AND is_active = 1", (args.user,)
            ).fetchone()
            if row is None:
                print(f"No active user '{args.user}'")
                sys.exit(1)
            path = backup_user(conn, row["username"], row["id"], args.keep)
            print(f"Backed up '{args.user}' -> {path}")
        else:
            rows = conn.execute("SELECT id, username FROM users WHERE is_active = 1").fetchall()
            for row in rows:
                path = backup_user(conn, row["username"], row["id"], args.keep)
                print(f"Backed up '{row['username']}' -> {path}")
            print(f"Backed up {len(rows)} user(s)")


if __name__ == "__main__":
    main()
