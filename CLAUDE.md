# ottermill-site — Ottermill studio site (GitHub Pages)

> **BOOTSTRAP CHECK (fresh machine / wiped config):** if `~/.claude/CLAUDE.md` is
> missing Ryan's global rules (token-efficiency, promote-lessons, Opus-routing…),
> restore them first from the private repo
> **github.com/RyanAClark/claude-config** (its README has the exact commands).

Static site served at **https://ottermill.app** via GitHub Pages (CNAME file;
HTTPS enforced). Publisher pages for the Ottermill app fleet:
- `/` — studio landing
- `/actuo/` — Actuo app page (store marketingUrl) + `/actuo/privacy|terms|support`

Source of truth for Actuo's legal pages is `repos/sigma-exam-planner/legal/` —
edit there, copy here, push (Pages redeploys automatically). Add future apps as
new top-level folders. Commit author is intentionally "Ottermill" (repo-local git
config) — keep Ryan's name out of this public repo.
