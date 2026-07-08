"""Send the daily digest email for one or all active users who've opted in
(Settings -> "Daily digest email"). Cron-friendly. One user's send failure never
stops the others.

Usage:
    python -m scripts.digest              # all active users
    python -m scripts.digest --user logan  # just one user
"""
import argparse
import sys
from datetime import date

from app.config import DB_PATH
from app.db import get_connection
from app.services import bill_reminders, digest, low_balance_alert


def send_for_user(conn, user_id: int, username: str, today: date) -> str:
    try:
        sent = digest.send_digest(conn, user_id, today)
    except digest.DigestError as exc:
        return f"error ({exc})"
    return "sent" if sent else "skipped (no digest email set)"


def send_pushes_for_user(conn, user_id: int, today: date) -> str:
    """Bill-due and low-safe-to-spend pushes -- independent of the digest email,
    so they still fire for users who haven't set digest_email. One user's push
    failure never stops the others, matching send_for_user's error handling."""
    try:
        bill_sent = bill_reminders.send_due_bill_pushes(conn, user_id, today)
        low_balance_sent = low_balance_alert.check_low_safe_to_spend(conn, user_id, today)
    except Exception as exc:  # noqa: BLE001 -- best-effort, never let push failures abort the run
        return f"push error ({exc})"
    fired = [name for name, sent in (("bill", bill_sent), ("low-balance", low_balance_sent)) if sent]
    return f"push: {', '.join(fired)}" if fired else "push: none"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="username to send to (default: all active users)")
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
            result = send_for_user(conn, row["id"], args.user, today)
            push_result = send_pushes_for_user(conn, row["id"], today)
            print(f"  {args.user}: {result}; {push_result}")
        else:
            rows = conn.execute("SELECT id, username FROM users WHERE is_active = 1").fetchall()
            for row in rows:
                result = send_for_user(conn, row["id"], row["username"], today)
                push_result = send_pushes_for_user(conn, row["id"], today)
                print(f"  {row['username']}: {result}; {push_result}")
            print(f"Processed {len(rows)} user(s)")


if __name__ == "__main__":
    main()
