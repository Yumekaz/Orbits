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
import sys
import threading
import webbrowser
from pathlib import Path

from graph_engine import analyze_graph
from lang_dispatch import extract_all


class _GraphRequestHandler(http.server.BaseHTTPRequestHandler):
    visualizer_path: Path | None = None
    graph_path: Path | None = None
    asset_root: Path | None = None

    def do_GET(self):
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

    def log_message(self, *_args):
        pass

    def _resolve_route(self) -> tuple[Path | None, str]:
        route = self.path.split('?', 1)[0]
        if route in ('/', '/visualizer.html'):
            return self.visualizer_path, 'text/html; charset=utf-8'
        if route == '/graph.json':
            return self.graph_path, 'application/json; charset=utf-8'

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
