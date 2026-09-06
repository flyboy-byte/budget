from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.routers.auth as auth_module
from app.deps import get_db
from app.main import app
from app.repositories import snapshots as snapshots_repo
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


def test_dashboard_shows_safe_to_spend_from_accounts_only(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 50000)",
        (user_id,),
    )
    db.execute(
        """INSERT INTO income_events (user_id, source, expected_amount_cents, expected_date, confidence)
           VALUES (?, 'Paycheck', 999999, '2099-01-01', 'confirmed')""",
        (user_id,),
    )
    db.commit()

    response = client.get("/")
    assert response.status_code == 200
    assert "$500.00" in response.text
    # the future paycheck legitimately appears in its own "next paycheck" section, but
    # must never be folded into safe-to-spend, which stays at cash-on-hand ($500.00) only
    safe_to_spend_section = response.text.split("Safe to spend right now")[1].split("</section>")[0]
    assert "9,999.99" not in safe_to_spend_section


def test_dashboard_shows_first_run_state_with_no_accounts(client, db, user_id):
    response = client.get("/")
    assert response.status_code == 200
    assert "Get set up" in response.text
    assert "Add an account" in response.text
    assert "1. Add an account" in response.text
    assert "differ from your bank balance" in response.text
    # quick actions, sparkline-driving composition bar, and forecast are all suppressed
    assert "Update today" not in response.text
    assert "not the default number" not in response.text
    assert "composition-bar" not in response.text
    assert "$0.00" in response.text


def test_dashboard_shows_debt_priority_list(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, interest_status, apr_bps)
           VALUES (?, 'Visa', 'credit_card', 100000, 'accruing', 2499)""",
        (user_id,),
    )
    db.commit()

    response = client.get("/")
    assert "Visa" in response.text
    assert "$1,000.00" in response.text


def test_dashboard_has_logout_form_with_csrf_token(client):
    response = client.get("/")
    assert 'name="csrf_token"' in response.text
    assert 'action="/logout"' in response.text


def test_dashboard_shows_forecast_separately_labeled(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    response = client.get("/")
    assert "not the default number" in response.text


def test_dashboard_auto_captures_todays_snapshot(client, db, user_id):
    # the client fixture's login already triggers one dashboard view (redirect-follow)
    client.get("/")
    rows = db.execute("SELECT * FROM snapshots WHERE user_id = ?", (user_id,)).fetchall()
    assert len(rows) == 1
    import datetime
    assert rows[0]["snapshot_date"] == datetime.date.today().isoformat()


def test_dashboard_does_not_duplicate_todays_snapshot_on_repeat_visits(client, db, user_id):
    client.get("/")
    client.get("/")
    client.get("/")
    assert db.execute("SELECT COUNT(*) c FROM snapshots WHERE user_id = ?", (user_id,)).fetchone()["c"] == 1


def test_dashboard_shows_status_badge(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.commit()
    response = client.get("/")
    assert "On track" in response.text
    assert "badge--safe" in response.text


def test_dashboard_shows_negative_badge_when_overcommitted(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100)",
        (user_id,),
    )
    db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Rent', 'housing', 90000, ?)",
        (user_id, __import__("datetime").date.today().isoformat()),
    )
    db.commit()
    response = client.get("/")
    assert "Negative" in response.text
    assert "badge--risky" in response.text


def test_dashboard_negative_narrative_names_the_crossing_item(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100)",
        (user_id,),
    )
    db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Verizon', 'utilities', 90000, ?)",
        (user_id, date.today().isoformat()),
    )
    db.commit()
    response = client.get("/")
    assert "Verizon clears" in response.text
    assert "takes you under" in response.text
    assert "You need" not in response.text


def test_dashboard_negative_narrative_includes_next_paycheck(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100)",
        (user_id,),
    )
    db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Verizon', 'utilities', 90000, ?)",
        (user_id, date.today().isoformat()),
    )
    paycheck_date = (date.today() + timedelta(days=5)).isoformat()
    db.execute(
        """INSERT INTO income_events (user_id, source, expected_amount_cents, expected_date, confidence)
           VALUES (?, 'Paycheck', 200000, ?, 'confirmed')""",
        (user_id, paycheck_date),
    )
    db.commit()
    response = client.get("/")
    assert "Verizon clears" in response.text
    assert "Paycheck lands" in response.text


def test_dashboard_negative_narrative_falls_back_when_nothing_crosses_with_a_date(client, db, user_id):
    # Negative purely from the protected savings floor -- no dated obligation/debt/
    # purchase actually crosses zero, so there's nothing to name.
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100)",
        (user_id,),
    )
    db.execute(
        "INSERT INTO settings (user_id, key, value) VALUES (?, 'protected_savings_floor_cents', '5000')",
        (user_id,),
    )
    db.commit()
    response = client.get("/")
    assert "You need" in response.text
    assert "clears" not in response.text


def test_dashboard_reserved_breakdown_shows_only_nonzero_sources(client, db, user_id):
    from datetime import date

    due_soon = date.today().replace(day=min(date.today().day + 1, 28)).isoformat()
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 500000)",
        (user_id,),
    )
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, interest_status, minimum_payment_cents, next_due_date)
           VALUES (?, 'Visa', 'credit_card', 100000, 'accruing', 15000, ?)""",
        (user_id, due_soon),
    )
    db.commit()

    response = client.get("/")
    summary = response.text.split("Safe to spend right now")[1].split("</section>")[0]
    assert "Debt minimums $150.00" in summary
    assert "Bills $" not in summary
    assert "Committed purchases $" not in summary


def test_dashboard_reserved_breakdown_lists_multiple_sources(client, db, user_id):
    from datetime import date

    due_soon = date.today().replace(day=min(date.today().day + 1, 28)).isoformat()
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 500000)",
        (user_id,),
    )
    db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Rent', 'housing', 90000, ?)",
        (user_id, due_soon),
    )
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, interest_status, minimum_payment_cents, next_due_date)
           VALUES (?, 'Visa', 'credit_card', 100000, 'accruing', 15000, ?)""",
        (user_id, due_soon),
    )
    db.commit()

    response = client.get("/")
    summary = response.text.split("Safe to spend right now")[1].split("</section>")[0]
    assert "Bills $900.00" in summary
    assert "Debt minimums $150.00" in summary
    assert summary.index("Bills $900.00") < summary.index("Debt minimums $150.00")


def test_dashboard_shows_change_narrative_when_prior_snapshot_exists(client, db, user_id):
    from datetime import date, timedelta

    from app.repositories import snapshots as snapshots_repo

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    snapshots_repo.upsert_snapshot(
        db, user_id, yesterday,
        {
            "cash_on_hand_cents": 0, "total_debt_cents": 0, "net_position_cents": 0,
            "reserved_cash_cents": 0, "safe_to_spend_cents": 100000,
            "forecast_position_cents": 0, "protected_floor_cents": 0, "window_days": 14,
        },
    )
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 50000)",
        (user_id,),
    )
    db.commit()

    response = client.get("/")
    assert "since yesterday" in response.text


def test_dashboard_omits_change_narrative_with_no_prior_snapshot(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 50000)",
        (user_id,),
    )
    db.commit()
    response = client.get("/")
    assert "since yesterday" not in response.text


def test_dashboard_sparkline_appears_after_snapshot_history_exists(client, db, user_id):
    # first visit: only today's auto-captured snapshot exists (1 point) -> still renders a dot
    response = client.get("/")
    assert "<svg" in response.text


def test_dashboard_request_refreshes_session_cookie_max_age(client):
    # The server-side session slides forward on each request (touch_session); the
    # browser cookie's Max-Age must slide too, or it expires 30 days after login
    # regardless of ongoing activity.
    response = client.get("/")
    assert "session_id" in response.cookies
    set_cookie_header = response.headers["set-cookie"]
    assert "max-age=" in set_cookie_header.lower()


# ----- freshness tier (UI_AUDIT A1) -----

def test_dashboard_shows_no_stale_badge_for_fresh_balances(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 50000)",
        (user_id,),
    )
    db.commit()
    response = client.get("/")
    assert ">Stale<" not in response.text
    assert "running on old balances" not in response.text


def test_dashboard_shows_stale_state_for_an_old_balance(client, db, user_id):
    old = "2020-01-01T00:00:00.000000Z"
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents, updated_at) VALUES (?, 'Chase', 'checking', 50000, ?)",
        (user_id, old),
    )
    db.commit()
    response = client.get("/")
    assert ">Stale<" in response.text
    assert "running on old balances" in response.text
    assert "Chase last updated" in response.text
    assert "you&#39;re okay until" not in response.text and "you're okay until" not in response.text
    assert 'class="dim-stale"' in response.text
    assert 'href="/accounts"' in response.text


def test_dashboard_sparkline_dashes_after_last_real_update_when_stale(client, db, user_id):
    last_real = date.today() - timedelta(days=3)
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents, updated_at) VALUES (?, 'Chase', 'checking', 50000, ?)",
        (user_id, last_real.strftime("%Y-%m-%dT00:00:00.000000Z")),
    )
    db.execute(
        "INSERT INTO settings (user_id, key, value) VALUES (?, 'stale_balance_threshold_days', '1')",
        (user_id,),
    )
    db.commit()

    def snapshot_row(days_ago, safe_to_spend):
        snapshots_repo.upsert_snapshot(
            db, user_id, (date.today() - timedelta(days=days_ago)).isoformat(),
            {
                "cash_on_hand_cents": 50000, "total_debt_cents": 0, "net_position_cents": 50000,
                "reserved_cash_cents": 0, "safe_to_spend_cents": safe_to_spend, "forecast_position_cents": 50000,
                "protected_floor_cents": 0, "window_days": 14,
            },
        )

    snapshot_row(4, 40000)  # before the last real update -- genuine trend
    snapshot_row(3, 50000)  # the last real update itself
    snapshot_row(1, 50000)  # after it -- same frozen number re-captured
    db.commit()

    response = client.get("/")
    assert "stroke-dasharray" in response.text
    assert response.text.count("var(--bg-elevated)") >= 2


def test_dashboard_stale_state_wins_over_negative_framing(client, db, user_id):
    old = "2020-01-01T00:00:00.000000Z"
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents, updated_at) VALUES (?, 'Chase', 'checking', 100, ?)",
        (user_id, old),
    )
    db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Rent', 'housing', 90000, '2020-01-05')",
        (user_id,),
    )
    db.commit()
    response = client.get("/")
    assert ">Stale<" in response.text
    assert "Negative" not in response.text
    assert "card--hero-negative" not in response.text


def test_dashboard_stale_threshold_is_settings_backed(client, db, user_id):
    # 5 days old: stale under the default 7-day threshold if it were lower, but not
    # actually flagged until the setting says so.
    recent = "2026-01-01T00:00:00.000000Z"
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents, updated_at) VALUES (?, 'Checking', 'checking', 50000, ?)",
        (user_id, recent),
    )
    db.execute(
        "INSERT INTO settings (user_id, key, value) VALUES (?, 'stale_balance_threshold_days', '1')",
        (user_id,),
    )
    db.commit()
    response = client.get("/")
    assert ">Stale<" in response.text


def test_dashboard_stale_state_names_multiple_accounts_oldest_first(client, db, user_id):
    older = "2020-01-01T00:00:00.000000Z"
    newer = "2024-06-01T00:00:00.000000Z"
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents, updated_at) VALUES (?, 'Oldest', 'checking', 100, ?)",
        (user_id, older),
    )
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents, updated_at) VALUES (?, 'Newer', 'savings', 200, ?)",
        (user_id, newer),
    )
    db.commit()
    response = client.get("/")
    oldest_idx = response.text.find("Oldest last updated")
    newer_idx = response.text.find("Newer last updated")
    assert oldest_idx != -1 and newer_idx != -1
    assert oldest_idx < newer_idx


# ----- composition bar (UI_AUDIT A3) -----

def test_composition_bar_healthy_shows_four_segments_summing_to_cash_on_hand(client, db, user_id):
    from datetime import date

    due_soon = date.today().replace(day=min(date.today().day + 1, 28)).isoformat()
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Rent', 'housing', 40000, ?)",
        (user_id, due_soon),
    )
    db.commit()
    response = client.get("/")
    assert "composition-bar__segment--bills" in response.text
    assert "composition-bar__segment--covered" not in response.text
    assert "Bills $400.00" in response.text
    assert "Free $600.00" in response.text
    assert "Reserved cash" not in response.text


def test_composition_bar_negative_shows_covered_and_not_covered(client, db, user_id):
    from datetime import date

    due_soon = date.today().replace(day=min(date.today().day + 1, 28)).isoformat()
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 10000)",
        (user_id,),
    )
    db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Rent', 'housing', 90000, ?)",
        (user_id, due_soon),
    )
    db.commit()
    response = client.get("/")
    assert "composition-bar__segment--covered" in response.text
    assert "composition-bar__segment--uncovered" in response.text
    assert "Covered $100.00" in response.text
    assert "Not covered $800.00" in response.text
    assert "composition-bar__segment--free" not in response.text


def test_composition_bar_stays_four_segment_when_stale_and_negative(client, db, user_id):
    from datetime import date

    old = "2020-01-01T00:00:00.000000Z"
    due_soon = date.today().replace(day=min(date.today().day + 1, 28)).isoformat()
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents, updated_at) VALUES (?, 'Checking', 'checking', 100, ?)",
        (user_id, old),
    )
    db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Rent', 'housing', 90000, ?)",
        (user_id, due_soon),
    )
    db.commit()
    response = client.get("/")
    assert ">Stale<" in response.text
    assert "composition-bar__segment--covered" not in response.text
    assert "composition-bar__segment--free" in response.text
    # Regression: the 4-segment shape still applies when actually negative (stale
    # wins), but the "Free" legend must never show a negative dollar figure --
    # caught via screenshot during manual verification, not by an assertion.
    assert "Free -$" not in response.text
    assert "Free $0.00" in response.text
