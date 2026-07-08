import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.routers.auth as auth_module
from app.deps import get_db
from app.main import app
from app.repositories import push_subscriptions as repo
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
    monkeypatch.setenv("PUSH_VAPID_PUBLIC_KEY", "test-public-key")

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


def _other_user(db):
    cur = db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)", ("bob", "hash")
    )
    db.commit()
    return cur.lastrowid


def test_settings_page_shows_enable_button_when_vapid_configured(client):
    response = client.get("/settings")
    assert "Enable notifications" in response.text


def test_settings_page_hides_push_section_without_vapid_key(client, monkeypatch):
    monkeypatch.delenv("PUSH_VAPID_PUBLIC_KEY", raising=False)
    response = client.get("/settings")
    assert "Enable notifications" not in response.text


def test_subscribe_requires_csrf(client):
    response = client.post(
        "/settings/push/subscribe",
        json={"endpoint": "https://push.example/1", "keys": {"p256dh": "p", "auth": "a"}},
    )
    assert response.status_code == 403


def test_subscribe_persists_subscription(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings/push/subscribe",
        json={"endpoint": "https://push.example/1", "keys": {"p256dh": "p", "auth": "a"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    rows = repo.list_for_user(db, user_id)
    assert len(rows) == 1
    assert rows[0]["endpoint"] == "https://push.example/1"


def test_subscribe_rejects_malformed_body(client):
    csrf = get_csrf_token(client)
    response = client.post(
        "/settings/push/subscribe",
        json={"endpoint": "https://push.example/1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400


def test_unsubscribe_removes_own_subscription(client, db, user_id):
    sub_id = repo.create(db, user_id, "https://push.example/1", "p", "a")
    db.commit()
    csrf = get_csrf_token(client)
    response = client.post(
        f"/settings/push/{sub_id}/unsubscribe",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert repo.list_for_user(db, user_id) == []


def test_unsubscribe_cannot_target_another_users_subscription(client, db, user_id):
    other_id = _other_user(db)
    sub_id = repo.create(db, other_id, "https://push.example/theirs", "p", "a")
    db.commit()
    csrf = get_csrf_token(client)
    client.post(f"/settings/push/{sub_id}/unsubscribe", data={"csrf_token": csrf})
    assert len(repo.list_for_user(db, other_id)) == 1
