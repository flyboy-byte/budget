-- Bill-due push reminders (pwaplan.md Phase 6). Per-obligation dedup marker so a
-- daily cron run can't push the same bill twice in one day -- a single per-user
-- flag wouldn't work since multiple bills can be due independently on the same day.

ALTER TABLE obligations ADD COLUMN last_bill_push_date TEXT;
-- NULL until the first push fires for that obligation. Naturally stops blocking
-- once due_date rolls forward (mark_obligation_paid already advances it on
-- payment) -- no separate reset logic needed.
