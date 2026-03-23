import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import analyzer
from runtime_trace import _source_map_candidates, merge_runtime_trace, merge_runtime_traces


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

    def test_merge_runtime_traces_combines_python_and_node_sessions(self):
        static_graph = {
            'nodes': [
                {'id': 'app/main.py', 'filepath': 'app/main.py', 'name': 'main.py', 'dir': 'app', 'classification': 'ENTRY', 'language': 'python', 'size': 10, 'mtime': 1, 'depth': 0, 'island_id': -1},
                {'id': 'app/plugin.py', 'filepath': 'app/plugin.py', 'name': 'plugin.py', 'dir': 'app', 'classification': 'LEAF', 'language': 'python', 'size': 10, 'mtime': 1, 'depth': 1, 'island_id': -1},
                {'id': 'src/index.js', 'filepath': 'src/index.js', 'name': 'index.js', 'dir': 'src', 'classification': 'ENTRY', 'language': 'javascript', 'size': 10, 'mtime': 1, 'depth': 0, 'island_id': -1},
                {'id': 'src/runtime.js', 'filepath': 'src/runtime.js', 'name': 'runtime.js', 'dir': 'src', 'classification': 'LEAF', 'language': 'javascript', 'size': 10, 'mtime': 1, 'depth': 1, 'island_id': -1},
            ],
            'edges': [],
            'meta': {'root': 'C:/repo', 'languages': ['python', 'javascript'], 'import_stats': {'local': 0, 'stdlib': 0, 'external': 0, 'unknown': 0}, 'unsupported_languages': []},
            'summary': {'counts': {'ENTRY': 2, 'LEAF': 2}, 'total': 4, 'cycle_count': 0, 'island_count': 0, 'max_depth': 1, 'health_score': 100, 'unreachable': 0},
        }
        python_trace = {
            'language': 'python',
            'engine': 'python',
            'entry': {'mode': 'script', 'target': 'app/main.py', 'args': []},
            'summary': {'local_edge_count': 1, 'local_edge_hits': 1, 'local_file_access_count': 0, 'local_file_access_hits': 0},
            'timed_out': False,
            'elapsed_s': 0.1,
            'exit_code': 0,
            'error': None,
            'file_accesses': [],
            'edges': [{'source': 'app/main.py', 'target': 'app/plugin.py', 'line': 3, 'language': 'python', 'runtime_hits': 1, 'runtime_modules': ['app.plugin'], 'runtime_lines': [3]}],
        }
        node_trace = {
            'language': 'nodejs',
            'engine': 'node',
            'entry': {'mode': 'module', 'target': 'src/index.js', 'args': []},
            'summary': {'local_edge_count': 1, 'local_edge_hits': 2, 'local_file_access_count': 0, 'local_file_access_hits': 0},
            'timed_out': False,
            'elapsed_s': 0.2,
            'exit_code': 0,
            'error': None,
            'file_accesses': [],
            'edges': [{'source': 'src/index.js', 'target': 'src/runtime.js', 'line': 4, 'language': 'javascript', 'runtime_hits': 2, 'runtime_modules': ['./runtime.js'], 'runtime_lines': [4]}],
        }

        merged = merge_runtime_traces(
            static_graph,
            [
                (python_trace, Path('C:/tmp/python_runtime_trace.json'), False),
                (node_trace, Path('C:/tmp/node_runtime_trace.json'), True),
            ],
        )

        dynamic_pairs = {(edge['source'], edge['target']) for edge in merged['dynamic_edges']}
        self.assertEqual(dynamic_pairs, {('app/main.py', 'app/plugin.py'), ('src/index.js', 'src/runtime.js')})
        self.assertEqual(merged['meta']['runtime']['session_count'], 2)
        self.assertEqual(merged['meta']['runtime']['language'], 'mixed')
        self.assertEqual(set(merged['meta']['runtime']['languages']), {'python', 'nodejs'})
        self.assertTrue(merged['meta']['runtime']['stale'])
        self.assertEqual(len(merged['runtime']['sessions']), 2)

    def test_source_map_remap_maps_dist_js_runtime_edges_back_to_src_ts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'dist').mkdir()
            (root / 'dist' / 'server.js.map').write_text(json.dumps({
                'version': 3,
                'file': 'server.js',
                'sources': ['../src/server.ts'],
            }), encoding='utf-8')
            (root / 'dist' / 'dynamic.js.map').write_text(json.dumps({
                'version': 3,
                'file': 'dynamic.js',
                'sources': ['../src/dynamic.ts'],
            }), encoding='utf-8')
            static_graph = {
                'nodes': [
                    {'id': 'src/server.ts', 'filepath': 'src/server.ts', 'name': 'server.ts', 'dir': 'src', 'classification': 'ENTRY', 'language': 'typescript', 'size': 10, 'mtime': 1, 'depth': 0, 'island_id': -1},
                    {'id': 'src/dynamic.ts', 'filepath': 'src/dynamic.ts', 'name': 'dynamic.ts', 'dir': 'src', 'classification': 'LEAF', 'language': 'typescript', 'size': 10, 'mtime': 1, 'depth': 1, 'island_id': -1},
                ],
                'edges': [],
                'meta': {'root': str(root), 'languages': ['typescript'], 'import_stats': {'local': 0, 'stdlib': 0, 'external': 0, 'unknown': 0}, 'unsupported_languages': []},
                'summary': {'counts': {'ENTRY': 1, 'LEAF': 1}, 'total': 2, 'cycle_count': 0, 'island_count': 0, 'max_depth': 1, 'health_score': 100, 'unreachable': 0},
            }
            trace = {
                'language': 'nodejs',
                'engine': 'node',
                'entry': {'mode': 'script', 'target': 'dist/server.js', 'args': []},
                'summary': {'local_edge_count': 1, 'local_edge_hits': 1, 'local_file_access_count': 0, 'local_file_access_hits': 0},
                'timed_out': False,
                'elapsed_s': 0.1,
                'exit_code': 0,
                'error': None,
                'file_accesses': [],
                'edges': [{'source': 'dist/server.js', 'target': 'dist/dynamic.js', 'line': 7, 'language': 'javascript', 'runtime_hits': 1, 'runtime_modules': ['./dynamic.js'], 'runtime_lines': [7]}],
            }

            merged = merge_runtime_traces(static_graph, [(trace, root / 'runtime_trace.json', False)])
            dynamic_pairs = {(edge['source'], edge['target']) for edge in merged['dynamic_edges']}
            self.assertIn(('src/server.ts', 'src/dynamic.ts'), dynamic_pairs)
            self.assertEqual(merged['meta']['runtime']['dynamic_edges'], 1)

    def test_nested_build_prefix_remap_maps_package_dist_js_back_to_src_ts(self):
        static_graph = {
            'nodes': [
                {'id': 'packages/shared/src/index.ts', 'filepath': 'packages/shared/src/index.ts', 'name': 'index.ts', 'dir': 'packages/shared/src', 'classification': 'ENTRY', 'language': 'typescript', 'size': 10, 'mtime': 1, 'depth': 0, 'island_id': -1},
                {'id': 'packages/shared/src/runtime.ts', 'filepath': 'packages/shared/src/runtime.ts', 'name': 'runtime.ts', 'dir': 'packages/shared/src', 'classification': 'LEAF', 'language': 'typescript', 'size': 10, 'mtime': 1, 'depth': 1, 'island_id': -1},
            ],
            'edges': [],
            'meta': {'root': 'C:/repo', 'languages': ['typescript'], 'import_stats': {'local': 0, 'stdlib': 0, 'external': 0, 'unknown': 0}, 'unsupported_languages': []},
            'summary': {'counts': {'ENTRY': 1, 'LEAF': 1}, 'total': 2, 'cycle_count': 0, 'island_count': 0, 'max_depth': 1, 'health_score': 100, 'unreachable': 0},
        }
        trace = {
            'language': 'nodejs',
            'engine': 'node',
            'entry': {'mode': 'script', 'target': 'packages/shared/dist/index.js', 'args': []},
            'summary': {'local_edge_count': 1, 'local_edge_hits': 1, 'local_file_access_count': 0, 'local_file_access_hits': 0},
            'timed_out': False,
            'elapsed_s': 0.1,
            'exit_code': 0,
            'error': None,
            'file_accesses': [],
            'edges': [{'source': 'packages/shared/dist/index.js', 'target': 'packages/shared/dist/runtime.js', 'line': 5, 'language': 'javascript', 'runtime_hits': 1, 'runtime_modules': ['./runtime.js'], 'runtime_lines': [5]}],
        }

        merged = merge_runtime_traces(static_graph, [(trace, Path('C:/tmp/runtime_trace.json'), False)])
        dynamic_pairs = {(edge['source'], edge['target']) for edge in merged['dynamic_edges']}
        self.assertIn(('packages/shared/src/index.ts', 'packages/shared/src/runtime.ts'), dynamic_pairs)

    def test_node_runtime_file_accesses_remap_from_dist_js_to_src_ts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'dist').mkdir()
            (root / 'src' / 'server.ts').write_text('export const server = 1;\n', encoding='utf-8')
            (root / 'src' / 'config.ts').write_text('export const config = 2;\n', encoding='utf-8')
            (root / 'dist' / 'server.js.map').write_text(json.dumps({'version': 3, 'file': 'server.js', 'sources': ['../src/server.ts']}), encoding='utf-8')
            (root / 'dist' / 'config.js.map').write_text(json.dumps({'version': 3, 'file': 'config.js', 'sources': ['../src/config.ts']}), encoding='utf-8')

            static_graph = {
                'nodes': [
                    {'id': 'src/server.ts', 'filepath': 'src/server.ts', 'name': 'server.ts', 'dir': 'src', 'classification': 'ENTRY', 'language': 'typescript', 'size': 10, 'mtime': 1, 'depth': 0, 'island_id': -1},
                    {'id': 'src/config.ts', 'filepath': 'src/config.ts', 'name': 'config.ts', 'dir': 'src', 'classification': 'LEAF', 'language': 'typescript', 'size': 10, 'mtime': 1, 'depth': 1, 'island_id': -1},
                ],
                'edges': [],
                'meta': {'root': str(root), 'languages': ['typescript'], 'import_stats': {'local': 0, 'stdlib': 0, 'external': 0, 'unknown': 0}, 'unsupported_languages': []},
                'summary': {'counts': {'ENTRY': 1, 'LEAF': 1}, 'total': 2, 'cycle_count': 0, 'island_count': 0, 'max_depth': 1, 'health_score': 100, 'unreachable': 0},
            }
            trace = {
                'language': 'nodejs',
                'engine': 'node',
                'entry': {'mode': 'script', 'target': 'dist/server.js', 'args': []},
                'summary': {'local_edge_count': 0, 'local_edge_hits': 0, 'local_file_access_count': 1, 'local_file_access_hits': 1},
                'timed_out': False,
                'elapsed_s': 0.1,
                'exit_code': 0,
                'error': None,
                'edges': [],
                'file_accesses': [{'source': 'dist/server.js', 'path': 'dist/config.js', 'count': 1, 'modes': ['r'], 'lines': [4], 'line': 4}],
            }

            merged = merge_runtime_traces(static_graph, [(trace, root / 'runtime_trace.json', False)])
            self.assertEqual(merged['runtime']['file_accesses'][0]['source'], 'src/server.ts')
            self.assertEqual(merged['runtime']['file_accesses'][0]['path'], 'src/config.ts')

    def test_node_runtime_entrypoint_remaps_from_dist_js_to_src_ts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'dist').mkdir()
            (root / 'src' / 'server.ts').write_text('export const server = 1;\n', encoding='utf-8')
            (root / 'dist' / 'server.js.map').write_text(json.dumps({'version': 3, 'file': 'server.js', 'sources': ['../src/server.ts']}), encoding='utf-8')

            static_graph = {
                'nodes': [
                    {'id': 'src/server.ts', 'filepath': 'src/server.ts', 'name': 'server.ts', 'dir': 'src', 'classification': 'ENTRY', 'language': 'typescript', 'size': 10, 'mtime': 1, 'depth': 0, 'island_id': -1},
                ],
                'edges': [],
                'meta': {'root': str(root), 'languages': ['typescript'], 'import_stats': {'local': 0, 'stdlib': 0, 'external': 0, 'unknown': 0}, 'unsupported_languages': []},
                'summary': {'counts': {'ENTRY': 1}, 'total': 1, 'cycle_count': 0, 'island_count': 0, 'max_depth': 0, 'health_score': 100, 'unreachable': 0},
            }
            trace = {
                'language': 'nodejs',
                'engine': 'node',
                'entry': {'mode': 'script', 'target': 'dist/server.js', 'args': []},
                'summary': {'local_edge_count': 0, 'local_edge_hits': 0, 'local_file_access_count': 0, 'local_file_access_hits': 0},
                'timed_out': False,
                'elapsed_s': 0.1,
                'exit_code': 0,
                'error': None,
                'edges': [],
                'file_accesses': [],
            }

            merged = merge_runtime_traces(static_graph, [(trace, root / 'runtime_trace.json', False)])
            self.assertEqual(merged['meta']['runtime']['entrypoint'], 'src/server.ts')
            self.assertEqual(merged['runtime']['entry']['target'], 'src/server.ts')

    def test_source_map_candidates_accept_inline_and_custom_mapping_urls(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'dist').mkdir()
            (root / 'dist' / 'maps').mkdir()
            (root / 'src' / 'server.ts').write_text('export const server = 1;\n', encoding='utf-8')
            (root / 'src' / 'custom.ts').write_text('export const custom = 2;\n', encoding='utf-8')

            inline_map = base64.b64encode(json.dumps({
                'version': 3,
                'file': 'server.js',
                'sources': ['../src/server.ts'],
            }).encode('utf-8')).decode('ascii')
            (root / 'dist' / 'server.js').write_text(
                f'console.log("server");\n//# sourceMappingURL=data:application/json;base64,{inline_map}\n',
                encoding='utf-8',
            )
            (root / 'dist' / 'custom.js').write_text(
                'console.log("custom");\n//# sourceMappingURL=maps/custom.bundle.map\n',
                encoding='utf-8',
            )
            (root / 'dist' / 'maps' / 'custom.bundle.map').write_text(json.dumps({
                'version': 3,
                'file': 'custom.js',
                'sources': ['../../src/custom.ts'],
            }), encoding='utf-8')

            self.assertIn('src/server.ts', _source_map_candidates('dist/server.js', root))
            self.assertIn('src/custom.ts', _source_map_candidates('dist/custom.js', root))

    def test_source_map_candidates_handle_webpack_and_repo_absolute_sources(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'dist').mkdir()
            (root / 'src' / 'server.ts').write_text('export const server = 1;\n', encoding='utf-8')
            (root / 'src' / 'absolute.ts').write_text('export const absolute = 2;\n', encoding='utf-8')
            (root / 'dist' / 'server.js.map').write_text(json.dumps({
                'version': 3,
                'file': 'server.js',
                'sources': ['webpack://_N_E/./src/server.ts'],
            }), encoding='utf-8')
            (root / 'dist' / 'absolute.js.map').write_text(json.dumps({
                'version': 3,
                'file': 'absolute.js',
                'sources': ['/src/absolute.ts'],
            }), encoding='utf-8')

            self.assertIn('src/server.ts', _source_map_candidates('dist/server.js', root))
            self.assertIn('src/absolute.ts', _source_map_candidates('dist/absolute.js', root))

    def test_merge_runtime_traces_injects_runtime_only_node_for_mapped_source_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'dist').mkdir()
            (root / 'src' / 'server.ts').write_text('export const server = 1;\n', encoding='utf-8')
            (root / 'src' / 'dynamic.ts').write_text('export const dynamic = 2;\n', encoding='utf-8')
            (root / 'dist' / 'server.js.map').write_text(json.dumps({
                'version': 3,
                'file': 'server.js',
                'sources': ['../src/server.ts'],
            }), encoding='utf-8')
            (root / 'dist' / 'dynamic.js.map').write_text(json.dumps({
                'version': 3,
                'file': 'dynamic.js',
                'sources': ['../src/dynamic.ts'],
            }), encoding='utf-8')

            static_graph = {
                'nodes': [
                    {'id': 'src/server.ts', 'filepath': 'src/server.ts', 'name': 'server.ts', 'dir': 'src', 'classification': 'ENTRY', 'language': 'typescript', 'size': 10, 'mtime': 1, 'depth': 0, 'island_id': -1},
                ],
                'edges': [],
                'meta': {'root': str(root), 'languages': ['typescript'], 'import_stats': {'local': 0, 'stdlib': 0, 'external': 0, 'unknown': 0}, 'unsupported_languages': []},
                'summary': {'counts': {'ENTRY': 1}, 'total': 1, 'cycle_count': 0, 'island_count': 0, 'max_depth': 0, 'health_score': 100, 'unreachable': 0},
            }
            trace = {
                'language': 'nodejs',
                'engine': 'node',
                'entry': {'mode': 'script', 'target': 'dist/server.js', 'args': []},
                'summary': {'local_edge_count': 1, 'local_edge_hits': 1, 'local_file_access_count': 0, 'local_file_access_hits': 0},
                'timed_out': False,
                'elapsed_s': 0.1,
                'exit_code': 0,
                'error': None,
                'edges': [{'source': 'dist/server.js', 'target': 'dist/dynamic.js', 'line': 6, 'language': 'javascript', 'runtime_hits': 1, 'runtime_modules': ['./dynamic.js'], 'runtime_lines': [6]}],
                'file_accesses': [],
            }

            merged = merge_runtime_traces(static_graph, [(trace, root / 'runtime_trace.json', False)])
            node_ids = {node['id'] for node in merged['nodes']}
            self.assertIn('src/dynamic.ts', node_ids)
            injected = next(node for node in merged['nodes'] if node['id'] == 'src/dynamic.ts')
            self.assertTrue(injected.get('runtime_only'))
            dynamic_pairs = {(edge['source'], edge['target']) for edge in merged['dynamic_edges']}
            self.assertIn(('src/server.ts', 'src/dynamic.ts'), dynamic_pairs)

    def test_analyzer_run_merges_multiple_runtime_inputs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app').mkdir()
            (root / 'src').mkdir()
            (root / 'app' / 'main.py').write_text('print("ok")\n', encoding='utf-8')
            (root / 'app' / 'plugin.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'src' / 'index.js').write_text('console.log("ok")\n', encoding='utf-8')
            (root / 'src' / 'runtime.js').write_text('export const value = 1;\n', encoding='utf-8')

            python_trace = {
                'language': 'python',
                'engine': 'python',
                'entry': {'mode': 'script', 'target': 'app/main.py', 'args': []},
                'summary': {'local_edge_count': 1, 'local_edge_hits': 1, 'local_file_access_count': 0, 'local_file_access_hits': 0},
                'timed_out': False,
                'elapsed_s': 0.1,
                'exit_code': 0,
                'error': None,
                'file_accesses': [],
                'edges': [{'source': 'app/main.py', 'target': 'app/plugin.py', 'line': 2, 'language': 'python', 'runtime_hits': 1, 'runtime_modules': ['app.plugin'], 'runtime_lines': [2]}],
            }
            node_trace = {
                'language': 'nodejs',
                'engine': 'node',
                'entry': {'mode': 'module', 'target': 'src/index.js', 'args': []},
                'summary': {'local_edge_count': 1, 'local_edge_hits': 1, 'local_file_access_count': 0, 'local_file_access_hits': 0},
                'timed_out': False,
                'elapsed_s': 0.2,
                'exit_code': 0,
                'error': None,
                'file_accesses': [],
                'edges': [{'source': 'src/index.js', 'target': 'src/runtime.js', 'line': 1, 'language': 'javascript', 'runtime_hits': 1, 'runtime_modules': ['./runtime.js'], 'runtime_lines': [1]}],
            }
            py_path = root / 'py_runtime.json'
            node_path = root / 'node_runtime.json'
            py_path.write_text(json.dumps(python_trace, indent=2), encoding='utf-8')
            node_path.write_text(json.dumps(node_trace, indent=2), encoding='utf-8')

            graph = analyzer.run(
                root,
                verbose=False,
                runtime_overlays=[
                    (python_trace, py_path, False),
                    (node_trace, node_path, False),
                ],
            )

            self.assertEqual(graph['meta']['runtime']['session_count'], 2)
            self.assertEqual(set(graph['meta']['runtime']['languages']), {'python', 'nodejs'})
            dynamic_pairs = {(edge['source'], edge['target']) for edge in graph['dynamic_edges']}
            self.assertIn(('app/main.py', 'app/plugin.py'), dynamic_pairs)
            self.assertIn(('src/index.js', 'src/runtime.js'), dynamic_pairs)


if __name__ == '__main__':
    unittest.main()
