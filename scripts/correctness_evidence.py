#!/usr/bin/env python3
"""Validate live, language-native evidence for four correctness lanes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence, cast


LANES = frozenset(
    {
        "runtime-contracts",
        "property-stateful-tests",
        "real-entrypoint-recovery",
        "targeted-mutation",
    }
)
COMMON_FIELDS = frozenset(
    {"schema", "lane", "producer", "population", "harness_errors", "evidence"}
)
RUNTIME_REJECTIONS = frozenset(
    {"missing-field", "unknown-field", "wrong-type", "invalid-value"}
)
ENTRYPOINT_ROLES = frozenset({"success", "product-failure", "harness-failure"})


class EvidenceError(ValueError):
    """Evidence is malformed, incomplete, or confuses harness and product results."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceError(f"{label} must be a list")
    return cast(list[Any], value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{label} must be non-empty text")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise EvidenceError(f"{label} must be an integer >= {minimum}")
    return value


def _exact(record: Mapping[str, Any], fields: frozenset[str], label: str) -> None:
    if set(record) != set(fields):
        raise EvidenceError(f"{label} fields must be exactly {sorted(fields)}")


def _runtime(raw: Any) -> None:
    evidence = _object(raw, "runtime evidence")
    _exact(
        evidence,
        frozenset({"boundary", "positive", "rejections", "encoding_boundary"}),
        "runtime evidence",
    )
    _text(evidence["boundary"], "runtime evidence.boundary")
    positive = _object(evidence["positive"], "runtime positive control")
    _exact(positive, frozenset({"id", "accepted"}), "runtime positive control")
    _text(positive["id"], "runtime positive control.id")
    if positive["accepted"] is not True:
        raise EvidenceError("runtime positive control did not pass")
    rejection_kinds: set[str] = set()
    for index, raw_case in enumerate(_list(evidence["rejections"], "rejections")):
        case = _object(raw_case, f"rejection {index}")
        _exact(case, frozenset({"id", "kind", "rejected"}), f"rejection {index}")
        _text(case["id"], f"rejection {index}.id")
        kind = _text(case["kind"], f"rejection {index}.kind")
        if case["rejected"] is not True:
            raise EvidenceError(f"runtime rejection {kind} was accepted")
        rejection_kinds.add(kind)
    required = set(RUNTIME_REJECTIONS)
    if evidence["encoding_boundary"] is True:
        required.add("malformed-encoding")
    elif evidence["encoding_boundary"] is not False:
        raise EvidenceError("encoding_boundary must be boolean")
    missing = sorted(required - rejection_kinds)
    if missing:
        raise EvidenceError(f"runtime rejection controls missing: {missing}")


def _property(raw: Any) -> None:
    evidence = _object(raw, "property evidence")
    _exact(
        evidence,
        frozenset({"framework", "kind", "properties", "generated_cases", "replay"}),
        "property evidence",
    )
    _text(evidence["framework"], "property evidence.framework")
    if evidence["kind"] not in {"property", "stateful"}:
        raise EvidenceError("property evidence.kind must be property or stateful")
    properties = _list(evidence["properties"], "property evidence.properties")
    if not properties:
        raise EvidenceError("property evidence has zero properties")
    for index, item in enumerate(properties):
        _text(item, f"property {index}")
    _integer(evidence["generated_cases"], "generated_cases", minimum=1)
    if evidence["replay"] is not True:
        raise EvidenceError("property failure replay is not enabled")


def _entrypoint(raw: Any) -> None:
    evidence = _object(raw, "entrypoint evidence")
    _exact(evidence, frozenset({"entrypoint", "cases"}), "entrypoint evidence")
    _text(evidence["entrypoint"], "entrypoint evidence.entrypoint")
    roles: set[str] = set()
    for index, raw_case in enumerate(_list(evidence["cases"], "entrypoint cases")):
        case = _object(raw_case, f"entrypoint case {index}")
        _exact(
            case,
            frozenset({"id", "role", "verdict", "exit_code", "cleanup", "restart"}),
            f"entrypoint case {index}",
        )
        _text(case["id"], f"entrypoint case {index}.id")
        role = _text(case["role"], f"entrypoint case {index}.role")
        if role in roles:
            raise EvidenceError(f"duplicate entrypoint role: {role}")
        roles.add(role)
        exit_code = _integer(case["exit_code"], f"entrypoint case {index}.exit_code")
        if case["cleanup"] != "verified" or case["restart"] not in {
            "verified",
            "not-applicable",
        }:
            raise EvidenceError(
                f"entrypoint case {role} lacks cleanup/restart evidence"
            )
        expected = {
            "success": ("passed", 0),
            "product-failure": ("expected-refusal", None),
            "harness-failure": ("harness-error", None),
        }.get(role)
        if expected is None:
            raise EvidenceError(f"unknown entrypoint role: {role}")
        verdict, required_exit = expected
        if case["verdict"] != verdict:
            raise EvidenceError(f"entrypoint case {role} has wrong verdict")
        if required_exit is not None and exit_code != required_exit:
            raise EvidenceError("successful entrypoint control exited nonzero")
        if role != "success" and exit_code == 0:
            raise EvidenceError(
                f"entrypoint case {role} did not expose a failure channel"
            )
    if roles != set(ENTRYPOINT_ROLES):
        raise EvidenceError(f"entrypoint roles incomplete: {sorted(roles)}")


def _mutation_set(raw: Any, label: str) -> None:
    record = _object(raw, label)
    _exact(record, frozenset({"attempted", "caught", "survivors"}), label)
    attempted = _integer(record["attempted"], f"{label}.attempted", minimum=1)
    caught = _integer(record["caught"], f"{label}.caught")
    survivors = _list(record["survivors"], f"{label}.survivors")
    for index, survivor in enumerate(survivors):
        _text(survivor, f"{label}.survivors[{index}]")
    if caught + len(survivors) != attempted:
        raise EvidenceError(f"{label} counts do not reconcile")


def _mutation(raw: Any) -> None:
    evidence = _object(raw, "mutation evidence")
    _exact(
        evidence, frozenset({"control", "builder", "independent"}), "mutation evidence"
    )
    if evidence["control"] != "survived":
        raise EvidenceError("unmutated mutation control did not survive")
    _mutation_set(evidence["builder"], "builder mutation set")
    _mutation_set(evidence["independent"], "independent mutation set")


VALIDATORS = {
    "runtime-contracts": _runtime,
    "property-stateful-tests": _property,
    "real-entrypoint-recovery": _entrypoint,
    "targeted-mutation": _mutation,
}


def validate(raw: Any, expected_lane: str) -> None:
    record = _object(raw, "evidence")
    _exact(record, COMMON_FIELDS, "evidence")
    if record["schema"] != "correctness-evidence/v1":
        raise EvidenceError("unknown correctness evidence schema")
    if record["lane"] != expected_lane or expected_lane not in LANES:
        raise EvidenceError("evidence lane does not match the requested lane")
    _text(record["producer"], "evidence.producer")
    _integer(record["population"], "evidence.population", minimum=1)
    harness_errors = _list(record["harness_errors"], "evidence.harness_errors")
    if harness_errors:
        raise EvidenceError(f"harness errors are not empty: {harness_errors!r}")
    VALIDATORS[expected_lane](record["evidence"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", required=True, choices=sorted(LANES))
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
    # Product failure and harness failure are DIFFERENT channels, and this
    # script's own `real-entrypoint-recovery` lane requires callers to tell them
    # apart. Until 2026-08-18 both exited 2 printing "harness error", so
    # "your evidence is incomplete" was indistinguishable from "I could not read
    # the file" — the shape this gate exists to refuse, inside the gate itself.
    # Sibling `changed_branch_gate.py` already splits them the same way.
    try:
        raw = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"correctness-evidence: harness error: {exc}", file=sys.stderr)
        return 2
    try:
        validate(raw, args.lane)
    except EvidenceError as exc:
        print(f"correctness-evidence: incomplete evidence: {exc}", file=sys.stderr)
        return 1
    print(f"correctness-evidence: {args.lane} evidence is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
