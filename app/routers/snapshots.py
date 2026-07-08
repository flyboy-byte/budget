import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.deps import get_csrf_token, get_current_user_id, get_db, verify_csrf_token
from app.repositories import snapshots as repo
from app.services import calc
from app.templating import templates

router = APIRouter(prefix="/snapshots")


@router.get("")
def list_snapshots(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "snapshots/list.html",
        {"snapshots": repo.list_snapshots(db, user_id), "csrf_token": csrf_token},
    )


@router.post("", dependencies=[Depends(verify_csrf_token)])
def capture_snapshot(
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    today = date.today()
    values = calc.snapshot_values(db, user_id, today)
    repo.upsert_snapshot(db, user_id, today.isoformat(), values)
    db.commit()
    return RedirectResponse(url="/snapshots", status_code=status.HTTP_303_SEE_OTHER)


@router.delete("/{snapshot_id}", dependencies=[Depends(verify_csrf_token)])
def delete_snapshot(
    snapshot_id: int,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    deleted = repo.delete_snapshot(db, user_id, snapshot_id)
    db.commit()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return HTMLResponse("")
