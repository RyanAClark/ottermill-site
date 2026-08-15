#!/usr/bin/env python3
"""Run the repository's production site-validation entry point."""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sitecheck.core import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
