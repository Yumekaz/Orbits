from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EdgeKey = tuple[str, str]


def _normalize_id(value: Any) -> str:
    return str(value or '').replace('\\', '/')


def _node_ids(graph: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for node in graph.get('nodes', []):
        if not isinstance(node, dict):
            continue
        node_id = _normalize_id(node.get('id') or node.get('filepath'))
        if node_id:
            ids.add(node_id)
    return ids


def _edge_keys(graph: dict[str, Any]) -> set[EdgeKey]:
    keys: set[EdgeKey] = set()
    for edge in graph.get('edges', []):
        if not isinstance(edge, dict):
            continue
        source = _normalize_id(edge.get('source'))
        target = _normalize_id(edge.get('target'))
        if source and target:
            keys.add((source, target))
    return keys


def _waste_ids(graph: dict[str, Any]) -> set[str]:
    waste = graph.get('waste')
    ids: set[str] = set()
    if isinstance(waste, list):
        for item in waste:
            if not isinstance(item, dict):
                continue
            waste_id = _normalize_id(item.get('id') or item.get('filepath'))
            if waste_id:
                ids.add(waste_id)
        return ids

    for node in graph.get('nodes', []):
        if not isinstance(node, dict):
            continue
        if node.get('classification') not in {'ORPHAN', 'ISLAND'}:
            continue
        node_id = _normalize_id(node.get('id') or node.get('filepath'))
        if node_id:
            ids.add(node_id)
    return ids


def _sorted_edges(edges: set[EdgeKey]) -> list[dict[str, str]]:
    return [{'source': source, 'target': target} for source, target in sorted(edges)]


def load_graph(path: str | Path) -> dict[str, Any]:
    graph_path = Path(path)
    with graph_path.open('r', encoding='utf-8') as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f'Graph JSON must be an object: {graph_path}')
    return payload


def diff_graphs(
    baseline_graph: dict[str, Any],
    current_graph: dict[str, Any],
    baseline_path: str | Path | None = None,
    current_path: str | Path | None = None,
) -> dict[str, Any]:
    baseline_nodes = _node_ids(baseline_graph)
    current_nodes = _node_ids(current_graph)
    baseline_edges = _edge_keys(baseline_graph)
    current_edges = _edge_keys(current_graph)
    baseline_waste = _waste_ids(baseline_graph)
    current_waste = _waste_ids(current_graph)

    return {
        'baseline': {
            'path': str(baseline_path) if baseline_path is not None else None,
            'nodes': len(baseline_nodes),
            'edges': len(baseline_edges),
            'waste': len(baseline_waste),
        },
        'current': {
            'path': str(current_path) if current_path is not None else None,
            'nodes': len(current_nodes),
            'edges': len(current_edges),
            'waste': len(current_waste),
        },
        'nodes': {
            'added': sorted(current_nodes - baseline_nodes),
            'removed': sorted(baseline_nodes - current_nodes),
            'before': len(baseline_nodes),
            'after': len(current_nodes),
            'delta': len(current_nodes) - len(baseline_nodes),
        },
        'edges': {
            'added': _sorted_edges(current_edges - baseline_edges),
            'removed': _sorted_edges(baseline_edges - current_edges),
            'before': len(baseline_edges),
            'after': len(current_edges),
            'delta': len(current_edges) - len(baseline_edges),
        },
        'waste': {
            'added': sorted(current_waste - baseline_waste),
            'removed': sorted(baseline_waste - current_waste),
            'before': len(baseline_waste),
            'after': len(current_waste),
            'delta': len(current_waste) - len(baseline_waste),
        },
    }


def diff_graph_files(baseline_path: str | Path, current_path: str | Path) -> dict[str, Any]:
    baseline = load_graph(baseline_path)
    current = load_graph(current_path)
    return diff_graphs(baseline, current, Path(baseline_path), Path(current_path))


def _delta(value: int) -> str:
    if value > 0:
        return f'+{value}'
    return str(value)


def _append_list(lines: list[str], title: str, prefix: str, values: list[str], limit: int) -> None:
    if not values:
        return
    lines.append(f'  {title}:')
    for value in values[:limit]:
        lines.append(f'    {prefix} {value}')
    omitted = len(values) - limit
    if omitted > 0:
        lines.append(f'    ... {omitted} more')


def _append_edges(lines: list[str], title: str, prefix: str, edges: list[dict[str, str]], limit: int) -> None:
    if not edges:
        return
    lines.append(f'  {title}:')
    for edge in edges[:limit]:
        lines.append(f"    {prefix} {edge['source']} -> {edge['target']}")
    omitted = len(edges) - limit
    if omitted > 0:
        lines.append(f'    ... {omitted} more')


def format_graph_diff(diff: dict[str, Any], limit: int = 20) -> str:
    limit = max(0, limit)
    nodes = diff['nodes']
    edges = diff['edges']
    waste = diff['waste']
    baseline = diff['baseline']
    current = diff['current']

    lines = ['Graph dependency diff']
    if baseline.get('path') or current.get('path'):
        lines.append(f"Baseline: {baseline.get('path') or '<memory>'}")
        lines.append(f"Current:  {current.get('path') or '<memory>'}")
    lines.append('')
    lines.append(f"Nodes: {nodes['before']} -> {nodes['after']} ({_delta(nodes['delta'])})")
    _append_list(lines, 'Added nodes', '+', nodes['added'], limit)
    _append_list(lines, 'Removed nodes', '-', nodes['removed'], limit)
    if not nodes['added'] and not nodes['removed']:
        lines.append('  No node changes.')

    lines.append('')
    lines.append(f"Edges: {edges['before']} -> {edges['after']} ({_delta(edges['delta'])})")
    _append_edges(lines, 'Added edges', '+', edges['added'], limit)
    _append_edges(lines, 'Removed edges', '-', edges['removed'], limit)
    if not edges['added'] and not edges['removed']:
        lines.append('  No dependency edge changes.')

    lines.append('')
    lines.append(f"Waste: {waste['before']} -> {waste['after']} ({_delta(waste['delta'])})")
    _append_list(lines, 'New waste', '+', waste['added'], limit)
    _append_list(lines, 'Removed waste', '-', waste['removed'], limit)
    if not waste['added'] and not waste['removed']:
        lines.append('  Waste set unchanged.')

    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description='Compare two Orbits graph JSON files.')
    parser.add_argument('baseline', type=Path, help='Older graph JSON file')
    parser.add_argument('current', type=Path, help='Newer graph JSON file')
    parser.add_argument('--json', action='store_true', help='Print machine-readable JSON diff')
    parser.add_argument('--limit', type=int, default=20, help='Maximum items to show per section in text mode')
    args = parser.parse_args()

    diff = diff_graph_files(args.baseline, args.current)
    if args.json:
        print(json.dumps(diff, indent=2))
    else:
        print(format_graph_diff(diff, limit=args.limit))


if __name__ == '__main__':
    main()
