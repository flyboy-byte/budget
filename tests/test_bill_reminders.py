from datetime import date

import pytest

from app.repositories import obligations as obligations_repo
from app.services import bill_reminders, push


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


def test_no_push_when_bill_due_in_three_days(db, user_id, monkeypatch):
    _subscribe(db, user_id)
    calls = []
    _fake_webpush(monkeypatch, calls)

    obligations_repo.create_obligation(
        db, user_id, "Internet", "utilities", 6000, "2026-08-06"
    )
    db.commit()

    sent = bill_reminders.send_due_bill_pushes(db, user_id, today=date(2026, 8, 3))
    assert sent is False
    assert calls == []


def test_push_fires_once_for_bill_due_today(db, user_id, monkeypatch):
    _subscribe(db, user_id)
    calls = []
    _fake_webpush(monkeypatch, calls)

    obligations_repo.create_obligation(
        db, user_id, "Rent", "housing", 120000, "2026-08-03"
    )
    db.commit()

    sent = bill_reminders.send_due_bill_pushes(db, user_id, today=date(2026, 8, 3))
    assert sent is True
    assert len(calls) == 1
    payload = calls[0]["data"]
    # Copy says what changed (VOICE.md), not the bill's name/amount -- a due-date
    # announcement is a calendar notification, not this app's job. No prior day's
    # snapshot exists in this test, so narrative.py falls back to this line.
    assert "Safe to spend" in payload
    assert "Nothing recorded since yesterday." in payload
    assert "Rent" not in payload

    # Second same-day run must not re-push.
    sent_again = bill_reminders.send_due_bill_pushes(db, user_id, today=date(2026, 8, 3))
    assert sent_again is False
    assert len(calls) == 1


def test_bundles_multiple_same_day_bills_into_one_push(db, user_id, monkeypatch):
    _subscribe(db, user_id)
    calls = []
    _fake_webpush(monkeypatch, calls)

    obligations_repo.create_obligation(db, user_id, "Rent", "housing", 120000, "2026-08-03")
    obligations_repo.create_obligation(db, user_id, "Internet", "utilities", 6000, "2026-08-04")
    db.commit()

    sent = bill_reminders.send_due_bill_pushes(db, user_id, today=date(2026, 8, 3))
    assert sent is True
    # Two bills due the same day still bundle into a single push, not one per bill.
    assert len(calls) == 1


def test_push_body_is_the_change_narrative_not_the_bill(db, user_id, monkeypatch):
    _subscribe(db, user_id)
    calls = []
    _fake_webpush(monkeypatch, calls)

    from app.repositories import snapshots as snapshots_repo

    snapshots_repo.upsert_snapshot(
        db, user_id, "2026-08-02",
        {
            "cash_on_hand_cents": 100000, "total_debt_cents": 0, "net_position_cents": 100000,
            "reserved_cash_cents": 0, "safe_to_spend_cents": 100000, "forecast_position_cents": 100000,
            "protected_floor_cents": 0, "window_days": 14,
        },
    )
    obligations_repo.create_obligation(
        db, user_id, "Rent", "housing", 120000, "2026-08-03"
    )
    db.commit()

    sent = bill_reminders.send_due_bill_pushes(db, user_id, today=date(2026, 8, 3))
    assert sent is True
    payload = calls[0]["data"]
    assert "since yesterday" in payload
    assert "Rent" not in payload
    assert "Nothing recorded since yesterday." not in payload


def test_paid_bill_is_excluded(db, user_id, monkeypatch):
    _subscribe(db, user_id)
    calls = []
    _fake_webpush(monkeypatch, calls)

    obligation_id = obligations_repo.create_obligation(
        db, user_id, "Rent", "housing", 120000, "2026-08-03"
    )
    obligations_repo.update_obligation(
        db, user_id, obligation_id, "Rent", "housing", 120000, "2026-08-03",
        is_paid=1,
    )
    db.commit()

    sent = bill_reminders.send_due_bill_pushes(db, user_id, today=date(2026, 8, 3))
    assert sent is False
    assert calls == []
