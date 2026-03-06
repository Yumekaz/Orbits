const DEFAULT_CLASSES = ['CONNECTED', 'ENTRY', 'LEAF', 'ORPHAN', 'ISLAND', 'TEST', 'GENERATED'];
const THEME = {
  CONNECTED: { fill: '#00e5ff', bg: 'rgba(0,229,255,.14)' },
  ENTRY: { fill: '#00ff88', bg: 'rgba(0,255,136,.16)' },
  LEAF: { fill: '#ffaa00', bg: 'rgba(255,170,0,.16)' },
  ORPHAN: { fill: '#ff3557', bg: 'rgba(255,53,87,.16)' },
  ISLAND: { fill: '#b06fff', bg: 'rgba(176,111,255,.16)' },
  TEST: { fill: '#6e7ea8', bg: 'rgba(110,126,168,.16)' },
  GENERATED: { fill: '#223058', bg: 'rgba(34,48,88,.3)' },
  CLUSTER: { fill: '#b06fff', bg: 'rgba(176,111,255,.05)' },
};
const LANG_COLORS = {
  python: '#3b82f6', javascript: '#f59e0b', typescript: '#06b6d4', tsx: '#22d3ee', go: '#10b981', c: '#fb7185', cpp: '#f97316', java: '#ef4444', kotlin: '#a855f7', generic: '#64748b'
};
const PKG_HINTS = {
  javascript: ['tree-sitter-javascript', 'tree-sitter-typescript'], typescript: ['tree-sitter-javascript', 'tree-sitter-typescript'], tsx: ['tree-sitter-javascript', 'tree-sitter-typescript'], go: ['tree-sitter-go'], c: ['tree-sitter-c'], cpp: ['tree-sitter-cpp'], java: ['tree-sitter-java'], kotlin: ['tree-sitter-kotlin'], python: ['tree-sitter-python']
};

let G = null;
let cy = null;
let activeLangs = null;
let visibleClasses = new Set(DEFAULT_CLASSES);
let showLabels = true;
let clusterMode = false;
let highlightCycles = false;
let searchQuery = '';
let selectedId = null;
let dismissedWarning = false;
let cycleLookup = new Map();
let cycleNodeSet = new Set();
let worker = null;

function normalizeId(id) { return String(id || '').replace(/\\/g, '/'); }
function basename(path) { const bits = normalizeId(path).split('/'); return bits[bits.length - 1] || path; }
function dirname(path) { const bits = normalizeId(path).split('/'); bits.pop(); return bits.length ? bits.join('/') : '.'; }
function esc(value) { return String(value).replace(/[&<>"']/g, (m) => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[m])); }
function getTheme(cls) { return THEME[cls] || THEME.ORPHAN; }
function getLangColor(lang) { return LANG_COLORS[(lang || '').toLowerCase()] || '#94a3b8'; }
function displayLang(lang) { return ({ javascript:'JavaScript', typescript:'TypeScript', tsx:'TSX', python:'Python', go:'Go', c:'C', cpp:'C++', java:'Java', kotlin:'Kotlin', generic:'Generic' })[lang] || lang; }
function getNodeById(id) { return (G?.nodes || []).find((node) => normalizeId(node.id) === normalizeId(id)); }
function formatSize(bytes) { if (!bytes) return '0 B'; if (bytes < 1024) return `${bytes} B`; if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`; return `${(bytes / 1048576).toFixed(1)} MB`; }
function activeLanguageSet() { const langs = new Set((G?.nodes || []).map((node) => node.language).filter(Boolean)); return activeLangs === null ? langs : activeLangs; }
function nodeVisible(node) { return visibleClasses.has(node.classification) && (!node.language || activeLanguageSet().has(node.language)); }
function nodeMatchesSearch(node) { if (!searchQuery) return true; const q = searchQuery.toLowerCase(); return basename(node.id).toLowerCase().includes(q) || normalizeId(node.filepath || node.id).toLowerCase().includes(q); }

function buildCycleIndex() {
  cycleLookup = new Map();
  cycleNodeSet = new Set();
  (G?.cycles || []).forEach((cycle, index) => {
    const normalized = cycle.map(normalizeId);
    normalized.forEach((id) => {
      cycleNodeSet.add(id);
      if (!cycleLookup.has(id)) cycleLookup.set(id, []);
      cycleLookup.get(id).push({ index, path: normalized });
    });
  });
}

function updateStats() {
  if (!G) return;
  const summary = G.summary || {};
  const counts = summary.counts || {};
  const importStats = G.meta?.import_stats || {};
  const totalImports = Object.values(importStats).reduce((sum, value) => sum + value, 0);
  const pct = totalImports ? Math.round(((importStats.local || 0) / totalImports) * 100) : 0;
  document.getElementById('s-files').textContent = G.nodes.length;
  document.getElementById('s-edges').textContent = G.edges.length;
  document.getElementById('s-dead').textContent = (counts.ORPHAN || 0) + (counts.ISLAND || 0);
  document.getElementById('s-cycles').textContent = summary.cycle_count || 0;
  document.getElementById('s-health').textContent = summary.health_score ?? 0;
  document.getElementById('s-unreachable').textContent = summary.unreachable ?? 0;
  document.getElementById('s-max-depth').textContent = summary.max_depth ?? 0;
  document.getElementById('s-langs').textContent = [...new Set(G.nodes.map((node) => node.language).filter(Boolean))].length;
  const resolved = document.getElementById('s-resolved');
  resolved.textContent = `${pct}%`;
  resolved.className = 'tv ' + (pct >= 80 ? 'gv' : pct >= 50 ? 'yv' : 'ov');
  document.getElementById('hp-value').textContent = summary.health_score ?? '—';
  document.getElementById('elapsed').textContent = G.meta?.elapsed_s ? `${G.meta.elapsed_s}s` : '';
}

function renderLanguageMenu() {
  const grid = document.getElementById('lang-chip-grid');
  if (!G) { grid.innerHTML = ''; return; }
  const counts = {};
  G.nodes.forEach((node) => { if (node.language) counts[node.language] = (counts[node.language] || 0) + 1; });
  const active = activeLanguageSet();
  const languages = Object.keys(counts).sort();
  grid.innerHTML = languages.map((lang) => `<button class="toggle-chip ${active.has(lang) ? 'on' : ''}" data-lang="${esc(lang)}" style="color:${getLangColor(lang)};border-color:${active.has(lang) ? getLangColor(lang) : 'var(--border)'}">${esc(displayLang(lang))} <span class="count">(${counts[lang]})</span></button>`).join('');
  grid.querySelectorAll('[data-lang]').forEach((button) => button.addEventListener('click', () => toggleLanguage(button.dataset.lang)));
  const total = languages.length;
  document.getElementById('btn-lang').classList.toggle('on', activeLangs !== null && active.size !== total);
}

function toggleLanguage(lang) {
  const current = activeLangs === null ? new Set(activeLanguageSet()) : new Set(activeLangs);
  if (current.has(lang)) current.delete(lang); else current.add(lang);
  const total = new Set(G.nodes.map((node) => node.language).filter(Boolean)).size;
  activeLangs = current.size === total ? null : current;
  rerender();
}

function renderFilterPanel() {
  const grid = document.getElementById('filter-chip-grid');
  grid.innerHTML = DEFAULT_CLASSES.map((cls) => `<button class="toggle-chip ${visibleClasses.has(cls) ? 'on' : ''}" data-cls="${cls}" style="color:${getTheme(cls).fill};border-color:${visibleClasses.has(cls) ? getTheme(cls).fill : 'var(--border)'}">${cls.toLowerCase()}</button>`).join('');
  grid.querySelectorAll('[data-cls]').forEach((button) => button.addEventListener('click', () => {
    const cls = button.dataset.cls;
    if (visibleClasses.has(cls)) visibleClasses.delete(cls); else visibleClasses.add(cls);
    rerender();
  }));
  document.getElementById('btn-filter').classList.toggle('on', visibleClasses.size !== DEFAULT_CLASSES.length);
}

function renderListItem(item, color) {
  return `<div class="list-item" data-focus="${esc(normalizeId(item.id))}"><div class="item-dot" style="background:${color}"></div><div><div class="item-name">${esc(basename(item.id))}</div><div class="item-path">${esc(dirname(item.id))}</div></div></div>`;
}

function bindFocusButtons(root) {
  root.querySelectorAll('[data-focus]').forEach((element) => element.addEventListener('click', () => focusNode(element.dataset.focus)));
}

function renderWaste() {
  const body = document.getElementById('waste-panel');
  if (!G) { body.innerHTML = ''; return; }
  const items = (G.waste || []).filter((item) => {
    const node = getNodeById(item.id);
    return node ? nodeVisible(node) : true;
  });
  const badge = document.getElementById('waste-badge');
  badge.textContent = items.length;
  badge.className = `badge${items.length === 0 ? ' ok' : ''}`;
  if (!items.length) {
    body.innerHTML = '<div class="list-label">No dead files</div>';
    return;
  }
  const islands = items.filter((item) => item.classification === 'ISLAND');
  const orphans = items.filter((item) => item.classification === 'ORPHAN');
  const parts = [];
  if (islands.length) {
    const grouped = new Map();
    islands.forEach((item) => { const key = item.island_id ?? -1; if (!grouped.has(key)) grouped.set(key, []); grouped.get(key).push(item); });
    [...grouped.entries()].forEach(([id, group]) => {
      parts.push(`<div class="list-label">Isolated Cluster ${Number(id) + 1}</div>`);
      group.forEach((item) => parts.push(renderListItem(item, 'var(--purple)')));
    });
  }
  if (orphans.length) {
    parts.push('<div class="list-label">Orphan Files</div>');
    orphans.forEach((item) => parts.push(renderListItem(item, 'var(--red)')));
  }
  body.innerHTML = parts.join('');
  bindFocusButtons(body);
}

function renderCycles() {
  const body = document.getElementById('cycles-panel');
  if (!G) { body.innerHTML = ''; return; }
  const cycles = (G.cycles || []).filter((cycle) => cycle.every((id) => {
    const node = getNodeById(id);
    return node ? nodeVisible(node) : false;
  }));
  const badge = document.getElementById('cycle-badge');
  badge.textContent = cycles.length;
  badge.className = `badge${cycles.length === 0 ? ' ok' : ''}`;
  if (!cycles.length) {
    body.innerHTML = '<div class="list-label">No cycles detected</div>';
    return;
  }
  body.innerHTML = '<div class="sect-title"><button class="mini-btn ' + (highlightCycles ? 'on' : '') + '" id="btn-cycle-highlight">highlight all</button></div>' + cycles.map((cycle, index) => `<div class="cycle-item"><div><div class="cycle-title">Cycle ${index + 1}</div><div class="cycle-path">${cycle.map((id, idx) => `${idx ? '<span class="cycle-arrow">→</span>' : ''}<button class="cycle-node" data-focus="${esc(normalizeId(id))}">${esc(basename(id))}</button>`).join('')}</div></div></div>`).join('');
  bindFocusButtons(body);
  const toggle = document.getElementById('btn-cycle-highlight');
  if (toggle) toggle.onclick = () => { highlightCycles = !highlightCycles; applySearchAndSelectionState(); renderCycles(); };
}

function updateWarningBanner() {
  const items = G?.meta?.unsupported_languages || [];
  const banner = document.getElementById('warning-banner');
  if (!items.length || dismissedWarning) { banner.classList.remove('show'); return; }
  const pkgs = new Set();
  items.forEach((item) => (PKG_HINTS[item.language] || []).forEach((pkg) => pkgs.add(pkg)));
  const text = items.map((item) => `${displayLang(item.language)} parser unavailable - ${item.files} files were not analysed`).join(' • ');
  document.getElementById('warning-copy').innerHTML = `<strong>Warning</strong> ${esc(text)}. Run: <code>pip install ${esc([...pkgs].join(' '))}</code>`;
  banner.classList.add('show');
}

function edgeCountMap() {
  const counts = new Map();
  (G?.nodes || []).forEach((node) => counts.set(normalizeId(node.id), 0));
  (G?.edges || []).forEach((edge) => {
    const source = normalizeId(edge.source.id || edge.source);
    const target = normalizeId(edge.target.id || edge.target);
    counts.set(source, (counts.get(source) || 0) + 1);
    counts.set(target, (counts.get(target) || 0) + 1);
  });
  return counts;
}

function seedPosition(node, width, height, counts) {
  const degree = counts.get(normalizeId(node.id)) || 0;
  const dirHash = [...dirname(node.id)].reduce((sum, ch) => sum + ch.charCodeAt(0), 0);
  const jitterX = ((dirHash % 37) - 18) * 6;
  const jitterY = ((dirHash % 29) - 14) * 6;
  if (node.classification === 'ORPHAN') return { x: width * 0.18 + jitterX, y: height * 0.46 + jitterY };
  if (node.classification === 'ISLAND') return { x: width * 0.80 + jitterX, y: height * 0.24 + jitterY };
  if (node.classification === 'TEST') return { x: width * 0.78 + jitterX, y: height * 0.64 + jitterY };
  if (node.classification === 'GENERATED') return { x: width * 0.24 + jitterX, y: height * 0.72 + jitterY };
  const depth = node.depth >= 0 ? node.depth : 3;
  const angle = (dirHash % 360) * (Math.PI / 180);
  const radius = 90 + depth * 34 + Math.max(0, 4 - Math.min(degree, 4)) * 8;
  const x = width * 0.52 + Math.cos(angle) * radius + jitterX * 0.35;
  const y = height * 0.50 + Math.sin(angle) * radius * 0.72 + jitterY * 0.35;
  return { x, y };
}

function calculateNodePositions(nodes, edges) {
  const width = document.getElementById('graph').clientWidth || 1000;
  const height = document.getElementById('graph').clientHeight || 760;
  const counts = edgeCountMap();
  const simNodes = nodes.map((node) => {
    const seed = seedPosition(node, width, height, counts);
    return { ...node, id: normalizeId(node.id), x: seed.x, y: seed.y };
  });
  const simEdges = edges.map((edge) => ({ source: normalizeId(edge.source.id || edge.source), target: normalizeId(edge.target.id || edge.target) }));
  const map = new Map(simNodes.map((node) => [node.id, node]));
  const forceEdges = simEdges.filter((edge) => map.has(edge.source) && map.has(edge.target));
  const entryIds = new Set(simNodes.filter((node) => node.classification === 'ENTRY').map((node) => node.id));
  const simulation = d3.forceSimulation(simNodes)
    .force('link', d3.forceLink(forceEdges).id((d) => d.id).distance((edge) => {
      const source = map.get(edge.source.id || edge.source);
      const target = map.get(edge.target.id || edge.target);
      const sourceDepth = source?.depth >= 0 ? source.depth : 2;
      const targetDepth = target?.depth >= 0 ? target.depth : 2;
      return 74 + Math.abs(sourceDepth - targetDepth) * 10;
    }).strength(0.22))
    .force('charge', d3.forceManyBody().strength((node) => {
      if (node.classification === 'ENTRY') return -900;
      if (node.classification === 'CONNECTED') return -620;
      if (node.classification === 'LEAF') return -380;
      return -260;
    }).distanceMax(520))
    .force('collide', d3.forceCollide().radius((node) => node.classification === 'ENTRY' ? 34 : 24).iterations(2))
    .force('x', d3.forceX((node) => {
      if (node.classification === 'ORPHAN') return width * 0.18;
      if (node.classification === 'ISLAND') return width * 0.82;
      if (node.classification === 'TEST') return width * 0.80;
      if (node.classification === 'GENERATED') return width * 0.26;
      if (entryIds.has(node.id)) return width * 0.48;
      return width * 0.52;
    }).strength((node) => ['ORPHAN','ISLAND','TEST','GENERATED'].includes(node.classification) ? 0.18 : 0.05))
    .force('y', d3.forceY((node) => {
      if (node.classification === 'ORPHAN') return height * 0.42;
      if (node.classification === 'ISLAND') return height * 0.30;
      if (node.classification === 'TEST') return height * 0.66;
      if (node.classification === 'GENERATED') return height * 0.74;
      if (entryIds.has(node.id)) return height * 0.56;
      return height * 0.50;
    }).strength((node) => ['ORPHAN','ISLAND','TEST','GENERATED'].includes(node.classification) ? 0.18 : 0.05))
    .force('radial', d3.forceRadial((node) => {
      if (['ORPHAN','ISLAND','TEST','GENERATED'].includes(node.classification)) return 0;
      const depth = node.depth >= 0 ? node.depth : 3;
      return 40 + depth * 48;
    }, width * 0.52, height * 0.50).strength((node) => ['ORPHAN','ISLAND','TEST','GENERATED'].includes(node.classification) ? 0 : 0.12));
  for (let i = 0; i < 260; i += 1) simulation.tick();
  simulation.stop();
  const positions = {};
  simNodes.forEach((node) => { positions[node.id] = { x: node.x, y: node.y }; });
  return positions;
}

function buildElements() {
  const visibleNodes = (G?.nodes || []).filter(nodeVisible);
  const visibleIds = new Set(visibleNodes.map((node) => normalizeId(node.id)));
  const parents = new Set();
  const elements = [];
  if (clusterMode) {
    visibleNodes.forEach((node) => {
      const dir = dirname(node.id);
      if (!parents.has(dir)) {
        parents.add(dir);
        elements.push({ data: { id: `cluster:${dir}`, label: dir === '.' ? 'root' : basename(dir), isCluster: true } });
      }
    });
  }
  visibleNodes.forEach((node) => {
    elements.push({
      data: {
        id: normalizeId(node.id),
        label: node.name,
        fullpath: normalizeId(node.filepath || node.id),
        classification: node.classification,
        language: node.language,
        depth: node.depth,
        island_id: node.island_id,
        parent: clusterMode ? `cluster:${dirname(node.id)}` : undefined,
        isCluster: false,
      }
    });
  });
  (G?.edges || []).forEach((edge, index) => {
    const source = normalizeId(edge.source.id || edge.source);
    const target = normalizeId(edge.target.id || edge.target);
    if (visibleIds.has(source) && visibleIds.has(target)) {
      elements.push({ data: { id: `e:${index}:${source}:${target}`, source, target, line: edge.line || null } });
    }
  });
  return elements;
}

function makeCy() {
  const elements = buildElements();
  const visibleNodes = (G?.nodes || []).filter(nodeVisible);
  const visibleEdges = (G?.edges || []).filter((edge) => {
    const source = getNodeById(edge.source.id || edge.source);
    const target = getNodeById(edge.target.id || edge.target);
    return source && target && nodeVisible(source) && nodeVisible(target);
  });
  const positions = calculateNodePositions(visibleNodes, visibleEdges);
  if (cy) cy.destroy();
  cy = cytoscape({
    container: document.getElementById('graph'),
    elements,
    wheelSensitivity: 0.12,
    layout: { name: 'preset', fit: true, padding: 48, positions: (node) => positions[node.id()] || { x: 120, y: 120 } },
    style: [
      {
        selector: 'node',
        style: {
          'background-color': (ele) => getTheme(ele.data('classification')).fill,
          'border-width': 2,
          'border-color': (ele) => cycleNodeSet.has(ele.id()) ? '#ffaa00' : getLangColor(ele.data('language')),
          'label': showLabels ? 'data(label)' : '',
          'color': '#6f7fae',
          'font-size': 9,
          'font-weight': 500,
          'text-margin-y': -14,
          'text-wrap': 'none',
          'text-background-opacity': 0,
          'text-outline-color': 'rgba(5,6,13,.9)',
          'text-outline-width': 2,
          'width': (ele) => ele.data('classification') === 'ENTRY' ? 26 : 16,
          'height': (ele) => ele.data('classification') === 'ENTRY' ? 26 : 16,
          'shadow-blur': 0,
          'overlay-opacity': 0,
        }
      },
      {
        selector: '$node > node',
        style: {
          'background-color': 'rgba(176,111,255,.04)',
          'border-color': 'rgba(176,111,255,.22)',
          'border-width': 1,
          'border-style': 'dashed',
          'shape': 'roundrectangle',
          'label': 'data(label)',
          'font-size': 11,
          'font-weight': 700,
          'text-valign': 'top',
          'text-halign': 'left',
          'text-margin-x': 12,
          'text-margin-y': 12,
          'color': '#c18cff',
          'padding': 28,
        }
      },
      {
        selector: 'edge',
        style: {
          'curve-style': 'bezier',
          'line-color': 'rgba(41,57,102,.72)',
          'target-arrow-color': 'rgba(41,57,102,.72)',
          'target-arrow-shape': 'triangle',
          'arrow-scale': .72,
          'width': 1.15,
          'opacity': .84,
        }
      },
      { selector: '.dim', style: { 'opacity': .1, 'text-opacity': .1 } },
      { selector: '.active', style: { 'border-color': '#00e5ff', 'border-width': 3, 'opacity': 1, 'color': '#ffffff', 'text-outline-color': 'rgba(5,6,13,.95)', 'text-outline-width': 2 } },
      { selector: '.cycle', style: { 'border-color': '#ffaa00', 'line-color': '#ffaa00', 'target-arrow-color': '#ffaa00', 'opacity': 1 } },
      { selector: '.match', style: { 'opacity': 1, 'text-opacity': 1, 'line-color': 'rgba(0,229,255,.32)', 'target-arrow-color': 'rgba(0,229,255,.32)' } },
    ]
  });
  cy.on('tap', 'node', (event) => {
    if (event.target.data('isCluster')) return;
    selectNode(event.target.id());
  });
  cy.on('tap', (event) => { if (event.target === cy) clearSelection(); });
  cy.on('render zoom pan', drawMinimap);
  cy.ready(() => { drawMinimap(); });
  setTimeout(drawMinimap, 40);
  setTimeout(drawMinimap, 220);
  applySearchAndSelectionState();
  drawMinimap();
}

function getSearchTreeIds() {
  if (!searchQuery || !G) return null;
  const visibleIds = new Set((G.nodes || []).filter(nodeVisible).map((node) => normalizeId(node.id)));
  const matches = (G.nodes || []).filter((node) => nodeVisible(node) && nodeMatchesSearch(node)).map((node) => normalizeId(node.id));
  if (!matches.length) return new Set();
  const adj = new Map();
  (G.edges || []).forEach((edge) => {
    const source = normalizeId(edge.source.id || edge.source);
    const target = normalizeId(edge.target.id || edge.target);
    if (!visibleIds.has(source) || !visibleIds.has(target)) return;
    if (!adj.has(source)) adj.set(source, new Set());
    if (!adj.has(target)) adj.set(target, new Set());
    adj.get(source).add(target);
    adj.get(target).add(source);
  });
  const seen = new Set(matches);
  const queue = [...matches];
  while (queue.length) {
    const current = queue.shift();
    (adj.get(current) || new Set()).forEach((next) => {
      if (!seen.has(next)) { seen.add(next); queue.push(next); }
    });
  }
  return seen;
}

function applySearchAndSelectionState() {
  if (!cy) return;
  const tree = getSearchTreeIds();
  const hasSearch = !!searchQuery;
  const selected = normalizeId(selectedId || '');
  const matches = hasSearch ? (G.nodes || []).filter((node) => nodeVisible(node) && nodeMatchesSearch(node)).map((node) => normalizeId(node.id)) : [];
  document.getElementById('search-status').textContent = hasSearch ? (matches.length ? `${matches.length} match${matches.length !== 1 ? 'es' : ''} • dependency tree highlighted` : 'No matches') : '';
  cy.nodes().forEach((node) => {
    node.removeClass('dim active cycle match');
    if (node.data('isCluster')) return;
    if (highlightCycles && cycleNodeSet.has(node.id())) node.addClass('cycle');
    if (selected && node.id() === selected) node.addClass('active');
    if (hasSearch) {
      if (tree && tree.has(node.id())) node.addClass('match'); else node.addClass('dim');
    }
  });
  cy.edges().forEach((edge) => {
    edge.removeClass('dim active cycle match');
    const source = edge.data('source');
    const target = edge.data('target');
    if (highlightCycles && cycleNodeSet.has(source) && cycleNodeSet.has(target)) edge.addClass('cycle');
    if (selected && (source === selected || target === selected)) edge.addClass('active');
    if (hasSearch) {
      if (tree && tree.has(source) && tree.has(target)) edge.addClass('match'); else edge.addClass('dim');
    }
  });
  document.querySelectorAll('.list-item').forEach((item) => item.classList.toggle('active', normalizeId(item.dataset.focus) === selected));
}

function kv(key, value) { return `<div class="kv-row"><span class="k">${esc(key)}</span><span class="v">${value}</span></div>`; }

function renderEdgeChips(target, edges, direction) {
  if (!edges.length) {
    target.className = 'empty';
    target.textContent = 'None';
    return;
  }
  target.className = '';
  target.innerHTML = '<div class="chip-cont">' + edges.map((edge) => {
    const id = direction === 'in' ? normalizeId(edge.source.id || edge.source) : normalizeId(edge.target.id || edge.target);
    const line = edge.line ? `:${edge.line}` : '';
    return `<button class="chip ${direction}" data-focus="${esc(id)}" title="${esc(id + line)}">${esc(basename(id))}${line}</button>`;
  }).join('') + '</div>';
  bindFocusButtons(target);
}

function renderInspector(node) {
  if (!node) {
    document.getElementById('iname').textContent = 'Click any node to inspect';
    document.getElementById('iname').className = 'empty';
    document.getElementById('imeta').className = 'empty';
    document.getElementById('imeta').textContent = 'No node selected';
    document.getElementById('icycle').className = 'empty';
    document.getElementById('icycle').textContent = 'No node selected';
    document.getElementById('iout').className = 'empty';
    document.getElementById('iout').textContent = 'No node selected';
    document.getElementById('iin').className = 'empty';
    document.getElementById('iin').textContent = 'No node selected';
    return;
  }
  const nodeId = normalizeId(node.id);
  const inbound = (G.edges || []).filter((edge) => normalizeId(edge.target.id || edge.target) === nodeId && getNodeById(edge.source.id || edge.source) && nodeVisible(getNodeById(edge.source.id || edge.source)));
  const outbound = (G.edges || []).filter((edge) => normalizeId(edge.source.id || edge.source) === nodeId && getNodeById(edge.target.id || edge.target) && nodeVisible(getNodeById(edge.target.id || edge.target)));
  document.getElementById('iname').className = '';
  document.getElementById('iname').textContent = normalizeId(node.filepath || node.id);
  document.getElementById('imeta').className = '';
  document.getElementById('imeta').innerHTML = [
    kv('Class', `<span style="color:${getTheme(node.classification).fill}">${esc(node.classification)}</span>`),
    kv('Language', `<span style="color:${getLangColor(node.language)}">${esc(node.language || '—')}</span>`),
    kv('Size', esc(formatSize(node.size))),
    kv('Inbound', esc(String(inbound.length))),
    kv('Outbound', esc(String(outbound.length))),
    kv('Depth', esc(node.depth >= 0 ? String(node.depth) : '∞')),
    kv('Island', node.island_id >= 0 ? `<span style="color:#b06fff">Cluster ${node.island_id + 1}</span>` : '—'),
  ].join('');
  const cycles = cycleLookup.get(nodeId) || [];
  document.getElementById('icycle').className = '';
  document.getElementById('icycle').innerHTML = cycles.length ? cycles.map((entry) => `<div class="cycle-path">${entry.path.map((id, index) => `${index ? '<span class="cycle-arrow">→</span>' : ''}<span class="chip cycle" data-focus="${esc(id)}">${esc(basename(id))}</span>`).join('')}</div>`).join('') : '<span class="empty">This node is not part of a cycle.</span>';
  bindFocusButtons(document.getElementById('icycle'));
  renderEdgeChips(document.getElementById('iout'), outbound, 'out');
  renderEdgeChips(document.getElementById('iin'), inbound, 'in');
}

function selectNode(id) {
  selectedId = normalizeId(id);
  renderInspector(getNodeById(selectedId));
  applySearchAndSelectionState();
}

function clearSelection() {
  selectedId = null;
  renderInspector(null);
  applySearchAndSelectionState();
}

function focusNode(id) {
  const target = normalizeId(id);
  if (!cy) return;
  const node = cy.getElementById(target);
  if (!node || !node.length) return;
  cy.animate({ center: { eles: node }, zoom: Math.min(2.2, Math.max(cy.zoom(), 1.6)) }, { duration: 500 });
  selectNode(target);
}

function drawMinimap() {
  const canvas = document.getElementById('minimap');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const bg = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
  bg.addColorStop(0, 'rgba(9,14,32,.98)');
  bg.addColorStop(1, 'rgba(4,7,18,.98)');
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = 'rgba(0,229,255,.16)';
  ctx.strokeRect(.5, .5, canvas.width - 1, canvas.height - 1);
  if (!cy || !cy.nodes(':child').length) return;
  const nodes = cy.nodes(':child');
  const xs = nodes.map((node) => node.position('x'));
  const ys = nodes.map((node) => node.position('y'));
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const pad = 12;
  const scale = Math.min((canvas.width - pad * 2) / Math.max(1, maxX - minX), (canvas.height - pad * 2) / Math.max(1, maxY - minY));
  const mapX = (x) => pad + (x - minX) * scale;
  const mapY = (y) => pad + (y - minY) * scale;
  ctx.lineWidth = 1;
  ctx.strokeStyle = 'rgba(42,58,102,.62)';
  cy.edges().forEach((edge) => {
    const s = edge.source().position();
    const t = edge.target().position();
    ctx.beginPath();
    ctx.moveTo(mapX(s.x), mapY(s.y));
    ctx.lineTo(mapX(t.x), mapY(t.y));
    ctx.stroke();
  });
  nodes.forEach((node) => {
    const theme = getTheme(node.data('classification'));
    ctx.fillStyle = theme.fill;
    ctx.beginPath();
    ctx.arc(mapX(node.position('x')), mapY(node.position('y')), node.data('classification') === 'ENTRY' ? 3 : 2, 0, Math.PI * 2);
    ctx.fill();
  });
  const extent = cy.extent();
  ctx.strokeStyle = 'rgba(0,229,255,.85)';
  ctx.fillStyle = 'rgba(0,229,255,.08)';
  const x = mapX(extent.x1), y = mapY(extent.y1), w = Math.max(10, (extent.x2 - extent.x1) * scale), h = Math.max(10, (extent.y2 - extent.y1) * scale);
  ctx.fillRect(x, y, w, h);
  ctx.strokeRect(x, y, w, h);
}

function rerender() {
  if (!G) return;
  buildCycleIndex();
  updateStats();
  renderLanguageMenu();
  renderFilterPanel();
  renderWaste();
  renderCycles();
  makeCy();
  if (selectedId && getNodeById(selectedId) && nodeVisible(getNodeById(selectedId))) renderInspector(getNodeById(selectedId)); else clearSelection();
  updateWarningBanner();
}

function loadGraph(data) {
  G = data;
  activeLangs = null;
  visibleClasses = new Set(DEFAULT_CLASSES);
  clusterMode = false;
  highlightCycles = false;
  searchQuery = '';
  dismissedWarning = false;
  selectedId = null;
  document.getElementById('search-input').value = '';
  document.getElementById('search-box').classList.remove('open');
  document.getElementById('btn-cluster').classList.remove('on');
  rerender();
}

async function walkDirectory(handle, prefix, files) {
  for await (const [name, entry] of handle.entries()) {
    if (entry.kind === 'directory') {
      if (['.git', 'node_modules', '.venv', '__pycache__', '.pytest_cache', '.mypy_cache', 'dist', 'build', 'target'].includes(name)) continue;
      await walkDirectory(entry, prefix ? `${prefix}/${name}` : name, files);
    } else if (entry.kind === 'file') {
      const path = prefix ? `${prefix}/${name}` : name;
      if (/\.(json|py|js|jsx|ts|tsx|go|c|h|cc|cpp|cxx|hpp|hh|java|kt|kts)$/i.test(path)) {
        const file = await entry.getFile();
        if (file.size > 1024 * 1024 * 2) continue;
        files.push({ path, text: await file.text(), size: file.size, mtime: Math.floor(file.lastModified / 1000) });
      }
    }
  }
}

function ensureWorker() {
  if (worker) return worker;
  worker = new Worker('/visualizer_worker.js');
  worker.onmessage = (event) => {
    const { type, payload } = event.data || {};
    if (type === 'progress') showWorkerStatus(payload.message, payload.percent || 0);
    if (type === 'done') { hideWorkerStatus(); loadGraph(payload); }
    if (type === 'error') { hideWorkerStatus(); alert(payload.message || 'Worker analysis failed'); }
  };
  return worker;
}

function startBrowserAnalysis(files, rootName) {
  showWorkerStatus(`Analyzing ${files.length} files in browser...`, 8);
  ensureWorker().postMessage({ type: 'analyzeProject', payload: { files, rootName } });
}

function showWorkerStatus(message, percent) {
  const box = document.getElementById('worker-status');
  box.classList.add('show');
  document.getElementById('worker-message').textContent = message;
  document.querySelector('#worker-bar > div').style.width = `${Math.max(0, Math.min(100, percent || 0))}%`;
}

function hideWorkerStatus() { document.getElementById('worker-status').classList.remove('show'); }

async function openFolder() {
  if (!window.showDirectoryPicker) {
    alert('File System Access API is not available in this browser. Use a Chromium-based browser.');
    return;
  }
  const handle = await window.showDirectoryPicker();
  const files = [];
  showWorkerStatus('Reading files...', 4);
  await walkDirectory(handle, '', files);
  startBrowserAnalysis(files, handle.name || 'workspace');
}

function openGraphFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try { loadGraph(JSON.parse(reader.result)); } catch { alert('Invalid graph.json'); }
  };
  reader.readAsText(file);
}

function bindUi() {
  document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((item) => item.classList.remove('active'));
    document.querySelectorAll('#leftbar .panel-body').forEach((panel) => panel.classList.add('hidden'));
    tab.classList.add('active');
    document.getElementById(tab.dataset.panel).classList.remove('hidden');
  }));
  document.getElementById('btn-waste').onclick = () => { document.getElementById('leftbar').classList.toggle('collapsed'); document.getElementById('btn-waste').classList.toggle('on'); };
  document.getElementById('btn-cycles').onclick = () => { document.querySelector('[data-panel="cycles-panel"]').click(); document.getElementById('leftbar').classList.remove('collapsed'); document.getElementById('btn-waste').classList.add('on'); };
  document.getElementById('btn-inspector').onclick = () => { document.getElementById('inspector-wrap').classList.toggle('collapsed'); document.getElementById('btn-inspector').classList.toggle('on'); };
  document.getElementById('btn-cluster').onclick = () => { clusterMode = !clusterMode; document.getElementById('btn-cluster').classList.toggle('on', clusterMode); rerender(); };
  document.getElementById('btn-labels').onclick = () => { showLabels = !showLabels; document.getElementById('btn-labels').classList.toggle('on', showLabels); if (cy) makeCy(); };
  document.getElementById('btn-search').onclick = () => { document.getElementById('search-box').classList.add('open'); document.getElementById('search-input').focus(); document.getElementById('search-input').select(); };
  document.getElementById('btn-lang').onclick = () => { if (G) document.getElementById('lang-menu').classList.toggle('open'); };
  document.getElementById('btn-filter').onclick = () => { if (G) document.getElementById('filter-panel').classList.toggle('open'); };
  document.getElementById('btn-reset').onclick = () => { if (cy) cy.fit(cy.elements(':visible'), 40); };
  document.getElementById('btn-folder').onclick = () => openFolder().catch((error) => alert(error.message || String(error)));
  document.getElementById('btn-open').onclick = () => document.getElementById('file-input').click();
  document.getElementById('file-input').onchange = (event) => openGraphFile(event.target.files[0]);
  document.getElementById('lang-all').onclick = () => { activeLangs = null; rerender(); };
  document.getElementById('lang-none').onclick = () => { activeLangs = new Set(); rerender(); };
  document.getElementById('filter-all').onclick = () => { visibleClasses = new Set(DEFAULT_CLASSES); rerender(); };
  document.getElementById('filter-dead').onclick = () => { visibleClasses = new Set(['ORPHAN', 'ISLAND']); rerender(); };
  document.getElementById('filter-reset').onclick = () => { visibleClasses = new Set(DEFAULT_CLASSES); rerender(); };
  document.getElementById('warning-dismiss').onclick = () => { dismissedWarning = true; updateWarningBanner(); };
  document.getElementById('search-input').oninput = (event) => { searchQuery = event.target.value.trim(); applySearchAndSelectionState(); };
  document.getElementById('search-input').onkeydown = (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      const first = (G?.nodes || []).find((node) => nodeVisible(node) && nodeMatchesSearch(node));
      if (first) focusNode(first.id);
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      searchQuery = '';
      event.target.value = '';
      document.getElementById('search-box').classList.remove('open');
      applySearchAndSelectionState();
    }
  };
  document.addEventListener('keydown', (event) => {
    const editing = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault(); document.getElementById('btn-search').click();
    } else if (!editing && event.key === '/') {
      event.preventDefault(); document.getElementById('btn-search').click();
    }
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('#lang-menu') && !event.target.closest('#btn-lang')) document.getElementById('lang-menu').classList.remove('open');
    if (!event.target.closest('#filter-panel') && !event.target.closest('#btn-filter')) document.getElementById('filter-panel').classList.remove('open');
  });
  document.addEventListener('dragover', (event) => event.preventDefault());
  document.addEventListener('drop', (event) => { event.preventDefault(); openGraphFile(event.dataTransfer.files[0]); });
  window.addEventListener('resize', () => drawMinimap());
}

bindUi();
window.loadGraph = loadGraph;
if (location.protocol !== 'file:') {
  fetch('/graph.json').then((response) => response.ok ? response.json() : null).then((data) => { if (data?.nodes) loadGraph(data); }).catch(() => {});
}
