"""Runtime bootstrap helpers for local development dependencies."""

from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_local_venv() -> None:
    """Prepend the workspace venv site-packages if present."""
    root = Path(__file__).resolve().parent
    site_packages = root / '.venv' / 'Lib' / 'site-packages'
    if site_packages.exists():
        site_str = str(site_packages)
        if site_str not in sys.path:
            sys.path.insert(0, site_str)
