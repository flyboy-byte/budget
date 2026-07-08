-- Bank sync (SimpleFIN) schema. See ../bankplan.md for design rationale.
-- access_url_encrypted is a Fernet-encrypted bearer credential — as sensitive as a
-- password, never exported/backed up in plaintext (see app/services/export.py).

CREATE TABLE bank_connections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  access_url_encrypted BLOB NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','revoked','error')) DEFAULT 'active',
  last_synced_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_bank_connections_user ON bank_connections(user_id);

CREATE TABLE bank_account_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  bank_connection_id INTEGER NOT NULL REFERENCES bank_connections(id) ON DELETE CASCADE,
  sfin_account_id TEXT NOT NULL,
  sfin_account_name TEXT NOT NULL,
  account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  UNIQUE (bank_connection_id, sfin_account_id)
);
CREATE INDEX idx_bank_account_links_user ON bank_account_links(user_id);

CREATE TABLE bank_sync_staging (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  bank_account_link_id INTEGER NOT NULL REFERENCES bank_account_links(id) ON DELETE CASCADE,
  synced_balance_cents INTEGER NOT NULL,
  synced_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  applied INTEGER NOT NULL DEFAULT 0 CHECK (applied IN (0,1))
);
CREATE INDEX idx_bank_sync_staging_user ON bank_sync_staging(user_id);
