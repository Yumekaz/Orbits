"""
graph_engine.py — Orbits Phase 1 core.

Takes raw nodes + edges from the extractor and enriches them with:

  1. Node classification  — ENTRY / LEAF / CONNECTED / ORPHAN / ISLAND /
                            TEST / GENERATED
  2. Cycle detection      — finds every circular dependency chain
  3. Island detection     — disconnected subgraphs with no entry point
  4. Depth from entry     — BFS distance from nearest entry node
  5. Summary stats        — counts per classification, health score

All functions are pure (no I/O, no side effects). Input and output
are plain dicts/lists — easy to serialize to JSON.
"""

from collections import defaultdict, deque
from typing import Any


# ── Classification constants ───────────────────────────────────────────────

class NodeClass:
    ENTRY     = 'ENTRY'      # no inbound, has outbound — execution starts here
    LEAF      = 'LEAF'       # has inbound, no outbound — utils, constants
    CONNECTED = 'CONNECTED'  # has both — the glue code
    ORPHAN    = 'ORPHAN'     # no inbound, no outbound — genuinely dead
    ISLAND    = 'ISLAND'     # connected internally but cut off from all entry points
    TEST      = 'TEST'       # test file (by name pattern) — expected low inbound
    GENERATED = 'GENERATED'  # build artifact — ignored in waste reporting


# ── Helpers ────────────────────────────────────────────────────────────────

TEST_PATTERNS = (
    'test_', '_test.', '.test.', 'spec_', '_spec.',
    '/test/', '/tests/', '/spec/', '/specs/',
    'conftest', 'fixture',
)

GENERATED_DIRS = (
    '__pycache__', '.mypy_cache', '.pytest_cache',
    'dist/', 'build/', 'out/', 'target/',
    'generated/', '.eggs/', 'htmlcov/',
    'site-packages/', '.tox/',
)


def _is_test(node_id: str) -> bool:
    low = node_id.lower()
    return any(p in low for p in TEST_PATTERNS)


def _is_generated(node_id: str) -> bool:
    low = node_id.lower()
    return any(p in low for p in GENERATED_DIRS)


# ── Graph construction ──────────────────────────────────────────────────────

def _build_adjacency(
    node_ids: list[str],
    edges: list[dict],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """
    Returns (outbound, inbound) adjacency sets.
    outbound[A] = set of nodes A imports
    inbound[A]  = set of nodes that import A
    """
    outbound: dict[str, set[str]] = defaultdict(set)
    inbound:  dict[str, set[str]] = defaultdict(set)

    for nid in node_ids:
        outbound[nid]  # ensure key exists
        inbound[nid]

    for edge in edges:
        src = edge.get('source', '')
        tgt = edge.get('target', '')
        if src and tgt:
            outbound[src].add(tgt)
            inbound[tgt].add(src)

    return dict(outbound), dict(inbound)


# ── 1. Node classification ─────────────────────────────────────────────────

def classify_nodes(
    nodes: list[dict],
    edges: list[dict],
) -> dict[str, str]:
    """
    Returns {node_id: NodeClass} for every node.
    Order of precedence: GENERATED → TEST → structural classification.
    """
    node_ids = [n['id'] for n in nodes]
    outbound, inbound = _build_adjacency(node_ids, edges)

    result: dict[str, str] = {}

    for nid in node_ids:
        if _is_generated(nid):
            result[nid] = NodeClass.GENERATED
            continue
        if _is_test(nid):
            result[nid] = NodeClass.TEST
            continue

    # Clean inbound edges: ignore imports from TEST or GENERATED nodes
    clean_inbound = {}
    for nid in node_ids:
        clean_inbound[nid] = {
            src for src in inbound[nid]
            if result.get(src) not in (NodeClass.TEST, NodeClass.GENERATED)
        }

    for nid in node_ids:
        if nid in result: # Already classified as TEST or GENERATED
            continue

        has_out = bool(outbound[nid])
        has_in  = bool(clean_inbound[nid])

        if not has_in and not has_out:
            result[nid] = NodeClass.ORPHAN
        elif not has_in and has_out:
            result[nid] = NodeClass.ENTRY
        elif has_in and not has_out:
            result[nid] = NodeClass.LEAF
        else:
            result[nid] = NodeClass.CONNECTED

    return result


# ── 2. Cycle detection — iterative DFS ─────────────────────────────────────

def find_cycles(
    nodes: list[dict],
    edges: list[dict],
) -> list[list[str]]:
    """
    Returns a list of cycles. Each cycle is a list of node IDs forming
    the circular path, e.g. ['a.py', 'b.py', 'c.py', 'a.py'].

    Uses Johnson's algorithm concept simplified: DFS with a recursion
    stack. We collect all unique simple cycles, capped at 50 to avoid
    blowing up on pathological graphs.
    """
    node_ids = [n['id'] for n in nodes]
    outbound, _ = _build_adjacency(node_ids, edges)

    visited:   set[str] = set()
    rec_stack: list[str] = []
    rec_set:   set[str] = set()
    cycles:    list[list[str]] = []

    MAX_CYCLES = 50

    def dfs(node: str):
        if len(cycles) >= MAX_CYCLES:
            return
        visited.add(node)
        rec_stack.append(node)
        rec_set.add(node)

        for neighbor in outbound.get(node, set()):
            if len(cycles) >= MAX_CYCLES:
                break
            if neighbor not in visited:
                dfs(neighbor)
            elif neighbor in rec_set:
                # Found a cycle — extract it from the stack
                cycle_start = rec_stack.index(neighbor)
                cycle = rec_stack[cycle_start:] + [neighbor]
                # Deduplicate: normalize by rotating to smallest element
                min_idx = cycle[:-1].index(min(cycle[:-1]))
                normalized = cycle[min_idx:-1] + cycle[:min_idx] + [cycle[min_idx]]
                if normalized not in cycles:
                    cycles.append(normalized)

        rec_stack.pop()
        rec_set.discard(node)

    for nid in node_ids:
        if nid not in visited:
            dfs(nid)

    return cycles


# ── 3. Island detection ─────────────────────────────────────────────────────

def find_islands(
    nodes: list[dict],
    edges: list[dict],
    classifications: dict[str, str],
) -> list[list[str]]:
    """
    Finds disconnected subgraphs that contain no ENTRY node.

    These are 'island' clusters — internally connected groups of files
    that are cut off from the rest of the codebase. Classic sign of
    an abandoned feature or an extracted module nobody wired up.

    Returns a list of clusters, each cluster is a list of node IDs.
    Single-node clusters that are already ORPHAN are excluded
    (they're reported separately).
    """
    node_ids = [n['id'] for n in nodes]
    outbound, inbound = _build_adjacency(node_ids, edges)

    # Build undirected adjacency for component detection
    undirected: dict[str, set[str]] = defaultdict(set)
    for nid in node_ids:
        undirected[nid]
    for edge in edges:
        src = edge.get('source', '')
        tgt = edge.get('target', '')
        if src and tgt:
            undirected[src].add(tgt)
            undirected[tgt].add(src)

    # BFS to find connected components
    visited: set[str] = set()
    components: list[list[str]] = []

    for start in node_ids:
        if start in visited:
            continue
        component = []
        queue = deque([start])
        visited.add(start)
        while queue:
            node = queue.popleft()
            component.append(node)
            for neighbor in undirected.get(node, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        components.append(component)

    islands = []
    for component in components:
        # Skip single orphan nodes — already classified
        if len(component) == 1:
            continue
        # If no node in this component is an ENTRY, it's an island
        has_entry = any(
            classifications.get(nid) == NodeClass.ENTRY
            for nid in component
        )
        if not has_entry:
            # Upgrade classification for these nodes
            islands.append(component)

    return islands


# ── 4. Depth from entry — BFS ───────────────────────────────────────────────

def compute_depths(
    nodes: list[dict],
    edges: list[dict],
    classifications: dict[str, str],
) -> dict[str, int]:
    """
    BFS from all ENTRY nodes simultaneously.
    Returns {node_id: depth} where depth is hops from nearest entry.
    Nodes unreachable from any entry get depth = -1.
    """
    node_ids = [n['id'] for n in nodes]
    outbound, _ = _build_adjacency(node_ids, edges)

    depths: dict[str, int] = {nid: -1 for nid in node_ids}
    queue: deque[tuple[str, int]] = deque()

    # Seed BFS with all entry nodes at depth 0
    for nid in node_ids:
        if classifications.get(nid) == NodeClass.ENTRY:
            depths[nid] = 0
            queue.append((nid, 0))

    while queue:
        node, depth = queue.popleft()
        for neighbor in outbound.get(node, set()):
            if depths[neighbor] == -1:
                depths[neighbor] = depth + 1
                queue.append((neighbor, depth + 1))

    return depths


# ── 5. Summary stats ────────────────────────────────────────────────────────

def compute_summary(
    classifications: dict[str, str],
    cycles: list[list[str]],
    islands: list[list[str]],
    depths: dict[str, int],
) -> dict[str, Any]:
    """
    Returns a summary dict for the UI stats bar and health score.
    Health score: 100 minus penalties for orphans, cycles, islands.
    """
    counts: dict[str, int] = defaultdict(int)
    for cls in classifications.values():
        counts[cls] += 1

    total = len(classifications)
    orphan_count = counts[NodeClass.ORPHAN]
    island_nodes = sum(len(c) for c in islands)
    cycle_count  = len(cycles)

    # Penalty-based health score (rough but useful signal)
    penalty = 0
    if total > 0:
        penalty += (orphan_count / total) * 40      # orphans hurt most
        penalty += min(cycle_count * 5, 30)          # cycles are bad
        penalty += (island_nodes / max(total, 1)) * 20
    health = max(0, round(100 - penalty))

    max_depth = max((d for d in depths.values() if d >= 0), default=0)

    return {
        'counts':       dict(counts),
        'total':        total,
        'cycle_count':  cycle_count,
        'island_count': len(islands),
        'max_depth':    max_depth,
        'health_score': health,
        'unreachable':  sum(1 for d in depths.values() if d == -1),
    }


# ── Main entrypoint ─────────────────────────────────────────────────────────

def analyze_graph(raw: dict) -> dict:
    """
    Takes raw graph dict {nodes, edges, meta} from analyzer.py
    and returns an enriched graph dict with all phase 1 data attached.

    This is the single function analyzer.py calls.
    """
    nodes: list[dict] = raw.get('nodes', [])
    edges: list[dict] = raw.get('edges', [])
    meta:  dict       = raw.get('meta', {})

    # ── Run all analyses ───────────────────────────────────────────────────
    classifications = classify_nodes(nodes, edges)
    cycles          = find_cycles(nodes, edges)
    islands         = find_islands(nodes, edges, classifications)
    depths          = compute_depths(nodes, edges, classifications)

    # Upgrade ISLAND classification for island nodes
    for cluster in islands:
        for nid in cluster:
            if classifications.get(nid) not in (NodeClass.GENERATED, NodeClass.TEST):
                classifications[nid] = NodeClass.ISLAND

    # Re-run depths after island reclassification (islands have no entry,
    # so depths stay -1 — but we recompute to be safe)
    depths = compute_depths(nodes, edges, classifications)

    summary = compute_summary(classifications, cycles, islands, depths)

    # ── Attach enriched data to nodes ─────────────────────────────────────
    island_map: dict[str, int] = {}
    for i, cluster in enumerate(islands):
        for nid in cluster:
            island_map[nid] = i

    enriched_nodes = []
    for node in nodes:
        nid = node['id']
        enriched_nodes.append({
            **node,
            'classification': classifications.get(nid, NodeClass.ORPHAN),
            'depth':          depths.get(nid, -1),
            'island_id':      island_map.get(nid, -1),
        })

    # ── Build waste list (actionable dead files) ───────────────────────────
    intentional_files = set(meta.get('intentional_files', []))
    waste = []
    for node in enriched_nodes:
        cls = node['classification']
        if cls in (NodeClass.ORPHAN, NodeClass.ISLAND) and node['id'] not in intentional_files:
            waste.append({
                'id':             node['id'],
                'name':           node['name'],
                'classification': cls,
                'size':           node.get('size', 0),
                'island_id':      node.get('island_id', -1),
            })

    # Sort waste: islands grouped together, then orphans, alpha within
    waste.sort(key=lambda w: (
        0 if w['classification'] == NodeClass.ISLAND else 1,
        w.get('island_id', -1),
        w['id'],
    ))

    result = {
        'nodes':   enriched_nodes,
        'edges':   edges,
        'cycles':  cycles,
        'islands': islands,
        'waste':   waste,
        'summary': summary,
        'meta':    {
            **meta,
            'phase': 5 if raw.get('runtime') or meta.get('runtime', {}).get('enabled') else raw.get('meta', {}).get('phase', 3),
        },
    }
    if 'dynamic_edges' in raw:
        result['dynamic_edges'] = raw.get('dynamic_edges', [])
    if 'runtime' in raw:
        result['runtime'] = raw.get('runtime')
    return result
