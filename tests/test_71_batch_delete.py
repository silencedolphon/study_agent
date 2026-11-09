import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from core import batch_delete, config


class TestBatchDelete(unittest.TestCase):
    def test_notes_validation_and_selected_only(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(config, 'NOTES_DIR', Path(td)):
            root = Path(td)
            (root / 'a.md').write_text('a')
            (root / 'b.md').write_text('b')
            self.assertIn('error', batch_delete.notes({'files': ['../escape.md'], 'confirm': True}))
            self.assertIn('error', batch_delete.notes({'files': ['a.md']}))
            self.assertEqual(batch_delete.notes({'files': ['a.md'], 'dry_run': True})['deleted'], ['a.md'])
            self.assertTrue((root / 'a.md').exists())
            self.assertTrue(batch_delete.notes({'files': ['a.md'], 'confirm': True})['ok'])
            self.assertFalse((root / 'a.md').exists())
            self.assertTrue((root / 'b.md').exists())

    def test_textbook_chapters_do_not_touch_other_chapters(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(config, 'LOG_DIR', Path(td)):
            root = Path(td) / 'knowledge'
            root.mkdir()
            conn = sqlite3.connect(':memory:')
            self.addCleanup(conn.close)
            conn.execute('CREATE TABLE documents (id INTEGER, role TEXT)')
            conn.execute("INSERT INTO documents VALUES (1,'textbook')")
            for name in ['1-教材知识点-第1章.json', '1-教材知识点-第1章.review.json',
                         '1-教材知识点-第2章.json', '1-预习总结-第1章.json']:
                (root / name).write_text('{}')
            (root / '1-预习总结.json').write_text('{"meta":{"chapter_no":1}}')
            payload = {'doc_id': 1, 'chapters': [1], 'kind': 'textbook', 'confirm': True}
            self.assertTrue(batch_delete.chapters(conn, payload)['ok'])
            self.assertFalse((root / '1-教材知识点-第1章.json').exists())
            self.assertFalse((root / '1-教材知识点-第1章.review.json').exists())
            self.assertTrue((root / '1-教材知识点-第2章.json').exists())
            self.assertTrue((root / '1-预习总结-第1章.json').exists())
            self.assertTrue(batch_delete.chapters(conn, dict(payload, kind='preview'))['ok'])
            self.assertFalse((root / '1-预习总结-第1章.json').exists())
            self.assertFalse((root / '1-预习总结.json').exists())
