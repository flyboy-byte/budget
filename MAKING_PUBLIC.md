# Making this repo public — checklist

This repo is currently **private** on GitHub. If that ever changes, here's what to
check first, based on an actual audit of the tracked files and full git history (not
just current working-tree state — old commits are public forever too, even if the
file is later changed or deleted).

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

## Things to actually change before/when going public

1. **Personal domain and infrastructure details in `ARCHITECTURE.md`'s "Deployment"
   section.** It names the real subdomains (`budget.flyboybyte.com`,
   `disc.flyboybyte.com`, `trading.flyboybyte.com`) and a sibling private project
   (`moomoo`). None of this is a security hole by itself (the domains are already
   public DNS), but it broadcasts your personal domain and what else you host there
   to anyone browsing the repo. Either generalize that section (e.g. "a VPS running
   nginx + systemd `--user` services") or accept that it's public info.

2. **Commit author email is a real personal Gmail address**
   (`logan07night@gmail.com`), baked into every existing commit (growing steadily —
   check `git log --oneline | wc -l` for the current count) and visible in
   `git log`/`git blame` forever once public — rewriting it after the fact means
   rewriting history (force-pushing, breaking any clone/fork), which is disruptive
   and easy to get wrong. Two real options:
   - Accept it (plenty of public repos have a real email in commit history).
   - Before making it public, configure a GitHub-provided no-reply email
     (`<id>+<username>@users.noreply.github.com`, from GitHub Settings → Emails) for
     *future* commits, and decide separately whether rewriting the existing commits'
     author email is worth the disruption. Don't do this rewrite without explicitly
     deciding to — it's a destructive, hard-to-undo operation.

3. **No `LICENSE` file exists.** Right now `README.md` says "Personal project, not
   currently licensed for reuse" — that's a valid legal stance (all rights reserved
   by default), but if you want people to actually be able to use/fork the code,
   add a real license (MIT is the common permissive choice for a project like this).
   If you want to keep it "look but don't reuse," the current README wording is
   sufficient and no LICENSE file is needed.

4. **Decide whether `IMPLEMENTATION_HISTORY.md` stays in.** It's a genuinely useful
   "how this got built" document (phase-by-phase, including the bugs found along the
   way), but it's also a very candid, informal build log — worth a skim to make sure
   there's nothing in there you wouldn't want public (there currently isn't anything
   sensitive, just a more casual tone than the polished README).

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
