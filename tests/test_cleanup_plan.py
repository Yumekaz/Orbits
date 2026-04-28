import unittest

from cleanup_plan import build_cleanup_plan, format_cleanup_plan_markdown


class CleanupPlanTests(unittest.TestCase):
    def test_classifies_safe_risky_and_manual_candidates(self):
        graph = {
            'waste': [
                {
                    'id': 'old.py',
                    'classification': 'ORPHAN',
                    'size': 12,
                    'confidence_score': 88,
                    'confidence_level': 'high',
                    'confidence_reasons': ['structural orphan'],
                    'runtime_context': {'available': True, 'touched': False, 'stale': False},
                    'git': {'available': True, 'tracked': True, 'age_days': 400, 'churn_count': 2},
                },
                {
                    'id': 'maybe.py',
                    'classification': 'ISLAND',
                    'size': 44,
                    'dead_confidence': {'score': 68, 'level': 'medium', 'reasons': ['unreachable island']},
                    'runtime_context': {'available': True, 'touched': False, 'stale': True},
                    'git': {'available': True, 'tracked': True, 'age_days': 120, 'churn_count': 20},
                },
                {
                    'id': 'runtime.py',
                    'classification': 'ORPHAN',
                    'size': 5,
                    'confidence_score': 91,
                    'confidence_level': 'high',
                    'runtime_context': {'available': True, 'touched': True, 'stale': False},
                    'git': {'available': True, 'tracked': True, 'age_days': 300, 'churn_count': 1},
                },
                {
                    'id': 'fresh.py',
                    'classification': 'ORPHAN',
                    'size': 3,
                    'confidence_score': 49,
                    'confidence_level': 'low',
                    'runtime_context': {'available': False, 'touched': False, 'stale': False},
                    'git': {'available': False, 'reason': 'Not a git repository'},
                },
            ],
        }

        plan = build_cleanup_plan(graph)

        self.assertEqual(plan['summary']['total_candidates'], 4)
        self.assertEqual([item['id'] for item in plan['safe']], ['old.py'])
        self.assertEqual([item['id'] for item in plan['risky']], ['maybe.py'])
        self.assertEqual({item['id'] for item in plan['manual']}, {'runtime.py', 'fresh.py'})
        runtime_item = next(item for item in plan['manual'] if item['id'] == 'runtime.py')
        self.assertIn('observed in runtime trace', runtime_item['blockers'])

    def test_cleanup_plan_markdown_includes_all_buckets(self):
        plan = build_cleanup_plan({
            'waste': [
                {
                    'id': 'old.py',
                    'classification': 'ORPHAN',
                    'confidence_score': 82,
                    'confidence_level': 'high',
                    'confidence_reasons': ['structural orphan'],
                    'runtime_context': {'available': True, 'touched': False, 'stale': False},
                    'git': {'available': True, 'tracked': True, 'age_days': 365, 'churn_count': 1},
                },
            ],
        })

        markdown = format_cleanup_plan_markdown(plan)

        self.assertIn('# Orbits Cleanup Plan', markdown)
        self.assertIn('## Safe delete candidates', markdown)
        self.assertIn('`old.py`', markdown)
        self.assertIn('## Risky candidates', markdown)
        self.assertIn('## Manual review', markdown)


if __name__ == '__main__':
    unittest.main()
