import pytest

from app.services.recurrence import advance_date


def test_weekly():
    assert advance_date("2026-07-15", "weekly") == "2026-07-22"


def test_biweekly():
    assert advance_date("2026-07-15", "biweekly") == "2026-07-29"


def test_monthly():
    assert advance_date("2026-07-15", "monthly") == "2026-08-15"


def test_monthly_across_year_boundary():
    assert advance_date("2026-12-15", "monthly") == "2027-01-15"


def test_monthly_clamps_overflow_day():
    # Jan 31 + monthly -> Feb 28 (2026 not a leap year), not Mar 3
    assert advance_date("2026-01-31", "monthly") == "2026-02-28"


def test_monthly_clamps_to_leap_day():
    assert advance_date("2027-01-31", "monthly") == "2027-02-28"
    assert advance_date("2028-01-31", "monthly") == "2028-02-29"  # 2028 is a leap year


def test_yearly():
    assert advance_date("2026-07-15", "yearly") == "2027-07-15"


def test_yearly_clamps_leap_day():
    assert advance_date("2028-02-29", "yearly") == "2029-02-28"


def test_unknown_rule_raises():
    with pytest.raises(ValueError):
        advance_date("2026-07-15", "daily")
