"""S16 第四步：只读页结构读取拆到 core/kp_text.py 的回归（零模型）。"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import kp_build, kp_text  # noqa: E402


class TestKpTextSplit(unittest.TestCase):
    def test_legacy_names_are_same_objects(self):
        self.assertIs(kp_build.read_text_pages_by_ids,
                      kp_text.read_text_pages_by_ids)
        self.assertIs(kp_build._figure_rows, kp_text.figure_rows)

    def test_empty_ids_returns_empty_structure_without_db(self):
        out = kp_text.read_text_pages_by_ids(1, [])
        self.assertEqual(out["n_blocks"], 0)
        self.assertEqual(out["blocks"], {})
        self.assertEqual(out["pages_all"], [])

    def test_read_by_ids_and_bad_ref_and_missing_figure_table(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "x.db"
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id INTEGER,"
                         " ord INTEGER, text TEXT, source_ref TEXT)")
            conn.executemany(
                "INSERT INTO chunks(id,doc_id,ord,text,source_ref) VALUES(?,?,?,?,?)",
                [(1, 1, 1, "甲", "p.3"), (2, 1, 2, "乙", "p.4-5")])
            conn.commit()
            conn.close()
            out = kp_text.read_text_pages_by_ids(1, [1, 2], db_path=db)
            self.assertEqual(out["n_blocks"], 2)
            self.assertEqual(out["pages_all"], [1, 2, 3, 4, 5])
            self.assertEqual(out["figrows"], {})  # 表缺失安全降级
            conn = sqlite3.connect(str(db))
            conn.execute("UPDATE chunks SET source_ref='p.xxx' WHERE id=2")
            conn.commit()
            conn.close()
            with self.assertRaises(RuntimeError):
                kp_text.read_text_pages_by_ids(1, [1, 2], db_path=db)


if __name__ == "__main__":
    unittest.main()
