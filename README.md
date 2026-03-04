# Orbits ⬡

**Orbits** is a high-performance codebase dependency visualizer and dead-code detector for Python. It transforms complex import structures into interactive, actionable graphs, allowing developers to identify architectural bottlenecks, circular dependencies, and isolated "islands" of code.

![Orbits Interface](/C:/Users/Mihir/.gemini/antigravity/brain/ed4afcd8-434b-4337-92bf-6d3b999e7b6b/orbits_real_world_test_1772646282545.png)

---

## ⚡ Key Features (Phase 1)

- **Interactive Dependency Mapping**: Explore your codebase through a force-directed graph powered by D3.js.
- **Dead Code Detection**: Instantly identify **Orphans** (isolated files) and **Islands** (disconnected clusters).
- **Cycle Tracking**: Detect circular dependencies that can cause runtime issues and maintenance headaches.
- **Codebase Health Score**: A proprietary metric (0-100) based on the ratio of connected code to technical debt (cycles/orphans).
- **Depth Analysis**: Understand how "deeply" modules are nested from your project's entry points.
- **AST-Powered Parsing**: High-accuracy import extraction using Python's native Abstract Syntax Tree.

---

## 🛠️ Architecture

Orbits follows a modular analysis pipeline:

1.  **Crawl**: Recursively scans the project root while respecting `.gitignore` and skipping noise (e.g., `venv`, `node_modules`).
2.  **Extract**: Parses Python files with `ast` to retrieve every `import` and `from ... import` statement.
3.  **Resolve**: Maps import strings to absolute file system paths.
4.  **Analyze**: Processes the raw graph through the **Graph Engine** to detect cycles and calculate health.
5.  **Visualize**: Serves a local web interface to explore the results interactively.

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.8+
- No external Python dependencies required (standard library only for the analyzer).

### 2. Run Analysis
To analyze a project and start the visualizer immediately:

```bash
python analyzer.py /path/to/your/project --serve
```

### 3. CLI Options
- `-o, --output`: Specify a custom JSON filename (default: `graph.json`).
- `--port`: Use a specific port for the local server (default: `8765`).
- `--serve`: Automatically open the browser after analysis.

---

## 📁 Project Structure

```text
├── analyzer.py       # Main CLI entry point
├── graph_engine.py   # Core analysis & classification engine
├── extractor.py      # AST-based import extractor
├── crawler.py        # File system walker
└── visualizer.html   # D3.js powered UI
```

---

## 🏗️ Future Roadmap
- **Phase 2**: Multi-language support (JS/TS, Go, Rust).
- **Phase 3**: Cross-language dependency resolution.
- **Phase 4**: Real-time "Watch Mode" for live visualization changes.

---
*Created with ❤️ for Python Architects.*
