-- Web Push subscriptions (PWA push notifications, see pwaplan.md Phase 3).
-- One user can have multiple rows (multiple devices/browsers) -- normal for
-- Web Push, not a bug. `endpoint` is unique per browser-issued subscription;
-- p256dh/auth are the per-subscription encryption keys pywebpush needs to
-- encrypt a payload only that browser can decrypt.

CREATE TABLE push_subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  endpoint TEXT NOT NULL UNIQUE,
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_push_subscriptions_user ON push_subscriptions(user_id);
