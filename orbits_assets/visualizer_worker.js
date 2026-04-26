try {
  importScripts('node_modules/d3/dist/d3.min.js');
} catch (_err) {
  try {
    importScripts('/node_modules/d3/dist/d3.min.js');
  } catch (_err2) {
    self.d3 = self.d3 || null;
  }
}

const SKIP_DIRS = new Set([
  '.git', 'node_modules', '.venv', '__pycache__', '.pytest_cache', '.mypy_cache',
  'dist', 'build', 'target', '.next', '.turbo', '.idea', '.vscode'
]);

const LANG_BY_EXT = new Map([
  ['.py', 'python'],
  ['.js', 'javascript'],
  ['.jsx', 'javascript'],
  ['.ts', 'typescript'],
  ['.tsx', 'tsx'],
  ['.go', 'go'],
  ['.c', 'c'],
  ['.h', 'c'],
  ['.cc', 'cpp'],
  ['.cpp', 'cpp'],
  ['.cxx', 'cpp'],
  ['.hpp', 'cpp'],
  ['.hh', 'cpp'],
  ['.java', 'java'],
  ['.kt', 'kotlin'],
  ['.kts', 'kotlin'],
]);

self.onmessage = async (event) => {
  const { type, payload } = event.data || {};
  try {
    if (type === 'analyzeProject') {
      const started = Date.now();
      const graph = analyzeProject(payload.files || [], payload.rootName || 'workspace', started);
      self.postMessage({ type: 'done', payload: graph });
      return;
    }
    if (type === 'layoutGraph') {
      const positions = layoutGraph(payload || {});
      self.postMessage({ type: 'layout-done', payload: { requestId: payload?.requestId, positions } });
    }
  } catch (error) {
    self.postMessage({ type: 'error', payload: { message: error instanceof Error ? error.message : String(error) } });
  }
};

function analyzeProject(rawFiles, rootName, started) {
  const files = rawFiles
    .map((file) => normalizeFile(file))
    .filter((file) => file && !shouldSkipPath(file.path));

  postProgress('Indexing files', 10);

  const fileMap = new Map(files.map((file) => [file.path, file]));
  const packageMap = buildPackageMap(files);
  const tsConfig = buildTsConfig(files);
  const goModule = buildGoModule(files);
  const pythonModules = buildPythonModuleMap(files);
  const jvmPackages = buildJvmPackageMap(files);
  const cIncludeDirs = buildCIncludeDirs(files);

  const nodes = files
    .filter((file) => file.language)
    .map((file) => ({
      id: file.path,
      filepath: file.path,
      name: basename(file.path),
      language: file.language,
      size: file.size,
      mtime: file.mtime,
      dir: dirname(file.path),
    }));

  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = [];
  const edgeKeys = new Set();
  const importStats = { local: 0, external: 0, unresolved: 0 };

  files.forEach((file, index) => {
    if (!file.language) return;
    if (index % 20 === 0) {
      postProgress(`Analyzing ${basename(file.path)}`, 10 + Math.round((index / Math.max(files.length, 1)) * 65));
    }
    const imports = extractImports(file);
    imports.forEach((entry) => {
      const targets = resolveImport(entry, file, fileMap, packageMap, tsConfig, goModule, pythonModules, jvmPackages, cIncludeDirs)
        .filter((target) => nodeIds.has(target) && target !== file.path);
      if (targets.length) {
        targets.forEach((target) => pushEdge(edges, edgeKeys, file.path, target, entry.line));
        importStats.local += 1;
      } else if (entry.external) {
        importStats.external += 1;
      } else {
        importStats.unresolved += 1;
      }
    });
  });

  const graph = enrichGraph({
    nodes,
    edges,
    meta: {
      phase: 4,
      source: 'browser-worker',
      browser_analysis: true,
      root_name: rootName,
      languages: [...new Set(nodes.map((node) => node.language).filter(Boolean))].sort(),
      total_files: nodes.length,
      total_edges: edges.length,
      import_stats: importStats,
      unsupported_languages: [],
      elapsed_s: ((Date.now() - started) / 1000).toFixed(2),
    },
  });

  return graph;
}

function postProgress(message, percent) {
  self.postMessage({ type: 'progress', payload: { message, percent } });
}

function normalizeFile(file) {
  const path = normalizePath(file.path || '');
  if (!path) return null;
  const ext = extname(path);
  return {
    path,
    text: file.text || '',
    size: file.size || 0,
    mtime: file.mtime || 0,
    ext,
    language: LANG_BY_EXT.get(ext) || null,
  };
}

function normalizePath(value) {
  return String(value || '').replace(/\\/g, '/').replace(/^\.\//, '').replace(/^\//, '');
}

function dirname(path) {
  const clean = normalizePath(path);
  const parts = clean.split('/');
  parts.pop();
  return parts.length ? parts.join('/') : '.';
}

function basename(path) {
  const clean = normalizePath(path);
  const parts = clean.split('/');
  return parts[parts.length - 1] || clean;
}

function extname(path) {
  const base = basename(path);
  const idx = base.lastIndexOf('.');
  return idx >= 0 ? base.slice(idx).toLowerCase() : '';
}

function stem(path) {
  const base = basename(path);
  const idx = base.lastIndexOf('.');
  return idx >= 0 ? base.slice(0, idx) : base;
}

function shouldSkipPath(path) {
  return normalizePath(path).split('/').some((part) => SKIP_DIRS.has(part));
}

function pushEdge(edges, edgeKeys, source, target, line) {
  const key = `${source}=>${target}:${line || 0}`;
  if (edgeKeys.has(key)) return;
  edgeKeys.add(key);
  edges.push({ source, target, line: line || null });
}

function buildPackageMap(files) {
  const packages = new Map();
  files.filter((file) => basename(file.path) === 'package.json').forEach((file) => {
    try {
      const data = JSON.parse(file.text);
      if (data && typeof data.name === 'string' && data.name.trim()) {
        packages.set(data.name.trim(), {
          root: dirname(file.path),
          main: typeof data.module === 'string' ? data.module : typeof data.main === 'string' ? data.main : null,
        });
      }
    } catch {
      /* ignore invalid package.json */
    }
  });
  return packages;
}

function buildTsConfig(files) {
  const config = { baseUrl: null, paths: [] };
  const tsFile = files.find((file) => basename(file.path) === 'tsconfig.json' || basename(file.path) === 'jsconfig.json');
  if (!tsFile) return config;
  try {
    const data = JSON.parse(tsFile.text);
    const compilerOptions = data.compilerOptions || {};
    config.baseUrl = compilerOptions.baseUrl ? normalizePath(joinPath(dirname(tsFile.path), compilerOptions.baseUrl)) : null;
    const paths = compilerOptions.paths || {};
    Object.entries(paths).forEach(([alias, targets]) => {
      const prefix = alias.endsWith('/*') ? alias.slice(0, -2) : alias;
      (Array.isArray(targets) ? targets : [targets]).forEach((target) => {
        const cleanTarget = String(target || '');
        config.paths.push({
          prefix,
          wildcard: alias.endsWith('/*') && cleanTarget.endsWith('/*'),
          target: cleanTarget.endsWith('/*') ? cleanTarget.slice(0, -2) : cleanTarget,
          dir: dirname(tsFile.path),
        });
      });
    });
  } catch {
    /* ignore invalid tsconfig */
  }
  return config;
}

function buildGoModule(files) {
  const mod = files.find((file) => basename(file.path) === 'go.mod');
  if (!mod) return null;
  const match = mod.text.match(/^\s*module\s+(.+)$/m);
  return match ? match[1].trim() : null;
}

function buildPythonModuleMap(files) {
  const modules = new Map();
  files.filter((file) => file.language === 'python').forEach((file) => {
    const parts = normalizePath(file.path).split('/');
    const name = parts[parts.length - 1];
    if (name === '__init__.py') {
      const moduleName = parts.slice(0, -1).join('.');
      if (moduleName) setMulti(modules, moduleName, file.path);
    } else {
      const moduleName = [...parts.slice(0, -1), stem(name)].join('.');
      setMulti(modules, moduleName, file.path);
    }
  });
  return modules;
}

function buildJvmPackageMap(files) {
  const packages = new Map();
  files.filter((file) => file.language === 'java' || file.language === 'kotlin').forEach((file) => {
    const match = file.text.match(/^\s*package\s+([\w.]+)\s*;?/m);
    const pkg = match ? match[1].trim() : '';
    const className = stem(file.path);
    if (pkg) {
      setMulti(packages, `${pkg}.${className}`, file.path);
      setMulti(packages, `${pkg}.*`, file.path);
    }
  });
  return packages;
}

function buildCIncludeDirs(files) {
  const dirs = new Set(['include', 'src']);
  files.filter((file) => basename(file.path) === 'compile_commands.json').forEach((file) => {
    try {
      const entries = JSON.parse(file.text);
      (Array.isArray(entries) ? entries : []).forEach((entry) => {
        const command = entry.command || entry.arguments?.join(' ') || '';
        const matches = [...String(command).matchAll(/(?:-I|-iquote\s+|-isystem\s+)([^\s]+)/g)];
        matches.forEach((match) => dirs.add(normalizePath(match[1])));
      });
    } catch {
      /* ignore */
    }
  });
  return [...dirs];
}

function setMulti(map, key, value) {
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(value);
}

function extractImports(file) {
  switch (file.language) {
    case 'python':
      return extractPythonImports(file);
    case 'javascript':
    case 'typescript':
    case 'tsx':
      return extractJsImports(file);
    case 'go':
      return extractGoImports(file);
    case 'c':
    case 'cpp':
      return extractCImports(file);
    case 'java':
    case 'kotlin':
      return extractJvmImports(file);
    default:
      return [];
  }
}

function extractPythonImports(file) {
  const imports = [];
  const lines = file.text.split(/\r?\n/);
  lines.forEach((line, index) => {
    const importMatch = line.match(/^\s*import\s+(.+)$/);
    if (importMatch) {
      importMatch[1].split(',').map((part) => part.trim().split(/\s+as\s+/)[0]).filter(Boolean).forEach((spec) => {
        imports.push({ type: 'python-import', spec, line: index + 1, external: !spec.startsWith('.') });
      });
    }
    const fromMatch = line.match(/^\s*from\s+([.\w]+)\s+import\s+(.+)$/);
    if (fromMatch) {
      const moduleSpec = fromMatch[1].trim();
      const names = fromMatch[2].replace(/[()]/g, '').split(',').map((part) => part.trim().split(/\s+as\s+/)[0]).filter(Boolean);
      imports.push({ type: 'python-from', spec: moduleSpec, names, line: index + 1, external: !moduleSpec.startsWith('.') });
    }
  });
  return imports;
}

function extractJsImports(file) {
  const imports = [];
  const patterns = [
    /(?:import|export)\s+(?:[^'"`]+?\s+from\s+)?['"]([^'"]+)['"]/g,
    /require\(\s*['"]([^'"]+)['"]\s*\)/g,
    /import\(\s*['"]([^'"]+)['"]\s*\)/g,
  ];
  const lines = file.text.split(/\r?\n/);
  lines.forEach((line, index) => {
    patterns.forEach((pattern) => {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(line))) {
        const spec = match[1];
        imports.push({ type: 'js', spec, line: index + 1, external: !spec.startsWith('.') && !spec.startsWith('/') && !spec.startsWith('@') });
      }
    });
  });
  return imports;
}

function extractGoImports(file) {
  const imports = [];
  const lines = file.text.split(/\r?\n/);
  let inBlock = false;
  lines.forEach((line, index) => {
    if (/^\s*import\s*\(/.test(line)) {
      inBlock = true;
      return;
    }
    if (inBlock) {
      if (/^\s*\)/.test(line)) {
        inBlock = false;
        return;
      }
      const match = line.match(/"([^"]+)"/);
      if (match) imports.push({ type: 'go', spec: match[1], line: index + 1, external: true });
      return;
    }
    const match = line.match(/^\s*import\s+"([^"]+)"/);
    if (match) imports.push({ type: 'go', spec: match[1], line: index + 1, external: true });
  });
  return imports;
}

function extractCImports(file) {
  const imports = [];
  const lines = file.text.split(/\r?\n/);
  lines.forEach((line, index) => {
    const match = line.match(/^\s*#include\s*([<"])([^>"]+)[>"]/);
    if (match) imports.push({ type: 'c', spec: match[2], quoted: match[1] === '"', line: index + 1, external: match[1] === '<' });
  });
  return imports;
}

function extractJvmImports(file) {
  const imports = [];
  const lines = file.text.split(/\r?\n/);
  lines.forEach((line, index) => {
    const match = line.match(/^\s*import\s+([\w.*]+)\s*;?/);
    if (match) imports.push({ type: 'jvm', spec: match[1].trim(), line: index + 1, external: true });
  });
  return imports;
}

function resolveImport(entry, file, fileMap, packageMap, tsConfig, goModule, pythonModules, jvmPackages, cIncludeDirs) {
  switch (entry.type) {
    case 'python-import':
      return resolvePythonModule(entry.spec, file.path, pythonModules);
    case 'python-from':
      return resolvePythonFrom(entry, file.path, pythonModules);
    case 'js':
      return resolveJs(entry.spec, file.path, fileMap, packageMap, tsConfig);
    case 'go':
      return resolveGo(entry.spec, file.path, fileMap, goModule);
    case 'c':
      return resolveC(entry.spec, file.path, fileMap, cIncludeDirs, entry.quoted);
    case 'jvm':
      return resolveJvm(entry.spec, jvmPackages);
    default:
      return [];
  }
}

function resolvePythonModule(spec, filePath, pythonModules) {
  const normalized = normalizePythonSpec(spec, filePath);
  return normalized ? (pythonModules.get(normalized) || []) : [];
}

function resolvePythonFrom(entry, filePath, pythonModules) {
  const moduleName = normalizePythonSpec(entry.spec, filePath);
  if (!moduleName) return [];
  const targets = [];
  if (entry.names.includes('*')) {
    targets.push(...(pythonModules.get(moduleName) || []));
    return dedupe(targets);
  }
  entry.names.forEach((name) => {
    const child = `${moduleName}.${name}`;
    if (pythonModules.has(child)) {
      targets.push(...pythonModules.get(child));
    } else if (pythonModules.has(moduleName)) {
      targets.push(...pythonModules.get(moduleName));
    }
  });
  return dedupe(targets);
}

function normalizePythonSpec(spec, filePath) {
  if (!spec) return null;
  if (!spec.startsWith('.')) return spec;
  const level = spec.match(/^\.+/)[0].length;
  const baseParts = dirname(filePath).split('/').filter(Boolean);
  const kept = baseParts.slice(0, Math.max(0, baseParts.length - level + 1));
  const remainder = spec.slice(level);
  return [...kept, ...remainder.split('.').filter(Boolean)].join('.');
}

function resolveJs(spec, filePath, fileMap, packageMap, tsConfig) {
  const candidates = [];
  const fromDir = dirname(filePath);
  if (spec.startsWith('.')) {
    candidates.push(...expandJsCandidates(joinPath(fromDir, spec)));
  } else {
    tsConfig.paths.forEach((rule) => {
      if (spec === rule.prefix || (rule.wildcard && spec.startsWith(rule.prefix + '/'))) {
        const suffix = spec === rule.prefix ? '' : spec.slice(rule.prefix.length + 1);
        candidates.push(...expandJsCandidates(joinPath(rule.dir, rule.target, suffix)));
      }
    });
    if (tsConfig.baseUrl) {
      candidates.push(...expandJsCandidates(joinPath(tsConfig.baseUrl, spec)));
    }
    const pkg = findPackageTarget(spec, packageMap);
    if (pkg) candidates.push(...pkg);
  }
  return firstExistingCandidates(candidates, fileMap);
}

function findPackageTarget(spec, packageMap) {
  for (const [name, info] of packageMap.entries()) {
    if (spec === name || spec.startsWith(name + '/')) {
      const suffix = spec === name ? '' : spec.slice(name.length + 1);
      const base = info.main ? joinPath(info.root, info.main) : joinPath(info.root, suffix || 'index');
      return expandJsCandidates(base);
    }
  }
  return [];
}

function expandJsCandidates(base) {
  const clean = normalizePath(base);
  const exts = ['', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '/index.ts', '/index.tsx', '/index.js', '/index.jsx'];
  return exts.map((ext) => normalizePath(clean + ext));
}

function firstExistingCandidates(candidates, fileMap) {
  const found = [];
  candidates.forEach((candidate) => {
    if (fileMap.has(candidate) && !found.includes(candidate)) found.push(candidate);
  });
  return found;
}

function resolveGo(spec, filePath, fileMap, goModule) {
  if (!goModule || !spec.startsWith(goModule)) return [];
  const rel = spec.slice(goModule.length).replace(/^\//, '');
  const dir = normalizePath(rel);
  const prefix = dir ? `${dir}/` : '';
  return [...fileMap.keys()].filter((path) => path.startsWith(prefix) && extname(path) === '.go' && dirname(path) === (dir || '.'));
}

function resolveC(spec, filePath, fileMap, includeDirs, quoted) {
  const candidates = [];
  if (quoted) candidates.push(normalizePath(joinPath(dirname(filePath), spec)));
  includeDirs.forEach((dir) => candidates.push(normalizePath(joinPath(dir, spec))));
  return firstExistingCandidates(candidates, fileMap);
}

function resolveJvm(spec, jvmPackages) {
  if (jvmPackages.has(spec)) return dedupe(jvmPackages.get(spec));
  if (spec.endsWith('.*')) return dedupe(jvmPackages.get(spec) || []);
  return [];
}

function joinPath(...parts) {
  const joined = parts.filter(Boolean).join('/');
  return normalizePath(joined.replace(/\/+/g, '/').replace(/\/\.\//g, '/'));
}

function dedupe(items) {
  return [...new Set(items)];
}

const NodeClass = {
  ENTRY: 'ENTRY',
  LEAF: 'LEAF',
  CONNECTED: 'CONNECTED',
  ORPHAN: 'ORPHAN',
  ISLAND: 'ISLAND',
  TEST: 'TEST',
  GENERATED: 'GENERATED',
};

const TEST_PATTERNS = ['test_', '_test.', '.test.', 'spec_', '_spec.', '/test/', '/tests/', '/spec/', '/specs/', 'conftest', 'fixture'];
const GENERATED_DIRS = ['__pycache__', '.mypy_cache', '.pytest_cache', 'dist/', 'build/', 'out/', 'target/', 'generated/', '.eggs/', 'htmlcov/', 'site-packages/', '.tox/'];

function enrichGraph(raw) {
  const nodes = raw.nodes || [];
  const edges = raw.edges || [];
  const classifications = classifyNodes(nodes, edges);
  const cycles = findCycles(nodes, edges);
  const islands = findIslands(nodes, edges, classifications);
  islands.forEach((cluster) => {
    cluster.forEach((id) => {
      if (![NodeClass.GENERATED, NodeClass.TEST].includes(classifications[id])) classifications[id] = NodeClass.ISLAND;
    });
  });
  const depths = computeDepths(nodes, edges, classifications);
  const summary = computeSummary(classifications, cycles, islands, depths);
  const islandMap = new Map();
  islands.forEach((cluster, index) => cluster.forEach((id) => islandMap.set(id, index)));
  const enrichedNodes = nodes.map((node) => ({
    ...node,
    classification: classifications[node.id] || NodeClass.ORPHAN,
    depth: depths.get(node.id) ?? -1,
    island_id: islandMap.has(node.id) ? islandMap.get(node.id) : -1,
  }));
  const waste = enrichedNodes
    .filter((node) => [NodeClass.ORPHAN, NodeClass.ISLAND].includes(node.classification))
    .map((node) => ({ id: node.id, name: node.name, classification: node.classification, size: node.size || 0, island_id: node.island_id ?? -1 }))
    .sort((a, b) => (a.classification === b.classification ? (a.island_id - b.island_id) || a.id.localeCompare(b.id) : a.classification === NodeClass.ISLAND ? -1 : 1));
  return {
    nodes: enrichedNodes,
    edges,
    cycles,
    islands,
    waste,
    summary,
    meta: { ...(raw.meta || {}), phase: 4 },
  };
}

function buildAdjacency(nodes, edges) {
  const outbound = new Map();
  const inbound = new Map();
  nodes.forEach((node) => {
    outbound.set(node.id, new Set());
    inbound.set(node.id, new Set());
  });
  edges.forEach((edge) => {
    if (outbound.has(edge.source) && inbound.has(edge.target)) {
      outbound.get(edge.source).add(edge.target);
      inbound.get(edge.target).add(edge.source);
    }
  });
  return { outbound, inbound };
}

function isTest(id) {
  const low = String(id).toLowerCase();
  return TEST_PATTERNS.some((pattern) => low.includes(pattern));
}

function isGenerated(id) {
  const low = String(id).toLowerCase();
  return GENERATED_DIRS.some((pattern) => low.includes(pattern));
}

function classifyNodes(nodes, edges) {
  const { outbound, inbound } = buildAdjacency(nodes, edges);
  const result = {};
  nodes.forEach((node) => {
    if (isGenerated(node.id)) result[node.id] = NodeClass.GENERATED;
    else if (isTest(node.id)) result[node.id] = NodeClass.TEST;
  });
  nodes.forEach((node) => {
    if (result[node.id]) return;
    const cleanInbound = [...(inbound.get(node.id) || new Set())].filter((src) => ![NodeClass.TEST, NodeClass.GENERATED].includes(result[src]));
    const hasOut = (outbound.get(node.id) || new Set()).size > 0;
    const hasIn = cleanInbound.length > 0;
    if (!hasIn && !hasOut) result[node.id] = NodeClass.ORPHAN;
    else if (!hasIn && hasOut) result[node.id] = NodeClass.ENTRY;
    else if (hasIn && !hasOut) result[node.id] = NodeClass.LEAF;
    else result[node.id] = NodeClass.CONNECTED;
  });
  return result;
}

function findCycles(nodes, edges) {
  const { outbound } = buildAdjacency(nodes, edges);
  const visited = new Set();
  const stack = [];
  const inStack = new Set();
  const cycles = [];
  const seen = new Set();
  const MAX_CYCLES = 50;

  function dfs(node) {
    if (cycles.length >= MAX_CYCLES) return;
    visited.add(node);
    stack.push(node);
    inStack.add(node);
    for (const next of outbound.get(node) || []) {
      if (cycles.length >= MAX_CYCLES) break;
      if (!visited.has(next)) dfs(next);
      else if (inStack.has(next)) {
        const start = stack.indexOf(next);
        const cycle = stack.slice(start).concat(next);
        const normalized = normalizeCycle(cycle);
        const key = normalized.join('>');
        if (!seen.has(key)) {
          seen.add(key);
          cycles.push(normalized);
        }
      }
    }
    stack.pop();
    inStack.delete(node);
  }

  nodes.forEach((node) => {
    if (!visited.has(node.id)) dfs(node.id);
  });
  return cycles;
}

function normalizeCycle(cycle) {
  const body = cycle.slice(0, -1);
  const min = [...body].sort()[0];
  const idx = body.indexOf(min);
  const rotated = body.slice(idx).concat(body.slice(0, idx));
  rotated.push(rotated[0]);
  return rotated;
}

function findIslands(nodes, edges, classifications) {
  const { outbound, inbound } = buildAdjacency(nodes, edges);
  const undirected = new Map();
  nodes.forEach((node) => undirected.set(node.id, new Set()));
  nodes.forEach((node) => {
    (outbound.get(node.id) || new Set()).forEach((target) => {
      undirected.get(node.id).add(target);
      undirected.get(target).add(node.id);
    });
    (inbound.get(node.id) || new Set()).forEach((source) => {
      undirected.get(node.id).add(source);
      undirected.get(source).add(node.id);
    });
  });
  const seen = new Set();
  const islands = [];
  nodes.forEach((node) => {
    if (seen.has(node.id)) return;
    const queue = [node.id];
    const cluster = [];
    seen.add(node.id);
    while (queue.length) {
      const current = queue.shift();
      cluster.push(current);
      (undirected.get(current) || new Set()).forEach((next) => {
        if (!seen.has(next)) {
          seen.add(next);
          queue.push(next);
        }
      });
    }
    if (cluster.length > 1 && !cluster.some((id) => classifications[id] === NodeClass.ENTRY)) islands.push(cluster);
  });
  return islands;
}

function computeDepths(nodes, edges, classifications) {
  const { outbound } = buildAdjacency(nodes, edges);
  const depths = new Map(nodes.map((node) => [node.id, -1]));
  const queue = [];
  nodes.forEach((node) => {
    if (classifications[node.id] === NodeClass.ENTRY) {
      depths.set(node.id, 0);
      queue.push(node.id);
    }
  });
  while (queue.length) {
    const current = queue.shift();
    const depth = depths.get(current);
    (outbound.get(current) || new Set()).forEach((next) => {
      if (depths.get(next) === -1) {
        depths.set(next, depth + 1);
        queue.push(next);
      }
    });
  }
  return depths;
}

function computeSummary(classifications, cycles, islands, depths) {
  const counts = {};
  Object.values(classifications).forEach((cls) => { counts[cls] = (counts[cls] || 0) + 1; });
  const total = Object.keys(classifications).length;
  const orphanCount = counts[NodeClass.ORPHAN] || 0;
  const islandNodes = islands.reduce((sum, cluster) => sum + cluster.length, 0);
  const cycleCount = cycles.length;
  let penalty = 0;
  if (total > 0) {
    penalty += (orphanCount / total) * 40;
    penalty += Math.min(cycleCount * 5, 30);
    penalty += (islandNodes / Math.max(total, 1)) * 20;
  }
  return {
    counts,
    total,
    cycle_count: cycleCount,
    island_count: islands.length,
    max_depth: Math.max(0, ...[...depths.values()].filter((value) => value >= 0)),
    health_score: Math.max(0, Math.round(100 - penalty)),
    unreachable: [...depths.values()].filter((value) => value === -1).length,
  };
}

function layoutGraph(payload) {
  const width = Math.max(640, Number(payload.width) || 1000);
  const height = Math.max(480, Number(payload.height) || 760);
  const nodes = (payload.nodes || []).map((node) => ({ ...node }));
  const edges = (payload.edges || []).map((edge) => ({ source: edge.source, target: edge.target }));
  const cached = payload.cachedPositions || {};
  const positions = {};
  if (!nodes.length) return positions;

  const dirTargets = buildDirTargets(nodes, width, height);
  nodes.forEach((node, index) => {
    const cachedPos = cached[node.id];
    const seeded = cachedPos || seedLayoutPosition(node, index, width, height, dirTargets);
    node.x = seeded.x;
    node.y = seeded.y;
  });

  if (payload.layoutMode === 'radial') {
    applyRadialLayout(nodes, width, height);
  } else if (self.d3 && self.d3.forceSimulation) {
    const nodeIdSet = new Set(nodes.map((node) => node.id));
    const simEdges = edges.filter((edge) => nodeIdSet.has(edge.source) && nodeIdSet.has(edge.target));
    const largeGraph = nodes.length > 450 || simEdges.length > 1400;
    const hugeGraph = nodes.length > 900 || simEdges.length > 3200;
    const maxTicks = hugeGraph ? 80 : largeGraph ? 130 : 220;
    const linkStrength = payload.layoutMode === 'cluster' ? (largeGraph ? 0.22 : 0.34) : largeGraph ? 0.16 : 0.24;
    const charge = payload.layoutMode === 'cluster' ? (largeGraph ? -130 : -170) : largeGraph ? -170 : -230;
    const collideIterations = hugeGraph ? 1 : 2;
    const simulation = self.d3.forceSimulation(nodes)
      .alpha(largeGraph ? 0.18 : 0.24)
      .alphaDecay(hugeGraph ? 0.1 : largeGraph ? 0.075 : 0.055)
      .velocityDecay(largeGraph ? 0.38 : 0.3)
      .force('link', self.d3.forceLink(simEdges).id((d) => d.id).distance(payload.layoutMode === 'cluster' ? 72 : 92).strength(linkStrength))
      .force('charge', self.d3.forceManyBody().strength(charge).distanceMax(620))
      .force('collide', self.d3.forceCollide((node) => node.classification === 'ENTRY' ? 22 : 14).iterations(collideIterations))
      .force('center', self.d3.forceCenter(width / 2, height / 2));
    if (payload.layoutMode === 'cluster') {
      simulation.force('x', self.d3.forceX((node) => dirTargets.get(node.dir || '.')?.x || width / 2).strength(largeGraph ? 0.1 : 0.14));
      simulation.force('y', self.d3.forceY((node) => dirTargets.get(node.dir || '.')?.y || height / 2).strength(largeGraph ? 0.1 : 0.14));
    } else {
      simulation.force('x', self.d3.forceX(width / 2).strength(largeGraph ? 0.02 : 0.03));
      simulation.force('y', self.d3.forceY(height / 2).strength(largeGraph ? 0.02 : 0.03));
    }
    for (let i = 0; i < maxTicks; i += 1) {
      simulation.tick();
      if (simulation.alpha() < (largeGraph ? 0.05 : 0.035)) break;
    }
    simulation.stop();
  }

  nodes.forEach((node) => {
    positions[node.id] = { x: node.x, y: node.y };
  });
  return positions;
}

function buildDirTargets(nodes, width, height) {
  const dirs = [...new Set(nodes.map((node) => node.dir || '.'))].sort();
  const cols = Math.max(1, Math.ceil(Math.sqrt(dirs.length || 1)));
  const rows = Math.max(1, Math.ceil(dirs.length / cols));
  const xGap = width / (cols + 1);
  const yGap = height / (rows + 1);
  const targets = new Map();
  dirs.forEach((dir, index) => {
    const col = index % cols;
    const row = Math.floor(index / cols);
    targets.set(dir, { x: (col + 1) * xGap, y: (row + 1) * yGap });
  });
  return targets;
}

function seedLayoutPosition(node, index, width, height, dirTargets) {
  const target = dirTargets.get(node.dir || '.') || { x: width / 2, y: height / 2 };
  const hash = [...String(node.id || index)].reduce((sum, ch) => sum + ch.charCodeAt(0), 0);
  const angle = (hash % 360) * (Math.PI / 180);
  const depth = node.depth >= 0 ? node.depth : 3;
  const radius = 36 + depth * 28 + (node.importance || 0) * 2;
  return { x: target.x + Math.cos(angle) * radius, y: target.y + Math.sin(angle) * radius * 0.76 };
}

function applyRadialLayout(nodes, width, height) {
  const centerX = width / 2;
  const centerY = height / 2;
  const byDepth = new Map();
  nodes.forEach((node) => {
    const depth = node.depth >= 0 ? node.depth : 5;
    if (!byDepth.has(depth)) byDepth.set(depth, []);
    byDepth.get(depth).push(node);
  });
  [...byDepth.entries()].sort((a, b) => a[0] - b[0]).forEach(([depth, bucket]) => {
    const radius = 40 + depth * 64;
    bucket.sort((a, b) => String(a.id).localeCompare(String(b.id)));
    bucket.forEach((node, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(bucket.length, 1);
      node.x = centerX + Math.cos(angle) * radius;
      node.y = centerY + Math.sin(angle) * radius * 0.72;
    });
  });
}
