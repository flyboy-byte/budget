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
    response = client.get("/forecast")
    return response.text.split('name="csrf_token" value="')[1].split('"')[0]


def test_forecast_page_loads(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    response = client.get("/forecast")
    assert response.status_code == 200
    assert "Forecast" in response.text


def test_whatif_obligation_shows_delta_and_never_persists(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)

    response = client.post(
        "/forecast",
        data={
            "entity_type": "obligation",
            "description": "New phone bill",
            "amount": "50.00",
            "date": "2026-07-15",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert "With this hypothetical item" in response.text
    assert 'href="/obligations/new"' in response.text

    assert db.execute("SELECT COUNT(*) c FROM obligations WHERE user_id = ?", (user_id,)).fetchone()["c"] == 0


def test_whatif_requires_csrf(client):
    response = client.post(
        "/forecast",
        data={"entity_type": "obligation", "amount": "50.00", "date": "2026-07-15"},
    )
    assert response.status_code == 403


def test_whatif_purchase_confirm_link(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)
    response = client.post(
        "/forecast",
        data={"entity_type": "purchase", "amount": "200.00", "status": "ordered", "csrf_token": csrf},
    )
    assert 'href="/committed-purchases/new"' in response.text


def test_whatif_income_does_not_move_safe_to_spend(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)
    response = client.post(
        "/forecast",
        data={
            "entity_type": "income",
            "amount": "9999.00",
            "date": "2026-08-01",
            "confidence": "confirmed",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert "$1,000.00" in response.text  # safe-to-spend unchanged at cash-on-hand
