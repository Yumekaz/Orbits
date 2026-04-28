import importlib
from importlib import resources
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

        for module_name in {
            'analyzer',
            'cleanup_plan',
            'codebase_map',
            'entrypoints',
            'git_intel',
            'graph_diff',
            'graph_engine',
            'lang_dispatch',
            'orbits',
            'runtime_trace',
            'scale_proof',
        }:
            self.assertIn(module_name, modules)

        package_includes = set(self.metadata['tool']['setuptools']['packages']['find']['include'])
        self.assertIn('extractors', package_includes)
        self.assertIn('resolvers', package_includes)
        self.assertIn('orbits_assets', package_includes)

    def test_visualizer_assets_are_package_data(self):
        package_data = self.metadata['tool']['setuptools']['package-data']
        self.assertIn('visualizer.html', package_data['orbits_assets'])
        self.assertIn('visualizer_app.js', package_data['orbits_assets'])
        self.assertTrue(resources.files('orbits_assets').joinpath('visualizer.html').is_file())

    def test_packaged_visualizer_assets_mirror_source_tree(self):
        for filename in ('visualizer.html', 'visualizer_app.js', 'visualizer_worker.js', 'visualizer.css'):
            source = (ROOT / filename).read_bytes()
            packaged = (ROOT / 'orbits_assets' / filename).read_bytes()
            self.assertEqual(packaged, source)


if __name__ == '__main__':
    unittest.main()
