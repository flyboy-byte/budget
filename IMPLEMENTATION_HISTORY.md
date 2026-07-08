# Implementation History

A chronological record of what was built, when, why, and what broke along the way.
This is the build log — for what's currently true about the codebase (no dates, no
narrative), see `ARCHITECTURE.md`. `IDEAS.md` is the forward-looking backlog; this file
only records what already shipped.

This file absorbs the now-fully-built `bankplan.md` and `pwaplan.md` scoping docs
(both retired once every phase in them shipped) plus the build narrative that used to
live inline in `plan.md`.

## MVP build (through mid-2026-07)

Core data model, calculation engine, and CRUD screens for accounts/debts/obligations/
committed purchases/income events/spending leaks, the dashboard, forecast/what-if view,
CSV+JSON export/backup, and the 4-destination hub nav (Home/Money/Activity/More). See
`ARCHITECTURE.md` for the resulting shape — not re-narrated here since it predates this
file's phase-by-phase detail.

**2026-07-12 — Dashboard quick actions.** `/today` added six quick-action forms
(update balance, record payment, add purchase, log spending leak, mark bill paid, mark
income received) using htmx out-of-band swaps — the first place in the app using htmx
for more than delete-without-reload. `app/services/payments.py` shipped the same day:
the one place a purchase payment gets recorded from `/today`, writing a `transactions`
ledger row via the new `app/repositories/transactions.py`.

**2026-07-13 — Quick actions move money automatically; debt payoff projection;
"no bank sync" non-goal reversed.** Marking a bill paid, recording a purchase payment,
and marking income received now also debit/credit the default account
(`accounts_repo.get_default_account`/`adjust_balance`) instead of just flipping a flag —
before this, marking income received left `cash_on_hand` silently drifted from reality.
`calc.py::debt_payoff_projection` shipped as a pure read-time estimate, deliberately
separate from the safe-to-spend/forecast formulas. Separately: an outside user's
voice-message wishlist (Rocket Money's net-cash feature) prompted reversing the
"no bank sync/Plaid" non-goal — scoped narrowly to SimpleFIN-based balance sync, not a
general bank-data invitation; QuickBooks OAuth auto-categorization was explicitly
deprioritized.

## SimpleFIN bank sync (absorbed from `bankplan.md`)

**Why SimpleFIN over Plaid/Teller**: researched mid-2026. Plaid's free tier is
essentially gone for hobby use (200 calls per product *lifetime*, business application
required for Production). SimpleFIN Bridge is the same backend used by Actual Budget and
Firefly III — self-serve signup, $1.50/mo or $15/yr flat, no business application. How
it works: the user links banks through SimpleFIN Bridge's own hosted UI (this app never
collects bank credentials directly), generates a one-time Setup Token, this app claims
it once for a permanent Access URL (HTTP Basic Auth baked in), then syncs
`GET <access-url>/accounts` — hard rate limit ~24 req/day, so once-daily sync only.

**Design boundary, kept throughout**: bank sync is strictly "cash already in hand" —
never a path to auto-creating `income_events`, never auto-marking obligations/purchases
paid. Fetched data lands in a review/staging state, never directly into
`accounts.balance_cents`, until the user explicitly confirms.

- **Phase 1 (commit `ae0387f`, 2026-07-14)** — migration + repositories
  (`bank_connections`, `bank_account_links`, `bank_sync_staging`).
- **Phase 2 (2026-07-16)** — `app/services/bank_sync.py` SimpleFIN client
  (`claim_setup_token`, `fetch_accounts`), `app/crypto.py`'s `Fernet` wrapper. Verified
  experimentally: **httpx does NOT auto-send Basic Auth from a URL's embedded
  userinfo** — had to explicitly extract and pass `auth=httpx.BasicAuth(...)`.
- **Phase 3 (2026-07-16)** — `BANK_SYNC_ENCRYPTION_KEY` added to VPS secrets.
- **Phase 4 (2026-07-16)** — `app/routers/bank.py` (connect/list/sync/review/apply/
  disconnect). Manually verified end-to-end against SimpleFIN's real public demo
  endpoint (not the VPS) — full connect→sync→map→review→apply→disconnect loop against
  real (fake-data) responses. Corrected an assumption along the way: the Access URL
  already includes its own path segment, `/accounts` appends to *that*, not to a bare
  host.
- **Phase 5 (2026-07-17)** — `scripts/bank_sync.py`, the cron sync script. Live in the
  VPS crontab daily at 6:00 UTC.
- **Phase 6 (2026-07-16, from a VPS-side session)** — mandatory `/security-review`
  before any real bank connection. One real bug found and fixed (`cfe7b82`, unhandled
  decrypt failure in the sync route). Cleared for and now connected to a real bank
  ("umb").
- **SSRF guard fix (2026-07-16)** — `claim_setup_token`/`fetch_accounts` were POSTing to
  a user-submitted, base64-decoded URL with no validation, a real outbound-request risk
  given this app is multi-tenant-ready. Fixed with an exact-hostname allowlist
  (`bridge.simplefin.org`, `beta-bridge.simplefin.org`), HTTPS-only.
- **Post-launch refinements (2026-07-17)** — bank-sync links can now map to `debts`, not
  just `accounts` (SimpleFIN has no account-type field; a real linked "Loan" account had
  nowhere to go). Sync cooldown became a per-user setting (`bank_sync_cooldown_minutes`,
  default 360) instead of a hardcoded constant.
- **Hardening, 2026-07-21 (four separate passes same day)**: (1) repeated pre-apply
  syncing was inserting duplicate staging rows instead of replacing the pending one;
  a SimpleFIN entry missing its `id` field could violate a `NOT NULL` constraint
  mid-sync. (2) `fetch_accounts` now returns per-account errors instead of silently
  discarding them; `balance_date` now persisted and shown on the review screen; a
  "hasn't synced in a while" badge; an unapplied-staging count on the Money hub tile.
  (3) inline create-account/create-debt during mapping, no more forced trip to
  `/accounts`/`/debts` first. (4) transaction import + payment matching (the explicitly-
  deferred v2 feature): `bank_transaction_staging` table, `/bank/transactions` screen,
  `app/services/bank_transactions.py` — matches a bank-fed transaction to an
  obligation/purchase, writes a ledger row, **deliberately never touches
  `accounts.balance_cents`** (the next balance sync already reflects that movement;
  double-debiting would double-count it).
- **Correction, 2026-07-22 — a real bug that "verification" had missed.** The
  stage-1 debt-sign convention check read the already-*stored* `debts.balance_cents`
  value (a stale manual entry no sync had ever actually overwritten) instead of the
  live SimpleFIN input — every debt `/apply` had actually been silently failing with
  `{"detail":"Invalid data"}` the whole time, because SimpleFIN reports a loan/credit
  balance as **negative** (liability convention) while `debts.balance_cents` has
  `CHECK (balance_cents >= 0)`. Found by reading real staged rows directly. Fixed with
  `normalize_debt_balance_cents` (`abs()`), applied only to debt-mapped links (an
  account-mapped link's negative balance, e.g. overdrawn cash, is legitimate and left
  alone). **Lesson**: a "verification" that reads a persisted value instead of the
  actual live input it's meant to validate can pass while the real bug stays live.

## Feature additions, 2026-07 through early 2026-08

**2026-07-16 — Admin role, debt bank-sync links, self-service password change.**
`users.is_admin` (migration `0003`), enforced by `app/deps.py::require_admin`.
`POST /settings/users` tightened from "any logged-in user" to admin-only. Admin scope is
deliberately narrow — account/auth administration only, never a read/write path into
another user's financial data. No UI can grant the very first admin — that's
`scripts/set_admin.py`, CLI-only.

**2026-07-16 — Cash-on-hand vs. debt framing.** Dashboard's hero note switches to
shortfall phrasing when negative ("You need $X more to cover what's reserved"). Cash-on-
hand/total-debt pulled into their own side-by-side comparison card, both neutral color
(debt isn't inherently "bad" in this app's framing).

**2026-07-17 — Security/functional audit fixes.** TOTP timing leak, a replay race
condition, a negative-amount UX bug — see git log for `9251846`.

**2026-07-21 — Cron secrets bug, round 1 (see full saga below).**

**2026-07-22 — Free-text debt/account types, UI reverted to dropdown.** Started as a
request for "more vague/broader" debt-type choices; got redesigned mid-session into
fully free-text `<input>` + `<datalist>` fields, copying `obligations.category`'s
existing pattern (migration `0007_free_text_types.sql`, drops the `type` `CHECK`
constraint via SQLite's table-rebuild recipe, remaps the legacy `klarna_bnpl` value to
`buy_now_pay_later`). **The user tried it and said plainly they preferred picking from a
dropdown over typing a value by hand** — UI reverted back to `<select>`, keeping the
unconstrained storage and expanded `SEED_TYPES` list underneath. **Lesson, reinforced
twice in this codebase's history now**: don't silently upgrade "give me better dropdown
options" into "get rid of the dropdown," even when a cleaner pattern already exists
elsewhere — confirm the UI shape itself isn't also changing, not just the data model.
(Nearly repeated a third time on 2026-08-03 — a stale, unrelated Claude Code plan-mode
file for this same feature surfaced mid-session and was almost re-applied verbatim
before `IDEAS.md`'s own documented lesson caught it in time.)

**2026-07-29 — TOTP (two-factor authentication).** `users.auth_mode`
(password/totp/both) plus encrypted TOTP secrets (migration `0006_totp.sql`), a
distinct `TOTP_ENCRYPTION_KEY` from bank-sync's so a leak of one key doesn't expose the
other. QR enrollment via `segno`, rendered as a self-contained `data:` URI. Replay
protection via `totp_last_used_step`. No backup/recovery codes — deliberate,
consistent with this app's minimal-recovery posture; `scripts/reset_totp.py` is the
only way back in.

**2026-07-29 — Daily digest email.** Opt-in (`digest_email` setting, blank = off),
sent via Resend's HTTP API since the client-side EmailJS widget on `login.html` can't
run headless from a cron job.

**2026-07-29 — "Why did safe-to-spend move?" narrative + "Can I afford this?" check.**
`app/services/narrative.py` diffs today's live safe-to-spend against yesterday's
snapshot and names the top 1-2 ledger movers. `POST /today/afford` reuses
`whatif.py::run_whatif` unchanged with a single hypothetical purchase — read-only, no
commit.

### The cron dash-vs-bash saga (2026-07-29 through 2026-08-01)

A genuinely repeated lesson worth keeping intact rather than condensing away.

- **2026-07-17**: `scripts/bank_sync.py` added to the VPS crontab, daily at 6:00 UTC.
- **Silently failing from day one**: cron's environment doesn't source
  `budget.service`'s `EnvironmentFile=`, so `BANK_SYNC_ENCRYPTION_KEY` was never set for
  the cron process — every run crashed with `RuntimeError`, unnoticed, logged daily to
  `bank_sync.log`.
- **2026-07-29 "fix"**: added `source ~/.config/budget/secrets.env` to the crontab entry,
  "verified" by a manual run — **from an interactive bash shell**, not the way cron
  actually invokes commands.
- **Still broken the same day it "shipped"**: cron runs `/bin/sh`, which on this VPS is
  `dash`, not bash. `source` is a bash-only builtin — `sh -c '... source ... && ...'`
  fails immediately with `source: not found`, and the whole `&&` chain (including the
  final command's own log redirection) never runs. `bank_sync.log` stopped updating the
  same day the "fix" shipped; `digest.log` (added the same day, same broken pattern) had
  **never existed at all** — the daily digest email had never once sent.
  `digest_email` had also been sitting on the settings form silently disconnected the
  entire time it existed.
- **Real fix (2026-08-01)**: swap `source` for the POSIX `.` command, which works under
  both `dash` and `bash`. **Any future cron-secrets verification must test via
  `sh -c '<exact crontab line>'`, never an interactive shell** — an interactive shell's
  `$SHELL` masks exactly this class of bug. Verified this time by actually sending a
  real digest email and seeing real staged bank_sync data.

## PWA (absorbed from `pwaplan.md`)

Direct trigger: a friend who tried the app said "make it a PWA so I can get native
notifications... as well as it being handy in my app drawer." Scoped in three parts:
Part 1 (core installability/service-worker/push), Part 2 ("how native can a PWA
actually get" experiment — visual/chrome, app shortcuts, badging, haptics, explicitly
"full experiment and glory" per the app owner's choice), Part 3 (a real, not
aspirational, visual/UI design pass, flagged separately: "we got decent colors and good
functionality, but visuals and ease isn't perfect yet").

### Part 1 — core PWA

- **Phase 1 (installable shell)** and **Phase 2 (service worker for static assets
  only)** — manifest, icons (hand-rolled PNG encoder, no PIL in the venv), `sw.js`
  cache-first for `/static/*` GET only.
- **Two real bugs found only via a real browser (Playwright), invisible to
  TestClient/curl since neither executes JS** — confirmed push had been completely
  non-functional in production since first deploy:
  1. `base.html`'s inline script called `document.body.addEventListener(...)` while the
     browser was still parsing `<head>`, before `<body>` exists — threw immediately in
     every real browser, silently killing the service-worker registration line right
     after it. Fixed by deferring to `DOMContentLoaded`.
  2. `register('/static/sw.js')` with no explicit scope defaulted to scope `/static/`,
     which can never cover an actual page — `navigator.serviceWorker.ready` never
     resolved anywhere. Fixed by serving the worker at the site root (`GET /sw.js`) and
     registering with `{ scope: '/' }` explicitly.
- **Phase 3 (push subscription plumbing)** — VAPID keypair, `push_subscriptions` table,
  `app/services/push.py`, subscribe/unsubscribe UI. The subscribe button initially hung
  silently on any failure (no try/catch around the async flow) — fixed with per-step
  status messages.
- **Phase 4 (bank-sync cron failure alert)** and **Phase 5 (large-transaction alert)**
  — Phase 5 originally shipped as a vague "N new transactions to review" count, replaced
  same-day per explicit user request with a configurable-dollar-threshold alert once it
  became clear the vague version wasn't valuable.
- **Icon bug**: the first hand-rolled PNG icon generator produced a garbled cross-shape
  background (broken rounded-corner math) and an unreadable freehand "B" glyph. Rebuilt
  using a fixed 5×7 dot-matrix bitmap font instead of freehand vector math.
- **Phase 6 (bill-due push)** and **Phase 7 (low-safe-to-spend push)** — scoped
  2026-08-01, deliberately left unbuilt that session ("this is a future session's plan,
  not queued as next"), built 2026-08-03. Phase 7 confirmed daily-check-only for v1 (not
  real-time) — a real-time version would need the check wired into every money-moving
  route, a meaningfully bigger change; ship the cheap version first, upgrade only if the
  daily cadence proves too slow in practice.

### Part 2 — native-feel experiment, and Part 3 — visual pass (both 2026-08-03)

Researched current (2026) browser support before scoping, since it's genuinely uneven:
**Android Chrome does not support the Badging API at all**; **iOS Safari has never
supported the Vibration API**; manifest `shortcuts` don't exist on iOS. All noted in
`ARCHITECTURE.md`'s PWA section rather than oversold here.

- **Phase 8** — cross-document View Transitions, a single CSS opt-in line.
- **Phase 9** — standalone-mode-only app shell chrome: bottom nav (doubling as
  Phase 17's icon work), safe-area insets, killed pull-to-refresh.
- **Phase 10** — manifest `shortcuts`.
- **Phase 11** — Badging API, backed by a new `GET /bank/unmatched-count` endpoint.
- **Phase 12** — haptics on quick-action confirms and notification clicks (the latter
  needs a `postMessage` from the service worker to the focused client, since
  `navigator.vibrate` isn't available inside a service worker).
- **Phase 13-17** (visual pass) — hero card gets more visual weight (gradient top bar,
  accent tint, bigger figure, deeper shadow), a real `--accent-gradient` token used
  sparingly (the hero card only, not buttons everywhere — a gradient on every button is
  what makes a "gradient pass" look dated instead of intentional), hub-card hover
  elevation, `/settings` widened with a `.field-grid` utility for paired fields, and
  hand-rolled nav icons.
- **Stale `CACHE_NAME` bug (2026-08-03)**: every one of the above CSS/manifest changes
  shipped without bumping the service worker's `CACHE_NAME` — despite the worker's own
  code comment instructing exactly that. Returning devices kept serving a stale cached
  `app.css` cache-first; confirmed on a real device showing partially-updated icons but
  not the newer bottom-nav positioning, both from the same stale cache generation. Fixed
  by bumping `v1` → `v2`.
- **Settings rework (2026-08-03)** — moved to a "notifications first, easy to spot and
  change a single parameter" layout per explicit user request (framed around ADHD-
  friendly UX: fast, low-friction, minimal scanning to find the thing you want). Split
  the previously-single settings form into an independently-submittable "Notifications"
  form (push + digest email + both thresholds, `POST /settings/notifications`) and a
  separate "Calculation settings" form — changing one no longer re-submits or
  re-validates the other.

## Documentation reorg (2026-08-03)

`plan.md`, `bankplan.md`, and `pwaplan.md` retired. `bankplan.md`/`pwaplan.md` were both
fully built and had drifted into pure history logs; `plan.md` mixed current-state spec
with build narrative throughout, making it hard to tell "what's still true" from "what
happened once." Split into `ARCHITECTURE.md` (current state only) and this file
(everything dated/historical, this reorg included).
