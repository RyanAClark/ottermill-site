"""Pin this consumer to the canonical quality-template fingerprints."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "quality_gate.py"
EXPECTED_GATE_SHA256 = (
    "de39548d0453fee135dfc7ab00320d476ef9f3f9d10f41d4f07a128e6aa153e1"
)
# Re-pinned 2026-08-18 (roadmap row 156): the estate-wide correctness census was
# removed from the workflow because a runner checks out one repo and the census
# scans fifteen, so it failed there on every push and CI never reached the steps
# after it. The census itself is unchanged and still runs from .githooks/pre-commit
# on the estate machine. Re-pin this deliberately, in the same change as the edit -
# that is what makes an ACCIDENTAL workflow edit visible.
EXPECTED_WORKFLOW_SHA256 = (
    "553a9b16e24af62d6d4b2cbdf9160c27ed52fe364d2b6c579fac037243cc2864"
)
# pyrightconfig.json is RENDERED per repo, exactly as ruff.toml is: one base plus a
# declared delta. It used to be pinned byte-for-byte across the estate, so a repo
# whose importable modules do not all live under scripts/ had no way to say so
# without loosening the gate everywhere. Ryan chose the per-repo delta, 2026-08-18.
BASE_PYRIGHT: dict[str, object] = {
    "typeCheckingMode": "strict",
    "extraPaths": ["scripts"],
    "useLibraryCodeForTypes": True,
}
PYRIGHT_DELTAS: dict[str, dict[str, object]] = {
    "claude-config": {
        # hooks/ holds importable modules (outcome.py and friends) that the suite
        # imports directly. Unresolved, every symbol they reach becomes Unknown,
        # which strict mode reports as ~120 errors that say nothing about the code.
        # The repo root ("."), added 2026-08-19, is the same defect one import
        # form over: tests that import `from scripts.X import ...` (the package
        # form, not the extraPath form) resolved to nothing, so strict mode
        # reported Unknown for every symbol they reached. DECLARED HERE, not
        # hand-edited into pyrightconfig.json - that file is RENDERED from this
        # declaration and the test below asserts byte equality, so an in-place
        # edit desyncs the repo instead of fixing it (audit finding F2).
        "extraPaths": [".", "scripts", "hooks"],
        # A test suite reaching into a module's internals is what these tests are
        # FOR: they pin private seams deliberately. Renaming internals to satisfy a
        # checker would be the wrong direction.
        "executionEnvironments": [{"root": "tests", "reportPrivateUsage": "none"}],
    },
}
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
        "tests/fixtures/language-routing/",
    ),
    "cloud-claude-trading": ("spikes/", "docs/javascripts/"),
}


class QualityGateModule(Protocol):
    def template_sha256(self, path: Path) -> str: ...


def load_quality_gate() -> QualityGateModule:
    name = f"quality_gate_consumer_{ROOT.name.replace('-', '_')}"
    if name in sys.modules:
        return cast(QualityGateModule, sys.modules[name])
    spec = importlib.util.spec_from_file_location(name, GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return cast(QualityGateModule, module)


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


def repo_name_from_remote_url(url: str) -> str:
    """The repository name in `url`, whatever separator or scheme it uses.

    THE ONE HOME FOR THIS PARSE. It lives in this module, not in `scripts/`,
    because `scripts/new_repo.py` copies this file verbatim into every scaffolded
    repo, where nothing under `scripts/` of ours is importable - a helper module
    made the scaffolded copy fail to collect, which the standalone control in
    `tests/test_new_repo_quality_scaffold.py` caught immediately.

    Handles every form this estate produces: `https://host/owner/repo.git`,
    `git@host:owner/repo.git`, a trailing slash, and a local path in POSIX or
    Windows spelling. Raises on an empty string rather than returning one,
    because an empty identity silently matches no table.

    THE WINDOWS-PATH FORM IS THE THIRD FACE OF ONE DEFECT, and the first two are
    named in `repo_identity` below. Both earlier fixes moved identity off the
    filesystem path and onto the remote - correct, and incomplete, because a
    build clone made by `scripts/build_root.py` has a LOCAL PATH as its origin.
    Splitting on `/` alone returned the whole `C:\\Users\\...` path, matched no
    EXCLUSIONS key, and reported a byte-correct `ruff.toml` as hand-edited.
    Measured 2026-08-13 in the row 106 build clone.
    """
    cleaned = url.strip().replace("\\", "/").rstrip("/")
    if not cleaned:
        raise ValueError("cannot read a repository name from an empty remote url")
    return cleaned.rsplit("/", 1)[-1].removesuffix(".git")


def origin_url(root: Path = ROOT, timeout: int = 10) -> str | None:
    """`origin`'s URL for the checkout at `root`, or None when there is no remote."""
    proc = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    url = (proc.stdout or "").strip()
    if proc.returncode != 0 or not url:
        return None
    return url


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
    url = origin_url(root)
    if url:
        return repo_name_from_remote_url(url)
    return checkout_name(root)


def _pyright_json(value: object, indent: int = 0) -> str:
    """Serialise the way the checked-in configs are already written.

    Objects expand one key per line at two spaces; arrays of scalars stay inline.
    This is not cosmetic: a repo that declares NO delta has to render byte-for-byte
    identical to the file already committed there, or syncing this test breaks
    every consumer at once.
    """
    pad = " " * indent
    inner = " " * (indent + 2)
    if isinstance(value, dict):
        items = cast("dict[str, object]", value).items()
        body = ",\n".join(
            f"{inner}{json.dumps(k)}: {_pyright_json(v, indent + 2)}" for k, v in items
        )
        return "{\n" + body + "\n" + pad + "}"
    if isinstance(value, list):
        entries = cast("list[object]", value)
        if all(isinstance(e, (str, int, float, bool)) for e in entries):
            return json.dumps(entries)
        body = ",\n".join(f"{inner}{_pyright_json(e, indent + 2)}" for e in entries)
        return "[\n" + body + "\n" + pad + "]"
    return json.dumps(value)


def rendered_pyright() -> bytes:
    config = dict(BASE_PYRIGHT)
    config.update(PYRIGHT_DELTAS.get(repo_identity(), {}))
    return (_pyright_json(config) + "\n").encode("utf-8")


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


def test_generated_pyright_config_matches_declared_repo_deltas() -> None:
    assert canonical_bytes(ROOT / "pyrightconfig.json") == rendered_pyright()


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
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
    url = origin_url(ROOT)
    if url is None:
        pytest.skip("no origin remote yet (freshly scaffolded repo)")

    assert repo_identity() == repo_name_from_remote_url(url)


REMOTE_URL_FORMS = [
    ("https", "https://github.com/RyanAClark/claude-config.git"),
    ("https-no-suffix", "https://github.com/RyanAClark/claude-config"),
    ("ssh-scp", "git@github.com:RyanAClark/claude-config.git"),
    ("trailing-slash", "https://github.com/RyanAClark/claude-config/"),
    # A build clone made by scripts/build_root.py has a LOCAL path as its origin,
    # in Windows spelling. Every such clone resolved to the whole path before
    # 2026-08-13, matched no EXCLUSIONS key, and went red for nothing.
    ("local-windows-path", r"C:\Users\ryana\repos\claude-config"),
    ("local-posix-path", "/c/Users/ryana/repos/claude-config"),
]


@pytest.mark.parametrize(
    "label,url", REMOTE_URL_FORMS, ids=[row[0] for row in REMOTE_URL_FORMS]
)
def test_repo_identity_ignores_the_directory_name(
    label: str, url: str, tmp_path: Path
) -> None:
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
    tmp_path: Path,
) -> None:
    """A freshly scaffolded repo has no origin yet; that path is named, not silent."""
    checkout = tmp_path / "brand-new-repo"
    checkout.mkdir()
    init = _git(["init"], checkout)
    if init.returncode != 0:
        pytest.skip("git init unavailable in this environment")

    assert repo_identity(checkout) == "brand-new-repo"
