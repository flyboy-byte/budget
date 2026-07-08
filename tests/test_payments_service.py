import sqlite3

import pytest

from app.repositories import committed_purchases as purchases_repo
from app.services import payments


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


def test_record_purchase_payment_updates_purchase_and_logs_transaction(db, user_id):
    purchase_id = purchases_repo.create_purchase(
        db, user_id, "Drill", "career_tool", 20000, "ordered"
    )
    db.commit()

    result = payments.record_purchase_payment(db, user_id, purchase_id, 5000)
    db.commit()

    assert result is True
    purchase = purchases_repo.get_purchase(db, user_id, purchase_id)
    assert purchase["amount_paid_cents"] == 5000
    assert purchase["status"] == "partially_paid"

    txns = _transactions(db, user_id)
    assert len(txns) == 1
    assert txns[0]["target_type"] == "committed_purchase"
    assert txns[0]["committed_purchase_id"] == purchase_id
    assert txns[0]["amount_cents"] == 5000
    assert txns[0]["memo"] == "Drill"
    assert txns[0]["account_id"] is None  # no account to move money through


def test_record_purchase_payment_debits_default_account(db, user_id):
    account_id = _add_account(db, user_id, balance_cents=100000)
    purchase_id = purchases_repo.create_purchase(
        db, user_id, "Drill", "career_tool", 20000, "ordered"
    )
    db.commit()

    payments.record_purchase_payment(db, user_id, purchase_id, 5000)
    db.commit()

    balance = db.execute("SELECT balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()["balance_cents"]
    assert balance == 95000
    txns = _transactions(db, user_id)
    assert txns[0]["account_id"] == account_id


def test_record_purchase_payment_full_payoff_flips_status_paid(db, user_id):
    purchase_id = purchases_repo.create_purchase(
        db, user_id, "Drill", "career_tool", 20000, "ordered"
    )
    db.commit()

    payments.record_purchase_payment(db, user_id, purchase_id, 20000)
    db.commit()

    purchase = purchases_repo.get_purchase(db, user_id, purchase_id)
    assert purchase["status"] == "paid"


def test_record_purchase_payment_nonexistent_returns_none(db, user_id):
    assert payments.record_purchase_payment(db, user_id, 99999, 100) is None
    assert _transactions(db, user_id) == []


def test_record_purchase_payment_overpay_raises_and_logs_nothing(db, user_id):
    purchase_id = purchases_repo.create_purchase(
        db, user_id, "Drill", "career_tool", 20000, "ordered"
    )
    db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        payments.record_purchase_payment(db, user_id, purchase_id, 99900)
    db.rollback()

    assert _transactions(db, user_id) == []


def test_record_purchase_payment_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    purchase_id = purchases_repo.create_purchase(
        db, other_id, "Bob's Drill", "career_tool", 20000, "ordered"
    )
    db.commit()

    assert payments.record_purchase_payment(db, user_id, purchase_id, 100) is None
    assert purchases_repo.get_purchase(db, other_id, purchase_id)["amount_paid_cents"] == 0
    assert _transactions(db, user_id) == []
