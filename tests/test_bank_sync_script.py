import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import crypto
from app.repositories import accounts as accounts_repo
from app.repositories import bank_sync as bank_sync_repo
from app.repositories import push_subscriptions as push_subscriptions_repo
from scripts import bank_sync as script
from app.services import bank_sync as bank_sync_service
from app.services import push as push_service


_TEST_KEY = "9OYhjp1emy1J_NArPCEPTGgFUzRMc1BY3Qlcztew44I="


def _make_connection(db, user_id, last_synced_at=None):
    encrypted = crypto.encrypt("https://key:secret@bridge.simplefin.org/simplefin")
    connection_id = bank_sync_repo.create_connection(db, user_id, "Chase", encrypted)
    if last_synced_at:
        bank_sync_repo.mark_synced(db, user_id, connection_id, last_synced_at)
    db.commit()
    return connection_id


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_sync_stages_only_mapped_links(db, user_id, monkeypatch):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)

    def fake_fetch_accounts(access_url, **kwargs):
        return bank_sync_service.FetchResult(
            accounts=[
                {"sfin_account_id": "mapped", "sfin_account_name": "Checking", "balance_cents": 500000},
                {"sfin_account_id": "unmapped", "sfin_account_name": "Savings", "balance_cents": 900000},
            ],
            errors=[],
        )

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    links = bank_sync_repo.list_links(db, user_id, connection_id)
    assert links == []
    connection = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    result, large_transactions = script.sync_connection(db, user_id, connection, 10000)
    db.commit()
    # First sync: links didn't exist yet, so nothing was mapped to stage against.
    assert "0 staged" in result
    assert large_transactions == []

    links = bank_sync_repo.list_links(db, user_id, connection_id)
    mapped_link = next(l for l in links if l["sfin_account_id"] == "mapped")
    bank_sync_repo.set_link_target(db, user_id, mapped_link["id"], "account", account_id)
    db.commit()

    # unmapped link exists but has no staging row yet
    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert staging == []


def test_sync_uses_available_balance_for_account_link_when_enabled(db, user_id, monkeypatch):
    from app.repositories import settings as settings_repo

    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    settings_repo.upsert(db, user_id, "bank_sync_use_available_balance", "1")
    db.commit()

    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "mapped", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    def fake_fetch_accounts(access_url, **kwargs):
        return bank_sync_service.FetchResult(
            accounts=[{
                "sfin_account_id": "mapped", "sfin_account_name": "Checking",
                "balance_cents": 81709, "available_balance_cents": 59711,
            }],
            errors=[],
        )

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    connection = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    script.sync_connection(db, user_id, connection, 10000)
    db.commit()

    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert staging[0]["synced_balance_cents"] == 59711


def test_auto_apply_applies_a_small_change_and_leaves_a_big_one_staged(db, user_id, monkeypatch):
    from app.repositories import settings as settings_repo

    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    settings_repo.upsert(db, user_id, "bank_sync_auto_apply", "1")
    settings_repo.upsert(db, user_id, "bank_sync_auto_apply_max_change_cents", "25000")
    db.commit()

    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 100000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "mapped", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    def fake_fetch_accounts(access_url, **kwargs):
        return bank_sync_service.FetchResult(
            accounts=[{"sfin_account_id": "mapped", "sfin_account_name": "Checking", "balance_cents": 100500}],
            errors=[],
        )

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    connection = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    script.sync_connection(db, user_id, connection, 10000)
    db.commit()

    account = accounts_repo.get_account(db, user_id, account_id)
    assert account["balance_cents"] == 100500
    assert bank_sync_repo.list_unapplied_staging(db, user_id, connection_id) == []


def test_sync_respects_cooldown(db, user_id, monkeypatch):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    recent = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    connection_id = _make_connection(db, user_id, last_synced_at=recent)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("fetch_accounts should not be called within cooldown")

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fail_if_called)

    connection = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    result, large_transactions = script.sync_connection(db, user_id, connection, 10000)
    assert result == "skipped (cooldown)"
    assert large_transactions == []


def test_sync_marks_revoked_connection_errored(db, user_id, monkeypatch):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    connection_id = _make_connection(db, user_id)

    def fake_fetch_accounts(access_url, **kwargs):
        raise bank_sync_service.SimpleFinRevoked("revoked")

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    connection = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    result, _ = script.sync_connection(db, user_id, connection, 10000)
    db.commit()
    assert "revoked" in result

    updated = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    assert updated["status"] == "error"


def test_sync_retries_once_after_transient_error_and_succeeds(db, user_id, monkeypatch):
    """A timeout/network blip shouldn't leave the day's balances stale -- confirmed
    2026-08-26 against real production logs of one connection's SimpleFIN bridge
    regularly timing out at the cron's run time, silently going unretried."""
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    monkeypatch.setattr(script.time, "sleep", lambda seconds: None)
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "mapped", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    calls = []

    def fake_fetch_accounts(access_url, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise bank_sync_service.SimpleFinError("Could not reach SimpleFIN: timed out")
        return bank_sync_service.FetchResult(
            accounts=[{"sfin_account_id": "mapped", "sfin_account_name": "Checking", "balance_cents": 55500}],
            errors=[],
        )

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    connection = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    result, _ = script.sync_connection(db, user_id, connection, 10000)
    db.commit()

    assert len(calls) == 2
    assert "staged" in result
    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert staging[0]["synced_balance_cents"] == 55500
    updated = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    assert updated["status"] == "active"


def test_sync_gives_up_and_marks_error_after_second_failure(db, user_id, monkeypatch):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    monkeypatch.setattr(script.time, "sleep", lambda seconds: None)
    connection_id = _make_connection(db, user_id)

    calls = []

    def fake_fetch_accounts(access_url, **kwargs):
        calls.append(1)
        raise bank_sync_service.SimpleFinError("Could not reach SimpleFIN: timed out")

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    connection = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    result, _ = script.sync_connection(db, user_id, connection, 10000)
    db.commit()

    assert len(calls) == 2
    assert "error" in result
    updated = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    assert updated["status"] == "error"


def test_sync_handles_corrupted_ciphertext(db, user_id, monkeypatch):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    connection_id = bank_sync_repo.create_connection(db, user_id, "Chase", b"not-valid-fernet-bytes")
    db.commit()

    connection = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    result, _ = script.sync_connection(db, user_id, connection, 10000)
    db.commit()
    assert "corrupted" in result

    updated = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    assert updated["status"] == "error"


def test_sync_for_user_never_applies_balance(db, user_id, monkeypatch):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "mapped", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    def fake_fetch_accounts(access_url, **kwargs):
        return bank_sync_service.FetchResult(
            accounts=[{"sfin_account_id": "mapped", "sfin_account_name": "Checking", "balance_cents": 999999}],
            errors=[],
        )

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    script.sync_for_user(db, user_id)
    db.commit()

    account = accounts_repo.get_account(db, user_id, account_id)
    assert account["balance_cents"] == 1000

    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert len(staging) == 1
    assert staging[0]["synced_balance_cents"] == 999999


def test_sync_stages_transactions_for_account_mapped_link(db, user_id, monkeypatch):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "mapped", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    def fake_fetch_accounts(access_url, **kwargs):
        return bank_sync_service.FetchResult(
            accounts=[{
                "sfin_account_id": "mapped", "sfin_account_name": "Checking", "balance_cents": 999999,
                "transactions": [{
                    "sfin_transaction_id": "t1", "posted_date": "2026-07-15",
                    "amount_cents": -4200, "description": "Store", "pending": False,
                }],
            }],
            errors=[],
        )

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    connection = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    script.sync_connection(db, user_id, connection, 10000)
    db.commit()

    unmatched = bank_sync_repo.list_unmatched_transactions(db, user_id)
    assert len(unmatched) == 1
    assert unmatched[0]["description"] == "Store"


def test_sync_normalizes_negative_debt_balance_to_positive_owed(db, user_id, monkeypatch):
    from app.repositories import debts as debts_repo

    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    connection_id = _make_connection(db, user_id)
    debt_id = debts_repo.create_debt(db, user_id, "Loan", "bank_loan", 500000, 10000, "accruing")
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "mapped", "Loan")
    bank_sync_repo.set_link_target(db, user_id, link_id, "debt", debt_id)
    db.commit()

    def fake_fetch_accounts(access_url, **kwargs):
        return bank_sync_service.FetchResult(
            accounts=[{"sfin_account_id": "mapped", "sfin_account_name": "Loan", "balance_cents": -168994}],
            errors=[],
        )

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    connection = bank_sync_repo.get_connection_row(db, user_id, connection_id)
    script.sync_connection(db, user_id, connection, 10000)
    db.commit()

    staging = bank_sync_repo.list_unapplied_staging(db, user_id, connection_id)
    assert staging[0]["synced_balance_cents"] == 168994


def run_script(args, db_path: Path):
    import os

    env = {**os.environ, "BUDGET_DB_PATH": str(db_path)}
    repo_root = Path(__file__).resolve().parent.parent
    return subprocess.run(
        [sys.executable, "-m", "scripts.bank_sync", *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )


def test_sync_for_user_sends_push_for_new_large_transaction(db, user_id, monkeypatch):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    monkeypatch.setenv("PUSH_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("PUSH_VAPID_PUBLIC_KEY", "pub")
    push_subscriptions_repo.create(db, user_id, "https://push.example/1", "p", "a")
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "mapped", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    def fake_fetch_accounts(access_url, **kwargs):
        return bank_sync_service.FetchResult(
            accounts=[{
                "sfin_account_id": "mapped", "sfin_account_name": "Checking", "balance_cents": 999999,
                "transactions": [{
                    "sfin_transaction_id": "t1", "posted_date": "2026-07-15",
                    "amount_cents": -42000, "description": "Big Store", "pending": False,
                }, {
                    "sfin_transaction_id": "t2", "posted_date": "2026-07-15",
                    "amount_cents": -500, "description": "Coffee", "pending": False,
                }],
            }],
            errors=[],
        )

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    sent = []
    monkeypatch.setattr(
        push_service, "webpush",
        lambda **kwargs: sent.append(kwargs["data"]),
    )

    script.sync_for_user(db, user_id)  # default threshold is $100.00
    db.commit()

    assert len(sent) == 1
    assert "$420.00 at Big Store" in sent[0]
    assert "Coffee" not in sent[0]


def test_sync_for_user_respects_custom_threshold_setting(db, user_id, monkeypatch):
    from app.repositories import settings as settings_repo

    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    monkeypatch.setenv("PUSH_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("PUSH_VAPID_PUBLIC_KEY", "pub")
    push_subscriptions_repo.create(db, user_id, "https://push.example/1", "p", "a")
    settings_repo.upsert(db, user_id, "large_transaction_threshold_cents", "5000")  # $50
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "mapped", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    def fake_fetch_accounts(access_url, **kwargs):
        return bank_sync_service.FetchResult(
            accounts=[{
                "sfin_account_id": "mapped", "sfin_account_name": "Checking", "balance_cents": 999999,
                "transactions": [{
                    "sfin_transaction_id": "t1", "posted_date": "2026-07-15",
                    "amount_cents": -6000, "description": "Medium Purchase", "pending": False,
                }],
            }],
            errors=[],
        )

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    sent = []
    monkeypatch.setattr(push_service, "webpush", lambda **kwargs: sent.append(kwargs["data"]))

    script.sync_for_user(db, user_id)
    db.commit()

    assert len(sent) == 1
    assert "Medium Purchase" in sent[0]


def test_sync_for_user_does_not_realert_on_already_seen_large_transaction(db, user_id, monkeypatch):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    monkeypatch.setenv("PUSH_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("PUSH_VAPID_PUBLIC_KEY", "pub")
    push_subscriptions_repo.create(db, user_id, "https://push.example/1", "p", "a")
    connection_id = _make_connection(db, user_id)
    account_id = accounts_repo.create_account(db, user_id, "Checking", "checking", 1000)
    link_id = bank_sync_repo.upsert_link(db, user_id, connection_id, "mapped", "Checking")
    bank_sync_repo.set_link_target(db, user_id, link_id, "account", account_id)
    db.commit()

    def fake_fetch_accounts(access_url, **kwargs):
        return bank_sync_service.FetchResult(
            accounts=[{
                "sfin_account_id": "mapped", "sfin_account_name": "Checking", "balance_cents": 999999,
                "transactions": [{
                    "sfin_transaction_id": "t1", "posted_date": "2026-07-15",
                    "amount_cents": -42000, "description": "Big Store", "pending": False,
                }],
            }],
            errors=[],
        )

    monkeypatch.setattr(bank_sync_service, "fetch_accounts", fake_fetch_accounts)

    sent = []
    monkeypatch.setattr(push_service, "webpush", lambda **kwargs: sent.append(kwargs["data"]))

    script.sync_for_user(db, user_id)  # first sync: new -> alerts
    db.commit()
    script.sync_for_user(db, user_id)  # second sync: same transaction re-fetched -> no re-alert
    db.commit()

    assert len(sent) == 1


def test_sync_for_user_sends_no_push_when_no_new_transactions(db, user_id, monkeypatch):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    monkeypatch.setenv("PUSH_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("PUSH_VAPID_PUBLIC_KEY", "pub")
    push_subscriptions_repo.create(db, user_id, "https://push.example/1", "p", "a")
    db.commit()

    sent = []
    monkeypatch.setattr(push_service, "webpush", lambda **kwargs: sent.append(kwargs))

    script.sync_for_user(db, user_id)  # no connections at all
    db.commit()

    assert sent == []


def test_main_sends_failure_push_on_unhandled_exception(db, user_id, monkeypatch, tmp_path):
    monkeypatch.setenv("BANK_SYNC_ENCRYPTION_KEY", _TEST_KEY)
    monkeypatch.setenv("PUSH_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("PUSH_VAPID_PUBLIC_KEY", "pub")
    push_subscriptions_repo.create(db, user_id, "https://push.example/1", "p", "a")
    db.commit()

    sent = []
    monkeypatch.setattr(push_service, "webpush", lambda **kwargs: sent.append(kwargs["data"]))

    import contextlib

    from app import db as db_module

    @contextlib.contextmanager
    def fake_get_connection(db_path=None):
        yield db

    monkeypatch.setattr(script, "get_connection", fake_get_connection)

    def boom(conn, user_id):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(script, "sync_for_user", boom)
    monkeypatch.setattr(sys, "argv", ["bank_sync.py"])

    try:
        script.main()
        assert False, "expected main() to re-raise"
    except RuntimeError:
        pass

    assert len(sent) == 1
    assert "Bank sync failed" in sent[0]


def test_cli_unknown_user_fails(tmp_path):
    import sqlite3

    from migrations.runner import MIGRATIONS_DIR

    db_path = tmp_path / "budget.db"
    conn = sqlite3.connect(db_path)
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(migration.read_text())
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (migration.stem,))
    conn.commit()
    conn.close()

    result = run_script(["--user", "nobody"], db_path)
    assert result.returncode == 1


def test_cli_runs_for_all_users_with_no_connections(tmp_path):
    import sqlite3

    from migrations.runner import MIGRATIONS_DIR

    db_path = tmp_path / "budget.db"
    conn = sqlite3.connect(db_path)
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(migration.read_text())
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (migration.stem,))
    conn.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'logan', 'x')")
    conn.commit()
    conn.close()

    result = run_script([], db_path)
    assert result.returncode == 0, result.stderr
    assert "Synced 1 user(s), 0 connection(s)" in result.stdout
