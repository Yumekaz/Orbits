"""
extractor.py — Orbits Phase 2.
AST walker only. All resolution logic lives in resolver.py.
"""

import ast
from pathlib import Path
from resolver import PythonResolver, ImportKind


def extract_imports(filepath, root, resolver):
    try:
        source = filepath.read_text(encoding='utf-8', errors='replace')
    except (OSError, PermissionError):
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    try:
        file_rel = str(filepath.relative_to(root))
    except ValueError:
        return []

    results = []

    for node in ast.walk(tree):

        # import os / import utils.helpers
        if isinstance(node, ast.Import):
            for alias in node.names:
                r = resolver.resolve(alias.name, level=0, from_file=filepath)
                results.append({
                    'from':     file_rel,
                    'to':       r.path or alias.name,
                    'raw':      alias.name,
                    'kind':     r.kind,
                    'resolved': r.kind == ImportKind.LOCAL,
                    'type':     'import',
                    'line':     node.lineno,
                })

        # from x import y
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            level  = node.level
            for alias in node.names:
                r = resolver.resolve_from_import(
                    module_name=module,
                    attr_name=alias.name,
                    level=level,
                    from_file=filepath,
                )
                raw = ('.' * level) + (f"{module}.{alias.name}" if module else alias.name)
                results.append({
                    'from':     file_rel,
                    'to':       r.path or raw,
                    'raw':      raw,
                    'kind':     r.kind,
                    'resolved': r.kind == ImportKind.LOCAL,
                    'type':     'import_from',
                    'line':     node.lineno,
                })

    return results
