"""P0-A / S01：RunContext 快照隔离（chat_once/stream_chat 可选透传，零模型）。

覆盖：
  * make_context 在任务入口读一次设置 → 之后 config 切换不影响已建 context；
  * 两个线程各自 context 的 model/base/max_tokens 不同 → payload 零串用；
  * max_tokens=0 → 不发送 max_tokens；流式与非流式都覆盖；
  * 有 context 时**不调用** config.token_cap()/apply_token_cap()（用 side_effect 断言）；
  * AgentRequest 的 pipeline/model/max_tokens/thinking/effort 用 context；
  * PIPELINE_RUNTIME=0 → 忽略 context、走旧全局路径（不声称隔离）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import agent_bridge, chat, config  # noqa: E402
from core.pipeline_runtime import context as runtime_context  # noqa: E402

TOKEN_KEYS = sorted(set(config.TOKEN_CAP_KEYS.values()))


class _Resp:
    status = 200

    def __init__(self, body=None, lines=None):
        self._body = json.dumps(body or {
            "model": "deepseek-flash",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2}}).encode()
        self._lines = list(lines or [])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        return None


class _Isolated(unittest.TestCase):
    """用内存 settings 隔离：不依赖真实 config.local / 环境变量 / 文件写。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._settings = {}
        self._cap_calls = []

        def fake_value_of(key, default=None):
            if key in self._settings:
                return self._settings[key]
            try:
                return config._FIELDS_BY_KEY[key]["default"]
            except Exception:  # noqa: BLE001
                return default

        def fake_value_int(key, default=0):
            try:
                return int(fake_value_of(key, default))
            except (TypeError, ValueError):
                return default

        def fake_token_cap(pipeline):
            self._cap_calls.append(str(pipeline))
            key = config.TOKEN_CAP_KEYS.get(str(pipeline))
            if not key:
                return 0
            try:
                cap = int(self._settings.get(key, 0) or 0)
            except (TypeError, ValueError):
                cap = 0
            return cap if cap > 0 else 0

        self._patches = [
            mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "1"}),
            mock.patch.object(config, "value_of", side_effect=fake_value_of),
            mock.patch.object(config, "value_int", side_effect=fake_value_int),
            mock.patch.object(config, "token_cap", side_effect=fake_token_cap),
            mock.patch.object(config, "pipeline_key", return_value=""),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _set(self, **kv):
        self._settings.update({k: str(v) for k, v in kv.items()})


class TestMakeContext(_Isolated):
    def test_snapshot_frozen_after_settings_changed(self):
        self._set(CHAT_MODEL="model-a", CHAT_BASE_URL="https://a.example/v1",
                  CHAT_THINKING="1", CHAT_REASONING_EFFORT="high",
                  CHAT_MAX_TOKENS="321")
        ctx = runtime_context.make_context("chat", subject="subject-a", doc_id=7)
        snap = ctx.public_dict()
        self.assertEqual(snap["pipeline"], "chat")
        self.assertEqual(snap["subject_id"], "subject-a")
        self.assertEqual(snap["model"], "model-a")
        self.assertEqual(snap["api_base"], "https://a.example/v1")
        self.assertTrue(snap["thinking"])
        self.assertEqual(snap["reasoning_effort"], "high")
        self.assertEqual(snap["max_tokens"], 321)
        self.assertEqual(snap["credential_ref"], "chat", "只存引用，不存密钥")
        self.assertNotIn("key", snap)
        self.assertNotIn("sk-", json.dumps(snap, ensure_ascii=False))
        # 改设置：已建 context 不变
        self._set(CHAT_MODEL="model-b", CHAT_MAX_TOKENS="999")
        self.assertEqual(ctx.model, "model-a")
        self.assertEqual(ctx.max_tokens, 321)

    def test_zero_cap_context_means_no_limit(self):
        self._set(CHAT_MAX_TOKENS="0")
        self.assertEqual(runtime_context.make_context("chat").max_tokens, 0)


class TestChatRunContext(_Isolated):
    def _call_chat(self, ctx, **kw):
        payloads = []

        def fake(req, timeout):
            payloads.append(json.loads(req.data))
            return _Resp()

        with mock.patch.object(chat, "load_api_key", return_value="sk-fake-key"), \
             mock.patch.object(urllib.request, "urlopen", side_effect=fake), \
             mock.patch.object(config, "token_cap",
                               side_effect=AssertionError("context 路径不得读 token_cap")):
            r = chat.chat_once([{"role": "user", "content": "q"}],
                               run_context=ctx, **kw)
        return payloads[-1], r

    def test_context_controls_payload_and_ignores_current_settings(self):
        self._set(CHAT_MODEL="m-ctx", CHAT_BASE_URL="https://ctx.example/v1",
                  CHAT_THINKING="1", CHAT_REASONING_EFFORT="max",
                  CHAT_MAX_TOKENS="222")
        ctx = runtime_context.make_context("chat")
        # 运行中把全局设置切走：context 仍要用快照
        self._set(CHAT_MODEL="m-new", CHAT_MAX_TOKENS="999")
        payload, _ = self._call_chat(ctx)
        self.assertEqual(payload["model"], "m-ctx")
        self.assertEqual(payload["max_tokens"], 222)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "max")

    def test_zero_max_tokens_not_sent(self):
        self._set(CHAT_MAX_TOKENS="0")
        ctx = runtime_context.make_context("chat")
        payload, _ = self._call_chat(ctx)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("reasoning_effort", payload)

    def test_two_threads_contexts_do_not_leak(self):
        self._set(CHAT_MODEL="m1", CHAT_BASE_URL="https://one.example/v1",
                  CHAT_MAX_TOKENS="11")
        ctx1 = runtime_context.make_context("chat")
        self._set(CHAT_MODEL="m2", CHAT_BASE_URL="https://two.example/v1",
                  CHAT_MAX_TOKENS="22")
        ctx2 = runtime_context.make_context("chat")
        captured = {}
        lock = threading.Lock()

        def fake(req, timeout):
            body = json.loads(req.data)
            with lock:
                captured.setdefault(body["model"], []).append(
                    (req.full_url, body.get("max_tokens")))
            return _Resp()

        def worker(ctx):
            with mock.patch.object(chat, "load_api_key", return_value="sk-fake"), \
                 mock.patch.object(urllib.request, "urlopen", side_effect=fake):
                chat.chat_once([{"role": "user", "content": "q"}], run_context=ctx)

        threads = [threading.Thread(target=worker, args=(c,)) for c in (ctx1, ctx2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(set(captured), {"m1", "m2"})
        self.assertEqual(captured["m1"][0][0], "https://one.example/v1/chat/completions")
        self.assertEqual(captured["m1"][0][1], 11)
        self.assertEqual(captured["m2"][0][0], "https://two.example/v1/chat/completions")
        self.assertEqual(captured["m2"][0][1], 22)

    def test_stream_uses_context_and_zero_omits(self):
        self._set(CHAT_MODEL="m-stream", CHAT_BASE_URL="https://s.example/v1",
                  CHAT_THINKING="0", CHAT_MAX_TOKENS="0")
        ctx = runtime_context.make_context("chat")
        payloads = []

        def fake(req, timeout):
            payloads.append(json.loads(req.data))
            body = b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
            return _Resp(lines=body.splitlines(keepends=True))

        with mock.patch.object(chat, "load_api_key", return_value="sk-fake"), \
             mock.patch.object(urllib.request, "urlopen", side_effect=fake), \
             mock.patch.object(config, "token_cap",
                               side_effect=AssertionError("no token_cap")):
            events = list(chat.stream_chat([{"role": "user", "content": "q"}],
                                           run_context=ctx))
        self.assertEqual(payloads[-1]["model"], "m-stream")
        self.assertNotIn("max_tokens", payloads[-1])
        self.assertEqual(payloads[-1]["thinking"], {"type": "disabled"})
        self.assertEqual(events[0][0], "delta")
        # 正数 cap → 发送
        self._set(CHAT_MAX_TOKENS="77")
        ctx2 = runtime_context.make_context("chat")
        with mock.patch.object(chat, "load_api_key", return_value="sk-fake"), \
             mock.patch.object(urllib.request, "urlopen", side_effect=fake), \
             mock.patch.object(config, "token_cap",
                               side_effect=AssertionError("no token_cap")):
            list(chat.stream_chat([{"role": "user", "content": "q"}],
                                  run_context=ctx2))
        self.assertEqual(payloads[-1]["max_tokens"], 77)

    def test_agent_request_uses_context(self):
        reqs = []

        class Adapter:
            def complete(self, request):
                reqs.append(request)
                return {"content": "ok", "model": request.model,
                        "usage": {}, "elapsed": 0.0}

        fake = Adapter()
        agent_bridge.register_adapter("ctx-test", fake)
        self.addCleanup(agent_bridge.bind_adapter, "chat", None)
        agent_bridge.bind_adapter("chat", "ctx-test")
        self._set(CHAT_MODEL="m-adapter", CHAT_THINKING="1",
                  CHAT_REASONING_EFFORT="low", CHAT_MAX_TOKENS="55")
        ctx = runtime_context.make_context("chat")
        with mock.patch.object(config, "token_cap",
                               side_effect=AssertionError("no token_cap")):
            chat.chat_once([{"role": "user", "content": "q"}], run_context=ctx)
        req = reqs[-1]
        self.assertEqual((req.pipeline, req.model, req.max_tokens),
                         ("chat", "m-adapter", 55))
        self.assertTrue(req.thinking)
        self.assertEqual(req.reasoning_effort, "low")

    def test_runtime_disabled_ignores_context_and_uses_legacy_path(self):
        self._set(CHAT_MODEL="legacy-model", CHAT_MAX_TOKENS="88")
        ctx = SimpleNamespace(pipeline="chat", model="ctx-model",
                              api_base="https://ctx.example", thinking=False,
                              reasoning_effort="", max_tokens=999,
                              credential_ref="chat")
        payloads = []

        def fake(req, timeout):
            payloads.append(json.loads(req.data))
            return _Resp()

        with mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "0"}), \
             mock.patch.object(chat, "load_api_key", return_value="sk-fake"), \
             mock.patch.object(urllib.request, "urlopen", side_effect=fake):
            chat.chat_once([{"role": "user", "content": "q"}],
                           model="passed-model", run_context=ctx)
        self.assertEqual(payloads[-1]["model"], "passed-model",
                         "回退旧路径：忽略 context，用调用参数/当前设置")
        self.assertEqual(payloads[-1]["max_tokens"], 88)


if __name__ == "__main__":
    unittest.main(verbosity=2)
