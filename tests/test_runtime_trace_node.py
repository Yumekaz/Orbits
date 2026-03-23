import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import analyzer
from runtime_trace import NodeRuntimeTraceConfig, run_node_runtime_trace


@unittest.skipUnless(shutil.which('node'), 'Node.js required')
class NodeRuntimeTraceTests(unittest.TestCase):
    def test_node_runtime_trace_captures_imports_and_file_access(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'lib').mkdir()
            (root / 'data.txt').write_text('hello runtime\n', encoding='utf-8')
            (root / 'lib' / 'alpha.cjs').write_text('module.exports = { value: 1 };\n', encoding='utf-8')
            (root / 'lib' / 'beta.mjs').write_text('export const value = 2;\n', encoding='utf-8')
            (root / 'app.mjs').write_text(
                'import fs from "node:fs";\n'
                'import "./lib/alpha.cjs";\n'
                'fs.readFileSync(new URL("./data.txt", import.meta.url), "utf8");\n'
                'await import("./lib/beta.mjs");\n',
                encoding='utf-8',
            )

            trace = run_node_runtime_trace(
                root,
                NodeRuntimeTraceConfig(
                    mode='script',
                    target='app.mjs',
                    output_path=root / 'runtime_trace.json',
                    timeout_s=5,
                ),
                verbose=False,
            )

            edge_pairs = {(edge['source'], edge['target']) for edge in trace['edges']}
            self.assertIn(('app.mjs', 'lib/alpha.cjs'), edge_pairs)
            self.assertIn(('app.mjs', 'lib/beta.mjs'), edge_pairs)
            self.assertTrue(any(item['source'] == 'app.mjs' and item['path'] == 'data.txt' for item in trace['file_accesses']))
            self.assertEqual(trace['language'], 'nodejs')
            self.assertFalse(trace['timed_out'])

    def test_analyzer_run_merges_node_runtime_overlay(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'lib').mkdir()
            (root / 'lib' / 'alpha.cjs').write_text('module.exports = 1;\n', encoding='utf-8')
            (root / 'app.js').write_text('require("./lib/alpha.cjs");\n', encoding='utf-8')

            graph = analyzer.run(
                root,
                verbose=False,
                runtime_trace=NodeRuntimeTraceConfig(
                    mode='script',
                    target='app.js',
                    output_path=root / 'runtime_trace.json',
                    timeout_s=5,
                ),
            )

            dynamic_pairs = {(edge['source'], edge['target']) for edge in graph.get('dynamic_edges', [])}
            self.assertIn(('app.js', 'lib/alpha.cjs'), dynamic_pairs)
            self.assertTrue(graph['meta']['runtime']['enabled'])
            self.assertEqual(graph['meta']['runtime']['language'], 'nodejs')
            self.assertEqual(graph['meta']['runtime']['engine'], 'node')
            self.assertEqual(len(graph['meta']['runtime'].get('sessions', [])), 1)

    def test_rerun_graph_preserves_node_runtime_overlay_and_marks_it_stale(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'lib').mkdir()
            (root / 'lib' / 'alpha.cjs').write_text('module.exports = 1;\n', encoding='utf-8')
            (root / 'app.js').write_text('require("./lib/alpha.cjs");\n', encoding='utf-8')

            graph = analyzer.run(
                root,
                verbose=False,
                runtime_trace=NodeRuntimeTraceConfig(
                    mode='script',
                    target='app.js',
                    output_path=root / 'runtime_trace.json',
                    timeout_s=5,
                ),
            )
            graph_path = root / 'graph.json'
            graph_path.write_text(json.dumps(graph, indent=2), encoding='utf-8')

            refreshed = analyzer._rerun_graph(graph_path, runtime_stale=True)
            dynamic_pairs = {(edge['source'], edge['target']) for edge in refreshed.get('dynamic_edges', [])}
            self.assertIn(('app.js', 'lib/alpha.cjs'), dynamic_pairs)
            self.assertTrue(refreshed['meta']['runtime']['stale'])
            self.assertEqual(refreshed['meta']['runtime']['language'], 'nodejs')


if __name__ == '__main__':
    unittest.main()
