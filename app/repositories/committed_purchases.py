"""Raw parametrized SQL for the committed_purchases table. No business logic — every
function takes user_id explicitly and scopes every query by it.

Note: this repository lets amount_paid_cents/status be edited directly, same as any other
field, for plain CRUD purposes. Once app/services/payments.py exists (a later phase), it
becomes the only path that should record an actual payment (keeping transactions,
amount_paid_cents, and status in sync in one operation) — this repository stays dumb.
"""
import sqlite3

# Starter suggestions for the category datalist (committed_purchases/form.html and the
# dashboard's quick-purchase form), not an allowlist — `category` is plain free text
# (migration 0012_free_text_purchase_categories.sql dropped the DB CHECK that used to
# restrict it), same shape as accounts.type/debts.type (0007_free_text_types.sql).
SEED_CATEGORIES = ("career_tool", "school", "car", "debt", "hobby", "food", "gambling", "other")
STATUSES = ("planned", "ordered", "arrived", "partially_paid", "paid", "canceled")


def list_distinct_categories(conn: sqlite3.Connection, user_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT category FROM committed_purchases WHERE user_id = ?", (user_id,)
    ).fetchall()
    return sorted(set(SEED_CATEGORIES) | {r["category"] for r in rows})


def list_purchases(conn: sqlite3.Connection, user_id: int, include_resolved: bool = False) -> list[sqlite3.Row]:
    if include_resolved:
        return conn.execute(
            "SELECT * FROM committed_purchases WHERE user_id = ? ORDER BY payment_deadline IS NULL, payment_deadline, name",
            (user_id,),
        ).fetchall()
    return conn.execute(
        """SELECT * FROM committed_purchases WHERE user_id = ? AND status NOT IN ('paid', 'canceled')
           ORDER BY payment_deadline IS NULL, payment_deadline, name""",
        (user_id,),
    ).fetchall()


def get_purchase(conn: sqlite3.Connection, user_id: int, purchase_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM committed_purchases WHERE user_id = ? AND id = ?", (user_id, purchase_id)
    ).fetchone()


def create_purchase(
    conn: sqlite3.Connection,
    user_id: int,
    name: str,
    category: str,
    amount_cents: int,
    status: str,
    amount_paid_cents: int = 0,
    order_date: str | None = None,
    expected_arrival_date: str | None = None,
    payment_deadline: str | None = None,
    priority: int = 0,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO committed_purchases
           (user_id, name, category, amount_cents, amount_paid_cents, status,
            order_date, expected_arrival_date, payment_deadline, priority, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            name,
            category,
            amount_cents,
            amount_paid_cents,
            status,
            order_date,
            expected_arrival_date,
            payment_deadline,
            priority,
            notes,
        ),
    )
    return cur.lastrowid


def update_purchase(
    conn: sqlite3.Connection,
    user_id: int,
    purchase_id: int,
    name: str,
    category: str,
    amount_cents: int,
    amount_paid_cents: int,
    status: str,
    order_date: str | None = None,
    expected_arrival_date: str | None = None,
    payment_deadline: str | None = None,
    priority: int = 0,
    notes: str | None = None,
) -> bool:
    cur = conn.execute(
        """UPDATE committed_purchases
           SET name = ?, category = ?, amount_cents = ?, amount_paid_cents = ?, status = ?,
               order_date = ?, expected_arrival_date = ?, payment_deadline = ?, priority = ?,
               notes = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
           WHERE user_id = ? AND id = ?""",
        (
            name,
            category,
            amount_cents,
            amount_paid_cents,
            status,
            order_date,
            expected_arrival_date,
            payment_deadline,
            priority,
            notes,
            user_id,
            purchase_id,
        ),
    )
    return cur.rowcount > 0


def record_payment(conn: sqlite3.Connection, user_id: int, purchase_id: int, payment_cents: int) -> bool | None:
    """Quick-payment path (the 'Today' screen) — adds to amount_paid_cents and flips
    status to paid/partially_paid accordingly. Returns None if the purchase doesn't
    exist (vs False for 'exists but the update violated a constraint', which the
    caller sees as a raised sqlite3.IntegrityError from the CHECK(amount_paid_cents
    <= amount_cents) constraint — same safety net every other CRUD route relies on)."""
    purchase = get_purchase(conn, user_id, purchase_id)
    if purchase is None:
        return None

    new_paid_cents = purchase["amount_paid_cents"] + payment_cents
    new_status = "paid" if new_paid_cents >= purchase["amount_cents"] else "partially_paid"

    cur = conn.execute(
        """UPDATE committed_purchases
           SET amount_paid_cents = ?, status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
           WHERE user_id = ? AND id = ?""",
        (new_paid_cents, new_status, user_id, purchase_id),
    )
    return cur.rowcount > 0


def delete_purchase(conn: sqlite3.Connection, user_id: int, purchase_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM committed_purchases WHERE user_id = ? AND id = ?", (user_id, purchase_id)
    )
    return cur.rowcount > 0
