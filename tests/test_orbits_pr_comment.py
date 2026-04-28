import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'orbits_pr_comment.py'
SPEC = importlib.util.spec_from_file_location('orbits_pr_comment', SCRIPT_PATH)
orbits_pr_comment = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(orbits_pr_comment)


class OrbitsPrCommentTests(unittest.TestCase):
    def test_comment_body_summarizes_status_metrics_diff_and_dead_files(self):
        graph = {
            'meta': {'total_files': 4, 'total_edges': 3},
            'summary': {'health_score': 82, 'cycle_count': 1},
            'waste': [
                {
                    'id': 'orphan.py',
                    'classification': 'ORPHAN',
                    'size': 12,
                    'island_id': -1,
                    'confidence_score': 84,
                    'confidence_level': 'high',
                    'confidence_reasons': [
                        'structural orphan with no static in/out edges',
                        'last touched 410 days ago',
                        'not observed in fresh runtime trace',
                    ],
                },
                {'id': 'legacy/a.py', 'classification': 'ISLAND', 'size': 20, 'island_id': 0},
                {'id': 'legacy/b.py', 'classification': 'ISLAND', 'size': 21, 'island_id': 0},
            ],
        }
        diff = {
            'nodes': {'before': 3, 'after': 4, 'delta': 1, 'added': ['new.py'], 'removed': []},
            'edges': {
                'before': 2,
                'after': 3,
                'delta': 1,
                'added': [{'source': 'app.py', 'target': 'new.py'}],
                'removed': [],
            },
            'dynamic_edges': {
                'before': 0,
                'after': 1,
                'delta': 1,
                'added': [{'source': 'app.py', 'target': 'new.py'}],
                'removed': [],
            },
            'waste': {'before': 2, 'after': 3, 'delta': 1, 'added': ['orphan.py'], 'removed': []},
            'classification_changes': {'changed': [{'id': 'legacy/a.py', 'before': 'LEAF', 'after': 'ISLAND'}]},
            'confidence_changes': {'changed': [{'id': 'orphan.py', 'before_score': 50, 'after_score': 84, 'delta': 34}]},
            'architecture': {
                'impact': {'level': 'high', 'signals': ['new_dead_code', 'coupling_increased']},
                'coupling': {'delta': {'static_edges': 1}},
                'cycles': {'delta': 1},
                'health': {'delta': -8},
            },
        }

        body = orbits_pr_comment.build_comment_body(
            graph,
            check_exit_code=2,
            check_text='Check: FAIL\n- orphans 1 > 0\n',
            diff=diff,
            dead_report_path='orbits-artifacts/dead-files.md',
            artifact_name='orbits-report',
            limit=5,
        )

        self.assertTrue(body.startswith(orbits_pr_comment.COMMENT_MARKER))
        self.assertIn('**Status:** FAIL', body)
        self.assertIn('**Runtime: no trace merged**', body)
        self.assertIn('| 4 | 3 | 82/100 | 3 | 1 | 1 | 1 |', body)
        self.assertIn('`orphan.py`', body)
        self.assertIn('No static imports in or out', body)
        self.assertIn('High (84/100)', body)
        self.assertIn('structural orphan with no static in/out edges; last touched 410 days ago', body)
        self.assertIn('New probable dead files introduced by this PR:', body)
        self.assertIn('Runtime edges: 0 -> 1 (+1).', body)
        self.assertIn('Architecture impact: **HIGH**.', body)
        self.assertIn('Signals: new_dead_code, coupling_increased.', body)
        self.assertIn('| Nodes | 3 | 4 | +1 |', body)
        self.assertIn('| Static coupling edges | +1 |', body)
        self.assertIn('| Runtime edges | 0 | 1 | +1 |', body)
        self.assertIn('`+ app.py -> new.py`', body)
        self.assertIn('**Classification changes**', body)
        self.assertIn('`legacy/a.py`: LEAF -> ISLAND', body)
        self.assertIn('`orphan.py`: 50 -> 84 (+34)', body)
        self.assertIn('orphans 1 > 0', body)

    def test_comment_body_calls_out_no_new_dead_files_when_diff_is_clean(self):
        graph = {
            'meta': {'total_files': 2, 'total_edges': 1},
            'summary': {'health_score': 100, 'cycle_count': 0},
            'waste': [],
        }
        diff = {
            'nodes': {'before': 2, 'after': 2, 'delta': 0, 'added': [], 'removed': []},
            'edges': {'before': 1, 'after': 1, 'delta': 0, 'added': [], 'removed': []},
            'dynamic_edges': {'before': 0, 'after': 0, 'delta': 0, 'added': [], 'removed': []},
            'waste': {'before': 0, 'after': 0, 'delta': 0, 'added': [], 'removed': []},
        }

        body = orbits_pr_comment.build_comment_body(
            graph,
            check_exit_code=0,
            diff=diff,
        )

        self.assertIn('Graph size: 2 -> 2 files (0).', body)
        self.assertIn('No new probable dead files compared with the base branch.', body)
        self.assertIn('No node, edge, or dead-file changes detected.', body)

    def test_cli_writes_comment_file_from_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path = root / 'graph.json'
            exit_path = root / 'check.exit'
            output_path = root / 'comment.md'
            graph_path.write_text(
                json.dumps({
                    'meta': {'total_files': 1, 'total_edges': 0},
                    'summary': {'health_score': 100, 'cycle_count': 0},
                    'waste': [],
                }),
                encoding='utf-8',
            )
            exit_path.write_text('0', encoding='utf-8')

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    '--graph',
                    str(graph_path),
                    '--check-exit-code-file',
                    str(exit_path),
                    '--output',
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            body = output_path.read_text(encoding='utf-8')
            self.assertIn('**Status:** PASS', body)
            self.assertIn('No actionable dead files found.', body)


if __name__ == '__main__':
    unittest.main()
