import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

import analyzer
from lang_dispatch import _detect_language_support, extract_all


def _normalize_pairs(edges):
    return {(src.replace('\\', '/'), dst.replace('\\', '/')) for src, dst in edges}


class AnalyzerBehaviorTests(unittest.TestCase):
    def test_run_does_not_edit_gitignore(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.gitignore').write_text('node_modules\n', encoding='utf-8')
            (root / 'app.py').write_text('import os\n', encoding='utf-8')

            analyzer.run(root, verbose=False)

            self.assertEqual((root / '.gitignore').read_text(encoding='utf-8'), 'node_modules\n')

    def test_server_serves_visualizer_and_graph_without_chdir(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            viz = root / 'visualizer.html'
            graph = root / 'graph.json'
            viz.write_text('<html>viz</html>', encoding='utf-8')
            graph.write_text(json.dumps({'ok': True}), encoding='utf-8')

            handler = analyzer.make_server_handler(viz, graph)
            server = analyzer.http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                with urlopen(base + '/visualizer.html') as response:
                    self.assertEqual(response.read().decode('utf-8'), '<html>viz</html>')
                with urlopen(base + '/graph.json') as response:
                    self.assertEqual(json.loads(response.read().decode('utf-8')), {'ok': True})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_missing_parser_metadata_matches_environment(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.js').write_text("import './dep.js';\n", encoding='utf-8')
            (root / 'dep.js').write_text('export const x = 1;\n', encoding='utf-8')
            (root / 'main.go').write_text('package main\nimport "fmt"\n', encoding='utf-8')

            raw = extract_all(root, verbose=False)
            support = _detect_language_support()
            unsupported = {item['language'] for item in raw['meta']['unsupported_languages']}

            expected = {lang for lang in ('javascript', 'go') if not support[lang]['available']}
            self.assertEqual(unsupported, expected)


if __name__ == '__main__':
    unittest.main()
