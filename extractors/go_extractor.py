"""
extractors/go_extractor.py — Orbits Phase 3

Go import extraction via tree-sitter Query + QueryCursor.
"""

from pathlib import Path
from .base import BaseExtractor, ExtractResult, RawImport


def _load_grammar():
    try:
        import tree_sitter_go as tsgo
        from tree_sitter import Language, Parser, Query, QueryCursor
        return {
            'lang': Language(tsgo.language()),
            'Parser': Parser,
            'Query': Query,
            'QueryCursor': QueryCursor,
        }
    except ImportError:
        return None

_G = _load_grammar()

# Capture the string literal inside every import_spec
_GO_QUERY = """
  (import_spec
    path: (interpreted_string_literal) @path)
"""


class GoExtractor(BaseExtractor):
    @property
    def language(self) -> str: return 'go'
    @property
    def extensions(self) -> list[str]: return ['.go']

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        if _G is None: return ExtractResult()
        try: source = filepath.read_bytes()
        except (OSError, PermissionError): return ExtractResult(read_error=True)
        try: file_rel = str(filepath.relative_to(root))
        except ValueError: return ExtractResult()

        try:
            parser = _G['Parser'](_G['lang'])
            tree   = parser.parse(source)
            q      = _G['Query'](_G['lang'], _GO_QUERY)
            cursor = _G['QueryCursor'](q)
        except Exception:
            return ExtractResult(syntax_error=True)

        imports = []
        for _, captures in cursor.matches(tree.root_node):
            for node in captures.get('path', []):
                raw = node.text.decode('utf-8', errors='replace').strip('"')
                if raw:
                    imports.append(RawImport(
                        source_file=file_rel,
                        raw=raw,
                        line=node.start_point[0] + 1,
                        kind='import',
                        is_relative=raw.startswith(('./', '../')),
                    ))

        return ExtractResult(imports=imports)
