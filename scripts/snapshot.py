"""Capture today's snapshot for one or all active users. Cron-friendly.

Usage:
    python -m scripts.snapshot              # all active users
    python -m scripts.snapshot --user logan  # just one user
"""
import argparse
import sys
from datetime import date

from app.config import DB_PATH
from app.db import get_connection
from app.repositories import snapshots as repo
from app.services import calc


def capture_for_user(conn, user_id: int, today: date) -> None:
    values = calc.snapshot_values(conn, user_id, today)
    repo.upsert_snapshot(conn, user_id, today.isoformat(), values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="username to snapshot (default: all active users)")
    args = parser.parse_args()

    today = date.today()
    with get_connection(DB_PATH) as conn:
        if args.user:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ? AND is_active = 1", (args.user,)
            ).fetchone()
            if row is None:
                print(f"No active user '{args.user}'")
                sys.exit(1)
            capture_for_user(conn, row["id"], today)
            print(f"Captured snapshot for '{args.user}' on {today.isoformat()}")
        else:
            rows = conn.execute("SELECT id, username FROM users WHERE is_active = 1").fetchall()
            for row in rows:
                capture_for_user(conn, row["id"], today)
            print(f"Captured snapshot for {len(rows)} user(s) on {today.isoformat()}")


if __name__ == "__main__":
    main()
