-- Initial schema for the Cash-Commitment & Runway Tracker.
-- See ../plan.md for the design rationale and calculation rules that depend on this schema.
-- Money: INTEGER cents. Dates/timestamps: ISO-8601 UTC TEXT. Booleans: INTEGER CHECK (x IN (0,1)).
--
-- Multi-tenancy note: every domain table carries a user_id, even though the MVP only ever
-- creates one user via CLI (scripts/init_db.py). This keeps the app single-user in behavior
-- (no registration route, no user-switching UI) while making "add a second/third person" a
-- data-only operation (create a user row via CLI) instead of a schema migration. Each user's
-- data is fully isolated — this is N independent private budgets sharing one deployment, not
-- a shared household ledger.

PRAGMA foreign_keys = ON;

-- ===== auth =====
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
-- No registration route is ever exposed over HTTP, at 1 user or at N users.
-- Users are created only via scripts/init_db.py or a future scripts/add_user.py CLI.

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,                      -- random urlsafe token, stored as-is (server-side revocable)
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  expires_at TEXT NOT NULL,
  csrf_secret TEXT NOT NULL,                -- per-session token compared on mutating requests
  user_agent TEXT
);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);

-- ===== core money tables =====
CREATE TABLE accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('checking','savings','cash','other')),
  balance_cents INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  display_order INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_accounts_user ON accounts(user_id, is_active);

CREATE TABLE debts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('bank_loan','credit_card','klarna_bnpl','tool_truck','personal','other')),
  balance_cents INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
  apr_bps INTEGER CHECK (apr_bps IS NULL OR apr_bps >= 0),   -- APR in basis points (24.99% = 2499); NULL = unknown
  minimum_payment_cents INTEGER NOT NULL DEFAULT 0 CHECK (minimum_payment_cents >= 0),
  due_day_of_month INTEGER CHECK (due_day_of_month IS NULL OR due_day_of_month BETWEEN 1 AND 31),
  next_due_date TEXT,
  interest_status TEXT NOT NULL CHECK (interest_status IN ('accruing','not_accruing','promo_unknown')),
  is_flexible_payment INTEGER NOT NULL DEFAULT 0 CHECK (is_flexible_payment IN (0,1)),
  priority INTEGER NOT NULL DEFAULT 0,          -- manual override rank; 0 = no override, use computed tiering
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),  -- false once paid off/closed
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_debts_user ON debts(user_id, is_active);

CREATE TABLE obligations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
  is_recurring INTEGER NOT NULL DEFAULT 0 CHECK (is_recurring IN (0,1)),
  recurrence_rule TEXT CHECK (recurrence_rule IS NULL OR recurrence_rule IN ('weekly','biweekly','monthly','yearly')),
  due_date TEXT NOT NULL,
  is_required INTEGER NOT NULL DEFAULT 1 CHECK (is_required IN (0,1)),
  is_paid INTEGER NOT NULL DEFAULT 0 CHECK (is_paid IN (0,1)),
  paid_date TEXT,
  auto_pay INTEGER NOT NULL DEFAULT 0 CHECK (auto_pay IN (0,1)),
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_obligations_user ON obligations(user_id, is_paid, due_date);

CREATE TABLE committed_purchases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IN ('career_tool','school','car','debt','hobby','food','gambling','other')),
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
  amount_paid_cents INTEGER NOT NULL DEFAULT 0 CHECK (amount_paid_cents >= 0),
  remaining_cents INTEGER GENERATED ALWAYS AS (amount_cents - amount_paid_cents) STORED,
  status TEXT NOT NULL CHECK (status IN ('planned','ordered','arrived','partially_paid','paid','canceled')),
  order_date TEXT,
  expected_arrival_date TEXT,
  payment_deadline TEXT,
  priority INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (amount_paid_cents <= amount_cents)
);
CREATE INDEX idx_purchases_user ON committed_purchases(user_id, status);

CREATE TABLE income_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  expected_amount_cents INTEGER NOT NULL CHECK (expected_amount_cents >= 0),
  expected_date TEXT NOT NULL,
  confidence TEXT NOT NULL CHECK (confidence IN ('confirmed','likely','uncertain')),
  is_received INTEGER NOT NULL DEFAULT 0 CHECK (is_received IN (0,1)),
  received_date TEXT,
  received_amount_cents INTEGER,
  is_recurring INTEGER NOT NULL DEFAULT 0 CHECK (is_recurring IN (0,1)),
  recurrence_rule TEXT CHECK (recurrence_rule IS NULL OR recurrence_rule IN ('weekly','biweekly','monthly','yearly')),
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_income_user ON income_events(user_id, is_received, expected_date);

CREATE TABLE spending_leaks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  occurred_date TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
  category TEXT NOT NULL CHECK (category IN ('gambling','eating_out','hobby','discs','impulse','entertainment','other')),
  description TEXT,
  was_planned INTEGER NOT NULL DEFAULT 0 CHECK (was_planned IN (0,1)),
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_leaks_user ON spending_leaks(user_id, occurred_date);

-- Polymorphic ledger: exactly one of debt_id/obligation_id/committed_purchase_id set (or none for target_type='other')
CREATE TABLE transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  transaction_date TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('debt','obligation','committed_purchase','other')),
  debt_id INTEGER REFERENCES debts(id) ON DELETE SET NULL,
  obligation_id INTEGER REFERENCES obligations(id) ON DELETE SET NULL,
  committed_purchase_id INTEGER REFERENCES committed_purchases(id) ON DELETE SET NULL,
  memo TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (
    (target_type='debt' AND debt_id IS NOT NULL AND obligation_id IS NULL AND committed_purchase_id IS NULL) OR
    (target_type='obligation' AND obligation_id IS NOT NULL AND debt_id IS NULL AND committed_purchase_id IS NULL) OR
    (target_type='committed_purchase' AND committed_purchase_id IS NOT NULL AND debt_id IS NULL AND obligation_id IS NULL) OR
    (target_type='other' AND debt_id IS NULL AND obligation_id IS NULL AND committed_purchase_id IS NULL)
  )
);
CREATE INDEX idx_transactions_user ON transactions(user_id, transaction_date);

CREATE TABLE snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  snapshot_date TEXT NOT NULL,
  cash_on_hand_cents INTEGER NOT NULL,
  total_debt_cents INTEGER NOT NULL,
  net_position_cents INTEGER NOT NULL,
  reserved_cash_cents INTEGER NOT NULL,
  safe_to_spend_cents INTEGER NOT NULL,
  forecast_position_cents INTEGER NOT NULL,
  protected_floor_cents INTEGER NOT NULL,
  window_days INTEGER NOT NULL,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (user_id, snapshot_date)
);

CREATE TABLE settings (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);
-- seeded rows per user: protected_savings_floor_cents, reserved_window_mode ('next_paycheck'|'end_of_month'|'fixed_days'),
-- reserved_window_fixed_days, forecast_window_days, forecast_income_confidence ('confirmed'|'confirmed_likely'),
-- timezone (IANA name, e.g. 'America/Denver')

CREATE TABLE schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
