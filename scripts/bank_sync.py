"""Sync SimpleFIN bank connections for one or all active users. Cron-friendly.

Populates bank_sync_staging by default — never touches accounts.balance_cents. A
synced balance only reaches the real account after a user reviews and applies it
from /bank/{id}/review, same as a sync triggered from the UI, UNLESS the user has
opted into bank_sync_auto_apply (see _try_auto_apply below), in which case a
close-enough balance applies immediately instead of waiting on manual review.
Mirrors the exact sync logic in app/routers/bank.py::sync_connection (cooldown,
decrypt-error handling, staging only mapped links, auto-apply) so cron and UI
syncs behave identically.

Usage:
    python -m scripts.bank_sync              # all active users' connections
    python -m scripts.bank_sync --user logan  # just one user's connections
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

from app import crypto
from app.config import DB_PATH
from app.db import get_connection
from app.money import format_cents
from app.repositories import accounts as accounts_repo
from app.repositories import bank_sync as repo
from app.repositories import debts as debts_repo
from app.repositories import settings as settings_repo
from app.services import bank_sync as bank_sync_service
from app.services import push as push_service
from app.services.calc import DEFAULT_SETTINGS


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sync_cooldown(conn, user_id: int) -> timedelta:
    raw = settings_repo.get_all(conn, user_id).get(
        "bank_sync_cooldown_minutes", DEFAULT_SETTINGS["bank_sync_cooldown_minutes"]
    )
    return timedelta(minutes=int(raw))


def _large_transaction_threshold_cents(conn, user_id: int) -> int:
    raw = settings_repo.get_all(conn, user_id).get(
        "large_transaction_threshold_cents", DEFAULT_SETTINGS["large_transaction_threshold_cents"]
    )
    return int(raw)


# Unattended, nothing waiting on the response, so it can afford to be much more
# patient than the interactive UI sync route -- confirmed 2026-08-26 against real
# production logs that one connection's SimpleFIN bridge regularly needed longer
# than the UI route's 15s specifically at this cron's 6am run time.
_CRON_TIMEOUT = 45.0
_RETRY_DELAY_SECONDS = 15


def _try_auto_apply(conn, user_id: int, link, staging_id: int, new_balance_cents: int, max_change_cents: int) -> None:
    """See app/routers/bank.py::_try_auto_apply -- same logic, duplicated rather
    than shared because this script and the FastAPI route already diverge on how
    they get a DB connection; matches this codebase's existing pattern for the
    available-balance logic just above, which duplicates for the same reason."""
    if link["debt_id"] is not None:
        target = debts_repo.get_debt(conn, user_id, link["debt_id"])
    else:
        target = accounts_repo.get_account(conn, user_id, link["account_id"])
    if target is None:
        return

    if not bank_sync_service.within_auto_apply_threshold(target["balance_cents"], new_balance_cents, max_change_cents):
        return

    if link["debt_id"] is not None:
        debts_repo.update_balance(conn, user_id, link["debt_id"], new_balance_cents)
    else:
        accounts_repo.update_balance(conn, user_id, link["account_id"], new_balance_cents)
    repo.mark_staging_applied(conn, user_id, staging_id)


def sync_connection(conn, user_id: int, connection_row, threshold_cents: int) -> tuple[str, list[dict]]:
    connection_id = connection_row["id"]
    large_transactions = []

    if connection_row["last_synced_at"]:
        last_synced = datetime.strptime(
            connection_row["last_synced_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last_synced < _sync_cooldown(conn, user_id):
            return "skipped (cooldown)", large_transactions

    try:
        access_url = crypto.decrypt(connection_row["access_url_encrypted"])
    except ValueError:
        repo.mark_error(conn, user_id, connection_id, "Connection data corrupted — please disconnect and reconnect.")
        return "error (corrupted)", large_transactions

    try:
        fetch_result = bank_sync_service.fetch_accounts(access_url, timeout=_CRON_TIMEOUT)
    except bank_sync_service.SimpleFinRevoked:
        repo.mark_error(conn, user_id, connection_id, "Access revoked — reconnect from SimpleFIN.")
        return "error (revoked)", large_transactions
    except bank_sync_service.SimpleFinError as exc:
        # Retry once -- a revoked connection never reaches here (caught above), so
        # this is only ever a transient network/timeout failure worth one more try.
        time.sleep(_RETRY_DELAY_SECONDS)
        try:
            fetch_result = bank_sync_service.fetch_accounts(access_url, timeout=_CRON_TIMEOUT)
        except bank_sync_service.SimpleFinRevoked:
            repo.mark_error(conn, user_id, connection_id, "Access revoked — reconnect from SimpleFIN.")
            return "error (revoked)", large_transactions
        except bank_sync_service.SimpleFinError as retry_exc:
            repo.mark_error(conn, user_id, connection_id, str(retry_exc))
            return f"error ({retry_exc})", large_transactions

    saved_settings = settings_repo.get_all(conn, user_id)
    use_available_balance = saved_settings.get(
        "bank_sync_use_available_balance", DEFAULT_SETTINGS["bank_sync_use_available_balance"]
    ) == "1"
    auto_apply = saved_settings.get(
        "bank_sync_auto_apply", DEFAULT_SETTINGS["bank_sync_auto_apply"]
    ) == "1"
    auto_apply_max_change_cents = int(saved_settings.get(
        "bank_sync_auto_apply_max_change_cents", DEFAULT_SETTINGS["bank_sync_auto_apply_max_change_cents"]
    ))

    sfin_accounts = fetch_result.accounts
    staged = 0
    for sfin_account in sfin_accounts:
        link_id = repo.upsert_link(
            conn, user_id, connection_id, sfin_account["sfin_account_id"], sfin_account["sfin_account_name"]
        )
        link = repo.get_link(conn, user_id, link_id)
        if link["account_id"] is not None or link["debt_id"] is not None:
            balance_cents = sfin_account["balance_cents"]
            if link["debt_id"] is not None:
                balance_cents = bank_sync_service.normalize_debt_balance_cents(balance_cents)
            elif use_available_balance and sfin_account.get("available_balance_cents") is not None:
                balance_cents = sfin_account["available_balance_cents"]
            staging_id = repo.create_staging_row(conn, user_id, link_id, balance_cents, sfin_account.get("balance_date"))
            if auto_apply:
                _try_auto_apply(conn, user_id, link, staging_id, balance_cents, auto_apply_max_change_cents)
            staged += 1
        if link["account_id"] is not None:
            new_transactions = repo.create_transaction_staging_rows(
                conn, user_id, link_id, sfin_account.get("transactions", [])
            )
            for txn in new_transactions:
                if abs(txn["amount_cents"]) >= threshold_cents:
                    large_transactions.append(txn)

    warning = bank_sync_service.summarize_errors(fetch_result.errors)
    repo.mark_synced(conn, user_id, connection_id, _now_iso(), warning)
    summary = f"synced ({staged} staged, {len(sfin_accounts) - staged} unmapped)"
    return (f"{summary} — errors: {warning}" if warning else summary), large_transactions


def _send_large_transaction_alert(conn, user_id: int, large_transactions: list[dict], threshold_cents: int) -> None:
    large_transactions = sorted(large_transactions, key=lambda t: abs(t["amount_cents"]), reverse=True)
    shown = [f"{format_cents(t['amount_cents'])} at {t['description']}" for t in large_transactions[:3]]
    if len(large_transactions) > 3:
        shown.append(f"+{len(large_transactions) - 3} more")
    noun = "transaction" if len(large_transactions) == 1 else "transactions"
    push_service.send_to_user(
        conn, user_id,
        f"Large {noun} (over {format_cents(threshold_cents)})",
        ", ".join(shown),
        "/bank/transactions",
    )


def sync_for_user(conn, user_id: int) -> int:
    connections = repo.list_connections(conn, user_id)
    threshold_cents = _large_transaction_threshold_cents(conn, user_id)
    large_transactions = []
    for connection_row in connections:
        result, connection_large_transactions = sync_connection(conn, user_id, connection_row, threshold_cents)
        print(f"  connection {connection_row['id']} ({connection_row['label']}): {result}")
        large_transactions.extend(connection_large_transactions)

    if large_transactions:
        _send_large_transaction_alert(conn, user_id, large_transactions, threshold_cents)
    return len(connections)


def notify_failure(conn) -> None:
    """Push an alert to every active user on an unhandled cron failure -- this is
    exactly the bug class that went undetected for 9 days (silently-failing daily
    cron, nobody reading logs/bank_sync.log). Best-effort: a push failure (e.g. no
    VAPID key configured yet) must never mask the original exception."""
    try:
        for row in conn.execute("SELECT id FROM users WHERE is_active = 1").fetchall():
            push_service.send_to_user(
                conn, row["id"], "Bank sync failed", "The daily bank sync cron hit an error — check the logs.", "/bank"
            )
        conn.commit()
    except Exception as exc:
        print(f"  (also failed to send failure-alert push: {exc})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="username to sync (default: all active users)")
    args = parser.parse_args()

    with get_connection(DB_PATH) as conn:
        try:
            if args.user:
                row = conn.execute(
                    "SELECT id FROM users WHERE username = ? AND is_active = 1", (args.user,)
                ).fetchone()
                if row is None:
                    print(f"No active user '{args.user}'")
                    sys.exit(1)
                print(f"Syncing '{args.user}'")
                sync_for_user(conn, row["id"])
            else:
                rows = conn.execute("SELECT id, username FROM users WHERE is_active = 1").fetchall()
                total_connections = 0
                for row in rows:
                    print(f"Syncing '{row['username']}'")
                    total_connections += sync_for_user(conn, row["id"])
                print(f"Synced {len(rows)} user(s), {total_connections} connection(s)")
        except Exception:
            notify_failure(conn)
            raise


if __name__ == "__main__":
    main()
