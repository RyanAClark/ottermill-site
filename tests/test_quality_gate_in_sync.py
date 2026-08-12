"""Pin this consumer to the canonical quality-template fingerprints."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "quality_gate.py"
EXPECTED_GATE_SHA256 = (
    "29ef6b9050966c60d1a33c0d6dfcfb0f799b358d7b46aac225ffba44c9c75fd7"
)
EXPECTED_WORKFLOW_SHA256 = (
    "1ee4fae37cf748d790001dd02b9cfa063f4b100e73da96d6d44e6c5918cb5dc7"
)
BASE_RUFF = (
    "# generated from templates/code-quality/ruff.base.toml "
    "@ v1 - do not hand-edit\n"
    "[lint]\n"
    'select = ["E", "F", "B", "BLE", "C901", "PLR0915", "N"]\n'
)
EXCLUSIONS = {
    "claude-config": (
        "spikes/",
        "skills/amazon-bedrock/",
        "skills/aws-observability/",
        "skills/aws-serverless/",
        "skills/launch-with-aws/",
    ),
    "cloud-claude-trading": ("spikes/", "docs/javascripts/"),
}


def load_quality_gate():
    name = f"quality_gate_consumer_{ROOT.name.replace('-', '_')}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def rendered_ruff() -> bytes:
    excluded = EXCLUSIONS.get(ROOT.name, ("spikes/",))
    first, rest = BASE_RUFF.split("\n", 1)
    rendered = f"{first}\nextend-exclude = {json.dumps(excluded)}\n{rest}"
    return rendered.encode("utf-8")


def canonical_bytes(path: Path) -> bytes:
    content = path.read_bytes().replace(b"\r\n", b"\n")
    assert b"\r" not in content, f"lone carriage return in {path}"
    return content


def test_vendored_checker_and_workflow_match_template_hashes() -> None:
    quality_gate = load_quality_gate()

    assert quality_gate.template_sha256(GATE) == EXPECTED_GATE_SHA256
    workflow = ROOT / ".github" / "workflows" / "quality.yml"
    assert quality_gate.template_sha256(workflow) == EXPECTED_WORKFLOW_SHA256


def test_generated_ruff_config_matches_declared_repo_deltas() -> None:
    assert canonical_bytes(ROOT / "ruff.toml") == rendered_ruff()
