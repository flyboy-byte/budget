"""Landing pages that group the underlying CRUD screens into fewer top-level nav
destinations (Money / Activity / More instead of a flat list of every table).
Pure navigation — every entity screen underneath is untouched, same routes/tests.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from app.deps import get_current_user_id, get_db
from app.repositories import accounts as accounts_repo
from app.repositories import bank_sync as bank_sync_repo
from app.repositories import committed_purchases as purchases_repo
from app.repositories import debts as debts_repo
from app.repositories import income_events as income_repo
from app.repositories import obligations as obligations_repo
from app.repositories import snapshots as snapshots_repo
from app.repositories import transactions as transactions_repo
from app.services import bill_detection, calc, dates
from app.templating import templates

router = APIRouter()

STALE_THRESHOLD = timedelta(days=30)
RECENT_ACTIVITY_LIMIT = 8


def _is_stale_row(row: sqlite3.Row, threshold: timedelta = STALE_THRESHOLD) -> bool:
    """A fixed threshold, unlike bank sync's per-user cooldown — this is a much
    lower-stakes nudge (not a rate limit), so keeping it simple is the right
    trade-off. updated_at is bumped on every write path (manual edit, quick
    balance update, bank-sync apply), so this clears itself the moment the row
    is actually touched."""
    updated_at = dates.parse_db_timestamp(row["updated_at"])
    if updated_at is None:
        # Unreadable timestamp (only reachable via a restored backup) is no evidence
        # the row is current -- flag it rather than silently vouching for it.
        return True
    return datetime.now(timezone.utc) - updated_at > threshold


@router.get("/money")
def money_hub(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    accounts = accounts_repo.list_accounts(db, user_id)
    debts = debts_repo.list_debts(db, user_id)
    context = {
        "account_count": len(accounts),
        "cash_on_hand_cents": calc.cash_on_hand(db, user_id),
        "debt_count": len(debts),
        "total_debt_cents": calc.total_debt(db, user_id),
        "bill_count": len(obligations_repo.list_obligations(db, user_id)),
        "bank_connection_count": len(bank_sync_repo.list_connections(db, user_id)),
        "bank_pending_review_count": bank_sync_repo.count_unapplied_staging(db, user_id),
        "bank_unmatched_transaction_count": len(bank_sync_repo.list_unmatched_transactions(db, user_id)),
        "stale_account_count": sum(1 for a in accounts if _is_stale_row(a)),
        "stale_debt_count": sum(1 for d in debts if _is_stale_row(d)),
        "bill_suggestion_count": _bill_suggestion_count(db, user_id),
    }
    return templates.TemplateResponse(request, "hubs/money.html", context)


def _bill_suggestion_count(db: sqlite3.Connection, user_id: int) -> int:
    """Detection over locally-staged history only — no network call, since this runs
    on every Money hub render. The deeper SimpleFIN scan stays behind its own button
    on /bank/suggestions."""
    staged = [dict(row) for row in bank_sync_repo.list_all_transactions(db, user_id)]
    if not staged:
        return 0
    existing = {
        key
        for obligation in obligations_repo.list_obligations(db, user_id, include_paid=True)
        if (key := bill_detection.normalize_merchant(obligation["name"]))
    }
    return len(bill_detection.find_recurring_candidates(staged, exclude_names=existing))


@router.get("/activity")
def activity_hub(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    context = {
        "purchase_count": len(purchases_repo.list_purchases(db, user_id)),
        "income_count": len(income_repo.list_income_events(db, user_id)),
        # The hub itself shows the most recent ledger entries rather than only
        # linking to them -- landing on a page of navigation cards is a lot of
        # what made the app feel inert, and this is the one screen where actual
        # history can be surfaced without a second tap.
        "recent_entries": transactions_repo.list_recent(db, user_id, RECENT_ACTIVITY_LIMIT),
        "ledger_count": transactions_repo.count_for_user(db, user_id),
    }
    return templates.TemplateResponse(request, "hubs/activity.html", context)


@router.get("/more")
def more_hub(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    context = {
        "snapshot_count": len(snapshots_repo.list_snapshots(db, user_id)),
    }
    return templates.TemplateResponse(request, "hubs/more.html", context)
