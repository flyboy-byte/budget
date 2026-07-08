from app.repositories import accounts


def other_user(db):
    cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    return cur.lastrowid


def test_create_and_get_account(db, user_id):
    account_id = accounts.create_account(db, user_id, "Checking", "checking", 10000)
    db.commit()
    row = accounts.get_account(db, user_id, account_id)
    assert row["name"] == "Checking"
    assert row["balance_cents"] == 10000
    assert row["is_active"] == 1


def test_list_accounts_excludes_inactive_by_default(db, user_id):
    active_id = accounts.create_account(db, user_id, "Checking", "checking", 100)
    inactive_id = accounts.create_account(db, user_id, "Old", "checking", 0)
    db.commit()
    accounts.update_account(db, user_id, inactive_id, "Old", "checking", 0, is_active=0)
    db.commit()

    visible = accounts.list_accounts(db, user_id)
    assert [r["id"] for r in visible] == [active_id]

    all_accounts = accounts.list_accounts(db, user_id, include_inactive=True)
    assert {r["id"] for r in all_accounts} == {active_id, inactive_id}


def test_update_account_changes_fields(db, user_id):
    account_id = accounts.create_account(db, user_id, "Checking", "checking", 100)
    db.commit()
    updated = accounts.update_account(
        db, user_id, account_id, "Renamed", "savings", 500, is_active=1, notes="moved"
    )
    db.commit()
    assert updated is True
    row = accounts.get_account(db, user_id, account_id)
    assert row["name"] == "Renamed"
    assert row["type"] == "savings"
    assert row["balance_cents"] == 500
    assert row["notes"] == "moved"


def test_delete_account(db, user_id):
    account_id = accounts.create_account(db, user_id, "Checking", "checking", 100)
    db.commit()
    deleted = accounts.delete_account(db, user_id, account_id)
    db.commit()
    assert deleted is True
    assert accounts.get_account(db, user_id, account_id) is None


def test_get_account_scoped_to_owner(db, user_id):
    other_id = other_user(db)
    account_id = accounts.create_account(db, other_id, "Bob's Checking", "checking", 100)
    db.commit()
    assert accounts.get_account(db, user_id, account_id) is None


def test_update_account_scoped_to_owner_returns_false(db, user_id):
    other_id = other_user(db)
    account_id = accounts.create_account(db, other_id, "Bob's Checking", "checking", 100)
    db.commit()

    updated = accounts.update_account(
        db, user_id, account_id, "Hacked", "savings", 0, is_active=1
    )
    db.commit()
    assert updated is False
    assert accounts.get_account(db, other_id, account_id)["name"] == "Bob's Checking"


def test_delete_account_scoped_to_owner_returns_false(db, user_id):
    other_id = other_user(db)
    account_id = accounts.create_account(db, other_id, "Bob's Checking", "checking", 100)
    db.commit()

    deleted = accounts.delete_account(db, user_id, account_id)
    db.commit()
    assert deleted is False
    assert accounts.get_account(db, other_id, account_id) is not None


def test_update_balance_changes_only_balance(db, user_id):
    account_id = accounts.create_account(db, user_id, "Checking", "checking", 100, notes="original")
    db.commit()
    updated = accounts.update_balance(db, user_id, account_id, 5000)
    db.commit()
    assert updated is True
    row = accounts.get_account(db, user_id, account_id)
    assert row["balance_cents"] == 5000
    assert row["name"] == "Checking"
    assert row["notes"] == "original"


def test_update_balance_scoped_to_owner_returns_false(db, user_id):
    other_id = other_user(db)
    account_id = accounts.create_account(db, other_id, "Bob's Checking", "checking", 100)
    db.commit()
    updated = accounts.update_balance(db, user_id, account_id, 9999)
    db.commit()
    assert updated is False
    assert accounts.get_account(db, other_id, account_id)["balance_cents"] == 100


def test_create_account_accepts_arbitrary_free_text_type(db, user_id):
    # type has no DB-level allowlist anymore (migration 0007_free_text_types.sql).
    account_id = accounts.create_account(db, user_id, "Vault", "under the mattress", 100)
    db.commit()
    row = accounts.get_account(db, user_id, account_id)
    assert row["type"] == "under the mattress"


def test_list_distinct_types_returns_seed_values_for_new_user(db, user_id):
    types = accounts.list_distinct_types(db, user_id)
    assert set(accounts.SEED_TYPES).issubset(set(types))


def test_list_distinct_types_includes_custom_value_once_used(db, user_id):
    accounts.create_account(db, user_id, "Vault", "under the mattress", 100)
    db.commit()
    types = accounts.list_distinct_types(db, user_id)
    assert "under the mattress" in types
