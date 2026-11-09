import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.import_library_snapshot import import_snapshot, ensure_relocated


class ImportTest(unittest.TestCase):
    def test_copy_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            src, dst = root / 'snapshot', root / 'data'
            (src / 'index').mkdir(parents=True)
            (src / 'raw').mkdir()
            (src / 'raw' / 'x.txt').write_text('x')
            conn = sqlite3.connect(src / 'index' / 'study.db')
            conn.execute('create table documents(id integer primary key, path text, abs_path text)')
            conn.execute('insert into documents values(1, ?, ?)', (r'raw\x.txt', ''))
            conn.commit()
            conn.close()
            (src / 'logs').mkdir()
            (src / 'logs' / 'entry.json').write_text(json.dumps({'path': r'D:\study_agent\raw\x.txt'}))
            self.assertEqual(import_snapshot(src, dst)[:2], (1, 1))
            conn = sqlite3.connect(dst / 'index' / 'study.db')
            path, absolute = conn.execute('select path,abs_path from documents').fetchone()
            conn.close()
            self.assertEqual(path, 'raw/x.txt')
            self.assertEqual(Path(absolute).read_text(), 'x')
            self.assertEqual(json.loads((dst / 'logs' / 'entry.json').read_text())['path'], str(dst / 'raw' / 'x.txt'))
            with self.assertRaises(ValueError):
                import_snapshot(src, dst)
            moved = root / 'moved'
            dst.rename(moved)
            self.assertTrue(ensure_relocated(moved))
            self.assertFalse(ensure_relocated(moved))
            conn = sqlite3.connect(moved / 'index' / 'study.db')
            absolute = conn.execute('select abs_path from documents').fetchone()[0]
            conn.close()
            self.assertEqual(Path(absolute), moved / 'raw' / 'x.txt')
            self.assertEqual(json.loads((moved / 'logs' / 'entry.json').read_text())['path'], str(moved / 'raw' / 'x.txt'))

    def test_reject_traversal_and_links(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            src, dst = root / 'snapshot', root / 'data'
            (src / 'index').mkdir(parents=True)
            conn = sqlite3.connect(src / 'index' / 'study.db')
            conn.execute('create table documents(id integer primary key, path text, abs_path text)')
            conn.execute('insert into documents values(1, ?, ?)', (r'..\secret', ''))
            conn.commit()
            conn.close()
            with self.assertRaises(ValueError):
                import_snapshot(src, dst)
            self.assertFalse(dst.exists())
            if hasattr(os, 'symlink'):
                try:
                    (src / 'link').symlink_to(root, target_is_directory=True)
                except OSError:
                    return
                with self.assertRaises(ValueError):
                    import_snapshot(src, dst)

    def test_kb_home_root_isolated(self):
        with tempfile.TemporaryDirectory() as work:
            env = dict(os.environ, KB_HOME=work, PYTHONPATH=str(ROOT))
            code = 'from core import config; print(config.ROOT); print(config.BUNDLE_ROOT)'
            lines = subprocess.check_output([sys.executable, '-c', code], cwd=ROOT, env=env, text=True).splitlines()
            self.assertEqual(Path(lines[0]), Path(work).resolve())
            self.assertEqual(Path(lines[1]), ROOT)
            env.pop('KB_HOME')
            lines = subprocess.check_output([sys.executable, '-c', code], cwd=ROOT, env=env, text=True).splitlines()
            self.assertEqual(Path(lines[0]), ROOT)
            self.assertEqual(Path(lines[1]), ROOT)

    def test_env_resolution_priority_without_copying(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            source = base / 'source'
            data = base / 'data'
            (source / 'core').mkdir(parents=True)
            data.mkdir()
            (source / 'core' / '__init__.py').write_text('')
            (source / 'core' / 'config.py').write_bytes((ROOT / 'core' / 'config.py').read_bytes())
            (source / '.env').write_text('TEST_STUDY_ENV=source\nTEST_STUDY_SOURCE=source\n')
            (data / '.env').write_text('TEST_STUDY_ENV=data\n')
            env = dict(os.environ, KB_HOME=str(data), PYTHONPATH=str(source))
            env.pop('TEST_STUDY_ENV', None)
            env.pop('TEST_STUDY_SOURCE', None)
            code = 'import os; from core import config; print(os.environ["TEST_STUDY_ENV"], os.environ["TEST_STUDY_SOURCE"])'
            got = subprocess.check_output([sys.executable, '-c', code], cwd=source, env=env, text=True).strip()
            self.assertEqual(got, 'data source')
            env['TEST_STUDY_ENV'] = 'process'
            got = subprocess.check_output([sys.executable, '-c', code], cwd=source, env=env, text=True).strip()
            self.assertEqual(got, 'process source')


if __name__ == '__main__':
    unittest.main()
