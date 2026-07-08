"""CSV per-table export and full JSON backup/restore. Every function takes user_id
explicitly and scopes every query/mutation by it — a backup file never mixes two users'
records, and restore only ever touches the requesting user's own rows.
"""
import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timezone

BACKUP_VERSION = 1

# Explicit column lists (not SELECT *) so we never try to write the generated
# committed_purchases.remaining_cents column back on restore, and so CSV column order
# is stable regardless of schema column ordering.
TABLE_COLUMNS = {
    "accounts": ["id", "name", "type", "balance_cents", "is_active", "display_order", "notes", "created_at", "updated_at"],
    "debts": [
        "id", "name", "type", "balance_cents", "apr_bps", "minimum_payment_cents", "next_due_date",
        "interest_status", "is_flexible_payment", "priority", "is_active", "notes", "created_at", "updated_at",
    ],
    "obligations": [
        "id", "name", "category", "amount_cents", "is_recurring", "recurrence_rule", "due_date",
        "is_required", "is_paid", "paid_date", "auto_pay", "notes", "created_at", "updated_at",
    ],
    "committed_purchases": [
        "id", "name", "category", "amount_cents", "amount_paid_cents", "status", "order_date",
        "expected_arrival_date", "payment_deadline", "priority", "notes", "created_at", "updated_at",
    ],
    "income_events": [
        "id", "source", "expected_amount_cents", "expected_date", "confidence", "is_received",
        "received_date", "received_amount_cents", "is_recurring", "recurrence_rule", "notes",
        "created_at", "updated_at",
    ],
    "transactions": [
        "id", "transaction_date", "amount_cents", "account_id", "target_type", "debt_id",
        "obligation_id", "committed_purchase_id", "memo", "created_at",
    ],
    "snapshots": [
        "id", "snapshot_date", "cash_on_hand_cents", "total_debt_cents", "net_position_cents",
        "reserved_cash_cents", "safe_to_spend_cents", "forecast_position_cents", "protected_floor_cents",
        "window_days", "notes", "created_at",
    ],
}

CSV_TABLES = list(TABLE_COLUMNS.keys())
JSON_TABLES = CSV_TABLES + ["settings"]

# settings has no id (composite PK is user_id, key)
TABLE_COLUMNS["settings"] = ["key", "value"]

# insert order matters: tables referenced by transactions' FKs must exist first
_RESTORE_INSERT_ORDER = [
    "accounts", "debts", "obligations", "committed_purchases",
    "income_events", "transactions", "settings", "snapshots",
]
_RESTORE_DELETE_ORDER = list(reversed(_RESTORE_INSERT_ORDER))


_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Neutralize spreadsheet formula/DDE injection: a cell starting with =, +, -, or @
    is interpreted as a formula by Excel/LibreOffice/Sheets when the CSV is opened.
    User-controlled free-text fields (notes, description, memo) could carry this."""
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGER_CHARS):
        return "'" + value
    return value


def export_table_csv(conn: sqlite3.Connection, user_id: int, table: str) -> str:
    columns = TABLE_COLUMNS[table]
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM {table} WHERE user_id = ? ORDER BY id", (user_id,)
    ).fetchall()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(tuple(_csv_safe(v) for v in row))
    return buffer.getvalue()


def _combined_csv_columns() -> list[str]:
    """Union of every export table's columns, in first-seen order across CSV_TABLES —
    a stable column layout regardless of dict ordering."""
    seen: list[str] = []
    for table in CSV_TABLES:
        for column in TABLE_COLUMNS[table]:
            if column not in seen:
                seen.append(column)
    return seen


def export_combined_csv(conn: sqlite3.Connection, user_id: int) -> str:
    """One CSV across every table, `table` as the leading column, one row per record.
    Column set is the union across all tables — a record only fills the columns from
    its own table, the rest sit blank. Friendlier to paste into a single chat message
    than downloading and attaching 7 separate per-table files."""
    all_columns = _combined_csv_columns()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["table", *all_columns])
    for table in CSV_TABLES:
        columns = TABLE_COLUMNS[table]
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
        for row in rows:
            values = dict(zip(columns, row))
            writer.writerow([table, *(_csv_safe(values.get(col)) for col in all_columns)])
    return buffer.getvalue()


def export_json_backup(conn: sqlite3.Connection, user_id: int) -> dict:
    backup = {
        "version": BACKUP_VERSION,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "user_id": user_id,
        "tables": {},
    }
    for table in JSON_TABLES:
        columns = TABLE_COLUMNS[table]
        order_by = "id" if "id" in columns else "key"
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE user_id = ? ORDER BY {order_by}",
            (user_id,),
        ).fetchall()
        backup["tables"][table] = [dict(row) for row in rows]
    return backup


class RestoreError(ValueError):
    pass


_TRANSACTION_FK_COLUMNS = {
    "account_id": "accounts",
    "debt_id": "debts",
    "obligation_id": "obligations",
    "committed_purchase_id": "committed_purchases",
}


def restore_json_backup(conn: sqlite3.Connection, user_id: int, backup: dict) -> None:
    """Wipe-and-restore this user's own data only, inside the caller's transaction
    (commit/rollback is the caller's responsibility, matching app.db.get_connection).

    Row IDs are never preserved from the backup — every table's `id` is a single global
    AUTOINCREMENT sequence shared across all users (not per-user), so blindly reusing a
    backed-up id could collide with another user's live row. Instead we let SQLite assign
    fresh ids and remap transactions' FK references (account_id, debt_id, etc.) to the
    newly-assigned ids as we go.
    """
    if backup.get("version") != BACKUP_VERSION:
        raise RestoreError(f"Unsupported backup version: {backup.get('version')!r}")
    if "tables" not in backup:
        raise RestoreError("Backup is missing 'tables'")

    for table in _RESTORE_DELETE_ORDER:
        conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))

    id_maps: dict[str, dict[int, int]] = {}

    for table in _RESTORE_INSERT_ORDER:
        columns = TABLE_COLUMNS[table]
        has_id = "id" in columns
        insert_columns = [c for c in columns if c != "id"]
        col_list = ", ".join(insert_columns)
        placeholders = ", ".join("?" for _ in insert_columns)

        for row in backup["tables"].get(table, []):
            values = dict(row)
            if table == "transactions":
                for fk_column, referenced_table in _TRANSACTION_FK_COLUMNS.items():
                    old_id = values.get(fk_column)
                    if old_id is not None:
                        values[fk_column] = id_maps.get(referenced_table, {}).get(old_id)

            insert_values = tuple(values.get(c) for c in insert_columns)
            cur = conn.execute(
                f"INSERT INTO {table} (user_id, {col_list}) VALUES (?, {placeholders})",
                (user_id, *insert_values),
            )
            if has_id and row.get("id") is not None:
                id_maps.setdefault(table, {})[row["id"]] = cur.lastrowid


def backup_filename(username: str) -> str:
    """The web signup path (POST /settings/users) restricts usernames to a safe charset,
    but CLI-created ones (scripts/init_db.py, scripts/add_user.py) aren't validated —
    strip anything that isn't safe in a Content-Disposition filename so a username can
    never break header parsing or crash the response."""
    safe_username = re.sub(r"[^A-Za-z0-9_-]", "_", username) or "user"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"budget-backup-{safe_username}-{timestamp}.json"


def csv_filename(table: str) -> str:
    return f"{table}.csv"


def combined_csv_filename() -> str:
    return "budget-export-all-tables.csv"


def to_json_bytes(backup: dict) -> bytes:
    return json.dumps(backup, indent=2).encode("utf-8")
