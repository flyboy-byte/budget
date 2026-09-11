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
    response = client.get("/")
    return response.text.split('name="csrf_token" value="')[1].split('"')[0]


def test_quick_update_balance(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 10000)",
        (user_id,),
    )
    db.commit()
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(
        "/today/balance",
        data={"target": f"account:{account_id}", "amount": "150.00", "csrf_token": csrf},
    )
    assert response.status_code == 200
    assert "Balance updated" in response.text
    assert "$150.00" in response.text  # OOB summary reflects the new safe-to-spend
    assert db.execute("SELECT balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()["balance_cents"] == 15000


def test_quick_update_balance_response_includes_oob_summary(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 10000)",
        (user_id,),
    )
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, interest_status, minimum_payment_cents)
           VALUES (?, 'Visa', 'credit_card', 50000, 'accruing', 2000)""",
        (user_id,),
    )
    db.commit()
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(
        "/today/balance",
        data={"target": f"account:{account_id}", "amount": "50.00", "csrf_token": csrf},
    )
    assert 'id="dashboard-summary" hx-swap-oob="true"' in response.text
    # PLAN.md §1.4's watch item: the two-column split (.dashboard-main /
    # .dashboard-rail) must not move the rail outside the OOB-swapped element --
    # if it did, debt priority would silently stop updating after a quick action.
    assert 'class="dashboard-main"' in response.text
    assert 'class="dashboard-rail"' in response.text
    assert "Visa" in response.text


def test_quick_update_balance_requires_csrf(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 10000)",
        (user_id,),
    )
    db.commit()
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]
    response = client.post("/today/balance", data={"target": f"account:{account_id}", "amount": "50.00"})
    assert response.status_code == 403


def test_quick_update_balance_scoped_to_owner(client, db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Bobs', 'checking', 999)",
        (other_id,),
    )
    db.commit()
    other_account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (other_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(
        "/today/balance",
        data={"target": f"account:{other_account_id}", "amount": "1.00", "csrf_token": csrf},
    )
    assert response.status_code == 404
    assert db.execute("SELECT balance_cents FROM accounts WHERE id = ?", (other_account_id,)).fetchone()["balance_cents"] == 999


def test_quick_update_balance_covers_debts_too(client, db, user_id):
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, interest_status, minimum_payment_cents)
           VALUES (?, 'Visa', 'credit_card', 50000, 'accruing', 2000)""",
        (user_id,),
    )
    db.commit()
    debt_id = db.execute("SELECT id FROM debts WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(
        "/today/balance",
        data={"target": f"debt:{debt_id}", "amount": "300.00", "csrf_token": csrf},
    )
    assert response.status_code == 200
    assert "Balance updated" in response.text
    assert db.execute("SELECT balance_cents FROM debts WHERE id = ?", (debt_id,)).fetchone()["balance_cents"] == 30000


def test_quick_update_balance_adjust_mode_subtracts(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 10000)",
        (user_id,),
    )
    db.commit()
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(
        "/today/balance",
        data={
            "target": f"account:{account_id}", "amount": "40.00", "mode": "adjust", "direction": "-",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert db.execute("SELECT balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()["balance_cents"] == 6000


def test_quick_update_balance_adjust_mode_adds(client, db, user_id):
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, interest_status, minimum_payment_cents)
           VALUES (?, 'Visa', 'credit_card', 5000, 'accruing', 2000)""",
        (user_id,),
    )
    db.commit()
    debt_id = db.execute("SELECT id FROM debts WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(
        "/today/balance",
        data={
            "target": f"debt:{debt_id}", "amount": "25.00", "mode": "adjust", "direction": "+",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert db.execute("SELECT balance_cents FROM debts WHERE id = ?", (debt_id,)).fetchone()["balance_cents"] == 7500


def test_quick_update_balance_adjust_below_zero_debt_rejected(client, db, user_id):
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, interest_status, minimum_payment_cents)
           VALUES (?, 'Visa', 'credit_card', 5000, 'accruing', 2000)""",
        (user_id,),
    )
    db.commit()
    debt_id = db.execute("SELECT id FROM debts WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(
        "/today/balance",
        data={
            "target": f"debt:{debt_id}", "amount": "100.00", "mode": "adjust", "direction": "-",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200
    assert "below zero" in response.text
    assert db.execute("SELECT balance_cents FROM debts WHERE id = ?", (debt_id,)).fetchone()["balance_cents"] == 5000


def test_quick_update_balance_rejects_unknown_target_type(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/today/balance",
        data={"target": "obligation:1", "amount": "1.00", "csrf_token": csrf},
    )
    assert response.status_code == 400


def test_quick_record_payment(client, db, user_id):
    db.execute(
        """INSERT INTO committed_purchases (user_id, name, category, amount_cents, status)
           VALUES (?, 'Drill', 'career_tool', 20000, 'ordered')""",
        (user_id,),
    )
    db.commit()
    purchase_id = db.execute("SELECT id FROM committed_purchases WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(
        "/today/payment",
        data={"purchase_id": purchase_id, "amount": "50.00", "csrf_token": csrf},
    )
    assert response.status_code == 200
    assert "Payment recorded" in response.text
    row = db.execute("SELECT amount_paid_cents, status FROM committed_purchases WHERE id = ?", (purchase_id,)).fetchone()
    assert row["amount_paid_cents"] == 5000
    assert row["status"] == "partially_paid"
    txn = db.execute(
        "SELECT * FROM transactions WHERE user_id = ? AND committed_purchase_id = ?", (user_id, purchase_id)
    ).fetchone()
    assert txn is not None
    assert txn["amount_cents"] == 5000
    assert txn["target_type"] == "committed_purchase"


def test_quick_record_payment_debits_default_account(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.execute(
        """INSERT INTO committed_purchases (user_id, name, category, amount_cents, status)
           VALUES (?, 'Drill', 'career_tool', 20000, 'ordered')""",
        (user_id,),
    )
    db.commit()
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]
    purchase_id = db.execute("SELECT id FROM committed_purchases WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(
        "/today/payment",
        data={"purchase_id": purchase_id, "amount": "50.00", "csrf_token": csrf},
    )
    assert response.status_code == 200
    balance = db.execute("SELECT balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()["balance_cents"]
    assert balance == 95000


def test_quick_record_payment_exceeding_remaining_shows_friendly_error(client, db, user_id):
    db.execute(
        """INSERT INTO committed_purchases (user_id, name, category, amount_cents, status)
           VALUES (?, 'Drill', 'career_tool', 20000, 'ordered')""",
        (user_id,),
    )
    db.commit()
    purchase_id = db.execute("SELECT id FROM committed_purchases WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(
        "/today/payment",
        data={"purchase_id": purchase_id, "amount": "999.00", "csrf_token": csrf},
    )
    assert response.status_code == 200  # friendly fragment, not a raw 400
    assert "more than what" in response.text
    row = db.execute("SELECT amount_paid_cents FROM committed_purchases WHERE id = ?", (purchase_id,)).fetchone()
    assert row["amount_paid_cents"] == 0  # rolled back, nothing applied


def test_quick_add_purchase(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/today/purchase",
        data={"name": "New Drill", "category": "career_tool", "amount": "75.00", "csrf_token": csrf},
    )
    assert response.status_code == 200
    assert "Purchase added" in response.text
    row = db.execute("SELECT * FROM committed_purchases WHERE user_id = ?", (user_id,)).fetchone()
    assert row["name"] == "New Drill"
    assert row["amount_cents"] == 7500
    assert row["status"] == "ordered"


def test_quick_mark_bill_paid(client, db, user_id):
    db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Rent', 'housing', 90000, '2026-07-15')",
        (user_id,),
    )
    db.commit()
    obligation_id = db.execute("SELECT id FROM obligations WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(f"/today/bill-paid/{obligation_id}", data={"csrf_token": csrf})
    assert response.status_code == 200
    row = db.execute("SELECT is_paid FROM obligations WHERE id = ?", (obligation_id,)).fetchone()
    assert row["is_paid"] == 1


def test_quick_mark_bill_paid_recurring_rolls_forward(client, db, user_id):
    db.execute(
        """INSERT INTO obligations (user_id, name, category, amount_cents, due_date, is_recurring, recurrence_rule)
           VALUES (?, 'Rent', 'housing', 90000, '2026-07-15', 1, 'monthly')""",
        (user_id,),
    )
    db.commit()
    obligation_id = db.execute("SELECT id FROM obligations WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(f"/today/bill-paid/{obligation_id}", data={"csrf_token": csrf})
    assert response.status_code == 200
    row = db.execute("SELECT is_paid, due_date FROM obligations WHERE id = ?", (obligation_id,)).fetchone()
    assert row["is_paid"] == 0
    assert row["due_date"] == "2026-08-15"


def test_quick_mark_bill_paid_nonexistent_returns_404(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post("/today/bill-paid/99999", data={"csrf_token": csrf})
    assert response.status_code == 404


def test_quick_mark_income_received(client, db, user_id):
    db.execute(
        "INSERT INTO income_events (user_id, source, expected_amount_cents, expected_date, confidence) VALUES (?, 'Freelance', 30000, '2026-07-15', 'confirmed')",
        (user_id,),
    )
    db.commit()
    event_id = db.execute("SELECT id FROM income_events WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(f"/today/income-received/{event_id}", data={"csrf_token": csrf})
    assert response.status_code == 200
    row = db.execute("SELECT is_received FROM income_events WHERE id = ?", (event_id,)).fetchone()
    assert row["is_received"] == 1
    txn = db.execute("SELECT * FROM transactions WHERE user_id = ?", (user_id,)).fetchone()
    assert txn is not None
    assert txn["amount_cents"] == 30000
    assert txn["target_type"] == "other"


def test_quick_mark_income_received_credits_default_account(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.execute(
        "INSERT INTO income_events (user_id, source, expected_amount_cents, expected_date, confidence) VALUES (?, 'Freelance', 30000, '2026-07-15', 'confirmed')",
        (user_id,),
    )
    db.commit()
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]
    event_id = db.execute("SELECT id FROM income_events WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(f"/today/income-received/{event_id}", data={"csrf_token": csrf})
    assert response.status_code == 200
    balance = db.execute("SELECT balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()["balance_cents"]
    assert balance == 130000


def test_quick_mark_income_received_recurring_rolls_forward(client, db, user_id):
    db.execute(
        """INSERT INTO income_events (user_id, source, expected_amount_cents, expected_date, confidence, is_recurring, recurrence_rule)
           VALUES (?, 'Paycheck', 250000, '2026-07-15', 'confirmed', 1, 'biweekly')""",
        (user_id,),
    )
    db.commit()
    event_id = db.execute("SELECT id FROM income_events WHERE user_id = ?", (user_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(f"/today/income-received/{event_id}", data={"csrf_token": csrf})
    assert response.status_code == 200
    row = db.execute("SELECT is_received, expected_date FROM income_events WHERE id = ?", (event_id,)).fetchone()
    assert row["is_received"] == 0
    assert row["expected_date"] == "2026-07-29"


def test_quick_mark_income_received_nonexistent_returns_404(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post("/today/income-received/99999", data={"csrf_token": csrf})
    assert response.status_code == 404


def test_quick_mark_income_received_scoped_to_owner(client, db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    db.execute(
        "INSERT INTO income_events (user_id, source, expected_amount_cents, expected_date, confidence) VALUES (?, 'Bobs Pay', 999, '2026-07-15', 'confirmed')",
        (other_id,),
    )
    db.commit()
    other_event_id = db.execute("SELECT id FROM income_events WHERE user_id = ?", (other_id,)).fetchone()["id"]
    csrf = get_csrf_token(client)

    response = client.post(f"/today/income-received/{other_event_id}", data={"csrf_token": csrf})
    assert response.status_code == 404
    assert db.execute("SELECT is_received FROM income_events WHERE id = ?", (other_event_id,)).fetchone()["is_received"] == 0


def test_quick_mark_income_received_requires_csrf(client, db, user_id):
    db.execute(
        "INSERT INTO income_events (user_id, source, expected_amount_cents, expected_date, confidence) VALUES (?, 'Freelance', 30000, '2026-07-15', 'confirmed')",
        (user_id,),
    )
    db.commit()
    event_id = db.execute("SELECT id FROM income_events WHERE user_id = ?", (user_id,)).fetchone()["id"]
    response = client.post(f"/today/income-received/{event_id}", data={})
    assert response.status_code == 403


def test_today_page_shows_income_received_quick_action(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.execute(
        "INSERT INTO income_events (user_id, source, expected_amount_cents, expected_date, confidence) VALUES (?, 'Freelance', 30000, '2026-07-15', 'confirmed')",
        (user_id,),
    )
    db.commit()
    response = client.get("/")
    assert response.status_code == 200
    assert 'hx-post="/today/income-received/' in response.text
    assert "Mark income received" in response.text


def test_today_page_shows_quick_action_forms(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    response = client.get("/")
    assert response.status_code == 200
    assert 'hx-post="/today/balance"' in response.text
    assert 'hx-post="/today/payment"' in response.text
    assert 'hx-post="/today/purchase"' in response.text
    assert "Update today" in response.text


def test_afford_check_yes_when_affordable(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)

    response = client.post("/today/afford", data={"csrf_token": csrf, "amount": "30.00"})
    assert response.status_code == 200
    assert "alert--success" in response.text
    assert "Yes" in response.text
    assert "$970.00" in response.text


def test_afford_check_no_when_unaffordable(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 10000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)

    response = client.post("/today/afford", data={"csrf_token": csrf, "amount": "150.00"})
    assert response.status_code == 200
    assert "alert--error" in response.text
    assert "No" in response.text
    assert "$50.00" in response.text


def test_afford_check_does_not_mutate_anything(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)

    client.post("/today/afford", data={"csrf_token": csrf, "amount": "30.00"})

    assert db.execute("SELECT COUNT(*) c FROM committed_purchases WHERE user_id = ?", (user_id,)).fetchone()["c"] == 0
    account = db.execute("SELECT balance_cents FROM accounts WHERE user_id = ?", (user_id,)).fetchone()
    assert account["balance_cents"] == 100000


def test_afford_check_rejects_negative_amount(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)

    response = client.post("/today/afford", data={"csrf_token": csrf, "amount": "-50.00"})
    assert response.status_code == 200
    assert "alert--error" in response.text
    assert "positive amount" in response.text


def test_afford_check_requires_csrf(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    response = client.post("/today/afford", data={"amount": "30.00"})
    assert response.status_code == 403


def test_afford_check_scoped_to_requesting_user(client, db, user_id):
    other_id = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'x')").lastrowid
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Bob Checking', 'checking', 100000000)",
        (other_id,),
    )
    db.commit()
    csrf = get_csrf_token(client)

    # requesting user (alice) has no accounts at all — should be unaffordable
    # regardless of how much cash bob's account has.
    response = client.post("/today/afford", data={"csrf_token": csrf, "amount": "30.00"})
    assert "alert--error" in response.text


# ----- malformed target (2026-09-11) -----
# target is a user-supplied form field. A non-numeric or oversized id reached
# int() unguarded and raised, turning a bad request into a 500.

@pytest.mark.parametrize(
    "target",
    ["account:abc", "debt:1.5", "account:9999999999999999999999", "account:-", "debt:"],
)
def test_quick_update_balance_rejects_malformed_target(client, db, user_id, target):
    csrf = get_csrf_token(client)
    response = client.post(
        "/today/balance",
        data={"target": target, "amount": "10.00", "csrf_token": csrf},
    )
    assert response.status_code == 400
