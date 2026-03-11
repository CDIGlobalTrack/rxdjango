import os
import subprocess
import tempfile
import shutil
from pathlib import Path

from django.test import TestCase
from rxdjango.sdk import make_sdk


class E2ETestCase(TestCase):
    def setUp(self):
        self.frontend_dir = Path(__file__).parent.parent.parent / 'frontend'
        self.rxdjango_react_dir = Path(__file__).parent.parent.parent.parent / 'rxdjango-react'

    def _run_makefrontend(self):
        make_sdk(apply_changes=True, force=True)

    def test_generated_typescript_compiles(self):
        self._run_makefrontend()

        channels_ts = self.frontend_dir / 'react_test' / 'react_test.channels.ts'
        interfaces_ts = self.frontend_dir / 'react_test' / 'react_test.interfaces.d.ts'

        self.assertTrue(channels_ts.exists(), f"channels.ts not generated at {channels_ts}")
        self.assertTrue(interfaces_ts.exists(), f"interfaces.d.ts not generated at {interfaces_ts}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            node_modules_src = self.rxdjango_react_dir / 'node_modules'
            node_modules_dst = tmpdir / 'node_modules'
            if node_modules_src.exists():
                shutil.copytree(node_modules_src, node_modules_dst)

            dist_src = self.rxdjango_react_dir / 'dist'
            dist_dst = tmpdir / 'node_modules' / '@rxdjango' / 'react'
            if dist_src.exists():
                os.makedirs(dist_dst, exist_ok=True)
                shutil.copytree(dist_src, dist_dst, dirs_exist_ok=True)

            shutil.copytree(self.frontend_dir / 'react_test', tmpdir / 'react_test')

            tsconfig = {
                "compilerOptions": {
                    "target": "ES2020",
                    "module": "commonjs",
                    "strict": True,
                    "esModuleInterop": True,
                    "skipLibCheck": True,
                    "forceConsistentCasingInFileNames": True,
                    "moduleResolution": "node",
                    "noEmit": True,
                },
                "include": ["react_test/**/*.ts"]
            }

            import json
            with open(tmpdir / 'tsconfig.json', 'w') as f:
                json.dump(tsconfig, f)

            tsc_path = self.rxdjango_react_dir / 'node_modules' / '.bin' / 'tsc'
            if not tsc_path.exists():
                tsc_path = Path('tsc')

            result = subprocess.run(
                [str(tsc_path), '--project', str(tmpdir / 'tsconfig.json')],
                capture_output=True,
                text=True,
                cwd=str(tmpdir)
            )

            if result.returncode != 0:
                channels_content = channels_ts.read_text()
                self.fail(
                    f"Generated TypeScript has syntax errors:\n"
                    f"tsc stdout:\n{result.stdout}\n"
                    f"tsc stderr:\n{result.stderr}\n"
                    f"Generated channels.ts content:\n{channels_content}"
                )

    def test_channels_ts_socket_url_is_quoted(self):
        self._run_makefrontend()

        channels_ts = self.frontend_dir / 'react_test' / 'react_test.channels.ts'
        content = channels_ts.read_text()

        self.assertIn('const SOCKET_URL = "', content,
                      "SOCKET_URL should be a quoted string literal")
        self.assertNotRegex(content, r'const SOCKET_URL = http',
                           "SOCKET_URL should not have unquoted URL")

    def test_channels_ts_no_double_braces(self):
        self._run_makefrontend()

        channels_ts = self.frontend_dir / 'react_test' / 'react_test.channels.ts'
        content = channels_ts.read_text()

        lines = content.split('\n')
        for i, line in enumerate(lines):
            if '}}' in line and 'interface' not in line.lower() and '{' not in line.split('}}')[0][-5:]:
                self.fail(
                    f"Line {i+1} has '}}' which is likely a syntax error:\n{line}"
                )
