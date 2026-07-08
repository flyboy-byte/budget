from app.repositories import obligations


def test_create_and_get_obligation(db, user_id):
    obligation_id = obligations.create_obligation(db, user_id, "Rent", "housing", 90000, "2026-08-01")
    db.commit()
    row = obligations.get_obligation(db, user_id, obligation_id)
    assert row["name"] == "Rent"
    assert row["amount_cents"] == 90000
    assert row["is_paid"] == 0


def test_list_excludes_paid_by_default(db, user_id):
    unpaid_id = obligations.create_obligation(db, user_id, "Rent", "housing", 90000, "2026-08-01")
    paid_id = obligations.create_obligation(db, user_id, "Electric", "utility", 5000, "2026-07-01")
    db.commit()
    obligations.update_obligation(
        db, user_id, paid_id, "Electric", "utility", 5000, "2026-07-01", is_paid=1
    )
    db.commit()

    visible = obligations.list_obligations(db, user_id)
    assert [r["id"] for r in visible] == [unpaid_id]


def test_update_marks_paid(db, user_id):
    obligation_id = obligations.create_obligation(db, user_id, "Rent", "housing", 90000, "2026-08-01")
    db.commit()
    updated = obligations.update_obligation(
        db, user_id, obligation_id, "Rent", "housing", 90000, "2026-08-01", is_paid=1
    )
    db.commit()
    assert updated is True
    assert obligations.get_obligation(db, user_id, obligation_id)["is_paid"] == 1


def test_delete_obligation(db, user_id):
    obligation_id = obligations.create_obligation(db, user_id, "Rent", "housing", 90000, "2026-08-01")
    db.commit()
    assert obligations.delete_obligation(db, user_id, obligation_id) is True
    assert obligations.get_obligation(db, user_id, obligation_id) is None


def test_obligation_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    obligation_id = obligations.create_obligation(db, other_id, "Bob's Rent", "housing", 100, "2026-08-01")
    db.commit()
    assert obligations.get_obligation(db, user_id, obligation_id) is None
    assert obligations.delete_obligation(db, user_id, obligation_id) is False


def test_list_distinct_categories_dedupes_and_sorts(db, user_id):
    obligations.create_obligation(db, user_id, "Rent", "housing", 90000, "2026-08-01")
    obligations.create_obligation(db, user_id, "Electric", "utilities", 5000, "2026-08-01")
    obligations.create_obligation(db, user_id, "Water", "utilities", 3000, "2026-08-01")
    db.commit()
    assert obligations.list_distinct_categories(db, user_id) == ["housing", "utilities"]


def test_list_distinct_categories_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    obligations.create_obligation(db, other_id, "Bob's Rent", "bobs_category", 100, "2026-08-01")
    db.commit()
    assert obligations.list_distinct_categories(db, user_id) == []
