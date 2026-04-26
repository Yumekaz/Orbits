"""JavaScript and TypeScript import resolution helpers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from path_utils import relative_to_root


_SOURCE_EXTENSIONS = {'.js', '.mjs', '.cjs', '.jsx', '.ts', '.mts', '.cts', '.tsx'}
_STYLE_EXTENSIONS = {'.css', '.scss', '.sass', '.less'}
_ASSET_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico',
    '.json', '.wasm', '.mp4', '.mp3', '.wav', '.ttf', '.woff', '.woff2', '.eot',
}


@dataclass
class JsPackageInfo:
    name: str
    root: Path
    exports: dict[str, str] = field(default_factory=dict)
    entrypoints: list[str] = field(default_factory=list)


@dataclass
class JsProjectConfig:
    root: Path
    aliases: dict[str, str] = field(default_factory=dict)
    base_url: str = ''
    node_modules_skip: bool = True
    packages: dict[str, JsPackageInfo] = field(default_factory=dict)

    @classmethod
    def detect(cls, root: Path) -> 'JsProjectConfig':
        cfg = cls(root=root)
        cfg._read_tsconfig()
        cfg._read_packages()
        return cfg

    def _read_tsconfig(self):
        for name in ('tsconfig.json', 'jsconfig.json', 'tsconfig.base.json'):
            path = self.root / name
            if not path.exists():
                continue
            try:
                self._read_tsconfig_file(path, visited=set())
                break
            except Exception:
                pass

    def _read_tsconfig_file(self, path: Path, visited: set[str]):
        real = str(path.resolve())
        if real in visited:
            return
        visited.add(real)
        data = _read_json_with_comments(path)
        extends = data.get('extends', '')
        if extends:
            base_path = (path.parent / extends).resolve()
            for candidate in (base_path, base_path.with_suffix('.json')):
                if candidate.exists():
                    self._read_tsconfig_file(candidate, visited)
                    break
        compiler_options = data.get('compilerOptions', {})
        base_url = compiler_options.get('baseUrl', '')
        if base_url and not self.base_url:
            self.base_url = base_url
        for alias, targets in compiler_options.get('paths', {}).items():
            if not targets:
                continue
            self.aliases[alias.rstrip('/*').rstrip('/')] = targets[0].rstrip('/*').rstrip('/')

    def _read_packages(self) -> None:
        for manifest in _find_package_json_files(self.root):
            try:
                data = _read_json_with_comments(manifest)
            except Exception:
                continue
            name = data.get('name', '')
            if not name:
                continue
            exports = _normalize_exports(data.get('exports', {}))
            entrypoints = [value for value in [data.get('types'), data.get('module'), data.get('main')] if isinstance(value, str)]
            self.packages[name] = JsPackageInfo(name=name, root=manifest.parent, exports=exports, entrypoints=entrypoints)


def _find_package_json_files(root: Path) -> list[Path]:
    manifests: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in {'node_modules', '.git'} and not d.startswith('.')]
        if 'package.json' in filenames:
            manifests.append(Path(dirpath) / 'package.json')
    return manifests


def _normalize_exports(exports) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if isinstance(exports, str):
        normalized['.'] = exports
        return normalized
    if isinstance(exports, dict):
        for key, value in exports.items():
            if isinstance(value, str):
                normalized[key] = value
            elif isinstance(value, dict):
                for field in ('types', 'import', 'require', 'default'):
                    if isinstance(value.get(field), str):
                        normalized[key] = value[field]
                        break
    return normalized


def _read_json_with_comments(path: Path) -> dict:
    text = path.read_text(encoding='utf-8', errors='replace')
    text = re.sub(r'//[^\n]*', '', text)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r',\s*([}\]])', r'\1', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


class JsResolver:
    def __init__(self, root: Path, config: JsProjectConfig):
        self.root = root
        self.config = config
        self._cache: dict[str, tuple[str | None, str]] = {}

    def resolve(self, raw: str, from_file: Path) -> tuple[Optional[str], str]:
        suffix = Path(raw.split('?', 1)[0]).suffix.lower()
        if suffix in _STYLE_EXTENSIONS:
            path = self._resolve_relative_style(raw, from_file)
            return (path, 'LOCAL') if path else (None, 'ASSET')
        if suffix in _ASSET_EXTENSIONS:
            path = self._resolve_relative_asset(raw, from_file)
            return (path, 'ASSET') if path else (None, 'ASSET')

        cache_key = f'{from_file}:{raw}'
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._resolve_uncached(raw, from_file)
        self._cache[cache_key] = result
        return result

    def _resolve_uncached(self, raw: str, from_file: Path) -> tuple[Optional[str], str]:
        if raw.startswith('./') or raw.startswith('../'):
            path = self._resolve_relative(raw, from_file)
            return (path, 'LOCAL') if path else (None, 'UNKNOWN')

        aliased = self._apply_alias(raw)
        if aliased != raw:
            path = self._resolve_from_root(aliased)
            return (path, 'LOCAL') if path else (None, 'UNKNOWN')

        if self.config.base_url:
            candidate = self.config.base_url.rstrip('/') + '/' + raw
            path = self._resolve_from_root(candidate)
            if path:
                return path, 'LOCAL'

        path = self._resolve_local_package(raw)
        if path:
            return path, 'LOCAL'

        return None, 'EXTERNAL'

    def _apply_alias(self, raw: str) -> str:
        for alias, target in self.config.aliases.items():
            if raw == alias:
                return target
            if raw.startswith(alias + '/'):
                return target + raw[len(alias):]
        return raw

    def _resolve_relative(self, raw: str, from_file: Path) -> Optional[str]:
        return self._probe_extensions((from_file.parent / raw).resolve())

    def _resolve_relative_style(self, raw: str, from_file: Path) -> Optional[str]:
        clean = raw.split('?', 1)[0].split('#', 1)[0]
        if not clean.startswith(('./', '../', '/')):
            return None
        base = (self.root / clean.lstrip('/')).resolve() if clean.startswith('/') else (from_file.parent / clean).resolve()
        if base.suffix.lower() in _STYLE_EXTENSIONS and base.exists():
            return self._rel(base)
        return None

    def _resolve_relative_asset(self, raw: str, from_file: Path) -> Optional[str]:
        clean = raw.split('?', 1)[0].split('#', 1)[0]
        if not clean.startswith(('./', '../', '/')):
            return None
        base = (self.root / clean.lstrip('/')).resolve() if clean.startswith('/') else (from_file.parent / clean).resolve()
        if base.exists() and base.is_file():
            return self._rel(base)
        return None

    def _resolve_from_root(self, path_str: str) -> Optional[str]:
        return self._probe_extensions((self.root / path_str).resolve())

    def _resolve_local_package(self, raw: str) -> Optional[str]:
        package_name, subpath = self._split_package_specifier(raw)
        package = self.config.packages.get(package_name)
        if not package:
            return None

        export_key = '.' if not subpath else './' + subpath
        if export_key in package.exports:
            export_target = package.exports[export_key].lstrip('./')
            resolved = self._probe_extensions((package.root / export_target).resolve())
            if resolved:
                return resolved

        if not subpath:
            for entry in package.entrypoints:
                resolved = self._probe_extensions((package.root / entry.lstrip('./')).resolve())
                if resolved:
                    return resolved
            for fallback in ('src/index', 'index'):
                resolved = self._probe_extensions((package.root / fallback).resolve())
                if resolved:
                    return resolved
            return None

        return self._probe_extensions((package.root / subpath).resolve())

    def _split_package_specifier(self, raw: str) -> tuple[str, str]:
        if raw.startswith('@'):
            parts = raw.split('/')
            if len(parts) >= 2:
                name = '/'.join(parts[:2])
                return name, '/'.join(parts[2:])
        parts = raw.split('/')
        return parts[0], '/'.join(parts[1:])

    def _probe_extensions(self, base: Path) -> Optional[str]:
        if base.suffix in _SOURCE_EXTENSIONS and base.exists():
            return self._rel(base)
        for ext in ('.tsx', '.ts', '.jsx', '.js', '.mts', '.mjs', '.cts', '.cjs'):
            candidate = base.with_suffix(ext)
            if candidate.exists():
                return self._rel(candidate)
        for ext in ('.tsx', '.ts', '.jsx', '.js', '.mts', '.mjs', '.cts', '.cjs'):
            candidate = base / f'index{ext}'
            if candidate.exists():
                return self._rel(candidate)
        if base.suffix and base.suffix not in _SOURCE_EXTENSIONS:
            return None
        return None

    def _rel(self, path: Path) -> Optional[str]:
        return relative_to_root(path, self.root)
