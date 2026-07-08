import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, Request

from app.deps import get_csrf_token, get_current_user_id, get_db, verify_csrf_token
from app.money import parse_dollars_to_cents
from app.repositories.committed_purchases import STATUSES as PURCHASE_STATUSES
from app.repositories.debts import INTEREST_STATUSES
from app.repositories.income_events import CONFIDENCE_LEVELS
from app.services import calc, whatif
from app.templating import templates

router = APIRouter(prefix="/forecast")

CONFIRM_URLS = {
    "obligation": "/obligations/new",
    "purchase": "/committed-purchases/new",
    "debt": "/debts/new",
    "income": "/income-events/new",
}


def _base_context(db: sqlite3.Connection, user_id: int, today: date) -> dict:
    return {
        "result": {"real": whatif.run_whatif(db, user_id, today)["real"]},
        "forecast_window_days": int(calc.get_setting(db, user_id, "forecast_window_days")),
        "submitted": False,
        "entity_type": "obligation",
        "purchase_statuses": PURCHASE_STATUSES,
        "interest_statuses": INTEREST_STATUSES,
        "confidence_levels": CONFIDENCE_LEVELS,
    }


@router.get("")
def forecast_index(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    context = _base_context(db, user_id, date.today())
    context["csrf_token"] = csrf_token
    return templates.TemplateResponse(request, "forecast/index.html", context)


@router.post("", dependencies=[Depends(verify_csrf_token)])
def forecast_submit(
    request: Request,
    entity_type: str = Form(...),
    description: str = Form(""),
    amount: str = Form("0.00"),
    date_input: str = Form("", alias="date"),
    status: str = Form("ordered"),
    minimum_payment: str = Form("0.00"),
    interest_status: str = Form("accruing"),
    confidence: str = Form("confirmed"),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    today = date.today()
    amount_cents = parse_dollars_to_cents(amount) if amount else 0

    kwargs = {}
    if entity_type == "obligation":
        kwargs["hypothetical_obligations"] = [
            {"amount_cents": amount_cents, "due_date": date_input or today.isoformat()}
        ]
    elif entity_type == "purchase":
        kwargs["hypothetical_purchases"] = [
            {
                "amount_cents": amount_cents,
                "status": status,
                "payment_deadline": date_input or None,
            }
        ]
    elif entity_type == "debt":
        kwargs["hypothetical_debts"] = [
            {
                "balance_cents": amount_cents,
                "minimum_payment_cents": parse_dollars_to_cents(minimum_payment) if minimum_payment else 0,
                "next_due_date": date_input or None,
                "interest_status": interest_status,
            }
        ]
    elif entity_type == "income":
        kwargs["hypothetical_income_events"] = [
            {
                "expected_amount_cents": amount_cents,
                "expected_date": date_input or today.isoformat(),
                "confidence": confidence,
            }
        ]

    result = whatif.run_whatif(db, user_id, today, **kwargs)

    context = {
        "result": result,
        "forecast_window_days": int(calc.get_setting(db, user_id, "forecast_window_days")),
        "submitted": True,
        "entity_type": entity_type,
        "description": description,
        "amount": amount,
        "date_value": date_input,
        "status": status,
        "minimum_payment": minimum_payment,
        "interest_status": interest_status,
        "confidence": confidence,
        "purchase_statuses": PURCHASE_STATUSES,
        "interest_statuses": INTEREST_STATUSES,
        "confidence_levels": CONFIDENCE_LEVELS,
        "confirm_url": CONFIRM_URLS[entity_type],
        "csrf_token": csrf_token,
    }
    return templates.TemplateResponse(request, "forecast/index.html", context)
