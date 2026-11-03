"""Offline regression checks for bounded shared model requests and short decks."""
from __future__ import annotations

import concurrent.futures
import contextlib
import io
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import agent_bridge, chat, figures, kp_build, kp_pages, preview, structures
from core.pipeline_runtime import state as runtime_state


def _response():
    return {"content": "{}", "usage": {}, "model": "fake", "finish_reason": "stop"}


def _context(pipeline="ingest_vision", credential="ingest"):
    return SimpleNamespace(pipeline=pipeline, credential_ref=credential,
                           model="fake-model", thinking=True, max_tokens=0,
                           reasoning_effort="high", api_base="https://fake.invalid")


def _chapter(pages, chars=40):
    return {"pages_all": list(range(1, pages + 1)), "cover": {}, "figrows": {},
            "blocks": {i: [{"ord": i, "chunk_id": i, "kind": "正文块",
                            "text": "化学" * chars, "pages": [i]}]
                       for i in range(1, pages + 1)}}


class SharedGateTests(unittest.TestCase):
    def setUp(self):
        kp_build.configure_model_gate(2)
        self.release = threading.Event()
        self.workers = []

    def tearDown(self):
        self.release.set()
        for t in self.workers:
            t.join(3)
        end = time.monotonic() + 3
        while kp_build.model_gate_stats()["inflight"] and time.monotonic() < end:
            time.sleep(0.005)
        self.assertEqual(kp_build.model_gate_stats()["inflight"], 0)
        kp_build.configure_model_gate(4)

    def _thread(self, fn):
        t = threading.Thread(target=fn, daemon=True)
        self.workers.append(t)
        t.start()
        return t

    def test_timed_out_request_keeps_its_slot_until_transport_finishes(self):
        kp_build.configure_model_gate(1)
        started = threading.Event()
        def slow(*args, **kwargs):
            started.set()
            self.release.wait(3)
            return _response()
        with mock.patch.object(chat, "chat_once", side_effect=slow), \
             mock.patch.object(kp_build, "call_hard_timeout", return_value=0.05):
            result = kp_build._v2_call([], "knowledge", "s", "u", max_tokens=100,
                                       retry=2, log=lambda *_: None)
        self.assertTrue(started.is_set())
        self.assertTrue(result["remote_unknown"])
        self.assertTrue(result["call"]["cost_unknown"])
        stats = kp_build.model_gate_stats()
        self.assertEqual((stats["inflight"], stats["timed_out_inflight"]), (1, 1))
        with self.assertRaises(kp_build.ModelSlotTimeout):
            with kp_build.model_slot("second", timeout=0.03):
                self.fail("still-running request was not counted")
        with self.assertRaises(RuntimeError):
            kp_build.configure_model_gate(2)

    def test_paused_stream_does_not_lend_its_slot_to_consumer(self):
        kp_build.configure_model_gate(1)
        def events(*args, **kwargs):
            yield ("delta", "first")
            yield ("done", None)
        with mock.patch.object(chat, "_stream_chat_unthrottled", side_effect=events), \
             mock.patch.object(chat, "_chat_once_unthrottled", return_value=_response()), \
             mock.patch.object(kp_build, "slot_wait_timeout", return_value=0.03):
            stream = chat.stream_chat([])
            self.assertEqual(next(stream), ("delta", "first"))
            with self.assertRaises(kp_build.ModelSlotTimeout):
                chat.chat_once([])
            stream.close()
            self.assertEqual(chat.chat_once([])["model"], "fake")

    def test_stall_reclaim_does_not_release_other_live_requests(self):
        entered = [threading.Event(), threading.Event()]
        def request(i):
            with kp_build.model_slot("job"):
                entered[i].set()
                self.release.wait(3)
        for i in range(2):
            self._thread(lambda i=i: request(i))
        self.assertTrue(all(e.wait(1) for e in entered))
        self.assertEqual(kp_build.reclaim_stalled_slots(), 0)
        with self.assertRaises(kp_build.ModelSlotTimeout):
            with kp_build.model_slot("third", timeout=0.03):
                self.fail("reclaim released another task's slot")
        self.assertEqual(kp_build.model_gate_stats()["inflight"], 2)

    def test_mixed_64_requests_obey_same_process_limit(self):
        kp_build.configure_model_gate(6)
        lock = threading.Lock()
        state = {"active": 0, "peak": 0, "done": 0}
        saturated = threading.Event()
        def fake(*args, **kwargs):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                if state["active"] == 6:
                    saturated.set()
            try:
                self.release.wait(3)
                return _response()
            finally:
                with lock:
                    state["active"] -= 1
                    state["done"] += 1
        errors = []
        def request(i):
            try:
                if i % 2:
                    chat.chat_once([])
                else:
                    figures._http_post("https://fake.invalid", {}, "fake", retries=0)
            except Exception as e:  # surfaced after joins
                errors.append(e)
        with mock.patch.object(chat, "_chat_once_unthrottled", side_effect=fake), \
             mock.patch.object(figures, "_http_post_unthrottled", side_effect=fake):
            for i in range(64):
                self._thread(lambda i=i: request(i))
            self.assertTrue(saturated.wait(2))
            time.sleep(0.03)
            self.assertLessEqual(state["peak"], 6)
            self.release.set()
            for t in self.workers:
                t.join(5)
        self.assertFalse(errors)
        self.assertTrue(all(not t.is_alive() for t in self.workers))
        self.assertEqual((state["done"], state["peak"]), (64, 6))


class NetworkPolicyTests(unittest.TestCase):
    def test_gateway_errors_no_retry_and_stream_marked_unknown(self):
        for code in (502, 504):
            count = []
            def fail(*a, **kw):
                count.append(1)
                raise urllib.error.HTTPError("https://fake.invalid", code, "gateway", {}, io.BytesIO(b""))
            with mock.patch.object(agent_bridge, "complete", return_value=None), \
                 mock.patch.object(chat, "load_api_key", return_value="fake-key"), \
                 mock.patch("urllib.request.urlopen", side_effect=fail):
                result = kp_build._v2_call([], "knowledge", "s", "u", max_tokens=100,
                                           retry=3, log=lambda *_: None)
                self.assertTrue(result["remote_unknown"])
                self.assertEqual(len(count), 1)
                result = preview._preview_call([], "preview", "s", "u", max_tokens=100,
                                               retry=3, log=lambda *_: None)
                self.assertTrue(result["remote_unknown"])
                self.assertEqual(len(count), 2)
                with self.assertRaises(runtime_state.RemoteUnknownError):
                    list(chat.stream_chat([]))
                self.assertEqual(len(count), 3)

    def test_activation_does_not_replace_shared_fallback_key(self):
        fake_module = SimpleNamespace(DEEPSEEK_BASE="", CHAT_MODEL="")
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fallback-key"}), \
             mock.patch.object(figures.config, "pipeline_key",
                               side_effect=lambda ref: "kp-key" if ref == "kp" else ""), \
             mock.patch.object(figures.config, "_mod", return_value=fake_module), \
             mock.patch.object(figures.config, "_LAST_PIPELINE", "chat"):
            figures.config.activate("kp")
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "fallback-key")
            self.assertEqual(figures.vision_api_key(_context()), "fallback-key")

    def test_missing_context_key_uses_original_not_recent_pipeline_hook(self):
        captured = []
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'
        def send(req, timeout):
            captured.append(req.get_header("Authorization"))
            return Resp()
        def hooked(): return "wrong-pipeline-key"
        hooked._c0059_wrapped = True
        ctx = _context("courseware_kp", "kp")
        with mock.patch.object(agent_bridge, "complete", return_value=None), \
             mock.patch.object(figures.config, "pipeline_key", return_value=""), \
             mock.patch.object(chat, "load_api_key", hooked), \
             mock.patch.dict(figures.config._HOOKS, {"orig_load_api_key": lambda: "fallback-key"}), \
             mock.patch("urllib.request.urlopen", side_effect=send):
            chat.chat_once([], run_context=ctx)
        self.assertEqual(captured, ["Bearer fallback-key"])

    def test_vision_and_structure_choose_context_credential(self):
        ctx = _context()
        captured = []
        def fake_post(url, payload, key, **kw):
            captured.append(key)
            return 200, {"choices": [{"message": {"content": "内容"}}], "usage": {}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "page.png"
            p.write_bytes(b"fake-image")
            page = figures.PageContext(page=1, title="title", deck_title="deck")
            with mock.patch.object(figures.config, "pipeline_key", side_effect=lambda ref: "ingest-key" if ref == "ingest" else "wrong"), \
                 mock.patch.object(figures, "load_deepseek_key", return_value="stale-key"), \
                 mock.patch.object(figures, "_http_post", side_effect=fake_post):
                self.assertTrue(figures.describe_page(p, page, run_context=ctx).ok)
                sctx = _context("structures", "ingest")
                self.assertTrue(structures._vision_call(p, "prompt", model="m", max_tokens=1,
                                                        thinking=True, run_context=sctx)["ok"])
        self.assertEqual(captured, ["ingest-key", "ingest-key"])


class ShortDeckTests(unittest.TestCase):
    def test_guarded_short_plan_covers_every_page_and_can_roll_back(self):
        ch = _chapter(15)
        with mock.patch.dict(os.environ, {"KP_ADAPT_SHORT_STRUCTURE": "1"}):
            adaptive = kp_pages.plan_structure_segments(ch)
            legacy = kp_pages.plan_structure_segments(ch, seg_pages=12)
        self.assertEqual([len(s["pages"]) for s in adaptive], [15])
        self.assertEqual([len(s["pages"]) for s in legacy], [12, 3])
        self.assertEqual([p for seg in adaptive for p in seg["pages"]], ch["pages_all"])
        with mock.patch.dict(os.environ, {"KP_ADAPT_SHORT_STRUCTURE": "0"}):
            self.assertEqual(len(kp_pages.plan_structure_segments(ch)), 2)
        self.assertGreater(len(kp_pages.plan_structure_segments(_chapter(17))), 1)
        self.assertGreater(len(kp_pages.plan_structure_segments(_chapter(16, 250))), 1)

    def test_resume_signature_changes_for_source_and_plan(self):
        ch = _chapter(15)
        with mock.patch.dict(os.environ, {"KP_ADAPT_SHORT_STRUCTURE": "1"}):
            a = kp_build.structure_input_signature(ch)
            ch["blocks"][15][0]["text"] += "变更"
            b = kp_build.structure_input_signature(ch)
            self.assertNotEqual(a, b)
        with mock.patch.dict(os.environ, {"KP_ADAPT_SHORT_STRUCTURE": "0"}):
            self.assertNotEqual(b, kp_build.structure_input_signature(ch))


if __name__ == "__main__":
    unittest.main()
