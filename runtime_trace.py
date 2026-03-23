from __future__ import annotations

import argparse
import base64
import builtins
import importlib
import importlib.util
import inspect
import io
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from path_utils import relative_to_root


@dataclass
class PythonRuntimeTraceConfig:
    mode: str
    target: str
    args: list[str] = field(default_factory=list)
    output_path: Path | None = None
    timeout_s: int = 60


@dataclass
class NodeRuntimeTraceConfig:
    mode: str
    target: str
    args: list[str] = field(default_factory=list)
    output_path: Path | None = None
    timeout_s: int = 60
    node_bin: str = 'node'


@dataclass
class CppRuntimeTraceConfig:
    target: str
    args: list[str] = field(default_factory=list)
    output_path: Path | None = None
    timeout_s: int = 60


class RuntimeTraceCollector:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.import_edges: dict[tuple[str, str], dict[str, Any]] = {}
        self.file_accesses: dict[tuple[str, str], dict[str, Any]] = {}
        self.import_calls = 0
        self.external_import_calls = 0
        self._lock = threading.Lock()
        self._tls = threading.local()
        self._original_import = builtins.__import__
        self._original_import_module = importlib.import_module
        self._original_open = builtins.open
        self._original_io_open = io.open
        self._self_file = Path(__file__).resolve()

    def install(self) -> None:
        builtins.__import__ = self._wrap_import
        importlib.import_module = self._wrap_import_module
        builtins.open = self._wrap_open
        io.open = self._wrap_open

    def restore(self) -> None:
        builtins.__import__ = self._original_import
        importlib.import_module = self._original_import_module
        builtins.open = self._original_open
        io.open = self._original_io_open

    def _enter(self) -> bool:
        if getattr(self._tls, 'active', False):
            return False
        self._tls.active = True
        return True

    def _leave(self) -> None:
        self._tls.active = False

    def _normalize_local_path(self, value: Any) -> str | None:
        if value is None:
            return None
        try:
            path = Path(value)
        except TypeError:
            return None
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
        if path.suffix in {'.pyc', '.pyo'} and path.with_suffix('.py').exists():
            path = path.with_suffix('.py')
        return relative_to_root(path, self.root)

    def _caller_context(self, globals_dict: dict[str, Any] | None = None) -> tuple[str | None, int | None]:
        preferred_file = None
        if globals_dict:
            preferred_file = globals_dict.get('__file__')
        caller_rel = self._normalize_local_path(preferred_file) if preferred_file else None
        caller_abs = str(Path(preferred_file).resolve()) if preferred_file else None
        frame = inspect.currentframe()
        line = None
        try:
            frame = frame.f_back
            while frame:
                filename = frame.f_code.co_filename
                abs_name = str(Path(filename).resolve()) if filename else ''
                if abs_name == str(self._self_file):
                    frame = frame.f_back
                    continue
                rel = self._normalize_local_path(abs_name)
                if caller_abs and abs_name == caller_abs and rel:
                    return rel, frame.f_lineno
                if rel and line is None:
                    caller_rel = caller_rel or rel
                    line = frame.f_lineno
                    break
                frame = frame.f_back
        finally:
            del frame
        return caller_rel, line

    def _module_to_rel(self, module: ModuleType | None) -> str | None:
        if module is None:
            return None
        filename = getattr(module, '__file__', None)
        return self._normalize_local_path(filename)

    def _collect_import_targets(self, result: Any, name: str, fromlist: tuple[Any, ...] | list[Any] | None, level: int, package: str | None) -> tuple[set[str], list[str]]:
        resolved_name = name or ''
        if level:
            ref = '.' * level + (name or '')
            try:
                resolved_name = importlib.util.resolve_name(ref, package or '')
            except Exception:
                resolved_name = name or ''

        modules: list[ModuleType] = []
        seen: set[int] = set()
        names: set[str] = set()

        def add_module(module: Any):
            if not isinstance(module, ModuleType):
                return
            ident = id(module)
            if ident in seen:
                return
            seen.add(ident)
            modules.append(module)
            mod_name = getattr(module, '__name__', '')
            if mod_name:
                names.add(mod_name)

        add_module(result)
        if resolved_name:
            add_module(sys.modules.get(resolved_name))

        if fromlist:
            base_module = sys.modules.get(resolved_name) if resolved_name else result if isinstance(result, ModuleType) else None
            submodule_found = False
            for item in fromlist:
                if item in ('*', None):
                    continue
                attr_name = str(item)
                full_name = f'{resolved_name}.{attr_name}' if resolved_name else attr_name
                module = sys.modules.get(full_name)
                if module is not None:
                    add_module(module)
                    submodule_found = True
                    continue
                if isinstance(base_module, ModuleType):
                    add_module(getattr(base_module, attr_name, None))
            if '*' in fromlist and isinstance(base_module, ModuleType):
                add_module(base_module)
            elif not submodule_found and isinstance(base_module, ModuleType):
                add_module(base_module)
        elif resolved_name and '.' in resolved_name:
            add_module(sys.modules.get(resolved_name))

        rel_targets = {rel for rel in (self._module_to_rel(module) for module in modules) if rel}
        return rel_targets, sorted(names)

    def _record_import(self, source: str, target: str, line: int | None, modules: list[str]) -> None:
        key = (source, target)
        with self._lock:
            edge = self.import_edges.setdefault(key, {
                'source': source,
                'target': target,
                'type': 'runtime_import',
                'line': line or -1,
                'language': 'python',
                'runtime': True,
                'origins': ['runtime'],
                'dynamic': True,
                'runtime_hits': 0,
                'runtime_modules': set(),
                'runtime_lines': set(),
            })
            edge['runtime_hits'] += 1
            if line:
                edge['runtime_lines'].add(int(line))
                if edge.get('line', -1) <= 0 or line < edge['line']:
                    edge['line'] = int(line)
            edge['runtime_modules'].update(modules)

    def _record_open(self, source: str, target: str, line: int | None, mode: str) -> None:
        key = (source, target)
        with self._lock:
            item = self.file_accesses.setdefault(key, {
                'source': source,
                'path': target,
                'count': 0,
                'modes': set(),
                'lines': set(),
                'line': line or -1,
            })
            item['count'] += 1
            item['modes'].add(mode or 'r')
            if line:
                item['lines'].add(int(line))
                if item.get('line', -1) <= 0 or line < item['line']:
                    item['line'] = int(line)

    def _wrap_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        entered = self._enter()
        if not entered:
            return self._original_import(name, globals, locals, fromlist, level)
        try:
            caller_rel, line = self._caller_context(globals)
            package = globals.get('__package__') if globals else None
            result = self._original_import(name, globals, locals, fromlist, level)
            self.import_calls += 1
            if not caller_rel:
                return result
            targets, modules = self._collect_import_targets(result, str(name or ''), tuple(fromlist or ()), int(level or 0), package)
            local_targets = {target for target in targets if target != caller_rel}
            if not local_targets:
                self.external_import_calls += 1
                return result
            for target in sorted(local_targets):
                self._record_import(caller_rel, target, line, modules)
            return result
        finally:
            self._leave()

    def _wrap_import_module(self, name: str, package: str | None = None):
        entered = self._enter()
        if not entered:
            return self._original_import_module(name, package)
        try:
            caller_rel, line = self._caller_context()
            result = self._original_import_module(name, package)
            self.import_calls += 1
            if not caller_rel:
                return result
            level = len(name) - len(name.lstrip('.')) if isinstance(name, str) else 0
            bare_name = name.lstrip('.') if isinstance(name, str) else str(name)
            targets, modules = self._collect_import_targets(result, bare_name, (), level, package)
            local_targets = {target for target in targets if target != caller_rel}
            if not local_targets:
                self.external_import_calls += 1
                return result
            for target in sorted(local_targets):
                self._record_import(caller_rel, target, line, modules)
            return result
        finally:
            self._leave()

    def _wrap_open(self, file, mode='r', *args, **kwargs):
        entered = self._enter()
        if not entered:
            return self._original_open(file, mode, *args, **kwargs)
        try:
            caller_rel, line = self._caller_context()
            result = self._original_open(file, mode, *args, **kwargs)
            target_rel = self._normalize_local_path(file)
            if caller_rel and target_rel:
                self._record_open(caller_rel, target_rel, line, str(mode or 'r'))
            return result
        finally:
            self._leave()

    def serialize(self) -> dict[str, Any]:
        with self._lock:
            edges = []
            for edge in sorted(self.import_edges.values(), key=lambda item: (item['source'], item['target'])):
                lines = sorted(edge.pop('runtime_lines'))
                modules = sorted(edge.pop('runtime_modules'))
                edges.append({
                    **edge,
                    'runtime_lines': lines,
                    'runtime_modules': modules,
                })
            file_accesses = []
            for item in sorted(self.file_accesses.values(), key=lambda entry: (entry['source'], entry['path'])):
                lines = sorted(item.pop('lines'))
                modes = sorted(item.pop('modes'))
                file_accesses.append({
                    **item,
                    'lines': lines,
                    'modes': modes,
                })
        return {
            'edges': edges,
            'file_accesses': file_accesses,
            'summary': {
                'import_calls': self.import_calls,
                'external_import_calls': self.external_import_calls,
                'local_edge_hits': sum(edge['runtime_hits'] for edge in edges),
                'local_edge_count': len(edges),
                'local_file_access_hits': sum(item['count'] for item in file_accesses),
                'local_file_access_count': len(file_accesses),
            },
        }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def _run_target(root: Path, mode: str, target: str, args: list[str]) -> int:
    old_cwd = Path.cwd()
    old_argv = sys.argv[:]
    root_str = str(root)
    inserted_root = False
    try:
        os.chdir(root_str)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
            inserted_root = True
        if mode == 'script':
            script_path = (root / target).resolve()
            if not script_path.exists():
                raise FileNotFoundError(f'Python trace entry script not found: {script_path}')
            sys.argv = [str(script_path)] + list(args)
            script_dir = str(script_path.parent)
            inserted_script_dir = False
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
                inserted_script_dir = True
            try:
                runpy.run_path(str(script_path), run_name='__main__')
            finally:
                if inserted_script_dir:
                    try:
                        sys.path.remove(script_dir)
                    except ValueError:
                        pass
        elif mode == 'module':
            sys.argv = [target] + list(args)
            runpy.run_module(target, run_name='__main__', alter_sys=True)
        else:
            raise ValueError(f'Unsupported trace mode: {mode}')
        return 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        return code
    finally:
        sys.argv = old_argv
        if inserted_root:
            try:
                sys.path.remove(root_str)
            except ValueError:
                pass
        os.chdir(str(old_cwd))


def _build_trace_payload(root: Path, mode: str, target: str, args: list[str], timeout_s: int, collector_payload: dict[str, Any], elapsed_s: float, exit_code: int, timed_out: bool, error: str | None = None) -> dict[str, Any]:
    return {
        'version': 1,
        'language': 'python',
        'root': str(root),
        'entry': {
            'mode': mode,
            'target': target,
            'args': list(args),
        },
        'timeout_s': timeout_s,
        'timed_out': timed_out,
        'elapsed_s': round(elapsed_s, 3),
        'exit_code': exit_code,
        'error': error,
        **collector_payload,
    }


def _child_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--child-python-trace', action='store_true')
    parser.add_argument('--root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', required=True, choices=['script', 'module'])
    parser.add_argument('--target', required=True)
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--arg', dest='args', action='append', default=[])
    ns = parser.parse_args(argv)

    root = Path(ns.root).resolve()
    output_path = Path(ns.output).resolve()
    collector = RuntimeTraceCollector(root)
    start = time.time()
    state = {
        'timed_out': False,
        'written': False,
    }

    def flush_and_exit() -> None:
        if state['written']:
            return
        collector_payload = collector.serialize()
        payload = _build_trace_payload(
            root,
            ns.mode,
            ns.target,
            ns.args,
            ns.timeout,
            collector_payload,
            time.time() - start,
            124,
            timed_out=True,
            error=f'Runtime trace timed out after {ns.timeout}s',
        )
        _write_json(output_path, payload)
        state['written'] = True

    timer = None
    if ns.timeout and ns.timeout > 0:
        def on_timeout() -> None:
            state['timed_out'] = True
            try:
                flush_and_exit()
            finally:
                os._exit(124)
        timer = threading.Timer(ns.timeout, on_timeout)
        timer.daemon = True
        timer.start()

    collector.install()
    exit_code = 0
    error = None
    try:
        exit_code = _run_target(root, ns.mode, ns.target, ns.args)
    except BaseException as exc:  # noqa: BLE001
        exit_code = 1
        error = f'{type(exc).__name__}: {exc}'
    finally:
        collector.restore()
        if timer is not None:
            timer.cancel()

    if not state['written']:
        collector_payload = collector.serialize()
        payload = _build_trace_payload(
            root,
            ns.mode,
            ns.target,
            ns.args,
            ns.timeout,
            collector_payload,
            time.time() - start,
            exit_code,
            timed_out=state['timed_out'],
            error=error,
        )
        _write_json(output_path, payload)
    return exit_code


def run_python_runtime_trace(root: Path, config: PythonRuntimeTraceConfig, verbose: bool = True) -> dict[str, Any]:
    output_path = (config.output_path or (root / 'runtime_trace.json')).resolve()
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        '--child-python-trace',
        '--root', str(root.resolve()),
        '--output', str(output_path),
        '--mode', config.mode,
        '--target', config.target,
        '--timeout', str(max(0, int(config.timeout_s))),
    ]
    for arg in config.args:
        command.extend(['--arg', arg])
    proc = subprocess.run(command, cwd=str(Path(__file__).resolve().parent), capture_output=True, text=True)
    if not output_path.exists():
        stderr = (proc.stderr or '').strip()
        raise RuntimeError(stderr or 'Runtime tracing failed before writing an artifact')
    payload = json.loads(output_path.read_text(encoding='utf-8'))
    payload.setdefault('subprocess_exit_code', proc.returncode)
    if verbose:
        summary = payload.get('summary', {})
        print(
            f"  Runtime:   {summary.get('local_edge_count', 0)} dynamic edges, "
            f"{summary.get('local_file_access_count', 0)} file accesses, "
            f"exit {payload.get('exit_code', proc.returncode)}",
            file=sys.stderr,
        )
        if payload.get('timed_out'):
            print(f"  Runtime:   timed out after {payload.get('timeout_s', config.timeout_s)}s; partial trace kept", file=sys.stderr)
        elif payload.get('error'):
            print(f"  Runtime:   trace error recorded: {payload['error']}", file=sys.stderr)
    return payload


def _load_runtime_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def _patch_timed_out_payload(payload: dict[str, Any], output_path: Path, timeout_s: int, error: str) -> dict[str, Any]:
    summary = dict(payload.get('summary', {}))
    payload = {
        **payload,
        'timed_out': True,
        'timeout_s': timeout_s,
        'exit_code': 124,
        'error': error,
        'partial': False,
        'summary': summary,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    return payload


def _native_project_rel(raw_id: str, root: Path | None) -> str | None:
    normalized = str(raw_id or '').replace('\\', '/').lstrip('./')
    if not normalized:
        return None
    if root is not None:
        rel = relative_to_root(normalized, root)
        if rel:
            return rel
    candidate = PurePosixPath(normalized)
    if candidate.parts and candidate.parts[0] != '..':
        return str(candidate)
    return None


def _build_cpp_runtime_payload(
    root: Path,
    target: str,
    args: list[str],
    timeout_s: int,
    engine: str,
    edges: list[dict[str, Any]],
    elapsed_s: float,
    exit_code: int,
    timed_out: bool,
    error: str | None = None,
) -> dict[str, Any]:
    symbol_binding_count = sum(
        int(edge.get('runtime_symbol_hits', 0) or len(edge.get('runtime_symbols', []) or []))
        for edge in edges
    )
    return {
        'version': 1,
        'language': 'cpp',
        'engine': engine,
        'root': str(root),
        'entry': {
            'mode': 'binary',
            'target': target,
            'args': list(args),
        },
        'timeout_s': timeout_s,
        'timed_out': timed_out,
        'elapsed_s': round(elapsed_s, 3),
        'exit_code': exit_code,
        'error': error,
        'edges': edges,
        'file_accesses': [],
        'summary': {
            'import_calls': len(edges),
            'external_import_calls': 0,
            'local_edge_hits': sum(int(edge.get('runtime_hits', 0) or 0) for edge in edges),
            'local_edge_count': len(edges),
            'local_file_access_hits': 0,
            'local_file_access_count': 0,
            'symbol_binding_count': symbol_binding_count,
        },
    }


def _record_cpp_runtime_edge(
    edge_map: dict[tuple[str, str], dict[str, Any]],
    source: str,
    target: str,
    edge_type: str,
    symbol: str | None = None,
) -> None:
    key = (source, target)
    edge = edge_map.setdefault(key, {
        'source': source,
        'target': target,
        'type': edge_type,
        'line': -1,
        'language': 'cpp',
        'runtime': True,
        'origins': ['runtime'],
        'dynamic': True,
        'runtime_hits': 0,
        'runtime_modules': [target],
        'runtime_lines': [],
        'runtime_symbols': set(),
        'runtime_symbol_hits': 0,
    })
    edge['runtime_hits'] += 1
    if symbol:
        edge['type'] = 'runtime_bind'
        edge['runtime_symbols'].add(symbol)
        edge['runtime_symbol_hits'] += 1


def _finalize_cpp_runtime_edges(edge_map: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **edge,
            'runtime_symbols': sorted(edge['runtime_symbols']),
        }
        for _, edge in sorted(edge_map.items())
    ]


def _is_local_cpp_artifact(relpath: str | None, entry_rel: str) -> bool:
    if not relpath:
        return False
    if relpath == entry_rel:
        return True
    suffix = Path(relpath).suffix.lower()
    return suffix in _CPP_RUNTIME_EXTENSIONS


def _parse_linux_loader_edges(stderr: str, root: Path, entry_rel: str) -> list[dict[str, Any]]:
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    for line in (stderr or '').splitlines():
        match = re.search(r'calling init:\s*(.+)$', line)
        if match:
            rel = relative_to_root(match.group(1).strip(), root)
            if rel and rel != entry_rel and _is_local_cpp_artifact(rel, entry_rel):
                _record_cpp_runtime_edge(edges, entry_rel, rel, 'runtime_load')
            continue

        binding = re.search(
            r"binding file\s+(.+?)\s+\[\d+\]\s+to\s+(.+?)\s+\[\d+\]:\s+(?:normal|weak|protected)?\s*symbol\s+[`'\"]?([^`'\"]+)[`'\"]?",
            line,
        )
        if not binding:
            continue
        source_rel = relative_to_root(binding.group(1).strip(), root)
        target_rel = relative_to_root(binding.group(2).strip(), root)
        symbol = binding.group(3).strip()
        if not _is_local_cpp_artifact(source_rel, entry_rel) or not _is_local_cpp_artifact(target_rel, entry_rel):
            continue
        if not source_rel or not target_rel or source_rel == target_rel:
            continue
        _record_cpp_runtime_edge(edges, source_rel, target_rel, 'runtime_bind', symbol=symbol)
    return _finalize_cpp_runtime_edges(edges)


def _parse_macos_loader_edges(stderr: str, root: Path, entry_rel: str) -> list[dict[str, Any]]:
    hits: dict[str, int] = {}
    for line in (stderr or '').splitlines():
        match = re.search(r'loaded:\s*(.+)$', line)
        if not match:
            continue
        rel = relative_to_root(match.group(1).strip(), root)
        if not rel or rel == entry_rel or Path(rel).suffix.lower() not in _CPP_RUNTIME_EXTENSIONS:
            continue
        hits[rel] = hits.get(rel, 0) + 1
    return [
        {
            'source': entry_rel,
            'target': target,
            'type': 'runtime_load',
            'line': -1,
            'language': 'cpp',
            'runtime': True,
            'origins': ['runtime'],
            'dynamic': True,
            'runtime_hits': count,
            'runtime_modules': [target],
            'runtime_lines': [],
        }
        for target, count in sorted(hits.items())
    ]


def run_cpp_runtime_trace(root: Path, config: CppRuntimeTraceConfig, verbose: bool = True) -> dict[str, Any]:
    if os.name == 'nt':
        raise RuntimeError('C/C++ runtime tracing is not supported on Windows yet; use Linux or macOS loader tracing')
    executable = Path(config.target)
    if not executable.is_absolute():
        executable = (root / executable).resolve()
    else:
        executable = executable.resolve()
    if not executable.exists():
        raise FileNotFoundError(f'C/C++ trace entry binary not found: {executable}')
    entry_rel = relative_to_root(executable, root.resolve())
    if not entry_rel:
        raise RuntimeError('C/C++ trace entry must live under the analyzed project root')

    output_path = (config.output_path or (root / 'runtime_trace.json')).resolve()
    env = os.environ.copy()
    engine = ''
    if sys.platform.startswith('linux'):
        env['LD_DEBUG'] = 'libs,bindings'
        env.setdefault('LD_BIND_NOW', '1')
        env.setdefault('LD_BIND_NOT', '1')
        engine = 'ld_debug_bindings'
    elif sys.platform == 'darwin':
        env['DYLD_PRINT_LIBRARIES'] = '1'
        engine = 'dyld'
    else:
        raise RuntimeError(f'C/C++ runtime tracing is not supported on platform: {sys.platform}')

    start = time.time()
    command = [str(executable), *list(config.args or [])]
    try:
        proc = subprocess.run(
            command,
            cwd=str(root.resolve()),
            capture_output=True,
            text=True,
            timeout=max(5, int(config.timeout_s) + 5),
            env=env,
        )
        stderr = proc.stderr or ''
        exit_code = proc.returncode
        timed_out = False
        error = stderr.strip().splitlines()[-1] if proc.returncode and stderr.strip() else None
    except subprocess.TimeoutExpired as exc:
        stderr = ((exc.stderr.decode('utf-8', errors='replace') if isinstance(exc.stderr, bytes) else exc.stderr) or '')
        exit_code = 124
        timed_out = True
        error = f'Runtime trace timed out after {config.timeout_s}s'

    edges = _parse_linux_loader_edges(stderr, root.resolve(), entry_rel) if engine == 'ld_debug' else _parse_macos_loader_edges(stderr, root.resolve(), entry_rel)
    payload = _build_cpp_runtime_payload(
        root.resolve(),
        config.target,
        list(config.args or []),
        int(config.timeout_s),
        engine,
        edges,
        time.time() - start,
        exit_code,
        timed_out=timed_out,
        error=error,
    )
    _write_json(output_path, payload)
    payload.setdefault('subprocess_exit_code', exit_code)
    if verbose:
        print(
            f"  Runtime:   {payload['summary'].get('local_edge_count', 0)} dynamic loads, "
            f"0 file accesses, exit {payload.get('exit_code', exit_code)}",
            file=sys.stderr,
        )
        if payload.get('timed_out'):
            print(f"  Runtime:   timed out after {config.timeout_s}s; partial trace kept", file=sys.stderr)
        elif payload.get('error'):
            print(f"  Runtime:   trace error recorded: {payload['error']}", file=sys.stderr)
    return payload


def run_node_runtime_trace(root: Path, config: NodeRuntimeTraceConfig, verbose: bool = True) -> dict[str, Any]:
    output_path = (config.output_path or (root / 'runtime_trace.json')).resolve()
    node_bin = config.node_bin or os.environ.get('ORBITS_NODE_BIN', 'node')
    node_path = shutil.which(node_bin) or shutil.which(os.environ.get('ORBITS_NODE_BIN', '')) or shutil.which('node')
    if not node_path:
        raise RuntimeError('Node.js runtime tracing requested but no node executable was found')
    script_path = Path(__file__).with_name('node_runtime_trace.cjs').resolve()
    if not script_path.exists():
        raise FileNotFoundError(f'Node runtime tracer not found: {script_path}')

    entry_language = 'typescript' if Path(config.target).suffix.lower() in {'.ts', '.mts', '.cts'} else 'javascript'
    script_type = 'auto'
    suffix = Path(config.target).suffix.lower()
    if suffix in {'.mjs', '.mts'}:
        script_type = 'module'
    elif suffix in {'.cjs', '.cts'}:
        script_type = 'commonjs'

    command = [
        node_path,
        str(script_path),
        '--root', str(root.resolve()),
        '--output', str(output_path),
        '--mode', config.mode,
        '--target', config.target,
        '--timeout', str(max(0, int(config.timeout_s))),
        '--entry-language', entry_language,
        '--script-type', script_type,
    ]
    for arg in config.args:
        command.extend(['--arg', arg])

    try:
        proc = subprocess.run(
            command,
            cwd=str(root.resolve()),
            capture_output=True,
            text=True,
            timeout=max(5, int(config.timeout_s) + 5),
        )
    except subprocess.TimeoutExpired as exc:
        if not output_path.exists():
            raise RuntimeError(f'Node runtime tracing timed out after {config.timeout_s}s before writing an artifact') from exc
        payload = _load_runtime_payload(output_path)
        payload = _patch_timed_out_payload(
            payload,
            output_path,
            int(config.timeout_s),
            f'Runtime trace timed out after {config.timeout_s}s',
        )
        payload.setdefault('subprocess_exit_code', 124)
        if verbose:
            summary = payload.get('summary', {})
            print(
                f"  Runtime:   {summary.get('local_edge_count', 0)} dynamic edges, "
                f"{summary.get('local_file_access_count', 0)} file accesses, exit 124",
                file=sys.stderr,
            )
            print(f"  Runtime:   timed out after {config.timeout_s}s; partial trace kept", file=sys.stderr)
        return payload

    if not output_path.exists():
        stderr = (proc.stderr or '').strip()
        raise RuntimeError(stderr or 'Node runtime tracing failed before writing an artifact')

    payload = _load_runtime_payload(output_path)
    payload.setdefault('subprocess_exit_code', proc.returncode)
    if proc.returncode and not payload.get('error') and proc.stderr.strip():
        payload['error'] = proc.stderr.strip().splitlines()[-1]
    if verbose:
        summary = payload.get('summary', {})
        print(
            f"  Runtime:   {summary.get('local_edge_count', 0)} dynamic edges, "
            f"{summary.get('local_file_access_count', 0)} file accesses, "
            f"exit {payload.get('exit_code', proc.returncode)}",
            file=sys.stderr,
        )
        if payload.get('timed_out'):
            print(f"  Runtime:   timed out after {payload.get('timeout_s', config.timeout_s)}s; partial trace kept", file=sys.stderr)
        elif payload.get('error'):
            print(f"  Runtime:   trace error recorded: {payload['error']}", file=sys.stderr)
    return payload


def run_runtime_trace(root: Path, config: PythonRuntimeTraceConfig | NodeRuntimeTraceConfig | CppRuntimeTraceConfig, verbose: bool = True) -> dict[str, Any]:
    if isinstance(config, CppRuntimeTraceConfig):
        return run_cpp_runtime_trace(root, config, verbose=verbose)
    if isinstance(config, NodeRuntimeTraceConfig):
        return run_node_runtime_trace(root, config, verbose=verbose)
    return run_python_runtime_trace(root, config, verbose=verbose)


_NODE_RUNTIME_ALT_EXTENSIONS = ('.ts', '.tsx', '.mts', '.cts', '.jsx', '.js', '.mjs', '.cjs')
_NODE_RUNTIME_BUILD_PREFIXES = ('dist', 'build', 'out', 'lib')
_NODE_RUNTIME_SOURCE_EXTENSIONS = {'.ts', '.tsx', '.mts', '.cts', '.jsx', '.js', '.mjs', '.cjs'}
_CPP_RUNTIME_EXTENSIONS = {'.so', '.dylib', '.dll', '.exe'}
_SOURCE_MAP_BUNDLER_SCHEMES = ('webpack://', 'vite://', 'rollup://', 'parcel://', 'ng://', 'meteor://')


def _candidate_node_runtime_ids(raw_id: str) -> list[str]:
    normalized = str(raw_id).replace('\\', '/').lstrip('./')
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        candidate = candidate.replace('\\', '/')
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    add(normalized)
    path_obj = PurePosixPath(normalized)
    stem = path_obj.stem
    parent = '' if str(path_obj.parent) == '.' else str(path_obj.parent)
    suffix = path_obj.suffix.lower()

    if suffix in {'.js', '.mjs', '.cjs', '.jsx'}:
        for ext in _NODE_RUNTIME_ALT_EXTENSIONS:
            rel = f'{stem}{ext}' if not parent else f'{parent}/{stem}{ext}'
            add(rel)

    parts = path_obj.parts
    if parts and parts[0] in _NODE_RUNTIME_BUILD_PREFIXES and len(parts) > 1:
        rest = PurePosixPath(*parts[1:])
        rest_parent = '' if str(rest.parent) == '.' else str(rest.parent)
        for prefix in ('src', ''):
            for ext in _NODE_RUNTIME_ALT_EXTENSIONS:
                rel = f'{rest.stem}{ext}' if not rest_parent else f'{rest_parent}/{rest.stem}{ext}'
                add(f'{prefix}/{rel}' if prefix else rel)
    for index, part in enumerate(parts):
        if part not in _NODE_RUNTIME_BUILD_PREFIXES or index == 0 or index == len(parts) - 1:
            continue
        prefix_parts = parts[:index]
        rest = PurePosixPath(*parts[index + 1:])
        rest_parent = '' if str(rest.parent) == '.' else str(rest.parent)
        for source_dir in ('src', ''):
            for ext in _NODE_RUNTIME_ALT_EXTENSIONS:
                leaf = f'{rest.stem}{ext}' if not rest_parent else f'{rest_parent}/{rest.stem}{ext}'
                candidate_parts = list(prefix_parts)
                if source_dir:
                    candidate_parts.append(source_dir)
                if leaf:
                    candidate_parts.append(leaf)
                add('/'.join(candidate_parts))
    return candidates


def _map_runtime_node_id(raw_id: str, node_ids: set[str], trace_language: str) -> str | None:
    return _map_runtime_node_id_with_root(raw_id, node_ids, trace_language, None)


def _strip_source_map_noise(value: str | None) -> str:
    cleaned = str(value or '').strip().strip('"').strip("'")
    if not cleaned:
        return ''
    if '#' in cleaned:
        cleaned = cleaned.split('#', 1)[0]
    if '?' in cleaned:
        cleaned = cleaned.split('?', 1)[0]
    return cleaned.strip()


def _source_map_repo_hints(value: str | None) -> list[str]:
    cleaned = _strip_source_map_noise(value)
    if not cleaned or cleaned.startswith('file:') or cleaned.startswith('node:'):
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        normalized = candidate.replace('\\', '/').strip()
        if '/./' in normalized:
            normalized = normalized.split('/./', 1)[1]
        if normalized.startswith('./'):
            normalized = normalized[2:]
        if normalized.startswith('~/'):
            normalized = normalized[2:]
        if normalized.startswith('/'):
            normalized = normalized.lstrip('/')
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    if any(cleaned.startswith(prefix) for prefix in _SOURCE_MAP_BUNDLER_SCHEMES):
        _, remainder = cleaned.split('://', 1)
        remainder = remainder.lstrip('/')
        add(remainder)
        if '/' in remainder:
            add(remainder.split('/', 1)[1])
        return candidates

    add(cleaned)
    return candidates


def _source_map_root_bases(source_root: str | None, map_parent: Path, root: Path | None) -> list[Path]:
    cleaned = _strip_source_map_noise(source_root)
    bases: list[Path] = [map_parent]
    seen: set[str] = {str(map_parent.resolve())}
    if not cleaned:
        return bases

    def add(base: Path) -> None:
        resolved = base.resolve()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            bases.append(resolved)

    if cleaned.startswith('file:'):
        try:
            add(Path(url2pathname(urlparse(cleaned).path)))
        except Exception:
            return bases
        return bases

    if any(cleaned.startswith(prefix) for prefix in _SOURCE_MAP_BUNDLER_SCHEMES):
        if root is not None:
            for hint in _source_map_repo_hints(cleaned):
                add(root / hint)
        return bases

    source_root_path = Path(cleaned)
    if source_root_path.is_absolute():
        add(source_root_path)
        if root is not None:
            add(root / cleaned.lstrip('/\\'))
        return bases

    add(map_parent / cleaned)
    if root is not None:
        hint = cleaned.lstrip('./')
        if hint:
            add(root / hint)
    return bases


def _path_exists_under_root(rel_id: str, root: Path | None) -> bool:
    if root is None or not rel_id:
        return False
    try:
        candidate = (root / rel_id).resolve()
    except Exception:
        return False
    if not candidate.exists():
        return False
    return relative_to_root(candidate, root.resolve()) == rel_id


def _runtime_path_candidates(raw_id: str, trace_language: str, root: Path | None) -> list[str]:
    normalized = str(raw_id or '').replace('\\', '/').lstrip('./')
    if not normalized:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str | None) -> None:
        value = str(candidate or '').replace('\\', '/').lstrip('./')
        if value and value not in seen:
            seen.add(value)
            candidates.append(value)

    if trace_language == 'cpp':
        add(_native_project_rel(normalized, root))
        return candidates

    if trace_language == 'nodejs':
        for candidate in _source_map_candidates(normalized, root):
            add(candidate)
        for candidate in _candidate_node_runtime_ids(normalized):
            if candidate != normalized:
                add(candidate)
        add(normalized)
        return candidates

    add(normalized)
    return candidates


def _extract_source_mapping_url(source_path: Path) -> str | None:
    try:
        text = source_path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return None
    matches = re.findall(r'[#@]\s*sourceMappingURL\s*=\s*([^\s*]+)', text)
    return _strip_source_map_noise(matches[-1]) if matches else None


def _decode_inline_source_map(url: str) -> dict[str, Any] | None:
    if not url.startswith('data:application/json;base64,'):
        return None
    try:
        encoded = url.split(',', 1)[1]
        decoded = base64.b64decode(encoded)
        return json.loads(decoded.decode('utf-8'))
    except Exception:
        return None


def _load_source_map_payload(source_path: Path) -> tuple[dict[str, Any], Path | None] | tuple[None, None]:
    inline = None
    mapping_url = _extract_source_mapping_url(source_path)
    if mapping_url:
        inline = _decode_inline_source_map(mapping_url)
        if inline is not None:
            return inline, None
        custom_map_path = (source_path.parent / mapping_url).resolve()
        if custom_map_path.exists():
            try:
                return json.loads(custom_map_path.read_text(encoding='utf-8')), custom_map_path
            except Exception:
                return None, None
    map_path = Path(f'{source_path}.map')
    if not map_path.exists():
        return None, None
    try:
        return json.loads(map_path.read_text(encoding='utf-8')), map_path
    except Exception:
        return None, None


def _iter_source_map_sources(payload: dict[str, Any]) -> list[str]:
    sources = [source for source in payload.get('sources', []) if isinstance(source, str)]
    sections = payload.get('sections', [])
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict):
            continue
        nested = section.get('map')
        if isinstance(nested, dict):
            sources.extend(_iter_source_map_sources(nested))
    return sources


def _source_map_candidates(raw_id: str, root: Path | None) -> list[str]:
    if root is None:
        return []
    normalized = str(raw_id or '').replace('\\', '/').lstrip('./')
    suffix = PurePosixPath(normalized).suffix.lower()
    if suffix not in {'.js', '.mjs', '.cjs', '.jsx'}:
        return []

    source_path = (root / Path(normalized)).resolve()
    payload, map_path = _load_source_map_payload(source_path)
    if not payload:
        return []

    source_root = str(payload.get('sourceRoot', '') or '')
    seen: set[str] = set()
    candidates: list[str] = []

    def add_candidate(candidate_path: Path) -> None:
        rel_str = relative_to_root(candidate_path.resolve(), root.resolve())
        if not rel_str:
            return
        if rel_str not in seen:
            seen.add(rel_str)
            candidates.append(rel_str)

    map_parent = map_path.parent if map_path is not None else source_path.parent
    root_bases = _source_map_root_bases(source_root, map_parent, root.resolve())
    for source in _iter_source_map_sources(payload):
        if not isinstance(source, str):
            continue
        source_token = _strip_source_map_noise(source)
        if not source_token or source_token.startswith('node:'):
            continue
        try:
            if source_token.startswith('file:'):
                parsed = urlparse(source_token)
                add_candidate(Path(url2pathname(parsed.path)))
                continue
            if Path(source_token).is_absolute():
                add_candidate(Path(source_token))
                add_candidate(root.resolve() / source_token.lstrip('/\\'))
            for hint in _source_map_repo_hints(source_token):
                add_candidate(root.resolve() / hint)
            if '://' in source_token and not source_token.startswith('file:'):
                continue
            for base in root_bases:
                add_candidate((base / source_token).resolve())
                for hint in _source_map_repo_hints(source_token):
                    add_candidate((base / hint).resolve())
        except Exception:
            continue
    return candidates


def _map_runtime_node_id_with_root(raw_id: str, node_ids: set[str], trace_language: str, root: Path | None) -> str | None:
    for candidate in _runtime_path_candidates(raw_id, trace_language, root):
        if candidate in node_ids:
            return candidate
    for candidate in _runtime_path_candidates(raw_id, trace_language, root):
        if trace_language in {'nodejs', 'cpp'} and _path_exists_under_root(candidate, root):
            return candidate
    return None


def _ensure_runtime_node(
    merged: dict[str, Any],
    node_ids: set[str],
    rel_id: str,
    runtime_language: str,
    root: Path | None,
) -> None:
    if not rel_id or rel_id in node_ids:
        return
    disk_path = (root / rel_id).resolve() if root is not None else None
    stat = None
    if disk_path is not None and disk_path.exists():
        try:
            stat = disk_path.stat()
        except OSError:
            stat = None
    rel_path = PurePosixPath(rel_id)
    merged['nodes'].append({
        'id': rel_id,
        'filepath': rel_id,
        'name': rel_path.name,
        'language': runtime_language,
        'size': stat.st_size if stat else 0,
        'mtime': round(stat.st_mtime) if stat else 0,
        'dir': str(rel_path.parent) if str(rel_path.parent) != '.' else '.',
        'runtime_only': True,
    })
    node_ids.add(rel_id)


def _infer_runtime_only_language(rel_id: str, trace_language: str) -> str:
    ext = PurePosixPath(rel_id).suffix.lower()
    if trace_language == 'nodejs':
        if ext in {'.ts', '.mts', '.cts'}:
            return 'typescript'
        if ext in {'.tsx', '.jsx'}:
            return 'tsx'
        return 'javascript'
    return trace_language


def _resolve_runtime_node_id(
    merged: dict[str, Any],
    node_ids: set[str],
    raw_id: str,
    trace_language: str,
    root: Path | None,
) -> str | None:
    mapped = _map_runtime_node_id_with_root(raw_id, node_ids, trace_language, root)
    if not mapped:
        return None
    if trace_language in {'nodejs', 'cpp'} and mapped not in node_ids and _path_exists_under_root(mapped, root):
        _ensure_runtime_node(merged, node_ids, mapped, _infer_runtime_only_language(mapped, trace_language), root)
    return mapped


def _normalize_runtime_overlay_item(item: tuple[dict, Path] | tuple[dict, Path, bool]) -> tuple[dict[str, Any], Path, bool]:
    if len(item) == 2:
        trace, artifact_path = item
        return trace, artifact_path, False
    trace, artifact_path, stale = item
    return trace, artifact_path, bool(stale)


def merge_runtime_traces(
    static_graph: dict[str, Any],
    overlays: list[tuple[dict, Path] | tuple[dict, Path, bool]],
) -> dict[str, Any]:
    merged = {
        **static_graph,
        'nodes': [dict(node) for node in static_graph.get('nodes', [])],
        'edges': [dict(edge) for edge in static_graph.get('edges', [])],
        'dynamic_edges': [],
        'runtime': {
            'entry': {},
            'summary': {},
            'timed_out': False,
            'elapsed_s': 0,
            'exit_code': 0,
            'error': None,
            'file_accesses': [],
            'artifact': '',
            'stale': False,
            'sessions': [],
            'languages': [],
        },
        'meta': dict(static_graph.get('meta', {})),
    }
    root_str = static_graph.get('meta', {}).get('root')
    root_path = Path(root_str).resolve() if root_str else None
    node_ids = {str(node.get('id', '')).replace('\\', '/') for node in merged['nodes']}
    static_pairs = {(str(edge.get('source', '')).replace('\\', '/'), str(edge.get('target', '')).replace('\\', '/')) for edge in merged['edges']}
    for edge in merged['edges']:
        edge.setdefault('origins', ['static'])
        edge.setdefault('dynamic', False)
        edge.setdefault('runtime_hits', 0)
        edge.setdefault('runtime_modules', [])
    dynamic_edge_map: dict[tuple[str, str], dict[str, Any]] = {}
    aggregate_dynamic_pairs: set[tuple[str, str]] = set()
    runtime_file_accesses: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    aggregate_languages: list[str] = []
    total_elapsed = 0.0
    total_edge_hits = 0
    total_import_calls = 0
    total_external_import_calls = 0
    total_file_access_hits = 0
    total_symbol_binding_hits = 0
    errors: list[str] = []

    for index, overlay in enumerate(overlays):
        trace, artifact_path, stale = _normalize_runtime_overlay_item(overlay)
        trace_language = str(trace.get('language', 'python'))
        trace_engine = str(trace.get('engine', trace_language))
        session_pairs: set[tuple[str, str]] = set()
        session_dynamic_pairs: set[tuple[str, str]] = set()
        for item in trace.get('edges', []):
            source = _resolve_runtime_node_id(merged, node_ids, item.get('source', ''), trace_language, root_path)
            target = _resolve_runtime_node_id(merged, node_ids, item.get('target', ''), trace_language, root_path)
            if not source or not target or source == target:
                continue
            pair = (source, target)
            dynamic = pair not in static_pairs
            session_pairs.add(pair)
            if dynamic:
                session_dynamic_pairs.add(pair)
                aggregate_dynamic_pairs.add(pair)
            edge = dynamic_edge_map.setdefault(pair, {
                'source': source,
                'target': target,
                'type': item.get('type', 'runtime_import'),
                'line': item.get('line', -1),
                'language': item.get('language', trace_language),
                'origins': ['runtime'],
                'dynamic': dynamic,
                'runtime_hits': 0,
                'runtime_modules': set(),
                'runtime_lines': set(),
                'runtime_symbols': set(),
                'runtime_symbol_hits': 0,
                'runtime_sessions': set(),
            })
            edge['dynamic'] = edge['dynamic'] or dynamic
            line = int(item.get('line', -1) or -1)
            if line > 0 and (edge['line'] <= 0 or line < edge['line']):
                edge['line'] = line
            edge['runtime_hits'] += int(item.get('runtime_hits', item.get('count', 0) or 0))
            edge['runtime_modules'].update(item.get('runtime_modules', []))
            edge['runtime_lines'].update(item.get('runtime_lines', []))
            edge['runtime_symbols'].update(item.get('runtime_symbols', []))
            edge['runtime_symbol_hits'] += int(item.get('runtime_symbol_hits', 0) or len(item.get('runtime_symbols', []) or []))
            edge['runtime_sessions'].add(index)

        session_artifact = str(artifact_path.resolve())
        entry_target = _map_runtime_node_id_with_root(trace.get('entry', {}).get('target', ''), node_ids, trace_language, root_path) or trace.get('entry', {}).get('target', '')
        if trace_language in {'nodejs', 'cpp'} and entry_target and entry_target not in node_ids and _path_exists_under_root(entry_target, root_path):
            _ensure_runtime_node(merged, node_ids, entry_target, _infer_runtime_only_language(entry_target, trace_language), root_path)
        session = {
            'language': trace_language,
            'engine': trace_engine,
            'artifact': session_artifact,
            'entrypoint': entry_target,
            'entry_mode': trace.get('entry', {}).get('mode', 'script'),
            'args': list(trace.get('entry', {}).get('args', [])),
            'elapsed_s': trace.get('elapsed_s', 0),
            'runtime_edges': len(session_pairs),
            'dynamic_edges': len(session_dynamic_pairs),
            'file_accesses': len(trace.get('file_accesses', [])),
            'timed_out': bool(trace.get('timed_out', False)),
            'exit_code': trace.get('exit_code', 0),
            'stale': bool(stale),
            'error': trace.get('error'),
        }
        sessions.append(session)
        aggregate_languages.append(trace_language)
        total_elapsed += float(trace.get('elapsed_s', 0) or 0)
        summary = trace.get('summary', {})
        total_edge_hits += int(summary.get('local_edge_hits', 0) or 0)
        total_import_calls += int(summary.get('import_calls', 0) or 0)
        total_external_import_calls += int(summary.get('external_import_calls', 0) or 0)
        total_file_access_hits += int(summary.get('local_file_access_hits', 0) or 0)
        total_symbol_binding_hits += int(
            summary.get('symbol_binding_count', 0)
            or sum(int(item.get('runtime_symbol_hits', 0) or len(item.get('runtime_symbols', []) or [])) for item in trace.get('edges', []))
        )
        if trace.get('error'):
            errors.append(str(trace.get('error')))

        for access in trace.get('file_accesses', []):
            access_source = _map_runtime_node_id_with_root(access.get('source', ''), node_ids, trace_language, root_path) or access.get('source', '')
            access_path = _map_runtime_node_id_with_root(access.get('path', ''), node_ids, trace_language, root_path) or access.get('path', '')
            runtime_file_accesses.append({
                **dict(access),
                'source': access_source,
                'path': access_path,
                'language': trace_language,
                'artifact': session_artifact,
                'stale': bool(stale),
            })

    merged['dynamic_edges'] = [
        {
            **edge,
            'runtime_modules': sorted(edge['runtime_modules']),
            'runtime_lines': sorted(int(line) for line in edge['runtime_lines']),
            'runtime_symbols': sorted(edge['runtime_symbols']),
            'runtime_sessions': sorted(edge['runtime_sessions']),
        }
        for edge in sorted(dynamic_edge_map.values(), key=lambda item: (item['source'], item['target']))
    ]

    latest = sessions[-1] if sessions else {}
    language_set = sorted({lang for lang in aggregate_languages if lang})
    aggregate_language = language_set[0] if len(language_set) == 1 else ('mixed' if language_set else '')
    engine_set = sorted({session['engine'] for session in sessions if session.get('engine')})
    aggregate_engine = engine_set[0] if len(engine_set) == 1 else ('mixed' if engine_set else '')
    aggregate_error = '; '.join(dict.fromkeys(errors)) if errors else None
    aggregate_timed_out = any(session.get('timed_out') for session in sessions)
    aggregate_stale = any(session.get('stale') for session in sessions)

    merged['runtime'] = {
        'entry': {
            'mode': latest.get('entry_mode', 'script'),
            'target': latest.get('entrypoint', ''),
            'args': list(latest.get('args', [])),
        },
        'summary': {
            'session_count': len(sessions),
            'import_calls': total_import_calls,
            'external_import_calls': total_external_import_calls,
            'local_edge_hits': total_edge_hits,
            'local_edge_count': len(merged['dynamic_edges']),
            'local_file_access_hits': total_file_access_hits,
            'local_file_access_count': len(runtime_file_accesses),
            'symbol_binding_count': total_symbol_binding_hits,
        },
        'timed_out': aggregate_timed_out,
        'elapsed_s': round(total_elapsed, 3),
        'exit_code': latest.get('exit_code', 0),
        'error': aggregate_error,
        'file_accesses': runtime_file_accesses,
        'artifact': latest.get('artifact', ''),
        'stale': aggregate_stale,
        'sessions': sessions,
        'languages': language_set,
    }
    merged['meta']['runtime'] = {
        'enabled': bool(sessions),
        'language': aggregate_language,
        'languages': language_set,
        'engine': aggregate_engine,
        'artifact': latest.get('artifact', ''),
        'artifacts': [session['artifact'] for session in sessions],
        'entrypoint': latest.get('entrypoint', ''),
        'entry_mode': latest.get('entry_mode', 'script'),
        'args': list(latest.get('args', [])),
        'elapsed_s': round(total_elapsed, 3),
        'runtime_edges': len(merged['dynamic_edges']),
        'dynamic_edges': len(aggregate_dynamic_pairs),
        'file_accesses': len(runtime_file_accesses),
        'symbol_binding_count': total_symbol_binding_hits,
        'timed_out': aggregate_timed_out,
        'exit_code': latest.get('exit_code', 0),
        'stale': aggregate_stale,
        'error': aggregate_error,
        'sessions': sessions,
        'session_count': len(sessions),
    }
    return merged


def merge_runtime_trace(static_graph: dict[str, Any], trace: dict[str, Any], artifact_path: Path, stale: bool = False) -> dict[str, Any]:
    return merge_runtime_traces(static_graph, [(trace, artifact_path, stale)])


def _main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--child-python-trace' in argv:
        return _child_main(argv)
    parser = argparse.ArgumentParser(prog='runtime_trace')
    parser.add_argument('--root', required=True)
    parser.add_argument('--output', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--script')
    mode.add_argument('--module')
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--arg', dest='args', action='append', default=[])
    ns = parser.parse_args(argv)
    config = PythonRuntimeTraceConfig(
        mode='script' if ns.script else 'module',
        target=ns.script or ns.module,
        args=ns.args,
        output_path=Path(ns.output),
        timeout_s=ns.timeout,
    )
    run_python_runtime_trace(Path(ns.root), config, verbose=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
