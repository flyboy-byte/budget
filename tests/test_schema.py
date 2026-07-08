import os
import pathlib
import sqlite3

import pytest


def make_account(db, user_id, **overrides):
    fields = {"name": "Checking", "type": "checking", "balance_cents": 10000}
    fields.update(overrides)
    cur = db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, ?, ?, ?)",
        (user_id, fields["name"], fields["type"], fields["balance_cents"]),
    )
    db.commit()
    return cur.lastrowid


def test_committed_purchase_remaining_cents_is_generated(db, user_id):
    db.execute(
        """INSERT INTO committed_purchases
           (user_id, name, category, amount_cents, amount_paid_cents, status)
           VALUES (?, 'Drill', 'career_tool', 20000, 5000, 'partially_paid')""",
        (user_id,),
    )
    db.commit()
    row = db.execute("SELECT remaining_cents FROM committed_purchases").fetchone()
    assert row["remaining_cents"] == 15000


def test_committed_purchase_paid_cannot_exceed_amount(db, user_id):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO committed_purchases
               (user_id, name, category, amount_cents, amount_paid_cents, status)
               VALUES (?, 'Drill', 'career_tool', 100, 200, 'partially_paid')""",
            (user_id,),
        )


def test_debts_and_accounts_type_accepts_arbitrary_free_text(db, user_id):
    # migration 0007_free_text_types.sql dropped the CHECK that used to restrict
    # these columns to a fixed enum -- confirms it's actually gone, not just that
    # the Python layer happens not to validate it.
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Vault', 'under the mattress', 0)",
        (user_id,),
    )
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, interest_status, minimum_payment_cents)
           VALUES (?, 'Family Loan', 'a totally made up type', 0, 'not_accruing', 0)""",
        (user_id,),
    )
    db.commit()


def test_migration_0007_remaps_legacy_klarna_bnpl_and_preserves_fk_links():
    from migrations.runner import MIGRATIONS_DIR

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    pre_0007 = [m for m in migrations if m.stem < "0007_free_text_types"]
    migration_0007 = next(m for m in migrations if m.stem == "0007_free_text_types")

    for m in pre_0007:
        conn.executescript(m.read_text())
    conn.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'alice', 'x')")
    conn.execute(
        """INSERT INTO debts (id, user_id, name, type, balance_cents, interest_status, minimum_payment_cents)
           VALUES (1, 1, 'Card', 'klarna_bnpl', 5000, 'accruing', 100)"""
    )
    conn.execute(
        "INSERT INTO bank_connections (user_id, label, access_url_encrypted, status) VALUES (1, 'Test', X'00', 'active')"
    )
    conn.execute(
        """INSERT INTO bank_account_links (user_id, bank_connection_id, sfin_account_id, sfin_account_name, debt_id)
           VALUES (1, 1, 'sfin-1', 'Card', 1)"""
    )
    conn.commit()

    conn.executescript(migration_0007.read_text())
    conn.commit()

    assert conn.execute("SELECT type FROM debts WHERE id = 1").fetchone()["type"] == "buy_now_pay_later"
    assert conn.execute("SELECT debt_id FROM bank_account_links WHERE id = 1").fetchone()["debt_id"] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_transaction_target_type_must_match_populated_fk(db, user_id):
    account_id = make_account(db, user_id)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO transactions
               (user_id, transaction_date, amount_cents, account_id, target_type, debt_id)
               VALUES (?, '2026-01-01', 500, ?, 'obligation', NULL)""",
            (user_id, account_id),
        )


def test_transaction_other_requires_all_fks_null(db, user_id):
    account_id = make_account(db, user_id)
    db.execute(
        """INSERT INTO transactions
           (user_id, transaction_date, amount_cents, account_id, target_type)
           VALUES (?, '2026-01-01', 500, ?, 'other')""",
        (user_id, account_id),
    )
    db.commit()
    assert db.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == 1


def test_deleting_user_cascades_to_domain_rows(db, user_id):
    make_account(db, user_id)
    db.execute(
        "INSERT INTO settings (user_id, key, value) VALUES (?, 'timezone', 'UTC')",
        (user_id,),
    )
    db.execute(
        "INSERT INTO bank_connections (user_id, label, access_url_encrypted) VALUES (?, 'Chase', X'00')",
        (user_id,),
    )
    db.commit()

    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()

    assert db.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"] == 0
    assert db.execute("SELECT COUNT(*) c FROM settings").fetchone()["c"] == 0
    assert db.execute("SELECT COUNT(*) c FROM bank_connections").fetchone()["c"] == 0


def test_two_users_data_is_isolated(db, user_id):
    other_cur = db.execute(
        "INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')"
    )
    db.commit()
    other_user_id = other_cur.lastrowid

    make_account(db, user_id, name="Alice Checking")
    make_account(db, other_user_id, name="Bob Checking")

    alice_accounts = db.execute(
        "SELECT name FROM accounts WHERE user_id = ?", (user_id,)
    ).fetchall()
    assert [r["name"] for r in alice_accounts] == ["Alice Checking"]

    bob_accounts = db.execute(
        "SELECT name FROM accounts WHERE user_id = ?", (other_user_id,)
    ).fetchall()
    assert [r["name"] for r in bob_accounts] == ["Bob Checking"]


def test_snapshot_unique_per_user_per_date(db, user_id):
    snapshot_fields = (
        user_id, "2026-01-01", 0, 0, 0, 0, 0, 0, 0, 30,
    )
    db.execute(
        """INSERT INTO snapshots
           (user_id, snapshot_date, cash_on_hand_cents, total_debt_cents, net_position_cents,
            reserved_cash_cents, safe_to_spend_cents, forecast_position_cents, protected_floor_cents, window_days)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        snapshot_fields,
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO snapshots
               (user_id, snapshot_date, cash_on_hand_cents, total_debt_cents, net_position_cents,
                reserved_cash_cents, safe_to_spend_cents, forecast_position_cents, protected_floor_cents, window_days)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            snapshot_fields,
        )


def test_debt_balance_cannot_be_negative(db, user_id):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO debts
               (user_id, name, type, balance_cents, interest_status)
               VALUES (?, 'Card', 'credit_card', -100, 'accruing')""",
            (user_id,),
        )


def test_bank_connection_status_must_be_valid(db, user_id):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO bank_connections (user_id, label, access_url_encrypted, status)
               VALUES (?, 'Chase', X'00', 'bogus')""",
            (user_id,),
        )


def test_bank_account_link_unique_per_connection_and_sfin_id(db, user_id):
    conn_cur = db.execute(
        "INSERT INTO bank_connections (user_id, label, access_url_encrypted) VALUES (?, 'Chase', X'00')",
        (user_id,),
    )
    db.commit()
    connection_id = conn_cur.lastrowid
    db.execute(
        """INSERT INTO bank_account_links (user_id, bank_connection_id, sfin_account_id, sfin_account_name)
           VALUES (?, ?, 'sfin-1', 'Checking')""",
        (user_id, connection_id),
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO bank_account_links (user_id, bank_connection_id, sfin_account_id, sfin_account_name)
               VALUES (?, ?, 'sfin-1', 'Checking (dup)')""",
            (user_id, connection_id),
        )


def test_deleting_bank_connection_cascades_to_links_and_staging(db, user_id):
    conn_cur = db.execute(
        "INSERT INTO bank_connections (user_id, label, access_url_encrypted) VALUES (?, 'Chase', X'00')",
        (user_id,),
    )
    db.commit()
    connection_id = conn_cur.lastrowid
    link_cur = db.execute(
        """INSERT INTO bank_account_links (user_id, bank_connection_id, sfin_account_id, sfin_account_name)
           VALUES (?, ?, 'sfin-1', 'Checking')""",
        (user_id, connection_id),
    )
    db.commit()
    link_id = link_cur.lastrowid
    db.execute(
        "INSERT INTO bank_sync_staging (user_id, bank_account_link_id, synced_balance_cents) VALUES (?, ?, 5000)",
        (user_id, link_id),
    )
    db.commit()

    db.execute("DELETE FROM bank_connections WHERE id = ?", (connection_id,))
    db.commit()

    assert db.execute("SELECT COUNT(*) c FROM bank_account_links").fetchone()["c"] == 0
    assert db.execute("SELECT COUNT(*) c FROM bank_sync_staging").fetchone()["c"] == 0


def test_users_table_has_is_admin_column(db):
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    assert "is_admin" in columns


def test_is_admin_defaults_to_zero(db, user_id):
    row = db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    assert row["is_admin"] == 0


def test_bank_account_links_has_debt_id_column(db):
    columns = {row[1] for row in db.execute("PRAGMA table_info(bank_account_links)").fetchall()}
    assert "debt_id" in columns


def test_bank_transaction_staging_table_exists(db):
    columns = {row[1] for row in db.execute("PRAGMA table_info(bank_transaction_staging)").fetchall()}
    assert columns == {
        "id", "user_id", "bank_account_link_id", "sfin_transaction_id", "posted_date",
        "amount_cents", "description", "pending", "status", "matched_obligation_id",
        "matched_committed_purchase_id", "ledger_transaction_id", "fetched_at",
    }


# ----- migration 0010_spending_transactions.sql -----

def test_transactions_accepts_the_spending_target_type(db, user_id):
    db.execute(
        """INSERT INTO transactions (user_id, transaction_date, amount_cents, target_type, category, memo)
           VALUES (?, '2026-08-17', 1274, 'spending', 'gas', 'ALLSUP')""",
        (user_id,),
    )
    db.commit()
    row = db.execute("SELECT * FROM transactions").fetchone()
    assert row["target_type"] == "spending"
    assert row["category"] == "gas"


def test_spending_transaction_cannot_carry_a_commitment_fk(db, user_id):
    """'spending' means "not tied to a commitment" -- the CHECK has to enforce that,
    or the ledger could claim a coffee run paid off a bill."""
    obligation_id = db.execute(
        """INSERT INTO obligations (user_id, name, category, amount_cents, due_date)
           VALUES (?, 'Rent', 'housing', 90000, '2026-09-01')""",
        (user_id,),
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO transactions
               (user_id, transaction_date, amount_cents, target_type, obligation_id)
               VALUES (?, '2026-08-17', 1274, 'spending', ?)""",
            (user_id, obligation_id),
        )


def test_transactions_still_rejects_an_unknown_target_type(db, user_id):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO transactions (user_id, transaction_date, amount_cents, target_type)
               VALUES (?, '2026-08-17', 100, 'nonsense')""",
            (user_id,),
        )


def test_migration_0010_preserves_existing_rows_without_shifting_columns(tmp_path):
    """0010 rebuilds `transactions` and inserts `category` *before* `memo`. A
    positional `INSERT ... SELECT *` would have landed memo text in category and
    created_at in memo, silently corrupting every historical row -- so this walks a
    pre-0010 database through the real runner and checks each field individually."""
    import subprocess
    import sys

    db_path = tmp_path / "pre0010.db"

    # Build the schema as it stood at 0009, then seed a row of every target type.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for name in (
        "0001_initial", "0002_bank_sync", "0003_admin_and_debt_links",
        "0004_staging_balance_date", "0005_bank_transaction_staging", "0006_totp",
        "0007_free_text_types", "0008_push_subscriptions", "0009_obligation_push_reminders",
    ):
        conn.executescript((pathlib.Path("migrations") / f"{name}.sql").read_text())
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (name,))
    user_id = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES ('alice', 'hash')"
    ).lastrowid
    obligation_id = conn.execute(
        """INSERT INTO obligations (user_id, name, category, amount_cents, due_date)
           VALUES (?, 'Rent', 'housing', 90000, '2026-09-01')""",
        (user_id,),
    ).lastrowid
    conn.execute(
        """INSERT INTO transactions
           (user_id, transaction_date, amount_cents, target_type, obligation_id, memo)
           VALUES (?, '2026-08-01', 90000, 'obligation', ?, 'Landlord LLC')""",
        (user_id, obligation_id),
    )
    conn.execute(
        """INSERT INTO transactions (user_id, transaction_date, amount_cents, target_type, memo)
           VALUES (?, '2026-08-02', 250000, 'other', 'Income: Paycheck')""",
        (user_id,),
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [sys.executable, "-m", "migrations.runner"],
        env={**os.environ, "BUDGET_DB_PATH": str(db_path)},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM transactions ORDER BY transaction_date").fetchall()

    assert len(rows) == 2
    bill, income = rows
    assert bill["target_type"] == "obligation"
    assert bill["obligation_id"] == obligation_id
    assert bill["amount_cents"] == 90000
    assert bill["memo"] == "Landlord LLC"      # memo did NOT slide into category
    assert bill["category"] is None
    assert income["target_type"] == "other"
    assert income["memo"] == "Income: Paycheck"
    assert income["category"] is None
    # FKs survive the drop/rename dance.
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
