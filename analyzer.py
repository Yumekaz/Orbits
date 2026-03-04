"""
analyzer.py — Orbits entry point.

Pipeline:
    1. Crawl  — find all Python files, skip noise
    2. Extract — pull every import via ast
    3. Resolve — map import strings to actual file paths
    4. Analyze — classify nodes, detect cycles/islands, compute depths
    5. Output  — write enriched graph.json
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
from graph_engine import analyze_graph


def extract(root):
    root_path = Path(root).resolve()

    if not root_path.exists():
        print(f"ERROR: Path does not exist: {root_path}", file=sys.stderr)
        sys.exit(1)
    if not root_path.is_dir():
        print(f"ERROR: Not a directory: {root_path}", file=sys.stderr)
        sys.exit(1)

    print(f"  Scanning: {root_path}", file=sys.stderr)

    files_by_lang = crawl_by_language(root_path)
    python_files  = files_by_lang.get('python', [])

    if not python_files:
        print("  WARNING: No Python files found.", file=sys.stderr)

    print(f"  Found {len(python_files)} Python files", file=sys.stderr)

    nodes = {}
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
            'dir':      str(filepath.parent.relative_to(root_path))
                        if filepath.parent != root_path else '.',
        }

    print(f"  Extracting imports...", file=sys.stderr)

    all_edges = []
    for node_id in nodes:
        filepath  = root_path / node_id
        raw_edges = extract_imports(filepath, root_path)
        for edge in raw_edges:
            if edge['resolved'] and edge['to'] in nodes:
                all_edges.append({
                    'source': edge['from'],
                    'target': edge['to'],
                    'type':   edge['type'],
                    'line':   edge['line'],
                })

    seen = set()
    unique_edges = []
    for edge in all_edges:
        key = (edge['source'], edge['target'])
        if key not in seen:
            seen.add(key)
            unique_edges.append(edge)

    print(f"  {len(unique_edges)} internal edges", file=sys.stderr)

    return {
        'nodes': list(nodes.values()),
        'edges': unique_edges,
        'meta': {
            'root':        str(root_path),
            'total_files': len(nodes),
            'total_edges': len(unique_edges),
            'languages':   list(files_by_lang.keys()),
        },
    }


def run(root):
    raw      = extract(root)
    enriched = analyze_graph(raw)
    s = enriched['summary']
    print(f"  Orphans: {s['counts'].get('ORPHAN',0)}  "
          f"Islands: {s['island_count']}  "
          f"Cycles: {s['cycle_count']}  "
          f"Health: {s['health_score']}/100", file=sys.stderr)
    return enriched


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args): pass


def serve(output_path, port=8765):
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
    parser = argparse.ArgumentParser(prog='orbits',
        description='Orbits — codebase dependency graph & dead code detector')
    parser.add_argument('path', help='Project root directory')
    parser.add_argument('-o', '--output', default='graph.json')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()

    print(f"\nOrbits", file=sys.stderr)
    print(f"{'─'*40}", file=sys.stderr)

    graph       = run(args.path)
    output_path = Path(args.output)
    output_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False))

    meta = graph['meta']
    print(f"\n  Files:  {meta['total_files']}", file=sys.stderr)
    print(f"  Edges:  {meta['total_edges']}", file=sys.stderr)
    print(f"  Output: {output_path.resolve()}", file=sys.stderr)
    print(f"{'─'*40}\n", file=sys.stderr)

    if args.serve:
        serve(output_path, args.port)
    else:
        print(f"  Run with --serve to open the visualizer.\n", file=sys.stderr)


if __name__ == '__main__':
    main()
