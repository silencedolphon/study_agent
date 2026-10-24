# -*- coding: utf-8 -*-
"""知识点整理清单回归：**按 DB 里的课件枚举，而不是按 logs/outline 文件**。

2026-10-24 用户实测：在「药物化学」学科新入库了很多课件，知识点页签的
「选择课件批量整理」里却只有 3 份旧课件。根因：`api_kp_build_options()` 旧实现
遍历 `logs/outline/*.json`（v1 结构分析产物）；新入库走 v3 管线，不写 outline，
于是新课件永远进不了清单。本用例用 fake conn + 隔离的临时目录锁住"新入库
（无 outline、无 knowledge 产物）的 courseware 仍应出现在清单里"。

不启动服务、不调模型、不写真实数据。
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.dashboard as dash  # noqa: E402


def _conn(rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT, kind TEXT,"
        " role TEXT, corpus TEXT, status TEXT, n_chunks INTEGER, updated_at REAL)"
    )
    for r in rows:
        con.execute(
            "INSERT INTO documents (id,title,kind,role,corpus,status,n_chunks,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)", r)
    return con


class TestKpOptionsCoursewareEnumeration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.est_ok = {
            "ok": True, "n_pages": 10, "n_leaf_pages": 10,
            "excluded_parent_pages": 0, "kp_min": 1, "kp_max": 10,
            "est_calls": 11, "est_cost_cny": 0.10, "material_chars_total": 1000,
            "out_path": str(self.dir / "99-末级知识点总结.json"),
        }
        self.prev = {
            "exists": False, "n_kps": 0, "path": "", "pipeline_version": "",
            "source_card": "", "cost_cny": 0.0, "resumable": False,
            "n_done_pages": 0, "pages_total": 0,
        }
        dash._KP_OPT_CACHE = {"sig": None, "t": 0.0, "data": None}

    def tearDown(self):
        self.tmp.cleanup()
        dash._KP_OPT_CACHE = {"sig": None, "t": 0.0, "data": None}

    def _run(self, rows, est=None, excluded_ok=False):
        con = _conn(rows)
        fake_est = est or self.est_ok
        with mock.patch.object(dash, "_outline_dir", return_value=self.dir), \
                mock.patch.object(dash, "_knowledge_dir", return_value=self.dir), \
                mock.patch.object(dash, "_kp_options_sig", return_value=("fake",)), \
                mock.patch.object(dash.kp_build_mod, "estimate", return_value=fake_est), \
                mock.patch.object(dash.kp_build_mod, "prev_info", return_value=self.prev), \
                mock.patch.object(dash.kp_batch_mod, "done_state", return_value=(False, "")), \
                mock.patch.object(dash, "_lkp_index", return_value={"state": "none"}):
            data = dash.api_kp_build_options(con)
        con.close()
        return data

    def test_courseware_without_outline_is_listed(self):
        """新入库课件没有 logs/outline 也必须进清单。"""
        rows = [(99, "新入库-药物化学课件", "pptx", "courseware", "main", "ok", 10, 123.0)]
        data = self._run(rows)
        ids = [x["doc_id"] for x in data["documents"]]
        self.assertIn(99, ids, "没有 outline 的新课件被漏掉了")
        item = next(x for x in data["documents"] if x["doc_id"] == 99)
        self.assertEqual(item["title"], "新入库-药物化学课件")
        self.assertEqual(item["n_pages"], 10)
        self.assertEqual(data["n"], 1)
        self.assertEqual(data["n_excluded"], 0)

    def test_textbook_is_not_listed_but_courseware_is(self):
        """教材不算可整理课件；同库课件要出现。"""
        rows = [
            (98, "药理学教材", "pdf", "textbook", "main", "ok", 100, 1.0),
            (99, "课件", "pdf", "courseware", "main", "partial", 20, 2.0),
        ]
        data = self._run(rows)
        ids = [x["doc_id"] for x in data["documents"]]
        self.assertIn(99, ids)
        self.assertNotIn(98, ids)

    def test_unestimable_doc_goes_to_excluded_not_silently_dropped(self):
        """没有文本页的课件必须有原因，而不是像旧实现一样静默消失。"""
        rows = [(97, "扫描版无文本", "pdf", "courseware", "main", "partial", 0, 3.0)]
        bad = {"ok": False, "error": "这份资料没有可总结的文本页"}
        data = self._run(rows, est=bad)
        self.assertEqual(data["n"], 0)
        self.assertEqual(data["n_excluded"], 1)
        self.assertEqual(data["excluded"][0]["doc_id"], 97)
        self.assertIn("没有可总结", data["excluded"][0]["error"])


if __name__ == "__main__":
    unittest.main()
