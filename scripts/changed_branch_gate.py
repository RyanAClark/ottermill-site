#!/usr/bin/env python3
"""Require every branch outcome originating on changed lines to execute."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence, cast


ROOT = Path(__file__).resolve().parents[1]
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
CONDITION = re.compile(r"\((\d+)\s*/\s*(\d+)\)")
ADAPTER_EXTENSIONS = {
    "coverage-py-json": frozenset({".py", ".pyi"}),
    "istanbul-json": frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}),
    "cobertura-xml": frozenset({".cs", ".fs", ".vb"}),
}


class BranchGateError(ValueError):
    """Coverage evidence or Git selection is missing, stale, or malformed."""


@dataclass(frozen=True)
class BranchEvidence:
    path: str
    line: int
    covered: int
    total: int


def normalized_path(value: str) -> str:
    normalized = PurePosixPath(value.replace("\\", "/")).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def changed_lines(diff: str) -> dict[str, set[int]]:
    changed: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = normalized_path(line[6:]) if line.startswith("+++ b/") else None
            if current is not None:
                changed.setdefault(current, set())
            continue
        match = HUNK.match(line)
        if not match or current is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        changed[current].update(range(start, start + count))
    return {path: lines for path, lines in changed.items() if lines}


def git_diff_args(base: str | None, staged: bool) -> list[str]:
    if staged:
        return ["git", "diff", "--cached", "--unified=0", "--no-ext-diff", "--"]
    if not base:
        raise BranchGateError("CI mode requires a non-empty base revision")
    return ["git", "diff", "--unified=0", "--no-ext-diff", f"{base}...HEAD", "--"]


def git_changed_lines(base: str | None, staged: bool = False) -> dict[str, set[int]]:
    proc = subprocess.run(
        git_diff_args(base, staged),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        target = "staged index" if staged else f"base {base!r}"
        raise BranchGateError(f"cannot select changed lines from {target}: {detail}")
    return changed_lines(proc.stdout.decode("utf-8", errors="replace"))


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BranchGateError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def coverage_py_evidence(raw: Mapping[str, Any]) -> list[BranchEvidence]:
    meta = _object(raw.get("meta"), "coverage.py meta")
    if meta.get("branch_coverage") is not True:
        raise BranchGateError(
            "coverage.py report was not produced with branch coverage"
        )
    files = _object(raw.get("files"), "coverage.py files")
    evidence: list[BranchEvidence] = []
    for raw_path, raw_file in files.items():
        file_record = _object(raw_file, f"coverage.py file {raw_path}")
        evidence.append(BranchEvidence(normalized_path(raw_path), -1, 1, 1))
        executed = file_record.get("executed_branches")
        missing = file_record.get("missing_branches")
        if not isinstance(executed, list) or not isinstance(missing, list):
            raise BranchGateError(
                f"coverage.py file {raw_path} lacks branch populations"
            )
        executed_rows = cast(list[Any], executed)
        missing_rows = cast(list[Any], missing)
        by_line: dict[int, list[int]] = {}
        for rows, covered in ((executed_rows, 1), (missing_rows, 0)):
            for branch in rows:
                if not isinstance(branch, list):
                    raise BranchGateError(
                        f"coverage.py file {raw_path} has malformed branch"
                    )
                typed_branch = cast(list[Any], branch)
                if len(typed_branch) != 2:
                    raise BranchGateError(
                        f"coverage.py file {raw_path} has malformed branch"
                    )
                origin = typed_branch[0]
                if not isinstance(origin, int):
                    raise BranchGateError(
                        f"coverage.py file {raw_path} has non-integer line"
                    )
                by_line.setdefault(origin, []).append(covered)
        for line, outcomes in by_line.items():
            evidence.append(
                BranchEvidence(
                    normalized_path(raw_path), line, sum(outcomes), len(outcomes)
                )
            )
    return evidence


def istanbul_evidence(raw: Mapping[str, Any]) -> list[BranchEvidence]:
    evidence: list[BranchEvidence] = []
    for raw_path, raw_file in raw.items():
        file_record = _object(raw_file, f"Istanbul file {raw_path}")
        branch_map = _object(
            file_record.get("branchMap"), f"Istanbul {raw_path}.branchMap"
        )
        counts = _object(file_record.get("b"), f"Istanbul {raw_path}.b")
        if set(branch_map) != set(counts):
            raise BranchGateError(
                f"Istanbul file {raw_path} branch IDs do not match counts"
            )
        for branch_id, raw_branch in branch_map.items():
            branch = _object(raw_branch, f"Istanbul {raw_path} branch {branch_id}")
            locations = branch.get("locations")
            outcomes = counts[branch_id]
            if not isinstance(locations, list) or not isinstance(outcomes, list):
                raise BranchGateError(
                    f"Istanbul file {raw_path} has malformed branch {branch_id}"
                )
            typed_locations = cast(list[Any], locations)
            typed_outcomes = cast(list[Any], outcomes)
            if len(typed_locations) != len(typed_outcomes) or not typed_locations:
                raise BranchGateError(
                    f"Istanbul file {raw_path} has incomplete branch {branch_id}"
                )
            first = _object(typed_locations[0], f"Istanbul {raw_path} location")
            start = _object(first.get("start"), f"Istanbul {raw_path} location start")
            line = start.get("line")
            if not isinstance(line, int) or any(
                not isinstance(value, int) for value in typed_outcomes
            ):
                raise BranchGateError(
                    f"Istanbul file {raw_path} has invalid branch counts"
                )
            integer_outcomes = cast(list[int], typed_outcomes)
            evidence.append(
                BranchEvidence(
                    normalized_path(raw_path),
                    line,
                    sum(value > 0 for value in integer_outcomes),
                    len(integer_outcomes),
                )
            )
    return evidence


def cobertura_evidence(root: ET.Element) -> list[BranchEvidence]:
    evidence: list[BranchEvidence] = []
    for class_node in root.findall(".//class"):
        raw_path = class_node.get("filename")
        if not raw_path:
            raise BranchGateError("Cobertura class lacks filename")
        for line_node in class_node.findall("./lines/line[@branch='true']"):
            number = line_node.get("number")
            condition = line_node.get("condition-coverage")
            match = CONDITION.search(condition or "")
            if not number or not number.isdigit() or not match:
                raise BranchGateError(
                    f"Cobertura file {raw_path} has malformed branch line"
                )
            evidence.append(
                BranchEvidence(
                    normalized_path(raw_path),
                    int(number),
                    int(match.group(1)),
                    int(match.group(2)),
                )
            )
    return evidence


def load_evidence(adapter: str, report: Path) -> list[BranchEvidence]:
    try:
        if adapter == "cobertura-xml":
            return cobertura_evidence(ET.parse(report).getroot())
        raw = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ET.ParseError) as exc:
        raise BranchGateError(f"cannot load coverage report {report}: {exc}") from exc
    root = _object(raw, f"{adapter} report")
    if adapter == "coverage-py-json":
        return coverage_py_evidence(root)
    if adapter == "istanbul-json":
        return istanbul_evidence(root)
    raise BranchGateError(f"unknown branch adapter: {adapter}")


def branch_problems(
    changed: Mapping[str, set[int]], evidence: Iterable[BranchEvidence]
) -> list[str]:
    by_path: dict[str, list[BranchEvidence]] = {}
    for item in evidence:
        by_path.setdefault(normalized_path(item.path), []).append(item)
    problems: list[str] = []
    evidence_paths = set(by_path)
    for path, lines in sorted(changed.items()):
        normalized = normalized_path(path)
        matching_paths = [
            candidate for candidate in evidence_paths if candidate.endswith(normalized)
        ]
        if not matching_paths:
            problems.append(f"changed file has no branch report entry: {normalized}")
            continue
        obligations = [
            item
            for candidate in matching_paths
            for item in by_path[candidate]
            if item.line in lines
        ]
        for item in obligations:
            if item.total <= 0 or item.covered < 0 or item.covered > item.total:
                problems.append(f"invalid branch population: {normalized}:{item.line}")
            elif item.covered != item.total:
                problems.append(
                    f"uncovered changed branch: {normalized}:{item.line} "
                    f"({item.covered}/{item.total} outcomes)"
                )
    return problems


def applicable_changed(
    changed: Mapping[str, set[int]], adapter: str, include_roots: Sequence[str] = ()
) -> dict[str, set[int]]:
    extensions = ADAPTER_EXTENSIONS.get(adapter)
    if extensions is None:
        raise BranchGateError(f"unknown branch adapter: {adapter}")
    roots = tuple(
        normalized_path(root).rstrip("/") + "/"
        for root in include_roots
        if root.strip()
    )
    return {
        path: lines
        for path, lines in changed.items()
        if PurePosixPath(path).suffix.lower() in extensions
        and (not roots or normalized_path(path).startswith(roots))
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--base")
    source.add_argument("--staged", action="store_true")
    parser.add_argument(
        "--adapter",
        required=True,
        choices=("coverage-py-json", "istanbul-json", "cobertura-xml"),
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--include-root", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        changed = applicable_changed(
            git_changed_lines(args.base, staged=args.staged),
            args.adapter,
            args.include_root,
        )
        evidence = load_evidence(args.adapter, args.report)
        problems = branch_problems(changed, evidence)
    except BranchGateError as exc:
        print(f"changed-branch-gate: harness error: {exc}", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(f"changed-branch-gate: {problem}", file=sys.stderr)
        return 1
    print(f"changed-branch-gate: covered changed branch outcomes; files={len(changed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
