"""
resolvers/go_resolver.py — Orbits Phase 3

Resolves Go import paths to local file paths.
go.mod module name is used to detect which imports are local.
"""
from pathlib import Path
from typing import Optional


_GO_STDLIB_TOP = frozenset({
    'archive','bufio','builtin','bytes','cmp','compress','container',
    'context','crypto','database','debug','embed','encoding','errors',
    'expvar','flag','fmt','go','hash','html','image','index','io',
    'iter','log','maps','math','mime','net','os','path','plugin',
    'reflect','regexp','runtime','slices','sort','strconv','strings',
    'sync','syscall','testing','text','time','unicode','unique',
    'unsafe','internal',
})


def read_module_name(root: Path) -> str:
    gomod = root / 'go.mod'
    if not gomod.exists():
        return ''
    try:
        for line in gomod.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line.startswith('module '):
                return line[7:].strip()
    except Exception:
        pass
    return ''


class GoResolver:
    def __init__(self, root: Path):
        self.root = root
        self._module = read_module_name(root)

    def resolve(self, raw: str) -> tuple[Optional[str], str]:
        """Returns (path_relative_to_root_or_None, kind)"""
        top = raw.split('/')[0]

        # Stdlib
        if top in _GO_STDLIB_TOP:
            return None, 'STDLIB'

        # Relative path
        if raw.startswith('./') or raw.startswith('../'):
            path = self._find_go_package(self.root / raw)
            if path:
                return path, 'LOCAL'
            return None, 'UNKNOWN'

        # Local module: 'github.com/user/proj/internal/config' → 'internal/config'
        if self._module and raw.startswith(self._module + '/'):
            rel = raw[len(self._module) + 1:]
            path = self._find_go_package(self.root / rel)
            if path:
                return path, 'LOCAL'
            return None, 'UNKNOWN'

        return None, 'THIRD_PARTY'

    def _find_go_package(self, base: Path) -> Optional[str]:
        """Find any .go file in the package directory."""
        if not base.is_dir():
            return None
        for f in base.glob('*.go'):
            try:
                return str(f.relative_to(self.root))
            except ValueError:
                pass
        return None
