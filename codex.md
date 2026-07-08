# Brief for `codex` brainstorming sessions

You're being pointed at this file to brainstorm feature/UX ideas for a personal
finance app. Read `ARCHITECTURE.md` and `IDEAS.md` in this repo first — they're the actual
source of truth for what this app is, what's already built, and what's already been
considered or rejected.

## What this app is (short version — `ARCHITECTURE.md` has the full spec)

A private, single-user (multi-tenant-ready), self-hosted "cash-commitment and runway
tracker." It answers one question conservatively: *what's actually safe to spend right
now*, treating cash-in-hand and cash-that's-already-spoken-for as two different numbers.
Future income never counts toward the primary number — that's the one invariant every
idea must respect.

## What NOT to propose

Check `ARCHITECTURE.md`'s "Explicit Non-Goals" section and `IDEAS.md`'s "Rejected / parked"
section before suggesting anything. As of this writing, already ruled out: bank
sync/Plaid, shared/household budgets or linked accounts, investing/net-worth tracking,
receipt scanning, a mobile app, subscription billing, public SaaS features, a public
self-serve registration route, JS charting libraries, and proactive alerts/notification
digests (explicitly rejected — don't re-propose without a genuinely new angle).

## What to do

Brainstorm ideas that make the app feel less like a CRUD form / spreadsheet and more
like a tool that answers questions — in the same spirit as the ideas already under
consideration in `IDEAS.md` ("can I afford this?" instant answers, spending narratives).
Favor ideas that:
- Reuse the existing calculation engine (`app/services/calc.py`,
  `app/services/whatif.py`) rather than inventing new financial logic.
- Don't require a new external service/dependency unless there's a strong reason.
- Are scoped small enough to describe in 2-4 sentences, not a redesign.

## Output format — important

**Do not write or edit any code.** Append your raw output to `IDEAS.md`'s "## Raw /
unsorted" section only — as a bulleted list, one idea per bullet, each 1-3 sentences.
Do not touch any other section of `IDEAS.md` or any other file. A human (or a future
Claude Code session) will triage what you produce from there.
