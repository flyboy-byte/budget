-- Adds a real admin/root role and lets bank-sync links map to debts, not just accounts.
-- See bankplan.md and the settings/admin batch plan for rationale.

ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0,1));
-- is_admin starts 0 for everyone, including the app owner — bootstrapped separately via
-- scripts/set_admin.py, since granting the first admin can't be a UI action.

ALTER TABLE bank_account_links ADD COLUMN debt_id INTEGER REFERENCES debts(id) ON DELETE SET NULL;
-- No CHECK enforcing "not both account_id and debt_id set" — SQLite's ALTER TABLE ADD
-- COLUMN can't add a CHECK referencing an existing column on a non-empty table. Enforced
-- instead in app/repositories/bank_sync.py::set_link_target, which always nulls the other
-- column when setting one.
