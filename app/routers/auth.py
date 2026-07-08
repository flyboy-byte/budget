import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import crypto, ratelimit
from app.config import SECURE_COOKIES
from app.deps import get_db, verify_csrf_token
from app.security import (
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE_MAX_AGE_SECONDS,
    SESSION_COOKIE_NAME,
    create_session,
    delete_session,
    verify_password,
)
from app.services import request_access as request_access_service
from app.services import totp as totp_service
from app.templating import templates

router = APIRouter()

LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(""),
    totp_code: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),
):
    ip = _client_ip(request)
    if ratelimit.is_limited(f"login:{ip}", max_attempts=LOGIN_MAX_ATTEMPTS, window_seconds=LOGIN_WINDOW_SECONDS):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Too many login attempts. Try again in a few minutes."},
            status_code=429,
        )

    user = db.execute(
        "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
    ).fetchone()

    # Always run a real argon2 verification, even for a nonexistent username, so response
    # timing doesn't leak which usernames exist.
    password_hash = user["password_hash"] if user is not None else DUMMY_PASSWORD_HASH
    password_ok = verify_password(password, password_hash)

    auth_mode = user["auth_mode"] if user is not None else "password"
    password_required = auth_mode in ("password", "both")
    totp_required = auth_mode in ("totp", "both")

    candidate_step = None
    if user is not None and totp_required:
        secret = crypto.decrypt_totp_secret(user["totp_secret_encrypted"])
        candidate_step = totp_service.verify_code(secret, totp_code, user["totp_last_used_step"])
        totp_ok = candidate_step is not None
    else:
        # Always spend equivalent time on a dummy verification, mirroring
        # DUMMY_PASSWORD_HASH's anti-timing-enumeration trick above -- otherwise a
        # nonexistent username or a password-only user's response is measurably
        # faster than one that actually runs TOTP verification, leaking which
        # usernames exist and which have TOTP enabled.
        totp_service.verify_code(totp_service.DUMMY_TOTP_SECRET, totp_code, None)
        totp_ok = not totp_required

    success = user is not None and (not password_required or password_ok) and totp_ok

    # Only touch the DB once every other factor has already passed -- get_connection()
    # commits on any normal (non-exception) exit, including a 401 response, so writing
    # here unconditionally would burn a correct TOTP code even when paired with a wrong
    # password in "both" mode. Once we get here the attempt would otherwise fully
    # succeed, so this conditional write is what actually closes the replay race: two
    # near-simultaneous requests carrying the same captured, still-valid code can't
    # both read the same stale totp_last_used_step and both win, because SQLite
    # serializes writers -- the second one's WHERE clause sees the first's committed
    # value and rowcount comes back 0.
    if success and candidate_step is not None:
        cur = db.execute(
            """UPDATE users SET totp_last_used_step = ? WHERE id = ?
               AND (totp_last_used_step IS NULL OR totp_last_used_step < ?)""",
            (candidate_step, user["id"], candidate_step),
        )
        success = cur.rowcount > 0

    if not success:
        ratelimit.record_failure(f"login:{ip}")
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid username or password"}, status_code=401
        )

    ratelimit.reset(f"login:{ip}")
    session_id, _csrf_secret = create_session(db, user["id"], request.headers.get("user-agent"))
    db.commit()

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
    )
    return response


REQUEST_ACCESS_MAX_ATTEMPTS = 5
REQUEST_ACCESS_WINDOW_SECONDS = 3600


@router.post("/login/request-access")
def request_access_submit(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    message: str = Form(""),
):
    """No session exists yet at this point (same reasoning as /login itself), so this
    is CSRF-exempt and rate-limited by IP instead -- see request_access.py for why
    this replaced a client-side EmailJS call entirely rather than just adding a
    server-side proxy in front of it."""
    ip = _client_ip(request)
    if ratelimit.is_limited(
        f"request-access:{ip}", max_attempts=REQUEST_ACCESS_MAX_ATTEMPTS, window_seconds=REQUEST_ACCESS_WINDOW_SECONDS
    ):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": None, "request_access_error": "Too many requests. Try again later."},
            status_code=429,
        )

    if not name.strip() or not email.strip():
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": None, "request_access_error": "Name and email are both required."},
            status_code=400,
        )

    ratelimit.record_failure(f"request-access:{ip}")
    try:
        request_access_service.send_request_access_notification(name.strip(), email.strip(), message.strip())
    except request_access_service.RequestAccessError:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": None, "request_access_error": "Couldn't send the request. Try again later."},
            status_code=502,
        )

    return templates.TemplateResponse(
        request, "login.html", {"error": None, "request_access_success": True}
    )


@router.post("/logout", dependencies=[Depends(verify_csrf_token)])
def logout(request: Request, db: sqlite3.Connection = Depends(get_db)):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        delete_session(db, session_id)
        db.commit()

    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
