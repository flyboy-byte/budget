-- Removes the DB-level CHECK constraint on debts.type and accounts.type, making
-- both plain free text (matching obligations.category, which never had one). The
-- old CHECK forced a schema migration for every new/renamed label; the app repo
-- layer never validated type/category in Python, so this only removes a needless
-- constraint, not the only safety net. See IDEAS.md for the full discussion.
--
-- SQLite has no ALTER TABLE support for dropping/loosening a CHECK constraint, so
-- this is SQLite's own documented rebuild recipe: new table, copy rows (same IDs,
-- no remapping -- other tables' foreign keys depend on these staying put), drop
-- old, rename new into place, recreate indexes. Wrapped in foreign_keys OFF/ON
-- since bank_account_links.debt_id and transactions.debt_id/account_id reference
-- these tables and would otherwise trip mid-rebuild.

PRAGMA foreign_keys = OFF;

CREATE TABLE accounts_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  balance_cents INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  display_order INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
INSERT INTO accounts_new SELECT * FROM accounts;
DROP TABLE accounts;
ALTER TABLE accounts_new RENAME TO accounts;
CREATE INDEX idx_accounts_user ON accounts(user_id, is_active);

CREATE TABLE debts_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  balance_cents INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
  apr_bps INTEGER CHECK (apr_bps IS NULL OR apr_bps >= 0),
  minimum_payment_cents INTEGER NOT NULL DEFAULT 0 CHECK (minimum_payment_cents >= 0),
  due_day_of_month INTEGER CHECK (due_day_of_month IS NULL OR due_day_of_month BETWEEN 1 AND 31),
  next_due_date TEXT,
  interest_status TEXT NOT NULL CHECK (interest_status IN ('accruing','not_accruing','promo_unknown')),
  is_flexible_payment INTEGER NOT NULL DEFAULT 0 CHECK (is_flexible_payment IN (0,1)),
  priority INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
INSERT INTO debts_new SELECT * FROM debts;
-- Generalize the one vendor-specific legacy value now that type is free text.
UPDATE debts_new SET type = 'buy_now_pay_later' WHERE type = 'klarna_bnpl';
DROP TABLE debts;
ALTER TABLE debts_new RENAME TO debts;
CREATE INDEX idx_debts_user ON debts(user_id, is_active);

PRAGMA foreign_keys = ON;
PRAGMA foreign_key_check;
