import sqlite3

import pytest

from app.repositories import committed_purchases as repo


def test_create_and_get_purchase(db, user_id):
    purchase_id = repo.create_purchase(db, user_id, "Drill", "career_tool", 20000, "planned")
    db.commit()
    row = repo.get_purchase(db, user_id, purchase_id)
    assert row["name"] == "Drill"
    assert row["amount_cents"] == 20000
    assert row["remaining_cents"] == 20000


def test_list_excludes_paid_and_canceled_by_default(db, user_id):
    ordered_id = repo.create_purchase(db, user_id, "Drill", "career_tool", 20000, "ordered")
    paid_id = repo.create_purchase(db, user_id, "Saw", "career_tool", 10000, "paid", amount_paid_cents=10000)
    db.commit()

    visible = repo.list_purchases(db, user_id)
    assert [r["id"] for r in visible] == [ordered_id]

    everything = repo.list_purchases(db, user_id, include_resolved=True)
    assert {r["id"] for r in everything} == {ordered_id, paid_id}


def test_update_purchase_tracks_partial_payment(db, user_id):
    purchase_id = repo.create_purchase(db, user_id, "Drill", "career_tool", 20000, "ordered")
    db.commit()
    updated = repo.update_purchase(
        db, user_id, purchase_id, "Drill", "career_tool", 20000, 5000, "partially_paid"
    )
    db.commit()
    assert updated is True
    row = repo.get_purchase(db, user_id, purchase_id)
    assert row["remaining_cents"] == 15000
    assert row["status"] == "partially_paid"


def test_delete_purchase(db, user_id):
    purchase_id = repo.create_purchase(db, user_id, "Drill", "career_tool", 20000, "planned")
    db.commit()
    assert repo.delete_purchase(db, user_id, purchase_id) is True
    assert repo.get_purchase(db, user_id, purchase_id) is None


def test_purchase_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    purchase_id = repo.create_purchase(db, other_id, "Bob's Drill", "career_tool", 100, "planned")
    db.commit()
    assert repo.get_purchase(db, user_id, purchase_id) is None
    assert repo.delete_purchase(db, user_id, purchase_id) is False


def test_record_payment_partial(db, user_id):
    purchase_id = repo.create_purchase(db, user_id, "Drill", "career_tool", 20000, "ordered")
    db.commit()
    result = repo.record_payment(db, user_id, purchase_id, 5000)
    db.commit()
    assert result is True
    row = repo.get_purchase(db, user_id, purchase_id)
    assert row["amount_paid_cents"] == 5000
    assert row["remaining_cents"] == 15000
    assert row["status"] == "partially_paid"


def test_record_payment_full_marks_paid(db, user_id):
    purchase_id = repo.create_purchase(db, user_id, "Drill", "career_tool", 20000, "ordered")
    db.commit()
    repo.record_payment(db, user_id, purchase_id, 20000)
    db.commit()
    row = repo.get_purchase(db, user_id, purchase_id)
    assert row["remaining_cents"] == 0
    assert row["status"] == "paid"


def test_record_payment_accumulates_across_calls(db, user_id):
    purchase_id = repo.create_purchase(db, user_id, "Drill", "career_tool", 20000, "ordered")
    db.commit()
    repo.record_payment(db, user_id, purchase_id, 5000)
    db.commit()
    repo.record_payment(db, user_id, purchase_id, 5000)
    db.commit()
    row = repo.get_purchase(db, user_id, purchase_id)
    assert row["amount_paid_cents"] == 10000
    assert row["status"] == "partially_paid"


def test_record_payment_exceeding_total_raises_integrity_error(db, user_id):
    purchase_id = repo.create_purchase(db, user_id, "Drill", "career_tool", 20000, "ordered")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        repo.record_payment(db, user_id, purchase_id, 25000)


def test_record_payment_nonexistent_purchase_returns_none(db, user_id):
    assert repo.record_payment(db, user_id, 99999, 100) is None
