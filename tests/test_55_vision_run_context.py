"""P0-F 验收：视觉/结构式管线 RunContext 快照隔离（零网络/零模型）。"""
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
from core import agent_bridge, config, figures, structures  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        import shutil
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = Path(self.tmp)
        self.img = self.root / "p1.png"
        self.img.write_bytes(b"fake-png")
        self._patches = [mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "1"})]
        for p in self._patches:
            p.start()
        self.page = figures.PageContext(page=1, title="T", body="正文")

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _ctx(self, model="snap-model", cap=4096, thinking=True, effort="max",
             base="https://ctx.example/v1"):
        return SimpleNamespace(run_id="r", pipeline="ingest_vision", subject_id="默认",
                               doc_id=50, model=model, api_base=base,
                               thinking=thinking, reasoning_effort=effort,
                               max_tokens=cap, credential_ref="ingest_vision")

    @staticmethod
    def _ok_body(content="ok"):
        return 200, {"choices": [{"message": {"content": content}}], "usage": {}}


class TestHttpPost(_Base):
    def _call(self, ctx, payload):
        captured = []
        class Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}],
                                   "usage": {}}).encode()
        def fake(req, timeout):
            captured.append({"url": req.full_url,
                             "payload": json.loads(req.data.decode())})
            return Resp()
        with mock.patch.object(agent_bridge, "complete", return_value=None), \
             mock.patch("urllib.request.urlopen", side_effect=fake):
            st, body = figures._http_post("https://old.example/chat/completions",
                                          payload, "k", retries=0, wait=0,
                                          run_context=ctx)
        return captured, st, body

    def test_context_snapshot_overrides_payload_and_base(self):
        ctx = self._ctx()
        payload = {"model": "legacy", "messages": [], "max_tokens": 111,
                   "thinking": {"type": "disabled"}}
        with mock.patch.object(config, "apply_token_cap",
                               side_effect=AssertionError("不得读中央 cap")), \
             mock.patch.object(config, "token_cap",
                               side_effect=AssertionError("不得读 token_cap")):
            captured, st, _ = self._call(ctx, payload)
        self.assertEqual(captured[0]["url"], "https://ctx.example/v1/chat/completions")
        got = captured[0]["payload"]
        self.assertEqual((got["model"], got["max_tokens"]), ("snap-model", 4096))
        self.assertEqual(got["thinking"], {"type": "enabled"})
        self.assertEqual(got["reasoning_effort"], "max")

    def test_zero_max_tokens_not_sent(self):
        captured, _, _ = self._call(self._ctx(cap=0), {
            "model": "legacy", "messages": [], "max_tokens": 111})
        self.assertNotIn("max_tokens", captured[0]["payload"])

    def test_no_context_old_path(self):
        payload = {"model": "legacy", "messages": [], "max_tokens": 111}
        with mock.patch.object(config, "apply_token_cap",
                               side_effect=lambda p, pipe: dict(p)):
            captured, _, _ = self._call(None, payload)
        got = captured[0]["payload"]
        self.assertEqual(got["model"], "legacy")
        self.assertEqual(got["max_tokens"], 111)

    def test_runtime_disabled_old_path(self):
        ctx = self._ctx()
        with mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "0"}), \
             mock.patch.object(config, "apply_token_cap",
                               side_effect=lambda p, pipe: dict(p)):
            captured, _, _ = self._call(ctx, {
                "model": "legacy", "messages": [], "max_tokens": 111})
        self.assertEqual(captured[0]["payload"]["model"], "legacy")
        self.assertNotEqual(captured[0]["url"], "https://ctx.example/v1/chat/completions")

    def test_two_threads_do_not_cross(self):
        results = []
        lock = threading.Lock()
        class Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}],
                                   "usage": {}}).encode()
        def fake(req, timeout):
            with lock:
                results.append(json.loads(req.data.decode())["model"])
            return Resp()
        def worker(ctx):
            with mock.patch.object(agent_bridge, "complete", return_value=None), \
                 mock.patch("urllib.request.urlopen", side_effect=fake):
                figures._http_post("https://old", {"messages": [], "model": "x"},
                                   "k", retries=0, wait=0, run_context=ctx)
        ts = [threading.Thread(target=worker, args=(c,))
              for c in (self._ctx("m1", 11), self._ctx("m2", 22))]
        for t in ts: t.start()
        for t in ts: t.join(10)
        self.assertEqual(sorted(results), ["m1", "m2"])


class TestDescribeAndProbe(_Base):
    def test_describe_page_context_snapshot(self):
        ctx = self._ctx(cap=0, thinking=False, effort="")
        captured = []
        def fake_http(url, payload, key, **kw):
            captured.append((url, payload, kw.get("run_context")))
            return self._ok_body()
        with mock.patch.object(figures, "load_deepseek_key", return_value="k"), \
             mock.patch.object(agent_bridge, "has_adapter", return_value=False), \
             mock.patch.object(figures, "_http_post", side_effect=fake_http), \
             mock.patch.object(config, "token_cap",
                               side_effect=AssertionError("不得读 token cap")):
            res = figures.describe_page(self.img, self.page, run_context=ctx)
        self.assertTrue(res.ok)
        url, payload, got_ctx = captured[0]
        self.assertIs(got_ctx, ctx)
        self.assertEqual(payload["model"], "snap-model")
        self.assertNotIn("max_tokens", payload)
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_describe_page_no_context_old_path(self):
        captured = []
        def fake_http(url, payload, key, **kw):
            captured.append((payload, kw.get("run_context")))
            return self._ok_body()
        with mock.patch.object(figures, "load_deepseek_key", return_value="k"), \
             mock.patch.object(agent_bridge, "has_adapter", return_value=False), \
             mock.patch.object(figures, "_http_post", side_effect=fake_http), \
             mock.patch.object(config, "token_cap", return_value=2048):
            figures.describe_page(self.img, self.page, model="legacy", max_tokens=333)
        payload, ctx = captured[0]
        self.assertIsNone(ctx)
        self.assertEqual((payload["model"], payload["max_tokens"]), ("legacy", 333))

    def test_probe_retries_same_context(self):
        ctx = self._ctx()
        seen = []
        def fake_desc(img, page, **kw):
            seen.append(kw.get("run_context"))
            if len(seen) == 1:
                return figures.FigureResult(True, text="短")
            return figures.FigureResult(True, text="足够长的正文" * 5)
        with mock.patch.object(figures, "describe_page", side_effect=fake_desc):
            r, r2 = figures._probe_page(self.img, self.page, True, run_context=ctx)
        self.assertTrue(r.ok and r2 is not None)
        self.assertEqual(seen, [ctx, ctx], "重试也必须同一个 context")

    def test_visual_cache_key_uses_snapshot_fields(self):
        k1 = figures.visual_cache_key(self.page, "hash", thinking=True,
                                      model="m1", api_base="b1", reasoning_effort="max")
        k2 = figures.visual_cache_key(self.page, "hash", thinking=True,
                                      model="m2", api_base="b1", reasoning_effort="max")
        k3 = figures.visual_cache_key(self.page, "hash", thinking=False,
                                      model="m1", api_base="b1", reasoning_effort="max")
        self.assertEqual(len({k1, k2, k3}), 3)
        self.assertEqual(figures.visual_cache_state(self.root, 1, k1), "legacy")


class TestIngestEntry(_Base):
    def test_ingest_document_figures_creates_context_once(self):
        ctx = self._ctx()
        fake_conn = SimpleNamespace(commit=lambda: None, close=lambda: None)
        with mock.patch.object(figures, "extract_pages", return_value=("", [])), \
             mock.patch.object(figures.runtime_context, "make_context",
                               return_value=ctx) as mk:
            stats = figures.ingest_document_figures(
                fake_conn, 50, self.root / "x.pdf", kind="pdf")
        self.assertEqual(mk.call_count, 1)
        self.assertEqual(mk.call_args.args[:2], ("ingest_vision",))
        self.assertEqual(stats["described"], 0)

    def test_ingest_runtime0_no_context(self):
        fake_conn = SimpleNamespace(commit=lambda: None, close=lambda: None)
        with mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "0"}), \
             mock.patch.object(figures, "extract_pages", return_value=("", [])), \
             mock.patch.object(figures.runtime_context, "make_context",
                               side_effect=AssertionError("不得建 context")):
            figures.ingest_document_figures(fake_conn, 50, self.root / "x.pdf",
                                            kind="pdf")
        # 不抛 = 未建 context


class TestStructures(_Base):
    def test_vision_call_context_snapshot(self):
        ctx = SimpleNamespace(run_id="r", pipeline="structures", subject_id="默认",
                              doc_id=50, model="struct-model", api_base="https://s.example",
                              thinking=False, reasoning_effort="", max_tokens=0)
        captured = []
        def fake_http(url, payload, key, **kw):
            captured.append((url, payload, kw.get("run_context")))
            return self._ok_body('{"ok":1}')
        with mock.patch.object(structures, "_api_key", return_value="k"), \
             mock.patch.object(figures, "_http_post", side_effect=fake_http):
            res = structures._vision_call(self.img, "prompt", model="legacy",
                                          max_tokens=999, thinking=True,
                                          run_context=ctx)
        self.assertTrue(res["ok"])
        url, payload, got_ctx = captured[0]
        self.assertEqual(payload["model"], "struct-model")
        self.assertNotIn("max_tokens", payload)
        self.assertIs(got_ctx, ctx)

    def test_build_structures_creates_context_once(self):
        ctx = SimpleNamespace(run_id="r", pipeline="structures", subject_id="默认",
                              doc_id=50, model="struct-model", api_base="b",
                              thinking=False, reasoning_effort="", max_tokens=0)
        with mock.patch.object(structures.runtime_context, "make_context",
                               return_value=ctx) as mk, \
             mock.patch.object(structures, "_resolve_source",
                               side_effect=structures.StructureError("no source")):
            res = structures.build_structures(50, dry_run=True)
        self.assertFalse(res["ok"])
        self.assertEqual(mk.call_count, 1)
        self.assertEqual(mk.call_args.args[:2], ("structures",))

    def test_build_structures_runtime0_no_context(self):
        with mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "0"}), \
             mock.patch.object(structures.runtime_context, "make_context",
                               side_effect=AssertionError("不得建 context")), \
             mock.patch.object(structures, "_resolve_source",
                               side_effect=structures.StructureError("no source")):
            res = structures.build_structures(50, dry_run=True)
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
