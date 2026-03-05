"""
resolver.py — Orbits Phase 2 resolution engine.

Turns raw import strings into absolute file paths (or classifies them
as stdlib / third-party / unknown so we stop trying to find them on disk).

Architecture
────────────
  ProjectConfig   — reads pyproject.toml / setup.cfg / setup.py to
                    discover src dirs, package roots, package name
  StdlibIndex     — fast O(1) stdlib module lookup
  ThirdPartyIndex — reads requirements.txt / pyproject.toml deps
  PythonResolver  — main class, wraps everything, caches results

Usage (from extractor.py)
─────────────────────────
  config   = ProjectConfig.detect(root)
  resolver = PythonResolver(root, config)

  result = resolver.resolve('utils.helpers', level=0, from_file=Path('app/main.py'))
  # result.kind  → ImportKind.LOCAL
  # result.path  → 'utils/helpers.py'  (relative to root)

  result = resolver.resolve('os', level=0, from_file=...)
  # result.kind  → ImportKind.STDLIB
  # result.path  → None

  resolver.stats()  → {resolved, stdlib, third_party, unknown, total, pct_resolved}
"""

import sys
import ast
import configparser
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ── Import classification ──────────────────────────────────────────────────

class ImportKind:
    LOCAL       = 'LOCAL'        # found in the project on disk
    STDLIB      = 'STDLIB'       # Python standard library
    THIRD_PARTY = 'THIRD_PARTY'  # installed package (in requirements/pyproject)
    UNKNOWN     = 'UNKNOWN'      # can't determine — might be third-party or dynamic


@dataclass
class ResolveResult:
    raw:  str                    # the original import string as written
    kind: str = ImportKind.UNKNOWN
    path: Optional[str] = None   # relative path from root, only if LOCAL


# ── Stdlib index ───────────────────────────────────────────────────────────

def _build_stdlib_set() -> frozenset[str]:
    """
    Return the set of top-level stdlib module names.
    Python 3.10+ exposes sys.stdlib_module_names directly.
    For older Pythons we maintain a comprehensive static list.
    """
    if hasattr(sys, 'stdlib_module_names'):
        return frozenset(sys.stdlib_module_names)

    # Static list for Python < 3.10 (covers 3.7–3.9)
    return frozenset({
        '__future__', '_thread', 'abc', 'aifc', 'argparse', 'array',
        'ast', 'asynchat', 'asyncio', 'asyncore', 'atexit', 'audioop',
        'base64', 'bdb', 'binascii', 'binhex', 'bisect', 'builtins',
        'bz2', 'calendar', 'cgi', 'cgitb', 'chunk', 'cmath', 'cmd',
        'code', 'codecs', 'codeop', 'collections', 'colorsys', 'compileall',
        'concurrent', 'configparser', 'contextlib', 'contextvars', 'copy',
        'copyreg', 'cProfile', 'csv', 'ctypes', 'curses', 'dataclasses',
        'datetime', 'dbm', 'decimal', 'difflib', 'dis', 'distutils',
        'doctest', 'email', 'encodings', 'enum', 'errno', 'faulthandler',
        'fcntl', 'filecmp', 'fileinput', 'fnmatch', 'fractions', 'ftplib',
        'functools', 'gc', 'getopt', 'getpass', 'gettext', 'glob', 'grp',
        'gzip', 'hashlib', 'heapq', 'hmac', 'html', 'http', 'idlelib',
        'imaplib', 'imghdr', 'imp', 'importlib', 'inspect', 'io',
        'ipaddress', 'itertools', 'json', 'keyword', 'lib2to3', 'linecache',
        'locale', 'logging', 'lzma', 'mailbox', 'mailcap', 'marshal',
        'math', 'mimetypes', 'mmap', 'modulefinder', 'multiprocessing',
        'netrc', 'nis', 'nntplib', 'numbers', 'opcode', 'operator',
        'optparse', 'os', 'ossaudiodev', 'pathlib', 'pdb', 'pickle',
        'pickletools', 'pipes', 'pkgutil', 'platform', 'plistlib', 'poplib',
        'posix', 'posixpath', 'pprint', 'profile', 'pstats', 'pty',
        'pwd', 'py_compile', 'pyclbr', 'pydoc', 'queue', 'quopri',
        'random', 're', 'readline', 'reprlib', 'resource', 'rlcompleter',
        'runpy', 'sched', 'secrets', 'select', 'selectors', 'shelve',
        'shlex', 'shutil', 'signal', 'site', 'smtpd', 'smtplib', 'sndhdr',
        'socket', 'socketserver', 'spwd', 'sqlite3', 'sre_compile',
        'sre_constants', 'sre_parse', 'ssl', 'stat', 'statistics', 'string',
        'stringprep', 'struct', 'subprocess', 'sunau', 'symtable', 'sys',
        'sysconfig', 'syslog', 'tabnanny', 'tarfile', 'telnetlib', 'tempfile',
        'termios', 'test', 'textwrap', 'threading', 'time', 'timeit',
        'tkinter', 'token', 'tokenize', 'trace', 'traceback', 'tracemalloc',
        'tty', 'turtle', 'turtledemo', 'types', 'typing', 'unicodedata',
        'unittest', 'urllib', 'uu', 'uuid', 'venv', 'warnings', 'wave',
        'weakref', 'webbrowser', 'winreg', 'winsound', 'wsgiref', 'xdrlib',
        'xml', 'xmlrpc', 'zipapp', 'zipfile', 'zipimport', 'zlib',
        'zoneinfo', 'antigravity', 'cgi', 'cgitb', 'chunk', 'crypt',
        'imghdr', 'mailcap', 'msilib', 'nntplib', 'ossaudiodev', 'pipes',
        'sndhdr', 'spwd', 'sunau', 'telnetlib', 'uu', 'xdrlib',
        # common internal modules
        '_collections_abc', '_weakrefset', 'ntpath', 'posixpath', 'genericpath',
    })


STDLIB = _build_stdlib_set()


# ── Project config detection ───────────────────────────────────────────────

@dataclass
class ProjectConfig:
    """
    Discovered project layout info.
    All paths are absolute.
    """
    root:         Path
    src_dirs:     list[Path]   = field(default_factory=list)
    package_name: str          = ''
    third_party:  set[str]     = field(default_factory=set)

    @classmethod
    def detect(cls, root: Path) -> 'ProjectConfig':
        cfg = cls(root=root)
        cfg.src_dirs = [root]   # always search root first

        cfg._read_pyproject_toml()
        cfg._read_setup_cfg()
        cfg._read_setup_py()
        cfg._detect_src_layout()
        cfg._read_requirements()

        return cfg

    # ── pyproject.toml ─────────────────────────────────────────────────────

    def _read_pyproject_toml(self):
        p = self.root / 'pyproject.toml'
        if not p.exists():
            return
        try:
            # Use tomllib (3.11+) or fall back to manual parse
            try:
                import tomllib
                data = tomllib.loads(p.read_text(encoding='utf-8'))
            except ImportError:
                try:
                    import tomli as tomllib
                    data = tomllib.loads(p.read_text(encoding='utf-8'))
                except ImportError:
                    data = _parse_toml_minimal(p)

            # Package name
            name = (data.get('project', {}).get('name', '') or
                    data.get('tool', {}).get('poetry', {}).get('name', ''))
            if name:
                self.package_name = name.replace('-', '_')

            # src layout via flit/hatch/setuptools
            pkg_dir = (
                data.get('tool', {}).get('setuptools', {})
                    .get('package-dir', {}).get('', '')
            )
            if pkg_dir:
                candidate = self.root / pkg_dir
                if candidate.is_dir() and candidate not in self.src_dirs:
                    self.src_dirs.append(candidate)

            # Poetry packages
            packages = (data.get('tool', {}).get('poetry', {})
                            .get('packages', []))
            for pkg in packages:
                if isinstance(pkg, dict):
                    frm = pkg.get('from', '')
                    if frm:
                        candidate = self.root / frm
                        if candidate.is_dir() and candidate not in self.src_dirs:
                            self.src_dirs.append(candidate)

            # Dependencies as third-party hints
            deps = (data.get('project', {}).get('dependencies', []) or
                    list(data.get('tool', {}).get('poetry', {})
                             .get('dependencies', {}).keys()))
            for dep in deps:
                if isinstance(dep, str):
                    name = dep.split('[')[0].split('>')[0].split('<')[0]\
                               .split('=')[0].split('~')[0].strip()
                    if name and name.lower() not in ('python',):
                        self.third_party.add(name.replace('-', '_').lower())

        except Exception:
            pass  # never crash on config reading

    # ── setup.cfg ──────────────────────────────────────────────────────────

    def _read_setup_cfg(self):
        p = self.root / 'setup.cfg'
        if not p.exists():
            return
        try:
            cfg = configparser.ConfigParser()
            cfg.read(str(p))
            if cfg.has_option('metadata', 'name'):
                self.package_name = cfg['metadata']['name'].replace('-', '_')
            if cfg.has_option('options', 'package_dir'):
                val = cfg['options']['package_dir']
                # format: =src  or  pkg=src/pkg
                for part in val.split('\n'):
                    part = part.strip()
                    if part.startswith('='):
                        candidate = self.root / part[1:].strip()
                        if candidate.is_dir() and candidate not in self.src_dirs:
                            self.src_dirs.append(candidate)
            if cfg.has_option('options', 'install_requires'):
                for dep in cfg['options']['install_requires'].split('\n'):
                    name = dep.split('[')[0].split('>')[0].split('<')[0]\
                               .split('=')[0].strip()
                    if name:
                        self.third_party.add(name.replace('-', '_').lower())
        except Exception:
            pass

    # ── setup.py ───────────────────────────────────────────────────────────

    def _read_setup_py(self):
        """
        We don't execute setup.py (unsafe). We do a quick regex scan
        for package_dir and install_requires patterns.
        """
        p = self.root / 'setup.py'
        if not p.exists():
            return
        try:
            import re
            text = p.read_text(encoding='utf-8', errors='replace')

            # name='mypackage'  or  name="mypackage"
            m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', text)
            if m and not self.package_name:
                self.package_name = m.group(1).replace('-', '_')

            # package_dir={'': 'src'}
            m = re.search(r"package_dir\s*=\s*\{['\"][\s]*['\"]\s*:\s*['\"]([^'\"]+)['\"]", text)
            if m:
                candidate = self.root / m.group(1)
                if candidate.is_dir() and candidate not in self.src_dirs:
                    self.src_dirs.append(candidate)

        except Exception:
            pass

    # ── src/ layout autodetect ─────────────────────────────────────────────

    def _detect_src_layout(self):
        """
        If there's a src/ directory containing Python packages,
        add it as a search root even if not declared in config.
        """
        src = self.root / 'src'
        if src.is_dir() and src not in self.src_dirs:
            # Only add if it actually contains Python source
            has_py = any(src.rglob('*.py'))
            if has_py:
                self.src_dirs.append(src)

    # ── requirements.txt ───────────────────────────────────────────────────

    def _read_requirements(self):
        for fname in ('requirements.txt', 'requirements-dev.txt',
                      'requirements/base.txt', 'requirements/prod.txt'):
            p = self.root / fname
            if not p.exists():
                continue
            try:
                for line in p.read_text(encoding='utf-8').splitlines():
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith('-'):
                        continue
                    # strip version specifiers and extras
                    import re
                    name = re.split(r'[>=<!~\[\s;]', line)[0].strip()
                    if name:
                        self.third_party.add(name.replace('-', '_').lower())
            except Exception:
                pass


# ── Minimal TOML parser (fallback when tomllib not available) ──────────────

def _parse_toml_minimal(path: Path) -> dict:
    """
    Extracts only the fields Orbits needs from a TOML file without
    a full parser. Handles simple string/list/table values only.
    Sufficient for reading project.name and basic deps.
    """
    import re
    text   = path.read_text(encoding='utf-8', errors='replace')
    result: dict = {}
    current_section: list[str] = []

    def set_nested(d: dict, keys: list[str], val):
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = val

    def get_nested(d: dict, keys: list[str]):
        for k in keys:
            if not isinstance(d, dict):
                return {}
            d = d.get(k, {})
        return d

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # Section header [tool.poetry]
        m = re.match(r'^\[([^\]]+)\]$', line)
        if m:
            current_section = [p.strip() for p in m.group(1).split('.')]
            continue
        # key = "value"
        m = re.match(r'^(\w[\w-]*)\s*=\s*"([^"]*)"', line)
        if m:
            set_nested(result, current_section + [m.group(1)], m.group(2))
            continue
        # key = 'value'
        m = re.match(r"^(\w[\w-]*)\s*=\s*'([^']*)'", line)
        if m:
            set_nested(result, current_section + [m.group(1)], m.group(2))

    return result


# ── File-system resolution helpers ────────────────────────────────────────

def _find_on_disk(
    parts: list[str],
    search_dirs: list[Path],
    root: Path,
) -> Optional[str]:
    """
    Given module name parts and a list of directories to search,
    return the first matching file path relative to root, or None.

    Search order (per search_dir):
      1. parts/as/path/__init__.py    — package
      2. parts/as/path.py             — module
      3. top_level/__init__.py        — top-level package
      4. top_level.py                 — top-level module
      5. namespace package (no __init__.py, just a directory)
    """
    if not parts:
        return None

    for base in search_dirs:
        # Full path as package: utils/helpers/__init__.py
        pkg = base.joinpath(*parts) / '__init__.py'
        if pkg.exists():
            try: return str(pkg.relative_to(root))
            except ValueError: pass

        # Full path as module: utils/helpers.py
        if len(parts) >= 2:
            mod = base.joinpath(*parts[:-1]) / (parts[-1] + '.py')
            if mod.exists():
                try: return str(mod.relative_to(root))
                except ValueError: pass

        # Single-part module: utils/__init__.py or utils.py
        top_pkg = base / parts[0] / '__init__.py'
        if top_pkg.exists():
            try: return str(top_pkg.relative_to(root))
            except ValueError: pass

        top_mod = base / (parts[0] + '.py')
        if top_mod.exists():
            try: return str(top_mod.relative_to(root))
            except ValueError: pass

        # Namespace package: directory exists, no __init__.py
        # PEP 420 — treat as valid if it contains any .py files
        ns_dir = base.joinpath(*parts)
        if ns_dir.is_dir() and any(ns_dir.glob('*.py')):
            # Point to first .py file as proxy (best we can do without __init__)
            first = next(ns_dir.glob('*.py'))
            try: return str(first.relative_to(root))
            except ValueError: pass

    return None


# ── Main resolver ──────────────────────────────────────────────────────────

class PythonResolver:
    """
    Resolves Python import strings to project-local file paths.

    Thread-safe for read operations once constructed.
    Not designed for concurrent writes to _cache.
    """

    def __init__(self, root: Path, config: ProjectConfig):
        self.root    = root
        self.config  = config
        self._cache: dict[str, ResolveResult] = {}

        # Counters for stats()
        self._counts = {
            ImportKind.LOCAL:       0,
            ImportKind.STDLIB:      0,
            ImportKind.THIRD_PARTY: 0,
            ImportKind.UNKNOWN:     0,
        }

    # ── Public API ─────────────────────────────────────────────────────────

    def resolve(
        self,
        module_name: str,
        level: int,
        from_file: Path,
    ) -> ResolveResult:
        """
        Resolve one import.

        module_name: the dotted module string, e.g. 'utils.helpers'
                     For 'from . import x', this is '' with level=1
        level:       0 = absolute, 1 = '.', 2 = '..' etc.
        from_file:   absolute path of the file doing the import
        """
        # Relative imports bypass cache (they depend on from_file)
        if level > 0:
            return self._resolve_relative(module_name, level, from_file)

        # Absolute: check cache first
        cache_key = module_name
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._resolve_absolute(module_name, from_file)
        self._cache[cache_key] = result
        self._counts[result.kind] += 1
        return result

    def resolve_from_import(
        self,
        module_name: str,
        attr_name: str,
        level: int,
        from_file: Path,
    ) -> ResolveResult:
        """
        Resolve 'from <module_name> import <attr_name>'.

        Tries <module_name>.<attr_name> as a submodule first,
        then falls back to <module_name> (the attr is a name inside
        the module, not a submodule).
        """
        # Try submodule: from pkg import mod → pkg/mod.py
        sub = f"{module_name}.{attr_name}" if module_name else attr_name
        result = self.resolve(sub, level, from_file)
        if result.kind == ImportKind.LOCAL:
            return result

        # Fall back to the parent module
        if module_name:
            return self.resolve(module_name, level, from_file)

        return result

    def stats(self) -> dict:
        total = sum(self._counts.values())
        local = self._counts[ImportKind.LOCAL]
        return {
            'total':       total,
            'resolved':    local,
            'stdlib':      self._counts[ImportKind.STDLIB],
            'third_party': self._counts[ImportKind.THIRD_PARTY],
            'unknown':     self._counts[ImportKind.UNKNOWN],
            'pct_resolved': round(local / total * 100, 1) if total else 0.0,
        }

    # ── Internal ───────────────────────────────────────────────────────────

    def _resolve_absolute(self, module_name: str, from_file: Path) -> ResolveResult:
        if not module_name:
            return ResolveResult(raw=module_name, kind=ImportKind.UNKNOWN)

        top = module_name.split('.')[0].lower()

        # 1. Stdlib check — fast O(1)
        if top in STDLIB:
            return ResolveResult(raw=module_name, kind=ImportKind.STDLIB)

        # 2. Third-party check from config
        if top in self.config.third_party:
            return ResolveResult(raw=module_name, kind=ImportKind.THIRD_PARTY)

        # 3. Try to find on disk
        parts = module_name.split('.')

        # Search dirs: all configured src dirs + file's own dir
        search_dirs = list(self.config.src_dirs)
        if from_file.parent not in search_dirs:
            search_dirs.append(from_file.parent)

        # If root has __init__.py it's itself a package;
        # also search root.parent so 'from mypackage import x' works
        if (self.root / '__init__.py').exists():
            if self.root.parent not in search_dirs:
                search_dirs.append(self.root.parent)

        path = _find_on_disk(parts, search_dirs, self.root)
        if path:
            return ResolveResult(raw=module_name, kind=ImportKind.LOCAL, path=path)

        # 4. Unknown — could be third-party not listed in requirements,
        #    a C extension, or a conditional import
        return ResolveResult(raw=module_name, kind=ImportKind.UNKNOWN)

    def _resolve_relative(
        self,
        module_name: str,
        level: int,
        from_file: Path,
    ) -> ResolveResult:
        """
        Walk up `level` directories from from_file.parent, then
        resolve module_name relative to that anchor.
        """
        anchor = from_file.parent
        for _ in range(level - 1):
            parent = anchor.parent
            # Don't walk above project root
            if parent == self.root.parent or parent == anchor:
                return ResolveResult(raw=('.' * level) + module_name,
                                     kind=ImportKind.UNKNOWN)
            anchor = parent

        if not module_name:
            # 'from . import x' — anchor is the package
            init = anchor / '__init__.py'
            if init.exists():
                try:
                    return ResolveResult(
                        raw='.',
                        kind=ImportKind.LOCAL,
                        path=str(init.relative_to(self.root))
                    )
                except ValueError:
                    pass
            return ResolveResult(raw='.', kind=ImportKind.UNKNOWN)

        parts = module_name.split('.')
        path  = _find_on_disk(parts, [anchor], self.root)

        if path:
            self._counts[ImportKind.LOCAL] += 1
            return ResolveResult(
                raw=('.' * level) + module_name,
                kind=ImportKind.LOCAL,
                path=path,
            )

        self._counts[ImportKind.UNKNOWN] += 1
        return ResolveResult(
            raw=('.' * level) + module_name,
            kind=ImportKind.UNKNOWN,
        )
