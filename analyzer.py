"""
analyzer.py — Phase 0 entry point.

Usage:
    python analyzer.py /path/to/your/project
    python analyzer.py /path/to/your/project -o my_graph.json
    python analyzer.py /path/to/your/project --serve

What it does:
    1. Crawls your project, finds all Python files
    2. Extracts every import statement using ast
    3. Resolves imports to actual file paths where possible
    4. Writes a graph.json with nodes + edges
    5. Optionally starts a local HTTP server and opens the visualizer
"""

import json
import sys
import argparse
import threading
import webbrowser
import http.server
import os
from pathlib import Path

from crawler import crawl_by_language
from extractor import extract_imports


# ── Analysis ──────────────────────────────────────────────────────────────────

def analyze(root: str | Path) -> dict:
    root_path = Path(root).resolve()

    if not root_path.exists():
        print(f"ERROR: Path does not exist: {root_path}", file=sys.stderr)
        sys.exit(1)

    if not root_path.is_dir():
        print(f"ERROR: Path is not a directory: {root_path}", file=sys.stderr)
        sys.exit(1)

    print(f"  Crawling: {root_path}", file=sys.stderr)

    # ── Step 1: Crawl ────────────────────────────────────────────────────────
    files_by_lang = crawl_by_language(root_path)
    python_files = files_by_lang.get('python', [])

    if not python_files:
        print("  WARNING: No Python files found. Check your path.", file=sys.stderr)

    print(f"  Found {len(python_files)} Python files", file=sys.stderr)

    # ── Step 2: Build nodes ──────────────────────────────────────────────────
    nodes: dict[str, dict] = {}

    for filepath in python_files:
        try:
            rel = str(filepath.relative_to(root_path))
        except ValueError:
            continue

        stat = filepath.stat()
        nodes[rel] = {
            'id':       rel,
            'filepath': rel,
            'name':     filepath.name,
            'language': 'python',
            'size':     stat.st_size,
            'dir':      str(filepath.parent.relative_to(root_path)) if filepath.parent != root_path else '.',
        }

    # ── Step 3: Extract imports and build edges ──────────────────────────────
    print(f"  Extracting imports...", file=sys.stderr)

    all_edges: list[dict] = []
    parse_errors = 0

    for node_id, node_info in nodes.items():
        filepath = root_path / node_id
        raw_edges = extract_imports(filepath, root_path)

        for edge in raw_edges:
            # Only include edges where the target file is in our project
            if edge['resolved'] and edge['to'] in nodes:
                all_edges.append({
                    'source': edge['from'],
                    'target': edge['to'],
                    'type':   edge['type'],
                    'line':   edge['line'],
                })

    # ── Step 4: Deduplicate edges ────────────────────────────────────────────
    seen_edges: set[tuple] = set()
    unique_edges: list[dict] = []

    for edge in all_edges:
        key = (edge['source'], edge['target'])
        if key not in seen_edges:
            seen_edges.add(key)
            unique_edges.append(edge)

    # ── Step 5: Compute inbound/outbound counts (useful for viz) ─────────────
    inbound:  dict[str, int] = {nid: 0 for nid in nodes}
    outbound: dict[str, int] = {nid: 0 for nid in nodes}

    for edge in unique_edges:
        outbound[edge['source']] = outbound.get(edge['source'], 0) + 1
        inbound[edge['target']]  = inbound.get(edge['target'], 0) + 1

    for nid in nodes:
        nodes[nid]['inbound']  = inbound.get(nid, 0)
        nodes[nid]['outbound'] = outbound.get(nid, 0)

    print(f"  {len(unique_edges)} internal import edges found", file=sys.stderr)

    return {
        'nodes': list(nodes.values()),
        'edges': unique_edges,
        'meta': {
            'root':         str(root_path),
            'total_files':  len(nodes),
            'total_edges':  len(unique_edges),
            'languages':    list(files_by_lang.keys()),
        },
    }


# ── Server ────────────────────────────────────────────────────────────────────

class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that suppresses request logs."""
    def log_message(self, format, *args):
        pass


def serve(output_path: Path, port: int = 8765):
    viz_path = Path(__file__).parent / 'visualizer.html'

    if not viz_path.exists():
        print("ERROR: visualizer.html not found next to analyzer.py", file=sys.stderr)
        return

    # Serve from the directory containing graph.json
    serve_dir = output_path.parent.resolve()
    os.chdir(serve_dir)

    url = f'http://localhost:{port}/visualizer.html'
    print(f"\n  Serving at {url}", file=sys.stderr)
    print(f"  Press Ctrl+C to stop\n", file=sys.stderr)

    threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    with http.server.HTTPServer(('', port), _QuietHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Server stopped.", file=sys.stderr)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog='analyzer',
        description='Phase 0 — Python codebase dependency graph',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python analyzer.py .
  python analyzer.py ~/projects/myapp --serve
  python analyzer.py ~/projects/myapp -o custom_output.json
        """
    )
    parser.add_argument(
        'path',
        help='Root directory of the project to analyze'
    )
    parser.add_argument(
        '-o', '--output',
        default='graph.json',
        help='Output JSON file path (default: graph.json)'
    )
    parser.add_argument(
        '--serve',
        action='store_true',
        help='Start local server and open visualizer in browser after analysis'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=8765,
        help='Port for the local server (default: 8765)'
    )

    args = parser.parse_args()

    print(f"\nCodebase Visualizer — Phase 0", file=sys.stderr)
    print(f"{'─' * 40}", file=sys.stderr)

    graph = analyze(args.path)

    output_path = Path(args.output)
    output_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False))

    print(f"\n  Output: {output_path.resolve()}", file=sys.stderr)
    print(f"  Files:  {graph['meta']['total_files']}", file=sys.stderr)
    print(f"  Edges:  {graph['meta']['total_edges']}", file=sys.stderr)
    print(f"{'─' * 40}\n", file=sys.stderr)

    if args.serve:
        serve(output_path, args.port)
    else:
        print(f"  To visualize:", file=sys.stderr)
        print(f"    python analyzer.py {args.path} --serve", file=sys.stderr)
        print(f"  Or open visualizer.html and drop graph.json onto it.\n", file=sys.stderr)


if __name__ == '__main__':
    main()
