import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import analyzer


def _nodes_by_id(graph):
    return {node['id'].replace('\\', '/'): node for node in graph['nodes']}


def _waste_ids(graph):
    return {item['id'].replace('\\', '/') for item in graph['waste']}


def _reason_kinds(node):
    return {reason.get('kind') for reason in node.get('entrypoint_reasons', [])}


class EntryPointDetectionTests(unittest.TestCase):
    def test_package_json_fields_scripts_and_bin_mark_entries_without_edges(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'scripts').mkdir()
            (root / 'launcher.js').write_text('console.log("launch");\n', encoding='utf-8')
            (root / 'src' / 'module.ts').write_text('export const value = 1;\n', encoding='utf-8')
            (root / 'cli.ts').write_text('console.log("cli");\n', encoding='utf-8')
            (root / 'scripts' / 'worker.ts').write_text('console.log("worker");\n', encoding='utf-8')
            (root / 'notes.js').write_text('export const unused = 1;\n', encoding='utf-8')
            (root / 'package.json').write_text(
                json.dumps({
                    'main': './launcher.js',
                    'module': './src/module.ts',
                    'bin': {'demo': './cli.ts'},
                    'scripts': {'worker': 'node scripts/worker.ts'},
                }),
                encoding='utf-8',
            )

            graph = analyzer.run(root, verbose=False)
            nodes = _nodes_by_id(graph)

            expected = {
                'launcher.js': 'package.json:main',
                'src/module.ts': 'package.json:module',
                'cli.ts': 'package.json:bin',
                'scripts/worker.ts': 'package.json:scripts',
            }
            for relpath, reason_kind in expected.items():
                self.assertEqual(nodes[relpath]['classification'], 'ENTRY')
                self.assertTrue(nodes[relpath]['entrypoint'])
                self.assertIn(reason_kind, _reason_kinds(nodes[relpath]))
                self.assertNotIn(relpath, _waste_ids(graph))

            self.assertIn('notes.js', _waste_ids(graph))
            self.assertEqual(
                {item['id'] for item in graph['meta']['entrypoints']},
                set(expected),
            )

    def test_python_project_dockerfile_and_makefile_entrypoints(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src' / 'pkg').mkdir(parents=True)
            (root / 'tools').mkdir()
            for name in ('cli.py', 'web.py', 'legacy.py', 'job.py'):
                (root / 'src' / 'pkg' / name).write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'manage.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'tools' / 'run.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'unused.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'pyproject.toml').write_text(
                '\n'.join([
                    '[project.scripts]',
                    'demo = "pkg.cli:main"',
                    '',
                    '[tool.poetry.scripts]',
                    'serve = "pkg.web:main"',
                ]),
                encoding='utf-8',
            )
            (root / 'setup.cfg').write_text(
                '\n'.join([
                    '[options.entry_points]',
                    'console_scripts =',
                    '    legacy = pkg.legacy:main',
                ]),
                encoding='utf-8',
            )
            (root / 'setup.py').write_text(
                'from setuptools import setup\n'
                'setup(entry_points={"console_scripts": ["job=pkg.job:main"]})\n',
                encoding='utf-8',
            )
            (root / 'Dockerfile').write_text('ENTRYPOINT ["python", "manage.py", "runserver"]\n', encoding='utf-8')
            (root / 'Makefile').write_text('start:\n\tpython tools/run.py\n', encoding='utf-8')

            graph = analyzer.run(root, verbose=False)
            nodes = _nodes_by_id(graph)
            expected = {
                'src/pkg/cli.py': 'pyproject:scripts',
                'src/pkg/web.py': 'pyproject:poetry.scripts',
                'src/pkg/legacy.py': 'setup.cfg:console_scripts',
                'src/pkg/job.py': 'setup.py:console_scripts',
                'manage.py': 'Dockerfile:ENTRYPOINT',
                'tools/run.py': 'Makefile:target',
            }

            for relpath, reason_kind in expected.items():
                self.assertEqual(nodes[relpath]['classification'], 'ENTRY')
                self.assertIn(reason_kind, _reason_kinds(nodes[relpath]))
                self.assertNotIn(relpath, _waste_ids(graph))

            self.assertIn('unused.py', _waste_ids(graph))

    def test_common_entry_names_reduce_orphans_but_plain_files_still_waste(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'main.go').write_text('package main\nfunc main() {}\n', encoding='utf-8')
            (root / 'src' / 'index.ts').write_text('export const value = 1;\n', encoding='utf-8')
            (root / 'plain.py').write_text('VALUE = 1\n', encoding='utf-8')

            graph = analyzer.run(root, verbose=False)
            nodes = _nodes_by_id(graph)

            self.assertEqual(nodes['main.go']['classification'], 'ENTRY')
            self.assertEqual(nodes['src/index.ts']['classification'], 'ENTRY')
            self.assertIn('common-name', _reason_kinds(nodes['main.go']))
            self.assertIn('common-name', _reason_kinds(nodes['src/index.ts']))
            self.assertIn('plain.py', _waste_ids(graph))


if __name__ == '__main__':
    unittest.main()
