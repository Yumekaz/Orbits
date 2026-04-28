import unittest

from scale_proof import build_scale_proof, format_scale_proof_markdown


class ScaleProofTests(unittest.TestCase):
    def test_builds_scale_proof_from_graph_shape_and_metadata(self):
        graph = {
            'nodes': [
                {'id': 'app.py', 'language': 'python', 'size': 512},
                {'id': 'lib.py', 'language': 'python', 'size': 4096},
                {'id': 'ui/app.ts', 'language': 'typescript', 'size': 50_000},
                {'id': 'bundle.js', 'language': 'javascript', 'size': 2_000_000},
            ],
            'edges': [
                {'source': 'app.py', 'target': 'lib.py'},
                {'source': 'ui/app.ts', 'target': 'bundle.js'},
            ],
            'dynamic_edges': [{'source': 'app.py', 'target': 'lib.py'}],
            'summary': {'total': 4},
            'meta': {
                'elapsed_s': 1.25,
                'runtime': {'elapsed_s': 0.3, 'session_count': 2},
            },
        }

        proof = build_scale_proof(graph)

        self.assertEqual(proof['files'], 4)
        self.assertEqual(proof['edges'], {'static': 2, 'runtime': 1, 'total': 3})
        self.assertEqual(proof['languages'], {'javascript': 1, 'python': 2, 'typescript': 1})
        self.assertEqual(proof['scan_time']['elapsed_s'], 1.25)
        self.assertEqual(proof['scan_time']['runtime_elapsed_s'], 0.3)
        self.assertEqual(proof['size_buckets']['tiny'], 1)
        self.assertEqual(proof['size_buckets']['small'], 1)
        self.assertEqual(proof['size_buckets']['medium'], 1)
        self.assertEqual(proof['size_buckets']['huge'], 1)
        self.assertEqual(proof['largest_files'][0]['id'], 'bundle.js')

    def test_scale_proof_markdown_is_readme_ready(self):
        proof = build_scale_proof({
            'nodes': [{'id': 'app.py', 'language': 'python', 'size': 100}],
            'edges': [],
            'meta': {'elapsed_s': 0.01},
        })

        markdown = format_scale_proof_markdown(proof)

        self.assertIn('# Orbits Scale Proof', markdown)
        self.assertIn('| Language | Files |', markdown)
        self.assertIn('| python | 1 |', markdown)
        self.assertIn('## File size buckets', markdown)
        self.assertIn('`app.py`', markdown)


if __name__ == '__main__':
    unittest.main()
