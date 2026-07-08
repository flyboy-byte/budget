import sqlite3

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.deps import get_csrf_token, get_current_user_id, get_db, verify_csrf_token
from app.money import parse_dollars_to_cents
from app.repositories import accounts as repo
from app.templating import templates

router = APIRouter(prefix="/accounts")


@router.get("")
def list_accounts(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request, "accounts/list.html", {"accounts": repo.list_accounts(db, user_id), "csrf_token": csrf_token}
    )


@router.get("/new")
def new_account_form(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "accounts/form.html",
        {
            "account": None,
            "action": "/accounts",
            "account_types": repo.list_distinct_types(db, user_id),
            "csrf_token": csrf_token,
        },
    )


@router.post("", dependencies=[Depends(verify_csrf_token)])
def create_account(
    name: str = Form(...),
    type: str = Form(...),
    balance: str = Form(...),
    notes: str = Form(""),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    repo.create_account(db, user_id, name, type, parse_dollars_to_cents(balance), notes or None)
    db.commit()
    return RedirectResponse(url="/accounts", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{account_id}/edit")
def edit_account_form(
    account_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    account = repo.get_account(db, user_id, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "accounts/form.html",
        {
            "account": account,
            "action": f"/accounts/{account_id}",
            "account_types": repo.list_distinct_types(db, user_id),
            "csrf_token": csrf_token,
        },
    )


@router.post("/{account_id}", dependencies=[Depends(verify_csrf_token)])
def update_account(
    account_id: int,
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    balance: str = Form(...),
    is_active: str = Form(None),
    notes: str = Form(""),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    balance_cents = parse_dollars_to_cents(balance)
    if not is_active and balance_cents != 0:
        account = repo.get_account(db, user_id, account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return templates.TemplateResponse(
            request,
            "accounts/form.html",
            {
                "account": account,
                "action": f"/accounts/{account_id}",
                "account_types": repo.list_distinct_types(db, user_id),
                "csrf_token": csrf_token,
                "error": "Can't deactivate an account with a nonzero balance "
                         f"(${balance_cents / 100:,.2f}) — it would silently vanish from cash on hand. "
                         "Zero out the balance first, or leave it active.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    updated = repo.update_account(
        db,
        user_id,
        account_id,
        name,
        type,
        balance_cents,
        is_active=1 if is_active else 0,
        notes=notes or None,
    )
    db.commit()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return RedirectResponse(url="/accounts", status_code=status.HTTP_303_SEE_OTHER)


@router.delete("/{account_id}", dependencies=[Depends(verify_csrf_token)])
def delete_account(
    account_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    deleted = repo.delete_account(db, user_id, account_id)
    db.commit()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return HTMLResponse("")
