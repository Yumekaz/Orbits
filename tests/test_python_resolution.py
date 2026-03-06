import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lang_dispatch import extract_all


class PythonResolutionTests(unittest.TestCase):
    def test_import_from_and_relative_and_star_resolution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'pkg').mkdir()
            (root / 'pkg' / '__init__.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'pkg' / 'mod.py').write_text('MOD = 1\n', encoding='utf-8')
            (root / 'pkg' / 'sibling.py').write_text('SIBLING = 1\n', encoding='utf-8')
            (root / 'pkg' / 'consumer.py').write_text('from . import sibling\n', encoding='utf-8')
            (root / 'main.py').write_text('from pkg import mod\nfrom pkg import *\n', encoding='utf-8')

            raw = extract_all(root, verbose=False)
            edges = {
                (edge['source'].replace('\\', '/'), edge['target'].replace('\\', '/'))
                for edge in raw['edges']
            }

            self.assertIn(('main.py', 'pkg/mod.py'), edges)
            self.assertIn(('main.py', 'pkg/__init__.py'), edges)
            self.assertIn(('pkg/consumer.py', 'pkg/sibling.py'), edges)


if __name__ == '__main__':
    unittest.main()
