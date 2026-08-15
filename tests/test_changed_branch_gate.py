"""Pin zero-branch files and uncovered outcomes in the shared adapter."""

from __future__ import annotations

from scripts.changed_branch_gate import branch_problems, coverage_py_evidence


def test_zero_branch_file_still_has_a_report_population() -> None:
    evidence = coverage_py_evidence(
        {
            "meta": {"branch_coverage": True},
            "files": {
                "sitecheck\\__init__.py": {
                    "executed_branches": [],
                    "missing_branches": [],
                }
            },
        }
    )

    assert branch_problems({"sitecheck/__init__.py": {1, 2}}, evidence) == []


def test_uncovered_changed_outcome_is_refused() -> None:
    evidence = coverage_py_evidence(
        {
            "meta": {"branch_coverage": True},
            "files": {
                "sitecheck/core.py": {
                    "executed_branches": [[10, 11]],
                    "missing_branches": [[10, 12]],
                }
            },
        }
    )

    assert branch_problems({"sitecheck/core.py": {10}}, evidence) == [
        "uncovered changed branch: sitecheck/core.py:10 (1/2 outcomes)"
    ]
