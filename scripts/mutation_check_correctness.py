#!/usr/bin/env python3
"""Targeted mutation audit for Ottermill's site-validation boundary."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from mutation_file import MutationFile


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "sitecheck" / "core.py"


@dataclass(frozen=True)
class Mutation:
    group: str
    name: str
    anchor: str
    replacement: str
    test: str


MUTATIONS = (
    Mutation(
        "BUILDER",
        "accept an empty HTML population",
        "    if not result.html_files:\n",
        "    if False:\n",
        "tests/test_correctness_entrypoint.py::test_real_entrypoint_distinguishes_failure_then_recovers",
    ),
    Mutation(
        "BUILDER",
        "report a broken site as successful",
        "    if result.issues:\n",
        "    if False:\n",
        "tests/test_correctness_entrypoint.py::test_real_entrypoint_distinguishes_failure_then_recovers",
    ),
    Mutation(
        "BUILDER",
        "allow references to escape the site root",
        "    if not target.is_relative_to(resolved_root):\n",
        "    if False:\n",
        "tests/test_correctness_contracts.py::test_parent_escape_is_rejected_before_filesystem_resolution",
    ),
    Mutation(
        "INDEPENDENT",
        "ignore duplicate HTML attributes",
        "            if name in seen:\n",
        "            if False:\n",
        "tests/test_correctness_contracts.py::test_duplicate_reference_attribute_is_rejected",
    ),
    Mutation(
        "INDEPENDENT",
        "let invalid UTF-8 crash instead of becoming a contract error",
        "        except UnicodeDecodeError as error:\n",
        "        except LookupError as error:\n",
        "tests/test_correctness_contracts.py::test_invalid_utf8_is_rejected_as_a_contract_error",
    ),
    Mutation(
        "INDEPENDENT",
        "count external URLs as internal references",
        "        if not is_internal:\n",
        "        if False and not is_internal:\n",
        "tests/test_correctness_contracts.py::test_valid_utf8_page_and_external_reference_are_accepted",
    ),
)


def verdict(proc: subprocess.CompletedProcess[str], report: Path) -> str:
    if proc.returncode == 0:
        return "SURVIVED"
    if proc.returncode != 1 or not report.is_file():
        return f"HARNESS_ERROR(rc={proc.returncode})"
    root = ET.parse(report).getroot()
    errors = sum(int(suite.get("errors", "0")) for suite in root.iter("testsuite"))
    failures = sum(int(suite.get("failures", "0")) for suite in root.iter("testsuite"))
    return "CAUGHT" if failures > 0 and errors == 0 else "HARNESS_ERROR(report)"


def run_test(test: str, work: Path, sequence: int) -> tuple[str, str]:
    report = work / f"report-{sequence}.xml"
    basetemp = work / f"pytest-{sequence}"
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={report}",
            f"--basetemp={basetemp}",
            test,
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    return verdict(proc, report), proc.stdout + proc.stderr


def main() -> int:
    quality_tmp = ROOT / ".quality-tmp"
    quality_tmp.mkdir(exist_ok=True)
    results: list[tuple[Mutation, str]] = []
    with tempfile.TemporaryDirectory(
        prefix="ottermill-mutation-", dir=quality_tmp
    ) as raw:
        work = Path(raw)
        for sequence, test in enumerate(dict.fromkeys(m.test for m in MUTATIONS), 1):
            control, output = run_test(test, work, sequence)
            if control != "SURVIVED":
                print(output)
                print(f"CONTROL FAILURE: {test}: {control}")
                return 2
            print(f"CONTROL SURVIVED: {test}")

        for sequence, mutation in enumerate(MUTATIONS, len(MUTATIONS) + 1):
            result: str | None = None
            output = ""
            with MutationFile(TARGET) as target:
                target.apply(mutation.anchor, mutation.replacement)
                result, output = run_test(mutation.test, work, sequence)
            if result is None:
                raise RuntimeError(f"mutation did not run: {mutation.name}")
            results.append((mutation, result))
            print(f"{mutation.group} {mutation.name}: {result}")
            if result != "CAUGHT":
                print(output)

    for group in ("BUILDER", "INDEPENDENT"):
        selected = [result for mutation, result in results if mutation.group == group]
        caught = sum(result == "CAUGHT" for result in selected)
        errors = sum(result.startswith("HARNESS_ERROR") for result in selected)
        print(f"{group} {caught}/{len(selected)} caught; harness_errors={errors}")
    return 0 if all(result == "CAUGHT" for _, result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
