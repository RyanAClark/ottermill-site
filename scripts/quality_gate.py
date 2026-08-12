#!/usr/bin/env python3
"""Refuse new code-quality debt while grandfathering unchanged Python debt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Sequence


TEMPLATE_VERSION = 1
REQUIRED_RUFF = "0.16.2"
RUFF_BOOTSTRAP = 'python -m pip install "ruff==0.16.2"'
A5_RECOVERY = (
    "quality-gate: possible rename below Git's similarity threshold; "
    "split the rename from the edit into two commits."
)
FUNCTION_CODES = frozenset({"C901", "PLR0915"})
CODE_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".cjs",
        ".go",
        ".java",
        ".js",
        ".jsx",
        ".mjs",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".swift",
        ".ts",
        ".tsx",
    }
)


class GateError(RuntimeError):
    """A fail-closed refusal with user-facing recovery text."""


class BrokenCheckError(GateError):
    """The checker failed, so a clean result cannot be claimed."""


def template_sha256(path: Path) -> str:
    """Hash template bytes after canonicalizing CRLF line endings to LF."""
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    before_path: str | None


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    code: str
    message: str
    url: str
    text: str
    metric: int | None = None
    previous_metric: int | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.path, self.code, self.text)


@dataclass(frozen=True)
class Language:
    extensions: frozenset[str]
    baseline: str
    check: Callable[..., int]
    probe: Callable[..., Sequence[str]]


def _command(
    args: Sequence[str],
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    proc = _command(("git", *args), cwd=repo, input_bytes=input_bytes)
    if proc.returncode != 0:
        detail = _text(proc.stderr).strip() or _text(proc.stdout).strip()
        raise BrokenCheckError(
            f"quality-gate: BROKEN CHECK: git {' '.join(args)}\n{detail}"
        )
    return proc.stdout


def _repo_root(cwd: Path) -> Path:
    proc = _command(("git", "rev-parse", "--show-toplevel"), cwd=cwd)
    if proc.returncode != 0:
        raise BrokenCheckError(
            "quality-gate: BROKEN CHECK: current directory is not a git repo"
        )
    return Path(_text(proc.stdout).strip()).resolve()


def _parse_changes(payload: bytes) -> list[Change]:
    fields = payload.decode("utf-8", errors="surrogateescape").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changes: list[Change] = []
    cursor = 0
    while cursor < len(fields):
        status = fields[cursor]
        cursor += 1
        if status.startswith(("R", "C")):
            if cursor + 1 >= len(fields):
                raise BrokenCheckError(
                    "quality-gate: BROKEN CHECK: malformed rename record"
                )
            source, target = fields[cursor : cursor + 2]
            cursor += 2
            changes.append(Change(status[0], target, source))
        else:
            if cursor >= len(fields):
                raise BrokenCheckError(
                    "quality-gate: BROKEN CHECK: malformed change record"
                )
            path = fields[cursor]
            cursor += 1
            changes.append(
                Change(status[:1], path, None if status[:1] == "A" else path)
            )
    return changes


def _staged_changes(repo: Path) -> list[Change]:
    return _parse_changes(
        _git(
            repo,
            "diff",
            "--cached",
            "-M",
            "--diff-filter=ACMR",
            "--name-status",
            "-z",
        )
    )


def _commit_exists(repo: Path, revision: str) -> bool:
    proc = _command(
        ("git", "rev-parse", "--verify", f"{revision}^{{commit}}"), cwd=repo
    )
    return proc.returncode == 0


def _tree_base(repo: Path) -> str:
    explicit = os.environ.get("QUALITY_GATE_BASE", "").strip()
    if explicit and explicit != "0" * 40:
        if not _commit_exists(repo, explicit):
            raise BrokenCheckError(
                f"quality-gate: BROKEN CHECK: invalid QUALITY_GATE_BASE {explicit}"
            )
        return explicit
    base_ref = os.environ.get("GITHUB_BASE_REF", "").strip()
    if base_ref:
        raw = _git(repo, "merge-base", "HEAD", f"origin/{base_ref}")
        return _text(raw).strip()
    before = os.environ.get("GITHUB_EVENT_BEFORE", "").strip()
    if before and before != "0" * 40 and _commit_exists(repo, before):
        return before
    if _commit_exists(repo, "HEAD^"):
        return "HEAD^"
    return _text(
        _git(repo, "hash-object", "-t", "tree", "--stdin", input_bytes=b"")
    ).strip()


def _tree_changes(repo: Path) -> list[Change]:
    base = _tree_base(repo)
    return _parse_changes(
        _git(
            repo,
            "diff",
            "-M",
            "--diff-filter=ACMR",
            "--name-status",
            "-z",
            base,
            "HEAD",
        )
    )


def _read_blob(repo: Path, revision: str, path: str) -> bytes:
    object_name = f":{path}" if revision == ":" else f"{revision}:{path}"
    return _git(repo, "cat-file", "blob", object_name)


def _load_exclusions(repo: Path) -> tuple[str, ...]:
    config = repo / "ruff.toml"
    if not config.is_file():
        return ()
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise BrokenCheckError(
            f"quality-gate: BROKEN CHECK: cannot parse ruff.toml: {exc}"
        ) from exc
    values = data.get("extend-exclude", [])
    if not isinstance(values, list) or not all(
        isinstance(item, str) for item in values
    ):
        raise BrokenCheckError(
            "quality-gate: BROKEN CHECK: extend-exclude must be a string list"
        )
    return tuple(value.replace("\\", "/").strip("/") for value in values)


def _is_excluded(path: str, exclusions: Iterable[str]) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    for exclusion in exclusions:
        if normalized == exclusion or normalized.startswith(exclusion + "/"):
            return True
    return False


def _language_for(path: str) -> str | None:
    extension = PurePosixPath(path).suffix.lower()
    for name, language in LANGUAGES.items():
        if extension in language.extensions:
            return name
    if extension in CODE_EXTENSIONS:
        raise GateError(
            f"quality-gate: unregistered code extension {extension} for {path}; "
            "add a LANGUAGES registry entry"
        )
    return None


def _scope(repo: Path, changes: Sequence[Change]) -> dict[str, list[Change]]:
    exclusions = _load_exclusions(repo)
    scoped: dict[str, list[Change]] = defaultdict(list)
    for change in changes:
        if _is_excluded(change.path, exclusions):
            continue
        language = _language_for(change.path)
        if language is not None:
            scoped[language].append(change)
    return dict(scoped)


def _ruff_candidates() -> list[list[str]]:
    candidates: list[list[str]] = []
    try:
        from ruff.__main__ import find_ruff_bin

        binary = Path(find_ruff_bin())
        if binary.is_file():
            candidates.append([str(binary)])
    except (ImportError, OSError):
        pass
    candidates.append([sys.executable, "-m", "ruff"])
    on_path = shutil.which("ruff")
    if on_path:
        candidates.append([on_path])
    unique: list[list[str]] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _resolve_ruff(repo: Path) -> Sequence[str]:
    mismatch: str | None = None
    for candidate in _ruff_candidates():
        try:
            proc = _command((*candidate, "--version"), cwd=repo)
        except OSError:
            continue
        if proc.returncode != 0:
            continue
        version = _text(proc.stdout).strip()
        if version == f"ruff {REQUIRED_RUFF}":
            return candidate
        mismatch = version or "unknown version"
    if mismatch is not None:
        raise GateError(
            f"quality-gate: requires ruff {REQUIRED_RUFF}, found {mismatch}; "
            f"run: {RUFF_BOOTSTRAP}"
        )
    raise GateError(
        f"quality-gate: ruff {REQUIRED_RUFF} is required for in-scope files; "
        f"run: {RUFF_BOOTSTRAP}"
    )


def _run_ruff(
    ruff: Sequence[str], repo: Path, args: Sequence[str]
) -> subprocess.CompletedProcess[bytes]:
    try:
        return _command((*ruff, *args), cwd=repo)
    except OSError as exc:
        raise BrokenCheckError(
            f"quality-gate: BROKEN CHECK: ruff could not start: {exc}"
        ) from exc


def _ruff_checked(
    ruff: Sequence[str],
    repo: Path,
    args: Sequence[str],
    allowed: frozenset[int],
) -> subprocess.CompletedProcess[bytes]:
    proc = _run_ruff(ruff, repo, args)
    if proc.returncode not in allowed:
        output = (_text(proc.stdout) + _text(proc.stderr)).strip()
        raise BrokenCheckError(
            f"quality-gate: BROKEN CHECK: ruff exited {proc.returncode}\n{output}"
        )
    return proc


def _write_blob(root: Path, relative: str, content: bytes) -> Path:
    path = root / Path(*PurePosixPath(relative).parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _materialize_python(
    repo: Path,
    changes: Sequence[Change],
    after_revision: str,
    before_revision: str,
    before_root: Path,
    after_root: Path,
) -> tuple[dict[Path, str], dict[Path, str], set[str]]:
    before_map: dict[Path, str] = {}
    after_map: dict[Path, str] = {}
    added: set[str] = set()
    for change in changes:
        after = _write_blob(
            after_root, change.path, _read_blob(repo, after_revision, change.path)
        )
        after_map[after.resolve()] = change.path
        if change.before_path is None:
            added.add(change.path)
            continue
        before = _write_blob(
            before_root,
            change.path,
            _read_blob(repo, before_revision, change.before_path),
        )
        before_map[before.resolve()] = change.path
    return before_map, after_map, added


def _path_args(paths: Iterable[Path]) -> list[str]:
    return [str(path) for path in sorted(paths, key=lambda item: str(item).lower())]


def _assert_format_clean(
    ruff: Sequence[str], repo: Path, config: Path, path_map: dict[Path, str]
) -> None:
    paths = _path_args(path_map)
    proc = _ruff_checked(
        ruff,
        repo,
        ("format", "--check", "--no-cache", "--config", str(config), *paths),
        frozenset({0, 1}),
    )
    if proc.returncode == 0:
        return
    with tempfile.TemporaryDirectory(prefix="quality-format-") as raw:
        formatted_root = Path(raw)
        formatted: dict[Path, Path] = {}
        for source, relative in path_map.items():
            target = formatted_root / Path(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            formatted[target] = source
        _ruff_checked(
            ruff,
            repo,
            (
                "format",
                "--no-cache",
                "--config",
                str(config),
                *_path_args(formatted),
            ),
            frozenset({0}),
        )
        dirty = [
            path_map[source]
            for target, source in formatted.items()
            if target.read_bytes() != source.read_bytes()
        ]
    names = " ".join(sorted(dirty)) if dirty else " ".join(sorted(path_map.values()))
    raise GateError(
        "quality-gate: formatter-clean-on-touch refusal for "
        f"{names}\nrun: python -m ruff format {names}"
    )


def _canonicalize_before(
    ruff: Sequence[str], repo: Path, config: Path, paths: Iterable[Path]
) -> None:
    selected = _path_args(paths)
    if not selected:
        return
    _ruff_checked(
        ruff,
        repo,
        ("format", "--no-cache", "--config", str(config), *selected),
        frozenset({0}),
    )


def _metric(code: str, message: str) -> int | None:
    if code not in FUNCTION_CODES:
        return None
    match = re.search(r"\d+", message)
    if not match:
        raise BrokenCheckError(
            f"quality-gate: BROKEN CHECK: cannot parse {code} metric from: {message}"
        )
    return int(match.group(0))


def _violations(
    ruff: Sequence[str], repo: Path, config: Path, path_map: dict[Path, str]
) -> list[Violation]:
    if not path_map:
        return []
    proc = _ruff_checked(
        ruff,
        repo,
        (
            "check",
            "--output-format",
            "json",
            "--no-cache",
            "--config",
            str(config),
            *_path_args(path_map),
        ),
        frozenset({0, 1}),
    )
    try:
        records = json.loads(_text(proc.stdout))
    except json.JSONDecodeError as exc:
        raise BrokenCheckError(
            f"quality-gate: BROKEN CHECK: invalid ruff JSON: {exc}"
        ) from exc
    if not isinstance(records, list):
        raise BrokenCheckError(
            "quality-gate: BROKEN CHECK: ruff JSON root is not a list"
        )
    lookup = {
        str(path.resolve()).lower(): relative for path, relative in path_map.items()
    }
    results: list[Violation] = []
    for record in records:
        try:
            raw_path = Path(record["filename"]).resolve()
            relative = lookup[str(raw_path).lower()]
            code = record["code"]
            message = record["message"]
            line = int(record["location"]["row"])
            url = (
                record.get("url") or f"https://docs.astral.sh/ruff/rules/{code.lower()}"
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BrokenCheckError(
                f"quality-gate: BROKEN CHECK: malformed ruff record: {record}"
            ) from exc
        lines = raw_path.read_text(encoding="utf-8", errors="replace").splitlines()
        source = lines[line - 1].strip() if 0 < line <= len(lines) else ""
        results.append(
            Violation(
                relative, line, code, message, url, source, _metric(code, message)
            )
        )
    return results


def _new_violations(
    before: Sequence[Violation], after: Sequence[Violation]
) -> tuple[list[Violation], int]:
    before_groups: dict[tuple[str, str, str], list[Violation]] = defaultdict(list)
    after_groups: dict[tuple[str, str, str], list[Violation]] = defaultdict(list)
    for violation in before:
        before_groups[violation.key].append(violation)
    for violation in after:
        after_groups[violation.key].append(violation)
    new: list[Violation] = []
    legacy = 0
    for key, after_items in after_groups.items():
        before_items = before_groups.get(key, [])
        legacy += min(len(before_items), len(after_items))
        if key[1] not in FUNCTION_CODES:
            new.extend(after_items[len(before_items) :])
            continue
        old_metrics = sorted(item.metric or 0 for item in before_items)
        current = sorted(after_items, key=lambda item: item.metric or 0)
        for index, item in enumerate(current):
            if index >= len(old_metrics):
                new.append(item)
            elif (item.metric or 0) > old_metrics[index]:
                new.append(
                    Violation(
                        item.path,
                        item.line,
                        item.code,
                        item.message,
                        item.url,
                        item.text,
                        item.metric,
                        old_metrics[index],
                    )
                )
    return sorted(new, key=lambda item: (item.path, item.line, item.code)), legacy


def _print_refusal(new: Sequence[Violation], legacy: int, added: set[str]) -> None:
    for violation in new:
        suffix = ""
        if violation.previous_metric is not None:
            suffix = f" - was {violation.previous_metric} at HEAD"
        print(
            f"{violation.path}:{violation.line}  {violation.code}  "
            f"{violation.message}{suffix}  {violation.url}"
        )
    noun = "violation" if len(new) == 1 else "violations"
    print(f"quality-gate: {len(new)} new {noun}; {legacy} pre-existing not blocking")
    if added.intersection(item.path for item in new):
        print(A5_RECOVERY)


def _python_check(repo: Path, changes: Sequence[Change], mode: str) -> int:
    ruff = _resolve_ruff(repo)
    config = repo / "ruff.toml"
    if not config.is_file():
        raise GateError(
            "quality-gate: ruff.toml is missing; restore the generated config"
        )
    before_revision = ":" if mode == "staged" else _tree_base(repo)
    after_revision = ":" if mode == "staged" else "HEAD"
    with tempfile.TemporaryDirectory(prefix="quality-gate-") as raw:
        root = Path(raw)
        before_map, after_map, added = _materialize_python(
            repo,
            changes,
            after_revision,
            "HEAD" if mode == "staged" else before_revision,
            root / "before",
            root / "after",
        )
        _assert_format_clean(ruff, repo, config, after_map)
        _canonicalize_before(ruff, repo, config, before_map)
        old = _violations(ruff, repo, config, before_map)
        current = _violations(ruff, repo, config, after_map)
        new, legacy = _new_violations(old, current)
    if new:
        _print_refusal(new, legacy, added)
        return 1
    return 0


def _resolve_tsc(repo: Path) -> Sequence[str]:
    local = repo / "node_modules" / ".bin" / ("tsc.cmd" if os.name == "nt" else "tsc")
    if local.is_file():
        return (str(local),)
    raise GateError(
        "quality-gate: TypeScript is required for in-scope files; run: npm ci"
    )


def _typescript_check(repo: Path, changes: Sequence[Change], mode: str) -> int:
    del changes, mode
    tsc = _resolve_tsc(repo)
    proc = _command((*tsc, "--noEmit", "-p", "tsconfig.json"), cwd=repo)
    if proc.returncode != 0:
        output = (_text(proc.stdout) + _text(proc.stderr)).strip()
        print(f"quality-gate: TypeScript strict check refused\n{output}")
        return 1
    return 0


LANGUAGES: dict[str, Language] = {
    "python": Language(frozenset({".py"}), "RATCHET", _python_check, _resolve_ruff),
    "typescript": Language(
        frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}),
        "ABSOLUTE_ZERO",
        _typescript_check,
        _resolve_tsc,
    ),
}


def _fix(repo: Path, changes: Sequence[Change]) -> int:
    if not changes:
        print("quality-gate: no Python files in enforced scope staged - nothing to fix")
        return 0
    ruff = _resolve_ruff(repo)
    config = repo / "ruff.toml"
    paths = [change.path for change in changes]
    residual = ""
    for _pass in range(2):
        _ruff_checked(
            ruff,
            repo,
            ("format", "--no-cache", "--config", str(config), *paths),
            frozenset({0}),
        )
        _ruff_checked(
            ruff,
            repo,
            ("check", "--fix", "--no-cache", "--config", str(config), *paths),
            frozenset({0, 1}),
        )
        check = _ruff_checked(
            ruff,
            repo,
            ("check", "--no-cache", "--config", str(config), *paths),
            frozenset({0, 1}),
        )
        if check.returncode == 0:
            print(f"quality-gate: fixed {len(paths)} Python files")
            return 0
        residual = (_text(check.stdout) + _text(check.stderr)).strip()
    print("quality-gate: STOP after 2 fix passes; residual violations:")
    print(residual)
    return 1


def run(mode: str, cwd: Path | None = None) -> int:
    repo = _repo_root((cwd or Path.cwd()).resolve())
    changes = (
        _staged_changes(repo) if mode in {"staged", "fix"} else _tree_changes(repo)
    )
    if mode == "tree":
        tracked = _text(_git(repo, "ls-files", "-z")).split("\0")
        exclusions = _load_exclusions(repo)
        if not any(
            path and not _is_excluded(path, exclusions) and _language_for(path)
            for path in tracked
        ):
            raise GateError(
                "quality-gate: tree mode found zero files in enforced scope"
            )
    scoped = _scope(repo, changes)
    total = len(changes)
    in_scope = sum(len(items) for items in scoped.values())
    if in_scope == 0:
        scope_kind = "staged" if mode != "tree" else "changed"
        print(
            f"quality-gate: no files in enforced scope "
            f"{scope_kind} (0 of {total}) - nothing to check"
        )
        return 0
    if mode == "fix":
        return _fix(repo, scoped.get("python", []))
    failed = 0
    for name, selected in scoped.items():
        failed |= LANGUAGES[name].check(repo, selected, mode)
    if failed:
        return 1
    print(f"quality-gate: {in_scope} files checked, 0 new violations")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--staged", action="store_true")
    modes.add_argument("--tree", action="store_true")
    modes.add_argument("--fix", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    mode = "staged" if args.staged else "tree" if args.tree else "fix"
    try:
        return run(mode)
    except GateError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
