'use strict';

const { spawnSync } = require('node:child_process');

const PYPI_PACKAGE = process.env.ORBITS_PIP_PACKAGE || 'orbits-codebase==0.1.0';

function pythonCandidates() {
  const candidates = [];
  if (process.env.ORBITS_PYTHON) {
    candidates.push([process.env.ORBITS_PYTHON]);
  }

  if (process.platform === 'win32') {
    candidates.push(['py', '-3']);
    candidates.push(['python']);
    candidates.push(['python3']);
  } else {
    candidates.push(['python3']);
    candidates.push(['python']);
  }

  return candidates;
}

function checkPython(candidate) {
  const [command, ...baseArgs] = candidate;
  const result = spawnSync(
    command,
    [...baseArgs, '-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'],
    { stdio: 'ignore' }
  );
  return result.status === 0;
}

function findPython() {
  for (const candidate of pythonCandidates()) {
    if (checkPython(candidate)) {
      return candidate;
    }
  }
  return null;
}

function hasOrbitsPackage(python) {
  const [command, ...baseArgs] = python;
  const result = spawnSync(
    command,
    [
      ...baseArgs,
      '-c',
      "from importlib import metadata; metadata.version('orbits-codebase')",
    ],
    { stdio: 'ignore' }
  );
  return result.status === 0;
}

function canRunOrbitsModule(python) {
  const [command, ...baseArgs] = python;
  const result = spawnSync(
    command,
    [...baseArgs, '-c', "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('orbits') else 1)"],
    { stdio: 'ignore' }
  );
  return result.status === 0;
}

function installOrbits(python, stdio = 'inherit') {
  const [command, ...baseArgs] = python;
  return spawnSync(command, [...baseArgs, '-m', 'pip', 'install', '--upgrade', PYPI_PACKAGE], {
    stdio,
  });
}

function runOrbits(python, args, stdio = 'inherit') {
  const [command, ...baseArgs] = python;
  return spawnSync(command, [...baseArgs, '-m', 'orbits', ...args], { stdio });
}

function commandLine(candidate) {
  return candidate.map((part) => (/\s/.test(part) ? JSON.stringify(part) : part)).join(' ');
}

module.exports = {
  PYPI_PACKAGE,
  canRunOrbitsModule,
  commandLine,
  findPython,
  hasOrbitsPackage,
  installOrbits,
  runOrbits,
};
