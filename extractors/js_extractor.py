"""
extractors/js_extractor.py — Orbits Phase 3

JS/TS extraction using tree-sitter Query + QueryCursor.
One query string per language. No AST node traversal.

Adding a new JS dialect = change two lines (grammar + query).
"""

from pathlib import Path
from .base import BaseExtractor, ExtractResult, RawImport

# ── Grammar loading (once per process) ────────────────────────────────────

def _load_grammars():
    try:
        import tree_sitter_javascript as tsjs
        import tree_sitter_typescript as tsts
        from tree_sitter import Language, Parser, Query, QueryCursor
        return {
            'js':  Language(tsjs.language()),
            'ts':  Language(tsts.language_typescript()),
            'tsx': Language(tsts.language_tsx()),
            'Parser': Parser,
            'Query': Query,
            'QueryCursor': QueryCursor,
        }
    except ImportError:
        return None

_G = _load_grammars()

# ── Query strings (.scm format) ────────────────────────────────────────────
# One query handles both JS and TS since TS grammar is a superset.
# Captures the string node of every static import/export/require.

_JS_QUERY = """
  (import_statement
    source: (string) @path)

  (export_statement
    source: (string) @path)

  (call_expression
    function: (identifier) @_fn
    arguments: (arguments (string) @path)
    (#eq? @_fn "require"))

  (call_expression
    function: (import) @_fn
    arguments: (arguments (string) @path))
"""


def _extract_with_query(source: bytes, lang, file_rel: str) -> list[RawImport]:
    if _G is None:
        return []
    Parser     = _G['Parser']
    Query      = _G['Query']
    QueryCursor= _G['QueryCursor']

    parser = Parser(lang)
    tree   = parser.parse(source)

    q      = Query(lang, _JS_QUERY)
    cursor = QueryCursor(q)

    imports = []
    seen    = set()

    for _, captures in cursor.matches(tree.root_node):
        nodes = captures.get('path', [])
        for node in nodes:
            raw = node.text.decode('utf-8', errors='replace')
            # Strip surrounding quotes: "x", 'x', `x`
            if len(raw) >= 2 and raw[0] in ('"', "'", '`'):
                raw = raw[1:-1]
            if not raw or raw in seen:
                continue
            # Skip template literals with expressions (dynamic)
            if '${' in raw:
                continue
            seen.add(raw)
            imports.append(RawImport(
                source_file=file_rel,
                raw=raw,
                line=node.start_point[0] + 1,
                kind='import',
                is_relative=raw.startswith(('./', '../')),
            ))

    return imports


# ── Extractor classes ──────────────────────────────────────────────────────

class JsExtractor(BaseExtractor):
    @property
    def language(self) -> str: return 'javascript'
    @property
    def extensions(self) -> list[str]: return ['.js', '.mjs', '.cjs']

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        if _G is None: return ExtractResult()
        try: source = filepath.read_bytes()
        except (OSError, PermissionError): return ExtractResult(read_error=True)
        try: file_rel = str(filepath.relative_to(root))
        except ValueError: return ExtractResult()
        try:
            imports = _extract_with_query(source, _G['js'], file_rel)
            return ExtractResult(imports=imports)
        except Exception:
            return ExtractResult(syntax_error=True)


class TsExtractor(BaseExtractor):
    @property
    def language(self) -> str: return 'typescript'
    @property
    def extensions(self) -> list[str]: return ['.ts', '.mts', '.cts']

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        if _G is None: return ExtractResult()
        try: source = filepath.read_bytes()
        except (OSError, PermissionError): return ExtractResult(read_error=True)
        try: file_rel = str(filepath.relative_to(root))
        except ValueError: return ExtractResult()
        try:
            imports = _extract_with_query(source, _G['ts'], file_rel)
            return ExtractResult(imports=imports)
        except Exception:
            return ExtractResult(syntax_error=True)


class TsxExtractor(BaseExtractor):
    @property
    def language(self) -> str: return 'tsx'
    @property
    def extensions(self) -> list[str]: return ['.tsx', '.jsx']

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        if _G is None: return ExtractResult()
        try: source = filepath.read_bytes()
        except (OSError, PermissionError): return ExtractResult(read_error=True)
        try: file_rel = str(filepath.relative_to(root))
        except ValueError: return ExtractResult()
        try:
            lang = _G['tsx'] if filepath.suffix == '.tsx' else _G['js']
            imports = _extract_with_query(source, lang, file_rel)
            return ExtractResult(imports=imports)
        except Exception:
            return ExtractResult(syntax_error=True)
