"""
extractors/jvm_extractor.py - Orbits Phase 3

Java and Kotlin import extraction via tree-sitter.
"""

from pathlib import Path

from runtime_env import bootstrap_local_venv

bootstrap_local_venv()

from .base import BaseExtractor, ExtractResult, RawImport


def _load_grammars():
    try:
        import tree_sitter_java as tsjava
        import tree_sitter_kotlin as tskotlin
        from tree_sitter import Language, Parser

        return {
            'java': Language(tsjava.language()),
            'kotlin': Language(tskotlin.language()),
            'Parser': Parser,
        }
    except ImportError:
        return None


_G = _load_grammars()


def _decode_import_text(text: bytes, language: str) -> str:
    raw = text.decode('utf-8', errors='replace').strip()
    if language == 'java':
        raw = raw.removeprefix('import').strip()
        raw = raw.removeprefix('static').strip()
        raw = raw.rstrip(';').strip()
    else:
        raw = raw.removeprefix('import').strip()
        if ' as ' in raw:
            raw = raw.split(' as ', 1)[0].strip()
    return raw


def _extract_imports(source: bytes, lang, file_rel: str, language_name: str) -> list[RawImport]:
    parser = _G['Parser'](lang)
    tree = parser.parse(source)
    imports: list[RawImport] = []
    seen: set[tuple[str, int]] = set()

    for child in tree.root_node.named_children:
        if child.type not in {'import_declaration', 'import'}:
            continue
        raw = _decode_import_text(child.text, language_name)
        line = child.start_point[0] + 1
        if not raw or (raw, line) in seen:
            continue
        seen.add((raw, line))
        imports.append(RawImport(
            source_file=file_rel,
            raw=raw,
            line=line,
            kind='import',
            is_relative=False,
        ))

    return imports


class JavaExtractor(BaseExtractor):
    @property
    def language(self) -> str:
        return 'java'

    @property
    def extensions(self) -> list[str]:
        return ['.java']

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
            return ExtractResult(imports=_extract_imports(source, _G['java'], file_rel, 'java'))
        except Exception:
            return ExtractResult(syntax_error=True)


class KotlinExtractor(BaseExtractor):
    @property
    def language(self) -> str:
        return 'kotlin'

    @property
    def extensions(self) -> list[str]:
        return ['.kt', '.kts']

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
            return ExtractResult(imports=_extract_imports(source, _G['kotlin'], file_rel, 'kotlin'))
        except Exception:
            return ExtractResult(syntax_error=True)
