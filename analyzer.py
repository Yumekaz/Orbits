"""
analyzer.py — Orbits Phase 3

Multi-language dependency graph analyzer.
Supports: Python, JavaScript, TypeScript, Go + generic fallback.

Usage:
    python analyzer.py /path/to/project --serve
    python analyzer.py /path/to/project -o graph.json
"""

import json
import sys
import argparse
import threading
import webbrowser
import http.server
import os
from pathlib import Path

from lang_dispatch import extract_all
from graph_engine  import analyze_graph



def _ensure_gitignore(root):
    """Add .orbits_cache.json to .gitignore if a .gitignore exists."""
    gi = root / '.gitignore'
    entry = '.orbits_cache.json'
    if gi.exists():
        text = gi.read_text(encoding='utf-8')
        if entry not in text:
            gi.write_text(text.rstrip() + '\n' + entry + '\n', encoding='utf-8')
    # else: don't create .gitignore, not our job

def run(root: str | Path) -> dict:
    root_path = Path(root).resolve()

    if not root_path.exists():
        print(f"ERROR: Path does not exist: {root_path}", file=sys.stderr)
        sys.exit(1)
    if not root_path.is_dir():
        print(f"ERROR: Not a directory: {root_path}", file=sys.stderr)
        sys.exit(1)

    _ensure_gitignore(root_path)
    raw      = extract_all(root_path, verbose=True)
    enriched = analyze_graph(raw)

    s = enriched['summary']
    langs = enriched['meta'].get('languages', [])
    print(f"  Languages: {', '.join(langs)}", file=sys.stderr)
    print(f"  Health:    {s['health_score']}/100  "
          f"Orphans:{s['counts'].get('ORPHAN',0)}  "
          f"Islands:{s['island_count']}  "
          f"Cycles:{s['cycle_count']}",
          file=sys.stderr)
    return enriched


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args): pass


def serve(output_path: Path, port: int = 8765):
    viz = Path(__file__).parent / 'visualizer.html'
    if not viz.exists():
        print("ERROR: visualizer.html not found", file=sys.stderr)
        return
    os.chdir(output_path.parent.resolve())
    url = f'http://localhost:{port}/visualizer.html'
    print(f"\n  → {url}", file=sys.stderr)
    print(f"  Ctrl+C to stop\n", file=sys.stderr)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    with http.server.HTTPServer(('', port), _QuietHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Stopped.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        prog='orbits',
        description='Orbits — multi-language codebase dependency graph & dead code detector',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Supports: Python, JavaScript, TypeScript, Go + generic fallback

Examples:
  python analyzer.py .
  python analyzer.py ~/projects/myapp --serve
  python analyzer.py ~/projects/myapp -o graph.json --serve
        """
    )
    parser.add_argument('path', help='Project root directory')
    parser.add_argument('-o', '--output', default='graph.json')
    parser.add_argument('--serve', action='store_true',
                        help='Open visualizer in browser after analysis')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()

    print(f"\nOrbits — Phase 3", file=sys.stderr)
    print(f"{'─'*40}", file=sys.stderr)

    graph       = run(args.path)
    output_path = Path(args.output)
    output_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False))

    meta = graph['meta']
    imp  = meta.get('import_stats', {})
    total = sum(imp.values())
    pct   = round(imp.get('local', 0) / total * 100, 1) if total else 0

    print(f"\n  Files:     {meta['total_files']}", file=sys.stderr)
    print(f"  Edges:     {meta['total_edges']}", file=sys.stderr)
    print(f"  Resolved:  {imp.get('local',0)}/{total} imports ({pct}%)", file=sys.stderr)
    print(f"  Output:    {output_path.resolve()}", file=sys.stderr)
    print(f"{'─'*40}\n", file=sys.stderr)

    if args.serve:
        serve(output_path, args.port)
    else:
        print(f"  Run with --serve to open the visualizer.\n", file=sys.stderr)


if __name__ == '__main__':
    main()
