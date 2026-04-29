from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


CONFIDENCE_ORDER = ('deep', 'partial', 'unknown')


LANGUAGE_PROFILES: dict[str, dict[str, Any]] = {
    'python': {
        'display': 'Python',
        'role': 'source',
        'tier': 'deep',
        'analysis_confidence': 'deep',
        'note': 'Tree-sitter extraction plus project-aware import resolution.',
    },
    'javascript': {
        'display': 'JavaScript',
        'role': 'source',
        'tier': 'deep',
        'analysis_confidence': 'deep',
        'note': 'Tree-sitter extraction plus Node/package-aware resolution.',
    },
    'typescript': {
        'display': 'TypeScript',
        'role': 'source',
        'tier': 'deep',
        'analysis_confidence': 'deep',
        'note': 'Tree-sitter extraction plus tsconfig alias/package resolution.',
    },
    'tsx': {
        'display': 'TSX/JSX',
        'role': 'source',
        'tier': 'deep',
        'analysis_confidence': 'deep',
        'note': 'Tree-sitter extraction plus tsconfig alias/package resolution.',
    },
    'go': {
        'display': 'Go',
        'role': 'source',
        'tier': 'deep',
        'analysis_confidence': 'deep',
        'note': 'Tree-sitter extraction plus Go module-aware resolution.',
    },
    'c': {
        'display': 'C',
        'role': 'source',
        'tier': 'deep',
        'analysis_confidence': 'deep',
        'note': 'Tree-sitter extraction plus include-path resolution.',
    },
    'cpp': {
        'display': 'C/C++',
        'role': 'source',
        'tier': 'deep',
        'analysis_confidence': 'deep',
        'note': 'Tree-sitter extraction plus include-path resolution.',
    },
    'java': {
        'display': 'Java',
        'role': 'source',
        'tier': 'deep',
        'analysis_confidence': 'deep',
        'note': 'Tree-sitter extraction plus package/symbol resolution.',
    },
    'kotlin': {
        'display': 'Kotlin',
        'role': 'source',
        'tier': 'deep',
        'analysis_confidence': 'deep',
        'note': 'Tree-sitter extraction plus package/symbol resolution.',
    },
    'html': {
        'display': 'HTML',
        'role': 'web',
        'tier': 'deep',
        'analysis_confidence': 'deep',
        'note': 'Static HTML references are extracted; runtime DOM creation is not inferred.',
    },
    'css': {
        'display': 'CSS',
        'role': 'web',
        'tier': 'deep',
        'analysis_confidence': 'deep',
        'note': 'Static CSS imports and url() references are extracted.',
    },
    'asset': {
        'display': 'Asset',
        'role': 'asset',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Referenced static asset; no source parsing is attempted.',
    },
    'rust': {
        'display': 'Rust',
        'role': 'source',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Best-effort module/use extraction; macro expansion and Cargo semantics are not fully modeled.',
    },
    'csharp': {
        'display': 'C#',
        'role': 'source',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Best-effort using/namespace extraction; MSBuild project semantics are not fully modeled.',
    },
    'php': {
        'display': 'PHP',
        'role': 'source',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Best-effort require/include/use extraction; dynamic autoloading is not fully modeled.',
    },
    'ruby': {
        'display': 'Ruby',
        'role': 'source',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Best-effort require/require_relative extraction; Rails autoloading is not fully modeled.',
    },
    'json': {
        'display': 'JSON',
        'role': 'config',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Configuration file is mapped and classified; schema-specific references are limited.',
    },
    'yaml': {
        'display': 'YAML',
        'role': 'config',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Configuration file is mapped and classified; schema-specific references are limited.',
    },
    'toml': {
        'display': 'TOML',
        'role': 'config',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Configuration file is mapped and classified; schema-specific references are limited.',
    },
    'dockerfile': {
        'display': 'Dockerfile',
        'role': 'build',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Build/deploy file is mapped; COPY/ADD path extraction is best effort.',
    },
    'docker-compose': {
        'display': 'Docker Compose',
        'role': 'build',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Compose file is mapped as production glue; service graph semantics are limited.',
    },
    'makefile': {
        'display': 'Makefile',
        'role': 'build',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Build/run file is mapped; include references are extracted best effort.',
    },
    'shell': {
        'display': 'Shell',
        'role': 'script',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Best-effort source/dot-script extraction; runtime shell expansion is not modeled.',
    },
    'sql': {
        'display': 'SQL',
        'role': 'data',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'SQL files are mapped; include directives are extracted best effort.',
    },
    'github-actions': {
        'display': 'GitHub Actions',
        'role': 'ci',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Workflow file is mapped as CI glue; action behavior is not executed.',
    },
    'generic': {
        'display': 'Generic',
        'role': 'source',
        'tier': 'partial',
        'analysis_confidence': 'partial',
        'note': 'Regex fallback for import-like statements.',
    },
    'unknown': {
        'display': 'Unknown',
        'role': 'unknown',
        'tier': 'unknown',
        'analysis_confidence': 'unknown',
        'note': 'File is visible in the map, but Orbits has no parser hint for it yet.',
    },
}


def language_profile(language: Any, parser_available: bool = True) -> dict[str, Any]:
    key = str(language or 'unknown').strip().lower() or 'unknown'
    base = dict(LANGUAGE_PROFILES.get(key, LANGUAGE_PROFILES['unknown']))
    base['language'] = key
    if not parser_available and base.get('analysis_confidence') == 'deep':
        base['tier'] = 'unavailable'
        base['analysis_confidence'] = 'unknown'
        base['note'] = 'Parser package unavailable in this environment.'
    return base


def annotate_node_language(node: dict[str, Any], parser_available: bool = True) -> dict[str, Any]:
    profile = language_profile(node.get('language'), parser_available=parser_available)
    node.setdefault('language_label', profile['display'])
    node.setdefault('language_role', profile['role'])
    node.setdefault('analysis_tier', profile['tier'])
    node.setdefault('analysis_confidence', profile['analysis_confidence'])
    node.setdefault('analysis_note', profile['note'])
    return node


def build_language_coverage(
    nodes: list[dict[str, Any]],
    language_support: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    total = len(nodes)
    by_language: dict[str, dict[str, Any]] = {}
    confidence_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    tier_counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    support = language_support or {}

    for node in nodes:
        language = str(node.get('language') or 'unknown').strip().lower() or 'unknown'
        status = support.get(language, {})
        parser_available = bool(status.get('available', True))
        profile = language_profile(language, parser_available=parser_available)
        confidence = str(node.get('analysis_confidence') or profile['analysis_confidence'])
        role = str(node.get('language_role') or profile['role'])
        tier = str(node.get('analysis_tier') or profile['tier'])

        confidence_counts[confidence] += 1
        role_counts[role] += 1
        tier_counts[tier] += 1
        bucket = by_language.setdefault(language, {
            'language': language,
            'display': profile['display'],
            'files': 0,
            'role': role,
            'tier': tier,
            'analysis_confidence': confidence,
            'parser_available': parser_available,
            'note': profile['note'],
            'examples': [],
        })
        bucket['files'] += 1
        if not parser_available:
            bucket['parser_available'] = False
            bucket['reason'] = status.get('reason', 'parser unavailable')
        node_id = str(node.get('id') or node.get('filepath') or '')
        if node_id and len(examples[language]) < 3:
            examples[language].append(node_id)

    for language, bucket in by_language.items():
        bucket['examples'] = examples.get(language, [])

    def pct(count: int) -> float:
        return round(count / total * 100, 1) if total else 0.0

    confidence = {
        name: {
            'files': confidence_counts.get(name, 0),
            'percent': pct(confidence_counts.get(name, 0)),
        }
        for name in CONFIDENCE_ORDER
    }
    for name, count in sorted(confidence_counts.items()):
        if name not in confidence:
            confidence[name] = {'files': count, 'percent': pct(count)}

    result = {
        'total_files': total,
        'confidence': confidence,
        'roles': {name: {'files': count, 'percent': pct(count)} for name, count in sorted(role_counts.items())},
        'tiers': {name: {'files': count, 'percent': pct(count)} for name, count in sorted(tier_counts.items())},
        'languages': sorted(by_language.values(), key=lambda item: (-item['files'], item['language'])),
    }
    for name in CONFIDENCE_ORDER:
        result[name] = confidence[name]
    return result


def _markdown_cell(value: Any) -> str:
    return str(value).replace('|', '\\|').replace('\n', ' ')


def format_language_coverage_markdown(report: dict[str, Any]) -> str:
    confidence = report.get('confidence', {}) if isinstance(report.get('confidence'), dict) else {}
    total = int(report.get('total_files', 0) or 0)
    lines = [
        '# Orbits Language Coverage',
        '',
        f'- Files mapped: {total}',
    ]
    for name in CONFIDENCE_ORDER:
        item = confidence.get(name, {}) if isinstance(confidence.get(name), dict) else {}
        lines.append(f"- {name.title()}: {int(item.get('files', 0) or 0)} files ({item.get('percent', 0)}%)")
    lines.append('')
    lines.extend([
        '| Language | Files | Confidence | Role | Tier | Parser | Notes | Examples |',
        '| --- | ---: | --- | --- | --- | --- | --- | --- |',
    ])
    for item in report.get('languages', []) if isinstance(report.get('languages'), list) else []:
        if not isinstance(item, dict):
            continue
        parser = 'yes' if item.get('parser_available', True) else 'no'
        examples = ', '.join(f"`{_markdown_cell(example)}`" for example in item.get('examples', []))
        lines.append(
            f"| {_markdown_cell(item.get('display') or item.get('language', 'unknown'))} "
            f"| {int(item.get('files', 0) or 0)} "
            f"| {_markdown_cell(item.get('analysis_confidence', 'unknown'))} "
            f"| {_markdown_cell(item.get('role', 'unknown'))} "
            f"| {_markdown_cell(item.get('tier', 'unknown'))} "
            f"| {parser} "
            f"| {_markdown_cell(item.get('reason') or item.get('note', ''))} "
            f"| {examples} |"
        )
    return '\n'.join(lines).rstrip() + '\n'
