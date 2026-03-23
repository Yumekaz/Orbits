#!/usr/bin/env node

const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const Module = require('node:module');
const process = require('node:process');
const { fileURLToPath, pathToFileURL } = require('node:url');

const { createRequire, registerHooks } = Module;

function parseArgs(argv) {
  const args = {
    root: null,
    output: null,
    mode: null,
    target: null,
    timeout: 60,
    entryLanguage: 'javascript',
    scriptType: 'auto',
    traceArgs: [],
  };

  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    const next = () => {
      i += 1;
      return argv[i];
    };
    switch (token) {
      case '--root':
        args.root = next();
        break;
      case '--output':
        args.output = next();
        break;
      case '--mode':
        args.mode = next();
        break;
      case '--target':
        args.target = next();
        break;
      case '--timeout':
        args.timeout = Number.parseInt(next(), 10) || 0;
        break;
      case '--entry-language':
        args.entryLanguage = next() || 'javascript';
        break;
      case '--script-type':
        args.scriptType = next() || 'auto';
        break;
      case '--arg':
        args.traceArgs.push(next() || '');
        break;
      default:
        throw new Error(`Unknown argument: ${token}`);
    }
  }

  if (!args.root || !args.output || !args.mode || !args.target) {
    throw new Error('Missing required runtime trace arguments');
  }
  return args;
}

const ARGS = parseArgs(process.argv.slice(2));
const ROOT = fs.realpathSync(ARGS.root);
const OUTPUT_PATH = path.resolve(ARGS.output);
const SELF_PATH = fs.realpathSync(__filename);
const ROOT_NORM = path.normalize(ROOT).toLowerCase();

function realpathSafe(value) {
  try {
    return fs.realpathSync(value);
  } catch {
    try {
      return path.resolve(value);
    } catch {
      return null;
    }
  }
}

function normalizeLocalPath(value) {
  if (!value) return null;
  let filePath = value;
  try {
    if (value instanceof URL) {
      filePath = fileURLToPath(value);
    } else if (typeof value === 'string' && value.startsWith('file:')) {
      filePath = fileURLToPath(value);
    }
  } catch {
    return null;
  }
  if (typeof filePath !== 'string' || !filePath) return null;
  const resolved = realpathSafe(filePath);
  if (!resolved) return null;
  const normalized = path.normalize(resolved);
  const lowered = normalized.toLowerCase();
  if (lowered === SELF_PATH.toLowerCase()) return null;
  if (lowered === ROOT_NORM) return null;
  if (!lowered.startsWith(ROOT_NORM + path.sep.toLowerCase())) return null;
  const rel = path.relative(ROOT, normalized);
  if (!rel || rel.startsWith('..') || path.isAbsolute(rel)) return null;
  return rel.split(path.sep).join('/');
}

function formatError(error) {
  if (!error) return null;
  if (typeof error === 'string') return error;
  const name = error.name || 'Error';
  const message = error.message || String(error);
  return `${name}: ${message}`;
}

function captureCaller(extraSkip = new Set()) {
  const previous = Error.prepareStackTrace;
  try {
    Error.prepareStackTrace = (_error, frames) => frames;
    const marker = {};
    Error.captureStackTrace(marker, captureCaller);
    const frames = marker.stack || [];
    for (const frame of frames) {
      const filename = frame.getFileName?.();
      if (!filename) continue;
      let resolved = filename;
      try {
        resolved = filename.startsWith('file:') ? fileURLToPath(filename) : filename;
      } catch {
        continue;
      }
      const real = realpathSafe(resolved);
      if (!real) continue;
      if (real.toLowerCase() === SELF_PATH.toLowerCase()) continue;
      if (extraSkip.has(real)) continue;
      const source = normalizeLocalPath(real);
      if (!source) continue;
      return { source, line: frame.getLineNumber?.() || -1 };
    }
  } finally {
    Error.prepareStackTrace = previous;
  }
  return { source: null, line: -1 };
}

const STATE = {
  start: Date.now(),
  finalized: false,
  timedOut: false,
  lastError: null,
  importCalls: 0,
  externalImportCalls: 0,
  importEdges: new Map(),
  fileAccesses: new Map(),
};

function edgeKey(source, target) {
  return `${source}>>${target}`;
}

function recordImport(source, target, request, line, loader, languageHint) {
  const key = edgeKey(source, target);
  const existing = STATE.importEdges.get(key) || {
    source,
    target,
    type: 'runtime_import',
    line: typeof line === 'number' && line > 0 ? line : -1,
    language: languageHint || ARGS.entryLanguage || 'javascript',
    runtime: true,
    origins: ['runtime'],
    dynamic: true,
    runtime_hits: 0,
    runtime_modules: new Set(),
    runtime_lines: new Set(),
    loaders: new Set(),
  };
  existing.runtime_hits += 1;
  if (request) existing.runtime_modules.add(String(request));
  if (loader) existing.loaders.add(String(loader));
  if (typeof line === 'number' && line > 0) {
    existing.runtime_lines.add(line);
    if (existing.line <= 0 || line < existing.line) existing.line = line;
  }
  STATE.importEdges.set(key, existing);
}

function recordFileAccess(source, target, line, mode) {
  const key = `${source}>>${target}`;
  const existing = STATE.fileAccesses.get(key) || {
    source,
    path: target,
    count: 0,
    modes: new Set(),
    lines: new Set(),
    line: typeof line === 'number' && line > 0 ? line : -1,
  };
  existing.count += 1;
  existing.modes.add(mode || 'r');
  if (typeof line === 'number' && line > 0) {
    existing.lines.add(line);
    if (existing.line <= 0 || line < existing.line) existing.line = line;
  }
  STATE.fileAccesses.set(key, existing);
}

function deriveLanguage(relpath) {
  const ext = path.extname(relpath || '').toLowerCase();
  if (ext === '.ts' || ext === '.mts' || ext === '.cts') return 'typescript';
  if (ext === '.tsx' || ext === '.jsx') return 'tsx';
  return 'javascript';
}

function writePayload({ exitCode, error, timedOut, partial = false }) {
  const elapsed = (Date.now() - STATE.start) / 1000;
  const edges = [...STATE.importEdges.values()]
    .sort((a, b) => a.source.localeCompare(b.source) || a.target.localeCompare(b.target))
    .map((edge) => ({
      source: edge.source,
      target: edge.target,
      type: edge.type,
      line: edge.line,
      language: edge.language,
      runtime: true,
      origins: ['runtime'],
      dynamic: true,
      runtime_hits: edge.runtime_hits,
      runtime_modules: [...edge.runtime_modules].sort(),
      runtime_lines: [...edge.runtime_lines].sort((a, b) => a - b),
      loaders: [...edge.loaders].sort(),
    }));
  const fileAccesses = [...STATE.fileAccesses.values()]
    .sort((a, b) => a.source.localeCompare(b.source) || a.path.localeCompare(b.path))
    .map((item) => ({
      source: item.source,
      path: item.path,
      count: item.count,
      modes: [...item.modes].sort(),
      lines: [...item.lines].sort((a, b) => a - b),
      line: item.line,
    }));
  const payload = {
    version: 1,
    language: 'nodejs',
    engine: 'node',
    root: ROOT,
    entry: {
      mode: ARGS.mode,
      target: ARGS.target,
      args: [...ARGS.traceArgs],
      script_type: ARGS.scriptType,
    },
    timeout_s: ARGS.timeout,
    timed_out: !!timedOut,
    elapsed_s: Number(elapsed.toFixed(3)),
    exit_code: typeof exitCode === 'number' ? exitCode : 0,
    error: error || null,
    partial: !!partial,
    edges,
    file_accesses: fileAccesses,
    summary: {
      import_calls: STATE.importCalls,
      external_import_calls: STATE.externalImportCalls,
      local_edge_hits: edges.reduce((sum, edge) => sum + (edge.runtime_hits || 0), 0),
      local_edge_count: edges.length,
      local_file_access_hits: fileAccesses.reduce((sum, item) => sum + (item.count || 0), 0),
      local_file_access_count: fileAccesses.length,
    },
  };
  fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
  fs.writeFileSync(OUTPUT_PATH, JSON.stringify(payload, null, 2), 'utf8');
  return payload;
}

const flushInterval = setInterval(() => {
  if (STATE.finalized) return;
  try {
    writePayload({
      exitCode: process.exitCode || 0,
      error: STATE.lastError,
      timedOut: STATE.timedOut,
      partial: true,
    });
  } catch {
    // Keep the trace process alive even if an interim flush fails.
  }
}, 500);
flushInterval.unref();

function finalize(exitCode = 0, error = null, timedOut = false) {
  if (STATE.finalized) return;
  STATE.finalized = true;
  STATE.timedOut = !!timedOut;
  STATE.lastError = error || STATE.lastError;
  clearInterval(flushInterval);
  if (timeoutTimer) clearTimeout(timeoutTimer);
  writePayload({ exitCode, error: STATE.lastError, timedOut: STATE.timedOut, partial: false });
  originalExit(typeof exitCode === 'number' ? exitCode : 0);
}

process.on('uncaughtException', (error) => finalize(1, formatError(error), false));
process.on('unhandledRejection', (reason) => finalize(1, formatError(reason), false));
process.on('beforeExit', (code) => {
  if (!STATE.finalized) finalize(typeof code === 'number' ? code : (process.exitCode || 0), STATE.lastError, STATE.timedOut);
});

const originalExit = process.exit.bind(process);
process.exit = function patchedExit(code = process.exitCode || 0) {
  finalize(typeof code === 'number' ? code : 0, STATE.lastError, STATE.timedOut);
};

const timeoutTimer = ARGS.timeout > 0
  ? setTimeout(() => finalize(124, `Runtime trace timed out after ${ARGS.timeout}s`, true), ARGS.timeout * 1000)
  : null;
if (timeoutTimer) timeoutTimer.unref();

registerHooks({
  resolve(specifier, context, nextResolve) {
    const result = nextResolve(specifier, context);
    const source = normalizeLocalPath(context.parentURL || null);
    if (!source) return result;
    STATE.importCalls += 1;
    const target = normalizeLocalPath(result?.url || null);
    if (!target || target === source) {
      STATE.externalImportCalls += 1;
      return result;
    }
    recordImport(source, target, specifier, -1, context.format || 'node', deriveLanguage(source));
    return result;
  },
});

function wrapFileAccesses() {
  const fsModule = require('node:fs');
  const fsPromises = require('node:fs/promises');

  const originalReadFileSync = fsModule.readFileSync.bind(fsModule);
  fsModule.readFileSync = function patchedReadFileSync(file, options) {
    const target = normalizeLocalPath(file);
    if (target) {
      const caller = captureCaller();
      if (caller.source) recordFileAccess(caller.source, target, caller.line, 'r');
    }
    return originalReadFileSync(file, options);
  };

  const originalReadFile = fsModule.readFile.bind(fsModule);
  fsModule.readFile = function patchedReadFile(file, options, callback) {
    const target = normalizeLocalPath(file);
    if (target) {
      const caller = captureCaller();
      if (caller.source) recordFileAccess(caller.source, target, caller.line, 'r');
    }
    return originalReadFile(file, options, callback);
  };

  const originalOpenSync = fsModule.openSync.bind(fsModule);
  fsModule.openSync = function patchedOpenSync(file, flags, mode) {
    const target = normalizeLocalPath(file);
    if (target) {
      const caller = captureCaller();
      if (caller.source) recordFileAccess(caller.source, target, caller.line, String(flags || 'r'));
    }
    return originalOpenSync(file, flags, mode);
  };

  const originalOpen = fsModule.open.bind(fsModule);
  fsModule.open = function patchedOpen(file, flags, mode, callback) {
    const target = normalizeLocalPath(file);
    if (target) {
      const caller = captureCaller();
      if (caller.source) recordFileAccess(caller.source, target, caller.line, String(flags || 'r'));
    }
    return originalOpen(file, flags, mode, callback);
  };

  const originalCreateReadStream = fsModule.createReadStream.bind(fsModule);
  fsModule.createReadStream = function patchedCreateReadStream(file, options) {
    const target = normalizeLocalPath(file);
    if (target) {
      const caller = captureCaller();
      if (caller.source) recordFileAccess(caller.source, target, caller.line, 'r');
    }
    return originalCreateReadStream(file, options);
  };

  const originalPromisesReadFile = fsPromises.readFile.bind(fsPromises);
  fsPromises.readFile = async function patchedPromisesReadFile(file, options) {
    const target = normalizeLocalPath(file);
    if (target) {
      const caller = captureCaller();
      if (caller.source) recordFileAccess(caller.source, target, caller.line, 'r');
    }
    return originalPromisesReadFile(file, options);
  };

  const originalPromisesOpen = fsPromises.open.bind(fsPromises);
  fsPromises.open = async function patchedPromisesOpen(file, flags, mode) {
    const target = normalizeLocalPath(file);
    if (target) {
      const caller = captureCaller();
      if (caller.source) recordFileAccess(caller.source, target, caller.line, String(flags || 'r'));
    }
    return originalPromisesOpen(file, flags, mode);
  };
}

function detectScriptType(scriptPath) {
  if (ARGS.scriptType && ARGS.scriptType !== 'auto') return ARGS.scriptType;
  const ext = path.extname(scriptPath).toLowerCase();
  if (ext === '.mjs') return 'module';
  if (ext === '.cjs') return 'commonjs';
  if (ext !== '.js') return 'auto';

  let current = path.dirname(scriptPath);
  while (true) {
    const pkg = path.join(current, 'package.json');
    if (fs.existsSync(pkg)) {
      try {
        const data = JSON.parse(fs.readFileSync(pkg, 'utf8'));
        if (data && data.type === 'module') return 'module';
      } catch {
        // Ignore broken package.json and keep walking upward.
      }
    }
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return 'commonjs';
}

async function runEntry() {
  process.chdir(ROOT);

  if (ARGS.mode === 'script') {
    const scriptPath = realpathSafe(path.resolve(ROOT, ARGS.target));
    if (!scriptPath || !fs.existsSync(scriptPath)) {
      throw new Error(`Node trace entry script not found: ${path.resolve(ROOT, ARGS.target)}`);
    }
    process.argv = [process.execPath, scriptPath, ...ARGS.traceArgs];
    const kind = detectScriptType(scriptPath);
    if (kind === 'module') {
      await import(pathToFileURL(scriptPath).href);
      return;
    }
    const localRequire = createRequire(pathToFileURL(scriptPath));
    localRequire(scriptPath);
    return;
  }

  if (ARGS.mode === 'module') {
    process.argv = [process.execPath, ARGS.target, ...ARGS.traceArgs];
    await import(ARGS.target);
    return;
  }

  throw new Error(`Unsupported Node trace mode: ${ARGS.mode}`);
}

wrapFileAccesses();

Promise.resolve(runEntry()).catch((error) => finalize(1, formatError(error), false));
