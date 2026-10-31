"""S00A：三个已知缺陷的零模型回归（2026-10-31 GPT 审阅）。

覆盖：
1. 教材知识点 `_plan` 快速路径：`第1章` / `第 1 章` / `第01章` 混用不能漏块；
2. 预习快速路径：坏 `source_ref` 不能静默跳过，必须保留旧路径的明确错误；
3. 课件知识点 `claim_pages`：预约键必须是“页号 → 持有者”，A/B 不能同页成功。

只使用临时 SQLite / LOG_DIR；不碰真实 index/logs/notes；不调模型。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, kp_build, preview, textbook_kp  # noqa: E402


def _new_db(path: Path, *, bad_ref: bool = False) -> Path:
    conn = sqlite3.connect(str(path))
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
        "role,corpus,status) VALUES(1,'raw/tb.pdf','/tb.pdf','pdf','测试教材','h',4,"
        "'textbook','main','ok')")
    rows = [
        (101, 1, 0, "第1章 A", "p.1", "测试教材 > 第1章 绪论"),
        (102, 1, 1, "第1章 B", "p.2", "测试教材 > 第 1 章 绪论"),
        (103, 1, 2, "第1章 C", "p.3", "测试教材 > 第01章 绪论"),
        (104, 1, 3, "第2章 D", "p.4", "测试教材 > 第2章 药代动力学"),
    ]
    if bad_ref:
        rows.append((105, 1, 4, "坏页码", "p.xxx", "测试教材 > 第1章 绪论"))
    conn.executemany(
        "INSERT INTO chunks(id,doc_id,ord,text,source_ref,heading_path) "
        "VALUES(?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


class TestS00AKnownDefects(unittest.TestCase):
    def test_textbook_fast_plan_handles_mixed_chapter_formats(self):
        with tempfile.TemporaryDirectory() as td:
            db = _new_db(Path(td) / "tb.db")
            fast = textbook_kp._plan(1, 1, db_path=db)
            with mock.patch.dict(os.environ, {"TEXTBOOK_KP_FAST_PLAN": "0"}):
                slow = textbook_kp._plan(1, 1, db_path=db)
            self.assertEqual(fast, slow)
            self.assertEqual(fast["n_blocks"], 3)
            self.assertIn("第1章 A", fast["segments"][0]["material"])

    def test_preview_reports_and_rejects_invalid_source_ref(self):
        with tempfile.TemporaryDirectory() as td:
            db = _new_db(Path(td) / "bad.db", bad_ref=True)
            logs = Path(td) / "logs"
            logs.mkdir()
            with mock.patch.object(config, "LOG_DIR", logs):
                listed = preview.list_chapters(1, db_path=db)
                self.assertTrue(listed["ok"])
                self.assertEqual([x["id"] for x in listed["invalid_refs"]], [105])
                with self.assertRaises(RuntimeError) as a:
                    preview.read_chapter_ch(1, 1, db_path=db)
                with mock.patch.dict(os.environ, {"PREVIEW_CHAPTER_FAST": "0"}):
                    with self.assertRaises(RuntimeError) as b:
                        preview.read_chapter_ch(1, 1, db_path=db)
                self.assertIn("解析不出页码", str(a.exception))
                self.assertIn("解析不出页码", str(b.exception))

    def test_preview_fast_and_slow_equal_without_bad_refs(self):
        with tempfile.TemporaryDirectory() as td:
            db = _new_db(Path(td) / "ok.db")
            logs = Path(td) / "logs"
            logs.mkdir()
            with mock.patch.object(config, "LOG_DIR", logs):
                fast = preview.read_chapter_ch(1, 1, db_path=db)
                with mock.patch.dict(os.environ, {"PREVIEW_CHAPTER_FAST": "0"}):
                    slow = preview.read_chapter_ch(1, 1, db_path=db)
                self.assertEqual(fast, slow)

    def test_claim_pages_uses_page_to_owner(self):
        claimed = {}
        lock = threading.Lock()
        a = kp_build.claim_pages(claimed, lock, "A", [5])
        b = kp_build.claim_pages(claimed, lock, "B", [5])
        self.assertEqual(a, [5])
        self.assertEqual(b, [])
        self.assertEqual(claimed, {5: "A"})
        # 同一持有者重复预约幂等
        self.assertEqual(kp_build.claim_pages(claimed, lock, "A", [5]), [5])
        kp_build.release_pages(claimed, lock, "B", [5])   # 不是持有者，不释放
        self.assertEqual(claimed, {5: "A"})
        kp_build.release_pages(claimed, lock, "A", [5])
        self.assertEqual(claimed, {})
        self.assertEqual(kp_build.claim_pages(claimed, lock, "B", [5]), [5])

    def test_claim_pages_concurrent_single_winner(self):
        claimed = {}
        lock = threading.Lock()
        barrier = threading.Barrier(2)
        got = []

        def worker(kp_id):
            barrier.wait()
            got.append(kp_build.claim_pages(claimed, lock, kp_id, [10]))

        ts = [threading.Thread(target=worker, args=(x,)) for x in ("A", "B")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sum(1 for x in got if x == [10]), 1)
        self.assertEqual(claimed, {10: "A"} if claimed.get(10) == "A" else {10: "B"})


if __name__ == "__main__":
    unittest.main()
