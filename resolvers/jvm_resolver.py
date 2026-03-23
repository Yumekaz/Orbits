"""Java and Kotlin import resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from path_utils import relative_to_root


_STDLIB_PREFIXES = ('java.', 'javax.', 'kotlin.')


@dataclass
class JvmProjectConfig:
    root: Path
    src_roots: list[Path] = field(default_factory=list)
    package_files: dict[str, list[str]] = field(default_factory=dict)
    symbol_files: dict[str, str] = field(default_factory=dict)

    @classmethod
    def detect(cls, root: Path) -> 'JvmProjectConfig':
        cfg = cls(root=root)
        candidates = [
            root / 'src',
            root / 'src' / 'main' / 'java',
            root / 'src' / 'test' / 'java',
            root / 'src' / 'main' / 'kotlin',
            root / 'src' / 'test' / 'kotlin',
            root / 'app' / 'src' / 'main' / 'java',
            root / 'app' / 'src' / 'main' / 'kotlin',
        ]
        for candidate in candidates:
            if candidate.is_dir() and candidate not in cfg.src_roots:
                cfg.src_roots.append(candidate)
        if root not in cfg.src_roots:
            cfg.src_roots.insert(0, root)
        cfg._index_sources()
        return cfg

    def _index_sources(self) -> None:
        for src_root in self.src_roots:
            if not src_root.exists():
                continue
            for path in src_root.rglob('*'):
                if path.suffix not in {'.java', '.kt'} or not path.is_file():
                    continue
                rel = relative_to_root(path, self.root)
                rel_to_src = relative_to_root(path, src_root)
                if not rel or not rel_to_src:
                    continue
                rel_to_src_path = Path(rel_to_src)
                package = '.'.join(rel_to_src_path.parent.parts) if rel_to_src_path.parent.parts else ''
                rel_str = rel
                self.package_files.setdefault(package, []).append(rel_str)
                symbol = rel_to_src_path.with_suffix('')
                fqcn = '.'.join(symbol.parts)
                if fqcn:
                    self.symbol_files.setdefault(fqcn, rel_str)


class JvmResolver:
    def __init__(self, root: Path, config: JvmProjectConfig):
        self.root = root
        self.config = config

    def resolve(self, raw: str) -> tuple[Optional[str | list[str]], str]:
        if not raw:
            return None, 'UNKNOWN'
        if raw.startswith(_STDLIB_PREFIXES):
            return None, 'STDLIB'

        if raw.endswith('.*'):
            package = raw[:-2]
            files = sorted(self.config.package_files.get(package, []))
            if files:
                return files, 'LOCAL'
            return None, 'THIRD_PARTY'

        resolved = self._resolve_symbol(raw)
        if resolved:
            return resolved, 'LOCAL'
        return None, 'THIRD_PARTY'

    def _resolve_symbol(self, raw: str) -> Optional[str]:
        parts = [part for part in raw.split('.') if part]
        for stop in range(len(parts), 0, -1):
            candidate = '.'.join(parts[:stop])
            if candidate in self.config.symbol_files:
                return self.config.symbol_files[candidate]
        return None
