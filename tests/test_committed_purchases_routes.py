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
    response = client.get("/committed-purchases/new")
    return response.text.split('name="csrf_token" value="')[1].split('"')[0]


def test_create_purchase(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/committed-purchases",
        data={
            "name": "Drill",
            "category": "career_tool",
            "amount": "200.00",
            "amount_paid": "0.00",
            "status": "ordered",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM committed_purchases WHERE user_id = ?", (user_id,)).fetchone()
    assert row["amount_cents"] == 20000
    assert row["remaining_cents"] == 20000


def test_create_purchase_amount_paid_over_total_rejected(client):
    csrf = get_csrf_token(client)
    response = client.post(
        "/committed-purchases",
        data={
            "name": "Drill",
            "category": "career_tool",
            "amount": "100.00",
            "amount_paid": "150.00",
            "status": "partially_paid",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 400


def test_list_excludes_paid_and_canceled(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/committed-purchases",
        data={
            "name": "Drill",
            "category": "career_tool",
            "amount": "100.00",
            "amount_paid": "100.00",
            "status": "paid",
            "csrf_token": csrf,
        },
    )
    response = client.get("/committed-purchases")
    assert "Nothing outstanding" in response.text


def test_update_purchase_to_partially_paid(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/committed-purchases",
        data={
            "name": "Drill",
            "category": "career_tool",
            "amount": "200.00",
            "amount_paid": "0.00",
            "status": "ordered",
            "csrf_token": csrf,
        },
    )
    purchase_id = db.execute(
        "SELECT id FROM committed_purchases WHERE user_id = ?", (user_id,)
    ).fetchone()["id"]

    response = client.post(
        f"/committed-purchases/{purchase_id}",
        data={
            "name": "Drill",
            "category": "career_tool",
            "amount": "200.00",
            "amount_paid": "50.00",
            "status": "partially_paid",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM committed_purchases WHERE id = ?", (purchase_id,)).fetchone()
    assert row["remaining_cents"] == 15000
    assert row["status"] == "partially_paid"


def test_delete_purchase(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/committed-purchases",
        data={
            "name": "Drill",
            "category": "career_tool",
            "amount": "200.00",
            "amount_paid": "0.00",
            "status": "ordered",
            "csrf_token": csrf,
        },
    )
    purchase_id = db.execute(
        "SELECT id FROM committed_purchases WHERE user_id = ?", (user_id,)
    ).fetchone()["id"]
    response = client.delete(f"/committed-purchases/{purchase_id}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert db.execute("SELECT * FROM committed_purchases WHERE id = ?", (purchase_id,)).fetchone() is None


def test_committed_purchases_list_page_carries_csrf_meta_tag_for_htmx_delete(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/committed-purchases",
        data={
            "name": "Drill", "category": "career_tool", "amount": "200.00",
            "amount_paid": "0.00", "status": "ordered", "csrf_token": csrf,
        },
    )
    purchase_id = db.execute(
        "SELECT id FROM committed_purchases WHERE user_id = ?", (user_id,)
    ).fetchone()["id"]

    list_page = client.get("/committed-purchases")
    assert 'meta name="csrf-token"' in list_page.text
    list_csrf = list_page.text.split('name="csrf-token" content="')[1].split('"')[0]

    response = client.delete(f"/committed-purchases/{purchase_id}", headers={"X-CSRF-Token": list_csrf})
    assert response.status_code == 200
