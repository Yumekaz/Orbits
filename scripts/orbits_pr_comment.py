from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


COMMENT_MARKER = '<!-- orbits-pr-comment -->'


def _read_json(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    json_path = Path(path)
    if not json_path.exists():
        return None
    try:
        with json_path.open('r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _read_text(path: str | Path | None) -> str:
    if not path:
        return ''
    text_path = Path(path)
    if not text_path.exists():
        return ''
    return text_path.read_text(encoding='utf-8', errors='replace')


def _markdown_cell(value: Any) -> str:
    return str(value).replace('|', '\\|').replace('\n', ' ')


def _normalize_id(value: Any) -> str:
    return str(value or '').replace('\\', '/')


def _delta(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f'+{number}' if number > 0 else str(number)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _waste_items(graph: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not graph:
        return []
    return [item for item in _as_list(graph.get('waste')) if isinstance(item, dict)]


def _waste_id(item: dict[str, Any]) -> str:
    return _normalize_id(item.get('id') or item.get('filepath'))


def _confidence_parts(item: dict[str, Any]) -> tuple[str, str]:
    confidence = item.get('dead_confidence') if isinstance(item.get('dead_confidence'), dict) else {}
    score = confidence.get('score', item.get('confidence_score', ''))
    level = confidence.get('level', item.get('confidence_level', ''))
    score_text = ''
    if score != '' and score is not None:
        try:
            score_text = f'{int(score)}/100'
        except (TypeError, ValueError):
            score_text = str(score)
    level_text = str(level or '').strip()
    if level_text and score_text:
        return level_text.capitalize(), score_text
    if level_text:
        return level_text.capitalize(), ''
    if score_text:
        return 'Unknown', score_text
    return 'Unknown', ''


def _confidence_text(item: dict[str, Any]) -> str:
    level, score = _confidence_parts(item)
    return f'{level} ({score})' if score else level


def _reason_text(item: dict[str, Any], limit: int = 3) -> str:
    confidence = item.get('dead_confidence') if isinstance(item.get('dead_confidence'), dict) else {}
    reasons = confidence.get('reasons', item.get('confidence_reasons', []))
    if isinstance(reasons, str):
        reason_list = [part.strip() for part in reasons.split(';') if part.strip()]
    elif isinstance(reasons, list):
        reason_list = [str(reason).strip() for reason in reasons if str(reason).strip()]
    else:
        reason_list = []
    if not reason_list:
        return 'No confidence reasons were recorded.'
    shown = reason_list[:limit]
    text = '; '.join(shown)
    omitted = len(reason_list) - len(shown)
    if omitted > 0:
        text += f'; +{omitted} more'
    return text


def _classification_explanation(item: dict[str, Any]) -> str:
    classification = str(item.get('classification') or '').upper()
    if classification == 'ORPHAN':
        return 'No static imports in or out'
    if classification == 'ISLAND':
        return 'Only connected to an unreachable island'
    return classification or 'Dead-file candidate'


def _waste_index(graph: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {_waste_id(item): item for item in _waste_items(graph) if _waste_id(item)}


def _tail_lines(text: str, limit: int = 16) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ''
    return '\n'.join(lines[-limit:])


def _check_status(exit_code: int | None) -> str:
    if exit_code == 0:
        return 'PASS'
    if exit_code == 2:
        return 'FAIL'
    if exit_code is None:
        return 'UNKNOWN'
    return f'ERROR ({exit_code})'


def _graph_counts(graph: dict[str, Any] | None) -> dict[str, int | str]:
    if not graph:
        return {
            'files': 'n/a',
            'edges': 'n/a',
            'health': 'n/a',
            'dead_files': 'n/a',
            'orphans': 'n/a',
            'islands': 'n/a',
            'cycles': 'n/a',
        }

    meta = graph.get('meta', {}) if isinstance(graph.get('meta'), dict) else {}
    summary = graph.get('summary', {}) if isinstance(graph.get('summary'), dict) else {}
    waste = _waste_items(graph)
    orphan_count = sum(1 for item in waste if isinstance(item, dict) and item.get('classification') == 'ORPHAN')
    island_keys: set[Any] = set()
    for item in waste:
        if not isinstance(item, dict) or item.get('classification') == 'ORPHAN':
            continue
        island_id = item.get('island_id', -1)
        island_keys.add(island_id if island_id != -1 else item.get('id'))

    return {
        'files': int(meta.get('total_files', summary.get('total', len(graph.get('nodes', [])))) or 0),
        'edges': int(meta.get('total_edges', len(graph.get('edges', []))) or 0),
        'health': int(summary.get('health_score', 0) or 0),
        'dead_files': len(waste),
        'orphans': orphan_count,
        'islands': len(island_keys),
        'cycles': int(summary.get('cycle_count', 0) or 0),
    }


def _runtime_status_text(graph: dict[str, Any] | None) -> str:
    if not graph:
        return 'Runtime: unavailable'
    meta = graph.get('meta', {}) if isinstance(graph.get('meta'), dict) else {}
    runtime = meta.get('runtime', {}) if isinstance(meta.get('runtime'), dict) else {}
    if not (runtime.get('enabled') or graph.get('runtime')):
        return 'Runtime: no trace merged'
    freshness = 'stale' if runtime.get('stale') else 'fresh'
    sessions = runtime.get('session_count', 1)
    edges = runtime.get('runtime_edges', len(graph.get('dynamic_edges', []) or []))
    entrypoint = runtime.get('entrypoint')
    suffix = f', entry `{_normalize_id(entrypoint)}`' if entrypoint else ''
    return f'Runtime: {freshness}, {sessions} session(s), {edges} observed edge(s){suffix}'


def _top_waste_rows(graph: dict[str, Any] | None, limit: int) -> list[str]:
    waste = _waste_items(graph)
    if not waste:
        return []

    rows = [
        '| Path | Why it is actionable | Confidence | Key evidence |',
        '| --- | --- | --- | --- |',
    ]
    for item in waste[:limit]:
        rows.append(
            f"| `{_markdown_cell(_normalize_id(item.get('id') or item.get('filepath')))}` "
            f"| {_markdown_cell(_classification_explanation(item))} "
            f"| {_markdown_cell(_confidence_text(item))} "
            f"| {_markdown_cell(_reason_text(item))} |"
        )
    omitted = len(waste) - limit
    if omitted > 0:
        rows.append(f'| ... {omitted} more | | | |')
    return rows


def _append_new_dead_file_rows(
    lines: list[str],
    graph: dict[str, Any] | None,
    new_dead_files: list[Any],
    limit: int,
) -> None:
    if not new_dead_files:
        lines.append('No new probable dead files compared with the base branch.')
        return

    waste_by_id = _waste_index(graph)
    lines.extend([
        'New probable dead files introduced by this PR:',
        '',
        '| Path | Why Orbits flagged it | Confidence | Reasons |',
        '| --- | --- | --- | --- |',
    ])
    for value in new_dead_files[:limit]:
        dead_id = _normalize_id(value)
        item = waste_by_id.get(dead_id, {'id': dead_id})
        lines.append(
            f"| `{_markdown_cell(dead_id)}` "
            f"| {_markdown_cell(_classification_explanation(item))} "
            f"| {_markdown_cell(_confidence_text(item))} "
            f"| {_markdown_cell(_reason_text(item))} |"
        )
    omitted = len(new_dead_files) - limit
    if omitted > 0:
        lines.append(f'| ... {omitted} more | | | |')


def _append_diff(lines: list[str], diff: dict[str, Any] | None, graph: dict[str, Any] | None, limit: int) -> None:
    if not diff:
        lines.append('No graph diff was generated for this run.')
        return

    nodes = diff.get('nodes', {})
    edges = diff.get('edges', {})
    dynamic_edges = diff.get('dynamic_edges', {})
    waste = diff.get('waste', {})
    classification_changes = _as_list(diff.get('classification_changes', {}).get('changed') if isinstance(diff.get('classification_changes'), dict) else [])
    confidence_changes = _as_list(diff.get('confidence_changes', {}).get('changed') if isinstance(diff.get('confidence_changes'), dict) else [])
    architecture = diff.get('architecture', {}) if isinstance(diff.get('architecture'), dict) else {}
    new_dead_files = _as_list(waste.get('added'))
    resolved_dead_files = _as_list(waste.get('removed'))
    added_edges = _as_list(edges.get('added'))
    removed_edges = _as_list(edges.get('removed'))

    lines.extend([
        (
            f"Graph size: {nodes.get('before', 'n/a')} -> {nodes.get('after', 'n/a')} files "
            f"({_delta(nodes.get('delta', 'n/a'))})."
        ),
        (
            f"Dependency edges: {edges.get('before', 'n/a')} -> {edges.get('after', 'n/a')} "
            f"({_delta(edges.get('delta', 'n/a'))})."
        ),
        (
            f"Runtime edges: {dynamic_edges.get('before', 'n/a')} -> {dynamic_edges.get('after', 'n/a')} "
            f"({_delta(dynamic_edges.get('delta', 'n/a'))})."
        ),
        (
            f"Actionable dead files: {waste.get('before', 'n/a')} -> {waste.get('after', 'n/a')} "
            f"({_delta(waste.get('delta', 'n/a'))})."
        ),
        '',
    ])
    impact = architecture.get('impact', {}) if isinstance(architecture.get('impact'), dict) else {}
    if impact:
        signals = _as_list(impact.get('signals'))
        signal_text = ', '.join(_markdown_cell(signal) for signal in signals) if signals else 'none'
        lines.extend([
            f"Architecture impact: **{str(impact.get('level', 'low')).upper()}**.",
            f"Signals: {signal_text}.",
            '',
        ])
    _append_new_dead_file_rows(lines, graph, new_dead_files, limit)
    lines.append('')

    if resolved_dead_files:
        resolved = ', '.join(f'`{_markdown_cell(_normalize_id(value))}`' for value in resolved_dead_files[:limit])
        omitted = len(resolved_dead_files) - limit
        suffix = f' and {omitted} more' if omitted > 0 else ''
        lines.append(f'Resolved dead-file candidates: {resolved}{suffix}.')
        lines.append('')

    lines.extend([
        '| Area | Before | After | Delta |',
        '| --- | ---: | ---: | ---: |',
        f"| Nodes | {nodes.get('before', 'n/a')} | {nodes.get('after', 'n/a')} | {_delta(nodes.get('delta', 'n/a'))} |",
        f"| Edges | {edges.get('before', 'n/a')} | {edges.get('after', 'n/a')} | {_delta(edges.get('delta', 'n/a'))} |",
        f"| Runtime edges | {dynamic_edges.get('before', 'n/a')} | {dynamic_edges.get('after', 'n/a')} | {_delta(dynamic_edges.get('delta', 'n/a'))} |",
        f"| Dead files | {waste.get('before', 'n/a')} | {waste.get('after', 'n/a')} | {_delta(waste.get('delta', 'n/a'))} |",
        '',
    ])
    if architecture:
        coupling = architecture.get('coupling', {}) if isinstance(architecture.get('coupling'), dict) else {}
        cycles = architecture.get('cycles', {}) if isinstance(architecture.get('cycles'), dict) else {}
        health = architecture.get('health', {}) if isinstance(architecture.get('health'), dict) else {}
        coupling_delta = coupling.get('delta', {}) if isinstance(coupling.get('delta'), dict) else {}
        lines.extend([
            '| Architecture signal | Delta |',
            '| --- | ---: |',
            f"| Static coupling edges | {_delta(coupling_delta.get('static_edges', 0))} |",
            f"| Cycles | {_delta(cycles.get('delta', 0))} |",
            f"| Health | {_delta(health.get('delta', 0))} |",
            '',
        ])

    details: list[str] = []
    for title, values, prefix in (
        ('Added nodes', _as_list(nodes.get('added')), '+'),
        ('Removed nodes', _as_list(nodes.get('removed')), '-'),
        ('New dead files', new_dead_files, '+'),
        ('Resolved dead files', resolved_dead_files, '-'),
    ):
        if not values:
            continue
        details.append(f'**{title}**')
        for value in values[:limit]:
            details.append(f'- `{prefix} {_normalize_id(value)}`')
        omitted = len(values) - limit
        if omitted > 0:
            details.append(f'- ... {omitted} more')
        details.append('')

    edge_changes = [
        ('Added edges', added_edges, '+'),
        ('Removed edges', removed_edges, '-'),
        ('Added runtime edges', _as_list(dynamic_edges.get('added')), '+'),
        ('Removed runtime edges', _as_list(dynamic_edges.get('removed')), '-'),
    ]
    for title, values, prefix in edge_changes:
        if not values:
            continue
        details.append(f'**{title}**')
        for edge in values[:limit]:
            if isinstance(edge, dict):
                source = _normalize_id(edge.get('source'))
                target = _normalize_id(edge.get('target'))
                details.append(f'- `{prefix} {source} -> {target}`')
        omitted = len(values) - limit
        if omitted > 0:
            details.append(f'- ... {omitted} more')
        details.append('')

    if classification_changes:
        details.append('**Classification changes**')
        for item in classification_changes[:limit]:
            if isinstance(item, dict):
                details.append(
                    f"- `{_normalize_id(item.get('id'))}`: "
                    f"{_markdown_cell(item.get('before', 'unknown'))} -> {_markdown_cell(item.get('after', 'unknown'))}"
                )
        omitted = len(classification_changes) - limit
        if omitted > 0:
            details.append(f'- ... {omitted} more')
        details.append('')

    if confidence_changes:
        details.append('**Confidence changes**')
        for item in confidence_changes[:limit]:
            if not isinstance(item, dict):
                continue
            before = item.get('before_score', 'n/a')
            after = item.get('after_score', 'n/a')
            delta = item.get('delta')
            delta_text = f' ({_delta(delta)})' if isinstance(delta, int) else ''
            details.append(f"- `{_normalize_id(item.get('id'))}`: {before} -> {after}{delta_text}")
        omitted = len(confidence_changes) - limit
        if omitted > 0:
            details.append(f'- ... {omitted} more')
        details.append('')

    if details:
        lines.extend([
            '<details>',
            '<summary>Diff details</summary>',
            '',
            *details,
            '</details>',
        ])
    else:
        lines.append('No node, edge, or dead-file changes detected.')


def build_comment_body(
    graph: dict[str, Any] | None,
    *,
    check_exit_code: int | None = None,
    check_text: str = '',
    diff: dict[str, Any] | None = None,
    dead_report_path: str | Path | None = None,
    artifact_name: str = 'orbits-report',
    run_url: str | None = None,
    limit: int = 10,
) -> str:
    counts = _graph_counts(graph)
    status = _check_status(check_exit_code)
    health = f"{counts['health']}/100" if isinstance(counts['health'], int) else counts['health']

    lines = [
        COMMENT_MARKER,
        '## Orbits check',
        '',
        f'**Status:** {status}',
        f'**{_runtime_status_text(graph)}**',
        '',
        '| Files | Edges | Health | Dead files | Orphans | Islands | Cycles |',
        '| ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
        (
            f"| {counts['files']} | {counts['edges']} | {health} | "
            f"{counts['dead_files']} | {counts['orphans']} | {counts['islands']} | {counts['cycles']} |"
        ),
        '',
    ]

    if dead_report_path:
        lines.append(f'Dead-file reports and `graph.json` are available in the `{artifact_name}` artifact.')
        lines.append(f'Markdown report path in the artifact: `{_normalize_id(dead_report_path)}`.')
        if run_url:
            lines.append(f'Download artifacts from the workflow run: {run_url}.')
        lines.append('')

    waste_rows = _top_waste_rows(graph, limit=limit)
    if waste_rows:
        lines.extend(['### Top dead files', '', *waste_rows, ''])
    elif graph:
        lines.extend(['### Dead files', '', 'No actionable dead files found.', ''])

    lines.extend(['### Graph diff', ''])
    _append_diff(lines, diff, graph, limit=limit)
    lines.append('')

    check_tail = _tail_lines(check_text)
    if check_tail:
        lines.extend([
            '<details>',
            '<summary>Check output</summary>',
            '',
            '```text',
            check_tail,
            '```',
            '</details>',
            '',
        ])

    if run_url:
        lines.extend([f'Workflow run: {run_url}', ''])

    return '\n'.join(lines).rstrip() + '\n'


def _parse_exit_code(value: str | None) -> int | None:
    if value is None or value == '':
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _default_run_url() -> str | None:
    server_url = os.environ.get('GITHUB_SERVER_URL')
    repo = os.environ.get('GITHUB_REPOSITORY')
    run_id = os.environ.get('GITHUB_RUN_ID')
    if server_url and repo and run_id:
        return f'{server_url}/{repo}/actions/runs/{run_id}'
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Build a markdown PR comment body for an Orbits CI run.')
    parser.add_argument('--graph', help='Current Orbits graph.json path')
    parser.add_argument('--diff-json', help='Optional graph diff JSON path')
    parser.add_argument('--dead-report-md', help='Optional markdown dead-file report path')
    parser.add_argument('--check-output', action='append', default=[], help='Optional check stdout/stderr text file; repeatable')
    parser.add_argument('--check-exit-code', type=int, help='Orbits check process exit code')
    parser.add_argument('--check-exit-code-file', help='File containing the Orbits check exit code')
    parser.add_argument('--artifact-name', default='orbits-report', help='Actions artifact name referenced in the comment')
    parser.add_argument('--run-url', default=_default_run_url(), help='Workflow run URL')
    parser.add_argument('--limit', type=int, default=10, help='Maximum dead/diff rows to show before truncating')
    parser.add_argument('--output', required=True, help='Markdown file to write')
    args = parser.parse_args(argv)

    check_exit_code = args.check_exit_code
    if check_exit_code is None:
        check_exit_code = _parse_exit_code(_read_text(args.check_exit_code_file))

    check_text = '\n'.join(_read_text(path) for path in args.check_output)
    body = build_comment_body(
        _read_json(args.graph),
        check_exit_code=check_exit_code,
        check_text=check_text,
        diff=_read_json(args.diff_json),
        dead_report_path=args.dead_report_md,
        artifact_name=args.artifact_name,
        run_url=args.run_url,
        limit=max(0, args.limit),
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(body, encoding='utf-8')
    print(output_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
