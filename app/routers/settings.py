import re
import sqlite3
import zoneinfo

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

from app import crypto, ratelimit, security
from app.deps import get_csrf_token, get_current_user_id, get_db, require_admin, verify_csrf_token
from app.money import cents_to_input_value, parse_dollars_to_cents
from app.repositories import push_subscriptions as push_subscriptions_repo
from app.repositories import settings as repo
from app.security import SESSION_COOKIE_NAME
from app.services import push as push_service
from app.services import totp as totp_service
from app.services.calc import DEFAULT_SETTINGS, FORECAST_INCOME_CONFIDENCE_OPTIONS, RESERVED_WINDOW_MODES
from app.totp_qr import build_qr_data_uri
from app.useragent import describe_user_agent
from app.templating import templates

router = APIRouter(prefix="/settings")

_VALID_TIMEZONES = zoneinfo.available_timezones()
MIN_PASSWORD_LENGTH = 8
MIN_BANK_SYNC_COOLDOWN_MINUTES = 5
# Alphanumeric/underscore/hyphen only — usernames flow unsanitized into the JSON
# backup's Content-Disposition filename (app/services/export.py::backup_filename), so
# CR/LF or quote characters there would break header parsing or crash the response.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# Deliberately loose — this only gates what an outbound digest email gets addressed
# to, not account security. Real deliverability failures surface from Resend's API
# response when scripts/digest.py actually sends, not from a stricter regex here.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ADD_USER_MAX_ATTEMPTS = 10
ADD_USER_WINDOW_SECONDS = 3600
CHANGE_PASSWORD_MAX_ATTEMPTS = 10
CHANGE_PASSWORD_WINDOW_SECONDS = 3600
TOTP_CONFIRM_MAX_ATTEMPTS = 10
TOTP_CONFIRM_WINDOW_SECONDS = 3600


def _saved_display_values(db: sqlite3.Connection, user_id: int) -> dict:
    """Reads what's actually persisted (merged with defaults) and formats for the form."""
    raw = {**DEFAULT_SETTINGS, **repo.get_all(db, user_id)}
    return {
        "protected_savings_floor": cents_to_input_value(int(raw["protected_savings_floor_cents"])),
        "reserved_window_mode": raw["reserved_window_mode"],
        "reserved_window_fixed_days": raw["reserved_window_fixed_days"],
        "forecast_window_days": raw["forecast_window_days"],
        "forecast_income_confidence": raw["forecast_income_confidence"],
        "timezone": raw["timezone"],
        "bank_sync_cooldown_minutes": raw["bank_sync_cooldown_minutes"],
        "bank_sync_use_available_balance": raw["bank_sync_use_available_balance"],
        "bank_sync_auto_apply": raw["bank_sync_auto_apply"],
        "bank_sync_auto_apply_max_change": cents_to_input_value(int(raw["bank_sync_auto_apply_max_change_cents"])),
        "digest_email": raw["digest_email"],
        "large_transaction_threshold": cents_to_input_value(int(raw["large_transaction_threshold_cents"])),
        "low_safe_to_spend_threshold": (
            cents_to_input_value(int(raw["low_safe_to_spend_threshold_cents"]))
            if raw["low_safe_to_spend_threshold_cents"]
            else ""
        ),
    }


def _totp_status(db: sqlite3.Connection, user_id: int) -> dict:
    """A pending (secret generated, not yet confirmed) setup is re-derived on every
    settings page load, not just right after /totp/setup — so navigating away and
    back still shows the same QR/manual key instead of silently losing it or forcing
    a restart. Only /totp/setup itself ever generates a *new* secret."""
    row = db.execute(
        "SELECT username, totp_secret_encrypted, totp_enabled, auth_mode FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    totp_enabled = bool(row["totp_enabled"])
    pending_qr = pending_secret = None
    if row["totp_secret_encrypted"] is not None and not totp_enabled:
        pending_secret = crypto.decrypt_totp_secret(row["totp_secret_encrypted"])
        uri = totp_service.provisioning_uri(pending_secret, row["username"])
        pending_qr = build_qr_data_uri(uri)
    return {
        "totp_enabled": totp_enabled,
        "auth_mode": row["auth_mode"],
        "totp_pending_qr": pending_qr,
        "totp_pending_secret": pending_secret,
    }


def _base_context(
    request: Request,
    db: sqlite3.Connection,
    user_id: int,
    csrf_token: str,
    *,
    error: str | None = None,
    saved: bool = False,
    notifications_error: str | None = None,
    notifications_saved: bool = False,
    new_user_error: str | None = None,
    new_user_created: str | None = None,
    password_error: str | None = None,
    password_saved: bool = False,
    totp_error: str | None = None,
    totp_confirmed: bool = False,
    totp_disabled: bool = False,
    auth_mode_error: str | None = None,
    auth_mode_saved: bool = False,
) -> dict:
    is_admin = bool(db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()["is_admin"])
    return {
        "values": _saved_display_values(db, user_id),
        "reserved_window_modes": RESERVED_WINDOW_MODES,
        "confidence_options": FORECAST_INCOME_CONFIDENCE_OPTIONS,
        "sessions": _session_rows(db, user_id, request.cookies[SESSION_COOKIE_NAME]),
        "csrf_token": csrf_token,
        "error": error,
        "saved": saved,
        "notifications_error": notifications_error,
        "notifications_saved": notifications_saved,
        "new_user_error": new_user_error,
        "new_user_created": new_user_created,
        "password_error": password_error,
        "password_saved": password_saved,
        **_totp_status(db, user_id),
        "totp_error": totp_error,
        "totp_confirmed": totp_confirmed,
        "totp_disabled": totp_disabled,
        "auth_mode_error": auth_mode_error,
        "auth_mode_saved": auth_mode_saved,
        "is_admin": is_admin,
        "all_users": _all_users_rows(db) if is_admin else None,
        "current_user_id": user_id,
        "push_public_key": _push_public_key(),
        "push_subscriptions": push_subscriptions_repo.list_for_user(db, user_id),
    }


def _push_public_key() -> str | None:
    """None (feature hidden in the template) until PUSH_VAPID_PUBLIC_KEY is
    configured on this deployment — same lazy-optional-feature pattern as
    digest_email being blank until a user opts in."""
    try:
        return push_service.get_public_key()
    except RuntimeError:
        return None


def _all_users_rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    """Admin-only: username/status/created_at plus last session activity across all
    users. Auth/account metadata only — never joins into any domain table, so an
    admin never sees another user's financial data through this."""
    return db.execute(
        """SELECT u.id, u.username, u.is_active, u.is_admin, u.created_at,
                  MAX(s.last_seen_at) AS last_seen_at
           FROM users u
           LEFT JOIN sessions s ON s.user_id = u.id
           GROUP BY u.id
           ORDER BY u.username"""
    ).fetchall()


def _session_rows(db: sqlite3.Connection, user_id: int, current_session_id: str) -> list[dict]:
    return [
        {
            "id": s["id"],
            "description": describe_user_agent(s["user_agent"]),
            "last_seen_at": s["last_seen_at"],
            "created_at": s["created_at"],
            "is_current": s["id"] == current_session_id,
        }
        for s in security.list_sessions(db, user_id)
    ]


@router.get("")
def settings_form(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        _base_context(request, db, user_id, csrf_token),
    )


@router.post("/sessions/{session_id}/revoke", dependencies=[Depends(verify_csrf_token)])
def revoke_session(
    session_id: str,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    security.delete_session_for_user(db, user_id, session_id)
    db.commit()
    return RedirectResponse(url="/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sessions/revoke-others", dependencies=[Depends(verify_csrf_token)])
def revoke_other_sessions(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    security.delete_other_sessions(db, user_id, request.cookies[SESSION_COOKIE_NAME])
    db.commit()
    return RedirectResponse(url="/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("", dependencies=[Depends(verify_csrf_token)])
def settings_submit(
    request: Request,
    protected_savings_floor: str = Form("0.00"),
    reserved_window_mode: str = Form(...),
    reserved_window_fixed_days: str = Form("14"),
    forecast_window_days: str = Form("30"),
    forecast_income_confidence: str = Form(...),
    timezone: str = Form("UTC"),
    bank_sync_cooldown_minutes: str = Form("360"),
    bank_sync_use_available_balance: str = Form(None),
    bank_sync_auto_apply: str = Form(None),
    bank_sync_auto_apply_max_change: str = Form("250.00"),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    submitted_values = {
        "protected_savings_floor": protected_savings_floor,
        "reserved_window_mode": reserved_window_mode,
        "reserved_window_fixed_days": reserved_window_fixed_days,
        "forecast_window_days": forecast_window_days,
        "forecast_income_confidence": forecast_income_confidence,
        "timezone": timezone,
        "bank_sync_cooldown_minutes": bank_sync_cooldown_minutes,
        "bank_sync_use_available_balance": "1" if bank_sync_use_available_balance else "0",
        "bank_sync_auto_apply": "1" if bank_sync_auto_apply else "0",
        "bank_sync_auto_apply_max_change": bank_sync_auto_apply_max_change,
    }

    def error_response(message: str):
        context = _base_context(request, db, user_id, csrf_token, error=message)
        context["values"] = {**context["values"], **submitted_values}
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            context,
            status_code=400,
        )

    if reserved_window_mode not in RESERVED_WINDOW_MODES:
        return error_response("Invalid reserved-cash window mode.")
    if forecast_income_confidence not in FORECAST_INCOME_CONFIDENCE_OPTIONS:
        return error_response("Invalid forecast income confidence.")
    if timezone not in _VALID_TIMEZONES:
        return error_response("Unrecognized timezone (must be an IANA name, e.g. America/Denver).")

    try:
        floor_cents = parse_dollars_to_cents(protected_savings_floor)
    except HTTPException:
        return error_response("Invalid protected savings floor amount.")
    if floor_cents < 0:
        return error_response("Protected savings floor can't be negative.")

    try:
        fixed_days = int(reserved_window_fixed_days)
        window_days = int(forecast_window_days)
    except ValueError:
        return error_response("Window/forecast days must be whole numbers.")
    if fixed_days < 1 or window_days < 1:
        return error_response("Window/forecast days must be at least 1.")

    try:
        cooldown_minutes = int(bank_sync_cooldown_minutes)
    except ValueError:
        return error_response("Bank sync cooldown must be a whole number.")
    if cooldown_minutes < MIN_BANK_SYNC_COOLDOWN_MINUTES:
        return error_response(f"Bank sync cooldown must be at least {MIN_BANK_SYNC_COOLDOWN_MINUTES} minutes.")

    try:
        auto_apply_max_change_cents = parse_dollars_to_cents(bank_sync_auto_apply_max_change)
    except HTTPException:
        return error_response("Invalid auto-apply threshold amount.")
    if auto_apply_max_change_cents < 0:
        return error_response("Auto-apply threshold can't be negative.")

    repo.upsert(db, user_id, "protected_savings_floor_cents", str(floor_cents))
    repo.upsert(db, user_id, "reserved_window_mode", reserved_window_mode)
    repo.upsert(db, user_id, "reserved_window_fixed_days", str(fixed_days))
    repo.upsert(db, user_id, "forecast_window_days", str(window_days))
    repo.upsert(db, user_id, "forecast_income_confidence", forecast_income_confidence)
    repo.upsert(db, user_id, "timezone", timezone)
    repo.upsert(db, user_id, "bank_sync_cooldown_minutes", str(cooldown_minutes))
    repo.upsert(db, user_id, "bank_sync_use_available_balance", submitted_values["bank_sync_use_available_balance"])
    repo.upsert(db, user_id, "bank_sync_auto_apply", submitted_values["bank_sync_auto_apply"])
    repo.upsert(db, user_id, "bank_sync_auto_apply_max_change_cents", str(auto_apply_max_change_cents))
    db.commit()

    return templates.TemplateResponse(
        request,
        "settings/index.html",
        _base_context(request, db, user_id, csrf_token, saved=True),
    )


@router.post("/notifications", dependencies=[Depends(verify_csrf_token)])
def settings_notifications_submit(
    request: Request,
    digest_email: str = Form(""),
    large_transaction_threshold: str = Form("100.00"),
    low_safe_to_spend_threshold: str = Form(""),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    submitted_values = {
        "digest_email": digest_email,
        "large_transaction_threshold": large_transaction_threshold,
        "low_safe_to_spend_threshold": low_safe_to_spend_threshold,
    }

    def error_response(message: str):
        context = _base_context(request, db, user_id, csrf_token, notifications_error=message)
        context["values"] = {**context["values"], **submitted_values}
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            context,
            status_code=400,
        )

    digest_email = digest_email.strip()
    if digest_email and not _EMAIL_RE.match(digest_email):
        return error_response("Digest email doesn't look like a valid address.")

    try:
        large_txn_threshold_cents = parse_dollars_to_cents(large_transaction_threshold)
    except HTTPException:
        return error_response("Invalid large-transaction alert threshold.")
    if large_txn_threshold_cents < 0:
        return error_response("Large-transaction alert threshold can't be negative.")

    low_safe_to_spend_threshold = low_safe_to_spend_threshold.strip()
    if low_safe_to_spend_threshold:
        try:
            low_safe_to_spend_threshold_cents = parse_dollars_to_cents(low_safe_to_spend_threshold)
        except HTTPException:
            return error_response("Invalid low-safe-to-spend alert threshold.")
        if low_safe_to_spend_threshold_cents < 0:
            return error_response("Low-safe-to-spend alert threshold can't be negative.")
    else:
        low_safe_to_spend_threshold_cents = None

    repo.upsert(db, user_id, "large_transaction_threshold_cents", str(large_txn_threshold_cents))
    repo.upsert(db, user_id, "digest_email", digest_email)

    new_threshold_str = "" if low_safe_to_spend_threshold_cents is None else str(low_safe_to_spend_threshold_cents)
    if new_threshold_str != repo.get_all(db, user_id).get("low_safe_to_spend_threshold_cents", ""):
        # Threshold changed -- clear the hysteresis flag so a stale "already alerted"
        # state from before the change can't suppress a legitimate new alert.
        repo.upsert(db, user_id, "low_safe_to_spend_alert_active", "0")
    repo.upsert(db, user_id, "low_safe_to_spend_threshold_cents", new_threshold_str)
    db.commit()

    return templates.TemplateResponse(
        request,
        "settings/index.html",
        _base_context(request, db, user_id, csrf_token, notifications_saved=True),
    )


@router.post("/users", dependencies=[Depends(verify_csrf_token)])
def add_user(
    request: Request,
    new_username: str = Form(...),
    new_password: str = Form(...),
    user_id: int = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    username = new_username.strip()
    rate_key = f"add-user:{user_id}"

    def error_response(message: str, status_code: int = 400):
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            _base_context(request, db, user_id, csrf_token, new_user_error=message),
            status_code=status_code,
        )

    if ratelimit.is_limited(rate_key, max_attempts=ADD_USER_MAX_ATTEMPTS, window_seconds=ADD_USER_WINDOW_SECONDS):
        return error_response("Too many accounts created recently. Try again later.", status_code=429)
    if not username:
        return error_response("Username can't be blank.")
    if not _USERNAME_RE.match(username):
        return error_response("Username can only contain letters, numbers, underscores, and hyphens.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return error_response(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        return error_response("That username is already taken.")

    ratelimit.record_failure(rate_key)
    db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, security.hash_password(new_password)),
    )
    db.commit()

    return templates.TemplateResponse(
        request,
        "settings/index.html",
        _base_context(request, db, user_id, csrf_token, new_user_created=username),
    )


@router.post("/password", dependencies=[Depends(verify_csrf_token)])
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    rate_key = f"change-password:{user_id}"

    def error_response(message: str, status_code: int = 400):
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            _base_context(request, db, user_id, csrf_token, password_error=message),
            status_code=status_code,
        )

    if ratelimit.is_limited(
        rate_key, max_attempts=CHANGE_PASSWORD_MAX_ATTEMPTS, window_seconds=CHANGE_PASSWORD_WINDOW_SECONDS
    ):
        return error_response("Too many attempts. Try again later.", status_code=429)

    row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None or not security.verify_password(current_password, row["password_hash"]):
        ratelimit.record_failure(rate_key)
        return error_response("Current password is incorrect.")

    if new_password != confirm_password:
        return error_response("New password and confirmation don't match.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return error_response(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (security.hash_password(new_password), user_id),
    )
    # Revoke every *other* session but keep this one valid — the user making this
    # request shouldn't be logged out by changing their own password.
    security.delete_other_sessions(db, user_id, request.cookies[SESSION_COOKIE_NAME])
    db.commit()

    return templates.TemplateResponse(
        request,
        "settings/index.html",
        _base_context(request, db, user_id, csrf_token, password_saved=True),
    )


@router.post("/totp/setup", dependencies=[Depends(verify_csrf_token)])
def totp_setup(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    """Always generates a fresh secret, overwriting any unconfirmed pending one --
    an explicit click here means "start over." A pending (unconfirmed) secret is
    otherwise preserved across page loads by _totp_status, not regenerated on every
    GET, so the QR the user already scanned stays valid until they confirm or
    deliberately restart."""
    secret = totp_service.generate_secret()
    db.execute(
        "UPDATE users SET totp_secret_encrypted = ?, totp_enabled = 0, totp_last_used_step = NULL WHERE id = ?",
        (crypto.encrypt_totp_secret(secret), user_id),
    )
    db.commit()
    return templates.TemplateResponse(
        request, "settings/index.html", _base_context(request, db, user_id, csrf_token)
    )


@router.post("/totp/confirm", dependencies=[Depends(verify_csrf_token)])
def totp_confirm(
    request: Request,
    code: str = Form(...),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    rate_key = f"totp-confirm:{user_id}"
    if ratelimit.is_limited(rate_key, max_attempts=TOTP_CONFIRM_MAX_ATTEMPTS, window_seconds=TOTP_CONFIRM_WINDOW_SECONDS):
        return templates.TemplateResponse(
            request, "settings/index.html",
            _base_context(request, db, user_id, csrf_token, totp_error="Too many attempts. Try again later."),
            status_code=429,
        )

    row = db.execute("SELECT totp_secret_encrypted FROM users WHERE id = ?", (user_id,)).fetchone()
    if row["totp_secret_encrypted"] is None:
        return templates.TemplateResponse(
            request, "settings/index.html",
            _base_context(request, db, user_id, csrf_token, totp_error="Start setup again before confirming."),
            status_code=400,
        )

    secret = crypto.decrypt_totp_secret(row["totp_secret_encrypted"])
    step = totp_service.verify_code(secret, code, last_used_step=None)
    if step is None:
        ratelimit.record_failure(rate_key)
        return templates.TemplateResponse(
            request, "settings/index.html",
            _base_context(request, db, user_id, csrf_token, totp_error="That code didn't match. Try again."),
            status_code=400,
        )

    db.execute("UPDATE users SET totp_enabled = 1, totp_last_used_step = ? WHERE id = ?", (step, user_id))
    db.commit()
    return templates.TemplateResponse(
        request, "settings/index.html", _base_context(request, db, user_id, csrf_token, totp_confirmed=True)
    )


@router.post("/totp/disable", dependencies=[Depends(verify_csrf_token)])
def totp_disable(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    # Forces auth_mode back to 'password' in the same statement -- the DB CHECK
    # requires it (auth_mode can't be totp/both once totp_enabled=0), and it's the
    # only sane behavior anyway: silently locking someone out isn't acceptable.
    db.execute(
        """UPDATE users SET totp_secret_encrypted = NULL, totp_enabled = 0,
           totp_last_used_step = NULL, auth_mode = 'password' WHERE id = ?""",
        (user_id,),
    )
    db.commit()
    return templates.TemplateResponse(
        request, "settings/index.html", _base_context(request, db, user_id, csrf_token, totp_disabled=True)
    )


@router.post("/auth-mode", dependencies=[Depends(verify_csrf_token)])
def set_auth_mode(
    request: Request,
    auth_mode: str = Form(...),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    if auth_mode not in ("password", "totp", "both"):
        return templates.TemplateResponse(
            request, "settings/index.html",
            _base_context(request, db, user_id, csrf_token, auth_mode_error="Invalid login mode."),
            status_code=400,
        )

    row = db.execute("SELECT totp_enabled FROM users WHERE id = ?", (user_id,)).fetchone()
    if auth_mode in ("totp", "both") and not row["totp_enabled"]:
        return templates.TemplateResponse(
            request, "settings/index.html",
            _base_context(
                request, db, user_id, csrf_token,
                auth_mode_error="Set up and confirm an authenticator app before requiring it to log in.",
            ),
            status_code=400,
        )

    db.execute("UPDATE users SET auth_mode = ? WHERE id = ?", (auth_mode, user_id))
    db.commit()
    return templates.TemplateResponse(
        request, "settings/index.html", _base_context(request, db, user_id, csrf_token, auth_mode_saved=True)
    )


@router.post("/users/{target_id}/toggle-active", dependencies=[Depends(verify_csrf_token)])
def toggle_user_active(
    target_id: int,
    request: Request,
    user_id: int = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    if target_id == user_id:
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            _base_context(request, db, user_id, csrf_token, error="You can't deactivate your own account."),
            status_code=400,
        )
    cur = db.execute("UPDATE users SET is_active = NOT is_active WHERE id = ?", (target_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.commit()
    return RedirectResponse(url="/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{target_id}/reset-password", dependencies=[Depends(verify_csrf_token)])
def admin_reset_password(
    target_id: int,
    request: Request,
    new_password: str = Form(...),
    user_id: int = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            _base_context(
                request, db, user_id, csrf_token,
                error=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
            ),
            status_code=400,
        )
    cur = db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (security.hash_password(new_password), target_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # Admin-initiated reset on someone else's account — revoke all their sessions,
    # matching scripts/reset_password.py's existing "locked out, reset everything"
    # behavior. There's no "current session" of the target's to preserve here.
    db.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
    db.commit()
    return RedirectResponse(url="/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{target_id}/reset-totp", dependencies=[Depends(verify_csrf_token)])
def admin_reset_totp(
    target_id: int,
    user_id: int = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
):
    cur = db.execute(
        """UPDATE users SET totp_secret_encrypted = NULL, totp_enabled = 0,
           totp_last_used_step = NULL, auth_mode = 'password' WHERE id = ?""",
        (target_id,),
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # Same "mutate + revoke sessions" pattern as admin_reset_password -- forcibly
    # changing someone's auth method out from under them should kill their sessions.
    db.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
    db.commit()
    return RedirectResponse(url="/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{target_id}/toggle-admin", dependencies=[Depends(verify_csrf_token)])
def toggle_user_admin(
    target_id: int,
    request: Request,
    user_id: int = Depends(require_admin),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    if target_id == user_id:
        return templates.TemplateResponse(
            request,
            "settings/index.html",
            _base_context(
                request, db, user_id, csrf_token,
                error="You can't change your own admin status here — use scripts.set_admin from the CLI.",
            ),
            status_code=400,
        )
    cur = db.execute("UPDATE users SET is_admin = NOT is_admin WHERE id = ?", (target_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.commit()
    return RedirectResponse(url="/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/push/subscribe", dependencies=[Depends(verify_csrf_token)])
async def push_subscribe(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    """Called from JS after pushManager.subscribe() succeeds — body is the browser's
    PushSubscription.toJSON() shape, not a form post."""
    body = await request.json()
    keys = body.get("keys", {})
    endpoint = body.get("endpoint")
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not endpoint or not p256dh or not auth:
        return JSONResponse({"error": "Malformed subscription."}, status_code=400)
    push_subscriptions_repo.create(db, user_id, endpoint, p256dh, auth)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/push/{subscription_id}/unsubscribe", dependencies=[Depends(verify_csrf_token)])
def push_unsubscribe(
    subscription_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    push_subscriptions_repo.delete(db, user_id, subscription_id)
    db.commit()
    return RedirectResponse(url="/settings", status_code=status.HTTP_303_SEE_OTHER)
