"""
worker.py - Orbits Phase 3 parallel worker.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkerResult:
    language: str
    edges: list[dict] = field(default_factory=list)
    cache_updates: dict[str, dict] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)
    syntax_errors: int = 0
    error: str = ''


def run_worker(language: str, file_strs: list[str], root_str: str, cache_data: dict[str, dict], resolver_config: dict) -> WorkerResult:
    try:
        return _run(language, file_strs, root_str, cache_data, resolver_config)
    except Exception as exc:
        return WorkerResult(language=language, error=str(exc))


def _run(language, file_strs, root_str, cache_data, resolver_config):
    root = Path(root_str)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    from extractors.c_family_extractor import CExtractor, CppExtractor
    from extractors.generic_extractor import GenericExtractor
    from extractors.go_extractor import GoExtractor
    from extractors.js_extractor import JsExtractor, TsExtractor, TsxExtractor
    from extractors.jvm_extractor import JavaExtractor, KotlinExtractor
    from extractors.python_extractor import PythonExtractor

    extractor_map = {
        'python': PythonExtractor(),
        'javascript': JsExtractor(),
        'typescript': TsExtractor(),
        'tsx': TsxExtractor(),
        'go': GoExtractor(),
        'c': CExtractor(),
        'cpp': CppExtractor(),
        'java': JavaExtractor(),
        'kotlin': KotlinExtractor(),
        'generic': GenericExtractor(),
    }

    extractor = extractor_map.get(language)
    if extractor is None:
        return WorkerResult(language=language, error=f'Unknown language: {language}')

    resolver = _make_resolver(language, root, resolver_config)
    result = WorkerResult(language=language)
    result.stats = {'local': 0, 'stdlib': 0, 'third_party': 0, 'external': 0, 'unknown': 0}
    known_nodes: set[str] = set(resolver_config.get('all_node_ids', []))

    for path_str in file_strs:
        filepath = Path(path_str)
        rel = str(filepath.relative_to(root))
        try:
            stat = filepath.stat()
        except OSError:
            continue

        cached = cache_data.get(rel)
        if cached and cached.get('mtime') == stat.st_mtime and cached.get('size') == stat.st_size:
            for imp_dict in cached['imports']:
                _resolve_and_add(imp_dict, language, filepath, root, resolver, known_nodes, result)
            continue

        extract_result = extractor.extract(filepath, root)
        if extract_result.syntax_error:
            result.syntax_errors += 1
            continue

        serialized = [
            {
                'source_file': imp.source_file,
                'raw': imp.raw,
                'line': imp.line,
                'kind': imp.kind,
                'is_relative': imp.is_relative,
                'module': imp.module,
                'imported_name': imp.imported_name,
                'level': imp.level,
            }
            for imp in extract_result.imports
        ]
        result.cache_updates[rel] = {'mtime': stat.st_mtime, 'size': stat.st_size, 'imports': serialized}

        for imp_dict in serialized:
            _resolve_and_add(imp_dict, language, filepath, root, resolver, known_nodes, result)

    return result


def _resolve_and_add(imp_dict, language, filepath, root, resolver, known_nodes, result: WorkerResult):
    resolved_paths, kind = resolver(imp_dict, language, filepath, root)
    kind_key = kind.lower()
    result.stats[kind_key] = result.stats.get(kind_key, 0) + 1

    if kind != 'LOCAL' or not resolved_paths:
        return

    if isinstance(resolved_paths, str):
        targets = [resolved_paths]
    else:
        targets = list(resolved_paths)

    for target in targets:
        if target and target in known_nodes:
            result.edges.append({
                'source': imp_dict['source_file'],
                'target': target,
                'type': imp_dict['kind'],
                'line': imp_dict['line'],
                'language': language,
            })


def _make_resolver(language: str, root: Path, config: dict):
    if language == 'python':
        from resolver import ProjectConfig, PythonResolver

        py_cfg = ProjectConfig(root=root)
        py_cfg.src_dirs = [root / d for d in config.get('py_src_dirs', [])]
        if root not in py_cfg.src_dirs:
            py_cfg.src_dirs.insert(0, root)
        py_cfg.third_party = set(config.get('py_third_party', []))
        py_cfg.package_name = config.get('py_package_name', '')
        resolver = PythonResolver(root, py_cfg)

        def resolve_py(imp_dict, lang, filepath, root):
            if imp_dict.get('kind') == 'import_from':
                resolved = resolver.resolve_from_import(
                    module_name=imp_dict.get('module', ''),
                    attr_name=imp_dict.get('imported_name', ''),
                    level=imp_dict.get('level', 0),
                    from_file=filepath,
                )
            else:
                resolved = resolver.resolve(imp_dict.get('raw', ''), level=imp_dict.get('level', 0), from_file=filepath)
            return resolved.path, resolved.kind

        return resolve_py

    if language in ('javascript', 'typescript', 'tsx'):
        from resolvers.js_resolver import JsProjectConfig, JsResolver

        js_cfg = JsProjectConfig(root=root)
        js_cfg.aliases = config.get('js_aliases', {})
        js_cfg.base_url = config.get('js_base_url', '')
        js_cfg.packages = js_cfg.detect(root).packages
        resolver = JsResolver(root, js_cfg)

        def resolve_js(imp_dict, lang, filepath, root):
            path, kind = resolver.resolve(imp_dict.get('raw', ''), from_file=filepath)
            if kind == 'EXTERNAL':
                kind = 'THIRD_PARTY'
            return path, kind

        return resolve_js

    if language == 'go':
        from resolvers.go_resolver import GoResolver

        resolver = GoResolver(root)
        return lambda imp_dict, lang, filepath, root: resolver.resolve(imp_dict.get('raw', ''))

    if language in ('c', 'cpp'):
        from resolvers.c_family_resolver import CFamilyResolver, CProjectConfig

        c_cfg = CProjectConfig(root=root)
        c_cfg.include_dirs = [root / d for d in config.get('c_include_dirs', [])]
        if root not in c_cfg.include_dirs:
            c_cfg.include_dirs.insert(0, root)
        resolver = CFamilyResolver(root, c_cfg)
        return lambda imp_dict, lang, filepath, root: resolver.resolve(imp_dict.get('raw', ''), filepath, imp_dict.get('kind', 'include'))

    if language in ('java', 'kotlin'):
        from resolvers.jvm_resolver import JvmProjectConfig, JvmResolver

        jvm_cfg = JvmProjectConfig.detect(root)
        configured_roots = [root / d for d in config.get('jvm_src_roots', [])]
        if configured_roots:
            jvm_cfg.src_roots = configured_roots
            jvm_cfg.package_files = {}
            jvm_cfg.symbol_files = {}
            jvm_cfg._index_sources()
        resolver = JvmResolver(root, jvm_cfg)
        return lambda imp_dict, lang, filepath, root: resolver.resolve(imp_dict.get('raw', ''))

    def resolve_generic(imp_dict, lang, filepath, root):
        raw = imp_dict.get('raw', '')
        if imp_dict.get('is_relative'):
            base = (filepath.parent / raw).resolve()
            if base.exists():
                try:
                    return str(base.relative_to(root)), 'LOCAL'
                except ValueError:
                    pass
        return None, 'UNKNOWN'

    return resolve_generic
