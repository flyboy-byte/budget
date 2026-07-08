from app.repositories import debts


def other_user(db):
    cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    return cur.lastrowid


def test_create_and_get_debt(db, user_id):
    debt_id = debts.create_debt(
        db, user_id, "Visa", "credit_card", 100000, 3000, "accruing", apr_bps=2499
    )
    db.commit()
    row = debts.get_debt(db, user_id, debt_id)
    assert row["name"] == "Visa"
    assert row["balance_cents"] == 100000
    assert row["apr_bps"] == 2499
    assert row["is_active"] == 1


def test_list_debts_excludes_inactive_by_default(db, user_id):
    active_id = debts.create_debt(db, user_id, "Visa", "credit_card", 100, 10, "accruing")
    paid_off_id = debts.create_debt(db, user_id, "Old Loan", "bank_loan", 0, 0, "not_accruing")
    db.commit()
    debts.update_debt(
        db, user_id, paid_off_id, "Old Loan", "bank_loan", 0, 0, "not_accruing", is_active=0
    )
    db.commit()

    visible = debts.list_debts(db, user_id)
    assert [r["id"] for r in visible] == [active_id]


def test_update_debt(db, user_id):
    debt_id = debts.create_debt(db, user_id, "Visa", "credit_card", 100000, 3000, "accruing")
    db.commit()
    updated = debts.update_debt(
        db,
        user_id,
        debt_id,
        "Visa Renamed",
        "credit_card",
        50000,
        1500,
        "not_accruing",
        is_active=1,
        apr_bps=None,
        is_flexible_payment=1,
    )
    db.commit()
    assert updated is True
    row = debts.get_debt(db, user_id, debt_id)
    assert row["balance_cents"] == 50000
    assert row["interest_status"] == "not_accruing"
    assert row["is_flexible_payment"] == 1


def test_delete_debt(db, user_id):
    debt_id = debts.create_debt(db, user_id, "Visa", "credit_card", 100, 10, "accruing")
    db.commit()
    assert debts.delete_debt(db, user_id, debt_id) is True
    assert debts.get_debt(db, user_id, debt_id) is None


def test_debt_scoped_to_owner(db, user_id):
    other_id = other_user(db)
    debt_id = debts.create_debt(db, other_id, "Bob's Card", "credit_card", 100, 10, "accruing")
    db.commit()
    assert debts.get_debt(db, user_id, debt_id) is None
    assert debts.delete_debt(db, user_id, debt_id) is False


def test_update_balance_changes_only_balance(db, user_id):
    debt_id = debts.create_debt(db, user_id, "Visa", "credit_card", 100, 10, "accruing")
    db.commit()
    updated = debts.update_balance(db, user_id, debt_id, 5000)
    db.commit()
    assert updated is True
    row = debts.get_debt(db, user_id, debt_id)
    assert row["balance_cents"] == 5000
    assert row["name"] == "Visa"


def test_update_balance_scoped_to_owner_returns_false(db, user_id):
    other_id = other_user(db)
    debt_id = debts.create_debt(db, other_id, "Bob's Card", "credit_card", 100, 10, "accruing")
    db.commit()
    updated = debts.update_balance(db, user_id, debt_id, 9999)
    db.commit()
    assert updated is False
    assert debts.get_debt(db, other_id, debt_id)["balance_cents"] == 100


def test_create_debt_accepts_arbitrary_free_text_type(db, user_id):
    # type has no DB-level allowlist anymore (migration 0007_free_text_types.sql) --
    # this is the actual behavior change being tested, not just a smoke test.
    debt_id = debts.create_debt(db, user_id, "Family Loan", "family loan from mom", 500, 0, "not_accruing")
    db.commit()
    row = debts.get_debt(db, user_id, debt_id)
    assert row["type"] == "family loan from mom"


def test_list_distinct_types_returns_seed_values_for_new_user(db, user_id):
    types = debts.list_distinct_types(db, user_id)
    assert set(debts.SEED_TYPES).issubset(set(types))


def test_list_distinct_types_includes_custom_value_once_used(db, user_id):
    debts.create_debt(db, user_id, "Weird One", "a very custom type", 100, 10, "accruing")
    db.commit()
    types = debts.list_distinct_types(db, user_id)
    assert "a very custom type" in types
    assert set(debts.SEED_TYPES).issubset(set(types))


def test_list_distinct_types_scoped_to_owner(db, user_id):
    other_id = other_user(db)
    debts.create_debt(db, other_id, "Bob's Weird Debt", "bobs-only-type", 100, 10, "accruing")
    db.commit()
    assert "bobs-only-type" not in debts.list_distinct_types(db, user_id)
    assert "bobs-only-type" in debts.list_distinct_types(db, other_id)
