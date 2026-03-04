"""
extractor.py — Python import extractor using the built-in ast module.

Handles:
  import os                        → absolute import
  import utils.helpers             → dotted absolute import
  from pathlib import Path         → from-import
  from . import sibling            → relative import (level=1)
  from ..core import base          → relative import (level=2)
  from .utils import something     → relative from-import

Does NOT handle:
  importlib.import_module(var)     → dynamic import (Phase 5)
  __import__(var)                  → dynamic import (Phase 5)

For each import, we try to resolve the module name to an actual
file path inside the project. If we can't (stdlib, third-party,
dynamic) we record it as unresolved and skip the edge.
"""

import ast
from pathlib import Path
from typing import Optional


# ── Resolution ────────────────────────────────────────────────────────────────

def resolve_module_path(
    module_parts: list[str],
    search_dirs: list[Path],
    root: Path,
) -> Optional[str]:
    """
    Given a module name split into parts (e.g. ['utils', 'helpers']),
    try to find it on disk in any of the search directories.

    Returns a path relative to root, or None if not found.
    """
    if not module_parts:
        return None

    for base in search_dirs:
        # Strategy 1: full dotted path as a package
        # utils/helpers/__init__.py
        package_init = base.joinpath(*module_parts) / '__init__.py'
        if package_init.exists():
            try:
                return str(package_init.relative_to(root))
            except ValueError:
                pass

        # Strategy 2: full dotted path as a module file
        # utils/helpers.py  (if module_parts = ['utils', 'helpers'])
        if len(module_parts) >= 2:
            module_file = base.joinpath(*module_parts[:-1]) / (module_parts[-1] + '.py')
            if module_file.exists():
                try:
                    return str(module_file.relative_to(root))
                except ValueError:
                    pass

        # Strategy 3: just the top-level name
        # utils/__init__.py
        top_init = base / module_parts[0] / '__init__.py'
        if top_init.exists():
            try:
                return str(top_init.relative_to(root))
            except ValueError:
                pass

        # utils.py
        top_module = base / (module_parts[0] + '.py')
        if top_module.exists():
            try:
                return str(top_module.relative_to(root))
            except ValueError:
                pass

    return None


def resolve_absolute(module_name: str, filepath: Path, root: Path) -> Optional[str]:
    """Resolve an absolute import like 'utils' or 'mypackage.core.base'."""
    if not module_name:
        return None
    parts = module_name.split('.')

    # Search dirs in priority order:
    # 1. Project root
    # 2. File's own directory
    # 3. Parent of root (handles case where you scanned a subpackage directly,
    #    e.g. scanning /project/mypackage and imports say 'from mypackage import x')
    search_dirs = [root, filepath.parent]

    root_init = root / '__init__.py'
    if root_init.exists() and root.parent not in search_dirs:
        search_dirs.append(root.parent)

    return resolve_module_path(parts, search_dirs, root)


def resolve_relative(
    module_name: str,
    level: int,
    filepath: Path,
    root: Path,
) -> Optional[str]:
    """
    Resolve a relative import.
    level=1 means 'from . import x'  (current package)
    level=2 means 'from .. import x' (parent package)
    """
    # Walk up `level` directories from the file's directory
    current = filepath.parent
    for _ in range(level - 1):
        parent = current.parent
        # Don't walk above the project root
        if parent == root.parent or parent == current:
            return None
        current = parent

    if not module_name:
        # 'from . import something' → reference is the current package
        init = current / '__init__.py'
        if init.exists():
            try:
                return str(init.relative_to(root))
            except ValueError:
                pass
        return None

    parts = module_name.split('.')
    return resolve_module_path(parts, [current], root)


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_imports(filepath: Path, root: Path) -> list[dict]:
    """
    Parse a Python file with ast and extract all import statements.

    Returns a list of dicts:
    {
        'from':     str,   # relative path of the importing file
        'to':       str,   # resolved relative path, or raw module name
        'raw':      str,   # the raw import string as written in source
        'resolved': bool,  # True if we found it on disk
        'type':     str,   # 'import' or 'import_from'
        'line':     int,   # line number in source
    }
    """
    try:
        source = filepath.read_text(encoding='utf-8', errors='replace')
    except (OSError, PermissionError):
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        # File has syntax errors — skip it, don't crash the whole run
        return []

    results = []

    try:
        file_rel = str(filepath.relative_to(root))
    except ValueError:
        return []

    for node in ast.walk(tree):

        # import os
        # import os, sys
        # import utils.helpers as uh
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved_path = resolve_absolute(alias.name, filepath, root)
                results.append({
                    'from': file_rel,
                    'to': resolved_path if resolved_path else alias.name,
                    'raw': alias.name,
                    'resolved': resolved_path is not None,
                    'type': 'import',
                    'line': node.lineno,
                })

        # from os import path
        # from . import sibling
        # from ..core import base
        # from utils import helpers  → try utils/helpers.py first, then utils/__init__.py
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            level = node.level  # 0 = absolute, 1 = '.', 2 = '..' etc.
            raw = ('.' * level) + module

            for alias in node.names:
                # First, try resolving as a submodule: 'from pkg import mod' → pkg/mod.py
                submodule = f"{module}.{alias.name}" if module else alias.name
                if level > 0:
                    resolved_path = resolve_relative(submodule, level, filepath, root)
                    if not resolved_path:
                        resolved_path = resolve_relative(module, level, filepath, root)
                else:
                    resolved_path = resolve_absolute(submodule, filepath, root)
                    if not resolved_path:
                        resolved_path = resolve_absolute(module, filepath, root)

                results.append({
                    'from': file_rel,
                    'to': resolved_path if resolved_path else (raw or 'unknown'),
                    'raw': f"{raw}.{alias.name}",
                    'resolved': resolved_path is not None,
                    'type': 'import_from',
                    'line': node.lineno,
                })

    return results
