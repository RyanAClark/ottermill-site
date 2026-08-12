# Testing — ottermill-site

Per `~/.claude/docs/TESTING_STANDARD.md` (claude-config). This repo already carries a real,
CI-wired suite (`scripts/check_site.py` + `tests/test_check_site.py`, built with Codex
`gpt-5.6-sol` 2026-08-07, Claude audit noted as pending in `docs/SITE-CHECKS.md`). This file
documents that suite against the standard's five points rather than starting from zero.

## 1. Gate command

```bash
python scripts/check_site.py
python -m unittest discover -s tests -v
```

Measured this pass (n=3 each, warm interpreter, `time.perf_counter`):
- `check_site.py`: 0.36s / 0.40s / 0.34s (mean ~0.37s)
- `unittest discover`: 0.35s / 0.43s / 0.35s (mean ~0.38s)

Combined gate ≈0.75s — far under the ≤90s target. `.github/workflows/site-check.yml` runs both
on every push and pull request (GitHub Actions, `ubuntu-latest`), so green is a fact checked by
CI, not a claim.

## 2. Tier markers

None named, and none needed at this size: one script scan (structural HTML validation +
internal-reference resolution) plus one `unittest` module, both offline, both sub-second. No
slow or flaky lane exists.

## 3. Seam list

| Seam | Frozen by |
|---|---|
| Site HTML source balance (tags, duplicate attributes, void-element rules) | `test_known_bad_malformed_markup_fails` (fixture: `tests/fixtures/malformed.html.txt`) |
| Internal link/reference resolution (`href`/`src` → file on disk, directory index, extensionless URL, same-host absolute URL, fragment-only) | `test_known_bad_broken_internal_link_fails`, `test_extensionless_reference_resolves_to_html_file`, `test_fragment_only_reference_resolves_to_its_source_page`, `test_directory_and_same_host_absolute_references_resolve` |
| Full repository corpus (every committed `*.html` outside `.git`) | `test_every_repository_page_is_parsed_and_links_resolve` — independently enumerates files so a narrowed scan fails |
| **External link reachability** (an `https://` URL to another site actually resolving, not 404/dead) | **Not covered.** `docs/SITE-CHECKS.md` states this deliberately: "does not fetch or validate external URLs." A draft, unwired script for this gap is added in this pass — see below. |
| CSS `url()` / `srcset` references, fragment-ID existence within the target page | **Not covered** (documented limitation, same source). |

## 4. Nightly

**None — "the script IS the suite," runs on publish** per the standard's per-repo table, and
that already happens: `.github/workflows/site-check.yml` fires on every push/PR, which for a
static-site repo with infrequent commits is a closer match to "on publish" than a clock-driven
nightly. No separate nightly task is needed or filed.

## 5. Known debt against the eight rules

| Rule | Status | Note |
|---|---|---|
| 1. Two tiers, both real | **Partial** | Gate tier is real, fast, and CI-enforced. No nightly exists, but the standard's own table calls that correct for this repo shape (§4). |
| 2. Zero standing red | Met | Both gates green this pass; CI enforces on every push. |
| 3. Pins execute behavior | Met | Tests call `scan_site`/`discover_html_files` directly against real (and fixture) HTML — none asserts on source text. |
| 4. New check proves itself on a planted failure | Met | Two known-bad fixture controls exist (`malformed.html.txt`, `broken-link.html.txt`) and both are asserted to fail the checker. |
| 5. Fix evidence is a pair | Not yet exercised | No fix has landed against this checker since it was built; no pair-ledger entry exists. Applies at the next bug fix, not now. |
| 6. Seams get contract tests | **Partial** | Internal-reference and markup seams have deliberate rejection cases (§3). The external-link seam has none — draft script below narrows this but is not wired into the gate. |
| 7. Pure core / thin shell | Met | `scan_site`/`_internal_target`/`discover_html_files` are pure functions over paths and strings; `main()` is the thin CLI shell (argv, stdout/stderr, exit code). |
| 8. Mutation checks diff-scoped | Not yet run | No mutation-check pass has been done against `check_site.py`; the pending "Claude audit" noted in `docs/SITE-CHECKS.md` is the natural place for one. |
| **Pending independent audit** | Open | `docs/SITE-CHECKS.md` itself flags "Codex-built, Claude audit pending" — that audit has not happened as of this sweep. Noted here so it is not lost. |

## Draft addition this pass (unwired, new file)

`scripts/draft_check_external_links.py` — extracts every `http(s)://` reference from the site's
HTML that does **not** resolve to the site's own `CNAME` host, issues a `HEAD` (falling back to
`GET`) request with a short timeout, and reports any non-2xx/3xx result or connection failure.
Proves itself with a `--self-test` mode: spins up a local `http.server` on `127.0.0.1` serving a
known-200 page and a known-404 page, points the checker at fixture HTML referencing both, and
asserts the checker flags the 404 and passes the 200 — a planted-failure control with no real
network dependency (rule 4). **Not referenced by `check_site.py`, `tests/`, or the CI workflow**
— it is a draft for Ryan to fold in (real network calls in CI need a rate/timeout policy decision
first, which is out of scope for this documentation pass).
