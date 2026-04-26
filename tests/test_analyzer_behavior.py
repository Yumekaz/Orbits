import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.request import urlopen

import analyzer
from lang_dispatch import _detect_language_support, extract_all


def _normalize_pairs(edges):
    return {(src.replace('\\', '/'), dst.replace('\\', '/')) for src, dst in edges}


class AnalyzerBehaviorTests(unittest.TestCase):
    def _minimal_graph(self):
        return {
            'nodes': [],
            'edges': [],
            'summary': {'health_score': 100},
            'meta': {
                'total_files': 0,
                'total_edges': 0,
                'import_stats': {},
            },
        }

    def test_scan_open_alias_runs_analysis_and_serves(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / 'scan-graph.json'
            served = []

            with patch.object(analyzer, 'run', return_value=self._minimal_graph()) as run_mock, \
                    patch.object(analyzer, 'serve', side_effect=lambda path, port: served.append((Path(path), port))):
                analyzer.main(['scan', str(root), '--open', '--output', str(output), '--port', '9001'])

            run_mock.assert_called_once()
            self.assertEqual(run_mock.call_args.args[0], root.resolve())
            self.assertEqual(served, [(output.resolve(), 9001)])
            self.assertTrue(output.is_file())

    def test_legacy_serve_still_runs_analysis_and_serves(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / 'legacy-graph.json'
            served = []

            with patch.object(analyzer, 'run', return_value=self._minimal_graph()), \
                    patch.object(analyzer, 'serve', side_effect=lambda path, port: served.append((Path(path), port))):
                analyzer.main([str(root), '--serve', '--output', str(output), '--port', '9002'])

            self.assertEqual(served, [(output.resolve(), 9002)])
            self.assertTrue(output.is_file())

    def test_delete_plan_allows_only_high_confidence_untouched_waste(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'dead.py'
            target.write_text('print("old")\n', encoding='utf-8')
            graph = {
                'nodes': [{'id': 'dead.py', 'classification': 'ORPHAN'}],
                'waste': [{
                    'id': 'dead.py',
                    'classification': 'ORPHAN',
                    'confidence_score': 84,
                    'confidence_level': 'high',
                    'confidence_reasons': ['structural orphan with no static in/out edges'],
                    'runtime_context': {'available': True, 'touched': False, 'stale': False},
                }],
            }

            plan = analyzer._build_delete_file_plan(graph, 'dead.py', target)

            self.assertTrue(plan['allowed'])
            self.assertEqual(plan['confidence_score'], 84)
            self.assertEqual(plan['blockers'], [])

    def test_delete_plan_blocks_runtime_touched_or_low_confidence_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'maybe.py'
            target.write_text('print("maybe")\n', encoding='utf-8')
            graph = {
                'nodes': [{'id': 'maybe.py', 'classification': 'ORPHAN'}],
                'waste': [{
                    'id': 'maybe.py',
                    'classification': 'ORPHAN',
                    'confidence_score': 54,
                    'confidence_level': 'low',
                    'runtime_context': {'available': True, 'touched': True, 'stale': False},
                }],
            }

            plan = analyzer._build_delete_file_plan(graph, 'maybe.py', target)

            self.assertFalse(plan['allowed'])
            self.assertIn('confidence score is 54/100, below the high-confidence threshold', plan['blockers'])
            self.assertIn('observed in a runtime trace', plan['blockers'])

    def test_run_does_not_edit_gitignore(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.gitignore').write_text('node_modules\n', encoding='utf-8')
            (root / 'app.py').write_text('import os\n', encoding='utf-8')

            analyzer.run(root, verbose=False)

            self.assertEqual((root / '.gitignore').read_text(encoding='utf-8'), 'node_modules\n')

    def test_server_serves_visualizer_and_graph_without_chdir(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            viz = root / 'visualizer.html'
            graph = root / 'graph.json'
            asset = root / 'visualizer_worker.js'
            viz.write_text('<html>viz</html>', encoding='utf-8')
            graph.write_text(json.dumps({'ok': True}), encoding='utf-8')
            asset.write_text('console.log(1);', encoding='utf-8')

            handler = analyzer.make_server_handler(viz, graph)
            server = analyzer.http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                with urlopen(base + '/visualizer.html') as response:
                    self.assertEqual(response.read().decode('utf-8'), '<html>viz</html>')
                with urlopen(base + '/graph.json') as response:
                    self.assertEqual(json.loads(response.read().decode('utf-8')), {'ok': True})
                with urlopen(base + '/visualizer_worker.js') as response:
                    self.assertEqual(response.read().decode('utf-8'), 'console.log(1);')
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_missing_parser_metadata_matches_environment(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.js').write_text("import './dep.js';\n", encoding='utf-8')
            (root / 'dep.js').write_text('export const x = 1;\n', encoding='utf-8')
            (root / 'main.go').write_text('package main\nimport "fmt"\n', encoding='utf-8')

            raw = extract_all(root, verbose=False)
            support = _detect_language_support()
            unsupported = {item['language'] for item in raw['meta']['unsupported_languages']}

            expected = {lang for lang in ('javascript', 'go') if not support[lang]['available']}
            self.assertEqual(unsupported, expected)


if __name__ == '__main__':
    unittest.main()
