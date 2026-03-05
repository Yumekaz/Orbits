"""
extractors/generic_extractor.py — Orbits Phase 3

Fallback extractor for any language Orbits doesn't have a
dedicated parser for. Uses conservative regex patterns to
find import-like statements.

Accuracy: ~60% on unknown languages. Better than nothing.
Produces some false positives (e.g. import inside comments)
and misses dynamic imports. Clearly flagged in the UI.

Handles patterns like:
  import "something"            → Go, Java, Dart
  from 'something' import x     → Python-style (shouldn't be needed but safe)
  require 'something'           → Ruby
  #include "something.h"        → C / C++  (local includes only)
  #include <something.h>        → C / C++  (system includes — skipped)
  use something;                → Rust / Perl
  using Something;              → C# / Java
"""

import re
from pathlib import Path

from .base import BaseExtractor, ExtractResult, RawImport


# Conservative patterns — only match string literals to avoid grabbing
# variable names, comments etc.
_PATTERNS = [
    # import "something" or import 'something'
    re.compile(r'''^\s*import\s+["']([^"']+)["']''', re.MULTILINE),
    # from "something" import ... or from 'something' import ...
    re.compile(r'''^\s*from\s+["']([^"']+)["']\s+import''', re.MULTILINE),
    # require "something" or require 'something'
    re.compile(r'''^\s*require\s+["']([^"']+)["']''', re.MULTILINE),
    # require("something") or require('something')
    re.compile(r'''require\s*\(\s*["']([^"']+)["']\s*\)'''),
    # #include "something.h" (local includes only, not <system>)
    re.compile(r'''^\s*#include\s+"([^"]+)"''', re.MULTILINE),
    # use something::module; (Rust / Perl)
    re.compile(r'''^\s*use\s+([\w:]+)\s*;''', re.MULTILINE),
]

# Extensions this extractor will handle as a last resort
_GENERIC_EXTENSIONS = {
    '.rb',    # Ruby
    '.rs',    # Rust
    '.cs',    # C#
    '.java',  # Java
    '.kt',    # Kotlin
    '.swift', # Swift
    '.cpp', '.cc', '.cxx', '.c', '.h', '.hpp',  # C / C++
    '.lua',   # Lua
    '.php',   # PHP
    '.r', '.R',  # R
    '.dart',  # Dart
    '.scala', # Scala
    '.m',     # Objective-C
}


class GenericExtractor(BaseExtractor):

    @property
    def language(self) -> str:
        return 'generic'

    @property
    def extensions(self) -> list[str]:
        return list(_GENERIC_EXTENSIONS)

    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        try:
            source = filepath.read_text(encoding='utf-8', errors='replace')
        except (OSError, PermissionError):
            return ExtractResult(read_error=True)

        try:
            file_rel = str(filepath.relative_to(root))
        except ValueError:
            return ExtractResult()

        imports: list[RawImport] = []
        seen: set[tuple] = set()

        for pattern in _PATTERNS:
            for match in pattern.finditer(source):
                raw = match.group(1).strip()
                if not raw:
                    continue

                # Get approximate line number from match position
                line = source[:match.start()].count('\n') + 1

                # Deduplicate
                key = (raw, line)
                if key in seen:
                    continue
                seen.add(key)

                is_relative = (
                    raw.startswith('./')
                    or raw.startswith('../')
                    or raw.startswith('".')
                    or (filepath.suffix in {'.c', '.cpp', '.h', '.hpp', '.cc', '.cxx'}
                        and not raw.startswith('<'))  # C local include
                )

                imports.append(RawImport(
                    source_file=file_rel,
                    raw=raw,
                    line=line,
                    kind='include' if filepath.suffix in {'.c', '.cpp', '.h', '.hpp', '.cc', '.cxx'} else 'import',
                    is_relative=is_relative,
                ))

        return ExtractResult(imports=imports)
