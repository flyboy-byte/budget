from datetime import date

from app.services.bill_detection import find_recurring_candidates, normalize_merchant

TODAY = date(2026, 8, 26)


def _txn(posted_date, amount_cents, description):
    return {"posted_date": posted_date, "amount_cents": amount_cents, "description": description}


# ----- normalize_merchant -----

def test_normalize_strips_terminal_id_state_and_date_stamp():
    # Real description shape from production data.
    assert normalize_merchant("ALLSUP 102011 FARWELL TX 08/13 2020") == "ALLSUP FARWELL"


def test_normalize_strips_processor_prefix_and_star():
    assert normalize_merchant("SQ *AVID COFFEE ROA Farwell TX 08/16 2020") == "AVID COFFEE"
    assert normalize_merchant("PIN GOOGLE *World o Mountain V CA 08/16 2020") == "GOOGLE WORLD"


def test_normalize_strips_masked_card_digits():
    assert normalize_merchant("OVH US LLC XXX-XX6703 VA 08/01 2020") == "OVH US"
    assert normalize_merchant("McDonalds XXXXX XXX-XX6918 NM 08/06 2020") == "MCDONALDS"


def test_normalize_returns_empty_when_nothing_identifying_survives():
    assert normalize_merchant("XXX-XX6918 08/06 2020") == ""
    assert normalize_merchant("") == ""


def test_same_merchant_different_stamps_normalizes_identically():
    a = normalize_merchant("OVH US LLC XXX-XX6703 VA 08/01 2020")
    b = normalize_merchant("OVH US LLC XXX-XX9911 VA 09/01 2020")
    assert a == b


# ----- find_recurring_candidates -----

def test_detects_a_steady_monthly_charge():
    txns = [
        _txn("2026-06-01", -798, "OVH US LLC XXX-XX6703 VA 06/01 2020"),
        _txn("2026-07-01", -798, "OVH US LLC XXX-XX6703 VA 07/01 2020"),
        _txn("2026-08-01", -798, "OVH US LLC XXX-XX6703 VA 08/01 2020"),
    ]
    [found] = find_recurring_candidates(txns, today=TODAY)

    assert found["merchant"] == "Ovh Us"
    assert found["amount_cents"] == 798
    assert found["recurrence_rule"] == "monthly"
    assert found["occurrence_count"] == 3
    assert found["last_seen"] == "2026-08-01"


def test_rejects_variable_spending_at_one_merchant():
    """The gate that keeps a convenience store off the bill list. Real production
    amounts from a store visited 6 times in a month."""
    txns = [
        _txn("2026-07-28", -1014, "ALLSUP 102011 FARWELL TX"),
        _txn("2026-08-03", -1145, "ALLSUP 102011 FARWELL TX"),
        _txn("2026-08-10", -3165, "ALLSUP 102011 FARWELL TX"),
        _txn("2026-08-17", -1578, "ALLSUP 102011 FARWELL TX"),
    ]
    assert find_recurring_candidates(txns, today=TODAY) == []


def test_rejects_a_single_occurrence():
    txns = [_txn("2026-08-01", -798, "OVH US LLC VA")]
    assert find_recurring_candidates(txns, today=TODAY) == []


def test_rejects_irregular_cadence():
    """Same merchant, same amount, but the gaps map to no supported rule."""
    txns = [
        _txn("2026-05-02", -500, "RANDOM SHOP TX"),
        _txn("2026-05-04", -500, "RANDOM SHOP TX"),
        _txn("2026-08-19", -500, "RANDOM SHOP TX"),
    ]
    assert find_recurring_candidates(txns, today=TODAY) == []


def test_ignores_inflows():
    """A recurring deposit is a paycheck — that belongs to income_events, and
    proposing it as a bill would be actively wrong."""
    txns = [
        _txn("2026-06-15", 250000, "ACME PAYROLL DIRECT DEP"),
        _txn("2026-07-15", 250000, "ACME PAYROLL DIRECT DEP"),
        _txn("2026-08-15", 250000, "ACME PAYROLL DIRECT DEP"),
    ]
    assert find_recurring_candidates(txns, today=TODAY) == []


def test_detects_weekly_and_biweekly():
    weekly = [
        _txn("2026-08-05", -2000, "GYM CLUB TX"),
        _txn("2026-08-12", -2000, "GYM CLUB TX"),
        _txn("2026-08-19", -2000, "GYM CLUB TX"),
    ]
    assert find_recurring_candidates(weekly, today=TODAY)[0]["recurrence_rule"] == "weekly"

    biweekly = [
        _txn("2026-07-08", -4500, "LAWN CARE TX"),
        _txn("2026-07-22", -4500, "LAWN CARE TX"),
        _txn("2026-08-05", -4500, "LAWN CARE TX"),
    ]
    assert find_recurring_candidates(biweekly, today=TODAY)[0]["recurrence_rule"] == "biweekly"


def test_next_due_is_rolled_forward_past_today():
    """History can be months stale; an accepted suggestion must not land already
    overdue, which would drop straight into reserved cash as a missed bill."""
    txns = [
        _txn("2026-03-10", -1200, "NEWS SUB NY"),
        _txn("2026-04-10", -1200, "NEWS SUB NY"),
    ]
    [found] = find_recurring_candidates(txns, today=TODAY)
    assert found["next_due_date"] >= TODAY.isoformat()


def test_same_day_duplicate_charges_do_not_fake_a_cadence():
    """Two charges at one merchant on one day is one visit — counting both would
    fabricate a 0-day gap and drag the median cadence out of every real window."""
    txns = [
        _txn("2026-07-15", -1000, "COFFEE CO TX"),
        _txn("2026-07-15", -1000, "COFFEE CO TX"),
        _txn("2026-08-15", -1000, "COFFEE CO TX"),
    ]
    [found] = find_recurring_candidates(txns, today=TODAY)
    assert found["recurrence_rule"] == "monthly"
    assert found["occurrence_count"] == 2


def test_excluded_merchants_are_not_resuggested():
    txns = [
        _txn("2026-06-01", -798, "OVH US LLC VA"),
        _txn("2026-07-01", -798, "OVH US LLC VA"),
    ]
    assert find_recurring_candidates(txns, today=TODAY) != []
    assert find_recurring_candidates(txns, today=TODAY, exclude_names={"OVH US"}) == []


def test_sorted_by_evidence_then_amount():
    txns = [
        _txn("2026-06-01", -500, "SMALL SUB TX"),
        _txn("2026-07-01", -500, "SMALL SUB TX"),
        _txn("2026-08-01", -500, "SMALL SUB TX"),
        _txn("2026-07-05", -9000, "BIG BILL TX"),
        _txn("2026-08-05", -9000, "BIG BILL TX"),
    ]
    found = find_recurring_candidates(txns, today=TODAY)
    # 3 occurrences beats 2, even though the other charge is far larger.
    assert [f["merchant"] for f in found] == ["Small Sub", "Big Bill"]
