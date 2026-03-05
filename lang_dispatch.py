"""
lang_dispatch.py — Orbits Phase 3

Multi-language extraction with:
  - Parallel execution  (one worker process per language)
  - Incremental cache   (skip unchanged files on reruns)
  - Query-based parsing (tree-sitter Query+QueryCursor)

Entry point: extract_all(root) → raw graph dict
"""

import sys
import os
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

from crawler import SKIP_DIRS, SKIP_EXTENSIONS
from cache   import ImportCache
from worker  import run_worker, WorkerResult

# Language display names
LANG_DISPLAY = {
    'python':     'Python',
    'javascript': 'JavaScript',
    'typescript': 'TypeScript',
    'tsx':        'TSX',
    'go':         'Go',
    'generic':    'Generic',
}

# Extension → language (used for crawling only)
EXT_TO_LANG = {
    '.py': 'python', '.pyi': 'python',
    '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
    '.ts': 'typescript', '.mts': 'typescript', '.cts': 'typescript',
    '.tsx': 'tsx', '.jsx': 'tsx',
    '.go': 'go',
    # Generic fallback
    '.rb': 'generic', '.rs': 'generic', '.cs': 'generic',
    '.java': 'generic', '.kt': 'generic', '.swift': 'generic',
    '.cpp': 'generic', '.cc': 'generic', '.cxx': 'generic',
    '.c': 'generic', '.h': 'generic', '.hpp': 'generic',
    '.lua': 'generic', '.php': 'generic', '.dart': 'generic',
    '.scala': 'generic',
}


# ── Crawl ──────────────────────────────────────────────────────────────────

def crawl_all(root: Path) -> dict[str, list[Path]]:
    """Walk tree, skip noise, bucket by language."""
    buckets: dict[str, list[Path]] = {}

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = sorted([
            d for d in dirnames
            if d not in SKIP_DIRS
            and not d.startswith('.')
            and not d.endswith('.egg-info')
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


# ── Resolver config serialization ─────────────────────────────────────────

def _build_resolver_config(root: Path, all_node_ids: list[str]) -> dict:
    """
    Serialize resolver config into a plain dict that can be pickled
    and sent to worker processes.
    """
    from resolver import ProjectConfig
    from resolvers.js_resolver import JsProjectConfig

    py_cfg = ProjectConfig.detect(root)
    js_cfg = JsProjectConfig.detect(root)

    return {
        # Python
        'py_src_dirs':    [str(d.relative_to(root)) for d in py_cfg.src_dirs
                          if d != root],
        'py_third_party': list(py_cfg.third_party),
        'py_package_name': py_cfg.package_name,
        # JS/TS
        'js_aliases':     js_cfg.aliases,
        'js_base_url':    js_cfg.base_url,
        # All node IDs (for edge validation in workers)
        'all_node_ids':   all_node_ids,
    }


# ── Main extraction ────────────────────────────────────────────────────────

def extract_all(root: Path, verbose: bool = True) -> dict:
    """
    Full multi-language extraction pipeline with parallel workers + cache.
    Returns raw graph dict for graph_engine.analyze_graph().
    """
    t_start = time.time()

    def log(msg):
        if verbose:
            print(msg, file=sys.stderr)

    log(f"  Scanning:  {root}")

    # ── Crawl ──────────────────────────────────────────────────────────────
    buckets     = crawl_all(root)
    total_files = sum(len(v) for v in buckets.values())

    if not total_files:
        log("  WARNING: No source files found.")

    for lang in sorted(buckets):
        log(f"  Found:  {len(buckets[lang]):6d}  {LANG_DISPLAY.get(lang, lang)}")

    # ── Load cache ─────────────────────────────────────────────────────────
    cache = ImportCache(root)

    # ── Build node registry ────────────────────────────────────────────────
    nodes: dict[str, dict] = {}
    for lang, files in buckets.items():
        for filepath in files:
            try:
                rel = str(filepath.relative_to(root))
            except ValueError:
                continue
            stat = filepath.stat()
            nodes[rel] = {
                'id':       rel,
                'filepath': rel,
                'name':     filepath.name,
                'language': lang,
                'size':     stat.st_size,
                'dir':      str(filepath.parent.relative_to(root))
                            if filepath.parent != root else '.',
            }

    # ── Resolver config (serializable for workers) ────────────────────────
    resolver_config = _build_resolver_config(root, list(nodes.keys()))

    # Log resolver info
    if resolver_config.get('py_package_name'):
        log(f"  Package:   {resolver_config['py_package_name']}")
    for src_dir in resolver_config.get('py_src_dirs', []):
        log(f"  Src dir:   {src_dir}")
    for alias, target in resolver_config.get('js_aliases', {}).items():
        log(f"  TS alias:  {alias} → {target}")

    # ── Serialize cache for workers ────────────────────────────────────────
    # Send only the cache entries relevant to each language's files
    cache_snapshot: dict[str, dict] = {}
    for lang, files in buckets.items():
        for filepath in files:
            try:
                rel = str(filepath.relative_to(root))
            except ValueError:
                continue
            entry = cache._data.get(rel)
            if entry:
                cache_snapshot[rel] = {
                    'mtime':   entry.mtime,
                    'size':    entry.size,
                    'imports': entry.imports,
                }

    log(f"  Cache:     {len(cache_snapshot)} files cached  "
        f"({len(nodes) - len(cache_snapshot)} to parse)")
    log(f"  Extracting imports...")

    # ── Parallel workers ───────────────────────────────────────────────────
    all_edges:     list[dict] = []
    total_stats                = {'local': 0, 'stdlib': 0, 'third_party': 0,
                                   'external': 0, 'unknown': 0}
    total_syntax_errors        = 0

    # Determine worker count: one per language, capped at CPU count
    num_langs   = len(buckets)
    max_workers = min(num_langs, os.cpu_count() or 4)

    root_str    = str(root)

    if num_langs == 1:
        # Skip subprocess overhead for single-language projects
        lang       = next(iter(buckets))
        file_strs  = [str(f) for f in buckets[lang]]
        result     = run_worker(lang, file_strs, root_str,
                                cache_snapshot, resolver_config)
        worker_results = [result]
    else:
        # Parallel: one process per language
        worker_results = []
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for lang, files in buckets.items():
                file_strs = [str(f) for f in files]
                future    = executor.submit(
                    run_worker, lang, file_strs, root_str,
                    cache_snapshot, resolver_config
                )
                futures[future] = lang

            for future in as_completed(futures):
                try:
                    res = future.result(timeout=120)
                except Exception as e:
                    lang = futures[future]
                    log(f"  WARNING: {lang} worker crashed: {e}")
                    res = WorkerResult(language=futures[future], error=str(e))
                worker_results.append(res)

    # ── Merge results ──────────────────────────────────────────────────────
    for res in worker_results:
        if res.error:
            log(f"  WARNING: {res.language} worker error: {res.error}")
            continue

        all_edges.extend(res.edges)
        for k, v in res.stats.items():
            total_stats[k] = total_stats.get(k, 0) + v
        total_syntax_errors += res.syntax_errors

        # Write cache updates back
        for rel, entry in res.cache_updates.items():
            filepath = root / rel
            cache._data[rel]   = type('E', (), {
                'mtime':   entry['mtime'],
                'size':    entry['size'],
                'imports': entry['imports'],
            })()
            cache._dirty = True

    # ── Deduplicate edges ──────────────────────────────────────────────────
    seen: set[tuple] = set()
    unique_edges: list[dict] = []
    for edge in all_edges:
        key = (edge['source'], edge['target'])
        if key not in seen:
            seen.add(key)
            unique_edges.append(edge)

    # ── Save cache ─────────────────────────────────────────────────────────
    cache.save()

    # ── Report ─────────────────────────────────────────────────────────────
    total_imports = sum(total_stats.values())
    pct = round(total_stats['local'] / total_imports * 100, 1) if total_imports else 0.0
    elapsed = time.time() - t_start

    log(f"  Imports:   {total_imports} total — "
        f"{total_stats['local']} local ({pct}%)  "
        f"{total_stats.get('stdlib',0)} stdlib  "
        f"{total_stats.get('third_party',0)+total_stats.get('external',0)} 3rd-party  "
        f"{total_stats.get('unknown',0)} unknown")
    log(f"  Edges:     {len(unique_edges)} internal")
    if total_syntax_errors:
        log(f"  Skipped:   {total_syntax_errors} files with syntax errors")
    log(f"  Time:      {elapsed:.2f}s")

    return {
        'nodes': list(nodes.values()),
        'edges': unique_edges,
        'meta': {
            'root':         str(root),
            'total_files':  len(nodes),
            'total_edges':  len(unique_edges),
            'languages':    list(buckets.keys()),
            'import_stats': total_stats,
            'package_name': resolver_config.get('py_package_name', ''),
            'phase':        3,
            'elapsed_s':    round(elapsed, 2),
        },
    }
