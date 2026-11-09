"""仅操作临时库，验证多选知识点删除及关联行为。"""
import tempfile
import unittest
from pathlib import Path
from core import batch_delete, db


class KnowledgeBatchDelete(unittest.TestCase):
    def test_selected_only_and_foreign_key_effects(self):
        with tempfile.TemporaryDirectory() as td:
            conn = db.connect(Path(td) / 'db.sqlite')
            try:
                db.init_db(conn)
                conn.execute("INSERT INTO knowledge_points(id,name) VALUES(1,'one'),(2,'two')")
                conn.execute("INSERT INTO reviews(kp_id,grade) VALUES(1,3),(2,4)")
                conn.execute("INSERT INTO kp_edges(src_id,dst_id) VALUES(1,2)")
                conn.execute("INSERT INTO mistakes(kp_id,question) VALUES(1,'question')")
                conn.commit()
                self.assertIn('error', batch_delete.knowledge_points(conn, {'ids':[1]}))
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM knowledge_points').fetchone()[0], 2)
                result = batch_delete.knowledge_points(conn, {'ids':[1,99], 'confirm':True})
                self.assertEqual((result['deleted'], result['missing']), (1,1))
                self.assertEqual(conn.execute('SELECT id FROM knowledge_points').fetchone()[0], 2)
                self.assertEqual(conn.execute('SELECT kp_id FROM reviews').fetchone()[0], 2)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM kp_edges').fetchone()[0], 0)
                self.assertIsNone(conn.execute('SELECT kp_id FROM mistakes').fetchone()[0])
            finally:
                conn.close()


if __name__ == '__main__':
    unittest.main()
