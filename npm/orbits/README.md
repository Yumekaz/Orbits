# @yumekaz/orbits

npm wrapper for [Orbits](https://github.com/Yumekaz/Orbits), a codebase dependency graph analyzer and dead-code detector.

```bash
npm install -g @yumekaz/orbits
orbits scan . --open
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
- `ORBITS_PIP_PACKAGE=orbits-codebase==0.1.1` overrides the PyPI package spec.
