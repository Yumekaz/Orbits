"""HTML and CSS dependency extraction for static web projects."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from .base import BaseExtractor, ExtractResult, RawImport
from path_utils import relative_to_root


_URL_RE = re.compile(r'''url\(\s*(?:"([^"]+)"|'([^']+)'|([^)'"\s]+))\s*\)''', re.IGNORECASE)
_CSS_IMPORT_RE = re.compile(
    r'''@import\s+(?:url\(\s*)?(?:"([^"]+)"|'([^']+)'|([^)'"\s;]+))''',
    re.IGNORECASE,
)

_HTML_ATTRS = {
    'a': ('href',),
    'audio': ('src',),
    'embed': ('src',),
    'form': ('action',),
    'iframe': ('src',),
    'img': ('src',),
    'input': ('src',),
    'link': ('href',),
    'object': ('data',),
    'script': ('src',),
    'source': ('src', 'srcset'),
    'track': ('src',),
    'video': ('src', 'poster'),
}


def _line_for(source: str, offset: int) -> int:
    return source[:offset].count('\n') + 1


def _clean_url(value: str) -> str:
    return value.strip().strip('"\'')


def _srcset_urls(value: str) -> list[str]:
    urls: list[str] = []
    for part in value.split(','):
        candidate = part.strip().split()
        if candidate:
            urls.append(candidate[0])
    return urls


class HtmlExtractor(BaseExtractor):
    @property
    def language(self) -> str:
        return 'html'

    @property
    def extensions(self) -> list[str]:
        return ['.html', '.htm']

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        try:
            source = filepath.read_text(encoding='utf-8', errors='replace')
        except (OSError, PermissionError):
            return ExtractResult(read_error=True)

        file_rel = relative_to_root(filepath, root)
        if not file_rel:
            return ExtractResult()

        parser = _HtmlDependencyParser(file_rel)
        try:
            parser.feed(source)
        except Exception:
            return ExtractResult(imports=parser.imports)

        seen: set[tuple[str, int, str]] = set()
        for match in _URL_RE.finditer(source):
            raw = _clean_url(match.group(1) or match.group(2) or match.group(3) or '')
            if not raw:
                continue
            line = _line_for(source, match.start())
            key = (raw, line, 'style_url')
            if key in seen:
                continue
            seen.add(key)
            parser.imports.append(RawImport(file_rel, raw, line, 'style_url', _is_local_hint(raw)))

        return ExtractResult(imports=parser.imports)


class _HtmlDependencyParser(HTMLParser):
    def __init__(self, source_file: str):
        super().__init__(convert_charrefs=True)
        self.source_file = source_file
        self.imports: list[RawImport] = []
        self._seen: set[tuple[str, int, str]] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or '' for name, value in attrs}
        for attr in _HTML_ATTRS.get(tag.lower(), ()):
            value = attr_map.get(attr, '')
            if not value:
                continue
            values = _srcset_urls(value) if attr == 'srcset' else [value]
            for raw_value in values:
                raw = _clean_url(raw_value)
                if not raw:
                    continue
                line, _column = self.getpos()
                kind = f'{tag}:{attr}'
                key = (raw, line, kind)
                if key in self._seen:
                    continue
                self._seen.add(key)
                self.imports.append(RawImport(self.source_file, raw, line, kind, _is_local_hint(raw)))


class CssExtractor(BaseExtractor):
    @property
    def language(self) -> str:
        return 'css'

    @property
    def extensions(self) -> list[str]:
        return ['.css', '.scss', '.sass', '.less']

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        try:
            source = filepath.read_text(encoding='utf-8', errors='replace')
        except (OSError, PermissionError):
            return ExtractResult(read_error=True)

        file_rel = relative_to_root(filepath, root)
        if not file_rel:
            return ExtractResult()

        imports: list[RawImport] = []
        seen: set[tuple[str, int, str]] = set()
        for kind, pattern in (('css_import', _CSS_IMPORT_RE), ('css_url', _URL_RE)):
            for match in pattern.finditer(source):
                raw = _clean_url(match.group(1) or match.group(2) or match.group(3) or '')
                if not raw:
                    continue
                line = _line_for(source, match.start())
                key = (raw, line, kind)
                if key in seen:
                    continue
                seen.add(key)
                imports.append(RawImport(file_rel, raw, line, kind, _is_local_hint(raw)))

        return ExtractResult(imports=imports)


def _is_local_hint(raw: str) -> bool:
    lowered = raw.strip().lower()
    if not lowered or lowered.startswith(('#', 'data:', 'mailto:', 'tel:', 'javascript:')):
        return False
    if re.match(r'^[a-z][a-z0-9+.-]*:', lowered):
        return False
    return True
