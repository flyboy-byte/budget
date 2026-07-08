"""Raw parametrized SQL for the accounts table. No business logic — every function takes
user_id explicitly and scopes every query by it."""
import sqlite3

# Starter suggestions for the type datalist (app/templates/accounts/form.html), not
# an allowlist — `type` is plain free text (migration 0007_free_text_types.sql
# dropped the DB CHECK that used to restrict it). list_distinct_types() merges these
# with whatever a user has actually typed.
SEED_TYPES = ("checking", "savings", "cash", "other")


def list_distinct_types(conn: sqlite3.Connection, user_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT type FROM accounts WHERE user_id = ?", (user_id,)
    ).fetchall()
    return sorted(set(SEED_TYPES) | {r["type"] for r in rows})


def list_accounts(conn: sqlite3.Connection, user_id: int, include_inactive: bool = False) -> list[sqlite3.Row]:
    if include_inactive:
        return conn.execute(
            "SELECT * FROM accounts WHERE user_id = ? ORDER BY display_order, name",
            (user_id,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM accounts WHERE user_id = ? AND is_active = 1 ORDER BY display_order, name",
        (user_id,),
    ).fetchall()


def get_account(conn: sqlite3.Connection, user_id: int, account_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM accounts WHERE user_id = ? AND id = ?", (user_id, account_id)
    ).fetchone()


def create_account(
    conn: sqlite3.Connection,
    user_id: int,
    name: str,
    type: str,
    balance_cents: int,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO accounts (user_id, name, type, balance_cents, notes)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, name, type, balance_cents, notes),
    )
    return cur.lastrowid


def update_account(
    conn: sqlite3.Connection,
    user_id: int,
    account_id: int,
    name: str,
    type: str,
    balance_cents: int,
    is_active: int,
    notes: str | None = None,
) -> bool:
    cur = conn.execute(
        """UPDATE accounts
           SET name = ?, type = ?, balance_cents = ?, is_active = ?, notes = ?,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
           WHERE user_id = ? AND id = ?""",
        (name, type, balance_cents, is_active, notes, user_id, account_id),
    )
    return cur.rowcount > 0


def update_balance(conn: sqlite3.Connection, user_id: int, account_id: int, balance_cents: int) -> bool:
    """Quick-update path (the 'Today' screen) — changes only the balance, not the
    full record. Full edits (name/type/notes/active) still go through update_account."""
    cur = conn.execute(
        """UPDATE accounts SET balance_cents = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
           WHERE user_id = ? AND id = ?""",
        (balance_cents, user_id, account_id),
    )
    return cur.rowcount > 0


def adjust_balance(conn: sqlite3.Connection, user_id: int, account_id: int, delta_cents: int) -> bool:
    """Atomic relative adjustment (+credit/-debit), for actions that represent a real
    event moving money through an account rather than a full balance overwrite."""
    cur = conn.execute(
        """UPDATE accounts SET balance_cents = balance_cents + ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
           WHERE user_id = ? AND id = ?""",
        (delta_cents, user_id, account_id),
    )
    return cur.rowcount > 0


def get_default_account(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    """The implicit account quick actions move money through — the first active
    account by the same (display_order, name) sort list_accounts already uses.
    Reordering accounts is the existing lever to change which one this picks."""
    return conn.execute(
        "SELECT * FROM accounts WHERE user_id = ? AND is_active = 1 ORDER BY display_order, name LIMIT 1",
        (user_id,),
    ).fetchone()


def delete_account(conn: sqlite3.Connection, user_id: int, account_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM accounts WHERE user_id = ? AND id = ?", (user_id, account_id)
    )
    return cur.rowcount > 0
