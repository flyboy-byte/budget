from app.repositories import income_events as repo


def test_create_and_get_income_event(db, user_id):
    event_id = repo.create_income_event(db, user_id, "Paycheck", 500000, "2026-08-01", "confirmed")
    db.commit()
    row = repo.get_income_event(db, user_id, event_id)
    assert row["source"] == "Paycheck"
    assert row["expected_amount_cents"] == 500000
    assert row["is_received"] == 0


def test_list_excludes_received_by_default(db, user_id):
    unreceived_id = repo.create_income_event(db, user_id, "Paycheck", 500000, "2026-08-01", "confirmed")
    received_id = repo.create_income_event(db, user_id, "Old Paycheck", 500000, "2026-07-01", "confirmed")
    db.commit()
    repo.update_income_event(
        db, user_id, received_id, "Old Paycheck", 500000, "2026-07-01", "confirmed", is_received=1
    )
    db.commit()

    visible = repo.list_income_events(db, user_id)
    assert [r["id"] for r in visible] == [unreceived_id]


def test_update_marks_received(db, user_id):
    event_id = repo.create_income_event(db, user_id, "Paycheck", 500000, "2026-08-01", "confirmed")
    db.commit()
    updated = repo.update_income_event(
        db,
        user_id,
        event_id,
        "Paycheck",
        500000,
        "2026-08-01",
        "confirmed",
        is_received=1,
        received_date="2026-08-01",
        received_amount_cents=500000,
    )
    db.commit()
    assert updated is True
    row = repo.get_income_event(db, user_id, event_id)
    assert row["is_received"] == 1
    assert row["received_amount_cents"] == 500000


def test_delete_income_event(db, user_id):
    event_id = repo.create_income_event(db, user_id, "Paycheck", 500000, "2026-08-01", "confirmed")
    db.commit()
    assert repo.delete_income_event(db, user_id, event_id) is True
    assert repo.get_income_event(db, user_id, event_id) is None


def test_income_event_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    event_id = repo.create_income_event(db, other_id, "Bob's Paycheck", 100, "2026-08-01", "confirmed")
    db.commit()
    assert repo.get_income_event(db, user_id, event_id) is None
    assert repo.delete_income_event(db, user_id, event_id) is False
