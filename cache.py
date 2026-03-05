"""
cache.py — Orbits Phase 3 incremental cache

Stores extraction results keyed by (filepath, mtime, size).
On a rerun, any file whose mtime and size haven't changed skips
extraction entirely and returns the cached imports directly.

On a 500-file project, the first run takes ~2.5s.
Every subsequent run (no changes) takes ~0.05s.
Only changed files re-parse.

Cache file: <project_root>/.orbits_cache.json
It's a plain JSON file so it's inspectable and deletable.
Add .orbits_cache.json to .gitignore.
"""

import json
import os
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


CACHE_FILENAME = '.orbits_cache.json'
CACHE_VERSION  = 3   # bump this to invalidate all caches on schema change


@dataclass
class CachedFile:
    mtime:   float       # os.stat().st_mtime
    size:    int         # os.stat().st_size
    imports: list[dict]  # serialized RawImport dicts


@dataclass
class CacheStats:
    hits:   int = 0
    misses: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0


class ImportCache:
    """
    Disk-backed cache mapping file paths to their extracted imports.
    Thread-safe for read. Not safe for concurrent writes.
    """

    def __init__(self, root: Path):
        self.root       = root
        self._path      = root / CACHE_FILENAME
        self._data: dict[str, CachedFile] = {}
        self._dirty     = False
        self.stats      = CacheStats()
        self._load()

    # ── Public API ──────────────────────────────────────────────────────────

    def get(self, filepath: Path) -> Optional[list[dict]]:
        """
        Return cached imports if file is unchanged, else None.
        Caller must call put() with fresh results on a miss.
        """
        try:
            stat = filepath.stat()
        except OSError:
            return None

        key = self._key(filepath)
        entry = self._data.get(key)

        if (entry is not None
                and entry.mtime == stat.st_mtime
                and entry.size  == stat.st_size):
            self.stats.hits += 1
            return entry.imports

        self.stats.misses += 1
        return None

    def put(self, filepath: Path, imports: list[dict]):
        """Store extraction results for a file."""
        try:
            stat = filepath.stat()
        except OSError:
            return
        key = self._key(filepath)
        self._data[key] = CachedFile(
            mtime=stat.st_mtime,
            size=stat.st_size,
            imports=imports,
        )
        self._dirty = True

    def save(self):
        """Flush to disk. Call once after all files are processed."""
        if not self._dirty:
            return
        try:
            payload = {
                '_version': CACHE_VERSION,
                '_saved':   time.time(),
                'files': {
                    k: {'mtime': v.mtime, 'size': v.size, 'imports': v.imports}
                    for k, v in self._data.items()
                }
            }
            tmp = self._path.with_suffix('.tmp')
            tmp.write_text(json.dumps(payload, separators=(',', ':')),
                           encoding='utf-8')
            tmp.replace(self._path)
            self._dirty = False
        except (OSError, PermissionError):
            pass  # cache write failure is never fatal

    def invalidate(self):
        """Wipe the cache."""
        self._data  = {}
        self._dirty = True
        self.save()

    # ── Internal ────────────────────────────────────────────────────────────

    def _key(self, filepath: Path) -> str:
        """Relative path string used as cache key."""
        try:
            return str(filepath.relative_to(self.root))
        except ValueError:
            return str(filepath)

    def _load(self):
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding='utf-8'))
            if payload.get('_version') != CACHE_VERSION:
                return  # version mismatch — treat as empty
            for k, v in payload.get('files', {}).items():
                self._data[k] = CachedFile(
                    mtime=v['mtime'],
                    size=v['size'],
                    imports=v['imports'],
                )
        except (OSError, json.JSONDecodeError, KeyError):
            self._data = {}  # corrupt cache — start fresh
