"""Date math for rolling a recurring obligation/income event forward to its next
occurrence. Pure stdlib — no dateutil dependency for something this small.
"""
import calendar
from datetime import date, timedelta

RECURRENCE_RULES = ("weekly", "biweekly", "monthly", "yearly")


def advance_date(date_str: str, rule: str) -> str:
    """'2026-07-15' + 'monthly' -> '2026-08-15'. Clamps day-of-month/leap-day
    overflow (Jan 31 + monthly -> Feb 28/29, not Mar 3)."""
    d = date.fromisoformat(date_str)

    if rule == "weekly":
        return (d + timedelta(days=7)).isoformat()
    if rule == "biweekly":
        return (d + timedelta(days=14)).isoformat()
    if rule == "monthly":
        month = d.month + 1
        year = d.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(d.day, last_day)).isoformat()
    if rule == "yearly":
        last_day = calendar.monthrange(d.year + 1, d.month)[1]
        return date(d.year + 1, d.month, min(d.day, last_day)).isoformat()

    raise ValueError(f"Unknown recurrence rule: {rule!r}")
