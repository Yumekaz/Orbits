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


def _node_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for node in graph.get('nodes', []):
        if not isinstance(node, dict):
            continue
        node_id = _normalize_id(node.get('id') or node.get('filepath'))
        if node_id:
            indexed[node_id] = node
    return indexed


def _edge_keys_from(items: Any) -> set[EdgeKey]:
    keys: set[EdgeKey] = set()
    edge_items = items if isinstance(items, list) else []
    for edge in edge_items:
        if not isinstance(edge, dict):
            continue
        source = _normalize_id(edge.get('source'))
        target = _normalize_id(edge.get('target'))
        if source and target:
            keys.add((source, target))
    return keys


def _edge_keys(graph: dict[str, Any]) -> set[EdgeKey]:
    return _edge_keys_from(graph.get('edges', []))


def _dynamic_edge_keys(graph: dict[str, Any]) -> set[EdgeKey]:
    return _edge_keys_from(graph.get('dynamic_edges', []))


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


def _classification_changes(baseline_graph: dict[str, Any], current_graph: dict[str, Any]) -> list[dict[str, str]]:
    baseline_nodes = _node_index(baseline_graph)
    current_nodes = _node_index(current_graph)
    changes: list[dict[str, str]] = []
    for node_id in sorted(set(baseline_nodes) & set(current_nodes)):
        before = str(baseline_nodes[node_id].get('classification') or '')
        after = str(current_nodes[node_id].get('classification') or '')
        if before != after:
            changes.append({'id': node_id, 'before': before, 'after': after})
    return changes


def _waste_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    waste = graph.get('waste', [])
    if not isinstance(waste, list):
        return indexed
    for item in waste:
        if not isinstance(item, dict):
            continue
        item_id = _normalize_id(item.get('id') or item.get('filepath'))
        if item_id:
            indexed[item_id] = item
    return indexed


def _confidence_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    confidence = item.get('dead_confidence') if isinstance(item.get('dead_confidence'), dict) else {}
    score = confidence.get('score', item.get('confidence_score'))
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = None
    level = str(confidence.get('level', item.get('confidence_level', '')) or '')
    return {'score': score, 'level': level}


def _confidence_changes(baseline_graph: dict[str, Any], current_graph: dict[str, Any]) -> list[dict[str, Any]]:
    baseline_waste = _waste_index(baseline_graph)
    current_waste = _waste_index(current_graph)
    changes: list[dict[str, Any]] = []
    for item_id in sorted(set(baseline_waste) & set(current_waste)):
        before = _confidence_snapshot(baseline_waste[item_id])
        after = _confidence_snapshot(current_waste[item_id])
        if before == after:
            continue
        before_score = before['score']
        after_score = after['score']
        delta = None
        if before_score is not None and after_score is not None:
            delta = after_score - before_score
        changes.append({
            'id': item_id,
            'before_score': before_score,
            'after_score': after_score,
            'delta': delta,
            'before_level': before['level'],
            'after_level': after['level'],
        })
    return changes


def _runtime_summary(graph: dict[str, Any]) -> dict[str, Any]:
    meta = graph.get('meta', {}) if isinstance(graph.get('meta'), dict) else {}
    runtime = meta.get('runtime', {}) if isinstance(meta.get('runtime'), dict) else {}
    return {
        'enabled': bool(runtime.get('enabled') or graph.get('runtime')),
        'stale': bool(runtime.get('stale')),
        'session_count': int(runtime.get('session_count', 0) or 0),
        'runtime_edges': int(runtime.get('runtime_edges', len(graph.get('dynamic_edges', []) or [])) or 0),
        'entrypoint': runtime.get('entrypoint', ''),
    }


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
    baseline_dynamic_edges = _dynamic_edge_keys(baseline_graph)
    current_dynamic_edges = _dynamic_edge_keys(current_graph)
    baseline_waste = _waste_ids(baseline_graph)
    current_waste = _waste_ids(current_graph)
    classification_changes = _classification_changes(baseline_graph, current_graph)
    confidence_changes = _confidence_changes(baseline_graph, current_graph)

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
        'dynamic_edges': {
            'added': _sorted_edges(current_dynamic_edges - baseline_dynamic_edges),
            'removed': _sorted_edges(baseline_dynamic_edges - current_dynamic_edges),
            'before': len(baseline_dynamic_edges),
            'after': len(current_dynamic_edges),
            'delta': len(current_dynamic_edges) - len(baseline_dynamic_edges),
        },
        'waste': {
            'added': sorted(current_waste - baseline_waste),
            'removed': sorted(baseline_waste - current_waste),
            'before': len(baseline_waste),
            'after': len(current_waste),
            'delta': len(current_waste) - len(baseline_waste),
        },
        'classification_changes': {
            'changed': classification_changes,
            'count': len(classification_changes),
        },
        'confidence_changes': {
            'changed': confidence_changes,
            'count': len(confidence_changes),
        },
        'runtime': {
            'before': _runtime_summary(baseline_graph),
            'after': _runtime_summary(current_graph),
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
    dynamic_edges = diff.get('dynamic_edges', {'before': 0, 'after': 0, 'delta': 0, 'added': [], 'removed': []})
    waste = diff['waste']
    classification_changes = diff.get('classification_changes', {}).get('changed', [])
    confidence_changes = diff.get('confidence_changes', {}).get('changed', [])
    runtime = diff.get('runtime', {})
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
    lines.append(f"Dynamic edges: {dynamic_edges['before']} -> {dynamic_edges['after']} ({_delta(dynamic_edges['delta'])})")
    _append_edges(lines, 'Added dynamic edges', '+', dynamic_edges.get('added', []), limit)
    _append_edges(lines, 'Removed dynamic edges', '-', dynamic_edges.get('removed', []), limit)
    if not dynamic_edges.get('added') and not dynamic_edges.get('removed'):
        lines.append('  No runtime edge changes.')

    lines.append('')
    lines.append(f"Waste: {waste['before']} -> {waste['after']} ({_delta(waste['delta'])})")
    _append_list(lines, 'New waste', '+', waste['added'], limit)
    _append_list(lines, 'Removed waste', '-', waste['removed'], limit)
    if not waste['added'] and not waste['removed']:
        lines.append('  Waste set unchanged.')

    lines.append('')
    lines.append(f"Classification changes: {len(classification_changes)}")
    if classification_changes:
        for item in classification_changes[:limit]:
            lines.append(f"  ~ {item['id']}: {item.get('before') or 'unknown'} -> {item.get('after') or 'unknown'}")
        omitted = len(classification_changes) - limit
        if omitted > 0:
            lines.append(f'  ... {omitted} more')
    else:
        lines.append('  No existing files changed classification.')

    lines.append('')
    lines.append(f"Confidence changes: {len(confidence_changes)}")
    if confidence_changes:
        for item in confidence_changes[:limit]:
            before = item.get('before_score')
            after = item.get('after_score')
            delta = item.get('delta')
            delta_text = f" ({_delta(delta)})" if isinstance(delta, int) else ''
            lines.append(
                f"  ~ {item['id']}: {before if before is not None else 'n/a'} -> "
                f"{after if after is not None else 'n/a'}{delta_text}"
            )
        omitted = len(confidence_changes) - limit
        if omitted > 0:
            lines.append(f'  ... {omitted} more')
    else:
        lines.append('  No shared dead-file confidence changes.')

    before_runtime = runtime.get('before', {}) if isinstance(runtime.get('before'), dict) else {}
    after_runtime = runtime.get('after', {}) if isinstance(runtime.get('after'), dict) else {}
    if before_runtime.get('enabled') or after_runtime.get('enabled'):
        lines.append('')
        lines.append(
            'Runtime: '
            f"{'enabled' if before_runtime.get('enabled') else 'off'}"
            f"{' stale' if before_runtime.get('stale') else ''} -> "
            f"{'enabled' if after_runtime.get('enabled') else 'off'}"
            f"{' stale' if after_runtime.get('stale') else ''}"
        )

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
