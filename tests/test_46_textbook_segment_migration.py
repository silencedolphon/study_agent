"""P3-B 验收：教材分段 v2（opt-in）+ 复习记录迁移联合门禁（零模型）。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import db, textbook_kp  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        import shutil as _sh
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(_sh.rmtree, self.tmp, ignore_errors=True)
        self.root = Path(self.tmp)
        self.logs = self.root / "logs"; self.logs.mkdir()
        self.dbpath = self.root / "t.db"
        self.conn = db.connect(self.dbpath, immediate=True)
        db.init_db(self.conn)
        self.conn.execute("INSERT INTO documents(id, path, abs_path, kind, title, "
                          "content_hash, role, n_chunks, n_chars, status) VALUES("
                          "50,'t.pdf','t.pdf','pdf','药理学','h','textbook',0,0,'ok')")
        self.conn.commit()
        self._patches = [mock.patch.dict(os.environ, {
            "TEXTBOOK_KP_FAST_PLAN": "1", "TEXTBOOK_KP_COVERAGE": "1",
            "TEXTBOOK_KP_SEGMENT_V2": "0", "PIPELINE_RUNTIME": "1"}),
            mock.patch.object(textbook_kp.config, "LOG_DIR", self.logs)]
        for p in self._patches:
            p.start()
        self._next_ord = 1

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.conn.close()

    def _chunk(self, text, ref="p.1", section="总论"):
        self.conn.execute(
            "INSERT INTO chunks(doc_id, ord, text, text_norm, heading_path, source_ref, "
            "char_len, token_est, content_hash, created_at) VALUES(50,?,?,?,?,?,?,?,?,0)",
            (self._next_ord, text, text,
             "药理学 > 第1章 总论 > " + section, ref, len(text), len(text) // 2,
             db.sha256_text(text)))
        self._next_ord += 1
        self.conn.commit()

    def _plan(self, env_v2=False):
        with mock.patch.dict(os.environ,
                             {"TEXTBOOK_KP_SEGMENT_V2": "1" if env_v2 else "0"}):
            return textbook_kp._plan(50, 1, db_path=self.dbpath)

    @staticmethod
    def _point(pid="p1", title="点", sources=None):
        return {"id": pid, "title": title, "summary": "s", "key_points": [],
                "memory_tip": "", "pitfall": "",
                "questions": [{"question": "q", "answer": "a"}],
                "source_ids": sources if sources is not None else [1]}

    def _fake_call(self):
        calls = []
        def fake(seg, plan, run_context=None):
            calls.append(1)
            return {"points": [self._point("n1", "点", seg["source_ids"])],
                    "usage": {}, "partial": False, "invalid_points": 0,
                    "finish_reason": "stop"}
        return calls, mock.patch.object(textbook_kp, "_call", side_effect=fake)


class TestSegmentV2(_Base):
    def _fixture(self):
        self._chunk("甲" * 100, "p.1", "第一节")
        self._chunk("乙" * 100, "p.2-3", "第一节")
        self._chunk("丙" * 100, "p.4", "第二节")

    def test_default_v1_old_structure_unchanged(self):
        self._fixture()
        plan = self._plan(env_v2=False)
        self.assertEqual(plan["segment_version"], "v1")
        for seg in plan["segments"]:
            self.assertEqual(set(seg.keys()), {"hash", "material", "source_ids"})
        fp1 = textbook_kp.settings_fingerprint()
        with mock.patch.dict(os.environ, {"TEXTBOOK_KP_SEGMENT_V2": "1"}):
            self.assertNotEqual(textbook_kp.settings_fingerprint(), fp1,
                                "fingerprint 必须纳入 segment version")

    def test_v2_segments_fields_and_signature(self):
        self._fixture()
        v1 = self._plan(env_v2=False)
        v2 = self._plan(env_v2=True)
        self.assertEqual(v2["segment_version"], "v2")
        self.assertNotEqual(v1["signature"], v2["signature"])
        for seg in v2["segments"]:
            for key in ("hash", "material", "source_ids", "pages",
                        "char_range", "block_ids", "sections"):
                self.assertIn(key, seg)
        self.assertEqual(v1["n_blocks"], v2["n_blocks"])

    def test_v2_no_char_or_block_loss(self):
        self._fixture()
        plan = self._plan(env_v2=True)
        used = set()
        block_ids = set()
        for seg in plan["segments"]:
            used.update(seg["source_ids"])
            block_ids.update(seg["block_ids"])
        self.assertEqual(used, block_ids)
        self.assertEqual(plan["coverage"]["segments_chars"], 300)
        self.assertEqual(plan["coverage"]["dropped_blocks"], 0)

    def test_v2_section_boundary(self):
        self._fixture()
        plan = self._plan(env_v2=True)
        for seg in plan["segments"]:
            self.assertEqual(len(seg["sections"]), 1,
                             "v2 不把不同节的块混进同一段")

    def test_v2_long_block_split_preserves_source(self):
        self._chunk("长" * 20000, "p.7", "第一节")
        plan = self._plan(env_v2=True)
        self.assertGreaterEqual(len(plan["segments"]), 2)
        self.assertEqual(plan["coverage"]["segments_blocks"], 1)
        self.assertEqual(plan["coverage"]["segments_chars"], 20000)
        self.assertEqual(plan["segments"][0]["source_ids"],
                         plan["segments"][-1]["source_ids"])

    def test_v2_not_cross_chapter(self):
        self._chunk("甲" * 50, "p.1", "第一节")
        self.conn.execute(
            "INSERT INTO chunks(doc_id, ord, text, text_norm, heading_path, source_ref, "
            "char_len, token_est, content_hash, created_at) VALUES("
            "50,?,?,?,?,?,?,?,?,0)",
            (99, "别章", "别章", "药理学 > 第2章 其他 > 第一节", "p.20",
             2, 1, db.sha256_text("别章")))
        self.conn.commit()
        plan = self._plan(env_v2=True)
        all_ids = {sid for seg in plan["segments"] for sid in seg["source_ids"]}
        self.assertNotIn(99, all_ids)
        self.assertEqual(len(all_ids), 1)

    def test_v2_only_explicit_force_generates(self):
        self._chunk("甲" * 50, "p.1", "第一节")
        plan = self._plan(env_v2=True)
        with mock.patch.dict(os.environ, {"TEXTBOOK_KP_SEGMENT_V2": "1"}):
            with self.assertRaises(ValueError):
                textbook_kp._build(plan, force=False, db_path=self.dbpath,
                                   log_dir=self.logs, max_calls=5)
            calls, fake = self._fake_call()
            with fake:
                res = textbook_kp._build(plan, force=True, db_path=self.dbpath,
                                         log_dir=self.logs, max_calls=5)
        self.assertEqual(res["status"], "done")
        final, _, _ = textbook_kp._paths(50, 1, self.logs)
        art = json.loads(final.read_text(encoding="utf-8"))
        self.assertEqual(art["segment_version"], "v2")
        self.assertEqual(len(calls), len(plan["segments"]))

    def test_v1_build_still_works_and_signature_stable(self):
        self._chunk("甲" * 50, "p.1", "第一节")
        plan = self._plan(env_v2=False)
        sig = plan["signature"]
        with mock.patch.dict(os.environ, {"TEXTBOOK_KP_SEGMENT_V2": "1"}):
            # v1 plan 已算出，不受环境切换影响（显式 v1）；force 仍能生成 v1
            calls, fake = self._fake_call()
            with fake:
                res = textbook_kp._build(plan, force=True, db_path=self.dbpath,
                                         log_dir=self.logs, max_calls=5)
        self.assertEqual(res["status"], "done")
        self.assertEqual(plan["signature"], sig)


class TestReviewMigration(_Base):
    def _old(self):
        return [{"id": "o1", "title": "药理学 总论", "source_ids": [1, 2]},
                {"id": "o2", "title": "药物代谢", "source_ids": [3]}]

    def _new(self):
        return [{"id": "n1", "title": "药理学总论", "source_ids": [2, 1]},
                {"id": "n2", "title": "药物代谢", "source_ids": [3]},
                {"id": "n3", "title": "无关章节", "source_ids": [9]}]

    def _reviews(self):
        return {"o1": {"stage": 2, "due": "2026-11-01", "last_review": "2026-10-25",
                       "remembered": True},
                "o2": {"stage": 1, "due": "2026-10-30", "last_review": "2026-10-20",
                       "remembered": False}}

    def test_unique_best_migrates_fields_exactly(self):
        res = textbook_kp.migrate_reviews(self._old(), self._new(), self._reviews())
        self.assertEqual(set(res["reviews"]), {"n1", "n2"})
        self.assertEqual(res["reviews"]["n1"], self._reviews()["o1"])
        self.assertEqual(res["reviews"]["n2"], self._reviews()["o2"])
        self.assertEqual(res["ambiguous"], [])
        self.assertEqual(res["unmatched"], [])

    def test_ambiguous_not_migrated(self):
        new = [{"id": "a", "title": "药理总论", "source_ids": [1]},
               {"id": "b", "title": "药理总论", "source_ids": [1]}]
        res = textbook_kp.migrate_reviews(
            [{"id": "o1", "title": "药理总论", "source_ids": [1]}], new,
            {"o1": {"stage": 1}})
        self.assertEqual(res["reviews"], {})
        self.assertEqual(len(res["ambiguous"]), 1)
        self.assertEqual(res["ambiguous"][0]["old_id"], "o1")

    def test_no_match_unmatched(self):
        res = textbook_kp.migrate_reviews(
            [{"id": "o1", "title": "完全无关", "source_ids": [8]}],
            [{"id": "n1", "title": "药理学总论", "source_ids": [1]}],
            {"o1": {"stage": 3}})
        self.assertEqual(res["reviews"], {})
        self.assertEqual(res["unmatched"], ["o1"])

    def test_unknown_old_point_unmatched(self):
        res = textbook_kp.migrate_reviews(
            [{"id": "o1", "title": "x", "source_ids": [1]}],
            [{"id": "n1", "title": "y", "source_ids": [1]}],
            {"o9": {"stage": 1}})
        self.assertIn("o9", res["unmatched"])

    def test_cross_chapter_isolation(self):
        res = textbook_kp.migrate_reviews(
            self._old(), self._new(), self._reviews(),
            old_chapter=1, new_chapter=2)
        self.assertTrue(res["cross_chapter"])
        self.assertEqual(res["reviews"], {})
        self.assertEqual(res["migrated"], [])

    def test_v2_build_backup_and_migrate_reviews(self):
        self._chunk("甲" * 50, "p.1", "第一节")
        plan = self._plan(env_v2=True)
        old_point = {"id": "old1", "title": "药理学 总论",
                     "summary": "旧", "source_ids": [1]}
        final, _, review = textbook_kp._paths(50, 1, self.logs)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text(json.dumps({
            "version": textbook_kp.VERSION, "status": "done",
            "signature": "old-sig", "doc_id": 50, "chapter": 1,
            "points": [old_point]}), encoding="utf-8")
        review.write_text(json.dumps({
            "version": textbook_kp.VERSION, "doc_id": 50, "chapter": 1,
            "points": {"old1": {"stage": 2, "due": "2026-11-01",
                                "last_review": "2026-10-25",
                                "remembered": True}}}), encoding="utf-8")
        calls = []
        def fake(seg, pl, run_context=None):
            calls.append(seg)
            return {"points": [{"id": "new1", "title": "药理学总论",
                                "summary": "新", "key_points": [],
                                "questions": [], "source_ids": [1]}],
                    "usage": {}, "partial": False, "invalid_points": 0,
                    "finish_reason": "stop"}
        # v1 旧产物在 v2 plan 之前写入；plan 是新 v2（签名不同）
        with mock.patch.dict(os.environ, {"TEXTBOOK_KP_SEGMENT_V2": "1"}), \
             mock.patch.object(textbook_kp, "_call", side_effect=fake):
            res = textbook_kp._build(plan, force=True, db_path=self.dbpath,
                                     log_dir=self.logs, max_calls=5)
        self.assertEqual(res["status"], "done")
        backups = list(review.parent.glob(review.name + ".bak-*"))
        self.assertEqual(len(backups), 1, "旧 review 必须先备份")
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8"))["points"],
                         {"old1": {"stage": 2, "due": "2026-11-01",
                                   "last_review": "2026-10-25",
                                   "remembered": True}})
        data = json.loads(review.read_text(encoding="utf-8"))
        self.assertIn("new1", data["points"])
        self.assertNotIn("old1", data["points"], "旧点 ID 不再写进新 review")
        self.assertEqual(data["migrated"][0]["old_id"], "old1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
