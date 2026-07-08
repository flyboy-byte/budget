import pytest
from fastapi import HTTPException

from app.money import format_cents, parse_dollars_to_cents, parse_percent_to_bps


def test_parse_dollars_to_cents_basic():
    assert parse_dollars_to_cents("12.34") == 1234
    assert parse_dollars_to_cents("0") == 0
    assert parse_dollars_to_cents("0.01") == 1


def test_parse_dollars_to_cents_rejects_sub_cent_precision():
    with pytest.raises(HTTPException) as exc_info:
        parse_dollars_to_cents("10.005")
    assert exc_info.value.status_code == 400


def test_parse_dollars_to_cents_rejects_malformed_input():
    with pytest.raises(HTTPException) as exc_info:
        parse_dollars_to_cents("not-a-number")
    assert exc_info.value.status_code == 400


def test_parse_percent_to_bps_basic():
    assert parse_percent_to_bps("24.99") == 2499
    assert parse_percent_to_bps("") is None
    assert parse_percent_to_bps(None) is None


def test_parse_percent_to_bps_rejects_sub_bps_precision():
    with pytest.raises(HTTPException) as exc_info:
        parse_percent_to_bps("24.995")
    assert exc_info.value.status_code == 400


def test_format_cents():
    assert format_cents(123456) == "$1,234.56"
    assert format_cents(-500) == "-$5.00"
    assert format_cents(0) == "$0.00"
