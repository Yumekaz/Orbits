# Orbits

Orbits analyzes a source tree, resolves project-local dependencies, and emits a `graph.json` file for the bundled visualizer.

The current implementation now has first-class extraction and resolution for:

- Python
- JavaScript / TypeScript / TSX
- Go
- C / C++
- Java
- Kotlin
- Generic fallback for everything else

## Architecture

The pipeline is:

1. Crawl the project tree while skipping noise.
2. Extract imports/includes with language-specific extractors.
3. Resolve local dependencies with language-specific resolvers.
4. Enrich the graph with cycles, islands, orphans, depth, and health.
5. Serve `visualizer.html` against the generated `graph.json`.

## Phase Status

### Phase 2: Resolution Engine

Implemented language-specific resolution for:

- Python: package roots, `src/` layouts, relative imports, stdlib and third-party classification.
- JS / TS / TSX: relative imports, extension omission, index files, `baseUrl`, and tsconfig path aliases.
- Go: `go.mod` module-aware local package resolution.
- C / C++: quoted include resolution using source-local paths plus include directories inferred from `compile_commands.json`, `CMakeLists.txt`, `include/`, and `src/`.
- Java / Kotlin: package-to-path resolution against common source roots such as `src/main/java`, `src/main/kotlin`, and related layouts.
- Unknown languages: conservative regex fallback.

### Phase 3: Multi-Language Extraction

Implemented with tree-sitter-backed extraction for:

- Python
- JavaScript / TypeScript / TSX
- Go
- C / C++
- Java
- Kotlin

The analyzer uses one worker per language when the environment allows subprocess workers and falls back to sequential execution when process creation is blocked.

## Local Runtime Setup

Tree-sitter grammars are installed in the workspace venv at `.venv/`.
The runtime bootstraps that local venv automatically, so `python analyzer.py ...` works from the repo without requiring global installs.

## Usage

Analyze a project:

```bash
python analyzer.py /path/to/project
```

Analyze and open the visualizer:

```bash
python analyzer.py /path/to/project --serve
```

Write output anywhere and still serve it correctly:

```bash
python analyzer.py /path/to/project -o C:/temp/my-graph.json --serve
```

## Behavior Guarantees

- Analysis does not edit the target repository's `.gitignore`.
- Cache writes are limited to `<project_root>/.orbits_cache.json`.
- `--serve` does not depend on changing the process working directory.
- Missing parser support is reported in CLI output and metadata instead of being silently treated as an empty graph.

## Key Files

- `analyzer.py`: CLI entry point and HTTP serving.
- `lang_dispatch.py`: crawling, worker dispatch, cache integration, metadata.
- `worker.py`: per-language extraction and resolution execution.
- `extractors/`: tree-sitter and fallback extractors.
- `resolvers/`: language-specific resolution logic.
- `graph_engine.py`: graph enrichment and summary metrics.
- `visualizer.html`: bundled UI.

## Verification

The repo includes regression coverage for:

- non-mutating analysis behavior
- serving behavior
- Python import-from resolution
- TypeScript alias resolution
- Go module resolution
- C include resolution
- Java and Kotlin package resolution
- end-to-end graph shape
