# Orbits Demo Evidence

These examples are intentionally small so a judge can run them quickly and compare the output by eye.

## Visual Evidence

![Orbits visualizer demo](orbits-demo.png)

The image above is a committed visual proof artifact from the Playwright baseline suite. Regenerate visual baselines with `npm run test:e2e:visual:update` when intentionally changing the UI.

## PR Comment Evidence

`demo-pr-comment.md` is a generated sample of the sticky GitHub PR comment. It shows the product wedge directly: new probable dead files, confidence evidence, runtime-edge deltas, classification changes, and graph impact in one reviewable note.

`fixtures/demo-graph-diff.json` is the machine-readable diff used by that sample comment.

## Install the CLI

From the repo root:

```powershell
python -m pip install -e .
orbits --help
python -m orbits --help
```

The package exposes the `orbits` console script and bundles the visualizer assets required by `orbits --open`.
The product-path command is `orbits scan . --open`; the older `orbits . --serve` form remains supported.

## Analyze

```powershell
orbits scan test_repo -o demo-output/test_repo.graph.json
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
orbits scan test_repo -o demo-output/test_repo.graph.json --check --max-orphans 10 --max-islands 10 --min-health 0
```

Expected result:

```text
Check:     PASS
```

Failing threshold example:

```powershell
orbits scan test_repo -o demo-output/test_repo.graph.json --check --max-orphans 0
```

Expected result:

```text
Check:     FAIL
- orphans 3 > 0
```

`--check` exits with code `2` when a threshold fails, which makes it usable in CI or rubric scripts.

## Reports

```powershell
orbits scan test_repo `
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
Nodes: 4 -> 4 (0)
  Added nodes:
    + new.py
  Removed nodes:
    - old.py

Edges: 1 -> 2 (+1)
  Added edges:
    + app.py -> new.py

Dynamic edges: 0 -> 1 (+1)
  Added dynamic edges:
    + app.py -> new.py

Waste: 2 -> 2 (0)
  New waste:
    + new.py
  Removed waste:
    - old.py

Classification changes: 1
  ~ util.py: INTERNAL -> LEAF

Confidence changes: 1
  ~ shared.py: 56 -> 79 (+23)
```

The diff also reports runtime-edge deltas, classification changes, and confidence-score changes when those fields exist in the compared graphs.

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

Static C/C++ extraction also recognizes literal local `dlopen(...)`, `LoadLibrary(...)`, and `LoadLibraryEx(...)` references when the library file exists in the repository.
