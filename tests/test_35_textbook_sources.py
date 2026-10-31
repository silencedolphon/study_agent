"""task-41 验收：教材知识点窗口式阅读的出处元数据（sources/pages/页码）。

全离线：临时教材 DB + 产物；`read_summary()` 只读补齐，不写产物、不调模型。
覆盖：页码解析 p.N / p.N-M / p.N–M、无页码跳过、只查本教材 doc_id、
text 截前 400 字、pages 排序去重且最多 20、范围首尾页、异常吞掉、真实产物只读抽查。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import textbook_kp  # noqa: E402


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class _Fixture(unittest.TestCase):
    doc_id = 50
    other_doc_id = 46
    chapter = 6

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "study.db"
        self.logs = self.root / "logs"
        self._make_db()
        self._make_artifact()

    def _make_db(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.executescript("""
        CREATE TABLE documents(id INTEGER PRIMARY KEY, title TEXT, role TEXT);
        CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER,
                            source_ref TEXT, heading_path TEXT, text TEXT);
        """)
        conn.execute("INSERT INTO documents VALUES(?,?,?)",
                     (self.doc_id, "药理学 第6版 刘晓东", "textbook"))
        conn.execute("INSERT INTO documents VALUES(?,?,?)",
                     (self.other_doc_id, "某课件", "courseware"))
        heading = "药理学 第6版 刘晓东 > 第6章 作用于胆碱受体药"
        rows = [
            (101, self.doc_id, 1, "p.101", heading, "甲" * 600),
            (102, self.doc_id, 2, "p.101-108", heading, "乙" * 200),
            (103, self.doc_id, 3, "p.10–12", heading, "丙" * 50),
            (104, self.doc_id, 4, "", heading, "丁" * 20),
            (107, self.doc_id, 5, "p.1-100", heading, "庚" * 100),
            (106, self.doc_id, 6, "slide 3", heading, "己" * 20),
            (105, self.other_doc_id, 1, "p.5", "课件 > 第1章", "戊" * 20),
        ]
        conn.executemany(
            "INSERT INTO chunks VALUES(?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()

    def _make_artifact(self, points=None) -> Path:
        final, _, _ = textbook_kp._paths(self.doc_id, self.chapter, self.logs)
        textbook_kp._write(final, {
            "version": textbook_kp.VERSION, "status": "done",
            "signature": "stale-for-test", "doc_id": self.doc_id,
            "chapter": self.chapter, "title": "药理学 第6版 刘晓东",
            "chapter_name": "作用于胆碱受体药", "generated_at": 0,
            "points": points if points is not None else [
                {"id": "p1", "title": "阿托品作用",
                 "summary": "s", "questions": [],
                 "source_ids": [101, 102, 103, 104, 105, 106, 107, 9999]},
                {"id": "p2", "title": "无出处", "summary": "s",
                 "questions": [], "source_ids": []},
                {"id": "p3", "title": "坏 id", "summary": "s",
                 "questions": [], "source_ids": ["bad", 105]},
            ],
        })
        return final

    def _summary(self):
        return textbook_kp.read_summary(self.doc_id, self.chapter,
                                        log_dir=self.logs, db_path=self.db)


class TestSources(_Fixture):
    def test_shape_parsing_and_filtering(self):
        """p.N / p.N-M / p.N–M 解析；无页码/他 doc/坏 id 跳过；text 截 400 字。"""
        result = self._summary()
        self.assertTrue(result["exists"])
        by_id = {p["id"]: p for p in result["points"]}
        p1 = by_id["p1"]
        self.assertEqual([s["id"] for s in p1["sources"]],
                         [107, 103, 101, 102], "按 page_start 排序")
        s101 = next(s for s in p1["sources"] if s["id"] == 101)
        self.assertEqual((s101["ref"], s101["page_start"], s101["page_end"]),
                         ("p.101", 101, 101))
        self.assertEqual(len(s101["text"]), 400, "摘录前 400 字")
        self.assertEqual(s101["text"], "甲" * 400)
        self.assertEqual(s101["heading_path"],
                         "药理学 第6版 刘晓东 > 第6章 作用于胆碱受体药")
        s102 = next(s for s in p1["sources"] if s["id"] == 102)
        self.assertEqual((s102["page_start"], s102["page_end"]), (101, 108))
        s103 = next(s for s in p1["sources"] if s["id"] == 103)
        self.assertEqual((s103["page_start"], s103["page_end"]), (10, 12),
                         "en dash p.10–12 也要解析")
        self.assertNotIn(104, [s["id"] for s in p1["sources"]],
                         "无页码的块应跳过")
        self.assertNotIn(105, [s["id"] for s in p1["sources"]],
                         "只能查该教材 doc_id（课件块必须跳过）")
        self.assertNotIn(106, [s["id"] for s in p1["sources"]],
                         "slide/非 p.N 的 ref 跳过")
        # 每个来源的字段齐全
        for s in p1["sources"]:
            for key in ("id", "ref", "page_start", "page_end",
                        "heading_path", "text"):
                self.assertIn(key, s)
        for pid in ("p2", "p3"):
            p = by_id[pid]
            self.assertEqual(p["sources"], [])
            self.assertEqual(p["pages"], [])
            self.assertEqual((p["page_start"], p["page_end"]), (0, 0))

    def test_pages_sorted_dedup_and_capped(self):
        """pages 排序去重、最多 20；page_start/page_end 是**全范围**首尾页。"""
        p1 = {p["id"]: p for p in self._summary()["points"]}["p1"]
        # 来源并集：1..108（p.1-100 + p.101-108 + p.10–12）
        self.assertEqual(p1["pages"], list(range(1, 21)),
                         "排序去重后只留前 20 个页码")
        self.assertEqual((p1["page_start"], p1["page_end"]), (1, 108),
                         "范围首尾页取全量、不被 20 截断")
        self.assertEqual(len(p1["pages"]), min(20, 108))

    def test_artifact_and_db_unchanged(self):
        """read_summary 只读：产物文件与 DB 字节 hash 前后一致。"""
        final, _, _ = textbook_kp._paths(self.doc_id, self.chapter, self.logs)
        db_sha, art_sha = _sha(self.db), _sha(final)
        before = sorted(str(p.relative_to(self.logs))
                        for p in self.logs.rglob("*") if p.is_file())
        self._summary()
        self.assertEqual(_sha(self.db), db_sha, "不得写 DB")
        self.assertEqual(_sha(final), art_sha, "不得写产物文件")
        after = sorted(str(p.relative_to(self.logs))
                       for p in self.logs.rglob("*") if p.is_file())
        self.assertEqual(after, before, "不得产生额外文件")

    def test_attach_sources_swallows_db_errors(self):
        """DB 不存在/坏掉时：字段补空、不抛异常、原 points 保留。"""
        result = {"doc_id": self.doc_id,
                  "points": [{"id": "p1", "source_ids": [101]}]}
        out = textbook_kp._attach_sources(
            result, db_path=self.root / "nope.db")
        self.assertIs(out, result)
        p = out["points"][0]
        self.assertEqual((p["sources"], p["pages"]), ([], []))
        self.assertEqual((p["page_start"], p["page_end"]), (0, 0))

    def test_ref_span_parser(self):
        f = textbook_kp._ref_span
        self.assertEqual(f("p.101"), (101, 101))
        self.assertEqual(f("p.101-108"), (101, 108))
        self.assertEqual(f("p.10–12"), (10, 12))
        self.assertEqual(f("p.5-3"), (3, 5))
        self.assertIsNone(f(""))
        self.assertIsNone(f("slide 3"))
        self.assertIsNone(f(None))


class TestRealArtifactReadOnly(unittest.TestCase):
    """真实产物只读抽查（有产物才跑；不写真实 DB/logs）。"""

    def test_real_chapter_six_sources_if_present(self):
        real_db = env.PROJECT / "index" / "study.db"
        real_logs = env.PROJECT / "logs"
        art = real_logs / "knowledge" / "50-教材知识点-第6章.json"
        if not (art.exists() and real_db.exists()):
            self.skipTest("真实教材知识点产物不存在，跳过")
        before = _sha(real_db), _sha(art)
        result = textbook_kp.read_summary(50, 6, log_dir=real_logs,
                                          db_path=real_db)
        self.assertTrue(result["exists"])
        with_ids = [p for p in result["points"] if p.get("source_ids")]
        self.assertTrue(with_ids, "第6章产物应有 source_ids")
        for p in result["points"]:
            self.assertIn("sources", p)
            self.assertIn("pages", p)
            self.assertIn("page_start", p)
            self.assertIn("page_end", p)
        self.assertTrue(any(p["sources"] for p in with_ids),
                        "至少一条知识点能补出教材原文出处")
        for src in with_ids[0]["sources"]:
            self.assertLessEqual(len(src["text"]), 400)
            self.assertGreater(src["page_start"], 0)
        self.assertEqual((_sha(real_db), _sha(art)), before,
                         "真实 DB/产物不得被修改")


if __name__ == "__main__":
    unittest.main(verbosity=2)
