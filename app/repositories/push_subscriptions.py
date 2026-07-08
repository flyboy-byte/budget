"""Raw parametrized SQL for push_subscriptions. No business logic — every function
takes user_id explicitly and scopes/ownership-checks every query by it."""
import sqlite3


def create(
    conn: sqlite3.Connection, user_id: int, endpoint: str, p256dh: str, auth: str
) -> int:
    """Re-subscribing with an endpoint the browser already gave out (e.g. after
    clearing site data) replaces the old keys rather than erroring, since the
    endpoint UNIQUE constraint would otherwise reject it outright."""
    cur = conn.execute(
        """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(endpoint) DO UPDATE SET
             user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth""",
        (user_id, endpoint, p256dh, auth),
    )
    return cur.lastrowid


def list_for_user(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM push_subscriptions WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()


def delete(conn: sqlite3.Connection, user_id: int, subscription_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM push_subscriptions WHERE user_id = ? AND id = ?",
        (user_id, subscription_id),
    )
    return cur.rowcount > 0


def delete_by_endpoint(conn: sqlite3.Connection, endpoint: str) -> bool:
    """Used by push.py to prune a dead subscription on a 404/410 from the push
    service — at that point we only have the endpoint, not the owning user_id."""
    cur = conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    return cur.rowcount > 0
