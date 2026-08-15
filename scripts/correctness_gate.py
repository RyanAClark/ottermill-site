#!/usr/bin/env python3
"""Refuse a repository whose six-layer correctness policy and code surface disagree."""

from __future__ import annotations

import argparse
import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence, cast


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "correctness.json"
LAYERS = (
    "runtime-contracts",
    "property-stateful-tests",
    "real-entrypoint-recovery",
    "changed-branch-coverage",
    "correctness-semgrep",
    "targeted-mutation",
)
STATES = frozenset({"ARMED", "NOT_APPLICABLE", "MIGRATION_BLOCKED"})
FIELDS = {
    "ARMED": frozenset({"state", "command", "positive_control", "negative_control"}),
    "NOT_APPLICABLE": frozenset({"state", "predicate", "reopen_on"}),
    "MIGRATION_BLOCKED": frozenset(
        {"state", "owner", "opened", "blocker", "next_action"}
    ),
}
SCAFFOLD_CODE = frozenset(
    {
        "scripts/changed_branch_gate.py",
        "scripts/correctness_evidence.py",
        "scripts/correctness_gate.py",
        "scripts/correctness_semgrep.py",
        "scripts/quality_gate.py",
        "tests/test_changed_branch_in_sync.py",
        "tests/test_correctness_policy.py",
        "tests/test_quality_gate_armed.py",
        "tests/test_quality_gate_in_sync.py",
        "semgrep/raw_text_fingerprint_fixture.py",
    }
)
JsonObject = dict[str, Any]


class GateError(ValueError):
    """The gate could not establish a valid, fully classified policy."""


def _object(value: Any, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise GateError(f"{label} must be an object")
    return cast(JsonObject, value)


def _text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise GateError(f"{label} must be non-empty text")


def _validate_layer(raw_record: Any, layer_name: str, generation: str) -> None:
    record = _object(raw_record, layer_name)
    state = record.get("state")
    if state not in STATES:
        raise GateError(f"{layer_name} has unknown state: {state!r}")
    if generation == "future" and state == "MIGRATION_BLOCKED":
        raise GateError(f"future repo cannot defer {layer_name} as migration debt")
    fields = FIELDS[cast(str, state)]
    if set(record) != set(fields):
        raise GateError(f"{layer_name} fields do not match state {state}")
    for field in fields - {"state"}:
        _text(record[field], f"{layer_name}.{field}")


def load_layers() -> tuple[str, JsonObject]:
    try:
        policy = _object(json.loads(POLICY.read_text(encoding="utf-8")), "policy")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot load correctness policy: {exc}") from exc
    if set(policy) != {"schema_version", "layers", "profiles", "repos"}:
        raise GateError("policy fields must be closed")
    if policy["schema_version"] != 1 or policy["layers"] != list(LAYERS):
        raise GateError("policy does not declare the closed six-layer schema")
    repos = _object(policy["repos"], "repos")
    if len(repos) != 1:
        raise GateError("a consumer policy must name exactly one repository")
    repo_name, raw_repo = next(iter(repos.items()))
    repo = _object(raw_repo, f"repo {repo_name}")
    if set(repo) != {"profile", "generation"}:
        raise GateError(f"repo {repo_name} fields must be closed")
    generation = repo["generation"]
    if generation not in {"existing", "future"}:
        raise GateError(f"repo {repo_name} has unknown generation: {generation!r}")
    profiles = _object(policy["profiles"], "profiles")
    profile_name = repo.get("profile")
    if not isinstance(profile_name, str) or profile_name not in profiles:
        raise GateError(f"repo {repo_name} references an unknown profile")
    layers = _object(profiles[profile_name], f"profile {profile_name}")
    if set(layers) != set(LAYERS):
        raise GateError(f"profile {profile_name} must classify all six layers")
    for layer_name in LAYERS:
        _validate_layer(layers[layer_name], layer_name, cast(str, generation))
    return repo_name, layers


def tracked_product_code() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise GateError(f"git index discovery failed: {detail}")
    gate = runpy.run_path(str(ROOT / "scripts" / "quality_gate.py"))
    extensions = cast(frozenset[str], gate["CODE_EXTENSIONS"])
    return sorted(
        path
        for path in proc.stdout.decode("utf-8", errors="surrogateescape").split("\0")
        if path
        and path.replace("\\", "/") not in SCAFFOLD_CODE
        and not path.replace("\\", "/").startswith("spikes/")
        and Path(path).suffix.lower() in extensions
    )


def check() -> list[str]:
    repo_name, layers = load_layers()
    product_code = tracked_product_code()
    if not product_code:
        return []
    unarmed = [
        layer_name
        for layer_name in LAYERS
        if _object(layers[layer_name], layer_name)["state"] != "ARMED"
    ]
    if unarmed:
        return [
            f"{repo_name}: product code {product_code} requires ARMED layers; "
            f"unarmed={unarmed}"
        ]
    return []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--tree", action="store_true")
    args = parser.parse_args(argv)
    if args.staged == args.tree:
        parser.error("choose exactly one of --staged or --tree")
    try:
        problems = check()
    except GateError as exc:
        print(f"correctness-gate: harness error: {exc}", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(f"correctness-gate: {problem}", file=sys.stderr)
        return 1
    print("correctness-gate: policy and indexed product surface agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
