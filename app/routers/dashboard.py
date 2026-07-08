import sqlite3
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from app.deps import get_current_user_id, get_db
from app.repositories import accounts as accounts_repo
from app.repositories import committed_purchases as purchases_repo
from app.repositories import income_events as income_repo
from app.repositories import obligations as obligations_repo
from app.repositories import settings as settings_repo
from app.repositories import snapshots as snapshots_repo
from app.security import SESSION_COOKIE_NAME, get_valid_session
from app.services import calc
from app.services import narrative
from app.sparkline import build_sparkline_svg
from app.templating import templates

router = APIRouter()

SPARKLINE_DAYS = 30


def _row_age(row: sqlite3.Row) -> timedelta:
    updated_at = datetime.strptime(row["updated_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated_at


def _balance_freshness(accounts: list[sqlite3.Row], debts: list[sqlite3.Row], threshold_days: int) -> dict:
    """Whether the headline number has a basis to be shown at full confidence.
    Mirrors hubs.py's _is_stale_row (same updated_at field, bumped on every real
    write — manual edit, quick balance update, bank-sync apply) but reports ages
    rather than a bool, since the stale callout has to name which accounts and how
    old. A distinct, tighter, settings-backed threshold from the Money hub's fixed
    30-day badge — this one gates whether the *headline number* gets shown at full
    confidence, a stricter bar than "worth a nudge on a list page"."""
    threshold = timedelta(days=threshold_days)
    rows = list(accounts) + list(debts)
    ages = [(row["name"], _row_age(row)) for row in rows]
    stale = sorted(((name, age) for name, age in ages if age > threshold), key=lambda pair: -pair[1].total_seconds())
    if not stale:
        return {"is_stale": False}
    # The most recent updated_at across every account/debt, not just the stale ones --
    # this is "the last day anything real happened," used by the sparkline (STATE_SPEC's
    # "solid up to its last real snapshot, then dashed") to know where real data ends and
    # repeated-frozen-number snapshots begin. A day-precision approximation: a snapshot
    # can still shift slightly from pure date-window effects (a bill rolling into/out of
    # the reserved window) with no balance edit at all -- not itemized further here, same
    # as the original §1.1 scoping note.
    freshest_updated_at = max((row["updated_at"] for row in rows), default=None)
    return {
        "is_stale": True,
        "stale_oldest_days": stale[0][1].days,
        "stale_rows": [{"name": name, "days": age.days} for name, age in stale],
        "last_real_update_date": freshest_updated_at[:10] if freshest_updated_at else None,
    }


def _bar_pct(amount_cents: int, denom_cents: int) -> float:
    if denom_cents <= 0:
        return 0.0
    return round(max(amount_cents, 0) / denom_cents * 100, 2)


def _composition_bar(
    cash_on_hand_cents: int,
    reserved_cash_cents: int,
    obligations_reserved_cents: int,
    debts_reserved_cents: int,
    purchases_reserved_cents: int,
    safe_to_spend_cents: int,
    is_stale: bool,
) -> dict:
    """Percentages for the composition bar (design/UI_AUDIT.md A3) — presentational
    derivation of numbers calc.py already produces, not a new calculation. Two
    shapes, matching design/STATE_SPEC.md: healthy/stale get a 4-segment
    bills/debts/purchases/free bar (denominator = cash on hand, since those four
    always sum to it exactly); negative gets a 2-segment covered/not-covered bar
    (denominator = reserved cash, since cash on hand no longer covers it). Stale
    wins over negative (design/STATE_SPEC.md) — an untrustworthy number must not
    also read as alarming, so a stale-and-negative balance still gets the
    4-segment shape, same as _balance_freshness's sibling suppressions in the
    hero markup."""
    if safe_to_spend_cents < 0 and not is_stale:
        denom = max(reserved_cash_cents, 1)
        return {
            "bar_negative": True,
            "covered_pct": _bar_pct(cash_on_hand_cents, denom),
            "uncovered_pct": _bar_pct(-safe_to_spend_cents, denom),
        }
    denom = max(cash_on_hand_cents, 1)
    return {
        "bar_negative": False,
        "bills_pct": _bar_pct(obligations_reserved_cents, denom),
        "debts_pct": _bar_pct(debts_reserved_cents, denom),
        "purchases_pct": _bar_pct(purchases_reserved_cents, denom),
        "free_pct": _bar_pct(safe_to_spend_cents, denom),
        # Clamped for display only -- a stale-and-actually-negative balance still
        # gets the 4-segment shape (stale wins), but "Free -$1,744.00" reads as
        # nonsense. The real number is never hidden: it's still the headline
        # figure and the callout above says the balances are old.
        "free_display_cents": max(safe_to_spend_cents, 0),
    }


def _debt_priority_reason(top_debt: sqlite3.Row) -> str:
    """Explains why the top row of the debt-priority table ranked first, mirroring
    calc.py::debt_priority's own tiering without duplicating its sort — a debt is
    tier B (paid off by due date, not balance-risk order) exactly when
    interest_status == 'not_accruing' or is_flexible_payment; tier A is ranked by
    APR descending, with unknown APR sorted as maximally risky (UI_AUDIT A7)."""
    if top_debt["priority"] != 0:
        return "manually prioritized"
    if top_debt["interest_status"] == "not_accruing":
        return "not accruing interest"
    if top_debt["is_flexible_payment"]:
        return "flexible payment"
    if top_debt["apr_bps"] is None:
        return "APR unknown — treated as highest risk"
    return "highest APR, accruing"


def _ensure_todays_snapshot(db: sqlite3.Connection, user_id: int, today: date) -> None:
    """Auto-captures today's snapshot on first dashboard view of the day, so the
    trend sparkline builds up on its own — no separate "go click capture" step."""
    existing = db.execute(
        "SELECT 1 FROM snapshots WHERE user_id = ? AND snapshot_date = ?",
        (user_id, today.isoformat()),
    ).fetchone()
    if existing is None:
        values = calc.snapshot_values(db, user_id, today)
        snapshots_repo.upsert_snapshot(db, user_id, today.isoformat(), values)
        db.commit()


def build_dashboard_context(db: sqlite3.Connection, user_id: int) -> dict:
    """Everything the dashboard shows, computed fresh from the DB. Shared by the full
    page (GET /) and by app/routers/quick_actions.py, which re-renders just the summary
    partial after each quick action so safe-to-spend visibly updates without a full
    page reload — the whole point of the 'Today' quick-actions screen is rapid repeated
    actions without losing your place."""
    today = date.today()
    window_end = calc.reserved_window_end(db, user_id, today)
    accounts = accounts_repo.list_accounts(db, user_id)
    is_first_run = len(accounts) == 0

    _ensure_todays_snapshot(db, user_id, today)

    recent_snapshots = list(reversed(snapshots_repo.list_snapshots(db, user_id, limit=SPARKLINE_DAYS)))
    sparkline_points = [(s["snapshot_date"], s["safe_to_spend_cents"]) for s in recent_snapshots]

    safe_to_spend_cents = calc.safe_to_spend(db, user_id, today)
    trend_delta_cents = None
    if len(recent_snapshots) >= 2:
        trend_delta_cents = safe_to_spend_cents - recent_snapshots[0]["safe_to_spend_cents"]

    change_narrative = narrative.build_change_narrative(db, user_id, today)

    debt_priority_rows = calc.debt_priority(db, user_id)
    debt_payoffs = {
        debt["id"]: calc.debt_payoff_projection(
            debt["balance_cents"], debt["apr_bps"], debt["minimum_payment_cents"], debt["interest_status"]
        )
        for debt in debt_priority_rows
    }

    saved_settings = settings_repo.get_all(db, user_id)
    stale_threshold_days = int(
        saved_settings.get("stale_balance_threshold_days", calc.DEFAULT_SETTINGS["stale_balance_threshold_days"])
    )
    freshness = _balance_freshness(accounts, debt_priority_rows, stale_threshold_days)

    cash_on_hand_cents = calc.cash_on_hand(db, user_id)
    reserved_cash_cents = calc.reserved_cash(db, user_id, window_end)
    obligations_reserved_cents = calc.obligations_reserved(db, user_id, window_end)
    debts_reserved_cents = calc.debts_reserved(db, user_id, window_end)
    purchases_reserved_cents = calc.committed_purchases_reserved(db, user_id, window_end)
    composition_bar = _composition_bar(
        cash_on_hand_cents, reserved_cash_cents, obligations_reserved_cents,
        debts_reserved_cents, purchases_reserved_cents, safe_to_spend_cents,
        freshness["is_stale"],
    )

    return {
        **freshness,
        **composition_bar,
        "is_first_run": is_first_run,
        "cash_on_hand_cents": cash_on_hand_cents,
        "total_debt_cents": calc.total_debt(db, user_id),
        "net_position_cents": calc.net_position(db, user_id),
        "reserved_cash_cents": reserved_cash_cents,
        "obligations_reserved_cents": obligations_reserved_cents,
        "debts_reserved_cents": debts_reserved_cents,
        "purchases_reserved_cents": purchases_reserved_cents,
        "safe_to_spend_cents": safe_to_spend_cents,
        "forecast_position_cents": calc.forecast_position(db, user_id, today),
        "next_paycheck": calc.next_paycheck(db, user_id, today),
        "next_due_payment": calc.next_due_payment(db, user_id),
        "debt_priority": debt_priority_rows,
        "debt_payoffs": debt_payoffs,
        "debt_priority_reason": _debt_priority_reason(debt_priority_rows[0]) if debt_priority_rows else None,
        "sparkline_svg": build_sparkline_svg(sparkline_points, last_real_date=freshness.get("last_real_update_date")),
        "trend_delta_cents": trend_delta_cents,
        "trend_days": len(recent_snapshots),
        "change_narrative": change_narrative,
    }


@router.get("/")
def dashboard(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    session = get_valid_session(db, request.cookies[SESSION_COOKIE_NAME])
    context = build_dashboard_context(db, user_id)
    context["csrf_token"] = session["csrf_secret"]
    context["quick_accounts"] = accounts_repo.list_accounts(db, user_id)
    context["quick_purchases"] = purchases_repo.list_purchases(db, user_id)
    context["quick_bills"] = obligations_repo.list_obligations(db, user_id)
    context["quick_income_events"] = income_repo.list_income_events(db, user_id)
    context["quick_purchase_categories"] = purchases_repo.list_distinct_categories(db, user_id)
    return templates.TemplateResponse(request, "dashboard.html", context)
