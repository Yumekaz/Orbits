"""
extractors/python_extractor.py — Orbits Phase 3

Python import extraction using the built-in ast module.
We keep ast for Python (not tree-sitter) because:
  - ast is part of stdlib, zero extra dependency
  - ast understands Python semantics, not just syntax
  - ast handles all encoding edge cases correctly
  - It's faster for Python than tree-sitter bindings
"""

import ast
from pathlib import Path

from .base import BaseExtractor, ExtractResult, RawImport


class PythonExtractor(BaseExtractor):

    @property
    def language(self) -> str:
        return 'python'

    @property
    def extensions(self) -> list[str]:
        return ['.py', '.pyi']

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        try:
            source = filepath.read_text(encoding='utf-8', errors='replace')
        except (OSError, PermissionError):
            return ExtractResult(read_error=True)

        try:
            tree = ast.parse(source, filename=str(filepath))
        except SyntaxError:
            return ExtractResult(syntax_error=True)

        try:
            file_rel = str(filepath.relative_to(root))
        except ValueError:
            return ExtractResult()

        imports: list[RawImport] = []

        for node in ast.walk(tree):

            # import os
            # import utils.helpers as uh
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(RawImport(
                        source_file=file_rel,
                        raw=alias.name,
                        line=node.lineno,
                        kind='import',
                        is_relative=False,
                    ))

            # from os import path
            # from . import sibling       → level=1, module=''
            # from ..core import base     → level=2, module='core'
            # from utils import helpers   → level=0, module='utils'
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                level  = node.level

                for alias in node.names:
                    # Build the most specific raw string:
                    # 'from utils import helpers' → try 'utils.helpers' first
                    if module:
                        raw = ('.' * level) + f"{module}.{alias.name}"
                    else:
                        raw = ('.' * level) + alias.name

                    imports.append(RawImport(
                        source_file=file_rel,
                        raw=raw,
                        line=node.lineno,
                        kind='import_from',
                        is_relative=level > 0,
                    ))

        return ExtractResult(imports=imports)
