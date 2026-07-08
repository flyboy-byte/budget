import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.deps import get_csrf_token, get_current_user_id, get_db, verify_csrf_token
from app.money import parse_dollars_to_cents
from app.repositories import obligations as repo
from app.services.recurrence import advance_date
from app.templating import templates

router = APIRouter(prefix="/obligations")


@router.get("")
def list_obligations(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "obligations/list.html",
        {"obligations": repo.list_obligations(db, user_id), "csrf_token": csrf_token},
    )


@router.get("/new")
def new_obligation_form(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "obligations/form.html",
        {
            "obligation": None,
            "action": "/obligations",
            "recurrence_rules": repo.RECURRENCE_RULES,
            "categories": repo.list_distinct_categories(db, user_id),
            "csrf_token": csrf_token,
        },
    )


@router.post("", dependencies=[Depends(verify_csrf_token)])
def create_obligation(
    name: str = Form(...),
    category: str = Form(...),
    amount: str = Form(...),
    due_date: str = Form(...),
    is_recurring: str = Form(None),
    recurrence_rule: str = Form(None),
    is_required: str = Form(None),
    auto_pay: str = Form(None),
    notes: str = Form(""),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    repo.create_obligation(
        db,
        user_id,
        name,
        category,
        parse_dollars_to_cents(amount),
        due_date,
        is_recurring=1 if is_recurring else 0,
        recurrence_rule=recurrence_rule or None,
        is_required=1 if is_required else 0,
        auto_pay=1 if auto_pay else 0,
        notes=notes or None,
    )
    db.commit()
    return RedirectResponse(url="/obligations", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{obligation_id}/edit")
def edit_obligation_form(
    obligation_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    obligation = repo.get_obligation(db, user_id, obligation_id)
    if obligation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "obligations/form.html",
        {
            "obligation": obligation,
            "action": f"/obligations/{obligation_id}",
            "recurrence_rules": repo.RECURRENCE_RULES,
            "categories": repo.list_distinct_categories(db, user_id),
            "csrf_token": csrf_token,
        },
    )


@router.post("/{obligation_id}", dependencies=[Depends(verify_csrf_token)])
def update_obligation(
    obligation_id: int,
    name: str = Form(...),
    category: str = Form(...),
    amount: str = Form(...),
    due_date: str = Form(...),
    is_paid: str = Form(None),
    is_recurring: str = Form(None),
    recurrence_rule: str = Form(None),
    is_required: str = Form(None),
    auto_pay: str = Form(None),
    notes: str = Form(""),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    recurring = bool(is_recurring)
    paid = bool(is_paid)

    if paid and recurring and recurrence_rule:
        # Marking a recurring obligation paid doesn't just check a box and leave it
        # there — it rolls forward to the next occurrence, same row, per the
        # roll-forward model: one row is always "the next occurrence."
        next_due_date = advance_date(due_date, recurrence_rule)
        updated = repo.update_obligation(
            db, user_id, obligation_id, name, category, parse_dollars_to_cents(amount),
            next_due_date, is_paid=0, is_recurring=1, recurrence_rule=recurrence_rule,
            is_required=1 if is_required else 0, auto_pay=1 if auto_pay else 0,
            notes=notes or None, paid_date=date.today().isoformat(),
        )
    else:
        updated = repo.update_obligation(
            db, user_id, obligation_id, name, category, parse_dollars_to_cents(amount),
            due_date, is_paid=1 if paid else 0, is_recurring=1 if recurring else 0,
            recurrence_rule=recurrence_rule or None, is_required=1 if is_required else 0,
            auto_pay=1 if auto_pay else 0, notes=notes or None,
            paid_date=date.today().isoformat() if paid else None,
        )
    db.commit()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return RedirectResponse(url="/obligations", status_code=status.HTTP_303_SEE_OTHER)


@router.delete("/{obligation_id}", dependencies=[Depends(verify_csrf_token)])
def delete_obligation(
    obligation_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    deleted = repo.delete_obligation(db, user_id, obligation_id)
    db.commit()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return HTMLResponse("")
