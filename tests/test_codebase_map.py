import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codebase_map import build_codebase_map, discover_framework_signals, discover_runtime_commands


def _sample_graph():
    return {
        'nodes': [
            {'id': 'src/main.tsx', 'name': 'main.tsx', 'language': 'typescript', 'classification': 'ENTRY', 'entrypoint': True, 'depth': 0},
            {'id': 'src/App.tsx', 'name': 'App.tsx', 'language': 'typescript', 'classification': 'CONNECTED', 'depth': 1},
            {'id': 'src/lib/api.ts', 'name': 'api.ts', 'language': 'typescript', 'classification': 'CONNECTED', 'depth': 2},
            {'id': 'server/index.js', 'name': 'index.js', 'language': 'javascript', 'classification': 'ENTRY', 'entrypoint': True, 'depth': 0},
            {'id': 'server/routes.js', 'name': 'routes.js', 'language': 'javascript', 'classification': 'LEAF', 'depth': 1},
            {'id': 'legacy/old.py', 'name': 'old.py', 'language': 'python', 'classification': 'ISLAND', 'depth': -1},
            {'id': 'legacy/helper.py', 'name': 'helper.py', 'language': 'python', 'classification': 'ISLAND', 'depth': -1},
            {'id': 'unused.py', 'name': 'unused.py', 'language': 'python', 'classification': 'ORPHAN', 'depth': -1},
        ],
        'edges': [
            {'source': 'src/main.tsx', 'target': 'src/App.tsx'},
            {'source': 'src/App.tsx', 'target': 'src/lib/api.ts'},
            {'source': 'server/index.js', 'target': 'server/routes.js'},
            {'source': 'legacy/old.py', 'target': 'legacy/helper.py'},
        ],
        'islands': [['legacy/old.py', 'legacy/helper.py']],
        'waste': [
            {'id': 'legacy/old.py', 'classification': 'ISLAND'},
            {'id': 'legacy/helper.py', 'classification': 'ISLAND'},
            {'id': 'unused.py', 'classification': 'ORPHAN'},
        ],
    }


class CodebaseMapTests(unittest.TestCase):
    def test_build_codebase_map_reports_regions_hubs_entrypoints_dead_areas_and_impact(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = build_codebase_map(root, _sample_graph(), top_n=3)

        regions = {item['id']: item for item in payload['regions']}
        self.assertEqual(regions['src']['node_count'], 3)
        self.assertEqual(regions['src']['entrypoint_count'], 1)
        self.assertEqual(regions['legacy']['dead_count'], 2)
        self.assertEqual(regions['.']['dead_count'], 1)

        hubs = [item['id'] for item in payload['core_hubs']]
        self.assertIn('src/App.tsx', hubs)
        self.assertNotIn('legacy/helper.py', hubs)

        entrypoints = payload['entrypoints']
        self.assertEqual(entrypoints['count'], 2)
        self.assertEqual(
            {item['id']: item['reaches'] for item in entrypoints['items']},
            {'server/index.js': 1, 'src/main.tsx': 2},
        )

        isolated = payload['isolated']
        self.assertEqual(isolated['dead_count'], 3)
        self.assertEqual(isolated['orphan_nodes'], ['unused.py'])
        self.assertEqual(isolated['islands'][0]['nodes'], ['legacy/helper.py', 'legacy/old.py'])

        impact = payload['impact']
        self.assertEqual(impact['src/lib/api.ts']['transitive_dependents'], 2)
        self.assertEqual(impact['src/main.tsx']['fan_out'], 3)
        self.assertTrue(impact['unused.py']['dead'])

    def test_discovers_framework_signals_from_manifests_and_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'package.json').write_text(
                json.dumps({
                    'dependencies': {'next': 'latest', 'react': 'latest', 'express': 'latest'},
                    'devDependencies': {'vite': 'latest'},
                }),
                encoding='utf-8',
            )
            (root / 'pyproject.toml').write_text(
                '\n'.join([
                    '[project]',
                    'dependencies = ["fastapi>=0.100", "Flask", "Django"]',
                ]),
                encoding='utf-8',
            )
            (root / 'next.config.js').write_text('module.exports = {};', encoding='utf-8')
            (root / 'vite.config.ts').write_text('export default {};', encoding='utf-8')
            (root / 'app.py').write_text('from fastapi import FastAPI\nfrom flask import Flask\n', encoding='utf-8')

            signals = discover_framework_signals(root)

        by_name = {item['framework']: item for item in signals}
        for framework in ('Next', 'Vite', 'React', 'Express', 'FastAPI', 'Flask', 'Django'):
            self.assertIn(framework, by_name)
            self.assertGreaterEqual(by_name[framework]['confidence'], 0.6)
            self.assertTrue(by_name[framework]['evidence'])

    def test_discovers_runtime_commands_from_package_pyproject_makefile_and_dockerfile(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'package.json').write_text(
                json.dumps({'scripts': {'dev': 'vite --host 0.0.0.0', 'lint': 'eslint .', 'test:e2e': 'playwright test'}}),
                encoding='utf-8',
            )
            (root / 'tests').mkdir()
            (root / 'tests' / 'test_app.py').write_text('def test_ok():\n    assert True\n', encoding='utf-8')
            (root / 'pyproject.toml').write_text(
                '\n'.join([
                    '[project.scripts]',
                    'api = "pkg.server:main"',
                    '',
                    '[tool.poetry.scripts]',
                    'worker = "pkg.worker:main"',
                ]),
                encoding='utf-8',
            )
            (root / 'Makefile').write_text('serve:\n\tpython app.py\nclean:\n\trm -rf build\n', encoding='utf-8')
            (root / 'Dockerfile').write_text('CMD ["python", "app.py"]\n', encoding='utf-8')

            commands = discover_runtime_commands(root)

        by_name = {(item['source'], item['name']): item for item in commands}
        self.assertEqual(by_name[('package.json', 'dev')]['command'], 'npm run dev')
        self.assertEqual(by_name[('package.json', 'test:e2e')]['category'], 'runtime')
        self.assertEqual(by_name[('pyproject.toml', 'api')]['command'], 'api')
        self.assertEqual(by_name[('pyproject.toml', 'worker')]['command'], 'poetry run worker')
        self.assertEqual(by_name[('tests/', 'pytest')]['command'], 'pytest')
        self.assertEqual(by_name[('Makefile', 'serve')]['command'], 'make serve')
        self.assertIn('docker run', by_name[('Dockerfile', 'cmd')]['command'])
        self.assertEqual(by_name[('Dockerfile', 'cmd')]['raw'], 'python app.py')


if __name__ == '__main__':
    unittest.main()
