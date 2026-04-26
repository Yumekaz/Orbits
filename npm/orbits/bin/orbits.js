#!/usr/bin/env node
'use strict';

const {
  PYPI_PACKAGE,
  canRunOrbitsModule,
  commandLine,
  findPython,
  hasOrbitsPackage,
  installOrbits,
  runOrbits,
} = require('../lib/python');

const python = findPython();
if (!python) {
  console.error('Orbits requires Python 3.10+.');
  console.error('Install Python, then run: npm install -g @yumekaz/orbits');
  process.exit(1);
}

if (!hasOrbitsPackage(python)) {
  if (process.env.ORBITS_NO_AUTO_INSTALL === '1') {
    if (!canRunOrbitsModule(python)) {
      console.error(`Python package ${PYPI_PACKAGE} is not installed for ${commandLine(python)}.`);
      console.error(`Run: ${commandLine(python)} -m pip install --upgrade ${PYPI_PACKAGE}`);
      process.exit(1);
    }
  } else {
    console.error(`Installing ${PYPI_PACKAGE} for ${commandLine(python)}...`);
    const install = installOrbits(python);
    if (install.status !== 0) {
      console.error(`Failed to install ${PYPI_PACKAGE}.`);
      process.exit(install.status || 1);
    }
  }
}

const result = runOrbits(python, process.argv.slice(2));
if (result.signal) {
  process.kill(process.pid, result.signal);
}
process.exit(result.status || 0);
