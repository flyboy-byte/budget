"""One-time setup: apply migrations and create the first user.

Usage: python -m scripts.init_db <username> <password>
"""
import sys

from app.config import DB_PATH
from app.db import get_connection
from app.security import hash_password
from migrations.runner import apply_migrations


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python -m scripts.init_db <username> <password>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]

    applied = apply_migrations(DB_PATH)
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("Schema already up to date.")

    password_hash = hash_password(password)
    with get_connection(DB_PATH) as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            print(f"User '{username}' already exists (id={existing['id']}).")
            return
        cur = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
        print(f"Created user '{username}' (id={cur.lastrowid}).")


if __name__ == "__main__":
    main()
