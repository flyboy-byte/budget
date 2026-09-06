-- Per-debt "coarse tracking" flag (PLAN.md §3, decided 2026-09-05: per-debt, not
-- global -- different debts churn at different rates). A flagged debt is exempt from
-- the staleness check (app/routers/dashboard.py::_balance_freshness) -- the point is
-- a high-churn debt (e.g. a credit card swiped daily) that's realistically only
-- updated statement-to-statement, not live, shouldn't dim the whole dashboard for
-- being "old." Doesn't change what feeds safe_to_spend (still just the minimum
-- payment due in the window, never the balance itself).
--
-- Plain ADD COLUMN, not a rebuild -- SQLite supports adding a nullable/defaulted
-- column without the CHECK-drop rebuild recipe 0007/0012 needed.

ALTER TABLE debts ADD COLUMN coarse_tracking INTEGER NOT NULL DEFAULT 0 CHECK (coarse_tracking IN (0,1));
