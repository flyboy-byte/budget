# Plan

Status: **In progress** — engineering backlog (auto-apply, shelby's digest) shipped
2026-08-30. A full UI/design audit landed 2026-09-02 (`design/`) and was folded in as
the primary next workstream. §1.1 (freshness tier), §1.9 (cut `spending_leaks`), §1.2
(dark-only token pass), §1.3 (hero + composition bar), §1.4 (two-column layout)
shipped 2026-09-02, and §1.5 (human dates), §1.6 (semantic colour + debt table), §1.7
(first-run state), §1.8 (bill_reminders.py copy), §1.10 (class-name cleanup) shipped
2026-09-03 — tests green. **§1 is entirely done, including the closing "Verification
(all of §1)" pass — all 16 checks (pytest, 4-state seed, quick-actions, export-restore,
real PWA install via Chromium `--app=` mode, cache-eviction, keyboard/a11y) passed
2026-09-03.** Deployed to production the same day (commit `4aa8b55`), confirmed live
at `budget.flyboybyte.com`. `origin` is set to the HTTPS remote (this network
intermittently blocks outbound SSH/22 — HTTPS via `gh` always works regardless).
`playwright` is now installed in `.venv` (dev-only) for future browser verification.
**§7 (VPS security audit) and §8 (README rewrite + public-facing risk pass) also
shipped 2026-09-03** — no vulnerabilities found, one low-risk pre-existing gap
re-confirmed and tracked, README rewritten to match house style with a real
screenshot, one cheap follow-up (EmailJS domain-restriction test) folded into §5.
§2 (`IDEAS.md` triage) also done 2026-09-03. **§3 is entirely done as of
2026-09-06**: free-text purchase categories, TOTP rate limit,
incomplete-debt indicator, combined CSV export, bulk transaction dismiss,
coarse debt tracking (per-debt flag), and the reconciliation/catch-up
screen (merged with the calculator-style balance-widget idea per the
user's own framing — `/today/balance` now covers debts with an
adjust-by-amount mode, and the stale-balance alert got inline
per-row quick-update forms, instead of a separate new page) — plus
`shelby`'s digest 403 already done 2026-08-30.
**2026-09-06: also closed every remaining "partial" item elsewhere on the
plan** — §0's digest spot-check confirmed `safe_to_spend` genuinely moves
day-to-day now; §5's EmailJS domain-restriction test found a real live
gap (not enforced) and it's fixed at the root (client-side EmailJS
replaced entirely by a backend route + the existing Resend integration,
see `CLAUDE.md`); §1.1's sparkline dashing and §1.3's negative-crossing
narrative (both originally flagged "deliberately not done") are both now
shipped and verified against a real `uvicorn`. **§4 (docs catch-up) also
done 2026-09-06** — `IMPLEMENTATION_HISTORY.md` caught up from 2026-08-03
through tonight. Only §5 (public-readiness execution, minus the now-done
EmailJS item) remains, and every remaining item in it needs the user's
own decision (license, deployment-doc generality, commit-author email,
and the visibility flip itself) — nothing left to build blind.

Living state document — current reality, not a wishlist. `IDEAS.md` stays the
open-ended backlog intake; this file is the ordered, scoped work queue pulled
from it. Update statuses in place as items land; strike through when done with
a one-line note on what shipped, don't delete the line.

## Decisions already made (do not re-ask)

- Bank-sync auto-apply is opt-in (`bank_sync_auto_apply` setting, default off)
  and threshold-gated (`bank_sync_auto_apply_max_change_cents`, default
  $250) — a jump bigger than the threshold still falls back to manual
  review. User explicitly chose this over "always auto-apply" specifically
  to keep a bad SimpleFIN read from silently overwriting a real balance.
- **Dark mode only** (2026-09-02, `design/VISUAL_SPEC.md`). The light branch and
  `prefers-color-scheme` come out of `app.css` entirely — the user has only ever
  used this app in dark mode. Keep the CSS token *names*, replace the values.
- **All seven quick actions stay open** on Today, in one uniform grid — not
  collapsed, not behind a picker (2026-09-02).
- **`spending_leaks` gets cut** — asked directly, user confirmed 0 rows ever used it
  (2026-09-02). Superseded by `target_type='spending'` free-text categories on the
  ledger. See §1.9. This is `design/COMPACTION.md` C1 — the only compaction proposal
  with a confirmed yes so far; C2/C3/C4 in that same file are NOT approved, don't
  start them without a direct ask.
- **Notifications say what changed, not what's due** (2026-09-02) — `digest.py`/
  `narrative.py` already do this right; `bill_reminders.py` is the gap (§1.8).
- **Nothing in the UI workstream touches `calc.py`.** Every item is presentation,
  copy, or information architecture. If a change would move `safe_to_spend`, it's
  out of scope for §1 and wrong.
- **Future income is never counted in the primary/default number.** Non-negotiable
  per `CLAUDE.md`/`ARCHITECTURE.md`; the forecast block stays visually subordinate
  and explicitly labelled under the UI rework too.
- `spending_leaks` is a cut candidate — **now actually scheduled** (§1.9), not just
  flagged. Table dropped by migration only after an export; don't drop it as a bare
  `DROP TABLE`.
- Public-readiness checklist (`MAKING_PUBLIC.md`) already re-verified
  2026-08-30 against current repo state — no secrets/`.env`/`.db` ever
  committed, no raw IP in tracked files, still true. Don't re-audit git
  history from scratch next session; trust that check unless files were
  added since.
- The actual `gh repo edit --visibility public` flip is never run by
  Claude — that decision point stays explicitly with the user regardless
  of how ready the checklist looks.
- **Coarse tracking mode for high-churn debts is a per-debt flag, not a global
  setting** (2026-09-05) — different debts churn at different rates (a credit card
  vs. a fixed personal loan), a global switch would force one behavior on all of
  them. Unblocks §3's item, but the exact behavior change (what "skip requiring an
  exact live balance" actually does — form field, staleness-check exemption, both)
  still needs its own scoping pass before implementation.

---

## ~~0. Bank-sync auto-apply + shelby's digest~~ — DONE (2026-08-30)

`within_auto_apply_threshold()` shipped end-to-end (commit `9b9deaf`), confirmed
live (`bank_sync_auto_apply = 1` in prod). shelby's digest 403 fixed same day via
`BUDGET_DIGEST_FROM_EMAIL=budget@reports.flyboybyte.com`. Full detail in
`IMPLEMENTATION_HISTORY.md` once §4 below catches that file up.

~~**Open follow-up**~~ — DONE (2026-09-06). Confirmed live: `safe_to_spend_cents` in
`snapshots` genuinely moves day-to-day now (`70085` → `128412` → `65803` → `68102` →
`96552` across the two weeks up to 2026-09-06), not frozen like the 2026-08-26
diagnosis found before auto-apply shipped.

## 1. UI/design overhaul — `design/` audit (2026-09-02)

Read-only audit of `app.css`, `templates/`, `services/`; nine findings ranked
Trust > Clarity > Craft in `design/UI_AUDIT.md`. Normative specs: `design/VISUAL_SPEC.md`
(tokens/type/space/layout), `design/STATE_SPEC.md` (Today's four states), `design/VOICE.md`
(copy rules). `design/mock/today-all-states.html` is illustrative only — where it
disagrees with a spec, the spec wins. Read a finding in `UI_AUDIT.md` before
implementing its fix; several have a non-obvious "why not the obvious thing" note.

**Four hard constraints for the whole workstream** (from `design/README.md`):
1. Nothing here changes a number — no item touches `calc.py`.
2. Future income stays out of the primary number.
3. **Bump `CACHE_NAME` in `app/static/sw.js`** on every deploy in this workstream —
   all of it touches `/static/`; this discipline already failed once (2026-08-03,
   stale mixed CSS on a real device).
4. Verify against a real `uvicorn`, not `TestClient` — `display-mode: standalone`
   CSS and client-side JS have both shipped broken past green tests before.

Ordered queue — do in this order, each item independently shippable/deployable:

### ~~1.1 Freshness tier on the headline number~~ — DONE (2026-09-02)

Shipped: `_balance_freshness()` in `app/routers/dashboard.py` (age from each active
account's/debt's `updated_at`, mirrors `hubs.py`'s `_is_stale_row` but reports ages,
not just a bool), wired into `build_dashboard_context` so both the full page and every
`/today/*` OOB refresh pick it up. `stale_balance_threshold_days` settings key added
(default 7). Stale render: number muted (`hero-stat__value--stale`), badge becomes
`Stale` (`badge--warning`, reused rather than inventing a new color before the dark
pass), covered-until suppressed (not computed), an `alert--info` callout names the
specific stale accounts and ages with "Update balances"/"Sync from bank" links, and the
Position rail (compare-grid + stat-grid) dims via a new `.dim-stale` class with its own
"as of" caption. Stale-wins-over-negative from `STATE_SPEC` implemented (negative
framing suppressed while stale). Verified against a real `uvicorn` with a backdated
account (Playwright) — badge, muted number, suppressed covered-until, named callout, and
dimmed rail all confirmed rendering correctly; first-run (0 accounts) confirmed to stay
non-stale. `CACHE_NAME` bumped (`v2` → `v3`) since this touched `app.css`.

~~**Sparkline dashing-after-last-real-point**~~ — DONE (2026-09-06). The
distinguishing signal needed was max(`updated_at`) across accounts/debts, already
computed for `is_stale`. `build_sparkline_svg` now splits the stroke there: solid
before, dashed after, hollow marker at the last real point — only when stale, per
`STATE_SPEC.md`. See `app/sparkline.py` / `_balance_freshness`'s
`last_real_update_date`.

### ~~1.2 Dark-only token pass~~ — DONE (2026-09-02)

Shipped: light branch and `prefers-color-scheme` query removed entirely;
`:root` now carries the dark values directly (`color-scheme: dark`). Every
token *name* kept, only values moved, per spec's table (accent `#9184d9`,
bad `#cf7f77`, good `#5fae8c`, hue-rotated at the accent's lightness/chroma).
Radii collapsed to `8px` everywhere. Spacing moved to the 0.7× scale
(`2.8/5.6/8.4/11.2/16.8/22.4/39.2px`). A8's gutter mismatch fixed via
`.hub-grid > .card { margin-bottom: 0 }` rather than editing every card.
Shadows flattened (spec: "elevation is an edge plus ambient darkness, not a
big blur") without removing any shadow usage from templates — that's still
§1.3/§1.4's job for the hero specifically. Buttons switched from filled to
accent-outline per spec's explicit rule ("Primary buttons are an accent
outline on transparent, not a fill") — caught and fixed a real contrast bug
this surfaced: `.btn--danger-solid` inherited the new outline text color and
would have shown invisible-on-itself text; gave it explicit dark text against
its solid fill. Added a global `:focus-visible` ring and `[disabled]` dimming
per the spec's Interaction rules (both were on §1's own verification
checklist). `CACHE_NAME` bumped again (`v3` → `v4`).

**Real environment finding, not a regression**: `app.css`'s existing
`@view-transition { navigation: auto; }` rule (present before this pass, not
added by it) freezes Playwright screenshots in this headless environment —
`page.screenshot()` returns the *previous* page's frame instead of timing out,
so a login→dashboard screenshot silently showed the login page. Worked around
for verification only by stripping that rule via `page.route()` interception
(never touched the shipped file). Worth remembering for any future headless
verification that spans a navigation on this app.

Verified visually against a real `uvicorn` with seeded data: dashboard (all
states including stale), `/debts`, `/money`, and keyboard focus ring all
screenshotted and reviewed — legible contrast throughout, A8 gutters visibly
even, stale-state amber callout and muted number both read correctly against
the new palette. 643 tests still green (CSS has no direct test coverage in
this repo; verification was screenshot-based).

Files: `app/static/css/app.css`, `app/static/sw.js`.

### ~~1.3 Hero + composition bar~~ — DONE (2026-09-02)

Shipped: one stacked bar (bills / debt minimums / committed purchases / free)
with a color-swatched legend beneath it, replacing the old run-on sentence
and the "Reserved cash" `stat-grid` tile. Percentages computed in
`dashboard.py::_composition_bar` (presentational derivation of numbers
`calc.py` already produces, not new business logic — kept out of `calc.py`
per the workstream's hard constraint). Two shapes per `design/STATE_SPEC.md`:
healthy/stale get the 4-segment bills/debts/purchases/free bar; negative gets
a 2-segment covered/hatched-not-covered bar instead (denominator switches
from cash-on-hand to reserved-cash accordingly, since cash no longer covers
it). Stale wins over negative here too, matching §1.1 — a stale-and-negative
balance still gets the 4-segment shape. Hero card lost its gradient top bar,
tinted background, and `--shadow-lg` entirely; negative state now signals
with only a border-color change, nothing louder. `--accent-gradient` token
removed as dead code. `CACHE_NAME` bumped again (`v4` → `v5`).

**Real bug caught during visual verification, not by tests**: when stale
forces the 4-segment shape on an actually-negative balance, the "Free" label
was showing the raw negative `safe_to_spend_cents` (e.g. "Free -$1,744.00"),
which reads as nonsense. Fixed with a separate clamped `free_display_cents`
(never negative) used only for that label — the real negative number is
still the headline figure and still shown at full size, this only stops one
legend line from contradicting itself. Added a regression test
(`test_composition_bar_stays_four_segment_when_stale_and_negative`) since
the existing suite's positive-only fixtures never would have caught it.

**Still deliberately not done**: `STATE_SPEC.md`'s stale-composition-bar heading
("Where the $X sat on &lt;date&gt;") — reused the existing "as of N days ago"
note instead rather than fabricating a single as-of date across
possibly-differently-aged accounts. Genuinely cosmetic, low value; not revisited.

~~The full negative-state narrative sentence~~ — DONE (2026-09-06). New
`_negative_crossing_narrative()` walks every dated reserved item in due-date order,
subtracting from cash on hand, and names the one whose subtraction first takes the
running total negative — falls back to the existing "You need $X more" sentence
when nothing dated actually crosses zero. Verified against a real `uvicorn`:
"Verizon clears Sat 5 Sep · today and takes you under. Paycheck lands Thu 10 Sep ·
in 5 days." — matches this spec's own example shape exactly.

Verified visually against a real `uvicorn` across all three states (healthy,
negative, stale-and-negative) — bar proportions, legend labels, hatched
pattern, and the border-only negative cue all read correctly. 646 tests
green, including 5 new ones for the composition bar itself.

Files: `templates/partials/_dashboard_summary.html`, `app/routers/dashboard.py`,
`app/static/css/app.css`, `app/static/sw.js`.

### ~~1.4 Two-column layout~~ — DONE (2026-09-02)

Shipped: `.dashboard-grid` (new wrapper in `dashboard.html`, `grid-template-areas:
"main rail" "quick rail"`, 1fr + 360px rail, one column below ~860px via media
query) holds the hero+bar (left), the rail (right: position/coming-up/debt-
priority/forecast), and the quick-action forms (left, below the hero) exactly per
spec. `.page--dashboard` widens the dashboard's own content column to ~1160px
(`72.5rem`) without touching `.page`'s 640px default used by every other screen.

**How the watch item was actually resolved**: `#dashboard-summary` stays the
literal single DOM node the `hx-swap-oob` swap replaces (unchanged — still wraps
both the hero and the rail together, so one swap refreshes both atomically). It
can't *also* be the CSS grid container, though, since a single swapped element can
only occupy one rectangular grid cell, and the hero (top-left) and rail
(top-right, spanning two rows) aren't a rectangle together. Fix: `#dashboard-
summary { display: contents; }` — its two children (`.dashboard-main`,
`.dashboard-rail`) become direct grid items of the *outer* `.dashboard-grid`
instead, so they land in separate columns while the DOM node HTMX swaps against
is untouched. `display: contents` doesn't affect HTMX at all (it manipulates the
DOM tree, not layout), so this is a pure CSS trick with zero swap-semantics risk.
Quick-action forms deliberately stay *outside* `#dashboard-summary` still (own
`.dashboard-quick` grid-area) — pulling them inside would have reset any
mid-typed input in a sibling form every time a different quick action fired,
which is exactly what the OOB-swap boundary exists to prevent.

Verified for real, not just by reasoning about it: fired a live balance-update
quick action against a real `uvicorn` server and confirmed (a) the hero number
updated in place, (b) the rail's debt-priority table still showed the debt, and
(c) text typed into an untouched sibling quick-action form was NOT reset by the
swap. Also strengthened the existing
`test_quick_update_balance_response_includes_oob_summary` test to assert
`.dashboard-main`/`.dashboard-rail` are both present in the OOB fragment and a
seeded debt name appears in it, so this doesn't regress silently again.
Screenshotted both breakpoints (1200px two-column, 480px single-column) — mobile
order is main → rail → quick, matching the pre-redesign reading order rather than
main → quick → rail. 646 tests green. `CACHE_NAME` bumped (`v5` → `v6`).

Files: `templates/dashboard.html`, `templates/partials/_dashboard_summary.html`,
`app/static/css/app.css`, `app/static/sw.js`, `tests/test_quick_actions_routes.py`.

### ~~1.5 Human dates~~ — `UI_AUDIT` A6 — DONE (2026-09-03)

New `app/services/dates.py::human_date(value, today) -> str` — pure function, no
request/DB access — formats `Wed 9 Sep · in 6 days` / `· today` / `· tomorrow` /
`· yesterday` / `· N days ago`. Storage untouched, still plain ISO-8601 `YYYY-MM-DD`
`TEXT`. Two call sites:
- `app/templating.py` registers it as the `human_date` Jinja filter, via a
  `@pass_context`-wrapped `_human_date_filter` that reads `request.state.timezone`
  (falls back to `"UTC"` if unset, e.g. the pre-login page) and computes "today" in
  that zone with `zoneinfo`, then calls the pure function.
- `app/services/digest.py`'s `_body()` calls it directly with the `today` already
  passed into that function — no new timezone plumbing needed there, the digest
  email and UI both go through the same formatting code, they just each supply
  their own "today."

`request.state.timezone` is set once per request in `app/deps.py::get_current_user_id`
(the one dependency almost every authenticated route already goes through) via
`calc.get_setting(db, user_id, "timezone")` — no route-by-route plumbing needed.
`calc.py::DEFAULT_SETTINGS["timezone"]` changed `"UTC"` → `"America/Chicago"` per the
2026-09-03 decision below; `logan`'s and `shelby`'s explicit per-user settings are
untouched either way.

Applied the `human_date` filter to every display-only date found: `debts/_row.html`,
`income_events/_row.html`, `obligations/_row.html`, `snapshots/_row.html`,
`_ledger_rows.html`, `bank/transactions.html`, `bank/suggestions.html`,
`dashboard.html`'s quick-action bill/income lists, and three spots in
`_dashboard_summary.html` (hero "you're okay until," next-paycheck, next-due-payment).
Left raw ISO alone anywhere a date is a form *value* (`<input type="date">`,
hidden fields re-submitted to another route) — those need machine format, not
display format.

Tests: fixed one pre-existing assertion in `test_obligations_routes.py` that checked
for a raw ISO string in rendered HTML (now checks for `id="obligation-{id}"` +
`"15 Aug"` instead, since the exact relative-date wording depends on the test's
run-date). No new dedicated date-formatting tests added — `dates.py::human_date` is a
pure function three lines of logic deep; the existing route tests already exercise it
by rendering real pages. Full suite green (646 passed).

Verified against a real server: seeded a throwaway SQLite DB (`scripts.init_db` +
direct inserts) with a due-today debt, a due-in-6-days obligation, a due-tomorrow
income event, and a debt with a `NULL` next-due-date — confirmed `Fri 4 Sep ·
tomorrow`, `Wed 9 Sep · in 6 days`, `Thu 3 Sep · today`, and an empty cell (no crash)
for the null case, on `/`, `/obligations`, and `/debts`. No `/static/` changes this
item, so no `sw.js` `CACHE_NAME` bump needed.

**Decided 2026-09-03**: the per-user `timezone` setting (`app/routers/settings.py`,
IANA name, validated against `zoneinfo.available_timezones()`) already existed and
already worked — confirmed live in production before this item started: `logan`
already had it explicitly set to `America/Chicago`, `shelby` explicitly had `UTC`.
`kelsey` and `landon` had never touched the field, so they were on the app-wide
fallback — changed from `UTC` to `America/Chicago` ("center of the continent" — the
user's own words) as part of this item.

### ~~1.6 Semantic colour discipline + debt table~~ — `UI_AUDIT` A5, A7 — DONE (2026-09-03)

New `.badge--accent` (CSS, `var(--accent-soft)`/`var(--accent)`) for "Main target" on
the dashboard's debt-priority table — was `badge--risky`, which conflated "the plan"
with "a hazard." APR is now plain text everywhere (both `_dashboard_summary.html`'s
priority table and `debts/_row.html`'s full list), not a red badge — it's a neutral
fact, not a warning. The negative colour (`.text-bad`, already existed) is now used
for exactly the two cases A5 calls out: negative net position (unchanged, already
correct) and `payoff_impossible` from `debt_payoff_projection` (both the dashboard's
priority table and the full `/debts` list page — the latter had the impossible-payoff
text in plain `text-muted` before, no color signal at all).

Debt-priority table (`_dashboard_summary.html`) now has a real `<thead>` (Debt /
Balance / APR) instead of one overloaded right-aligned cell holding balance + APR
badge + payoff note. The payoff projection is now a caption line under the debt name
(`<br>` + `.hero-stat__note`), not crammed into the balance cell.

Tier reason on the top row: new `app/routers/dashboard.py::_debt_priority_reason()`
reads the winning debt's own fields (`priority`, `interest_status`,
`is_flexible_payment`, `apr_bps`) to explain the ranking without duplicating
`calc.py::debt_priority`'s sort — "manually prioritized" / "not accruing interest" /
"flexible payment" / "APR unknown — treated as highest risk" / "highest APR,
accruing". Wired into `build_dashboard_context`'s returned dict as
`debt_priority_reason`, computed once from `debt_priority_rows[0]`.

`debts/list.html` and `debts/_row.html` already had a proper `<thead>` and separate
Interest/Payoff columns from earlier work — no header changes needed there, just the
badge→plain-text APR swap and the payoff-impossible color fix noted above.

Verified against a real server: seeded four debts covering every branch — known-APR
accruing, unknown-APR accruing (sorts first, "APR unknown" reason), not-accruing
(tier B), and a genuinely payoff-impossible one (minimum payment below monthly
interest) — confirmed correct tier ordering, correct reason text on row 1, and
`text-bad` only on the impossible-payoff line, nowhere else. 646/646 tests still
green (no test asserted on the old markup shape). No `/static/` CSS-only changes
beyond one new rule, so bumped `sw.js`'s `CACHE_NAME` anyway per the usual discipline
whenever `app.css` changes.

Files: `app/static/css/app.css`, `app/routers/dashboard.py`,
`app/templates/partials/_dashboard_summary.html`, `app/templates/debts/_row.html`.
`debts/list.html` needed no changes (already had the header this item was chasing).

### ~~1.7 First-run state~~ — `UI_AUDIT` A2 — DONE (2026-09-03)

`app/routers/dashboard.py::build_dashboard_context` computes `is_first_run` from a
single `accounts_repo.list_accounts` call (reused, not duplicated, for the existing
`_balance_freshness` call) and returns it in the context — no new `calc.py` logic, this
is presentational only, matching §1's standing invariant.

`templates/partials/_dashboard_summary.html`: when `is_first_run`, the hero card
collapses to just the label + `$0.00` at `.dim-first-run` (new CSS, `opacity: 0.22`,
distinct from the existing `.dim-stale` at 0.55 — "present, inert" per
`STATE_SPEC.md`'s First run section) — no badge, no composition bar/legend, no
sparkline, no trend/change-narrative note. The rail's entire contents (compare-grid,
net position, next paycheck, next due, debt priority, forecast card) are replaced with
a `<h2>Get set up</h2>` section and an `<ol>` of three steps, each an existing `.card`
with a `.btn` linking straight to the relevant form: 1) Add an account → `/accounts/new`;
2) Add what's already committed → `/obligations/new` + `/debts/new`, with the sentence
plainly stating this is what makes the number differ from a bank balance (`UI_AUDIT`
A2's specific requirement); 3) Set the reserve window → `/settings`, copy recommends
"next paycheck" without changing `DEFAULT_SETTINGS["reserved_window_mode"]` itself
(still `end_of_month` — changing the actual system default was out of scope, this is
onboarding copy only, not a calc-affecting change). Closes with the "about four
minutes" line from `STATE_SPEC.md`.

`templates/dashboard.html`: the "Update today" quick-actions block (7 forms) is
suppressed entirely (`{% if not is_first_run %}`) rather than rendered against an
empty account/purchase/bill list — the existing per-select JS (`prefillFromSelectedOption`)
already null-checks missing elements, so no script error when the block is absent.

No state column added — `is_first_run` is a separate boolean from
`is_stale`/`safe_to_spend_cents < 0`, not folded into `_balance_freshness`'s state
machine, per `UI_AUDIT` A2's explicit "do not add a state column."

`app/static/sw.js` bumped v7→v8 (CSS change). Four existing tests
(`test_dashboard_shows_debt_priority_list`, `test_dashboard_shows_forecast_separately_labeled`,
`test_today_page_shows_income_received_quick_action`, `test_today_page_shows_quick_action_forms`)
had never seeded an account and so were unknowingly exercising what is now the
first-run branch — fixed by seeding a `Checking` account in each, matching what they
were actually meant to test. Added a new dedicated test,
`test_dashboard_shows_first_run_state_with_no_accounts`. 647 passed (was 646).

Verified against a real `uvicorn` server with a fresh zero-account user: first-run view
confirmed (onboarding steps present, quick-actions/forecast/composition-bar absent,
`dim-first-run` class present, `$0.00` shown); then added one account via `POST
/accounts` and confirmed the page flips back to the full cockpit (forecast, "Update
today", cash-on-hand reflecting the new balance) with no first-run markup left over.

Files: `app/routers/dashboard.py`, `app/templates/dashboard.html`,
`app/templates/partials/_dashboard_summary.html`, `app/static/css/app.css`,
`app/static/sw.js`, `tests/test_dashboard_routes.py`, `tests/test_quick_actions_routes.py`.

### ~~1.8 `bill_reminders.py` copy~~ — `VOICE` — DONE (2026-09-03)

`send_due_bill_pushes` still triggers on the same condition (an unpaid obligation due
today/tomorrow, not already pushed today) — only the push's title/body changed. Body
is now `narrative.build_change_narrative(conn, user_id, today)`, the exact same
function `digest.py`'s WHAT HAPPENED section already calls — no new copy logic
duplicated, reused as-is per `VOICE.md`'s "Notifications" section (confirmed
2026-09-02 preference: "notifications should say what changed, not what is due").
Falls back to `"Nothing recorded since yesterday."` when there's no prior day's
snapshot to diff against (narrative returns `None` in that case — e.g. first bill
push ever, before any snapshot history exists). Title changed from
`"Bill due"`/`"N bills due"` to `"Safe to spend"`, matching the dashboard's own
hero-card label and `low_balance_alert.py`'s existing title convention
(`"Safe-to-spend is low"`) rather than inventing a third naming style. `format_cents`
import dropped (no longer used — bill names/amounts are no longer in the copy at
all). Bundling behaviour is unchanged: multiple same-day bills still produce exactly
one push, not one per bill.

Three of the four existing tests in `tests/test_bill_reminders.py` asserted on the
old literal bill-name/amount copy — updated to assert on the new fallback text and
the one-push-per-bundle behaviour instead of bill names. Added
`test_push_body_is_the_change_narrative_not_the_bill`, seeding a prior day's
snapshot so the actual "Down/Up ... since yesterday" narrative sentence (not just
the no-prior-snapshot fallback) is exercised. 648 passed (was 647). No template/CSS
touched, so no `sw.js` bump and no real-server/browser verification needed — plain
unit coverage with the existing fake-webpush transport is sufficient for a
notification-copy-only change.

Files: `app/services/bill_reminders.py`, `tests/test_bill_reminders.py`.

### ~~1.9 Cut `spending_leaks`~~ — DONE (2026-09-02)

Removed end-to-end: `app/routers/spending_leaks.py`, `app/repositories/spending_leaks.py`,
the `templates/spending_leaks/` trio, the dashboard quick-action card, the Activity hub
card + `leak_count`, the nav active-path entry, `export.py`'s `TABLE_COLUMNS`/
`_RESTORE_INSERT_ORDER` entries, and the dedicated test files
(`test_spending_leaks_routes.py`, `test_repositories_spending_leaks.py`), plus
spending_leaks-specific fixtures/assertions in `test_export.py`, `test_hubs_routes.py`,
`test_quick_actions_routes.py`. Table dropped via `migrations/0011_drop_spending_leaks.sql`
(confirmed 0 rows in production per the audit, so no export was actually needed before
dropping — the migration file still documents that check for any environment where it
might not hold). `ARCHITECTURE.md`/`README.md`/`IDEAS.md` updated to stop describing it
as a live feature. 638 tests pass; verified against a real `uvicorn` post-migration
(dashboard/activity/money/more/export all 200, JSON backup confirmed missing the
`spending_leaks` key, no console errors).

### ~~1.10 Class-name cleanup~~ — `UI_AUDIT` A9 — DONE (2026-09-03)

Two misuses, both introduced by earlier §1 items and caught exactly as `UI_AUDIT` A9
predicted ("rename at point of reuse while templates are already open"):

- `hero-stat__note`, used inside the debt-priority `<table>`'s cells (§1.6's
  `debt_priority_reason`/payoff-note spans) — not a hero-stat context at all. New
  `.table-note` class added to `app.css` (identical rule, no `margin-top` since a
  table cell doesn't need the hero card's stacking gap) and swapped in at the three
  spots in `partials/_dashboard_summary.html`. Every other `hero-stat__note` use
  (the hero card's own notes, `bank/list.html`'s connection-status caption paired
  with a `hero-stat__label`) was already legitimate and left untouched.
- `hub-card__title`, used as the heading atop each of `dashboard.html`'s six
  quick-action forms — those sit in a plain `.card`, not a `.hub-card` link tile.
  New `.form-title` class added (identical rule) and swapped in at all six. Every
  `hub-card__title` use inside `templates/hubs/*.html` was already correct (real
  `.hub-card` grid tiles) and left untouched.

`sw.js` bumped v8→v9 (CSS change). 648 passed (unchanged — pure rename, no behavior
change, no test referenced either old class name). Verified against a real `uvicorn`
server: seeded an account + a debt, confirmed `.table-note` renders on the debt-priority
row and `.form-title` renders on all six quick-action cards with the same visual
weight as before.

Files: `app/static/css/app.css`, `app/templates/partials/_dashboard_summary.html`,
`app/templates/dashboard.html`, `app/static/sw.js`.

### ~~Verification (all of §1)~~ — DONE (2026-09-03)

- [x] `pytest` green (648 passed) — reserved-cash/debt-priority tests unmoved.
- [x] Seeded all four states against a real `uvicorn` in one consolidated pass
  (single DB, two users): first run (fresh user, zero accounts) → added an account +
  obligation → healthy → backdated `updated_at` on the same rows → stale (confirmed
  stale wins over negative — 4-segment bar, not 2-segment, even with cash under
  reserved). Negative verified on a second user (low cash, rent due) before
  backdating. All four confirmed via response markup (`badge--safe`/`--risky`/
  `--warning`, `dim-stale`, `composition-bar__segment--covered/uncovered` vs.
  `--bills/--free`).
- [x] Fired all six `/today/*` mutating quick actions (afford-check is read-only, no
  oob expected and none seen) — `balance`, `purchase`, `payment`, `bill-paid`,
  `income-received` each confirmed to return the `dashboard-summary` `hx-swap-oob`
  fragment.
- [x] Export round-trip: exported a user with one row in every table, wiped all of
  it (dashboard correctly regressed to first-run), restored, confirmed every table's
  row count and sample field values match the original export exactly. (First
  restore attempt correctly no-opped with an inline error — omitted the required
  `confirm` field — confirming the safety gate itself works before retrying with it
  set.)
- [x] Install the PWA: standalone bottom nav, `env(safe-area-inset-*)` padding,
  clean collapse to one column. `playwright` installed into `.venv` (dev-only, not
  added to `requirements.txt` — a one-off browser-verification tool, not a test-suite
  dependency; Chromium was already cached at `~/.cache/ms-playwright`). Genuine
  `display-mode: standalone` cannot be produced by CDP media emulation on a normal
  tab (confirmed experimentally — `Emulation.setEmulatedMedia` has no effect on it);
  the only real way is Chromium's `--app=<url>` launch mode (headed, not headless —
  exactly what an installed PWA opens as). Confirmed at 390×844: nav is
  `position: fixed`, `body` gets `padding-bottom: 64px`, dashboard-grid collapses to
  one column; at 1280px it's two columns.
- [x] `CACHE_NAME` bumped (now v9); load on a device that had old CSS cached.
  Seeded a fake stale response under the old `budget-static-v6` cache key, forced a
  genuine unregister→re-register install/activate cycle (`reg.update()` alone
  doesn't refire `activate` when `sw.js`'s bytes haven't changed between requests —
  confirmed experimentally), and verified the stale cache key is swept and
  `/static/css/app.css` serves the real current file, not the stale one.
- [x] Keyboard pass: every quick action reachable, `:focus-visible` visible on the
  dark ground, sparkline keeps its accessible name. Confirmed `qa-amount` is
  keyboard-focusable with a visible 2px outline on the dark background, and no
  positive-`tabindex` traps in the quick-actions grid. Sparkline: a single-point
  trend correctly has no per-point `<title>` (relies on the `<svg>`'s own
  `aria-label` instead — verified present); seeded a second day's snapshot and
  confirmed the multi-point trend does render per-point `<title>` tooltips.

All 16 checks passed. `playwright` stays available in this venv going forward for
future browser-only verification (service workers, push, DOM event wiring).

### Not scoped here — needs a separate, explicit yes

`design/COMPACTION.md` C2 (merge obligations/debts/purchases into one "commitments"
list with a `kind` tag — schema-touching, own session), C3 (demote `/snapshots` from
nav), C4 (collapse nav from 4 destinations to 2, try only after C1 and C3 land). Don't
start any of these off this document.

## ~~2. `IDEAS.md` triage~~ — DONE (2026-09-03)

Moved four entries from "Under consideration" to "Promoted": "Why did
safe-to-spend move?" narrative, "Can I afford this?" box, the daily digest
(also corrected its stale "not yet wired into the VPS crontab" line — it is,
confirmed by the `shelby` 403 incident having come from a real scheduled send),
and "Payment history / ledger" (its "nothing reads it back" gap closed
2026-08-26 via `/ledger` + the inline activity feed — folded that into the
promoted entry's text rather than leaving it looking open). The three
event-shaped quick actions' auto-logging turned out **already** Promoted
("Auto-move account balances + debt payoff projection") — nothing to move
there. Fixed two now-dangling cross-references left behind by the moves (the
"Event-style entry" entry pointed at "Payment history / ledger... above"; the
narrative entry pointed at "the daily digest... below" — both still resolve,
just needed their wording adjusted for the new location).

"Freshness/staleness indicators" updated in place — was already correctly
pointing at §1.1, tightened "Fully scoped" to "Scoped and shipped" since §1.1
has since shipped. Left untouched, genuinely still open: everything under §3,
the "dismiss all" bulk-action idea, and the two-minute data-fixes note (needs
the user's real numbers — not something to invent, so not touched this pass).

## ~~3. Small scoped backlog items~~ — DONE (2026-09-06)

- ~~**`committed_purchases` free-text categories**~~ — DONE (2026-09-05). Migration
  `0012_free_text_purchase_categories.sql` (same rebuild-table shape as
  `0007_free_text_types.sql`), `repo.list_distinct_categories()` added, both the full
  form and the dashboard's quick-purchase form swapped `<select>` → `<input
  list>`+`<datalist>`. Verified against a real `uvicorn`: a category inserted directly
  via SQL and one submitted through the actual form both round-trip with no `CHECK`
  violation. (`spending_leaks`'s half of this item was already dropped — see §1.9.)
- ~~**Rate limit on `/settings/totp/confirm`**~~ — DONE (2026-09-05). Mirrors
  `CHANGE_PASSWORD_MAX_ATTEMPTS`'s shape exactly (10/hour, keyed per `user_id`).
  Closes the gap re-confirmed in §7's audit.
- ~~**"Incomplete debt" indicator**~~ — DONE (2026-09-05). Condition used the exact
  two fields the backlog note itself named: `minimum_payment_cents == 0` or
  `next_due_date IS NULL`. New `debts.py::_is_incomplete()`, badged on `/debts` with
  the existing `.badge--warning` style — an ongoing indicator, not just the one-time
  `from_bank_sync` banner. Presentational only, doesn't touch `calc.py`.
- ~~**Combined spreadsheet export**~~ — DONE (2026-09-05). Picked combined-CSV over a
  ZIP per this item's own note that it's friendlier to paste into a chat in one shot —
  matches how this app's data actually gets reviewed. New `GET /export/csv-combined`:
  one file, `table` column, union of every table's columns (blank where a record's own
  table doesn't have that column), reuses `export._csv_safe`.
- ~~**"Dismiss all" bulk action on `/bank/transactions`**~~ — DONE (2026-09-05).
  Checkbox per row via the HTML5 `form=` attribute (no nested `<form>`s needed, since
  each row already has its own match/spending forms) + a "Dismiss selected" button.
  New `POST /bank/transactions/dismiss-selected` loops over the submitted ids like
  `apply_sync` already does; a bad/other-user id in the batch just no-ops instead of
  failing the whole request. Verified against a real `uvicorn`: seeded 3 staged
  transactions, dismissed 2 via the actual checkboxes, confirmed the third untouched.
- ~~**`shelby`'s digest email 403ing**~~ — DONE (2026-08-30). See §0.

~~**Reconciliation / "catch-up" review screen**~~ — DONE (2026-09-06), merged with
the dashboard mini balance-update widget idea per the user's own framing that they
overlap ("scope them together... make the existing function better"). Rather than a
separate new page: (1) `/today/balance` now covers debts too (previously
accounts-only — a real gap, not just a nice-to-have) and gained an "adjust by
amount" mode (+/- delta against the current balance, computed server-side — the
actual "calculator feel" ask) alongside the original "set exact balance"; (2) the
dashboard's stale-balance alert now lists each stale account/debt with its own
inline one-field quick-update form, so clearing everything stale happens in one
pass on the dashboard itself, no navigation. Bills due / income to mark received /
purchase payments were already all listed in the existing "Update today" grid — the
only genuine gaps were debt balance updates and the "type the new absolute number"
friction, both closed here. Found and fixed a real CSS/JS bug via Playwright along
the way: a `hidden` attribute silently overridden by a same-element class also
setting `display`. Verified end-to-end against a real `uvicorn` + Playwright.

- ~~**Coarse tracking mode for high-churn debts**~~ — DONE (2026-09-06). Per-debt
  `coarse_tracking` flag (migration `0013`, plain `ADD COLUMN`) exempts a debt from
  `_balance_freshness`'s staleness gate entirely — never triggers "Stale," never
  counts toward the sparkline's last-real-update date. Doesn't touch what feeds
  `safe_to_spend` (still just the minimum payment due in the window). Checkbox +
  explanation on the debt form. The Money hub's separate fixed 30-day list-page
  nudge (`hubs.py::_is_stale_row`) is deliberately untouched — a different, lower-
  stakes mechanism than the dashboard's confidence gate this item was actually about.

**New idea surfaced 2026-09-05** (user's own observation using the app day to day,
not yet scoped, logged in `IDEAS.md`'s "Under consideration"): a dashboard-resident
mini balance-update widget (account dropdown + amount, "calculator feel"), separate
from and in addition to the existing `/today` quick-actions grid. SimpleFIN auto-sync
is confirmed working well, so this is about manual updates for unsynced/cash
accounts — not a bank-sync gap. Needs its own scoping pass, not part of this §3 batch.

## ~~4. Documentation catch-up~~ — DONE (2026-09-06)

`IMPLEMENTATION_HISTORY.md` caught up from 2026-08-03 through tonight: the
available-balance setting, the 2026-08-26 CSRF list-delete bug + cron-timeout
fix + full "feels dead" fix set, the 2026-08-30 backlog triage (auto-apply,
shelby's digest, `PLAN.md`'s own creation), the 2026-09-02/03 UI/design
overhaul (condensed, full detail stays in §1 above), the security audit +
README rewrite + public risk pass, and everything from tonight's §3
completion including the EmailJS security gap it found and fixed. `snapshot.md`
regenerated. §5 item 4 (skim this file before going public) can now actually
happen — nothing was skimmable before this.

## 5. Public-readiness (`MAKING_PUBLIC.md` execution)

Do after §4, so the history skim in step 4 below has something current to skim.
Consider doing after §1 lands too — no strict dependency, but a public repo's first
impression (and any README screenshot from §6) should show the current design, not
the pre-audit one.

1. **License** — needs the user's actual choice (MIT vs. keep "look but
   don't reuse," current README wording already covers the latter with no
   file needed). Not a default to just pick.
2. **`ARCHITECTURE.md`'s Deployment section** — decide: generalize away
   the real subdomains/sibling-project name, or accept as public info (the
   domains are already public DNS, so this is a "how much do you want to
   broadcast" call, not a security one).
3. **Commit author email** — per `MAKING_PUBLIC.md`, either accept the
   real Gmail in history, or set a GitHub no-reply email for *future*
   commits only. Explicitly do not rewrite existing commit history without
   a direct, separate ask — that's destructive and hard to undo.
4. **Skim `IMPLEMENTATION_HISTORY.md`** once §4 is written, for anything
   not fit for public eyes (expect nothing, but actually look).
5. ~~**Test the EmailJS "request access" form's domain restriction**~~ — DONE
   (2026-09-06), and it found a real gap: calling the endpoint with a spoofed
   `Origin` header returned `200 OK` — the domain restriction was not actually
   enforced. Fixed properly rather than patched: the form now POSTs to a real
   backend route (`POST /login/request-access`, IP-rate-limited, same posture as
   `/login`) which sends via the same Resend integration `digest.py` already
   uses (`app/services/request_access.py`). EmailJS is fully removed — no more
   client-visible keys, no dependence on a third party's dashboard setting. See
   `CLAUDE.md`'s updated paragraph for the mechanism.
6. **The visibility flip itself** — `gh repo edit flyboy-byte/budget
   --visibility public` — stays with the user. Not run automatically even
   once every above box is checked.

## ~~6. README rewrite~~ — DONE, via §8 (2026-09-03)

Done as §8 item 1 — see there for what shipped.

## ~~7. Local `budget`-specific security audit, synced to VPS `~/security`~~ — DONE (2026-09-03)

Reviewed CSRF coverage on every mutating route (only gap: `POST /login`, already
documented as deliberate — no session exists yet to check a token against, rate-limited
instead), `app/crypto.py`'s TOTP key handling (mirrors the bank-sync key pattern
exactly, distinct env var), the admin-role boundary (5 routes, all
`/settings/users/*`, self-lockout guards confirmed in code), push-subscription
ownership scoping, the SimpleFIN SSRF allowlist (unchanged), session cookie flags
(unchanged), and re-verified the EmailJS client-side keys are still present/intentional
in `login.html`. Re-verified `bank_connections`/`access_url_encrypted` exclusion from
exports is a whole-table exclusion in `export.py`'s `TABLE_COLUMNS`, not a per-column
filter — stronger than the prior doc's phrasing implied. One re-confirmed (not new)
open item: `/settings/totp/confirm` has no rate limiter (low risk, already tracked in
`IDEAS.md` since 2026-07-29) — not fixed this pass, it was a docs-sync review, not a
code-change session.

`security-audit.md` was materially stale (missing TOTP, admin role, push, digest
email, and the §1 UI pass entirely) — refreshed in place on the VPS with a "what's
changed since the last read-through" section. `secrets-and-backups.md`'s secrets
table was missing `TOTP_ENCRYPTION_KEY`/`BUDGET_DIGEST_FROM_EMAIL` — added.
`threat-assessment.md` got one new open item (#10, the TOTP-confirm gap).
`hardening-log.md` got a new dated entry. Pre-edit versions of all four files backed
up on the VPS as `<file>.bak-20260903` before overwriting. Nothing here touched this
repo's own code — pure review + VPS-side doc sync, per §7's own process note.

## ~~8. Going public — README skill + public-facing risk review~~ — DONE (2026-09-03)

**1. README skill.** Ran, this *is* §6. Restructured to the skimmer-first skeleton
(hero + framing note, proof screenshot, honest status table, quickstart moved up
before feature prose) and matched to this author's sibling-repo house style
(`disc-tracker`, `riscv-pico`, `watranscribe`) rather than the previous prose-heavy
version. Screenshot is a real Playwright capture of the actual running dashboard —
seeded fake data on a disposable local DB, never real production data. Verified
rendered in a real browser (GitHub's markdown API + `github-markdown-css`,
screenshotted and read section by section, not just eyeballed as raw markdown)
before committing — alert renders correctly, image resolves, status table and code
blocks are clean, no horizontal overflow. Commit `27126cb`.

**2. Public-facing risk pass.** Go/no-go items, weighed against the live system
(not just the code):

- ~~**EmailJS "request access" form"**~~ — 🟢 **resolved, not just watched
  (2026-09-06).** This assessment assumed EmailJS's dashboard domain restriction was
  actually enforced; §5 item 5 tested it directly (spoofed `Origin` header) and got a
  live `200 OK` — it wasn't. Fixed at the root instead of patched: the form now POSTs
  to a real backend route (rate-limited by IP, same posture as `/login`) which sends
  via the existing Resend integration. EmailJS is gone entirely, not just fronted.
- **Rate limits at internet scale** — 🟢 **better than assumed, no action
  needed.** App-level: 10 attempts/5min per IP on `/login` (`app/ratelimit.py`,
  in-memory). Nginx-level: confirmed live on the box — `/login` also has its own
  `limit_req zone=login burst=3 nodelay`, a second layer the app-level limiter
  doesn't know about. `location /` (everything else, all of it behind auth+CSRF
  already) has no nginx-level limit, same posture as most of this VPS's other
  authenticated surfaces — not a new gap introduced by going public.
- **Error-response leakage** — 🟢 **confirmed clean, no action needed.** Live-tested
  against the real deployed site: a bad route returns a generic `{"detail":"Not
  Found"}` (no path/stack info), `Server: nginx` only (no version string, no
  uvicorn/Python version leak), and the full security-header suite (CSP, HSTS,
  X-Frame-Options, X-Content-Type-Options, Permissions-Policy) is already present
  on every response. FastAPI/Starlette's default (no `debug=True` anywhere) plus
  the existing `IntegrityError`/`HTTPException` handlers in `app/main.py` mean an
  uncaught exception was never going to leak a traceback either.
- **Admin-creation story** — 🟢 **resolved by the README rewrite itself.** The new
  status table's "Public registration ❌" row links straight to `ARCHITECTURE.md`'s
  explanation (CLI-only, admin-gated form) — reads as an intentional design choice,
  not an undocumented bug, without needing a separate callout.
- **SimpleFIN/bank-sync calculus** — 🟡 **raises the stakes on the items above,
  doesn't add a new one.** The app-level protections (SSRF allowlist, Fernet
  encryption, CSRF, whole-table export exclusion) don't change based on
  visibility — but a bank-adjacent app is a more attractive scanning target than a
  random hobby project, which was the actual argument for testing the EmailJS
  item before, not after, going public. That test found a real gap and it's now
  fixed — see above.

Net: nothing found here blocks the visibility flip. The one real follow-up
(EmailJS domain-restriction test) turned out to matter — it found a live gap,
now fixed via §5 item 5.
