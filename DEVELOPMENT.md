# Development Notes

This file is for whoever (human or Claude) is actively working in this codebase.

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full spec — data model, calculation rules, screens,
and multi-user notes. See [`CLAUDE.md`](./CLAUDE.md) for architecture/convention notes
aimed specifically at AI coding agents.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Local database

```bash
# First time: applies migrations/0001_initial.sql and creates your user
.venv/bin/python -m scripts.init_db <username> <password>

# Add a second/third user later (no HTTP registration route exists, by design)
.venv/bin/python -m scripts.add_user <username> <password>

# Reset a forgotten password (also revokes that user's existing sessions)
.venv/bin/python -m scripts.reset_password <username> <new_password>
```

The database lives at `data/budget.db` by default (gitignored). Override with
`BUDGET_DB_PATH=/some/other/path`.

## Running the app locally

```bash
.venv/bin/uvicorn app.main:app --reload
```

Cookies default to `Secure` (HTTPS-only), which breaks login over plain local HTTP.
For local dev:

```bash
BUDGET_INSECURE_COOKIES=1 .venv/bin/uvicorn app.main:app --reload
```

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Tests run against an in-memory SQLite database (see `tests/conftest.py`'s `db`
fixture) — nothing touches `data/budget.db`. Route-level tests use FastAPI's
`TestClient` with `app.dependency_overrides[get_db]` pointed at that same in-memory
connection, so a single test can seed data via the raw connection and then hit real
HTTP routes against it.

Run a single file or test:

```bash
.venv/bin/python -m pytest tests/test_calc.py -q
.venv/bin/python -m pytest tests/test_calc.py::test_safe_to_spend_can_go_negative -q
```

## Migrations

Add a new numbered file in `migrations/` (e.g. `0002_something.sql`) — don't edit
`0001_initial.sql` after it's been applied anywhere. `migrations/runner.py` tracks
what's applied via the `schema_migrations` table and only runs what's new:

```bash
.venv/bin/python -m migrations.runner              # applies to data/budget.db
.venv/bin/python -m migrations.runner /path/to.db   # or an explicit path
```

## Cron jobs (installed on the VPS — see `ARCHITECTURE.md`'s Deployment section)

```bash
.venv/bin/python -m scripts.snapshot                 # capture today's snapshot, all active users
.venv/bin/python -m scripts.snapshot --user logan    # just one user

.venv/bin/python -m scripts.backup                   # full JSON backup, all active users
.venv/bin/python -m scripts.backup --user logan --keep 30   # one user, prune to 30 most recent
```

Backups land in `data/backups/` (gitignored). See `ARCHITECTURE.md`'s "Deployment" section
for a concrete crontab example.

## Ideas / outside-AI feedback loop

```bash
# Regenerate snapshot.md (combined docs dump) before pasting the project into another
# AI chat/tool — don't hand-edit snapshot.md itself, it's derived.
.venv/bin/python -m scripts.make_snapshot
```

Drop new feature ideas — your own, or pasted from another AI chat — into `IDEAS.md`'s
"Raw / unsorted" section anytime. To brainstorm with the local `codex` CLI instead:

```bash
codex exec "$(cat codex.md)"
```

`codex.md` instructs it to append raw ideas to `IDEAS.md` only, not touch code. Claude
Code triages `IDEAS.md` at the start of sessions touching new feature work — `ARCHITECTURE.md`
stays the source of truth for what's actually built.

## Project layout

See `ARCHITECTURE.md`'s "Project Structure" section for the annotated tree. Short version:
`app/repositories/` is raw parametrized SQL only (no business logic), `app/services/`
is where the financial logic lives (`calc.py` is the source of truth for every
formula — treat any mismatch with `ARCHITECTURE.md` as a bug in the code, not the spec),
`app/routers/` is a thin HTTP layer over both.

## Picking this up cold

Read in this order: `ARCHITECTURE.md`'s "Calculation Rules" section (the formulas, and the
future-income-exclusion invariant that the whole app is built around), then
`app/services/calc.py` (should match the formulas exactly), then `tests/test_calc.py`
for the edge cases that are already locked down. After that, `app/routers/dashboard.py`
is the smallest complete example of the repo → service → router → template pattern
every other screen follows.
