"""
extractors/go_extractor.py - Orbits Phase 3

Go import extraction via tree-sitter Query + QueryCursor.
"""

from pathlib import Path

from runtime_env import bootstrap_local_venv

bootstrap_local_venv()

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

_GO_QUERY = """
  (import_spec path: (interpreted_string_literal (interpreted_string_literal_content) @path))
"""


class GoExtractor(BaseExtractor):
    @property
    def language(self) -> str:
        return 'go'

    @property
    def extensions(self) -> list[str]:
        return ['.go']

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        if _G is None:
            return ExtractResult()
        try:
            source = filepath.read_bytes()
        except (OSError, PermissionError):
            return ExtractResult(read_error=True)
        try:
            file_rel = str(filepath.relative_to(root))
        except ValueError:
            return ExtractResult()

        try:
            parser = _G['Parser'](_G['lang'])
            tree = parser.parse(source)
            query = _G['Query'](_G['lang'], _GO_QUERY)
            cursor = _G['QueryCursor'](query)
        except Exception:
            return ExtractResult(syntax_error=True)

        imports: list[RawImport] = []
        seen: set[tuple[str, int]] = set()
        for _, captures in cursor.matches(tree.root_node):
            for node in captures.get('path', []):
                raw = node.text.decode('utf-8', errors='replace')
                line = node.start_point[0] + 1
                key = (raw, line)
                if not raw or key in seen:
                    continue
                seen.add(key)
                imports.append(RawImport(
                    source_file=file_rel,
                    raw=raw,
                    line=line,
                    kind='import',
                    is_relative=raw.startswith(('./', '../')),
                ))

        return ExtractResult(imports=imports)
