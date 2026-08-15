#!/usr/bin/env python3
"""Validate the site's HTML structure and internal file references."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import unquote, urlsplit


VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
REFERENCE_ATTRIBUTES = frozenset({"href", "src"})
NON_SITE_DIRECTORIES = frozenset({".git", ".venv", "node_modules"})


@dataclass(frozen=True)
class Issue:
    path: Path
    line: int
    column: int
    message: str

    def render(self, root: Path) -> str:
        try:
            display_path = self.path.relative_to(root)
        except ValueError:
            display_path = self.path
        return f"{display_path}:{self.line}:{self.column}: {self.message}"


@dataclass(frozen=True)
class Reference:
    path: Path
    line: int
    column: int
    value: str


@dataclass(frozen=True)
class ScanResult:
    html_files: tuple[Path, ...]
    internal_references: int
    issues: tuple[Issue, ...]


class StrictSiteParser(HTMLParser):
    """HTMLParser with explicit source-balance and duplicate-attribute checks."""

    def __init__(self, path: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.stack: list[tuple[str, int, int]] = []
        self.issues: list[Issue] = []
        self.references: list[Reference] = []

    def _location(self) -> tuple[int, int]:
        line, zero_based_column = self.getpos()
        return line, zero_based_column + 1

    def _record_attributes(self, attrs: list[tuple[str, str | None]]) -> None:
        line, column = self._location()
        seen: set[str] = set()
        for name, value in attrs:
            name = name.lower()
            if name in seen:
                self.issues.append(
                    Issue(self.path, line, column, f'duplicate attribute "{name}"')
                )
            seen.add(name)
            if name not in REFERENCE_ATTRIBUTES:
                continue
            if value is None:
                self.issues.append(
                    Issue(self.path, line, column, f'attribute "{name}" has no value')
                )
                continue
            self.references.append(Reference(self.path, line, column, value))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self._record_attributes(attrs)
        if tag not in VOID_ELEMENTS:
            line, column = self._location()
            self.stack.append((tag, line, column))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self._record_attributes(attrs)
        if tag not in VOID_ELEMENTS:
            line, column = self._location()
            self.issues.append(
                Issue(
                    self.path,
                    line,
                    column,
                    f"non-void element <{tag}> cannot be self-closing in HTML",
                )
            )

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        line, column = self._location()
        if tag in VOID_ELEMENTS:
            self.issues.append(
                Issue(self.path, line, column, f"unexpected closing tag </{tag}>")
            )
            return
        if not self.stack:
            self.issues.append(
                Issue(self.path, line, column, f"unexpected closing tag </{tag}>")
            )
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return

        expected = self.stack[-1][0]
        self.issues.append(
            Issue(
                self.path,
                line,
                column,
                f"mismatched closing tag </{tag}>; expected </{expected}>",
            )
        )
        matching_index = next(
            (
                index
                for index in range(len(self.stack) - 1, -1, -1)
                if self.stack[index][0] == tag
            ),
            None,
        )
        if matching_index is not None:
            for open_tag, open_line, open_column in self.stack[matching_index + 1 :]:
                self.issues.append(
                    Issue(
                        self.path,
                        open_line,
                        open_column,
                        f"unclosed tag <{open_tag}> before </{tag}>",
                    )
                )
            del self.stack[matching_index:]

    def finish(self) -> None:
        super().close()
        for tag, line, column in reversed(self.stack):
            self.issues.append(Issue(self.path, line, column, f"unclosed tag <{tag}>"))
        self.stack.clear()


def discover_html_files(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in root.rglob("*.html")
            if NON_SITE_DIRECTORIES.isdisjoint(path.relative_to(root).parts)
        )
    )


def _site_hosts(root: Path) -> frozenset[str]:
    cname = root / "CNAME"
    if not cname.is_file():
        return frozenset()
    host = cname.read_text(encoding="utf-8").strip().lower().rstrip(".")
    return frozenset({host}) if host else frozenset()


def _classify_reference(
    reference: str, site_hosts: frozenset[str]
) -> tuple[bool, str | None, str | None]:
    try:
        parsed = urlsplit(reference)
    except ValueError as error:
        return True, None, f'invalid URL reference "{reference}": {error}'

    scheme = parsed.scheme.lower()
    if scheme and scheme not in {"http", "https"}:
        return False, None, None
    if parsed.netloc:
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if hostname not in site_hosts:
            return False, None, None
        return True, parsed.path or "/", None
    if scheme:
        return True, None, f'invalid internal URL reference "{reference}"'
    return True, parsed.path, None


def _resolve_internal_path(
    root: Path, source: Path, reference: str, url_path: str
) -> tuple[Path | None, str | None]:
    if not url_path:
        if source.is_file():
            return source, None
        return None, f'unresolved internal reference "{reference}"'

    decoded_path = unquote(url_path)
    if "\\" in decoded_path:
        return None, f'internal reference uses a backslash: "{reference}"'

    if decoded_path.startswith("/"):
        unresolved = root / decoded_path.lstrip("/")
    else:
        unresolved = source.parent / decoded_path
    target = unresolved.resolve()
    resolved_root = root.resolve()
    if not target.is_relative_to(resolved_root):
        return None, f'internal reference escapes the site root: "{reference}"'

    if not decoded_path or decoded_path.endswith("/"):
        candidates = [target / "index.html"]
    else:
        candidates = [target]
        if not target.suffix:
            candidates.extend([target.with_suffix(".html"), target / "index.html"])

    resolved = next(
        (candidate for candidate in candidates if candidate.is_file()), None
    )
    if resolved is None:
        return None, f'unresolved internal reference "{reference}"'
    return resolved, None


def resolve_internal_target(
    root: Path, source: Path, reference: str, site_hosts: frozenset[str]
) -> tuple[bool, Path | None, str | None]:
    is_internal, url_path, error = _classify_reference(reference, site_hosts)
    if not is_internal or error:
        return is_internal, None, error
    target, error = _resolve_internal_path(root, source, reference, cast(str, url_path))
    return True, target, error


def scan_site(root: Path) -> ScanResult:
    root = root.resolve()
    html_files = discover_html_files(root)
    issues: list[Issue] = []
    references: list[Reference] = []

    for html_file in html_files:
        try:
            source = html_file.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            issues.append(
                Issue(html_file, 1, error.start + 1, "file is not valid UTF-8")
            )
            continue
        parser = StrictSiteParser(html_file)
        parser.feed(source)
        parser.finish()
        issues.extend(parser.issues)
        references.extend(parser.references)

    internal_references = 0
    site_hosts = _site_hosts(root)
    for reference in references:
        is_internal, _target, error = resolve_internal_target(
            root, reference.path, reference.value, site_hosts
        )
        if not is_internal:
            continue
        internal_references += 1
        if error:
            issues.append(
                Issue(reference.path, reference.line, reference.column, error)
            )

    return ScanResult(html_files, internal_references, tuple(issues))


def emit(message: str, stream: TextIO = sys.stdout) -> None:
    try:
        print(message, file=stream)
    except UnicodeEncodeError:
        encoding = stream.encoding or "ascii"
        replacement = message.encode(encoding, errors="replace").decode(encoding)
        print(replacement, file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="site root (defaults to the repository root)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        emit(f"ERROR: site root does not exist: {root}", sys.stderr)
        return 2

    result = scan_site(root)
    if not result.html_files:
        emit(f"ERROR: no HTML files found under {root}", sys.stderr)
        return 2
    if result.issues:
        for issue in result.issues:
            emit(issue.render(root), sys.stderr)
        emit(
            f"FAIL: {len(result.issues)} issue(s) across "
            f"{len(result.html_files)} HTML file(s)",
            sys.stderr,
        )
        return 1

    emit(
        f"OK: {len(result.html_files)} HTML file(s) parsed; "
        f"{result.internal_references} internal reference(s) resolved"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# quality-gate control (c): benign touch of a legacy file.
