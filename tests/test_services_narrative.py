from datetime import date

from app.repositories import accounts as accounts_repo
from app.repositories import committed_purchases as purchases_repo
from app.repositories import obligations as obligations_repo
from app.repositories import snapshots as snapshots_repo
from app.repositories import transactions as transactions_repo
from app.services import narrative

TODAY = date(2026, 7, 30)
YESTERDAY = "2026-07-29"


def _seed_yesterday_snapshot(db, user_id, safe_to_spend_cents):
    snapshots_repo.upsert_snapshot(
        db, user_id, YESTERDAY,
        {
            "cash_on_hand_cents": 0,
            "total_debt_cents": 0,
            "net_position_cents": 0,
            "reserved_cash_cents": 0,
            "safe_to_spend_cents": safe_to_spend_cents,
            "forecast_position_cents": 0,
            "protected_floor_cents": 0,
            "window_days": 14,
        },
    )
    db.commit()


def test_no_prior_snapshot_returns_none(db, user_id):
    accounts_repo.create_account(db, user_id, "Checking", "checking", 100000)
    db.commit()
    assert narrative.build_change_narrative(db, user_id, TODAY) is None


def test_unchanged_since_yesterday(db, user_id):
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 100000)
    _seed_yesterday_snapshot(db, user_id, 100000)
    assert narrative.build_change_narrative(db, user_id, TODAY) == "Unchanged since yesterday."


def test_delta_with_no_matching_transactions_is_number_only(db, user_id):
    accounts_repo.create_account(db, user_id, "Checking", "checking", 100000)
    _seed_yesterday_snapshot(db, user_id, 50000)
    result = narrative.build_change_narrative(db, user_id, TODAY)
    assert result == "Up $500.00 since yesterday."


def test_delta_with_obligation_transaction_names_the_mover(db, user_id):
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    obligation_id = obligations_repo.create_obligation(db, user_id, "Rent", "housing", 90000, YESTERDAY)
    obligations_repo.update_obligation(
        db, user_id, obligation_id, "Rent", "housing", 90000, YESTERDAY, is_paid=1,
    )
    _seed_yesterday_snapshot(db, user_id, 100000)
    transactions_repo.create_transaction(
        db, user_id, YESTERDAY, 90000, "obligation",
        account_id=account_id, obligation_id=obligation_id, memo="Rent",
    )
    db.commit()
    result = narrative.build_change_narrative(db, user_id, TODAY)
    assert result == "Down $900.00 since yesterday — Rent ($900.00)."


def test_delta_with_income_transaction_shows_inflow(db, user_id):
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 200000)
    _seed_yesterday_snapshot(db, user_id, 80000)
    transactions_repo.create_transaction(
        db, user_id, YESTERDAY, 120000, "other", account_id=account_id, memo="Income: Paycheck",
    )
    db.commit()
    result = narrative.build_change_narrative(db, user_id, TODAY)
    assert result == "Up $1,200.00 since yesterday — Income: Paycheck ($1,200.00)."


def test_only_top_two_movers_shown(db, user_id):
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    rent_id = obligations_repo.create_obligation(db, user_id, "Rent", "housing", 50000, YESTERDAY)
    phone_id = obligations_repo.create_obligation(db, user_id, "Phone bill", "other", 10000, YESTERDAY)
    drill_id = purchases_repo.create_purchase(db, user_id, "Drill", "career_tool", 30000, "ordered")
    _seed_yesterday_snapshot(db, user_id, 100000)
    transactions_repo.create_transaction(
        db, user_id, YESTERDAY, 50000, "obligation", account_id=account_id, obligation_id=rent_id, memo="Rent",
    )
    transactions_repo.create_transaction(
        db, user_id, YESTERDAY, 30000, "committed_purchase",
        account_id=account_id, committed_purchase_id=drill_id, memo="Drill",
    )
    transactions_repo.create_transaction(
        db, user_id, YESTERDAY, 10000, "obligation",
        account_id=account_id, obligation_id=phone_id, memo="Phone bill",
    )
    db.commit()
    result = narrative.build_change_narrative(db, user_id, TODAY)
    assert "Rent" in result
    assert "Drill" in result
    assert "Phone bill" not in result


def test_scoped_to_owner_only(db, user_id):
    other_id = db.execute(
        "INSERT INTO users (username, password_hash) VALUES ('bob', 'x')"
    ).lastrowid
    db.commit()
    accounts_repo.create_account(db, user_id, "Checking", "checking", 100000)
    _seed_yesterday_snapshot(db, user_id, 50000)
    # Another user's snapshot must not leak into this one's narrative.
    assert narrative.build_change_narrative(db, other_id, TODAY) is None
