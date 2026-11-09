"""各学科错题批量删除：仅使用临时数据库。"""
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from core import db
from tools import dashboard


class TestQuestionBatchDelete(unittest.TestCase):
    def test_selected_questions_only_and_subject_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(db.connect(Path(folder) / 'first.sqlite3')) as first, \
                 closing(db.connect(Path(folder) / 'second.sqlite3')) as second:
                db.init_db(first)
                db.init_db(second)
                for conn in (first, second):
                    conn.executemany('INSERT INTO mistakes(id, question) VALUES (?, ?)',
                                     [(1, '题目一'), (2, '题目二'), (3, '题目三')])
                    conn.commit()
                denied = dashboard.delete_mistakes(first, [], False, False)
                self.assertIn('error', denied)
                self.assertEqual(denied['remaining'], 3)
                result = dashboard.delete_mistakes(first, [1, 3], False, False)
                self.assertEqual(result['deleted'], 2)
                self.assertEqual([r['id'] for r in first.execute('SELECT id FROM mistakes')], [2])
                self.assertEqual(second.execute('SELECT COUNT(*) FROM mistakes').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
