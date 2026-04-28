"""Backend codebase-map intelligence for Orbits graphs.

It accepts an already-built graph plus a repository root and returns higher-level
map data that the CLI and visualizer attach to each scan result.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable


SOURCE_EXTENSIONS = {
    '.py', '.pyi',
    '.js', '.mjs', '.cjs', '.jsx',
    '.ts', '.mts', '.cts', '.tsx',
    '.go', '.java', '.kt', '.kts',
    '.c', '.cc', '.cpp', '.cxx', '.h', '.hpp',
    '.html', '.htm', '.css',
}

SKIP_DIRS = {
    '.git', '.hg', '.svn',
    'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache',
    'dist', 'build', 'out', 'target', '.tox', '.venv', 'venv',
}

RUNTIME_SCRIPT_NAMES = {
    'start', 'dev', 'serve', 'server', 'web', 'worker', 'preview',
    'build', 'test', 'lint', 'check',
}

MAKE_RUNTIME_TARGETS = {
    'run', 'start', 'serve', 'server', 'dev', 'web', 'worker',
    'build', 'test', 'lint', 'check',
}

FRAMEWORKS = ('Next', 'Vite', 'React', 'Express', 'FastAPI', 'Flask', 'Django')


def build_codebase_map(root: str | Path, graph: dict[str, Any], top_n: int = 10) -> dict[str, Any]:
    """Return map intelligence derived from graph structure and repo manifests."""

    root_path = Path(root).resolve()
    nodes = _normal_nodes(graph.get('nodes', []))
    edges = _normal_edges(graph.get('edges', []), set(nodes))
    outbound, inbound = _build_adjacency(nodes, edges)
    reverse_outbound = _reverse_adjacency(inbound)
    entry_ids = _entrypoint_ids(nodes)
    waste_ids = _waste_ids(graph, nodes)
    islands = _normal_islands(graph.get('islands', []))

    impact_by_node = _impact_metrics(nodes, outbound, inbound, reverse_outbound, entry_ids, waste_ids)
    regions = _folder_regions(nodes, edges, impact_by_node, entry_ids, waste_ids)
    hubs = _core_hubs(nodes, impact_by_node, top_n)
    entrypoints = _entrypoint_summary(nodes, outbound, impact_by_node, entry_ids)
    isolated = _isolated_dead_areas(nodes, islands, waste_ids, impact_by_node)

    return {
        'regions': regions,
        'core_hubs': hubs,
        'entrypoints': entrypoints,
        'isolated': isolated,
        'impact': impact_by_node,
        'framework_signals': discover_framework_signals(root_path),
        'runtime_commands': discover_runtime_commands(root_path),
    }


def discover_framework_signals(root: str | Path) -> list[dict[str, Any]]:
    """Detect common web/backend framework signals from manifests and source."""

    root_path = Path(root).resolve()
    evidence: dict[str, set[str]] = {name: set() for name in FRAMEWORKS}
    deps = _package_dependencies(root_path)

    package_hits = {
        'next': 'Next',
        'vite': 'Vite',
        'react': 'React',
        'express': 'Express',
    }
    for dep, framework in package_hits.items():
        if dep in deps:
            evidence[framework].add(f'package.json dependency: {dep}')

    py_deps = _python_dependencies(root_path)
    python_hits = {
        'fastapi': 'FastAPI',
        'flask': 'Flask',
        'django': 'Django',
    }
    for dep, framework in python_hits.items():
        if dep in py_deps:
            evidence[framework].add(f'pyproject dependency: {dep}')

    file_names = {path.name for path in _walk_files(root_path)}
    if any(name.startswith('next.config.') for name in file_names):
        evidence['Next'].add('next.config.*')
    if any(name.startswith('vite.config.') for name in file_names):
        evidence['Vite'].add('vite.config.*')

    for relpath, text in _source_samples(root_path):
        low = text.lower()
        if re.search(r'\bfrom\s+["\']react["\']|\bimport\s+react\b', text, flags=re.IGNORECASE):
            evidence['React'].add(f'{relpath}: React import')
        if re.search(r'\bfrom\s+["\']express["\']|\brequire\(["\']express["\']\)', text):
            evidence['Express'].add(f'{relpath}: Express import')
        if 'from fastapi import' in low or 'import fastapi' in low:
            evidence['FastAPI'].add(f'{relpath}: FastAPI import')
        if 'from flask import' in low or 'import flask' in low:
            evidence['Flask'].add(f'{relpath}: Flask import')
        if 'django.' in low or 'from django' in low or 'import django' in low:
            evidence['Django'].add(f'{relpath}: Django import')
        if 'next/' in low or "from 'next" in low or 'from "next' in low:
            evidence['Next'].add(f'{relpath}: Next import')
        if '/@vite' in low or 'import.meta.env' in text:
            evidence['Vite'].add(f'{relpath}: Vite runtime marker')

    signals = []
    for framework in FRAMEWORKS:
        items = sorted(evidence[framework])
        if not items:
            continue
        confidence = min(0.95, 0.45 + 0.2 * len(items))
        signals.append({
            'framework': framework,
            'confidence': round(confidence, 2),
            'evidence': items[:8],
        })
    return signals


def discover_runtime_commands(root: str | Path) -> list[dict[str, str]]:
    """Suggest runnable commands from common project manifests."""

    root_path = Path(root).resolve()
    commands: list[dict[str, str]] = []
    commands.extend(_package_json_commands(root_path))
    commands.extend(_pyproject_commands(root_path))
    commands.extend(_makefile_commands(root_path))
    commands.extend(_dockerfile_commands(root_path))
    return _dedupe_commands(commands)


def _normal_nodes(raw_nodes: list[Any]) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        node_id = _norm_id(item.get('id', ''))
        if not node_id:
            continue
        nodes[node_id] = {**item, 'id': node_id}
    return nodes


def _normal_edges(raw_edges: list[Any], node_ids: set[str]) -> list[dict[str, str]]:
    edges = []
    for item in raw_edges:
        if not isinstance(item, dict):
            continue
        source = _norm_id(item.get('source', ''))
        target = _norm_id(item.get('target', ''))
        if source in node_ids and target in node_ids:
            edges.append({'source': source, 'target': target})
    return edges


def _norm_id(value: Any) -> str:
    return str(value or '').replace('\\', '/').strip('/')


def _build_adjacency(nodes: dict[str, dict[str, Any]], edges: list[dict[str, str]]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    outbound = {node_id: set() for node_id in nodes}
    inbound = {node_id: set() for node_id in nodes}
    for edge in edges:
        outbound[edge['source']].add(edge['target'])
        inbound[edge['target']].add(edge['source'])
    return outbound, inbound


def _reverse_adjacency(inbound: dict[str, set[str]]) -> dict[str, set[str]]:
    return {node_id: set(dependents) for node_id, dependents in inbound.items()}


def _entrypoint_ids(nodes: dict[str, dict[str, Any]]) -> set[str]:
    ids = set()
    for node_id, node in nodes.items():
        if node.get('entrypoint') or str(node.get('classification', '')).upper() == 'ENTRY':
            ids.add(node_id)
    return ids


def _waste_ids(graph: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> set[str]:
    ids = {_norm_id(item.get('id', '')) for item in graph.get('waste', []) if isinstance(item, dict)}
    for node_id, node in nodes.items():
        if str(node.get('classification', '')).upper() in {'ORPHAN', 'ISLAND'}:
            ids.add(node_id)
    return {node_id for node_id in ids if node_id}


def _normal_islands(raw: Any) -> list[list[str]]:
    islands = []
    if not isinstance(raw, list):
        return islands
    for cluster in raw:
        if isinstance(cluster, list):
            ids = [_norm_id(item) for item in cluster if _norm_id(item)]
            if ids:
                islands.append(ids)
    return islands


def _impact_metrics(
    nodes: dict[str, dict[str, Any]],
    outbound: dict[str, set[str]],
    inbound: dict[str, set[str]],
    reverse_outbound: dict[str, set[str]],
    entry_ids: set[str],
    waste_ids: set[str],
) -> dict[str, dict[str, Any]]:
    impact = {}
    for node_id, node in sorted(nodes.items()):
        dependents = _reachable(node_id, reverse_outbound)
        dependencies = _reachable(node_id, outbound)
        direct_dependents = len(inbound.get(node_id, set()))
        direct_dependencies = len(outbound.get(node_id, set()))
        transitive_dependents = len(dependents)
        transitive_dependencies = len(dependencies)
        hub_score = (
            direct_dependents * 4
            + direct_dependencies * 2
            + transitive_dependents * 2
            + transitive_dependencies
        )
        impact[node_id] = {
            'id': node_id,
            'region': _region_id(node_id),
            'classification': node.get('classification', 'UNKNOWN'),
            'entrypoint': node_id in entry_ids,
            'dead': node_id in waste_ids,
            'direct_dependents': direct_dependents,
            'direct_dependencies': direct_dependencies,
            'transitive_dependents': transitive_dependents,
            'transitive_dependencies': transitive_dependencies,
            'blast_radius': direct_dependents + transitive_dependents,
            'fan_out': direct_dependencies + transitive_dependencies,
            'hub_score': hub_score,
            'depth': node.get('depth', -1),
        }
    return impact


def _reachable(start: str, adjacency: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    queue = deque(sorted(adjacency.get(start, set())))
    while queue:
        node_id = queue.popleft()
        if node_id == start or node_id in seen:
            continue
        seen.add(node_id)
        queue.extend(sorted(adjacency.get(node_id, set()) - seen))
    return seen


def _folder_regions(
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, str]],
    impact: dict[str, dict[str, Any]],
    entry_ids: set[str],
    waste_ids: set[str],
) -> list[dict[str, Any]]:
    regions: dict[str, dict[str, Any]] = {}
    for node_id, node in nodes.items():
        region_id = _region_id(node_id)
        region = regions.setdefault(region_id, {
            'id': region_id,
            'kind': 'root' if region_id == '.' else 'folder',
            'node_count': 0,
            'entrypoint_count': 0,
            'dead_count': 0,
            'internal_edge_count': 0,
            'incoming_edge_count': 0,
            'outgoing_edge_count': 0,
            'languages': set(),
            'hub_score': 0,
        })
        region['node_count'] += 1
        region['entrypoint_count'] += int(node_id in entry_ids)
        region['dead_count'] += int(node_id in waste_ids)
        region['hub_score'] += impact[node_id]['hub_score']
        language = node.get('language') or _language_from_path(node_id)
        if language:
            region['languages'].add(language)

    for edge in edges:
        source_region = _region_id(edge['source'])
        target_region = _region_id(edge['target'])
        if source_region == target_region:
            regions[source_region]['internal_edge_count'] += 1
        else:
            regions[source_region]['outgoing_edge_count'] += 1
            regions[target_region]['incoming_edge_count'] += 1

    result = []
    for region in regions.values():
        result.append({
            **region,
            'languages': sorted(region['languages']),
        })
    return sorted(result, key=lambda item: (-item['hub_score'], item['id']))


def _region_id(node_id: str) -> str:
    parts = [part for part in node_id.split('/') if part]
    return parts[0] if len(parts) > 1 else '.'


def _language_from_path(node_id: str) -> str:
    ext = Path(node_id).suffix.lower()
    return {
        '.py': 'python',
        '.pyi': 'python',
        '.js': 'javascript',
        '.mjs': 'javascript',
        '.cjs': 'javascript',
        '.jsx': 'javascript',
        '.ts': 'typescript',
        '.mts': 'typescript',
        '.cts': 'typescript',
        '.tsx': 'typescript',
        '.go': 'go',
        '.java': 'java',
        '.kt': 'kotlin',
        '.kts': 'kotlin',
        '.c': 'c',
        '.h': 'c',
        '.cc': 'cpp',
        '.cpp': 'cpp',
        '.cxx': 'cpp',
        '.hpp': 'cpp',
        '.html': 'html',
        '.htm': 'html',
        '.css': 'css',
    }.get(ext, '')


def _core_hubs(nodes: dict[str, dict[str, Any]], impact: dict[str, dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    ranked = sorted(
        (item for item in impact.values() if not item['dead']),
        key=lambda item: (
            -item['hub_score'],
            -item['blast_radius'],
            -item['fan_out'],
            item['id'],
        ),
    )
    hubs = []
    for item in ranked[:max(top_n, 0)]:
        if item['hub_score'] <= 0:
            continue
        hubs.append({
            'id': item['id'],
            'name': nodes[item['id']].get('name') or Path(item['id']).name,
            'region': item['region'],
            'classification': item['classification'],
            'hub_score': item['hub_score'],
            'blast_radius': item['blast_radius'],
            'fan_out': item['fan_out'],
            'direct_dependents': item['direct_dependents'],
            'direct_dependencies': item['direct_dependencies'],
        })
    return hubs


def _entrypoint_summary(
    nodes: dict[str, dict[str, Any]],
    outbound: dict[str, set[str]],
    impact: dict[str, dict[str, Any]],
    entry_ids: set[str],
) -> dict[str, Any]:
    entries = []
    for node_id in sorted(entry_ids):
        node = nodes[node_id]
        entries.append({
            'id': node_id,
            'name': node.get('name') or Path(node_id).name,
            'region': impact[node_id]['region'],
            'reaches': impact[node_id]['transitive_dependencies'],
            'direct_dependencies': len(outbound.get(node_id, set())),
            'reasons': node.get('entrypoint_reasons', []),
        })
    return {
        'count': len(entries),
        'items': entries,
    }


def _isolated_dead_areas(
    nodes: dict[str, dict[str, Any]],
    islands: list[list[str]],
    waste_ids: set[str],
    impact: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    island_items = []
    island_node_ids = set()
    for index, cluster in enumerate(islands):
        ids = sorted(node_id for node_id in cluster if node_id in nodes)
        island_node_ids.update(ids)
        island_items.append({
            'id': index,
            'nodes': ids,
            'node_count': len(ids),
            'regions': sorted({_region_id(node_id) for node_id in ids}),
            'hub_score': sum(impact[node_id]['hub_score'] for node_id in ids),
        })

    orphan_ids = sorted(node_id for node_id in waste_ids if node_id in nodes and node_id not in island_node_ids)
    return {
        'dead_count': len(waste_ids),
        'orphan_nodes': orphan_ids,
        'islands': island_items,
    }


def _walk_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [
            dirname for dirname in sorted(dirnames)
            if dirname not in SKIP_DIRS and not dirname.startswith('.')
        ]
        for filename in sorted(filenames):
            yield Path(dirpath) / filename


def _source_samples(root: Path, limit: int = 250) -> Iterable[tuple[str, str]]:
    count = 0
    for path in _walk_files(root):
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        relpath = _relpath(root, path)
        yield relpath, text[:20000]
        count += 1
        if count >= limit:
            break


def _package_dependencies(root: Path) -> set[str]:
    deps: set[str] = set()
    for manifest in _walk_named(root, 'package.json'):
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for section in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'):
            value = data.get(section)
            if isinstance(value, dict):
                deps.update(str(key).lower() for key in value)
    return deps


def _python_dependencies(root: Path) -> set[str]:
    deps: set[str] = set()
    for path in _walk_named(root, 'pyproject.toml'):
        try:
            import tomllib
            data = tomllib.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        project = data.get('project', {}) if isinstance(data, dict) else {}
        if isinstance(project, dict):
            for item in project.get('dependencies', []) or []:
                name = re.split(r'[<>=~!;\[]', str(item), 1)[0].strip().lower()
                if name:
                    deps.add(name)
        poetry = data.get('tool', {}).get('poetry', {}) if isinstance(data.get('tool'), dict) else {}
        if isinstance(poetry, dict):
            for section in ('dependencies', 'dev-dependencies', 'group'):
                value = poetry.get(section)
                if isinstance(value, dict):
                    deps.update(str(key).lower() for key in value if str(key).lower() != 'python')
    return deps


def _walk_named(root: Path, filename: str) -> Iterable[Path]:
    for path in _walk_files(root):
        if path.name == filename:
            yield path


def _package_json_commands(root: Path) -> list[dict[str, str]]:
    commands = []
    for manifest in _walk_named(root, 'package.json'):
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
        except Exception:
            continue
        scripts = data.get('scripts') if isinstance(data, dict) else None
        if not isinstance(scripts, dict):
            continue
        rel = _relpath(root, manifest)
        prefix = _package_runner(root, manifest.parent)
        for name, command in sorted(scripts.items()):
            if not isinstance(command, str):
                continue
            category = 'runtime' if _is_runtime_script_name(name) else 'task'
            commands.append({
                'source': rel,
                'name': name,
                'command': f'{prefix} run {name}',
                'raw': command,
                'category': category,
            })
    return commands


def _is_runtime_script_name(name: str) -> bool:
    normalized = name.strip().lower()
    base = normalized.split(':', 1)[0]
    return base in RUNTIME_SCRIPT_NAMES or any(part in normalized for part in ('start', 'dev', 'serve'))


def _package_runner(root: Path, directory: Path) -> str:
    prefix = 'npm'
    if (directory / 'pnpm-lock.yaml').exists():
        prefix = 'pnpm'
    elif (directory / 'yarn.lock').exists():
        prefix = 'yarn'
    elif (directory / 'bun.lockb').exists():
        prefix = 'bun'
    if directory.resolve() == root.resolve():
        return prefix
    return f'{prefix} --prefix {_quote(_relpath(root, directory))}'


def _pyproject_commands(root: Path) -> list[dict[str, str]]:
    commands = []
    for path in _walk_named(root, 'pyproject.toml'):
        try:
            import tomllib
            data = tomllib.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        rel = _relpath(root, path)
        project = data.get('project', {}) if isinstance(data, dict) else {}
        scripts = project.get('scripts', {}) if isinstance(project, dict) else {}
        if isinstance(scripts, dict):
            for name in sorted(scripts):
                commands.append({
                    'source': rel,
                    'name': name,
                    'command': name,
                    'raw': str(scripts[name]),
                    'category': 'runtime',
                })
        poetry = data.get('tool', {}).get('poetry', {}) if isinstance(data.get('tool'), dict) else {}
        poetry_scripts = poetry.get('scripts', {}) if isinstance(poetry, dict) else {}
        if isinstance(poetry_scripts, dict):
            for name in sorted(poetry_scripts):
                commands.append({
                    'source': rel,
                    'name': name,
                    'command': f'poetry run {name}',
                    'raw': str(poetry_scripts[name]),
                    'category': 'runtime',
                })
    pytest_source = _pytest_source(root)
    if pytest_source:
        commands.append({
            'source': pytest_source,
            'name': 'pytest',
            'command': 'pytest',
            'raw': 'detected Python tests',
            'category': 'runtime',
        })
    return commands


def _pytest_source(root: Path) -> str:
    for name in ('pytest.ini', 'tox.ini', 'noxfile.py'):
        if (root / name).exists():
            return name
    pyproject = root / 'pyproject.toml'
    if pyproject.exists():
        try:
            import tomllib
            data = tomllib.loads(pyproject.read_text(encoding='utf-8'))
        except Exception:
            data = {}
        tool = data.get('tool') if isinstance(data, dict) else {}
        if isinstance(tool, dict) and 'pytest' in tool:
            return 'pyproject.toml'
    tests_dir = root / 'tests'
    if tests_dir.exists():
        for path in tests_dir.rglob('test_*.py'):
            if path.is_file():
                return 'tests/'
        for path in tests_dir.rglob('*_test.py'):
            if path.is_file():
                return 'tests/'
    return ''


def _makefile_commands(root: Path) -> list[dict[str, str]]:
    commands = []
    for path in _walk_files(root):
        if path.name not in {'Makefile', 'makefile', 'GNUmakefile'}:
            continue
        try:
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            continue
        rel = _relpath(root, path)
        for line in lines:
            match = re.match(r'^([A-Za-z0-9_.-]+)\s*:(?![=])', line)
            if not match:
                continue
            name = match.group(1)
            if name.startswith('.'):
                continue
            directory = path.parent
            base = 'make' if directory.resolve() == root.resolve() else f'make -C {_quote(_relpath(root, directory))}'
            commands.append({
                'source': rel,
                'name': name,
                'command': f'{base} {name}',
                'raw': line.strip(),
                'category': 'runtime' if name in MAKE_RUNTIME_TARGETS else 'task',
            })
    return commands


def _dockerfile_commands(root: Path) -> list[dict[str, str]]:
    commands = []
    for path in _walk_files(root):
        low = path.name.lower()
        if low != 'dockerfile' and not low.startswith('dockerfile.'):
            continue
        try:
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            continue
        rel = _relpath(root, path)
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            match = re.match(r'^(CMD|ENTRYPOINT)\s+(.+)$', stripped, flags=re.IGNORECASE)
            if match:
                directive = match.group(1).upper()
                raw = _docker_command_text(match.group(2).strip())
                commands.append({
                    'source': rel,
                    'name': directive.lower(),
                    'command': f'docker build -t orbits-app . && docker run --rm orbits-app',
                    'raw': raw,
                    'category': 'runtime',
                })
    return commands


def _docker_command_text(value: str) -> str:
    if not value.startswith('['):
        return value
    try:
        parsed = json.loads(value)
    except Exception:
        return value
    if isinstance(parsed, list):
        return ' '.join(str(item) for item in parsed)
    return value


def _dedupe_commands(commands: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    deduped = []
    for command in commands:
        key = (command.get('source'), command.get('name'), command.get('command'))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(command)
    return sorted(deduped, key=lambda item: (item.get('category') != 'runtime', item.get('source', ''), item.get('name', '')))


def _quote(value: str) -> str:
    if re.search(r'\s', value):
        return shlex.quote(value)
    return value


def _relpath(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
