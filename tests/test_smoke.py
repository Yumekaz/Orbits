import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import analyzer


class EndToEndSmokeTests(unittest.TestCase):
    def test_run_returns_stable_graph_shape(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'pkg').mkdir()
            (root / 'pkg' / '__init__.py').write_text('', encoding='utf-8')
            (root / 'pkg' / 'util.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'main.py').write_text('from pkg import util\n', encoding='utf-8')
            (root / 'orphan.py').write_text('VALUE = 2\n', encoding='utf-8')

            graph = analyzer.run(root, verbose=False)
            edges = {
                (edge['source'].replace('\\', '/'), edge['target'].replace('\\', '/'))
                for edge in graph['edges']
            }

            self.assertIn('nodes', graph)
            self.assertIn('edges', graph)
            self.assertIn('summary', graph)
            self.assertIn('meta', graph)
            self.assertEqual(graph['meta']['total_files'], 4)
            self.assertGreaterEqual(graph['meta']['total_edges'], 1)
            self.assertIn(('main.py', 'pkg/util.py'), edges)
            self.assertGreaterEqual(graph['summary']['health_score'], 0)
            self.assertTrue(any(node['id'] == 'orphan.py' and node['classification'] == 'ORPHAN' for node in graph['nodes']))


if __name__ == '__main__':
    unittest.main()
