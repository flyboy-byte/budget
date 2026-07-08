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
    response = client.get("/snapshots")
    return response.text.split('name="csrf_token" value="')[1].split('"')[0]


def test_capture_snapshot(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)

    response = client.post("/snapshots", data={"csrf_token": csrf}, follow_redirects=False)
    assert response.status_code == 303
    row = db.execute("SELECT * FROM snapshots WHERE user_id = ?", (user_id,)).fetchone()
    assert row["safe_to_spend_cents"] == 100000


def test_capturing_twice_same_day_overwrites(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)
    client.post("/snapshots", data={"csrf_token": csrf})

    db.execute("UPDATE accounts SET balance_cents = 50000 WHERE user_id = ?", (user_id,))
    db.commit()
    client.post("/snapshots", data={"csrf_token": csrf})

    rows = db.execute("SELECT * FROM snapshots WHERE user_id = ?", (user_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["safe_to_spend_cents"] == 50000


def test_list_shows_snapshot(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)
    client.post("/snapshots", data={"csrf_token": csrf})
    response = client.get("/snapshots")
    assert "$1,000.00" in response.text


def test_delete_snapshot(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)
    client.post("/snapshots", data={"csrf_token": csrf})
    snapshot_id = db.execute("SELECT id FROM snapshots WHERE user_id = ?", (user_id,)).fetchone()["id"]

    response = client.delete(f"/snapshots/{snapshot_id}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert db.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone() is None


def test_capture_requires_csrf(client):
    response = client.post("/snapshots")
    assert response.status_code == 403
