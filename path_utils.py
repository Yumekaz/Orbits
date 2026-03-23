from __future__ import annotations

import os
from pathlib import Path


def canonical_path(path: str | Path) -> Path:
    return Path(os.path.realpath(os.path.normpath(str(Path(path)))))


def relative_to_root(path: str | Path, root: str | Path) -> str | None:
    child = canonical_path(path)
    parent = canonical_path(root)
    try:
        rel = os.path.relpath(str(child), str(parent))
    except ValueError:
        return None
    if rel in ('.', ''):
        return '.'
    if rel == '..' or rel.startswith(f'..{os.sep}'):
        return None
    return rel.replace('\\', '/')

