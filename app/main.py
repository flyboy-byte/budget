import sqlite3

from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, SECURE_COOKIES
from app.routers import (
    accounts,
    auth,
    bank,
    committed_purchases,
    dashboard,
    debts,
    export,
    forecast,
    hubs,
    income_events,
    ledger,
    obligations,
    quick_actions,
    settings,
    snapshots,
)
from app.security import SESSION_COOKIE_MAX_AGE_SECONDS, SESSION_COOKIE_NAME

app = FastAPI(title="Budget")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")


@app.get("/sw.js")
def service_worker():
    # Served at the site root (not /static/sw.js) so its default scope is "/" --
    # a service worker can only control pages at or below the directory it's
    # served from, and everything under /static/ is assets, never an actual page.
    return FileResponse(BASE_DIR / "app" / "static" / "sw.js", media_type="text/javascript")
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(accounts.router)
app.include_router(debts.router)
app.include_router(obligations.router)
app.include_router(committed_purchases.router)
app.include_router(income_events.router)
app.include_router(forecast.router)
app.include_router(snapshots.router)
app.include_router(ledger.router)
app.include_router(export.router)
app.include_router(settings.router)
app.include_router(hubs.router)
app.include_router(quick_actions.router)
app.include_router(bank.router)


@app.middleware("http")
async def refresh_session_cookie_middleware(request: Request, call_next):
    """Slides the browser cookie's Max-Age forward alongside the server-side session
    (see get_current_user_id in app/deps.py) so an actively-used session never gets
    logged out just because 30 days have passed since the original login."""
    response = await call_next(request)
    session_id = getattr(request.state, "session_id", None)
    if session_id:
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_id,
            httponly=True,
            secure=SECURE_COOKIES,
            samesite="lax",
            max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
        )
    return response


@app.exception_handler(HTTPException)
async def redirect_unauthenticated_to_login(request: Request, exc: HTTPException):
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(url="/login", status_code=303)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(sqlite3.IntegrityError)
async def handle_integrity_error(request: Request, exc: sqlite3.IntegrityError):
    """CRUD routes don't re-validate every CHECK constraint (enum values, non-negative
    amounts, etc.) before hitting the DB — a request that violates one should get a
    clean 400, not a raw 500 with a stack trace."""
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": "Invalid data"})
