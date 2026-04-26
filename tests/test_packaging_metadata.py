import importlib
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackagingMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))

    def test_console_script_points_at_cli_main(self):
        scripts = self.metadata['project']['scripts']

        self.assertEqual(scripts.get('orbits'), 'analyzer:main')
        module_name, function_name = scripts['orbits'].split(':', 1)
        module = importlib.import_module(module_name)

        self.assertTrue(callable(getattr(module, function_name)))

    def test_flat_module_packaging_includes_cli_dependencies(self):
        setuptools_config = self.metadata['tool']['setuptools']
        modules = set(setuptools_config['py-modules'])

        for module_name in {'analyzer', 'entrypoints', 'git_intel', 'graph_diff', 'graph_engine', 'lang_dispatch', 'runtime_trace'}:
            self.assertIn(module_name, modules)

        package_includes = set(self.metadata['tool']['setuptools']['packages']['find']['include'])
        self.assertIn('extractors', package_includes)
        self.assertIn('resolvers', package_includes)


if __name__ == '__main__':
    unittest.main()
