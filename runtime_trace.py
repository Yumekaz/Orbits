from __future__ import annotations

import argparse
import builtins
import importlib
import importlib.util
import inspect
import io
import json
import os
import runpy
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any


@dataclass
class PythonRuntimeTraceConfig:
    mode: str
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
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return None
        return str(rel).replace('\\', '/')

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
    proc = __import__('subprocess').run(command, cwd=str(Path(__file__).resolve().parent), capture_output=True, text=True)
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


def merge_runtime_trace(static_graph: dict[str, Any], trace: dict[str, Any], artifact_path: Path, stale: bool = False) -> dict[str, Any]:
    merged = {
        **static_graph,
        'nodes': [dict(node) for node in static_graph.get('nodes', [])],
        'edges': [dict(edge) for edge in static_graph.get('edges', [])],
        'dynamic_edges': [],
        'runtime': {
            'entry': trace.get('entry', {}),
            'summary': trace.get('summary', {}),
            'timed_out': bool(trace.get('timed_out', False)),
            'elapsed_s': trace.get('elapsed_s', 0),
            'exit_code': trace.get('exit_code', 0),
            'error': trace.get('error'),
            'file_accesses': trace.get('file_accesses', []),
            'artifact': str(artifact_path.resolve()),
            'stale': bool(stale),
        },
        'meta': dict(static_graph.get('meta', {})),
    }
    node_ids = {str(node.get('id', '')).replace('\\', '/') for node in merged['nodes']}
    static_pairs = {(str(edge.get('source', '')).replace('\\', '/'), str(edge.get('target', '')).replace('\\', '/')) for edge in merged['edges']}
    for edge in merged['edges']:
        edge.setdefault('origins', ['static'])
        edge.setdefault('dynamic', False)
        edge.setdefault('runtime_hits', 0)
        edge.setdefault('runtime_modules', [])
    dynamic_only = 0
    for item in trace.get('edges', []):
        source = str(item.get('source', '')).replace('\\', '/')
        target = str(item.get('target', '')).replace('\\', '/')
        if not source or not target or source not in node_ids or target not in node_ids or source == target:
            continue
        pair = (source, target)
        dynamic = pair not in static_pairs
        if dynamic:
            dynamic_only += 1
        merged['dynamic_edges'].append({
            'source': source,
            'target': target,
            'type': item.get('type', 'runtime_import'),
            'line': item.get('line', -1),
            'language': item.get('language', 'python'),
            'origins': ['runtime'],
            'dynamic': dynamic,
            'runtime_hits': int(item.get('runtime_hits', item.get('count', 0) or 0)),
            'runtime_modules': list(item.get('runtime_modules', [])),
            'runtime_lines': list(item.get('runtime_lines', [])),
        })
    merged['meta']['runtime'] = {
        'enabled': True,
        'language': trace.get('language', 'python'),
        'artifact': str(artifact_path.resolve()),
        'entrypoint': trace.get('entry', {}).get('target', ''),
        'entry_mode': trace.get('entry', {}).get('mode', 'script'),
        'args': list(trace.get('entry', {}).get('args', [])),
        'elapsed_s': trace.get('elapsed_s', 0),
        'runtime_edges': len(merged['dynamic_edges']),
        'dynamic_edges': dynamic_only,
        'file_accesses': len(trace.get('file_accesses', [])),
        'timed_out': bool(trace.get('timed_out', False)),
        'exit_code': trace.get('exit_code', 0),
        'stale': bool(stale),
        'error': trace.get('error'),
    }
    return merged


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
