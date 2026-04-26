"""
extractors/js_extractor.py - Orbits Phase 3

JS/TS extraction using tree-sitter Query + QueryCursor.
"""

from pathlib import Path

from runtime_env import bootstrap_local_venv
from path_utils import relative_to_root

bootstrap_local_venv()

from .base import BaseExtractor, ExtractResult, RawImport


def _load_grammars():
    try:
        import tree_sitter_javascript as tsjs
        import tree_sitter_typescript as tsts
        from tree_sitter import Language, Parser, Query, QueryCursor

        return {
            'js': Language(tsjs.language()),
            'ts': Language(tsts.language_typescript()),
            'tsx': Language(tsts.language_tsx()),
            'Parser': Parser,
            'Query': Query,
            'QueryCursor': QueryCursor,
        }
    except ImportError:
        return None


_G = _load_grammars()

_JS_QUERY = """
  (import_statement source: (string) @path)
  (export_statement source: (string) @path)
  (call_expression
    function: (identifier) @_fn
    arguments: (arguments (string) @path)
    (#eq? @_fn "require"))
  (call_expression
    function: (import) @_fn
    arguments: (arguments (string) @path))
"""


def _extract_with_query(source: bytes, lang, file_rel: str) -> list[RawImport]:
    parser = _G['Parser'](lang)
    tree = parser.parse(source)
    query = _G['Query'](lang, _JS_QUERY)
    cursor = _G['QueryCursor'](query)

    imports: list[RawImport] = []
    seen: set[tuple[str, int]] = set()
    for _, captures in cursor.matches(tree.root_node):
        for node in captures.get('path', []):
            raw = node.text.decode('utf-8', errors='replace')
            if len(raw) >= 2 and raw[0] in ('"', "'", '`'):
                raw = raw[1:-1]
            if not raw or '${' in raw:
                continue
            line = node.start_point[0] + 1
            key = (raw, line)
            if key in seen:
                continue
            seen.add(key)
            imports.append(RawImport(
                source_file=file_rel,
                raw=raw,
                line=line,
                kind='import',
                is_relative=raw.startswith(('./', '../')),
            ))
    return imports


class JsExtractor(BaseExtractor):
    @property
    def language(self) -> str:
        return 'javascript'

    @property
    def extensions(self) -> list[str]:
        return ['.js', '.mjs', '.cjs']

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        if _G is None:
            return ExtractResult()
        try:
            source = filepath.read_bytes()
        except (OSError, PermissionError):
            return ExtractResult(read_error=True)
        file_rel = relative_to_root(filepath, root)
        if not file_rel:
            return ExtractResult()
        try:
            return ExtractResult(imports=_extract_with_query(source, _G['js'], file_rel))
        except Exception:
            return ExtractResult(syntax_error=True)


class TsExtractor(BaseExtractor):
    @property
    def language(self) -> str:
        return 'typescript'

    @property
    def extensions(self) -> list[str]:
        return ['.ts', '.mts', '.cts']

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        if _G is None:
            return ExtractResult()
        try:
            source = filepath.read_bytes()
        except (OSError, PermissionError):
            return ExtractResult(read_error=True)
        file_rel = relative_to_root(filepath, root)
        if not file_rel:
            return ExtractResult()
        try:
            return ExtractResult(imports=_extract_with_query(source, _G['ts'], file_rel))
        except Exception:
            return ExtractResult(syntax_error=True)


class TsxExtractor(BaseExtractor):
    @property
    def language(self) -> str:
        return 'tsx'

    @property
    def extensions(self) -> list[str]:
        return ['.tsx', '.jsx']

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        if _G is None:
            return ExtractResult()
        try:
            source = filepath.read_bytes()
        except (OSError, PermissionError):
            return ExtractResult(read_error=True)
        file_rel = relative_to_root(filepath, root)
        if not file_rel:
            return ExtractResult()
        try:
            lang = _G['tsx'] if filepath.suffix.lower() == '.tsx' else _G['js']
            return ExtractResult(imports=_extract_with_query(source, lang, file_rel))
        except Exception:
            return ExtractResult(syntax_error=True)
