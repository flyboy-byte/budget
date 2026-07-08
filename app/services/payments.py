"""Records an actual payment against a committed purchase: keeps
committed_purchases.amount_paid_cents/status and the transactions ledger in sync in
one operation. The repo layer (app.repositories.committed_purchases) stays dumb —
this is the cross-table orchestration CLAUDE.md flagged as the next real feature."""
import sqlite3
from datetime import date

from app.repositories import accounts as accounts_repo
from app.repositories import committed_purchases as purchases_repo
from app.repositories import transactions as transactions_repo


def record_purchase_payment(
    conn: sqlite3.Connection, user_id: int, purchase_id: int, amount_cents: int
) -> bool | None:
    purchase = purchases_repo.get_purchase(conn, user_id, purchase_id)
    if purchase is None:
        return None

    updated = purchases_repo.record_payment(conn, user_id, purchase_id, amount_cents)
    if not updated:
        return updated

    account = accounts_repo.get_default_account(conn, user_id)
    if account is not None:
        accounts_repo.adjust_balance(conn, user_id, account["id"], -amount_cents)

    transactions_repo.create_transaction(
        conn,
        user_id,
        date.today().isoformat(),
        amount_cents,
        "committed_purchase",
        account_id=account["id"] if account is not None else None,
        committed_purchase_id=purchase_id,
        memo=purchase["name"],
    )
    return updated
