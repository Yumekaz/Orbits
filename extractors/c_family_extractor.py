"""
extractors/c_family_extractor.py - Orbits Phase 3

C and C++ include extraction via tree-sitter.
"""

import re
from pathlib import Path

from runtime_env import bootstrap_local_venv
from path_utils import relative_to_root

bootstrap_local_venv()

from .base import BaseExtractor, ExtractResult, RawImport


def _load_grammars():
    try:
        import tree_sitter_c as tsc
        import tree_sitter_cpp as tscpp
        from tree_sitter import Language, Parser, Query, QueryCursor

        return {
            'c': Language(tsc.language()),
            'cpp': Language(tscpp.language()),
            'Parser': Parser,
            'Query': Query,
            'QueryCursor': QueryCursor,
        }
    except ImportError:
        return None


_G = _load_grammars()

_C_QUERY = """
  (preproc_include path: (string_literal (string_content) @local_path))
  (preproc_include path: (system_lib_string) @system_path)
"""

_DYNAMIC_LOAD_RE = re.compile(
    r'''\b(?:dlopen|LoadLibraryA?|LoadLibraryExA?|LoadPackagedLibrary)\s*\(\s*(?:"([^"]+)"|'([^']+)')''',
    re.IGNORECASE,
)


def _extract_with_query(source: bytes, lang, file_rel: str) -> list[RawImport]:
    parser = _G['Parser'](lang)
    tree = parser.parse(source)
    query = _G['Query'](lang, _C_QUERY)
    cursor = _G['QueryCursor'](query)

    imports: list[RawImport] = []
    seen: set[tuple[str, int, str]] = set()
    for _, captures in cursor.matches(tree.root_node):
        for node in captures.get('local_path', []):
            raw = node.text.decode('utf-8', errors='replace')
            line = node.start_point[0] + 1
            key = (raw, line, 'include')
            if raw and key not in seen:
                seen.add(key)
                imports.append(RawImport(
                    source_file=file_rel,
                    raw=raw,
                    line=line,
                    kind='include',
                    is_relative=True,
                ))
        for node in captures.get('system_path', []):
            raw = node.text.decode('utf-8', errors='replace').strip('<>')
            line = node.start_point[0] + 1
            key = (raw, line, 'system_include')
            if raw and key not in seen:
                seen.add(key)
                imports.append(RawImport(
                    source_file=file_rel,
                    raw=raw,
                    line=line,
                    kind='system_include',
                    is_relative=False,
                ))
    text = source.decode('utf-8', errors='replace')
    for match in _DYNAMIC_LOAD_RE.finditer(text):
        raw = (match.group(1) or match.group(2) or '').strip()
        line = text[:match.start()].count('\n') + 1
        key = (raw, line, 'dynamic_load')
        if raw and key not in seen:
            seen.add(key)
            imports.append(RawImport(
                source_file=file_rel,
                raw=raw,
                line=line,
                kind='dynamic_load',
                is_relative=True,
            ))
    return imports


class CExtractor(BaseExtractor):
    @property
    def language(self) -> str:
        return 'c'

    @property
    def extensions(self) -> list[str]:
        return ['.c']

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
            return ExtractResult(imports=_extract_with_query(source, _G['c'], file_rel))
        except Exception:
            return ExtractResult(syntax_error=True)


class CppExtractor(BaseExtractor):
    @property
    def language(self) -> str:
        return 'cpp'

    @property
    def extensions(self) -> list[str]:
        return ['.cc', '.cpp', '.cxx', '.hpp', '.hh', '.h']

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
            return ExtractResult(imports=_extract_with_query(source, _G['cpp'], file_rel))
        except Exception:
            return ExtractResult(syntax_error=True)
