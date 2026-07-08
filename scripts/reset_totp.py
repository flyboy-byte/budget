"""Disable a user's TOTP (authenticator-app) login and revert them to password-only,
from the command line (no HTTP route exists for this without an existing admin --
see app/routers/settings.py's admin-gated /settings/users/{id}/reset-totp for the
"an admin exists" case; this is the last-resort recovery path for total lockout).

Usage: python -m scripts.reset_totp <username>
"""
import sys

from app.config import DB_PATH
from app.db import get_connection


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m scripts.reset_totp <username>")
        sys.exit(1)

    username = sys.argv[1]

    with get_connection(DB_PATH) as conn:
        cur = conn.execute(
            """UPDATE users SET totp_secret_encrypted = NULL, totp_enabled = 0,
               totp_last_used_step = NULL, auth_mode = 'password' WHERE username = ?""",
            (username,),
        )
        if cur.rowcount == 0:
            print(f"No user '{username}'")
            sys.exit(1)
        conn.execute(
            "DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)",
            (username,),
        )
        print(f"TOTP disabled for '{username}'; login mode reset to password; all existing sessions were revoked.")


if __name__ == "__main__":
    main()
