from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
from typing import Dict, List

LANG_SPECS = [
    ('python', '.py'),
    ('javascript', '.js'),
    ('typescript', '.ts'),
    ('go', '.go'),
    ('cpp', '.cpp'),
    ('java', '.java'),
]
CLASSES = ['CONNECTED', 'LEAF', 'CONNECTED', 'CONNECTED', 'TEST', 'GENERATED']


def _node_path(index: int, language: str, ext: str) -> str:
    bucket = index % 18
    return f"src/domain_{bucket:02d}/{language}/module_{index:04d}{ext}"


def generate_graph(node_count: int = 1200, seed: int = 7) -> Dict[str, object]:
    rng = random.Random(seed)
    nodes: List[Dict[str, object]] = []
    edges: List[Dict[str, object]] = []
    cycles: List[List[str]] = []
    waste: List[Dict[str, object]] = []

    for index in range(node_count):
        language, ext = LANG_SPECS[index % len(LANG_SPECS)]
        node_id = _node_path(index, language, ext)
        classification = 'ENTRY' if index < 5 else CLASSES[index % len(CLASSES)]
        if index % 97 == 0 and index > 10:
            classification = 'ORPHAN'
        elif 420 <= index < 450:
            classification = 'ISLAND'
        depth = 0 if classification == 'ENTRY' else (index % 9) + 1
        island_id = 0 if classification == 'ISLAND' else -1
        node = {
            'id': node_id,
            'filepath': node_id,
            'name': Path(node_id).name,
            'dir': str(Path(node_id).parent).replace('\\', '/'),
            'classification': classification,
            'language': language,
            'size': 300 + (index % 17) * 41,
            'mtime': 1_730_000_000 + index,
            'depth': depth,
            'island_id': island_id,
        }
        nodes.append(node)
        if classification in {'ORPHAN', 'ISLAND'}:
            waste.append(node.copy())

    for index in range(node_count):
        source = nodes[index]['id']
        if index < 5:
            continue
        fan_in = 1 + (index % 3)
        for hop in range(1, fan_in + 1):
            target_index = max(0, index - (hop * (1 + (index % 5))))
            if target_index == index:
                continue
            edges.append({'source': source, 'target': nodes[target_index]['id'], 'line': 5 + hop + (index % 70)})
        if index % 11 == 0:
            edges.append({'source': source, 'target': nodes[index % 5]['id'], 'line': 40 + (index % 20)})

    for base in range(30, min(node_count - 3, 630), 120):
        cycle = [nodes[base]['id'], nodes[base + 1]['id'], nodes[base + 2]['id']]
        cycles.append(cycle + [cycle[0]])
        edges.append({'source': cycle[0], 'target': cycle[1], 'line': 12})
        edges.append({'source': cycle[1], 'target': cycle[2], 'line': 18})
        edges.append({'source': cycle[2], 'target': cycle[0], 'line': 27})

    counts: Dict[str, int] = {}
    for node in nodes:
        counts[node['classification']] = counts.get(node['classification'], 0) + 1

    import_stats = {
        'local': len(edges),
        'unknown': max(12, node_count // 30),
        'stdlib': max(25, node_count // 20),
        'external': max(40, node_count // 16),
    }

    return {
        'nodes': nodes,
        'edges': edges,
        'cycles': cycles,
        'waste': waste,
        'summary': {
            'counts': counts,
            'total': len(nodes),
            'cycle_count': len(cycles),
            'island_count': 1 if any(node['classification'] == 'ISLAND' for node in nodes) else 0,
            'max_depth': max(int(node['depth']) for node in nodes),
            'health_score': max(12, 100 - counts.get('ORPHAN', 0) - len(cycles) * 4),
            'unreachable': counts.get('ORPHAN', 0) + counts.get('ISLAND', 0),
        },
        'meta': {
            'elapsed_s': round(rng.uniform(0.8, 2.4), 2),
            'import_stats': import_stats,
            'unsupported_languages': [],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate a deterministic synthetic Orbits graph fixture.')
    parser.add_argument('--nodes', type=int, default=1200, help='Number of nodes to generate.')
    parser.add_argument('--seed', type=int, default=7, help='Deterministic random seed.')
    parser.add_argument('--output', type=Path, default=Path('large_graph.json'), help='Output JSON path.')
    args = parser.parse_args()
    graph = generate_graph(node_count=args.nodes, seed=args.seed)
    args.output.write_text(json.dumps(graph, indent=2))
    print(f"wrote {args.output} with {len(graph['nodes'])} nodes and {len(graph['edges'])} edges")


if __name__ == '__main__':
    main()
