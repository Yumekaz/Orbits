<!-- orbits-pr-comment -->
## Orbits check

**Status:** FAIL
**Runtime: fresh, 1 session(s), 1 observed edge(s), entry `app.py`**

| Files | Edges | Health | Dead files | Orphans | Islands | Cycles |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 2 | 75/100 | 2 | 2 | 0 | 0 |

Dead-file reports and `graph.json` are available in the `orbits-report` artifact.
Markdown report path in the artifact: `examples/demo-dead-files.md`.
Download artifacts from the workflow run: https://github.com/Yumekaz/Orbits/actions/runs/example.

### Top dead files

| Path | Why it is actionable | Confidence | Key evidence |
| --- | --- | --- | --- |
| `new.py` | No static imports in or out | High (86/100) | structural orphan with no static in/out edges; last touched 420 days ago; not observed in fresh runtime trace |
| `shared.py` | No static imports in or out | High (79/100) | structural orphan with no static in/out edges; last touched 95 days ago; not observed in fresh runtime trace |

### Graph diff

Graph size: 4 -> 4 files (0).
Dependency edges: 1 -> 2 (+1).
Runtime edges: 0 -> 1 (+1).
Actionable dead files: 2 -> 2 (0).

New probable dead files introduced by this PR:

| Path | Why Orbits flagged it | Confidence | Reasons |
| --- | --- | --- | --- |
| `new.py` | No static imports in or out | High (86/100) | structural orphan with no static in/out edges; last touched 420 days ago; not observed in fresh runtime trace |

Resolved dead-file candidates: `old.py`.

| Area | Before | After | Delta |
| --- | ---: | ---: | ---: |
| Nodes | 4 | 4 | 0 |
| Edges | 1 | 2 | +1 |
| Runtime edges | 0 | 1 | +1 |
| Dead files | 2 | 2 | 0 |

<details>
<summary>Diff details</summary>

**Added nodes**
- `+ new.py`

**Removed nodes**
- `- old.py`

**New dead files**
- `+ new.py`

**Resolved dead files**
- `- old.py`

**Added edges**
- `+ app.py -> new.py`

**Added runtime edges**
- `+ app.py -> new.py`

**Classification changes**
- `util.py`: INTERNAL -> LEAF

**Confidence changes**
- `shared.py`: 56 -> 79 (+23)

</details>

Workflow run: https://github.com/Yumekaz/Orbits/actions/runs/example
