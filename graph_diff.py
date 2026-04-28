from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EdgeKey = tuple[str, str]
CycleKey = tuple[str, ...]


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


def _edge_density(edge_count: int, node_count: int) -> float:
    if node_count <= 1:
        return 0.0
    return round(edge_count / (node_count * (node_count - 1)), 4)


def _endpoint_ids(edges: set[EdgeKey]) -> list[str]:
    endpoints: set[str] = set()
    for source, target in edges:
        endpoints.add(source)
        endpoints.add(target)
    return sorted(endpoints)


def _cycle_keys(graph: dict[str, Any]) -> set[CycleKey]:
    cycles = graph.get('cycles', [])
    if not isinstance(cycles, list):
        return set()

    keys: set[CycleKey] = set()
    for cycle in cycles:
        if not isinstance(cycle, list):
            continue
        normalized = [_normalize_id(item) for item in cycle]
        normalized = [item for item in normalized if item]
        if len(normalized) < 2:
            continue
        if normalized[0] == normalized[-1]:
            normalized = normalized[:-1]
        if not normalized:
            continue
        min_idx = min(range(len(normalized)), key=normalized.__getitem__)
        rotated = normalized[min_idx:] + normalized[:min_idx]
        rotated.append(rotated[0])
        keys.add(tuple(rotated))
    return keys


def _sorted_cycles(cycles: set[CycleKey]) -> list[list[str]]:
    return [list(cycle) for cycle in sorted(cycles)]


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


def _summary_int(graph: dict[str, Any], key: str, fallback: int = 0) -> int:
    summary = graph.get('summary', {}) if isinstance(graph.get('summary'), dict) else {}
    try:
        return int(summary.get(key, fallback) or 0)
    except (TypeError, ValueError):
        return fallback


def _confidence_impact(changes: list[dict[str, Any]]) -> dict[str, Any]:
    increased = 0
    decreased = 0
    max_increase = 0
    max_decrease = 0
    for item in changes:
        delta = item.get('delta')
        if not isinstance(delta, int):
            continue
        if delta > 0:
            increased += 1
            max_increase = max(max_increase, delta)
        elif delta < 0:
            decreased += 1
            max_decrease = min(max_decrease, delta)
    return {
        'changed': len(changes),
        'increased': increased,
        'decreased': decreased,
        'max_increase': max_increase,
        'max_decrease': max_decrease,
    }


def _classification_impact(changes: list[dict[str, str]]) -> dict[str, Any]:
    into_dead = 0
    out_of_dead = 0
    structural = 0
    dead_classes = {'ORPHAN', 'ISLAND'}
    for item in changes:
        before = str(item.get('before') or '').upper()
        after = str(item.get('after') or '').upper()
        if before not in dead_classes and after in dead_classes:
            into_dead += 1
        elif before in dead_classes and after not in dead_classes:
            out_of_dead += 1
        else:
            structural += 1
    return {
        'changed': len(changes),
        'into_dead': into_dead,
        'out_of_dead': out_of_dead,
        'structural': structural,
    }


def _architecture_impact_level(signals: list[str]) -> str:
    high_signals = {
        'new_cycles',
        'new_dead_code',
        'runtime_became_stale',
        'runtime_disabled',
        'confidence_increased',
        'classification_into_dead',
    }
    medium_signals = {
        'coupling_increased',
        'runtime_edges_changed',
        'classification_changed',
        'confidence_changed',
    }
    if any(signal in high_signals for signal in signals):
        return 'high'
    if any(signal in medium_signals for signal in signals):
        return 'medium'
    return 'low'


def _architecture_summary(
    baseline_graph: dict[str, Any],
    current_graph: dict[str, Any],
    baseline_nodes: set[str],
    current_nodes: set[str],
    baseline_edges: set[EdgeKey],
    current_edges: set[EdgeKey],
    baseline_dynamic_edges: set[EdgeKey],
    current_dynamic_edges: set[EdgeKey],
    baseline_waste: set[str],
    current_waste: set[str],
    classification_changes: list[dict[str, str]],
    confidence_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    added_edges = current_edges - baseline_edges
    removed_edges = baseline_edges - current_edges
    added_dynamic_edges = current_dynamic_edges - baseline_dynamic_edges
    removed_dynamic_edges = baseline_dynamic_edges - current_dynamic_edges
    baseline_cycles = _cycle_keys(baseline_graph)
    current_cycles = _cycle_keys(current_graph)
    added_cycles = current_cycles - baseline_cycles
    removed_cycles = baseline_cycles - current_cycles
    before_cycle_count = _summary_int(baseline_graph, 'cycle_count', len(baseline_cycles))
    after_cycle_count = _summary_int(current_graph, 'cycle_count', len(current_cycles))
    before_runtime = _runtime_summary(baseline_graph)
    after_runtime = _runtime_summary(current_graph)
    classification = _classification_impact(classification_changes)
    confidence = _confidence_impact(confidence_changes)

    signals: list[str] = []
    if len(current_edges) > len(baseline_edges):
        signals.append('coupling_increased')
    if added_cycles or after_cycle_count > before_cycle_count:
        signals.append('new_cycles')
    if current_waste - baseline_waste:
        signals.append('new_dead_code')
    if before_runtime.get('enabled') and not after_runtime.get('enabled'):
        signals.append('runtime_disabled')
    if not before_runtime.get('stale') and after_runtime.get('stale'):
        signals.append('runtime_became_stale')
    if len(current_dynamic_edges) != len(baseline_dynamic_edges):
        signals.append('runtime_edges_changed')
    if classification['into_dead'] > 0:
        signals.append('classification_into_dead')
    elif classification['changed'] > 0:
        signals.append('classification_changed')
    if confidence['increased'] > 0:
        signals.append('confidence_increased')
    elif confidence['changed'] > 0:
        signals.append('confidence_changed')

    return {
        'impact': {
            'level': _architecture_impact_level(signals),
            'signals': signals,
        },
        'coupling': {
            'before': {
                'nodes': len(baseline_nodes),
                'static_edges': len(baseline_edges),
                'edge_density': _edge_density(len(baseline_edges), len(baseline_nodes)),
            },
            'after': {
                'nodes': len(current_nodes),
                'static_edges': len(current_edges),
                'edge_density': _edge_density(len(current_edges), len(current_nodes)),
            },
            'delta': {
                'static_edges': len(current_edges) - len(baseline_edges),
                'edge_density': round(
                    _edge_density(len(current_edges), len(current_nodes))
                    - _edge_density(len(baseline_edges), len(baseline_nodes)),
                    4,
                ),
            },
            'added_dependencies': len(added_edges),
            'removed_dependencies': len(removed_edges),
            'affected_nodes': _endpoint_ids(added_edges | removed_edges),
        },
        'cycles': {
            'before': before_cycle_count,
            'after': after_cycle_count,
            'delta': after_cycle_count - before_cycle_count,
            'added': _sorted_cycles(added_cycles),
            'removed': _sorted_cycles(removed_cycles),
        },
        'dead_code': {
            'before': len(baseline_waste),
            'after': len(current_waste),
            'delta': len(current_waste) - len(baseline_waste),
            'added': sorted(current_waste - baseline_waste),
            'removed': sorted(baseline_waste - current_waste),
        },
        'runtime': {
            'before': before_runtime,
            'after': after_runtime,
            'delta': {
                'dynamic_edges': len(current_dynamic_edges) - len(baseline_dynamic_edges),
                'runtime_edges': after_runtime['runtime_edges'] - before_runtime['runtime_edges'],
                'session_count': after_runtime['session_count'] - before_runtime['session_count'],
            },
            'added_dynamic_edges': len(added_dynamic_edges),
            'removed_dynamic_edges': len(removed_dynamic_edges),
            'enabled_changed': before_runtime['enabled'] != after_runtime['enabled'],
            'stale_changed': before_runtime['stale'] != after_runtime['stale'],
        },
        'classification': classification,
        'confidence': confidence,
        'health': {
            'before': _summary_int(baseline_graph, 'health_score', 0),
            'after': _summary_int(current_graph, 'health_score', 0),
            'delta': _summary_int(current_graph, 'health_score', 0) - _summary_int(baseline_graph, 'health_score', 0),
        },
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
    architecture = _architecture_summary(
        baseline_graph,
        current_graph,
        baseline_nodes,
        current_nodes,
        baseline_edges,
        current_edges,
        baseline_dynamic_edges,
        current_dynamic_edges,
        baseline_waste,
        current_waste,
        classification_changes,
        confidence_changes,
    )

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
        'architecture': architecture,
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


def _append_architecture(lines: list[str], architecture: dict[str, Any]) -> None:
    if not architecture:
        return
    impact = architecture.get('impact', {}) if isinstance(architecture.get('impact'), dict) else {}
    coupling = architecture.get('coupling', {}) if isinstance(architecture.get('coupling'), dict) else {}
    cycles = architecture.get('cycles', {}) if isinstance(architecture.get('cycles'), dict) else {}
    dead_code = architecture.get('dead_code', {}) if isinstance(architecture.get('dead_code'), dict) else {}
    runtime = architecture.get('runtime', {}) if isinstance(architecture.get('runtime'), dict) else {}
    health = architecture.get('health', {}) if isinstance(architecture.get('health'), dict) else {}
    coupling_delta = coupling.get('delta', {}) if isinstance(coupling.get('delta'), dict) else {}
    runtime_delta = runtime.get('delta', {}) if isinstance(runtime.get('delta'), dict) else {}
    signals = impact.get('signals') if isinstance(impact.get('signals'), list) else []

    lines.append('')
    lines.append(f"Architecture impact: {str(impact.get('level', 'low')).upper()}")
    if signals:
        lines.append(f"  Signals: {', '.join(str(signal) for signal in signals)}")
    lines.append(
        '  Coupling: '
        f"static edges {_delta(int(coupling_delta.get('static_edges', 0) or 0))}, "
        f"density {_delta(float(coupling_delta.get('edge_density', 0) or 0))}"
    )
    lines.append(
        '  Cycles: '
        f"{cycles.get('before', 0)} -> {cycles.get('after', 0)} ({_delta(int(cycles.get('delta', 0) or 0))})"
    )
    lines.append(
        '  Dead code: '
        f"{dead_code.get('before', 0)} -> {dead_code.get('after', 0)} ({_delta(int(dead_code.get('delta', 0) or 0))})"
    )
    lines.append(
        '  Runtime dynamic edges: '
        f"{_delta(int(runtime_delta.get('dynamic_edges', 0) or 0))}"
    )
    if 'delta' in health:
        lines.append(f"  Health: {_delta(int(health.get('delta', 0) or 0))}")


def format_graph_diff(diff: dict[str, Any], limit: int = 20) -> str:
    limit = max(0, limit)
    nodes = diff['nodes']
    edges = diff['edges']
    dynamic_edges = diff.get('dynamic_edges', {'before': 0, 'after': 0, 'delta': 0, 'added': [], 'removed': []})
    waste = diff['waste']
    classification_changes = diff.get('classification_changes', {}).get('changed', [])
    confidence_changes = diff.get('confidence_changes', {}).get('changed', [])
    runtime = diff.get('runtime', {})
    architecture = diff.get('architecture', {})
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

    _append_architecture(lines, architecture if isinstance(architecture, dict) else {})

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
