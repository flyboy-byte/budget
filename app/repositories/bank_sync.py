"""Raw parametrized SQL for bank_connections / bank_account_links /
bank_sync_staging. No business logic — every function takes user_id explicitly
and scopes every query by it. access_url_encrypted is stored/returned as opaque
bytes here; encryption/decryption lives in app/crypto.py, not this module."""
import sqlite3

CONNECTION_STATUSES = ("active", "revoked", "error")


# ----- bank_connections -----

def list_connections(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM bank_connections WHERE user_id = ? ORDER BY created_at", (user_id,)
    ).fetchall()


def get_connection_row(conn: sqlite3.Connection, user_id: int, connection_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM bank_connections WHERE user_id = ? AND id = ?", (user_id, connection_id)
    ).fetchone()


def create_connection(
    conn: sqlite3.Connection, user_id: int, label: str, access_url_encrypted: bytes
) -> int:
    cur = conn.execute(
        """INSERT INTO bank_connections (user_id, label, access_url_encrypted)
           VALUES (?, ?, ?)""",
        (user_id, label, access_url_encrypted),
    )
    return cur.lastrowid


def mark_synced(
    conn: sqlite3.Connection, user_id: int, connection_id: int, synced_at: str, warning: str | None = None
) -> bool:
    """warning is a non-fatal note (e.g. a summarized SimpleFIN errlist) — status
    stays 'active' either way, since a partial per-account error doesn't mean the
    whole connection failed; it's just surfaced instead of clearing last_error."""
    cur = conn.execute(
        """UPDATE bank_connections SET last_synced_at = ?, status = 'active', last_error = ?
           WHERE user_id = ? AND id = ?""",
        (synced_at, warning, user_id, connection_id),
    )
    return cur.rowcount > 0


def mark_error(conn: sqlite3.Connection, user_id: int, connection_id: int, error: str) -> bool:
    cur = conn.execute(
        "UPDATE bank_connections SET status = 'error', last_error = ? WHERE user_id = ? AND id = ?",
        (error, user_id, connection_id),
    )
    return cur.rowcount > 0


def delete_connection(conn: sqlite3.Connection, user_id: int, connection_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM bank_connections WHERE user_id = ? AND id = ?", (user_id, connection_id)
    )
    return cur.rowcount > 0


# ----- bank_account_links -----

def list_links(conn: sqlite3.Connection, user_id: int, connection_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM bank_account_links WHERE user_id = ? AND bank_connection_id = ?",
        (user_id, connection_id),
    ).fetchall()


def get_link(conn: sqlite3.Connection, user_id: int, link_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM bank_account_links WHERE user_id = ? AND id = ?", (user_id, link_id)
    ).fetchone()


def upsert_link(
    conn: sqlite3.Connection,
    user_id: int,
    connection_id: int,
    sfin_account_id: str,
    sfin_account_name: str,
) -> int:
    """Called once per SimpleFIN account seen during a sync — creates the link row
    if new, leaves account_id untouched (still NULL/unmapped) if it already exists."""
    conn.execute(
        """INSERT INTO bank_account_links (user_id, bank_connection_id, sfin_account_id, sfin_account_name)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (bank_connection_id, sfin_account_id)
           DO UPDATE SET sfin_account_name = excluded.sfin_account_name""",
        (user_id, connection_id, sfin_account_id, sfin_account_name),
    )
    # cur.lastrowid is unreliable on the DO UPDATE path (not guaranteed reset),
    # so always look the id up explicitly rather than trust it.
    return conn.execute(
        "SELECT id FROM bank_account_links WHERE bank_connection_id = ? AND sfin_account_id = ?",
        (connection_id, sfin_account_id),
    ).fetchone()["id"]


def set_link_target(
    conn: sqlite3.Connection, user_id: int, link_id: int, target_type: str | None, target_id: int | None
) -> bool:
    """target_type is 'account', 'debt', or None (unlink). Always nulls the other
    column, so a link can never end up mapped to both at once."""
    account_id = target_id if target_type == "account" else None
    debt_id = target_id if target_type == "debt" else None
    cur = conn.execute(
        "UPDATE bank_account_links SET account_id = ?, debt_id = ? WHERE user_id = ? AND id = ?",
        (account_id, debt_id, user_id, link_id),
    )
    return cur.rowcount > 0


# ----- bank_sync_staging -----

def create_staging_row(
    conn: sqlite3.Connection,
    user_id: int,
    link_id: int,
    synced_balance_cents: int,
    balance_date: int | None = None,
) -> int:
    """Replaces any not-yet-applied staging row already pending for this link, so
    re-syncing before reviewing/applying updates the pending balance in place instead
    of piling up a duplicate row per sync on the review screen. balance_date is
    SimpleFIN's own "as of" unix timestamp for the balance, shown on the review
    screen so a stale bank-side refresh is visible before confirming."""
    conn.execute(
        "DELETE FROM bank_sync_staging WHERE user_id = ? AND bank_account_link_id = ? AND applied = 0",
        (user_id, link_id),
    )
    cur = conn.execute(
        """INSERT INTO bank_sync_staging (user_id, bank_account_link_id, synced_balance_cents, balance_date)
           VALUES (?, ?, ?, ?)""",
        (user_id, link_id, synced_balance_cents, balance_date),
    )
    return cur.lastrowid


def list_unapplied_staging(conn: sqlite3.Connection, user_id: int, connection_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT s.* FROM bank_sync_staging s
           JOIN bank_account_links l ON l.id = s.bank_account_link_id
           WHERE s.user_id = ? AND l.bank_connection_id = ? AND s.applied = 0
           ORDER BY s.synced_at DESC""",
        (user_id, connection_id),
    ).fetchall()


def count_unapplied_staging(conn: sqlite3.Connection, user_id: int) -> int:
    """Total not-yet-applied staging rows across every connection for this user —
    used for the Money hub's "pending review" nudge, since an unattended cron sync
    can leave a review waiting with nothing else surfacing it."""
    return conn.execute(
        "SELECT COUNT(*) FROM bank_sync_staging WHERE user_id = ? AND applied = 0",
        (user_id,),
    ).fetchone()[0]


def mark_staging_applied(conn: sqlite3.Connection, user_id: int, staging_id: int) -> bool:
    cur = conn.execute(
        "UPDATE bank_sync_staging SET applied = 1 WHERE user_id = ? AND id = ?",
        (user_id, staging_id),
    )
    return cur.rowcount > 0


# ----- bank_transaction_staging -----

def create_transaction_staging_rows(
    conn: sqlite3.Connection, user_id: int, link_id: int, transactions: list[dict]
) -> list[dict]:
    """Unlike balance staging (replace-on-resync), transactions accumulate over
    SimpleFIN's rolling window and the same ones get re-fetched repeatedly — this
    upserts by (link, sfin_transaction_id) and, on conflict, only refreshes
    amount/description/pending (a pending transaction can post later); it never
    touches status/matched_* so an already-matched-or-dismissed row can't be
    silently reset by the next sync.

    Returns the subset of `transactions` that were genuinely new this call (not
    just refreshed) -- used by scripts/bank_sync.py to alert on new large
    transactions without re-alerting on ones already seen in a prior sync."""
    newly_inserted = []
    for txn in transactions:
        existing = conn.execute(
            """SELECT 1 FROM bank_transaction_staging
               WHERE bank_account_link_id = ? AND sfin_transaction_id = ?""",
            (link_id, txn["sfin_transaction_id"]),
        ).fetchone()
        if existing is None:
            newly_inserted.append(txn)
        conn.execute(
            """INSERT INTO bank_transaction_staging
               (user_id, bank_account_link_id, sfin_transaction_id, posted_date,
                amount_cents, description, pending)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (bank_account_link_id, sfin_transaction_id)
               DO UPDATE SET amount_cents = excluded.amount_cents,
                              description = excluded.description,
                              pending = excluded.pending""",
            (
                user_id,
                link_id,
                txn["sfin_transaction_id"],
                txn["posted_date"],
                txn["amount_cents"],
                txn["description"],
                int(txn["pending"]),
            ),
        )
    return newly_inserted


def list_unmatched_transactions(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """App-wide, not per-connection — matching a bill doesn't care which bank
    connection the money came from."""
    return conn.execute(
        """SELECT s.* FROM bank_transaction_staging s
           WHERE s.user_id = ? AND s.status = 'unmatched'
           ORDER BY s.posted_date DESC""",
        (user_id,),
    ).fetchall()


def list_all_transactions(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """Every staged bank transaction regardless of status — unlike
    list_unmatched_transactions, this deliberately includes matched and dismissed
    rows. Recurring-bill detection wants the full history as evidence: a bill the
    user already matched once is exactly the kind of thing that recurs, and
    dropping dismissed rows would hide the repeat charges that make a pattern
    visible in the first place. Read-only, never used for a mutation path."""
    return conn.execute(
        """SELECT * FROM bank_transaction_staging WHERE user_id = ?
           ORDER BY posted_date DESC""",
        (user_id,),
    ).fetchall()


def get_transaction_staging(conn: sqlite3.Connection, user_id: int, staging_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM bank_transaction_staging WHERE user_id = ? AND id = ?",
        (user_id, staging_id),
    ).fetchone()


def dismiss_transaction(conn: sqlite3.Connection, user_id: int, staging_id: int) -> bool:
    cur = conn.execute(
        "UPDATE bank_transaction_staging SET status = 'dismissed' WHERE user_id = ? AND id = ? AND status = 'unmatched'",
        (user_id, staging_id),
    )
    return cur.rowcount > 0


def mark_transaction_matched(
    conn: sqlite3.Connection,
    user_id: int,
    staging_id: int,
    ledger_transaction_id: int,
    *,
    obligation_id: int | None = None,
    committed_purchase_id: int | None = None,
) -> bool:
    cur = conn.execute(
        """UPDATE bank_transaction_staging
           SET status = 'matched', matched_obligation_id = ?, matched_committed_purchase_id = ?,
               ledger_transaction_id = ?
           WHERE user_id = ? AND id = ? AND status = 'unmatched'""",
        (obligation_id, committed_purchase_id, ledger_transaction_id, user_id, staging_id),
    )
    return cur.rowcount > 0
