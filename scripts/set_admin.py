"""Grant or revoke admin (root) status for a user from the command line. No in-app
route can grant the very first admin, since granting requires already being one.

Usage: python -m scripts.set_admin <username> <on|off>
"""
import sys

from app.config import DB_PATH
from app.db import get_connection


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[2] not in ("on", "off"):
        print("Usage: python -m scripts.set_admin <username> <on|off>")
        sys.exit(1)

    username, action = sys.argv[1], sys.argv[2]
    is_admin = 1 if action == "on" else 0

    with get_connection(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE users SET is_admin = ? WHERE username = ?", (is_admin, username)
        )
        if cur.rowcount == 0:
            print(f"No user '{username}'")
            sys.exit(1)
        print(f"'{username}' is now {'an admin' if is_admin else 'not an admin'}.")


if __name__ == "__main__":
    main()
