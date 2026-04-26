import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const packageRoot = path.join(repoRoot, 'npm', 'orbits');
const npmExecPath = process.env.npm_execpath;
const command = npmExecPath ? process.execPath : 'npm';
const args = npmExecPath
  ? [npmExecPath, '--cache', path.join(repoRoot, '.npm-cache'), 'pack', '--dry-run']
  : ['--cache', path.join(repoRoot, '.npm-cache'), 'pack', '--dry-run'];

const result = spawnSync(command, args, {
  cwd: packageRoot,
  stdio: 'inherit',
  shell: !npmExecPath && process.platform === 'win32',
});

if (result.error) {
  console.error(result.error.message);
}

process.exit(result.status ?? 1);
