"""Exercise Ottermill's standalone correctness adoption gate."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def gate() -> dict[str, Any]:
    return runpy.run_path(str(ROOT / "scripts" / "correctness_gate.py"))


def test_correctness_policy_agrees_with_the_indexed_product_surface() -> None:
    assert gate()["check"]() == []
