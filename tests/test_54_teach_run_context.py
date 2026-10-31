"""P0-E 验收：teach 教材补充 RunContext 快照隔离（零模型/零网络）。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import teach  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "1"}),
            mock.patch.object(teach, "_kp_model", return_value="legacy-model"),
            mock.patch.object(teach, "_kp_thinking", return_value=False),
            mock.patch.object(teach, "_kp_effort", return_value="low"),
            mock.patch.object(teach, "_kp_max_tokens", return_value=1234),
            mock.patch.object(teach, "_ai_enabled", return_value=True),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _ctx(self, model="snap-model", cap=4096, thinking=True, effort="max"):
        return SimpleNamespace(run_id="r", pipeline="textbook_teach", subject_id="默认",
                               doc_id=50, model=model, api_base="https://ctx.example",
                               thinking=thinking, reasoning_effort=effort,
                               max_tokens=cap, credential_ref="textbook_teach")

    def _fake_chat(self, side_effect=None):
        calls = []
        def fn(messages, **kw):
            calls.append(kw)
            if side_effect is not None:
                return side_effect(len(calls), kw)
            return {"content": json.dumps({"ok": 1}), "model": kw.get("model"),
                    "usage": {}, "finish_reason": "stop"}
        return calls, mock.patch("core.chat.chat_once", side_effect=fn)


class TestAiChatSnapshot(_Base):
    def test_context_snapshot_overrides_globals(self):
        ctx = self._ctx()
        calls, chat = self._fake_chat()
        with chat:
            res = teach._ai_chat("teach.map", "sys", "user",
                                 log=lambda *a: None, run_context=ctx)
        self.assertTrue(res["ok"])
        kw = calls[0]
        self.assertEqual((kw["model"], kw["max_tokens"], kw["thinking"]),
                         ("snap-model", 4096, True))
        self.assertEqual(kw["reasoning_effort"], "max")
        self.assertIs(kw["run_context"], ctx)
        self.assertEqual(res["model"], "snap-model")

    def test_no_context_old_path(self):
        calls, chat = self._fake_chat()
        with chat:
            res = teach._ai_chat("teach.map", "sys", "user", log=lambda *a: None)
        self.assertEqual((calls[0]["model"], calls[0]["max_tokens"],
                          calls[0]["thinking"]), ("legacy-model", 1234, False))
        self.assertIsNone(calls[0]["run_context"])

    def test_runtime_disabled_old_path(self):
        ctx = self._ctx()
        calls, chat = self._fake_chat()
        with mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "0"}), chat:
            teach._ai_chat("teach.map", "sys", "user", log=lambda *a: None,
                           run_context=ctx)
        self.assertEqual((calls[0]["model"], calls[0]["max_tokens"]),
                         ("legacy-model", 1234))
        self.assertIsNone(calls[0]["run_context"], "回退旧路径不传 context")

    def test_config_switch_after_context_no_leak(self):
        ctx = self._ctx()
        with mock.patch.object(teach, "_kp_model", return_value="changed"), \
             mock.patch.object(teach, "_kp_max_tokens", return_value=7), \
             mock.patch.object(teach, "_kp_thinking", return_value=True), \
             mock.patch.object(teach, "_kp_effort", return_value="high"):
            calls, chat = self._fake_chat()
            with chat:
                teach._ai_chat("teach.map", "sys", "user", log=lambda *a: None,
                               run_context=ctx)
        self.assertEqual((calls[0]["model"], calls[0]["max_tokens"]),
                         ("snap-model", 4096))

    def test_retry_each_attempt_same_context(self):
        ctx = self._ctx()
        seen = []

        def side(n, kw):
            seen.append(kw.get("run_context"))
            if n == 1:
                raise RuntimeError("first fail")
            return {"content": json.dumps({"ok": 1}),
                    "model": kw.get("model"), "usage": {}, "finish_reason": "stop"}

        calls, chat = self._fake_chat(side_effect=side)
        with mock.patch.object(teach.time, "sleep", return_value=None), chat:
            res = teach._ai_chat("teach.map", "sys", "user", log=lambda *a: None,
                                 run_context=ctx)
        self.assertTrue(res["ok"])
        self.assertEqual(len(seen), 2)
        for got in seen:
            self.assertIs(got, ctx, "每次重试必须用同一个 context")

    def test_two_threads_isolated(self):
        ctx1, ctx2 = self._ctx("m1", 11), self._ctx("m2", 22)
        captured = []
        lock = threading.Lock()

        def fake(messages, **kw):
            with lock:
                captured.append((kw["model"], kw["max_tokens"]))
            return {"content": json.dumps({"ok": 1}), "model": kw["model"],
                    "usage": {}, "finish_reason": "stop"}

        def worker(ctx):
            with mock.patch("core.chat.chat_once", side_effect=fake):
                teach._ai_chat("teach.map", "sys", "user", log=lambda *a: None,
                               run_context=ctx)

        ts = [threading.Thread(target=worker, args=(c,)) for c in (ctx1, ctx2)]
        for t in ts: t.start()
        for t in ts: t.join(10)
        self.assertEqual(sorted(captured), [("m1", 11), ("m2", 22)])


class TestEntryAndEstimate(_Base):
    def _fake_conn(self):
        return SimpleNamespace(execute=lambda *a, **kw: None, close=lambda: None)

    def test_build_supplement_creates_context_once_and_meta_snapshot(self):
        ctx = self._ctx()
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "supp.json"
            with mock.patch.object(teach, "_courseware_kps",
                                  return_value=(Path("kp.json"), [{"id": "KP-1"}],
                                                "sha", 1.0)), \
                 mock.patch.object(teach, "pick_textbook", return_value=None), \
                 mock.patch.object(teach, "_load_prompts",
                                   side_effect=teach.TeachError("no prompt")), \
                 mock.patch.object(teach, "_doc_title", return_value="T"), \
                 mock.patch.object(teach.runtime_context, "make_context",
                                   return_value=ctx) as mk:
                res = teach.build_supplement(50, out_path=out,
                                             conn=self._fake_conn(),
                                             log=lambda *a: None)
            self.assertEqual(mk.call_count, 1)
            self.assertEqual(mk.call_args.args[:2], ("textbook_teach",))
            self.assertEqual(res["meta"]["ai_model"], "snap-model")

    def test_estimate_map_uses_snapshot(self):
        ctx = self._ctx()
        with mock.patch.object(teach, "_courseware_kps",
                               return_value=(Path("kp.json"), [], "sha", 0.0)), \
             mock.patch.object(teach, "pick_textbook", return_value=None), \
             mock.patch.object(teach, "_load_prompts",
                               side_effect=teach.TeachError("no prompt")):
            est = teach.estimate_map(50, conn=self._fake_conn(), run_context=ctx)
        self.assertEqual((est["model"], est["thinking"],
                          est["reasoning_effort"], est["max_tokens"]),
                         ("snap-model", True, "max", 4096))
        self.assertEqual(est["calls"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
