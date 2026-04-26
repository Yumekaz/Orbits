import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import analyzer


class Phase7ProductizationTests(unittest.TestCase):
    def test_config_normalizes_check_and_resolver_override_placeholders(self):
        config = analyzer.normalize_project_config({
            'check': {'maxOrphans': 2, 'minHealth': 90},
            'resolver_overrides': {'python': {'src_dirs': ['src']}},
        })

        self.assertEqual(config['check'], {'max_orphans': 2, 'min_health': 90})
        self.assertEqual(config['resolver_overrides']['python']['src_dirs'], ['src'])

    def test_config_ignore_and_intentional_files_shape_waste(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'legacy').mkdir()
            (root / 'app.py').write_text('import used\n', encoding='utf-8')
            (root / 'used.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'keep.py').write_text('VALUE = 2\n', encoding='utf-8')
            (root / 'legacy' / 'dead.py').write_text('VALUE = 3\n', encoding='utf-8')
            (root / '.orbits.json').write_text(
                json.dumps({
                    'ignore': {'dirs': ['legacy/**']},
                    'intentional_files': ['keep.py'],
                }),
                encoding='utf-8',
            )

            graph = analyzer.run(root, verbose=False)
            node_ids = {node['id'].replace('\\', '/') for node in graph['nodes']}
            waste_ids = {item['id'].replace('\\', '/') for item in graph['waste']}

            self.assertNotIn('legacy/dead.py', node_ids)
            self.assertIn('keep.py', graph['meta']['intentional_files'])
            self.assertNotIn('keep.py', waste_ids)

    def test_dead_file_reports_are_written_by_explicit_cli_flags(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path = root / 'graph.json'
            md_path = root / 'dead.md'
            csv_path = root / 'dead.csv'
            (root / 'orphan.py').write_text('VALUE = 1\n', encoding='utf-8')

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(analyzer.__file__).resolve()),
                    str(root),
                    '-o',
                    str(graph_path),
                    '--dead-report-md',
                    str(md_path),
                    '--dead-report-csv',
                    str(csv_path),
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('orphan.py', md_path.read_text(encoding='utf-8'))
            self.assertIn('id,name,classification,size,island_id', csv_path.read_text(encoding='utf-8').splitlines()[0])
            self.assertIn('orphan.py', csv_path.read_text(encoding='utf-8'))

    def test_check_mode_exits_nonzero_when_threshold_is_exceeded(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'orphan.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / '.orbits.json').write_text(json.dumps({'check': {'max_orphans': 0}}), encoding='utf-8')

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(analyzer.__file__).resolve()),
                    str(root),
                    '-o',
                    str(root / 'graph.json'),
                    '--check',
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn('orphans 1 > 0', proc.stderr)

    def test_flag_thresholds_override_config_thresholds(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'orphan.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / '.orbits.json').write_text(json.dumps({'check': {'max_orphans': 99}}), encoding='utf-8')

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(analyzer.__file__).resolve()),
                    str(root),
                    '-o',
                    str(root / 'graph.json'),
                    '--check',
                    '--max-orphans',
                    '0',
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn('orphans 1 > 0', proc.stderr)


if __name__ == '__main__':
    unittest.main()
