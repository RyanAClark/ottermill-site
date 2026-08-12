"""Keep the versioned quality hook present and the clone pointed at it."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BLOCK = b"""# >>> quality-gate v1 (revert unit; do not edit inside) >>>
QUALITY_GATE_ROOT=$(git rev-parse --show-toplevel) || exit 1
QUALITY_GATE_SCRIPT="$QUALITY_GATE_ROOT/scripts/quality_gate.py"
if [ ! -f "$QUALITY_GATE_SCRIPT" ]; then
  echo "quality-gate: checker missing in this worktree; run: git merge origin/main"
  exit 1
fi
if [ -n "${PY:-}" ]; then
  QUALITY_GATE_PY=$PY
elif command -v python >/dev/null 2>&1; then
  QUALITY_GATE_PY=python
elif command -v python3 >/dev/null 2>&1; then
  QUALITY_GATE_PY=python3
else
  echo "quality-gate: Python interpreter missing; install Python, then run:" >&2
  echo '  python -m pip install "ruff==0.16.2"' >&2
  exit 1
fi
"$QUALITY_GATE_PY" "$QUALITY_GATE_SCRIPT" --staged || exit 1
# <<< quality-gate v1 <<<
"""


def configured_hooks_path() -> str:
    override = os.environ.get("QUALITY_GATE_HOOKS_PATH_UNDER_TEST")
    if override is not None:
        return override
    proc = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={ROOT.as_posix()}",
            "config",
            "--get",
            "core.hooksPath",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, (
        "quality gate is unarmed; run: git config core.hooksPath .githooks"
    )
    return proc.stdout.strip()


def test_quality_gate_hook_contains_the_exact_delimited_block() -> None:
    hook = (ROOT / ".githooks" / "pre-commit").read_bytes()

    assert EXPECTED_BLOCK in hook.replace(b"\r\n", b"\n")


def test_core_hooks_path_resolves_to_this_worktree_hook() -> None:
    configured = Path(configured_hooks_path())
    resolved = configured if configured.is_absolute() else ROOT / configured

    assert resolved.resolve() == (ROOT / ".githooks").resolve(), (
        "quality gate points outside this worktree; run: "
        "git config core.hooksPath .githooks"
    )
