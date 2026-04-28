from __future__ import annotations

from collections import Counter
from typing import Any


BYTE_BUCKETS = (
    ('tiny', 0, 1_024),
    ('small', 1_025, 10_240),
    ('medium', 10_241, 102_400),
    ('large', 102_401, 1_048_576),
    ('huge', 1_048_577, None),
)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_language(value: Any) -> str:
    language = str(value or '').strip().lower()
    return language or 'unknown'


def _count_bucket(value: int, thresholds: tuple[tuple[str, int], ...]) -> str:
    for name, upper in thresholds:
        if value <= upper:
            return name
    return thresholds[-1][0]


def _byte_bucket(size: int) -> str:
    for name, lower, upper in BYTE_BUCKETS:
        if size >= lower and (upper is None or size <= upper):
            return name
    return 'unknown'


def _scan_time(meta: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ('elapsed_s', 'scan_elapsed_s', 'started_at', 'finished_at', 'generated_at'):
        if key in meta:
            result[key] = meta[key]
    runtime_meta = meta.get('runtime') if isinstance(meta.get('runtime'), dict) else {}
    if 'elapsed_s' in runtime_meta:
        result['runtime_elapsed_s'] = runtime_meta.get('elapsed_s')
    elif 'elapsed_s' in runtime:
        result['runtime_elapsed_s'] = runtime.get('elapsed_s')
    if 'session_count' in runtime_meta:
        result['runtime_session_count'] = runtime_meta.get('session_count')
    return result


def build_scale_proof(graph: dict[str, Any]) -> dict[str, Any]:
    """Build README/CI-friendly evidence that summarizes graph scale."""
    nodes = [node for node in _as_list(graph.get('nodes')) if isinstance(node, dict)]
    edges = [edge for edge in _as_list(graph.get('edges')) if isinstance(edge, dict)]
    dynamic_edges = [edge for edge in _as_list(graph.get('dynamic_edges')) if isinstance(edge, dict)]
    meta = graph.get('meta') if isinstance(graph.get('meta'), dict) else {}
    summary = graph.get('summary') if isinstance(graph.get('summary'), dict) else {}
    runtime = graph.get('runtime') if isinstance(graph.get('runtime'), dict) else {}

    language_counts = Counter(_normalize_language(node.get('language')) for node in nodes)
    size_buckets: Counter[str] = Counter()
    total_bytes = 0
    largest_files: list[dict[str, Any]] = []
    for node in nodes:
        try:
            size = int(node.get('size') or 0)
        except (TypeError, ValueError):
            size = 0
        total_bytes += max(0, size)
        size_buckets[_byte_bucket(max(0, size))] += 1
        largest_files.append({
            'id': str(node.get('id') or node.get('filepath') or ''),
            'language': _normalize_language(node.get('language')),
            'size': max(0, size),
        })
    largest_files.sort(key=lambda item: (-item['size'], item['id']))

    file_count = int(meta.get('total_files', summary.get('total', len(nodes))) or 0)
    static_edge_count = int(meta.get('total_edges', len(edges)) or 0)
    dynamic_edge_count = len(dynamic_edges)

    return {
        'files': file_count,
        'edges': {
            'static': static_edge_count,
            'runtime': dynamic_edge_count,
            'total': static_edge_count + dynamic_edge_count,
        },
        'languages': dict(sorted(language_counts.items())),
        'scan_time': _scan_time(meta, runtime),
        'size_buckets': {name: size_buckets.get(name, 0) for name, *_ in BYTE_BUCKETS},
        'total_bytes': total_bytes,
        'largest_files': largest_files[:10],
        'scale_buckets': {
            'files': _count_bucket(file_count, (
                ('tiny', 25),
                ('small', 100),
                ('medium', 500),
                ('large', 2_500),
                ('huge', 10**18),
            )),
            'edges': _count_bucket(static_edge_count + dynamic_edge_count, (
                ('tiny', 50),
                ('small', 250),
                ('medium', 1_500),
                ('large', 10_000),
                ('huge', 10**18),
            )),
            'bytes': _byte_bucket(total_bytes),
        },
    }


def _markdown_cell(value: Any) -> str:
    return str(value).replace('|', '\\|').replace('\n', ' ')


def format_scale_proof_markdown(proof: dict[str, Any]) -> str:
    edges = proof.get('edges', {}) if isinstance(proof.get('edges'), dict) else {}
    scale = proof.get('scale_buckets', {}) if isinstance(proof.get('scale_buckets'), dict) else {}
    lines = [
        '# Orbits Scale Proof',
        '',
        f"- Files: {proof.get('files', 0)} ({scale.get('files', 'unknown')})",
        f"- Static edges: {edges.get('static', 0)}",
        f"- Runtime edges: {edges.get('runtime', 0)}",
        f"- Total edges: {edges.get('total', 0)} ({scale.get('edges', 'unknown')})",
        f"- Total indexed bytes: {proof.get('total_bytes', 0)} ({scale.get('bytes', 'unknown')})",
        '',
    ]

    scan_time = proof.get('scan_time', {}) if isinstance(proof.get('scan_time'), dict) else {}
    if scan_time:
        lines.extend(['## Scan metadata', ''])
        for key in sorted(scan_time):
            lines.append(f"- {key}: `{_markdown_cell(scan_time[key])}`")
        lines.append('')

    languages = proof.get('languages', {}) if isinstance(proof.get('languages'), dict) else {}
    lines.extend(['## Languages', ''])
    if languages:
        lines.extend(['| Language | Files |', '| --- | ---: |'])
        for language, count in sorted(languages.items(), key=lambda item: (-int(item[1]), item[0])):
            lines.append(f"| {_markdown_cell(language)} | {int(count)} |")
    else:
        lines.append('No language data recorded.')
    lines.append('')

    buckets = proof.get('size_buckets', {}) if isinstance(proof.get('size_buckets'), dict) else {}
    lines.extend(['## File size buckets', '', '| Bucket | Files |', '| --- | ---: |'])
    for name, *_ in BYTE_BUCKETS:
        lines.append(f"| {name} | {int(buckets.get(name, 0) or 0)} |")
    lines.append('')

    largest = proof.get('largest_files', []) if isinstance(proof.get('largest_files'), list) else []
    if largest:
        lines.extend(['## Largest files', '', '| Path | Language | Bytes |', '| --- | --- | ---: |'])
        for item in largest:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| `{_markdown_cell(item.get('id', ''))}` "
                f"| {_markdown_cell(item.get('language', 'unknown'))} "
                f"| {int(item.get('size', 0) or 0)} |"
            )
        lines.append('')

    return '\n'.join(lines).rstrip() + '\n'
