"""Explains what moved safe-to-spend since yesterday, in plain language. Reuses the
existing daily snapshot capture (app/routers/dashboard.py::_ensure_todays_snapshot)
and the transactions ledger's memo/target_type — no new financial logic, this only
narrates numbers calc.py already computes.
"""
import sqlite3
from datetime import date, timedelta

from app.money import format_cents
from app.repositories import snapshots as snapshots_repo
from app.repositories import transactions as transactions_repo
from app.services import calc

# "other" is the only target_type any writer currently uses for an inflow (see
# bills.py::mark_income_received) -- obligation/committed_purchase are debits, and
# "debt" exists in the schema but nothing writes it yet. Everything else defaults
# to an outflow rather than being allowlisted as an inflow: if a future feature
# ever logs a "debt" (or any other new) transaction type, treating it as a debit
# by default is the safer wrong guess for a financial narrative than the reverse.
_INFLOW_TYPES = ("other",)
_MAX_MOVERS = 2


def build_change_narrative(conn: sqlite3.Connection, user_id: int, today: date | None = None) -> str | None:
    today = today or date.today()
    yesterday = (today - timedelta(days=1)).isoformat()

    prior = snapshots_repo.get_snapshot_by_date(conn, user_id, yesterday)
    if prior is None:
        return None

    current_cents = calc.safe_to_spend(conn, user_id, today)
    delta_cents = current_cents - prior["safe_to_spend_cents"]

    if delta_cents == 0:
        return "Unchanged since yesterday."

    direction = "Up" if delta_cents > 0 else "Down"
    headline = f"{direction} {format_cents(abs(delta_cents))} since yesterday"

    rows = transactions_repo.list_since(conn, user_id, yesterday)
    movers = []
    for row in rows:
        signed = row["amount_cents"] if row["target_type"] in _INFLOW_TYPES else -row["amount_cents"]
        label = row["memo"] or row["target_type"]
        movers.append((label, signed))
    movers.sort(key=lambda m: abs(m[1]), reverse=True)

    if not movers:
        return f"{headline}."

    parts = [f"{label} ({format_cents(abs(amount))})" for label, amount in movers[:_MAX_MOVERS]]
    return f"{headline} — {', '.join(parts)}."
