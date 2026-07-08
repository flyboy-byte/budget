import sqlite3

from fastapi import Depends, HTTPException, Request, status

from app.db import get_connection
from app.security import SESSION_COOKIE_NAME, get_valid_session, touch_session, verify_csrf
from app.services.calc import get_setting


def get_db():
    with get_connection() as conn:
        yield conn


def get_current_user_id(request: Request, db: sqlite3.Connection = Depends(get_db)) -> int:
    """Raises 401 if there's no valid session. Slides the session expiration forward
    server-side (sessions.expires_at). The browser cookie's Max-Age is refreshed by
    refresh_session_cookie_middleware in app/main.py, via request.state.session_id set
    here — a dependency can't reliably set cookies on a route that returns its own
    Response object (TemplateResponse/RedirectResponse), since FastAPI only merges an
    injected Response dependency's headers when the endpoint returns a plain value."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    session = get_valid_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    touch_session(db, session_id)
    db.commit()

    request.state.session_id = session_id
    request.state.timezone = get_setting(db, session["user_id"], "timezone")
    return session["user_id"]


def require_admin(
    user_id: int = Depends(get_current_user_id), db: sqlite3.Connection = Depends(get_db)
) -> int:
    """For admin-only routes (user management). Raises 403 for a non-admin — never
    silently no-ops, since these routes mutate other users' accounts."""
    row = db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None or not row["is_admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user_id


def get_csrf_token(request: Request, db: sqlite3.Connection = Depends(get_db)) -> str:
    """For embedding in forms/htmx headers on already-authenticated pages. Every current
    call site pairs this with get_current_user_id, which already validated the session —
    but that pairing isn't enforced by the type system, so this still fails cleanly
    (401) rather than a raw 500 if it's ever used without get_current_user_id."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    session = get_valid_session(db, session_id) if session_id else None
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return session["csrf_secret"]


async def verify_csrf_token(
    request: Request, db: sqlite3.Connection = Depends(get_db)
) -> None:
    """Dependency for mutating routes. Compares the session's csrf_secret against a
    submitted `csrf_token` form field or `X-CSRF-Token` header."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed")

    session = get_valid_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed")

    submitted = request.headers.get("X-CSRF-Token")
    if submitted is None:
        form = await request.form()
        submitted = form.get("csrf_token")

    if not verify_csrf(session["csrf_secret"], submitted):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed")
