"""P0-C 验收：preview 管线 RunContext 隔离（零模型/零网络）。"""
from __future__ import annotations

import contextlib
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
from core import config, preview  # noqa: E402
from core.pipeline_runtime import context as runtime_context  # noqa: E402

_JSON = json.dumps({"overview": "x", "subsections": [], "key_terms": []})


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        import shutil
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = Path(self.tmp)
        self.settings = {}
        def value_of(key, default=None):
            return self.settings.get(key, default)
        def value_int(key, default=0):
            try:
                return int(self.settings.get(key, default))
            except (TypeError, ValueError):
                return default
        def token_cap(pipeline):
            try:
                return max(0, int(self.settings.get("PREVIEW_MAX_TOKENS", 0) or 0))
            except (TypeError, ValueError):
                return 0
        self._patches = [
            mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "1"}),
            mock.patch.object(config, "LOG_DIR", self.root / "logs"),
            mock.patch.object(config, "DB_PATH", self.root / "x.db"),
            mock.patch.object(config, "value_of", side_effect=value_of),
            mock.patch.object(config, "value_int", side_effect=value_int),
            mock.patch.object(config, "token_cap", side_effect=token_cap),
        ]
        for p in self._patches:
            p.start()
        self._set(4048, "m-ctx", "https://ctx.example", 1, "high")

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _set(self, max_tokens, model, base, thinking, effort):
        self.settings.update({
            "PREVIEW_MAX_TOKENS": max_tokens, "PREVIEW_MODEL": model,
            "PREVIEW_BASE_URL": base, "PREVIEW_THINKING": thinking,
            "PREVIEW_REASONING_EFFORT": effort,
        })

    def _ctx(self, model="m-ctx", cap=4048, thinking=True, effort="high"):
        return SimpleNamespace(run_id="r", pipeline="preview", subject_id="默认",
                               doc_id=50, model=model, api_base="https://ctx.example",
                               thinking=thinking, reasoning_effort=effort,
                               max_tokens=cap, credential_ref="preview")

    @staticmethod
    def _fake_chat():
        calls = []
        def fn(messages, **kw):
            calls.append(kw)
            return {"content": _JSON, "model": kw.get("model"), "usage": {},
                    "finish_reason": "stop"}
        return calls, mock.patch("core.chat.chat_once", side_effect=fn)


class TestContextSnapshot(_Base):
    def test_make_context_preview_fields(self):
        ctx = runtime_context.make_context("preview", doc_id=50)
        self.assertEqual(ctx.pipeline, "preview")
        self.assertEqual(ctx.model, "m-ctx")
        self.assertEqual(ctx.api_base, "https://ctx.example")
        self.assertTrue(ctx.thinking)
        self.assertEqual(ctx.reasoning_effort, "high")
        self.assertEqual(ctx.max_tokens, 4048)
        self.assertEqual(ctx.doc_id, 50)
        self.assertEqual(ctx.credential_ref, "preview")

    def test_preview_call_uses_snapshot_not_globals(self):
        ctx = self._ctx()
        calls, chat = self._fake_chat()
        with mock.patch.object(preview, "model_name",
                               side_effect=AssertionError("不得读全局 model")), \
             mock.patch.object(preview, "thinking_on",
                               side_effect=AssertionError("不得读全局 thinking")), \
             mock.patch.object(preview, "effort",
                               side_effect=AssertionError("不得读全局 effort")), \
             mock.patch.object(preview, "max_tokens",
                               side_effect=AssertionError("不得读全局 max")), \
             mock.patch.object(preview.kp_build, "model_slot",
                               return_value=contextlib.nullcontext()), \
             chat:
            res = preview._preview_call([], "preview.chapter", "s", "u",
                                        max_tokens=999, retry=0, log=lambda *a: None,
                                        thinking=False, reasoning_effort="low",
                                        model="legacy", run_context=ctx)
        self.assertTrue(res["ok"])
        kw = calls[0]
        self.assertEqual((kw["model"], kw["max_tokens"], kw["thinking"]),
                         ("m-ctx", 4048, True))
        self.assertEqual(kw["reasoning_effort"], "high")
        self.assertIs(kw["run_context"], ctx)
        self.assertEqual(res["call"]["max_tokens"], 4048)

    def test_config_switch_after_context_no_leak(self):
        ctx = runtime_context.make_context("preview", doc_id=50)
        self._set(1024, "m-new", "https://new.example", 0, "low")
        calls, chat = self._fake_chat()
        with mock.patch.object(preview.kp_build, "model_slot",
                               return_value=contextlib.nullcontext()), chat:
            preview._preview_call([], "preview.chapter", "s", "u", max_tokens=0,
                                  retry=0, log=lambda *a: None, run_context=ctx)
        self.assertEqual((calls[0]["model"], calls[0]["max_tokens"]),
                         ("m-ctx", 4048), "运行中改设置不得影响已建 context")

    def test_two_threads_contexts_do_not_leak(self):
        ctx1 = self._ctx("m-one", 111)
        ctx2 = self._ctx("m-two", 222)
        captured = []
        lock = threading.Lock()

        def fake(messages, **kw):
            with lock:
                captured.append((kw["model"], kw["max_tokens"]))
            return {"content": _JSON, "model": kw["model"], "usage": {},
                    "finish_reason": "stop"}

        def worker(ctx):
            preview._preview_call([], "preview.chapter", "s", "u",
                                  max_tokens=0, retry=0,
                                  log=lambda *a: None, run_context=ctx)

        threads = [threading.Thread(target=worker, args=(c,)) for c in (ctx1, ctx2)]
        # Patch the shared functions once for both workers; per-thread patchers race
        # while restoring module globals and can leave a mock installed for later tests.
        with mock.patch.object(preview.kp_build, "model_slot",
                               return_value=contextlib.nullcontext()), \
             mock.patch("core.chat.chat_once", side_effect=fake):
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
        self.assertEqual(sorted(captured), [("m-one", 111), ("m-two", 222)])

    def test_runtime_disabled_old_path(self):
        ctx = self._ctx()
        calls, chat = self._fake_chat()
        with mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "0"}), \
             mock.patch.object(preview, "model_name", return_value="legacy"), \
             mock.patch.object(preview, "thinking_on", return_value=False), \
             mock.patch.object(preview, "effort", return_value="low"), \
             mock.patch.object(preview, "max_tokens", return_value=2048), \
             mock.patch.object(preview.kp_build, "model_slot",
                               return_value=contextlib.nullcontext()), chat:
            preview._preview_call([], "preview.chapter", "s", "u", max_tokens=0,
                                  retry=0, log=lambda *a: None, run_context=ctx)
        self.assertEqual((calls[0]["model"], calls[0]["max_tokens"],
                          calls[0]["thinking"]), ("legacy", 1024, True))
        self.assertIsNone(calls[0]["run_context"], "回退旧路径不传 context")

    def test_no_context_old_path(self):
        calls, chat = self._fake_chat()
        with mock.patch.object(preview, "model_name", return_value="legacy"), \
             mock.patch.object(preview, "thinking_on", return_value=True), \
             mock.patch.object(preview, "effort", return_value="high"), \
             mock.patch.object(preview, "max_tokens", return_value=2048), \
             mock.patch.object(preview.kp_build, "model_slot",
                               return_value=contextlib.nullcontext()), chat:
            preview._preview_call([], "preview.chapter", "s", "u", max_tokens=4096,
                                  retry=0, log=lambda *a: None, thinking=True,
                                  reasoning_effort="high", model="passed")
        self.assertEqual(calls[0]["model"], "passed")
        self.assertEqual(calls[0]["max_tokens"], 4096)
        self.assertIsNone(calls[0]["run_context"])


class TestMetaAndEntry(_Base):
    def _meta_args(self):
        return dict(doc_id=50, info={"no": 1, "name": "章", "heading_path": "h",
                                     "pages": [1], "page_start": 1, "page_end": 1,
                                     "source": "chunks"},
                    est={"title": "T", "n_blocks": 1}, prompt={"path": "p", "sha256": "h"},
                    calls=[], started=0.0, status="done", err="", inv=[],
                    render_stats={}, sec={}, subs=[], terms=[], warnings=[],
                    material_sha="m", run_no=1, sections_source="none",
                    parser="v2_subsections", prev_backup="")

    def test_v2_meta_uses_snapshot(self):
        ctx = self._ctx()
        meta = preview._v2_meta(**self._meta_args(), run_context=ctx)
        self.assertEqual((meta["model"], meta["thinking"],
                          meta["reasoning_effort"], meta["max_tokens"]),
                         ("m-ctx", True, "high", 4048))
        self.assertEqual(meta["params"]["max_tokens"], 4048)
        for key in ("preview_version", "chapter", "usage_this_run", "calls"):
            self.assertIn(key, meta)

    def test_ensure_sections_accepts_context(self):
        ctx = self._ctx()
        with mock.patch.object(preview, "sections_out_file",
                               return_value=self.root / "sec.json"), \
             mock.patch.object(preview, "_load_sections_artifact",
                               return_value={"ok": True, "chapters": [], "note": ""}):
            a = preview.ensure_sections_artifact(50, run_context=ctx)
            b = preview.ensure_sections_artifact(50)
        self.assertTrue(a["ok"])
        self.assertEqual(a, b, "纯本地阶段接 context 不改变输出")

    def _build_fakes(self):
        est = {"ok": True, "out_path": str(self.root / "out.json"), "title": "T",
               "chapter": "第1章", "n_pages": 1, "n_blocks": 1, "page_label": "p.1",
               "skip_would_happen": False, "model": "legacy", "thinking": False,
               "reasoning_effort": "low", "max_tokens": 1}
        info = {"ok": True, "no": 1, "name": "章", "heading_path": "h",
                "pages": [1], "page_start": 1, "page_end": 1, "source": "chunks"}
        return est, info

    def test_build_preview_creates_context_once(self):
        ctx = self._ctx()
        est, info = self._build_fakes()
        got = {}
        def fake_call(calls, kind, sys_p, user_p, **kw):
            got.update(kw)
            return {"ok": True, "parsed": {"subsections": [], "overview": ""},
                    "usage": {}, "raw": "", "model": ctx.model,
                    "call_index": 0, "call": {}}
        with mock.patch.object(preview.runtime_context, "make_context",
                               side_effect=lambda *a, **kw: ctx) as mk, \
             mock.patch.object(preview, "enabled_state",
                               return_value={"enabled": True}), \
             mock.patch.object(preview, "ensure_sections_artifact",
                               return_value={"created": False}), \
             mock.patch.object(preview, "estimate_preview", return_value=est), \
             mock.patch.object(preview, "load_preview_prompts",
                               return_value={"sys": "s", "user": "u"}), \
             mock.patch.object(preview, "read_chapter_ch",
                               return_value=({}, info, "材料", [], {})), \
             mock.patch.object(preview, "chapter_sections",
                               return_value={"read": True, "n_sections": 0,
                                             "sections": [], "source": "artifact",
                                             "fallback": False, "note": ""}), \
             mock.patch.object(preview, "render_sections_outline",
                               return_value=""), \
             mock.patch.object(preview, "_preview_call", side_effect=fake_call), \
             mock.patch.object(preview.kp_build, "_save", return_value=None):
            res = preview.build_preview(50, 1, log=lambda *a: None)
        self.assertTrue(res["ok"])
        self.assertEqual(mk.call_count, 1, "同一任务只建一次 context")
        self.assertIs(got.get("run_context"), ctx)
        self.assertEqual(res["meta"]["model"], "m-ctx")
        self.assertEqual(res["meta"]["max_tokens"], 4048)

    def test_build_preview_runtime0_no_context(self):
        est, info = self._build_fakes()
        got = {}
        def fake_call(calls, kind, sys_p, user_p, **kw):
            got.update(kw)
            return {"ok": True, "parsed": {"subsections": [], "overview": ""},
                    "usage": {}, "raw": "", "model": "legacy",
                    "call_index": 0, "call": {}}
        with mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "0"}), \
             mock.patch.object(preview.runtime_context, "make_context",
                               side_effect=AssertionError("不得建 context")), \
             mock.patch.object(preview, "enabled_state",
                               return_value={"enabled": True}), \
             mock.patch.object(preview, "ensure_sections_artifact",
                               return_value={"created": False}), \
             mock.patch.object(preview, "estimate_preview", return_value=est), \
             mock.patch.object(preview, "load_preview_prompts",
                               return_value={"sys": "s", "user": "u"}), \
             mock.patch.object(preview, "read_chapter_ch",
                               return_value=({}, info, "材料", [], {})), \
             mock.patch.object(preview, "chapter_sections",
                               return_value={"read": True, "n_sections": 0,
                                             "sections": [], "source": "artifact",
                                             "fallback": False, "note": ""}), \
             mock.patch.object(preview, "render_sections_outline",
                               return_value=""), \
             mock.patch.object(preview, "_preview_call", side_effect=fake_call), \
             mock.patch.object(preview.kp_build, "_save", return_value=None):
            res = preview.build_preview(50, 1, log=lambda *a: None)
        self.assertTrue(res["ok"])
        self.assertIsNone(got.get("run_context"), "PIPELINE_RUNTIME=0 旧路径")
        self.assertEqual(res["meta"]["model"], "m-ctx",
                         "回退旧路径读当前全局设置")


if __name__ == "__main__":
    unittest.main(verbosity=2)
