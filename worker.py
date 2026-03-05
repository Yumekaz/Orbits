"""
worker.py — Orbits Phase 3 parallel worker

Each language runs in its own worker process via ProcessPoolExecutor.
Grammars and extractors are initialized once per worker process,
not once per file. On a mixed Python+TS+Go project all three
parse simultaneously.

Usage (internal — called by lang_dispatch.py):
    future = executor.submit(run_worker, lang, file_paths, root_str, cache_data)
    result = future.result()  # WorkerResult

Design rules:
  - Workers receive plain strings/lists (picklable), not Path objects
  - Workers return plain dicts (picklable), not dataclasses
  - A worker crash returns an error result, never propagates an exception
  - Grammars are module-level globals in the worker process (loaded once)
"""

from __future__ import annotations
import sys
import os
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class WorkerResult:
    language:      str
    edges:         list[dict]        = field(default_factory=list)
    cache_updates: dict[str, list]   = field(default_factory=dict)
    stats:         dict[str, int]    = field(default_factory=dict)
    syntax_errors: int               = 0
    error:         str               = ''   # non-empty = worker crashed


# ── Worker entry point ─────────────────────────────────────────────────────
# This function runs in a subprocess. It must be importable at top-level
# (not a closure) for multiprocessing to pickle it correctly.

def run_worker(
    language:   str,
    file_strs:  list[str],    # absolute paths as strings
    root_str:   str,           # project root as string
    cache_data: dict[str, dict], # pre-loaded cache: rel_path → {mtime,size,imports}
    resolver_config: dict,    # serialized resolver config
) -> WorkerResult:
    """
    Process all files for one language.
    Runs in a separate process. Imports grammars lazily here so the
    main process doesn't need to pre-load them.
    """
    try:
        return _run(language, file_strs, root_str, cache_data, resolver_config)
    except Exception as e:
        return WorkerResult(language=language, error=str(e))


def _run(language, file_strs, root_str, cache_data, resolver_config):
    import os, sys
    root = Path(root_str)

    # Add project root to path so local imports work in subprocess
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    from extractors.python_extractor import PythonExtractor
    from extractors.js_extractor     import JsExtractor, TsExtractor, TsxExtractor
    from extractors.go_extractor     import GoExtractor
    from extractors.generic_extractor import GenericExtractor

    EXTRACTOR_MAP = {
        'python':     PythonExtractor(),
        'javascript': JsExtractor(),
        'typescript': TsExtractor(),
        'tsx':        TsxExtractor(),
        'go':         GoExtractor(),
        'generic':    GenericExtractor(),
    }

    extractor = EXTRACTOR_MAP.get(language)
    if extractor is None:
        return WorkerResult(language=language, error=f'Unknown language: {language}')

    # Build resolver for this language
    resolver = _make_resolver(language, root, resolver_config)

    result = WorkerResult(language=language)
    result.stats = {'local': 0, 'stdlib': 0, 'third_party': 0,
                    'external': 0, 'unknown': 0}

    # Build set of all known node IDs (for edge validation)
    known_nodes: set[str] = set(resolver_config.get('all_node_ids', []))

    for path_str in file_strs:
        filepath = Path(path_str)
        rel      = str(filepath.relative_to(root))

        # ── Cache check ───────────────────────────────────────────────────
        try:
            stat = filepath.stat()
        except OSError:
            continue

        cached = cache_data.get(rel)
        if (cached
                and cached.get('mtime') == stat.st_mtime
                and cached.get('size')  == stat.st_size):
            # Use cached imports — still need to resolve to edges
            raw_imports = cached['imports']
            for imp_dict in raw_imports:
                _resolve_and_add(imp_dict, language, filepath, root,
                                 resolver, known_nodes, result)
            continue

        # ── Fresh extraction ──────────────────────────────────────────────
        extract_result = extractor.extract(filepath, root)
        if extract_result.syntax_error:
            result.syntax_errors += 1
            continue

        # Serialize imports for cache storage
        serialized = [
            {'source_file': imp.source_file, 'raw': imp.raw,
             'line': imp.line, 'kind': imp.kind, 'is_relative': imp.is_relative}
            for imp in extract_result.imports
        ]
        result.cache_updates[rel] = {
            'mtime':   stat.st_mtime,
            'size':    stat.st_size,
            'imports': serialized,
        }

        for imp_dict in serialized:
            _resolve_and_add(imp_dict, language, filepath, root,
                             resolver, known_nodes, result)

    return result


def _resolve_and_add(imp_dict, language, filepath, root, resolver,
                     known_nodes, result: WorkerResult):
    """Resolve one import dict and append edge if LOCAL + known node."""
    resolved_path, kind = resolver(imp_dict['raw'], imp_dict['is_relative'],
                                   language, filepath, root)

    kind_key = kind.lower()
    result.stats[kind_key] = result.stats.get(kind_key, 0) + 1

    if kind == 'LOCAL' and resolved_path and resolved_path in known_nodes:
        result.edges.append({
            'source':   imp_dict['source_file'],
            'target':   resolved_path,
            'type':     imp_dict['kind'],
            'line':     imp_dict['line'],
            'language': language,
        })


def _make_resolver(language: str, root: Path, config: dict):
    """
    Returns a callable: (raw, is_relative, language, filepath, root) → (path|None, kind)
    Built from serialized config so it works across process boundaries.
    """
    if language == 'python':
        from resolver import PythonResolver, ProjectConfig
        py_cfg         = ProjectConfig(root=root)
        py_cfg.src_dirs    = [root / d for d in config.get('py_src_dirs', [])]
        if root not in py_cfg.src_dirs:
            py_cfg.src_dirs.insert(0, root)
        py_cfg.third_party = set(config.get('py_third_party', []))
        py_cfg.package_name= config.get('py_package_name', '')
        res = PythonResolver(root, py_cfg)

        def resolve_py(raw, is_relative, lang, filepath, root):
            level = 0
            s     = raw
            while s.startswith('.'):
                level += 1
                s = s[1:]
            r = res.resolve(s, level=level, from_file=filepath)
            return r.path, r.kind

        return resolve_py

    elif language in ('javascript', 'typescript', 'tsx'):
        from resolvers.js_resolver import JsResolver, JsProjectConfig
        js_cfg         = JsProjectConfig(root=root)
        js_cfg.aliases = config.get('js_aliases', {})
        js_cfg.base_url= config.get('js_base_url', '')
        js_res = JsResolver(root, js_cfg)

        def resolve_js(raw, is_relative, lang, filepath, root):
            path, kind = js_res.resolve(raw, from_file=filepath)
            # Normalize EXTERNAL to THIRD_PARTY for consistent stats
            if kind == 'EXTERNAL': kind = 'THIRD_PARTY'
            return path, kind

        return resolve_js

    elif language == 'go':
        from resolvers.go_resolver import GoResolver
        go_res = GoResolver(root)

        def resolve_go(raw, is_relative, lang, filepath, root):
            return go_res.resolve(raw)

        return resolve_go

    else:
        def resolve_generic(raw, is_relative, lang, filepath, root):
            if is_relative:
                base = (filepath.parent / raw).resolve()
                if base.exists():
                    try:
                        return str(base.relative_to(root)), 'LOCAL'
                    except ValueError:
                        pass
            return None, 'UNKNOWN'

        return resolve_generic
