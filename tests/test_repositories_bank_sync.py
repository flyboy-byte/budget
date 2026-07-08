from app.repositories import accounts as accounts_repo
from app.repositories import bank_sync


def other_user(db):
    cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    return cur.lastrowid


def make_connection(db, user_id, label="Chase", access_url_encrypted=b"encrypted-blob"):
    connection_id = bank_sync.create_connection(db, user_id, label, access_url_encrypted)
    db.commit()
    return connection_id


# ----- bank_connections -----

def test_create_and_get_connection(db, user_id):
    connection_id = make_connection(db, user_id)
    row = bank_sync.get_connection_row(db, user_id, connection_id)
    assert row["label"] == "Chase"
    assert row["access_url_encrypted"] == b"encrypted-blob"
    assert row["status"] == "active"


def test_list_connections_scoped_to_owner(db, user_id):
    other_id = other_user(db)
    make_connection(db, user_id, label="Mine")
    make_connection(db, other_id, label="Bob's")
    assert [r["label"] for r in bank_sync.list_connections(db, user_id)] == ["Mine"]


def test_get_connection_scoped_to_owner(db, user_id):
    other_id = other_user(db)
    connection_id = make_connection(db, other_id)
    assert bank_sync.get_connection_row(db, user_id, connection_id) is None


def test_mark_synced_updates_timestamp_and_clears_error(db, user_id):
    connection_id = make_connection(db, user_id)
    bank_sync.mark_error(db, user_id, connection_id, "boom")
    db.commit()
    updated = bank_sync.mark_synced(db, user_id, connection_id, "2026-07-14T00:00:00.000Z")
    db.commit()
    assert updated is True
    row = bank_sync.get_connection_row(db, user_id, connection_id)
    assert row["last_synced_at"] == "2026-07-14T00:00:00.000Z"
    assert row["status"] == "active"
    assert row["last_error"] is None


def test_mark_error_sets_status(db, user_id):
    connection_id = make_connection(db, user_id)
    updated = bank_sync.mark_error(db, user_id, connection_id, "403 revoked")
    db.commit()
    assert updated is True
    row = bank_sync.get_connection_row(db, user_id, connection_id)
    assert row["status"] == "error"
    assert row["last_error"] == "403 revoked"


def test_mark_synced_scoped_to_owner_returns_false(db, user_id):
    other_id = other_user(db)
    connection_id = make_connection(db, other_id)
    updated = bank_sync.mark_synced(db, user_id, connection_id, "2026-07-14T00:00:00.000Z")
    db.commit()
    assert updated is False
    assert bank_sync.get_connection_row(db, other_id, connection_id)["last_synced_at"] is None


def test_delete_connection(db, user_id):
    connection_id = make_connection(db, user_id)
    deleted = bank_sync.delete_connection(db, user_id, connection_id)
    db.commit()
    assert deleted is True
    assert bank_sync.get_connection_row(db, user_id, connection_id) is None


def test_delete_connection_scoped_to_owner_returns_false(db, user_id):
    other_id = other_user(db)
    connection_id = make_connection(db, other_id)
    deleted = bank_sync.delete_connection(db, user_id, connection_id)
    db.commit()
    assert deleted is False
    assert bank_sync.get_connection_row(db, other_id, connection_id) is not None


# ----- bank_account_links -----

def test_upsert_link_creates_new_row(db, user_id):
    connection_id = make_connection(db, user_id)
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()
    row = bank_sync.get_link(db, user_id, link_id)
    assert row["sfin_account_id"] == "sfin-1"
    assert row["sfin_account_name"] == "Checking"
    assert row["account_id"] is None


def test_upsert_link_is_idempotent_and_updates_name(db, user_id):
    connection_id = make_connection(db, user_id)
    first_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()
    second_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking (renamed)")
    db.commit()
    assert first_id == second_id
    row = bank_sync.get_link(db, user_id, first_id)
    assert row["sfin_account_name"] == "Checking (renamed)"


def test_upsert_link_preserves_existing_account_mapping(db, user_id):
    connection_id = make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    db.commit()
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()
    bank_sync.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking (re-synced)")
    db.commit()

    row = bank_sync.get_link(db, user_id, link_id)
    assert row["account_id"] == account_id


def test_list_links_scoped_to_connection(db, user_id):
    connection_a = make_connection(db, user_id, label="A")
    connection_b = make_connection(db, user_id, label="B")
    bank_sync.upsert_link(db, user_id, connection_a, "sfin-a", "A Checking")
    bank_sync.upsert_link(db, user_id, connection_b, "sfin-b", "B Checking")
    db.commit()
    assert [r["sfin_account_id"] for r in bank_sync.list_links(db, user_id, connection_a)] == ["sfin-a"]


def test_set_link_target_scoped_to_owner_returns_false(db, user_id):
    other_id = other_user(db)
    connection_id = make_connection(db, other_id)
    link_id = bank_sync.upsert_link(db, other_id, connection_id, "sfin-1", "Checking")
    db.commit()
    updated = bank_sync.set_link_target(db, user_id, link_id, None, None)
    db.commit()
    assert updated is False


def test_set_link_target_debt_clears_account_and_vice_versa(db, user_id):
    from app.repositories import debts as debts_repo

    connection_id = make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    debt_id = debts_repo.create_debt(db, user_id, "Loan", "bank_loan", 50000, 1000, "accruing")
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Loan")
    db.commit()

    bank_sync.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()
    row = bank_sync.get_link(db, user_id, link_id)
    assert row["account_id"] == account_id
    assert row["debt_id"] is None

    bank_sync.set_link_target(db, user_id, link_id, "debt", debt_id)
    db.commit()
    row = bank_sync.get_link(db, user_id, link_id)
    assert row["account_id"] is None
    assert row["debt_id"] == debt_id

    bank_sync.set_link_target(db, user_id, link_id, None, None)
    db.commit()
    row = bank_sync.get_link(db, user_id, link_id)
    assert row["account_id"] is None
    assert row["debt_id"] is None


# ----- bank_sync_staging -----

def test_create_staging_row_and_list_unapplied(db, user_id):
    connection_id = make_connection(db, user_id)
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()
    staging_id = bank_sync.create_staging_row(db, user_id, link_id, 12345)
    db.commit()

    unapplied = bank_sync.list_unapplied_staging(db, user_id, connection_id)
    assert len(unapplied) == 1
    assert unapplied[0]["id"] == staging_id
    assert unapplied[0]["synced_balance_cents"] == 12345


def test_create_staging_row_stores_balance_date(db, user_id):
    connection_id = make_connection(db, user_id)
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()
    bank_sync.create_staging_row(db, user_id, link_id, 12345, 1751990400)
    db.commit()

    unapplied = bank_sync.list_unapplied_staging(db, user_id, connection_id)
    assert unapplied[0]["balance_date"] == 1751990400


def test_count_unapplied_staging_sums_across_connections(db, user_id):
    connection_a = make_connection(db, user_id, label="A")
    connection_b = make_connection(db, user_id, label="B")
    link_a = bank_sync.upsert_link(db, user_id, connection_a, "sfin-a", "A")
    link_b = bank_sync.upsert_link(db, user_id, connection_b, "sfin-b", "B")
    db.commit()

    assert bank_sync.count_unapplied_staging(db, user_id) == 0

    staging_a = bank_sync.create_staging_row(db, user_id, link_a, 100)
    bank_sync.create_staging_row(db, user_id, link_b, 200)
    db.commit()

    assert bank_sync.count_unapplied_staging(db, user_id) == 2

    bank_sync.mark_staging_applied(db, user_id, staging_a)
    db.commit()
    assert bank_sync.count_unapplied_staging(db, user_id) == 1


def test_count_unapplied_staging_scoped_to_owner(db, user_id):
    other_id = other_user(db)
    connection_id = make_connection(db, other_id)
    link_id = bank_sync.upsert_link(db, other_id, connection_id, "sfin-1", "Checking")
    db.commit()
    bank_sync.create_staging_row(db, other_id, link_id, 100)
    db.commit()

    assert bank_sync.count_unapplied_staging(db, user_id) == 0


def test_create_staging_row_replaces_pending_row_for_same_link(db, user_id):
    """Re-syncing before reviewing/applying should update the pending balance in
    place, not pile up a duplicate row per sync for the same account."""
    connection_id = make_connection(db, user_id)
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()
    bank_sync.create_staging_row(db, user_id, link_id, 100)
    db.commit()
    second_id = bank_sync.create_staging_row(db, user_id, link_id, 200)
    db.commit()

    unapplied = bank_sync.list_unapplied_staging(db, user_id, connection_id)
    assert len(unapplied) == 1
    assert unapplied[0]["id"] == second_id
    assert unapplied[0]["synced_balance_cents"] == 200


def test_create_staging_row_does_not_touch_already_applied_rows(db, user_id):
    connection_id = make_connection(db, user_id)
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()
    applied_id = bank_sync.create_staging_row(db, user_id, link_id, 100)
    db.commit()
    bank_sync.mark_staging_applied(db, user_id, applied_id)
    db.commit()

    bank_sync.create_staging_row(db, user_id, link_id, 200)
    db.commit()

    all_rows = db.execute("SELECT id FROM bank_sync_staging").fetchall()
    assert len(all_rows) == 2


def test_mark_staging_applied_excludes_from_unapplied_list(db, user_id):
    connection_id = make_connection(db, user_id)
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()
    staging_id = bank_sync.create_staging_row(db, user_id, link_id, 12345)
    db.commit()

    updated = bank_sync.mark_staging_applied(db, user_id, staging_id)
    db.commit()
    assert updated is True
    assert bank_sync.list_unapplied_staging(db, user_id, connection_id) == []


def test_mark_staging_applied_scoped_to_owner_returns_false(db, user_id):
    other_id = other_user(db)
    connection_id = make_connection(db, other_id)
    link_id = bank_sync.upsert_link(db, other_id, connection_id, "sfin-1", "Checking")
    db.commit()
    staging_id = bank_sync.create_staging_row(db, other_id, link_id, 12345)
    db.commit()

    updated = bank_sync.mark_staging_applied(db, user_id, staging_id)
    db.commit()
    assert updated is False
    assert len(bank_sync.list_unapplied_staging(db, other_id, connection_id)) == 1


# ----- bank_transaction_staging -----

def _txn(sfin_id="t1", posted_date="2026-07-15", amount_cents=-1000, description="Store", pending=False):
    return {
        "sfin_transaction_id": sfin_id,
        "posted_date": posted_date,
        "amount_cents": amount_cents,
        "description": description,
        "pending": pending,
    }


def test_create_transaction_staging_rows_and_list_unmatched(db, user_id):
    connection_id = make_connection(db, user_id)
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()

    bank_sync.create_transaction_staging_rows(db, user_id, link_id, [_txn()])
    db.commit()

    unmatched = bank_sync.list_unmatched_transactions(db, user_id)
    assert len(unmatched) == 1
    assert unmatched[0]["sfin_transaction_id"] == "t1"
    assert unmatched[0]["status"] == "unmatched"


def test_create_transaction_staging_rows_returns_only_newly_inserted(db, user_id):
    connection_id = make_connection(db, user_id)
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()

    first = bank_sync.create_transaction_staging_rows(db, user_id, link_id, [_txn(sfin_id="t1")])
    db.commit()
    assert [t["sfin_transaction_id"] for t in first] == ["t1"]

    # Re-fetching the same transaction (refreshed amount) alongside a genuinely new one:
    # only the new one should come back.
    second = bank_sync.create_transaction_staging_rows(
        db, user_id, link_id, [_txn(sfin_id="t1", amount_cents=-1050), _txn(sfin_id="t2")]
    )
    db.commit()
    assert [t["sfin_transaction_id"] for t in second] == ["t2"]


def test_create_transaction_staging_rows_upserts_without_duplicating(db, user_id):
    connection_id = make_connection(db, user_id)
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()

    bank_sync.create_transaction_staging_rows(db, user_id, link_id, [_txn(amount_cents=-1000, pending=True)])
    db.commit()
    bank_sync.create_transaction_staging_rows(db, user_id, link_id, [_txn(amount_cents=-1050, pending=False)])
    db.commit()

    unmatched = bank_sync.list_unmatched_transactions(db, user_id)
    assert len(unmatched) == 1
    assert unmatched[0]["amount_cents"] == -1050
    assert unmatched[0]["pending"] == 0


def test_create_transaction_staging_rows_does_not_reset_matched_status(db, user_id):
    connection_id = make_connection(db, user_id)
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()
    bank_sync.create_transaction_staging_rows(db, user_id, link_id, [_txn()])
    db.commit()
    staging = bank_sync.list_unmatched_transactions(db, user_id)[0]

    obligation_id = db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Rent', 'housing', 1000, '2026-08-01')",
        (user_id,),
    ).lastrowid
    ledger_id = db.execute(
        "INSERT INTO transactions (user_id, transaction_date, amount_cents, target_type, obligation_id) VALUES (?, '2026-07-15', 1000, 'obligation', ?)",
        (user_id, obligation_id),
    ).lastrowid
    db.commit()
    bank_sync.mark_transaction_matched(
        db, user_id, staging["id"], ledger_transaction_id=ledger_id, obligation_id=obligation_id
    )
    db.commit()

    # Re-sync fetches the same transaction again — it must not come back as unmatched.
    bank_sync.create_transaction_staging_rows(db, user_id, link_id, [_txn(amount_cents=-1099)])
    db.commit()

    assert bank_sync.list_unmatched_transactions(db, user_id) == []
    row = bank_sync.get_transaction_staging(db, user_id, staging["id"])
    assert row["status"] == "matched"
    assert row["amount_cents"] == -1099  # amount still refreshed


def test_dismiss_transaction(db, user_id):
    connection_id = make_connection(db, user_id)
    link_id = bank_sync.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()
    bank_sync.create_transaction_staging_rows(db, user_id, link_id, [_txn()])
    db.commit()
    staging = bank_sync.list_unmatched_transactions(db, user_id)[0]

    dismissed = bank_sync.dismiss_transaction(db, user_id, staging["id"])
    db.commit()
    assert dismissed is True
    assert bank_sync.list_unmatched_transactions(db, user_id) == []


def test_dismiss_transaction_scoped_to_owner_returns_false(db, user_id):
    other_id = other_user(db)
    connection_id = make_connection(db, other_id)
    link_id = bank_sync.upsert_link(db, other_id, connection_id, "sfin-1", "Checking")
    db.commit()
    bank_sync.create_transaction_staging_rows(db, other_id, link_id, [_txn()])
    db.commit()
    staging = bank_sync.list_unmatched_transactions(db, other_id)[0]

    dismissed = bank_sync.dismiss_transaction(db, user_id, staging["id"])
    db.commit()
    assert dismissed is False


def test_list_unmatched_transactions_scoped_to_owner(db, user_id):
    other_id = other_user(db)
    connection_id = make_connection(db, other_id)
    link_id = bank_sync.upsert_link(db, other_id, connection_id, "sfin-1", "Checking")
    db.commit()
    bank_sync.create_transaction_staging_rows(db, other_id, link_id, [_txn()])
    db.commit()

    assert bank_sync.list_unmatched_transactions(db, user_id) == []
