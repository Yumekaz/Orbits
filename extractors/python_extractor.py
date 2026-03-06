"""
extractors/python_extractor.py - Orbits Phase 3

Python import extraction using tree-sitter so the multi-language extraction
layer is consistent across supported languages.
"""

from pathlib import Path

from runtime_env import bootstrap_local_venv

bootstrap_local_venv()

from .base import BaseExtractor, ExtractResult, RawImport


def _load_grammar():
    try:
        import tree_sitter_python as tsp
        from tree_sitter import Language, Parser

        return {
            'lang': Language(tsp.language()),
            'Parser': Parser,
        }
    except ImportError:
        return None


_G = _load_grammar()


def _node_text(node) -> str:
    return node.text.decode('utf-8', errors='replace')


def _import_name_nodes(statement):
    for child in statement.named_children:
        if child.type in {'dotted_name', 'aliased_import'}:
            yield child


def _extract_import_name(node) -> str:
    if node.type == 'aliased_import':
        target = node.child_by_field_name('name')
        return _node_text(target) if target else ''
    return _node_text(node)


def _split_relative_module(module_node) -> tuple[str, int]:
    if module_node is None:
        return '', 0
    text = _node_text(module_node)
    level = len(text) - len(text.lstrip('.'))
    return text.lstrip('.'), level


class PythonExtractor(BaseExtractor):
    @property
    def language(self) -> str:
        return 'python'

    @property
    def extensions(self) -> list[str]:
        return ['.py', '.pyi']

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
        except Exception:
            return ExtractResult(syntax_error=True)

        imports: list[RawImport] = []
        for statement in tree.root_node.named_children:
            if statement.type == 'import_statement':
                for name_node in _import_name_nodes(statement):
                    name = _extract_import_name(name_node)
                    if not name:
                        continue
                    imports.append(RawImport(
                        source_file=file_rel,
                        raw=name,
                        line=statement.start_point[0] + 1,
                        kind='import',
                        is_relative=False,
                        imported_name=name,
                    ))

            elif statement.type == 'import_from_statement':
                module_node = statement.child_by_field_name('module_name')
                module, level = _split_relative_module(module_node)
                prefix = '.' * level
                for child in statement.named_children:
                    if child is module_node:
                        continue
                    if child.type == 'wildcard_import':
                        imported_name = '*'
                    elif child.type in {'dotted_name', 'aliased_import'}:
                        imported_name = _extract_import_name(child)
                    else:
                        continue
                    raw = prefix + (f"{module}.{imported_name}" if module else imported_name)
                    imports.append(RawImport(
                        source_file=file_rel,
                        raw=raw,
                        line=statement.start_point[0] + 1,
                        kind='import_from',
                        is_relative=level > 0,
                        module=module,
                        imported_name=imported_name,
                        level=level,
                    ))

        return ExtractResult(imports=imports)
