import json
import sqlite3

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.exceptions import HTTPException
from fastapi.responses import Response

from app.deps import get_csrf_token, get_current_user_id, get_db, verify_csrf_token
from app.services import export
from app.templating import templates

router = APIRouter(prefix="/export")


@router.get("")
def export_index(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    csrf_token: str = Depends(get_csrf_token),
):
    return templates.TemplateResponse(
        request,
        "export/index.html",
        {
            "csv_tables": export.CSV_TABLES,
            "csrf_token": csrf_token,
            "restore_error": None,
            "restore_success": False,
        },
    )


@router.get("/csv/{table}")
def export_csv(
    table: str,
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    if table not in export.CSV_TABLES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    csv_text = export.export_table_csv(db, user_id, table)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{export.csv_filename(table)}"'},
    )


@router.get("/csv-combined")
def export_csv_combined(
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    csv_text = export.export_combined_csv(db, user_id)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{export.combined_csv_filename()}"'},
    )


@router.get("/json")
def export_json(
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
):
    row = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    backup = export.export_json_backup(db, user_id)
    return Response(
        content=export.to_json_bytes(backup),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{export.backup_filename(row["username"])}"'},
    )


@router.post("/restore", dependencies=[Depends(verify_csrf_token)])
async def restore_backup(
    request: Request,
    backup_file: UploadFile = File(...),
    confirm: str = Form(None),
    user_id: int = Depends(get_current_user_id),
    db: sqlite3.Connection = Depends(get_db),
    csrf_token: str = Depends(get_csrf_token),
):
    error = None
    success = False

    if not confirm:
        error = "You must confirm before restoring."
    else:
        try:
            raw = await backup_file.read()
            backup = json.loads(raw)
            export.restore_json_backup(db, user_id, backup)
            db.commit()
            success = True
        except (json.JSONDecodeError, export.RestoreError) as exc:
            db.rollback()
            error = f"Restore failed: {exc}"

    return templates.TemplateResponse(
        request,
        "export/index.html",
        {
            "csv_tables": export.CSV_TABLES,
            "csrf_token": csrf_token,
            "restore_error": error,
            "restore_success": success,
        },
    )
