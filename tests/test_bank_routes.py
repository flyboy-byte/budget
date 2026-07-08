from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.routers.auth as auth_module
import app.routers.bank as bank_module
from app.deps import get_db
from app.main import app
from app import crypto
from app.repositories import accounts as accounts_repo
from app.repositories import bank_sync as bank_sync_repo
from app.security import hash_password
from app.services import bank_sync as bank_sync_service

_TEST_KEY = "9OYhjp1emy1J_NArPCEPTGgFUzRMc1BY3Qlcztew44I="


@pytest.fixture
def client(db, user_id, monkeypatch):
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password("correct-password"), user_id),
    )
    db.commit()
    monkeypatch.setattr(auth_module, "SECURE_COOKIES", False)
    monkeypatch.setattr(main_module, "SECURE_COOKIES", False)
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", "9OYhjp1emy1J_NArPCEPTGgFUzRMc1BY3Qlcztew44I=")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    test_client.post("/login", data={"username": "alice", "password": "correct-password"})
    yield test_client
    app.dependency_overrides.clear()


def get_csrf_token(client, path="/bank/connect"):
    response = client.get(path)
    return response.text.split('name="csrf_token" value="')[1].split('"')[0]


def other_user(db):
    cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    return cur.lastrowid


# ----- auth required -----

def test_list_requires_login():
    resp = TestClient(app).get("/bank")
    assert resp.status_code == 401


def test_connect_requires_login():
    resp = TestClient(app).get("/bank/connect")
    assert resp.status_code == 401


# ----- unmatched count (Badging API backend, Phase 11) -----

def test_unmatched_count_requires_login():
    resp = TestClient(app).get("/bank/unmatched-count")
    assert resp.status_code == 401


def test_unmatched_count_zero_with_no_staged_transactions(client):
    resp = client.get("/bank/unmatched-count")
    assert resp.status_code == 200
    assert resp.json() == {"count": 0}


def test_unmatched_count_reflects_staged_rows(client, db, user_id):
    connection_id = db.execute(
        "INSERT INTO bank_connections (user_id, access_url_encrypted, label) VALUES (?, 'x', 'Test Bank')",
        (user_id,),
    ).lastrowid
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 0)
    link_id = db.execute(
        """INSERT INTO bank_account_links (user_id, bank_connection_id, sfin_account_id, sfin_account_name, account_id)
           VALUES (?, ?, 'acct-1', 'Checking', ?)""",
        (user_id, connection_id, account_id),
    ).lastrowid
    for i in range(2):
        db.execute(
            """INSERT INTO bank_transaction_staging
               (user_id, bank_account_link_id, sfin_transaction_id, posted_date, amount_cents, description, status)
               VALUES (?, ?, ?, '2026-08-01', 1000, 'Test', 'unmatched')""",
            (user_id, link_id, f"txn-{i}"),
        )
    db.commit()

    resp = client.get("/bank/unmatched-count")
    assert resp.status_code == 200
    assert resp.json() == {"count": 2}


# ----- connect flow -----

def test_connect_requires_csrf(client):
    resp = client.post("/bank/connect", data={"label": "Chase", "setup_token": "abc"})
    assert resp.status_code == 403


def test_connect_happy_path_encrypts_access_url(client, db, user_id, monkeypatch):
    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "claim_setup_token",
        lambda token, **kw: "https://mykey:mysecret@bridge.simplefin.org/simplefin",
    )
    csrf = get_csrf_token(client, "/bank/connect")
    resp = client.post(
        "/bank/connect",
        data={"label": "Chase", "setup_token": "faketoken", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    row = bank_sync_repo.list_connections(db, user_id)[0]
    assert row["label"] == "Chase"
    assert b"mysecret" not in row["access_url_encrypted"]
    assert b"bridge.simplefin.org" not in row["access_url_encrypted"]


def test_connect_claim_failure_does_not_leak_token(client, monkeypatch):
    def raise_error(token, **kw):
        raise bank_sync_service.SimpleFinError("boom")

    monkeypatch.setattr(bank_module.bank_sync_service, "claim_setup_token", raise_error)
    csrf = get_csrf_token(client, "/bank/connect")
    secret_token = "super-secret-setup-token-value"
    resp = client.post(
        "/bank/connect",
        data={"label": "Chase", "setup_token": secret_token, "csrf_token": csrf},
    )
    assert resp.status_code == 400
    assert secret_token not in resp.text


def test_connect_blank_label_rejected(client):
    csrf = get_csrf_token(client, "/bank/connect")
    resp = client.post(
        "/bank/connect",
        data={"label": "   ", "setup_token": "abc", "csrf_token": csrf},
    )
    assert resp.status_code == 400


# ----- ownership scoping -----

def _make_connection(db, user_id, last_synced_at=None):
    # Callers always depend on the `client` fixture too, which sets
    # BANK_SYNC_ENCRYPTION_KEY before this runs.
    encrypted = crypto.encrypt("https://key:secret@bridge.simplefin.org/simplefin")
    connection_id = bank_sync_repo.create_connection(db, user_id, "Chase", encrypted)
    if last_synced_at:
        bank_sync_repo.mark_synced(db, user_id, connection_id, last_synced_at)
    db.commit()
    return connection_id


def test_sync_scoped_to_owner_returns_404(client, db, user_id):
    other_id = other_user(db)
    connection_id = _make_connection(db, other_id)
    csrf = get_csrf_token(client)
    resp = client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf})
    assert resp.status_code == 404


def test_disconnect_scoped_to_owner_returns_404(client, db, user_id):
    other_id = other_user(db)
    connection_id = _make_connection(db, other_id)
    csrf = get_csrf_token(client)
    resp = client.post(f"/bank/{connection_id}/disconnect", data={"csrf_token": csrf})
    assert resp.status_code == 404


def test_review_scoped_to_owner_returns_404(client, db, user_id):
    other_id = other_user(db)
    connection_id = _make_connection(db, other_id)
    resp = client.get(f"/bank/{connection_id}/review")
    assert resp.status_code == 404


# ----- sync rate limiting -----

def test_sync_refused_within_cooldown_does_not_call_fetch(client, db, user_id, monkeypatch):
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    connection_id = _make_connection(db, user_id, last_synced_at=recent)

    def must_not_be_called(*a, **kw):
        raise AssertionError("fetch_accounts must not be called during cooldown")

    monkeypatch.setattr(bank_module.bank_sync_service, "fetch_accounts", must_not_be_called)
    csrf = get_csrf_token(client)
    resp = client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303


def test_sync_allowed_after_cooldown_elapsed(client, db, user_id, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    connection_id = _make_connection(db, user_id, last_synced_at=old)

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(accounts=[], errors=[]),
    )
    csrf = get_csrf_token(client)
    resp = client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/bank/{connection_id}/review"


# ----- sync only stages mapped links -----

def test_sync_stages_only_mapped_links(client, db, user_id, monkeypatch):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    db.commit()

    fake_accounts = [
        {"sfin_account_id": "mapped-1", "sfin_account_name": "Mapped", "balance_cents": 55555, "balance_date": None},
        {"sfin_account_id": "unmapped-1", "sfin_account_name": "Unmapped", "balance_cents": 99999, "balance_date": None},
    ]
    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(accounts=fake_accounts, errors=[]),
    )

    # first sync: nothing mapped yet, so no staging rows, but links get created
    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf})
    links = bank_sync_repo.list_links(db, user_id, connection_id)
    assert len(links) == 2
    assert bank_sync_repo.list_unapplied_staging(db, user_id, connection_id) == []

    mapped_link = next(l for l in links if l["sfin_account_id"] == "mapped-1")
    bank_sync_repo.set_link_target(db, user_id, mapped_link["id"], "account", account_id)
    db.commit()

    # Bypass the 6h sync cooldown to simulate time passing before the second sync —
    # the cooldown itself is covered separately in test_sync_refused_within_cooldown.
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    bank_sync_repo.mark_synced(db, user_id, connection_id, old)
    db.commit()

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf})
    staged = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert len(staged) == 1
    assert staged[0]["synced_balance_cents"] == 55555


# ----- review + apply -----

def test_apply_updates_balance_and_marks_applied(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()
    staging_id = bank_sync_repo.create_staging_row(db, user_id, link_id, 77777)
    db.commit()

    csrf = get_csrf_token(client)
    resp = client.post(
        f"/bank/{connection_id}/apply",
        data={"csrf_token": csrf, "staging_id": [str(staging_id)]},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    account = accounts_repo.get_account(db, user_id, account_id)
    assert account["balance_cents"] == 77777
    assert bank_sync_repo.list_unapplied_staging(db, user_id, connection_id) == []


def test_apply_skips_unselected_staging_rows(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()
    bank_sync_repo.create_staging_row(db, user_id, link_id, 77777)
    db.commit()

    csrf = get_csrf_token(client)
    resp = client.post(f"/bank/{connection_id}/apply", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303

    account = accounts_repo.get_account(db, user_id, account_id)
    assert account["balance_cents"] == 10000
    assert len(bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)) == 1


# ----- stale-connection badge -----

def test_list_shows_stale_badge_when_long_overdue(client, db, user_id):
    old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _make_connection(db, user_id, last_synced_at=old)

    resp = client.get("/bank")
    assert "Hasn&#39;t synced in a while" in resp.text or "Hasn't synced in a while" in resp.text


def test_list_no_stale_badge_when_recently_synced(client, db, user_id):
    recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _make_connection(db, user_id, last_synced_at=recent)

    resp = client.get("/bank")
    assert "Hasn&#39;t synced in a while" not in resp.text and "Hasn't synced in a while" not in resp.text


def test_partial_sync_warning_shown_without_needs_reconnect(client, db, user_id, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    connection_id = _make_connection(db, user_id, last_synced_at=old)

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            accounts=[], errors=[{"msg": "Savings temporarily unavailable"}]
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)

    resp = client.get("/bank")
    assert "Partial sync" in resp.text
    assert "Savings temporarily unavailable" in resp.text
    assert "Needs reconnect" not in resp.text


# ----- disconnect -----

def test_disconnect_removes_connection(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    csrf = get_csrf_token(client)
    resp = client.post(f"/bank/{connection_id}/disconnect", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert bank_sync_repo.get_connection_row(db, user_id, connection_id) is None


# ----- export exclusion (IMPLEMENTATION_HISTORY.md Security section: must be tested, not just documented) -----

def test_export_never_includes_bank_connection_secrets(db, user_id):
    from app.services.export import export_json_backup

    bank_sync_repo.create_connection(db, user_id, "Chase", b"super-secret-encrypted-blob")
    db.commit()

    backup = export_json_backup(db, user_id)
    serialized = str(backup)
    assert "bank_connections" not in serialized
    assert "super-secret-encrypted-blob" not in serialized


# ----- inline create-account / create-debt during mapping -----

def test_link_can_create_new_account_inline(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()

    csrf = get_csrf_token(client, path="/bank")
    resp = client.post(
        f"/bank/{connection_id}/links/{link_id}",
        data={"csrf_token": csrf, "target": "new_account", "new_name": "New Checking"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    link = bank_sync_repo.get_link(db, user_id, link_id)
    assert link["account_id"] is not None
    account = accounts_repo.get_account(db, user_id, link["account_id"])
    assert account["name"] == "New Checking"
    assert account["balance_cents"] == 0


def test_link_can_create_new_debt_inline(client, db, user_id):
    from app.repositories import debts as debts_repo

    connection_id = _make_connection(db, user_id)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-loan", "Loan")
    db.commit()

    csrf = get_csrf_token(client, path="/bank")
    resp = client.post(
        f"/bank/{connection_id}/links/{link_id}",
        data={"csrf_token": csrf, "target": "new_debt", "new_name": "New Loan"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    link = bank_sync_repo.get_link(db, user_id, link_id)
    assert link["debt_id"] is not None
    debt = debts_repo.get_debt(db, user_id, link["debt_id"])
    assert debt["name"] == "New Loan"
    assert debt["balance_cents"] == 0
    # Redirects to the edit form (not /bank) since the new debt has no minimum
    # payment/due date yet and would otherwise silently reserve $0 for it.
    assert resp.headers["location"] == f"/debts/{link['debt_id']}/edit?from_bank_sync=1"


def test_link_create_new_account_rejects_blank_name(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    db.commit()

    csrf = get_csrf_token(client, path="/bank")
    resp = client.post(
        f"/bank/{connection_id}/links/{link_id}",
        data={"csrf_token": csrf, "target": "new_account", "new_name": "  "},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    link = bank_sync_repo.get_link(db, user_id, link_id)
    assert link["account_id"] is None


def test_link_create_new_scoped_to_owner_returns_404(client, db, user_id):
    other_id = other_user(db)
    connection_id = _make_connection(db, other_id)
    link_id = bank_sync_repo.upsert_link(db, other_id, connection_id, "sfin-1", "Checking")
    db.commit()

    csrf = get_csrf_token(client)
    resp = client.post(
        f"/bank/{connection_id}/links/{link_id}",
        data={"csrf_token": csrf, "target": "new_account", "new_name": "Sneaky"},
        follow_redirects=False,
    )
    assert resp.status_code == 404
    assert accounts_repo.list_accounts(db, other_id) == []


# ----- debt linking -----

def test_link_can_map_to_debt(client, db, user_id):
    from app.repositories import debts as debts_repo

    connection_id = _make_connection(db, user_id)
    debt_id = debts_repo.create_debt(db, user_id, "Loan", "bank_loan", 500000, 10000, "accruing")
    db.commit()
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-loan", "Loan")
    db.commit()

    csrf = get_csrf_token(client, path="/bank")
    resp = client.post(
        f"/bank/{connection_id}/links/{link_id}",
        data={"csrf_token": csrf, "target": f"debt:{debt_id}"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    link = bank_sync_repo.get_link(db, user_id, link_id)
    assert link["debt_id"] == debt_id
    assert link["account_id"] is None


def test_sync_and_apply_updates_debt_balance_not_account(client, db, user_id, monkeypatch):
    from app.repositories import debts as debts_repo

    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)
    debt_id = debts_repo.create_debt(db, user_id, "Loan", "bank_loan", 500000, 10000, "accruing")
    db.commit()
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-loan", "Loan")
    bank_sync_repo.set_link_target(db, user_id, link_id, "debt", debt_id)
    db.commit()

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            accounts=[{"sfin_account_id": "sfin-loan", "sfin_account_name": "Loan", "balance_cents": 450000, "balance_date": None}],
            errors=[],
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)
    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert len(staging) == 1

    resp = client.post(
        f"/bank/{connection_id}/apply",
        data={"csrf_token": csrf, "staging_id": [str(staging[0]["id"])]},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    debt = debts_repo.get_debt(db, user_id, debt_id)
    assert debt["balance_cents"] == 450000
    account = accounts_repo.get_account(db, user_id, account_id)
    assert account["balance_cents"] == 1000  # untouched


def test_sync_normalizes_negative_debt_balance_to_positive_owed(client, db, user_id, monkeypatch):
    """SimpleFIN reports a credit card/loan balance as negative (liability
    convention) — debts.balance_cents' CHECK (balance_cents >= 0) means applying
    that raw signed value fails with a 400 ("Invalid data"). Confirmed against real
    staged production data 2026-07-22."""
    from app.repositories import debts as debts_repo

    connection_id = _make_connection(db, user_id)
    debt_id = debts_repo.create_debt(db, user_id, "Loan", "bank_loan", 500000, 10000, "accruing")
    db.commit()
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-loan", "Loan")
    bank_sync_repo.set_link_target(db, user_id, link_id, "debt", debt_id)
    db.commit()

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            accounts=[{"sfin_account_id": "sfin-loan", "sfin_account_name": "Loan", "balance_cents": -168994, "balance_date": None}],
            errors=[],
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)
    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert staging[0]["synced_balance_cents"] == 168994  # normalized, not left negative

    resp = client.post(
        f"/bank/{connection_id}/apply",
        data={"csrf_token": csrf, "staging_id": [str(staging[0]["id"])]},
        follow_redirects=False,
    )
    assert resp.status_code == 303  # no longer 400 "Invalid data"

    debt = debts_repo.get_debt(db, user_id, debt_id)
    assert debt["balance_cents"] == 168994


def test_sync_does_not_normalize_account_balance_sign(client, db, user_id, monkeypatch):
    """Only debt-mapped links get sign-normalized — an overdrawn cash account
    legitimately has a negative balance and accounts.balance_cents has no such
    CHECK constraint, so that sign must be preserved as-is."""
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-checking", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            accounts=[{"sfin_account_id": "sfin-checking", "sfin_account_name": "Checking", "balance_cents": -500, "balance_date": None}],
            errors=[],
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)
    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert staging[0]["synced_balance_cents"] == -500


def test_sync_ignores_available_balance_by_default(client, db, user_id, monkeypatch):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-checking", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            accounts=[{
                "sfin_account_id": "sfin-checking", "sfin_account_name": "Checking",
                "balance_cents": 81709, "available_balance_cents": 59711, "balance_date": None,
            }],
            errors=[],
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)
    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert staging[0]["synced_balance_cents"] == 81709  # ledger balance, setting is off by default


def test_sync_uses_available_balance_for_account_link_when_enabled(client, db, user_id, monkeypatch):
    from app.repositories import settings as settings_repo

    settings_repo.upsert(db, user_id, "bank_sync_use_available_balance", "1")
    db.commit()

    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-checking", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            accounts=[{
                "sfin_account_id": "sfin-checking", "sfin_account_name": "Checking",
                "balance_cents": 81709, "available_balance_cents": 59711, "balance_date": None,
            }],
            errors=[],
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)
    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert staging[0]["synced_balance_cents"] == 59711


def test_sync_never_uses_available_balance_for_debt_link_even_when_enabled(client, db, user_id, monkeypatch):
    from app.repositories import debts as debts_repo
    from app.repositories import settings as settings_repo

    settings_repo.upsert(db, user_id, "bank_sync_use_available_balance", "1")
    db.commit()

    connection_id = _make_connection(db, user_id)
    debt_id = debts_repo.create_debt(db, user_id, "Loan", "bank_loan", 500000, 10000, "accruing")
    db.commit()
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-loan", "Loan")
    bank_sync_repo.set_link_target(db, user_id, link_id, "debt", debt_id)
    db.commit()

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            # SimpleFIN doesn't populate available-balance meaningfully for loans —
            # comes back 0 regardless of real balance; must never be used for debts.
            accounts=[{
                "sfin_account_id": "sfin-loan", "sfin_account_name": "Loan",
                "balance_cents": -168994, "available_balance_cents": 0, "balance_date": None,
            }],
            errors=[],
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)
    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert staging[0]["synced_balance_cents"] == 168994  # normalized ledger balance, not 0


# ----- auto-apply -----

def test_auto_apply_off_by_default_leaves_staging_unapplied(client, db, user_id, monkeypatch):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 100000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-checking", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            accounts=[{"sfin_account_id": "sfin-checking", "sfin_account_name": "Checking",
                       "balance_cents": 100500, "balance_date": None}],
            errors=[],
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)

    account = accounts_repo.get_account(db, user_id, account_id)
    assert account["balance_cents"] == 100000  # unchanged
    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert len(staging) == 1
    assert staging[0]["applied"] == 0


def test_auto_apply_on_applies_a_small_change_immediately(client, db, user_id, monkeypatch):
    from app.repositories import settings as settings_repo

    settings_repo.upsert(db, user_id, "bank_sync_auto_apply", "1")
    settings_repo.upsert(db, user_id, "bank_sync_auto_apply_max_change_cents", "25000")
    db.commit()

    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 100000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-checking", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            accounts=[{"sfin_account_id": "sfin-checking", "sfin_account_name": "Checking",
                       "balance_cents": 100500, "balance_date": None}],
            errors=[],
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)

    account = accounts_repo.get_account(db, user_id, account_id)
    assert account["balance_cents"] == 100500  # applied, no manual review
    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert staging == []


def test_auto_apply_on_still_holds_a_big_change_for_manual_review(client, db, user_id, monkeypatch):
    from app.repositories import settings as settings_repo

    settings_repo.upsert(db, user_id, "bank_sync_auto_apply", "1")
    settings_repo.upsert(db, user_id, "bank_sync_auto_apply_max_change_cents", "25000")
    db.commit()

    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 100000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-checking", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            # A $2,000 jump -- looks like a bad SimpleFIN read, must NOT apply blind.
            accounts=[{"sfin_account_id": "sfin-checking", "sfin_account_name": "Checking",
                       "balance_cents": 300000, "balance_date": None}],
            errors=[],
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)

    account = accounts_repo.get_account(db, user_id, account_id)
    assert account["balance_cents"] == 100000  # unchanged -- waits for manual review
    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert len(staging) == 1
    assert staging[0]["synced_balance_cents"] == 300000


def test_auto_apply_on_applies_a_small_change_for_a_debt_link_too(client, db, user_id, monkeypatch):
    from app.repositories import debts as debts_repo
    from app.repositories import settings as settings_repo

    settings_repo.upsert(db, user_id, "bank_sync_auto_apply", "1")
    settings_repo.upsert(db, user_id, "bank_sync_auto_apply_max_change_cents", "25000")
    db.commit()

    connection_id = _make_connection(db, user_id)
    debt_id = debts_repo.create_debt(db, user_id, "Loan", "bank_loan", 500000, 10000, "accruing")
    db.commit()
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-loan", "Loan")
    bank_sync_repo.set_link_target(db, user_id, link_id, "debt", debt_id)
    db.commit()

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            # SimpleFIN's negative-for-owed convention -- normalizes to 499500.
            accounts=[{"sfin_account_id": "sfin-loan", "sfin_account_name": "Loan",
                       "balance_cents": -499500, "balance_date": None}],
            errors=[],
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)

    debt = debts_repo.get_debt(db, user_id, debt_id)
    assert debt["balance_cents"] == 499500


# ----- configurable sync cooldown -----

def test_custom_short_cooldown_allows_near_immediate_resync(client, db, user_id, monkeypatch):
    from app.repositories import settings as settings_repo

    settings_repo.upsert(db, user_id, "bank_sync_cooldown_minutes", "1")
    db.commit()

    recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    connection_id = _make_connection(db, user_id, last_synced_at=recent)

    called = {"count": 0}

    def fake_fetch_accounts(*a, **kw):
        called["count"] += 1
        return bank_module.bank_sync_service.FetchResult(accounts=[], errors=[])

    monkeypatch.setattr(bank_module.bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    csrf = get_csrf_token(client)
    resp = client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert called["count"] == 1


def test_default_cooldown_still_refuses_recent_sync(client, db, user_id, monkeypatch):
    recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    connection_id = _make_connection(db, user_id, last_synced_at=recent)

    def fail_if_called(*a, **kw):
        raise AssertionError("fetch_accounts should not be called within the default cooldown")

    monkeypatch.setattr(bank_module.bank_sync_service, "fetch_accounts", fail_if_called)

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)


# ----- transaction import + matching -----

def _stage_transaction(db, user_id, connection_id, account_id, amount_cents=-9000):
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()
    bank_sync_repo.create_transaction_staging_rows(
        db, user_id, link_id,
        [{
            "sfin_transaction_id": "t1",
            "posted_date": "2026-07-15",
            "amount_cents": amount_cents,
            "description": "Landlord LLC",
            "pending": False,
        }],
    )
    db.commit()
    return bank_sync_repo.list_unmatched_transactions(db, user_id)[0]["id"]


def test_sync_stages_transactions_for_account_mapped_link(client, db, user_id, monkeypatch):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    monkeypatch.setattr(
        bank_module.bank_sync_service,
        "fetch_accounts",
        lambda *a, **kw: bank_module.bank_sync_service.FetchResult(
            accounts=[{
                "sfin_account_id": "sfin-1", "sfin_account_name": "Checking", "balance_cents": 10000,
                "balance_date": None,
                "transactions": [{
                    "sfin_transaction_id": "t1", "posted_date": "2026-07-15",
                    "amount_cents": -4200, "description": "Store", "pending": False,
                }],
            }],
            errors=[],
        ),
    )

    csrf = get_csrf_token(client)
    client.post(f"/bank/{connection_id}/sync", data={"csrf_token": csrf}, follow_redirects=False)

    unmatched = bank_sync_repo.list_unmatched_transactions(db, user_id)
    assert len(unmatched) == 1
    assert unmatched[0]["description"] == "Store"


def test_transactions_page_lists_unmatched(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    _stage_transaction(db, user_id, connection_id, account_id)

    resp = client.get("/bank/transactions")
    assert resp.status_code == 200
    assert "Landlord LLC" in resp.text


def test_match_transaction_to_obligation(client, db, user_id):
    from app.repositories import obligations as obligations_repo

    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 500000)
    staging_id = _stage_transaction(db, user_id, connection_id, account_id, amount_cents=-90000)
    obligation_id = obligations_repo.create_obligation(db, user_id, "Rent", "housing", 90000, "2026-07-20")
    db.commit()

    csrf = get_csrf_token(client, path="/bank/transactions")
    resp = client.post(
        f"/bank/transactions/{staging_id}/match",
        data={"csrf_token": csrf, "target": f"obligation:{obligation_id}"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    obligation = obligations_repo.get_obligation(db, user_id, obligation_id)
    assert obligation["is_paid"] == 1
    account = accounts_repo.get_account(db, user_id, account_id)
    assert account["balance_cents"] == 410000


def test_match_transaction_to_purchase(client, db, user_id):
    from app.repositories import committed_purchases as purchases_repo

    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 500000)
    staging_id = _stage_transaction(db, user_id, connection_id, account_id, amount_cents=-20000)
    purchase_id = purchases_repo.create_purchase(db, user_id, "Drill", "career_tool", 50000, "ordered")
    db.commit()

    csrf = get_csrf_token(client, path="/bank/transactions")
    resp = client.post(
        f"/bank/transactions/{staging_id}/match",
        data={"csrf_token": csrf, "target": f"purchase:{purchase_id}"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    purchase = purchases_repo.get_purchase(db, user_id, purchase_id)
    assert purchase["amount_paid_cents"] == 20000


def test_match_transaction_already_resolved_returns_400(client, db, user_id):
    from app.repositories import obligations as obligations_repo

    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 500000)
    staging_id = _stage_transaction(db, user_id, connection_id, account_id)
    obligation_id = obligations_repo.create_obligation(db, user_id, "Rent", "housing", 90000, "2026-07-20")
    db.commit()
    obligations_repo.update_obligation(db, user_id, obligation_id, "Rent", "housing", 90000, "2026-07-20", is_paid=1)
    db.commit()

    csrf = get_csrf_token(client, path="/bank/transactions")
    resp = client.post(
        f"/bank/transactions/{staging_id}/match",
        data={"csrf_token": csrf, "target": f"obligation:{obligation_id}"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_match_transaction_overpayment_returns_friendly_400(client, db, user_id):
    from app.repositories import committed_purchases as purchases_repo

    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 500000)
    staging_id = _stage_transaction(db, user_id, connection_id, account_id, amount_cents=-90000)
    purchase_id = purchases_repo.create_purchase(db, user_id, "Drill", "career_tool", 50000, "ordered")
    db.commit()

    csrf = get_csrf_token(client, path="/bank/transactions")
    resp = client.post(
        f"/bank/transactions/{staging_id}/match",
        data={"csrf_token": csrf, "target": f"purchase:{purchase_id}"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "more than what's left" in resp.text


def test_dismiss_transaction_route(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    staging_id = _stage_transaction(db, user_id, connection_id, account_id)

    csrf = get_csrf_token(client, path="/bank/transactions")
    resp = client.post(
        f"/bank/transactions/{staging_id}/dismiss",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert bank_sync_repo.list_unmatched_transactions(db, user_id) == []


def test_dismiss_transaction_scoped_to_owner_returns_404(client, db, user_id):
    other_id = other_user(db)
    connection_id = bank_sync_repo.create_connection(db, other_id, "Chase", crypto.encrypt("https://key:secret@bridge.simplefin.org/simplefin"))
    account_id = accounts_repo.create_account(db, other_id, "Checking", "checking", 10000)
    staging_id = _stage_transaction(db, other_id, connection_id, account_id)

    csrf = get_csrf_token(client)
    resp = client.post(
        f"/bank/transactions/{staging_id}/dismiss",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 404


def _stage_distinct_transactions(db, user_id, connection_id, account_id, count):
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()
    bank_sync_repo.create_transaction_staging_rows(
        db, user_id, link_id,
        [
            {
                "sfin_transaction_id": f"t{i}",
                "posted_date": "2026-07-15",
                "amount_cents": -1000 - i,
                "description": f"Vendor {i}",
                "pending": False,
            }
            for i in range(count)
        ],
    )
    db.commit()
    return [t["id"] for t in bank_sync_repo.list_unmatched_transactions(db, user_id)]


def test_dismiss_selected_transactions_bulk(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    staging_id_1, staging_id_2, staging_id_3 = _stage_distinct_transactions(db, user_id, connection_id, account_id, 3)

    csrf = get_csrf_token(client, path="/bank/transactions")
    resp = client.post(
        "/bank/transactions/dismiss-selected",
        data={"csrf_token": csrf, "staging_id": [str(staging_id_1), str(staging_id_2)]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    remaining = bank_sync_repo.list_unmatched_transactions(db, user_id)
    assert [t["id"] for t in remaining] == [staging_id_3]


def test_dismiss_selected_transactions_ignores_other_users_ids(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 10000)
    mine = _stage_transaction(db, user_id, connection_id, account_id)

    other_id = other_user(db)
    other_connection_id = bank_sync_repo.create_connection(db, other_id, "Chase", crypto.encrypt("https://key:secret@bridge.simplefin.org/simplefin"))
    other_account_id = accounts_repo.create_account(db, other_id, "Checking", "checking", 10000)
    theirs = _stage_transaction(db, other_id, other_connection_id, other_account_id)

    csrf = get_csrf_token(client, path="/bank/transactions")
    resp = client.post(
        "/bank/transactions/dismiss-selected",
        data={"csrf_token": csrf, "staging_id": [str(mine), str(theirs)]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert bank_sync_repo.list_unmatched_transactions(db, user_id) == []
    assert [t["id"] for t in bank_sync_repo.list_unmatched_transactions(db, other_id)] == [theirs]


# ----- "just spending": absorbing a transaction that isn't a commitment -----

def test_record_as_spending_writes_ledger_row_and_clears_the_queue(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 50000)
    staging_id = _stage_transaction(db, user_id, connection_id, account_id, amount_cents=-1274)

    csrf = get_csrf_token(client)
    response = client.post(
        f"/bank/transactions/{staging_id}/spending",
        data={"csrf_token": csrf, "category": "gas"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    entry = db.execute(
        "SELECT * FROM transactions WHERE user_id = ? AND target_type = 'spending'", (user_id,)
    ).fetchone()
    assert entry["amount_cents"] == 1274
    assert entry["category"] == "gas"
    assert entry["memo"] == "Landlord LLC"
    assert entry["transaction_date"] == "2026-07-15"
    # No commitment was involved, so neither FK is set.
    assert entry["obligation_id"] is None
    assert entry["committed_purchase_id"] is None

    assert bank_sync_repo.list_unmatched_transactions(db, user_id) == []


def test_record_as_spending_does_not_touch_the_account_balance(client, db, user_id):
    """Unlike a commitment match, plain spending has no reserved_cash drop to
    compensate for, and the posted amount is already inside the synced balance --
    debiting here would understate cash until the next sync overwrote it."""
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 50000)
    staging_id = _stage_transaction(db, user_id, connection_id, account_id, amount_cents=-1274)

    csrf = get_csrf_token(client)
    client.post(
        f"/bank/transactions/{staging_id}/spending",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    account = accounts_repo.get_account(db, user_id, account_id)
    assert account["balance_cents"] == 50000


def test_record_as_spending_blank_category_stored_as_null(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 50000)
    staging_id = _stage_transaction(db, user_id, connection_id, account_id)

    csrf = get_csrf_token(client)
    client.post(
        f"/bank/transactions/{staging_id}/spending",
        data={"csrf_token": csrf, "category": "   "},
        follow_redirects=False,
    )
    entry = db.execute(
        "SELECT category FROM transactions WHERE user_id = ? AND target_type = 'spending'", (user_id,)
    ).fetchone()
    assert entry["category"] is None


def test_record_as_spending_twice_is_rejected(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 50000)
    staging_id = _stage_transaction(db, user_id, connection_id, account_id)

    csrf = get_csrf_token(client)
    client.post(f"/bank/transactions/{staging_id}/spending", data={"csrf_token": csrf},
                follow_redirects=False)
    second = client.post(f"/bank/transactions/{staging_id}/spending", data={"csrf_token": csrf},
                         follow_redirects=False)
    assert second.status_code == 400
    assert db.execute(
        "SELECT COUNT(*) c FROM transactions WHERE user_id = ?", (user_id,)
    ).fetchone()["c"] == 1


def test_record_as_spending_requires_csrf(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 50000)
    staging_id = _stage_transaction(db, user_id, connection_id, account_id)
    response = client.post(f"/bank/transactions/{staging_id}/spending", data={"category": "gas"})
    assert response.status_code == 403


# ----- detected recurring bills -----

def _stage_recurring(db, user_id, connection_id, account_id):
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "sfin-1", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()
    bank_sync_repo.create_transaction_staging_rows(
        db, user_id, link_id,
        [
            {"sfin_transaction_id": f"r{i}", "posted_date": d, "amount_cents": -798,
             "description": "OVH US LLC XXX-XX6703 VA", "pending": False}
            for i, d in enumerate(["2026-06-01", "2026-07-01", "2026-08-01"])
        ],
    )
    db.commit()


def test_suggestions_page_lists_a_detected_recurring_charge(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 50000)
    _stage_recurring(db, user_id, connection_id, account_id)

    response = client.get("/bank/suggestions")
    assert response.status_code == 200
    assert "Ovh Us" in response.text
    assert "monthly" in response.text


def test_accepting_a_suggestion_creates_a_recurring_obligation(client, db, user_id):
    from app.repositories import obligations as obligations_repo

    csrf = get_csrf_token(client)
    response = client.post(
        "/bank/suggestions/accept",
        data={
            "csrf_token": csrf,
            "merchant": "Ovh Us",
            "amount_cents": "798",
            "recurrence_rule": "monthly",
            "next_due_date": "2026-09-01",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    [obligation] = obligations_repo.list_obligations(db, user_id)
    assert obligation["name"] == "Ovh Us"
    assert obligation["amount_cents"] == 798
    assert obligation["is_recurring"] == 1
    assert obligation["recurrence_rule"] == "monthly"
    assert obligation["due_date"] == "2026-09-01"


def test_accepted_suggestion_stops_being_resuggested(client, db, user_id):
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 50000)
    _stage_recurring(db, user_id, connection_id, account_id)

    assert "Ovh Us" in client.get("/bank/suggestions").text

    csrf = get_csrf_token(client)
    client.post(
        "/bank/suggestions/accept",
        data={"csrf_token": csrf, "merchant": "Ovh Us", "amount_cents": "798",
              "recurrence_rule": "monthly", "next_due_date": "2026-09-01"},
        follow_redirects=False,
    )
    assert "Ovh Us" not in client.get("/bank/suggestions").text


def test_accepting_a_suggestion_rejects_a_bogus_recurrence_rule(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/bank/suggestions/accept",
        data={"csrf_token": csrf, "merchant": "Evil", "amount_cents": "100",
              "recurrence_rule": "hourly", "next_due_date": "2026-09-01"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert db.execute("SELECT COUNT(*) c FROM obligations", ()).fetchone()["c"] == 0


def test_accepting_a_suggestion_rejects_a_bogus_date(client, db, user_id):
    csrf = get_csrf_token(client)
    response = client.post(
        "/bank/suggestions/accept",
        data={"csrf_token": csrf, "merchant": "Evil", "amount_cents": "100",
              "recurrence_rule": "monthly", "next_due_date": "not-a-date"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_accepting_a_suggestion_requires_csrf(client, db, user_id):
    response = client.post(
        "/bank/suggestions/accept",
        data={"merchant": "Ovh Us", "amount_cents": "798",
              "recurrence_rule": "monthly", "next_due_date": "2026-09-01"},
    )
    assert response.status_code == 403


def test_deep_scan_reads_simplefin_without_staging_anything(client, db, user_id, monkeypatch):
    """The scan must stay read-only: staging months of history would dump hundreds
    of rows into the match queue the user then has to clear by hand."""
    _make_connection(db, user_id)
    captured = {}

    def fake_fetch(access_url, **kwargs):
        captured["start_date"] = kwargs.get("start_date")
        return bank_module.bank_sync_service.FetchResult(
            accounts=[{
                "sfin_account_id": "sfin-1", "sfin_account_name": "Checking",
                "balance_cents": 1000, "balance_date": None,
                "transactions": [
                    {"sfin_transaction_id": f"s{i}", "posted_date": d, "amount_cents": -798,
                     "description": "OVH US LLC VA", "pending": False}
                    for i, d in enumerate(["2026-06-01", "2026-07-01", "2026-08-01"])
                ],
            }],
            errors=[],
        )

    monkeypatch.setattr(bank_module.bank_sync_service, "fetch_accounts", fake_fetch)

    csrf = get_csrf_token(client)
    response = client.post("/bank/suggestions/scan", data={"csrf_token": csrf})

    assert response.status_code == 200
    assert "Ovh Us" in response.text
    assert captured["start_date"] is not None
    # Read-only: nothing landed in the match queue.
    assert bank_sync_repo.list_unmatched_transactions(db, user_id) == []


def test_deep_scan_surfaces_connection_errors_without_failing(client, db, user_id, monkeypatch):
    _make_connection(db, user_id)

    def fake_fetch(access_url, **kwargs):
        raise bank_module.bank_sync_service.SimpleFinError("timed out")

    monkeypatch.setattr(bank_module.bank_sync_service, "fetch_accounts", fake_fetch)

    csrf = get_csrf_token(client)
    response = client.post("/bank/suggestions/scan", data={"csrf_token": csrf})
    assert response.status_code == 200
    assert "timed out" in response.text
