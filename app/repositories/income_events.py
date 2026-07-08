"""Raw parametrized SQL for the income_events table. No business logic — every function
takes user_id explicitly and scopes every query by it."""
import sqlite3

CONFIDENCE_LEVELS = ("confirmed", "likely", "uncertain")
RECURRENCE_RULES = ("weekly", "biweekly", "monthly", "yearly")


def list_income_events(conn: sqlite3.Connection, user_id: int, include_received: bool = False) -> list[sqlite3.Row]:
    if include_received:
        return conn.execute(
            "SELECT * FROM income_events WHERE user_id = ? ORDER BY expected_date", (user_id,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM income_events WHERE user_id = ? AND is_received = 0 ORDER BY expected_date",
        (user_id,),
    ).fetchall()


def get_income_event(conn: sqlite3.Connection, user_id: int, income_event_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM income_events WHERE user_id = ? AND id = ?", (user_id, income_event_id)
    ).fetchone()


def create_income_event(
    conn: sqlite3.Connection,
    user_id: int,
    source: str,
    expected_amount_cents: int,
    expected_date: str,
    confidence: str,
    is_recurring: int = 0,
    recurrence_rule: str | None = None,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO income_events
           (user_id, source, expected_amount_cents, expected_date, confidence,
            is_recurring, recurrence_rule, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, source, expected_amount_cents, expected_date, confidence, is_recurring, recurrence_rule, notes),
    )
    return cur.lastrowid


def update_income_event(
    conn: sqlite3.Connection,
    user_id: int,
    income_event_id: int,
    source: str,
    expected_amount_cents: int,
    expected_date: str,
    confidence: str,
    is_received: int,
    received_date: str | None = None,
    received_amount_cents: int | None = None,
    is_recurring: int = 0,
    recurrence_rule: str | None = None,
    notes: str | None = None,
) -> bool:
    cur = conn.execute(
        """UPDATE income_events
           SET source = ?, expected_amount_cents = ?, expected_date = ?, confidence = ?,
               is_received = ?, received_date = ?, received_amount_cents = ?,
               is_recurring = ?, recurrence_rule = ?, notes = ?,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
           WHERE user_id = ? AND id = ?""",
        (
            source,
            expected_amount_cents,
            expected_date,
            confidence,
            is_received,
            received_date,
            received_amount_cents,
            is_recurring,
            recurrence_rule,
            notes,
            user_id,
            income_event_id,
        ),
    )
    return cur.rowcount > 0


def delete_income_event(conn: sqlite3.Connection, user_id: int, income_event_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM income_events WHERE user_id = ? AND id = ?", (user_id, income_event_id)
    )
    return cur.rowcount > 0
