"""SimpleFIN bank-sync UI — connect, list/map, review-and-confirm, disconnect.

Fetched balances only ever land in bank_sync_staging (via /sync) and only ever
reach accounts.balance_cents through an explicit user confirmation (/apply) —
never auto-applied. See IMPLEMENTATION_HISTORY.md for the full design this implements.
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse

from app import crypto
from app.deps import get_csrf_token, get_current_user_id, get_db, verify_csrf_token
from app.forms import parse_row_id, parse_target
from app.repositories import accounts as accounts_repo
from app.repositories import bank_sync as repo
from app.repositories import committed_purchases as purchases_repo
from app.repositories import debts as debts_repo
from app.repositories import obligations as obligations_repo
from app.repositories import settings as settings_repo
from app.repositories import transactions as transactions_repo
from app.services import bank_sync as bank_sync_service
from app.services import bank_transactions as bank_transactions_service
from app.services import bill_detection
from app.services import dates
from app.services.calc import DEFAULT_SETTINGS
from app.templating import templates

router = APIRouter(prefix="/bank")

# How far back the opt-in deep scan asks SimpleFIN to reach. Four months is enough
# to see 3-4 occurrences of a monthly bill (detection needs at least 2 to compute a
# gap at all), without asking a bridge for a year of data it may refuse or stall on.
_SCAN_HISTORY_DAYS = 120
# Generous relative to the 15s interactive default: this pulls far more data than a
# routine sync, and the user pressed a button and is watching a spinner for it.
_SCAN_TIMEOUT = 60.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _try_auto_apply(
    db: sqlite3.Connection,
    user_id: int,
    link: sqlite3.Row,
    staging_id: int,
    new_balance_cents: int,
    max_change_cents: int,
) -> None:
    """Applies a just-staged balance immediately when it's close enough to the
    currently-stored one, instead of leaving it for manual review. Only called when
    the user has opted in (bank_sync_auto_apply); a jump bigger than
    max_change_cents still falls through to the normal review queue, which is the
    safety net against a single bad SimpleFIN read silently overwriting a real
    balance. Mirrors exactly what /apply does for one staging row, just done
    inline instead of waiting on a second request."""
    if link["debt_id"] is not None:
        target = debts_repo.get_debt(db, user_id, link["debt_id"])
    else:
        target = accounts_repo.get_account(db, user_id, link["account_id"])
    if target is None:
        return

    if not bank_sync_service.within_auto_apply_threshold(
        target["balance_cents"], new_balance_cents, max_change_cents
    ):
        return

    if link["debt_id"] is not None:
        debts_repo.update_balance(db, user_id, link["debt_id"], new_balance_cents)
    else:
        accounts_repo.update_balance(db, user_id, link["account_id"], new_balance_cents)
    repo.mark_staging_applied(db, user_id, staging_id)


def _existing_obligation_keys(db: sqlite3.Connection, user_id: int) -> set[str]:
    """Normalized merchant keys for obligations that already exist, so a bill the
    user has already accepted stops being suggested again on every visit. Runs the
    obligation's *name* through the same normalizer the detector applies to bank
    descriptions, so the two are comparable at all."""
    return {
        key
        for obligation in obligations_repo.list_obligations(db, user_id, include_paid=True)
        if (key := bill_detection.normalize_merchant(obligation["name"]))
    }


def _sync_cooldown(db: sqlite3.Connection, user_id: int) -> timedelta:
    raw = settings_repo.get_all(db, user_id).get(
        "bank_sync_cooldown_minutes", DEFAULT_SETTINGS["bank_sync_cooldown_minutes"]
    )
    return timedelta(minutes=int(raw))


def _is_stale(conn_row: sqlite3.Row, cooldown: timedelta) -> bool:
    """Flags a connection that hasn't synced in over 2x its configured cooldown —
    catches a silently-broken cron job before it goes unnoticed for weeks."""
    if conn_row["status"] != "active" or not conn_row["last_synced_at"]:
        return False
    last_synced = dates.parse_db_timestamp(conn_row["last_synced_at"])
    if last_synced is None:
        # Can't read when it last ran -- that's exactly the silently-broken case
        # this warning exists to catch, so surface it.
        return True
    return datetime.now(timezone.utc) - last_synced > cooldown * 2


def _connections_context(db: sqlite3.Connection, user_id: int) -> list[dict]:
    cooldown = _sync_cooldown(db, user_id)
    result = []
    for conn_row in repo.list_connections(db, user_id):
        links = []
        for link in repo.list_links(db, user_id, conn_row["id"]):
            linked_account = accounts_repo.get_account(db, user_id, link["account_id"]) if link["account_id"] else None
            linked_debt = debts_repo.get_debt(db, user_id, link["debt_id"]) if link["debt_id"] else None
            links.append({
                "row": link,
                "linked_account": linked_account,
                "linked_debt": linked_debt,
            })
        result.append({"row": conn_row, "links": links, "stale": _is_stale(conn_row, cooldown)})
    return result


@router.get("")
def list_connections(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "bank/list.html",
        {
            "connections": _connections_context(db, user_id),
            "accounts": accounts_repo.list_accounts(db, user_id),
            "debts": debts_repo.list_debts(db, user_id),
            "unmatched_transaction_count": len(repo.list_unmatched_transactions(db, user_id)),
            "csrf_token": csrf_token,
        },
    )


@router.get("/connect")
def connect_form(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request, "bank/connect.html", {"csrf_token": csrf_token, "error": None, "label": ""}
    )


@router.post("/connect", dependencies=[Depends(verify_csrf_token)])
def connect_submit(
    request: Request,
    label: str = Form(...),
    setup_token: str = Form(...),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    label = label.strip()
    if not label:
        return templates.TemplateResponse(
            request,
            "bank/connect.html",
            {"csrf_token": csrf_token, "error": "Label can't be blank.", "label": label},
            status_code=400,
        )

    try:
        access_url = bank_sync_service.claim_setup_token(setup_token.strip())
    except bank_sync_service.SimpleFinError:
        # Never echo the submitted token back, even on error — it's a single-use
        # bearer credential and this failure already invalidated it either way.
        return templates.TemplateResponse(
            request,
            "bank/connect.html",
            {
                "csrf_token": csrf_token,
                "error": "Could not claim that Setup Token. It may already be used or expired — generate a new one from SimpleFIN and try again.",
                "label": label,
            },
            status_code=400,
        )

    encrypted = crypto.encrypt(access_url)
    repo.create_connection(db, user_id, label, encrypted)
    db.commit()
    return RedirectResponse(url="/bank", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{connection_id}/sync", dependencies=[Depends(verify_csrf_token)])
def sync_connection(
    connection_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    connection = repo.get_connection_row(db, user_id, connection_id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    last_synced = dates.parse_db_timestamp(connection["last_synced_at"])
    # An unreadable last_synced_at falls through to syncing, same as a never-synced
    # connection: blocking instead would strand the connection permanently, and the
    # sync itself rewrites last_synced_at correctly, so this self-heals on first use.
    if last_synced is not None:
        if datetime.now(timezone.utc) - last_synced < _sync_cooldown(db, user_id):
            return RedirectResponse(url="/bank", status_code=status.HTTP_303_SEE_OTHER)

    try:
        access_url = crypto.decrypt(connection["access_url_encrypted"])
    except ValueError:
        repo.mark_error(db, user_id, connection_id, "Connection data corrupted — please disconnect and reconnect.")
        db.commit()
        return RedirectResponse(url="/bank", status_code=status.HTTP_303_SEE_OTHER)

    try:
        fetch_result = bank_sync_service.fetch_accounts(access_url)
    except bank_sync_service.SimpleFinRevoked:
        repo.mark_error(db, user_id, connection_id, "Access revoked — reconnect from SimpleFIN.")
        db.commit()
        return RedirectResponse(url="/bank", status_code=status.HTTP_303_SEE_OTHER)
    except bank_sync_service.SimpleFinError as exc:
        repo.mark_error(db, user_id, connection_id, str(exc))
        db.commit()
        return RedirectResponse(url="/bank", status_code=status.HTTP_303_SEE_OTHER)

    saved_settings = settings_repo.get_all(db, user_id)
    use_available_balance = saved_settings.get(
        "bank_sync_use_available_balance", DEFAULT_SETTINGS["bank_sync_use_available_balance"]
    ) == "1"
    auto_apply = saved_settings.get(
        "bank_sync_auto_apply", DEFAULT_SETTINGS["bank_sync_auto_apply"]
    ) == "1"
    auto_apply_max_change_cents = int(saved_settings.get(
        "bank_sync_auto_apply_max_change_cents", DEFAULT_SETTINGS["bank_sync_auto_apply_max_change_cents"]
    ))

    for sfin_account in fetch_result.accounts:
        link_id = repo.upsert_link(
            db, user_id, connection_id, sfin_account["sfin_account_id"], sfin_account["sfin_account_name"]
        )
        link = repo.get_link(db, user_id, link_id)
        if link["account_id"] is not None or link["debt_id"] is not None:
            balance_cents = sfin_account["balance_cents"]
            if link["debt_id"] is not None:
                balance_cents = bank_sync_service.normalize_debt_balance_cents(balance_cents)
            elif use_available_balance and sfin_account.get("available_balance_cents") is not None:
                # Only for account (asset) links, never debt links — SimpleFIN doesn't
                # populate available-balance meaningfully for loan/credit products
                # (confirmed 2026-08-05: comes back as 0.00 regardless of real balance).
                balance_cents = sfin_account["available_balance_cents"]
            staging_id = repo.create_staging_row(db, user_id, link_id, balance_cents, sfin_account.get("balance_date"))
            if auto_apply:
                _try_auto_apply(db, user_id, link, staging_id, balance_cents, auto_apply_max_change_cents)
        if link["account_id"] is not None:
            # Transaction import only makes sense for cash accounts — a debt-linked
            # (credit card/loan) account's statement lines are a different, higher-
            # volume matching problem, out of scope for this feature.
            repo.create_transaction_staging_rows(db, user_id, link_id, sfin_account.get("transactions", []))

    warning = bank_sync_service.summarize_errors(fetch_result.errors)
    repo.mark_synced(db, user_id, connection_id, _now_iso(), warning)
    db.commit()
    return RedirectResponse(url=f"/bank/{connection_id}/review", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{connection_id}/review")
def review_sync(
    connection_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    connection = repo.get_connection_row(db, user_id, connection_id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    staging_rows = repo.list_unapplied_staging(db, user_id, connection_id)
    rows = []
    for staging in staging_rows:
        link = repo.get_link(db, user_id, staging["bank_account_link_id"])
        if link is None:
            continue
        if link["account_id"] is not None:
            target = accounts_repo.get_account(db, user_id, link["account_id"])
            target_kind = "Account"
        elif link["debt_id"] is not None:
            target = debts_repo.get_debt(db, user_id, link["debt_id"])
            target_kind = "Debt"
        else:
            target = None
            target_kind = None
        if target is None:
            continue
        rows.append({
            "staging": staging,
            "link": link,
            "target": target,
            "target_kind": target_kind,
            "old_balance_cents": target["balance_cents"],
        })

    return templates.TemplateResponse(
        request,
        "bank/review.html",
        {"connection": connection, "rows": rows, "csrf_token": csrf_token},
    )


@router.post("/{connection_id}/apply", dependencies=[Depends(verify_csrf_token)])
async def apply_sync(
    connection_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    connection = repo.get_connection_row(db, user_id, connection_id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    form = await request.form()
    selected_ids = {parse_row_id(v) for v in form.getlist("staging_id")}

    for staging in repo.list_unapplied_staging(db, user_id, connection_id):
        if staging["id"] not in selected_ids:
            continue
        link = repo.get_link(db, user_id, staging["bank_account_link_id"])
        if link is None:
            continue
        if link["account_id"] is not None:
            accounts_repo.update_balance(db, user_id, link["account_id"], staging["synced_balance_cents"])
        elif link["debt_id"] is not None:
            debts_repo.update_balance(db, user_id, link["debt_id"], staging["synced_balance_cents"])
        else:
            continue
        repo.mark_staging_applied(db, user_id, staging["id"])

    db.commit()
    return RedirectResponse(url="/bank", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{connection_id}/links/{link_id}", dependencies=[Depends(verify_csrf_token)])
def set_link(
    connection_id: int,
    link_id: int,
    target: str = Form(""),
    new_name: str = Form(""),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    if repo.get_link(db, user_id, link_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    newly_created_debt_id = None
    if target in ("new_account", "new_debt"):
        name = new_name.strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name can't be blank.")
        if target == "new_account":
            # Seeded at 0 — the very next sync/apply against this link sets the real
            # balance, so a placeholder value here would just be misleading.
            target_type, target_id = "account", accounts_repo.create_account(db, user_id, name, "checking", 0)
        else:
            # Seeded with no minimum_payment/next_due_date — debts_reserved() only
            # counts a debt toward reserved cash once next_due_date is set, so an
            # unfilled minimum would silently reserve $0 for this card. Send the user
            # straight to the edit form afterward so that gap doesn't go unnoticed.
            target_type, target_id = "debt", debts_repo.create_debt(db, user_id, name, "other", 0, 0, "accruing")
            newly_created_debt_id = target_id
    elif target:
        target_type, target_id = parse_target(target, ("account", "debt"))
    else:
        target_type, target_id = None, None

    updated = repo.set_link_target(db, user_id, link_id, target_type, target_id)
    db.commit()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if newly_created_debt_id is not None:
        return RedirectResponse(
            url=f"/debts/{newly_created_debt_id}/edit?from_bank_sync=1",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(url="/bank", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{connection_id}/disconnect", dependencies=[Depends(verify_csrf_token)])
def disconnect(
    connection_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    deleted = repo.delete_connection(db, user_id, connection_id)
    db.commit()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return RedirectResponse(url="/bank", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/transactions")
def list_transactions(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "bank/transactions.html",
        {
            "staged_transactions": repo.list_unmatched_transactions(db, user_id),
            "obligations": obligations_repo.list_obligations(db, user_id),
            "purchases": purchases_repo.list_purchases(db, user_id),
            "spending_categories": transactions_repo.list_distinct_spending_categories(db, user_id),
            "csrf_token": csrf_token,
        },
    )


@router.get("/unmatched-count")
def unmatched_count(
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    """Backs the Badging API (IMPLEMENTATION_HISTORY.md Phase 11) -- read-only, no CSRF needed."""
    return {"count": len(repo.list_unmatched_transactions(db, user_id))}


@router.post("/transactions/{staging_id}/match", dependencies=[Depends(verify_csrf_token)])
def match_transaction(
    staging_id: int,
    target: str = Form(...),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    target_type, target_id = parse_target(target, ("obligation", "purchase"))

    try:
        if target_type == "obligation":
            matched = bank_transactions_service.confirm_obligation_match(db, user_id, staging_id, target_id)
        else:
            matched = bank_transactions_service.confirm_purchase_match(db, user_id, staging_id, target_id)
    except sqlite3.IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That's more than what's left on this purchase.",
        )

    if not matched:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Couldn't match — the transaction or target may already be resolved.",
        )
    db.commit()
    return RedirectResponse(url="/bank/transactions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/transactions/{staging_id}/spending", dependencies=[Depends(verify_csrf_token)])
def record_transaction_as_spending(
    staging_id: int,
    category: str = Form(""),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    """"That was just life" — absorb the transaction into the ledger without
    pretending it paid off a commitment. See bank_transactions.record_as_spending
    for why this doesn't touch the account balance."""
    recorded = bank_transactions_service.record_as_spending(db, user_id, staging_id, category)
    if not recorded:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Couldn't record — the transaction may already be resolved.",
        )
    db.commit()
    return RedirectResponse(url="/bank/transactions", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/suggestions")
def bill_suggestions(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    """Recurring-bill candidates from whatever transaction history is already
    staged locally. No network call — the deeper /suggestions/scan below is the
    opt-in one that goes back to SimpleFIN for more history."""
    staged = [dict(row) for row in repo.list_all_transactions(db, user_id)]
    suggestions = bill_detection.find_recurring_candidates(
        staged, exclude_names=_existing_obligation_keys(db, user_id)
    )
    return templates.TemplateResponse(
        request,
        "bank/suggestions.html",
        {
            "suggestions": suggestions,
            "scanned_count": len(staged),
            "deep_scanned": False,
            "csrf_token": csrf_token,
        },
    )


@router.post("/suggestions/scan", dependencies=[Depends(verify_csrf_token)])
def scan_for_bill_suggestions(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    """Pull a deeper history window straight from SimpleFIN and run detection over
    it in memory. Deliberately does NOT stage anything: writing months of old
    transactions into bank_transaction_staging would dump hundreds of rows into
    the match queue the user then has to clear one by one, which is the opposite
    of the point. Read-only, so it's also safe to re-run.
    """
    since = datetime.now(timezone.utc) - timedelta(days=_SCAN_HISTORY_DAYS)
    fetched: list[dict] = []
    errors: list[str] = []

    for connection in repo.list_connections(db, user_id):
        try:
            access_url = crypto.decrypt(connection["access_url_encrypted"])
        except ValueError:
            errors.append(f"{connection['label']}: connection data corrupted")
            continue
        try:
            result = bank_sync_service.fetch_accounts(
                access_url, timeout=_SCAN_TIMEOUT, start_date=int(since.timestamp())
            )
        except bank_sync_service.SimpleFinError as exc:
            errors.append(f"{connection['label']}: {exc}")
            continue
        for account in result.accounts:
            fetched.extend(account.get("transactions", []))

    suggestions = bill_detection.find_recurring_candidates(
        fetched, exclude_names=_existing_obligation_keys(db, user_id)
    )
    return templates.TemplateResponse(
        request,
        "bank/suggestions.html",
        {
            "suggestions": suggestions,
            "scanned_count": len(fetched),
            "deep_scanned": True,
            "scan_days": _SCAN_HISTORY_DAYS,
            "scan_errors": errors,
            "csrf_token": csrf_token,
        },
    )


@router.post("/suggestions/accept", dependencies=[Depends(verify_csrf_token)])
def accept_bill_suggestion(
    merchant: str = Form(...),
    amount_cents: int = Form(...),
    recurrence_rule: str = Form(...),
    next_due_date: str = Form(...),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    """The one tap. Everything here is re-validated rather than trusted: the form
    round-trips through the browser, so a tampered post could otherwise write an
    obligation with a bogus recurrence rule or a negative amount."""
    if recurrence_rule not in obligations_repo.RECURRENCE_RULES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid recurrence rule.")
    if amount_cents < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount can't be negative.")
    try:
        date.fromisoformat(next_due_date)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid due date.")
    name = merchant.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name can't be blank.")

    obligations_repo.create_obligation(
        db, user_id, name, "detected", amount_cents, next_due_date,
        is_recurring=1, recurrence_rule=recurrence_rule,
        notes="Created from a detected recurring bank charge.",
    )
    db.commit()
    return RedirectResponse(url="/bank/suggestions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/transactions/{staging_id}/dismiss", dependencies=[Depends(verify_csrf_token)])
def dismiss_transaction(
    staging_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    dismissed = repo.dismiss_transaction(db, user_id, staging_id)
    db.commit()
    if not dismissed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return RedirectResponse(url="/bank/transactions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/transactions/dismiss-selected", dependencies=[Depends(verify_csrf_token)])
async def dismiss_selected_transactions(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    """Bulk version of dismiss_transaction — loops over a submitted list of staging
    ids the same way apply_sync already does for balance staging. Ownership is still
    enforced per-id by repo.dismiss_transaction (a bad/other-user id just no-ops
    instead of raising, since a bulk action shouldn't fail the whole batch over one
    already-resolved row)."""
    form = await request.form()
    selected_ids = {parse_row_id(v) for v in form.getlist("staging_id")}
    for staging_id in selected_ids:
        repo.dismiss_transaction(db, user_id, staging_id)
    db.commit()
    return RedirectResponse(url="/bank/transactions", status_code=status.HTTP_303_SEE_OTHER)
