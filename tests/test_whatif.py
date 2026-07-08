from datetime import date

from app.services import whatif


def add_account(db, user_id, balance_cents=10000):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', ?)",
        (user_id, balance_cents),
    )
    db.commit()


def test_whatif_never_writes_to_real_db(db, user_id):
    add_account(db, user_id, 10000)
    before = db.execute("SELECT COUNT(*) c FROM obligations").fetchone()["c"]

    whatif.run_whatif(
        db, user_id, date(2026, 1, 1),
        hypothetical_obligations=[{"amount_cents": 5000, "due_date": "2026-01-10"}],
    )

    after = db.execute("SELECT COUNT(*) c FROM obligations").fetchone()["c"]
    assert before == after == 0


def test_whatif_with_no_hypotheticals_matches_real(db, user_id):
    add_account(db, user_id, 10000)
    result = whatif.run_whatif(db, user_id, date(2026, 1, 1))
    assert result["real"] == result["hypothetical"]
    assert all(v == 0 for v in result["delta"].values())


def test_whatif_hypothetical_obligation_reduces_safe_to_spend(db, user_id):
    add_account(db, user_id, 10000)
    result = whatif.run_whatif(
        db, user_id, date(2026, 1, 1),
        hypothetical_obligations=[{"amount_cents": 3000, "due_date": "2026-01-15"}],
    )
    assert result["real"]["safe_to_spend_cents"] == 10000
    assert result["hypothetical"]["safe_to_spend_cents"] == 7000
    assert result["delta"]["safe_to_spend_cents"] == -3000


def test_whatif_hypothetical_purchase_ordered_always_reserved(db, user_id):
    add_account(db, user_id, 10000)
    result = whatif.run_whatif(
        db, user_id, date(2026, 1, 1),
        hypothetical_purchases=[{"amount_cents": 4000, "status": "ordered"}],
    )
    assert result["hypothetical"]["reserved_cash_cents"] == 4000
    assert result["hypothetical"]["safe_to_spend_cents"] == 6000


def test_whatif_hypothetical_income_only_affects_forecast_not_safe_to_spend(db, user_id):
    add_account(db, user_id, 10000)
    result = whatif.run_whatif(
        db, user_id, date(2026, 1, 1),
        hypothetical_income_events=[
            {"expected_amount_cents": 200000, "expected_date": "2026-01-15", "confidence": "confirmed"}
        ],
    )
    assert result["delta"]["safe_to_spend_cents"] == 0
    assert result["delta"]["forecast_position_cents"] == 200000


def test_whatif_hypothetical_debt_affects_total_debt_and_net_position(db, user_id):
    add_account(db, user_id, 10000)
    result = whatif.run_whatif(
        db, user_id, date(2026, 1, 1),
        hypothetical_debts=[{"balance_cents": 50000, "interest_status": "accruing"}],
    )
    assert result["delta"]["total_debt_cents"] == 50000
    assert result["delta"]["net_position_cents"] == -50000


def test_whatif_respects_real_settings(db, user_id):
    add_account(db, user_id, 10000)
    db.execute(
        "INSERT INTO settings (user_id, key, value) VALUES (?, 'protected_savings_floor_cents', '2000')",
        (user_id,),
    )
    db.commit()
    result = whatif.run_whatif(db, user_id, date(2026, 1, 1))
    assert result["real"]["safe_to_spend_cents"] == 8000
    assert result["hypothetical"]["safe_to_spend_cents"] == 8000


def test_whatif_multiple_users_data_not_mixed(db, user_id):
    add_account(db, user_id, 10000)
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Bobs', 'checking', 999999)",
        (other_id,),
    )
    db.commit()

    result = whatif.run_whatif(db, user_id, date(2026, 1, 1))
    assert result["real"]["cash_on_hand_cents"] == 10000


def test_whatif_obligation_without_due_date_falls_back_to_today(db, user_id):
    # due_date is NOT NULL with no column default; omitting it must not crash
    add_account(db, user_id, 10000)
    result = whatif.run_whatif(
        db, user_id, date(2026, 1, 1),
        hypothetical_obligations=[{"amount_cents": 1000}],
    )
    assert result["delta"]["safe_to_spend_cents"] == -1000


def test_whatif_income_without_expected_date_falls_back_to_today(db, user_id):
    # expected_date is NOT NULL with no column default; omitting it must not crash
    add_account(db, user_id, 10000)
    result = whatif.run_whatif(
        db, user_id, date(2026, 1, 1),
        hypothetical_income_events=[{"expected_amount_cents": 5000}],
    )
    assert result["delta"]["forecast_position_cents"] == 5000
