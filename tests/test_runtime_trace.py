import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import analyzer
from runtime_trace import PythonRuntimeTraceConfig, run_python_runtime_trace


class RuntimeTraceTests(unittest.TestCase):
    def test_python_runtime_trace_captures_dynamic_import_and_file_access(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'plugins').mkdir()
            (root / 'plugins' / '__init__.py').write_text('', encoding='utf-8')
            (root / 'plugins' / 'alpha.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'app.py').write_text(
                'import importlib\n'
                'with open(__file__, "r", encoding="utf-8") as handle:\n'
                '    handle.read(8)\n'
                'importlib.import_module("plugins.alpha")\n',
                encoding='utf-8',
            )

            trace = run_python_runtime_trace(
                root,
                PythonRuntimeTraceConfig(
                    mode='script',
                    target='app.py',
                    output_path=root / 'runtime_trace.json',
                    timeout_s=5,
                ),
                verbose=False,
            )

            edge_pairs = {(edge['source'], edge['target']) for edge in trace['edges']}
            self.assertIn(('app.py', 'plugins/alpha.py'), edge_pairs)
            self.assertTrue(any(item['source'] == 'app.py' and item['path'] == 'app.py' for item in trace['file_accesses']))
            self.assertFalse(trace['timed_out'])

    def test_analyzer_run_merges_runtime_overlay(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'plugins').mkdir()
            (root / 'plugins' / '__init__.py').write_text('', encoding='utf-8')
            (root / 'plugins' / 'alpha.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'app.py').write_text(
                'import importlib\n'
                'importlib.import_module("plugins.alpha")\n',
                encoding='utf-8',
            )

            graph = analyzer.run(
                root,
                verbose=False,
                runtime_trace=PythonRuntimeTraceConfig(
                    mode='script',
                    target='app.py',
                    output_path=root / 'runtime_trace.json',
                    timeout_s=5,
                ),
            )

            dynamic_pairs = {(edge['source'], edge['target']) for edge in graph.get('dynamic_edges', [])}
            self.assertIn(('app.py', 'plugins/alpha.py'), dynamic_pairs)
            self.assertTrue(graph['meta']['runtime']['enabled'])
            self.assertGreaterEqual(graph['meta']['runtime']['dynamic_edges'], 1)
            self.assertIn('runtime', graph)

    def test_rerun_graph_preserves_runtime_overlay_and_marks_it_stale(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'plugins').mkdir()
            (root / 'plugins' / '__init__.py').write_text('', encoding='utf-8')
            (root / 'plugins' / 'alpha.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'app.py').write_text(
                'import importlib\n'
                'importlib.import_module("plugins.alpha")\n',
                encoding='utf-8',
            )

            graph = analyzer.run(
                root,
                verbose=False,
                runtime_trace=PythonRuntimeTraceConfig(
                    mode='script',
                    target='app.py',
                    output_path=root / 'runtime_trace.json',
                    timeout_s=5,
                ),
            )
            graph_path = root / 'graph.json'
            graph_path.write_text(json.dumps(graph, indent=2), encoding='utf-8')

            refreshed = analyzer._rerun_graph(graph_path, runtime_stale=True)
            dynamic_pairs = {(edge['source'], edge['target']) for edge in refreshed.get('dynamic_edges', [])}
            self.assertIn(('app.py', 'plugins/alpha.py'), dynamic_pairs)
            self.assertTrue(refreshed['meta']['runtime']['stale'])


if __name__ == '__main__':
    unittest.main()
