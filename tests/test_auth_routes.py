import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import app.main as main_module
import app.routers.auth as auth_module
from app import crypto
from app.deps import get_db
from app.main import app
from app.security import hash_password

TOTP_SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture
def client(db, user_id, monkeypatch):
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password("correct-password"), user_id),
    )
    db.commit()

    # TestClient talks plain HTTP; Secure cookies would never be echoed back on
    # subsequent requests, breaking session persistence across the test's requests.
    monkeypatch.setattr(auth_module, "SECURE_COOKIES", False)
    monkeypatch.setattr(main_module, "SECURE_COOKIES", False)
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _enroll_totp(db, user_id, auth_mode, secret=TOTP_SECRET):
    db.execute(
        "UPDATE users SET totp_secret_encrypted = ?, totp_enabled = 1, auth_mode = ? WHERE id = ?",
        (crypto.encrypt_totp_secret(secret), auth_mode, user_id),
    )
    db.commit()


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "Log in" in response.text


def test_login_with_correct_password_sets_session_cookie(client):
    response = client.post(
        "/login", data={"username": "alice", "password": "correct-password"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "session_id" in response.cookies


def test_login_with_wrong_password_rejected(client):
    response = client.post("/login", data={"username": "alice", "password": "wrong"})
    assert response.status_code == 401
    assert "session_id" not in response.cookies


def test_login_with_unknown_username_rejected(client):
    response = client.post("/login", data={"username": "nobody", "password": "x"})
    assert response.status_code == 401


def test_root_requires_authentication_redirects_browsers_to_login(client):
    response = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_root_requires_authentication_returns_401_for_api_clients(client):
    response = client.get("/", headers={"accept": "application/json"})
    assert response.status_code == 401


def test_root_accessible_after_login(client, user_id):
    client.post("/login", data={"username": "alice", "password": "correct-password"})
    response = client.get("/")
    assert response.status_code == 200
    assert "Dashboard" in response.text


def test_logout_requires_csrf_token(client):
    client.post("/login", data={"username": "alice", "password": "correct-password"})
    response = client.post("/logout")
    assert response.status_code == 403


def test_logout_with_valid_csrf_clears_session(client, db):
    client.post("/login", data={"username": "alice", "password": "correct-password"})
    csrf_secret = db.execute("SELECT csrf_secret FROM sessions").fetchone()["csrf_secret"]

    response = client.post(
        "/logout", headers={"X-CSRF-Token": csrf_secret}, follow_redirects=False
    )
    assert response.status_code == 303
    assert db.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 0

    response = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 303


def test_no_registration_route_exists(client):
    for path in ("/register", "/signup", "/users/new"):
        response = client.get(path)
        assert response.status_code == 404


def test_request_access_requires_name_and_email(client):
    response = client.post("/login/request-access", data={"name": "", "email": ""})
    assert response.status_code == 400
    assert "required" in response.text


def test_request_access_sends_notification_and_shows_success(client, monkeypatch):
    captured = {}

    def fake_send(name, email, message):
        captured["args"] = (name, email, message)

    monkeypatch.setattr(auth_module.request_access_service, "send_request_access_notification", fake_send)

    response = client.post(
        "/login/request-access",
        data={"name": "Alice", "email": "alice@example.com", "message": "please add me"},
    )
    assert response.status_code == 200
    assert "Request sent" in response.text
    assert captured["args"] == ("Alice", "alice@example.com", "please add me")


def test_request_access_shows_error_when_send_fails(client, monkeypatch):
    def fake_send(name, email, message):
        raise auth_module.request_access_service.RequestAccessError("boom")

    monkeypatch.setattr(auth_module.request_access_service, "send_request_access_notification", fake_send)

    response = client.post(
        "/login/request-access", data={"name": "Alice", "email": "alice@example.com"}
    )
    assert response.status_code == 502
    assert "Couldn" in response.text


def test_request_access_rate_limited_after_repeated_attempts(client, monkeypatch):
    from app.routers.auth import REQUEST_ACCESS_MAX_ATTEMPTS

    monkeypatch.setattr(auth_module.request_access_service, "send_request_access_notification", lambda *a: None)

    for _ in range(REQUEST_ACCESS_MAX_ATTEMPTS):
        client.post("/login/request-access", data={"name": "Alice", "email": "alice@example.com"})

    response = client.post("/login/request-access", data={"name": "Alice", "email": "alice@example.com"})
    assert response.status_code == 429
    assert "Too many requests" in response.text


def test_login_locks_out_after_repeated_failures(client):
    from app.routers.auth import LOGIN_MAX_ATTEMPTS

    for _ in range(LOGIN_MAX_ATTEMPTS):
        response = client.post("/login", data={"username": "alice", "password": "wrong"})
        assert response.status_code == 401

    response = client.post("/login", data={"username": "alice", "password": "wrong"})
    assert response.status_code == 429
    assert "Too many login attempts" in response.text

    # even the correct password is blocked while rate-limited
    response = client.post("/login", data={"username": "alice", "password": "correct-password"})
    assert response.status_code == 429


def test_successful_login_resets_rate_limit_counter(client):
    for _ in range(3):
        client.post("/login", data={"username": "alice", "password": "wrong"})

    response = client.post(
        "/login", data={"username": "alice", "password": "correct-password"}, follow_redirects=False
    )
    assert response.status_code == 303

    from app import ratelimit

    assert not ratelimit.is_limited("login:testclient", max_attempts=10, window_seconds=300)


# ----- auth_mode matrix -----

def test_password_only_mode_ignores_totp_code_field(client, db, user_id):
    # Default mode, unchanged behavior -- submitting garbage in totp_code must not
    # break a normal password login.
    response = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password", "totp_code": "000000"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_totp_only_mode_rejects_correct_password_without_code(client, db, user_id):
    _enroll_totp(db, user_id, "totp")
    response = client.post("/login", data={"username": "alice", "password": "correct-password"})
    assert response.status_code == 401
    assert "session_id" not in response.cookies


def test_totp_only_mode_accepts_correct_code_with_blank_password(client, db, user_id):
    _enroll_totp(db, user_id, "totp")
    code = pyotp.TOTP(TOTP_SECRET).now()
    response = client.post(
        "/login", data={"username": "alice", "password": "", "totp_code": code}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "session_id" in response.cookies


def test_totp_only_mode_rejects_wrong_code(client, db, user_id):
    _enroll_totp(db, user_id, "totp")
    response = client.post("/login", data={"username": "alice", "password": "", "totp_code": "000000"})
    assert response.status_code == 401


def test_totp_code_cannot_be_replayed(client, db, user_id):
    _enroll_totp(db, user_id, "totp")
    code = pyotp.TOTP(TOTP_SECRET).now()
    first = client.post(
        "/login", data={"username": "alice", "password": "", "totp_code": code}, follow_redirects=False
    )
    assert first.status_code == 303
    second = client.post("/login", data={"username": "alice", "password": "", "totp_code": code})
    assert second.status_code == 401


def test_both_mode_requires_password_and_code(client, db, user_id):
    _enroll_totp(db, user_id, "both")
    code = pyotp.TOTP(TOTP_SECRET).now()

    # password alone: rejected
    resp = client.post("/login", data={"username": "alice", "password": "correct-password"})
    assert resp.status_code == 401

    # code alone: rejected
    resp = client.post("/login", data={"username": "alice", "password": "", "totp_code": code})
    assert resp.status_code == 401

    # both: succeeds
    resp = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password", "totp_code": code},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_failed_totp_login_does_not_reveal_which_factor_failed(client, db, user_id):
    _enroll_totp(db, user_id, "both")
    resp = client.post(
        "/login", data={"username": "alice", "password": "wrong", "totp_code": "000000"}
    )
    assert resp.status_code == 401
    assert "Invalid username or password" in resp.text


def test_wrong_password_with_valid_totp_code_does_not_burn_the_code(client, db, user_id):
    # get_connection() commits on any normal exit, including a 401 response -- a
    # naive "always persist totp_last_used_step once verified" would burn a
    # correct code on a failed *password* attempt in "both" mode, forcing the
    # real user to wait for a fresh code. The fix only persists once every other
    # factor has already passed.
    _enroll_totp(db, user_id, "both")
    code = pyotp.TOTP(TOTP_SECRET).now()

    wrong_password_attempt = client.post(
        "/login", data={"username": "alice", "password": "wrong", "totp_code": code}
    )
    assert wrong_password_attempt.status_code == 401

    retry = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password", "totp_code": code},
        follow_redirects=False,
    )
    assert retry.status_code == 303


def test_totp_replay_protection_uses_conditional_write(client, db, user_id):
    # Directly exercises the rowcount-checked UPDATE: pre-set totp_last_used_step
    # to the step this code belongs to (simulating another request having already
    # won the race) and confirm the login is rejected rather than racing past it.
    _enroll_totp(db, user_id, "totp")
    import time

    from app.services import totp as totp_service

    code = pyotp.TOTP(TOTP_SECRET).now()
    current_step = int(time.time() // totp_service.STEP_SECONDS)
    db.execute("UPDATE users SET totp_last_used_step = ? WHERE id = ?", (current_step, user_id))
    db.commit()

    resp = client.post("/login", data={"username": "alice", "password": "", "totp_code": code})
    assert resp.status_code == 401
