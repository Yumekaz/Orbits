"""
lang_dispatch.py - Orbits Phase 3

Multi-language extraction with:
  - Parallel execution  (one worker process per language when available)
  - Incremental cache   (skip unchanged files on reruns)
  - Tree-sitter parsing for the first-class language set

Entry point: extract_all(root) -> raw graph dict
"""

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from cache import CachedFile, ImportCache
from crawler import SKIP_DIRS, SKIP_EXTENSIONS
from path_utils import relative_to_root
from worker import WorkerResult, run_worker

LANG_DISPLAY = {
    'python': 'Python',
    'javascript': 'JavaScript',
    'typescript': 'TypeScript',
    'tsx': 'TSX',
    'go': 'Go',
    'c': 'C',
    'cpp': 'C/C++',
    'java': 'Java',
    'kotlin': 'Kotlin',
    'generic': 'Generic',
}

EXT_TO_LANG = {
    '.py': 'python', '.pyi': 'python',
    '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
    '.ts': 'typescript', '.mts': 'typescript', '.cts': 'typescript',
    '.tsx': 'tsx', '.jsx': 'tsx',
    '.go': 'go',
    '.c': 'c',
    '.h': 'cpp', '.hh': 'cpp', '.hpp': 'cpp', '.hxx': 'cpp', '.cc': 'cpp', '.cpp': 'cpp', '.cxx': 'cpp',
    '.java': 'java',
    '.kt': 'kotlin', '.kts': 'kotlin',
    '.rb': 'generic', '.rs': 'generic', '.cs': 'generic',
    '.swift': 'generic', '.lua': 'generic', '.php': 'generic', '.dart': 'generic', '.scala': 'generic',
}


def crawl_all(root: Path) -> dict[str, list[Path]]:
    buckets: dict[str, list[Path]] = {}

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = sorted([
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith('.') and not d.endswith('.egg-info')
        ])
        for filename in filenames:
            if filename.startswith('.'):
                continue
            filepath = Path(dirpath) / filename
            if filepath.suffix in SKIP_EXTENSIONS:
                continue
            lang = EXT_TO_LANG.get(filepath.suffix.lower())
            if lang:
                buckets.setdefault(lang, []).append(filepath)

    return buckets


def _build_resolver_config(root: Path, all_node_ids: list[str]) -> dict:
    from resolver import ProjectConfig
    from resolvers.c_family_resolver import CProjectConfig
    from resolvers.js_resolver import JsProjectConfig
    from resolvers.jvm_resolver import JvmProjectConfig

    py_cfg = ProjectConfig.detect(root)
    js_cfg = JsProjectConfig.detect(root)
    c_cfg = CProjectConfig.detect(root)
    jvm_cfg = JvmProjectConfig.detect(root)

    return {
        'py_src_dirs': [rel for d in py_cfg.src_dirs if d != root if (rel := relative_to_root(d, root))],
        'py_third_party': list(py_cfg.third_party),
        'py_package_name': py_cfg.package_name,
        'js_aliases': js_cfg.aliases,
        'js_base_url': js_cfg.base_url,
        'c_include_dirs': [rel for d in c_cfg.include_dirs if d != root if (rel := relative_to_root(d, root))],
        'jvm_src_roots': [rel for d in jvm_cfg.src_roots if d != root if (rel := relative_to_root(d, root))],
        'all_node_ids': all_node_ids,
    }


def _detect_language_support() -> dict[str, dict[str, str | bool]]:
    from extractors.c_family_extractor import _G as c_grammars
    from extractors.go_extractor import _G as go_grammar
    from extractors.js_extractor import _G as js_grammar
    from extractors.jvm_extractor import _G as jvm_grammars
    from extractors.python_extractor import _G as py_grammar

    support = {
        'python': {'available': py_grammar is not None, 'reason': ''},
        'javascript': {'available': js_grammar is not None, 'reason': ''},
        'typescript': {'available': js_grammar is not None, 'reason': ''},
        'tsx': {'available': js_grammar is not None, 'reason': ''},
        'go': {'available': go_grammar is not None, 'reason': ''},
        'c': {'available': c_grammars is not None, 'reason': ''},
        'cpp': {'available': c_grammars is not None, 'reason': ''},
        'java': {'available': jvm_grammars is not None, 'reason': ''},
        'kotlin': {'available': jvm_grammars is not None, 'reason': ''},
        'generic': {'available': True, 'reason': ''},
    }

    if not py_grammar:
        support['python']['reason'] = 'tree-sitter Python grammar is not installed.'
    if not js_grammar:
        reason = 'tree-sitter JavaScript/TypeScript grammars are not installed.'
        support['javascript']['reason'] = reason
        support['typescript']['reason'] = reason
        support['tsx']['reason'] = reason
    if not go_grammar:
        support['go']['reason'] = 'tree-sitter Go grammar is not installed.'
    if not c_grammars:
        reason = 'tree-sitter C/C++ grammars are not installed.'
        support['c']['reason'] = reason
        support['cpp']['reason'] = reason
    if not jvm_grammars:
        reason = 'tree-sitter Java/Kotlin grammars are not installed.'
        support['java']['reason'] = reason
        support['kotlin']['reason'] = reason
    return support




def _load_intentional_files(root: Path) -> list[str]:
    marker = root / '.orbits_intentional.json'
    if not marker.exists():
        return []
    try:
        import json
        data = json.loads(marker.read_text(encoding='utf-8'))
    except Exception:
        return []
    files = data.get('intentional_files', []) if isinstance(data, dict) else []
    return sorted({str(item).replace('\\', '/') for item in files if isinstance(item, str)})

def _run_sequential_workers(buckets, root_str, cache_snapshot, resolver_config):
    return [run_worker(lang, [str(f) for f in files], root_str, cache_snapshot, resolver_config) for lang, files in buckets.items()]


def extract_all(root: Path, verbose: bool = True) -> dict:
    t_start = time.time()

    def log(message: str):
        if verbose:
            print(message, file=sys.stderr)

    log(f"  Scanning:  {root}")

    buckets = crawl_all(root)
    total_files = sum(len(files) for files in buckets.values())
    language_support = _detect_language_support()

    if not total_files:
        log('  WARNING: No source files found.')

    for lang in sorted(buckets):
        status = language_support.get(lang, {'available': True, 'reason': ''})
        suffix = '' if status['available'] else ' (parser unavailable)'
        log(f"  Found:  {len(buckets[lang]):6d}  {LANG_DISPLAY.get(lang, lang)}{suffix}")
        if not status['available'] and status['reason']:
            log(f"  WARNING: {LANG_DISPLAY.get(lang, lang)} support unavailable: {status['reason']}")

    cache = ImportCache(root)

    nodes: dict[str, dict] = {}
    for lang, files in buckets.items():
        for filepath in files:
            rel = relative_to_root(filepath, root)
            if not rel:
                continue
            stat = filepath.stat()
            nodes[rel] = {
                'id': rel,
                'filepath': rel,
                'name': filepath.name,
                'language': lang,
                'size': stat.st_size,
                'mtime': round(stat.st_mtime),
                'dir': relative_to_root(filepath.parent, root) if filepath.parent != root else '.',
            }

    resolver_config = _build_resolver_config(root, list(nodes.keys()))

    if resolver_config.get('py_package_name'):
        log(f"  Package:   {resolver_config['py_package_name']}")
    for src_dir in resolver_config.get('py_src_dirs', []):
        log(f"  Src dir:   {src_dir}")
    for alias, target in resolver_config.get('js_aliases', {}).items():
        log(f"  TS alias:  {alias} -> {target}")
    for include_dir in resolver_config.get('c_include_dirs', []):
        log(f"  C include: {include_dir}")
    for src_root in resolver_config.get('jvm_src_roots', []):
        log(f"  JVM src:   {src_root}")

    cache_snapshot: dict[str, dict] = {}
    for files in buckets.values():
        for filepath in files:
            rel = relative_to_root(filepath, root)
            if not rel:
                continue
            entry = cache._data.get(rel)
            if entry:
                cache_snapshot[rel] = {'mtime': entry.mtime, 'size': entry.size, 'imports': entry.imports}

    log(f"  Cache:     {len(cache_snapshot)} files cached  ({len(nodes) - len(cache_snapshot)} to parse)")
    log('  Extracting imports...')

    all_edges: list[dict] = []
    total_stats = {'local': 0, 'stdlib': 0, 'third_party': 0, 'external': 0, 'unknown': 0}
    total_syntax_errors = 0
    num_langs = len(buckets)
    max_workers = min(num_langs, os.cpu_count() or 4) if num_langs else 1
    root_str = str(root)

    if num_langs == 0:
        worker_results: list[WorkerResult] = []
    elif num_langs == 1:
        worker_results = _run_sequential_workers(buckets, root_str, cache_snapshot, resolver_config)
    else:
        try:
            worker_results = []
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for lang, files in buckets.items():
                    future = executor.submit(run_worker, lang, [str(f) for f in files], root_str, cache_snapshot, resolver_config)
                    futures[future] = lang
                for future in as_completed(futures):
                    try:
                        worker_results.append(future.result(timeout=120))
                    except Exception as exc:
                        lang = futures[future]
                        log(f"  WARNING: {lang} worker crashed: {exc}")
                        worker_results.append(WorkerResult(language=lang, error=str(exc)))
        except (OSError, PermissionError) as exc:
            log(f"  WARNING: parallel workers unavailable, falling back to sequential execution: {exc}")
            worker_results = _run_sequential_workers(buckets, root_str, cache_snapshot, resolver_config)

    for res in worker_results:
        if res.error:
            log(f"  WARNING: {res.language} worker error: {res.error}")
            continue
        all_edges.extend(res.edges)
        for key, value in res.stats.items():
            total_stats[key] = total_stats.get(key, 0) + value
        total_syntax_errors += res.syntax_errors
        for rel, entry in res.cache_updates.items():
            cache._data[rel] = CachedFile(mtime=entry['mtime'], size=entry['size'], imports=entry['imports'])
            cache._dirty = True

    seen: set[tuple[str, str]] = set()
    unique_edges: list[dict] = []
    for edge in all_edges:
        key = (edge['source'], edge['target'])
        if key not in seen:
            seen.add(key)
            unique_edges.append(edge)

    cache.save()

    unsupported_languages = [
        {'language': lang, 'reason': status['reason'], 'files': len(buckets.get(lang, []))}
        for lang, status in language_support.items()
        if not status['available'] and buckets.get(lang)
    ]

    intentional_files = _load_intentional_files(root)

    total_imports = sum(total_stats.values())
    pct = round(total_stats['local'] / total_imports * 100, 1) if total_imports else 0.0
    elapsed = time.time() - t_start

    log(
        f"  Imports:   {total_imports} total - "
        f"{total_stats['local']} local ({pct}%)  "
        f"{total_stats.get('stdlib', 0)} stdlib  "
        f"{total_stats.get('third_party', 0) + total_stats.get('external', 0)} 3rd-party  "
        f"{total_stats.get('unknown', 0)} unknown"
    )
    log(f"  Edges:     {len(unique_edges)} internal")
    if total_syntax_errors:
        log(f"  Skipped:   {total_syntax_errors} files with syntax errors")
    if unsupported_languages:
        log('  Support:   parser packages missing for some detected languages')
    log(f"  Time:      {elapsed:.2f}s")

    return {
        'nodes': list(nodes.values()),
        'edges': unique_edges,
        'meta': {
            'root': str(root),
            'total_files': len(nodes),
            'total_edges': len(unique_edges),
            'languages': list(buckets.keys()),
            'import_stats': total_stats,
            'package_name': resolver_config.get('py_package_name', ''),
            'phase': 3,
            'elapsed_s': round(elapsed, 2),
            'language_support': language_support,
            'unsupported_languages': unsupported_languages,
            'intentional_files': intentional_files,
        },
    }
