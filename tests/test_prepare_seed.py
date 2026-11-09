import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import prepare_seed as seed


class SeedTest(unittest.TestCase):
    def test_stream_download_checksum_and_size(self):
        content = b'known fixture' * 1000
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(content)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                url = f'http://127.0.0.1:{server.server_port}/fixture'
                out = Path(td) / 'ok'
                seed.download(url, out, hashlib.sha256(content).hexdigest(), len(content))
                self.assertEqual(out.read_bytes(), content)
                with self.assertRaises(ValueError):
                    seed.download(url, Path(td) / 'bad', '0' * 64, len(content))
                with self.assertRaises(ValueError):
                    seed.download(url, Path(td) / 'short', hashlib.sha256(content).hexdigest(), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_safe_archive_and_reject_traversal_symlinks_and_collisions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / 'good.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('raw/sample.txt', 'content')
            seed.extract_verified(archive, root / 'out')
            self.assertEqual((root / 'out/raw/sample.txt').read_text(), 'content')
            for n, members in enumerate((['../escape'], ['C:/escape'], ['same', 'SAME'])):
                bad = root / f'bad{n}.zip'
                with zipfile.ZipFile(bad, 'w') as z:
                    for m in members:
                        z.writestr(m, 'bad')
                with self.assertRaises(ValueError):
                    seed.extract_verified(bad, root / f'out{n}')
            link = root / 'link.zip'
            i = zipfile.ZipInfo('link')
            i.create_system = 3
            i.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(link, 'w') as z:
                z.writestr(i, '../outside')
            with self.assertRaises(ValueError):
                seed.extract_verified(link, root / 'outlink')
            self.assertFalse((root / 'escape').exists())

    def test_existing_data_never_downloads_or_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'data'
            target.mkdir()
            existing = target / 'existing.txt'
            existing.write_text('keep')
            with patch.object(seed, 'download', side_effect=AssertionError('must not download')):
                with self.assertRaises(ValueError):
                    seed.prepare(target)
                (target / 'index').mkdir()
                (target / 'index' / 'study.db').write_text('existing database')
                self.assertFalse(seed.prepare(target))
            self.assertEqual(existing.read_text(), 'keep')

    def test_initialization_calls_importer_once(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'data'
            def download(url, dest, digest, size):
                with zipfile.ZipFile(dest, 'w') as z:
                    z.writestr('raw/sample.txt', 'content')
            def importer(source, destination):
                destination.mkdir()
                (destination / 'index').mkdir()
                (destination / 'index' / 'study.db').touch()
                (destination / 'seed.txt').write_text((source / 'raw/sample.txt').read_text())
                return (1, 1, 0)
            fake = types.SimpleNamespace(import_snapshot=importer)
            with patch.object(seed, 'download', side_effect=download), patch.dict(sys.modules, import_library_snapshot=fake):
                self.assertTrue(seed.prepare(target))
                self.assertFalse(seed.prepare(target))
            self.assertEqual((target / 'seed.txt').read_text(), 'content')

    def test_local_subjects_are_preserved_and_windows_launcher_passes_args(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            tools = project / 'tools'
            tools.mkdir()
            for name in ('prepare_seed.py', 'start_with_seed.py'):
                (tools / name).write_bytes((Path(seed.__file__).parent / name).read_bytes())
            (project / 'subjects' / 'existing').mkdir(parents=True)
            (tools / 'dashboard.py').write_text(
                "import os,sys; from pathlib import Path; Path('result.txt').write_text("
                "str(sys.argv[1:]) + '|KB_HOME=' + os.environ.get('KB_HOME',''), encoding='utf-8')",
                encoding='utf-8')
            env = dict(__import__('os').environ)
            env.pop('KB_HOME', None)
            result = subprocess.run([sys.executable, str(tools / 'start_with_seed.py'), '--port', '8757'],
                                    cwd=project, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("['--port', '8757']|KB_HOME=", (project / 'result.txt').read_text())
            self.assertFalse((project / '.study_data').exists())

    def test_existing_imported_library_checks_relocation(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'data'
            (target / 'index').mkdir(parents=True)
            (target / 'index' / 'study.db').touch()
            (target / '.study-data-root.json').write_text('{}')
            called = []
            fake = types.SimpleNamespace(ensure_relocated=lambda p: called.append(p) or False)
            with patch.dict(sys.modules, import_library_snapshot=fake):
                self.assertFalse(seed.prepare(target))
            self.assertEqual(called, [target])


if __name__ == '__main__':
    unittest.main()
