#!/usr/bin/env python3
"""Run the incident-derived correctness rules only when that policy lane is armed."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any, Mapping, Sequence, cast


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "correctness.json"
RULES = ROOT / "semgrep" / "raw_text_fingerprint_fixture.yml"
FIXTURES = ROOT / "semgrep"
REQUIRED_VERSION = "1.173.0"


class SemgrepGateError(ValueError):
    """The rule controls, scan population, or Semgrep process are invalid."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SemgrepGateError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def lane_state(policy: Mapping[str, Any]) -> str:
    repos = _object(policy.get("repos"), "repos")
    if len(repos) != 1:
        raise SemgrepGateError("consumer policy must name exactly one repository")
    repo = _object(next(iter(repos.values())), "repository")
    profiles = _object(policy.get("profiles"), "profiles")
    profile_name = repo.get("profile")
    if not isinstance(profile_name, str) or profile_name not in profiles:
        raise SemgrepGateError("repository references an unknown profile")
    profile = _object(profiles[profile_name], f"profile {profile_name}")
    lane = _object(profile.get("correctness-semgrep"), "correctness-semgrep")
    state = lane.get("state")
    if state not in {"ARMED", "NOT_APPLICABLE", "MIGRATION_BLOCKED"}:
        raise SemgrepGateError(f"correctness-semgrep has unknown state: {state!r}")
    return cast(str, state)


def load_policy() -> dict[str, Any]:
    try:
        return _object(json.loads(POLICY.read_text(encoding="utf-8")), "policy")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemgrepGateError(f"cannot load correctness policy: {exc}") from exc


def user_scripts_directory() -> Path | None:
    """Where pip's per-user install scheme puts console scripts, if it has one.

    `pip install --user` — the fallback whenever the interpreter's own scripts
    directory is not writable, which is the ordinary case for a system Python on
    Windows — lands here. It is neither a sibling of `sys.executable` nor on
    `PATH`. Measured 2026-08-18: this gate reported a correct, pinned semgrep as
    missing for exactly that reason and blocked every commit in two repositories.
    """

    scheme = "nt_user" if os.name == "nt" else "posix_user"
    if scheme not in sysconfig.get_scheme_names():
        return None
    try:
        return Path(sysconfig.get_path("scripts", scheme=scheme))
    except KeyError:
        return None


def semgrep_binary() -> str:
    configured = os.environ.get("SEMGREP_BIN")
    if configured:
        return configured
    names = ("semgrep.exe", "semgrep") if os.name == "nt" else ("semgrep",)
    executable_dir = Path(sys.executable).resolve().parent
    directories = [executable_dir]
    user_scripts = user_scripts_directory()
    if user_scripts is not None:
        directories.append(user_scripts)
    # `executable_dir` is already the scripts directory inside a venv and on
    # POSIX, but a Windows BASE install puts the interpreter one level up
    # (`C:\Python314\python.exe`, console scripts in `C:\Python314\Scripts`).
    # improvement-lab had fixed this locally in its vendored copy; merged here
    # 2026-08-18 rather than overwritten by the sync.
    #
    # It goes LAST, after the per-user scheme, and that order is load-bearing:
    # a stale system-wide semgrep in the base `Scripts` directory would
    # otherwise win over the pinned per-user copy, and `verify_tool` raises on
    # version drift with no fallback — re-blocking every commit, which is the
    # incident this whole search exists to fix (review, 2026-08-18).
    if os.name == "nt":
        directories.append(executable_dir / "Scripts")
    for directory in directories:
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)
    binary = shutil.which("semgrep")
    if not binary:
        raise SemgrepGateError(
            f"semgrep {REQUIRED_VERSION} is missing; install the pinned project tool"
        )
    return binary


def run(binary: str, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SEMGREP_SEND_METRICS"] = "off"
    # The console script resolves its `pysemgrep` sibling through PATH, so a
    # binary found OUTSIDE PATH fails with "executing pysemgrep failed" even
    # though it is the correct pinned tool. Put its own directory first.
    # `resolve()` is load-bearing: a bare-name SEMGREP_BIN would otherwise make
    # this the relative ".", putting the scanned repository root on the child
    # PATH so a checked-in `pysemgrep` could run instead of the real one.
    directory = Path(binary).resolve().parent
    if directory.is_dir():
        existing = env.get("PATH", "")
        env["PATH"] = (
            f"{directory}{os.pathsep}{existing}" if existing else str(directory)
        )
    return subprocess.run(
        [binary, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
        env=env,
    )


def verify_tool(binary: str) -> None:
    proc = run(binary, ["--version"])
    if proc.returncode != 0:
        raise SemgrepGateError(f"semgrep version check failed: {proc.stderr.strip()}")
    if proc.stdout.strip() != REQUIRED_VERSION:
        actual = proc.stdout.strip()
        raise SemgrepGateError(
            f"semgrep version drift: expected {REQUIRED_VERSION}, got {actual!r}"
        )


def verify_rule_controls(binary: str) -> None:
    proc = run(binary, ["--test", str(FIXTURES)])
    output = proc.stdout + proc.stderr
    if proc.returncode != 0:
        raise SemgrepGateError(f"correctness rule fixture failure: {output.strip()}")
    if "All tests passed" not in output or "No unit tests found" in output:
        raise SemgrepGateError(
            "correctness rule controls did not execute a positive population"
        )


def scan(binary: str) -> list[str]:
    proc = run(
        binary,
        [
            "--config",
            str(RULES),
            "--json",
            "--quiet",
            "--exclude",
            "*raw_text_fingerprint_fixture.py",
            str(ROOT),
        ],
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip()
        raise SemgrepGateError(
            f"correctness scan process failed ({proc.returncode}): {detail}"
        )
    try:
        report = _object(json.loads(proc.stdout), "Semgrep report")
    except json.JSONDecodeError as exc:
        raise SemgrepGateError(f"Semgrep emitted invalid JSON: {exc}") from exc
    errors = report.get("errors")
    if not isinstance(errors, list) or errors:
        raise SemgrepGateError(f"Semgrep report contains errors: {errors!r}")
    paths = _object(report.get("paths"), "Semgrep paths")
    scanned = paths.get("scanned")
    if not isinstance(scanned, list) or not scanned:
        raise SemgrepGateError("Semgrep scanned zero files")
    results = report.get("results")
    if not isinstance(results, list):
        raise SemgrepGateError("Semgrep results must be a list")
    findings: list[str] = []
    for result in cast(list[Any], results):
        record = _object(result, "Semgrep finding")
        path = record.get("path")
        start = _object(record.get("start"), "Semgrep finding start")
        line = start.get("line")
        if not isinstance(path, str) or not isinstance(line, int):
            raise SemgrepGateError("Semgrep finding lacks path or line")
        findings.append(f"{path}:{line}")
    return findings


def main() -> int:
    try:
        state = lane_state(load_policy())
        if state == "NOT_APPLICABLE":
            print("correctness-semgrep: NOT_APPLICABLE for current product surface")
            return 0
        if state != "ARMED":
            raise SemgrepGateError(f"correctness-semgrep lane is not armed: {state}")
        binary = semgrep_binary()
        verify_tool(binary)
        verify_rule_controls(binary)
        findings = scan(binary)
    except SemgrepGateError as exc:
        print(f"correctness-semgrep: harness error: {exc}", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(f"correctness-semgrep: finding: {finding}", file=sys.stderr)
        return 1
    print("correctness-semgrep: controls passed and no correctness findings remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
