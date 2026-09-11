from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.routers.auth as auth_module
from app.deps import get_db
from app.main import app
from app.security import hash_password
from app.services.dates import parse_db_timestamp


@pytest.fixture
def client(db, user_id, monkeypatch):
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password("correct-password"), user_id),
    )
    db.commit()
    monkeypatch.setattr(auth_module, "SECURE_COOKIES", False)
    monkeypatch.setattr(main_module, "SECURE_COOKIES", False)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    test_client.post("/login", data={"username": "alice", "password": "correct-password"})
    yield test_client
    app.dependency_overrides.clear()


def test_parses_sqlite_strftime_output():
    assert parse_db_timestamp("2026-09-11T14:38:12.345Z") == datetime(
        2026, 9, 11, 14, 38, 12, 345000, tzinfo=timezone.utc
    )


def test_parses_near_miss_formats():
    assert parse_db_timestamp("2026-09-11T14:38:12Z") is not None
    assert parse_db_timestamp("2026-09-11 14:38:12") is not None
    assert parse_db_timestamp("2026-09-11T14:38:12+00:00") is not None


def test_always_returns_utc_aware():
    for value in ("2026-09-11T14:38:12.345Z", "2026-09-11 14:38:12"):
        assert parse_db_timestamp(value).tzinfo is not None


def test_returns_none_for_unreadable_values():
    for value in ("not-a-date", "", None, "2026-13-45T99:99:99Z"):
        assert parse_db_timestamp(value) is None


# ----- an unreadable timestamp must degrade to "stale", never 500 a screen -----

def test_dashboard_survives_unreadable_updated_at(db, user_id, client):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 50000)",
        (user_id,),
    )
    db.execute("UPDATE accounts SET updated_at = 'not-a-timestamp' WHERE user_id = ?", (user_id,))
    db.commit()

    response = client.get("/")
    assert response.status_code == 200


def test_money_hub_survives_unreadable_updated_at(db, user_id, client):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 50000)",
        (user_id,),
    )
    db.execute("UPDATE accounts SET updated_at = 'not-a-timestamp' WHERE user_id = ?", (user_id,))
    db.commit()

    response = client.get("/money")
    assert response.status_code == 200


def test_unreadable_timestamp_is_not_given_a_made_up_age(db, user_id, client):
    # Degrading to "stale" is right; inventing a precise day count for a timestamp
    # we couldn't read is not — the page must say it doesn't know.
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 50000)",
        (user_id,),
    )
    db.execute("UPDATE accounts SET updated_at = 'not-a-timestamp' WHERE user_id = ?", (user_id,))
    db.commit()

    body = client.get("/").text
    assert "3650 day" not in body
    assert "last update unknown" in body or "no readable last-updated date" in body
