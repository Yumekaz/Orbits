"""
analyzer.py - Orbits Phase 3

Multi-language dependency graph analyzer.
Supports: Python, JavaScript, TypeScript, Go + generic fallback
Phase 5: optional Python and Node.js runtime tracing.

Usage:
    python analyzer.py /path/to/project --serve
    python analyzer.py /path/to/project -o graph.json
    python analyzer.py --diff old_graph.json new_graph.json
"""

import argparse
import csv
import http.server
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from graph_diff import diff_graph_files, format_graph_diff
from graph_engine import analyze_graph
from lang_dispatch import extract_all
from runtime_trace import (
    CppRuntimeTraceConfig,
    NodeRuntimeTraceConfig,
    PythonRuntimeTraceConfig,
    merge_runtime_traces,
    run_runtime_trace,
)


CONFIG_FILENAMES = ('codegraph.config.json', '.orbits.json')


def _as_string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


def _merge_config(base: dict, incoming: dict) -> dict:
    merged = dict(base)
    for key, value in incoming.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_config(merged[key], value)
        elif key in merged and isinstance(merged[key], list) and isinstance(value, list):
            merged[key] = [*merged[key], *value]
        else:
            merged[key] = value
    return merged


def _normalize_relpath(value: str) -> str:
    return str(value).replace('\\', '/').strip()


def _unique_strings(values: list[str]) -> list[str]:
    return sorted({item for item in (_normalize_relpath(value) for value in values) if item})


def _normalize_check_thresholds(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return {}
    aliases = {
        'max_orphans': ('max_orphans', 'maxOrphans', 'orphans'),
        'max_islands': ('max_islands', 'maxIslands', 'islands'),
        'min_health': ('min_health', 'minHealth', 'health'),
    }
    normalized = {}
    for key, names in aliases.items():
        for name in names:
            if name not in raw:
                continue
            value = raw.get(name)
            if value is None or value == '':
                continue
            try:
                normalized[key] = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f'Invalid check threshold {name}: {value!r}') from exc
            break
    return normalized


def normalize_project_config(raw: dict | None, config_files: list[str] | None = None) -> dict:
    raw = raw if isinstance(raw, dict) else {}

    ignore_dirs: list[str] = []
    ignore_files: list[str] = []
    ignore_common: list[str] = []
    ignore = raw.get('ignore')
    if isinstance(ignore, dict):
        ignore_dirs.extend(_as_string_list(ignore.get('dirs')))
        ignore_dirs.extend(_as_string_list(ignore.get('directories')))
        ignore_dirs.extend(_as_string_list(ignore.get('dir_globs')))
        ignore_files.extend(_as_string_list(ignore.get('files')))
        ignore_files.extend(_as_string_list(ignore.get('file_globs')))
        ignore_common.extend(_as_string_list(ignore.get('patterns')))
        ignore_common.extend(_as_string_list(ignore.get('globs')))
    else:
        ignore_common.extend(_as_string_list(ignore))

    ignore_dirs.extend(_as_string_list(raw.get('ignore_dirs')))
    ignore_dirs.extend(_as_string_list(raw.get('ignoreDirectories')))
    ignore_dirs.extend(_as_string_list(raw.get('ignore_dir_globs')))
    ignore_files.extend(_as_string_list(raw.get('ignore_files')))
    ignore_files.extend(_as_string_list(raw.get('ignoreFiles')))
    ignore_files.extend(_as_string_list(raw.get('ignore_file_globs')))

    intentional_files = []
    intentional_files.extend(_as_string_list(raw.get('intentional_files')))
    intentional_files.extend(_as_string_list(raw.get('intentionalFiles')))

    check_raw = {}
    if isinstance(raw.get('thresholds'), dict):
        check_raw = _merge_config(check_raw, raw['thresholds'])
    if isinstance(raw.get('check'), dict):
        check_raw = _merge_config(check_raw, raw['check'])
    if isinstance(raw.get('checkThresholds'), dict):
        check_raw = _merge_config(check_raw, raw['checkThresholds'])

    resolver_overrides = {}
    for key in ('resolvers', 'resolver_overrides', 'resolverOverrides'):
        if isinstance(raw.get(key), dict):
            resolver_overrides = _merge_config(resolver_overrides, raw[key])

    return {
        'files': list(config_files or []),
        'ignore': {
            'dirs': _unique_strings([*ignore_common, *ignore_dirs]),
            'files': _unique_strings([*ignore_common, *ignore_files]),
        },
        'intentional_files': _unique_strings(intentional_files),
        'check': _normalize_check_thresholds(check_raw),
        'resolver_overrides': resolver_overrides,
    }


def load_project_config(root: str | Path) -> dict:
    root_path = Path(root).resolve()
    merged: dict = {}
    loaded: list[str] = []
    for name in CONFIG_FILENAMES:
        config_path = root_path / name
        if not config_path.exists():
            continue
        try:
            data = json.loads(config_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise ValueError(f'Invalid JSON in {config_path}: {exc}') from exc
        if not isinstance(data, dict):
            raise ValueError(f'Config file must contain a JSON object: {config_path}')
        merged = _merge_config(merged, data)
        loaded.append(name)
    return normalize_project_config(merged, loaded)


def _load_graph_payload(graph_path: Path) -> dict:
    return json.loads(graph_path.read_text(encoding='utf-8'))


def _graph_root_from_file(graph_path: Path) -> Path | None:
    try:
        payload = _load_graph_payload(graph_path)
    except Exception:
        return None
    root = payload.get('meta', {}).get('root')
    return Path(root) if root else None


def _intentional_file_path(root: Path) -> Path:
    return root / '.orbits_intentional.json'


def _load_intentional_state(root: Path) -> dict:
    marker = _intentional_file_path(root)
    if not marker.exists():
        return {'intentional_files': []}
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
    except Exception:
        return {'intentional_files': []}
    if not isinstance(data, dict):
        return {'intentional_files': []}
    files = data.get('intentional_files', [])
    if not isinstance(files, list):
        files = []
    return {'intentional_files': sorted({str(item).replace('\\', '/') for item in files if isinstance(item, str)})}


def _save_intentional_state(root: Path, state: dict) -> None:
    _intentional_file_path(root).write_text(json.dumps(state, indent=2), encoding='utf-8')


def _load_runtime_artifact(path: str | Path) -> tuple[dict, Path] | None:
    artifact_path = Path(path)
    if not artifact_path.exists():
        return None
    try:
        trace = json.loads(artifact_path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return trace, artifact_path.resolve()


def _load_runtime_overlays(payload: dict) -> list[tuple[dict, Path, bool]]:
    overlays: list[tuple[dict, Path, bool]] = []
    if not isinstance(payload, dict):
        return overlays
    runtime_meta = payload.get('meta', {}).get('runtime', {})
    runtime_payload = payload.get('runtime', {}) if isinstance(payload.get('runtime'), dict) else {}
    sessions = runtime_payload.get('sessions') or runtime_meta.get('sessions') or []
    if sessions:
        for session in sessions:
            if not isinstance(session, dict):
                continue
            artifact = session.get('artifact')
            if not artifact:
                continue
            loaded = _load_runtime_artifact(artifact)
            if not loaded:
                continue
            trace, artifact_path = loaded
            overlays.append((trace, artifact_path, bool(session.get('stale', runtime_meta.get('stale', False)))))
        return overlays
    artifact = runtime_meta.get('artifact')
    if not artifact:
        return overlays
    loaded = _load_runtime_artifact(artifact)
    if not loaded:
        return overlays
    trace, artifact_path = loaded
    overlays.append((trace, artifact_path, bool(runtime_meta.get('stale', False))))
    return overlays


def _blame_summary(root: Path, relpath: str) -> dict:
    git_dir = root / '.git'
    if not git_dir.exists():
        return {'available': False, 'summary': 'Not a git repository'}
    try:
        proc = subprocess.run(['git', 'blame', '--line-porcelain', '--', relpath], cwd=root, capture_output=True, text=True, timeout=15)
    except Exception as exc:
        return {'available': False, 'summary': f'Blame unavailable: {exc}'}
    if proc.returncode != 0:
        return {'available': False, 'summary': proc.stderr.strip() or 'Blame unavailable'}
    authors: dict[str, int] = {}
    commits: set[str] = set()
    latest_author = ''
    latest_time = -1
    for line in proc.stdout.splitlines():
        if line.startswith('author '):
            author = line[7:].strip()
            authors[author] = authors.get(author, 0) + 1
            if not latest_author:
                latest_author = author
        elif line.startswith('author-time '):
            try:
                ts = int(line.split(' ', 1)[1])
            except ValueError:
                ts = -1
            if ts > latest_time:
                latest_time = ts
        elif line and not line.startswith(('author ', 'author-mail ', 'author-time ', 'author-tz ', 'summary ', 'filename ', '	', 'committer ', 'committer-mail ', 'committer-time ', 'committer-tz ', 'previous ', 'boundary')):
            commits.add(line.split(' ', 1)[0])
    top = sorted(authors.items(), key=lambda item: (-item[1], item[0]))[:3]
    if not top:
        return {'available': False, 'summary': 'No blame data'}
    summary = ', '.join(f'{name} ({count})' for name, count in top)
    return {'available': True, 'summary': summary, 'commit_count': len(commits)}


def _rerun_graph(graph_path: Path, runtime_stale: bool = False) -> dict:
    previous = _load_graph_payload(graph_path)
    root = _graph_root_from_file(graph_path)
    if not root:
        raise FileNotFoundError('Graph root unavailable for re-analysis')
    overlays = _load_runtime_overlays(previous)
    refreshed = run(root, verbose=False, runtime_overlays=overlays, runtime_stale=runtime_stale and bool(overlays))
    graph_path.write_text(json.dumps(refreshed, indent=2, ensure_ascii=False), encoding='utf-8')
    return refreshed


class _GraphRequestHandler(http.server.BaseHTTPRequestHandler):
    visualizer_path: Path | None = None
    graph_path: Path | None = None
    asset_root: Path | None = None

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == '/api/node-info':
            params = parse_qs(route.query)
            relpath = str((params.get('id') or [''])[0]).replace('\\', '/')
            try:
                target = self._resolve_node_path(relpath)
                stat = target.stat()
                root = _graph_root_from_file(self.graph_path)
                blame = _blame_summary(root, relpath) if root else {'available': False, 'summary': 'Graph root unavailable'}
                intentional = relpath in _load_intentional_state(root).get('intentional_files', []) if root else False
                self._send_json({'ok': True, 'mtime': round(stat.st_mtime), 'mtime_iso': __import__('datetime').datetime.fromtimestamp(stat.st_mtime).isoformat(), 'blame': blame, 'intentional': intentional, 'can_modify': bool(root)})
            except Exception as exc:
                self._send_json({'ok': False, 'error': str(exc)}, status=404)
            return
        filepath, content_type = self._resolve_route()
        if filepath is None:
            self.send_error(404, 'Not Found')
            return
        self._send_file(filepath, content_type)

    def do_HEAD(self):
        filepath, content_type = self._resolve_route()
        if filepath is None:
            self.send_error(404, 'Not Found')
            return
        self._send_headers(filepath, content_type)

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            payload = self._read_json()
            if route == '/api/open-file':
                self._handle_open_file(payload)
                return
            if route == '/api/delete-file':
                self._handle_delete_file(payload)
                return
            if route == '/api/mark-intentional':
                self._handle_mark_intentional(payload)
                return
            if route == '/api/reanalyze':
                self._handle_reanalyze()
                return
            self.send_error(404, 'Not Found')
        except Exception as exc:
            self._send_json({'ok': False, 'error': str(exc)}, status=500)

    def log_message(self, *_args):
        pass


    def _read_json(self) -> dict:
        length = int(self.headers.get('Content-Length', '0'))
        raw = self.rfile.read(length) if length else b'{}'
        if not raw:
            return {}
        return json.loads(raw.decode('utf-8'))

    def _send_json(self, payload: dict, status: int = 200):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.end_headers()
        self.wfile.write(data)

    def _resolve_node_path(self, relpath: str) -> Path:
        root = _graph_root_from_file(self.graph_path)
        if not root:
            raise FileNotFoundError('Graph root unavailable')
        candidate = (root / relpath).resolve()
        candidate.relative_to(root.resolve())
        return candidate

    def _handle_open_file(self, payload: dict):
        relpath = str(payload.get('id', '')).replace('\\', '/')
        target = self._resolve_node_path(relpath)
        if not target.exists():
            raise FileNotFoundError(relpath)
        os.startfile(str(target))
        self._send_json({'ok': True})

    def _handle_delete_file(self, payload: dict):
        relpath = str(payload.get('id', '')).replace('\\', '/')
        target = self._resolve_node_path(relpath)
        if not target.exists():
            raise FileNotFoundError(relpath)
        if target.is_dir():
            raise IsADirectoryError(relpath)
        target.unlink()
        graph = _rerun_graph(self.graph_path, runtime_stale=True)
        self._send_json({'ok': True, 'graph': graph})

    def _handle_mark_intentional(self, payload: dict):
        relpath = str(payload.get('id', '')).replace('\\', '/')
        intentional = bool(payload.get('intentional', True))
        root = _graph_root_from_file(self.graph_path)
        if not root:
            raise FileNotFoundError('Graph root unavailable')
        state = _load_intentional_state(root)
        files = set(state.get('intentional_files', []))
        if intentional:
            files.add(relpath)
        else:
            files.discard(relpath)
        state['intentional_files'] = sorted(files)
        _save_intentional_state(root, state)
        graph = _rerun_graph(self.graph_path, runtime_stale=False)
        self._send_json({'ok': True, 'graph': graph, 'intentional': intentional})

    def _handle_reanalyze(self):
        graph = _rerun_graph(self.graph_path, runtime_stale=True)
        self._send_json({'ok': True, 'graph': graph})

    def _resolve_route(self) -> tuple[Path | None, str]:
        route = self.path.split('?', 1)[0]
        if route in ('/', '/visualizer.html'):
            return self.visualizer_path, 'text/html; charset=utf-8'
        if route == '/graph.json':
            return self.graph_path, 'application/json; charset=utf-8'
        if route == '/api/node-info':
            return None, 'application/json; charset=utf-8'

        if not self.asset_root:
            return None, 'application/octet-stream'

        candidate = (self.asset_root / route.lstrip('/')).resolve()
        try:
            candidate.relative_to(self.asset_root.resolve())
        except ValueError:
            return None, 'application/octet-stream'

        if not candidate.exists() or not candidate.is_file():
            return None, 'application/octet-stream'

        content_type = mimetypes.guess_type(str(candidate))[0] or 'application/octet-stream'
        return candidate, content_type

    def _send_headers(self, filepath: Path | None, content_type: str):
        if not filepath or not filepath.exists():
            self.send_error(404, 'Not Found')
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(filepath.stat().st_size))
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.end_headers()

    def _send_file(self, filepath: Path | None, fallback_type: str):
        if not filepath or not filepath.exists():
            self.send_error(404, 'Not Found')
            return
        content_type = mimetypes.guess_type(str(filepath))[0] or fallback_type
        self._send_headers(filepath, content_type)
        with filepath.open('rb') as handle:
            self.wfile.write(handle.read())


def make_server_handler(visualizer_path: Path, graph_path: Path):
    """Bind static file locations without changing process-wide cwd."""

    class Handler(_GraphRequestHandler):
        pass

    Handler.visualizer_path = visualizer_path
    Handler.graph_path = graph_path
    Handler.asset_root = visualizer_path.parent.resolve()
    return Handler


def run(
    root: str | Path,
    verbose: bool = True,
    runtime_trace: PythonRuntimeTraceConfig | NodeRuntimeTraceConfig | CppRuntimeTraceConfig | None = None,
    runtime_overlays: list[tuple[dict, Path] | tuple[dict, Path, bool]] | None = None,
    runtime_stale: bool = False,
    config: dict | None = None,
) -> dict:
    root_path = Path(root).resolve()

    if not root_path.exists():
        print(f"ERROR: Path does not exist: {root_path}", file=sys.stderr)
        sys.exit(1)
    if not root_path.is_dir():
        print(f"ERROR: Not a directory: {root_path}", file=sys.stderr)
        sys.exit(1)

    project_config = config if config is not None else load_project_config(root_path)
    raw = extract_all(root_path, verbose=verbose, config=project_config)

    overlays = list(runtime_overlays or [])
    if runtime_trace is not None:
        trace = run_runtime_trace(root_path, runtime_trace, verbose=verbose)
        artifact_path = (runtime_trace.output_path or (root_path / 'runtime_trace.json')).resolve()
        overlays.append((trace, artifact_path, False))
    if overlays:
        if runtime_stale:
            overlays = [(trace, artifact_path, True) for trace, artifact_path, *_rest in overlays]
        raw = merge_runtime_traces(raw, overlays)

    enriched = analyze_graph(raw)

    if verbose:
        summary = enriched['summary']
        meta = enriched['meta']
        langs = meta.get('languages', [])
        print(f"  Languages: {', '.join(langs) if langs else 'none'}", file=sys.stderr)
        unsupported = meta.get('unsupported_languages', [])
        if unsupported:
            labels = ', '.join(item['language'] for item in unsupported)
            print(f"  Missing:   parser support unavailable for {labels}", file=sys.stderr)
        runtime_meta = meta.get('runtime', {})
        if runtime_meta.get('enabled'):
            print(
                f"  Runtime:   {runtime_meta.get('runtime_edges', 0)} observed edges  "
                f"Dynamic:{runtime_meta.get('dynamic_edges', 0)}  "
                f"Exit:{runtime_meta.get('exit_code', 0)}",
                file=sys.stderr,
            )
            if runtime_meta.get('timed_out'):
                print('  Runtime:   trace timed out; partial results kept', file=sys.stderr)
            if runtime_meta.get('stale'):
                print('  Runtime:   preserved runtime overlay is stale after source changes', file=sys.stderr)
            if runtime_meta.get('error'):
                print(f"  Runtime:   {runtime_meta.get('error')}", file=sys.stderr)
        print(
            f"  Health:    {summary['health_score']}/100  "
            f"Orphans:{summary['counts'].get('ORPHAN', 0)}  "
            f"Islands:{summary['island_count']}  "
            f"Cycles:{summary['cycle_count']}",
            file=sys.stderr,
        )
    return enriched


def _dead_file_counts(graph: dict) -> dict[str, int]:
    waste = graph.get('waste', [])
    orphan_count = sum(1 for item in waste if item.get('classification') == 'ORPHAN')
    island_keys = set()
    for item in waste:
        if item.get('classification') != 'ISLAND':
            continue
        island_id = item.get('island_id', -1)
        island_keys.add(island_id if island_id != -1 else item.get('id'))
    return {
        'dead_files': len(waste),
        'orphans': orphan_count,
        'islands': len(island_keys),
        'health': int(graph.get('summary', {}).get('health_score', 0)),
    }


def _markdown_cell(value) -> str:
    return str(value).replace('|', '\\|')


def format_dead_report_markdown(graph: dict) -> str:
    counts = _dead_file_counts(graph)
    root = graph.get('meta', {}).get('root', '')
    lines = [
        '# Orbits Dead File Report',
        '',
        f'- Root: `{root}`',
        f"- Dead files: {counts['dead_files']}",
        f"- Orphans: {counts['orphans']}",
        f"- Island clusters: {counts['islands']}",
        f"- Health: {counts['health']}/100",
        '',
    ]
    waste = graph.get('waste', [])
    if not waste:
        lines.append('No dead files found.')
        return '\n'.join(lines) + '\n'

    lines.extend([
        '| Path | Classification | Size | Island |',
        '| --- | --- | ---: | ---: |',
    ])
    for item in waste:
        lines.append(
            f"| `{_markdown_cell(item.get('id', ''))}` "
            f"| {_markdown_cell(item.get('classification', ''))} "
            f"| {int(item.get('size', 0) or 0)} "
            f"| {item.get('island_id', -1)} |"
        )
    return '\n'.join(lines) + '\n'


def write_dead_report_markdown(graph: dict, output: str | Path) -> None:
    content = format_dead_report_markdown(graph)
    if str(output) == '-':
        sys.stdout.write(content)
        return
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def write_dead_report_csv(graph: dict, output: str | Path) -> None:
    fieldnames = ['id', 'name', 'classification', 'size', 'island_id']
    if str(output) == '-':
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(graph.get('waste', []))
        return
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(graph.get('waste', []))


def _check_thresholds_from_args(config: dict, args) -> dict:
    thresholds = dict(config.get('check', {}) if isinstance(config, dict) else {})
    if args.max_orphans is not None:
        thresholds['max_orphans'] = args.max_orphans
    if args.max_islands is not None:
        thresholds['max_islands'] = args.max_islands
    if args.min_health is not None:
        thresholds['min_health'] = args.min_health
    return thresholds


def evaluate_check_thresholds(graph: dict, thresholds: dict) -> list[str]:
    counts = _dead_file_counts(graph)
    failures: list[str] = []
    max_orphans = thresholds.get('max_orphans')
    if max_orphans is not None and counts['orphans'] > int(max_orphans):
        failures.append(f"orphans {counts['orphans']} > {int(max_orphans)}")
    max_islands = thresholds.get('max_islands')
    if max_islands is not None and counts['islands'] > int(max_islands):
        failures.append(f"islands {counts['islands']} > {int(max_islands)}")
    min_health = thresholds.get('min_health')
    if min_health is not None and counts['health'] < int(min_health):
        failures.append(f"health {counts['health']} < {int(min_health)}")
    return failures


def serve(output_path: Path, port: int = 8765):
    viz = Path(__file__).with_name('visualizer.html')
    if not viz.exists():
        print('ERROR: visualizer.html not found', file=sys.stderr)
        return
    if not output_path.exists():
        print(f'ERROR: graph output not found: {output_path}', file=sys.stderr)
        return

    handler = make_server_handler(viz, output_path.resolve())
    url = f'http://localhost:{port}/visualizer.html'
    print(f"\n  -> {url}", file=sys.stderr)
    print('  Ctrl+C to stop\n', file=sys.stderr)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    with http.server.ThreadingHTTPServer(('', port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\n  Stopped.', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        prog='orbits',
        description='Orbits - multi-language codebase dependency graph and dead code detector',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Supports: Python, JavaScript, TypeScript, Go + generic fallback
Phase 5: optional Python, Node.js, and scoped C/C++ runtime tracing

Examples:
  python analyzer.py .
  python analyzer.py ~/projects/myapp --serve
  python analyzer.py ~/projects/myapp -o graph.json --serve
  python analyzer.py --diff old_graph.json new_graph.json
        """,
    )
    parser.add_argument('path', nargs='?', help='Project root directory')
    parser.add_argument('-o', '--output', default='graph.json')
    parser.add_argument('--diff', nargs=2, metavar=('BASELINE', 'CURRENT'), help='Compare two existing Orbits graph JSON files')
    parser.add_argument('--diff-json', action='store_true', help='Print dependency diff as JSON instead of text')
    parser.add_argument('--diff-limit', type=int, default=20, help='Maximum diff items to show per section in text mode')
    trace_group = parser.add_mutually_exclusive_group()
    trace_group.add_argument('--trace-python', help='Project-relative Python entry script to execute under runtime tracing')
    trace_group.add_argument('--trace-module', help='Python module to execute under runtime tracing')
    trace_group.add_argument('--trace-node', help='Project-relative Node.js entry script to execute under runtime tracing')
    trace_group.add_argument('--trace-node-module', help='Node.js module specifier to execute under runtime tracing')
    trace_group.add_argument('--trace-cpp', help='Project-relative native executable to execute under scoped native runtime tracing')
    parser.add_argument('--trace-arg', action='append', default=[], help='Repeatable argument passed to the traced runtime entry or module')
    parser.add_argument('--trace-timeout', type=int, default=60, help='Maximum seconds to allow traced runtime execution before cutting it off')
    parser.add_argument('--runtime-output', default='runtime_trace.json', help='Path for the runtime trace artifact when tracing is enabled')
    parser.add_argument('--runtime-input', action='append', default=[], help='Existing runtime trace artifact to merge; repeat to include multiple trace sessions')
    parser.add_argument('--node-bin', default=os.environ.get('ORBITS_NODE_BIN', 'node'), help='Node executable to use for Node.js runtime tracing')
    parser.add_argument('--dead-report-md', help='Write a Markdown report of actionable dead files to this path; use - for stdout')
    parser.add_argument('--dead-report-csv', help='Write a CSV report of actionable dead files to this path; use - for stdout')
    parser.add_argument('--check', action='store_true', help='Exit nonzero when configured or flag-provided thresholds are exceeded')
    parser.add_argument('--max-orphans', type=int, help='Check threshold: maximum actionable orphan files')
    parser.add_argument('--max-islands', type=int, help='Check threshold: maximum actionable island clusters')
    parser.add_argument('--min-health', type=int, help='Check threshold: minimum graph health score')
    parser.add_argument('--serve', action='store_true', help='Open visualizer in browser after analysis')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()

    if args.diff:
        diff = diff_graph_files(args.diff[0], args.diff[1])
        if args.diff_json:
            print(json.dumps(diff, indent=2))
        else:
            print(format_graph_diff(diff, limit=max(0, args.diff_limit)))
        return

    if not args.path:
        parser.error('path is required unless --diff is used')

    print('\nOrbits - Phase 5', file=sys.stderr)
    print(f"{'-' * 40}", file=sys.stderr)

    root_path = Path(args.path).resolve()
    try:
        project_config = load_project_config(root_path)
    except ValueError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)

    runtime_trace = None
    if args.trace_python or args.trace_module:
        runtime_trace = PythonRuntimeTraceConfig(
            mode='script' if args.trace_python else 'module',
            target=args.trace_python or args.trace_module,
            args=list(args.trace_arg or []),
            output_path=Path(args.runtime_output).resolve(),
            timeout_s=args.trace_timeout,
        )
    elif args.trace_node or args.trace_node_module:
        runtime_trace = NodeRuntimeTraceConfig(
            mode='script' if args.trace_node else 'module',
            target=args.trace_node or args.trace_node_module,
            args=list(args.trace_arg or []),
            output_path=Path(args.runtime_output).resolve(),
            timeout_s=args.trace_timeout,
            node_bin=args.node_bin,
        )
    elif args.trace_cpp:
        runtime_trace = CppRuntimeTraceConfig(
            target=args.trace_cpp,
            args=list(args.trace_arg or []),
            output_path=Path(args.runtime_output).resolve(),
            timeout_s=args.trace_timeout,
        )

    runtime_overlays = []
    for runtime_input in args.runtime_input or []:
        loaded = _load_runtime_artifact(runtime_input)
        if not loaded:
            print(f"ERROR: Runtime trace artifact not found or unreadable: {runtime_input}", file=sys.stderr)
            sys.exit(1)
        runtime_overlays.append((*loaded, False))

    graph = run(root_path, verbose=True, runtime_trace=runtime_trace, runtime_overlays=runtime_overlays, config=project_config)
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding='utf-8')

    meta = graph['meta']
    imp = meta.get('import_stats', {})
    total = sum(imp.values())
    pct = round(imp.get('local', 0) / total * 100, 1) if total else 0

    print(f"\n  Files:     {meta['total_files']}", file=sys.stderr)
    print(f"  Edges:     {meta['total_edges']}", file=sys.stderr)
    print(f"  Resolved:  {imp.get('local', 0)}/{total} imports ({pct}%)", file=sys.stderr)
    if meta.get('unsupported_languages'):
        for item in meta['unsupported_languages']:
            print(f"  Warning:   {item['language']} parser unavailable ({item['reason']})", file=sys.stderr)
    runtime_meta = meta.get('runtime', {})
    if runtime_meta.get('enabled'):
        if runtime_meta.get('session_count', 1) > 1:
            print(f"  Runtime:   {runtime_meta.get('session_count')} sessions merged", file=sys.stderr)
        print(f"  Runtime:   {runtime_meta.get('artifact')}", file=sys.stderr)
    if args.dead_report_md:
        write_dead_report_markdown(graph, args.dead_report_md)
        print(f"  Report MD: {args.dead_report_md}", file=sys.stderr)
    if args.dead_report_csv:
        write_dead_report_csv(graph, args.dead_report_csv)
        print(f"  Report CSV: {args.dead_report_csv}", file=sys.stderr)
    print(f"  Output:    {output_path}", file=sys.stderr)
    print(f"{'-' * 40}\n", file=sys.stderr)

    if args.check:
        thresholds = _check_thresholds_from_args(project_config, args)
        failures = evaluate_check_thresholds(graph, thresholds)
        if failures:
            print('  Check:     FAIL', file=sys.stderr)
            for failure in failures:
                print(f'  - {failure}', file=sys.stderr)
            sys.exit(2)
        suffix = '' if thresholds else ' (no thresholds configured)'
        print(f'  Check:     PASS{suffix}', file=sys.stderr)

    if args.serve:
        serve(output_path, args.port)
    else:
        print('  Run with --serve to open the visualizer.\n', file=sys.stderr)


if __name__ == '__main__':
    main()

