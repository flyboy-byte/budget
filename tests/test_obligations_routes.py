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
    response = client.get("/obligations/new")
    return response.text.split('name="csrf_token" value="')[1].split('"')[0]


def test_create_obligation(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/obligations",
        data={
            "name": "Rent",
            "category": "housing",
            "amount": "900.00",
            "due_date": "2026-08-01",
            "is_required": "1",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM obligations WHERE user_id = ?", (user_id,)).fetchone()
    assert row["amount_cents"] == 90000
    assert row["is_required"] == 1


def test_list_excludes_paid(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/obligations",
        data={"name": "Rent", "category": "housing", "amount": "900.00", "due_date": "2026-08-01", "csrf_token": csrf},
    )
    obligation_id = db.execute("SELECT id FROM obligations WHERE user_id = ?", (user_id,)).fetchone()["id"]
    client.post(
        f"/obligations/{obligation_id}",
        data={
            "name": "Rent",
            "category": "housing",
            "amount": "900.00",
            "due_date": "2026-08-01",
            "is_paid": "1",
            "csrf_token": csrf,
        },
    )
    response = client.get("/obligations")
    assert "No unpaid obligations" in response.text


def test_update_obligation(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/obligations",
        data={"name": "Rent", "category": "housing", "amount": "900.00", "due_date": "2026-08-01", "csrf_token": csrf},
    )
    obligation_id = db.execute("SELECT id FROM obligations WHERE user_id = ?", (user_id,)).fetchone()["id"]

    response = client.post(
        f"/obligations/{obligation_id}",
        data={
            "name": "Rent Increase",
            "category": "housing",
            "amount": "950.00",
            "due_date": "2026-09-01",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM obligations WHERE id = ?", (obligation_id,)).fetchone()
    assert row["amount_cents"] == 95000
    assert row["due_date"] == "2026-09-01"


def test_marking_recurring_obligation_paid_rolls_forward(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/obligations",
        data={
            "name": "Rent",
            "category": "housing",
            "amount": "900.00",
            "due_date": "2026-07-15",
            "is_recurring": "1",
            "recurrence_rule": "monthly",
            "csrf_token": csrf,
        },
    )
    obligation_id = db.execute("SELECT id FROM obligations WHERE user_id = ?", (user_id,)).fetchone()["id"]

    client.post(
        f"/obligations/{obligation_id}",
        data={
            "name": "Rent",
            "category": "housing",
            "amount": "900.00",
            "due_date": "2026-07-15",
            "is_paid": "1",
            "is_recurring": "1",
            "recurrence_rule": "monthly",
            "csrf_token": csrf,
        },
    )

    row = db.execute("SELECT * FROM obligations WHERE id = ?", (obligation_id,)).fetchone()
    assert row["is_paid"] == 0
    assert row["due_date"] == "2026-08-15"
    assert row["paid_date"] is not None

    # still shows up in the active list, since it's now next month's occurrence
    response = client.get("/obligations")
    assert f'id="obligation-{obligation_id}"' in response.text
    assert "15 Aug" in response.text


def test_marking_non_recurring_obligation_paid_does_not_roll_forward(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/obligations",
        data={"name": "One-off", "category": "misc", "amount": "50.00", "due_date": "2026-07-15", "csrf_token": csrf},
    )
    obligation_id = db.execute("SELECT id FROM obligations WHERE user_id = ?", (user_id,)).fetchone()["id"]

    client.post(
        f"/obligations/{obligation_id}",
        data={"name": "One-off", "category": "misc", "amount": "50.00", "due_date": "2026-07-15", "is_paid": "1", "csrf_token": csrf},
    )

    row = db.execute("SELECT * FROM obligations WHERE id = ?", (obligation_id,)).fetchone()
    assert row["is_paid"] == 1
    assert row["due_date"] == "2026-07-15"


def test_delete_obligation(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/obligations",
        data={"name": "Rent", "category": "housing", "amount": "900.00", "due_date": "2026-08-01", "csrf_token": csrf},
    )
    obligation_id = db.execute("SELECT id FROM obligations WHERE user_id = ?", (user_id,)).fetchone()["id"]
    response = client.delete(f"/obligations/{obligation_id}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert db.execute("SELECT * FROM obligations WHERE id = ?", (obligation_id,)).fetchone() is None


def test_obligations_list_page_carries_csrf_meta_tag_for_htmx_delete(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/obligations",
        data={"name": "Rent", "category": "housing", "amount": "900.00", "due_date": "2026-08-01", "csrf_token": csrf},
    )
    obligation_id = db.execute("SELECT id FROM obligations WHERE user_id = ?", (user_id,)).fetchone()["id"]

    list_page = client.get("/obligations")
    assert 'meta name="csrf-token"' in list_page.text
    list_csrf = list_page.text.split('name="csrf-token" content="')[1].split('"')[0]

    response = client.delete(f"/obligations/{obligation_id}", headers={"X-CSRF-Token": list_csrf})
    assert response.status_code == 200
