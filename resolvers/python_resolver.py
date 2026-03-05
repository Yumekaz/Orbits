"""
resolvers/python_resolver.py — Orbits Phase 3

Wraps Phase 2 resolver.py — re-exports what the dispatch layer needs.
Python resolution is already solid; this just makes it fit the new interface.
"""
import sys
from pathlib import Path

# Phase 2 resolver lives at root level — import it directly
sys.path.insert(0, str(Path(__file__).parent.parent))
from resolver import PythonResolver, ProjectConfig, ImportKind

__all__ = ['PythonResolver', 'ProjectConfig', 'ImportKind']
