"""
extractors/base.py - Orbits Phase 3

The contract every language extractor must implement.
The graph engine and analyzer only talk to this interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RawImport:
    """
    A single import statement as seen in source code.
    The resolver will later map 'raw' to an actual file path.
    """
    source_file: str    # relative path of the file doing the import
    raw:         str    # the import string as written, e.g. './utils' or 'os'
    line:        int    # line number in source
    kind:        str    # 'import' | 'import_from' | 'require' | 'include'
    is_relative: bool   # True for './x', '../x', 'from . import x'
    module:      str = ''    # parent module for import-from statements
    imported_name: str = ''  # imported symbol/module for import-from statements
    level:       int = 0     # Python relative import level, else 0


@dataclass
class ExtractResult:
    """What an extractor returns for one file."""
    imports:      list[RawImport] = field(default_factory=list)
    syntax_error: bool            = False
    read_error:   bool            = False


class BaseExtractor(ABC):
    """
    Every language extractor inherits from this.
    Only extract() needs to be implemented.
    """

    @property
    @abstractmethod
    def language(self) -> str:
        """Lowercase language name, e.g. 'python', 'javascript'"""
        ...

    @property
    @abstractmethod
    def extensions(self) -> list[str]:
        """File extensions this extractor handles, e.g. ['.js', '.mjs']"""
        ...

    def can_handle(self, filepath: Path) -> bool:
        return filepath.suffix.lower() in self.extensions

    @abstractmethod
    def extract(self, filepath: Path, root: Path) -> ExtractResult:
        """
        Parse a single file and return all its raw imports.
        Must NEVER raise - catch all errors and return them in ExtractResult.
        """
        ...
