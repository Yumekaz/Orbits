import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lang_dispatch import extract_all


def _normalized_edges(raw_graph):
    return {
        (edge['source'].replace('\\', '/'), edge['target'].replace('\\', '/'))
        for edge in raw_graph['edges']
    }


class MultiLanguageExtractionTests(unittest.TestCase):
    def test_typescript_alias_resolution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src' / 'components').mkdir(parents=True)
            (root / 'tsconfig.json').write_text('{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}}}', encoding='utf-8')
            (root / 'src' / 'components' / 'button.ts').write_text('export const button = 1;\n', encoding='utf-8')
            (root / 'src' / 'main.ts').write_text("import { button } from '@/components/button';\n", encoding='utf-8')

            raw = extract_all(root, verbose=False)
            self.assertIn(('src/main.ts', 'src/components/button.ts'), _normalized_edges(raw))

    def test_local_package_json_resolution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'packages' / 'shared' / 'src').mkdir(parents=True)
            (root / 'packages' / 'shared' / 'package.json').write_text(
                '{"name":"@acme/shared","exports":"./src/index.ts"}',
                encoding='utf-8',
            )
            (root / 'packages' / 'shared' / 'src' / 'index.ts').write_text('export const shared = 1;\n', encoding='utf-8')
            (root / 'app.ts').write_text("import { shared } from '@acme/shared';\n", encoding='utf-8')

            raw = extract_all(root, verbose=False)
            self.assertIn(('app.ts', 'packages/shared/src/index.ts'), _normalized_edges(raw))

    @unittest.skipUnless(os.name == 'nt', 'Windows path normalization regression')
    def test_local_package_json_resolution_with_case_mismatched_root(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'packages' / 'shared' / 'src').mkdir(parents=True)
            (root / 'packages' / 'shared' / 'package.json').write_text(
                '{"name":"@acme/shared","exports":"./src/index.ts"}',
                encoding='utf-8',
            )
            (root / 'packages' / 'shared' / 'src' / 'index.ts').write_text('export const shared = 1;\n', encoding='utf-8')
            (root / 'app.ts').write_text("import { shared } from '@acme/shared';\n", encoding='utf-8')

            raw = extract_all(Path(str(root).upper()), verbose=False)
            self.assertIn(('app.ts', 'packages/shared/src/index.ts'), _normalized_edges(raw))

    def test_go_module_resolution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'pkg').mkdir()
            (root / 'go.mod').write_text('module example.com/demo\n', encoding='utf-8')
            (root / 'pkg' / 'util.go').write_text('package pkg\n', encoding='utf-8')
            (root / 'main.go').write_text('package main\nimport "example.com/demo/pkg"\n', encoding='utf-8')

            raw = extract_all(root, verbose=False)
            self.assertIn(('main.go', 'pkg/util.go'), _normalized_edges(raw))

    def test_c_include_resolution_from_compile_commands(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'include' / 'lib').mkdir(parents=True)
            (root / 'src').mkdir()
            (root / 'compile_commands.json').write_text(
                '[{"directory":"' + str(root).replace('\\', '\\\\') + '","command":"cc -Iinclude src/main.c","file":"src/main.c"}]',
                encoding='utf-8',
            )
            (root / 'include' / 'lib' / 'foo.h').write_text('#pragma once\n', encoding='utf-8')
            (root / 'src' / 'main.c').write_text('#include "lib/foo.h"\nint main(){return 0;}\n', encoding='utf-8')

            raw = extract_all(root, verbose=False)
            self.assertIn(('src/main.c', 'include/lib/foo.h'), _normalized_edges(raw))

    @unittest.skipUnless(os.name == 'nt', 'Windows path normalization regression')
    def test_c_include_resolution_from_compile_commands_with_case_mismatched_root(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'include' / 'lib').mkdir(parents=True)
            (root / 'src').mkdir()
            (root / 'compile_commands.json').write_text(
                '[{"directory":"' + str(root).replace('\\', '\\\\') + '","command":"cc -Iinclude src/main.c","file":"src/main.c"}]',
                encoding='utf-8',
            )
            (root / 'include' / 'lib' / 'foo.h').write_text('#pragma once\n', encoding='utf-8')
            (root / 'src' / 'main.c').write_text('#include "lib/foo.h"\nint main(){return 0;}\n', encoding='utf-8')

            raw = extract_all(Path(str(root).upper()), verbose=False)
            self.assertIn(('src/main.c', 'include/lib/foo.h'), _normalized_edges(raw))

    def test_cpp_include_resolution_from_cmake(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'include' / 'detail').mkdir(parents=True)
            (root / 'src').mkdir()
            (root / 'CMakeLists.txt').write_text('target_include_directories(app PRIVATE include)\n', encoding='utf-8')
            (root / 'include' / 'detail' / 'foo.hpp').write_text('#pragma once\n', encoding='utf-8')
            (root / 'src' / 'main.cpp').write_text('#include "detail/foo.hpp"\nint main(){return 0;}\n', encoding='utf-8')

            raw = extract_all(root, verbose=False)
            self.assertIn(('src/main.cpp', 'include/detail/foo.hpp'), _normalized_edges(raw))

    def test_c_dynamic_loader_literal_resolution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'plugins').mkdir()
            (root / 'plugins' / 'plugin.so').write_bytes(b'ELF')
            (root / 'src' / 'main.c').write_text(
                '#include <dlfcn.h>\n'
                'int main(){ dlopen("../plugins/plugin.so", 0); return 0; }\n',
                encoding='utf-8',
            )

            raw = extract_all(root, verbose=False)
            self.assertIn(('src/main.c', 'plugins/plugin.so'), _normalized_edges(raw))
            nodes = {node['id'].replace('\\', '/'): node for node in raw['nodes']}
            self.assertEqual(nodes['plugins/plugin.so']['language'], 'asset')

    def test_java_resolution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src' / 'main' / 'java' / 'com' / 'acme' / 'util').mkdir(parents=True)
            (root / 'src' / 'main' / 'java' / 'com' / 'acme' / 'Main.java').write_text(
                'package com.acme; import com.acme.util.Helper; class Main {}\n',
                encoding='utf-8',
            )
            (root / 'src' / 'main' / 'java' / 'com' / 'acme' / 'util' / 'Helper.java').write_text(
                'package com.acme.util; class Helper {}\n',
                encoding='utf-8',
            )

            raw = extract_all(root, verbose=False)
            self.assertIn(
                ('src/main/java/com/acme/Main.java', 'src/main/java/com/acme/util/Helper.java'),
                _normalized_edges(raw),
            )

    def test_java_wildcard_resolution_fans_out_to_package(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src' / 'main' / 'java' / 'com' / 'acme' / 'util').mkdir(parents=True)
            (root / 'src' / 'main' / 'java' / 'com' / 'acme' / 'Main.java').write_text(
                'package com.acme; import com.acme.util.*; class Main {}\n',
                encoding='utf-8',
            )
            (root / 'src' / 'main' / 'java' / 'com' / 'acme' / 'util' / 'Helper.java').write_text('package com.acme.util; class Helper {}\n', encoding='utf-8')
            (root / 'src' / 'main' / 'java' / 'com' / 'acme' / 'util' / 'Other.java').write_text('package com.acme.util; class Other {}\n', encoding='utf-8')

            raw = extract_all(root, verbose=False)
            edges = _normalized_edges(raw)
            self.assertIn(('src/main/java/com/acme/Main.java', 'src/main/java/com/acme/util/Helper.java'), edges)
            self.assertIn(('src/main/java/com/acme/Main.java', 'src/main/java/com/acme/util/Other.java'), edges)

    def test_kotlin_resolution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src' / 'main' / 'kotlin' / 'com' / 'acme' / 'util').mkdir(parents=True)
            (root / 'src' / 'main' / 'kotlin' / 'com' / 'acme' / 'App.kt').write_text(
                'package com.acme\nimport com.acme.util.KHelper\nclass App\n',
                encoding='utf-8',
            )
            (root / 'src' / 'main' / 'kotlin' / 'com' / 'acme' / 'util' / 'KHelper.kt').write_text(
                'package com.acme.util\nclass KHelper\n',
                encoding='utf-8',
            )

            raw = extract_all(root, verbose=False)
            self.assertIn(
                ('src/main/kotlin/com/acme/App.kt', 'src/main/kotlin/com/acme/util/KHelper.kt'),
                _normalized_edges(raw),
            )


if __name__ == '__main__':
    unittest.main()
