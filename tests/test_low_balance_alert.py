from datetime import date

import pytest

from app.repositories import settings as settings_repo
from app.services import low_balance_alert, push


@pytest.fixture(autouse=True)
def _vapid_env(monkeypatch):
    monkeypatch.setenv("PUSH_VAPID_PUBLIC_KEY", "test-public-key")
    monkeypatch.setenv("PUSH_VAPID_PRIVATE_KEY", "test-private-key")


def _subscribe(db, user_id):
    from app.repositories import push_subscriptions as sub_repo

    sub_repo.create(db, user_id, "https://push.example/1", "p256dh-key", "auth-key")
    db.commit()


def _fake_webpush(monkeypatch, calls):
    def fake(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(push, "webpush", fake)


@pytest.fixture
def second_user_id(db):
    cur = db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        ("bob", "hash"),
    )
    db.commit()
    return cur.lastrowid


def _fund_account(db, user_id, cents):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', ?)",
        (user_id, cents),
    )
    db.commit()


def test_no_push_when_threshold_unset(db, user_id, monkeypatch):
    _subscribe(db, user_id)
    calls = []
    _fake_webpush(monkeypatch, calls)
    _fund_account(db, user_id, 100)

    sent = low_balance_alert.check_low_safe_to_spend(db, user_id, today=date(2026, 8, 3))
    assert sent is False
    assert calls == []


def test_push_fires_on_first_drop_below_threshold(db, user_id, monkeypatch):
    _subscribe(db, user_id)
    calls = []
    _fake_webpush(monkeypatch, calls)
    _fund_account(db, user_id, 5000)
    settings_repo.upsert(db, user_id, "low_safe_to_spend_threshold_cents", "10000")
    db.commit()

    sent = low_balance_alert.check_low_safe_to_spend(db, user_id, today=date(2026, 8, 3))
    assert sent is True
    assert len(calls) == 1
    assert settings_repo.get_all(db, user_id)["low_safe_to_spend_alert_active"] == "1"


def test_no_repeat_push_on_second_still_below_run(db, user_id, monkeypatch):
    _subscribe(db, user_id)
    calls = []
    _fake_webpush(monkeypatch, calls)
    _fund_account(db, user_id, 5000)
    settings_repo.upsert(db, user_id, "low_safe_to_spend_threshold_cents", "10000")
    db.commit()

    low_balance_alert.check_low_safe_to_spend(db, user_id, today=date(2026, 8, 3))
    sent_again = low_balance_alert.check_low_safe_to_spend(db, user_id, today=date(2026, 8, 4))

    assert sent_again is False
    assert len(calls) == 1


def test_flag_clears_and_rearms_after_recovery(db, user_id, monkeypatch):
    _subscribe(db, user_id)
    calls = []
    _fake_webpush(monkeypatch, calls)
    settings_repo.upsert(db, user_id, "low_safe_to_spend_threshold_cents", "10000")
    db.commit()

    cur = db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 5000)",
        (user_id,),
    )
    account_id = cur.lastrowid
    db.commit()

    # Drop below -> pushes once.
    low_balance_alert.check_low_safe_to_spend(db, user_id, today=date(2026, 8, 3))
    assert len(calls) == 1

    # Recover above threshold -> clears flag, no push.
    db.execute("UPDATE accounts SET balance_cents = 20000 WHERE id = ?", (account_id,))
    db.commit()
    recovered = low_balance_alert.check_low_safe_to_spend(db, user_id, today=date(2026, 8, 4))
    assert recovered is False
    assert len(calls) == 1
    assert settings_repo.get_all(db, user_id)["low_safe_to_spend_alert_active"] == "0"

    # Dip below again -> re-arms and pushes.
    db.execute("UPDATE accounts SET balance_cents = 5000 WHERE id = ?", (account_id,))
    db.commit()
    sent_again = low_balance_alert.check_low_safe_to_spend(db, user_id, today=date(2026, 8, 5))
    assert sent_again is True
    assert len(calls) == 2


def test_respects_per_user_threshold(db, user_id, second_user_id, monkeypatch):
    _subscribe(db, user_id)
    _subscribe(db, second_user_id)
    calls = []
    _fake_webpush(monkeypatch, calls)

    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 5000)",
        (user_id,),
    )
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 5000)",
        (second_user_id,),
    )
    settings_repo.upsert(db, user_id, "low_safe_to_spend_threshold_cents", "10000")
    settings_repo.upsert(db, second_user_id, "low_safe_to_spend_threshold_cents", "1000")
    db.commit()

    sent_first = low_balance_alert.check_low_safe_to_spend(db, user_id, today=date(2026, 8, 3))
    sent_second = low_balance_alert.check_low_safe_to_spend(db, second_user_id, today=date(2026, 8, 3))

    assert sent_first is True
    assert sent_second is False
