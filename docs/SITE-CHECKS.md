# Site validation

`scripts/check_site.py` scans every committed `*.html` page outside `.git`, checks
strictly balanced source markup and duplicate attributes, and resolves each internal
`href` or `src` against the repository. It recognizes page files, directory indexes,
extensionless URLs such as `/actuo/support`, and absolute URLs on the `CNAME` host.

Run both gates from the repository root:

```text
python scripts/check_site.py
python -m unittest discover -s tests -v
```

The tests include known-bad fixture controls for malformed markup and a missing
internal page. The repository-corpus test independently enumerates every HTML file so
a narrowed checker scan fails. GitHub Actions runs both gates on every push and pull
request.

## Audit status

**Codex-built, Claude audit pending.** Built with Codex `gpt-5.6-sol` on 2026-08-07.
The repo has no roadmap or claim record, so the orchestrator assignment was the only
work claim; no tracker format was invented during this small build.

Claude audit focus: verify the strict stack rules against any intentionally omitted
HTML end tags, challenge URL classification and root-escape handling, and re-run both
known-bad controls. The checker deliberately does not fetch or validate external URLs,
parse CSS URLs or `srcset`, or validate fragment IDs; internal references are checked
to the target file only. Its standard-library parser enforces source balancing and the
listed structural rules, not the full WHATWG browser-conformance specification.
