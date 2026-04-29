"""
worker.py - Orbits Phase 3 parallel worker.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from language_coverage import annotate_node_language
from path_utils import relative_to_root


@dataclass
class WorkerResult:
    language: str
    extra_nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    cache_updates: dict[str, dict] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)
    syntax_errors: int = 0
    error: str = ''


def run_worker(language: str, file_strs: list[str], root_str: str, cache_data: dict[str, dict], resolver_config: dict) -> WorkerResult:
    try:
        return _run(language, file_strs, root_str, cache_data, resolver_config)
    except Exception as exc:
        return WorkerResult(language=language, error=str(exc))


def _run(language, file_strs, root_str, cache_data, resolver_config):
    root = Path(root_str)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    from extractors.c_family_extractor import CExtractor, CppExtractor
    from extractors.generic_extractor import GenericExtractor
    from extractors.go_extractor import GoExtractor
    from extractors.js_extractor import JsExtractor, TsExtractor, TsxExtractor
    from extractors.jvm_extractor import JavaExtractor, KotlinExtractor
    from extractors.python_extractor import PythonExtractor
    from extractors.web_extractor import CssExtractor, HtmlExtractor

    extractor_map = {
        'python': PythonExtractor(),
        'javascript': JsExtractor(),
        'typescript': TsExtractor(),
        'tsx': TsxExtractor(),
        'html': HtmlExtractor(),
        'css': CssExtractor(),
        'go': GoExtractor(),
        'c': CExtractor(),
        'cpp': CppExtractor(),
        'java': JavaExtractor(),
        'kotlin': KotlinExtractor(),
        'rust': GenericExtractor(),
        'csharp': GenericExtractor(),
        'php': GenericExtractor(),
        'ruby': GenericExtractor(),
        'json': GenericExtractor(),
        'yaml': GenericExtractor(),
        'toml': GenericExtractor(),
        'dockerfile': GenericExtractor(),
        'docker-compose': GenericExtractor(),
        'makefile': GenericExtractor(),
        'shell': GenericExtractor(),
        'sql': GenericExtractor(),
        'github-actions': GenericExtractor(),
        'generic': GenericExtractor(),
        'unknown': GenericExtractor(),
    }

    extractor = extractor_map.get(language)
    if extractor is None:
        return WorkerResult(language=language, error=f'Unknown language: {language}')

    resolver = _make_resolver(language, root, resolver_config)
    result = WorkerResult(language=language)
    result.stats = {'local': 0, 'stdlib': 0, 'third_party': 0, 'external': 0, 'unknown': 0}
    known_nodes: set[str] = set(resolver_config.get('all_node_ids', []))

    for path_str in file_strs:
        filepath = Path(path_str)
        rel = relative_to_root(filepath, root)
        if not rel:
            continue
        try:
            stat = filepath.stat()
        except OSError:
            continue

        cached = cache_data.get(rel)
        if cached and cached.get('mtime') == stat.st_mtime and cached.get('size') == stat.st_size:
            for imp_dict in cached['imports']:
                _resolve_and_add(imp_dict, language, filepath, root, resolver, known_nodes, result)
            continue

        extract_result = extractor.extract(filepath, root)
        if extract_result.syntax_error:
            result.syntax_errors += 1
            continue

        serialized = [
            {
                'source_file': imp.source_file,
                'raw': imp.raw,
                'line': imp.line,
                'kind': imp.kind,
                'is_relative': imp.is_relative,
                'module': imp.module,
                'imported_name': imp.imported_name,
                'level': imp.level,
            }
            for imp in extract_result.imports
        ]
        result.cache_updates[rel] = {'mtime': stat.st_mtime, 'size': stat.st_size, 'imports': serialized}

        for imp_dict in serialized:
            _resolve_and_add(imp_dict, language, filepath, root, resolver, known_nodes, result)

    return result


def _resolve_and_add(imp_dict, language, filepath, root, resolver, known_nodes, result: WorkerResult):
    resolved_paths, kind = resolver(imp_dict, language, filepath, root)
    kind_key = kind.lower()
    result.stats[kind_key] = result.stats.get(kind_key, 0) + 1

    if kind not in ('LOCAL', 'ASSET') or not resolved_paths:
        return

    if isinstance(resolved_paths, str):
        targets = [resolved_paths]
    else:
        targets = list(resolved_paths)

    for target in targets:
        if not target:
            continue
        if target in known_nodes or kind == 'ASSET':
            if target not in known_nodes:
                _add_asset_node(result, root, target)
            result.edges.append({
                'source': imp_dict['source_file'],
                'target': target,
                'type': imp_dict['kind'],
                'line': imp_dict['line'],
                'language': language,
            })


def _add_asset_node(result: WorkerResult, root: Path, relpath: str) -> None:
    if relpath in result.extra_nodes:
        return
    path = (root / relpath).resolve()
    try:
        stat = path.stat()
    except OSError:
        return
    result.extra_nodes[relpath] = {
        'id': relpath,
        'filepath': relpath,
        'name': path.name,
        'language': 'asset',
        'asset': True,
        'size': stat.st_size,
        'mtime': round(stat.st_mtime),
        'dir': relative_to_root(path.parent, root) if path.parent != root else '.',
    }
    annotate_node_language(result.extra_nodes[relpath])


def _make_resolver(language: str, root: Path, config: dict):
    if language == 'python':
        from resolver import ProjectConfig, PythonResolver

        py_cfg = ProjectConfig(root=root)
        py_cfg.src_dirs = [root / d for d in config.get('py_src_dirs', [])]
        if root not in py_cfg.src_dirs:
            py_cfg.src_dirs.insert(0, root)
        py_cfg.third_party = set(config.get('py_third_party', []))
        py_cfg.package_name = config.get('py_package_name', '')
        resolver = PythonResolver(root, py_cfg)

        def resolve_py(imp_dict, lang, filepath, root):
            if imp_dict.get('kind') == 'import_from':
                resolved = resolver.resolve_from_import(
                    module_name=imp_dict.get('module', ''),
                    attr_name=imp_dict.get('imported_name', ''),
                    level=imp_dict.get('level', 0),
                    from_file=filepath,
                )
            else:
                resolved = resolver.resolve(imp_dict.get('raw', ''), level=imp_dict.get('level', 0), from_file=filepath)
            return resolved.path, resolved.kind

        return resolve_py

    if language in ('javascript', 'typescript', 'tsx'):
        from resolvers.js_resolver import JsProjectConfig, JsResolver

        js_cfg = JsProjectConfig(root=root)
        js_cfg.aliases = config.get('js_aliases', {})
        js_cfg.base_url = config.get('js_base_url', '')
        js_cfg.packages = js_cfg.detect(root).packages
        resolver = JsResolver(root, js_cfg)

        def resolve_js(imp_dict, lang, filepath, root):
            path, kind = resolver.resolve(imp_dict.get('raw', ''), from_file=filepath)
            if kind == 'EXTERNAL':
                kind = 'THIRD_PARTY'
            return path, kind

        return resolve_js

    if language in ('html', 'css'):
        resolver = WebResolver(root)
        return lambda imp_dict, lang, filepath, root: resolver.resolve(imp_dict.get('raw', ''), from_file=filepath)

    if language == 'go':
        from resolvers.go_resolver import GoResolver

        resolver = GoResolver(root)
        return lambda imp_dict, lang, filepath, root: resolver.resolve(imp_dict.get('raw', ''))

    if language in ('c', 'cpp'):
        from resolvers.c_family_resolver import CFamilyResolver, CProjectConfig

        c_cfg = CProjectConfig(root=root)
        c_cfg.include_dirs = [root / d for d in config.get('c_include_dirs', [])]
        if root not in c_cfg.include_dirs:
            c_cfg.include_dirs.insert(0, root)
        resolver = CFamilyResolver(root, c_cfg)
        return lambda imp_dict, lang, filepath, root: resolver.resolve(imp_dict.get('raw', ''), filepath, imp_dict.get('kind', 'include'))

    if language in ('java', 'kotlin'):
        from resolvers.jvm_resolver import JvmProjectConfig, JvmResolver

        jvm_cfg = JvmProjectConfig.detect(root)
        configured_roots = [root / d for d in config.get('jvm_src_roots', [])]
        if configured_roots:
            jvm_cfg.src_roots = configured_roots
            jvm_cfg.package_files = {}
            jvm_cfg.symbol_files = {}
            jvm_cfg._index_sources()
        resolver = JvmResolver(root, jvm_cfg)
        return lambda imp_dict, lang, filepath, root: resolver.resolve(imp_dict.get('raw', ''))

    partial_resolver = PartialLanguageResolver(root)

    def resolve_generic(imp_dict, lang, filepath, root):
        raw = imp_dict.get('raw', '')
        return partial_resolver.resolve(raw, language=lang, from_file=filepath, kind=imp_dict.get('kind', 'import'))

    return resolve_generic


class WebResolver:
    source_extensions = {
        '.html', '.htm',
        '.css', '.scss', '.sass', '.less',
        '.js', '.mjs', '.cjs', '.jsx',
        '.ts', '.mts', '.cts', '.tsx',
        '.py', '.pyi', '.go',
        '.c', '.h', '.hh', '.hpp', '.hxx', '.cc', '.cpp', '.cxx',
        '.java', '.kt', '.kts',
        '.rs', '.cs', '.php', '.rb',
        '.json', '.jsonc', '.yaml', '.yml', '.toml',
        '.sql', '.sh', '.bash', '.zsh', '.fish', '.ps1', '.mk',
    }
    asset_extensions = {
        '.avif', '.bmp', '.gif', '.ico', '.jpeg', '.jpg', '.png', '.svg', '.webp',
        '.eot', '.otf', '.ttf', '.woff', '.woff2',
        '.json', '.map', '.pdf', '.txt', '.xml',
        '.mp3', '.mp4', '.ogg', '.wav', '.webm',
        '.wasm',
    }

    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, raw: str, from_file: Path) -> tuple[str | None, str]:
        cleaned = self._clean(raw)
        if not cleaned:
            return None, 'UNKNOWN'
        if self._is_external(cleaned):
            return None, 'EXTERNAL'

        candidates = self._candidate_paths(cleaned, from_file)
        for candidate in candidates:
            probed = self._probe(candidate)
            if not probed:
                continue
            rel = relative_to_root(probed, self.root)
            if not rel:
                continue
            rel = rel.replace('\\', '/')
            suffix = probed.suffix.lower()
            if suffix in self.source_extensions:
                return rel, 'LOCAL'
            if suffix in self.asset_extensions:
                return rel, 'ASSET'
            return rel, 'ASSET'
        return None, 'UNKNOWN'

    def _clean(self, raw: str) -> str:
        value = unquote(str(raw or '').strip().strip('"\''))
        if not value or value.startswith('#'):
            return ''
        parsed = urlparse(value)
        if parsed.scheme and parsed.scheme.lower() != 'file':
            return value
        path = parsed.path or value
        return path.replace('\\', '/').strip()

    def _is_external(self, value: str) -> bool:
        lowered = value.lower()
        if lowered.startswith(('//', 'data:', 'mailto:', 'tel:', 'javascript:', 'blob:')):
            return True
        parsed = urlparse(value)
        return bool(parsed.scheme and parsed.scheme.lower() not in {'file'})

    def _candidate_paths(self, value: str, from_file: Path) -> list[Path]:
        if value.startswith('/'):
            return [(self.root / value.lstrip('/')).resolve()]
        path = Path(value)
        if path.is_absolute():
            return [path.resolve()]
        return [(from_file.parent / value).resolve()]

    def _probe(self, base: Path) -> Path | None:
        if base.exists():
            if base.is_dir():
                for name in ('index.html', 'index.htm'):
                    candidate = base / name
                    if candidate.exists():
                        return candidate
                return None
            return base
        if not base.suffix:
            for suffix in (
                '.html', '.htm', '.css', '.js', '.mjs', '.ts', '.tsx',
                '.py', '.go', '.rs', '.cs', '.php', '.rb',
                '.sql', '.sh', '.yml', '.yaml', '.toml',
            ):
                candidate = base.with_suffix(suffix)
                if candidate.exists():
                    return candidate
        return None


class PartialLanguageResolver:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.web = WebResolver(root)

    def resolve(self, raw: str, language: str, from_file: Path, kind: str = 'import') -> tuple[str | None, str]:
        cleaned = str(raw or '').strip().strip('"\'')
        if not cleaned:
            return None, 'UNKNOWN'
        if language in {'json', 'yaml', 'toml'}:
            return None, 'UNKNOWN'
        if language == 'rust':
            return self._resolve_rust(cleaned, from_file, kind)
        if language == 'csharp':
            return self._resolve_csharp(cleaned)
        if language in {'ruby', 'php', 'shell', 'sql', 'makefile', 'dockerfile', 'docker-compose', 'github-actions', 'generic', 'unknown'}:
            return self._resolve_path_like(cleaned, from_file)
        return self._resolve_path_like(cleaned, from_file)

    def _resolve_path_like(self, raw: str, from_file: Path) -> tuple[str | None, str]:
        rel, kind = self.web.resolve(raw, from_file=from_file)
        if rel:
            return rel, kind
        if raw.startswith(('http://', 'https://', 'git@')) or '://' in raw:
            return None, 'EXTERNAL'
        return None, 'UNKNOWN'

    def _resolve_rust(self, raw: str, from_file: Path, kind: str) -> tuple[str | None, str]:
        if kind == 'module':
            module = raw.replace('::', '/').strip('/')
            candidates = [
                from_file.parent / f'{module}.rs',
                from_file.parent / module / 'mod.rs',
            ]
            return self._first_existing(candidates)

        cleaned = raw
        for prefix in ('crate::', 'self::', 'super::'):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
                break
        parts = [part for part in cleaned.split('::') if part and part != '*']
        if not parts:
            return None, 'UNKNOWN'
        module_path = Path(*parts)
        roots = [self.root]
        if (self.root / 'src').is_dir():
            roots.insert(0, self.root / 'src')
        if raw.startswith('super::'):
            roots.insert(0, from_file.parent.parent)
        elif raw.startswith('self::'):
            roots.insert(0, from_file.parent)
        candidates = []
        for base in roots:
            candidates.extend([base / f'{module_path}.rs', base / module_path / 'mod.rs'])
        return self._first_existing(candidates)

    def _resolve_csharp(self, raw: str) -> tuple[str | None, str]:
        if raw.startswith(('System', 'Microsoft')):
            return None, 'STDLIB'
        symbol = raw.split('.')[-1]
        if not symbol:
            return None, 'UNKNOWN'
        needle_set = (f'class {symbol}', f'interface {symbol}', f'record {symbol}', f'struct {symbol}')
        for path in self.root.rglob('*.cs'):
            try:
                text = path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            if any(needle in text for needle in needle_set):
                rel = relative_to_root(path, self.root)
                if rel:
                    return rel, 'LOCAL'
        return None, 'UNKNOWN'

    def _first_existing(self, candidates: list[Path]) -> tuple[str | None, str]:
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                rel = relative_to_root(candidate, self.root)
                if rel:
                    return rel, 'LOCAL'
        return None, 'UNKNOWN'
