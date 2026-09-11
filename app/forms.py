"""Parsing for user-supplied form values that become row ids.

Anything arriving as a form field is a string of the user's choosing, so the
conversion to an int has to be part of request validation, not an afterthought:
bare int() raises ValueError on "abc" and sqlite3 raises OverflowError past a
signed 64-bit value, and either one surfaces as a 500 for what is really a bad
request. Path parameters don't need this -- FastAPI already coerces and rejects
those from the type annotation -- so these helpers are specifically for Form()
values and request.form() lists.

Mirrors app/money.py's convention of raising HTTPException(400) directly from the
parse, so routers stay a thin layer over a validated value.
"""
from fastapi import HTTPException, status

# Largest value sqlite3 accepts for an INTEGER column. Python ints are unbounded,
# so without this an oversized id reaches the driver and raises OverflowError
# instead of simply matching no rows.
SQLITE_MAX_INT = 2**63 - 1


def parse_row_id(value: str) -> int:
    """A positive row id, or HTTPException(400)."""
    text = (value or "").strip()
    if not text.isdigit():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    row_id = int(text)
    if row_id > SQLITE_MAX_INT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    return row_id


def parse_target(value: str, allowed_types: tuple[str, ...]) -> tuple[str, int]:
    """Split the "<type>:<id>" encoding used by the quick-action and bank-matching
    forms into a validated (type, id) pair, or HTTPException(400)."""
    target_type, _, target_id_raw = (value or "").partition(":")
    if target_type not in allowed_types or not target_id_raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    return target_type, parse_row_id(target_id_raw)
