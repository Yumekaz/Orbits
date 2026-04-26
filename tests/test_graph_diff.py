import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import graph_diff


def _base_graph():
    return {
        'nodes': [
            {'id': 'app.py', 'classification': 'ENTRY'},
            {'id': 'lib.py', 'classification': 'LEAF'},
            {'id': 'dead.py', 'classification': 'ORPHAN'},
        ],
        'edges': [
            {'source': 'app.py', 'target': 'lib.py', 'line': 3},
        ],
        'waste': [
            {'id': 'dead.py', 'classification': 'ORPHAN'},
        ],
    }


def _current_graph():
    return {
        'nodes': [
            {'id': 'app.py', 'classification': 'ENTRY'},
            {'id': 'lib.py', 'classification': 'LEAF'},
            {'id': 'new.py', 'classification': 'ORPHAN'},
        ],
        'edges': [
            {'source': 'app.py', 'target': 'lib.py', 'line': 99},
            {'source': 'app.py', 'target': 'new.py', 'line': 8},
        ],
        'waste': [
            {'id': 'new.py', 'classification': 'ORPHAN'},
        ],
    }


class GraphDiffTests(unittest.TestCase):
    def test_diff_reports_nodes_edges_and_waste_without_line_churn(self):
        diff = graph_diff.diff_graphs(_base_graph(), _current_graph())

        self.assertEqual(diff['nodes']['added'], ['new.py'])
        self.assertEqual(diff['nodes']['removed'], ['dead.py'])
        self.assertEqual(diff['edges']['added'], [{'source': 'app.py', 'target': 'new.py'}])
        self.assertEqual(diff['edges']['removed'], [])
        self.assertEqual(diff['edges']['delta'], 1)
        self.assertEqual(diff['waste']['added'], ['new.py'])
        self.assertEqual(diff['waste']['removed'], ['dead.py'])
        self.assertEqual(diff['waste']['delta'], 0)

    def test_waste_falls_back_to_node_classification(self):
        baseline = {'nodes': [{'id': 'old.py', 'classification': 'ORPHAN'}], 'edges': []}
        current = {'nodes': [{'id': 'old.py', 'classification': 'LEAF'}], 'edges': []}

        diff = graph_diff.diff_graphs(baseline, current)

        self.assertEqual(diff['waste']['before'], 1)
        self.assertEqual(diff['waste']['after'], 0)
        self.assertEqual(diff['waste']['removed'], ['old.py'])

    def test_format_graph_diff_outputs_human_readable_summary(self):
        diff = graph_diff.diff_graphs(_base_graph(), _current_graph(), 'old.json', 'new.json')

        text = graph_diff.format_graph_diff(diff)

        self.assertIn('Graph dependency diff', text)
        self.assertIn('Nodes: 3 -> 3 (0)', text)
        self.assertIn('+ app.py -> new.py', text)
        self.assertIn('Waste: 1 -> 1 (0)', text)

    def test_analyzer_cli_diff_json(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / 'old.json'
            current = root / 'new.json'
            baseline.write_text(json.dumps(_base_graph()), encoding='utf-8')
            current.write_text(json.dumps(_current_graph()), encoding='utf-8')

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / 'analyzer.py'),
                    '--diff',
                    str(baseline),
                    str(current),
                    '--diff-json',
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload['nodes']['added'], ['new.py'])
        self.assertEqual(payload['edges']['added'], [{'source': 'app.py', 'target': 'new.py'}])


if __name__ == '__main__':
    unittest.main()
