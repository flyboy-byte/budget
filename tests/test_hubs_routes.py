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


def test_money_hub_requires_login(db, user_id, monkeypatch):
    monkeypatch.setattr(auth_module, "SECURE_COOKIES", False)
    monkeypatch.setattr(main_module, "SECURE_COOKIES", False)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    anon_client = TestClient(app)
    response = anon_client.get("/money", headers={"accept": "application/json"})
    assert response.status_code == 401
    app.dependency_overrides.clear()


def test_money_hub_shows_accounts_debts_bills_with_counts(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, interest_status, minimum_payment_cents)
           VALUES (?, 'Visa', 'credit_card', 50000, 'accruing', 2000)""",
        (user_id,),
    )
    db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Rent', 'housing', 90000, '2026-08-01')",
        (user_id,),
    )
    db.commit()

    response = client.get("/money")
    assert response.status_code == 200
    assert "$1,000.00" in response.text
    assert "$500.00" in response.text
    assert "1 unpaid" in response.text
    assert 'href="/accounts"' in response.text
    assert 'href="/debts"' in response.text
    assert 'href="/obligations"' in response.text


# ----- staleness nudge -----

def _other_user(db):
    cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    return cur.lastrowid


def test_money_hub_no_stale_badge_for_fresh_rows(client, db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 100000)",
        (user_id,),
    )
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, interest_status, minimum_payment_cents)
           VALUES (?, 'Visa', 'credit_card', 50000, 'accruing', 2000)""",
        (user_id,),
    )
    db.commit()

    response = client.get("/money")
    assert "not updated in a while" not in response.text


def test_money_hub_shows_stale_badge_for_old_rows(client, db, user_id):
    old = "2020-01-01T00:00:00.000000Z"
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents, updated_at) VALUES (?, 'Checking', 'checking', 100000, ?)",
        (user_id, old),
    )
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, interest_status, minimum_payment_cents, updated_at)
           VALUES (?, 'Visa', 'credit_card', 50000, 'accruing', 2000, ?)""",
        (user_id, old),
    )
    db.commit()

    response = client.get("/money")
    assert response.text.count("not updated in a while") == 2
    assert "1 not updated in a while" in response.text


def test_money_hub_stale_badge_clears_after_balance_update(client, db, user_id):
    old = "2020-01-01T00:00:00.000000Z"
    cur = db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents, updated_at) VALUES (?, 'Checking', 'checking', 100000, ?)",
        (user_id, old),
    )
    account_id = cur.lastrowid
    db.commit()

    from app.repositories import accounts as accounts_repo
    accounts_repo.update_balance(db, user_id, account_id, 200000)
    db.commit()

    response = client.get("/money")
    assert "not updated in a while" not in response.text


def test_money_hub_stale_count_scoped_to_owner(client, db, user_id):
    other_id = _other_user(db)
    old = "2020-01-01T00:00:00.000000Z"
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents, updated_at) VALUES (?, 'Checking', 'checking', 100000, ?)",
        (other_id, old),
    )
    db.commit()

    response = client.get("/money")
    assert "not updated in a while" not in response.text


def test_money_hub_shows_pending_bank_review_count(client, db, user_id, monkeypatch):
    from app import crypto
    from app.repositories import bank_sync as bank_sync_repo

    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", "9OYhjp1emy1J_NArPCEPTGgFUzRMc1BY3Qlcztew44I=")
    encrypted = crypto.encrypt("https://key:secret@bridge.simplefin.org/simplefin")
    connection_id = bank_sync_repo.create_connection(db, user_id, "Chase", encrypted)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()
    bank_sync_repo.create_staging_row(db, user_id, link_id, 12345)
    db.commit()

    response = client.get("/money")
    assert response.status_code == 200
    assert "1 to review" in response.text


def test_activity_hub_shows_purchases_and_income_with_counts(client, db, user_id):
    db.execute(
        """INSERT INTO committed_purchases (user_id, name, category, amount_cents, status)
           VALUES (?, 'Drill', 'career_tool', 20000, 'ordered')""",
        (user_id,),
    )
    db.execute(
        "INSERT INTO income_events (user_id, source, expected_amount_cents, expected_date, confidence) VALUES (?, 'Paycheck', 300000, '2026-08-01', 'confirmed')",
        (user_id,),
    )
    db.commit()

    response = client.get("/activity")
    assert response.status_code == 200
    assert "1 outstanding" in response.text
    assert "1 upcoming" in response.text
    assert 'href="/committed-purchases"' in response.text
    assert 'href="/income-events"' in response.text


def test_more_hub_links_to_forecast_snapshots_export_settings(client):
    response = client.get("/more")
    assert response.status_code == 200
    assert 'href="/forecast"' in response.text
    assert 'href="/snapshots"' in response.text
    assert 'href="/export"' in response.text
    assert 'href="/settings"' in response.text


def test_nav_has_four_top_level_destinations(client):
    response = client.get("/")
    assert '>Home<' in response.text
    assert '>Money<' in response.text
    assert '>Activity<' in response.text
    assert '>More<' in response.text
    # the old flat nav items should no longer be top-level nav links
    assert 'href="/obligations">Obligations' not in response.text


def test_nav_highlights_money_when_on_a_money_subpage(client):
    response = client.get("/debts")
    assert 'nav__link nav__link--active" href="/money"' in response.text


def test_nav_highlights_activity_when_on_an_activity_subpage(client):
    response = client.get("/income-events")
    assert 'nav__link nav__link--active" href="/activity"' in response.text


def test_nav_highlights_more_when_on_a_more_subpage(client):
    response = client.get("/settings")
    assert 'nav__link nav__link--active" href="/more"' in response.text
