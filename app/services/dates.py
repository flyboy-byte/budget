"""Human-readable date formatting shared between the web UI's `human_date`
Jinja filter (app/templating.py) and the daily digest email (app/services/digest.py),
so the two never disagree about what day something is due. Storage stays plain
ISO-8601 `YYYY-MM-DD` TEXT everywhere; this only changes how it's displayed.

Also owns parsing the `created_at`/`updated_at`/`last_synced_at` timestamp columns,
which are a different shape from the plain date columns above.
"""
from datetime import date, datetime, timezone

# What SQLite's strftime('%Y-%m-%dT%H:%M:%fZ','now') actually emits -- %f there is
# "SS.SSS" (seconds with milliseconds), so the fractional part is always present.
_DB_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def parse_db_timestamp(value: str | None) -> datetime | None:
    """Parse a stored timestamp into an aware UTC datetime, or None if it can't be read.

    Every timestamp the app writes itself comes from SQLite's strftime and matches
    _DB_TIMESTAMP_FORMAT exactly. A restored JSON backup is the exception: created_at
    and updated_at are round-tripped verbatim, so a hand-edited or third-party-generated
    backup can carry any string at all. Returning None instead of raising keeps one bad
    row from 500ing a whole screen -- callers decide what an unknown timestamp means,
    and the conservative answer is almost always "treat it as stale" rather than
    "assume it's fresh".
    """
    if not value:
        return None
    try:
        return datetime.strptime(str(value), _DB_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass
    # Tolerate the common near-misses (no milliseconds, space separator, +00:00
    # offset) rather than discarding a timestamp that is perfectly readable.
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def human_date(value: str | None, today: date) -> str:
    if not value:
        return ""
    d = date.fromisoformat(value)
    delta = (d - today).days

    if delta == 0:
        rel = "today"
    elif delta == 1:
        rel = "tomorrow"
    elif delta == -1:
        rel = "yesterday"
    elif delta > 1:
        rel = f"in {delta} days"
    else:
        rel = f"{-delta} days ago"

    return f"{d.strftime('%a %-d %b')} · {rel}"
