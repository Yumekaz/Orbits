import csv
import os
import shutil
import subprocess
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import analyzer
from git_intel import COMMIT_MARKER, enrich_dead_code_confidence, parse_git_log_numstat


def _git(root: Path, args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc


def _commit(root: Path, message: str, date: str) -> None:
    env = os.environ.copy()
    env.update({
        'GIT_AUTHOR_DATE': date,
        'GIT_COMMITTER_DATE': date,
    })
    _git(root, ['add', '.'], env=env)
    _git(root, ['commit', '-m', message], env=env)


class GitIntelTests(unittest.TestCase):
    def test_parse_git_log_numstat_extracts_age_churn_and_authors(self):
        payload = '\n'.join([
            f'{COMMIT_MARKER}\tdef456\t1706745600\tAlice',
            '2\t1\told.py',
            f'{COMMIT_MARKER}\tabc123\t1704067200\tAlice',
            '1\t0\told.py',
            '',
        ])
        reference = datetime(2026, 4, 26, tzinfo=UTC)

        context = parse_git_log_numstat(payload, reference_time=reference)

        self.assertTrue(context['available'])
        self.assertTrue(context['tracked'])
        self.assertEqual(context['commit_count'], 2)
        self.assertEqual(context['churn_count'], 4)
        self.assertEqual(context['last_touched_iso'], '2024-02-01T00:00:00Z')
        self.assertEqual(context['age_days'], (reference - datetime(2024, 2, 1, tzinfo=UTC)).days)
        self.assertEqual(context['top_authors'], [{'name': 'Alice', 'commits': 2}])

    @unittest.skipUnless(shutil.which('git'), 'git CLI not available')
    def test_analyzer_enriches_waste_with_git_history(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git(root, ['init'])
            _git(root, ['config', 'user.name', 'Test User'])
            _git(root, ['config', 'user.email', 'test@example.com'])
            (root / 'old.py').write_text('VALUE = 1\n', encoding='utf-8')
            _commit(root, 'add old orphan', '2024-01-01T00:00:00+0000')
            (root / 'old.py').write_text('VALUE = 2\nEXTRA = 3\n', encoding='utf-8')
            _commit(root, 'touch old orphan', '2024-02-01T00:00:00+0000')

            graph = analyzer.run(root, verbose=False)
            item = next(entry for entry in graph['waste'] if entry['id'] == 'old.py')

            self.assertTrue(item['git']['available'])
            self.assertTrue(item['git']['tracked'])
            self.assertEqual(item['git']['commit_count'], 2)
            self.assertGreaterEqual(item['git']['churn_count'], 3)
            self.assertEqual(item['git']['top_authors'][0]['name'], 'Test User')
            self.assertEqual(item['confidence_level'], 'high')
            self.assertIn('structural orphan', '; '.join(item['confidence_reasons']))
            self.assertTrue(graph['meta']['git']['available'])

    def test_runtime_touch_lowers_dead_code_confidence_without_git(self):
        with TemporaryDirectory() as tmp:
            graph = {
                'waste': [
                    {'id': 'dead.py', 'name': 'dead.py', 'classification': 'ORPHAN', 'size': 1, 'island_id': -1},
                    {'id': 'runtime.py', 'name': 'runtime.py', 'classification': 'ORPHAN', 'size': 1, 'island_id': -1},
                ],
                'dynamic_edges': [{'source': 'main.py', 'target': 'runtime.py', 'runtime_hits': 1}],
                'runtime': {'entry': {'target': 'main.py'}, 'file_accesses': [], 'stale': False},
                'meta': {'root': tmp, 'runtime': {'enabled': True, 'stale': False, 'entrypoint': 'main.py'}},
            }

            enrich_dead_code_confidence(graph, Path(tmp))
            by_id = {item['id']: item for item in graph['waste']}

            self.assertFalse(by_id['dead.py']['git']['available'])
            self.assertFalse(by_id['dead.py']['runtime_context']['touched'])
            self.assertTrue(by_id['runtime.py']['runtime_context']['touched'])
            self.assertGreater(by_id['dead.py']['confidence_score'], by_id['runtime.py']['confidence_score'])
            self.assertIn('observed in fresh runtime trace', '; '.join(by_id['runtime.py']['confidence_reasons']))

    def test_dead_reports_include_confidence_git_and_runtime_fields(self):
        with TemporaryDirectory() as tmp:
            graph = {
                'summary': {'health_score': 80},
                'meta': {'root': tmp},
                'waste': [
                    {
                        'id': 'dead.py',
                        'name': 'dead.py',
                        'classification': 'ORPHAN',
                        'size': 12,
                        'island_id': -1,
                        'git': {'available': False, 'reason': 'Not a git repository'},
                        'runtime_context': {'available': False, 'touched': False, 'stale': False},
                        'dead_confidence': {'score': 72, 'level': 'medium', 'reasons': ['structural orphan']},
                    },
                ],
            }
            csv_path = Path(tmp) / 'dead.csv'

            markdown = analyzer.format_dead_report_markdown(graph)
            analyzer.write_dead_report_csv(graph, csv_path)
            with csv_path.open(encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))

            self.assertIn('Confidence', markdown)
            self.assertIn('Git age', markdown)
            self.assertIn('Runtime', markdown)
            self.assertEqual(rows[0]['confidence_score'], '72')
            self.assertEqual(rows[0]['git_available'], 'False')
            self.assertEqual(rows[0]['runtime_touched'], 'False')


if __name__ == '__main__':
    unittest.main()
