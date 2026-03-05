"""
resolvers/js_resolver.py — Orbits Phase 3

Resolves JavaScript and TypeScript import strings to project-local file paths.

The hard parts:
  1. tsconfig.json path aliases: '@/components' → 'src/components'
  2. Barrel files: 'import { x } from "./utils"' → utils/index.ts
  3. Extension omission: './Button' → './Button.tsx' or './Button/index.tsx'
  4. node_modules: external, skip
  5. CSS/asset imports: './styles.css' — not source, skip
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# Extensions that are source code (produce edges)
_SOURCE_EXTENSIONS = {
    '.js', '.mjs', '.cjs', '.jsx',
    '.ts', '.mts', '.cts', '.tsx',
}

# Extensions that are assets (never produce edges)
_ASSET_EXTENSIONS = {
    '.css', '.scss', '.sass', '.less',
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico',
    '.json', '.wasm', '.mp4', '.mp3', '.wav',
    '.ttf', '.woff', '.woff2', '.eot',
}


@dataclass
class JsProjectConfig:
    root: Path
    aliases: dict[str, str] = field(default_factory=dict)  # '@/' → 'src/'
    base_url: str = ''
    node_modules_skip: bool = True

    @classmethod
    def detect(cls, root: Path) -> 'JsProjectConfig':
        cfg = cls(root=root)
        cfg._read_tsconfig()
        return cfg

    def _read_tsconfig(self):
        """
        Read path aliases from tsconfig.json or jsconfig.json.
        Follows 'extends' chains so aliases in base configs are included.
        Child aliases win over base aliases (same key = child takes priority).
        """
        for name in ('tsconfig.json', 'jsconfig.json', 'tsconfig.base.json'):
            p = self.root / name
            if not p.exists():
                continue
            try:
                self._read_tsconfig_file(p, visited=set())
                break  # Use first tsconfig found
            except Exception:
                pass

    def _read_tsconfig_file(self, path: Path, visited: set):
        """Recursively read a tsconfig file and any configs it extends."""
        real = str(path.resolve())
        if real in visited:
            return  # Prevent infinite loops
        visited.add(real)

        try:
            data = _read_json_with_comments(path)
        except Exception:
            return

        # Follow 'extends' FIRST so child values override base values
        extends = data.get('extends', '')
        if extends:
            # Resolve relative to current tsconfig's directory
            base_path = (path.parent / extends).resolve()
            # Try with and without .json extension
            for candidate in (base_path, base_path.with_suffix('.json')):
                if candidate.exists():
                    self._read_tsconfig_file(candidate, visited)
                    break

        co = data.get('compilerOptions', {})

        # baseUrl: child overrides base
        base_url = co.get('baseUrl', '')
        if base_url and not self.base_url:
            self.base_url = base_url

        # paths: child aliases override base aliases (child processed after base)
        paths = co.get('paths', {})
        for alias, targets in paths.items():
            if not targets:
                continue
            target = targets[0]
            alias_clean  = alias.rstrip('/*').rstrip('/')
            target_clean = target.rstrip('/*').rstrip('/')
            if alias_clean:
                self.aliases[alias_clean] = target_clean


def _read_json_with_comments(path: Path) -> dict:
    """Parse JSON that may contain // comments and trailing commas (tsconfig style)."""
    text = path.read_text(encoding='utf-8', errors='replace')
    # Strip // comments
    text = re.sub(r'//[^\n]*', '', text)
    # Strip /* */ comments
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


class JsResolver:
    """
    Resolves a JS/TS import string to a project-local file path.

    Returns (path_relative_to_root, kind) where kind is:
      'LOCAL'       — found on disk
      'EXTERNAL'    — node_modules or bare specifier
      'ASSET'       — CSS/image/font, not source code
      'UNKNOWN'     — can't determine
    """

    def __init__(self, root: Path, config: JsProjectConfig):
        self.root   = root
        self.config = config
        self._cache: dict[str, tuple[str | None, str]] = {}

    def resolve(self, raw: str, from_file: Path) -> tuple[Optional[str], str]:
        """
        Returns (resolved_path_or_None, kind).
        resolved_path is relative to root if kind == 'LOCAL'.
        """
        # Asset imports — never a graph edge
        suffix = Path(raw.split('?')[0]).suffix.lower()
        if suffix in _ASSET_EXTENSIONS:
            return None, 'ASSET'

        # Cache key includes from_file for relative imports
        cache_key = f"{from_file}:{raw}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._resolve_uncached(raw, from_file)
        self._cache[cache_key] = result
        return result

    def _resolve_uncached(self, raw: str, from_file: Path) -> tuple[Optional[str], str]:
        # Relative import: './utils', '../core/index'
        if raw.startswith('./') or raw.startswith('../'):
            path = self._resolve_relative(raw, from_file)
            if path:
                return path, 'LOCAL'
            return None, 'UNKNOWN'

        # Alias: '@/components/Button' → 'src/components/Button'
        aliased = self._apply_alias(raw)
        if aliased != raw:
            path = self._resolve_from_root(aliased)
            if path:
                return path, 'LOCAL'
            return None, 'UNKNOWN'

        # baseUrl resolution: if baseUrl set, bare 'components/Button' → 'src/components/Button'
        if self.config.base_url:
            candidate = self.config.base_url.rstrip('/') + '/' + raw
            path = self._resolve_from_root(candidate)
            if path:
                return path, 'LOCAL'

        # Everything else: node_modules / bare specifier
        return None, 'EXTERNAL'

    def _apply_alias(self, raw: str) -> str:
        """Replace alias prefix with actual path."""
        for alias, target in self.config.aliases.items():
            if raw == alias:
                return target
            if raw.startswith(alias + '/'):
                return target + raw[len(alias):]
        return raw

    def _resolve_relative(self, raw: str, from_file: Path) -> Optional[str]:
        """Resolve a './x' or '../x' import from the given file."""
        base_dir = from_file.parent
        # Normalize: './utils' → base_dir/utils
        candidate_base = (base_dir / raw).resolve()

        return self._probe_extensions(candidate_base)

    def _resolve_from_root(self, path_str: str) -> Optional[str]:
        """Resolve a root-relative path string."""
        candidate_base = (self.root / path_str).resolve()
        return self._probe_extensions(candidate_base)

    def _probe_extensions(self, base: Path) -> Optional[str]:
        """
        Given a base path (no extension or wrong extension),
        try all source extensions and index file patterns.
        Returns path relative to root if found, else None.
        """
        # 1. Exact path with existing extension
        if base.suffix in _SOURCE_EXTENSIONS and base.exists():
            return self._rel(base)

        # 2. Try adding each source extension: Button → Button.tsx
        for ext in ('.tsx', '.ts', '.jsx', '.js', '.mts', '.mjs', '.cts', '.cjs'):
            candidate = base.with_suffix(ext)
            if candidate.exists():
                return self._rel(candidate)

        # 3. Try as directory with index file: utils → utils/index.tsx
        for ext in ('.tsx', '.ts', '.jsx', '.js'):
            candidate = base / f'index{ext}'
            if candidate.exists():
                return self._rel(candidate)

        # 4. If path already has an extension that's not in source extensions
        #    (e.g. .css imported for side-effects), skip
        if base.suffix and base.suffix not in _SOURCE_EXTENSIONS:
            return None

        return None

    def _rel(self, path: Path) -> Optional[str]:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return None
