"""C and C++ include resolution helpers."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from path_utils import relative_to_root


_HEADER_EXTENSIONS = ('.h', '.hpp', '.hh', '.hxx', '.inc')


@dataclass
class CProjectConfig:
    root: Path
    include_dirs: list[Path] = field(default_factory=list)

    @classmethod
    def detect(cls, root: Path) -> 'CProjectConfig':
        cfg = cls(root=root, include_dirs=[root])
        cfg._read_compile_commands()
        cfg._read_cmake_lists()
        for folder in ('include', 'src'):
            candidate = root / folder
            if candidate.is_dir() and candidate not in cfg.include_dirs:
                cfg.include_dirs.append(candidate)
        return cfg

    def _read_compile_commands(self) -> None:
        path = self.root / 'compile_commands.json'
        if not path.exists():
            return
        try:
            commands = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return
        for entry in commands:
            directory = Path(entry.get('directory', self.root))
            args = entry.get('arguments') or shlex.split(entry.get('command', ''), posix=False)
            self._consume_include_flags(directory, args)

    def _read_cmake_lists(self) -> None:
        path = self.root / 'CMakeLists.txt'
        if not path.exists():
            return
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return
        patterns = [r'include_directories\s*\((.*?)\)', r'target_include_directories\s*\((.*?)\)']
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE | re.DOTALL):
                body = match.group(1)
                for token in re.split(r'[\s\n]+', body):
                    token = token.strip().strip('"')
                    if not token or token.startswith('$') or token.upper() in {'PUBLIC', 'PRIVATE', 'INTERFACE'}:
                        continue
                    candidate = (self.root / token).resolve()
                    if candidate.is_dir() and candidate not in self.include_dirs:
                        self.include_dirs.append(candidate)

    def _consume_include_flags(self, directory: Path, args: list[str]) -> None:
        i = 0
        while i < len(args):
            arg = args[i]
            value = ''
            if arg in ('-I', '-isystem', '-iquote') and i + 1 < len(args):
                value = args[i + 1]
                i += 1
            elif any(arg.startswith(prefix) and len(arg) > len(prefix) for prefix in ('-I', '/I', '-isystem', '-iquote')):
                for prefix in ('-isystem', '-iquote', '-I', '/I'):
                    if arg.startswith(prefix) and len(arg) > len(prefix):
                        value = arg[len(prefix):]
                        break
            if value:
                candidate = Path(value)
                if not candidate.is_absolute():
                    candidate = (directory / candidate).resolve()
                if candidate.is_dir() and candidate not in self.include_dirs:
                    self.include_dirs.append(candidate)
            i += 1


class CFamilyResolver:
    def __init__(self, root: Path, config: CProjectConfig):
        self.root = root
        self.config = config

    def resolve(self, raw: str, from_file: Path, kind: str) -> tuple[Optional[str], str]:
        if kind == 'system_include':
            return None, 'STDLIB'

        if kind == 'dynamic_load':
            resolved = self._resolve_dynamic_load(raw, from_file)
            return (resolved, 'ASSET') if resolved else (None, 'UNKNOWN')

        search_dirs = [from_file.parent]
        for include_dir in self.config.include_dirs:
            if include_dir not in search_dirs:
                search_dirs.append(include_dir)

        for base in search_dirs:
            candidate = (base / raw).resolve()
            resolved = self._probe(candidate)
            if resolved:
                return resolved, 'LOCAL'

        return None, 'UNKNOWN'

    def _probe(self, candidate: Path) -> Optional[str]:
        if candidate.exists() and candidate.is_file():
            return self._rel(candidate)
        if candidate.suffix:
            return None
        for ext in _HEADER_EXTENSIONS:
            with_ext = candidate.with_suffix(ext)
            if with_ext.exists():
                return self._rel(with_ext)
        return None

    def _rel(self, path: Path) -> Optional[str]:
        return relative_to_root(path, self.root)

    def _resolve_dynamic_load(self, raw: str, from_file: Path) -> Optional[str]:
        value = raw.strip().strip('"\'')
        if not value or value.startswith(('$', '%')):
            return None
        raw_path = Path(value)
        candidates: list[Path] = []
        if raw_path.is_absolute():
            candidates.append(raw_path)
        else:
            candidates.append((from_file.parent / value).resolve())
            candidates.append((self.root / value).resolve())
            for include_dir in self.config.include_dirs:
                candidates.append((include_dir / value).resolve())

        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.exists() and candidate.is_file():
                return self._rel(candidate)
        return None
