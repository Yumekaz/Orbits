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
            {'id': 'shared.py', 'classification': 'ORPHAN'},
        ],
        'edges': [
            {'source': 'app.py', 'target': 'lib.py', 'line': 3},
        ],
        'dynamic_edges': [],
        'waste': [
            {'id': 'dead.py', 'classification': 'ORPHAN', 'confidence_score': 81, 'confidence_level': 'high'},
            {'id': 'shared.py', 'classification': 'ORPHAN', 'confidence_score': 55, 'confidence_level': 'medium'},
        ],
        'cycles': [],
        'summary': {'cycle_count': 0, 'health_score': 92},
        'meta': {'runtime': {'enabled': False, 'runtime_edges': 0}},
    }


def _current_graph():
    return {
        'nodes': [
            {'id': 'app.py', 'classification': 'ENTRY'},
            {'id': 'lib.py', 'classification': 'INTERNAL'},
            {'id': 'new.py', 'classification': 'ORPHAN'},
            {'id': 'shared.py', 'classification': 'ORPHAN'},
        ],
        'edges': [
            {'source': 'app.py', 'target': 'lib.py', 'line': 99},
            {'source': 'app.py', 'target': 'new.py', 'line': 8},
        ],
        'dynamic_edges': [
            {'source': 'app.py', 'target': 'new.py', 'runtime_hits': 1},
        ],
        'waste': [
            {'id': 'new.py', 'classification': 'ORPHAN', 'confidence_score': 79, 'confidence_level': 'high'},
            {'id': 'shared.py', 'classification': 'ORPHAN', 'confidence_score': 83, 'confidence_level': 'high'},
        ],
        'cycles': [['app.py', 'new.py', 'app.py']],
        'summary': {'cycle_count': 1, 'health_score': 76},
        'meta': {'runtime': {'enabled': True, 'runtime_edges': 1, 'stale': False}},
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
        self.assertEqual(diff['dynamic_edges']['added'], [{'source': 'app.py', 'target': 'new.py'}])
        self.assertEqual(diff['classification_changes']['changed'], [{'id': 'lib.py', 'before': 'LEAF', 'after': 'INTERNAL'}])
        self.assertEqual(diff['confidence_changes']['changed'][0]['id'], 'shared.py')
        self.assertEqual(diff['confidence_changes']['changed'][0]['delta'], 28)
        self.assertFalse(diff['runtime']['before']['enabled'])
        self.assertTrue(diff['runtime']['after']['enabled'])
        self.assertIn('architecture', diff)
        self.assertEqual(diff['architecture']['impact']['level'], 'high')
        self.assertEqual(
            diff['architecture']['impact']['signals'],
            [
                'coupling_increased',
                'new_cycles',
                'new_dead_code',
                'runtime_edges_changed',
                'classification_changed',
                'confidence_increased',
            ],
        )
        self.assertEqual(diff['architecture']['coupling']['delta']['static_edges'], 1)
        self.assertEqual(diff['architecture']['coupling']['added_dependencies'], 1)
        self.assertEqual(diff['architecture']['coupling']['affected_nodes'], ['app.py', 'new.py'])
        self.assertEqual(diff['architecture']['cycles']['before'], 0)
        self.assertEqual(diff['architecture']['cycles']['after'], 1)
        self.assertEqual(diff['architecture']['cycles']['added'], [['app.py', 'new.py', 'app.py']])
        self.assertEqual(diff['architecture']['dead_code']['added'], ['new.py'])
        self.assertEqual(diff['architecture']['runtime']['delta']['dynamic_edges'], 1)
        self.assertEqual(diff['architecture']['classification']['changed'], 1)
        self.assertEqual(diff['architecture']['confidence']['increased'], 1)
        self.assertEqual(diff['architecture']['health']['delta'], -16)

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
        self.assertIn('Nodes: 4 -> 4 (0)', text)
        self.assertIn('+ app.py -> new.py', text)
        self.assertIn('Dynamic edges: 0 -> 1 (+1)', text)
        self.assertIn('Classification changes: 1', text)
        self.assertIn('~ lib.py: LEAF -> INTERNAL', text)
        self.assertIn('Confidence changes: 1', text)
        self.assertIn('~ shared.py: 55 -> 83 (+28)', text)
        self.assertIn('Waste: 2 -> 2 (0)', text)
        self.assertIn('Architecture impact: HIGH', text)
        self.assertIn('Signals: coupling_increased, new_cycles, new_dead_code', text)

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
        self.assertEqual(payload['dynamic_edges']['added'], [{'source': 'app.py', 'target': 'new.py'}])
        self.assertEqual(payload['architecture']['impact']['level'], 'high')
        self.assertEqual(payload['architecture']['cycles']['delta'], 1)


if __name__ == '__main__':
    unittest.main()
