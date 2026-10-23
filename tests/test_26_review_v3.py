"""复习自测 v3（C-0128-review-v3，task-35）专项验收：结构路径题面 + 已掌握/未掌握。

与 `test_23_review_flow.py` 互补：这里专测 **v3 新契约的边界**
（22→11 抽样、branch 构造规则、results 多种写法、旧 v2 legacy、状态自洽）。
全离线、不调 AI。
"""
from __future__ import annotations

import json
import random
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, kp_build, review, teach  # noqa: E402

T26 = env.VERIFY_DIR / "t26_private"
DOC = 46
TB = 50


def _kps_22() -> list:
    return [
        {"id": "KP-%02d" % i, "name": "知识点%d" % i,
         "node_name": "知识点%d" % i,
         "summary": "第%d条知识点的总结：阿托品阻断M胆碱受体，用于内脏绞痛。" % i,
         "points": ["要点%d" % i], "pages": [i],
         "node_path": ["课件根节点", "父级分组%d" % (i % 3), "知识点%d" % i]}
        for i in range(1, 23)
    ]


def setUpModule():
    shutil.rmtree(T26, ignore_errors=True)
    T26.mkdir(parents=True, exist_ok=True)


def tearDownModule():
    shutil.rmtree(T26, ignore_errors=True)


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = T26 / self._testMethodName
        shutil.rmtree(self.dir, ignore_errors=True)
        self.logdir = self.dir / "logs"
        (self.logdir / "knowledge").mkdir(parents=True, exist_ok=True)
        self.notesdir = self.dir / "notes"
        self.notesdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "study.db"
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript("""
        CREATE TABLE documents (id INTEGER PRIMARY KEY, path TEXT, abs_path TEXT,
            kind TEXT, title TEXT, content_hash TEXT, size_bytes INTEGER,
            mtime REAL, n_chunks INTEGER, role TEXT, corpus TEXT, status TEXT,
            warnings TEXT, extra TEXT, indexed_at REAL, updated_at REAL);
        """)
        conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,n_chunks,"
                     "role,corpus,status) VALUES(46,'raw/x.pptx','/x.pptx','pptx',"
                     "'幻灯片课件',25,'courseware','main','ok')")
        conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,n_chunks,"
                     "role,corpus,status) VALUES(50,'raw/tb.pdf','/tb.pdf','pdf',"
                     "'药理学',882,'textbook','main','ok')")
        conn.commit()
        conn.close()
        self.kp_path = self.logdir / "knowledge" / "46-末级知识点总结.json"
        self.kp_path.write_text(json.dumps({
            "meta": {"doc_id": DOC, "deck": "幻灯片课件"},
            "knowledge_points": _kps_22(),
        }, ensure_ascii=False), encoding="utf-8")
        (self.logdir / "knowledge" / "46-教材补充.json").write_text(json.dumps({
            "meta": {"chapter_map": {"chapter_no": 6, "name": "第6章 胆碱受体药",
                                     "page_label": "p.70–p.82"}}, "kps": {}},
            ensure_ascii=False), encoding="utf-8")
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.logdir),
            mock.patch.object(config, "NOTES_DIR", self.notesdir),
            mock.patch.object(kp_build, "out_file", lambda doc_id: self.kp_path),
            mock.patch.object(kp_build, "doc_title_v2",
                              lambda doc_id, db_path=None: "幻灯片课件"),
            mock.patch.object(teach, "map_courseware_chapter",
                              lambda *a, **k: {"chapter_no": 0}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _start(self, seed=0):
        return review.start_selftest(DOC, db_path=self.db_path,
                                     rng=random.Random(seed))


class TestV3Contract(_Base):
    def test_22_to_11_items_contract(self):
        """22 条 KP → 11 条；items 无挖空字段，branch 最后一层=自身，summary 可展开。"""
        rep = self._start(11)
        self.assertTrue(rep["ok"])
        self.assertEqual((rep["n_total"], rep["n_sample"]), (22, 11))
        self.assertEqual(len(rep["items"]), 11)
        self.assertEqual(len({it["kp_id"] for it in rep["items"]}), 11, "不放回抽样")
        for it in rep["items"]:
            self.assertEqual(set(it.keys()),
                             {"kp_id", "name", "node_path", "branch", "summary",
                              "unfamiliar"})
            self.assertNotIn("segments", it)
            self.assertNotIn("answers", it)
            self.assertEqual(it["branch"][-1]["name"], it["name"])
            self.assertGreaterEqual(len(it["branch"]), 2)
            self.assertIn("父级分组", it["branch"][0]["name"])
            self.assertEqual(it["node_path"][0], "课件根节点")
        # 开始不写盘
        self.assertFalse(review.selftest_out_file(DOC).exists())
        # kps 摘要（前端旧渲染路径）仍可用
        self.assertEqual(len(rep["kps"]), 11)
        self.assertTrue(all("kp_id" in k for k in rep["kps"]))

    def test_submit_variants_and_aliases(self):
        """results 支持 dict(bool/str/0/1)、list(已掌握 id)；缺失按未掌握；过渡别名齐全。"""
        rep = self._start(3)
        ids = [it["kp_id"] for it in rep["items"]]
        results = {}
        for i, kid in enumerate(ids):
            results[kid] = [True, "true", 1, "已掌握", False, "false", 0,
                            "未掌握", None, "yes", "no"][i % 11]
        res = review.submit_selftest(DOC, rep["session_id"], results)
        self.assertTrue(res["ok"])
        expected = sum(1 for v in [True, "true", 1, "已掌握", False, "false", 0,
                                   "未掌握", None, "yes", "no"][:11]
                       if v is True or v in ("true", 1, "已掌握", "yes"))
        self.assertEqual(res["n_mastered"], expected)
        self.assertEqual(res["n_mastered"] + res["n_unmastered"], 11)
        self.assertEqual(res["n_familiar"], res["n_mastered"])
        self.assertAlmostEqual(res["familiarity"], round(expected / 11, 4))
        # list 形式：只提交已掌握 id
        rep2 = self._start(4)
        ids2 = [it["kp_id"] for it in rep2["items"]]
        res2 = review.submit_selftest(DOC, rep2["session_id"], ids2[:4])
        self.assertEqual(res2["n_mastered"], 4)
        self.assertEqual(res2["n_unmastered"], 7)
        self.assertEqual(set(res2["unfamiliar_ids"]), set(ids2[4:]))
        # missing KP 按未掌握（第三条路径：dict 只有 1 个 true）
        rep3 = self._start(5)
        res3 = review.submit_selftest(DOC, rep3["session_id"],
                                      {rep3["items"][0]["kp_id"]: True})
        self.assertEqual(res3["n_mastered"], 1)
        self.assertEqual(res3["n_unmastered"], 10)
        # 非法 results 类型 → 明确错误
        rep4 = self._start(6)
        bad = review.submit_selftest(DOC, rep4["session_id"], "nope")
        self.assertFalse(bad["ok"])
        self.assertIn("results", bad["error"])

    def test_old_v2_file_legacy_and_new_submit_upgrades(self):
        """旧 v2 文件（familiar 键）→ legacy 可读；新提交后版本升 v3 且自洽。"""
        out = review.selftest_out_file(DOC)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "meta": {"version": "C-0127-review-v2", "doc_id": DOC,
                     "n_total": 22, "n_sample": 11, "n_mastered": 10,
                     "n_familiar": 10, "n_unfamiliar": 1,
                     "familiarity": round(10 / 11, 4)},
            "kps": {("KP-%02d" % i): {"familiar": i <= 10} for i in range(1, 12)},
        }, ensure_ascii=False), encoding="utf-8")
        st = review.selftest_status(DOC)
        self.assertTrue(st["legacy"])
        self.assertEqual((st["n_mastered"], st["n_unmastered"]), (10, 1))
        self.assertEqual(st["unfamiliar_ids"], ["KP-11"])
        rep = self._start(7)
        res = review.submit_selftest(DOC, rep["session_id"],
                                     {it["kp_id"]: True for it in rep["items"]})
        self.assertEqual(res["familiarity"], 1.0)
        st2 = review.selftest_status(DOC)
        self.assertFalse(st2["legacy"])
        self.assertEqual(st2["n_mastered"], 11)
        self.assertEqual(st2["n_unmastered"], 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["meta"]["version"], review.REVIEW_VERSION)
        self.assertEqual(len(data["kps"]), data["meta"]["n_sample"])

    def test_flow_mastered_consistency(self):
        """flow 的 mastered/unfamiliar 与 status 严格一致（同一 kps 现算）。"""
        rep = self._start(8)
        first = rep["items"][0]["kp_id"]
        review.submit_selftest(DOC, rep["session_id"],
                               {it["kp_id"]: it["kp_id"] != first
                                for it in rep["items"]})
        st = review.selftest_status(DOC)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            flow = review.build_flow(conn, DOC)
        finally:
            conn.close()
        by_id = {k["kp_id"]: k for k in flow["kps"]}
        self.assertEqual(set(by_id), {"KP-%02d" % i for i in range(1, 23)})
        for kid in st["kps"]:
            self.assertEqual(by_id[kid]["mastered"], st["kps"][kid]["mastered"])
            self.assertEqual(by_id[kid]["unfamiliar"],
                             not st["kps"][kid]["mastered"])
        self.assertEqual(by_id[first]["mastered"], False)
        self.assertTrue(by_id[first]["unfamiliar"])
        self.assertEqual(flow["selftest"]["n_mastered"], st["n_mastered"])
        self.assertEqual(flow["selftest"]["n_unmastered"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
