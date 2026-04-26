from __future__ import annotations

import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


GIT_TIMEOUT_S = 10
COMMIT_MARKER = '--ORBITS-COMMIT--'


def _normalize_relpath(value: Any) -> str:
    return str(value or '').replace('\\', '/').strip().lstrip('./')


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        'available': False,
        'reason': reason,
        'tracked': False,
        'last_touched_ts': None,
        'last_touched_iso': '',
        'age_days': None,
        'commit_count': None,
        'churn_count': None,
        'top_authors': [],
    }


def _no_history() -> dict[str, Any]:
    return {
        'available': True,
        'reason': 'No committed history for file',
        'tracked': False,
        'last_touched_ts': None,
        'last_touched_iso': '',
        'age_days': None,
        'commit_count': 0,
        'churn_count': 0,
        'top_authors': [],
    }


def _run_git(repo_root: Path, args: list[str], timeout_s: int = GIT_TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ['git', *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def git_repo_info(root: str | Path) -> dict[str, Any]:
    root_path = Path(root).resolve()
    try:
        inside = _run_git(root_path, ['rev-parse', '--is-inside-work-tree'], timeout_s=5)
        if inside.returncode != 0 or inside.stdout.strip().lower() != 'true':
            return _unavailable('Not a git repository')
        top = _run_git(root_path, ['rev-parse', '--show-toplevel'], timeout_s=5)
        if top.returncode != 0:
            return _unavailable(top.stderr.strip() or 'Git repository root unavailable')
    except FileNotFoundError:
        return _unavailable('git executable not found')
    except Exception as exc:
        return _unavailable(f'Git unavailable: {exc}')

    repo_root = Path(top.stdout.strip()).resolve()
    return {
        'available': True,
        'root': str(repo_root),
        'reason': '',
    }


def _git_relpath(root: Path, repo_root: Path, relpath: str) -> str | None:
    if not relpath:
        return None
    try:
        return (root / relpath).resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return None


def _iso_from_ts(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace('+00:00', 'Z')


def parse_git_log_numstat(output: str, reference_time: datetime | None = None) -> dict[str, Any]:
    commits: set[str] = set()
    authors: Counter[str] = Counter()
    latest_ts: int | None = None
    churn = 0

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(COMMIT_MARKER):
            parts = line.split('\t', 3)
            if len(parts) < 4:
                continue
            commit_hash = parts[1]
            commits.add(commit_hash)
            try:
                timestamp = int(parts[2])
            except ValueError:
                timestamp = 0
            if timestamp and (latest_ts is None or timestamp > latest_ts):
                latest_ts = timestamp
            authors[parts[3] or 'unknown'] += 1
            continue

        parts = line.split('\t')
        if len(parts) < 3:
            continue
        try:
            additions = int(parts[0])
            deletions = int(parts[1])
        except ValueError:
            continue
        churn += additions + deletions

    if not commits:
        return _no_history()

    now = reference_time or datetime.now(tz=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    age_days = None
    if latest_ts is not None:
        age_days = max(0, int((now.timestamp() - latest_ts) // 86400))

    return {
        'available': True,
        'reason': '',
        'tracked': True,
        'last_touched_ts': latest_ts,
        'last_touched_iso': _iso_from_ts(latest_ts) if latest_ts is not None else '',
        'age_days': age_days,
        'commit_count': len(commits),
        'churn_count': churn,
        'top_authors': [
            {'name': name, 'commits': count}
            for name, count in sorted(authors.items(), key=lambda item: (-item[1], item[0]))[:3]
        ],
    }


def collect_git_context(
    root: str | Path,
    relpaths: list[str],
    reference_time: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    normalized = sorted({_normalize_relpath(path) for path in relpaths if _normalize_relpath(path)})
    if not normalized:
        return {}

    repo = git_repo_info(root)
    if not repo.get('available'):
        unavailable = _unavailable(str(repo.get('reason') or 'Git unavailable'))
        return {path: dict(unavailable) for path in normalized}

    root_path = Path(root).resolve()
    repo_root = Path(str(repo['root'])).resolve()
    results: dict[str, dict[str, Any]] = {}

    for relpath in normalized:
        git_path = _git_relpath(root_path, repo_root, relpath)
        if not git_path:
            results[relpath] = _unavailable('File is outside git repository')
            continue
        try:
            proc = _run_git(
                repo_root,
                [
                    'log',
                    '--follow',
                    '--date=unix',
                    f'--format={COMMIT_MARKER}%x09%H%x09%ct%x09%an',
                    '--numstat',
                    '--',
                    git_path,
                ],
            )
        except FileNotFoundError:
            results[relpath] = _unavailable('git executable not found')
            continue
        except Exception as exc:
            results[relpath] = _unavailable(f'Git log unavailable: {exc}')
            continue

        if proc.returncode != 0:
            results[relpath] = _unavailable(proc.stderr.strip() or 'Git log unavailable')
            continue
        results[relpath] = parse_git_log_numstat(proc.stdout, reference_time=reference_time)

    return results


def _runtime_context(graph: dict[str, Any]) -> dict[str, Any]:
    runtime_meta = graph.get('meta', {}).get('runtime', {})
    runtime_payload = graph.get('runtime', {}) if isinstance(graph.get('runtime'), dict) else {}
    enabled = bool(runtime_meta.get('enabled') or runtime_payload)
    stale = bool(runtime_meta.get('stale') or runtime_payload.get('stale'))
    touched: set[str] = set()

    for edge in graph.get('dynamic_edges', []) or []:
        if not isinstance(edge, dict):
            continue
        source = _normalize_relpath(edge.get('source'))
        target = _normalize_relpath(edge.get('target'))
        if source:
            touched.add(source)
        if target:
            touched.add(target)

    for access in runtime_payload.get('file_accesses', []) or []:
        if not isinstance(access, dict):
            continue
        source = _normalize_relpath(access.get('source'))
        path = _normalize_relpath(access.get('path'))
        if source:
            touched.add(source)
        if path:
            touched.add(path)

    runtime_entry = runtime_payload.get('entry', {})
    entry_target = runtime_entry.get('target') if isinstance(runtime_entry, dict) else ''
    entrypoint = _normalize_relpath(runtime_meta.get('entrypoint') or entry_target)
    if entrypoint:
        touched.add(entrypoint)

    return {
        'available': enabled,
        'stale': stale,
        'touched_ids': touched,
    }


def _confidence_level(score: int) -> str:
    if score >= 75:
        return 'high'
    if score >= 55:
        return 'medium'
    return 'low'


def score_dead_code(item: dict[str, Any], git: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    classification = str(item.get('classification') or '')
    score = 50
    reasons: list[str] = []

    if classification == 'ORPHAN':
        score += 22
        reasons.append('structural orphan with no static in/out edges')
    elif classification == 'ISLAND':
        score += 14
        reasons.append('unreachable island component')
    else:
        reasons.append(f'structural classification is {classification or "unknown"}')

    if not git.get('available'):
        reasons.append(f"git history unavailable: {git.get('reason') or 'unknown'}")
    elif not git.get('tracked'):
        score -= 8
        reasons.append('no committed git history for this file')
    else:
        age_days = git.get('age_days')
        commit_count = int(git.get('commit_count') or 0)
        churn_count = int(git.get('churn_count') or 0)
        if isinstance(age_days, int):
            if age_days >= 365:
                score += 12
                reasons.append(f'last touched {age_days} days ago')
            elif age_days >= 90:
                score += 6
                reasons.append(f'last touched {age_days} days ago')
            elif age_days <= 14:
                score -= 20
                reasons.append(f'recently touched {age_days} days ago')
            elif age_days <= 30:
                score -= 12
                reasons.append(f'recently touched {age_days} days ago')

        if commit_count <= 1:
            score += 8
            reasons.append('single-commit file history')
        elif commit_count <= 3:
            score += 4
            reasons.append(f'low commit count ({commit_count})')
        elif commit_count >= 20:
            score -= 12
            reasons.append(f'high commit count ({commit_count})')
        elif commit_count >= 10:
            score -= 6
            reasons.append(f'moderate commit count ({commit_count})')

        if churn_count <= 5:
            score += 4
            reasons.append(f'low churn ({churn_count} changed lines)')
        elif churn_count >= 200:
            score -= 10
            reasons.append(f'high churn ({churn_count} changed lines)')
        elif churn_count >= 50:
            score -= 5
            reasons.append(f'moderate churn ({churn_count} changed lines)')

    item_id = _normalize_relpath(item.get('id') or item.get('filepath'))
    runtime_available = bool(runtime.get('available'))
    runtime_stale = bool(runtime.get('stale'))
    runtime_touched = item_id in runtime.get('touched_ids', set())
    if runtime_available and runtime_touched and not runtime_stale:
        score -= 45
        reasons.append('observed in fresh runtime trace')
    elif runtime_available and runtime_touched and runtime_stale:
        score -= 20
        reasons.append('observed only in stale runtime trace')
    elif runtime_available and not runtime_touched and not runtime_stale:
        score += 8
        reasons.append('not observed in fresh runtime trace')
    elif runtime_available:
        reasons.append('runtime trace is stale, absence is weak evidence')
    else:
        reasons.append('no runtime evidence available')

    score = max(0, min(100, int(score)))
    return {
        'score': score,
        'level': _confidence_level(score),
        'reasons': reasons,
    }


def enrich_dead_code_confidence(
    graph: dict[str, Any],
    root: str | Path | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    waste = graph.get('waste', [])
    if not isinstance(waste, list):
        return graph

    root_value = root or graph.get('meta', {}).get('root')
    relpaths = [_normalize_relpath(item.get('id') or item.get('filepath')) for item in waste if isinstance(item, dict)]
    git_contexts = collect_git_context(root_value, relpaths, reference_time=reference_time) if root_value else {
        relpath: _unavailable('Graph root unavailable')
        for relpath in relpaths
    }
    runtime = _runtime_context(graph)

    for item in waste:
        if not isinstance(item, dict):
            continue
        relpath = _normalize_relpath(item.get('id') or item.get('filepath'))
        git = git_contexts.get(relpath, _unavailable('Git context unavailable'))
        confidence = score_dead_code(item, git, runtime)
        item['git'] = git
        item['runtime_context'] = {
            'available': bool(runtime.get('available')),
            'stale': bool(runtime.get('stale')),
            'touched': relpath in runtime.get('touched_ids', set()),
        }
        item['dead_confidence'] = confidence
        item['confidence_score'] = confidence['score']
        item['confidence_level'] = confidence['level']
        item['confidence_reasons'] = list(confidence['reasons'])

    meta = graph.setdefault('meta', {})
    repo = git_repo_info(root_value) if root_value else _unavailable('Graph root unavailable')
    meta['git'] = {
        'available': bool(repo.get('available')),
        'root': repo.get('root', ''),
        'reason': repo.get('reason', ''),
        'waste_items_enriched': len(waste),
    }
    return graph


def format_top_authors(git: dict[str, Any]) -> str:
    authors = git.get('top_authors') or []
    return ', '.join(f"{item.get('name', 'unknown')} ({item.get('commits', 0)})" for item in authors)


def flatten_waste_for_report(item: dict[str, Any]) -> dict[str, Any]:
    git = item.get('git') if isinstance(item.get('git'), dict) else {}
    runtime = item.get('runtime_context') if isinstance(item.get('runtime_context'), dict) else {}
    confidence = item.get('dead_confidence') if isinstance(item.get('dead_confidence'), dict) else {}
    reasons = confidence.get('reasons', item.get('confidence_reasons', []))
    if isinstance(reasons, list):
        reason_text = '; '.join(str(reason) for reason in reasons)
    else:
        reason_text = str(reasons or '')
    git_age_days = git.get('age_days', '')
    git_commit_count = git.get('commit_count', '')
    git_churn_count = git.get('churn_count', '')
    return {
        **item,
        'confidence_score': confidence.get('score', item.get('confidence_score', '')),
        'confidence_level': confidence.get('level', item.get('confidence_level', '')),
        'confidence_reasons': reason_text,
        'git_available': git.get('available', ''),
        'git_reason': git.get('reason', ''),
        'git_last_touched_iso': git.get('last_touched_iso', ''),
        'git_age_days': '' if git_age_days is None else git_age_days,
        'git_commit_count': '' if git_commit_count is None else git_commit_count,
        'git_churn_count': '' if git_churn_count is None else git_churn_count,
        'git_top_authors': format_top_authors(git),
        'runtime_available': runtime.get('available', ''),
        'runtime_touched': runtime.get('touched', ''),
        'runtime_stale': runtime.get('stale', ''),
    }
