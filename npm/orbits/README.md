# @yumekaz/orbits

npm wrapper for [Orbits](https://github.com/Yumekaz/Orbits), a codebase map, dependency graph analyzer, and cleanup-confidence CLI.

```bash
npm install -g @yumekaz/orbits
orbits scan . --open
orbits cleanup-plan .
orbits scale-proof .
orbits language-coverage .
```

This package installs the Python package `orbits-codebase` from PyPI during `postinstall`, then exposes the `orbits` command through npm.

Requirements:

- Node.js 18+
- Python 3.10+
- `pip`

Useful environment variables:

- `ORBITS_PYTHON=/path/to/python` chooses the Python executable.
- `ORBITS_SKIP_PIP_INSTALL=1` skips the npm postinstall pip step.
- `ORBITS_NO_AUTO_INSTALL=1` prevents first-run auto-install.
- `ORBITS_PIP_PACKAGE=orbits-codebase==0.1.2` overrides the PyPI package spec.
