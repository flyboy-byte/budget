-- Adds target_type='spending' to the transactions ledger, plus a free-text
-- `category` column.
--
-- Why a new target_type instead of reusing 'other': 'other' is already the
-- inflow type (app/services/bills.py::mark_income_received writes income with
-- it, and app/services/narrative.py's _INFLOW_TYPES allowlists exactly it).
-- Recording ordinary spending as 'other' would make every purchase read as
-- money coming IN, silently inverting the sign of the change narrative. The
-- two need to be distinguishable, so 'spending' gets its own value.
--
-- Same SQLite rebuild recipe as 0007: there's no ALTER TABLE support for
-- widening a CHECK constraint, so it's create-new / copy-rows (same IDs, other
-- tables' FKs depend on them staying put) / drop-old / rename / recreate index.
-- foreign_keys OFF around it because transactions.debt_id/obligation_id/
-- committed_purchase_id/account_id all reference tables that would otherwise
-- trip the check mid-rebuild.

PRAGMA foreign_keys = OFF;

CREATE TABLE transactions_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  transaction_date TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('debt','obligation','committed_purchase','other','spending')),
  debt_id INTEGER REFERENCES debts(id) ON DELETE SET NULL,
  obligation_id INTEGER REFERENCES obligations(id) ON DELETE SET NULL,
  committed_purchase_id INTEGER REFERENCES committed_purchases(id) ON DELETE SET NULL,
  category TEXT,
  memo TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (
    (target_type='debt' AND debt_id IS NOT NULL AND obligation_id IS NULL AND committed_purchase_id IS NULL) OR
    (target_type='obligation' AND obligation_id IS NOT NULL AND debt_id IS NULL AND committed_purchase_id IS NULL) OR
    (target_type='committed_purchase' AND committed_purchase_id IS NOT NULL AND debt_id IS NULL AND obligation_id IS NULL) OR
    (target_type='other' AND debt_id IS NULL AND obligation_id IS NULL AND committed_purchase_id IS NULL) OR
    (target_type='spending' AND debt_id IS NULL AND obligation_id IS NULL AND committed_purchase_id IS NULL)
  )
);

-- Column list is explicit (not SELECT *) because the new table interleaves
-- `category` before `memo`; a positional copy would land memo text in category.
INSERT INTO transactions_new
  (id, user_id, transaction_date, amount_cents, account_id, target_type,
   debt_id, obligation_id, committed_purchase_id, category, memo, created_at)
SELECT
  id, user_id, transaction_date, amount_cents, account_id, target_type,
  debt_id, obligation_id, committed_purchase_id, NULL, memo, created_at
FROM transactions;

DROP TABLE transactions;
ALTER TABLE transactions_new RENAME TO transactions;
CREATE INDEX idx_transactions_user ON transactions(user_id, transaction_date);

PRAGMA foreign_keys = ON;
PRAGMA foreign_key_check;
