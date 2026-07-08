from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app.config import BASE_DIR
from app.money import format_cents
from app.services.dates import human_date


def format_unix_date(value: int | None) -> str:
    """SimpleFIN's balance-date is a unix timestamp — shown on the bank-sync
    review screen so a stale bank-side refresh is visible before confirming."""
    if not value:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%b %-d")


@pass_context
def _human_date_filter(context, value: str | None) -> str:
    """`human_date` needs "today" in the viewing user's timezone, not the
    filter's own args — pulled from request.state.timezone, set once per
    request in app.deps.get_current_user_id. Falls back to UTC for the rare
    unauthenticated render (e.g. login.html) that has no user session yet."""
    request = context.get("request")
    tz_name = getattr(getattr(request, "state", None), "timezone", None) or "UTC"
    today = datetime.now(ZoneInfo(tz_name)).date()
    return human_date(value, today)


templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
templates.env.filters["money"] = format_cents
templates.env.filters["unixdate"] = format_unix_date
templates.env.filters["human_date"] = _human_date_filter
