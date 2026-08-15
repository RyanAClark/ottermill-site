"""Runtime rejection controls for Ottermill's HTML and URL contracts."""

from __future__ import annotations

import io
from pathlib import Path

from sitecheck import Issue, resolve_internal_target, scan_site
from sitecheck.core import emit, main


def _messages(root: Path) -> list[str]:
    return [issue.message for issue in scan_site(root).issues]


def test_valid_utf8_page_and_external_reference_are_accepted(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<html><body><a href="https://example.net/x">away</a></body></html>',
        encoding="utf-8",
    )

    result = scan_site(tmp_path)

    assert result.issues == ()
    assert result.internal_references == 0


def test_invalid_utf8_is_rejected_as_a_contract_error(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_bytes(b"<html>\xff</html>")

    assert "file is not valid UTF-8" in _messages(tmp_path)


def test_duplicate_reference_attribute_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<html><body><a href="/" href="/other">bad</a></body></html>',
        encoding="utf-8",
    )

    assert 'duplicate attribute "href"' in _messages(tmp_path)


def test_parent_escape_is_rejected_before_filesystem_resolution(tmp_path: Path) -> None:
    source = tmp_path / "index.html"
    source.write_text("<html></html>", encoding="utf-8")

    internal, target, error = resolve_internal_target(
        tmp_path, source, "../outside.html", frozenset()
    )

    assert internal is True
    assert target is None
    assert error == 'internal reference escapes the site root: "../outside.html"'


def test_parser_rejects_missing_values_and_unbalanced_edge_shapes(
    tmp_path: Path,
) -> None:
    (tmp_path / "index.html").write_text(
        "</span><html><body><a href>missing</a><div/></br></span><div>",
        encoding="utf-8",
    )

    messages = _messages(tmp_path)

    assert 'attribute "href" has no value' in messages
    assert "non-void element <div> cannot be self-closing in HTML" in messages
    assert "unexpected closing tag </br>" in messages
    assert "unexpected closing tag </span>" in messages
    assert "mismatched closing tag </span>; expected </body>" in messages
    assert "unclosed tag <div>" in messages


def test_reference_classifier_covers_invalid_and_non_http_shapes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "missing.html"

    invalid = resolve_internal_target(tmp_path, source, "http://[", frozenset())
    non_http = resolve_internal_target(tmp_path, source, "mailto:a@b.test", frozenset())
    relative_https = resolve_internal_target(
        tmp_path, source, "https:relative", frozenset()
    )
    empty = resolve_internal_target(tmp_path, source, "#fragment", frozenset())
    backslash = resolve_internal_target(
        tmp_path, source, "folder\\page.html", frozenset()
    )
    explicit_missing = resolve_internal_target(
        tmp_path, source, "/absent.html", frozenset()
    )

    assert invalid[0] is True and invalid[2] and "invalid URL reference" in invalid[2]
    assert non_http == (False, None, None)
    assert relative_https == (
        True,
        None,
        'invalid internal URL reference "https:relative"',
    )
    assert empty == (
        True,
        None,
        'unresolved internal reference "#fragment"',
    )
    assert backslash == (
        True,
        None,
        'internal reference uses a backslash: "folder\\page.html"',
    )
    assert explicit_missing == (
        True,
        None,
        'unresolved internal reference "/absent.html"',
    )


def test_issue_render_and_emit_degrade_outside_root_and_ascii(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.html"
    assert Issue(outside, 2, 3, "bad").render(tmp_path).startswith(str(outside))

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict")
    emit("snowman: \u2603", stream)
    stream.flush()
    assert raw.getvalue() == b"snowman: ?\r\n" or raw.getvalue() == b"snowman: ?\n"


def test_main_contracts_execute_all_product_verdicts(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert main([str(missing)]) == 2

    site = tmp_path / "site"
    site.mkdir()
    assert main([str(site)]) == 2

    page = site / "index.html"
    page.write_text('<html><a href="/absent">bad</a></html>', encoding="utf-8")
    assert main([str(site)]) == 1

    page.write_text("<html><body>ok</body></html>", encoding="utf-8")
    assert main([str(site)]) == 0
