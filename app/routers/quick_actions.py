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

from app.deps import get_current_user_id, get_db, verify_csrf_token
from app.money import format_cents, parse_dollars_to_cents
from app.repositories import accounts as accounts_repo
from app.repositories import committed_purchases as purchases_repo
from app.routers.dashboard import build_dashboard_context
from app.services import bills, payments, whatif
from app.templating import templates

router = APIRouter(prefix="/today")


def _summary_fragment(request: Request, db: sqlite3.Connection, user_id: int) -> str:
    context = build_dashboard_context(db, user_id)
    return templates.get_template("partials/_dashboard_summary.html").render(
        {"request": request, **context}
    )


def _response(request: Request, db: sqlite3.Connection, user_id: int, confirmation: str) -> HTMLResponse:
    return HTMLResponse(confirmation + _summary_fragment(request, db, user_id))


@router.post("/balance", dependencies=[Depends(verify_csrf_token)])
def quick_update_balance(
    request: Request,
    account_id: int = Form(...),
    balance: str = Form(...),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    updated = accounts_repo.update_balance(db, user_id, account_id, parse_dollars_to_cents(balance))
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
