import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.routers.auth as auth_module
from app.deps import get_db
from app.main import app
from app.repositories import accounts as accounts_repo
from app.repositories import obligations as obligations_repo
from app.repositories import transactions as transactions_repo
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


def test_ledger_requires_login():
    assert TestClient(app).get("/ledger").status_code == 401


def test_empty_ledger_shows_empty_state(client):
    response = client.get("/ledger")
    assert response.status_code == 200
    assert "Nothing recorded yet" in response.text


def test_ledger_shows_spending_with_category_and_outflow_sign(client, db, user_id):
    transactions_repo.create_transaction(
        db, user_id, "2026-08-17", 1274, "spending", category="gas", memo="ALLSUP FARWELL"
    )
    db.commit()

    response = client.get("/ledger")
    assert "ALLSUP FARWELL" in response.text
    assert "gas" in response.text
    assert "−$12.74" in response.text


def test_ledger_shows_income_as_an_inflow(client, db, user_id):
    transactions_repo.create_transaction(
        db, user_id, "2026-08-17", 250000, "other", memo="Income: Paycheck"
    )
    db.commit()

    response = client.get("/ledger")
    assert "+$2,500.00" in response.text


def test_ledger_resolves_the_obligation_name(client, db, user_id):
    obligation_id = obligations_repo.create_obligation(
        db, user_id, "Rent", "housing", 90000, "2026-09-01"
    )
    transactions_repo.create_transaction(
        db, user_id, "2026-08-01", 90000, "obligation", obligation_id=obligation_id
    )
    db.commit()

    response = client.get("/ledger")
    assert "Rent" in response.text
    assert "−$900.00" in response.text


def test_ledger_shows_the_account_a_movement_touched(client, db, user_id):
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 50000)
    transactions_repo.create_transaction(
        db, user_id, "2026-08-17", 500, "spending", account_id=account_id, memo="Coffee"
    )
    db.commit()

    assert "Checking" in client.get("/ledger").text


def test_ledger_is_scoped_to_the_owner(client, db, user_id):
    other_id = db.execute(
        "INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')"
    ).lastrowid
    transactions_repo.create_transaction(
        db, other_id, "2026-08-17", 9999, "spending", memo="BOBS SECRET PURCHASE"
    )
    db.commit()

    assert "BOBS SECRET PURCHASE" not in client.get("/ledger").text


def test_ledger_is_newest_first(client, db, user_id):
    transactions_repo.create_transaction(db, user_id, "2026-08-01", 100, "spending", memo="OLDER")
    transactions_repo.create_transaction(db, user_id, "2026-08-20", 100, "spending", memo="NEWER")
    db.commit()

    text = client.get("/ledger").text
    assert text.index("NEWER") < text.index("OLDER")


def test_activity_hub_shows_recent_entries_inline(client, db, user_id):
    transactions_repo.create_transaction(
        db, user_id, "2026-08-17", 1274, "spending", memo="ALLSUP FARWELL"
    )
    db.commit()

    response = client.get("/activity")
    assert response.status_code == 200
    assert "Recent activity" in response.text
    assert "ALLSUP FARWELL" in response.text


def test_activity_hub_empty_state_when_nothing_recorded(client):
    response = client.get("/activity")
    assert "Nothing recorded yet" in response.text
