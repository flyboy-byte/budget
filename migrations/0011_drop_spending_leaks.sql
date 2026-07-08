-- spending_leaks confirmed unused by the user (0 rows ever, 2026-09-02) and
-- superseded by target_type='spending' free-text categories on the ledger
-- (see 0010_spending_transactions.sql). Export any existing data (per-user
-- CSV/JSON backup via /export) before running this in an environment where
-- the table might not actually be empty.
DROP TABLE spending_leaks;
