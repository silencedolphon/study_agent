"""批量删除本机文件的隔离验证。"""
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from core import config, db
from tools import dashboard


class RawBatchDelete(unittest.TestCase):
    def test_indexed_and_pending_files_and_stale_confirmation(self):
        with tempfile.TemporaryDirectory() as td, closing(db.connect(Path(td) / 'test.db')) as conn:
            root = Path(td)
            raw = root / 'raw'
            raw.mkdir()
            (raw / 'indexed.txt').write_text('indexed', encoding='utf8')
            (raw / 'pending.txt').write_text('pending', encoding='utf8')
            (raw / 'keep.txt').write_text('keep', encoding='utf8')
            db.init_db(conn)
            conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,content_hash) VALUES(1,?,?,?,?,?)",
                         ('raw/indexed.txt', str(raw/'indexed.txt'), 'txt', 'indexed', 'fake'))
            conn.commit()
            with mock.patch.object(config, 'ROOT', root), mock.patch.object(config, 'RAW_DIR', raw), \
                 mock.patch.object(config, 'LOG_DIR', root/'logs'), mock.patch.object(dashboard, '_subject_running', return_value=''):
                body = {'paths':['indexed.txt', 'pending.txt']}
                plan = dashboard.api_delete_raw_batch(conn, dict(body, dry_run=True))
                self.assertEqual(plan['indexed'], 1)
                self.assertTrue((raw/'indexed.txt').exists())
                self.assertIn('error', dashboard.api_delete_raw_batch(conn, dict(body, confirm=True, signatures={})))
                result = dashboard.api_delete_raw_batch(conn, dict(body, confirm=True, signatures=plan['signatures']))
                self.assertEqual(result['deleted'], 2, result)
                self.assertFalse((raw/'indexed.txt').exists())
                self.assertFalse((raw/'pending.txt').exists())
                self.assertTrue((raw/'keep.txt').exists())
                self.assertEqual(conn.execute('SELECT count(*) FROM documents').fetchone()[0], 0)

    def test_path_traversal_and_invalid_payload_are_rejected(self):
        with tempfile.TemporaryDirectory() as td, closing(db.connect(Path(td)/'test.db')) as conn, mock.patch.object(config, 'RAW_DIR', Path(td)), \
             mock.patch.object(dashboard, '_subject_running', return_value=''):
            db.init_db(conn)
            self.assertIn('error', dashboard.api_delete_raw_batch(conn, {'paths':['../x'], 'dry_run':True}))
            self.assertIn('error', dashboard.api_delete_raw_batch(conn, {'paths':[[]], 'dry_run':True}))
