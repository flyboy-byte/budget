# Ideas & Intentions

A living backlog — drop anything here anytime: a one-line thought, something pasted
from another AI chat (financial-advice or otherwise), a raw dump from a local `codex`
brainstorm (see [`codex.md`](./codex.md)). No formatting required in "Raw / unsorted" —
the point is zero friction to capture something before it's lost.

`ARCHITECTURE.md` stays the source of truth for what's actually *built*. This file is the queue
feeding into it. At the start of any session touching new feature work, Claude Code
reads this file, triages "Raw / unsorted" into "Under consideration" or "Rejected /
parked," and moves things to "Promoted" once they actually ship.

## Raw / unsorted

_(nothing yet — drop ideas here)_

## Under consideration

### 2026-08-26 diagnosis: "sorta helpful, feels dead"

Evidence from the live production DB (user `logan`), not speculation:

- `safe_to_spend` was **byte-identical for 6 straight days** (2026-08-21 → 08-26:
  cash `151552`, reserved `55000`, safe `96552` every single day). The daily digest
  repeating itself isn't an email bug — the underlying number genuinely never moved.
- **`obligations` table: 0 rows.** Zero recurring bills exist in the app. The entire
  `55000` reserved figure is one committed purchase (`spec`, $400) plus two debt
  minimums (snapon $125 + capital one $25). Nothing in that set changes day to day.
- `discover` ($1,143) and `student loans` ($3,472) both have
  `minimum_payment_cents = 0`, so they contribute nothing to reserved cash or debt
  priority. `student loans` also has no `next_due_date`.
- **18 unmatched `bank_transaction_staging` rows**, oldest 2026-07-28 (a month
  stale). All small discretionary spend (Allsup's, McDonald's, Sonic, coffee,
  Google). None can match anything — there are no obligations to match against.
- `transactions` (the ledger): **2 rows, and no route in the app reads it back**
  (`grep` for `transactions_repo` in `app/routers/` returns nothing). Every quick
  action writes into a table the user can never see.
- `spending_leaks`: 0 rows, ever.

**Root cause:** the app models *commitments*, the user has entered ~none, so
safe-to-spend reduces to "cash on hand minus a constant." That's what the bank app
already shows. The machinery (windows, forecast, priority, sparkline, push, digest)
is all built and correct — it's running on an empty model.

All four ranked fixes from this diagnosis shipped 2026-08-26 — see the Promoted
section for what each turned into.

**Two-minute data fixes, no code required:** set real `minimum_payment_cents` on
`discover` and `student loans` (and a `next_due_date` on the latter) — reserved cash
becomes truthful immediately. Delete the stale `parts tools` debt (0/0), now that the
list-page delete bug is fixed.

**Cut, 2026-09-02:** `spending_leaks` — 0 rows since it shipped. Manual entry plus
judgmental framing. Superseded by `target_type='spending'` free-text categories on the
ledger (fix 2 above). Removed end-to-end (router, repo, templates, nav, quick-action
card) via `PLAN.md` §1.9, table dropped by migration `0011_drop_spending_leaks.sql`.

- **One-click "export everything as a single spreadsheet" for pasting into an AI
  chat.** User wants to hand a full financial snapshot to ChatGPT/another AI for
  budgeting advice. `/export` today only offers one CSV download per table
  (`export.CSV_TABLES`) plus a separate full JSON backup (machine-format, not
  spreadsheet-friendly, meant for restore not reading) — no single combined
  download. Shape: either one combined CSV with a table-name column/section
  headers, or a ZIP of the existing per-table CSVs bundled behind one button.
  Existing CSV-injection guard (`_csv_safe`) already covers whichever format
  this reuses.
- **Automate the service worker's `CACHE_NAME` bump.** `app/static/sw.js` caches
  `/static/*` cache-first, keyed by a hardcoded version string that has to be bumped by
  hand on any deploy touching `/static/`. That discipline already failed once
  (2026-08-03 — several CSS/manifest deploys shipped in a row without a bump, so
  returning devices served a stale mix of old and new `app.css`, confirmed on a real
  device before being caught). A hash of the static files' mtimes/content, computed at
  deploy or app-startup time, would make this correct by construction instead of
  relying on remembering. Low urgency (now bumped, and it self-heals once any future
  deploy does remember to bump it) but a real recurring risk otherwise.
- **Free-text `committed_purchases.category`** — fast-follow to the 2026-07-29
  free-text-types work below. Still has a DB-level `CHECK (category IN (...))`
  constraint, the same design gap `debts.type`/`accounts.type` had before that
  fix (adding/renaming a category currently requires a migration). Same fix,
  same shape: drop the CHECK via a table-rebuild migration, add
  `list_distinct_categories(conn, user_id)` to the repo (mirroring
  `obligations.py`'s original version and the new
  `debts.py`/`accounts.py::list_distinct_types`), swap the `<select>` for an
  `<input list="...">` + `<datalist>`. Deliberately kept out of the
  `debts`/`accounts` migration to limit that one's blast radius on a live
  production DB — do this once there's a concrete reason (a real complaint
  about `('career_tool','school','car','debt','hobby','food','gambling',
  'other')` being too narrow, same as happened with debt types). The
  `spending_leaks.category` half of this idea no longer applies —
  `spending_leaks` was cut 2026-09-02 (see above).
- **Rate limit on `/settings/totp/confirm`** — found during a 2026-07-29
  security audit of the TOTP feature. Currently only the `/login` route has
  a dedicated attempt limiter; the confirm-code step during enrollment has
  none. Low real risk today (requires an authenticated session, and a
  code's ~90s validity window makes brute force impractical over HTTP) but
  inconsistent with the rest of the app's rate-limiting posture.
- **"Incomplete debt" indicator** — found during the same audit. A debt
  created inline during bank-sync mapping redirects to its edit form with a
  one-time banner nudging the user to fill in minimum payment/due date, but
  if they navigate away without doing so, the debt sits permanently
  incomplete (its minimum silently never counts toward reserved cash) with
  zero ongoing indicator anywhere — the existing staleness badge only
  checks `updated_at` age, which looks fresh right after creation. Would
  need a real "missing required field" concept, not just a one-time nudge.
- **SimpleFIN account-balance sync** — **status 2026-07-21: all 6 v1 phases
  shipped and live, plus a 4-stage hardening pass following real use with a
  real connected bank.** Schema/repo layer, SSRF-hardened SimpleFIN client +
  Fernet encryption, VPS secrets file, full `/bank` UI (connect/list/sync/
  review/apply/disconnect), the mandatory VPS-side `/security-review`, and
  `scripts/bank_sync.py` (cron-friendly, live in the VPS crontab). The
  hardening pass added: debt linking, a configurable sync cooldown, two real
  bugfixes found via live use (duplicate staging rows, a missing-`id` crash),
  `errlist`/`balance_date` surfacing, a stale-connection badge, inline
  create-account/create-debt during mapping, and — closing v1's explicitly
  deferred item — **transaction import + payment matching**: SimpleFIN's
  per-account transactions are now staged and can be matched against an open
  obligation/committed purchase (marks it paid, writes a `transactions`
  ledger row, never touches account balance — see `IMPLEMENTATION_HISTORY.md`'s stage 4
  entry for the double-counting risk this specifically avoids). Full phased
  build order and the hardening pass detail live in `IMPLEMENTATION_HISTORY.md`; this entry
  is the backlog-level summary/history, not the implementation spec.
  Originated 2026-07-13, following a full backlog reassessment prompted by
  the user saying "I still don't find myself just wanting to do this"
  despite three feature rounds shipped in ~36 hours. All
  three rounds optimized actions taken *after* real data is already in the
  app; none touched the actual first bottleneck — getting real account
  balances/bills/debts entered in the first place, which still means the
  exact manual typing the user says they avoid budgeting apps for.

  Research finding: [Firefly III](https://www.firefly-iii.org/) and
  [Actual Budget](https://actualbudget.org/) are mature FOSS self-hosted
  finance apps that already do bank-sync + auto-categorization + net-worth
  tracking better than a bespoke rebuild here ever would — both integrate
  with [SimpleFIN Bridge](https://www.simplefin.org/) (~$15/year, read-only,
  no screen-scraping, no third-party credential storage — a privacy-
  respecting middleman built specifically for hobbyist self-hosted apps like
  this one, unlike Plaid which is closed-source, paid past a limited tier,
  and a much bigger vendor-lock/trust commitment). Trying to out-build those
  two on categorization/budgeting UX is a losing battle. But neither of them
  computes what this app computes — the safe-to-spend/reserved-cash-window
  model. That's the real, un-replicated differentiator, confirmed by the
  user as the reason to keep building this app rather than switching to one
  of them wholesale (see "Considered and declined" below).

  **Scope, deliberately narrow**: auto-populate `accounts.balance_cents` via
  SimpleFIN, NOT a QuickBooks-style transaction-categorization system —
  keeps `safe_to_spend`/reserved-cash as this app's own engine, uses
  SimpleFIN purely as a data-acquisition layer for the one number that's
  most tedious to keep current by hand. Whether to also pull SimpleFIN's
  ~90-day transaction history into the (already-existing, already-partially-
  wired) `transactions` table is a real follow-on question, not in scope for
  a first version.

  **Confirmed 2026-07-13 by reading the protocol directly**
  (simplefin.org/protocol.html) and hitting SimpleFIN's own public demo
  endpoint (`demo:demo@beta-bridge.simplefin.org` — safe to test against,
  fake data, see `DEVELOPMENT.md`-style dev notes): the protocol has **no
  account-type field at all**. Every linked account (checking, savings,
  credit card) comes back in the same generic shape (`balance`,
  `transactions`, `holdings`, `extra`) — SimpleFIN cannot tell this app
  whether a linked account belongs in `accounts` (checking/savings/cash) or
  `debts` (credit cards/loans). Real consequences for scoping: (a) no
  auto-routing — whoever builds this needs a one-time manual classification
  step per linked account; (b) even a linked credit card only ever syncs
  `balance_cents` — SimpleFIN has no APR/minimum-payment/due-date fields, so
  `debts.apr_bps`/`minimum_payment_cents`/`next_due_date` still need manual
  entry regardless; (c) the protocol doesn't specify a sign convention for a
  credit card's `balance` (negative-for-owed vs. positive) — that must be
  verified against a real linked card before writing any mapping code, or
  `total_debt`/`safe_to_spend` could silently corrupt.

  **Open questions a dedicated future scoping/threat-model session must
  answer before any implementation plan** (per the standing "risky
  integrations" principle — lead with complications/mitigations, not "which
  API works"): where/how the SimpleFIN access token gets stored (encrypted
  at rest — this app has never held a live external credential before, a
  materially bigger blast radius than a session cookie); the manual
  account-type-classification step above; the unconfirmed credit-card sign
  convention above; sync cadence (SimpleFIN itself refreshes ~daily, fits the
  existing `scripts/snapshot.py`/`scripts/backup.py` cron pattern); what
  happens to the manual balance quick action once a synced account exists
  (disabled per-account, or just becomes an override that gets overwritten
  on next sync?); and an explicit disconnect/revoke path.

  **Expectation-setting, added 2026-07-14 after building Phase 1 and talking
  through the actual user flow: "least input" is real for the *ongoing*
  cost, not the *initial* one.** There is no in-app bank-linking interface,
  and there deliberately shouldn't be one — this app never collects bank
  credentials directly (that's the entire point of using SimpleFIN as an
  intermediary rather than building bespoke per-bank integrations). A user
  still has to, once, entirely outside this app: create a SimpleFIN account,
  link each bank through Bridge's own hosted flow, and generate a Setup
  Token to paste in here. Firefly III and Actual Budget work identically —
  this isn't a gap a better implementation would close, it's the standard
  shape of this category of integration. What this feature actually
  eliminates is the *recurring* manual balance-typing, not the one-time
  per-bank setup step. Keep this distinction explicit in any UI copy for
  `/bank/connect` so it doesn't read like a zero-setup promise it can't
  keep.

- **Spending narrative on Today** — a plain-language "here's what happened this week"
  summary (2-3 bullets) instead of raw ledger rows, built from `transactions` history
  (`spending_leaks` cut 2026-09-02 — this would read `target_type='spending'` rows
  instead). Discussed alongside the "can I afford this" idea as the other
  candidate for escaping the CRUD feel; not chosen as the lead candidate but not
  rejected either.
- **Event-style entry via the `transactions` table** — accept "I spent $X on this card,"
  "I paid $X toward this card from checking," "I got paid $X" instead of always asking
  for the new exact final balance. Same underlying gap as the "Payment history /
  ledger" idea (now Promoted, below) — the `transactions` table is what both build
  on — but framed around the input model (event vs. state) rather than just
  history/reporting.
  **Partially shipped 2026-07-12, extended 2026-07-13**: the three "Today" quick
  actions that already represent a real event (mark bill paid, record a purchase
  payment, mark income received) now silently log a `transactions` row *and* move
  money in an account automatically (debit for bill/payment, credit for income) —
  see the "Promoted" entry below. Still not built: a standalone "log an event" entry
  form for spending/transfers that aren't already backed by an obligation/purchase/
  income row, and the balance-update quick action still isn't logged (no
  signed-direction column in the schema to represent a state-overwrite delta
  honestly — would need a migration).
- **Freshness/staleness indicators** — last-updated timestamps on account/debt balances,
  a stale badge on inputs that haven't been touched recently, and a note on the
  dashboard when safe-to-spend is computed from stale data. Aimed at distinguishing
  "mathematically correct" from "trustworthy today" rather than presenting every number
  with the same confidence. **Scoped and shipped 2026-09-02** as `PLAN.md` §1.1, per
  the UI audit's A1 finding (`design/UI_AUDIT.md`) — see there, not here.
- **Coarse/low-friction tracking mode for high-churn debts** — an option to track a
  credit card by statement balance + due date + minimum only (optionally an approximate
  current balance), instead of requiring an exact live balance at all times. Aimed at
  cards that move daily, where exact tracking is the highest-upkeep part of the app.
- **Opinionated default entry shortcuts** — a quick-add recurring-paycheck template,
  "same amount as last time" for a bill, purchase presets/recent categories, and
  debt-update shortcuts from the debt list itself, to reduce how often the user starts
  from a blank generic form. **Partially addressed 2026-07-12**: the balance and
  payment quick-action inputs on "Today" now prefill from the value the app already
  knows (current balance, remaining payoff amount) instead of a blank field — covers
  the common "nothing changed but a small delta" case. True "last typed value" memory
  (distinct from the stored/expected amount) is still not built.

- **Configurable default account** — quick actions that move money currently pick
  the implicit default account by reusing the existing `display_order`/`name` sort
  (first active account), not a dedicated setting. Reordering accounts is the
  current lever to change which one that is. Worth a real `default_account_id`
  setting only if the implicit pick turns out wrong in practice for a
  multi-account user — no evidence of that yet, so deliberately not built
  preemptively.

- **Net-cash-view shortfall framing** — from an outside user's wishlist
  (2026-07-13, voice-message transcript): they described Rocket Money's one
  feature they actually use as "sums my bills and tells me how much I need to
  add to my bank account to cover them." This app already computes exactly
  that (`safe_to_spend`/`reserved_cash` in `calc.py`) — no new build needed,
  it's validation the core model is right. Small possible follow-up: when
  `safe_to_spend` goes negative, phrase the dashboard note as "you need $X
  more to cover what's reserved" rather than just showing a negative dollar
  figure, closer to how he described wanting it framed.
- **QuickBooks/bank-sync auto-categorization** — **deprioritized 2026-07-13**
  in favor of the narrower "SimpleFIN account-balance sync" entry above,
  during a full backlog reassessment. SimpleFIN accomplishes the "least
  input" goal with dramatically less effort/risk than this: no OAuth into
  QuickBooks, no bespoke categorization-rule engine to build and maintain
  (Firefly III's rules engine already does that better if deep
  categorization is ever genuinely wanted — see the SimpleFIN entry's
  research). Keeping the risk-framing work below rather than deleting it, in
  case QuickBooks-specific auto-categorization is ever revisited on its own
  merits — but it's a distant second choice, not a live plan right now.
  Original framing: from the same 2026-07-13 feedback: pull transaction
  vendor/description/category data from QuickBooks
  (or a bank/credit-card feed) to build auto-categorization rules, so routine
  spending gets categorized with near-zero manual entry. This reverses the
  former "no bank sync/Plaid" Non-Goal — see `ARCHITECTURE.md`'s Explicit Non-Goals
  for the reversal note.

  **Explicitly not "wire up whichever API gets this working."** The user was
  clear: the move isn't "if I can figure out how to get a Plaid-type autofill
  system to work, add it" — it's understanding what complications an external
  financial-data connector actually introduces to *this* app, how those get
  mitigated, and doing it in a FOSS/trustworthy way. This app's whole security
  posture today (Argon2, revocable server-side sessions, CSRF, no third-party
  data flows at all — everything lives in a self-hosted SQLite file the user
  controls) doesn't have an equivalent for "a live OAuth token that grants
  read access to someone's actual bank/QuickBooks data." That's a materially
  bigger blast radius than a leaked session cookie, and needs its own
  threat-model pass (encryption at rest for tokens, scoped read-only access,
  an explicit disconnect/revoke path, probably a dedicated security review
  before shipping — same rigor as the 2026-07-09 codex security/ops review)
  before any of "which API" gets decided.

  Also worth weighing before defaulting to Plaid specifically: Plaid is
  closed-source, third-party-hosted, and not free past a limited tier —
  a recurring-cost, single-vendor dependency that cuts against this app's
  self-hosted/low-maintenance identity. FOSS-friendlier alternatives exist in
  the personal-finance-app space (e.g. SimpleFIN's user-controlled,
  single-token model, popular with FOSS budgeting tools like Actual Budget)
  and QuickBooks's own OAuth API is arguably a smaller trust leap than adding
  a brand-new third-party aggregator, since the user already trusts and uses
  QuickBooks. Also flagged by the speaker himself: deep auto-categorization
  risks just duplicating QuickBooks ("reinventing the wheel"), and it's
  unconfirmed whether the QuickBooks API even exposes transaction/category
  data usefully. This is the single biggest, least-defined item in the
  backlog — needs its own dedicated scoping/threat-modeling session before
  any code, not a same-session bolt-on, and the deliverable of that session
  should be a risk/mitigation writeup before an implementation plan.
- **AI integration** — same feedback, but the speaker himself framed it as
  "dope factor" more than actual usefulness, and this specifically was NOT
  part of what got reversed (see below) — `ARCHITECTURE.md`'s "no AI advice" Non-Goal
  still stands. Logged here as parked/aspirational only.

## Considered and declined

- **Switching to Firefly III / Actual Budget wholesale** — weighed seriously
  during the 2026-07-13 backlog reassessment (see the SimpleFIN entry above
  for the research behind this). Both are mature FOSS tools that already do
  bank-sync + categorization + net-worth tracking better than this app ever
  will. Declined because neither replicates this app's actual differentiator
  — the safe-to-spend/reserved-cash-window model (what's already spoken for
  vs. what's truly free right now, future income never counted). The user
  chose to keep building this app's own model, using SimpleFIN narrowly as a
  data-acquisition layer rather than replacing the app itself.
- **Pausing all further building to force a real week-long usage test
  first** — also weighed 2026-07-13, given three feature rounds shipped in
  ~36 hours without any real living-with-it period in between. Legitimate
  concern, not acted on this round — logged here so a future session has the
  context rather than re-raising it from scratch. Worth revisiting if
  SimpleFIN sync ships and adoption still doesn't change.

## Rejected / parked

- **Shared/household view, linked accounts** — explicitly out of scope when multiuser
  registration was added; each user's data stays fully isolated unless requested later.
  See `ARCHITECTURE.md`'s Non-Goals.
- A `codex` product review (2026-07-09, see the "codex.md" workflow) independently
  re-confirmed household/shared budgets and public SaaS/growth framing as the wrong
  direction for this app — no new reasoning, just corroboration from a second pass.
  (That same review also flagged bank-sync-as-default and alert-heavy workflows, but
  both of those were explicitly reversed 2026-07-13 — see "Under consideration" above
  and `ARCHITECTURE.md`'s Non-Goals note. "No AI advice" was not reversed.)

## Promoted

- **`shelby`'s daily digest email 403ing** — fixed 2026-08-30. Turned out
  already unblocked: a separate app on the same VPS (`square-report`) had a
  Resend domain (`reports.flyboybyte.com`) already verified under the same
  Resend account as budget's API key. Added
  `BUDGET_DIGEST_FROM_EMAIL=budget@reports.flyboybyte.com` to
  `~/.config/budget/secrets.env`; confirmed via a manual `scripts.digest`
  run: `shelby: sent` (was `error 403`).
- **Bank-sync auto-apply** — shipped 2026-08-30 (commit `9b9deaf`). Opt-in
  `bank_sync_auto_apply` setting (default off) plus a
  `bank_sync_auto_apply_max_change_cents` threshold (default $250): a
  freshly-synced balance within the threshold of the currently-stored one
  applies immediately instead of waiting on manual review at
  `/bank/{id}/review`. Built because the manual-only review step was found
  the same day to silently stall `safe_to_spend` for days — sync succeeded
  daily but nothing ever prompted the user to actually go apply it. A jump
  bigger than the threshold still falls through to manual review regardless
  of the setting.
- **Payment history / ledger** — **fully shipped, status corrected 2026-09-03
  (moved from "Under consideration," where it was mechanically stale).**
  `app/services/payments.py` and `app/services/bills.py` write to the
  `transactions` table for the three "Today" quick actions (mark bill paid, mark
  income received, record a purchase payment); bank-fed transactions can also be
  matched against an obligation/committed purchase, writing the same ledger row
  with the real synced account attached. The one gap this entry used to flag —
  "nothing reads it back" — closed 2026-08-26 as fix #3 of the "feels dead"
  diagnosis below: `app/routers/ledger.py` + `/ledger`, plus the `/activity` hub
  rendering recent entries inline.
- **The four fixes from the 2026-08-26 "feels dead" diagnosis** — all shipped
  2026-08-26. In the order they were ranked:
  1. *Recurring-bill detection from bank history.* `app/services/bill_detection.py`
     (pure, no DB/network) groups outflows by a normalized merchant key and proposes
     a bill only when the amount spread is ≤25% and the median gap maps to a real
     recurrence rule. `/bank/suggestions` lists candidates with a one-tap "Add as
     bill"; its "Scan 120 days" button re-reads history from SimpleFIN and detects in
     memory *without* staging anything, so it can't flood the match queue.
     `fetch_accounts` gained a `start_date` passthrough for that.
  2. *"Just spending" as a third option on an unmatched bank transaction.* Needed a
     migration (`0010`): `target_type='other'` was already the **inflow** type for
     income, so reusing it would have inverted the sign of every purchase in the
     change narrative. Added `target_type='spending'` plus a free-text `category`.
     Unlike a commitment match this deliberately does not debit the account — there's
     no reserved_cash drop to compensate for and the posted amount is already inside
     the synced balance.
  3. *The activity feed.* `app/routers/ledger.py` + `/ledger`, and the `/activity` hub
     now renders recent entries inline instead of only linking onward. The ledger had
     been written to since day one with no route ever reading it back.
  4. *Digest leads with change.* Restructured to WHAT HAPPENED → COMING UP → WHERE YOU
     STAND, with the day's movement in the subject line, and an explicit "nothing
     recorded" rather than silently restating a static figure.

  Verified in a real browser end-to-end, not just TestClient: a detected bill accepted
  with one tap moved safe-to-spend by its amount the moment it landed inside the
  reserved window. 632 tests green.

- **Full PWA support (installable, static-asset service worker, Android/
  desktop push notifications)** — scoped 2026-07-31, shipped 2026-08-01,
  see `IMPLEMENTATION_HISTORY.md` for the full writeup. Installability (manifest + icons)
  + a service worker that caches static assets only (explicitly NOT
  rendered HTML/offline financial data — a stale-but-confidently-shown
  safe-to-spend number would violate the app's core "live cash only"
  invariant) + standard Web Push (VAPID + `pywebpush`, new
  `push_subscriptions` table) with two triggers live at launch:
  bank-sync-cron-failure alerts and new-staged-transactions-ready alerts.
  Bill-due and low-safe-to-spend-threshold triggers deliberately deferred
  (need dedup/hysteresis design work) — see `IMPLEMENTATION_HISTORY.md`'s "Later / not
  yet scheduled" section if picking this back up.

- **"Can I afford this?" instant-answer box on Today** — shipped 2026-07-29.
  `POST /today/afford` (`app/routers/quick_actions.py`) reuses
  `whatif.run_whatif` unchanged with a single hypothetical `status='ordered'`
  purchase, answers yes/no + remaining-or-shortfall amount. Read-only, no
  `db.commit()`, no OOB dashboard refresh (nothing mutated).
- **"Why did safe-to-spend move?" explanation layer** — shipped 2026-07-29.
  `app/services/narrative.py::build_change_narrative` diffs today's live
  `safe_to_spend` against yesterday's snapshot (`snapshots_repo
  .get_snapshot_by_date`), attributes the delta to the top 1-2 `transactions`
  rows since then (direction inferred from `target_type` — obligation/
  committed_purchase are outflows, `other` is income, the only three values
  any writer produces). Shown on the dashboard summary and reused verbatim in
  the daily digest email below. Returns `None` with no prior snapshot rather
  than a misleading guess.
- **Daily digest, email instead of push** — shipped 2026-07-29. Delivered the
  underlying goal (a glance is enough, no need to open the app) via email
  rather than Web Push — this app had zero push infrastructure at the time
  (no service worker/PWA manifest yet) and building one just for this was
  judged not worth it next to a simpler HTTP-API email send.
  `app/services/digest.py::send_digest` posts to Resend's HTTP API (via the
  already-present `httpx` dependency, no new package) with safe-to-spend,
  cash-on-hand/debt, the reserved-cash breakdown, next due payment, and the
  change-narrative sentence above. Opt-in only via a `digest_email` field on
  `/settings` (stored in the existing generic `settings` table, no schema
  migration) — blank means opted out. `scripts/digest.py` mirrors
  `scripts/snapshot.py`'s cron-script shape and **is wired into the VPS
  crontab** — confirmed actually running by the `shelby`'s-digest-403 incident
  above, which could only have surfaced from a real scheduled send, not a
  manual test. "Remaining budget per category" from the original ask was
  dropped — no per-category budget concept exists in the schema, and the
  digest sticks to numbers the app already computes rather than inventing a
  new stored concept for this.
- **Broader `debts.type`/`accounts.type` choices, storage made free-text
  underneath, UI stayed a dropdown** — shipped 2026-07-29, migration
  `0007_free_text_types.sql`. Started as a request for "more vague/
  ambiguous" debt-type options (a bigger hardcoded list); got redesigned
  mid-session into fully free-text `<input>` + `<datalist>` fields (copying
  `obligations.category`'s existing pattern) — but the user tried it and
  said plainly they preferred the dropdown and just wanted better/broader
  choices in it, not to type a category by hand. **Reverted the UI back to
  `<select>`** in `app/templates/debts/form.html`/`accounts/form.html`,
  keeping everything underneath unchanged: no DB-level `CHECK` (migration
  `0007` stays exactly as shipped — personal-specific values like
  `tool_truck`/`klarna_bnpl` no longer force a schema migration to
  add/rename), `SEED_TYPES` in `app/repositories/debts.py`/`accounts.py`
  is still the actual source of the dropdown's options, merged per-user
  with `list_distinct_types()` so a custom value already in someone's data
  still appears (and is still selected correctly when editing that row).
  Net effect: same "no migration needed for new labels" architecture win as
  originally intended, same broader option list, but the picklist UX the
  user actually wanted. **Lesson**: don't silently upgrade "give me better
  dropdown options" into "get rid of the dropdown," even when a cleaner
  pattern for the latter already exists in the codebase — confirm the UI
  shape itself isn't also changing, not just the underlying data model.
  529 tests passing both before and after the UI revert.
- **TOTP (authenticator-app) login, per-user flexible mode** — shipped 2026-07-29,
  direct user request (not from the backlog above). `users.auth_mode` (`password`/
  `totp`/`both`) plus `totp_enabled`/`totp_secret_encrypted`/`totp_last_used_step`
  (migration `0006_totp.sql`). Password hash is never cleared for any mode — "off"
  only ever means "not checked at login," so switching back to password-only later
  just works. Enrollment (QR via `segno`, code confirm via `pyotp`) and the
  password/TOTP/both selector live on `/settings`; login (`app/routers/auth.py`)
  stays a single-step form with both fields always present, validated server-side
  per the user's mode, same generic failure message regardless of which factor
  failed. Replay protection via `totp_last_used_step` (a captured code can't be
  reused). No backup codes — recovery is CLI-only (`scripts/reset_totp.py`, mirrors
  `scripts/reset_password.py`) or admin-gated
  (`/settings/users/{id}/reset-totp`, mirrors the existing reset-password route),
  consistent with this app's existing minimal-recovery philosophy. Separate Fernet
  key (`TOTP_ENCRYPTION_KEY`) from the bank-sync one, to keep blast radii apart.

- **Dashboard "cash on hand vs. debt" feel** — shipped 2026-07-16. Two
  changes to `app/templates/partials/_dashboard_summary.html`: (1)
  shortfall framing on the hero note when `safe_to_spend_cents` is
  negative ("You need $X more to cover what's reserved" instead of just a
  negative number), reusing the existing `.text-bad` class; (2) cash-on-hand
  and total-debt pulled out of the equal-weight `.stat-grid` into their own
  `.compare-grid` card (new CSS, first side-by-side comparison layout in the
  codebase) placed right under the hero stat, deliberately neutral-colored
  on both sides since debt isn't inherently "bad" in this app's framing.
  Verified live: seeded an over-committed scenario ($200 cash, $1,500 rent
  due) and confirmed the shortfall note renders correctly ("You need
  $1,300.00 more..."). No test changes needed — confirmed pre-ship that
  nothing asserts on this markup. Independent of the SimpleFIN track;
  neither blocked the other.
- **Auto-move account balances + debt payoff projection** — shipped 2026-07-13.
  Directly requested after the user said "more like math and automatic autofill."
  Marking a bill paid, recording a purchase payment, or marking income received now
  automatically debits/credits the implicit default account (first active account
  by `display_order`/`name`) instead of requiring a separate manual "update a
  balance" step — verified against a real server to be exactly safe-to-spend-neutral
  for bill/purchase payments and a real correctness fix for income received (which
  previously updated nothing about `cash_on_hand`, only the income event's own
  `is_received` flag). Also added `calc.py::debt_payoff_projection` — a pure,
  read-time-only computed "~14 mo / $340 interest" estimate shown on the debts list
  and dashboard debt-priority section, derived from each debt's existing
  balance/APR/minimum fields, not a new stored value. **Caveat noted for future
  sessions**: manually re-updating a balance for an event already covered by one of
  these three quick actions will now double-count it — "update a balance" is for
  reconciliation/drift correction only, not routine event logging, going forward.
- **Dashboard quick action for "income received"** — shipped 2026-07-12:
  `POST /today/income-received/{id}` wraps the already-existing
  `app/services/bills.py::mark_income_received`, following the same per-row-button
  pattern as "mark a bill paid." Directly asked-for after the user identified entry
  friction (not missing features, not trust, not habit) as the real blocker to daily
  use.
- **Admin-gated multiuser registration** (`POST /settings/users`) — shipped
  2026-07-09, commit `630ed57`.
- **"Email admin to add an account" login-page form** — shipped 2026-07-09, commit
  `6d59abc` (originally via EmailJS's client-side API). **Rebuilt 2026-09-06**: a
  live domain-restriction test found the EmailJS integration wasn't actually
  protected against being called from anywhere — replaced with a real backend
  route (`POST /login/request-access`) using the Resend integration `digest.py`
  already had running. See `CLAUDE.md`.
- **"Dismiss all" bulk action on `/bank/transactions`** — shipped 2026-09-05.
  Checkbox per row via the HTML5 `form=` attribute (no nested `<form>`s needed,
  each row already has its own match/spending forms) + a "Dismiss selected"
  button. `POST /bank/transactions/dismiss-selected` loops over the submitted ids
  the same way `apply_sync` already does for balance staging.
- **Reconciliation / "catch-up" review workflow + dashboard mini balance-update
  widget** — shipped 2026-09-06, merged into one item per the user's own framing
  that they overlap ("make the existing function better, apply codex review").
  Rather than a separate new screen: `/today/balance` now covers debts too
  (previously accounts-only) and gained an "adjust by amount" mode — pick +/- and
  an amount, the server does the arithmetic against the current balance instead of
  you computing the new total (the actual "calculator feel" ask), same sign
  convention for both accounts and debts. The dashboard's stale-balance alert now
  lists each stale account/debt with its own inline one-field quick-update form,
  so clearing everything stale happens in one pass on the dashboard itself. Bills
  due / income to mark received / purchase payments were already all listed in the
  existing "Update today" grid — the only genuine gaps were debt balance updates
  and the "type the new absolute number" friction, both closed here.
- **"Today" quick-action cockpit** (balance/payment/purchase/leak/mark-bill-paid, HTMX
  OOB summary refresh) — shipped, see `ARCHITECTURE.md`'s "Home is a cockpit, not just a
  summary" section.
