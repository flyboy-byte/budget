-- Drops the DB-level CHECK constraint on committed_purchases.category, making it
-- plain free text — same shape as 0007_free_text_types.sql for accounts.type and
-- debts.type. The app repo layer never validated category in Python either; this
-- only removes a needless constraint, not the only safety net. See PLAN.md §3.
--
-- SQLite has no ALTER TABLE support for dropping/loosening a CHECK constraint, so
-- this is SQLite's own documented rebuild recipe: new table, copy rows (same IDs,
-- no remapping -- transactions.committed_purchase_id references this table and
-- would otherwise trip mid-rebuild). Wrapped in foreign_keys OFF/ON accordingly.

PRAGMA foreign_keys = OFF;

CREATE TABLE committed_purchases_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
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
INSERT INTO committed_purchases_new
  (id, user_id, name, category, amount_cents, amount_paid_cents, status,
   order_date, expected_arrival_date, payment_deadline, priority, notes,
   created_at, updated_at)
  SELECT id, user_id, name, category, amount_cents, amount_paid_cents, status,
         order_date, expected_arrival_date, payment_deadline, priority, notes,
         created_at, updated_at
  FROM committed_purchases;
DROP TABLE committed_purchases;
ALTER TABLE committed_purchases_new RENAME TO committed_purchases;
CREATE INDEX idx_purchases_user ON committed_purchases(user_id, status);

PRAGMA foreign_keys = ON;
PRAGMA foreign_key_check;
