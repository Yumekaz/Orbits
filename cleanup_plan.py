from __future__ import annotations

from collections import defaultdict
from typing import Any


SAFE_THRESHOLD = 75
RISKY_THRESHOLD = 55


def _normalize_id(value: Any) -> str:
    return str(value or '').replace('\\', '/').strip().lstrip('./')


def _confidence(item: dict[str, Any]) -> tuple[int | None, str, list[str]]:
    payload = item.get('dead_confidence') if isinstance(item.get('dead_confidence'), dict) else {}
    raw_score = payload.get('score', item.get('confidence_score'))
    try:
        score = int(raw_score)
    except (TypeError, ValueError):
        score = None
    level = str(payload.get('level', item.get('confidence_level', '')) or '').lower()
    raw_reasons = payload.get('reasons', item.get('confidence_reasons', []))
    if isinstance(raw_reasons, str):
        reasons = [part.strip() for part in raw_reasons.split(';') if part.strip()]
    elif isinstance(raw_reasons, list):
        reasons = [str(reason).strip() for reason in raw_reasons if str(reason).strip()]
    else:
        reasons = []
    return score, level, reasons


def _runtime_context(item: dict[str, Any]) -> dict[str, Any]:
    runtime = item.get('runtime_context')
    if isinstance(runtime, dict):
        return {
            'available': bool(runtime.get('available')),
            'touched': bool(runtime.get('touched')),
            'stale': bool(runtime.get('stale')),
        }
    return {'available': False, 'touched': False, 'stale': False}


def _git_context(item: dict[str, Any]) -> dict[str, Any]:
    git = item.get('git')
    if not isinstance(git, dict):
        return {'available': False, 'tracked': False, 'reason': 'Git evidence missing'}
    return {
        'available': bool(git.get('available')),
        'tracked': bool(git.get('tracked')),
        'reason': str(git.get('reason') or ''),
        'age_days': git.get('age_days'),
        'commit_count': git.get('commit_count'),
        'churn_count': git.get('churn_count'),
        'last_touched_iso': str(git.get('last_touched_iso') or ''),
    }


def _classify_candidate(
    item: dict[str, Any],
    *,
    safe_threshold: int,
    risky_threshold: int,
) -> tuple[str, list[str], list[str]]:
    score, level, reasons = _confidence(item)
    runtime = _runtime_context(item)
    git = _git_context(item)
    classification = str(item.get('classification') or '').upper()
    blockers: list[str] = []

    if classification not in {'ORPHAN', 'ISLAND'}:
        blockers.append(f'classification is {classification or "unknown"}')
    if item.get('entrypoint'):
        blockers.append('detected as an entrypoint')
    if item.get('runtime_only'):
        blockers.append('exists only in a runtime overlay')
    if runtime['touched']:
        blockers.append('observed in runtime trace')
    if score is None:
        blockers.append('missing confidence score')

    risk_notes: list[str] = []
    if not runtime['available']:
        risk_notes.append('runtime evidence unavailable')
    if runtime['stale']:
        risk_notes.append('runtime evidence is stale')
    if not git['available']:
        reason = git.get('reason') or 'unknown'
        risk_notes.append(f'git evidence unavailable: {reason}')
    elif not git['tracked']:
        risk_notes.append('file has no committed git history')
    if isinstance(git.get('age_days'), int) and git['age_days'] <= 30:
        risk_notes.append(f"recently touched in git ({git['age_days']} days ago)")
    if isinstance(git.get('churn_count'), int) and git['churn_count'] >= 200:
        risk_notes.append(f"high git churn ({git['churn_count']} changed lines)")

    if blockers:
        return 'manual', blockers, reasons

    if score is not None and score >= safe_threshold and level in {'', 'high'} and not risk_notes:
        return 'safe', [], reasons

    if score is not None and score >= risky_threshold:
        return 'risky', risk_notes, reasons

    low_reason = f'confidence score is {score}/100' if score is not None else 'confidence score unavailable'
    return 'manual', [low_reason, *risk_notes], reasons


def build_cleanup_plan(
    graph: dict[str, Any],
    *,
    safe_threshold: int = SAFE_THRESHOLD,
    risky_threshold: int = RISKY_THRESHOLD,
) -> dict[str, Any]:
    """Classify graph waste into safe, risky, and manual cleanup buckets."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    waste = graph.get('waste', [])
    if not isinstance(waste, list):
        waste = []

    for item in waste:
        if not isinstance(item, dict):
            continue
        score, level, reasons = _confidence(item)
        runtime = _runtime_context(item)
        git = _git_context(item)
        bucket, blockers, evidence = _classify_candidate(
            item,
            safe_threshold=safe_threshold,
            risky_threshold=risky_threshold,
        )
        candidate = {
            'id': _normalize_id(item.get('id') or item.get('filepath')),
            'classification': str(item.get('classification') or 'unknown').upper(),
            'size': int(item.get('size') or 0),
            'island_id': item.get('island_id', -1),
            'confidence_score': score,
            'confidence_level': level or 'unknown',
            'runtime': runtime,
            'git': git,
            'evidence': evidence or reasons,
            'blockers': blockers,
        }
        buckets[bucket].append(candidate)

    for values in buckets.values():
        values.sort(key=lambda item: (
            -(item.get('confidence_score') if item.get('confidence_score') is not None else -1),
            item['id'],
        ))

    counts = {name: len(buckets.get(name, [])) for name in ('safe', 'risky', 'manual')}
    return {
        'summary': {
            'total_candidates': sum(counts.values()),
            **counts,
            'safe_threshold': safe_threshold,
            'risky_threshold': risky_threshold,
        },
        'safe': buckets.get('safe', []),
        'risky': buckets.get('risky', []),
        'manual': buckets.get('manual', []),
    }


def _markdown_cell(value: Any) -> str:
    return str(value).replace('|', '\\|').replace('\n', ' ')


def _reason_text(candidate: dict[str, Any]) -> str:
    values = candidate.get('blockers') or candidate.get('evidence') or []
    if isinstance(values, list):
        return '; '.join(str(value) for value in values)
    return str(values or '')


def format_cleanup_plan_markdown(plan: dict[str, Any]) -> str:
    summary = plan.get('summary', {}) if isinstance(plan.get('summary'), dict) else {}
    lines = [
        '# Orbits Cleanup Plan',
        '',
        f"- Total candidates: {summary.get('total_candidates', 0)}",
        f"- Safe: {summary.get('safe', 0)}",
        f"- Risky: {summary.get('risky', 0)}",
        f"- Manual: {summary.get('manual', 0)}",
        '',
    ]

    sections = (
        ('safe', 'Safe delete candidates'),
        ('risky', 'Risky candidates'),
        ('manual', 'Manual review'),
    )
    for key, title in sections:
        candidates = plan.get(key, [])
        lines.extend([f'## {title}', ''])
        if not candidates:
            lines.extend(['None.', ''])
            continue
        lines.extend([
            '| Path | Class | Score | Runtime | Git | Reason |',
            '| --- | --- | ---: | --- | --- | --- |',
        ])
        for candidate in candidates:
            runtime = candidate.get('runtime', {})
            runtime_bits = []
            if runtime.get('available'):
                runtime_bits.append('touched' if runtime.get('touched') else 'not touched')
                if runtime.get('stale'):
                    runtime_bits.append('stale')
            else:
                runtime_bits.append('unavailable')
            git = candidate.get('git', {})
            if git.get('available') and git.get('tracked'):
                git_text = f"age {git.get('age_days', '')}d, churn {git.get('churn_count', '')}"
            elif git.get('available'):
                git_text = 'untracked'
            else:
                git_text = git.get('reason') or 'unavailable'
            lines.append(
                f"| `{_markdown_cell(candidate.get('id', ''))}` "
                f"| {_markdown_cell(candidate.get('classification', ''))} "
                f"| {_markdown_cell(candidate.get('confidence_score', ''))} "
                f"| {_markdown_cell(', '.join(runtime_bits))} "
                f"| {_markdown_cell(git_text)} "
                f"| {_markdown_cell(_reason_text(candidate))} |"
            )
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'
