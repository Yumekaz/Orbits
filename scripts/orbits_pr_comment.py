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
    waste = graph.get('waste', []) if isinstance(graph.get('waste'), list) else []
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


def _top_waste_rows(graph: dict[str, Any] | None, limit: int) -> list[str]:
    if not graph:
        return []
    waste = graph.get('waste', [])
    if not isinstance(waste, list) or not waste:
        return []

    rows = [
        '| Path | Classification | Size | Island |',
        '| --- | --- | ---: | ---: |',
    ]
    for item in waste[:limit]:
        if not isinstance(item, dict):
            continue
        rows.append(
            f"| `{_markdown_cell(_normalize_id(item.get('id') or item.get('filepath')))}` "
            f"| {_markdown_cell(item.get('classification', ''))} "
            f"| {int(item.get('size', 0) or 0)} "
            f"| {_markdown_cell(item.get('island_id', -1))} |"
        )
    omitted = len(waste) - limit
    if omitted > 0:
        rows.append(f'| ... {omitted} more | | | |')
    return rows


def _append_diff(lines: list[str], diff: dict[str, Any] | None, limit: int) -> None:
    if not diff:
        lines.append('No graph diff was generated for this run.')
        return

    nodes = diff.get('nodes', {})
    edges = diff.get('edges', {})
    waste = diff.get('waste', {})
    lines.extend([
        '| Area | Before | After | Delta |',
        '| --- | ---: | ---: | ---: |',
        f"| Nodes | {nodes.get('before', 'n/a')} | {nodes.get('after', 'n/a')} | {_delta(nodes.get('delta', 'n/a'))} |",
        f"| Edges | {edges.get('before', 'n/a')} | {edges.get('after', 'n/a')} | {_delta(edges.get('delta', 'n/a'))} |",
        f"| Dead files | {waste.get('before', 'n/a')} | {waste.get('after', 'n/a')} | {_delta(waste.get('delta', 'n/a'))} |",
        '',
    ])

    details: list[str] = []
    for title, values, prefix in (
        ('Added nodes', nodes.get('added', []), '+'),
        ('Removed nodes', nodes.get('removed', []), '-'),
        ('New dead files', waste.get('added', []), '+'),
        ('Resolved dead files', waste.get('removed', []), '-'),
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
        ('Added edges', edges.get('added', []), '+'),
        ('Removed edges', edges.get('removed', []), '-'),
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
        lines.append('')

    waste_rows = _top_waste_rows(graph, limit=limit)
    if waste_rows:
        lines.extend(['### Top dead files', '', *waste_rows, ''])
    elif graph:
        lines.extend(['### Dead files', '', 'No actionable dead files found.', ''])

    lines.extend(['### Graph diff', ''])
    _append_diff(lines, diff, limit=limit)
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
