"""Raw parametrized SQL for the settings table. No business logic — every function takes
user_id explicitly and scopes every query by it. Valid keys/defaults live in
app.services.calc (DEFAULT_SETTINGS), not here — this module just reads/writes rows.
"""
import sqlite3


def get_all(conn: sqlite3.Connection, user_id: int) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings WHERE user_id = ?", (user_id,)).fetchall()
    return {row["key"]: row["value"] for row in rows}


def upsert(conn: sqlite3.Connection, user_id: int, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?)
           ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value""",
        (user_id, key, value),
    )
