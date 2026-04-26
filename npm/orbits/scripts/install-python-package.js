'use strict';

const {
  PYPI_PACKAGE,
  commandLine,
  findPython,
  hasOrbitsPackage,
  installOrbits,
} = require('../lib/python');

if (process.env.ORBITS_SKIP_PIP_INSTALL === '1') {
  console.log('Skipping Orbits Python package install because ORBITS_SKIP_PIP_INSTALL=1.');
  process.exit(0);
}

const python = findPython();
if (!python) {
  console.error('Orbits npm wrapper requires Python 3.10+.');
  console.error('Install Python, then run: npm install -g @yumekaz/orbits');
  process.exit(1);
}

if (hasOrbitsPackage(python)) {
  console.log(`Orbits Python package is already available via ${commandLine(python)}.`);
  process.exit(0);
}

console.log(`Installing ${PYPI_PACKAGE} via ${commandLine(python)}...`);
const result = installOrbits(python);
if (result.status !== 0) {
  console.error(`Failed to install ${PYPI_PACKAGE}.`);
  console.error(`Manual fallback: ${commandLine(python)} -m pip install --upgrade ${PYPI_PACKAGE}`);
  process.exit(result.status || 1);
}
