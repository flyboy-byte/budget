from datetime import datetime, timedelta, timezone

from app import security


def test_hash_and_verify_password_roundtrip():
    hashed = security.hash_password("correct horse battery staple")
    assert security.verify_password("correct horse battery staple", hashed)
    assert not security.verify_password("wrong password", hashed)


def test_create_and_get_valid_session(db, user_id):
    session_id, csrf_secret = security.create_session(db, user_id, user_agent="pytest")
    db.commit()

    session = security.get_valid_session(db, session_id)
    assert session is not None
    assert session["user_id"] == user_id
    assert session["csrf_secret"] == csrf_secret


def test_expired_session_is_rejected(db, user_id):
    session_id, _ = security.create_session(db, user_id)
    db.commit()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    db.execute("UPDATE sessions SET expires_at = ? WHERE id = ?", (past, session_id))
    db.commit()

    assert security.get_valid_session(db, session_id) is None


def test_delete_session_invalidates_it(db, user_id):
    session_id, _ = security.create_session(db, user_id)
    db.commit()
    security.delete_session(db, session_id)
    db.commit()

    assert security.get_valid_session(db, session_id) is None


def test_touch_session_extends_expiration(db, user_id):
    session_id, _ = security.create_session(db, user_id)
    db.commit()
    original_expiry = db.execute(
        "SELECT expires_at FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()["expires_at"]

    near_expiry = (datetime.now(timezone.utc) + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    db.execute("UPDATE sessions SET expires_at = ? WHERE id = ?", (near_expiry, session_id))
    db.commit()

    security.touch_session(db, session_id)
    db.commit()
    new_expiry = db.execute(
        "SELECT expires_at FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()["expires_at"]

    assert new_expiry > near_expiry
    assert new_expiry != original_expiry


def test_verify_password_returns_false_on_malformed_hash():
    assert not security.verify_password("anything", "not-a-real-argon2-hash")


def test_dummy_password_hash_is_a_real_argon2_hash():
    # used by the login route to equalize timing for nonexistent usernames
    assert security.DUMMY_PASSWORD_HASH.startswith("$argon2")
    assert not security.verify_password("anything", security.DUMMY_PASSWORD_HASH)


def test_verify_csrf_matches_only_correct_token():
    assert security.verify_csrf("secret-value", "secret-value")
    assert not security.verify_csrf("secret-value", "wrong-value")
    assert not security.verify_csrf("secret-value", None)
    assert not security.verify_csrf("secret-value", "")
