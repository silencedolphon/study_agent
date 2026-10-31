"""P0-H：outline 全局分析入口 RunContext 快照回归（零网络/零模型）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import outline  # noqa: E402
from core.pipeline_runtime.context import RunContext  # noqa: E402


def _ctx(**kw):
    base = dict(
        run_id="outline-test", pipeline="ingest_outline", subject_id="default",
        db_path=Path("."), log_dir=Path("."), model="ctx-model",
        api_base="https://ctx.example", thinking=True, reasoning_effort="max",
        max_tokens=0, prompt_version="", schema_version="",
        credential_ref="ingest_outline", doc_id=7,
    )
    base.update(kw)
    return RunContext(**base)


class TestOutlineRunContext(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "outline.json"
        self.captured = []
        self.pages = [{"page": 1, "title": "甲", "body": "乙",
                       "figure": "", "text": "乙", "kind": "课件"}]
        self.patches = [
            mock.patch.object(outline, "_cache_path", lambda doc_id: self.cache),
            mock.patch.object(outline, "_parse", return_value=({1: ["甲"]}, [])),
            mock.patch("core.chat.chat_once", side_effect=self._chat),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _chat(self, messages, **kw):
        self.captured.append(kw)
        return {"content": "ok", "model": kw.get("model") or "fake",
                "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    def _run(self, ctx, enabled=True):
        from core.pipeline_runtime import context as runtime_context
        with mock.patch.object(runtime_context, "runtime_enabled", return_value=enabled), \
             mock.patch.object(runtime_context, "make_context", return_value=ctx):
            return outline.analyze_deck(None, 7, Path("x.pptx"), "测试课件",
                                        pages=list(self.pages), force=True)

    def test_context_used_and_retry_gets_same_snapshot(self):
        out = self._run(_ctx())
        self.assertEqual(len(self.captured), 1)
        kw = self.captured[0]
        self.assertEqual(kw["model"], "ctx-model")
        self.assertEqual(kw["run_context"].model, "ctx-model")
        self.assertEqual(kw["run_context"].max_tokens, 32768)

    def test_runtime_disabled_legacy_model_and_no_context(self):
        out = self._run(_ctx(), enabled=False)
        kw = self.captured[0]
        self.assertNotIn("run_context", kw)
        self.assertNotEqual(kw.get("model"), "ctx-model")

    def test_context_cap_overrides_default_budget(self):
        out = self._run(_ctx(max_tokens=1234))
        kw = self.captured[0]
        self.assertEqual(kw["run_context"].max_tokens, 1234)

    def test_default_cap_kept_when_zero(self):
        self._run(_ctx(max_tokens=0))
        self.assertEqual(self.captured[0]["run_context"].max_tokens, 32768)


if __name__ == "__main__":
    unittest.main()
