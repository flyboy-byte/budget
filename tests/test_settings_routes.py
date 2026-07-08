import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import app.main as main_module
import app.routers.auth as auth_module
from app.deps import get_db
from app.main import app
from app.security import hash_password
from app.services import calc


@pytest.fixture
def client(db, user_id, monkeypatch):
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password("correct-password"), user_id),
    )
    db.commit()
    monkeypatch.setattr(auth_module, "SECURE_COOKIES", False)
    monkeypatch.setattr(main_module, "SECURE_COOKIES", False)
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    test_client.post("/login", data={"username": "alice", "password": "correct-password"})
    yield test_client
    app.dependency_overrides.clear()


def get_csrf_token(client):
    response = client.get("/settings")
    return response.text.split('name="csrf_token" value="')[1].split('"')[0]


@pytest.fixture
def admin_client(client, db, user_id):
    db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user_id,))
    db.commit()
    return client


def test_settings_page_shows_defaults_when_none_saved(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert 'value="0.00"' in response.text
    assert "end_of_month" in response.text


def test_saving_settings_persists_and_affects_calc(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings",
        data={
            "protected_savings_floor": "500.00",
            "reserved_window_mode": "fixed_days",
            "reserved_window_fixed_days": "10",
            "forecast_window_days": "45",
            "forecast_income_confidence": "confirmed_likely",
            "timezone": "America/Denver",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert "Saved" in response.text

    assert calc.get_setting(db, user_id, "protected_savings_floor_cents") == "50000"
    assert calc.get_setting(db, user_id, "reserved_window_mode") == "fixed_days"
    assert calc.get_setting(db, user_id, "reserved_window_fixed_days") == "10"
    assert calc.get_setting(db, user_id, "timezone") == "America/Denver"


def test_bank_sync_use_available_balance_defaults_off_and_can_be_enabled(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/settings",
        data={
            "protected_savings_floor": "0.00",
            "reserved_window_mode": "end_of_month",
            "reserved_window_fixed_days": "14",
            "forecast_window_days": "30",
            "forecast_income_confidence": "confirmed",
            "timezone": "UTC",
            "csrf_token": csrf,
        },
    )
    assert calc.get_setting(db, user_id, "bank_sync_use_available_balance") == "0"

    csrf = get_csrf_token(client)
    response = client.post(
        "/settings",
        data={
            "protected_savings_floor": "0.00",
            "reserved_window_mode": "end_of_month",
            "reserved_window_fixed_days": "14",
            "forecast_window_days": "30",
            "forecast_income_confidence": "confirmed",
            "timezone": "UTC",
            "bank_sync_use_available_balance": "1",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert calc.get_setting(db, user_id, "bank_sync_use_available_balance") == "1"


def test_bank_sync_auto_apply_defaults_off_and_can_be_enabled_with_custom_threshold(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings",
        data={
            "protected_savings_floor": "0.00",
            "reserved_window_mode": "end_of_month",
            "reserved_window_fixed_days": "14",
            "forecast_window_days": "30",
            "forecast_income_confidence": "confirmed",
            "timezone": "UTC",
            "bank_sync_auto_apply": "1",
            "bank_sync_auto_apply_max_change": "100.00",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert calc.get_setting(db, user_id, "bank_sync_auto_apply") == "1"
    assert calc.get_setting(db, user_id, "bank_sync_auto_apply_max_change_cents") == "10000"


def test_bank_sync_auto_apply_rejects_a_negative_threshold(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings",
        data={
            "protected_savings_floor": "0.00",
            "reserved_window_mode": "end_of_month",
            "reserved_window_fixed_days": "14",
            "forecast_window_days": "30",
            "forecast_income_confidence": "confirmed",
            "timezone": "UTC",
            "bank_sync_auto_apply_max_change": "-5.00",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 400


def test_saving_digest_email_persists(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings/notifications",
        data={
            "digest_email": "alice@example.com",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert calc.get_setting(db, user_id, "digest_email") == "alice@example.com"


def test_saving_low_safe_to_spend_threshold_persists(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings/notifications",
        data={
            "low_safe_to_spend_threshold": "50.00",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert calc.get_setting(db, user_id, "low_safe_to_spend_threshold_cents") == "5000"


def test_blank_low_safe_to_spend_threshold_allowed_as_opt_out(client, db, user_id):
    from app.repositories import settings as settings_repo

    settings_repo.upsert(db, user_id, "low_safe_to_spend_threshold_cents", "5000")
    db.commit()

    csrf = get_csrf_token(client)
    response = client.post(
        "/settings/notifications",
        data={
            "low_safe_to_spend_threshold": "",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert calc.get_setting(db, user_id, "low_safe_to_spend_threshold_cents") == ""


def test_invalid_digest_email_rejected(client):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings/notifications",
        data={
            "digest_email": "not-an-email",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 400
    assert "Digest email" in response.text


def test_blank_digest_email_allowed_as_opt_out(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings/notifications",
        data={
            "digest_email": "",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert calc.get_setting(db, user_id, "digest_email") == ""


def test_invalid_reserved_window_mode_rejected(client):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings",
        data={
            "protected_savings_floor": "0.00",
            "reserved_window_mode": "not_a_real_mode",
            "reserved_window_fixed_days": "10",
            "forecast_window_days": "30",
            "forecast_income_confidence": "confirmed",
            "timezone": "UTC",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 400
    assert "Invalid reserved-cash window mode" in response.text


def test_invalid_timezone_rejected(client):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings",
        data={
            "protected_savings_floor": "0.00",
            "reserved_window_mode": "end_of_month",
            "reserved_window_fixed_days": "10",
            "forecast_window_days": "30",
            "forecast_income_confidence": "confirmed",
            "timezone": "Not/A_Real_Zone",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 400
    assert "Unrecognized timezone" in response.text


def test_negative_floor_rejected(client):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings",
        data={
            "protected_savings_floor": "-100.00",
            "reserved_window_mode": "end_of_month",
            "reserved_window_fixed_days": "10",
            "forecast_window_days": "30",
            "forecast_income_confidence": "confirmed",
            "timezone": "UTC",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 400
    assert "can" in response.text and "be negative" in response.text


def test_settings_requires_csrf(client):
    response = client.post(
        "/settings",
        data={
            "reserved_window_mode": "end_of_month",
            "forecast_income_confidence": "confirmed",
        },
    )
    assert response.status_code == 403


def test_settings_notifications_requires_csrf(client):
    response = client.post(
        "/settings/notifications",
        data={"digest_email": "alice@example.com"},
    )
    assert response.status_code == 403


def test_settings_affects_dashboard_safe_to_spend(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)
    client.post(
        "/settings",
        data={
            "protected_savings_floor": "200.00",
            "reserved_window_mode": "end_of_month",
            "reserved_window_fixed_days": "14",
            "forecast_window_days": "30",
            "forecast_income_confidence": "confirmed",
            "timezone": "UTC",
            "csrf_token": csrf,
        },
    )
    response = client.get("/")
    assert "$800.00" in response.text  # 1000 - 200 floor


def test_settings_shows_current_session_as_this_device(client):
    response = client.get("/settings")
    assert "this device" in response.text
    assert "Active sessions" in response.text


def test_settings_shows_second_device_session(client, db):
    other_client = TestClient(app)
    other_client.post("/login", data={"username": "alice", "password": "correct-password"})

    response = client.get("/settings")
    # two distinct sessions now exist for alice; only one is "this device" from client's view
    assert response.text.count("this device") == 1
    assert db.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 2


def test_revoke_other_sessions_logs_out_other_device(client, db):
    other_client = TestClient(app)
    other_client.post("/login", data={"username": "alice", "password": "correct-password"})
    assert db.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 2

    csrf = get_csrf_token(client)
    response = client.post("/settings/sessions/revoke-others", data={"csrf_token": csrf}, follow_redirects=False)
    assert response.status_code == 303
    assert db.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 1

    other_response = other_client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert other_response.status_code == 303  # redirected to login, session revoked


def test_revoke_single_session_by_id(client, db, user_id):
    other_client = TestClient(app)
    other_client.post("/login", data={"username": "alice", "password": "correct-password"})
    all_sessions = db.execute("SELECT id FROM sessions WHERE user_id = ?", (user_id,)).fetchall()
    assert len(all_sessions) == 2
    to_revoke = [s["id"] for s in all_sessions if s["id"] != client.cookies["session_id"]][0]

    csrf = get_csrf_token(client)
    response = client.post(f"/settings/sessions/{to_revoke}/revoke", data={"csrf_token": csrf}, follow_redirects=False)
    assert response.status_code == 303
    assert db.execute("SELECT COUNT(*) c FROM sessions WHERE user_id = ?", (user_id,)).fetchone()["c"] == 1
    # the surviving session is the one that made the request
    remaining = db.execute("SELECT id FROM sessions WHERE user_id = ?", (user_id,)).fetchone()
    assert remaining["id"] == client.cookies["session_id"]


def test_revoke_session_requires_csrf(client, db, user_id):
    session_id = db.execute("SELECT id FROM sessions WHERE user_id = ?", (user_id,)).fetchone()["id"]
    response = client.post(f"/settings/sessions/{session_id}/revoke")
    assert response.status_code == 403


def test_add_user_requires_admin(client, db):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings/users",
        data={"new_username": "bob", "new_password": "hunter22", "csrf_token": csrf},
    )
    assert response.status_code == 403
    assert db.execute("SELECT id FROM users WHERE username = 'bob'").fetchone() is None


def test_add_user_creates_isolated_account(admin_client, db):
    csrf = get_csrf_token(admin_client)
    response = admin_client.post(
        "/settings/users",
        data={"new_username": "bob", "new_password": "hunter22", "csrf_token": csrf},
    )
    assert response.status_code == 200
    assert 'Created user "bob"' in response.text
    row = db.execute("SELECT id, password_hash FROM users WHERE username = 'bob'").fetchone()
    assert row is not None
    assert row["password_hash"] != "hunter22"  # stored hashed, not plaintext


def test_add_user_requires_csrf(admin_client):
    response = admin_client.post("/settings/users", data={"new_username": "bob", "new_password": "hunter22"})
    assert response.status_code == 403


def test_add_user_rejects_short_password(admin_client, db):
    csrf = get_csrf_token(admin_client)
    response = admin_client.post(
        "/settings/users",
        data={"new_username": "bob", "new_password": "short", "csrf_token": csrf},
    )
    assert response.status_code == 400
    assert "at least 8 characters" in response.text
    assert db.execute("SELECT id FROM users WHERE username = 'bob'").fetchone() is None


def test_add_user_rejects_unsafe_username_chars(admin_client, db):
    csrf = get_csrf_token(admin_client)
    response = admin_client.post(
        "/settings/users",
        data={"new_username": 'evil"\r\nX-Injected: 1', "new_password": "hunter22", "csrf_token": csrf},
    )
    assert response.status_code == 400
    assert "letters, numbers, underscores, and hyphens" in response.text
    assert db.execute("SELECT id FROM users WHERE username LIKE 'evil%'").fetchone() is None


def test_add_user_rate_limited_after_repeated_attempts(admin_client, db):
    csrf = get_csrf_token(admin_client)
    for i in range(10):
        admin_client.post(
            "/settings/users",
            data={"new_username": f"user{i}", "new_password": "hunter22", "csrf_token": csrf},
        )
    response = admin_client.post(
        "/settings/users",
        data={"new_username": "oneMore", "new_password": "hunter22", "csrf_token": csrf},
    )
    assert response.status_code == 429
    assert "Too many accounts" in response.text
    assert db.execute("SELECT id FROM users WHERE username = 'oneMore'").fetchone() is None


def test_add_user_rejects_blank_username(admin_client, db):
    csrf = get_csrf_token(admin_client)
    response = admin_client.post(
        "/settings/users",
        data={"new_username": "   ", "new_password": "hunter22", "csrf_token": csrf},
    )
    assert response.status_code == 400
    assert "can" in response.text and "t be blank" in response.text


def test_add_user_rejects_duplicate_username(admin_client, db, user_id):
    csrf = get_csrf_token(admin_client)
    existing_username = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()["username"]
    response = admin_client.post(
        "/settings/users",
        data={"new_username": existing_username, "new_password": "hunter22", "csrf_token": csrf},
    )
    assert response.status_code == 400
    assert "already taken" in response.text


def test_new_user_can_log_in_and_has_isolated_data(admin_client, db):
    csrf = get_csrf_token(admin_client)
    admin_client.post(
        "/settings/users",
        data={"new_username": "bob", "new_password": "hunter22", "csrf_token": csrf},
    )
    bob_client = TestClient(app)
    login_response = bob_client.post(
        "/login", data={"username": "bob", "password": "hunter22"}, follow_redirects=False
    )
    assert login_response.status_code == 303
    dashboard = bob_client.get("/")
    assert dashboard.status_code == 200
    assert "$0.00" in dashboard.text  # no accounts yet — separate from alice's data


def test_cannot_revoke_another_users_session(client, db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    from app import security

    bobs_session_id, _ = security.create_session(db, other_id)
    db.commit()

    csrf = get_csrf_token(client)
    client.post(f"/settings/sessions/{bobs_session_id}/revoke", data={"csrf_token": csrf})
    assert security.get_valid_session(db, bobs_session_id) is not None


# ----- self-service password change -----

def test_change_password_happy_path(client, db, user_id):
    from app import security

    other_session_id, _ = security.create_session(db, user_id)
    db.commit()

    csrf = get_csrf_token(client)
    resp = client.post(
        "/settings/password",
        data={
            "csrf_token": csrf,
            "current_password": "correct-password",
            "new_password": "new-password-123",
            "confirm_password": "new-password-123",
        },
    )
    assert resp.status_code == 200
    assert "Password changed" in resp.text

    row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    assert security.verify_password("new-password-123", row["password_hash"])
    # other sessions revoked, current session (implicitly, since this request succeeded) untouched
    assert security.get_valid_session(db, other_session_id) is None


def test_change_password_wrong_current_password_rejected(client, db, user_id):
    from app import security

    csrf = get_csrf_token(client)
    resp = client.post(
        "/settings/password",
        data={
            "csrf_token": csrf,
            "current_password": "totally-wrong",
            "new_password": "new-password-123",
            "confirm_password": "new-password-123",
        },
    )
    assert resp.status_code == 400
    assert "incorrect" in resp.text
    row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    assert security.verify_password("correct-password", row["password_hash"])


def test_change_password_mismatch_rejected(client, db):
    csrf = get_csrf_token(client)
    resp = client.post(
        "/settings/password",
        data={
            "csrf_token": csrf,
            "current_password": "correct-password",
            "new_password": "new-password-123",
            "confirm_password": "does-not-match",
        },
    )
    assert resp.status_code == 400
    assert "match" in resp.text


def test_change_password_requires_csrf(client):
    resp = client.post(
        "/settings/password",
        data={
            "current_password": "correct-password",
            "new_password": "new-password-123",
            "confirm_password": "new-password-123",
        },
    )
    assert resp.status_code == 403


# ----- admin user management -----

def test_toggle_active_requires_admin(client, db, user_id):
    other_id = other_user_row(db)
    csrf = get_csrf_token(client)
    resp = client.post(f"/settings/users/{other_id}/toggle-active", data={"csrf_token": csrf})
    assert resp.status_code == 403


def test_admin_can_deactivate_and_reactivate_another_user(admin_client, db):
    other_id = other_user_row(db)
    csrf = get_csrf_token(admin_client)

    resp = admin_client.post(f"/settings/users/{other_id}/toggle-active", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert db.execute("SELECT is_active FROM users WHERE id = ?", (other_id,)).fetchone()["is_active"] == 0

    admin_client.post(f"/settings/users/{other_id}/toggle-active", data={"csrf_token": csrf}, follow_redirects=False)
    assert db.execute("SELECT is_active FROM users WHERE id = ?", (other_id,)).fetchone()["is_active"] == 1


def test_admin_cannot_deactivate_self(admin_client, db, user_id):
    csrf = get_csrf_token(admin_client)
    resp = admin_client.post(f"/settings/users/{user_id}/toggle-active", data={"csrf_token": csrf})
    assert resp.status_code == 400
    assert db.execute("SELECT is_active FROM users WHERE id = ?", (user_id,)).fetchone()["is_active"] == 1


def test_admin_can_reset_another_users_password(admin_client, db):
    from app import security

    other_id = other_user_row(db)
    other_session_id, _ = security.create_session(db, other_id)
    db.commit()

    csrf = get_csrf_token(admin_client)
    resp = admin_client.post(
        f"/settings/users/{other_id}/reset-password",
        data={"csrf_token": csrf, "new_password": "brand-new-password"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    row = db.execute("SELECT password_hash FROM users WHERE id = ?", (other_id,)).fetchone()
    assert security.verify_password("brand-new-password", row["password_hash"])
    # admin-initiated reset revokes ALL of the target's sessions
    assert security.get_valid_session(db, other_session_id) is None


def test_admin_can_promote_and_demote_another_user(admin_client, db):
    other_id = other_user_row(db)
    csrf = get_csrf_token(admin_client)

    resp = admin_client.post(f"/settings/users/{other_id}/toggle-admin", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert db.execute("SELECT is_admin FROM users WHERE id = ?", (other_id,)).fetchone()["is_admin"] == 1

    admin_client.post(f"/settings/users/{other_id}/toggle-admin", data={"csrf_token": csrf}, follow_redirects=False)
    assert db.execute("SELECT is_admin FROM users WHERE id = ?", (other_id,)).fetchone()["is_admin"] == 0


def test_admin_cannot_demote_self(admin_client, db, user_id):
    csrf = get_csrf_token(admin_client)
    resp = admin_client.post(f"/settings/users/{user_id}/toggle-admin", data={"csrf_token": csrf})
    assert resp.status_code == 400
    assert db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()["is_admin"] == 1


def test_manage_users_card_hidden_for_non_admin(client):
    resp = client.get("/settings")
    assert "Manage users" not in resp.text


def test_manage_users_card_visible_for_admin(admin_client):
    resp = admin_client.get("/settings")
    assert "Manage users" in resp.text


def other_user_row(db):
    cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    return cur.lastrowid


# ----- TOTP setup/confirm/disable/auth-mode -----

def _decrypted_pending_secret(client) -> str:
    """Pull the manual-entry key straight back out of the rendered settings page."""
    resp = client.get("/settings")
    return resp.text.split("Enter this key manually: <code>")[1].split("</code>")[0]


def test_totp_setup_generates_pending_secret_not_yet_enabled(client, db, user_id):
    csrf = get_csrf_token(client)
    resp = client.post("/settings/totp/setup", data={"csrf_token": csrf})
    assert resp.status_code == 200
    assert "Scan this with your authenticator app" in resp.text
    row = db.execute("SELECT totp_enabled, totp_secret_encrypted FROM users WHERE id = ?", (user_id,)).fetchone()
    assert row["totp_enabled"] == 0
    assert row["totp_secret_encrypted"] is not None


def test_totp_setup_persists_pending_state_across_page_loads(client):
    csrf = get_csrf_token(client)
    client.post("/settings/totp/setup", data={"csrf_token": csrf})
    secret_1 = _decrypted_pending_secret(client)
    secret_2 = _decrypted_pending_secret(client)
    assert secret_1 == secret_2


def test_totp_setup_again_generates_a_new_secret(client):
    csrf = get_csrf_token(client)
    client.post("/settings/totp/setup", data={"csrf_token": csrf})
    secret_1 = _decrypted_pending_secret(client)
    client.post("/settings/totp/setup", data={"csrf_token": csrf})
    secret_2 = _decrypted_pending_secret(client)
    assert secret_1 != secret_2


def test_totp_confirm_with_correct_code_enables(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post("/settings/totp/setup", data={"csrf_token": csrf})
    secret = _decrypted_pending_secret(client)
    code = pyotp.TOTP(secret).now()

    resp = client.post("/settings/totp/confirm", data={"csrf_token": csrf, "code": code})
    assert resp.status_code == 200
    assert "Authenticator app enabled" in resp.text
    assert db.execute("SELECT totp_enabled FROM users WHERE id = ?", (user_id,)).fetchone()["totp_enabled"] == 1


def test_totp_confirm_with_wrong_code_does_not_enable(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post("/settings/totp/setup", data={"csrf_token": csrf})

    resp = client.post("/settings/totp/confirm", data={"csrf_token": csrf, "code": "000000"})
    assert resp.status_code == 400
    assert "didn" in resp.text and "match" in resp.text
    assert db.execute("SELECT totp_enabled FROM users WHERE id = ?", (user_id,)).fetchone()["totp_enabled"] == 0


def test_totp_confirm_without_pending_setup_rejected(client):
    csrf = get_csrf_token(client)
    resp = client.post("/settings/totp/confirm", data={"csrf_token": csrf, "code": "123456"})
    assert resp.status_code == 400
    assert "Start setup again" in resp.text


def test_totp_confirm_rate_limited_after_repeated_wrong_codes(client):
    csrf = get_csrf_token(client)
    client.post("/settings/totp/setup", data={"csrf_token": csrf})
    for _ in range(10):
        client.post("/settings/totp/confirm", data={"csrf_token": csrf, "code": "000000"})
    resp = client.post("/settings/totp/confirm", data={"csrf_token": csrf, "code": "000000"})
    assert resp.status_code == 429
    assert "Too many attempts" in resp.text


def _enable_totp(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post("/settings/totp/setup", data={"csrf_token": csrf})
    secret = _decrypted_pending_secret(client)
    code = pyotp.TOTP(secret).now()
    client.post("/settings/totp/confirm", data={"csrf_token": csrf, "code": code})
    return secret


def test_totp_disable_clears_state_and_resets_auth_mode(client, db, user_id):
    _enable_totp(client, db, user_id)
    csrf = get_csrf_token(client)
    client.post("/settings/auth-mode", data={"csrf_token": csrf, "auth_mode": "totp"})

    resp = client.post("/settings/totp/disable", data={"csrf_token": csrf})
    assert resp.status_code == 200
    assert "disabled" in resp.text.lower()

    row = db.execute(
        "SELECT totp_enabled, totp_secret_encrypted, auth_mode FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    assert row["totp_enabled"] == 0
    assert row["totp_secret_encrypted"] is None
    assert row["auth_mode"] == "password"


def test_auth_mode_rejected_when_totp_not_enabled(client, db, user_id):
    csrf = get_csrf_token(client)
    resp = client.post("/settings/auth-mode", data={"csrf_token": csrf, "auth_mode": "totp"})
    assert resp.status_code == 400
    assert "Set up and confirm" in resp.text
    assert db.execute("SELECT auth_mode FROM users WHERE id = ?", (user_id,)).fetchone()["auth_mode"] == "password"


def test_auth_mode_both_accepted_once_totp_enabled(client, db, user_id):
    _enable_totp(client, db, user_id)
    csrf = get_csrf_token(client)
    resp = client.post("/settings/auth-mode", data={"csrf_token": csrf, "auth_mode": "both"})
    assert resp.status_code == 200
    assert db.execute("SELECT auth_mode FROM users WHERE id = ?", (user_id,)).fetchone()["auth_mode"] == "both"


def test_auth_mode_rejects_invalid_value(client):
    csrf = get_csrf_token(client)
    resp = client.post("/settings/auth-mode", data={"csrf_token": csrf, "auth_mode": "carrier-pigeon"})
    assert resp.status_code == 400


def test_admin_reset_totp_clears_target_and_revokes_sessions(admin_client, db, user_id):
    from app import crypto

    other_id = other_user_row(db)
    db.execute(
        "UPDATE users SET totp_secret_encrypted = ?, totp_enabled = 1, auth_mode = 'totp' WHERE id = ?",
        (crypto.encrypt_totp_secret("JBSWY3DPEHPK3PXP"), other_id),
    )
    db.execute(
        "INSERT INTO sessions (id, user_id, csrf_secret, expires_at) VALUES ('sess1', ?, 'x', '2099-01-01T00:00:00.000000Z')",
        (other_id,),
    )
    db.commit()

    csrf = get_csrf_token(admin_client)
    resp = admin_client.post(f"/settings/users/{other_id}/reset-totp", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303

    row = db.execute(
        "SELECT totp_enabled, totp_secret_encrypted, auth_mode FROM users WHERE id = ?", (other_id,)
    ).fetchone()
    assert row["totp_enabled"] == 0
    assert row["totp_secret_encrypted"] is None
    assert row["auth_mode"] == "password"
    assert db.execute("SELECT COUNT(*) c FROM sessions WHERE user_id = ?", (other_id,)).fetchone()["c"] == 0


def test_reset_totp_requires_admin(client, db, user_id):
    other_id = other_user_row(db)
    csrf = get_csrf_token(client)
    resp = client.post(f"/settings/users/{other_id}/reset-totp", data={"csrf_token": csrf})
    assert resp.status_code == 403
