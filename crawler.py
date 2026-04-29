"""
crawler.py — Recursive file system walker.
Skips all the noise: venv, caches, build artifacts, VCS folders.
"""

import os
from pathlib import Path
from typing import Iterator

# Directories that are NEVER your code
SKIP_DIRS = {
    # Python virtualenvs
    'venv', '.venv', 'env', '.env', 'virtualenv',
    # Python caches / artifacts
    '__pycache__', '.pytest_cache', '.mypy_cache',
    '.ruff_cache', '.tox', '.eggs', 'htmlcov',
    '*.egg-info',
    # JS / Node
    'node_modules', '.yarn', '.npm',
    # Build outputs
    'dist', 'build', 'out', 'target', 'bin', 'obj',
    # VCS
    '.git', '.hg', '.svn',
    # IDE
    '.idea', '.vscode',
    # OS junk
    '__MACOSX', '.DS_Store',
    # Coverage / reports
    '.coverage', 'coverage',
    # Compiled C extensions
    'site-packages', 'lib64',
}

# File extensions that are never source code we care about
SKIP_EXTENSIONS = {
    '.pyc', '.pyo', '.pyd',
    '.so', '.dll', '.dylib',
    '.egg', '.whl',
    '.class',
    '.o', '.a',
    '.jpg', '.jpeg', '.png', '.gif', '.svg', '.ico', '.webp',
    '.mp3', '.mp4', '.wav', '.avi',
    '.zip', '.tar', '.gz', '.rar', '.7z',
    '.pdf', '.docx', '.xlsx',
    '.md', '.mdx', '.rst', '.adoc', '.txt',
    '.log', '.out', '.err',
    '.db', '.sqlite', '.sqlite3',
    '.lock',  # package-lock.json is useful but not for Phase 0
}


def crawl(root: str | Path) -> Iterator[Path]:
    """
    Walk a directory tree and yield every file that could be source code.
    Skips known noise directories and binary file extensions.
    """
    root = Path(root).resolve()

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        # Prune in-place — this tells os.walk not to descend into these
        dirnames[:] = sorted([
            d for d in dirnames
            if d not in SKIP_DIRS
            and not d.startswith('.')
            and not d.endswith('.egg-info')
        ])

        for filename in filenames:
            filepath = Path(dirpath) / filename

            if filepath.suffix in SKIP_EXTENSIONS:
                continue

            # Skip hidden files
            if filename.startswith('.'):
                continue

            yield filepath


def crawl_by_language(root: str | Path) -> dict[str, list[Path]]:
    """
    Same as crawl() but buckets files by language.
    Legacy helper used by earlier phases. Phase 3 uses lang_dispatch.crawl_all().
    """
    LANGUAGE_MAP = {
        '.py': 'python',
        '.pyi': 'python',
        # Future phases will add:
        # '.js': 'javascript',
        # '.ts': 'typescript',
        # '.jsx': 'javascript',
        # '.tsx': 'typescript',
        # '.go': 'go',
        # '.cpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp',
        # '.c': 'c',
        # '.h': 'c_header', '.hpp': 'cpp_header',
        # '.kt': 'kotlin',
        # '.java': 'java',
        # '.rs': 'rust',
    }

    buckets: dict[str, list[Path]] = {}

    for filepath in crawl(root):
        lang = LANGUAGE_MAP.get(filepath.suffix)
        if lang:
            buckets.setdefault(lang, []).append(filepath)

    return buckets
