"""Daily digest email — safe-to-spend plus what's due soon, sent via Resend's HTTP
API (https://api.resend.com/emails) using the httpx dependency already in this app.
No new financial logic: every figure here is read straight from calc.py/narrative.py,
the same functions the dashboard uses. Skips silently (returns False) when a user
hasn't set a digest_email in Settings — this is opt-in, not on by default.
"""
import sqlite3
from datetime import date, timedelta

import httpx

from app import config
from app.money import format_cents
from app.repositories import bank_sync as bank_sync_repo
from app.repositories import settings as settings_repo
from app.repositories import snapshots as snapshots_repo
from app.repositories import transactions as transactions_repo
from app.services import calc, narrative
from app.services.dates import human_date

_TIMEOUT = httpx.Timeout(10.0)


class DigestError(Exception):
    """Any failure sending via Resend — bad/missing key, network error, non-2xx."""


ACTIVITY_WINDOW_DAYS = 7


def _activity_summary(conn: sqlite3.Connection, user_id: int, today: date) -> list[str]:
    """What actually happened lately, in plain language. Leads the email because a
    standing balance repeated verbatim every morning reads as noise — this digest
    went out with a byte-identical safe-to-spend figure six days running before
    this section existed, and the user's read on it was that the app felt dead."""
    since = (today - timedelta(days=ACTIVITY_WINDOW_DAYS)).isoformat()
    rows = transactions_repo.list_since(conn, user_id, since)

    spending = [r for r in rows if r["target_type"] == "spending"]
    bills_paid = [r for r in rows if r["target_type"] in ("obligation", "committed_purchase", "debt")]
    income = [r for r in rows if r["target_type"] == "other"]

    lines = []
    if spending:
        total = sum(r["amount_cents"] for r in spending)
        noun = "purchase" if len(spending) == 1 else "purchases"
        lines.append(f"You spent {format_cents(total)} across {len(spending)} {noun}.")
    if bills_paid:
        total = sum(r["amount_cents"] for r in bills_paid)
        noun = "commitment" if len(bills_paid) == 1 else "commitments"
        lines.append(f"You cleared {format_cents(total)} of {noun} ({len(bills_paid)} paid).")
    if income:
        total = sum(r["amount_cents"] for r in income)
        lines.append(f"Money in: {format_cents(total)}.")

    if not lines:
        lines.append(f"Nothing recorded in the last {ACTIVITY_WINDOW_DAYS} days.")
    return lines


def _body(conn: sqlite3.Connection, user_id: int, today: date) -> str:
    window_end = calc.reserved_window_end(conn, user_id, today)
    safe_to_spend_cents = calc.safe_to_spend(conn, user_id, today)
    cash_on_hand_cents = calc.cash_on_hand(conn, user_id)
    total_debt_cents = calc.total_debt(conn, user_id)
    reserved_cash_cents = calc.reserved_cash(conn, user_id, window_end)
    next_due = calc.next_due_payment(conn, user_id)
    change = narrative.build_change_narrative(conn, user_id, today)

    lines = ["WHAT HAPPENED"]
    if change:
        lines.append(change)
    lines.extend(_activity_summary(conn, user_id, today))

    lines.append("")
    lines.append("COMING UP")
    if next_due:
        lines.append(
            f"{next_due['name']} ({next_due['kind']}) — "
            f"{format_cents(next_due['amount_cents'])} due {human_date(next_due['due_date'], today)}"
        )
    else:
        lines.append("Nothing scheduled. If that's wrong, the app doesn't know about your bills yet.")

    unmatched = len(bank_sync_repo.list_unmatched_transactions(conn, user_id))
    if unmatched:
        noun = "transaction" if unmatched == 1 else "transactions"
        lines.append(f"{unmatched} bank {noun} waiting to be sorted (Money → Match Transactions).")

    lines.append("")
    lines.append("WHERE YOU STAND")
    lines.append(f"Safe to spend: {format_cents(safe_to_spend_cents)}")
    lines.append(f"Cash on hand: {format_cents(cash_on_hand_cents)}")
    lines.append(f"Reserved (already spoken for): {format_cents(reserved_cash_cents)}")
    lines.append(f"Total debt: {format_cents(total_debt_cents)}")
    lines.append("Doesn't include future paychecks.")
    return "\n".join(lines)


def _subject(conn: sqlite3.Connection, user_id: int, today: date) -> str:
    """Puts the movement in the subject line so the mornings where something
    actually changed are distinguishable from the mailbox list, without opening."""
    safe_to_spend_cents = calc.safe_to_spend(conn, user_id, today)
    yesterday = (today - timedelta(days=1)).isoformat()
    prior = snapshots_repo.get_snapshot_by_date(conn, user_id, yesterday)

    if prior is not None:
        delta = safe_to_spend_cents - prior["safe_to_spend_cents"]
        if delta:
            direction = "up" if delta > 0 else "down"
            return (
                f"Budget: {direction} {format_cents(abs(delta))} — "
                f"{format_cents(safe_to_spend_cents)} safe to spend"
            )
    return f"Budget: {format_cents(safe_to_spend_cents)} safe to spend"


def send_digest(
    conn: sqlite3.Connection,
    user_id: int,
    today: date | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    today = today or date.today()
    digest_email = settings_repo.get_all(conn, user_id).get("digest_email", "").strip()
    if not digest_email:
        return False

    if not config.RESEND_API_KEY:
        raise DigestError("BUDGET_RESEND_API_KEY is not set.")

    payload = {
        "from": config.DIGEST_FROM_EMAIL,
        "to": [digest_email],
        "subject": _subject(conn, user_id, today),
        "text": _body(conn, user_id, today),
    }

    with httpx.Client(transport=transport, timeout=_TIMEOUT) as client:
        try:
            response = client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
            )
        except httpx.HTTPError as exc:
            raise DigestError(f"Could not reach Resend: {exc}") from exc

    if response.status_code >= 300:
        raise DigestError(f"Resend returned {response.status_code}: {response.text}")
    return True
