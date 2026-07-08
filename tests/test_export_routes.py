import io
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.routers.auth as auth_module
from app.deps import get_db
from app.main import app
from app.security import hash_password
from app.services.export import JSON_TABLES


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
    response = client.get("/export")
    return response.text.split('name="csrf_token" value="')[1].split('"')[0]


def test_export_index_lists_csv_links(client):
    response = client.get("/export")
    assert "/export/csv/accounts" in response.text
    assert "/export/json" in response.text


def test_export_csv_downloads_account(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 10000)",
        (user_id,),
    )
    db.commit()
    response = client.get("/export/csv/accounts")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "Checking" in response.text


def test_export_csv_rejects_unknown_table(client):
    response = client.get("/export/csv/users")
    assert response.status_code == 404


def test_export_index_lists_combined_csv_link(client):
    response = client.get("/export")
    assert "/export/csv-combined" in response.text


def test_export_csv_combined_downloads_all_tables(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 10000)",
        (user_id,),
    )
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, minimum_payment_cents, interest_status)
           VALUES (?, 'Visa', 'credit_card', 5000, 100, 'accruing')""",
        (user_id,),
    )
    db.commit()
    response = client.get("/export/csv-combined")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "budget-export-all-tables.csv" in response.headers["content-disposition"]
    assert "Checking" in response.text
    assert "Visa" in response.text


def test_export_json_downloads_full_backup(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 10000)",
        (user_id,),
    )
    db.commit()
    response = client.get("/export/json")
    assert response.status_code == 200
    body = json.loads(response.text)
    assert body["tables"]["accounts"][0]["name"] == "Checking"


def test_restore_requires_confirmation_checkbox(client, db, user_id):
    csrf = get_csrf_token(client)
    backup = {"version": 1, "tables": {t: [] for t in JSON_TABLES}}
    files = {"backup_file": ("backup.json", io.BytesIO(json.dumps(backup).encode()), "application/json")}
    response = client.post("/export/restore", data={"csrf_token": csrf}, files=files)
    assert "must confirm" in response.text


def test_restore_full_roundtrip(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 10000)",
        (user_id,),
    )
    db.commit()

    backup_response = client.get("/export/json")
    backup_bytes = backup_response.content

    db.execute("DELETE FROM accounts WHERE user_id = ?", (user_id,))
    db.commit()
    assert db.execute("SELECT COUNT(*) c FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["c"] == 0

    csrf = get_csrf_token(client)
    files = {"backup_file": ("backup.json", io.BytesIO(backup_bytes), "application/json")}
    response = client.post("/export/restore", data={"csrf_token": csrf, "confirm": "1"}, files=files)
    assert "Restore complete" in response.text

    row = db.execute("SELECT name FROM accounts WHERE user_id = ?", (user_id,)).fetchone()
    assert row["name"] == "Checking"


def test_restore_rejects_malformed_json(client):
    csrf = get_csrf_token(client)
    files = {"backup_file": ("backup.json", io.BytesIO(b"not json"), "application/json")}
    response = client.post("/export/restore", data={"csrf_token": csrf, "confirm": "1"}, files=files)
    assert "Restore failed" in response.text


def test_export_requires_login(db, user_id, monkeypatch):
    monkeypatch.setattr(auth_module, "SECURE_COOKIES", False)
    monkeypatch.setattr(main_module, "SECURE_COOKIES", False)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    anon_client = TestClient(app)
    response = anon_client.get("/export/json")
    assert response.status_code == 401
    app.dependency_overrides.clear()
