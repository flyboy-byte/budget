"""Finds probable recurring bills in raw bank transaction history, so a user can
confirm one with a tap instead of typing an obligation in by hand.

Pure functions over already-fetched transaction dicts — no DB access, no network.
Nothing here creates an obligation; it only proposes. The caller (a route) decides
what to persist, and the user is the final filter: these are *suggestions*, and a
wrong one costs a glance, not bad data.

Deliberately conservative. The failure mode that kills this feature is flooding the
screen with every gas-station run, so a candidate has to clear three gates:
consistent merchant, consistent amount, and a gap between charges that actually
maps to a supported recurrence rule. A real-world check against production data:
a convenience store visited 6 times in a month ($6.91–$39.12) is correctly
rejected on amount spread, while a $7.98 hosting charge on the 1st of each month
is exactly what should survive.
"""
import re
from collections import defaultdict
from datetime import date

# Leading processor/network junk that says nothing about who was actually paid.
_PREFIX_NOISE = re.compile(r"^(PIN|POS|ACH|SQ|TST|PP|PAYPAL|DEBIT|CREDIT|RECURRING)\b[\s*]*", re.I)
# A trailing "MM/DD YYYY"-ish stamp most issuers append to the description.
_TRAILING_STAMP = re.compile(r"\s+\d{1,2}/\d{1,2}(\s+\d{2,4})?\s*$")
# Tokens that identify a *terminal or card*, not a merchant: pure digits (102011),
# and masked runs that interleave X's, digits and hyphens (XXX-XX6918, XXXXX).
# Matched before the has-a-letter check below, because a masked run is *made of*
# letters and would otherwise sail through as if it named someone.
_NOISE_TOKEN = re.compile(r"^#?[x\d][x\d-]*$", re.I)
_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}

# Median gap (days) -> recurrence rule. Windows are wide enough to absorb weekend
# shifts and 28-vs-31-day months without bleeding into the neighbouring rule.
_CADENCES = (
    (5, 9, "weekly"),
    (12, 16, "biweekly"),
    (26, 35, "monthly"),
    (350, 380, "yearly"),
)

MIN_OCCURRENCES = 2
# (max-min)/max across the run. A bill can drift (usage-based utilities) but a
# genuine bill doesn't swing 4x the way discretionary spending at one merchant does.
MAX_AMOUNT_SPREAD = 0.25
# How many merchant tokens form the grouping key. Two is enough to separate
# "RED STONE PIZZA" from "RED ROOF INN" without splitting a single merchant whose
# description gains or loses a trailing word between charges.
_KEY_TOKENS = 2


def normalize_merchant(description: str) -> str:
    """'ALLSUP 102011 FARWELL TX 08/13 2020' -> 'ALLSUP FARWELL'.

    Strips the processor prefix, the trailing date stamp, terminal/card-number
    tokens, and state codes, then keeps the leading words that actually name the
    merchant. Returns '' when nothing identifying survives, which the caller
    treats as un-groupable rather than lumping every mystery charge together.
    """
    text = _TRAILING_STAMP.sub("", description or "")
    text = _PREFIX_NOISE.sub("", text)
    text = text.replace("*", " ")

    tokens = []
    for raw in text.split():
        token = raw.strip(".,-#").upper()
        if not token or _NOISE_TOKEN.match(token) or token in _US_STATES:
            continue
        if not any(ch.isalpha() for ch in token):
            continue
        tokens.append(token)

    return " ".join(tokens[:_KEY_TOKENS])


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _cadence_for(gaps: list[int]) -> str | None:
    if not gaps:
        return None
    median_gap = _median(gaps)
    for low, high, rule in _CADENCES:
        if low <= median_gap <= high:
            return rule
    return None


def _amount_spread(amounts: list[int]) -> float:
    largest = max(amounts)
    if largest == 0:
        return 0.0
    return (largest - min(amounts)) / largest


def find_recurring_candidates(
    transactions: list[dict],
    *,
    today: date | None = None,
    exclude_names: set[str] | None = None,
) -> list[dict]:
    """transactions: dicts shaped like app/services/bank_sync.py's parsed output
    ({"posted_date": "YYYY-MM-DD", "amount_cents": int (negative = outflow),
    "description": str}).

    Returns suggestions sorted most-confident first:
    {"merchant", "amount_cents", "recurrence_rule", "next_due_date",
     "occurrence_count", "last_seen", "sample_description"}

    exclude_names: normalized merchant keys already tracked as obligations, so a
    bill the user has confirmed once stops being re-suggested every visit.
    """
    today = today or date.today()
    exclude_names = exclude_names or set()

    groups = defaultdict(list)
    for txn in transactions:
        # Outflows only. An inflow that recurs is a paycheck, which belongs to
        # income_events and has its own screen -- suggesting it as a *bill* would
        # be actively wrong.
        if txn.get("amount_cents", 0) >= 0:
            continue
        key = normalize_merchant(txn.get("description", ""))
        if not key or key in exclude_names:
            continue
        groups[key].append(txn)

    suggestions = []
    for key, rows in groups.items():
        # Collapse same-day duplicates first: two charges at one merchant on one
        # day are one visit, and counting both would fabricate a 0-day gap that
        # drags the median cadence down.
        by_date = {}
        for row in rows:
            by_date.setdefault(row["posted_date"], row)
        dated = sorted(by_date.items())
        if len(dated) < MIN_OCCURRENCES:
            continue

        amounts = [abs(row["amount_cents"]) for _, row in dated]
        if _amount_spread(amounts) > MAX_AMOUNT_SPREAD:
            continue

        dates = [date.fromisoformat(d) for d, _ in dated]
        gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:])]
        rule = _cadence_for(gaps)
        if rule is None:
            continue

        last_seen = dates[-1]
        next_due = _next_due_after(last_seen, rule, today)
        suggestions.append({
            "merchant": key.title(),
            "amount_cents": round(_median(amounts)),
            "recurrence_rule": rule,
            "next_due_date": next_due.isoformat(),
            "occurrence_count": len(dated),
            "last_seen": last_seen.isoformat(),
            "sample_description": dated[-1][1].get("description", ""),
        })

    # Most occurrences first (strongest evidence), then largest amount -- a
    # recurring $400 charge matters more to safe-to-spend than a recurring $4 one.
    suggestions.sort(key=lambda s: (s["occurrence_count"], s["amount_cents"]), reverse=True)
    return suggestions


def _next_due_after(last_seen: date, rule: str, today: date) -> date:
    """Roll the last-seen date forward by `rule` until it lands on or after today,
    so a suggestion built from stale history still proposes a future due date
    rather than one already in the past (which would land straight in reserved
    cash as overdue the moment it was accepted)."""
    from app.services.recurrence import advance_date

    current = last_seen
    # Bounded rather than `while True`: a corrupt rule that failed to advance
    # would otherwise spin forever. 400 iterations covers ~8 years of weekly.
    for _ in range(400):
        if current >= today:
            return current
        current = date.fromisoformat(advance_date(current.isoformat(), rule))
    return current
