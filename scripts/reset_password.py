"""Reset a user's password from the command line (no HTTP route exists for this, by
design — see CLAUDE.md on why there's no registration/self-service route).

Usage: python -m scripts.reset_password <username> <new_password>
"""
import sys

from app.config import DB_PATH
from app.db import get_connection
from app.security import hash_password


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python -m scripts.reset_password <username> <new_password>")
        sys.exit(1)

    username, new_password = sys.argv[1], sys.argv[2]
    password_hash = hash_password(new_password)

    with get_connection(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?", (password_hash, username)
        )
        if cur.rowcount == 0:
            print(f"No user '{username}'")
            sys.exit(1)
        conn.execute(
            "DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)",
            (username,),
        )
        print(f"Password reset for '{username}'; all existing sessions were revoked.")


if __name__ == "__main__":
    main()
