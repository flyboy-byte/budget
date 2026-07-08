"""Raw parametrized SQL for the debts table. No business logic — every function takes
user_id explicitly and scopes every query by it."""
import sqlite3

# Starter suggestions for the type datalist (app/templates/debts/form.html), not an
# allowlist — `type` is plain free text (migration 0007_free_text_types.sql dropped
# the DB CHECK that used to restrict it). list_distinct_types() merges these with
# whatever a user has actually typed, so a brand-new user isn't staring at an empty
# datalist, but nothing stops them from typing something else entirely.
SEED_TYPES = (
    "bank_loan", "other_loan", "credit_card", "line_of_credit", "credit_debt",
    "auto_loan", "student_loan", "medical_debt", "buy_now_pay_later", "collections",
    "store_card", "tax_debt", "personal", "tool_truck", "other",
)
INTEREST_STATUSES = ("accruing", "not_accruing", "promo_unknown")


def list_distinct_types(conn: sqlite3.Connection, user_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT type FROM debts WHERE user_id = ?", (user_id,)
    ).fetchall()
    return sorted(set(SEED_TYPES) | {r["type"] for r in rows})


def list_debts(conn: sqlite3.Connection, user_id: int, include_inactive: bool = False) -> list[sqlite3.Row]:
    if include_inactive:
        return conn.execute(
            "SELECT * FROM debts WHERE user_id = ? ORDER BY name", (user_id,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM debts WHERE user_id = ? AND is_active = 1 ORDER BY name", (user_id,)
    ).fetchall()


def get_debt(conn: sqlite3.Connection, user_id: int, debt_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM debts WHERE user_id = ? AND id = ?", (user_id, debt_id)
    ).fetchone()


def create_debt(
    conn: sqlite3.Connection,
    user_id: int,
    name: str,
    type: str,
    balance_cents: int,
    minimum_payment_cents: int,
    interest_status: str,
    apr_bps: int | None = None,
    next_due_date: str | None = None,
    is_flexible_payment: int = 0,
    priority: int = 0,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO debts
           (user_id, name, type, balance_cents, apr_bps, minimum_payment_cents, next_due_date,
            interest_status, is_flexible_payment, priority, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            name,
            type,
            balance_cents,
            apr_bps,
            minimum_payment_cents,
            next_due_date,
            interest_status,
            is_flexible_payment,
            priority,
            notes,
        ),
    )
    return cur.lastrowid


def update_debt(
    conn: sqlite3.Connection,
    user_id: int,
    debt_id: int,
    name: str,
    type: str,
    balance_cents: int,
    minimum_payment_cents: int,
    interest_status: str,
    is_active: int,
    apr_bps: int | None = None,
    next_due_date: str | None = None,
    is_flexible_payment: int = 0,
    priority: int = 0,
    notes: str | None = None,
) -> bool:
    cur = conn.execute(
        """UPDATE debts
           SET name = ?, type = ?, balance_cents = ?, apr_bps = ?, minimum_payment_cents = ?,
               next_due_date = ?, interest_status = ?, is_flexible_payment = ?, priority = ?,
               is_active = ?, notes = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
           WHERE user_id = ? AND id = ?""",
        (
            name,
            type,
            balance_cents,
            apr_bps,
            minimum_payment_cents,
            next_due_date,
            interest_status,
            is_flexible_payment,
            priority,
            is_active,
            notes,
            user_id,
            debt_id,
        ),
    )
    return cur.rowcount > 0


def update_balance(conn: sqlite3.Connection, user_id: int, debt_id: int, balance_cents: int) -> bool:
    """Quick-update path (bank sync's apply step) — changes only the balance, not the
    full record. Full edits (name/type/APR/minimum payment/etc.) still go through update_debt."""
    cur = conn.execute(
        """UPDATE debts SET balance_cents = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
           WHERE user_id = ? AND id = ?""",
        (balance_cents, user_id, debt_id),
    )
    return cur.rowcount > 0


def delete_debt(conn: sqlite3.Connection, user_id: int, debt_id: int) -> bool:
    cur = conn.execute("DELETE FROM debts WHERE user_id = ? AND id = ?", (user_id, debt_id))
    return cur.rowcount > 0
