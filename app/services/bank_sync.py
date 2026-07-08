"""SimpleFIN Bridge client — claim a Setup Token, fetch linked-account balances.

Pure functions, no DB access (Phase 1's app/repositories/bank_sync.py owns storage;
wiring this client's output into it is a later phase). See IMPLEMENTATION_HISTORY.md for the full
protocol writeup this implements.
"""
import base64
import binascii
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

import httpx

_TIMEOUT = 15.0

# Outbound requests are only ever allowed to these exact hosts. claim_setup_token's URL
# comes from base64-decoding user-submitted input — without this check it's a textbook
# SSRF: a crafted "Setup Token" could point the server at localhost, a cloud metadata
# endpoint, or another service on the same shared VPS. This is an exact-hostname
# allowlist, not IP-range/DNS-rebinding protection — proportionate for this app's
# threat model (a handful of trusted-ish users on a personal self-hosted tool, not a
# public multi-tenant SaaS). beta-bridge.simplefin.org is SimpleFIN's public demo
# endpoint, kept allowed for safe manual testing. If a different SimpleFIN-compatible
# bridge is ever wanted, extend this set explicitly — don't remove the check.
_ALLOWED_HOSTS = {"bridge.simplefin.org", "beta-bridge.simplefin.org"}


class SimpleFinError(Exception):
    """Any failure talking to SimpleFIN — bad token, network error, non-2xx response."""


class SimpleFinRevoked(SimpleFinError):
    """The Access URL was rejected with 403 — the user revoked access on SimpleFIN's
    own site. Callers should mark the connection status='error' and prompt to reconnect,
    not treat this as an application bug."""


def _require_allowed_host(url: str) -> None:
    parsed = httpx.URL(url)
    if parsed.scheme != "https" or parsed.host not in _ALLOWED_HOSTS:
        raise SimpleFinError(f"Refusing to contact untrusted host: {parsed.host!r}")


def claim_setup_token(setup_token: str, *, transport: httpx.BaseTransport | None = None) -> str:
    """Base64-decode a Setup Token to its claim URL and POST to it once. Returns the
    resulting Access URL. The token is single-use — if this raises, the user must
    generate a new Setup Token from SimpleFIN; this can't be retried with the same one."""
    try:
        claim_url = base64.b64decode(setup_token).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise SimpleFinError(f"Invalid Setup Token: {exc}") from exc

    _require_allowed_host(claim_url)

    with httpx.Client(transport=transport, timeout=_TIMEOUT) as client:
        try:
            response = client.post(claim_url)
        except httpx.HTTPError as exc:
            raise SimpleFinError(f"Could not reach SimpleFIN: {exc}") from exc

    if response.status_code != 200:
        raise SimpleFinError(
            f"Claiming Setup Token failed ({response.status_code})"
        )
    access_url = response.text.strip()
    if not access_url:
        raise SimpleFinError("SimpleFIN returned an empty Access URL")
    return access_url


def _parse_balance_cents(balance: str) -> int:
    scaled = Decimal(balance) * 100
    exact = scaled.to_integral_exact()
    if exact != scaled:
        raise InvalidOperation(f"sub-cent balance: {balance!r}")
    return int(exact)


class FetchResult(NamedTuple):
    """accounts: successfully-parsed accounts, same shape as before this was added.
    errors: SimpleFIN's own per-account `errlist` entries ({"code", "msg",
    "account_id"}), passed through as-is — a non-empty errors list means some
    accounts on this connection failed to refresh, without failing the whole sync."""

    accounts: list[dict]
    errors: list[dict]


def summarize_errors(errors: list[dict]) -> str | None:
    """Turns SimpleFIN's per-account errlist into one display string, or None if
    there were no errors. Shared by the UI sync route and the cron script so the
    two can't drift on how a partial-failure warning reads."""
    if not errors:
        return None
    return "; ".join(e.get("msg") or e.get("code") or "unknown error" for e in errors)


def within_auto_apply_threshold(old_balance_cents: int, new_balance_cents: int, max_change_cents: int) -> bool:
    """Whether a freshly-synced balance is close enough to the currently-stored one
    to apply automatically rather than sit in the review queue.

    Exists because the manual review/apply step (bank.py::apply_sync) was found
    2026-08-30 to be a real adoption blocker: sync succeeded every day but nothing
    ever prompted the user to go apply it, so accounts.balance_cents (and therefore
    safe-to-spend) sat frozen for 9 days despite the sync itself working. Auto-apply
    is opt-in (bank_sync_auto_apply setting, default off) specifically BECAUSE
    this changes the core cash-on-hand number, same reasoning as
    bank_sync_use_available_balance. The threshold exists to keep a single bad
    SimpleFIN read (a stale cache, a mis-parsed value) from silently overwriting a
    real balance with garbage -- a jump bigger than the threshold still falls back
    to the existing manual-review path instead of applying blind."""
    return abs(new_balance_cents - old_balance_cents) <= max_change_cents


def normalize_debt_balance_cents(balance_cents: int) -> int:
    """SimpleFIN reports a credit card/loan's balance using a negative-for-owed
    liability convention — confirmed 2026-07-22 from real staged data (a Loan and a
    credit card both came back negative), contradicting an earlier same-session
    check that (mistakenly) read an already-stored, never-actually-synced debts row
    instead of a live fetch. This app's own convention (and debts.balance_cents'
    CHECK (balance_cents >= 0)) is positive = owed, so debt-mapped links must be
    normalized here, before the >= 0 constraint or the review screen ever sees the
    raw signed value."""
    return abs(balance_cents)


def _parse_transactions(raw_transactions: list[dict]) -> list[dict]:
    """Only outflows (negative amount) are kept — bank transaction import only
    exists to match against obligations/committed purchases, which are always
    money leaving the account; deposits/refunds have nothing to match against.
    A malformed or missing amount/id/posted-date skips that one transaction,
    same non-fatal-skip pattern as a malformed account balance."""
    transactions = []
    for raw in raw_transactions:
        if not raw.get("id") or raw.get("posted") is None:
            continue
        try:
            amount_cents = _parse_balance_cents(raw["amount"])
        except (KeyError, InvalidOperation, ValueError):
            continue
        if amount_cents >= 0:
            continue
        posted_date = datetime.fromtimestamp(raw["posted"], tz=timezone.utc).date().isoformat()
        transactions.append({
            "sfin_transaction_id": raw["id"],
            "posted_date": posted_date,
            "amount_cents": amount_cents,
            "description": raw.get("description", ""),
            "pending": bool(raw.get("pending", False)),
        })
    return transactions


def fetch_accounts(
    access_url: str,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float | None = None,
    start_date: int | None = None,
) -> FetchResult:
    """GET {access_url}/accounts and return a FetchResult whose `accounts` list holds:
    {"sfin_account_id": str, "sfin_account_name": str, "balance_cents": int,
     "available_balance_cents": int | None, "balance_date": int | None,
     "transactions": list[dict]}

    Accounts with a malformed `balance` are skipped rather than failing the whole
    fetch, matching SimpleFIN's own errlist-per-account error shape. Each account's
    transactions are parsed the same non-fatal way (see _parse_transactions).

    `available-balance` is optional per SimpleFIN's protocol and reflects pending
    holds/transactions that `balance` (the posted/ledger balance) does not — confirmed
    2026-08-05 from real staged data where an asset account's available-balance
    differed from its ledger balance by several hundred dollars with zero itemized
    pending transactions returned. It's parsed here but a malformed/missing value
    just becomes None rather than skipping the whole account (unlike `balance`,
    it's never the only balance signal available).

    `timeout` overrides the default 15s (used as-is by the interactive UI sync route,
    which is a blocking HTTP request a user is waiting on). The unattended cron sync
    passes a longer value plus its own retry — confirmed 2026-08-26 against real
    production logs that one connection's SimpleFIN bridge regularly took longer than
    15s specifically at the cron's 6am run time, silently leaving that day's balances
    (and the digest email built from them) stale until a manual UI resync happened to
    land when SimpleFIN responded faster.

    `start_date` is a unix timestamp passed through as SimpleFIN's own `start-date`
    query param, asking the bridge for transaction history reaching further back
    than its default window. Used by the read-only recurring-bill scan, which needs
    months of history before a pattern is visible; the routine sync leaves it unset
    so it keeps pulling only the recent default window.
    """
    _require_allowed_host(access_url)
    url = httpx.URL(access_url)
    auth = httpx.BasicAuth(url.username, url.password) if url.username else None
    params = {"start-date": str(start_date)} if start_date is not None else None

    with httpx.Client(transport=transport, timeout=timeout or _TIMEOUT) as client:
        try:
            response = client.get(f"{access_url.rstrip('/')}/accounts", auth=auth, params=params)
        except httpx.HTTPError as exc:
            raise SimpleFinError(f"Could not reach SimpleFIN: {exc}") from exc

    if response.status_code == 403:
        raise SimpleFinRevoked("Access URL was rejected (403) — connection may be revoked")
    if response.status_code != 200:
        raise SimpleFinError(f"Fetching accounts failed ({response.status_code})")

    try:
        payload = response.json()
    except ValueError as exc:
        raise SimpleFinError(f"SimpleFIN returned invalid JSON: {exc}") from exc

    accounts = []
    for raw in payload.get("accounts", []):
        if not raw.get("id"):
            # sfin_account_id is NOT NULL in bank_account_links — an entry missing an
            # id can't be stored, so skip it the same way a malformed balance is skipped
            # below, rather than letting an INSERT fail with an IntegrityError mid-sync.
            continue
        try:
            balance_cents = _parse_balance_cents(raw["balance"])
        except (KeyError, InvalidOperation, ValueError):
            continue
        available_balance_cents = None
        if raw.get("available-balance") is not None:
            try:
                available_balance_cents = _parse_balance_cents(raw["available-balance"])
            except (InvalidOperation, ValueError):
                available_balance_cents = None
        accounts.append({
            "sfin_account_id": raw["id"],
            "sfin_account_name": raw.get("name", ""),
            "balance_cents": balance_cents,
            "available_balance_cents": available_balance_cents,
            "balance_date": raw.get("balance-date"),
            "transactions": _parse_transactions(raw.get("transactions", [])),
        })
    return FetchResult(accounts=accounts, errors=payload.get("errlist", []))
