"""Hypothetical/what-if overlay. Never writes to the real database — builds a throwaway
in-memory SQLite database seeded with a copy of the user's real data plus the hypothetical
entries, then runs it through the exact same app.services.calc functions used everywhere
else. This guarantees what-if numbers can never drift from the real calculation rules.
"""
import sqlite3
from datetime import date

from app.services import calc

# Explicit column lists (not PRAGMA introspection) so we never accidentally try to
# INSERT the generated committed_purchases.remaining_cents column, and so every NOT
# NULL column without a DEFAULT is covered.
_REAL_TABLE_COLUMNS = {
    "accounts": ["name", "type", "balance_cents", "is_active"],
    "debts": [
        "name",
        "type",
        "balance_cents",
        "apr_bps",
        "minimum_payment_cents",
        "next_due_date",
        "interest_status",
        "is_flexible_payment",
        "priority",
        "is_active",
    ],
    "obligations": ["name", "category", "amount_cents", "due_date", "is_paid"],
    "committed_purchases": ["name", "category", "amount_cents", "amount_paid_cents", "status", "payment_deadline"],
    "income_events": ["source", "expected_amount_cents", "expected_date", "confidence", "is_received"],
    "settings": ["key", "value"],
}

_HYPOTHETICAL_DEFAULTS = {
    "accounts": {"name": "Hypothetical account", "type": "checking", "balance_cents": 0, "is_active": 1},
    "debts": {
        "name": "Hypothetical debt",
        "type": "other",
        "balance_cents": 0,
        "minimum_payment_cents": 0,
        "interest_status": "accruing",
        "is_active": 1,
        "is_flexible_payment": 0,
        "priority": 0,
    },
    "obligations": {
        "name": "Hypothetical obligation",
        "category": "other",
        "amount_cents": 0,
        "is_paid": 0,
    },
    "committed_purchases": {
        "name": "Hypothetical purchase",
        "category": "other",
        "amount_cents": 0,
        "amount_paid_cents": 0,
        "status": "planned",
    },
    "income_events": {
        "source": "Hypothetical income",
        "expected_amount_cents": 0,
        "confidence": "confirmed",
        "is_received": 0,
    },
}


def _build_shadow_db(real_conn: sqlite3.Connection, user_id: int) -> sqlite3.Connection:
    from migrations.runner import MIGRATIONS_DIR

    shadow = sqlite3.connect(":memory:")
    shadow.row_factory = sqlite3.Row
    shadow.execute("PRAGMA foreign_keys = ON")
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        shadow.executescript(migration.read_text())

    real_user = real_conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    shadow.execute(
        "INSERT INTO users (id, username, password_hash) VALUES (?, ?, 'x')",
        (real_user["id"], real_user["username"]),
    )

    for table, columns in _REAL_TABLE_COLUMNS.items():
        col_list = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        rows = real_conn.execute(f"SELECT {col_list} FROM {table} WHERE user_id = ?", (user_id,)).fetchall()
        for row in rows:
            shadow.execute(
                f"INSERT INTO {table} (user_id, {col_list}) VALUES (?, {placeholders})",
                (user_id, *row),
            )

    shadow.commit()
    return shadow


def _insert_hypothetical(
    shadow: sqlite3.Connection,
    user_id: int,
    table: str,
    entries: list[dict],
    extra_defaults: dict | None = None,
) -> None:
    defaults = {**_HYPOTHETICAL_DEFAULTS[table], **(extra_defaults or {})}
    for entry in entries:
        merged = {**defaults, **entry, "user_id": user_id}
        columns = ", ".join(merged.keys())
        placeholders = ", ".join("?" for _ in merged)
        shadow.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(merged.values()))
    shadow.commit()


def _snapshot(conn: sqlite3.Connection, user_id: int, today: date) -> dict:
    window_end = calc.reserved_window_end(conn, user_id, today)
    return {
        "cash_on_hand_cents": calc.cash_on_hand(conn, user_id),
        "total_debt_cents": calc.total_debt(conn, user_id),
        "net_position_cents": calc.net_position(conn, user_id),
        "reserved_cash_cents": calc.reserved_cash(conn, user_id, window_end),
        "safe_to_spend_cents": calc.safe_to_spend(conn, user_id, today),
        "forecast_position_cents": calc.forecast_position(conn, user_id, today),
    }


def run_whatif(
    real_conn: sqlite3.Connection,
    user_id: int,
    today: date | None = None,
    *,
    hypothetical_accounts: list[dict] = (),
    hypothetical_debts: list[dict] = (),
    hypothetical_obligations: list[dict] = (),
    hypothetical_purchases: list[dict] = (),
    hypothetical_income_events: list[dict] = (),
) -> dict:
    """Never writes to real_conn. Returns {"real": {...}, "hypothetical": {...}, "delta": {...}},
    each a dict of the same aggregate cents figures shown on the dashboard."""
    today = today or date.today()

    real_result = _snapshot(real_conn, user_id, today)

    shadow = _build_shadow_db(real_conn, user_id)
    try:
        _insert_hypothetical(shadow, user_id, "accounts", list(hypothetical_accounts))
        _insert_hypothetical(shadow, user_id, "debts", list(hypothetical_debts))
        # due_date/expected_date are NOT NULL with no column default; fall back to
        # `today` here rather than requiring every caller to supply one.
        _insert_hypothetical(
            shadow, user_id, "obligations", list(hypothetical_obligations),
            extra_defaults={"due_date": today.isoformat()},
        )
        _insert_hypothetical(shadow, user_id, "committed_purchases", list(hypothetical_purchases))
        _insert_hypothetical(
            shadow, user_id, "income_events", list(hypothetical_income_events),
            extra_defaults={"expected_date": today.isoformat()},
        )

        hypothetical_result = _snapshot(shadow, user_id, today)
    finally:
        shadow.close()

    delta = {key: hypothetical_result[key] - real_result[key] for key in real_result}

    return {"real": real_result, "hypothetical": hypothetical_result, "delta": delta}
