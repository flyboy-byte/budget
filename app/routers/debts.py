import sqlite3

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.deps import get_csrf_token, get_current_user_id, get_db, verify_csrf_token
from app.money import parse_dollars_to_cents, parse_percent_to_bps
from app.repositories import debts as repo
from app.services import calc
from app.templating import templates

router = APIRouter(prefix="/debts")


def _is_incomplete(debt: sqlite3.Row) -> bool:
    """A debt missing its minimum payment or next due date silently never counts
    toward reserved cash (calc.py only sums minimums with a due date inside the
    reserved-cash window) — flagged as an ongoing indicator, not just the one-time
    from_bank_sync banner shown right after creation. Presentational only, doesn't
    touch calc.py."""
    return debt["minimum_payment_cents"] == 0 or debt["next_due_date"] is None


@router.get("")
def list_debts(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    debts = repo.list_debts(db, user_id)
    payoffs = {
        debt["id"]: calc.debt_payoff_projection(
            debt["balance_cents"], debt["apr_bps"], debt["minimum_payment_cents"], debt["interest_status"]
        )
        for debt in debts
    }
    incomplete_debt_ids = {debt["id"] for debt in debts if _is_incomplete(debt)}
    return templates.TemplateResponse(
        request,
        "debts/list.html",
        {
            "debts": debts,
            "debt_payoffs": payoffs,
            "incomplete_debt_ids": incomplete_debt_ids,
            "csrf_token": csrf_token,
        },
    )


@router.get("/new")
def new_debt_form(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "debts/form.html",
        {
            "debt": None,
            "action": "/debts",
            "debt_types": repo.list_distinct_types(db, user_id),
            "interest_statuses": repo.INTEREST_STATUSES,
            "csrf_token": csrf_token,
        },
    )


@router.post("", dependencies=[Depends(verify_csrf_token)])
def create_debt(
    name: str = Form(...),
    type: str = Form(...),
    balance: str = Form(...),
    minimum_payment: str = Form(...),
    apr: str = Form(None),
    next_due_date: str = Form(None),
    interest_status: str = Form(...),
    is_flexible_payment: str = Form(None),
    priority: int = Form(0),
    notes: str = Form(""),
    coarse_tracking: str = Form(None),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    repo.create_debt(
        db,
        user_id,
        name,
        type,
        parse_dollars_to_cents(balance),
        parse_dollars_to_cents(minimum_payment),
        interest_status,
        apr_bps=parse_percent_to_bps(apr),
        next_due_date=next_due_date or None,
        is_flexible_payment=1 if is_flexible_payment else 0,
        priority=priority,
        notes=notes or None,
        coarse_tracking=1 if coarse_tracking else 0,
    )
    db.commit()
    return RedirectResponse(url="/debts", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{debt_id}/edit")
def edit_debt_form(
    debt_id: int,
    request: Request,
    from_bank_sync: str = None,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    debt = repo.get_debt(db, user_id, debt_id)
    if debt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "debts/form.html",
        {
            "debt": debt,
            "action": f"/debts/{debt_id}",
            "debt_types": repo.list_distinct_types(db, user_id),
            "interest_statuses": repo.INTEREST_STATUSES,
            "csrf_token": csrf_token,
            "info": (
                "This debt was just linked from bank sync — set its minimum payment and "
                "next due date now, or it won't count toward reserved cash."
            ) if from_bank_sync else None,
        },
    )


@router.post("/{debt_id}", dependencies=[Depends(verify_csrf_token)])
def update_debt(
    debt_id: int,
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    balance: str = Form(...),
    minimum_payment: str = Form(...),
    apr: str = Form(None),
    next_due_date: str = Form(None),
    interest_status: str = Form(...),
    is_flexible_payment: str = Form(None),
    priority: int = Form(0),
    is_active: str = Form(None),
    notes: str = Form(""),
    coarse_tracking: str = Form(None),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    balance_cents = parse_dollars_to_cents(balance)
    if not is_active and balance_cents != 0:
        debt = repo.get_debt(db, user_id, debt_id)
        if debt is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return templates.TemplateResponse(
            request,
            "debts/form.html",
            {
                "debt": debt,
                "action": f"/debts/{debt_id}",
                "debt_types": repo.list_distinct_types(db, user_id),
                "interest_statuses": repo.INTEREST_STATUSES,
                "csrf_token": csrf_token,
                "error": "Can't deactivate a debt with a nonzero balance "
                         f"(${balance_cents / 100:,.2f}) — it would silently vanish from total debt. "
                         "Zero out the balance first, or leave it active.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    updated = repo.update_debt(
        db,
        user_id,
        debt_id,
        name,
        type,
        balance_cents,
        parse_dollars_to_cents(minimum_payment),
        interest_status,
        is_active=1 if is_active else 0,
        apr_bps=parse_percent_to_bps(apr),
        next_due_date=next_due_date or None,
        is_flexible_payment=1 if is_flexible_payment else 0,
        priority=priority,
        notes=notes or None,
        coarse_tracking=1 if coarse_tracking else 0,
    )
    db.commit()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return RedirectResponse(url="/debts", status_code=status.HTTP_303_SEE_OTHER)


@router.delete("/{debt_id}", dependencies=[Depends(verify_csrf_token)])
def delete_debt(
    debt_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    deleted = repo.delete_debt(db, user_id, debt_id)
    db.commit()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return HTMLResponse("")
