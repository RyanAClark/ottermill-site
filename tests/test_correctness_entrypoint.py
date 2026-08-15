"""Execute the shipped site-check CLI through failure and recovery."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "check_site.py"


def _run(site: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), str(site)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_real_entrypoint_distinguishes_failure_then_recovers(tmp_path: Path) -> None:
    missing_population = _run(tmp_path)
    assert missing_population.returncode == 2
    assert "no HTML files found" in missing_population.stderr

    page = tmp_path / "index.html"
    page.write_text('<html><body><a href="/missing">bad</a></body></html>', "utf-8")
    product_failure = _run(tmp_path)
    assert product_failure.returncode == 1
    assert "unresolved internal reference" in product_failure.stderr

    page.write_text("<html><body>recovered</body></html>", "utf-8")
    recovered = _run(tmp_path)
    assert recovered.returncode == 0
    assert "OK: 1 HTML file(s) parsed" in recovered.stdout
