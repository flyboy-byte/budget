from app.repositories import accounts as accounts_repo
from app.repositories import bank_sync as bank_sync_repo
from app.repositories import committed_purchases as purchases_repo
from app.repositories import obligations as obligations_repo
from app.services import bank_transactions as service


def _make_link(db, user_id, account_id=None):
    connection_id = bank_sync_repo.create_connection(db, user_id, "Chase", b"encrypted-blob")
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    if account_id is not None:
        bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()
    return link_id


def _stage_transaction(db, user_id, link_id, amount_cents=-9000, posted_date="2026-07-15"):
    bank_sync_repo.create_transaction_staging_rows(
        db, user_id, link_id,
        [{
            "sfin_transaction_id": "t1",
            "posted_date": posted_date,
            "amount_cents": amount_cents,
            "description": "Landlord LLC",
            "pending": False,
        }],
    )
    db.commit()
    return bank_sync_repo.list_unmatched_transactions(db, user_id)[0]["id"]


# ----- confirm_obligation_match -----

def test_confirm_obligation_match_marks_paid_and_debits_linked_account(db, user_id):
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 500000)
    link_id = _make_link(db, user_id, account_id)
    staging_id = _stage_transaction(db, user_id, link_id, amount_cents=-90000)

    obligation_id = obligations_repo.create_obligation(db, user_id, "Rent", "housing", 90000, "2026-07-20")
    db.commit()

    matched = service.confirm_obligation_match(db, user_id, staging_id, obligation_id)
    db.commit()
    assert matched is True

    obligation = obligations_repo.get_obligation(db, user_id, obligation_id)
    assert obligation["is_paid"] == 1
    assert obligation["paid_date"] == "2026-07-15"

    account = accounts_repo.get_account(db, user_id, account_id)
    # Debited immediately — safe because the next balance sync writes an absolute
    # balance (not a delta), so it supersedes this interim debit rather than stacking.
    assert account["balance_cents"] == 410000

    ledger_rows = db.execute("SELECT * FROM transactions WHERE user_id = ?", (user_id,)).fetchall()
    assert len(ledger_rows) == 1
    assert ledger_rows[0]["amount_cents"] == 90000
    assert ledger_rows[0]["obligation_id"] == obligation_id
    assert ledger_rows[0]["account_id"] == account_id

    staging = bank_sync_repo.get_transaction_staging(db, user_id, staging_id)
    assert staging["status"] == "matched"
    assert staging["matched_obligation_id"] == obligation_id
    assert staging["ledger_transaction_id"] == ledger_rows[0]["id"]


def test_confirm_obligation_match_rolls_forward_recurring_due_date(db, user_id):
    link_id = _make_link(db, user_id)
    staging_id = _stage_transaction(db, user_id, link_id)
    obligation_id = obligations_repo.create_obligation(
        db, user_id, "Rent", "housing", 90000, "2026-07-01",
        is_recurring=1, recurrence_rule="monthly",
    )
    db.commit()

    matched = service.confirm_obligation_match(db, user_id, staging_id, obligation_id)
    db.commit()
    assert matched is True

    obligation = obligations_repo.get_obligation(db, user_id, obligation_id)
    assert obligation["is_paid"] == 0  # rolled forward to the next cycle, not left "paid"
    assert obligation["due_date"] == "2026-08-01"


def test_confirm_obligation_match_rejects_already_paid_obligation(db, user_id):
    link_id = _make_link(db, user_id)
    staging_id = _stage_transaction(db, user_id, link_id)
    obligation_id = obligations_repo.create_obligation(db, user_id, "Rent", "housing", 90000, "2026-07-20")
    db.commit()
    obligations_repo.update_obligation(
        db, user_id, obligation_id, "Rent", "housing", 90000, "2026-07-20", is_paid=1,
    )
    db.commit()

    matched = service.confirm_obligation_match(db, user_id, staging_id, obligation_id)
    assert matched is False
    assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_confirm_obligation_match_rejects_already_matched_staging_row(db, user_id):
    link_id = _make_link(db, user_id)
    staging_id = _stage_transaction(db, user_id, link_id)
    obligation_id = obligations_repo.create_obligation(db, user_id, "Rent", "housing", 90000, "2026-07-20")
    db.commit()

    assert service.confirm_obligation_match(db, user_id, staging_id, obligation_id) is True
    db.commit()

    obligation_id_2 = obligations_repo.create_obligation(db, user_id, "Other Bill", "other", 90000, "2026-07-25")
    db.commit()
    assert service.confirm_obligation_match(db, user_id, staging_id, obligation_id_2) is False


# ----- confirm_purchase_match -----

def test_confirm_purchase_match_records_payment_and_debits_linked_account(db, user_id):
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 500000)
    link_id = _make_link(db, user_id, account_id)
    staging_id = _stage_transaction(db, user_id, link_id, amount_cents=-20000)

    purchase_id = purchases_repo.create_purchase(db, user_id, "Drill", "career_tool", 50000, "ordered")
    db.commit()

    matched = service.confirm_purchase_match(db, user_id, staging_id, purchase_id)
    db.commit()
    assert matched is True

    purchase = purchases_repo.get_purchase(db, user_id, purchase_id)
    assert purchase["amount_paid_cents"] == 20000
    assert purchase["status"] == "partially_paid"

    account = accounts_repo.get_account(db, user_id, account_id)
    assert account["balance_cents"] == 480000

    ledger_rows = db.execute("SELECT * FROM transactions WHERE user_id = ?", (user_id,)).fetchall()
    assert len(ledger_rows) == 1
    assert ledger_rows[0]["committed_purchase_id"] == purchase_id


def test_confirm_purchase_match_rejects_already_resolved_purchase(db, user_id):
    link_id = _make_link(db, user_id)
    staging_id = _stage_transaction(db, user_id, link_id)
    purchase_id = purchases_repo.create_purchase(
        db, user_id, "Drill", "career_tool", 50000, "paid", amount_paid_cents=50000,
    )
    db.commit()

    matched = service.confirm_purchase_match(db, user_id, staging_id, purchase_id)
    assert matched is False
    assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_confirm_purchase_match_overpayment_raises_integrity_error(db, user_id):
    import pytest
    import sqlite3

    link_id = _make_link(db, user_id)
    staging_id = _stage_transaction(db, user_id, link_id, amount_cents=-90000)
    purchase_id = purchases_repo.create_purchase(db, user_id, "Drill", "career_tool", 50000, "ordered")
    db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        service.confirm_purchase_match(db, user_id, staging_id, purchase_id)
