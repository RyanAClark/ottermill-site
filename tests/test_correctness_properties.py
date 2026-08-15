"""Generated controls for site discovery and reference resolution."""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, strategies as st

from sitecheck import discover_html_files, resolve_internal_target


SLUG = st.from_regex(r"[a-z][a-z0-9]{0,8}", fullmatch=True)


@given(st.lists(SLUG, min_size=1, max_size=12, unique=True))
def test_discovery_is_complete_and_order_independent(slugs: list[str]) -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        for slug in reversed(slugs):
            target = root / slug / "index.html"
            target.parent.mkdir(parents=True)
            target.write_text("<html></html>", encoding="utf-8")

        discovered = discover_html_files(root)

        assert discovered == tuple(sorted(root / slug / "index.html" for slug in slugs))


@given(SLUG)
def test_extensionless_internal_targets_resolve_to_html(slug: str) -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "index.html"
        target = root / f"{slug}.html"
        source.write_text("<html></html>", encoding="utf-8")
        target.write_text("<html></html>", encoding="utf-8")

        internal, resolved, error = resolve_internal_target(
            root, source, f"/{slug}?from=property#section", frozenset()
        )

        assert internal is True
        assert resolved == target
        assert error is None


@given(SLUG)
def test_generated_parent_escapes_always_refuse(slug: str) -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "index.html"
        source.write_text("<html></html>", encoding="utf-8")

        internal, resolved, error = resolve_internal_target(
            root, source, f"../{slug}.html", frozenset()
        )

        assert internal is True
        assert resolved is None
        assert error and "escapes the site root" in error
