import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import analyzer
from lang_dispatch import extract_all
from language_coverage import format_language_coverage_markdown


def _nodes_by_id(graph):
    return {node['id'].replace('\\', '/'): node for node in graph['nodes']}


class Phase6LanguageCoverageTests(unittest.TestCase):
    def test_glue_language_detection_and_partial_confidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'deploy').mkdir()
            (root / 'scripts').mkdir()
            (root / 'deploy' / 'docker-compose.yml').write_text('services:\n  app:\n    build: ..\n', encoding='utf-8')
            (root / 'scripts' / 'bootstrap.sh').write_text('#!/usr/bin/env bash\npython app.py\n', encoding='utf-8')
            (root / 'pyproject.toml').write_text('[project]\nname = "demo"\n', encoding='utf-8')

            raw = extract_all(root, verbose=False)
            nodes = _nodes_by_id(raw)

            self.assertEqual(nodes['deploy/docker-compose.yml']['language'], 'docker-compose')
            self.assertEqual(nodes['scripts/bootstrap.sh']['language'], 'shell')
            self.assertEqual(nodes['pyproject.toml']['language'], 'toml')
            self.assertEqual(nodes['deploy/docker-compose.yml']['analysis_confidence'], 'partial')
            self.assertEqual(nodes['scripts/bootstrap.sh']['analysis_confidence'], 'partial')
            self.assertEqual(nodes['pyproject.toml']['analysis_confidence'], 'partial')

    def test_deep_partial_and_unknown_confidence_are_attached_per_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('import helper\n', encoding='utf-8')
            (root / 'helper.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'pipeline.yml').write_text('steps:\n  - run: python app.py\n', encoding='utf-8')
            (root / 'module.widget').write_text('load "helper.py"\n', encoding='utf-8')

            graph = analyzer.run(root, verbose=False)
            nodes = _nodes_by_id(graph)

            self.assertEqual(nodes['app.py']['analysis_confidence'], 'deep')
            self.assertEqual(nodes['helper.py']['analysis_confidence'], 'deep')
            self.assertEqual(nodes['pipeline.yml']['analysis_confidence'], 'partial')
            self.assertEqual(nodes['module.widget']['language'], 'unknown')
            self.assertEqual(nodes['module.widget']['analysis_confidence'], 'unknown')

    def test_unknown_language_fallback_keeps_file_visible_without_edges(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'main.py').write_text('print("ready")\n', encoding='utf-8')
            (root / 'feature.customlang').write_text('import "missing.customlang"\n', encoding='utf-8')

            graph = analyzer.run(root, verbose=False)
            nodes = _nodes_by_id(graph)
            edges = {
                (edge['source'].replace('\\', '/'), edge['target'].replace('\\', '/'))
                for edge in graph['edges']
            }

            self.assertIn('feature.customlang', nodes)
            self.assertEqual(nodes['feature.customlang']['language'], 'unknown')
            self.assertEqual(nodes['feature.customlang']['analysis_confidence'], 'unknown')
            self.assertNotIn(('feature.customlang', 'missing.customlang'), edges)

    def test_language_coverage_summary_reports_confidence_percentages(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('print("ready")\n', encoding='utf-8')
            (root / 'settings.yml').write_text('app: demo\n', encoding='utf-8')
            (root / 'task.unknownext').write_text('do something\n', encoding='utf-8')
            (root / 'package.json').write_text('{"scripts":{"start":"python app.py"}}\n', encoding='utf-8')

            graph = analyzer.run(root, verbose=False)
            coverage = graph['meta']['language_coverage']

            self.assertEqual(coverage['total_files'], 4)
            self.assertEqual(coverage['deep']['files'], 1)
            self.assertEqual(coverage['partial']['files'], 2)
            self.assertEqual(coverage['unknown']['files'], 1)
            self.assertEqual(coverage['deep']['percent'], 25.0)
            self.assertEqual(coverage['partial']['percent'], 50.0)
            self.assertEqual(coverage['unknown']['percent'], 25.0)

    def test_github_actions_workflows_are_not_lost_in_hidden_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.github' / 'workflows').mkdir(parents=True)
            (root / '.github' / 'workflows' / 'ci.yml').write_text('name: ci\non: [push]\n', encoding='utf-8')

            graph = analyzer.run(root, verbose=False)
            nodes = _nodes_by_id(graph)

            self.assertIn('.github/workflows/ci.yml', nodes)
            self.assertEqual(nodes['.github/workflows/ci.yml']['language'], 'github-actions')
            self.assertEqual(nodes['.github/workflows/ci.yml']['analysis_confidence'], 'partial')

    def test_language_coverage_markdown_formats_named_languages(self):
        report = {
            'total_files': 2,
            'confidence': {
                'deep': {'files': 1, 'percent': 50.0},
                'partial': {'files': 1, 'percent': 50.0},
                'unknown': {'files': 0, 'percent': 0.0},
            },
            'languages': [
                {'display': 'Python', 'language': 'python', 'files': 1, 'analysis_confidence': 'deep', 'role': 'source', 'tier': 'deep', 'parser_available': True, 'note': 'ok', 'examples': ['app.py']},
                {'display': 'Dockerfile', 'language': 'dockerfile', 'files': 1, 'analysis_confidence': 'partial', 'role': 'build', 'tier': 'partial', 'parser_available': True, 'note': 'partial', 'examples': ['Dockerfile']},
            ],
        }

        markdown = format_language_coverage_markdown(report)

        self.assertIn('# Orbits Language Coverage', markdown)
        self.assertIn('| Python | 1 | deep | source | deep | yes | ok | `app.py` |', markdown)
        self.assertIn('| Dockerfile | 1 | partial | build | partial | yes | partial | `Dockerfile` |', markdown)

    def test_named_partial_source_languages_extract_local_edges_best_effort(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'src' / 'main.rs').write_text('mod util;\nfn main() {}\n', encoding='utf-8')
            (root / 'src' / 'util.rs').write_text('pub fn ok() {}\n', encoding='utf-8')
            (root / 'app').mkdir()
            (root / 'app' / 'main.rb').write_text("require_relative 'helper'\n", encoding='utf-8')
            (root / 'app' / 'helper.rb').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'web').mkdir()
            (root / 'web' / 'index.php').write_text("<?php require 'lib.php';\n", encoding='utf-8')
            (root / 'web' / 'lib.php').write_text('<?php $x = 1;\n', encoding='utf-8')
            (root / 'Program.cs').write_text('using Demo.Services;\nclass Program {}\n', encoding='utf-8')
            (root / 'Service.cs').write_text('namespace Demo { class Services {} }\n', encoding='utf-8')

            graph = analyzer.run(root, verbose=False)
            nodes = _nodes_by_id(graph)
            edges = {
                (edge['source'].replace('\\', '/'), edge['target'].replace('\\', '/'))
                for edge in graph['edges']
            }

            self.assertEqual(nodes['src/main.rs']['language'], 'rust')
            self.assertEqual(nodes['app/main.rb']['language'], 'ruby')
            self.assertEqual(nodes['web/index.php']['language'], 'php')
            self.assertEqual(nodes['Program.cs']['language'], 'csharp')
            self.assertEqual(nodes['src/main.rs']['analysis_confidence'], 'partial')
            self.assertIn(('src/main.rs', 'src/util.rs'), edges)
            self.assertIn(('app/main.rb', 'app/helper.rb'), edges)
            self.assertIn(('web/index.php', 'web/lib.php'), edges)
            self.assertIn(('Program.cs', 'Service.cs'), edges)


if __name__ == '__main__':
    unittest.main()
