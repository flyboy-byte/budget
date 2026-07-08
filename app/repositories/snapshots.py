"""Raw parametrized SQL for the snapshots table. No business logic — every function takes
user_id explicitly and scopes every query by it."""
import sqlite3


def list_snapshots(conn: sqlite3.Connection, user_id: int, limit: int = 365) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM snapshots WHERE user_id = ? ORDER BY snapshot_date DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()


def get_snapshot(conn: sqlite3.Connection, user_id: int, snapshot_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM snapshots WHERE user_id = ? AND id = ?", (user_id, snapshot_id)
    ).fetchone()


def get_snapshot_by_date(conn: sqlite3.Connection, user_id: int, snapshot_date: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM snapshots WHERE user_id = ? AND snapshot_date = ?", (user_id, snapshot_date)
    ).fetchone()


def upsert_snapshot(
    conn: sqlite3.Connection,
    user_id: int,
    snapshot_date: str,
    values: dict,
    notes: str | None = None,
) -> None:
    """One snapshot per user per day — capturing again on the same date overwrites it,
    since the point is a fresh read of "today", not a append-only log of every capture."""
    conn.execute(
        """INSERT INTO snapshots
           (user_id, snapshot_date, cash_on_hand_cents, total_debt_cents, net_position_cents,
            reserved_cash_cents, safe_to_spend_cents, forecast_position_cents,
            protected_floor_cents, window_days, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (user_id, snapshot_date) DO UPDATE SET
             cash_on_hand_cents = excluded.cash_on_hand_cents,
             total_debt_cents = excluded.total_debt_cents,
             net_position_cents = excluded.net_position_cents,
             reserved_cash_cents = excluded.reserved_cash_cents,
             safe_to_spend_cents = excluded.safe_to_spend_cents,
             forecast_position_cents = excluded.forecast_position_cents,
             protected_floor_cents = excluded.protected_floor_cents,
             window_days = excluded.window_days,
             notes = excluded.notes""",
        (
            user_id,
            snapshot_date,
            values["cash_on_hand_cents"],
            values["total_debt_cents"],
            values["net_position_cents"],
            values["reserved_cash_cents"],
            values["safe_to_spend_cents"],
            values["forecast_position_cents"],
            values["protected_floor_cents"],
            values["window_days"],
            notes,
        ),
    )


def delete_snapshot(conn: sqlite3.Connection, user_id: int, snapshot_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM snapshots WHERE user_id = ? AND id = ?", (user_id, snapshot_id)
    )
    return cur.rowcount > 0
