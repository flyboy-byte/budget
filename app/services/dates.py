"""Human-readable date formatting shared between the web UI's `human_date`
Jinja filter (app/templating.py) and the daily digest email (app/services/digest.py),
so the two never disagree about what day something is due. Storage stays plain
ISO-8601 `YYYY-MM-DD` TEXT everywhere; this only changes how it's displayed."""
from datetime import date


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
