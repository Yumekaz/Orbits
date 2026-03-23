(() => {
  const DEFAULT_CLASSES = ['CONNECTED', 'ENTRY', 'LEAF', 'ORPHAN', 'ISLAND', 'TEST', 'GENERATED'];
  const THEME = {
    CONNECTED: { fill: 'rgba(0,229,255,.12)', stroke: '#00e5ff' },
    ENTRY: { fill: 'rgba(0,255,136,.16)', stroke: '#00ff88' },
    LEAF: { fill: 'rgba(255,170,0,.12)', stroke: '#ffaa00' },
    ORPHAN: { fill: 'rgba(255,53,87,.12)', stroke: '#ff3557' },
    ISLAND: { fill: 'rgba(176,111,255,.12)', stroke: '#b06fff' },
    TEST: { fill: 'rgba(109,133,193,.11)', stroke: '#6d85c1' },
    GENERATED: { fill: 'rgba(59,74,112,.1)', stroke: '#3d4a70' }
  };
  const LANG_COLORS = { python: '#3b82f6', javascript: '#f59e0b', typescript: '#06b6d4', tsx: '#22d3ee', go: '#10b981', c: '#fb7185', cpp: '#f97316', java: '#ef4444', kotlin: '#a855f7', generic: '#6b7280' };
  const PKG_HINTS = { python: ['tree-sitter-python'], javascript: ['tree-sitter-javascript', 'tree-sitter-typescript'], typescript: ['tree-sitter-javascript', 'tree-sitter-typescript'], tsx: ['tree-sitter-javascript', 'tree-sitter-typescript'], go: ['tree-sitter-go'], c: ['tree-sitter-c'], cpp: ['tree-sitter-cpp'], java: ['tree-sitter-java'], kotlin: ['tree-sitter-kotlin'] };
  const PERF = {
    largeGraphNodes: 420,
    hugeGraphNodes: 850,
    largeGraphEdges: 1400,
    hugeGraphEdges: 3200,
    maxRenderNodesZoomedOut: 220,
    maxRenderNodesMidZoom: 360,
    maxRenderNodesZoomedIn: 520,
    maxRenderEdgesDense: 900,
    maxRenderEdgesNormal: 1500,
    maxMinimapEdges: 700,
    maxMinimapNodes: 420,
    simRenderIntervalMs: 33
  };
  const ELS = {};
  const APP = {
    graph: null,
    indexes: null,
    layoutPositions: new Map(),
    renderModel: null,
    worker: null,
    workerRequestId: 0,
    pendingRender: 0,
    shellRefreshTimer: 0,
    debounceSearch: 0,
    canvas: null,
    ctx: null,
    zoomBehavior: null,
    dpr: Math.max(1, window.devicePixelRatio || 1),
    draggingNodeId: null,
    dragMoved: false,
    dragInfluence: null,
    dragDelta: { x: 0, y: 0 },
    dragReleaseFrame: 0,
    anchorPositions: new Map(),
    dragOriginPositions: null,
    simulation: null,
    simNodeById: new Map(),
    motionEnabled: true,
    lastSimRenderAt: 0,
    state: createDefaultState()
  };

  function createDefaultState() {
    return {
      selectedId: null,
      hoveredId: null,
      visibleClasses: new Set(DEFAULT_CLASSES),
      activeLangs: null,
      searchQuery: '',
      leftPanel: 'waste-panel',
      layoutMode: 'force',
      edgeMode: 'static',
      focusRadius: 0,
      highlightCycles: false,
      zoom: d3.zoomIdentity,
      showLabels: true,
      showFullGraph: false,
      dismissedWarning: false,
      perfMode: 'auto'
    };
  }



  function graphMetrics() {
    return { nodes: APP.graph?.nodes?.length || 0, edges: APP.graph?.edges?.length || 0 };
  }

  function getPerformanceProfile(nodeCount = 0, edgeCount = 0) {
    const mode = APP.state?.perfMode || 'auto';
    if (mode === 'quality') {
      return {
        motionNodes: 700,
        motionEdges: 2200,
        maxRenderNodesZoomedOut: 280,
        maxRenderNodesMidZoom: 460,
        maxRenderNodesZoomedIn: 700,
        maxRenderEdgesDense: 1200,
        maxRenderEdgesNormal: 2200,
        maxMinimapEdges: 1000,
        maxMinimapNodes: 550,
        autoCollapse: false
      };
    }
    if (mode === 'safe') {
      return {
        motionNodes: 260,
        motionEdges: 900,
        maxRenderNodesZoomedOut: 140,
        maxRenderNodesMidZoom: 240,
        maxRenderNodesZoomedIn: 360,
        maxRenderEdgesDense: 500,
        maxRenderEdgesNormal: 900,
        maxMinimapEdges: 360,
        maxMinimapNodes: 220,
        autoCollapse: true
      };
    }
    const extraLarge = nodeCount > PERF.hugeGraphNodes || edgeCount > PERF.hugeGraphEdges;
    return {
      motionNodes: PERF.largeGraphNodes,
      motionEdges: PERF.largeGraphEdges,
      maxRenderNodesZoomedOut: extraLarge ? 170 : PERF.maxRenderNodesZoomedOut,
      maxRenderNodesMidZoom: extraLarge ? 280 : PERF.maxRenderNodesMidZoom,
      maxRenderNodesZoomedIn: extraLarge ? 420 : PERF.maxRenderNodesZoomedIn,
      maxRenderEdgesDense: extraLarge ? 650 : PERF.maxRenderEdgesDense,
      maxRenderEdgesNormal: extraLarge ? 1100 : PERF.maxRenderEdgesNormal,
      maxMinimapEdges: extraLarge ? 420 : PERF.maxMinimapEdges,
      maxMinimapNodes: extraLarge ? 260 : PERF.maxMinimapNodes,
      autoCollapse: extraLarge
    };
  }

  function applyLargeGraphDefaults() {
    const metrics = graphMetrics();
    const profile = getPerformanceProfile(metrics.nodes, metrics.edges);
    if (!profile.autoCollapse) return;
    APP.state.layoutMode = 'cluster';
    APP.state.showLabels = false;
    APP.state.showFullGraph = false;
  }

  function $(id) { return document.getElementById(id); }
  function normalizeId(id) { return String(id || '').replace(/\\/g, '/'); }
  function basename(id) { const parts = normalizeId(id).split('/'); return parts[parts.length - 1] || id; }
  function dirname(id) { const parts = normalizeId(id).split('/'); parts.pop(); return parts.join('/') || '.'; }
  function escapeHtml(value) { return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
  function displayLang(lang) { return ({ javascript: 'JavaScript', typescript: 'TypeScript', tsx: 'TSX', python: 'Python', go: 'Go', c: 'C', cpp: 'C/C++', java: 'Java', kotlin: 'Kotlin', generic: 'Generic' })[lang] || lang || 'Unknown'; }
  function getTheme(cls) { return THEME[cls] || THEME.ORPHAN; }
  function getLangColor(lang) { return LANG_COLORS[(lang || '').toLowerCase()] || '#6b7280'; }
  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }
  function formatSize(bytes) { if (!bytes) return '0 B'; if (bytes < 1024) return `${bytes} B`; if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`; return `${(bytes / 1048576).toFixed(1)} MB`; }
  function formatDate(ts) { if (!ts) return '—'; const d = new Date(ts * 1000); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString(); }
  function setEquals(a, b) { if (a.size !== b.size) return false; for (const item of a) if (!b.has(item)) return false; return true; }
  function getCanvasSize() { return { width: ELS.canvasWrap.clientWidth || 1000, height: ELS.canvasWrap.clientHeight || 800 }; }
  function cacheKey(hash, mode) { return `orbits.layout.${hash}.${mode}`; }
  function currentLanguageSet() { return APP.state.activeLangs === null ? APP.indexes?.allLanguages || new Set() : APP.state.activeLangs; }
  function nodeVisible(node) { return APP.state.visibleClasses.has(node.classification) && (!node.language || currentLanguageSet().has(node.language)); }

  function captureElements() {
    ['graph-canvas', 'canvas-wrap', 'minimap', 'drop-overlay', 'search-box', 'search-input', 'search-status', 'warning-banner', 'warning-copy', 'worker-status', 'worker-message', 'health-pill', 'elapsed', 'perf-mode', 'toast', 'btn-waste', 'btn-cycles', 'btn-insp', 'btn-cluster', 'btn-labels', 'btn-filter', 'btn-lang', 'btn-search', 'btn-reset', 'btn-folder', 'btn-open', 'btn-cycle-highlight', 'btn-focus', 'btn-full', 'btn-perf', 'layout-menu', 'layout-force', 'layout-cluster', 'layout-radial', 'edge-mode-static', 'edge-mode-runtime', 'edge-mode-combined', 'lang-menu', 'lang-chip-grid', 'filter-panel', 'filter-chip-grid', 'waste-list', 'waste-badge', 'cycle-list', 'cycle-badge', 'left-rail', 'inspector', 'ip', 'ic', 'iname', 'imeta', 'ihistory', 'icycle', 'iout', 'iin', 'file-input', 'warning-dismiss', 'lang-all', 'lang-none', 'filter-all', 'filter-waste', 'filter-default', 's-files', 's-edges', 's-orphans', 's-cycles', 's-health', 's-unreachable', 's-max-depth', 's-resolved', 's-langs'].forEach((id) => { ELS[id] = $(id); });
    ELS.canvasWrap = $('canvas-wrap');
    APP.canvas = ELS['graph-canvas'];
    APP.ctx = APP.canvas.getContext('2d');
  }

  function simpleHash(value) {
    let hash = 2166136261;
    for (let i = 0; i < value.length; i += 1) {
      hash ^= value.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16);
  }

  function graphHash(graph) {
    const nodes = [...graph.nodes].map((n) => normalizeId(n.id)).sort();
    const edges = [...graph.edges].map((e) => `${normalizeId(e.source)}>${normalizeId(e.target)}:${e.line || 0}`).sort();
    return simpleHash(`${nodes.join('|')}::${edges.join('|')}`);
  }

  function normalizeEdge(edge, defaultOrigin) {
    const origins = new Set(Array.isArray(edge.origins) ? edge.origins : []);
    if (defaultOrigin) origins.add(defaultOrigin);
    const runtime = origins.has('runtime') || !!edge.runtime;
    const isStatic = origins.has('static') || defaultOrigin === 'static';
    const runtimeHits = Number(edge.runtime_hits || edge.count || 0);
    return {
      ...edge,
      source: normalizeId(edge.source.id || edge.source),
      target: normalizeId(edge.target.id || edge.target),
      line: edge.line || null,
      type: edge.type || (runtime && !isStatic ? 'runtime_import' : 'import'),
      language: edge.language || 'python',
      origins: [...origins],
      runtime,
      static: isStatic,
      dynamic: edge.dynamic ?? (runtime && !isStatic),
      runtime_hits: runtimeHits,
      runtime_modules: Array.isArray(edge.runtime_modules) ? edge.runtime_modules : [],
      runtime_lines: Array.isArray(edge.runtime_lines) ? edge.runtime_lines : []
    };
  }

  function edgeKey(edge) {
    return `${normalizeId(edge.source)}>>${normalizeId(edge.target)}`;
  }

  function buildEdgeIndexes(edges, nodeById) {
    const outboundByNode = new Map();
    const inboundByNode = new Map();
    const edgeListByNode = new Map();
    const neighborsByNode = new Map();
    nodeById.forEach((_node, id) => {
      outboundByNode.set(id, []);
      inboundByNode.set(id, []);
      edgeListByNode.set(id, []);
      neighborsByNode.set(id, new Set());
    });
    edges.forEach((edge) => {
      if (!nodeById.has(edge.source) || !nodeById.has(edge.target)) return;
      outboundByNode.get(edge.source).push(edge);
      inboundByNode.get(edge.target).push(edge);
      edgeListByNode.get(edge.source).push(edge);
      edgeListByNode.get(edge.target).push(edge);
      neighborsByNode.get(edge.source)?.add(edge.target);
      neighborsByNode.get(edge.target)?.add(edge.source);
    });
    return { outboundByNode, inboundByNode, edgeListByNode, neighborsByNode };
  }

  function mergeEdgeSets(staticEdges, dynamicEdges) {
    const merged = new Map();
    staticEdges.forEach((edge) => {
      merged.set(edgeKey(edge), {
        ...edge,
        origins: [...new Set(edge.origins || ['static'])],
        static: true,
        runtime: false,
        dynamic: false,
        runtime_hits: edge.runtime_hits || 0,
        runtime_modules: [...(edge.runtime_modules || [])],
        runtime_lines: [...(edge.runtime_lines || [])]
      });
    });
    dynamicEdges.forEach((edge) => {
      const key = edgeKey(edge);
      const existing = merged.get(key);
      if (!existing) {
        merged.set(key, { ...edge, origins: [...new Set([...(edge.origins || []), 'runtime'])], static: false, runtime: true, dynamic: true });
        return;
      }
      merged.set(key, {
        ...existing,
        runtime: true,
        dynamic: false,
        origins: [...new Set([...(existing.origins || []), 'runtime'])],
        runtime_hits: (existing.runtime_hits || 0) + (edge.runtime_hits || 0),
        runtime_modules: [...new Set([...(existing.runtime_modules || []), ...(edge.runtime_modules || [])])],
        runtime_lines: [...new Set([...(existing.runtime_lines || []), ...(edge.runtime_lines || [])])]
      });
    });
    return [...merged.values()].sort((a, b) => edgeKey(a).localeCompare(edgeKey(b)));
  }

  function activeEdgeMode() {
    const hasRuntime = !!(APP.graph?.dynamic_edges?.length);
    if (!hasRuntime) return 'static';
    return ['static', 'runtime', 'combined'].includes(APP.state.edgeMode) ? APP.state.edgeMode : 'combined';
  }

  function activeEdgeBundle() {
    return APP.indexes?.edgeSets?.[activeEdgeMode()] || APP.indexes?.edgeSets?.static || {
      edges: [],
      outboundByNode: new Map(),
      inboundByNode: new Map(),
      edgeListByNode: new Map(),
      neighborsByNode: new Map()
    };
  }



  function getSearchTokens(node) {
    const full = normalizeId(node.filepath || node.id).toLowerCase();
    const base = basename(node.id).toLowerCase();
    return { base, full, tokens: new Set(`${base} ${full}`.split(/[^a-z0-9_./-]+/i).filter(Boolean)) };
  }

  function showWorkerStatus(message, percent) {
    ELS['worker-status'].classList.add('show');
    ELS['worker-message'].textContent = message;
    document.querySelector('#worker-bar > div').style.width = `${clamp(percent || 0, 0, 100)}%`;
  }

  function hideWorkerStatus() { ELS['worker-status'].classList.remove('show'); }

  function ensureWorker() {
    if (APP.worker) return APP.worker;
    APP.worker = new Worker('visualizer_worker.js');
    APP.worker.onmessage = handleWorkerMessage;
    return APP.worker;
  }

  function handleWorkerMessage(event) {
    const { type, payload } = event.data || {};
    if (type === 'progress') showWorkerStatus(payload.message, payload.percent || 0);
    if (type === 'done') {
      hideWorkerStatus();
      setGraphData(payload, { source: 'browser-analysis' });
      toast('Folder analyzed in browser');
      return;
    }
    if (type === 'layout-done') {
      if (!payload || payload.requestId !== APP.workerRequestId) return;
      hideWorkerStatus();
      APP.layoutPositions = new Map(Object.entries(payload.positions || {}).map(([id, pos]) => [normalizeId(id), { x: pos.x, y: pos.y }]));
      APP.anchorPositions = new Map([...APP.layoutPositions.entries()].map(([id, pos]) => [id, { x: pos.x, y: pos.y }]));
      try { localStorage.setItem(cacheKey(APP.indexes.hash, APP.state.layoutMode), JSON.stringify(payload.positions || {})); } catch { }
      restartMotionSimulation();
      scheduleRender();
      return;
    }
    if (type === 'error') { hideWorkerStatus(); toast(payload.message || 'Worker failed'); }
  }

  function persistLayoutPositions() {
    if (!APP.indexes?.hash || !APP.layoutPositions?.size) return;
    try {
      const payload = Object.fromEntries([...APP.layoutPositions.entries()].map(([id, pos]) => [id, { x: pos.x, y: pos.y }]));
      localStorage.setItem(cacheKey(APP.indexes.hash, APP.state.layoutMode), JSON.stringify(payload));
    } catch { }
  }

  function buildIndexes(graph) {
    const nodeById = new Map();
    const nodesByLanguage = new Map();
    const nodesByClass = new Map();
    const cycleMembershipByNode = new Map();
    const searchByNode = new Map();

    graph.nodes.forEach((node) => {
      const normalized = { ...node, id: normalizeId(node.id), filepath: normalizeId(node.filepath || node.id), dir: node.dir || dirname(node.id) };
      nodeById.set(normalized.id, normalized);
      if (normalized.language) {
        if (!nodesByLanguage.has(normalized.language)) nodesByLanguage.set(normalized.language, new Set());
        nodesByLanguage.get(normalized.language).add(normalized.id);
      }
      if (!nodesByClass.has(normalized.classification)) nodesByClass.set(normalized.classification, new Set());
      nodesByClass.get(normalized.classification).add(normalized.id);
      searchByNode.set(normalized.id, getSearchTokens(normalized));
    });

    const staticEdges = (graph.edges || []).map((edge) => normalizeEdge(edge, 'static')).filter((edge) => nodeById.has(edge.source) && nodeById.has(edge.target));
    const dynamicEdges = (graph.dynamic_edges || []).map((edge) => normalizeEdge(edge, 'runtime')).filter((edge) => nodeById.has(edge.source) && nodeById.has(edge.target));
    const combinedEdges = mergeEdgeSets(staticEdges, dynamicEdges);

    const staticBundle = buildEdgeIndexes(staticEdges, nodeById);
    const dynamicBundle = buildEdgeIndexes(dynamicEdges, nodeById);
    const combinedBundle = buildEdgeIndexes(combinedEdges, nodeById);

    (graph.cycles || []).forEach((cycle, index) => {
      cycle.map(normalizeId).forEach((id) => {
        if (!cycleMembershipByNode.has(id)) cycleMembershipByNode.set(id, []);
        cycleMembershipByNode.get(id).push({ index, path: cycle.map(normalizeId) });
      });
    });

    nodeById.forEach((node, id) => {
      const outbound = staticBundle.outboundByNode.get(id)?.length || 0;
      const inbound = staticBundle.inboundByNode.get(id)?.length || 0;
      const degree = outbound + inbound;
      const cycleBoost = cycleMembershipByNode.has(id) ? 3 : 0;
      const depthBoost = node.depth >= 0 ? Math.max(0, 6 - Math.min(node.depth, 6)) : 0;
      const entryBoost = node.classification === 'ENTRY' ? 5 : 0;
      const leafPenalty = node.classification === 'LEAF' ? -1.5 : 0;
      node.importance = degree * 1.6 + cycleBoost + depthBoost + entryBoost + leafPenalty;
    });

    return {
      nodeById,
      edges: staticEdges,
      dynamicEdges,
      combinedEdges,
      edgeSets: {
        static: { edges: staticEdges, ...staticBundle },
        runtime: { edges: dynamicEdges, ...dynamicBundle },
        combined: { edges: combinedEdges, ...combinedBundle }
      },
      outboundByNode: staticBundle.outboundByNode,
      inboundByNode: staticBundle.inboundByNode,
      edgeListByNode: staticBundle.edgeListByNode,
      neighborsByNode: staticBundle.neighborsByNode,
      nodesByLanguage,
      nodesByClass,
      cycleMembershipByNode,
      searchByNode,
      allLanguages: new Set([...nodesByLanguage.keys()]),
      hash: graphHash({ nodes: [...nodeById.values()], edges: staticEdges })
    };
  }



  function setGraphData(data, options = {}) {
    APP.graph = {
      ...data,
      nodes: (data.nodes || []).map((node) => ({ ...node, id: normalizeId(node.id), filepath: normalizeId(node.filepath || node.id), dir: node.dir || dirname(node.id), name: node.name || basename(node.id) })),
      edges: (data.edges || []).map((edge) => ({ ...edge, source: normalizeId(edge.source.id || edge.source), target: normalizeId(edge.target.id || edge.target), line: edge.line || null })),
      dynamic_edges: (data.dynamic_edges || []).map((edge) => ({ ...edge, source: normalizeId(edge.source.id || edge.source), target: normalizeId(edge.target.id || edge.target), line: edge.line || null })),
      cycles: (data.cycles || []).map((cycle) => cycle.map(normalizeId)),
      waste: (data.waste || []).map((node) => ({ ...node, id: normalizeId(node.id) })),
      runtime: data.runtime || null
    };
    APP.indexes = buildIndexes(APP.graph);
    APP.graph.nodes = APP.graph.nodes.map((node) => ({ ...node, importance: APP.indexes.nodeById.get(node.id)?.importance || 0 }));
    stopSimulation();
    APP.layoutPositions = new Map();
    APP.anchorPositions = new Map();
    APP.dragOriginPositions = null;
    const previousPerfMode = APP.state?.perfMode || 'auto';
    APP.state = createDefaultState();
    APP.state.perfMode = previousPerfMode;
    APP.state.visibleClasses = new Set(DEFAULT_CLASSES);
    APP.state.showLabels = true;
    APP.state.showFullGraph = APP.graph.nodes.length <= 220;
    APP.state.edgeMode = APP.graph.dynamic_edges.length ? 'combined' : 'static';
    applyLargeGraphDefaults();
    ELS['search-input'].value = '';
    ELS['drop-overlay'].classList.add('hidden');
    updateUnsupportedBanner();
    renderLanguageMenu();
    renderFilterPanel();
    updateStats();
    updateLeftPanel();
    updateInspector();
    syncShellState();
    requestLayout();
    if ((APP.graph.summary?.cycle_count || 0) > 0 && options.source !== 'browser-analysis') toast(`Alert: ${APP.graph.summary.cycle_count} circular dependencies found!`);
    if (APP.graph.dynamic_edges.length && options.source !== 'browser-analysis') {
      const runtimeMeta = APP.graph.meta?.runtime || {};
      const count = runtimeMeta.dynamic_edges || APP.graph.dynamic_edges.length;
      toast(`Runtime trace loaded: ${count} dynamic edge${count === 1 ? '' : 's'}`);
    }
  }



  function requestLayout() {
    if (!APP.graph || !APP.indexes) return;
    stopSimulation();
    const { width, height } = getCanvasSize();
    let cachedPositions = null;
    try {
      const raw = localStorage.getItem(cacheKey(APP.indexes.hash, APP.state.layoutMode));
      if (raw) {
        cachedPositions = JSON.parse(raw);
        APP.layoutPositions = new Map(Object.entries(cachedPositions).map(([id, pos]) => [normalizeId(id), { x: pos.x, y: pos.y }]));
        APP.anchorPositions = new Map([...APP.layoutPositions.entries()].map(([id, pos]) => [id, { x: pos.x, y: pos.y }]));
        restartMotionSimulation();
        scheduleRender();
      }
    } catch { }
    APP.workerRequestId += 1;
    showWorkerStatus('Laying out graph…', 12);
    ensureWorker().postMessage({ type: 'layoutGraph', payload: { requestId: APP.workerRequestId, width, height, layoutMode: APP.state.layoutMode, nodes: APP.graph.nodes.map((node) => ({ id: node.id, dir: node.dir, classification: node.classification, depth: node.depth, importance: node.importance || 0 })), edges: APP.indexes.edges.map((edge) => ({ source: edge.source, target: edge.target })), cachedPositions } });
  }

  function stopSimulation() {
    if (APP.simulation) {
      APP.simulation.on('tick', null);
      APP.simulation.stop();
      APP.simulation = null;
    }
    APP.simNodeById = new Map();
    APP.lastSimRenderAt = 0;
  }

  function updateMotionMode(nodeCount, edgeCount) {
    const profile = getPerformanceProfile(nodeCount, edgeCount);
    APP.motionEnabled = nodeCount <= profile.motionNodes && edgeCount <= profile.motionEdges;
  }

  function motionTickShouldRender() {
    const now = performance.now();
    if (!APP.lastSimRenderAt || now - APP.lastSimRenderAt >= PERF.simRenderIntervalMs) {
      APP.lastSimRenderAt = now;
      return true;
    }
    return false;
  }

  function restartMotionSimulation() {
    stopSimulation();
    if (!APP.graph || !APP.indexes || !APP.anchorPositions.size || !d3.forceSimulation) return;
    const simNodes = APP.graph.nodes.filter(nodeVisible).map((node) => {
      const pos = APP.layoutPositions.get(node.id) || APP.anchorPositions.get(node.id) || { x: 0, y: 0 };
      return { ...node, x: pos.x, y: pos.y };
    });
    if (!simNodes.length) return;
    const nodeIds = new Set(simNodes.map((node) => node.id));
    const simEdges = APP.indexes.edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)).map((edge) => ({ source: edge.source, target: edge.target }));
    APP.simNodeById = new Map(simNodes.map((node) => [node.id, node]));
    updateMotionMode(simNodes.length, simEdges.length);
    if (!APP.motionEnabled) return;
    const linkDistance = APP.state.layoutMode === 'cluster' ? 86 : APP.state.layoutMode === 'radial' ? 96 : 104;
    const linkStrength = APP.state.layoutMode === 'cluster' ? 0.2 : 0.15;
    const anchorStrength = APP.state.layoutMode === 'cluster' ? 0.15 : APP.state.layoutMode === 'radial' ? 0.12 : 0.085;
    const charge = APP.state.layoutMode === 'cluster' ? -180 : -235;
    APP.simulation = d3.forceSimulation(simNodes)
      .alpha(0.18)
      .alphaDecay(0.065)
      .velocityDecay(0.34)
      .force('link', d3.forceLink(simEdges).id((node) => node.id).distance(linkDistance).strength(linkStrength))
      .force('charge', d3.forceManyBody().strength(charge).distanceMax(520))
      .force('collide', d3.forceCollide((node) => node.classification === 'ENTRY' ? 22 : 15).iterations(2))
      .force('x', d3.forceX((node) => APP.anchorPositions.get(node.id)?.x ?? node.x).strength(anchorStrength))
      .force('y', d3.forceY((node) => APP.anchorPositions.get(node.id)?.y ?? node.y).strength(anchorStrength))
      .on('tick', () => {
        simNodes.forEach((node) => {
          APP.layoutPositions.set(node.id, { x: node.x, y: node.y });
        });
        if (motionTickShouldRender()) scheduleRender();
        if (APP.simulation?.alpha() < 0.03) stopSimulation();
      });
  }

  function scheduleRender() {
    if (APP.pendingRender) return;
    APP.pendingRender = requestAnimationFrame(() => {
      APP.pendingRender = 0;
      renderGraph();
    });
  }

  function resizeCanvas() {
    const { width, height } = getCanvasSize();
    APP.dpr = Math.max(1, window.devicePixelRatio || 1);
    APP.canvas.width = Math.round(width * APP.dpr);
    APP.canvas.height = Math.round(height * APP.dpr);
    APP.canvas.style.width = `${width}px`;
    APP.canvas.style.height = `${height}px`;
    APP.ctx.setTransform(APP.dpr, 0, 0, APP.dpr, 0, 0);
    scheduleRender();
  }

  function getViewportBounds(pad = 80) {
    const { width, height } = getCanvasSize();
    const t = APP.state.zoom;
    return { minX: (-t.x) / t.k - pad, minY: (-t.y) / t.k - pad, maxX: (width - t.x) / t.k + pad, maxY: (height - t.y) / t.k + pad };
  }

  function buildSearchTreeSet(matchIds, allowedIds, neighborsByNode) {
    if (!matchIds.length) return new Set();
    const seen = new Set(matchIds);
    const queue = [...matchIds];
    while (queue.length) {
      const id = queue.shift();
      (neighborsByNode.get(id) || new Set()).forEach((next) => {
        if (!allowedIds.has(next) || seen.has(next)) return;
        seen.add(next);
        queue.push(next);
      });
    }
    return seen;
  }

  function buildFocusSet(selectedId, radius, allowedIds, neighborsByNode) {
    if (!selectedId || radius <= 0) return null;
    const seen = new Set([selectedId]);
    let frontier = new Set([selectedId]);
    for (let depth = 0; depth < radius; depth += 1) {
      const next = new Set();
      frontier.forEach((id) => {
        (neighborsByNode.get(id) || new Set()).forEach((neighbor) => {
          if (!allowedIds.has(neighbor) || seen.has(neighbor)) return;
          seen.add(neighbor);
          next.add(neighbor);
        });
      });
      frontier = next;
      if (!frontier.size) break;
    }
    return seen;
  }



  function buildDragInfluence(rootId, maxDepth = 3) {
    if (!rootId) return new Map();
    const influence = new Map([[rootId, 1]]);
    const queue = [{ id: rootId, depth: 0 }];
    while (queue.length) {
      const current = queue.shift();
      if (current.depth >= maxDepth) continue;
      (APP.indexes.neighborsByNode.get(current.id) || new Set()).forEach((neighbor) => {
        if (influence.has(neighbor)) return;
        const nextDepth = current.depth + 1;
        const factor = nextDepth === 1 ? 0.22 : nextDepth === 2 ? 0.1 : 0.035;
        influence.set(neighbor, factor);
        queue.push({ id: neighbor, depth: nextDepth });
      });
    }
    return influence;
  }

  function captureDragOrigins(influence) {
    APP.dragOriginPositions = new Map();
    (influence || new Map()).forEach((_factor, id) => {
      const source = APP.anchorPositions.get(id) || APP.layoutPositions.get(id);
      if (!source) return;
      APP.dragOriginPositions.set(id, { x: source.x, y: source.y });
    });
  }

  function applyDragPositions(rootId, pointerPos) {
    if (!rootId || !APP.dragOriginPositions?.size) return;
    const rootOrigin = APP.dragOriginPositions.get(rootId);
    if (!rootOrigin) return;
    const dx = pointerPos.x - rootOrigin.x;
    const dy = pointerPos.y - rootOrigin.y;
    APP.dragDelta = { x: dx, y: dy };
    (APP.dragInfluence || new Map([[rootId, 1]])).forEach((factor, id) => {
      const origin = APP.dragOriginPositions.get(id);
      if (!origin) return;
      if (id === rootId) {
        APP.layoutPositions.set(id, { x: pointerPos.x, y: pointerPos.y });
        return;
      }
      const targetX = origin.x + dx * factor;
      const targetY = origin.y + dy * factor;
      const current = APP.layoutPositions.get(id) || origin;
      const follow = 0.28 + factor * 0.45;
      APP.layoutPositions.set(id, {
        x: current.x + (targetX - current.x) * follow,
        y: current.y + (targetY - current.y) * follow
      });
    });
  }

  function animateDragSettle(rootId, influence) {
    if (APP.dragReleaseFrame) cancelAnimationFrame(APP.dragReleaseFrame);
    if (!rootId || !influence?.size) return;
    const rootPos = APP.layoutPositions.get(rootId);
    if (rootPos) APP.anchorPositions.set(rootId, { x: rootPos.x, y: rootPos.y });
    let frame = 0;
    const tick = () => {
      frame += 1;
      let moving = false;
      influence.forEach((factor, id) => {
        if (id === rootId) return;
        const target = APP.anchorPositions.get(id);
        const pos = APP.layoutPositions.get(id);
        if (!target || !pos) return;
        const stiffness = 0.26 + (1 - factor) * 0.18;
        const nx = pos.x + (target.x - pos.x) * stiffness;
        const ny = pos.y + (target.y - pos.y) * stiffness;
        if (Math.abs(target.x - nx) > 0.2 || Math.abs(target.y - ny) > 0.2) moving = true;
        APP.layoutPositions.set(id, { x: nx, y: ny });
      });
      scheduleRender();
      if (moving && frame < 18) {
        APP.dragReleaseFrame = requestAnimationFrame(tick);
      } else {
        influence.forEach((_factor, id) => {
          if (id === rootId) return;
          const target = APP.anchorPositions.get(id);
          if (target) APP.layoutPositions.set(id, { x: target.x, y: target.y });
        });
        APP.dragReleaseFrame = 0;
        persistLayoutPositions();
        scheduleRender();
      }
    };
    APP.dragReleaseFrame = requestAnimationFrame(tick);
  }

  function commitDraggedNodePosition(nodeId) {
    if (!nodeId) return;
    const simNode = APP.simNodeById.get(nodeId);
    const pos = simNode ? { x: simNode.x, y: simNode.y } : APP.layoutPositions.get(nodeId);
    if (!pos || !Number.isFinite(pos.x) || !Number.isFinite(pos.y)) return;
    APP.layoutPositions.set(nodeId, { x: pos.x, y: pos.y });
    APP.anchorPositions.set(nodeId, { x: pos.x, y: pos.y });
    persistLayoutPositions();
  }

  function nodePriority(node, selectedId, searchTree) {
    let score = node.importance || 0;
    if (node.id === selectedId) score += 1000;
    if (searchTree?.has(node.id)) score += 500;
    if (node.classification === 'ENTRY') score += 120;
    if (APP.indexes.cycleMembershipByNode.has(node.id)) score += 90;
    return score;
  }

  function capNodesForRender(nodes, selectedId, searchTree, zoomK) {
    const profile = getPerformanceProfile(APP.graph?.nodes?.length || nodes.length, APP.graph?.edges?.length || 0);
    const cap = zoomK < 0.45 ? profile.maxRenderNodesZoomedOut : zoomK < 1 ? profile.maxRenderNodesMidZoom : profile.maxRenderNodesZoomedIn;
    if (nodes.length <= cap) return nodes;
    return [...nodes]
      .sort((a, b) => nodePriority(b, selectedId, searchTree) - nodePriority(a, selectedId, searchTree))
      .slice(0, cap);
  }

  function edgePriority(edge, selectedId, searchTree) {
    let score = 0;
    if (selectedId && (edge.source === selectedId || edge.target === selectedId)) score += 1000;
    if (searchTree && searchTree.has(edge.source) && searchTree.has(edge.target)) score += 500;
    score += (APP.indexes.nodeById.get(edge.source)?.importance || 0) + (APP.indexes.nodeById.get(edge.target)?.importance || 0);
    return score;
  }

  function capEdgesForRender(edges, selectedId, searchTree, dense) {
    const profile = getPerformanceProfile(APP.graph?.nodes?.length || 0, APP.graph?.edges?.length || edges.length);
    const cap = dense ? profile.maxRenderEdgesDense : profile.maxRenderEdgesNormal;
    if (edges.length <= cap) return edges;
    return [...edges]
      .sort((a, b) => edgePriority(b, selectedId, searchTree) - edgePriority(a, selectedId, searchTree))
      .slice(0, cap);
  }

  function withinViewport(node, bounds) {
    const pos = APP.layoutPositions.get(node.id);
    if (!pos) return false;
    return pos.x >= bounds.minX && pos.x <= bounds.maxX && pos.y >= bounds.minY && pos.y <= bounds.maxY;
  }

  function segmentVisible(edge, bounds) {
    const source = APP.layoutPositions.get(edge.source);
    const target = APP.layoutPositions.get(edge.target);
    if (!source || !target) return false;
    if ((source.x >= bounds.minX && source.x <= bounds.maxX && source.y >= bounds.minY && source.y <= bounds.maxY) || (target.x >= bounds.minX && target.x <= bounds.maxX && target.y >= bounds.minY && target.y <= bounds.maxY)) return true;
    const minX = Math.min(source.x, target.x);
    const maxX = Math.max(source.x, target.x);
    const minY = Math.min(source.y, target.y);
    const maxY = Math.max(source.y, target.y);
    return !(maxX < bounds.minX || minX > bounds.maxX || maxY < bounds.minY || minY > bounds.maxY);
  }

  function deriveRenderModel() {
    if (!APP.graph || !APP.indexes) return null;
    const edgeBundle = activeEdgeBundle();
    const activeEdges = edgeBundle.edges;
    const baseNodes = APP.graph.nodes.filter(nodeVisible);
    const baseIds = new Set(baseNodes.map((node) => node.id));
    const matchedNodes = APP.state.searchQuery ? baseNodes.filter((node) => {
      const search = APP.indexes.searchByNode.get(node.id);
      const q = APP.state.searchQuery.toLowerCase();
      return search.base.includes(q) || search.full.includes(q) || [...search.tokens].some((token) => token.includes(q));
    }) : [];
    const searchTree = APP.state.searchQuery ? buildSearchTreeSet(matchedNodes.map((node) => node.id), baseIds, edgeBundle.neighborsByNode) : null;
    const focusSet = buildFocusSet(APP.state.selectedId, APP.state.focusRadius, baseIds, edgeBundle.neighborsByNode);
    let workingNodes = focusSet ? baseNodes.filter((node) => focusSet.has(node.id)) : baseNodes;
    const hugeGraph = baseNodes.length > 220;
    const zoomK = APP.state.zoom.k;
    let clusterOnly = false;
    if (hugeGraph && !APP.state.showFullGraph && !APP.state.searchQuery && !APP.state.selectedId) {
      if (zoomK < 0.38) {
        clusterOnly = true;
        workingNodes = workingNodes.filter((node) => node.importance >= 4 || node.classification === 'ENTRY' || APP.indexes.cycleMembershipByNode.has(node.id));
      } else if (zoomK < 0.82) {
        workingNodes = workingNodes.filter((node) => node.importance >= 2.8 || node.classification !== 'LEAF' || APP.indexes.cycleMembershipByNode.has(node.id));
      }
    }
    const workingIds = new Set(workingNodes.map((node) => node.id));
    const workingEdges = activeEdges.filter((edge) => workingIds.has(edge.source) && workingIds.has(edge.target));
    const bounds = getViewportBounds();
    let renderNodes = workingNodes.filter((node) => withinViewport(node, bounds) || node.id === APP.state.selectedId || (searchTree && searchTree.has(node.id)));
    if (hugeGraph) renderNodes = capNodesForRender(renderNodes, APP.state.selectedId, searchTree, zoomK);
    const renderIds = new Set(renderNodes.map((node) => node.id));
    let renderEdges = workingEdges.filter((edge) => {
      if (APP.state.selectedId && (edge.source === APP.state.selectedId || edge.target === APP.state.selectedId)) return true;
      if (searchTree && searchTree.has(edge.source) && searchTree.has(edge.target)) return true;
      return segmentVisible(edge, bounds) && renderIds.has(edge.source) && renderIds.has(edge.target);
    });
    let dense = renderNodes.length > 240 || renderEdges.length > 700;
    if (hugeGraph || renderEdges.length > PERF.maxRenderEdgesNormal) renderEdges = capEdgesForRender(renderEdges, APP.state.selectedId, searchTree, dense);
    dense = renderNodes.length > 240 || renderEdges.length > 700;
    const showLabels = APP.state.showLabels && (zoomK > 0.62 || APP.state.selectedId || APP.state.searchQuery) && !(hugeGraph && zoomK < 0.52 && !APP.state.selectedId && !APP.state.searchQuery);
    return { baseNodes, workingNodes, workingEdges, renderNodes, renderEdges, matchedNodes, searchTree, bounds, dense, showLabels, clusterOnly, hugeGraph, zoomK, activeEdgeMode: activeEdgeMode(), visibleDynamicEdges: renderEdges.filter((edge) => edge.runtime).length };
  }



  function toScreen(x, y) {
    const t = APP.state.zoom;
    return { x: x * t.k + t.x, y: y * t.k + t.y };
  }

  function drawRoundRect(ctx, x, y, width, height, radius, fill, stroke) {
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + width, y, x + width, y + height, radius);
    ctx.arcTo(x + width, y + height, x, y + height, radius);
    ctx.arcTo(x, y + height, x, y, radius);
    ctx.arcTo(x, y, x + width, y, radius);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.stroke(); }
  }

  function drawClusters(model) {
    if (!model.workingNodes.length) return;
    const groups = new Map();
    model.workingNodes.forEach((node) => {
      const pos = APP.layoutPositions.get(node.id);
      if (!pos) return;
      const key = dirname(node.id);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push({ node, pos });
    });
    APP.ctx.save();
    groups.forEach((items, dir) => {
      if (items.length < 2) return;
      const xs = items.map((item) => item.pos.x);
      const ys = items.map((item) => item.pos.y);
      const topLeft = toScreen(Math.min(...xs) - 26, Math.min(...ys) - 26);
      const bottomRight = toScreen(Math.max(...xs) + 26, Math.max(...ys) + 26);
      const width = bottomRight.x - topLeft.x;
      const height = bottomRight.y - topLeft.y;
      if (width < 18 || height < 18) return;
      drawRoundRect(APP.ctx, topLeft.x, topLeft.y, width, height, 14, 'rgba(176,111,255,.045)', 'rgba(176,111,255,.18)');
      APP.ctx.fillStyle = 'rgba(176,111,255,.78)';
      APP.ctx.font = '10px JetBrains Mono';
      APP.ctx.textBaseline = 'top';
      APP.ctx.fillText(dir === '.' ? 'root' : basename(dir), topLeft.x + 12, topLeft.y + 10);
    });
    APP.ctx.restore();
  }

  function edgeStyle(edge, model) {
    const selected = APP.state.selectedId;
    const inCycle = APP.state.highlightCycles && APP.indexes.cycleMembershipByNode.has(edge.source) && APP.indexes.cycleMembershipByNode.has(edge.target);
    const runtimeOnly = !!edge.dynamic;
    const bothOrigins = !!edge.runtime && !runtimeOnly;
    const baseStroke = runtimeOnly ? 'rgba(255,148,212,.82)' : bothOrigins ? 'rgba(124,247,207,.78)' : 'rgba(31,37,64,.72)';
    const baseWidth = runtimeOnly ? (model.dense ? 1 : 1.35) : bothOrigins ? (model.dense ? 1.05 : 1.4) : (model.dense ? .8 : 1.1);
    const baseDash = runtimeOnly ? [8, 5] : [];
    if (selected && (edge.source === selected || edge.target === selected)) {
      const stroke = runtimeOnly ? 'rgba(255,148,212,.98)' : bothOrigins ? 'rgba(124,247,207,.98)' : 'rgba(0,229,255,.95)';
      return { stroke, opacity: 1, width: runtimeOnly ? 2.05 : 1.9, arrow: !runtimeOnly && !model.dense, dash: runtimeOnly ? [9, 5] : [] };
    }
    if (inCycle) return { stroke: 'rgba(255,170,0,.92)', opacity: .95, width: 1.7, arrow: !model.dense && !runtimeOnly, dash: runtimeOnly ? [8, 5] : [] };
    if (model.searchTree && model.searchTree.has(edge.source) && model.searchTree.has(edge.target)) {
      return { stroke: runtimeOnly ? 'rgba(255,148,212,.72)' : bothOrigins ? 'rgba(124,247,207,.72)' : 'rgba(0,229,255,.5)', opacity: .6, width: runtimeOnly ? 1.5 : 1.25, arrow: false, dash: runtimeOnly ? [7, 4] : [] };
    }
    if (selected) return { stroke: 'rgba(31,37,64,.55)', opacity: .08, width: 1, arrow: false, dash: [] };
    if (APP.state.searchQuery) return { stroke: baseStroke, opacity: model.searchTree && model.searchTree.has(edge.source) && model.searchTree.has(edge.target) ? .4 : .05, width: baseWidth, arrow: false, dash: baseDash };
    return { stroke: baseStroke, opacity: 1, width: baseWidth, arrow: !runtimeOnly && !model.dense && model.zoomK > .75 && model.renderEdges.length < 220, dash: baseDash };
  }

  function drawArrowhead(x1, y1, x2, y2, color) {
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const size = 6;
    APP.ctx.save();
    APP.ctx.translate(x2, y2);
    APP.ctx.rotate(angle);
    APP.ctx.beginPath();
    APP.ctx.moveTo(0, 0);
    APP.ctx.lineTo(-size, -size * .5);
    APP.ctx.lineTo(-size, size * .5);
    APP.ctx.closePath();
    APP.ctx.fillStyle = color;
    APP.ctx.fill();
    APP.ctx.restore();
  }

  function drawEdges(model) {
    APP.ctx.save();
    model.renderEdges.forEach((edge) => {
      const source = APP.layoutPositions.get(edge.source);
      const target = APP.layoutPositions.get(edge.target);
      if (!source || !target) return;
      const a = toScreen(source.x, source.y);
      const b = toScreen(target.x, target.y);
      const style = edgeStyle(edge, model);
      APP.ctx.beginPath();
      APP.ctx.setLineDash(style.dash || []);
      APP.ctx.moveTo(a.x, a.y);
      APP.ctx.lineTo(b.x, b.y);
      APP.ctx.strokeStyle = style.stroke;
      APP.ctx.globalAlpha = style.opacity;
      APP.ctx.lineWidth = style.width;
      APP.ctx.stroke();
      APP.ctx.setLineDash([]);
      if (style.arrow) drawArrowhead(a.x, a.y, b.x, b.y, style.stroke);
    });
    APP.ctx.restore();
  }



  function nodeOpacity(node, model) {
    if (APP.state.searchQuery) return model.searchTree && model.searchTree.has(node.id) ? 1 : .1;
    return 1;
  }

  function drawNodes(model) {
    APP.ctx.save();
    model.renderNodes.forEach((node) => {
      const pos = APP.layoutPositions.get(node.id);
      if (!pos) return;
      const screen = toScreen(pos.x, pos.y);
      const theme = getTheme(node.classification);
      const radius = node.classification === 'ENTRY' ? 10 : 7;
      const halo = node.classification === 'ENTRY' ? 13 : 9;
      const opacity = nodeOpacity(node, model);
      APP.ctx.globalAlpha = opacity;
      APP.ctx.beginPath();
      APP.ctx.arc(screen.x, screen.y, radius, 0, Math.PI * 2);
      APP.ctx.fillStyle = theme.fill;
      APP.ctx.fill();
      APP.ctx.beginPath();
      APP.ctx.arc(screen.x, screen.y, halo, 0, Math.PI * 2);
      APP.ctx.strokeStyle = getLangColor(node.language);
      APP.ctx.lineWidth = .85;
      APP.ctx.globalAlpha = opacity * .72;
      APP.ctx.stroke();
      APP.ctx.globalAlpha = opacity;
      APP.ctx.beginPath();
      APP.ctx.arc(screen.x, screen.y, radius + (node.id === APP.state.selectedId ? 3 : APP.state.highlightCycles && APP.indexes.cycleMembershipByNode.has(node.id) ? 2.5 : 1.4), 0, Math.PI * 2);
      APP.ctx.strokeStyle = APP.indexes.cycleMembershipByNode.has(node.id) ? 'rgba(255,170,0,.95)' : theme.stroke;
      APP.ctx.lineWidth = node.id === APP.state.selectedId ? 2.2 : APP.state.highlightCycles && APP.indexes.cycleMembershipByNode.has(node.id) ? 2 : 1.3;
      APP.ctx.stroke();
    });
    APP.ctx.restore();
  }

  function shouldDrawLabel(node, model) {
    if (!model.showLabels) return false;
    if (node.id === APP.state.selectedId || node.id === APP.state.hoveredId) return true;
    if (APP.state.searchQuery && model.searchTree?.has(node.id)) return true;
    if (model.zoomK > 1.45) return true;
    if (model.renderNodes.length < 120 && node.importance >= 2.5) return true;
    return node.classification === 'ENTRY' || APP.indexes.cycleMembershipByNode.has(node.id);
  }

  function drawLabels(model) {
    APP.ctx.save();
    APP.ctx.font = '10px JetBrains Mono';
    APP.ctx.textAlign = 'center';
    APP.ctx.textBaseline = 'middle';
    model.renderNodes.forEach((node) => {
      if (!shouldDrawLabel(node, model)) return;
      const pos = APP.layoutPositions.get(node.id);
      if (!pos) return;
      const screen = toScreen(pos.x, pos.y);
      const label = node.name || basename(node.id);
      const width = APP.ctx.measureText(label).width + 12;
      const height = 16;
      const x = screen.x - width / 2;
      const y = screen.y - 26;
      APP.ctx.globalAlpha = nodeOpacity(node, model);
      drawRoundRect(APP.ctx, x, y, width, height, 4, 'rgba(6,11,24,.9)', 'rgba(18,29,58,.62)');
      APP.ctx.fillStyle = node.id === APP.state.selectedId ? '#ffffff' : 'rgba(188,200,232,.92)';
      APP.ctx.fillText(label, screen.x, y + height / 2 + .2);
    });
    APP.ctx.restore();
  }

  function updateSearchStatus(model = APP.renderModel) {
    if (!model || !APP.state.searchQuery) { ELS['search-status'].textContent = ''; return; }
    ELS['search-status'].textContent = model.matchedNodes.length ? `${model.matchedNodes.length} match${model.matchedNodes.length !== 1 ? 'es' : ''} • dependency tree highlighted` : 'No matches';
  }

  function updateMinimap() {
    const canvas = ELS['minimap'];
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = 'rgba(7,9,18,.94)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (!APP.layoutPositions.size || !APP.graph) return;
    const positions = [...APP.layoutPositions.values()];
    const xs = positions.map((p) => p.x), ys = positions.map((p) => p.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const graphW = Math.max(1, maxX - minX), graphH = Math.max(1, maxY - minY), pad = 10;
    const scale = Math.min((canvas.width - pad * 2) / graphW, (canvas.height - pad * 2) / graphH);
    const mapX = (x) => pad + (x - minX) * scale;
    const mapY = (y) => pad + (y - minY) * scale;
    ctx.strokeStyle = 'rgba(31,37,64,.8)';
    ctx.lineWidth = 1;
    const profile = getPerformanceProfile(APP.graph.nodes.length, APP.graph.edges.length);
    const minimapEdges = APP.indexes.edges.length > profile.maxMinimapEdges
      ? APP.indexes.edges.filter((_edge, index) => index % Math.ceil(APP.indexes.edges.length / profile.maxMinimapEdges) === 0)
      : APP.indexes.edges;
    minimapEdges.forEach((edge) => {
      const s = APP.layoutPositions.get(edge.source);
      const t = APP.layoutPositions.get(edge.target);
      if (!s || !t) return;
      ctx.beginPath();
      ctx.moveTo(mapX(s.x), mapY(s.y));
      ctx.lineTo(mapX(t.x), mapY(t.y));
      ctx.stroke();
    });
    const minimapNodes = APP.graph.nodes.length > profile.maxMinimapNodes
      ? [...APP.graph.nodes].sort((a, b) => ((APP.indexes.nodeById.get(b.id)?.importance || b.importance || 0) - (APP.indexes.nodeById.get(a.id)?.importance || a.importance || 0))).slice(0, profile.maxMinimapNodes)
      : APP.graph.nodes;
    minimapNodes.forEach((node) => {
      const pos = APP.layoutPositions.get(node.id);
      if (!pos) return;
      ctx.fillStyle = getTheme(node.classification).stroke;
      ctx.beginPath();
      ctx.arc(mapX(pos.x), mapY(pos.y), node.classification === 'ENTRY' ? 3 : 2, 0, Math.PI * 2);
      ctx.fill();
    });
    const { width, height } = getCanvasSize();
    const t = APP.state.zoom;
    const vx1 = (-t.x) / t.k, vy1 = (-t.y) / t.k, vx2 = (width - t.x) / t.k, vy2 = (height - t.y) / t.k;
    ctx.fillStyle = 'rgba(0,229,255,.08)';
    ctx.strokeStyle = 'rgba(0,229,255,.72)';
    ctx.fillRect(mapX(vx1), mapY(vy1), Math.max(8, (vx2 - vx1) * scale), Math.max(8, (vy2 - vy1) * scale));
    ctx.strokeRect(mapX(vx1), mapY(vy1), Math.max(8, (vx2 - vx1) * scale), Math.max(8, (vy2 - vy1) * scale));
  }

  function renderGraph() {
    if (!APP.graph || !APP.indexes) { APP.ctx.clearRect(0, 0, APP.canvas.width, APP.canvas.height); return; }
    APP.renderModel = deriveRenderModel();
    APP.ctx.save();
    APP.ctx.setTransform(APP.dpr, 0, 0, APP.dpr, 0, 0);
    APP.ctx.clearRect(0, 0, APP.canvas.width, APP.canvas.height);
    if (!APP.layoutPositions.size) { APP.ctx.restore(); return; }
    if (APP.state.layoutMode === 'cluster' || APP.renderModel.clusterOnly) drawClusters(APP.renderModel);
    drawEdges(APP.renderModel);
    drawNodes(APP.renderModel);
    drawLabels(APP.renderModel);
    APP.ctx.restore();
    updateMinimap();
    updateSearchStatus();
  }

  function renderLanguageMenu() {
    const grid = ELS['lang-chip-grid'];
    if (!APP.graph) { grid.innerHTML = ''; ELS['btn-lang'].textContent = 'languages'; return; }
    const counts = {};
    APP.graph.nodes.forEach((node) => { if (node.language) counts[node.language] = (counts[node.language] || 0) + 1; });
    const active = currentLanguageSet();
    const langs = [...APP.indexes.allLanguages].sort();
    grid.innerHTML = langs.map((lang) => `<button class="toggle-chip ${active.has(lang) ? 'on' : ''}" data-lang="${escapeHtml(lang)}" style="color:${getLangColor(lang)};border-color:${active.has(lang) ? getLangColor(lang) : ''}">${escapeHtml(displayLang(lang))} <span style="opacity:.6;font-size:8px">(${counts[lang] || 0})</span></button>`).join('');
    grid.querySelectorAll('[data-lang]').forEach((el) => el.addEventListener('click', () => toggleLanguage(el.dataset.lang)));
    const total = APP.indexes.allLanguages.size;
    const activeCount = active.size;
    ELS['btn-lang'].textContent = activeCount === total ? 'languages' : `languages ${activeCount}/${total}`;
    ELS['btn-lang'].classList.toggle('on', activeCount !== total);
  }

  function renderFilterPanel() {
    const grid = ELS['filter-chip-grid'];
    grid.innerHTML = DEFAULT_CLASSES.map((cls) => `<button class="toggle-chip ${APP.state.visibleClasses.has(cls) ? 'on' : ''}" data-cls="${cls}" style="color:${getTheme(cls).stroke};border-color:${APP.state.visibleClasses.has(cls) ? getTheme(cls).stroke : ''}">${cls.toLowerCase()}</button>`).join('');
    grid.querySelectorAll('[data-cls]').forEach((el) => el.addEventListener('click', () => {
      const cls = el.dataset.cls;
      const next = new Set(APP.state.visibleClasses);
      if (next.has(cls)) next.delete(cls); else next.add(cls);
      setVisibleClasses(next);
    }));
    ELS['btn-filter'].classList.toggle('on', APP.state.visibleClasses.size !== DEFAULT_CLASSES.length);
  }

  function updateUnsupportedBanner() {
    const messages = [];
    const unsupported = APP.graph?.meta?.unsupported_languages || [];
    if (unsupported.length) {
      const pkgs = new Set();
      unsupported.forEach((item) => (PKG_HINTS[item.language] || []).forEach((pkg) => pkgs.add(pkg)));
      messages.push(`<strong>Warning</strong> ${unsupported.map((item) => `${displayLang(item.language)} parser unavailable - ${item.files} files were not analysed`).join(' • ')}. Run: <code>pip install ${[...pkgs].join(' ')}</code>`);
    }
    const runtimeMeta = APP.graph?.meta?.runtime || {};
    if (runtimeMeta.enabled && runtimeMeta.stale) messages.push('<strong>Runtime trace stale</strong> Static analysis was refreshed after source changes. Re-run tracing to refresh dynamic edges.');
    if (runtimeMeta.enabled && runtimeMeta.timed_out) messages.push('<strong>Runtime trace partial</strong> The traced program timed out, so dynamic edges may be incomplete.');
    if (runtimeMeta.enabled && runtimeMeta.error) messages.push(`<strong>Runtime trace error</strong> ${escapeHtml(String(runtimeMeta.error))}`);
    if (!messages.length || APP.state.dismissedWarning) { ELS['warning-banner'].classList.remove('show'); return; }
    ELS['warning-copy'].innerHTML = messages.join('<br><br>');
    ELS['warning-banner'].classList.add('show');
  }



  function updateStats() {
    if (!APP.graph) return;
    const s = APP.graph.summary || {};
    const imp = APP.graph.meta?.import_stats || {};
    const totalImports = Object.values(imp).reduce((sum, value) => sum + value, 0);
    const resolvedPct = totalImports ? Math.round(((imp.local || 0) / totalImports) * 100) : 0;
    const edgeBundle = activeEdgeBundle();
    const runtimeMeta = APP.graph.meta?.runtime || {};
    ELS['s-files'].textContent = APP.graph.nodes.length;
    ELS['s-edges'].textContent = edgeBundle.edges.length;
    ELS['s-orphans'].textContent = (APP.graph.waste || []).length;
    ELS['s-cycles'].textContent = s.cycle_count || 0;
    ELS['s-health'].textContent = s.health_score ?? '—';
    ELS['s-unreachable'].textContent = s.unreachable ?? 0;
    ELS['s-max-depth'].textContent = s.max_depth ?? 0;
    ELS['s-langs'].textContent = APP.indexes.allLanguages.size;
    ELS['s-resolved'].textContent = `${resolvedPct}%`;
    ELS['s-resolved'].style.color = resolvedPct > 80 ? 'var(--green)' : resolvedPct > 50 ? 'var(--amber)' : 'var(--red)';
    ELS['health-pill'].querySelector('span').textContent = s.health_score ?? '—';
    ELS['health-pill'].classList.remove('hidden');
    const runtimeSuffix = runtimeMeta.enabled ? ` • Runtime ${runtimeMeta.dynamic_edges || 0} dyn` : '';
    ELS['elapsed'].textContent = APP.graph.meta?.elapsed_s ? `Analysed in ${APP.graph.meta.elapsed_s}s${runtimeSuffix}` : runtimeSuffix.replace(/^ • /, '');
    updatePerformanceControls();
  }



  function renderChips(el, edges, dir) {
    if (!edges.length) { el.innerHTML = '<div style="color:var(--dim);font-size:10px">None</div>'; return; }
    el.innerHTML = `<div class="chip-cont">${edges.map((edge) => {
      const id = dir === 'in' ? edge.source : edge.target;
      const line = edge.line ? `:${edge.line}` : '';
      const runtimeTag = edge.runtime ? (edge.dynamic ? ' dyn' : ' rt') : '';
      const hitTag = edge.runtime_hits ? ` ×${edge.runtime_hits}` : '';
      const flavor = edge.runtime ? (edge.dynamic ? 'runtime' : 'both') : '';
      return `<div class="chip ${dir} ${flavor}" data-focus="${escapeHtml(id)}" title="${escapeHtml(id + line + runtimeTag + hitTag)}">${escapeHtml(basename(id))}${line}${runtimeTag}${hitTag}</div>`;
    }).join('')}</div>`;
    el.querySelectorAll('[data-focus]').forEach((chip) => chip.addEventListener('click', () => focusNode(chip.dataset.focus)));
  }



  function itemRow(node, color) {
    const id = escapeHtml(normalizeId(node.id));
    return `<div class="list-item" data-id="${id}"><div class="item-dot" style="background:${color}"></div><div class="item-info"><div class="item-name">${escapeHtml(basename(node.id))}</div><div class="item-path">${escapeHtml(dirname(node.id))}</div></div><div class="item-actions"><button class="item-btn" data-open="${id}">open</button><button class="item-btn" data-intentional="${id}">keep</button><button class="item-btn" data-delete="${id}">del</button></div></div>`;
  }

  function bindRowClicks(container) {
    container.querySelectorAll('.list-item').forEach((el) => el.addEventListener('click', (event) => {
      if (event.target.closest('[data-open],[data-delete],[data-intentional]')) return;
      focusNode(el.dataset.id);
    }));
    container.querySelectorAll('[data-open]').forEach((el) => el.addEventListener('click', (event) => { event.stopPropagation(); openNodeFile(el.dataset.open).catch((err) => toast(err.message)); }));
    container.querySelectorAll('[data-delete]').forEach((el) => el.addEventListener('click', (event) => { event.stopPropagation(); deleteNodeFile(el.dataset.delete).catch((err) => toast(err.message)); }));
    container.querySelectorAll('[data-intentional]').forEach((el) => el.addEventListener('click', (event) => { event.stopPropagation(); markIntentional(el.dataset.intentional, true).catch((err) => toast(err.message)); }));
  }

  function renderWaste() {
    if (!APP.graph) return;
    const waste = (APP.graph.waste || []).map((item) => APP.indexes.nodeById.get(normalizeId(item.id)) || item).filter((node) => nodeVisible(node));
    ELS['waste-badge'].textContent = waste.length;
    ELS['waste-badge'].className = `badge${waste.length === 0 ? ' ok' : ''}`;
    if (!waste.length) { ELS['waste-list'].innerHTML = '<div style="padding:40px;text-align:center;color:var(--green);font-size:11px">✓ Clean codebase</div>'; return; }
    let html = '';
    const islands = waste.filter((item) => item.classification === 'ISLAND');
    const orphans = waste.filter((item) => item.classification === 'ORPHAN');
    if (islands.length) {
      const groups = {};
      islands.forEach((item) => { const key = item.island_id >= 0 ? item.island_id : 'x'; (groups[key] = groups[key] || []).push(item); });
      Object.entries(groups).forEach(([id, items]) => { html += `<div class="list-label">Isolated Cluster ${parseInt(id, 10) + 1}</div>`; items.forEach((item) => { html += itemRow(item, 'var(--purple)'); }); });
    }
    if (orphans.length) { html += '<div class="list-label">Orphan Files</div>'; orphans.forEach((item) => { html += itemRow(item, 'var(--red)'); }); }
    ELS['waste-list'].innerHTML = html;
    bindRowClicks(ELS['waste-list']);
    syncActiveListRows();
  }

  function renderCycles() {
    if (!APP.graph) return;
    const cycles = (APP.graph.cycles || []).filter((cycle) => cycle.every((id) => { const node = APP.indexes.nodeById.get(normalizeId(id)); return node ? nodeVisible(node) : false; }));
    ELS['cycle-badge'].textContent = cycles.length;
    ELS['cycle-badge'].className = `badge${cycles.length === 0 ? ' ok' : ''}`;
    ELS['btn-cycle-highlight'].classList.toggle('on', APP.state.highlightCycles);
    if (!cycles.length) { ELS['cycle-list'].innerHTML = '<div style="padding:40px;text-align:center;color:var(--green);font-size:11px">✓ No cycles detected</div>'; return; }
    ELS['cycle-list'].innerHTML = cycles.map((cycle, index) => `<div class="cycle-item"><div class="cycle-title">Cycle ${index + 1}</div><div class="cycle-path">${cycle.map((id, idx) => `${idx ? '<span class="cycle-arrow">→</span>' : ''}<button class="cycle-node" data-focus="${escapeHtml(normalizeId(id))}">${escapeHtml(basename(id))}</button>`).join('')}</div></div>`).join('');
    ELS['cycle-list'].querySelectorAll('[data-focus]').forEach((el) => el.addEventListener('click', () => focusNode(el.dataset.focus)));
  }

  async function apiPost(route, payload) {
    const response = await fetch(route, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) });
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || `Request failed: ${route}`);
    return data;
  }
  async function fetchNodeInfo(id) {
    const response = await fetch(`/api/node-info?id=${encodeURIComponent(id)}`);
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || 'Node info unavailable');
    return data;
  }
  async function openNodeFile(id) { await apiPost('/api/open-file', { id }); toast('Opened file'); }
  async function deleteNodeFile(id) { if (!confirm(`Delete ${id}?`)) return; const data = await apiPost('/api/delete-file', { id }); setGraphData(data.graph); toast('File deleted'); }
  async function markIntentional(id, intentional = true) { const data = await apiPost('/api/mark-intentional', { id, intentional }); setGraphData(data.graph); toast(intentional ? 'Marked intentional' : 'Removed intentional mark'); }

  function updateInspector() {
    if (!APP.graph || !APP.state.selectedId) {
      ELS.ip.style.display = 'block';
      ELS.ic.style.display = 'none';
      ELS.ihistory.innerHTML = '<div style="color:var(--dim);font-size:10px">No node selected</div>';
      ELS.icycle.innerHTML = '<div style="color:var(--dim);font-size:10px">No cycle selected</div>';
      renderChips(ELS.iout, [], 'out');
      renderChips(ELS.iin, [], 'in');
      syncActiveListRows();
      return;
    }
    const node = APP.indexes.nodeById.get(APP.state.selectedId);
    if (!node) { clearSelection(); return; }
    const edgeBundle = activeEdgeBundle();
    const inEdges = edgeBundle.inboundByNode.get(node.id) || [];
    const outEdges = edgeBundle.outboundByNode.get(node.id) || [];
    const runtimeOut = outEdges.filter((edge) => edge.runtime).length;
    const runtimeIn = inEdges.filter((edge) => edge.runtime).length;
    ELS.ip.style.display = 'none';
    ELS.ic.style.display = 'block';
    ELS.iname.textContent = normalizeId(node.filepath || node.id);
    const island = node.island_id >= 0 ? `<span class="v" style="color:var(--purple)">Cluster ${node.island_id + 1}</span>` : '<span class="v">—</span>';
    const runtimeRow = APP.graph.dynamic_edges?.length ? `<div class="kv-row"><span class="k">Runtime</span><span class="v">${runtimeOut} out / ${runtimeIn} in</span></div>` : '';
    ELS.imeta.innerHTML = `<div class="kv-row"><span class="k">Class</span><span class="v" style="color:${getTheme(node.classification).stroke}">${escapeHtml(node.classification)}</span></div><div class="kv-row"><span class="k">Size</span><span class="v">${formatSize(node.size)}</span></div><div class="kv-row"><span class="k">Inbound</span><span class="v">${inEdges.length}</span></div><div class="kv-row"><span class="k">Outbound</span><span class="v">${outEdges.length}</span></div><div class="kv-row"><span class="k">Language</span><span class="v" style="color:${getLangColor(node.language)}">${escapeHtml(node.language || '—')}</span></div><div class="kv-row"><span class="k">Depth</span><span class="v">${node.depth >= 0 ? node.depth : '∞'}</span></div><div class="kv-row"><span class="k">Island</span>${island}</div>${runtimeRow}<div class="kv-row"><span class="k">Modified</span><span class="v">${formatDate(node.mtime)}</span></div>`;
    const cycles = APP.indexes.cycleMembershipByNode.get(node.id) || [];
    ELS.icycle.innerHTML = cycles.length ? cycles.map((entry) => `<div class="chip-cont"><div style="font-size:9px;color:var(--amber);letter-spacing:.12em;text-transform:uppercase">Cycle ${entry.index + 1}</div><div class="cycle-path">${entry.path.map((id, idx) => `${idx ? '<span class="cycle-arrow">→</span>' : ''}<span class="chip cycle" data-focus="${escapeHtml(id)}">${escapeHtml(basename(id))}</span>`).join('')}</div></div>`).join('') : '<div style="color:var(--dim);font-size:10px">This node is not part of a cycle.</div>';
    ELS.icycle.querySelectorAll('[data-focus]').forEach((chip) => chip.addEventListener('click', () => focusNode(chip.dataset.focus)));
    renderChips(ELS.iout, outEdges, 'out');
    renderChips(ELS.iin, inEdges, 'in');
    ELS.ihistory.innerHTML = '<div style="color:var(--dim);font-size:10px">Loading git history…</div>';
    fetchNodeInfo(node.id).then((info) => {
      ELS.ihistory.innerHTML = `<div class="kv-row"><span class="k">Modified</span><span class="v">${escapeHtml(info.mtime_iso ? new Date(info.mtime_iso).toLocaleString() : formatDate(node.mtime))}</span></div><div style="margin-top:8px;font-size:10px;line-height:1.6;color:var(--text)"><span style="color:var(--muted);text-transform:uppercase;letter-spacing:.12em">Blame</span><br>${escapeHtml(info.blame?.summary || 'Unavailable')}</div>`;
    }).catch((err) => {
      ELS.ihistory.innerHTML = `<div style="color:var(--dim);font-size:10px">${escapeHtml(err.message)}</div>`;
    });
    syncActiveListRows();
  }



  function syncActiveListRows() { document.querySelectorAll('.list-item').forEach((item) => item.classList.toggle('active', normalizeId(item.dataset.id) === APP.state.selectedId)); }
  function setSelection(id) { APP.state.selectedId = id ? normalizeId(id) : null; updateInspector(); scheduleRender(); }
  function clearSelection() { setSelection(null); }

  function focusNode(id) {
    const nid = normalizeId(id);
    const pos = APP.layoutPositions.get(nid);
    if (!pos || !APP.zoomBehavior) { setSelection(nid); return; }
    const { width, height } = getCanvasSize();
    d3.select(APP.canvas).transition().duration(500).call(APP.zoomBehavior.transform, d3.zoomIdentity.translate(width / 2, height / 2).scale(Math.max(APP.state.zoom.k, 1.6)).translate(-pos.x, -pos.y));
    setSelection(nid);
  }

  function setSearch(query) { APP.state.searchQuery = query.trim(); scheduleRender(); }

  function setVisibleClasses(nextSet) {
    APP.state.visibleClasses = nextSet;
    const selectedNode = APP.state.selectedId ? APP.indexes.nodeById.get(APP.state.selectedId) : null;
    if (selectedNode && !nodeVisible(selectedNode)) APP.state.selectedId = null;
    renderFilterPanel();
    renderWaste();
    renderCycles();
    updateInspector();
    restartMotionSimulation();
    scheduleRender();
  }

  function toggleLanguage(lang) {
    const total = APP.indexes.allLanguages;
    const next = APP.state.activeLangs === null ? new Set(total) : new Set(APP.state.activeLangs);
    if (next.has(lang)) next.delete(lang); else next.add(lang);
    APP.state.activeLangs = setEquals(next, total) ? null : next;
    const selectedNode = APP.state.selectedId ? APP.indexes.nodeById.get(APP.state.selectedId) : null;
    if (selectedNode && !nodeVisible(selectedNode)) APP.state.selectedId = null;
    renderLanguageMenu();
    renderWaste();
    renderCycles();
    updateInspector();
    restartMotionSimulation();
    scheduleRender();
  }

  function setLeftPanel(panelId) { APP.state.leftPanel = panelId; updateLeftPanel(); }

  function updateLeftPanel() {
    document.querySelectorAll('.left-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.leftPanel === APP.state.leftPanel));
    $('waste-panel').classList.toggle('left-panel-hidden', APP.state.leftPanel !== 'waste-panel');
    $('cycles-panel').classList.toggle('left-panel-hidden', APP.state.leftPanel !== 'cycles-panel');
    renderWaste();
    renderCycles();
  }

  function performanceLabel() {
    const mode = APP.state.perfMode || 'auto';
    return mode === 'quality' ? 'perf full' : mode === 'safe' ? 'perf safe' : 'perf auto';
  }

  function performanceBadgeText() {
    const mode = (APP.state.perfMode || 'auto').toUpperCase();
    return APP.motionEnabled ? `Perf ${mode}` : `Perf ${mode}  STATIC`;
  }

  function updatePerformanceControls() {
    ELS['btn-perf'].textContent = performanceLabel();
    ELS['btn-perf'].classList.toggle('on', APP.state.perfMode !== 'auto' || !APP.motionEnabled);
    ELS['perf-mode'].textContent = performanceBadgeText();
  }

  function cyclePerformanceMode() {
    APP.state.perfMode = APP.state.perfMode === 'auto' ? 'quality' : APP.state.perfMode === 'quality' ? 'safe' : 'auto';
    if (APP.graph) {
      if (APP.state.perfMode === 'auto') applyLargeGraphDefaults();
      restartMotionSimulation();
      scheduleRender();
    }
    updatePerformanceControls();
  }

  function updateLayoutControls() {
    const mode = activeEdgeMode();
    const hasRuntime = !!(APP.graph?.dynamic_edges?.length);
    ELS['layout-force'].classList.toggle('on', APP.state.layoutMode === 'force');
    ELS['layout-cluster'].classList.toggle('on', APP.state.layoutMode === 'cluster');
    ELS['layout-radial'].classList.toggle('on', APP.state.layoutMode === 'radial');
    ELS['edge-mode-static'].classList.toggle('on', mode === 'static');
    ELS['edge-mode-runtime'].classList.toggle('on', mode === 'runtime');
    ELS['edge-mode-combined'].classList.toggle('on', mode === 'combined');
    ELS['edge-mode-runtime'].disabled = !hasRuntime;
    ELS['edge-mode-combined'].disabled = !hasRuntime;
    const defaultViewMode = hasRuntime ? 'combined' : 'static';
    const viewActive = APP.state.layoutMode !== 'force' || ELS['layout-menu'].classList.contains('open') || APP.state.focusRadius > 0 || APP.state.showFullGraph || APP.state.perfMode !== 'auto' || !APP.state.showLabels || mode !== defaultViewMode;
    ELS['btn-cluster'].classList.toggle('on', viewActive);
    ELS['btn-focus'].textContent = APP.state.focusRadius === 0 ? 'focus off' : `focus r${APP.state.focusRadius}`;
    ELS['btn-focus'].classList.toggle('on', APP.state.focusRadius > 0);
    ELS['btn-full'].classList.toggle('on', APP.state.showFullGraph);
    ELS['btn-full'].textContent = APP.state.showFullGraph ? 'full on' : 'full off';
    updatePerformanceControls();
  }



  function syncShellState() {
    ELS['left-rail'].classList.remove('collapsed');
    ELS['inspector'].classList.remove('collapsed');
    ELS['btn-waste'].classList.add('on');
    ELS['btn-cycles'].classList.add('on');
    ELS['btn-insp'].classList.add('on');
    ELS['btn-labels'].classList.toggle('on', APP.state.showLabels);
    updateLeftPanel();
    updateLayoutControls();
    updatePerformanceControls();
  }

  function setEdgeMode(mode) {
    if (!APP.graph) return;
    const hasRuntime = !!(APP.graph.dynamic_edges || []).length;
    const nextMode = hasRuntime ? mode : 'static';
    if (APP.state.edgeMode === nextMode) return;
    APP.state.edgeMode = nextMode;
    updateStats();
    updateInspector();
    updateLayoutControls();
    scheduleRender();
  }

  function setLayoutMode(mode) {
    if (APP.state.layoutMode === mode) return;
    APP.state.layoutMode = mode;
    updateLayoutControls();
    requestLayout();
  }

  function cycleFocusRadius() {
    APP.state.focusRadius = (APP.state.focusRadius + 1) % 3;
    updateLayoutControls();
    scheduleRender();
  }

  function toggleFullGraph() {
    APP.state.showFullGraph = !APP.state.showFullGraph;
    updateLayoutControls();
    scheduleRender();
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
          if (file.size > 2 * 1024 * 1024) continue;
          files.push({ path, text: await file.text(), size: file.size, mtime: Math.floor(file.lastModified / 1000) });
        }
      }
    }
  }

  function openFolder() {
    if (!window.showDirectoryPicker) { toast('Folder loading requires a Chromium-based browser'); return Promise.resolve(); }
    return window.showDirectoryPicker().then(async (handle) => {
      const files = [];
      showWorkerStatus('Reading files…', 6);
      await walkDirectory(handle, '', files);
      showWorkerStatus(`Analyzing ${files.length} files…`, 10);
      ensureWorker().postMessage({ type: 'analyzeProject', payload: { files, rootName: handle.name || 'workspace' } });
    });
  }

  function loadFile(file) {
    if (!file || !file.name.endsWith('.json')) { toast('Please select a valid graph.json'); return; }
    const reader = new FileReader();
    reader.onload = (event) => {
      try { setGraphData(JSON.parse(event.target.result), { source: 'file' }); toast('Graph loaded successfully'); }
      catch { toast('Error parsing JSON'); }
    };
    reader.readAsText(file);
  }

  function findNodeAtPointer(event) {
    if (!APP.renderModel) return null;
    const rect = APP.canvas.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    const t = APP.state.zoom;
    const worldX = (px - t.x) / t.k;
    const worldY = (py - t.y) / t.k;
    let best = null;
    let bestDist = Infinity;
    APP.renderModel.renderNodes.forEach((node) => {
      const pos = APP.layoutPositions.get(node.id);
      if (!pos) return;
      const r = (node.classification === 'ENTRY' ? 10 : 7) + 6 / t.k;
      const dx = worldX - pos.x;
      const dy = worldY - pos.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist <= r && dist < bestDist) { best = node; bestDist = dist; }
    });
    return best;
  }

  function bindCanvasInteractions() {
    APP.zoomBehavior = d3.zoom()
      .scaleExtent([.08, 8])
      .filter((event) => {
        if (event.type === 'mousedown') return !findNodeAtPointer(event);
        return !event.button;
      })
      .on('zoom', (event) => { APP.state.zoom = event.transform; scheduleRender(); });
    d3.select(APP.canvas).call(APP.zoomBehavior);
    APP.canvas.addEventListener('mousemove', (event) => {
      const hit = findNodeAtPointer(event);
      APP.state.hoveredId = hit ? hit.id : null;
      APP.canvas.style.cursor = hit ? 'pointer' : 'grab';
      if (APP.draggingNodeId && APP.layoutPositions.has(APP.draggingNodeId)) {
        const rect = APP.canvas.getBoundingClientRect();
        const px = event.clientX - rect.left;
        const py = event.clientY - rect.top;
        const nextPos = { x: (px - APP.state.zoom.x) / APP.state.zoom.k, y: (py - APP.state.zoom.y) / APP.state.zoom.k };
        const simNode = APP.simNodeById.get(APP.draggingNodeId);
        if (APP.motionEnabled && simNode && APP.simulation) {
          simNode.fx = nextPos.x;
          simNode.fy = nextPos.y;
          APP.layoutPositions.set(APP.draggingNodeId, { x: nextPos.x, y: nextPos.y });
          APP.simulation.alphaTarget(0.24).restart();
        } else {
          if (APP.dragReleaseFrame) {
            cancelAnimationFrame(APP.dragReleaseFrame);
            APP.dragReleaseFrame = 0;
          }
          applyDragPositions(APP.draggingNodeId, nextPos);
        }
        APP.dragMoved = true;
      }
      scheduleRender();
    });
    APP.canvas.addEventListener('mousedown', (event) => {
      const hit = findNodeAtPointer(event);
      if (hit) {
        if (APP.dragReleaseFrame) {
          cancelAnimationFrame(APP.dragReleaseFrame);
          APP.dragReleaseFrame = 0;
        }
        APP.draggingNodeId = hit.id;
        APP.dragInfluence = buildDragInfluence(hit.id);
        captureDragOrigins(APP.dragInfluence);
        APP.dragDelta = { x: 0, y: 0 };
        APP.dragMoved = false;
        const simNode = APP.simNodeById.get(hit.id);
        if (APP.motionEnabled && simNode && APP.simulation) {
          simNode.fx = simNode.x;
          simNode.fy = simNode.y;
          APP.simulation.alphaTarget(0.24).restart();
        }
      }
    });
    window.addEventListener('mouseup', () => {
      if (APP.draggingNodeId) {
        if (APP.motionEnabled) {
          if (APP.dragMoved) commitDraggedNodePosition(APP.draggingNodeId);
          const simNode = APP.simNodeById.get(APP.draggingNodeId);
          if (simNode) {
            simNode.fx = null;
            simNode.fy = null;
          }
          if (APP.simulation) APP.simulation.alphaTarget(0);
        } else if (APP.dragInfluence && APP.dragMoved) {
          animateDragSettle(APP.draggingNodeId, APP.dragInfluence);
        }
      }
      APP.draggingNodeId = null;
      APP.dragInfluence = null;
      APP.dragOriginPositions = null;
      APP.dragDelta = { x: 0, y: 0 };
      APP.dragMoved = false;
    });
    APP.canvas.addEventListener('click', (event) => {
      const hit = findNodeAtPointer(event);
      if (APP.dragMoved) return;
      if (hit) setSelection(hit.id); else clearSelection();
    });
  }

  function closeMenus() {
    ELS['lang-menu'].classList.remove('open');
    ELS['filter-panel'].classList.remove('open');
    ELS['layout-menu'].classList.remove('open');
    ELS['btn-filter'].classList.remove('on');
    if (APP.state.activeLangs === null) ELS['btn-lang'].classList.remove('on');
    updateLayoutControls();
  }

  function openAnchoredMenu(buttonEl, menuEl) {
    const wasOpen = menuEl.classList.contains('open');
    closeMenus();
    if (wasOpen) return;
    menuEl.style.left = '50%';
    menuEl.style.top = '72px';
    menuEl.style.transform = 'translateX(-50%)';
    menuEl.classList.add('open');
    buttonEl.classList.add('on');
    updateLayoutControls();
  }

  function toast(message) {
    ELS.toast.textContent = message;
    ELS.toast.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => ELS.toast.classList.remove('show'), 2600);
  }

  function toggleSearch(open = true) {
    ELS['search-box'].classList.toggle('open', open);
    if (open) { ELS['search-input'].focus(); ELS['search-input'].select(); }
  }

  function focusFirstSearchMatch() {
    if (!APP.renderModel?.matchedNodes?.length) return;
    focusNode(APP.renderModel.matchedNodes[0].id);
  }

  function clearSearch(hide = false) {
    ELS['search-input'].value = '';
    setSearch('');
    if (hide) ELS['search-box'].classList.remove('open');
  }

  function refreshViewportAfterShellChange() {
    const refresh = () => {
      resizeCanvas();
      scheduleRender();
    };
    refresh();
    requestAnimationFrame(() => {
      refresh();
      requestAnimationFrame(refresh);
    });
    if (APP.shellRefreshTimer) clearTimeout(APP.shellRefreshTimer);
    APP.shellRefreshTimer = setTimeout(() => {
      APP.shellRefreshTimer = 0;
      refresh();
    }, 340);
  }

  function debugNodeCanvasPoint(id) {
    const nid = normalizeId(id);
    const pos = APP.layoutPositions.get(nid);
    if (!pos) return null;
    const screen = toScreen(pos.x, pos.y);
    return { x: screen.x, y: screen.y };
  }

  function wireUi() {
    ELS['btn-folder'].onclick = () => openFolder().catch((err) => toast(err.message || String(err)));
    ELS['btn-open'].onclick = () => ELS['file-input'].click();
    ELS['file-input'].onchange = (event) => loadFile(event.target.files[0]);
    document.addEventListener('dragover', (event) => event.preventDefault());
    document.addEventListener('drop', (event) => { event.preventDefault(); loadFile(event.dataTransfer.files[0]); });
    ELS['btn-waste'].onclick = function () { ELS['left-rail'].classList.toggle('collapsed'); this.classList.toggle('on'); refreshViewportAfterShellChange(); };
    ELS['btn-cycles'].onclick = function () { ELS['left-rail'].classList.remove('collapsed'); ELS['btn-waste'].classList.add('on'); setLeftPanel('cycles-panel'); refreshViewportAfterShellChange(); };
    document.querySelectorAll('.left-tab').forEach((tab) => tab.addEventListener('click', () => setLeftPanel(tab.dataset.leftPanel)));
    ELS['btn-insp'].onclick = function () { ELS.inspector.classList.toggle('collapsed'); this.classList.toggle('on'); refreshViewportAfterShellChange(); };
    ELS['btn-cluster'].onclick = () => openAnchoredMenu(ELS['btn-cluster'], ELS['layout-menu']);
    ELS['edge-mode-static'].onclick = () => setEdgeMode('static');
    ELS['edge-mode-runtime'].onclick = () => setEdgeMode('runtime');
    ELS['edge-mode-combined'].onclick = () => setEdgeMode('combined');
    ELS['layout-force'].onclick = () => { setLayoutMode('force'); ELS['layout-menu'].classList.remove('open'); };
    ELS['layout-cluster'].onclick = () => { setLayoutMode('cluster'); ELS['layout-menu'].classList.remove('open'); };
    ELS['layout-radial'].onclick = () => { setLayoutMode('radial'); ELS['layout-menu'].classList.remove('open'); };
    ELS['btn-focus'].onclick = cycleFocusRadius;
    ELS['btn-full'].onclick = toggleFullGraph;
    ELS['btn-perf'].onclick = cyclePerformanceMode;
    ELS['btn-labels'].onclick = function () { APP.state.showLabels = !APP.state.showLabels; this.classList.toggle('on', APP.state.showLabels); scheduleRender(); };
    ELS['btn-filter'].onclick = () => openAnchoredMenu(ELS['btn-filter'], ELS['filter-panel']);
    ELS['btn-lang'].onclick = () => { if (APP.graph) openAnchoredMenu(ELS['btn-lang'], ELS['lang-menu']); };
    ELS['lang-all'].onclick = () => { APP.state.activeLangs = null; renderLanguageMenu(); renderWaste(); renderCycles(); updateInspector(); scheduleRender(); };
    ELS['lang-none'].onclick = () => { APP.state.activeLangs = new Set(); renderLanguageMenu(); renderWaste(); renderCycles(); updateInspector(); scheduleRender(); };
    ELS['filter-all'].onclick = () => setVisibleClasses(new Set(DEFAULT_CLASSES));
    ELS['filter-waste'].onclick = () => setVisibleClasses(new Set(['ORPHAN', 'ISLAND']));
    ELS['filter-default'].onclick = () => setVisibleClasses(new Set(DEFAULT_CLASSES));
    ELS['btn-search'].onclick = () => toggleSearch(true);
    ELS['btn-reset'].onclick = () => { d3.select(APP.canvas).transition().duration(450).call(APP.zoomBehavior.transform, d3.zoomIdentity); };
    ELS['btn-cycle-highlight'].onclick = () => { APP.state.highlightCycles = !APP.state.highlightCycles; renderCycles(); scheduleRender(); };
    ELS['warning-dismiss'].onclick = () => { APP.state.dismissedWarning = true; updateUnsupportedBanner(); };
    ELS['search-input'].oninput = (event) => { clearTimeout(APP.debounceSearch); APP.debounceSearch = setTimeout(() => setSearch(event.target.value), 80); };
    ELS['search-input'].onkeydown = (event) => { if (event.key === 'Enter') { event.preventDefault(); focusFirstSearchMatch(); } if (event.key === 'Escape') { event.preventDefault(); clearSearch(true); } };
    document.addEventListener('click', (event) => {
      const target = event.target;
      if (target.closest('#lang-menu') || target.closest('#btn-lang') || target.closest('#filter-panel') || target.closest('#btn-filter') || target.closest('#layout-menu') || target.closest('#btn-cluster')) return;
      closeMenus();
    });
    document.addEventListener('keydown', (event) => {
      const tag = document.activeElement?.tagName;
      const editing = ['INPUT', 'TEXTAREA'].includes(tag) || document.activeElement?.isContentEditable;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); toggleSearch(true); return; }
      if (!editing && event.key === '/') { event.preventDefault(); toggleSearch(true); return; }
      if (event.key === 'Escape' && ELS['search-box'].classList.contains('open')) { event.preventDefault(); clearSearch(true); }
    });
    window.addEventListener('resize', () => { closeMenus(); resizeCanvas(); if (APP.graph) requestLayout(); });
  }

  function boot() {
    captureElements();
    resizeCanvas();
    bindCanvasInteractions();
    wireUi();
    syncShellState();

    const tryLoad = async () => {
      if (window.orbitsData) {
        setGraphData(window.orbitsData, { source: 'script' });
        return;
      }
      if (location.protocol !== 'file:') {
        try {
          const res = await fetch('graph.json');
          if (res.ok) {
            const data = await res.json();
            if (data) setGraphData(data, { source: 'server' });
          }
        } catch (e) {
          console.warn('Could not auto-fetch graph.json', e);
        }
      }
    };

    tryLoad();
    window.loadGraph = (data) => setGraphData(data, { source: 'manual' });
    window.__orbitsDebug = {
      get graphLoaded() { return !!APP.graph; },
      get graphNodeCount() { return APP.graph?.nodes?.length ?? 0; },
      get graphEdgeCount() { return APP.graph?.edges?.length ?? 0; },
      get graphDynamicEdgeCount() { return APP.graph?.dynamic_edges?.length ?? 0; },
      get selectedNodeId() { return APP.state.selectedId; },
      get visibleNodeCount() { return APP.renderModel?.renderNodes?.length ?? 0; },
      get visibleEdgeCount() { return APP.renderModel?.renderEdges?.length ?? 0; },
      get visibleDynamicEdgeCount() { return APP.renderModel?.renderEdges?.filter((edge) => edge.runtime).length ?? 0; },
      get edgeMode() { return activeEdgeMode(); },
      get menuStates() {
        return {
          langOpen: ELS['lang-menu'].classList.contains('open'),
          filterOpen: ELS['filter-panel'].classList.contains('open'),
          layoutOpen: ELS['layout-menu'].classList.contains('open'),
          searchOpen: ELS['search-box'].classList.contains('open')
        };
      },
      get zoom() { return { x: APP.state.zoom.x, y: APP.state.zoom.y, k: APP.state.zoom.k }; },
      get shellState() {
        return {
          leftRailCollapsed: ELS['left-rail'].classList.contains('collapsed'),
          inspectorCollapsed: ELS['inspector'].classList.contains('collapsed')
        };
      },
      get performanceState() {
        return {
          perfMode: APP.state.perfMode,
          motionEnabled: APP.motionEnabled,
          layoutMode: APP.state.layoutMode,
          showFullGraph: APP.state.showFullGraph
        };
      },
      getNodeCanvasPoint(id) { return debugNodeCanvasPoint(id); }
    };
  }

  boot();
})();



