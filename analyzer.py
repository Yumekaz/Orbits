"""
analyzer.py - Orbits Phase 3

Multi-language dependency graph analyzer.
Supports: Python, JavaScript, TypeScript, Go + generic fallback.

Usage:
    python analyzer.py /path/to/project --serve
    python analyzer.py /path/to/project -o graph.json
"""

import argparse
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

from graph_engine import analyze_graph
from lang_dispatch import extract_all


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


def _rerun_graph(graph_path: Path) -> dict:
    root = _graph_root_from_file(graph_path)
    if not root:
        raise FileNotFoundError('Graph root unavailable for re-analysis')
    refreshed = run(root, verbose=False)
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
        graph = _rerun_graph(self.graph_path)
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
        graph = _rerun_graph(self.graph_path)
        self._send_json({'ok': True, 'graph': graph, 'intentional': intentional})

    def _handle_reanalyze(self):
        graph = _rerun_graph(self.graph_path)
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


def run(root: str | Path, verbose: bool = True) -> dict:
    root_path = Path(root).resolve()

    if not root_path.exists():
        print(f"ERROR: Path does not exist: {root_path}", file=sys.stderr)
        sys.exit(1)
    if not root_path.is_dir():
        print(f"ERROR: Not a directory: {root_path}", file=sys.stderr)
        sys.exit(1)

    raw = extract_all(root_path, verbose=verbose)
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
        print(
            f"  Health:    {summary['health_score']}/100  "
            f"Orphans:{summary['counts'].get('ORPHAN', 0)}  "
            f"Islands:{summary['island_count']}  "
            f"Cycles:{summary['cycle_count']}",
            file=sys.stderr,
        )
    return enriched


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

Examples:
  python analyzer.py .
  python analyzer.py ~/projects/myapp --serve
  python analyzer.py ~/projects/myapp -o graph.json --serve
        """,
    )
    parser.add_argument('path', help='Project root directory')
    parser.add_argument('-o', '--output', default='graph.json')
    parser.add_argument('--serve', action='store_true', help='Open visualizer in browser after analysis')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()

    print('\nOrbits - Phase 3', file=sys.stderr)
    print(f"{'-' * 40}", file=sys.stderr)

    graph = run(args.path, verbose=True)
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
    print(f"  Output:    {output_path}", file=sys.stderr)
    print(f"{'-' * 40}\n", file=sys.stderr)

    if args.serve:
        serve(output_path, args.port)
    else:
        print('  Run with --serve to open the visualizer.\n', file=sys.stderr)


if __name__ == '__main__':
    main()

