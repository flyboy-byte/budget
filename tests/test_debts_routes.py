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
    response = client.get("/debts/new")
    return response.text.split('name="csrf_token" value="')[1].split('"')[0]


def test_create_debt_with_apr(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/debts",
        data={
            "name": "Visa",
            "type": "credit_card",
            "balance": "1000.00",
            "minimum_payment": "30.00",
            "apr": "24.99",
            "interest_status": "accruing",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM debts WHERE user_id = ?", (user_id,)).fetchone()
    assert row["balance_cents"] == 100000
    assert row["minimum_payment_cents"] == 3000
    assert row["apr_bps"] == 2499


def test_create_debt_without_apr_leaves_it_null(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "Klarna",
            "type": "klarna_bnpl",
            "balance": "200.00",
            "minimum_payment": "50.00",
            "interest_status": "promo_unknown",
            "csrf_token": csrf,
        },
    )
    row = db.execute("SELECT * FROM debts WHERE user_id = ?", (user_id,)).fetchone()
    assert row["apr_bps"] is None


def test_create_debt_accepts_arbitrary_free_text_type(client, db, user_id):
    # type has no server-side allowlist -- the datalist is suggestions only.
    csrf = get_csrf_token(client)
    response = client.post(
        "/debts",
        data={
            "name": "Family Loan",
            "type": "loan from my sister",
            "balance": "500.00",
            "minimum_payment": "0.00",
            "interest_status": "not_accruing",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM debts WHERE user_id = ?", (user_id,)).fetchone()
    assert row["type"] == "loan from my sister"


def test_create_debt_with_coarse_tracking_checked(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "Daily Card",
            "type": "credit_card",
            "balance": "500.00",
            "minimum_payment": "25.00",
            "interest_status": "accruing",
            "coarse_tracking": "1",
            "csrf_token": csrf,
        },
    )
    row = db.execute("SELECT coarse_tracking FROM debts WHERE user_id = ?", (user_id,)).fetchone()
    assert row["coarse_tracking"] == 1


def test_create_debt_without_coarse_tracking_defaults_off(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "Fixed Loan",
            "type": "personal",
            "balance": "500.00",
            "minimum_payment": "25.00",
            "interest_status": "accruing",
            "csrf_token": csrf,
        },
    )
    row = db.execute("SELECT coarse_tracking FROM debts WHERE user_id = ?", (user_id,)).fetchone()
    assert row["coarse_tracking"] == 0


def test_new_debt_form_shows_type_dropdown(client):
    response = client.get("/debts/new")
    assert response.status_code == 200
    assert '<select id="type" name="type">' in response.text
    assert "auto_loan" in response.text


def test_list_shows_debt_and_apr(client):
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "Visa",
            "type": "credit_card",
            "balance": "1000.00",
            "minimum_payment": "30.00",
            "apr": "24.99",
            "interest_status": "accruing",
            "csrf_token": csrf,
        },
    )
    response = client.get("/debts")
    assert "Visa" in response.text
    assert "24.99" in response.text


def test_list_flags_debt_missing_minimum_or_due_date_as_incomplete(client):
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "No Minimum Set",
            "type": "personal",
            "balance": "500.00",
            "minimum_payment": "0.00",
            "interest_status": "accruing",
            "csrf_token": csrf,
        },
    )
    response = client.get("/debts")
    assert "No Minimum Set" in response.text
    assert "Incomplete" in response.text


def test_list_does_not_flag_a_fully_specified_debt(client):
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "Fully Specified",
            "type": "personal",
            "balance": "500.00",
            "minimum_payment": "25.00",
            "next_due_date": "2026-10-01",
            "interest_status": "accruing",
            "csrf_token": csrf,
        },
    )
    response = client.get("/debts")
    assert "Fully Specified" in response.text
    assert "Incomplete" not in response.text


def test_update_debt(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "Visa",
            "type": "credit_card",
            "balance": "1000.00",
            "minimum_payment": "30.00",
            "interest_status": "accruing",
            "csrf_token": csrf,
        },
    )
    debt_id = db.execute("SELECT id FROM debts WHERE user_id = ?", (user_id,)).fetchone()["id"]

    response = client.post(
        f"/debts/{debt_id}",
        data={
            "name": "Visa Paid Down",
            "type": "credit_card",
            "balance": "500.00",
            "minimum_payment": "15.00",
            "interest_status": "not_accruing",
            "is_active": "1",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
    assert row["balance_cents"] == 50000
    assert row["interest_status"] == "not_accruing"


def test_update_debt_can_turn_on_coarse_tracking(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "Visa",
            "type": "credit_card",
            "balance": "1000.00",
            "minimum_payment": "30.00",
            "interest_status": "accruing",
            "csrf_token": csrf,
        },
    )
    debt_id = db.execute("SELECT id FROM debts WHERE user_id = ?", (user_id,)).fetchone()["id"]

    client.post(
        f"/debts/{debt_id}",
        data={
            "name": "Visa",
            "type": "credit_card",
            "balance": "1000.00",
            "minimum_payment": "30.00",
            "interest_status": "accruing",
            "is_active": "1",
            "coarse_tracking": "1",
            "csrf_token": csrf,
        },
    )
    row = db.execute("SELECT coarse_tracking FROM debts WHERE id = ?", (debt_id,)).fetchone()
    assert row["coarse_tracking"] == 1


def test_deactivate_debt_with_nonzero_balance_rejected(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "Visa",
            "type": "credit_card",
            "balance": "500.00",
            "minimum_payment": "15.00",
            "interest_status": "accruing",
            "csrf_token": csrf,
        },
    )
    debt_id = db.execute("SELECT id FROM debts WHERE user_id = ?", (user_id,)).fetchone()["id"]

    response = client.post(
        f"/debts/{debt_id}",
        data={
            "name": "Visa",
            "type": "credit_card",
            "balance": "500.00",
            "minimum_payment": "15.00",
            "interest_status": "accruing",
            # is_active omitted — trying to deactivate
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 400
    assert "deactivate" in response.text
    row = db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
    assert row["is_active"] == 1  # unchanged


def test_deactivate_debt_with_zero_balance_allowed(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "Paid Off Card",
            "type": "credit_card",
            "balance": "0.00",
            "minimum_payment": "0.00",
            "interest_status": "not_accruing",
            "csrf_token": csrf,
        },
    )
    debt_id = db.execute("SELECT id FROM debts WHERE user_id = ?", (user_id,)).fetchone()["id"]

    response = client.post(
        f"/debts/{debt_id}",
        data={
            "name": "Paid Off Card",
            "type": "credit_card",
            "balance": "0.00",
            "minimum_payment": "0.00",
            "interest_status": "not_accruing",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
    assert row["is_active"] == 0


def test_delete_debt(client, db, user_id):
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "Visa",
            "type": "credit_card",
            "balance": "1000.00",
            "minimum_payment": "30.00",
            "interest_status": "accruing",
            "csrf_token": csrf,
        },
    )
    debt_id = db.execute("SELECT id FROM debts WHERE user_id = ?", (user_id,)).fetchone()["id"]
    response = client.delete(f"/debts/{debt_id}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone() is None


def test_debts_list_page_carries_csrf_meta_tag_for_htmx_delete(client, db, user_id):
    """The real delete button on /debts is an hx-delete with no form field -- it only
    works because base.html's htmx:configRequest listener reads a <meta name="csrf-
    token"> tag and attaches it as X-CSRF-Token. That tag only renders if the /debts
    list route itself passes csrf_token into the template (a real 2026-08-26 bug: it
    didn't, so every delete button on this page silently 403'd in production while
    every other route's Depends(get_csrf_token) masked the gap in tests)."""
    csrf = get_csrf_token(client)
    client.post(
        "/debts",
        data={
            "name": "Visa", "type": "credit_card", "balance": "1000.00",
            "minimum_payment": "30.00", "interest_status": "accruing", "csrf_token": csrf,
        },
    )
    debt_id = db.execute("SELECT id FROM debts WHERE user_id = ?", (user_id,)).fetchone()["id"]

    list_page = client.get("/debts")
    assert 'meta name="csrf-token"' in list_page.text
    list_csrf = list_page.text.split('name="csrf-token" content="')[1].split('"')[0]

    response = client.delete(f"/debts/{debt_id}", headers={"X-CSRF-Token": list_csrf})
    assert response.status_code == 200


def test_cannot_edit_other_users_debt(client, db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, minimum_payment_cents, interest_status)
           VALUES (?, 'Bobs Card', 'credit_card', 100, 10, 'accruing')""",
        (other_id,),
    )
    db.commit()
    other_debt_id = db.execute("SELECT id FROM debts WHERE user_id = ?", (other_id,)).fetchone()["id"]
    assert client.get(f"/debts/{other_debt_id}/edit").status_code == 404
