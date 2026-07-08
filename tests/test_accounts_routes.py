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
    response = client.get("/accounts/new")
    return response.text.split('name="csrf_token" value="')[1].split('"')[0]


def test_list_accounts_empty(client):
    response = client.get("/accounts")
    assert response.status_code == 200
    assert "No accounts yet" in response.text


def test_create_account(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/accounts",
        data={"name": "Checking", "type": "checking", "balance": "123.45", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM accounts WHERE user_id = ?", (user_id,)).fetchone()
    assert row["name"] == "Checking"
    assert row["balance_cents"] == 12345


def test_create_account_accepts_arbitrary_free_text_type(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/accounts",
        data={"name": "Vault", "type": "under the mattress", "balance": "50.00", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM accounts WHERE user_id = ?", (user_id,)).fetchone()
    assert row["type"] == "under the mattress"


def test_create_account_without_csrf_rejected(client):
    response = client.post(
        "/accounts", data={"name": "Checking", "type": "checking", "balance": "1.00"}
    )
    assert response.status_code == 403


def test_create_account_with_invalid_amount_rejected(client):
    csrf = get_csrf_token(client)
    response = client.post(
        "/accounts",
        data={"name": "Checking", "type": "checking", "balance": "not-a-number", "csrf_token": csrf},
    )
    assert response.status_code == 400


def test_list_shows_created_account(client):
    csrf = get_csrf_token(client)
    client.post(
        "/accounts",
        data={"name": "Checking", "type": "checking", "balance": "50.00", "csrf_token": csrf},
    )
    response = client.get("/accounts")
    assert "Checking" in response.text
    assert "$50.00" in response.text


def test_update_account(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/accounts",
        data={"name": "Checking", "type": "checking", "balance": "50.00", "csrf_token": csrf},
    )
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]

    response = client.post(
        f"/accounts/{account_id}",
        data={
            "name": "Renamed",
            "type": "savings",
            "balance": "75.00",
            "is_active": "1",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    assert row["name"] == "Renamed"
    assert row["balance_cents"] == 7500


def test_deactivate_account_with_nonzero_balance_rejected(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/accounts",
        data={"name": "Checking", "type": "checking", "balance": "50.00", "csrf_token": csrf},
    )
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]

    response = client.post(
        f"/accounts/{account_id}",
        data={
            "name": "Checking",
            "type": "checking",
            "balance": "50.00",
            # is_active omitted — trying to deactivate
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 400
    assert "Can" in response.text and "deactivate" in response.text
    row = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    assert row["is_active"] == 1  # unchanged


def test_deactivate_account_with_zero_balance_allowed(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/accounts",
        data={"name": "Old Card", "type": "checking", "balance": "0.00", "csrf_token": csrf},
    )
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]

    response = client.post(
        f"/accounts/{account_id}",
        data={"name": "Old Card", "type": "checking", "balance": "0.00", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    assert row["is_active"] == 0


def test_update_nonexistent_account_returns_404(client):
    csrf = get_csrf_token(client)
    response = client.post(
        "/accounts/99999",
        data={"name": "X", "type": "checking", "balance": "1.00", "csrf_token": csrf},
    )
    assert response.status_code == 404


def test_delete_account(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/accounts",
        data={"name": "Checking", "type": "checking", "balance": "50.00", "csrf_token": csrf},
    )
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]

    response = client.delete(f"/accounts/{account_id}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone() is None


def test_accounts_list_page_carries_csrf_meta_tag_for_htmx_delete(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/accounts",
        data={"name": "Checking", "type": "checking", "balance": "50.00", "csrf_token": csrf},
    )
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]

    list_page = client.get("/accounts")
    assert 'meta name="csrf-token"' in list_page.text
    list_csrf = list_page.text.split('name="csrf-token" content="')[1].split('"')[0]

    response = client.delete(f"/accounts/{account_id}", headers={"X-CSRF-Token": list_csrf})
    assert response.status_code == 200


def test_delete_account_without_csrf_rejected(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/accounts",
        data={"name": "Checking", "type": "checking", "balance": "50.00", "csrf_token": csrf},
    )
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]

    response = client.delete(f"/accounts/{account_id}")
    assert response.status_code == 403
    assert db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone() is not None


def test_cannot_access_other_users_account(client, db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Bobs', 'checking', 100)",
        (other_id,),
    )
    db.commit()
    other_account_id = db.execute(
        "SELECT id FROM accounts WHERE user_id = ?", (other_id,)
    ).fetchone()["id"]

    assert client.get(f"/accounts/{other_account_id}/edit").status_code == 404

    csrf = get_csrf_token(client)
    assert (
        client.delete(f"/accounts/{other_account_id}", headers={"X-CSRF-Token": csrf}).status_code
        == 404
    )
