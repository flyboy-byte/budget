import sqlite3

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.deps import get_csrf_token, get_current_user_id, get_db, verify_csrf_token
from app.money import parse_dollars_to_cents
from app.repositories import committed_purchases as repo
from app.templating import templates

router = APIRouter(prefix="/committed-purchases")


def _validate_paid_not_over_total(amount_cents: int, amount_paid_cents: int) -> None:
    if amount_paid_cents > amount_cents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Amount paid cannot exceed total amount"
        )


@router.get("")
def list_purchases(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "committed_purchases/list.html",
        {"purchases": repo.list_purchases(db, user_id), "csrf_token": csrf_token},
    )


@router.get("/new")
def new_purchase_form(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "committed_purchases/form.html",
        {
            "purchase": None,
            "action": "/committed-purchases",
            "categories": repo.list_distinct_categories(db, user_id),
            "statuses": repo.STATUSES,
            "csrf_token": csrf_token,
        },
    )


@router.post("", dependencies=[Depends(verify_csrf_token)])
def create_purchase(
    name: str = Form(...),
    category: str = Form(...),
    amount: str = Form(...),
    amount_paid: str = Form("0.00"),
    status_: str = Form(..., alias="status"),
    order_date: str = Form(None),
    expected_arrival_date: str = Form(None),
    payment_deadline: str = Form(None),
    priority: int = Form(0),
    notes: str = Form(""),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    amount_cents = parse_dollars_to_cents(amount)
    amount_paid_cents = parse_dollars_to_cents(amount_paid)
    _validate_paid_not_over_total(amount_cents, amount_paid_cents)

    repo.create_purchase(
        db,
        user_id,
        name,
        category,
        amount_cents,
        status_,
        amount_paid_cents=amount_paid_cents,
        order_date=order_date or None,
        expected_arrival_date=expected_arrival_date or None,
        payment_deadline=payment_deadline or None,
        priority=priority,
        notes=notes or None,
    )
    db.commit()
    return RedirectResponse(url="/committed-purchases", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{purchase_id}/edit")
def edit_purchase_form(
    purchase_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    purchase = repo.get_purchase(db, user_id, purchase_id)
    if purchase is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "committed_purchases/form.html",
        {
            "purchase": purchase,
            "action": f"/committed-purchases/{purchase_id}",
            "categories": repo.list_distinct_categories(db, user_id),
            "statuses": repo.STATUSES,
            "csrf_token": csrf_token,
        },
    )


@router.post("/{purchase_id}", dependencies=[Depends(verify_csrf_token)])
def update_purchase(
    purchase_id: int,
    name: str = Form(...),
    category: str = Form(...),
    amount: str = Form(...),
    amount_paid: str = Form("0.00"),
    status_: str = Form(..., alias="status"),
    order_date: str = Form(None),
    expected_arrival_date: str = Form(None),
    payment_deadline: str = Form(None),
    priority: int = Form(0),
    notes: str = Form(""),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    amount_cents = parse_dollars_to_cents(amount)
    amount_paid_cents = parse_dollars_to_cents(amount_paid)
    _validate_paid_not_over_total(amount_cents, amount_paid_cents)

    updated = repo.update_purchase(
        db,
        user_id,
        purchase_id,
        name,
        category,
        amount_cents,
        amount_paid_cents,
        status_,
        order_date=order_date or None,
        expected_arrival_date=expected_arrival_date or None,
        payment_deadline=payment_deadline or None,
        priority=priority,
        notes=notes or None,
    )
    db.commit()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return RedirectResponse(url="/committed-purchases", status_code=status.HTTP_303_SEE_OTHER)


@router.delete("/{purchase_id}", dependencies=[Depends(verify_csrf_token)])
def delete_purchase(
    purchase_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    deleted = repo.delete_purchase(db, user_id, purchase_id)
    db.commit()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return HTMLResponse("")
