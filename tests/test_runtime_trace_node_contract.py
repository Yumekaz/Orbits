import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import analyzer
from runtime_trace import merge_runtime_trace


class NodeRuntimeTraceContractTests(unittest.TestCase):
    def test_merge_runtime_trace_accepts_node_language_overlay(self):
        static_graph = {
            'nodes': [
                {
                    'id': 'src/server.js',
                    'filepath': 'src/server.js',
                    'name': 'server.js',
                    'dir': 'src',
                    'classification': 'ENTRY',
                    'language': 'javascript',
                    'size': 120,
                    'mtime': 1730001001,
                    'depth': 0,
                    'island_id': -1,
                },
                {
                    'id': 'src/runtime.js',
                    'filepath': 'src/runtime.js',
                    'name': 'runtime.js',
                    'dir': 'src',
                    'classification': 'CONNECTED',
                    'language': 'javascript',
                    'size': 90,
                    'mtime': 1730001002,
                    'depth': 1,
                    'island_id': -1,
                },
                {
                    'id': 'src/dynamic.js',
                    'filepath': 'src/dynamic.js',
                    'name': 'dynamic.js',
                    'dir': 'src',
                    'classification': 'LEAF',
                    'language': 'javascript',
                    'size': 90,
                    'mtime': 1730001003,
                    'depth': 1,
                    'island_id': -1,
                },
            ],
            'edges': [
                {
                    'source': 'src/server.js',
                    'target': 'src/runtime.js',
                    'line': 2,
                    'origins': ['static'],
                },
            ],
            'meta': {
                'root': 'C:/repo',
                'languages': ['javascript'],
                'import_stats': {'local': 1, 'stdlib': 0, 'external': 0, 'unknown': 0},
                'unsupported_languages': [],
            },
            'summary': {
                'counts': {'ENTRY': 1, 'CONNECTED': 1, 'LEAF': 1},
                'total': 3,
                'cycle_count': 0,
                'island_count': 0,
                'max_depth': 1,
                'health_score': 99,
                'unreachable': 0,
            },
        }
        trace = {
            'language': 'nodejs',
            'entry': {
                'mode': 'module',
                'target': 'src/server.js',
                'args': ['--mode', 'dev'],
            },
            'summary': {
                'local_edge_count': 2,
                'local_edge_hits': 4,
                'local_file_access_count': 1,
                'local_file_access_hits': 1,
            },
            'timed_out': False,
            'elapsed_s': 0.12,
            'exit_code': 0,
            'error': None,
            'file_accesses': [
                {
                    'source': 'src/server.js',
                    'path': 'src/dynamic.js',
                    'count': 1,
                    'modes': ['r'],
                    'lines': [4],
                    'line': 4,
                }
            ],
            'edges': [
                {
                    'source': 'src/server.js',
                    'target': 'src/runtime.js',
                    'line': 2,
                    'language': 'nodejs',
                    'runtime_hits': 2,
                    'runtime_modules': ['node:fs'],
                    'runtime_lines': [2],
                },
                {
                    'source': 'src/server.js',
                    'target': 'src/dynamic.js',
                    'line': 4,
                    'language': 'nodejs',
                    'runtime_hits': 2,
                    'runtime_modules': ['./dynamic.js'],
                    'runtime_lines': [4],
                },
            ],
        }

        merged = merge_runtime_trace(static_graph, trace, Path('C:/tmp/runtime_trace.json'), stale=False)

        dynamic_pairs = {(edge['source'], edge['target']) for edge in merged['dynamic_edges']}
        self.assertIn(('src/server.js', 'src/runtime.js'), dynamic_pairs)
        self.assertIn(('src/server.js', 'src/dynamic.js'), dynamic_pairs)
        self.assertEqual(merged['meta']['runtime']['language'], 'nodejs')
        self.assertEqual(merged['meta']['runtime']['entry_mode'], 'module')
        self.assertTrue(any(edge['dynamic'] for edge in merged['dynamic_edges']))
        self.assertEqual(merged['runtime']['entry']['mode'], 'module')

    def test_rerun_graph_preserves_node_overlay_and_marks_it_stale(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'src' / 'server.js').write_text('import "./runtime.js";\n', encoding='utf-8')
            (root / 'src' / 'runtime.js').write_text('export const value = 1;\n', encoding='utf-8')
            (root / 'src' / 'dynamic.js').write_text('export const dyn = 2;\n', encoding='utf-8')

            static_graph = analyzer.run(root, verbose=False)
            trace_path = root / 'runtime_trace.json'
            trace_path.write_text(json.dumps({
                'language': 'nodejs',
                'entry': {
                    'mode': 'module',
                    'target': 'src/server.js',
                    'args': [],
                },
                'summary': {
                    'local_edge_count': 2,
                    'local_edge_hits': 4,
                    'local_file_access_count': 0,
                    'local_file_access_hits': 0,
                },
                'timed_out': False,
                'elapsed_s': 0.08,
                'exit_code': 0,
                'error': None,
                'file_accesses': [],
                'edges': [
                    {
                        'source': 'src/server.js',
                        'target': 'src/runtime.js',
                        'line': 1,
                        'language': 'nodejs',
                        'runtime_hits': 1,
                        'runtime_modules': ['node:fs'],
                        'runtime_lines': [1],
                    },
                    {
                        'source': 'src/server.js',
                        'target': 'src/dynamic.js',
                        'line': 1,
                        'language': 'nodejs',
                        'runtime_hits': 1,
                        'runtime_modules': ['./dynamic.js'],
                        'runtime_lines': [1],
                    },
                ],
            }, indent=2), encoding='utf-8')

            static_graph['meta']['runtime'] = {
                'enabled': True,
                'language': 'nodejs',
                'artifact': str(trace_path),
                'entrypoint': 'src/server.js',
                'entry_mode': 'module',
                'args': [],
                'elapsed_s': 0.08,
                'runtime_edges': 2,
                'dynamic_edges': 1,
                'file_accesses': 0,
                'timed_out': False,
                'exit_code': 0,
                'stale': False,
                'error': None,
            }
            graph_path = root / 'graph.json'
            graph_path.write_text(json.dumps(static_graph, indent=2), encoding='utf-8')

            refreshed = analyzer._rerun_graph(graph_path, runtime_stale=True)
            dynamic_pairs = {(edge['source'], edge['target']) for edge in refreshed.get('dynamic_edges', [])}
            self.assertIn(('src/server.js', 'src/runtime.js'), dynamic_pairs)
            self.assertIn(('src/server.js', 'src/dynamic.js'), dynamic_pairs)
            self.assertEqual(refreshed['meta']['runtime']['language'], 'nodejs')
            self.assertTrue(refreshed['meta']['runtime']['stale'])


if __name__ == '__main__':
    unittest.main()
