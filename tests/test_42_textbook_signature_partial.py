"""P2-B 验收：教材实际配置签名（不改旧 signature）+ partial 有效点保留。

零模型：fake chat/_call；临时 DB/目录。覆盖旧产物可读、不自动重跑、续跑只补缺段。
"""
from __future__ import annotations

import contextlib
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
from core import config, db, kp_build, textbook_kp  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.dbpath = self.root / "t.db"
        self.conn = db.connect(self.dbpath, immediate=True)
        db.init_db(self.conn)
        self.conn.execute(
            "INSERT INTO documents(id, path, abs_path, kind, title, content_hash, "
            "role, n_chunks, n_chars, status) VALUES(50,'t.pdf','t.pdf','pdf','药理学',"
            "'h','textbook',2,20,'ok')")
        self._chunk(1, "甲" * 80, "p.1")
        self._chunk(2, "乙" * 80, "p.2")
        self.conn.commit()
        self.settings = {}
        def value_of(key, default=None):
            if key in self.settings:
                return self.settings[key]
            try:
                return config._FIELDS_BY_KEY[key]["default"]
            except Exception:  # noqa: BLE001
                return default
        def value_int(key, default=0):
            try:
                return int(value_of(key, default))
            except (TypeError, ValueError):
                return default
        def token_cap(pipeline):
            try:
                return max(0, int(self.settings.get("TEXTBOOK_KP_MAX_TOKENS", 0)))
            except (TypeError, ValueError):
                return 0
        self._patches = [
            mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "1",
                                          "TEXTBOOK_KP_FAST_PLAN": "1"}),
            mock.patch.object(config, "LOG_DIR", self.logs),
            mock.patch.object(config, "DB_PATH", self.dbpath),
            mock.patch.object(config, "value_of", side_effect=value_of),
            mock.patch.object(config, "value_int", side_effect=value_int),
            mock.patch.object(config, "token_cap", side_effect=token_cap),
            mock.patch.object(config, "pipeline_key", return_value=""),
            mock.patch.object(textbook_kp.chat, "load_api_key", return_value="sk-fake"),
        ]
        for p in self._patches:
            p.start()
        self._set(MODEL="deepseek-flash", BASE="https://api.deepseek.com",
                  THINKING="1", EFFORT="max", MAX="0")

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def _chunk(self, ord_, text, ref):
        self.conn.execute(
            "INSERT INTO chunks(doc_id, ord, text, text_norm, heading_path, source_ref, "
            "char_len, token_est, content_hash, created_at) VALUES(50,?,?,?,?,?,?,?,?,0)",
            (ord_, text, text, "药理学 > 第1章 总论", ref, len(text), len(text) // 2,
             db.sha256_text(text)))

    def _set(self, *, MODEL="deepseek-flash", BASE="https://api.deepseek.com",
             THINKING="1", EFFORT="max", MAX="0"):
        self.settings.update({"TEXTBOOK_KP_MODEL": MODEL,
                              "TEXTBOOK_KP_BASE_URL": BASE,
                              "TEXTBOOK_KP_THINKING": THINKING,
                              "TEXTBOOK_KP_REASONING_EFFORT": EFFORT,
                              "TEXTBOOK_KP_MAX_TOKENS": MAX})

    def _plan(self, chars=100):
        with mock.patch.object(textbook_kp, "SEGMENT_CHARS", chars):
            return textbook_kp._plan(50, 1, db_path=self.dbpath)

    @staticmethod
    def _point(i=1, **kw):
        p = {"title": "点%d" % i, "summary": "解释",
             "key_points": ["k"], "memory_tip": "", "pitfall": "",
             "questions": [{"question": "q", "answer": "a"}],
             "source_ids": [1]}
        p.update(kw)
        return p

    def _content(self, points):
        return json.dumps({"points": points}, ensure_ascii=False)

    def _wire_call(self, content):
        calls = []
        def fake_invoke(fn, timeout, kind):
            calls.append(1)
            return {"content": content, "usage": {"prompt_tokens": 1,
                                                 "completion_tokens": 2},
                    "model": "m", "finish_reason": "stop"}
        return calls, mock.patch.object(kp_build, "model_slot",
                                        return_value=contextlib.nullcontext()), \
            mock.patch.object(kp_build, "_call_with_hard_timeout", side_effect=fake_invoke)


class TestSettingsFingerprint(_Base):
    def test_fingerprint_changes_with_actual_settings_no_key(self):
        fp1 = textbook_kp.settings_fingerprint()
        self.assertRegex(fp1, r"^[0-9a-f]{64}$")
        for kwargs in ({"MODEL": "other"}, {"BASE": "https://b.example"},
                       {"THINKING": "0"}, {"EFFORT": "high"}, {"MAX": "4096"}):
            self._set(**kwargs)
            self.assertNotEqual(textbook_kp.settings_fingerprint(), fp1, kwargs)
        self._set()
        self.assertEqual(textbook_kp.settings_fingerprint(), fp1)

    def test_plan_config_fingerprint_changes_but_old_signature_stable(self):
        plan1 = self._plan(16000)
        self.assertTrue(plan1.get("config_fingerprint"))
        self._set(MODEL="other-model")
        plan2 = self._plan(16000)
        self.assertEqual(plan1["signature"], plan2["signature"],
                         "旧 signature 算法/值不能因设置变化而变")
        self.assertNotEqual(plan1["config_fingerprint"], plan2["config_fingerprint"])

    def test_artifact_meta_only_adds_fields(self):
        plan = self._plan(16000)
        calls, slot, hard = self._wire_call(self._content([self._point()]))
        with slot, hard:
            result = textbook_kp._build(plan, force=True, db_path=self.dbpath,
                                        log_dir=self.logs, max_calls=5)
        self.assertEqual(result["status"], "done")
        final, _, _ = textbook_kp._paths(50, 1, self.logs)
        art = json.loads(final.read_text(encoding="utf-8"))
        for key in ("version", "status", "signature", "doc_id", "chapter", "points"):
            self.assertIn(key, art, "旧字段必须保留")
        self.assertEqual(art["config_fingerprint"], plan["config_fingerprint"])
        self.assertEqual(art["prompt_version"], textbook_kp.PROMPT_VERSION)
        self.assertEqual(art["schema_version"], textbook_kp.SCHEMA_VERSION)

    def test_read_summary_old_artifact_unknown_and_no_auto_rerun(self):
        final, _, _ = textbook_kp._paths(50, 1, self.logs)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text(json.dumps({"version": "textbook-kp-v1", "status": "done",
                                     "signature": self._plan(16000)["signature"],
                                     "doc_id": 50, "chapter": 1,
                                     "points": [{"id": "old1", "title": "旧点"}]}),
                         encoding="utf-8")
        with mock.patch.object(textbook_kp, "_call",
                               side_effect=AssertionError("不得自动重跑")):
            res = textbook_kp.read_summary(50, 1, log_dir=self.logs,
                                           db_path=self.dbpath)
        self.assertTrue(res["ok"])
        self.assertTrue(res["settings_unknown"], "旧产物无 fingerprint = unknown")
        self.assertFalse(res["settings_changed"])

    def test_read_summary_settings_changed_only_hint(self):
        plan = self._plan(16000)
        calls, slot, hard = self._wire_call(self._content([self._point()]))
        with slot, hard:
            textbook_kp._build(plan, force=True, db_path=self.dbpath,
                               log_dir=self.logs, max_calls=5)
        res = textbook_kp.read_summary(50, 1, log_dir=self.logs, db_path=self.dbpath)
        self.assertFalse(res["settings_unknown"])
        self.assertFalse(res["settings_changed"])
        self._set(MODEL="changed-model")
        res2 = textbook_kp.read_summary(50, 1, log_dir=self.logs, db_path=self.dbpath)
        self.assertTrue(res2["settings_changed"], "设置变化只提示，不自动重跑")


class TestPartial(_Base):
    def test_call_keeps_valid_points_and_marks_partial(self):
        content = self._content([self._point(1), {"title": "缺正文"}, "不是对象"])
        calls, slot, hard = self._wire_call(content)
        segment = {"hash": "h1", "source_ids": [1], "material": "m"}
        with slot, hard:
            part = textbook_kp._call(segment, {"title": "t", "chapter": 1,
                                               "chapter_name": ""})
        self.assertTrue(part["partial"])
        self.assertEqual(part["invalid_points"], 2)
        self.assertEqual(len(part["points"]), 1)
        self.assertEqual(len(calls), 1)

    def test_call_severe_errors_fail_and_never_retry(self):
        segment = {"hash": "h1", "source_ids": [1], "material": "m"}
        for content, finish in ((json.dumps({"points": []}), "stop"),
                                ("", "stop"),
                                ("not json", "stop"),
                                (json.dumps({"points": [self._point()]}), "length")):
            calls = []
            def fake_invoke(fn, timeout, kind, c=content, f=finish):
                calls.append(1)
                return {"content": c, "usage": {"completion_tokens": 1},
                        "model": "m", "finish_reason": f}
            with mock.patch.object(kp_build, "model_slot",
                                   return_value=contextlib.nullcontext()), \
                 mock.patch.object(kp_build, "_call_with_hard_timeout",
                                   side_effect=fake_invoke):
                with self.assertRaises(textbook_kp.SegmentOutputError):
                    textbook_kp._call(segment, {"title": "t", "chapter": 1,
                                                "chapter_name": ""})
            self.assertEqual(len(calls), 1, "严重失败不得自动重试/修 JSON")

    def test_build_partial_status_keeps_all_valid_points(self):
        plan = self._plan(100)
        self.assertGreaterEqual(len(plan["segments"]), 2)
        parts = {}
        for i, seg in enumerate(plan["segments"]):
            parts[seg["hash"]] = {
                "points": [self._point(i + 1)], "usage": {}, "model": "m",
                "thinking": True, "reasoning_effort": "max", "output_cap": 0,
                "finish_reason": "stop",
                "partial": i == 0, "invalid_points": 2 if i == 0 else 0}
        calls = []
        def fake_call(seg, pl, run_context=None):
            calls.append(seg["hash"])
            return parts[seg["hash"]]
        with mock.patch.object(textbook_kp, "_call", side_effect=fake_call):
            res = textbook_kp._build(plan, force=True, db_path=self.dbpath,
                                     log_dir=self.logs, max_calls=10)
        self.assertEqual(res["status"], "partial", "partial 不能冒充 done")
        self.assertEqual(res["invalid_points"], 2)
        final, pending, _ = textbook_kp._paths(50, 1, self.logs)
        art = json.loads(final.read_text(encoding="utf-8"))
        self.assertEqual(art["status"], "partial")
        self.assertEqual(len(art["points"]), len(plan["segments"]))
        self.assertFalse(pending.exists())
        self.assertIn("parts", art, "partial 产物带分段缓存用于续跑")
        self.assertEqual(len(calls), len(plan["segments"]))

    def test_partial_resume_only_missing_segment(self):
        plan = self._plan(100)
        self.assertGreaterEqual(len(plan["segments"]), 2)
        final, pending, _ = textbook_kp._paths(50, 1, self.logs)
        first_hash = plan["segments"][0]["hash"]
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text(json.dumps({
            "version": textbook_kp.VERSION, "status": "partial",
            "signature": plan["signature"], "doc_id": 50, "chapter": 1,
            "title": plan["title"], "chapter_name": plan["chapter_name"],
            "config_fingerprint": plan["config_fingerprint"],
            "points": [self._point(1)],
            "parts": {first_hash: {"points": [self._point(1)], "partial": True,
                                   "invalid_points": 1, "usage": {},
                                   "finish_reason": "stop"}}}), encoding="utf-8")
        calls = []
        def fake_call(seg, pl, run_context=None):
            calls.append(seg["hash"])
            return {"points": [self._point(2)], "usage": {}, "partial": False,
                    "invalid_points": 0, "finish_reason": "stop"}
        with mock.patch.object(textbook_kp, "_call", side_effect=fake_call):
            res = textbook_kp._build(plan, force=False, db_path=self.dbpath,
                                     log_dir=self.logs, max_calls=10)
        self.assertEqual(len(calls), len(plan["segments"]) - 1,
                         "续跑只补缺段（已成功段不重调）")
        self.assertIn(res["status"], ("done", "partial"))

    def test_build_failure_keeps_pending_and_no_retry(self):
        plan = self._plan(16000)
        calls = []
        def boom(*a, **kw):
            calls.append(1)
            raise textbook_kp.SegmentOutputError("bad", {}, "length")
        with mock.patch.object(textbook_kp, "_call", side_effect=boom):
            with self.assertRaises(textbook_kp.SegmentOutputError):
                textbook_kp._build(plan, force=True, db_path=self.dbpath,
                                   log_dir=self.logs, max_calls=5)
        self.assertEqual(len(calls), 1, "失败不自动付费重试")
        _, pending, _ = textbook_kp._paths(50, 1, self.logs)
        data = json.loads(pending.read_text(encoding="utf-8"))
        self.assertEqual(len(data.get("attempts") or []), 1)

    def test_partial_artifact_read_summary_and_old_fields(self):
        plan = self._plan(100)
        final, _, review = textbook_kp._paths(50, 1, self.logs)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text(json.dumps({
            "version": textbook_kp.VERSION, "status": "partial",
            "signature": plan["signature"], "doc_id": 50, "chapter": 1,
            "title": plan["title"], "chapter_name": plan["chapter_name"],
            "config_fingerprint": plan["config_fingerprint"],
            "points": [self._point(1), self._point(2)],
            "reviews": {}}), encoding="utf-8")
        res = textbook_kp.read_summary(50, 1, log_dir=self.logs, db_path=self.dbpath)
        self.assertTrue(res["exists"])
        self.assertEqual(res["status"], "partial")
        self.assertEqual(len(res["points"]), 2)
        self.assertFalse(res["settings_unknown"])
        self.assertIn("reviews", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
