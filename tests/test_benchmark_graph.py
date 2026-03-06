import unittest

import benchmark_graph


class BenchmarkGraphTests(unittest.TestCase):
    def test_generate_graph_returns_large_stable_fixture(self):
        graph = benchmark_graph.generate_graph(node_count=1200, seed=11)
        self.assertEqual(len(graph['nodes']), 1200)
        self.assertGreater(len(graph['edges']), 1500)
        self.assertGreaterEqual(graph['summary']['cycle_count'], 1)
        self.assertIn('meta', graph)
        self.assertIn('import_stats', graph['meta'])
        self.assertEqual(graph['summary']['total'], 1200)
        self.assertTrue(any(node['classification'] == 'ENTRY' for node in graph['nodes']))
        self.assertTrue(any(node['classification'] == 'ORPHAN' for node in graph['nodes']))


if __name__ == '__main__':
    unittest.main()
