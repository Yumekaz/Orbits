import os
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from runtime_trace import (
    CppRuntimeTraceConfig,
    _parse_cpp_loader_edges,
    _parse_linux_loader_edges,
    _parse_windows_loader_edges,
    _parse_windows_pe_imports,
    merge_runtime_traces,
    run_cpp_runtime_trace,
)


def _minimal_pe(import_name: str | None = None) -> bytes:
    data = bytearray(0x600)
    data[:2] = b'MZ'
    pe_offset = 0x80
    struct.pack_into('<I', data, 0x3C, pe_offset)
    data[pe_offset:pe_offset + 4] = b'PE\0\0'

    coff_offset = pe_offset + 4
    optional_size = 0xF0
    struct.pack_into('<HHIIIHH', data, coff_offset, 0x8664, 1, 0, 0, 0, optional_size, 0x2022)

    optional_offset = coff_offset + 20
    struct.pack_into('<H', data, optional_offset, 0x20B)
    data_directory_offset = optional_offset + 112
    import_rva = 0x2000 if import_name else 0
    struct.pack_into('<II', data, data_directory_offset + 8, import_rva, 40 if import_name else 0)

    section_offset = optional_offset + optional_size
    data[section_offset:section_offset + 8] = b'.rdata\0\0'
    struct.pack_into('<IIIIIIHHI', data, section_offset + 8, 0x1000, 0x2000, 0x400, 0x200, 0, 0, 0, 0, 0x40000040)

    if import_name:
        descriptor_offset = 0x200
        name_rva = 0x2060
        struct.pack_into('<IIIII', data, descriptor_offset, 0, 0, 0, name_rva, 0)
        name_offset = 0x260
        encoded = import_name.encode('ascii') + b'\0'
        data[name_offset:name_offset + len(encoded)] = encoded
    return bytes(data)


class CppRuntimeTraceTests(unittest.TestCase):
    def test_linux_loader_parser_captures_symbol_bindings(self):
        root = Path('/repo')
        stderr = """
            3400: calling init: /repo/plugins/libfilter.so
            3400: binding file /repo/bin/demo [0] to /repo/plugins/libfilter.so [0]: normal symbol `plugin_init'
            3400: binding file /repo/plugins/libfilter.so [0] to /repo/plugins/libmath.so [0]: normal symbol `transform'
            3400: binding file /usr/lib/libc.so.6 [0] to /repo/plugins/libfilter.so [0]: normal symbol `puts'
        """

        edges = _parse_linux_loader_edges(stderr, root, 'bin/demo')
        edge_map = {(edge['source'], edge['target']): edge for edge in edges}

        self.assertIn(('bin/demo', 'plugins/libfilter.so'), edge_map)
        self.assertIn(('plugins/libfilter.so', 'plugins/libmath.so'), edge_map)
        self.assertEqual(edge_map[('bin/demo', 'plugins/libfilter.so')]['type'], 'runtime_bind')
        self.assertIn('plugin_init', edge_map[('bin/demo', 'plugins/libfilter.so')]['runtime_symbols'])
        self.assertIn('transform', edge_map[('plugins/libfilter.so', 'plugins/libmath.so')]['runtime_symbols'])

    def test_linux_engine_name_uses_linux_loader_parser(self):
        root = Path('/repo')
        stderr = """
            3400: binding file /repo/bin/demo [0] to /repo/plugins/libfilter.so [0]: normal symbol `plugin_init'
        """

        edges, error = _parse_cpp_loader_edges('ld_debug_bindings', stderr, root, root / 'bin' / 'demo', 'bin/demo')

        self.assertIsNone(error)
        edge_map = {(edge['source'], edge['target']): edge for edge in edges}
        self.assertIn(('bin/demo', 'plugins/libfilter.so'), edge_map)
        self.assertIn('plugin_init', edge_map[('bin/demo', 'plugins/libfilter.so')]['runtime_symbols'])

    def test_windows_pe_import_parser_and_local_loader_edges(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'bin').mkdir()
            (root / 'plugins').mkdir()
            exe = root / 'bin' / 'demo.exe'
            dll = root / 'plugins' / 'filter.dll'
            exe.write_bytes(_minimal_pe('filter.dll'))
            dll.write_bytes(_minimal_pe())

            self.assertEqual(_parse_windows_pe_imports(exe), ['filter.dll'])
            edges, error = _parse_windows_loader_edges(root, exe, 'bin/demo.exe')

            self.assertIsNone(error)
            edge_pairs = {(edge['source'], edge['target']) for edge in edges}
            self.assertIn(('bin/demo.exe', 'plugins/filter.dll'), edge_pairs)

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
                'edges': [{'source': str(exe), 'target': str(dll), 'line': -1, 'language': 'cpp', 'runtime_hits': 1, 'runtime_modules': ['plugins/filter.dll'], 'runtime_lines': [], 'runtime_symbols': ['plugin_init'], 'runtime_symbol_hits': 1}],
            }

            merged = merge_runtime_traces(static_graph, [(trace, root / 'runtime_trace.json', False)])

            node_ids = {node['id'] for node in merged['nodes']}
            self.assertIn('bin/demo.exe', node_ids)
            self.assertIn('plugins/filter.dll', node_ids)
            dynamic_pairs = {(edge['source'], edge['target']) for edge in merged['dynamic_edges']}
            self.assertIn(('bin/demo.exe', 'plugins/filter.dll'), dynamic_pairs)
            self.assertEqual(merged['meta']['runtime']['language'], 'cpp')
            self.assertEqual(merged['meta']['runtime']['symbol_binding_count'], 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows-specific PE import-table regression')
    def test_cpp_runtime_trace_on_windows_writes_scoped_import_overlay(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'plugins').mkdir()
            exe = root / 'demo.exe'
            dll = root / 'plugins' / 'filter.dll'
            exe.write_bytes(_minimal_pe('filter.dll'))
            dll.write_bytes(_minimal_pe())

            trace = run_cpp_runtime_trace(
                root,
                CppRuntimeTraceConfig(target='demo.exe', output_path=root / 'runtime_trace.json', timeout_s=1),
                verbose=False,
            )

            self.assertEqual(trace['engine'], 'pe_import_table')
            edge_pairs = {(edge['source'], edge['target']) for edge in trace['edges']}
            self.assertIn(('demo.exe', 'plugins/filter.dll'), edge_pairs)


if __name__ == '__main__':
    unittest.main()
