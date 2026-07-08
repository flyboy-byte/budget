"""'Mark paid/received' for the quick-actions ('Today') screen — operates on the
currently-stored row only (no form-submitted field changes), unlike the full edit
forms in app/routers/obligations.py / income_events.py, which let you edit other
fields in the same submit. The recurring-roll-forward *rule* itself lives in
app.services.recurrence.advance_date and is shared; the surrounding orchestration
here is intentionally the simpler, stored-data-only case.
"""
import sqlite3
from datetime import date

from app.repositories import accounts as accounts_repo
from app.repositories import income_events as income_repo
from app.repositories import obligations as obligations_repo
from app.repositories import transactions as transactions_repo
from app.services.recurrence import advance_date


def mark_obligation_paid(conn: sqlite3.Connection, user_id: int, obligation_id: int) -> bool:
    obligation = obligations_repo.get_obligation(conn, user_id, obligation_id)
    if obligation is None:
        return False

    if obligation["is_recurring"] and obligation["recurrence_rule"]:
        next_due_date = advance_date(obligation["due_date"], obligation["recurrence_rule"])
        updated = obligations_repo.update_obligation(
            conn, user_id, obligation_id,
            obligation["name"], obligation["category"], obligation["amount_cents"],
            next_due_date, is_paid=0, is_recurring=1,
            recurrence_rule=obligation["recurrence_rule"],
            is_required=obligation["is_required"], auto_pay=obligation["auto_pay"],
            notes=obligation["notes"], paid_date=date.today().isoformat(),
        )
    else:
        updated = obligations_repo.update_obligation(
            conn, user_id, obligation_id,
            obligation["name"], obligation["category"], obligation["amount_cents"],
            obligation["due_date"], is_paid=1, is_recurring=0, recurrence_rule=None,
            is_required=obligation["is_required"], auto_pay=obligation["auto_pay"],
            notes=obligation["notes"], paid_date=date.today().isoformat(),
        )

    if updated:
        account = accounts_repo.get_default_account(conn, user_id)
        if account is not None:
            accounts_repo.adjust_balance(conn, user_id, account["id"], -obligation["amount_cents"])
        transactions_repo.create_transaction(
            conn, user_id, date.today().isoformat(), obligation["amount_cents"], "obligation",
            account_id=account["id"] if account is not None else None,
            obligation_id=obligation_id, memo=obligation["name"],
        )
    return updated


def mark_income_received(conn: sqlite3.Connection, user_id: int, income_event_id: int) -> bool:
    event = income_repo.get_income_event(conn, user_id, income_event_id)
    if event is None:
        return False

    if event["is_recurring"] and event["recurrence_rule"]:
        next_expected_date = advance_date(event["expected_date"], event["recurrence_rule"])
        updated = income_repo.update_income_event(
            conn, user_id, income_event_id,
            event["source"], event["expected_amount_cents"], next_expected_date,
            event["confidence"], is_received=0, received_date=date.today().isoformat(),
            received_amount_cents=event["expected_amount_cents"],
            is_recurring=1, recurrence_rule=event["recurrence_rule"], notes=event["notes"],
        )
    else:
        updated = income_repo.update_income_event(
            conn, user_id, income_event_id,
            event["source"], event["expected_amount_cents"], event["expected_date"],
            event["confidence"], is_received=1, received_date=date.today().isoformat(),
            received_amount_cents=event["expected_amount_cents"],
            is_recurring=0, recurrence_rule=None, notes=event["notes"],
        )

    if updated:
        account = accounts_repo.get_default_account(conn, user_id)
        if account is not None:
            accounts_repo.adjust_balance(conn, user_id, account["id"], event["expected_amount_cents"])
        transactions_repo.create_transaction(
            conn, user_id, date.today().isoformat(), event["expected_amount_cents"], "other",
            account_id=account["id"] if account is not None else None,
            memo=f"Income: {event['source']}",
        )
    return updated
