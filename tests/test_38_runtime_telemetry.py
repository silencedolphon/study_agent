"""P0-A / S02：telemetry 最小阶段记录（JSONL）+ textbook_kp plan/segment_call/write。

零模型：fake `_call` / 临时目录；只验证字段、计时、无敏感内容、未知 usage ≠ 0、开关回退。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, textbook_kp  # noqa: E402
from core.pipeline_runtime import context as runtime_context, telemetry  # noqa: E402

_ALLOWED = {"ts", "run_id", "pipeline", "subject", "doc_id", "stage", "status",
            "work_ms", "cache_hit", "calls", "input_tokens", "output_tokens",
            "reasoning_tokens", "finish_reason", "error_class"}


def _ctx(**kw):
    base = {"run_id": "run-t38", "pipeline": "textbook_kp",
            "subject_id": "默认", "doc_id": 50, "max_tokens": 0}
    base.update(kw)
    return SimpleNamespace(**base)


class _TelemetryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / "telemetry.jsonl"
        self._patches = [
            mock.patch.dict(os.environ, {
                "PIPELINE_RUNTIME": "1", "RUNTIME_TELEMETRY": "1",
                "RUNTIME_TELEMETRY_FILE": str(self.file)}),
            mock.patch.object(config, "LOG_DIR", self.root / "logs"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _records(self):
        return telemetry.read_records(self.file)


class TestTelemetryRecord(_TelemetryBase):
    def test_fields_and_whitelist(self):
        rec = telemetry.record("plan", run_context=_ctx(), status="ok",
                               work_ms=12.34, cache_hit=True, calls=2,
                               input_tokens=10, output_tokens=20,
                               reasoning_tokens=5, finish_reason="stop")
        self.assertIsNotNone(rec)
        recs = self._records()
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(set(rec.keys()), _ALLOWED, "只能落白名单字段")
        self.assertEqual((rec["run_id"], rec["pipeline"], rec["subject"]),
                         ("run-t38", "textbook_kp", "默认"))
        self.assertEqual(rec["stage"], "plan")
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(rec["work_ms"], 12.3)
        self.assertTrue(rec["cache_hit"])
        self.assertEqual(rec["calls"], 2)
        self.assertEqual((rec["input_tokens"], rec["output_tokens"],
                          rec["reasoning_tokens"]), (10, 20, 5))
        self.assertEqual(rec["finish_reason"], "stop")

    def test_no_sensitive_content(self):
        text = "这是一段教材正文，绝不应该落盘"
        key = "sk-abcdefghijklmnopqrstuvwxyz"
        telemetry.record("segment_call", run_context=_ctx(), status="ok",
                         input_tokens=1, output_tokens=2)
        raw = self.file.read_text(encoding="utf-8")
        self.assertNotIn(text, raw)
        self.assertNotIn(key, raw)
        lowered = raw.lower()
        for banned in ("api_key", "authorization", "messages", "prompt\":",
                       "reasoning_content", "content\":", "sk-"):
            self.assertNotIn(banned, lowered)

    def test_unknown_usage_not_zero(self):
        telemetry.record("segment_call", run_context=_ctx(), status="failed",
                         error_class="TimeoutError", input_tokens=None,
                         output_tokens=None, reasoning_tokens=None,
                         finish_reason="")
        rec = self._records()[0]
        self.assertIsNone(rec["input_tokens"], "未知 usage 必须是 null 而不是 0")
        self.assertIsNone(rec["output_tokens"])
        self.assertIsNone(rec["reasoning_tokens"])
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(rec["error_class"], "TimeoutError")
        self.assertIsNone(rec["work_ms"])

    def test_usage_tokens_helper(self):
        tokens = telemetry.usage_tokens({
            "prompt_tokens": 7, "completion_tokens": 9,
            "completion_tokens_details": {"reasoning_tokens": 3}})
        self.assertEqual(tokens, {"input_tokens": 7, "output_tokens": 9,
                                  "reasoning_tokens": 3})
        empty = telemetry.usage_tokens({})
        self.assertEqual(empty, {"input_tokens": None, "output_tokens": None,
                                 "reasoning_tokens": None})

    def test_switches_disable_telemetry(self):
        ctx = _ctx()
        telemetry.record("plan", run_context=ctx)
        self.assertEqual(len(self._records()), 1)
        with mock.patch.dict(os.environ, {"RUNTIME_TELEMETRY": "0"}):
            self.assertIsNone(telemetry.record("plan", run_context=ctx))
            self.assertFalse(telemetry.enabled())
        with mock.patch.dict(os.environ, {"RUNTIME_TELEMETRY": "1",
                                          "PIPELINE_RUNTIME": "0"}):
            self.assertIsNone(telemetry.record("plan", run_context=ctx))
            self.assertFalse(telemetry.enabled())
        self.assertEqual(len(self._records()), 1, "开关关闭不该再写")
        self.assertIsNone(telemetry.record("plan"), "无 context 不写")


class TestTextbookPilotTelemetry(_TelemetryBase):
    def _plan(self, n=2):
        return {"doc_id": 50, "chapter": 6, "title": "药理学",
                "chapter_name": "胆碱受体药", "signature": "sig",
                "segments": [{"hash": "h%d" % i, "material": "m",
                              "source_ids": [i + 1]} for i in range(n)]}

    @staticmethod
    def _part():
        return {"points": [{"id": "p1", "title": "t", "summary": "s",
                            "key_points": [], "memory_tip": "", "pitfall": "",
                            "questions": [], "source_ids": [1]}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 22,
                          "completion_tokens_details": {"reasoning_tokens": 3}},
                "model": "m", "thinking": True, "reasoning_effort": "max",
                "output_cap": 0, "finish_reason": "stop"}

    def test_build_records_plan_segment_call_write(self):
        logs = self.root / "logs"
        ctx = _ctx(max_tokens=0)
        with mock.patch.object(textbook_kp, "_call",
                               return_value=self._part()):
            result = textbook_kp._build(self._plan(2), force=True,
                                        db_path=self.root / "x.db",
                                        log_dir=logs, max_calls=2,
                                        run_context=ctx, plan_ms=5.5)
        self.assertEqual(result["status"], "done")
        recs = self._records()
        stages = [r["stage"] for r in recs]
        self.assertEqual(stages, ["plan", "segment_call", "segment_call", "write"])
        plan = recs[0]
        self.assertEqual(plan["work_ms"], 5.5)
        self.assertEqual(plan["calls"], 2)
        calls = [r for r in recs if r["stage"] == "segment_call"]
        self.assertEqual(len(calls), 2)
        for rec in calls:
            self.assertEqual(rec["calls"], 1)
            self.assertEqual((rec["input_tokens"], rec["output_tokens"],
                              rec["reasoning_tokens"]), (11, 22, 3))
            self.assertEqual(rec["status"], "ok")
            self.assertGreaterEqual(rec["work_ms"], 0.0)
        self.assertEqual(recs[-1]["status"], "ok")

    def test_build_failure_records_failed_segment(self):
        logs = self.root / "logs"
        with mock.patch.object(textbook_kp, "_call",
                               side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                textbook_kp._build(self._plan(1), force=True,
                                   db_path=self.root / "x.db",
                                   log_dir=logs, max_calls=1,
                                   run_context=_ctx(), plan_ms=1.0)
        recs = self._records()
        self.assertEqual([r["stage"] for r in recs], ["plan", "segment_call"])
        self.assertEqual(recs[-1]["status"], "failed")
        self.assertEqual(recs[-1]["error_class"], "ValueError")

    def test_runtime_disabled_no_telemetry(self):
        logs = self.root / "logs"
        with mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "0"}), \
             mock.patch.object(textbook_kp, "max_output_tokens", return_value=0), \
             mock.patch.object(textbook_kp, "_call",
                               return_value=self._part()):
            textbook_kp._build(self._plan(1), force=True,
                               db_path=self.root / "x.db", log_dir=logs,
                               max_calls=1, run_context=_ctx())
        self.assertFalse(self.file.exists(), "PIPELINE_RUNTIME=0 不写 telemetry")

    def test_start_creates_context_and_progress_run_id(self):
        logs = self.root / "logs"
        plan = self._plan(1)
        captured = {}

        def fake_build(*a, **kw):
            captured.update(kw)
            return {"ok": True, "status": "done"}

        with mock.patch.object(textbook_kp.config, "DB_PATH", self.root / "x.db"), \
             mock.patch.object(textbook_kp.config, "LOG_DIR", logs), \
             mock.patch.object(textbook_kp.runtime_context, "make_context",
                               return_value=_ctx()), \
             mock.patch.object(textbook_kp, "_plan", return_value=plan), \
             mock.patch.object(textbook_kp, "estimate",
                               return_value={"calls": 1,
                                             "estimated_output_cap": 0}), \
             mock.patch.object(textbook_kp, "_build", side_effect=fake_build):
            r = textbook_kp.start(50, 6, "sig", max_calls=1, max_output_tokens=0)
            self.assertTrue(r.get("started"), r)
            import time
            for _ in range(100):
                pr = textbook_kp.progress()
                if not pr["active"]:
                    break
                time.sleep(0.01)
        self.assertIn("run_context", captured)
        self.assertIsNotNone(captured["run_context"])
        self.assertEqual(captured["run_context"].pipeline, "textbook_kp")
        self.assertTrue(textbook_kp.progress()["run_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
