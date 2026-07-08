"""Low safe-to-spend push alert (IMPLEMENTATION_HISTORY.md Phase 7). Daily-check only for v1 --
checked once per day from the same cron run as the digest email/bill reminders,
not real-time. Uses a hysteresis flag (low_safe_to_spend_alert_active) so a
balance sitting below the threshold doesn't push once a day forever: it pushes
once on the drop below, then stays silent until it recovers above the threshold
and dips below again.
"""
import sqlite3
from datetime import date

from app.money import format_cents
from app.repositories import settings as settings_repo
from app.services import calc
from app.services import push as push_service


def check_low_safe_to_spend(conn: sqlite3.Connection, user_id: int, today: date | None = None) -> bool:
    """Returns whether a push was sent."""
    today = today or date.today()
    raw = settings_repo.get_all(conn, user_id)
    threshold_str = raw.get("low_safe_to_spend_threshold_cents", "").strip()
    if not threshold_str:
        return False
    threshold_cents = int(threshold_str)

    was_active = raw.get("low_safe_to_spend_alert_active", "0") == "1"
    safe_to_spend_cents = calc.safe_to_spend(conn, user_id, today)

    if safe_to_spend_cents >= threshold_cents:
        if was_active:
            settings_repo.upsert(conn, user_id, "low_safe_to_spend_alert_active", "0")
        return False

    if was_active:
        return False

    settings_repo.upsert(conn, user_id, "low_safe_to_spend_alert_active", "1")
    push_service.send_to_user(
        conn,
        user_id,
        "Safe-to-spend is low",
        f"Safe to spend has dropped to {format_cents(safe_to_spend_cents)}, "
        f"below your {format_cents(threshold_cents)} alert threshold.",
        url="/",
    )
    return True
