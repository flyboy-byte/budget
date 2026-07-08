"""Confirms a user-picked match between a staged bank transaction and an open
obligation/committed purchase. Mirrors app/services/bills.py and payments.py's
orchestration shape (repo layer stays dumb, this keeps status + the transactions
ledger in sync in one operation), including debiting the matched account the same
way those do. This is safe from double-counting even though a balance sync will
also touch this account later: app/repositories/bank_sync.py's create_staging_row
always writes an ABSOLUTE balance (not a delta), and app/routers/bank.py's apply
step does an absolute SET, so the next sync's synced_balance_cents supersedes this
interim debit rather than stacking on top of it. Debiting immediately closes the
window where reserved_cash has already dropped (the obligation/purchase is marked
paid) but cash_on_hand hadn't caught up yet, which briefly overstated safe-to-spend
between confirming a match and the next successful sync.
bank_sync.mark_transaction_matched marks the staging row 'matched' and links it to
the ledger row so it can't be re-matched or accidentally reset by a resync.
"""
import sqlite3

from app.repositories import accounts as accounts_repo
from app.repositories import bank_sync as bank_sync_repo
from app.repositories import committed_purchases as purchases_repo
from app.repositories import obligations as obligations_repo
from app.repositories import transactions as transactions_repo
from app.services.recurrence import advance_date


def confirm_obligation_match(
    conn: sqlite3.Connection, user_id: int, staging_id: int, obligation_id: int
) -> bool:
    staging = bank_sync_repo.get_transaction_staging(conn, user_id, staging_id)
    if staging is None or staging["status"] != "unmatched":
        return False
    obligation = obligations_repo.get_obligation(conn, user_id, obligation_id)
    if obligation is None or obligation["is_paid"]:
        return False

    link = bank_sync_repo.get_link(conn, user_id, staging["bank_account_link_id"])

    if obligation["is_recurring"] and obligation["recurrence_rule"]:
        next_due_date = advance_date(obligation["due_date"], obligation["recurrence_rule"])
        updated = obligations_repo.update_obligation(
            conn, user_id, obligation_id,
            obligation["name"], obligation["category"], obligation["amount_cents"],
            next_due_date, is_paid=0, is_recurring=1,
            recurrence_rule=obligation["recurrence_rule"],
            is_required=obligation["is_required"], auto_pay=obligation["auto_pay"],
            notes=obligation["notes"], paid_date=staging["posted_date"],
        )
    else:
        updated = obligations_repo.update_obligation(
            conn, user_id, obligation_id,
            obligation["name"], obligation["category"], obligation["amount_cents"],
            obligation["due_date"], is_paid=1, is_recurring=0, recurrence_rule=None,
            is_required=obligation["is_required"], auto_pay=obligation["auto_pay"],
            notes=obligation["notes"], paid_date=staging["posted_date"],
        )
    if not updated:
        return False

    if link and link["account_id"] is not None:
        accounts_repo.adjust_balance(conn, user_id, link["account_id"], -abs(staging["amount_cents"]))

    ledger_id = transactions_repo.create_transaction(
        conn, user_id, staging["posted_date"], abs(staging["amount_cents"]), "obligation",
        account_id=link["account_id"] if link else None,
        obligation_id=obligation_id, memo=staging["description"],
    )
    return bank_sync_repo.mark_transaction_matched(
        conn, user_id, staging_id, ledger_id, obligation_id=obligation_id
    )


def record_as_spending(
    conn: sqlite3.Connection, user_id: int, staging_id: int, category: str | None = None
) -> bool:
    """Absorb a staged bank transaction as ordinary spending — not a bill, not a
    committed purchase, just something that happened. Before this existed the only
    options were match-to-a-commitment or dismiss (which forgets it entirely), so
    the app couldn't learn what a user actually spends money on, which is most of
    what they spend money on.

    Deliberately does NOT debit the account, unlike confirm_obligation_match /
    confirm_purchase_match above. Those over-debit on purpose to close the window
    where reserved_cash has already dropped (the bill got marked paid) but
    cash_on_hand hasn't caught up. Recording ordinary spending moves reserved_cash
    by exactly zero — there was never a commitment reserving it — so there's no
    gap to close, and this is a *posted* bank transaction whose amount is already
    baked into the synced balance. Debiting here would understate cash until the
    next sync overwrote it.
    """
    staging = bank_sync_repo.get_transaction_staging(conn, user_id, staging_id)
    if staging is None or staging["status"] != "unmatched":
        return False

    link = bank_sync_repo.get_link(conn, user_id, staging["bank_account_link_id"])
    category = (category or "").strip() or None

    ledger_id = transactions_repo.create_transaction(
        conn, user_id, staging["posted_date"], abs(staging["amount_cents"]), "spending",
        account_id=link["account_id"] if link else None,
        category=category, memo=staging["description"],
    )
    # Reuses the 'matched' status rather than adding a new one: from the staging
    # queue's point of view "matched" means resolved-and-linked-to-a-ledger-row,
    # which is exactly what happened. matched_obligation_id/
    # matched_committed_purchase_id both stay NULL, which is what distinguishes
    # this from a real commitment match.
    return bank_sync_repo.mark_transaction_matched(conn, user_id, staging_id, ledger_id)


def confirm_purchase_match(
    conn: sqlite3.Connection, user_id: int, staging_id: int, purchase_id: int
) -> bool:
    staging = bank_sync_repo.get_transaction_staging(conn, user_id, staging_id)
    if staging is None or staging["status"] != "unmatched":
        return False
    purchase = purchases_repo.get_purchase(conn, user_id, purchase_id)
    if purchase is None or purchase["status"] in ("paid", "canceled"):
        return False

    link = bank_sync_repo.get_link(conn, user_id, staging["bank_account_link_id"])
    amount_cents = abs(staging["amount_cents"])

    updated = purchases_repo.record_payment(conn, user_id, purchase_id, amount_cents)
    if not updated:
        return False

    if link and link["account_id"] is not None:
        accounts_repo.adjust_balance(conn, user_id, link["account_id"], -amount_cents)

    ledger_id = transactions_repo.create_transaction(
        conn, user_id, staging["posted_date"], amount_cents, "committed_purchase",
        account_id=link["account_id"] if link else None,
        committed_purchase_id=purchase_id, memo=staging["description"],
    )
    return bank_sync_repo.mark_transaction_matched(
        conn, user_id, staging_id, ledger_id, committed_purchase_id=purchase_id
    )
