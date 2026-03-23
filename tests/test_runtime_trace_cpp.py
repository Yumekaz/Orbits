import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from runtime_trace import CppRuntimeTraceConfig, merge_runtime_traces, run_cpp_runtime_trace


class CppRuntimeTraceTests(unittest.TestCase):
    def test_merge_runtime_traces_adds_cpp_runtime_only_nodes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'bin').mkdir()
            (root / 'plugins').mkdir()
            exe = root / 'bin' / 'demo.exe'
            dll = root / 'plugins' / 'filter.dll'
            exe.write_bytes(b'')
            dll.write_bytes(b'')

            static_graph = {
                'nodes': [],
                'edges': [],
                'meta': {'root': str(root), 'languages': ['cpp'], 'import_stats': {'local': 0, 'stdlib': 0, 'external': 0, 'unknown': 0}, 'unsupported_languages': []},
                'summary': {'counts': {}, 'total': 0, 'cycle_count': 0, 'island_count': 0, 'max_depth': 0, 'health_score': 100, 'unreachable': 0},
            }
            trace = {
                'language': 'cpp',
                'engine': 'ld_debug',
                'entry': {'mode': 'binary', 'target': 'bin/demo.exe', 'args': []},
                'summary': {'local_edge_count': 1, 'local_edge_hits': 1, 'local_file_access_count': 0, 'local_file_access_hits': 0},
                'timed_out': False,
                'elapsed_s': 0.1,
                'exit_code': 0,
                'error': None,
                'file_accesses': [],
                'edges': [{'source': str(exe), 'target': str(dll), 'line': -1, 'language': 'cpp', 'runtime_hits': 1, 'runtime_modules': ['plugins/filter.dll'], 'runtime_lines': []}],
            }

            merged = merge_runtime_traces(static_graph, [(trace, root / 'runtime_trace.json', False)])

            node_ids = {node['id'] for node in merged['nodes']}
            self.assertIn('bin/demo.exe', node_ids)
            self.assertIn('plugins/filter.dll', node_ids)
            dynamic_pairs = {(edge['source'], edge['target']) for edge in merged['dynamic_edges']}
            self.assertIn(('bin/demo.exe', 'plugins/filter.dll'), dynamic_pairs)
            self.assertEqual(merged['meta']['runtime']['language'], 'cpp')

    @unittest.skipUnless(os.name == 'nt', 'Windows-specific unsupported-path regression')
    def test_cpp_runtime_trace_is_explicitly_unsupported_on_windows(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / 'demo.exe'
            exe.write_bytes(b'')
            with self.assertRaises(RuntimeError) as ctx:
                run_cpp_runtime_trace(root, CppRuntimeTraceConfig(target='demo.exe', output_path=root / 'runtime_trace.json', timeout_s=1), verbose=False)
            self.assertIn('Windows', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
