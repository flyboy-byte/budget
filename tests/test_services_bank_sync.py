import base64

import httpx
import pytest

from app.services.bank_sync import (
    SimpleFinError,
    SimpleFinRevoked,
    claim_setup_token,
    fetch_accounts,
    normalize_debt_balance_cents,
    summarize_errors,
    within_auto_apply_threshold,
)
from app.services.bank_sync import _parse_transactions


def _mock_client(handler):
    return httpx.MockTransport(handler)


# ----- claim_setup_token -----

def test_claim_setup_token_returns_access_url():
    claim_url = "https://bridge.simplefin.org/simplefin/claim/abc123"
    setup_token = base64.b64encode(claim_url.encode()).decode()

    def handler(request):
        assert str(request.url) == claim_url
        assert request.method == "POST"
        return httpx.Response(200, text="https://key:secret@bridge.simplefin.org/accounts")

    access_url = claim_setup_token(setup_token, transport=_mock_client(handler))
    assert access_url == "https://key:secret@bridge.simplefin.org/accounts"


def test_claim_setup_token_invalid_base64_raises():
    with pytest.raises(SimpleFinError):
        claim_setup_token("not-valid-base64!!!", transport=_mock_client(lambda r: httpx.Response(200)))


def test_claim_setup_token_rejects_untrusted_host():
    # SSRF guard: a decoded "claim URL" pointing anywhere but the SimpleFIN allowlist
    # must never trigger an outbound request.
    claim_url = "http://127.0.0.1:5758/admin"
    setup_token = base64.b64encode(claim_url.encode()).decode()

    def handler(request):
        raise AssertionError("must not make a request to a non-allowlisted host")

    with pytest.raises(SimpleFinError):
        claim_setup_token(setup_token, transport=_mock_client(handler))


def test_claim_setup_token_non_200_raises():
    claim_url = "https://bridge.simplefin.org/simplefin/claim/abc123"
    setup_token = base64.b64encode(claim_url.encode()).decode()

    def handler(request):
        return httpx.Response(400, text="already claimed")

    with pytest.raises(SimpleFinError):
        claim_setup_token(setup_token, transport=_mock_client(handler))


# ----- fetch_accounts -----

_ACCOUNTS_RESPONSE = {
    "errlist": [],
    "accounts": [
        {
            "id": "acc-1",
            "name": "Checking",
            "currency": "USD",
            "balance": "1234.56",
            "balance-date": 1751990400,
        },
        {
            "id": "acc-2",
            "name": "Savings (bad data)",
            "currency": "USD",
            "balance": "not-a-number",
            "balance-date": 1751990400,
        },
    ],
}


def test_fetch_accounts_parses_balance_to_cents_and_sends_basic_auth():
    access_url = "https://mykey:mysecret@bridge.simplefin.org/simplefin"

    def handler(request):
        assert request.url.path == "/simplefin/accounts"
        auth_header = request.headers.get("authorization")
        assert auth_header is not None
        scheme, _, encoded = auth_header.partition(" ")
        assert scheme == "Basic"
        assert base64.b64decode(encoded).decode() == "mykey:mysecret"
        return httpx.Response(200, json=_ACCOUNTS_RESPONSE)

    result = fetch_accounts(access_url, transport=_mock_client(handler))
    accounts = result.accounts

    assert len(accounts) == 1
    assert result.errors == []
    assert accounts[0] == {
        "sfin_account_id": "acc-1",
        "sfin_account_name": "Checking",
        "balance_cents": 123456,
        "available_balance_cents": None,
        "balance_date": 1751990400,
        "transactions": [],
    }


def test_fetch_accounts_parses_available_balance_when_present():
    payload = {
        "accounts": [
            {"id": "acc-1", "name": "Checking", "balance": "817.09", "available-balance": "597.11"},
        ]
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    result = fetch_accounts("https://key:secret@bridge.simplefin.org", transport=_mock_client(handler))

    assert result.accounts[0]["balance_cents"] == 81709
    assert result.accounts[0]["available_balance_cents"] == 59711


def test_fetch_accounts_malformed_available_balance_becomes_none():
    payload = {
        "accounts": [
            {"id": "acc-1", "name": "Checking", "balance": "100.00", "available-balance": "not-a-number"},
        ]
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    result = fetch_accounts("https://key:secret@bridge.simplefin.org", transport=_mock_client(handler))

    assert result.accounts[0]["balance_cents"] == 10000
    assert result.accounts[0]["available_balance_cents"] is None


def test_fetch_accounts_skips_entries_missing_id():
    payload = {
        "accounts": [
            {"name": "No ID Loan", "balance": "500.00"},
            {"id": "acc-2", "name": "Checking", "balance": "100.00"},
        ]
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    result = fetch_accounts("https://key:secret@bridge.simplefin.org", transport=_mock_client(handler))

    assert len(result.accounts) == 1
    assert result.accounts[0]["sfin_account_id"] == "acc-2"


def test_fetch_accounts_surfaces_errlist():
    payload = {
        "accounts": [{"id": "acc-1", "name": "Checking", "balance": "100.00"}],
        "errlist": [{"code": "ACCT-NOT-FOUND", "msg": "Savings temporarily unavailable"}],
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    result = fetch_accounts("https://key:secret@bridge.simplefin.org", transport=_mock_client(handler))

    assert len(result.accounts) == 1
    assert result.errors == [{"code": "ACCT-NOT-FOUND", "msg": "Savings temporarily unavailable"}]


def test_summarize_errors_returns_none_when_empty():
    assert summarize_errors([]) is None


def test_summarize_errors_joins_messages():
    errors = [{"msg": "Savings unavailable"}, {"code": "RATE-LIMITED"}]
    assert summarize_errors(errors) == "Savings unavailable; RATE-LIMITED"


# ----- normalize_debt_balance_cents -----

def test_normalize_debt_balance_cents_flips_negative_to_positive():
    assert normalize_debt_balance_cents(-168994) == 168994


def test_normalize_debt_balance_cents_leaves_positive_unchanged():
    assert normalize_debt_balance_cents(27000) == 27000


def test_normalize_debt_balance_cents_leaves_zero_unchanged():
    assert normalize_debt_balance_cents(0) == 0


# ----- within_auto_apply_threshold -----

def test_within_auto_apply_threshold_small_change_passes():
    assert within_auto_apply_threshold(100000, 100500, 25000) is True


def test_within_auto_apply_threshold_big_change_fails():
    assert within_auto_apply_threshold(100000, 150000, 25000) is False


def test_within_auto_apply_threshold_exact_boundary_passes():
    assert within_auto_apply_threshold(100000, 125000, 25000) is True


def test_within_auto_apply_threshold_one_cent_over_boundary_fails():
    assert within_auto_apply_threshold(100000, 125001, 25000) is False


def test_within_auto_apply_threshold_negative_delta_uses_absolute_value():
    assert within_auto_apply_threshold(100000, 80000, 25000) is True
    assert within_auto_apply_threshold(100000, 50000, 25000) is False


def test_within_auto_apply_threshold_zero_threshold_requires_exact_match():
    assert within_auto_apply_threshold(100000, 100000, 0) is True
    assert within_auto_apply_threshold(100000, 100001, 0) is False


# ----- _parse_transactions -----

def test_parse_transactions_keeps_only_outflows():
    raw = [
        {"id": "t1", "posted": 1751990400, "amount": "-42.10", "description": "Store"},
        {"id": "t2", "posted": 1751990400, "amount": "500.00", "description": "Paycheck"},
    ]
    parsed = _parse_transactions(raw)
    assert len(parsed) == 1
    assert parsed[0] == {
        "sfin_transaction_id": "t1",
        "posted_date": "2025-07-08",
        "amount_cents": -4210,
        "description": "Store",
        "pending": False,
    }


def test_parse_transactions_skips_malformed_entries():
    raw = [
        {"id": "t1", "posted": 1751990400, "amount": "not-a-number", "description": "Bad"},
        {"posted": 1751990400, "amount": "-10.00", "description": "No id"},
        {"id": "t2", "amount": "-10.00", "description": "No posted date"},
        {"id": "t3", "posted": 1751990400, "amount": "-10.00"},
    ]
    parsed = _parse_transactions(raw)
    assert len(parsed) == 1
    assert parsed[0]["sfin_transaction_id"] == "t3"
    assert parsed[0]["description"] == ""


def test_parse_transactions_carries_pending_flag():
    raw = [{"id": "t1", "posted": 1751990400, "amount": "-10.00", "pending": True}]
    parsed = _parse_transactions(raw)
    assert parsed[0]["pending"] is True


def test_fetch_accounts_includes_transactions():
    payload = {
        "accounts": [
            {
                "id": "acc-1", "name": "Checking", "balance": "100.00",
                "transactions": [{"id": "t1", "posted": 1751990400, "amount": "-5.00", "description": "Coffee"}],
            }
        ]
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    result = fetch_accounts("https://key:secret@bridge.simplefin.org", transport=_mock_client(handler))

    assert len(result.accounts[0]["transactions"]) == 1
    assert result.accounts[0]["transactions"][0]["sfin_transaction_id"] == "t1"


def test_fetch_accounts_403_raises_revoked():
    def handler(request):
        return httpx.Response(403)

    with pytest.raises(SimpleFinRevoked):
        fetch_accounts("https://key:secret@bridge.simplefin.org", transport=_mock_client(handler))


def test_fetch_accounts_other_non_200_raises_simplefin_error():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(SimpleFinError):
        fetch_accounts("https://key:secret@bridge.simplefin.org", transport=_mock_client(handler))


def test_fetch_accounts_invalid_json_raises():
    def handler(request):
        return httpx.Response(200, text="not json")

    with pytest.raises(SimpleFinError):
        fetch_accounts("https://key:secret@bridge.simplefin.org", transport=_mock_client(handler))


def test_fetch_accounts_rejects_untrusted_host():
    def handler(request):
        raise AssertionError("must not make a request to a non-allowlisted host")

    with pytest.raises(SimpleFinError):
        fetch_accounts("http://key:secret@127.0.0.1:5758", transport=_mock_client(handler))
