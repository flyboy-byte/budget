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


# No real amount of money in this app should ever reach this -- it exists purely
# to reject "1" followed by hundreds of zeros before it becomes a stored value,
# not to model any plausible balance. Comfortably past anything a debt, account,
# or purchase should ever hold, with room to spare.
_MAX_ABS_DOLLARS = Decimal(10**15)


def _parse_to_smallest_unit(value: str, error_detail: str) -> int:
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)

    # Decimal("inf")/Decimal("Infinity") parse without raising InvalidOperation above,
    # so they need their own check -- left unguarded, to_integral_exact() on an
    # infinite value raises OverflowError converting it to an int, a 500 instead of
    # the 400 every other malformed-amount path returns. A finite-but-astronomical
    # value (e.g. "1" + 400 zeros) parses and converts to an int just fine; the bound
    # below is what actually rejects it.
    if not parsed.is_finite() or abs(parsed) > _MAX_ABS_DOLLARS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)

    scaled = parsed * 100
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
