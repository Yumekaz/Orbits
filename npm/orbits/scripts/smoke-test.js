'use strict';

const path = require('node:path');
const { spawnSync } = require('node:child_process');

const packageRoot = path.resolve(__dirname, '..');
const repoRoot = path.resolve(packageRoot, '..', '..');
const bin = path.join(packageRoot, 'bin', 'orbits.js');

const env = {
  ...process.env,
  ORBITS_NO_AUTO_INSTALL: '1',
  PYTHONPATH: [repoRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
};

const syntax = spawnSync(process.execPath, ['--check', bin], { stdio: 'inherit' });
if (syntax.status !== 0) {
  process.exit(syntax.status || 1);
}

const help = spawnSync(process.execPath, [bin, '--help'], { cwd: repoRoot, env, stdio: 'inherit' });
process.exit(help.status || 0);
