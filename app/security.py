"""Password hashing, server-revocable sessions, and CSRF token handling.

Sessions are opaque random tokens stored server-side (sessions table) so they can be
revoked instantly — a cookie alone is never trusted. Session lifetime slides forward on
each authenticated request up to SESSION_LIFETIME from "now", capped by re-issuing
expires_at rather than growing unbounded.
"""
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

SESSION_LIFETIME = timedelta(days=30)
SESSION_COOKIE_NAME = "session_id"
SESSION_COOKIE_MAX_AGE_SECONDS = int(SESSION_LIFETIME.total_seconds())

_hasher = PasswordHasher()

# Used to run a real argon2 verification (and thus take real argon2 time) when the
# username doesn't exist, so login response time doesn't leak which usernames are valid.
DUMMY_PASSWORD_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (Argon2Error, ValueError):
        # InvalidHashError (malformed hash) is a ValueError subclass, not Argon2Error.
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _expires_at_iso() -> str:
    return (datetime.now(timezone.utc) + SESSION_LIFETIME).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def create_session(conn: sqlite3.Connection, user_id: int, user_agent: str | None = None) -> tuple[str, str]:
    """Returns (session_id, csrf_secret). Caller is responsible for committing."""
    session_id = secrets.token_urlsafe(32)
    csrf_secret = secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO sessions (id, user_id, expires_at, csrf_secret, user_agent)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, user_id, _expires_at_iso(), csrf_secret, user_agent),
    )
    return session_id, csrf_secret


def get_valid_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    if row["expires_at"] <= _now_iso():
        return None
    return row


def touch_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Slides the expiration forward. Caller is responsible for committing."""
    conn.execute(
        "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
        (_now_iso(), _expires_at_iso(), session_id),
    )


def delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def list_sessions(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """Active (non-expired) sessions, most-recently-active first."""
    return conn.execute(
        "SELECT * FROM sessions WHERE user_id = ? AND expires_at > ? ORDER BY last_seen_at DESC",
        (user_id, _now_iso()),
    ).fetchall()


def delete_session_for_user(conn: sqlite3.Connection, user_id: int, session_id: str) -> bool:
    """Ownership-scoped revoke — prevents guessing another user's session id."""
    cur = conn.execute("DELETE FROM sessions WHERE user_id = ? AND id = ?", (user_id, session_id))
    return cur.rowcount > 0


def delete_other_sessions(conn: sqlite3.Connection, user_id: int, keep_session_id: str) -> int:
    """'Log out everywhere else.' Returns how many sessions were revoked."""
    cur = conn.execute(
        "DELETE FROM sessions WHERE user_id = ? AND id != ?", (user_id, keep_session_id)
    )
    return cur.rowcount


def verify_csrf(session_csrf_secret: str, submitted_token: str | None) -> bool:
    if not submitted_token:
        return False
    return secrets.compare_digest(session_csrf_secret, submitted_token)
