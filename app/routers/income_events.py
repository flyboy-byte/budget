import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.deps import get_csrf_token, get_current_user_id, get_db, verify_csrf_token
from app.money import parse_dollars_to_cents
from app.repositories import income_events as repo
from app.services.recurrence import advance_date
from app.templating import templates

router = APIRouter(prefix="/income-events")


@router.get("")
def list_income_events(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "income_events/list.html",
        {"income_events": repo.list_income_events(db, user_id), "csrf_token": csrf_token},
    )


@router.get("/new")
def new_income_event_form(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "income_events/form.html",
        {
            "event": None,
            "action": "/income-events",
            "confidence_levels": repo.CONFIDENCE_LEVELS,
            "recurrence_rules": repo.RECURRENCE_RULES,
            "csrf_token": csrf_token,
        },
    )


@router.post("", dependencies=[Depends(verify_csrf_token)])
def create_income_event(
    source: str = Form(...),
    amount: str = Form(...),
    expected_date: str = Form(...),
    confidence: str = Form(...),
    is_recurring: str = Form(None),
    recurrence_rule: str = Form(None),
    notes: str = Form(""),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    repo.create_income_event(
        db,
        user_id,
        source,
        parse_dollars_to_cents(amount),
        expected_date,
        confidence,
        is_recurring=1 if is_recurring else 0,
        recurrence_rule=recurrence_rule or None,
        notes=notes or None,
    )
    db.commit()
    return RedirectResponse(url="/income-events", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{income_event_id}/edit")
def edit_income_event_form(
    income_event_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    event = repo.get_income_event(db, user_id, income_event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "income_events/form.html",
        {
            "event": event,
            "action": f"/income-events/{income_event_id}",
            "confidence_levels": repo.CONFIDENCE_LEVELS,
            "recurrence_rules": repo.RECURRENCE_RULES,
            "csrf_token": csrf_token,
        },
    )


@router.post("/{income_event_id}", dependencies=[Depends(verify_csrf_token)])
def update_income_event(
    income_event_id: int,
    source: str = Form(...),
    amount: str = Form(...),
    expected_date: str = Form(...),
    confidence: str = Form(...),
    is_received: str = Form(None),
    received_date: str = Form(None),
    received_amount: str = Form(None),
    is_recurring: str = Form(None),
    recurrence_rule: str = Form(None),
    notes: str = Form(""),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    recurring = bool(is_recurring)
    received = bool(is_received)
    received_amount_cents = parse_dollars_to_cents(received_amount) if received_amount else None

    if received and recurring and recurrence_rule:
        # Marking a recurring paycheck received rolls it forward to the next
        # occurrence, same row — matches the obligations roll-forward model.
        next_expected_date = advance_date(expected_date, recurrence_rule)
        updated = repo.update_income_event(
            db, user_id, income_event_id, source, parse_dollars_to_cents(amount),
            next_expected_date, confidence, is_received=0,
            received_date=date.today().isoformat(), received_amount_cents=received_amount_cents,
            is_recurring=1, recurrence_rule=recurrence_rule, notes=notes or None,
        )
    else:
        updated = repo.update_income_event(
            db, user_id, income_event_id, source, parse_dollars_to_cents(amount),
            expected_date, confidence, is_received=1 if received else 0,
            received_date=received_date or None, received_amount_cents=received_amount_cents,
            is_recurring=1 if recurring else 0, recurrence_rule=recurrence_rule or None,
            notes=notes or None,
        )
    db.commit()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return RedirectResponse(url="/income-events", status_code=status.HTTP_303_SEE_OTHER)


@router.delete("/{income_event_id}", dependencies=[Depends(verify_csrf_token)])
def delete_income_event(
    income_event_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    deleted = repo.delete_income_event(db, user_id, income_event_id)
    db.commit()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return HTMLResponse("")
