# Security context — for a Claude instance running on the VPS

You're reading this because you're operating *on the box itself* (SSH session, cron
job, or some other agent context on `ubuntu@<vps>`), not in a clone of this repo on
someone's laptop. This file exists to give you — specifically — enough context to
reason about how this one project, `budget`, affects the security of the machine
you're standing on, which is shared with at least two other services. Nothing here
is aspirational; every claim below was either read directly from a live config file
or verified by running a command against the real system, most recently updated
2026-07-16 after Phase 4 shipped. If something below looks stale, trust the live
system over this file and update it — this file has already been wrong once this
session (see the Phase 4 note below) after a route count changed underneath it.

**Phase 6 security review: COMPLETE as of 2026-07-16.** All checklist items passed.
One medium finding fixed (`crypto.decrypt()` in `sync_connection()` now wrapped in
try-except ValueError — commit `cfe7b82`). Budget app is cleared for real bank
connection. See `~/security/hardening-log.md` for the full audit report.

**Mandatory checkpoint, not just background reading:** this file exists specifically
because `IMPLEMENTATION_HISTORY.md`'s Phase 6 requires a dedicated `/security-review` pass on this
box before SimpleFIN bank-sync is used with a real bank connection — that promise was
made explicitly to the app owner and is treated as non-negotiable, not a nice-to-have.
**Phase 4 has already landed (2026-07-16) — `/bank/connect` and friends are real,
reachable routes right now, not a future hypothetical.** Phase 6 HAS NOW HAPPENED
(2026-07-16). When re-running for future phases, it must be run from a VPS-side session using this file as the
starting context, not skipped or done only from a laptop clone that can't see the
real systemd unit, the real `~/.config/budget/` contents, or the real nginx config.
Read this whole file, verify every claim in it against the live system (don't trust
it blindly — it may be stale by then), and specifically check: is
`~/.config/budget/secrets.env` still mode 600? Does the live `bank_connections`
table have any row that looks like a real bank (not the SimpleFIN demo endpoint) —
if so, Phase 6 is already overdue, not upcoming? Does `access_url_encrypted`
actually get excluded from `data/backups/*.json` (test it, don't just read the
code — there's already an automated test for this in `tests/test_bank_routes.py`,
confirm it still passes rather than re-deriving it by hand)? Only after that pass
is this app cleared to be used with a real bank connection.

## What you're looking at, in one paragraph

`budget` is a private, single-tenant-by-default FastAPI app (self-hosted personal
finance tracker) running as `budget.service`, a systemd `--user` unit under the
`ubuntu` account, reverse-proxied by nginx at `budget.flyboybyte.com`. It shares this
VPS with at least `disc.flyboybyte.com` and `trading.flyboybyte.com` — same pattern,
same user, same nginx, no containerization, no per-app OS-level isolation between
them. If you're auditing this box's overall attack surface, `budget` is one of
several roughly-identical Python processes bound to `127.0.0.1` behind the same
reverse proxy, not a special case — except for the credential-handling note below,
which is genuinely new as of 2026-07-16 and worth your attention specifically.

## Process / network surface this app adds

- **Listens on `127.0.0.1:5758` only** — never bound to a public interface directly.
  nginx terminates TLS and reverse-proxies to it. If you ever find this process bound
  to `0.0.0.0` or a public IP, something is wrong — check `ExecStart` in
  `~/.config/systemd/user/budget.service` for `--host` and confirm it still reads
  `127.0.0.1`.
- **`app/routers/auth.py` trusts `X-Forwarded-For`** for rate-limiting purposes. This
  is only safe because nginx is the only thing that can reach this process — if
  nginx's proxy config ever stops setting/overwriting that header correctly, or if
  something else gets a path to hit port 5758 directly, this becomes a rate-limit
  bypass. Don't change the nginx vhost for this app without keeping that in mind.
- **Outbound network calls now possible, as of Phase 4 (2026-07-16).**
  `app/services/bank_sync.py` is the only code in this app that makes an outbound
  HTTP request to a third party (SimpleFIN Bridge) — and it is **no longer dead
  code**: `app/routers/bank.py` calls it from real, reachable routes
  (`POST /bank/connect` → `claim_setup_token`, `POST /bank/{id}/sync` →
  `fetch_accounts`). Verify this yourself rather than trusting this paragraph:
  `grep -rn "bank_sync" app/routers/ app/main.py` — if it returns matches (it
  should, now), outbound calls are possible whenever a logged-in user submits
  a Setup Token or clicks "Sync now." Two things bound that surface: (1) the
  SSRF host-allowlist in `bank_sync.py::_require_allowed_host` restricts every
  outbound call to `bridge.simplefin.org`/`beta-bridge.simplefin.org` over
  `https` only — nothing else, regardless of what a user submits; (2) as of
  this writing, no real bank has ever been connected — everything tested so far
  used SimpleFIN's public demo endpoint (fake data). Check `bank_connections`
  row count / `label` values in the live DB if you need to confirm whether a
  *real* connection exists before assuming this is still purely theoretical.

## What this app stores, and how sensitive it is

- SQLite file at `~/budget/data/budget.db` (not web-served, not in the git repo,
  gitignored). Contains: password hashes (Argon2id, not reversible), server-side
  session tokens (opaque random, revocable, not JWTs), and one or more users' full
  financial pictures (account balances, debts, obligations, income). Treat this file
  as equivalent in sensitivity to a password database plus someone's bank statements.
- Daily JSON/CSV backups at `~/budget/data/backups/` — **plaintext, uncompressed,
  full copies of the same data**, on the same disk. If you're assessing blast radius
  of a disk-level compromise, backups don't add new risk (same disk, same access), but
  they do mean a compromise isn't limited to "whatever's in the live DB right now" —
  30 days of point-in-time snapshots are sitting there too (`--keep 30` in the backup
  cron job, see `ARCHITECTURE.md`'s Deployment section).
- **New as of 2026-07-16: `~/.config/budget/secrets.env`**, mode 600, owned by
  `ubuntu`, holding `BANK_SYNC_ENCRYPTION_KEY` — a Fernet symmetric key. This is the
  first time this app has put a *decryptable* secret on disk (everything before this
  was one-way hashes or opaque random tokens with no recoverable plaintext). **As of
  Phase 4, this key is live** — `POST /bank/connect` encrypts a real Access URL with
  it the moment anyone submits a Setup Token, and `accounts.balance_cents` in
  `~/budget/data/budget.db` may now contain values sourced from a bank feed rather
  than manual entry. If you're auditing this box and find this file, know that: (a) it's intentionally outside the git working directory
  so a `git pull`/`clean` can never touch it, (b) it's referenced by
  `~/.config/systemd/user/budget.service` via a non-optional `EnvironmentFile=` line
  (missing the file fails the service to start, on purpose — fail loud, not silent),
  (c) losing it means any future encrypted bank-sync data becomes permanently
  unrecoverable — there is no recovery path, which is the point of Fernet, but also
  means this file needs to be in *your* mental model of "what actually needs backing
  up off-box," separate from the routine `data/backups/` cron job, which does not
  and should not ever include it.

## What this app does NOT do to the shared VPS

- No new system users, no new sudo grants, no changes to SSH config, no new open
  ports beyond the existing nginx-fronted pattern every other app here already uses.
- No Docker, no new package manager, no language runtime beyond the Python venv
  already local to `~/budget/.venv` — doesn't touch system Python or any other app's
  venv.
- `deploy.sh`'s remote steps (`git pull`, `pip install -r requirements.txt` inside
  the app's own venv, migrations, `systemctl --user restart budget.service`) are
  scoped to this app's own directory and its own systemd unit — it has no mechanism
  to reach `disc`'s or `trading`'s services, files, or venvs. If you're checking
  whether one app's deploy could clobber a sibling app, the answer for `budget` is no,
  by construction, not just by convention.

## The one thing actively in progress — read `IMPLEMENTATION_HISTORY.md` before assuming more exists

As of 2026-07-16, SimpleFIN bank-sync integration is mid-build (Phases 1-4 of 6
shipped: schema, HTTP client, VPS secrets file, and now the actual `/bank`
routes/UI). **There IS now an in-app way to link a bank (`/bank/connect`) and
routes that call SimpleFIN for real.** As of the last commit checked, no *real*
bank has been connected yet — every end-to-end test used SimpleFIN's public demo
endpoint (`demo:demo@beta-bridge.simplefin.org`, fake data) run from a developer's
local machine, never against this VPS. **The mandatory, explicitly-promised
`/security-review` pass (Phase 6) has NOT happened as of this writing** — it must
happen before any real bank connection is made, and it must be run from a VPS-side
session per the note at the top of this file. Do not assume that pass has happened
just because this file exists; check `IMPLEMENTATION_HISTORY.md`'s status header and `git log`
for a commit that says so, and check the live `bank_connections` table for any
row with a non-demo-looking `label` — that would mean a real connection already
exists and Phase 6 is now overdue, not upcoming.

If you're asked to help build Phase 5+ from a VPS-side session: the same rules from
`IMPLEMENTATION_HISTORY.md`'s Security section apply regardless of which machine is doing the
building — Access URL treated as sensitive as a password, encrypted at rest via the
key already described above, staged fetched data reviewed before it ever writes to
`accounts.balance_cents`, and every new mutating route needs the same CSRF +
session-scoping every other route in this app already has (`app/deps.py`'s
`verify_csrf_token`, `get_current_user_id`).

## Sibling-app context (for blast-radius reasoning, not because this repo owns them)

`disc.flyboybyte.com` and `trading.flyboybyte.com` run on this same VPS, same user,
same systemd `--user` pattern, same nginx. This repo doesn't document their internals
and you shouldn't assume anything about their code quality or data sensitivity from
this file. What's relevant here: because everything runs as the same `ubuntu` user
with no container/namespace isolation, a compromise of *any* one app's process
(including this one) has the same practical blast radius as a compromise of the
`ubuntu` account itself — file read/write across all of them, not sandboxed per-app.
This is a known, accepted tradeoff for a lightly-loaded personal VPS, not an oversight
specific to `budget` — see `MAKING_PUBLIC.md`'s "Not a concern" section for the
box-level hardening that exists independently of any one app (SSH key-only auth,
`ufw`, `fail2ban`, unattended-upgrades, nginx rate-limiting).

## If you're about to change something here

- Don't add `Environment=`/`EnvironmentFile=` lines to `budget.service` casually —
  there's now exactly one secret this app needs, and it has exactly one file. If a
  reason arises for a second secret, put it in the same `~/.config/budget/secrets.env`
  file rather than proliferating separate secret files, unless there's a real reason
  to scope them differently (e.g. different rotation cadence).
- Don't ever let `data/backups/*.json` or CSV exports include `access_url_encrypted`
  (or any future bank-sync secret column) — `IMPLEMENTATION_HISTORY.md`'s Security section calls
  this out explicitly as something that must be *tested*, not just documented, before
  Phase 6 is considered done. If you're touching `app/services/export.py`, check this.
- If you find yourself about to run a command that reads this app's live secrets
  (printing the key, dumping `/proc/<pid>/environ`, etc.) as part of "just double
  checking" — don't, unless it's the specific thing you were asked to verify. A
  service coming back `active (running)` after a restart with a non-optional
  `EnvironmentFile=` line is already sufficient proof the file was read successfully;
  you don't need to see the value to know it loaded.
