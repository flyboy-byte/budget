-- Bank transaction import (bankplan.md's deferred v2 feature). Transactions are
-- evidence a bill/purchase was paid, never authorization — every row here stays
-- 'unmatched' until the user explicitly confirms a match or dismisses it.
CREATE TABLE bank_transaction_staging (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  bank_account_link_id INTEGER NOT NULL REFERENCES bank_account_links(id) ON DELETE CASCADE,
  sfin_transaction_id TEXT NOT NULL,
  posted_date TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  description TEXT NOT NULL,
  pending INTEGER NOT NULL DEFAULT 0 CHECK (pending IN (0,1)),
  status TEXT NOT NULL DEFAULT 'unmatched' CHECK (status IN ('unmatched','matched','dismissed')),
  matched_obligation_id INTEGER REFERENCES obligations(id) ON DELETE SET NULL,
  matched_committed_purchase_id INTEGER REFERENCES committed_purchases(id) ON DELETE SET NULL,
  ledger_transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
  fetched_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (bank_account_link_id, sfin_transaction_id)
);
CREATE INDEX idx_bank_transaction_staging_user ON bank_transaction_staging(user_id);
