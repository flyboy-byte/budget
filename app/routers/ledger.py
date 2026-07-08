"""Read-only view over the transactions ledger.

The ledger has been written to since the app's first release — by bills.py,
payments.py and bank_transactions.py — but until now no route ever read it back,
so every quick action a user took vanished into a table they could never see.
This is that missing half: nothing here mutates anything.
"""
import sqlite3

from fastapi import APIRouter, Depends, Request

from app.deps import get_csrf_token, get_current_user_id, get_db
from app.repositories import transactions as repo
from app.templating import templates

router = APIRouter(prefix="/ledger")

PAGE_SIZE = 100


@router.get("")
def list_ledger(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "ledger/list.html",
        {
            "entries": repo.list_recent(db, user_id, PAGE_SIZE),
            "total_count": repo.count_for_user(db, user_id),
            "page_size": PAGE_SIZE,
            "csrf_token": csrf_token,
        },
    )
