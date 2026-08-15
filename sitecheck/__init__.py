"""Ottermill static-site validation core."""

from .core import (
    Issue,
    Reference,
    ScanResult,
    discover_html_files,
    resolve_internal_target,
    scan_site,
)

__all__ = (
    "Issue",
    "Reference",
    "ScanResult",
    "discover_html_files",
    "resolve_internal_target",
    "scan_site",
)
