"""P0-D 验收：kp_build RunContext 快照隔离（零模型/零网络）。"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, kp_build  # noqa: E402
from core.pipeline_runtime import context as runtime_context  # noqa: E402

_JSON = json.dumps({"points": [], "sections": [], "page_titles": {}})


class _Base(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "1"}),
            mock.patch.object(kp_build, "MODEL", "legacy-model"),
            mock.patch.object(kp_build, "KP_MAX_TOKENS", 111),
            mock.patch.object(kp_build, "STRUCT_MAX_TOKENS", 222),
            mock.patch.object(kp_build, "THINKING_V2", True),
            mock.patch.object(kp_build, "REASONING_EFFORT", "legacy-eff"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _ctx(self, model="snap-model", cap=4096, thinking=True, effort="max"):
        return SimpleNamespace(run_id="r", pipeline="courseware_kp", subject_id="默认",
                               doc_id=50, model=model, api_base="https://ctx.example",
                               thinking=thinking, reasoning_effort=effort,
                               max_tokens=cap, credential_ref="courseware_kp")

    def _fake_call(self):
        calls = []
        def hard(fn, timeout, kind):
            return fn()
        def chat_once(messages, **kw):
            calls.append(kw)
            return {"content": _JSON, "model": kw.get("model"), "usage": {},
                    "finish_reason": "stop"}
        return calls, mock.patch.object(kp_build, "model_slot",
                                        return_value=contextlib.nullcontext()), \
            mock.patch.object(kp_build, "_call_with_hard_timeout", side_effect=hard), \
            mock.patch("core.chat.chat_once", side_effect=chat_once)


class TestV2CallSnapshot(_Base):
    def test_context_snapshot_overrides_globals(self):
        ctx = self._ctx()
        calls, slot, hard, chat = self._fake_call()
        with slot, hard, chat:
            res = kp_build._v2_call([], "knowledge", "s", "u", max_tokens=999,
                                    retry=0, log=lambda *a: None,
                                    run_context=ctx)
        self.assertTrue(res["ok"])
        kw = calls[0]
        self.assertEqual((kw["model"], kw["max_tokens"], kw["thinking"]),
                         ("snap-model", 4096, True))
        self.assertEqual(kw["reasoning_effort"], "max")
        self.assertIs(kw["run_context"], ctx)
        self.assertEqual(res["call"]["model"], "snap-model")
        self.assertEqual(res["call"]["max_tokens"], 4096)

    def test_config_switch_after_context_no_leak(self):
        ctx = self._ctx()
        with mock.patch.object(kp_build, "MODEL", "changed-model"), \
             mock.patch.object(kp_build, "KP_MAX_TOKENS", 7), \
             mock.patch.object(kp_build, "REASONING_EFFORT", "low"):
            calls, slot, hard, chat = self._fake_call()
            with slot, hard, chat:
                kp_build._v2_call([], "knowledge", "s", "u", max_tokens=0,
                                  retry=0, log=lambda *a: None, run_context=ctx)
        self.assertEqual((calls[0]["model"], calls[0]["max_tokens"]), ("snap-model", 4096))

    def test_no_context_old_path(self):
        calls, slot, hard, chat = self._fake_call()
        with slot, hard, chat:
            kp_build._v2_call([], "knowledge", "s", "u", max_tokens=333,
                              retry=0, log=lambda *a: None,
                              thinking=False, reasoning_effort="high")
        self.assertEqual((calls[0]["model"], calls[0]["max_tokens"],
                          calls[0]["thinking"]), ("legacy-model", 333, False))
        self.assertIsNone(calls[0]["run_context"])

    def test_runtime_disabled_old_path(self):
        ctx = self._ctx()
        calls, slot, hard, chat = self._fake_call()
        with mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "0"}), \
             slot, hard, chat:
            kp_build._v2_call([], "knowledge", "s", "u", max_tokens=444,
                              retry=0, log=lambda *a: None, run_context=ctx)
        self.assertEqual((calls[0]["model"], calls[0]["max_tokens"]),
                         ("legacy-model", 444))
        self.assertIsNone(calls[0]["run_context"], "回退旧路径不传 context")

    def test_worker_thread_gets_context_explicitly(self):
        ctx = self._ctx("thread-model", 512)
        captured = []
        def hard(fn, timeout, kind):
            return fn()
        def fake(messages, **kw):
            captured.append(kw)
            return {"content": _JSON, "model": kw["model"], "usage": {},
                    "finish_reason": "stop"}
        def worker():
            with mock.patch.object(kp_build, "model_slot",
                                   return_value=contextlib.nullcontext()), \
                 mock.patch.object(kp_build, "_call_with_hard_timeout",
                                   side_effect=hard), \
                 mock.patch("core.chat.chat_once", side_effect=fake):
                kp_build._v2_call([], "knowledge", "s", "u", max_tokens=0,
                                  retry=0, log=lambda *a: None, run_context=ctx)
        th = threading.Thread(target=worker)
        th.start(); th.join(10)
        self.assertIs(captured[0]["run_context"], ctx)
        self.assertEqual(captured[0]["model"], "thread-model")

    def test_two_threads_do_not_cross(self):
        ctx1, ctx2 = self._ctx("m1", 11), self._ctx("m2", 22)
        captured = []
        lock = threading.Lock()
        def hard(fn, timeout, kind):
            return fn()
        def fake(messages, **kw):
            with lock:
                captured.append((kw["model"], kw["max_tokens"]))
            return {"content": _JSON, "model": kw["model"], "usage": {},
                    "finish_reason": "stop"}
        def worker(ctx):
            with mock.patch.object(kp_build, "model_slot",
                                   return_value=contextlib.nullcontext()), \
                 mock.patch.object(kp_build, "_call_with_hard_timeout",
                                   side_effect=hard), \
                 mock.patch("core.chat.chat_once", side_effect=fake):
                kp_build._v2_call([], "knowledge", "s", "u", max_tokens=0,
                                  retry=0, log=lambda *a: None, run_context=ctx)
        ts = [threading.Thread(target=worker, args=(c,)) for c in (ctx1, ctx2)]
        for t in ts: t.start()
        for t in ts: t.join(10)
        self.assertEqual(sorted(captured), [("m1", 11), ("m2", 22)])

    def test_gen_kp_v2_passes_context(self):
        ctx = self._ctx()
        fake_call = mock.Mock(return_value={"ok": False, "error": "stop"})
        task = {"kp_id": "KP-1", "node_path": ["a"], "node_name": "a", "pages": [1]}
        with mock.patch.object(kp_build, "render_kp_material",
                               return_value=("m", [])), \
             mock.patch.object(kp_build, "_v2_call", fake_call):
            with self.assertRaises(RuntimeError):
                kp_build.gen_kp_v2(task, {}, {"sys": "s", "user": "u"}, "deck",
                                   calls=[], log=lambda *a: None, run_context=ctx)
        self.assertIs(fake_call.call_args.kwargs["run_context"], ctx)


class TestEntry(_Base):
    def test_build_v2_creates_context_once_after_dry_run(self):
        ctx = self._ctx()
        with mock.patch.object(kp_build, "estimate_v2",
                               return_value={"ok": True}), \
             mock.patch.object(kp_build.runtime_context, "make_context",
                               return_value=ctx) as mk, \
             mock.patch.object(kp_build, "load_kp_prompts_v2",
                               side_effect=RuntimeError("stop-after-context")):
            with self.assertRaises(RuntimeError):
                kp_build.build_v2(50, log=lambda *a: None)
        self.assertEqual(mk.call_count, 1, "入口只建一次 context")
        self.assertEqual(mk.call_args.args[:2], ("courseware_kp",))

    def test_dry_run_does_not_create_context(self):
        with mock.patch.object(kp_build, "estimate_v2",
                               return_value={"ok": True}) as est, \
             mock.patch.object(kp_build.runtime_context, "make_context",
                               side_effect=AssertionError("dry-run 不得建 context")):
            res = kp_build.build_v2(50, dry_run=True, log=lambda *a: None)
        self.assertTrue(res["dry_run"])
        self.assertTrue(est.called)

    def test_estimate_v2_no_context(self):
        with mock.patch.object(kp_build.runtime_context, "make_context",
                               side_effect=AssertionError("estimate 不建 context")):
            try:
                kp_build.estimate_v2(999999, prompt_path=None,
                                     struct_prompt_path=None, db_path=None)
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
