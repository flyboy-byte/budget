import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.routers.auth as auth_module
from app.deps import get_db
from app.main import app
from app.security import hash_password


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


def get_csrf_token(client):
    response = client.get("/income-events/new")
    return response.text.split('name="csrf_token" value="')[1].split('"')[0]


def test_create_income_event(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/income-events",
        data={
            "source": "Paycheck",
            "amount": "5000.00",
            "expected_date": "2026-08-01",
            "confidence": "confirmed",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM income_events WHERE user_id = ?", (user_id,)).fetchone()
    assert row["expected_amount_cents"] == 500000


def test_list_excludes_received(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/income-events",
        data={"source": "Paycheck", "amount": "5000.00", "expected_date": "2026-08-01", "confidence": "confirmed", "csrf_token": csrf},
    )
    event_id = db.execute("SELECT id FROM income_events WHERE user_id = ?", (user_id,)).fetchone()["id"]
    client.post(
        f"/income-events/{event_id}",
        data={
            "source": "Paycheck",
            "amount": "5000.00",
            "expected_date": "2026-08-01",
            "confidence": "confirmed",
            "is_received": "1",
            "csrf_token": csrf,
        },
    )
    response = client.get("/income-events")
    assert "No upcoming income logged" in response.text


def test_update_income_event(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/income-events",
        data={"source": "Paycheck", "amount": "5000.00", "expected_date": "2026-08-01", "confidence": "confirmed", "csrf_token": csrf},
    )
    event_id = db.execute("SELECT id FROM income_events WHERE user_id = ?", (user_id,)).fetchone()["id"]

    response = client.post(
        f"/income-events/{event_id}",
        data={
            "source": "Paycheck Adjusted",
            "amount": "5500.00",
            "expected_date": "2026-08-15",
            "confidence": "likely",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM income_events WHERE id = ?", (event_id,)).fetchone()
    assert row["expected_amount_cents"] == 550000
    assert row["confidence"] == "likely"


def test_marking_recurring_income_received_rolls_forward(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/income-events",
        data={
            "source": "Paycheck",
            "amount": "2500.00",
            "expected_date": "2026-07-15",
            "confidence": "confirmed",
            "is_recurring": "1",
            "recurrence_rule": "biweekly",
            "csrf_token": csrf,
        },
    )
    event_id = db.execute("SELECT id FROM income_events WHERE user_id = ?", (user_id,)).fetchone()["id"]

    client.post(
        f"/income-events/{event_id}",
        data={
            "source": "Paycheck",
            "amount": "2500.00",
            "expected_date": "2026-07-15",
            "confidence": "confirmed",
            "is_received": "1",
            "is_recurring": "1",
            "recurrence_rule": "biweekly",
            "csrf_token": csrf,
        },
    )

    row = db.execute("SELECT * FROM income_events WHERE id = ?", (event_id,)).fetchone()
    assert row["is_received"] == 0
    assert row["expected_date"] == "2026-07-29"
    assert row["received_date"] is not None


def test_delete_income_event(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/income-events",
        data={"source": "Paycheck", "amount": "5000.00", "expected_date": "2026-08-01", "confidence": "confirmed", "csrf_token": csrf},
    )
    event_id = db.execute("SELECT id FROM income_events WHERE user_id = ?", (user_id,)).fetchone()["id"]
    response = client.delete(f"/income-events/{event_id}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert db.execute("SELECT * FROM income_events WHERE id = ?", (event_id,)).fetchone() is None


def test_income_events_list_page_carries_csrf_meta_tag_for_htmx_delete(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/income-events",
        data={"source": "Paycheck", "amount": "5000.00", "expected_date": "2026-08-01", "confidence": "confirmed", "csrf_token": csrf},
    )
    event_id = db.execute("SELECT id FROM income_events WHERE user_id = ?", (user_id,)).fetchone()["id"]

    list_page = client.get("/income-events")
    assert 'meta name="csrf-token"' in list_page.text
    list_csrf = list_page.text.split('name="csrf-token" content="')[1].split('"')[0]

    response = client.delete(f"/income-events/{event_id}", headers={"X-CSRF-Token": list_csrf})
    assert response.status_code == 200
