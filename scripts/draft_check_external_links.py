#!/usr/bin/env python3
"""DRAFT — external link reachability check. NOT wired into check_site.py, tests/, or CI.

Fills the one gap `scripts/check_site.py` documents deliberately: it does not fetch or
validate external URLs. This script extracts every http(s):// reference from the site's
committed HTML that does NOT point at the site's own CNAME host, and issues a real HTTP
request to confirm it resolves (2xx/3xx). It is a separate, unwired file so a future
session can decide the CI/timeout/rate-limit policy before adopting it into the gate.

Self-test mode (proves the checker actually detects a broken link before trusting it,
per TESTING_STANDARD.md rule 4 — "a new check proves itself on a planted failure first"):

    python scripts/draft_check_external_links.py --self-test

This starts a local HTTP server on 127.0.0.1 (no real network dependency), serving a
known-200 page and a known-404 path, points the checker at fixture HTML referencing both,
and asserts the checker: (a) flags the 404 as broken, (b) does NOT flag the 200 page, and
(c) ignores an internal-only reference. Exits non-zero if either assertion fails, meaning
the checker's own control did not behave as a working detector should.

Normal mode:

    python scripts/draft_check_external_links.py [site_root]

Scans every *.html file under site_root (default: repo root) for http(s):// references
whose host is not the CNAME host, requests each once, and reports any non-2xx/3xx result
or connection failure. Exit code is the count of broken links found (0 = all resolved).
"""

from __future__ import annotations

import argparse
import http.server
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

EXTERNAL_LINK_RE = re.compile(r'''(?:href|src)=["'](https?://[^"']+)["']''', re.IGNORECASE)
TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class LinkCheck:
    url: str
    source: Path
    ok: bool
    detail: str


def _site_host(root: Path) -> str | None:
    cname = root / "CNAME"
    if not cname.is_file():
        return None
    host = cname.read_text(encoding="utf-8").strip().lower().rstrip(".")
    return host or None


def find_external_links(root: Path) -> list[tuple[str, Path]]:
    site_host = _site_host(root)
    found: list[tuple[str, Path]] = []
    for html_file in sorted(root.rglob("*.html")):
        if ".git" in html_file.relative_to(root).parts:
            continue
        text = html_file.read_text(encoding="utf-8", errors="replace")
        for match in EXTERNAL_LINK_RE.finditer(text):
            url = match.group(1)
            host = (urlsplit(url).hostname or "").lower()
            if site_host and host == site_host:
                continue
            found.append((url, html_file))
    return found


def check_link(url: str, source: Path) -> LinkCheck:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        # Some servers reject HEAD; fall back to GET before declaring it broken.
        if error.code == 405:
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, method="GET"), timeout=TIMEOUT_SECONDS
                ) as response:
                    status = response.status
            except (urllib.error.HTTPError, urllib.error.URLError) as retry_error:
                return LinkCheck(url, source, False, f"GET fallback failed: {retry_error}")
        else:
            status = error.code
    except urllib.error.URLError as error:
        return LinkCheck(url, source, False, f"connection failed: {error.reason}")

    ok = 200 <= status < 400
    return LinkCheck(url, source, ok, f"HTTP {status}")


def scan(root: Path) -> list[LinkCheck]:
    return [check_link(url, source) for url, source in find_external_links(root)]


class _SelfTestHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:  # noqa: N802 - required override name
        if self.path == "/ok":
            self.send_response(200)
        else:
            self.send_response(404)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - required override name
        self.do_HEAD()

    def log_message(self, *_args: object) -> None:  # silence server noise
        pass


def run_self_test() -> int:
    server = http.server.HTTPServer(("127.0.0.1", 0), _SelfTestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "CNAME").write_text("example.test\n", encoding="utf-8")
            (root / "index.html").write_text(
                f'<!doctype html><html><body>'
                f'<a href="http://127.0.0.1:{port}/ok">good external</a>'
                f'<a href="http://127.0.0.1:{port}/missing">broken external</a>'
                f'<a href="/local-page">internal, ignored</a>'
                f'<a href="https://example.test/page">same-host, ignored</a>'
                f'</body></html>',
                encoding="utf-8",
            )
            results = scan(root)

        by_url = {result.url: result for result in results}
        good_url = f"http://127.0.0.1:{port}/ok"
        bad_url = f"http://127.0.0.1:{port}/missing"

        failures = []
        if len(results) != 2:
            failures.append(f"expected 2 external links found, got {len(results)}: {[r.url for r in results]}")
        if good_url not in by_url or not by_url[good_url].ok:
            failures.append(f"known-good link was not reported OK: {by_url.get(good_url)}")
        if bad_url not in by_url or by_url[bad_url].ok:
            failures.append(f"known-bad (404) link was NOT flagged as broken: {by_url.get(bad_url)}")

        if failures:
            for failure in failures:
                print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
            return 1

        print("SELF-TEST OK: known-good link passed, known-bad 404 was correctly flagged")
        return 0
    finally:
        server.shutdown()
        thread.join(timeout=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=None)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the planted-failure control instead of scanning the real site",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    results = scan(root)
    broken = [result for result in results if not result.ok]
    for result in results:
        marker = "OK" if result.ok else "BROKEN"
        print(f"{marker}  {result.url}  ({result.detail})  <- {result.source.name}")
    print(f"\n{len(results)} external link(s) checked, {len(broken)} broken")
    return len(broken)


if __name__ == "__main__":
    raise SystemExit(main())
