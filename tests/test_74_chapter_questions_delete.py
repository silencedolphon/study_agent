import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from core import chapter_question_delete as module, config, subjects


class QuestionsDelete(unittest.TestCase):
    def test_revision_backup_and_minimum_question(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(config, 'LOG_DIR', Path(td)):
            root = Path(td) / 'knowledge'
            root.mkdir()
            path = root / '1-教材知识点-第1章.json'
            doc = {'points':[{'title':'A','questions':[{'question':'q1','answer':'a1'}, {'question':'q2','answer':'a2'}]},
                             {'title':'B','questions':[{'question':'q3','answer':'a3'}]}]}
            original = json.dumps(doc).encode()
            path.write_bytes(original)
            conn = sqlite3.connect(':memory:')
            self.addCleanup(conn.close)
            conn.execute('CREATE TABLE documents(id INTEGER, role TEXT)')
            conn.execute("INSERT INTO documents VALUES(1, 'textbook')")
            body = {'mode':'textbook','doc_id':1,'chapter':1,'expected_subject':subjects.current()}
            self.assertIn('error', module.handle(conn, dict(body, expected_subject='wrong-subject')))
            listing = module.handle(conn, body)
            self.assertEqual(len(listing['items']), 3)
            wrong = dict(body, delete=True, confirm=True, revision='stale', scope=listing['scope'], ids=['0:0'])
            self.assertIn('error', module.handle(conn, wrong))
            prohibited = dict(wrong, revision=listing['revision'], ids=['0:0','0:1'])
            self.assertIn('error', module.handle(conn, prohibited))
            self.assertIn('error', module.handle(conn, dict(prohibited, ids=['0:0'], scope='another-subject')))
            result = module.handle(conn, dict(prohibited, ids=['0:0']))
            self.assertTrue(result['ok'])
            self.assertEqual(Path(result['backup']).read_bytes(), original)
            self.assertEqual(json.loads(path.read_text(encoding='utf8'))['points'][0]['questions'][0]['question'], 'q2')

    def test_preview_selected_question_only(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(config, 'LOG_DIR', Path(td)):
            root = Path(td) / 'knowledge'
            root.mkdir()
            path = root / '1-预习总结-第2章.json'
            path.write_text(json.dumps({'subsections':[{'title':'一','self_check':[{'q':'q1'}, {'q':'q2'}]}]}), encoding='utf8')
            conn = sqlite3.connect(':memory:')
            self.addCleanup(conn.close)
            conn.execute('CREATE TABLE documents(id INTEGER, role TEXT)')
            conn.execute("INSERT INTO documents VALUES(1, 'textbook')")
            body = {'mode':'preview','doc_id':1,'chapter':2,'expected_subject':subjects.current()}
            listing = module.handle(conn, body)
            result = module.handle(conn, dict(body, delete=True, confirm=True, revision=listing['revision'], scope=listing['scope'], ids=['0:1']))
            self.assertTrue(result['ok'])
            self.assertEqual(json.loads(path.read_text(encoding='utf8'))['subsections'][0]['self_check'], [{'q':'q1'}])
