"""Raw parametrized SQL for the obligations table. No business logic — every function
takes user_id explicitly and scopes every query by it."""
import sqlite3

RECURRENCE_RULES = ("weekly", "biweekly", "monthly", "yearly")


def list_obligations(conn: sqlite3.Connection, user_id: int, include_paid: bool = False) -> list[sqlite3.Row]:
    if include_paid:
        return conn.execute(
            "SELECT * FROM obligations WHERE user_id = ? ORDER BY due_date", (user_id,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM obligations WHERE user_id = ? AND is_paid = 0 ORDER BY due_date",
        (user_id,),
    ).fetchall()


def list_distinct_categories(conn: sqlite3.Connection, user_id: int) -> list[str]:
    """Category is free-text (no CHECK constraint, unlike committed_purchases)
    — this powers a <datalist> of the user's own past values so
    "Housing" and "housing" don't quietly fork into two categories."""
    rows = conn.execute(
        "SELECT DISTINCT category FROM obligations WHERE user_id = ? ORDER BY category",
        (user_id,),
    ).fetchall()
    return [r["category"] for r in rows]


def get_obligation(conn: sqlite3.Connection, user_id: int, obligation_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM obligations WHERE user_id = ? AND id = ?", (user_id, obligation_id)
    ).fetchone()


def create_obligation(
    conn: sqlite3.Connection,
    user_id: int,
    name: str,
    category: str,
    amount_cents: int,
    due_date: str,
    is_recurring: int = 0,
    recurrence_rule: str | None = None,
    is_required: int = 1,
    auto_pay: int = 0,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO obligations
           (user_id, name, category, amount_cents, due_date, is_recurring, recurrence_rule,
            is_required, auto_pay, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            name,
            category,
            amount_cents,
            due_date,
            is_recurring,
            recurrence_rule,
            is_required,
            auto_pay,
            notes,
        ),
    )
    return cur.lastrowid


def update_obligation(
    conn: sqlite3.Connection,
    user_id: int,
    obligation_id: int,
    name: str,
    category: str,
    amount_cents: int,
    due_date: str,
    is_paid: int,
    is_recurring: int = 0,
    recurrence_rule: str | None = None,
    is_required: int = 1,
    auto_pay: int = 0,
    notes: str | None = None,
    paid_date: str | None = None,
) -> bool:
    cur = conn.execute(
        """UPDATE obligations
           SET name = ?, category = ?, amount_cents = ?, due_date = ?, is_paid = ?,
               is_recurring = ?, recurrence_rule = ?, is_required = ?, auto_pay = ?, notes = ?,
               paid_date = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
           WHERE user_id = ? AND id = ?""",
        (
            name,
            category,
            amount_cents,
            due_date,
            is_paid,
            is_recurring,
            recurrence_rule,
            is_required,
            auto_pay,
            notes,
            paid_date,
            user_id,
            obligation_id,
        ),
    )
    return cur.rowcount > 0


def delete_obligation(conn: sqlite3.Connection, user_id: int, obligation_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM obligations WHERE user_id = ? AND id = ?", (user_id, obligation_id)
    )
    return cur.rowcount > 0


def list_due_soon_unpushed(
    conn: sqlite3.Connection, user_id: int, today: str, tomorrow: str
) -> list[sqlite3.Row]:
    """Unpaid obligations due today or tomorrow that haven't already had a
    bill-due push fire today -- backs Phase 6's send_due_bill_pushes."""
    return conn.execute(
        """SELECT * FROM obligations
           WHERE user_id = ? AND is_paid = 0 AND due_date IN (?, ?)
             AND (last_bill_push_date IS NULL OR last_bill_push_date != ?)
           ORDER BY due_date""",
        (user_id, today, tomorrow, today),
    ).fetchall()


def mark_bill_push_sent(conn: sqlite3.Connection, user_id: int, obligation_id: int, today: str) -> None:
    conn.execute(
        "UPDATE obligations SET last_bill_push_date = ? WHERE user_id = ? AND id = ?",
        (today, user_id, obligation_id),
    )
