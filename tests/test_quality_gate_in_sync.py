"""Pin this consumer to the canonical quality-template fingerprints."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


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


def checkout_name(root: Path) -> str:
    """The primary worktree's DIRECTORY name, stable across linked worktrees.

    Kept only as the no-remote fallback for `repo_identity`. Resolves the
    primary worktree the same way test_hook_wiring's _main_clone_root does.
    """
    dot_git = root / ".git"
    if dot_git.is_dir():
        return root.name
    pointer = dot_git.read_text(encoding="utf-8").strip()
    git_dir = Path(pointer.removeprefix("gitdir: "))
    if not git_dir.is_absolute():
        git_dir = (root / git_dir).resolve()
    common = git_dir / "commondir"
    common_dir = Path(common.read_text(encoding="utf-8").strip())
    if not common_dir.is_absolute():
        common_dir = (git_dir / common_dir).resolve()
    return common_dir.parent.name


def repo_identity(root: Path = ROOT) -> str:
    """Name the REPO, from its remote - never the directory it happens to sit in.

    Estate-audit note W1 (2026-08-12) saw half of this: keying EXCLUSIONS on a
    checkout name makes the comparison depend on what the DIRECTORY is called,
    so a mismatch silently falls to the default exclusion tuple and this test
    goes red with nothing actually wrong. W1 fixed the linked-worktree face and
    left the clone-name face, which bit the same day: claude-config has two
    clones, and the live one is `C:\\Users\\ryana\\.claude`, so this resolved to
    `.claude`, missed the EXCLUSIONS key, and reported a hand-edited ruff.toml
    that was in fact byte-correct. Both faces are one defect - identity read off
    the filesystem path - so it is read off the remote instead, which is what
    actually names the repo and is identical in every clone and worktree.

    Falls back to the checkout name only when there is no remote at all (a
    freshly scaffolded repo before `git remote add`). That path can still pick
    the default tuple for a misnamed directory; it is narrow and named here
    rather than silent.
    """
    proc = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    url = (proc.stdout or "").strip()
    if proc.returncode == 0 and url:
        return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    return checkout_name(root)


def rendered_ruff() -> bytes:
    excluded = EXCLUSIONS.get(repo_identity(), ("spikes/",))
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


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60
    )


def test_repo_identity_matches_this_checkouts_own_remote() -> None:
    """Names no repo: scripts/new_repo.py vendors this file into every consumer.

    The founding case was claude-config, which is checked out twice under two
    different names - `C:\\Users\\ryana\\.claude` (the live config directory)
    and `C:\\Users\\ryana\\repos\\claude-config`. Keying on the folder made this
    suite pass in one clone and fail in the other, which is a property of the
    folder, not of the repository.

    This assertion only has TEETH in a checkout whose folder name differs from
    its repo name; everywhere else it is satisfied either way, and the hermetic
    controls below carry the load. Stated rather than left for a reader to
    discover, because a test that cannot fail is a wish.
    """
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    url = (proc.stdout or "").strip()
    if proc.returncode != 0 or not url:
        pytest.skip("no origin remote yet (freshly scaffolded repo)")

    assert repo_identity() == url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


REMOTE_URL_FORMS = [
    ("https", "https://github.com/RyanAClark/claude-config.git"),
    ("https-no-suffix", "https://github.com/RyanAClark/claude-config"),
    ("ssh-scp", "git@github.com:RyanAClark/claude-config.git"),
    ("trailing-slash", "https://github.com/RyanAClark/claude-config/"),
]


@pytest.mark.parametrize(
    "label,url", REMOTE_URL_FORMS, ids=[row[0] for row in REMOTE_URL_FORMS]
)
def test_repo_identity_ignores_the_directory_name(label, url, tmp_path) -> None:
    """The hermetic control: a checkout deliberately named something else.

    This is the assertion that goes RED against the pre-2026-08-12 logic, which
    returned the directory name and would answer `dot-claude-lookalike` here.
    """
    checkout = tmp_path / "dot-claude-lookalike"
    checkout.mkdir()
    init = _git(["init"], checkout)
    if init.returncode != 0:
        pytest.skip("git init unavailable in this environment")
    assert _git(["remote", "add", "origin", url], checkout).returncode == 0

    assert repo_identity(checkout) == "claude-config", label


def test_repo_identity_falls_back_to_the_checkout_name_with_no_remote(
    tmp_path,
) -> None:
    """A freshly scaffolded repo has no origin yet; that path is named, not silent."""
    checkout = tmp_path / "brand-new-repo"
    checkout.mkdir()
    init = _git(["init"], checkout)
    if init.returncode != 0:
        pytest.skip("git init unavailable in this environment")

    assert repo_identity(checkout) == "brand-new-repo"
