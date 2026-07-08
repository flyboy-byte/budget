"""Bill-due push reminders (IMPLEMENTATION_HISTORY.md Phase 6). Independent of digest.py's email --
fires whether or not the user has a digest_email set, since this is push-only.
"""
import sqlite3
from datetime import date, timedelta

from app.repositories import obligations as obligations_repo
from app.services import narrative
from app.services import push as push_service


def send_due_bill_pushes(conn: sqlite3.Connection, user_id: int, today: date | None = None) -> bool:
    """A bill due today or tomorrow (that hasn't already pushed today) is what
    triggers this push, but the copy says what changed, not what's due (VOICE.md
    "Notifications") -- a due-date announcement is what the phone's calendar
    already provides, so this reuses narrative.py's same "what changed" sentence
    digest.py's WHAT HAPPENED section leads with, rather than duplicating it.
    Returns whether a push was sent."""
    today = today or date.today()
    today_str = today.isoformat()
    tomorrow_str = (today + timedelta(days=1)).isoformat()

    due = obligations_repo.list_due_soon_unpushed(conn, user_id, today_str, tomorrow_str)
    if not due:
        return False

    body = narrative.build_change_narrative(conn, user_id, today) or "Nothing recorded since yesterday."

    push_service.send_to_user(conn, user_id, "Safe to spend", body, url="/today")

    for obligation in due:
        obligations_repo.mark_bill_push_sent(conn, user_id, obligation["id"], today_str)

    return True
