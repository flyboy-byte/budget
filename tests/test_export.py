import csv
import io

import pytest

from app.services import export


def seed_data(db, user_id):
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Checking', 'checking', 10000)",
        (user_id,),
    )
    db.execute(
        """INSERT INTO debts (user_id, name, type, balance_cents, minimum_payment_cents, interest_status)
           VALUES (?, 'Visa', 'credit_card', 5000, 100, 'accruing')""",
        (user_id,),
    )
    db.execute(
        "INSERT INTO obligations (user_id, name, category, amount_cents, due_date) VALUES (?, 'Rent', 'housing', 90000, '2026-08-01')",
        (user_id,),
    )
    db.execute(
        """INSERT INTO committed_purchases (user_id, name, category, amount_cents, amount_paid_cents, status)
           VALUES (?, 'Drill', 'career_tool', 20000, 5000, 'partially_paid')""",
        (user_id,),
    )
    db.execute(
        "INSERT INTO income_events (user_id, source, expected_amount_cents, expected_date, confidence) VALUES (?, 'Paycheck', 500000, '2026-08-01', 'confirmed')",
        (user_id,),
    )
    db.execute(
        "INSERT INTO settings (user_id, key, value) VALUES (?, 'timezone', 'America/Denver')", (user_id,)
    )
    db.commit()


def test_export_table_csv_has_header_and_rows(db, user_id):
    seed_data(db, user_id)
    csv_text = export.export_table_csv(db, user_id, "accounts")
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert rows[0] == export.TABLE_COLUMNS["accounts"]
    assert len(rows) == 2
    assert "Checking" in rows[1]


def test_export_table_csv_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    seed_data(db, user_id)
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Bobs Account', 'checking', 1)",
        (other_id,),
    )
    db.commit()

    csv_text = export.export_table_csv(db, user_id, "accounts")
    assert "Bobs Account" not in csv_text


def test_export_combined_csv_has_table_column_and_all_rows(db, user_id):
    seed_data(db, user_id)
    csv_text = export.export_combined_csv(db, user_id)
    rows = list(csv.reader(io.StringIO(csv_text)))
    header = rows[0]
    assert header[0] == "table"
    # every per-table column shows up somewhere in the union header
    for table in export.CSV_TABLES:
        for column in export.TABLE_COLUMNS[table]:
            assert column in header

    data_rows = rows[1:]
    tables_seen = {row[0] for row in data_rows}
    assert tables_seen == {"accounts", "debts", "obligations", "committed_purchases", "income_events"}
    # a row from one table leaves other tables' columns blank
    accounts_row = next(dict(zip(header, row)) for row in data_rows if row[0] == "accounts")
    assert accounts_row["name"] == "Checking"
    assert accounts_row["category"] == ""


def test_export_combined_csv_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    seed_data(db, user_id)
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Bobs Account', 'checking', 1)",
        (other_id,),
    )
    db.commit()

    csv_text = export.export_combined_csv(db, user_id)
    assert "Bobs Account" not in csv_text


def test_export_json_backup_contains_all_tables(db, user_id):
    seed_data(db, user_id)
    backup = export.export_json_backup(db, user_id)
    assert backup["version"] == export.BACKUP_VERSION
    assert set(backup["tables"].keys()) == set(export.JSON_TABLES)
    assert len(backup["tables"]["accounts"]) == 1
    assert backup["tables"]["accounts"][0]["name"] == "Checking"
    assert len(backup["tables"]["settings"]) == 1


def test_restore_replaces_users_data(db, user_id):
    seed_data(db, user_id)
    backup = export.export_json_backup(db, user_id)

    db.execute("DELETE FROM accounts WHERE user_id = ?", (user_id,))
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Wrong', 'checking', 1)",
        (user_id,),
    )
    db.commit()

    export.restore_json_backup(db, user_id, backup)
    db.commit()

    accounts = db.execute("SELECT name FROM accounts WHERE user_id = ?", (user_id,)).fetchall()
    assert [r["name"] for r in accounts] == ["Checking"]


def test_restore_remaps_transaction_foreign_keys_to_new_ids(db, user_id):
    # IDs are a single global AUTOINCREMENT sequence shared across all users, so restore
    # must never blindly reuse a backed-up id (it could collide with another user's live
    # row) — it assigns fresh ids and remaps transactions' FKs to match.
    seed_data(db, user_id)
    account_id = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["id"]
    db.execute(
        """INSERT INTO transactions (user_id, transaction_date, amount_cents, account_id, target_type)
           VALUES (?, '2026-07-01', 100, ?, 'other')""",
        (user_id, account_id),
    )
    db.commit()

    backup = export.export_json_backup(db, user_id)
    export.restore_json_backup(db, user_id, backup)
    db.commit()

    tx = db.execute("SELECT account_id FROM transactions WHERE user_id = ?", (user_id,)).fetchone()
    restored_account = db.execute("SELECT id FROM accounts WHERE user_id = ?", (user_id,)).fetchone()
    # self-consistent (tx points at the restored account), not necessarily the same
    # numeric id as before restore
    assert tx["account_id"] == restored_account["id"]


def test_restore_never_touches_other_users_data(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    db.execute(
        "INSERT INTO accounts (user_id, name, type, balance_cents) VALUES (?, 'Bobs Account', 'checking', 1)",
        (other_id,),
    )
    db.commit()

    seed_data(db, user_id)
    backup = export.export_json_backup(db, user_id)
    export.restore_json_backup(db, user_id, backup)
    db.commit()

    bob_accounts = db.execute("SELECT name FROM accounts WHERE user_id = ?", (other_id,)).fetchall()
    assert [r["name"] for r in bob_accounts] == ["Bobs Account"]


def test_restore_does_not_collide_with_another_users_row_id(db, user_id):
    # Regression test: ids are a single global AUTOINCREMENT sequence shared across all
    # users. A backup captured earlier can contain an id that now belongs to a different
    # user's live row; restore must not try to reuse it.
    seed_data(db, user_id)
    backup = export.export_json_backup(db, user_id)
    backed_up_account_id = backup["tables"]["accounts"][0]["id"]

    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid

    # Free up the backed-up id (SQLite's AUTOINCREMENT never re-assigns a used id
    # automatically, but an explicit INSERT can reuse it once it's not occupied), then
    # give it to Bob directly, simulating "this id now belongs to someone else" by the
    # time Alice restores her older backup.
    db.execute("DELETE FROM accounts WHERE id = ?", (backed_up_account_id,))
    db.execute(
        "INSERT INTO accounts (id, user_id, name, type, balance_cents) VALUES (?, ?, 'Bobs Account', 'checking', 1)",
        (backed_up_account_id, other_id),
    )
    db.commit()

    # restoring Alice's backup must not fail even though Bob now owns that row id
    export.restore_json_backup(db, user_id, backup)
    db.commit()

    bob_accounts = db.execute("SELECT name FROM accounts WHERE user_id = ?", (other_id,)).fetchall()
    assert [r["name"] for r in bob_accounts] == ["Bobs Account"]
    alice_accounts = db.execute("SELECT name FROM accounts WHERE user_id = ?", (user_id,)).fetchall()
    assert [r["name"] for r in alice_accounts] == ["Checking"]


def test_restore_rejects_wrong_version(db, user_id):
    with pytest.raises(export.RestoreError):
        export.restore_json_backup(db, user_id, {"version": 999, "tables": {}})


def test_restore_rejects_missing_tables_key(db, user_id):
    with pytest.raises(export.RestoreError):
        export.restore_json_backup(db, user_id, {"version": export.BACKUP_VERSION})


def test_backup_filename_sanitizes_crlf_and_quotes():
    name = export.backup_filename('evil"\r\nX-Injected: 1')
    assert "\r" not in name and "\n" not in name and '"' not in name


def test_backup_filename_preserves_safe_username():
    name = export.backup_filename("logan")
    assert "budget-backup-logan-" in name


# ----- restore input validation (2026-09-06) -----

def test_restore_rejects_non_iso_snapshot_date(db, user_id):
    backup = {
        "version": export.BACKUP_VERSION,
        "tables": {"snapshots": [{"snapshot_date": '"><script>alert(1)</script>',
                                  "safe_to_spend_cents": 1}]},
    }
    with pytest.raises(export.RestoreError):
        export.restore_json_backup(db, user_id, backup)


def test_restore_still_accepts_valid_dates_and_nulls(db, user_id):
    seed_data(db, user_id)
    backup = export.export_json_backup(db, user_id)
    export.restore_json_backup(db, user_id, backup)  # must not raise
    assert db.execute("SELECT COUNT(*) c FROM accounts WHERE user_id = ?", (user_id,)).fetchone()["c"] == 1
