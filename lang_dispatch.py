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
from fnmatch import fnmatch
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from cache import CachedFile, ImportCache
from crawler import SKIP_DIRS, SKIP_EXTENSIONS
from entrypoints import detect_entrypoints
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


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


def _matches_glob(relpath: str, basename: str, patterns: list[str]) -> bool:
    rel = relpath.replace('\\', '/').strip('/')
    candidates = {rel, f'{rel}/', basename}
    return any(fnmatch(candidate, pattern) for pattern in patterns for candidate in candidates)


def _ignored_dir(dirname: str, relpath: str, patterns: list[str]) -> bool:
    return bool(patterns) and _matches_glob(relpath, dirname, patterns)


def _ignored_file(filename: str, relpath: str, patterns: list[str]) -> bool:
    return bool(patterns) and _matches_glob(relpath, filename, patterns)


def _config_ignore(config: dict | None) -> tuple[list[str], list[str]]:
    ignore = config.get('ignore', {}) if isinstance(config, dict) else {}
    if not isinstance(ignore, dict):
        return [], []
    dirs = [pattern.replace('\\', '/') for pattern in _as_list(ignore.get('dirs')) if pattern]
    files = [pattern.replace('\\', '/') for pattern in _as_list(ignore.get('files')) if pattern]
    return dirs, files


def crawl_all(root: Path, config: dict | None = None) -> dict[str, list[Path]]:
    buckets: dict[str, list[Path]] = {}
    ignore_dirs, ignore_files = _config_ignore(config)

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        kept_dirs = []
        for dirname in dirnames:
            dir_rel = relative_to_root(Path(dirpath) / dirname, root) or dirname
            if dirname in SKIP_DIRS or dirname.startswith('.') or dirname.endswith('.egg-info'):
                continue
            if _ignored_dir(dirname, dir_rel, ignore_dirs):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = sorted(kept_dirs)
        for filename in filenames:
            if filename.startswith('.'):
                continue
            filepath = Path(dirpath) / filename
            if filepath.suffix in SKIP_EXTENSIONS:
                continue
            rel = relative_to_root(filepath, root)
            if not rel or _ignored_file(filename, rel, ignore_files):
                continue
            lang = EXT_TO_LANG.get(filepath.suffix.lower())
            if lang:
                buckets.setdefault(lang, []).append(filepath)

    return buckets


def _rel_list(root: Path, values) -> list[str]:
    result = []
    for value in _as_list(values):
        raw = value.replace('\\', '/').strip()
        if not raw:
            continue
        path = Path(raw)
        if path.is_absolute():
            rel = relative_to_root(path, root)
            result.append(rel if rel else raw)
        else:
            result.append(raw.strip('/'))
    return result


def _resolver_section(overrides: dict, *names: str) -> dict:
    for name in names:
        section = overrides.get(name)
        if isinstance(section, dict):
            return section
    return {}


def _build_resolver_config(root: Path, all_node_ids: list[str], config: dict | None = None) -> dict:
    from resolver import ProjectConfig
    from resolvers.c_family_resolver import CProjectConfig
    from resolvers.js_resolver import JsProjectConfig
    from resolvers.jvm_resolver import JvmProjectConfig

    py_cfg = ProjectConfig.detect(root)
    js_cfg = JsProjectConfig.detect(root)
    c_cfg = CProjectConfig.detect(root)
    jvm_cfg = JvmProjectConfig.detect(root)

    resolver_overrides = config.get('resolver_overrides', {}) if isinstance(config, dict) else {}
    if not isinstance(resolver_overrides, dict):
        resolver_overrides = {}

    py_override = _resolver_section(resolver_overrides, 'python', 'py')
    js_override = _resolver_section(resolver_overrides, 'javascript', 'js', 'typescript', 'ts')
    c_override = _resolver_section(resolver_overrides, 'c_family', 'c', 'cpp', 'cxx')
    jvm_override = _resolver_section(resolver_overrides, 'jvm', 'java', 'kotlin')

    py_src_dirs = [rel for d in py_cfg.src_dirs if d != root if (rel := relative_to_root(d, root))]
    if 'src_dirs' in py_override:
        py_src_dirs = _rel_list(root, py_override.get('src_dirs'))
    py_third_party = list(py_cfg.third_party)
    if 'third_party' in py_override:
        py_third_party = _as_list(py_override.get('third_party'))
    py_package_name = py_cfg.package_name
    if 'package_name' in py_override:
        py_package_name = str(py_override.get('package_name') or '')

    js_aliases = js_cfg.aliases
    if isinstance(js_override.get('aliases'), dict):
        js_aliases = dict(js_override.get('aliases'))
    js_base_url = js_cfg.base_url
    if 'base_url' in js_override:
        js_base_url = str(js_override.get('base_url') or '')

    c_include_dirs = [rel for d in c_cfg.include_dirs if d != root if (rel := relative_to_root(d, root))]
    if 'include_dirs' in c_override:
        c_include_dirs = _rel_list(root, c_override.get('include_dirs'))

    jvm_src_roots = [rel for d in jvm_cfg.src_roots if d != root if (rel := relative_to_root(d, root))]
    if 'src_roots' in jvm_override:
        jvm_src_roots = _rel_list(root, jvm_override.get('src_roots'))

    return {
        'py_src_dirs': py_src_dirs,
        'py_third_party': py_third_party,
        'py_package_name': py_package_name,
        'js_aliases': js_aliases,
        'js_base_url': js_base_url,
        'c_include_dirs': c_include_dirs,
        'jvm_src_roots': jvm_src_roots,
        'resolver_overrides': resolver_overrides,
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




def _load_intentional_files(root: Path, config: dict | None = None) -> list[str]:
    configured = []
    if isinstance(config, dict):
        configured = [
            item.replace('\\', '/')
            for item in _as_list(config.get('intentional_files'))
            if item
        ]
    marker = root / '.orbits_intentional.json'
    if not marker.exists():
        return sorted(set(configured))
    try:
        import json
        data = json.loads(marker.read_text(encoding='utf-8'))
    except Exception:
        return sorted(set(configured))
    files = data.get('intentional_files', []) if isinstance(data, dict) else []
    return sorted({*configured, *(str(item).replace('\\', '/') for item in files if isinstance(item, str))})

def _run_sequential_workers(buckets, root_str, cache_snapshot, resolver_config):
    return [run_worker(lang, [str(f) for f in files], root_str, cache_snapshot, resolver_config) for lang, files in buckets.items()]


def extract_all(root: Path, verbose: bool = True, config: dict | None = None) -> dict:
    t_start = time.time()

    def log(message: str):
        if verbose:
            print(message, file=sys.stderr)

    log(f"  Scanning:  {root}")

    buckets = crawl_all(root, config=config)
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

    node_ids = list(nodes.keys())
    resolver_config = _build_resolver_config(root, node_ids, config=config)
    entrypoints = detect_entrypoints(root, node_ids)

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
    if entrypoints:
        log(f"  Entrypoints: {len(entrypoints)} detected")

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

    intentional_files = _load_intentional_files(root, config=config)

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
            'entrypoints': entrypoints,
            'config': {
                'files': list(config.get('files', [])) if isinstance(config, dict) else [],
                'ignore': dict(config.get('ignore', {})) if isinstance(config, dict) else {},
                'check': dict(config.get('check', {})) if isinstance(config, dict) else {},
                'resolver_overrides': dict(config.get('resolver_overrides', {})) if isinstance(config, dict) else {},
            },
        },
    }
