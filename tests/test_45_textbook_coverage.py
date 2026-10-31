"""P3-A / S13：教材长章覆盖统计（只增字段，不改签名/分段/点 ID；零模型）。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import db, kp_build, textbook_kp  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        import shutil
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = Path(self.tmp)
        self.logs = self.root / "logs"; self.logs.mkdir()
        self.dbpath = self.root / "t.db"
        conn = db.connect(self.dbpath, immediate=True)
        db.init_db(conn)
        conn.execute("INSERT INTO documents(id, path, abs_path, kind, title, "
                     "content_hash, role, n_chunks, n_chars, status) VALUES("
                     "50,'t.pdf','t.pdf','pdf','药理学','h','textbook',0,0,'ok')")
        self.conn = conn
        conn.commit()
        self._patches = [mock.patch.dict(os.environ, {
            "TEXTBOOK_KP_FAST_PLAN": "1", "TEXTBOOK_KP_COVERAGE": "1",
            "PIPELINE_RUNTIME": "1"}),
            mock.patch.object(textbook_kp.config, "LOG_DIR", self.logs)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.conn.close()

    def _chunk(self, ord_, text, ref="p.1"):
        self.conn.execute(
            "INSERT INTO chunks(doc_id, ord, text, text_norm, heading_path, source_ref, "
            "char_len, token_est, content_hash, created_at) VALUES(50,?,?,?,?,?,?,?,?,0)",
            (ord_, text, text, "药理学 > 第1章 总论", ref, len(text), len(text) // 2,
             db.sha256_text(text)))
        self.conn.commit()

    def _plan(self, chars=16000):
        with mock.patch.object(textbook_kp, "SEGMENT_CHARS", chars):
            return textbook_kp._plan(50, 1, db_path=self.dbpath)

    @staticmethod
    def _point(i=1):
        return {"title": "点%d" % i, "summary": "解释", "key_points": [],
                "memory_tip": "", "pitfall": "",
                "questions": [{"question": "q", "answer": "a"}], "source_ids": [1]}


class TestCoverage(_Base):
    def test_small_chapter_fields(self):
        self._chunk(1, "甲" * 100, "p.1")
        self._chunk(2, "乙" * 100, "p.2-4")
        plan = self._plan(16000)
        cov = plan["coverage"]
        self.assertEqual(cov["total_blocks"], 2)
        self.assertEqual(cov["segments_blocks"], 2)
        self.assertEqual(cov["dropped_blocks"], 0)
        self.assertEqual(cov["coverage_ratio"], 1.0)
        self.assertEqual(cov["page_range"], [1, 4])
        self.assertEqual(cov["total_pages"], 4)
        self.assertEqual(plan["total_blocks"], 2)

    def test_long_chapter_multiple_segments(self):
        for i in range(3):
            self._chunk(i + 1, "长" * 9000, "p.%d" % (i + 1))
        plan = self._plan(16000)
        self.assertGreaterEqual(len(plan["segments"]), 2)
        cov = plan["coverage"]
        self.assertEqual(cov["total_blocks"], 3)
        self.assertEqual(cov["segments_blocks"], 3)
        self.assertEqual(cov["segments_chars"], 27000)
        self.assertEqual(cov["dropped_blocks"], 0)
        self.assertEqual(cov["coverage_ratio"], 1.0)

    def test_empty_block_dropped_reason(self):
        self._chunk(1, "甲" * 10)
        self._chunk(2, "乙" * 10)
        self._chunk(3, "", "p.2")
        plan = self._plan(16000)
        cov = plan["coverage"]
        self.assertEqual(cov["total_blocks"], 3)
        self.assertEqual(cov["segments_blocks"], 2)
        self.assertEqual(cov["dropped_blocks"], 1)
        self.assertEqual(cov["dropped_reasons"], {"empty_text": 1})
        self.assertLess(cov["coverage_ratio"], 1.0)

    def test_no_page_ref_has_empty_range(self):
        self._chunk(1, "甲" * 10, "")
        plan = self._plan(16000)
        self.assertEqual(plan["coverage"]["page_range"], [])
        self.assertEqual(plan["coverage"]["total_pages"], 0)
        self.assertEqual(plan["coverage"]["coverage_ratio"], 1.0)

    def test_coverage_switch_off_old_fields_only(self):
        self._chunk(1, "甲" * 10)
        plan_on = self._plan(16000)
        with mock.patch.dict(os.environ, {"TEXTBOOK_KP_COVERAGE": "0"}):
            plan_off = self._plan(16000)
        self.assertIn("coverage", plan_on)
        self.assertNotIn("coverage", plan_off)
        self.assertEqual(plan_on["signature"], plan_off["signature"],
                         "覆盖统计不能改旧 signature")
        self.assertEqual(plan_on["segments"], plan_off["segments"])

    def test_estimate_and_artifact_only_add_coverage(self):
        self._chunk(1, "甲" * 10, "p.1")
        plan = self._plan(16000)
        est = textbook_kp.estimate(50, 1, db_path=self.dbpath, log_dir=self.logs)
        self.assertEqual(est["coverage"]["total_blocks"], 1)
        self.assertIn("signature", est)
        fake = {"points": [self._point()], "usage": {}, "partial": False,
                "invalid_points": 0, "finish_reason": "stop"}
        with mock.patch.object(textbook_kp, "_call", return_value=fake):
            res = textbook_kp._build(plan, force=True, db_path=self.dbpath,
                                     log_dir=self.logs, max_calls=5)
        self.assertEqual(res["status"], "done")
        final, _, _ = textbook_kp._paths(50, 1, self.logs)
        art = json.loads(final.read_text(encoding="utf-8"))
        self.assertEqual(art["coverage"]["total_blocks"], 1)
        self.assertIn("signature", art)
        self.assertEqual(art["points"][0]["title"], "点1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
