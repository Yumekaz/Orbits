import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import analyzer


class WebAssetExtractionTests(unittest.TestCase):
    def test_html_css_and_static_assets_are_graph_edges(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'assets').mkdir()
            (root / 'pages').mkdir()
            (root / 'index.html').write_text(
                '<!doctype html>\n'
                '<link rel="stylesheet" href="styles/site.css">\n'
                '<script src="scripts/app.js"></script>\n'
                '<img src="assets/photo.jpg">\n'
                '<a href="pages/about.html">About</a>\n',
                encoding='utf-8',
            )
            (root / 'styles').mkdir()
            (root / 'styles' / 'site.css').write_text(
                '@import "./theme.css";\n'
                'body { background-image: url("../assets/bg.png"); }\n',
                encoding='utf-8',
            )
            (root / 'styles' / 'theme.css').write_text(':root { color: black; }\n', encoding='utf-8')
            (root / 'scripts').mkdir()
            (root / 'scripts' / 'app.js').write_text(
                'import "../styles/site.css";\n'
                'console.log("ok");\n',
                encoding='utf-8',
            )
            (root / 'pages' / 'about.html').write_text('<h1>About</h1>\n', encoding='utf-8')
            (root / 'assets' / 'photo.jpg').write_bytes(b'jpg')
            (root / 'assets' / 'bg.png').write_bytes(b'png')

            graph = analyzer.run(root, verbose=False)

        nodes = {node['id']: node for node in graph['nodes']}
        edges = {(edge['source'], edge['target']) for edge in graph['edges']}

        self.assertEqual(nodes['index.html']['classification'], 'ENTRY')
        self.assertEqual(nodes['styles/site.css']['language'], 'css')
        self.assertEqual(nodes['assets/photo.jpg']['language'], 'asset')
        self.assertEqual(nodes['assets/bg.png']['language'], 'asset')
        self.assertIn(('index.html', 'styles/site.css'), edges)
        self.assertIn(('index.html', 'scripts/app.js'), edges)
        self.assertIn(('scripts/app.js', 'styles/site.css'), edges)
        self.assertIn(('index.html', 'assets/photo.jpg'), edges)
        self.assertIn(('index.html', 'pages/about.html'), edges)
        self.assertIn(('styles/site.css', 'styles/theme.css'), edges)
        self.assertIn(('styles/site.css', 'assets/bg.png'), edges)
        self.assertIn('html', graph['meta']['languages'])
        self.assertIn('css', graph['meta']['languages'])
        self.assertIn('asset', graph['meta']['languages'])


if __name__ == '__main__':
    unittest.main()
