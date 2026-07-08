-- Adds TOTP (authenticator-app) as an optional login factor, per-user configurable
-- as password-only, TOTP-only, or both. See IDEAS.md for rationale.

ALTER TABLE users ADD COLUMN totp_secret_encrypted BLOB;
-- Fernet-encrypted (app.crypto.encrypt_totp_secret), NULL until enrollment starts.

ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0 CHECK (totp_enabled IN (0,1));
-- Only flips to 1 after the user proves the QR scan worked by submitting one valid
-- code back -- never enabled by generating a secret alone.

ALTER TABLE users ADD COLUMN totp_last_used_step INTEGER;
-- Replay protection: the last 30s time-step successfully consumed at login.

ALTER TABLE users ADD COLUMN auth_mode TEXT NOT NULL DEFAULT 'password'
  CHECK (auth_mode IN ('password','totp','both'))
  CHECK (auth_mode = 'password' OR totp_enabled = 1);
-- Structurally can't require totp/both without a confirmed, enabled secret --
-- verified this cross-column CHECK is actually enforced by ALTER TABLE ADD COLUMN
-- on this project's SQLite version (3.53+), unlike the bank_account_links.debt_id
-- case in migration 0003 which worked around an older/different limitation.
