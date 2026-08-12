# ottermill-site — Ottermill studio site (GitHub Pages)

> **BOOTSTRAP CHECK (fresh machine / wiped config):** if `~/.claude/CLAUDE.md` or
> `~/.claude/rules/` is missing Ryan's global rules (token-efficiency,
> promote-lessons, Opus-routing…), restore them first from the private repo
> **github.com/RyanAClark/claude-config** (its README has the exact commands).

Static site served at **https://ottermill.app** via GitHub Pages (CNAME file;
HTTPS enforced). Publisher pages for the Ottermill app fleet:
- `/` — studio landing
- `/actuo/` — Actuo app page (store marketingUrl) + `/actuo/privacy|terms|support`

**CANONICALITY (resolved 2026-07-31, CAL phase 1A): this repo's `actuo/*.html` copies
of Actuo's legal pages are CANONICAL, because this repo is what actually serves
ottermill.app** (it holds the `CNAME` and GitHub Pages publishes from it).
`repos/sigma-exam-planner/legal/` is a MIRROR and must be updated in the same change,
then the live URL verified (`curl` the page and grep for the new text — a successful
push is not evidence the served page moved). This supersedes the previous line naming
sigma-exam-planner the source of truth, which contradicted that repo's own CLAUDE.md.
Add future apps as new top-level folders. Commit author is intentionally "Ottermill" (repo-local git
config) — keep Ryan's name out of this public repo.

Testing: `docs/TESTING.md` (instantiates the estate testing standard, `claude-config/docs/TESTING_STANDARD.md`).
