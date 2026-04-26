"""Best-effort project entrypoint detection for Orbits."""

from __future__ import annotations

import ast
import configparser
import json
import os
import re
import shlex
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from path_utils import relative_to_root


SOURCE_EXTENSIONS = (
    '.py', '.pyi',
    '.js', '.mjs', '.cjs', '.jsx',
    '.ts', '.mts', '.cts', '.tsx',
    '.html', '.htm',
    '.go',
)

JS_ENTRY_FIELDS = ('main', 'module', 'browser', 'source')
SCRIPT_EXTENSIONS = (
    '.py',
    '.js', '.mjs', '.cjs', '.jsx',
    '.ts', '.mts', '.cts', '.tsx',
    '.go',
)

COMMON_ROOT_NAMES = {
    'main.py', 'app.py', 'server.py', 'manage.py', 'wsgi.py', 'asgi.py', 'cli.py',
    'index.js', 'index.mjs', 'index.cjs', 'index.jsx',
    'index.ts', 'index.mts', 'index.cts', 'index.tsx',
    'index.html', 'index.htm', 'default.html', 'home.html',
    'main.js', 'main.mjs', 'main.cjs', 'main.jsx',
    'main.ts', 'main.mts', 'main.cts', 'main.tsx',
    'app.js', 'app.ts', 'server.js', 'server.ts',
    'main.go',
}

COMMON_ENTRY_DIRS = {
    '.', 'src', 'app', 'server', 'backend', 'frontend', 'client',
}

MAKE_ENTRY_TARGETS = {
    'run', 'start', 'serve', 'server', 'dev', 'develop', 'web', 'worker', 'cli',
}

TEST_HINTS = (
    'test_', '_test.', '.test.', 'spec_', '_spec.',
    '/test/', '/tests/', '/spec/', '/specs/',
)

GENERATED_HINTS = (
    '__pycache__/', '.mypy_cache/', '.pytest_cache/',
    'dist/', 'build/', 'out/', 'target/', 'generated/',
    '.eggs/', 'htmlcov/', 'site-packages/', '.tox/',
)


def detect_entrypoints(root: str | Path, node_ids: Iterable[str]) -> list[dict]:
    """Return detected source entrypoints as graph metadata-ready dicts."""

    root_path = Path(root).resolve()
    node_set = {str(node_id).replace('\\', '/').strip('/') for node_id in node_ids}
    reasons: dict[str, list[dict]] = defaultdict(list)

    def add(relpath: str | None, kind: str, source: str, detail: str) -> None:
        if not relpath or relpath not in node_set:
            return
        reason = {'kind': kind, 'source': source, 'detail': detail}
        if reason not in reasons[relpath]:
            reasons[relpath].append(reason)

    for relpath in sorted(node_set):
        reason = _common_entry_reason(root_path, relpath)
        if reason:
            add(relpath, 'common-name', relpath, reason)

    for manifest in _find_files(root_path, {'package.json'}):
        for relpath, kind, detail in _detect_package_json(root_path, node_set, manifest):
            add(relpath, kind, _rel_source(root_path, manifest), detail)

    pyproject = root_path / 'pyproject.toml'
    if pyproject.exists():
        for relpath, kind, detail in _detect_pyproject(root_path, node_set, pyproject):
            add(relpath, kind, _rel_source(root_path, pyproject), detail)

    setup_cfg = root_path / 'setup.cfg'
    if setup_cfg.exists():
        for relpath, kind, detail in _detect_setup_cfg(root_path, node_set, setup_cfg):
            add(relpath, kind, _rel_source(root_path, setup_cfg), detail)

    setup_py = root_path / 'setup.py'
    if setup_py.exists():
        for relpath, kind, detail in _detect_setup_py(root_path, node_set, setup_py):
            add(relpath, kind, _rel_source(root_path, setup_py), detail)

    for dockerfile in _find_dockerfiles(root_path):
        for relpath, kind, detail in _detect_dockerfile(root_path, node_set, dockerfile):
            add(relpath, kind, _rel_source(root_path, dockerfile), detail)

    for makefile in _find_makefiles(root_path):
        for relpath, kind, detail in _detect_makefile(root_path, node_set, makefile):
            add(relpath, kind, _rel_source(root_path, makefile), detail)

    return [
        {'id': relpath, 'reasons': sorted(items, key=lambda item: (item['kind'], item['source'], item['detail']))}
        for relpath, items in sorted(reasons.items())
    ]


def _find_files(root: Path, names: set[str]) -> list[Path]:
    matches: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [
            dirname for dirname in sorted(dirnames)
            if dirname not in {'node_modules', '.git', '.hg', '.svn', '__pycache__'}
            and not dirname.startswith('.')
        ]
        for filename in sorted(filenames):
            if filename in names:
                matches.append(Path(dirpath) / filename)
    return matches


def _find_dockerfiles(root: Path) -> list[Path]:
    matches: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [
            dirname for dirname in sorted(dirnames)
            if dirname not in {'node_modules', '.git', '.hg', '.svn', '__pycache__'}
            and not dirname.startswith('.')
        ]
        for filename in sorted(filenames):
            low = filename.lower()
            if low == 'dockerfile' or low.startswith('dockerfile.'):
                matches.append(Path(dirpath) / filename)
    return matches


def _find_makefiles(root: Path) -> list[Path]:
    names = {'Makefile', 'makefile', 'GNUmakefile'}
    return [path for path in _find_files(root, names)]


def _rel_source(root: Path, path: Path) -> str:
    return relative_to_root(path, root) or path.name


def _detect_package_json(root: Path, node_set: set[str], manifest: Path) -> list[tuple[str, str, str]]:
    try:
        data = json.loads(manifest.read_text(encoding='utf-8'))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []

    results: list[tuple[str, str, str]] = []

    for field in JS_ENTRY_FIELDS:
        value = data.get(field)
        if isinstance(value, str):
            _append_file_candidate(results, root, node_set, manifest.parent, value, f'package.json:{field}', f'{field}={value}')

    bin_value = data.get('bin')
    if isinstance(bin_value, str):
        _append_file_candidate(results, root, node_set, manifest.parent, bin_value, 'package.json:bin', f'bin={bin_value}')
    elif isinstance(bin_value, dict):
        for name, value in bin_value.items():
            if isinstance(value, str):
                _append_file_candidate(results, root, node_set, manifest.parent, value, 'package.json:bin', f'bin.{name}={value}')

    for export_key, value in _iter_export_targets(data.get('exports')):
        _append_file_candidate(results, root, node_set, manifest.parent, value, 'package.json:exports', f'exports.{export_key}={value}')

    scripts = data.get('scripts')
    if isinstance(scripts, dict):
        for name, command in scripts.items():
            if isinstance(command, str):
                for relpath in _resolve_command_hints(root, node_set, manifest.parent, command):
                    results.append((relpath, 'package.json:scripts', f'scripts.{name}={command}'))

    return results


def _iter_export_targets(value, prefix: str = 'exports') -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield prefix, value
    elif isinstance(value, dict):
        for key, child in value.items():
            if key in {'types'}:
                continue
            if isinstance(child, str):
                yield str(key), child
            elif isinstance(child, dict):
                for condition in ('source', 'import', 'require', 'default', 'browser', 'node'):
                    target = child.get(condition)
                    if isinstance(target, str):
                        yield f'{key}.{condition}', target
                        break


def _detect_pyproject(root: Path, node_set: set[str], path: Path) -> list[tuple[str, str, str]]:
    try:
        import tomllib
    except ModuleNotFoundError:
        return []
    try:
        data = tomllib.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []

    results: list[tuple[str, str, str]] = []
    project = data.get('project', {})
    if isinstance(project, dict):
        for table_name in ('scripts', 'gui-scripts'):
            table = project.get(table_name, {})
            if isinstance(table, dict):
                _append_python_script_table(results, root, node_set, table, f'pyproject:{table_name}')

    poetry = data.get('tool', {}).get('poetry', {}) if isinstance(data.get('tool'), dict) else {}
    poetry_scripts = poetry.get('scripts', {}) if isinstance(poetry, dict) else {}
    if isinstance(poetry_scripts, dict):
        _append_python_script_table(results, root, node_set, poetry_scripts, 'pyproject:poetry.scripts')

    return results


def _append_python_script_table(results: list[tuple[str, str, str]], root: Path, node_set: set[str], table: dict, kind: str) -> None:
    for name, value in table.items():
        reference = _python_script_reference(value)
        if not reference:
            continue
        relpath = _resolve_python_reference(root, node_set, reference)
        if relpath:
            results.append((relpath, kind, f'{name}={reference}'))


def _python_script_reference(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ('reference', 'callable', 'module'):
            item = value.get(key)
            if isinstance(item, str):
                return item
    return ''


def _detect_setup_cfg(root: Path, node_set: set[str], path: Path) -> list[tuple[str, str, str]]:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding='utf-8')
    except Exception:
        return []

    results: list[tuple[str, str, str]] = []
    sections = [section for section in ('options.entry_points', 'entry_points') if parser.has_section(section)]
    for section in sections:
        for key in ('console_scripts', 'gui_scripts'):
            if not parser.has_option(section, key):
                continue
            for name, reference in _parse_entry_point_lines(parser.get(section, key)):
                relpath = _resolve_python_reference(root, node_set, reference)
                if relpath:
                    results.append((relpath, f'setup.cfg:{key}', f'{name}={reference}'))
    return results


def _detect_setup_py(root: Path, node_set: set[str], path: Path) -> list[tuple[str, str, str]]:
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return []

    results: list[tuple[str, str, str]] = []
    for block in re.findall(r'(?:console_scripts|gui_scripts)[^=\n]*=\s*\[(.*?)\]', text, flags=re.DOTALL):
        for raw in re.findall(r'["\']([^"\']+)["\']', block):
            for name, reference in _parse_entry_point_lines(raw):
                relpath = _resolve_python_reference(root, node_set, reference)
                if relpath:
                    results.append((relpath, 'setup.py:entry_points', f'{name}={reference}'))

    if not results:
        results.extend(_detect_setup_py_ast(root, node_set, text))
    return results


def _detect_setup_py_ast(root: Path, node_set: set[str], text: str) -> list[tuple[str, str, str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    results: list[tuple[str, str, str]] = []

    def literal(node):
        try:
            return ast.literal_eval(node)
        except Exception:
            return None

    for call in [node for node in ast.walk(tree) if isinstance(node, ast.Call)]:
        for keyword in call.keywords:
            if keyword.arg != 'entry_points':
                continue
            value = literal(keyword.value)
            if isinstance(value, dict):
                for key in ('console_scripts', 'gui_scripts'):
                    for item in value.get(key, []) or []:
                        if not isinstance(item, str):
                            continue
                        for name, reference in _parse_entry_point_lines(item):
                            relpath = _resolve_python_reference(root, node_set, reference)
                            if relpath:
                                results.append((relpath, f'setup.py:{key}', f'{name}={reference}'))
    return results


def _parse_entry_point_lines(value: str) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for line in value.splitlines():
        line = line.strip().strip(',')
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, reference = line.split('=', 1)
        reference = reference.strip().strip('"\'')
        if reference:
            parsed.append((name.strip(), reference))
    return parsed


def _detect_dockerfile(root: Path, node_set: set[str], path: Path) -> list[tuple[str, str, str]]:
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except Exception:
        return []

    results: list[tuple[str, str, str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        match = re.match(r'^(CMD|ENTRYPOINT)\s+(.+)$', stripped, flags=re.IGNORECASE)
        if not match:
            continue
        directive = match.group(1).upper()
        payload = match.group(2).strip()
        command = _docker_command_to_text(payload)
        for relpath in _resolve_command_hints(root, node_set, root, command):
            results.append((relpath, f'Dockerfile:{directive}', f'{directive} {command}'))
        if path.parent != root:
            for relpath in _resolve_command_hints(root, node_set, path.parent, command):
                results.append((relpath, f'Dockerfile:{directive}', f'{directive} {command}'))
    return results


def _docker_command_to_text(payload: str) -> str:
    if payload.startswith('['):
        try:
            value = json.loads(payload)
        except Exception:
            return payload
        if isinstance(value, list):
            return ' '.join(str(item) for item in value)
    return payload


def _detect_makefile(root: Path, node_set: set[str], path: Path) -> list[tuple[str, str, str]]:
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except Exception:
        return []

    results: list[tuple[str, str, str]] = []
    current_target = ''
    commands: list[str] = []

    def flush() -> None:
        if current_target not in MAKE_ENTRY_TARGETS:
            return
        for command in commands:
            for relpath in _resolve_command_hints(root, node_set, path.parent, command):
                results.append((relpath, 'Makefile:target', f'{current_target}: {command.strip()}'))

    for line in lines:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.match(r'^([A-Za-z0-9_.-]+)\s*:(?![=])', line)
        if match and not line.startswith((' ', '\t')):
            flush()
            current_target = match.group(1)
            commands = []
            continue
        if current_target and line.startswith(('\t', ' ')):
            commands.append(line.strip())
    flush()
    return results


def _append_file_candidate(
    results: list[tuple[str, str, str]],
    root: Path,
    node_set: set[str],
    base_dir: Path,
    raw: str,
    kind: str,
    detail: str,
) -> None:
    relpath = _resolve_file_reference(root, node_set, base_dir, raw)
    if relpath:
        results.append((relpath, kind, detail))


def _resolve_command_hints(root: Path, node_set: set[str], base_dir: Path, command: str) -> list[str]:
    tokens = _command_tokens(command)
    resolved: list[str] = []
    for token in tokens:
        clean = _clean_token(token)
        if not clean or clean.startswith('-') or clean in {'python', 'python3', 'node', 'npm', 'yarn', 'pnpm', 'bun', 'make'}:
            continue
        if clean in {'-m', 'module'}:
            continue
        relpath = None
        if _looks_like_file_hint(clean):
            relpath = _resolve_file_reference(root, node_set, base_dir, clean)
        elif _looks_like_python_module_hint(clean):
            relpath = _resolve_python_reference(root, node_set, clean)
        if relpath and relpath not in resolved:
            resolved.append(relpath)
    return resolved


def _command_tokens(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = re.split(r'\s+', command)
    extras = re.findall(r'[\w./@-]+\.(?:py|js|mjs|cjs|jsx|ts|mts|cts|tsx|go)\b', command)
    return [*tokens, *extras]


def _clean_token(token: str) -> str:
    token = token.strip().strip('"\'`')
    token = token.split('?', 1)[0].split('#', 1)[0]
    token = token.rstrip(';,')
    if token.startswith('./'):
        return token
    return token.strip()


def _looks_like_file_hint(token: str) -> bool:
    if token.startswith(('./', '../')):
        return True
    suffix = Path(token).suffix.lower()
    return suffix in SCRIPT_EXTENSIONS or '/' in token or '\\' in token


def _looks_like_python_module_hint(token: str) -> bool:
    return bool(re.match(r'^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?::[A-Za-z_]\w*)?$', token))


def _resolve_file_reference(root: Path, node_set: set[str], base_dir: Path, raw: str) -> str | None:
    raw = raw.strip().strip('"\'')
    if not raw or raw.startswith(('#', 'http://', 'https://')):
        return None
    raw = raw.removeprefix('file:')
    raw = raw.split('?', 1)[0].split('#', 1)[0]
    if raw.startswith('./'):
        raw = raw[2:]
    if not raw:
        return None

    candidates: list[Path] = []
    path = Path(raw)
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.append((base_dir / raw).resolve())
        if base_dir != root:
            candidates.append((root / raw).resolve())

    for candidate in candidates:
        resolved = _probe_node_path(root, node_set, candidate)
        if resolved:
            return resolved
    return None


def _resolve_python_reference(root: Path, node_set: set[str], reference: str) -> str | None:
    module = reference.strip().split(':', 1)[0].strip()
    if not module:
        return None
    if module.endswith('.py') or '/' in module or '\\' in module:
        return _resolve_file_reference(root, node_set, root, module)
    module_path = module.replace('.', '/')
    for prefix in ('', 'src'):
        base = root / prefix / module_path if prefix else root / module_path
        for candidate in (base.with_suffix('.py'), base / '__init__.py'):
            resolved = _probe_node_path(root, node_set, candidate)
            if resolved:
                return resolved
    return None


def _probe_node_path(root: Path, node_set: set[str], candidate: Path) -> str | None:
    rel = relative_to_root(candidate, root)
    if rel and rel in node_set:
        return rel

    suffix = candidate.suffix.lower()
    base = candidate.with_suffix('') if suffix else candidate
    if suffix in SOURCE_EXTENSIONS or not suffix:
        for ext in SOURCE_EXTENSIONS:
            rel = relative_to_root(base.with_suffix(ext), root)
            if rel and rel in node_set:
                return rel
        for ext in SOURCE_EXTENSIONS:
            rel = relative_to_root(base / f'index{ext}', root)
            if rel and rel in node_set:
                return rel
    return None


def _common_entry_reason(root: Path, relpath: str) -> str:
    low = relpath.lower()
    normalized = low.replace('\\', '/')
    if any(hint in normalized for hint in TEST_HINTS):
        return ''
    if any(hint in normalized for hint in GENERATED_HINTS):
        return ''

    path = Path(relpath)
    name = path.name.lower()
    parent = str(path.parent).replace('\\', '/')
    parts = relpath.split('/')

    if name == '__main__.py':
        return 'Python __main__.py module entrypoint'

    if name == 'main.go' and _go_file_has_main(root / relpath):
        if parent == '.' or parent.startswith('cmd/') or parent in COMMON_ENTRY_DIRS:
            return 'Go package main'

    if name not in COMMON_ROOT_NAMES:
        return ''

    if parent in COMMON_ENTRY_DIRS:
        return f'common entry filename {name}'
    if len(parts) >= 3 and parts[-2] in {'src', 'app', 'server'}:
        return f'common entry filename {name} under {parts[-2]}/'
    if len(parts) >= 4 and parts[-3] == 'cmd' and name == 'main.go':
        return 'Go cmd/* main.go'
    return ''


def _go_file_has_main(path: Path) -> bool:
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return False
    return bool(re.search(r'^\s*package\s+main\b', text, flags=re.MULTILINE))
