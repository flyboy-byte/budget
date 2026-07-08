"""Create an additional user against an already-initialized database.

Usage: python -m scripts.add_user <username> <password>
"""
import sys

from app.config import DB_PATH
from app.db import get_connection
from app.security import hash_password


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python -m scripts.add_user <username> <password>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]
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
