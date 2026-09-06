# Making this repo public — checklist

This repo is currently **private** on GitHub. Every decision below is made
(2026-09-06) and the LICENSE file is already added — the only thing left is the
actual visibility flip, and that stays the user's own call, run manually
whenever they're ready. Based on an actual audit of the tracked files and full
git history (not just current working-tree state — old commits are public
forever too, even if the file is later changed or deleted).

## Already verified clean (as of this checklist)

- **No secrets, credentials, or private keys** anywhere in tracked files or git
  history — searched every commit's diff, not just current files.
- **No database files or `.env` ever committed** — searched the full history of
  added files for `*.db`/`.env`, none found. `.gitignore` covers `data/`, `*.db*`,
  and `.env` going forward.
- **No raw IP address committed** anywhere in tracked files.
- Real financial data was never at risk in the first place: the app is self-hosted,
  `data/` is gitignored from the start, and nothing in this repo's history ever
  contained real account/debt/balance numbers.

## Decisions made (2026-09-06) — nothing left to decide before the flip

1. ~~**Personal domain and infrastructure details in `ARCHITECTURE.md`'s
   "Deployment" section.**~~ **Decided: leave as-is.** The user's own call — the
   domains are already public DNS, and the doc doubles as this project's own
   local AI-agent context, where the real detail is more useful than a
   generalized placeholder would be.
2. ~~**Commit author email is a real personal Gmail address**~~ **Decided: keep
   it.** Already baked into every existing commit either way; a future-only
   no-reply switch had limited privacy benefit, so not worth the added
   complexity. Existing history is never rewritten for this without a
   separate, direct ask.
3. ~~**No `LICENSE` file exists.**~~ **Decided: GNU GPLv3** — added as
   `LICENSE` (canonical text from gnu.org), `README.md`'s License section
   updated to match. Copyleft: any distributed derivative must stay open
   source under the same license.
4. ~~**Decide whether `IMPLEMENTATION_HISTORY.md` stays in.**~~ **Skimmed
   2026-09-06, nothing found.** No secret values, IPs, or leaked keys — env var
   *names* appear throughout, which is normal documentation, not a leak.
   Staying in as-is.

## The actual visibility flip

When you're ready: `gh repo edit flyboy-byte/budget --visibility public` (or via
GitHub's repo settings UI). This tool will not run that command for you — it's a
one-way-feeling change (technically reversible, but the point of asking is that
you should be the one deciding when).

## Not a concern

- Security posture (argon2 hashing, CSRF, session handling, rate limiting) — the
  design is meant to hold up under public scrutiny; there's no security-through-
  obscurity being relied on here. Public code doesn't weaken any of it.
- The VPS deployment itself — hardened independently (SSH key-only, `ufw`,
  `fail2ban`, unattended-upgrades, nginx rate-limiting), not dependent on this
  repo staying private.
