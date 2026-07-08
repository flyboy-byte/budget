-- SimpleFIN's balance-date per account was already parsed by fetch_accounts but
-- discarded before reaching the DB. Storing it lets the review screen show how
-- stale a synced balance is before the user confirms it.
ALTER TABLE bank_sync_staging ADD COLUMN balance_date INTEGER;
