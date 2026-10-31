"""章级材料快速路径：A/B 等价性与可回滚开关（零模型、零真实库）。

背景：2026-10-31 GPT 性能建议指出 `preview.read_chapter_ch` 每次先读整本教材
（doc#50 约 900 块）再按 block_ids 过滤；`textbook_kp._plan` 也整表扫描后过滤。
本用例锁定两条快速路径与旧路径**输出逐字等价**：

- `preview.read_chapter_ch` 默认走 `read_text_pages_by_ids`（章节定向 SQL）；
  `PREVIEW_CHAPTER_FAST=0` 回退旧整本读取；
- `textbook_kp._plan` 默认按 `heading_path LIKE '%第N章%'` 定向查询；
  `TEXTBOOK_KP_FAST_PLAN=0` 回退整表扫描；
- `preview.list_chapters` 暴露 block_ids，`locate_chapter` 不再二次整表扫描。

测试只读临时 sqlite 与临时 LOG_DIR；不碰真实 index/logs/notes。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, kp_build, preview, textbook_kp  # noqa: E402

T36 = env.VERIFY_DIR / "t36_private"


def setUpModule():
    shutil.rmtree(T36, ignore_errors=True)
    T36.mkdir(parents=True, exist_ok=True)


def tearDownModule():
    shutil.rmtree(T36, ignore_errors=True)


def _new_db(name="fast.db") -> Path:
    p = T36 / name
    try:
        p.unlink()
    except OSError:
        pass
    conn = sqlite3.connect(str(p))
    conn.executescript("""
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY, path TEXT, abs_path TEXT, kind TEXT, title TEXT,
        content_hash TEXT, size_bytes INTEGER, mtime REAL, n_chunks INTEGER,
        role TEXT, corpus TEXT, status TEXT, warnings TEXT, extra TEXT,
        indexed_at REAL, updated_at REAL);
    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER, text TEXT,
        text_norm TEXT, heading_path TEXT, topic TEXT, source_ref TEXT,
        char_len INTEGER, token_est INTEGER, content_hash TEXT, created_at REAL);
    CREATE TABLE figure_desc (
        doc_id INTEGER, page INTEGER, no_info INTEGER,
        PRIMARY KEY (doc_id, page));
    """)
    conn.execute(
        "INSERT INTO documents(id,path,abs_path,kind,title,content_hash,n_chunks,"
        "role,corpus,status) VALUES(1,'raw/tb.pdf','/tb.pdf','pdf','测试教材','h',3,"
        "'textbook','main','ok')")
    rows = [
        (101, 1, 0, "第一章 绪论\n药物是影响机体生理过程的物质。", "p.1",
         "测试教材 > 第1章 绪论"),
        (102, 1, 1, "第二章 药代动力学\n药代动力学研究ADME。", "p.3",
         "测试教材 > 第2章 药代动力学"),
        (103, 1, 2, "第一章补充\n药效学研究药物作用。", "p.1-2",
         "测试教材 > 第1章 绪论"),
        (104, 1, 3, "图：药物作用通路示意", "p.2 图",
         "测试教材 > 第1章 绪论"),
    ]
    conn.executemany(
        "INSERT INTO chunks(id,doc_id,ord,text,source_ref,heading_path) "
        "VALUES(?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO figure_desc(doc_id,page,no_info) VALUES(1,2,0)")
    conn.commit()
    conn.close()
    return p


class TestChapterFastPath(unittest.TestCase):
    def setUp(self):
        self.db = _new_db(self._testMethodName + ".db")
        self.log = T36 / self._testMethodName / "logs"
        self.log.mkdir(parents=True, exist_ok=True)
        self._p = mock.patch.object(config, "LOG_DIR", self.log)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_list_chapters_exposes_block_ids_and_locate_avoids_second_scan(self):
        listed = preview.list_chapters(1, db_path=self.db)
        self.assertTrue(listed["ok"])
        ch = next(c for c in listed["chapters"] if c["no"] == 1)
        self.assertEqual(ch["block_ids"], [101, 103, 104])
        with mock.patch.object(preview, "agg_block_ids",
                               side_effect=AssertionError("不应二次整表扫描")):
            info = preview.locate_chapter(1, 1, db_path=self.db)
        self.assertTrue(info["ok"])
        self.assertEqual(info["block_ids"], [101, 103, 104])
        self.assertEqual(info["pages"], [1, 2])

    def test_fast_and_slow_chapter_material_are_identical(self):
        fast = preview.read_chapter_ch(1, 1, db_path=self.db)
        with mock.patch.dict(os.environ, {"PREVIEW_CHAPTER_FAST": "0"}):
            slow = preview.read_chapter_ch(1, 1, db_path=self.db)
        self.assertEqual(fast, slow)
        ch, info, material, inv, stats = fast
        self.assertEqual(ch["n_blocks"], 3)
        self.assertIn("第一章 绪论", material)
        self.assertIn("p.1", material)
        self.assertEqual(stats["mode"], "full")

    def test_read_text_pages_by_ids_matches_whole_read_subset(self):
        full = kp_build.read_text_pages(1, self.db)
        sub = kp_build.read_text_pages_by_ids(1, [101, 103, 104], self.db)
        want_blocks = {}
        for page, bs in full["blocks"].items():
            keep = [b for b in bs if b["chunk_id"] in {101, 103, 104}]
            if keep:
                want_blocks[page] = keep
        self.assertEqual(sub["blocks"], want_blocks)
        self.assertEqual(sub["n_blocks"], 3)
        self.assertEqual(sub["max_page"], 2)
        self.assertEqual(sub["pages_all"], [1, 2])

    def test_textbook_plan_fast_and_slow_signature_identical(self):
        fast = textbook_kp._plan(1, 1, db_path=self.db)
        with mock.patch.dict(os.environ, {"TEXTBOOK_KP_FAST_PLAN": "0"}):
            slow = textbook_kp._plan(1, 1, db_path=self.db)
        self.assertEqual(fast, slow)
        self.assertEqual(fast["chapter"], 1)
        self.assertEqual(fast["chapter_name"], "绪论")
        self.assertGreater(len(fast["segments"]), 0)


if __name__ == "__main__":
    unittest.main()
