from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sitecheck import discover_html_files, scan_site


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def write_page(root: Path, relative_path: str, contents: str) -> None:
    destination = root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(contents, encoding="utf-8")


class SiteCheckerTests(unittest.TestCase):
    def test_every_repository_page_is_parsed_and_links_resolve(self) -> None:
        expected_pages = tuple(
            sorted(
                path
                for path in REPO_ROOT.rglob("*.html")
                if {".git", ".venv", "node_modules"}.isdisjoint(
                    path.relative_to(REPO_ROOT).parts
                )
            )
        )

        result = scan_site(REPO_ROOT)

        self.assertGreater(len(expected_pages), 0)
        self.assertEqual(expected_pages, result.html_files)
        self.assertEqual((), result.issues)

    def test_known_bad_malformed_markup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_page(
                root,
                "index.html",
                (FIXTURES / "malformed.html.txt").read_text(encoding="utf-8"),
            )

            result = scan_site(root)

        messages = [issue.message for issue in result.issues]
        self.assertTrue(
            any("mismatched closing tag" in message for message in messages), messages
        )

    def test_known_bad_broken_internal_link_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_page(
                root,
                "index.html",
                (FIXTURES / "broken-link.html.txt").read_text(encoding="utf-8"),
            )

            result = scan_site(root)

        messages = [issue.message for issue in result.issues]
        self.assertIn('unresolved internal reference "/missing/page"', messages)

    def test_extensionless_reference_resolves_to_html_file(self) -> None:
        valid_page = "<!doctype html><html><body>ok</body></html>"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_page(
                root,
                "index.html",
                '<!doctype html><html><body><a href="/actuo/support?from=test#contact">'
                "Support</a></body></html>",
            )
            write_page(root, "actuo/support.html", valid_page)

            result = scan_site(root)

        self.assertEqual((), result.issues)
        self.assertEqual(1, result.internal_references)

    def test_fragment_only_reference_resolves_to_its_source_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_page(
                root,
                "actuo/support.html",
                '<!doctype html><html><body><a href="#contact">'
                "Contact</a></body></html>",
            )

            result = scan_site(root)

        self.assertEqual((), result.issues)
        self.assertEqual(1, result.internal_references)

    def test_directory_and_same_host_absolute_references_resolve(self) -> None:
        valid_page = "<!doctype html><html><body>ok</body></html>"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "CNAME").write_text("example.test\n", encoding="utf-8")
            write_page(
                root,
                "index.html",
                '<!doctype html><html><body><a href="/actuo/">Actuo</a>'
                '<a href="https://example.test">Home</a></body></html>',
            )
            write_page(root, "actuo/index.html", valid_page)

            result = scan_site(root)

        self.assertEqual((), result.issues)
        self.assertEqual(2, result.internal_references)

    def test_discovery_excludes_generated_tooling_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_page(root, "nested/page.html", "<html></html>")
            write_page(root, ".git/hidden.html", "<html></html>")
            write_page(root, ".venv/generated.html", "<html></html>")
            write_page(root, "node_modules/package.html", "<html></html>")

            pages = discover_html_files(root)

        self.assertEqual((root / "nested/page.html",), pages)


if __name__ == "__main__":
    unittest.main()
