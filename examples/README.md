# Orbits Demo Evidence

These examples are intentionally small so a judge can run them quickly and compare the output by eye.

## Install the CLI

From the repo root:

```powershell
python -m pip install -e .
orbits --help
```

The editable install exposes the `orbits` console script while keeping the bundled visualizer assets in the source tree for `--serve`.

## Analyze

```powershell
orbits test_repo -o demo-output/test_repo.graph.json
```

Expected evidence from the bundled fixture:

```text
Files:     4
Edges:     0
Entrypoints: 1 detected
Health:    70/100  Orphans:3  Islands:0  Cycles:0
Output:    ...\demo-output\test_repo.graph.json
```

On locked-down Windows environments, Orbits may print a parallel-worker fallback warning. That warning is environmental; the sequential analyzer path should still produce the same graph shape.

## Check

Passing threshold example:

```powershell
orbits test_repo -o demo-output/test_repo.graph.json --check --max-orphans 10 --max-islands 10 --min-health 0
```

Expected result:

```text
Check:     PASS
```

Failing threshold example:

```powershell
orbits test_repo -o demo-output/test_repo.graph.json --check --max-orphans 0
```

Expected result:

```text
Check:     FAIL
- orphans 3 > 0
```

`--check` exits with code `2` when a threshold fails, which makes it usable in CI or rubric scripts.

## Reports

```powershell
orbits test_repo `
  -o demo-output/test_repo.graph.json `
  --dead-report-md demo-output/dead-files.md `
  --dead-report-csv demo-output/dead-files.csv
```

Dead-file reports include structural classification plus git/runtime confidence columns when that context is available. Generate these reports fresh for a demo because git ages and authors are repository-history dependent.

## Diff

```powershell
orbits --diff examples/fixtures/baseline-graph.json examples/fixtures/current-graph.json
orbits --diff examples/fixtures/baseline-graph.json examples/fixtures/current-graph.json --diff-json
```

Expected text-mode highlights:

```text
Nodes: 3 -> 3 (0)
  Added nodes:
    + new.py
  Removed nodes:
    - old.py

Edges: 1 -> 2 (+1)
  Added edges:
    + app.py -> new.py

Waste: 1 -> 1 (0)
  New waste:
    + new.py
  Removed waste:
    - old.py
```

## Runtime Tracing Boundaries

Runtime tracing executes a real entrypoint and writes a separate runtime artifact:

```powershell
orbits path\to\project --trace-python app.py --runtime-output demo-output/python_runtime_trace.json
orbits path\to\project --trace-node app.js --runtime-output demo-output/node_runtime_trace.json
orbits path\to\project --runtime-input demo-output/python_runtime_trace.json --runtime-input demo-output/node_runtime_trace.json
```

Scoped native tracing is intentionally narrower:

```powershell
orbits path\to\project --trace-cpp build\my_binary --runtime-output demo-output/cpp_runtime_trace.json
```

Runtime edges are overlays. They show executed Python/Node paths or scoped native loader/import dependencies, but they do not replace static analysis and they do not rewrite static health, cycle, depth, or waste metrics.
