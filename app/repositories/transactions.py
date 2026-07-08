"""Raw parametrized SQL for the transactions table (the polymorphic ledger). No
business logic — every function takes user_id explicitly and scopes every query by
it. Callers are responsible for satisfying the target_type/FK CHECK constraint
(see migrations/0001_initial.sql, widened by 0010_spending_transactions.sql)."""
import sqlite3

TARGET_TYPES = ("debt", "obligation", "committed_purchase", "other", "spending")


def create_transaction(
    conn: sqlite3.Connection,
    user_id: int,
    transaction_date: str,
    amount_cents: int,
    target_type: str,
    *,
    account_id: int | None = None,
    debt_id: int | None = None,
    obligation_id: int | None = None,
    committed_purchase_id: int | None = None,
    category: str | None = None,
    memo: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO transactions
           (user_id, transaction_date, amount_cents, account_id, target_type,
            debt_id, obligation_id, committed_purchase_id, category, memo)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            transaction_date,
            amount_cents,
            account_id,
            target_type,
            debt_id,
            obligation_id,
            committed_purchase_id,
            category,
            memo,
        ),
    )
    return cur.lastrowid


def list_since(conn: sqlite3.Connection, user_id: int, since_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM transactions WHERE user_id = ? AND transaction_date >= ?
           ORDER BY transaction_date DESC, created_at DESC""",
        (user_id, since_date),
    ).fetchall()


def list_recent(conn: sqlite3.Connection, user_id: int, limit: int = 50) -> list[sqlite3.Row]:
    """Newest-first ledger slice for the activity feed. Joins the target's name so
    the feed can say "Rent" rather than "obligation #4" without the router doing an
    N+1 lookup per row; the name is NULL for spending/other, which carry their own
    memo instead."""
    return conn.execute(
        """SELECT t.*,
                  o.name AS obligation_name,
                  cp.name AS purchase_name,
                  d.name AS debt_name,
                  a.name AS account_name
           FROM transactions t
           LEFT JOIN obligations o ON o.id = t.obligation_id
           LEFT JOIN committed_purchases cp ON cp.id = t.committed_purchase_id
           LEFT JOIN debts d ON d.id = t.debt_id
           LEFT JOIN accounts a ON a.id = t.account_id
           WHERE t.user_id = ?
           ORDER BY t.transaction_date DESC, t.created_at DESC
           LIMIT ?""",
        (user_id, limit),
    ).fetchall()


def count_for_user(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE user_id = ?", (user_id,)
    ).fetchone()[0]


def list_distinct_spending_categories(conn: sqlite3.Connection, user_id: int) -> list[str]:
    """Powers the <datalist> on the "just spending" form, same per-user
    autocomplete pattern as obligations.list_distinct_categories — so "gas" and
    "Gas" don't quietly fork into two categories."""
    rows = conn.execute(
        """SELECT DISTINCT category FROM transactions
           WHERE user_id = ? AND category IS NOT NULL AND category <> ''
           ORDER BY category""",
        (user_id,),
    ).fetchall()
    return [r["category"] for r in rows]
