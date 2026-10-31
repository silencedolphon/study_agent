"""P0-H / GPT-H：outline 全局分析入口 RunContext 快照 + 输出 token 上限语义（零网络/零模型）。

GPT-H（2026-11-01）修的两个问题：
1. cap=0 时不能把内部默认 32768 塞进快照/请求（0 的语义是"客户端不发送 max_tokens"）；
2. cap>0 时第一次与**重试都不得翻倍**（未经用户确认不能多花）。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, outline  # noqa: E402
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
        # 默认：一次成功返回；重试用例可改成首轮空输出
        self.replies = None
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
        if self.replies is not None:
            content = self.replies.pop(0) if self.replies else "ok"
        else:
            content = "ok"
        return {"content": content, "model": kw.get("model") or "fake",
                "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    def _run(self, ctx, enabled=True):
        from core.pipeline_runtime import context as runtime_context
        with mock.patch.object(runtime_context, "runtime_enabled", return_value=enabled), \
             mock.patch.object(runtime_context, "make_context", return_value=ctx):
            return outline.analyze_deck(None, 7, Path("x.pptx"), "测试课件",
                                        pages=list(self.pages), force=True)

    # ---------------- 快照 ----------------
    def test_context_used_and_retry_gets_same_snapshot(self):
        out = self._run(_ctx())
        self.assertEqual(len(self.captured), 1)
        kw = self.captured[0]
        self.assertEqual(kw["model"], "ctx-model")
        self.assertEqual(kw["run_context"].model, "ctx-model")
        # GPT-H：cap=0 语义 = 不发送 max_tokens，**不再塞 32768**
        self.assertEqual(kw["run_context"].max_tokens, 0)
        self.assertEqual(kw["max_tokens"], 0)

    def test_runtime_disabled_legacy_model_and_no_context(self):
        out = self._run(_ctx(), enabled=False)
        kw = self.captured[0]
        self.assertNotIn("run_context", kw)
        self.assertNotEqual(kw.get("model"), "ctx-model")
        # 旧路径也按项目统一 cap 语义（来自管线中央设置，不再用内部 32768 冒充）
        self.assertEqual(int(kw["max_tokens"]),
                         max(0, int(config.token_cap("ingest_outline") or 0)))

    def test_context_cap_overrides_default_budget(self):
        out = self._run(_ctx(max_tokens=1234))
        kw = self.captured[0]
        self.assertEqual(kw["run_context"].max_tokens, 1234)
        self.assertEqual(kw["max_tokens"], 1234)

    def test_default_cap_kept_when_zero(self):
        """cap=0：快照与请求参数都保持 0（0=客户端不发送 max_tokens）。"""
        self._run(_ctx(max_tokens=0))
        kw = self.captured[0]
        self.assertEqual(kw["run_context"].max_tokens, 0)
        self.assertEqual(kw["max_tokens"], 0)

    # ---------------- GPT-H：重试不放大 ----------------
    def test_positive_cap_retry_not_doubled(self):
        """cap=1234：首轮空输出重试一次，两次都必须是 1234，不得 2468。"""
        self.replies = ["", "ok"]
        out = self._run(_ctx(max_tokens=1234))
        self.assertEqual(len(self.captured), 2)
        self.assertEqual([c["run_context"].max_tokens for c in self.captured],
                         [1234, 1234])
        self.assertEqual([c["max_tokens"] for c in self.captured], [1234, 1234])
        self.assertTrue(any("不放大" in w for w in out.warnings), out.warnings)
        self.assertFalse(any("2468" in w or "放大到 2468" in w for w in out.warnings))

    def test_zero_cap_retry_not_doubled(self):
        """cap=0：重试也不得从 0 变成 32768/任何非 0 值。"""
        self.replies = ["", "ok"]
        out = self._run(_ctx(max_tokens=0))
        self.assertEqual(len(self.captured), 2)
        self.assertEqual([c["run_context"].max_tokens for c in self.captured], [0, 0])
        self.assertEqual([c["max_tokens"] for c in self.captured], [0, 0])
        self.assertTrue(any("不发送 max_tokens" in w for w in out.warnings),
                        out.warnings)
        self.assertFalse(any("32768" in w for w in out.warnings), out.warnings)


if __name__ == "__main__":
    unittest.main()
