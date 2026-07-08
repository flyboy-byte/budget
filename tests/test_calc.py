from datetime import date

from app.services import calc


def add_account(db, user_id, balance_cents=10000, is_active=1, name="Checking"):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents, is_active) VALUES (?, ?, 'checking', ?, ?)",
        (user_id, name, balance_cents, is_active),
    )
    db.commit()


def add_debt(
    db,
    user_id,
    balance_cents=0,
    minimum_payment_cents=0,
    next_due_date=None,
    is_active=1,
    interest_status="accruing",
    apr_bps=None,
    is_flexible_payment=0,
    priority=0,
    due=None,
):
    cur = db.execute(
        """INSERT INTO debts
           (user_id, name, type, balance_cents, apr_bps, minimum_payment_cents, next_due_date,
            interest_status, is_flexible_payment, priority, is_active)
           VALUES (?, 'Card', 'credit_card', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            balance_cents,
            apr_bps,
            minimum_payment_cents,
            next_due_date,
            interest_status,
            is_flexible_payment,
            priority,
            is_active,
        ),
    )
    db.commit()
    return cur.lastrowid


def add_obligation(db, user_id, amount_cents, due_date, is_paid=0):
    db.execute(
        """INSERT INTO obligations (user_id, name, category, amount_cents, due_date, is_paid)
           VALUES (?, 'Rent', 'housing', ?, ?, ?)""",
        (user_id, amount_cents, due_date, is_paid),
    )
    db.commit()


def add_purchase(db, user_id, amount_cents, amount_paid_cents, status, payment_deadline=None):
    db.execute(
        """INSERT INTO committed_purchases
           (user_id, name, category, amount_cents, amount_paid_cents, status, payment_deadline)
           VALUES (?, 'Tool', 'career_tool', ?, ?, ?, ?)""",
        (user_id, amount_cents, amount_paid_cents, status, payment_deadline),
    )
    db.commit()


def add_income(db, user_id, expected_amount_cents, expected_date, confidence="confirmed", is_received=0):
    db.execute(
        """INSERT INTO income_events (user_id, source, expected_amount_cents, expected_date, confidence, is_received)
           VALUES (?, 'Paycheck', ?, ?, ?, ?)""",
        (user_id, expected_amount_cents, expected_date, confidence, is_received),
    )
    db.commit()


def set_setting(db, user_id, key, value):
    db.execute(
        "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?)",
        (user_id, key, value),
    )
    db.commit()


# ---------- basic aggregates ----------

def test_cash_on_hand_sums_only_active_accounts(db, user_id):
    add_account(db, user_id, balance_cents=10000, is_active=1)
    add_account(db, user_id, balance_cents=5000, is_active=0)
    assert calc.cash_on_hand(db, user_id) == 10000


def test_net_position(db, user_id):
    add_account(db, user_id, balance_cents=10000)
    add_debt(db, user_id, balance_cents=4000)
    assert calc.net_position(db, user_id) == 6000


# ---------- reserved cash ----------

def test_obligations_reserved_excludes_paid_and_future(db, user_id):
    add_obligation(db, user_id, 5000, "2026-01-10", is_paid=0)
    add_obligation(db, user_id, 3000, "2026-01-10", is_paid=1)  # paid, excluded
    add_obligation(db, user_id, 7000, "2026-02-15", is_paid=0)  # outside window
    assert calc.obligations_reserved(db, user_id, "2026-01-31") == 5000


def test_debts_reserved_uses_minimum_not_full_balance(db, user_id):
    add_debt(db, user_id, balance_cents=500000, minimum_payment_cents=2500, next_due_date="2026-01-15")
    assert calc.debts_reserved(db, user_id, "2026-01-31") == 2500


def test_debts_reserved_excludes_inactive_and_out_of_window(db, user_id):
    add_debt(db, user_id, minimum_payment_cents=2500, next_due_date="2026-01-15", is_active=0)
    add_debt(db, user_id, minimum_payment_cents=1000, next_due_date="2026-03-01")
    assert calc.debts_reserved(db, user_id, "2026-01-31") == 0


def test_committed_purchase_ordered_is_always_reserved_regardless_of_window(db, user_id):
    add_purchase(db, user_id, amount_cents=10000, amount_paid_cents=0, status="ordered")
    # window_end far in the past — ordered purchases reserve unconditionally
    assert calc.committed_purchases_reserved(db, user_id, "2000-01-01") == 10000


def test_committed_purchase_planned_gated_by_deadline(db, user_id):
    add_purchase(db, user_id, 10000, 0, "planned", payment_deadline="2026-01-20")
    add_purchase(db, user_id, 5000, 0, "planned", payment_deadline="2026-03-01")
    assert calc.committed_purchases_reserved(db, user_id, "2026-01-31") == 10000


def test_committed_purchase_paid_and_canceled_never_reserved(db, user_id):
    add_purchase(db, user_id, 10000, 10000, "paid")
    add_purchase(db, user_id, 5000, 0, "canceled")
    assert calc.committed_purchases_reserved(db, user_id, "2099-01-01") == 0


def test_reserved_cash_combines_all_three(db, user_id):
    add_obligation(db, user_id, 1000, "2026-01-05")
    add_debt(db, user_id, minimum_payment_cents=2000, next_due_date="2026-01-10")
    add_purchase(db, user_id, 3000, 0, "ordered")
    assert calc.reserved_cash(db, user_id, "2026-01-31") == 6000


# ---------- safe to spend ----------

def test_safe_to_spend_excludes_future_income(db, user_id):
    add_account(db, user_id, balance_cents=10000)
    add_income(db, user_id, 999999, "2026-01-05")  # must never affect safe_to_spend
    set_setting(db, user_id, "reserved_window_mode", "fixed_days")
    set_setting(db, user_id, "reserved_window_fixed_days", "14")
    today = date(2026, 1, 1)
    assert calc.safe_to_spend(db, user_id, today) == 10000


def test_safe_to_spend_subtracts_reserved_and_floor(db, user_id):
    add_account(db, user_id, balance_cents=10000)
    add_obligation(db, user_id, 2000, "2026-01-05")
    set_setting(db, user_id, "protected_savings_floor_cents", "1000")
    set_setting(db, user_id, "reserved_window_mode", "fixed_days")
    set_setting(db, user_id, "reserved_window_fixed_days", "14")
    today = date(2026, 1, 1)
    assert calc.safe_to_spend(db, user_id, today) == 10000 - 2000 - 1000


def test_safe_to_spend_can_go_negative(db, user_id):
    add_account(db, user_id, balance_cents=1000)
    add_obligation(db, user_id, 5000, "2026-01-05")
    set_setting(db, user_id, "reserved_window_mode", "fixed_days")
    set_setting(db, user_id, "reserved_window_fixed_days", "14")
    today = date(2026, 1, 1)
    assert calc.safe_to_spend(db, user_id, today) == 1000 - 5000


# ---------- reserved window modes ----------

def test_window_end_of_month(db, user_id):
    today = date(2026, 2, 10)
    set_setting(db, user_id, "reserved_window_mode", "end_of_month")
    assert calc.reserved_window_end(db, user_id, today) == "2026-02-28"


def test_window_fixed_days(db, user_id):
    today = date(2026, 1, 1)
    set_setting(db, user_id, "reserved_window_mode", "fixed_days")
    set_setting(db, user_id, "reserved_window_fixed_days", "10")
    assert calc.reserved_window_end(db, user_id, today) == "2026-01-11"


def test_window_next_paycheck_uses_earliest_future_income(db, user_id):
    today = date(2026, 1, 1)
    set_setting(db, user_id, "reserved_window_mode", "next_paycheck")
    add_income(db, user_id, 100000, "2026-01-15", confidence="uncertain")  # confidence irrelevant for timing
    add_income(db, user_id, 100000, "2026-01-20")
    assert calc.reserved_window_end(db, user_id, today) == "2026-01-15"


def test_window_next_paycheck_falls_back_to_end_of_month_if_none(db, user_id):
    today = date(2026, 1, 1)
    set_setting(db, user_id, "reserved_window_mode", "next_paycheck")
    assert calc.reserved_window_end(db, user_id, today) == "2026-01-31"


# ---------- forecast ----------

def test_forecast_position_includes_future_confirmed_income(db, user_id):
    add_account(db, user_id, balance_cents=1000)
    add_debt(db, user_id, balance_cents=500)
    add_income(db, user_id, 5000, "2026-01-15", confidence="confirmed")
    set_setting(db, user_id, "forecast_window_days", "30")
    set_setting(db, user_id, "forecast_income_confidence", "confirmed")
    today = date(2026, 1, 1)
    # 1000 cash + 5000 income - 0 obligations - 0 debts_reserved - 0 purchases - 500 total_debt
    assert calc.forecast_position(db, user_id, today) == 1000 + 5000 - 500


def test_forecast_position_excludes_unconfirmed_income_by_default(db, user_id):
    add_account(db, user_id, balance_cents=1000)
    add_income(db, user_id, 5000, "2026-01-15", confidence="likely")
    set_setting(db, user_id, "forecast_window_days", "30")
    set_setting(db, user_id, "forecast_income_confidence", "confirmed")
    today = date(2026, 1, 1)
    assert calc.forecast_position(db, user_id, today) == 1000


def test_forecast_position_includes_likely_when_configured(db, user_id):
    add_account(db, user_id, balance_cents=1000)
    add_income(db, user_id, 5000, "2026-01-15", confidence="likely")
    set_setting(db, user_id, "forecast_window_days", "30")
    set_setting(db, user_id, "forecast_income_confidence", "confirmed_likely")
    today = date(2026, 1, 1)
    assert calc.forecast_position(db, user_id, today) == 6000


def test_forecast_position_excludes_income_outside_window(db, user_id):
    add_account(db, user_id, balance_cents=1000)
    add_income(db, user_id, 5000, "2026-03-01", confidence="confirmed")
    set_setting(db, user_id, "forecast_window_days", "30")
    today = date(2026, 1, 1)
    assert calc.forecast_position(db, user_id, today) == 1000


# ---------- debt priority ----------

def test_debt_priority_pinned_first(db, user_id):
    a = add_debt(db, user_id, balance_cents=100, interest_status="accruing", priority=2)
    b = add_debt(db, user_id, balance_cents=100, interest_status="accruing", priority=1)
    ranked = calc.debt_priority(db, user_id)
    assert [r["id"] for r in ranked[:2]] == [b, a]


def test_debt_priority_tier_a_before_tier_b(db, user_id):
    flexible = add_debt(db, user_id, interest_status="not_accruing", balance_cents=100)
    risky = add_debt(db, user_id, interest_status="accruing", apr_bps=1000, balance_cents=100)
    ranked = calc.debt_priority(db, user_id)
    assert [r["id"] for r in ranked] == [risky, flexible]


def test_debt_priority_promo_unknown_treated_as_risky_and_sorts_by_apr_then_balance(db, user_id):
    unknown = add_debt(db, user_id, interest_status="promo_unknown", apr_bps=None, balance_cents=100)
    high_apr = add_debt(db, user_id, interest_status="accruing", apr_bps=2000, balance_cents=50)
    low_apr = add_debt(db, user_id, interest_status="accruing", apr_bps=500, balance_cents=50)
    ranked = calc.debt_priority(db, user_id)
    # NULL apr (unknown/promo_unknown) sorts as highest risk, ahead of any known APR
    assert [r["id"] for r in ranked] == [unknown, high_apr, low_apr]


def test_debt_priority_tier_b_sorted_by_due_date_then_balance(db, user_id):
    later = add_debt(db, user_id, interest_status="not_accruing", next_due_date="2026-03-01", balance_cents=100)
    sooner_big = add_debt(db, user_id, interest_status="not_accruing", next_due_date="2026-01-01", balance_cents=500)
    sooner_small = add_debt(db, user_id, interest_status="not_accruing", next_due_date="2026-01-01", balance_cents=100)
    ranked = calc.debt_priority(db, user_id)
    assert [r["id"] for r in ranked] == [sooner_small, sooner_big, later]


def test_debt_priority_flexible_payment_goes_to_tier_b_even_if_accruing(db, user_id):
    flexible_but_accruing = add_debt(
        db, user_id, interest_status="accruing", is_flexible_payment=1, balance_cents=100
    )
    strictly_risky = add_debt(db, user_id, interest_status="accruing", apr_bps=1000, balance_cents=100)
    ranked = calc.debt_priority(db, user_id)
    assert [r["id"] for r in ranked] == [strictly_risky, flexible_but_accruing]


def test_debt_priority_excludes_inactive_debts(db, user_id):
    add_debt(db, user_id, is_active=0)
    assert calc.debt_priority(db, user_id) == []


# ---------- debt payoff projection ----------
# Pure function (no conn/user_id) — not part of the cross-user-isolation test below.

def test_debt_payoff_projection_zero_percent_exact_months(db, user_id):
    result = calc.debt_payoff_projection(
        balance_cents=50000, apr_bps=None, minimum_payment_cents=10000, interest_status="not_accruing"
    )
    assert result == {"payoff_impossible": False, "months": 5, "total_interest_cents": 0}


def test_debt_payoff_projection_not_accruing_ignores_apr_bps(db, user_id):
    result = calc.debt_payoff_projection(
        balance_cents=6000, apr_bps=999, minimum_payment_cents=2000, interest_status="not_accruing"
    )
    assert result == {"payoff_impossible": False, "months": 3, "total_interest_cents": 0}


def test_debt_payoff_projection_accruing_hand_verified(db, user_id):
    # 12% APR -> 1% monthly. Hand-verified: m1 interest=100 remaining=5100,
    # m2 interest=51 remaining=151, m3 interest=1.51 remaining=0. total=152.51 -> 153.
    result = calc.debt_payoff_projection(
        balance_cents=10000, apr_bps=1200, minimum_payment_cents=5000, interest_status="accruing"
    )
    assert result == {"payoff_impossible": False, "months": 3, "total_interest_cents": 153}


def test_debt_payoff_projection_unknown_apr_returns_none(db, user_id):
    assert calc.debt_payoff_projection(
        balance_cents=10000, apr_bps=None, minimum_payment_cents=500, interest_status="accruing"
    ) is None
    assert calc.debt_payoff_projection(
        balance_cents=10000, apr_bps=None, minimum_payment_cents=500, interest_status="promo_unknown"
    ) is None


def test_debt_payoff_projection_minimum_below_interest_is_impossible(db, user_id):
    result = calc.debt_payoff_projection(
        balance_cents=100000, apr_bps=2400, minimum_payment_cents=1500, interest_status="accruing"
    )
    assert result == {"payoff_impossible": True}


def test_debt_payoff_projection_no_minimum_or_zero_balance_returns_none(db, user_id):
    assert calc.debt_payoff_projection(0, 1200, 5000, "accruing") is None
    assert calc.debt_payoff_projection(10000, 1200, 0, "accruing") is None


# ---------- next paycheck / next due payment ----------

def test_next_paycheck_returns_earliest_future_unreceived(db, user_id):
    add_income(db, user_id, 1000, "2026-01-20")
    add_income(db, user_id, 2000, "2026-01-10")
    add_income(db, user_id, 3000, "2026-01-05", is_received=1)  # already received, excluded
    today = date(2026, 1, 1)
    result = calc.next_paycheck(db, user_id, today)
    assert result["expected_amount_cents"] == 2000


def test_next_paycheck_none_when_no_upcoming_income(db, user_id):
    today = date(2026, 1, 1)
    assert calc.next_paycheck(db, user_id, today) is None


def test_next_due_payment_picks_earliest_across_obligations_and_debts(db, user_id):
    add_obligation(db, user_id, 5000, "2026-01-20")
    add_debt(db, user_id, minimum_payment_cents=2500, next_due_date="2026-01-10")
    result = calc.next_due_payment(db, user_id)
    assert result["kind"] == "debt"
    assert result["due_date"] == "2026-01-10"


def test_next_due_payment_none_when_nothing_outstanding(db, user_id):
    assert calc.next_due_payment(db, user_id) is None


# ---------- snapshot_values ----------

def test_snapshot_values_matches_individual_calc_functions(db, user_id):
    add_account(db, user_id, balance_cents=10000)
    add_debt(db, user_id, balance_cents=2000, minimum_payment_cents=100, next_due_date="2026-01-05")
    add_obligation(db, user_id, 500, "2026-01-05")
    set_setting(db, user_id, "protected_savings_floor_cents", "1000")
    set_setting(db, user_id, "reserved_window_mode", "fixed_days")
    set_setting(db, user_id, "reserved_window_fixed_days", "14")
    today = date(2026, 1, 1)

    values = calc.snapshot_values(db, user_id, today)
    assert values["cash_on_hand_cents"] == 10000
    assert values["total_debt_cents"] == 2000
    assert values["net_position_cents"] == 8000
    assert values["safe_to_spend_cents"] == calc.safe_to_spend(db, user_id, today)
    assert values["forecast_position_cents"] == calc.forecast_position(db, user_id, today)
    assert values["protected_floor_cents"] == 1000
    assert values["window_days"] == 14


# ---------- multi-user isolation ----------

def test_calc_functions_never_leak_across_users(db, user_id):
    other_cur = db.execute(
        "INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')"
    )
    db.commit()
    other_user_id = other_cur.lastrowid

    add_account(db, user_id, balance_cents=10000)
    add_debt(db, user_id, balance_cents=2000, minimum_payment_cents=100, next_due_date="2026-01-05")
    add_obligation(db, user_id, 500, "2026-01-05")
    add_purchase(db, user_id, 300, 0, "ordered")
    add_income(db, user_id, 5000, "2026-01-10")

    add_account(db, other_user_id, balance_cents=999999)
    add_debt(db, other_user_id, balance_cents=888888, minimum_payment_cents=99999, next_due_date="2026-01-05")
    add_obligation(db, other_user_id, 77777, "2026-01-05")
    add_purchase(db, other_user_id, 66666, 0, "ordered")
    add_income(db, other_user_id, 55555, "2026-01-10")

    today = date(2026, 1, 1)
    assert calc.cash_on_hand(db, user_id) == 10000
    assert calc.total_debt(db, user_id) == 2000
    assert calc.reserved_cash(db, user_id, "2026-01-31") == 100 + 500 + 300
    assert calc.safe_to_spend(db, user_id, today) == 10000 - (100 + 500 + 300)
    assert calc.forecast_position(db, user_id, today) == 10000 + 5000 - (100 + 500 + 300) - 2000
    assert [r["id"] for r in calc.debt_priority(db, user_id)] == [
        r["id"] for r in db.execute("SELECT id FROM debts WHERE user_id = ?", (user_id,)).fetchall()
    ]
