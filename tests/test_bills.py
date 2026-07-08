from app.repositories import income_events as income_repo
from app.repositories import obligations as obligations_repo
from app.services import bills


def _transactions(db, user_id):
    return db.execute(
        "SELECT * FROM transactions WHERE user_id = ? ORDER BY id", (user_id,)
    ).fetchall()


def _add_account(db, user_id, balance_cents=100000):
    cur = db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', ?)",
        (user_id, balance_cents),
    )
    db.commit()
    return cur.lastrowid


def test_mark_obligation_paid_non_recurring(db, user_id):
    obligation_id = obligations_repo.create_obligation(
        db, user_id, "One-off", "misc", 5000, "2026-07-15"
    )
    db.commit()
    updated = bills.mark_obligation_paid(db, user_id, obligation_id)
    db.commit()
    assert updated is True
    row = obligations_repo.get_obligation(db, user_id, obligation_id)
    assert row["is_paid"] == 1
    assert row["due_date"] == "2026-07-15"
    assert row["paid_date"] is not None
    txns = _transactions(db, user_id)
    assert len(txns) == 1
    assert txns[0]["target_type"] == "obligation"
    assert txns[0]["obligation_id"] == obligation_id
    assert txns[0]["amount_cents"] == 5000
    assert txns[0]["account_id"] is None  # no account to move money through


def test_mark_obligation_paid_recurring_rolls_forward(db, user_id):
    obligation_id = obligations_repo.create_obligation(
        db, user_id, "Rent", "housing", 90000, "2026-07-15",
        is_recurring=1, recurrence_rule="monthly",
    )
    db.commit()
    updated = bills.mark_obligation_paid(db, user_id, obligation_id)
    db.commit()
    assert updated is True
    row = obligations_repo.get_obligation(db, user_id, obligation_id)
    assert row["is_paid"] == 0
    assert row["due_date"] == "2026-08-15"
    assert row["paid_date"] is not None
    txns = _transactions(db, user_id)
    assert len(txns) == 1
    assert txns[0]["target_type"] == "obligation"
    assert txns[0]["amount_cents"] == 90000


def test_mark_obligation_paid_debits_default_account(db, user_id):
    account_id = _add_account(db, user_id, balance_cents=100000)
    obligation_id = obligations_repo.create_obligation(
        db, user_id, "Rent", "housing", 30000, "2026-07-15"
    )
    db.commit()
    bills.mark_obligation_paid(db, user_id, obligation_id)
    db.commit()
    balance = db.execute("SELECT balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()["balance_cents"]
    assert balance == 70000
    txns = _transactions(db, user_id)
    assert txns[0]["account_id"] == account_id


def test_mark_obligation_paid_nonexistent_returns_false(db, user_id):
    assert bills.mark_obligation_paid(db, user_id, 99999) is False
    assert _transactions(db, user_id) == []


def test_mark_obligation_paid_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    obligation_id = obligations_repo.create_obligation(db, other_id, "Bob's Rent", "housing", 100, "2026-07-15")
    db.commit()
    assert bills.mark_obligation_paid(db, user_id, obligation_id) is False
    assert obligations_repo.get_obligation(db, other_id, obligation_id)["is_paid"] == 0
    assert _transactions(db, user_id) == []


def test_mark_income_received_non_recurring(db, user_id):
    event_id = income_repo.create_income_event(
        db, user_id, "Freelance", 30000, "2026-07-15", "confirmed"
    )
    db.commit()
    updated = bills.mark_income_received(db, user_id, event_id)
    db.commit()
    assert updated is True
    row = income_repo.get_income_event(db, user_id, event_id)
    assert row["is_received"] == 1
    assert row["expected_date"] == "2026-07-15"
    assert row["received_amount_cents"] == 30000
    txns = _transactions(db, user_id)
    assert len(txns) == 1
    assert txns[0]["target_type"] == "other"
    assert txns[0]["amount_cents"] == 30000
    assert txns[0]["memo"] == "Income: Freelance"
    assert txns[0]["account_id"] is None  # no account to move money through


def test_mark_income_received_recurring_rolls_forward(db, user_id):
    event_id = income_repo.create_income_event(
        db, user_id, "Paycheck", 250000, "2026-07-15", "confirmed",
        is_recurring=1, recurrence_rule="biweekly",
    )
    db.commit()
    updated = bills.mark_income_received(db, user_id, event_id)
    db.commit()
    assert updated is True
    row = income_repo.get_income_event(db, user_id, event_id)
    assert row["is_received"] == 0
    assert row["expected_date"] == "2026-07-29"
    assert row["received_date"] is not None
    txns = _transactions(db, user_id)
    assert len(txns) == 1
    assert txns[0]["amount_cents"] == 250000


def test_mark_income_received_credits_default_account(db, user_id):
    account_id = _add_account(db, user_id, balance_cents=100000)
    event_id = income_repo.create_income_event(
        db, user_id, "Freelance", 30000, "2026-07-15", "confirmed"
    )
    db.commit()
    bills.mark_income_received(db, user_id, event_id)
    db.commit()
    balance = db.execute("SELECT balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()["balance_cents"]
    assert balance == 130000
    txns = _transactions(db, user_id)
    assert txns[0]["account_id"] == account_id


def test_mark_income_received_nonexistent_returns_false(db, user_id):
    assert bills.mark_income_received(db, user_id, 99999) is False
    assert _transactions(db, user_id) == []


def test_mark_income_received_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    event_id = income_repo.create_income_event(db, other_id, "Bob's Pay", 100, "2026-07-15", "confirmed")
    db.commit()
    assert bills.mark_income_received(db, user_id, event_id) is False
    assert income_repo.get_income_event(db, other_id, event_id)["is_received"] == 0
    assert _transactions(db, user_id) == []
