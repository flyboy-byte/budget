"""The 'Update today' quick-action forms on the dashboard. Every route here mutates
one thing, then returns a small confirmation fragment *plus* an out-of-band refresh
of the dashboard summary (safe-to-spend, sparkline, debt priority, etc.) so the
numbers visibly update in place — no navigation, no losing your scroll position.
This is the whole point of this screen: rapid repeated real-life actions (update a
balance, log a purchase, mark a bill paid) without feeling like you're editing a
database table.
"""
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.deps import get_csrf_token, get_current_user_id, get_db, verify_csrf_token
from app.forms import parse_target
from app.money import format_cents, parse_dollars_to_cents
from app.repositories import accounts as accounts_repo
from app.repositories import committed_purchases as purchases_repo
from app.repositories import debts as debts_repo
from app.routers.dashboard import build_dashboard_context
from app.services import bills, payments, whatif
from app.templating import templates

router = APIRouter(prefix="/today")


def _summary_fragment(request: Request, db: sqlite3.Connection, user_id: int) -> str:
    context = build_dashboard_context(db, user_id)
    # The stale-balance alert now embeds its own inline quick-update forms (PLAN.md
    # §3's reconciliation-screen item), so csrf_token has to ride along on every OOB
    # refresh too, not just the full-page render (dashboard.py sets it there
    # separately since build_dashboard_context itself stays request-agnostic).
    context["csrf_token"] = get_csrf_token(request, db)
    return templates.get_template("partials/_dashboard_summary.html").render(
        {"request": request, **context}
    )


def _response(request: Request, db: sqlite3.Connection, user_id: int, confirmation: str) -> HTMLResponse:
    return HTMLResponse(confirmation + _summary_fragment(request, db, user_id))


@router.post("/balance", dependencies=[Depends(verify_csrf_token)])
def quick_update_balance(
    request: Request,
    target: str = Form(...),
    amount: str = Form(...),
    mode: str = Form("set"),
    direction: str = Form("+"),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    """Covers both accounts and debts (debts previously had no quick-update path at
    all -- the full edit form was the only way to touch a debt's balance). `target`
    is "account:<id>" or "debt:<id>", the same encoding bank.py's match_transaction
    already uses. `mode` "set" is the original behavior (amount *is* the new
    balance); "adjust" does the arithmetic for you -- amount is a delta applied to
    the current balance_cents, signed by `direction` -- the "calculator style" ask.
    A "+" always raises balance_cents and a "-" always lowers it, identically for
    both entity types (for a debt that means charging more vs. paying down); no
    per-type sign flipping needed since it operates on the raw stored number."""
    target_type, target_id = parse_target(target, ("account", "debt"))
    amount_cents = parse_dollars_to_cents(amount)

    repo = accounts_repo if target_type == "account" else debts_repo
    current = accounts_repo.get_account(db, user_id, target_id) if target_type == "account" else debts_repo.get_debt(db, user_id, target_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if mode == "adjust":
        new_balance_cents = current["balance_cents"] + (amount_cents if direction == "+" else -amount_cents)
    else:
        new_balance_cents = amount_cents

    try:
        updated = repo.update_balance(db, user_id, target_id, new_balance_cents)
    except sqlite3.IntegrityError:
        db.rollback()
        return _response(
            request, db, user_id,
            '<p class="alert alert--error">That would take the balance below zero.</p>',
        )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.commit()
    return _response(request, db, user_id, '<p class="alert alert--success">Balance updated.</p>')


@router.post("/payment", dependencies=[Depends(verify_csrf_token)])
def quick_record_payment(
    request: Request,
    purchase_id: int = Form(...),
    amount: str = Form(...),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    try:
        result = payments.record_purchase_payment(db, user_id, purchase_id, parse_dollars_to_cents(amount))
    except sqlite3.IntegrityError:
        db.rollback()
        return _response(
            request, db, user_id,
            '<p class="alert alert--error">That\'s more than what\'s left on this purchase.</p>',
        )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.commit()
    return _response(request, db, user_id, '<p class="alert alert--success">Payment recorded.</p>')


@router.post("/purchase", dependencies=[Depends(verify_csrf_token)])
def quick_add_purchase(
    request: Request,
    name: str = Form(...),
    category: str = Form(...),
    amount: str = Form(...),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    purchases_repo.create_purchase(db, user_id, name, category, parse_dollars_to_cents(amount), "ordered")
    db.commit()
    return _response(request, db, user_id, '<p class="alert alert--success">Purchase added.</p>')


@router.post("/bill-paid/{obligation_id}", dependencies=[Depends(verify_csrf_token)])
def quick_mark_bill_paid(
    request: Request,
    obligation_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    updated = bills.mark_obligation_paid(db, user_id, obligation_id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.commit()
    # this button's own hx-target/hx-swap removes just its row; the OOB summary
    # fragment still needs to ride along in the same response to refresh safe-to-spend
    return _response(request, db, user_id, "")


@router.post("/afford", dependencies=[Depends(verify_csrf_token)])
def quick_afford_check(
    amount: str = Form(...),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    """Read-only — never commits, never mutates. Models "if I bought this right
    now" as an unconditionally-reserved (status='ordered') hypothetical purchase,
    the same conservative rule committed_purchases_reserved() already applies to
    real ordered purchases, then reuses the exact same calc.py functions via
    whatif.run_whatif — no new financial logic."""
    amount_cents = parse_dollars_to_cents(amount)
    if amount_cents < 0:
        return HTMLResponse('<p class="alert alert--error">Enter a positive amount.</p>')
    result = whatif.run_whatif(
        db, user_id, date.today(),
        hypothetical_purchases=[{"amount_cents": amount_cents, "status": "ordered"}],
    )
    remaining = result["hypothetical"]["safe_to_spend_cents"]
    if remaining >= 0:
        return HTMLResponse(
            f'<p class="alert alert--success">Yes — you\'d still have {format_cents(remaining)} left.</p>'
        )
    return HTMLResponse(
        f'<p class="alert alert--error">No — you\'d be {format_cents(abs(remaining))} short.</p>'
    )


@router.post("/income-received/{income_event_id}", dependencies=[Depends(verify_csrf_token)])
def quick_mark_income_received(
    request: Request,
    income_event_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    updated = bills.mark_income_received(db, user_id, income_event_id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.commit()
    return _response(request, db, user_id, "")
