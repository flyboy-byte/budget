"""Dollar-string <-> integer-cents conversions, shared by templates and CRUD routers."""
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException, status


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    dollars = abs(cents) // 100
    remainder = abs(cents) % 100
    return f"{sign}${dollars:,}.{remainder:02d}"


def cents_to_input_value(cents: int) -> str:
    """Plain '12.34' / '-12.34' — no $ sign, no thousands separator — for pre-filling a
    number input's value attribute (as opposed to format_cents, which is for display)."""
    sign = "-" if cents < 0 else ""
    dollars = abs(cents) // 100
    remainder = abs(cents) % 100
    return f"{sign}{dollars}.{remainder:02d}"


def _parse_to_smallest_unit(value: str, error_detail: str) -> int:
    try:
        scaled = Decimal(value) * 100
    except InvalidOperation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)

    exact = scaled.to_integral_exact()
    if exact != scaled:
        # e.g. "10.005" — sub-cent precision. to_integral_exact() would otherwise
        # silently round this (Inexact isn't trapped by the default decimal context)
        # instead of rejecting it, which is the wrong failure mode for money input.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)
    return int(exact)


def parse_dollars_to_cents(value: str) -> int:
    return _parse_to_smallest_unit(value, "Invalid amount")


def parse_percent_to_bps(value: str | None) -> int | None:
    """'24.99' -> 2499 basis points. Blank/None -> None (unknown APR)."""
    if value is None or value.strip() == "":
        return None
    return _parse_to_smallest_unit(value, "Invalid APR")
