# Orbits

Orbits analyzes a source tree, resolves project-local dependencies, writes a `graph.json`, and serves a bundled visualizer for exploring the dependency graph.

The current stack is:

- Python backend analyzer
- D3 + canvas visualizer
- browser-side worker analysis for folder loading in supported browsers
- local HTTP serving through `analyzer.py --serve`
- optional Python and Node.js runtime tracing with merged dynamic-edge overlays

## Quick Start

From the repo root:

1. Create and activate a local venv.
2. Install Python dependencies into that venv.
3. Install frontend dependencies once.
4. Run the analyzer with `--serve`.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
npm install
python analyzer.py . --serve
```

Then open the URL printed by the server, typically:

```text
http://127.0.0.1:8765/visualizer.html
```

### If You Only Want a Graph File

```powershell
python analyzer.py .
```

That writes `graph.json` in the repo root by default.

### If You Already Have a Venv

```powershell
.\.venv\Scripts\python.exe analyzer.py . --serve
```

## What It Does

- Crawls a project tree while skipping common noise
- Extracts imports/includes for multiple languages
- Resolves project-local dependencies
- Computes cycles, islands, orphans, depth, health, and summary stats
- Serves an interactive visualizer for the generated graph

## Supported Languages

First-class extraction and resolution:

- Python
- JavaScript / TypeScript / TSX
- Go
- C / C++
- Java
- Kotlin

Fallback:

- Generic regex-based extraction for unsupported or unknown languages

## Pipeline

1. Crawl the project tree.
2. Extract raw import/include statements.
3. Resolve local edges against project files.
4. Build graph metadata and summary metrics.
5. Optionally trace a Python or Node.js entrypoint at runtime and write `runtime_trace.json`.
6. Merge dynamic runtime edges into the served/exported graph overlay.
7. Write `graph.json`.
8. Optionally serve the visualizer and graph assets.

## Current Frontend Architecture

The active visualizer is not Cytoscape.

It currently uses:

- D3 for zoom, motion, and interaction
- `canvas` for graph rendering
- DOM panels for controls, inspector, waste, cycles, and search
- `visualizer_worker.js` for browser-side folder analysis

This preserves the older `3.5f` visual feel while keeping the later browser-side workflow and performance work.

## Phase 4 Status

Implemented in the current UI:

- Cluster view
- Filter panel
- File sidebar / inspector
- Waste panel
- Cycles panel
- Search with dependency-tree highlighting
- Minimap
- Language multi-select
- Unsupported-language warning banner
- Folder loading via File System Access API in Chromium-based browsers
- Browser-side worker analysis
- Large-graph performance modes and auto-degradation

Not implemented as originally claimed in older docs:

- Cytoscape.js / react-force-graph as the active renderer

## Phase 5 Status

Implemented now:

- Python runtime tracing in a separate subprocess
- Node.js runtime tracing in a separate subprocess
- separate `runtime_trace.json` artifact
- merged runtime edge overlay in `graph.json` via `dynamic_edges`
- multi-session runtime metadata via `meta.runtime.sessions[]`
- runtime-aware `view` menu edge modes: `static`, `runtime`, `combined`
- runtime edge styling in the D3 + canvas visualizer
- stale runtime overlay preservation across static reanalysis paths

Current scope and honest boundary:

- runtime tracing is shipped for Python and Node.js today
- multiple runtime artifacts can be merged into one graph as separate runtime sessions
- static graph metrics like cycles, depth, waste, and health remain static-analysis-based
- runtime edges are an overlay, not a replacement for the static graph
- reanalysis preserves prior runtime overlay, but marks it stale after source-changing actions until you retrace
- Node traces are best for `.js` / `.cjs` / `.mjs` entrypoints; direct TypeScript runtime execution is still not claimed
- runtime-to-static remapping for transpiled `dist/*.js` to `src/*.ts` now uses source maps when available, but is still not compiler-perfect

## Performance Reality

The visualizer is now much safer on large graphs than the old SVG version, but this is the honest boundary:

- `500+` files is a reasonable target
- large graphs degrade by reducing labels, limiting visible nodes/edges, sampling the minimap, and disabling live motion when needed
- very dense multi-thousand-node graphs can still be heavy depending on browser and machine
- this is engineered to degrade gracefully, not a promise that every arbitrarily huge graph can never slow down

### Performance Modes

The visualizer has three modes in the `view` menu:

- `perf auto`: chooses safer defaults automatically for large graphs
- `perf full`: favors richer motion and higher draw limits
- `perf safe`: favors stability and stricter draw limits

On very large graphs, Orbits may automatically start with:

- cluster layout
- labels off
- full graph off

## Browser Features

The visualizer supports two data sources:

1. Backend-generated `graph.json`
2. Browser-side folder analysis from the `folder` button

Browser folder analysis:

- uses a Web Worker so analysis does not block the UI thread
- requires a Chromium-based browser for `showDirectoryPicker()`
- produces the same top-level graph shape as the backend visualizer expects
- is heuristic and not guaranteed to match backend analysis exactly on every repo

## Runtime Requirements

### Python

Recommended: use the workspace venv.

The repo expects local Python dependencies in `.venv/`.
Optional parser support depends on installed tree-sitter language packages.
If grammars are missing, Orbits reports unsupported languages in CLI output and graph metadata instead of silently pretending those files had no imports.

Node runtime tracing uses the same overlay contract as Python:

```bash
python analyzer.py /path/to/project --trace-node app.js
python analyzer.py /path/to/project --trace-node-module myapp.cli --trace-arg=--mode --trace-arg=dev
```

It writes a separate runtime artifact and merges into `dynamic_edges` exactly like Python.

You can also merge existing runtime artifacts back into a fresh static analysis:

```bash
python analyzer.py /path/to/project --runtime-input C:/tmp/python_runtime.json --runtime-input C:/tmp/node_runtime.json
```

### Frontend

Install frontend dependencies once:

```bash
npm install
```

The active frontend uses D3. A Cytoscape dependency may still exist in `package.json`, but it is not the active renderer path.

## Usage

Analyze a project:

```bash
python analyzer.py /path/to/project
```

Analyze and serve the visualizer:

```bash
python analyzer.py /path/to/project --serve
```

Trace a Python script at runtime and merge dynamic edges:

```bash
python analyzer.py /path/to/project --trace-python app.py --serve
```

Trace a Python module with arguments:

```bash
python analyzer.py /path/to/project --trace-module myapp.cli --trace-arg=--mode --trace-arg=dev
```

Trace a Node.js script or module with the same argument style:

```bash
python analyzer.py /path/to/project --trace-node app.js
python analyzer.py /path/to/project --trace-node-module myapp.cli --trace-arg=--mode --trace-arg=dev
```

Write the runtime artifact somewhere else:

```bash
python analyzer.py /path/to/project --trace-python app.py --runtime-output C:/temp/runtime_trace.json
```

Merge one or more existing runtime artifacts into a fresh graph:

```bash
python analyzer.py /path/to/project --runtime-input C:/temp/python_runtime.json --runtime-input C:/temp/node_runtime.json
```

Write output somewhere else and still serve correctly:

```bash
python analyzer.py /path/to/project -o C:/temp/my-graph.json --serve
```

Load a graph directly in the browser UI:

- open the served visualizer
- use `OPEN GRAPH FILE`
- or drag and drop a `graph.json`

If runtime data is present, the `view` menu lets you switch between:

- `static` edges only
- `runtime` edges only
- `combined` static + runtime edges

## Behavior Guarantees

- Analysis does not edit the target repository's `.gitignore`
- Cache writes stay in Orbits-owned files
- `--serve` does not depend on changing the process working directory
- Missing parser support is surfaced in metadata and UI/CLI messaging
- Runtime tracing writes to a separate artifact instead of mutating static cache files
- Source-changing reanalysis preserves runtime overlay but marks it stale until you rerun tracing
- Multiple runtime traces are preserved as individual sessions and also aggregated into one runtime overlay

## Visualizer Features

Inspector shows:

- file path
- classification
- inbound/outbound references
- runtime edge markers (`dyn` or `rt`) when present
- depth
- island
- cycle membership
- modified time
- git blame summary when available

Waste panel supports:

- `open`
- `keep` to mark a file as intentional waste
- `del`

Intentional suppressions are stored in:

- `.orbits_intentional.json`

## Key Files

- `analyzer.py`: CLI entry point, HTTP serving, file actions, metadata APIs
- `runtime_trace.py`: Python + Node runtime trace orchestration, artifact writer, runtime merge helpers
- `node_runtime_trace.cjs`: Node.js runtime tracer child process and artifact writer
- `lang_dispatch.py`: crawl orchestration, worker dispatch, language support metadata
- `worker.py`: per-language extraction and resolution execution
- `extractors/`: tree-sitter and fallback extractors
- `resolvers/`: language-specific resolution logic
- `graph_engine.py`: enrichment, waste detection, summary metrics
- `visualizer.html`: bundled shell/UI
- `visualizer_app.js`: active D3 + canvas visualizer logic
- `visualizer_worker.js`: browser-side worker analysis and layout
- `benchmark_graph.py`: deterministic large-graph benchmark fixture generator

## Benchmarking

Generate a large synthetic graph fixture:

```bash
python benchmark_graph.py --nodes 1200 --seed 7 --output large_graph.json
```

This is useful for testing:

- render stability
- minimap behavior
- perf mode changes
- large-graph regressions

## Verification

The repo currently includes regression coverage for:

- non-mutating analysis behavior
- serving behavior
- unsupported parser metadata
- Python import-from resolution
- Python runtime trace capture and merge behavior
- Node runtime trace capture and merge behavior
- multi-session runtime merge behavior and Node source-map remapping
- TypeScript alias resolution
- Go module resolution
- C / C++ include resolution
- Java and Kotlin package resolution
- end-to-end graph shape
- synthetic benchmark graph generation
- Playwright Stage 1 browser coverage for the visualizer, including runtime edge mode switching

Run backend tests:

```bash
python -m unittest discover -s tests -v
```

Run browser tests:

```bash
npm run test:e2e -- --reporter=line
```

## Limitations

- Dynamic imports, reflection, generated code, and macro-heavy systems remain hard limits for static analysis alone
- Python runtime tracing only sees code paths that actually execute
- Python runtime tracing is time-bounded; timed-out sessions produce partial traces
- Node runtime tracing is contract-tested but not yet shipped as a backend tracer
- Browser-side worker analysis is not guaranteed to match backend analysis exactly
- Large graphs are handled more safely now, but browser and machine limits still matter
- Git blame and file actions depend on local environment support and repository state
- Runtime edges are an overlay today; they do not currently rewrite static health, cycle, depth, or waste calculations
