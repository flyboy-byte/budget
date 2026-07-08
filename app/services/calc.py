"""Core calculation engine: cash position, reserved cash, safe-to-spend, forecast, debt ranking.

Every function takes user_id explicitly and scopes every query by it — never a global
"current user". See ARCHITECTURE.md "Calculation Rules" for the formulas this module implements;
treat any discrepancy between this file and ARCHITECTURE.md as a bug in this file.
"""
import calendar
import sqlite3
from datetime import date, timedelta

DEFAULT_SETTINGS = {
    "protected_savings_floor_cents": "0",
    "reserved_window_mode": "end_of_month",
    "reserved_window_fixed_days": "14",
    "forecast_window_days": "30",
    "forecast_income_confidence": "confirmed",
    "timezone": "America/Chicago",
    "bank_sync_cooldown_minutes": "360",
    "bank_sync_use_available_balance": "0",
    "bank_sync_auto_apply": "0",
    "bank_sync_auto_apply_max_change_cents": "25000",
    "digest_email": "",
    "large_transaction_threshold_cents": "10000",
    "low_safe_to_spend_threshold_cents": "",
    "low_safe_to_spend_alert_active": "0",
    "stale_balance_threshold_days": "7",
}

RESERVED_WINDOW_MODES = ("next_paycheck", "end_of_month", "fixed_days")
FORECAST_INCOME_CONFIDENCE_OPTIONS = ("confirmed", "confirmed_likely")


def get_setting(conn: sqlite3.Connection, user_id: int, key: str) -> str:
    row = conn.execute(
        "SELECT value FROM settings WHERE user_id = ? AND key = ?", (user_id, key)
    ).fetchone()
    if row is not None:
        return row["value"]
    return DEFAULT_SETTINGS[key]


def cash_on_hand(conn: sqlite3.Connection, user_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(balance_cents), 0) c FROM accounts WHERE user_id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()
    return row["c"]


def total_debt(conn: sqlite3.Connection, user_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(balance_cents), 0) c FROM debts WHERE user_id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()
    return row["c"]


def net_position(conn: sqlite3.Connection, user_id: int) -> int:
    return cash_on_hand(conn, user_id) - total_debt(conn, user_id)


def _end_of_month(today: date) -> date:
    last_day = calendar.monthrange(today.year, today.month)[1]
    return date(today.year, today.month, last_day)


def reserved_window_end(conn: sqlite3.Connection, user_id: int, today: date | None = None) -> str:
    today = today or date.today()
    mode = get_setting(conn, user_id, "reserved_window_mode")

    if mode == "fixed_days":
        days = int(get_setting(conn, user_id, "reserved_window_fixed_days"))
        return (today + timedelta(days=days)).isoformat()

    if mode == "next_paycheck":
        row = conn.execute(
            """SELECT MIN(expected_date) d FROM income_events
               WHERE user_id = ? AND is_received = 0 AND expected_date >= ?""",
            (user_id, today.isoformat()),
        ).fetchone()
        if row["d"] is not None:
            return row["d"]
        return _end_of_month(today).isoformat()

    # end_of_month (default/fallback for unrecognized modes)
    return _end_of_month(today).isoformat()


def obligations_reserved(conn: sqlite3.Connection, user_id: int, window_end: str) -> int:
    row = conn.execute(
        """SELECT COALESCE(SUM(amount_cents), 0) c FROM obligations
           WHERE user_id = ? AND is_paid = 0 AND due_date <= ?""",
        (user_id, window_end),
    ).fetchone()
    return row["c"]


def debts_reserved(conn: sqlite3.Connection, user_id: int, window_end: str) -> int:
    row = conn.execute(
        """SELECT COALESCE(SUM(minimum_payment_cents), 0) c FROM debts
           WHERE user_id = ? AND is_active = 1 AND next_due_date IS NOT NULL AND next_due_date <= ?""",
        (user_id, window_end),
    ).fetchone()
    return row["c"]


def committed_purchases_reserved(conn: sqlite3.Connection, user_id: int, window_end: str) -> int:
    always_reserved = conn.execute(
        """SELECT COALESCE(SUM(remaining_cents), 0) c FROM committed_purchases
           WHERE user_id = ? AND status IN ('ordered','arrived','partially_paid')""",
        (user_id,),
    ).fetchone()["c"]
    planned_in_window = conn.execute(
        """SELECT COALESCE(SUM(remaining_cents), 0) c FROM committed_purchases
           WHERE user_id = ? AND status = 'planned'
             AND payment_deadline IS NOT NULL AND payment_deadline <= ?""",
        (user_id, window_end),
    ).fetchone()["c"]
    return always_reserved + planned_in_window


def reserved_cash(conn: sqlite3.Connection, user_id: int, window_end: str) -> int:
    return (
        obligations_reserved(conn, user_id, window_end)
        + debts_reserved(conn, user_id, window_end)
        + committed_purchases_reserved(conn, user_id, window_end)
    )


def safe_to_spend(conn: sqlite3.Connection, user_id: int, today: date | None = None) -> int:
    window_end = reserved_window_end(conn, user_id, today)
    floor_cents = int(get_setting(conn, user_id, "protected_savings_floor_cents"))
    return cash_on_hand(conn, user_id) - reserved_cash(conn, user_id, window_end) - floor_cents


def forecast_position(conn: sqlite3.Connection, user_id: int, today: date | None = None) -> int:
    today = today or date.today()
    forecast_window_days = int(get_setting(conn, user_id, "forecast_window_days"))
    forecast_window_end = (today + timedelta(days=forecast_window_days)).isoformat()

    confidence_setting = get_setting(conn, user_id, "forecast_income_confidence")
    confirmed_set = (
        ("confirmed", "likely") if confidence_setting == "confirmed_likely" else ("confirmed",)
    )
    placeholders = ",".join("?" for _ in confirmed_set)
    row = conn.execute(
        f"""SELECT COALESCE(SUM(expected_amount_cents), 0) c FROM income_events
            WHERE user_id = ? AND is_received = 0 AND expected_date <= ?
              AND confidence IN ({placeholders})""",
        (user_id, forecast_window_end, *confirmed_set),
    ).fetchone()
    forecast_income = row["c"]

    return (
        cash_on_hand(conn, user_id)
        + forecast_income
        - obligations_reserved(conn, user_id, forecast_window_end)
        - debts_reserved(conn, user_id, forecast_window_end)
        - committed_purchases_reserved(conn, user_id, forecast_window_end)
        - total_debt(conn, user_id)
    )


def snapshot_values(conn: sqlite3.Connection, user_id: int, today: date | None = None) -> dict:
    """Every field snapshots.<x>_cents/window_days needs, computed fresh. Used by both the
    manual "capture snapshot" action and the cron-friendly scripts/snapshot.py."""
    today = today or date.today()
    window_end = reserved_window_end(conn, user_id, today)
    mode = get_setting(conn, user_id, "reserved_window_mode")
    window_days = (
        int(get_setting(conn, user_id, "reserved_window_fixed_days"))
        if mode == "fixed_days"
        else (date.fromisoformat(window_end) - today).days
    )
    return {
        "cash_on_hand_cents": cash_on_hand(conn, user_id),
        "total_debt_cents": total_debt(conn, user_id),
        "net_position_cents": net_position(conn, user_id),
        "reserved_cash_cents": reserved_cash(conn, user_id, window_end),
        "safe_to_spend_cents": safe_to_spend(conn, user_id, today),
        "forecast_position_cents": forecast_position(conn, user_id, today),
        "protected_floor_cents": int(get_setting(conn, user_id, "protected_savings_floor_cents")),
        "window_days": window_days,
    }


def debt_priority(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM debts WHERE user_id = ? AND is_active = 1", (user_id,)
    ).fetchall()

    pinned = sorted((r for r in rows if r["priority"] != 0), key=lambda r: r["priority"])

    unpinned = [r for r in rows if r["priority"] == 0]
    tier_b = [r for r in unpinned if r["interest_status"] == "not_accruing" or r["is_flexible_payment"]]
    tier_b_ids = {r["id"] for r in tier_b}
    tier_a = [r for r in unpinned if r["id"] not in tier_b_ids]

    tier_a.sort(key=lambda r: (-(r["apr_bps"] if r["apr_bps"] is not None else float("inf")), -r["balance_cents"]))
    tier_b.sort(key=lambda r: (r["next_due_date"] or "9999-99-99", r["balance_cents"]))

    return pinned + tier_a + tier_b


MAX_PAYOFF_MONTHS = 600  # 50-year safety bound against a bad/edge-case input looping forever


def debt_payoff_projection(
    balance_cents: int, apr_bps: int | None, minimum_payment_cents: int, interest_status: str
) -> dict | None:
    """Pure math, no DB access — a computed display value from a debt's own fields,
    not a stored/synced one. Does NOT reuse debt_priority's NULL-APR-as-infinity sort
    trick; that's a sort-order convenience, not a valid rate to plug into interest math.
    Returns None when there's nothing to honestly project from (unknown APR)."""
    if minimum_payment_cents <= 0 or balance_cents <= 0:
        return None

    if interest_status == "not_accruing":
        monthly_rate = 0.0
    elif apr_bps is None:
        return None
    else:
        monthly_rate = apr_bps / 10000 / 12

    if monthly_rate > 0 and minimum_payment_cents <= balance_cents * monthly_rate:
        return {"payoff_impossible": True}

    remaining = balance_cents
    total_interest_cents = 0
    months = 0
    while remaining > 0 and months < MAX_PAYOFF_MONTHS:
        interest = remaining * monthly_rate
        principal = min(minimum_payment_cents - interest, remaining)
        remaining -= principal
        total_interest_cents += interest
        months += 1

    return {
        "payoff_impossible": False,
        "months": months,
        "total_interest_cents": round(total_interest_cents),
    }


def next_paycheck(conn: sqlite3.Connection, user_id: int, today: date | None = None) -> sqlite3.Row | None:
    today = today or date.today()
    return conn.execute(
        """SELECT * FROM income_events
           WHERE user_id = ? AND is_received = 0 AND expected_date >= ?
           ORDER BY expected_date ASC LIMIT 1""",
        (user_id, today.isoformat()),
    ).fetchone()


def next_due_payment(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """Earliest upcoming (or overdue) obligation or debt minimum payment, whichever is sooner."""
    obligation = conn.execute(
        """SELECT name, due_date AS due_date, amount_cents FROM obligations
           WHERE user_id = ? AND is_paid = 0
           ORDER BY due_date ASC LIMIT 1""",
        (user_id,),
    ).fetchone()
    debt = conn.execute(
        """SELECT name, next_due_date AS due_date, minimum_payment_cents AS amount_cents FROM debts
           WHERE user_id = ? AND is_active = 1 AND next_due_date IS NOT NULL
           ORDER BY next_due_date ASC LIMIT 1""",
        (user_id,),
    ).fetchone()

    candidates = []
    if obligation is not None:
        candidates.append({"kind": "obligation", **dict(obligation)})
    if debt is not None:
        candidates.append({"kind": "debt", **dict(debt)})

    if not candidates:
        return None
    return min(candidates, key=lambda c: c["due_date"])
