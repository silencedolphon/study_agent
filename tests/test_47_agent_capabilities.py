"""P4 验收：AgentCapabilities / 绑定校验 / 图片与流式语义（零网络）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import agent_bridge as ab  # noqa: E402


def _img_messages():
    return [{"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
    ]}]


class _Fake:
    def __init__(self, caps=None, events=None, error=None, with_stream=True):
        self.caps = caps
        if not with_stream:
            self.stream = None
        self.calls = []
        self.events = events
        self.error = error
        if caps is None:
            self.capabilities = None   # 模拟旧适配器：没有 capabilities() 方法

    def capabilities(self):
        return self.caps

    def complete(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return {"content": "ok", "model": request.model, "usage": {"total_tokens": 1}}

    def stream(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        yield from (self.events or [("delta", "a"), ("done", None)])


class _Base(unittest.TestCase):
    def setUp(self):
        with ab._lock:
            ab._adapters.clear()
            ab._routes.clear()
        self.addCleanup(self._clear)

    def _clear(self):
        with ab._lock:
            ab._adapters.clear()
            ab._routes.clear()

    @staticmethod
    def _req(pipeline="chat", messages=None, response_format=None):
        return ab.AgentRequest(pipeline=pipeline, messages=messages or [{"role": "user", "content": "q"}],
                               model="m", max_tokens=0, thinking=False,
                               reasoning_effort=None, temperature=0.3, timeout=10,
                               response_format=response_format)


class TestLegacyCompat(_Base):
    def test_old_adapter_no_capabilities_bound_all(self):
        fake = _Fake()
        ab.register_adapter("old", fake)
        for pipeline in ab.PIPELINES:
            ab.bind_adapter(pipeline, "old")
            self.assertTrue(ab.has_adapter(pipeline))
        res = ab.complete(self._req(response_format={"type": "json_object"}))
        self.assertEqual(res["content"], "ok")
        # 旧适配器保持旧行为：图片不做能力拒绝，直传
        ab.complete(self._req(messages=_img_messages()))
        self.assertEqual(len(fake.calls), 2)

    def test_old_adapter_stream_falls_back_to_full(self):
        fake = _Fake(with_stream=False)
        ab.register_adapter("old", fake)
        ab.bind_adapter("chat", "old")
        events = list(ab.stream(self._req()))
        self.assertEqual([e[0] for e in events], ["delta", "usage", "done"])
        self.assertEqual(events[0][1], "ok")


class TestCapabilities(_Base):
    def test_bind_unsupported_pipeline_rejected_no_fallback(self):
        fake = _Fake(ab.AgentCapabilities(pipelines=frozenset({"chat"})))
        ab.register_adapter("a", fake)
        ab.bind_adapter("chat", "a")
        with self.assertRaises(ValueError):
            ab.bind_adapter("deck_chat", "a")
        self.assertFalse(ab.has_adapter("deck_chat"), "拒绝绑定后不得 fallback")
        self.assertIsNone(ab.complete(self._req("deck_chat")))
        self.assertEqual(fake.calls, [])

    def test_images_false_rejects_before_adapter(self):
        fake = _Fake(ab.AgentCapabilities(pipelines=frozenset({"chat"}), images=False))
        ab.register_adapter("a", fake)
        ab.bind_adapter("chat", "a")
        with self.assertRaises(ValueError):
            ab.complete(self._req(messages=_img_messages()))
        self.assertEqual(fake.calls, [], "图片请求不得发给 images=False 适配器")

    def test_images_true_passes(self):
        fake = _Fake(ab.AgentCapabilities(pipelines=frozenset({"chat"}), images=True))
        ab.register_adapter("a", fake)
        ab.bind_adapter("chat", "a")
        res = ab.complete(self._req(messages=_img_messages()))
        self.assertEqual(res["content"], "ok")
        self.assertEqual(len(fake.calls), 1)

    def test_json_capability(self):
        fake = _Fake(ab.AgentCapabilities(pipelines=frozenset({"chat"}), json=False))
        ab.register_adapter("a", fake)
        ab.bind_adapter("chat", "a")
        with self.assertRaises(ValueError):
            ab.complete(self._req(response_format={"type": "json_object"}))
        fake.caps = ab.AgentCapabilities(pipelines=frozenset({"chat"}), json=True)
        self.assertEqual(ab.complete(self._req(
            response_format={"type": "json_object"}))["content"], "ok")

    def test_capabilities_dict_and_empty_pipelines(self):
        fake = _Fake({"pipelines": [], "stream": True, "images": True,
                      "json": True, "usage": False})
        ab.register_adapter("a", fake)
        ab.bind_adapter("preview", "a")
        caps, declared = ab.adapter_capabilities(fake)
        self.assertTrue(declared)
        self.assertIsNone(caps.pipelines, "空 pipelines = 旧兼容全部管线")
        self.assertTrue(caps.stream and caps.images and caps.json)
        self.assertFalse(caps.usage)

    def test_invalid_capabilities_raises(self):
        class Bad:
            def capabilities(self):
                return None
            def complete(self, request):
                return {"content": "x"}
        ab.register_adapter("bad", Bad())
        with self.assertRaises(ValueError):
            ab.bind_adapter("chat", "bad")


class TestStream(_Base):
    def test_declared_stream_false_falls_back_once(self):
        fake = _Fake(ab.AgentCapabilities(pipelines=frozenset({"chat"}), stream=False),
                     with_stream=False)
        ab.register_adapter("a", fake)
        ab.bind_adapter("chat", "a")
        events = list(ab.stream(self._req()))
        self.assertEqual([e[0] for e in events], ["delta", "usage", "done"])

    def test_declared_stream_true_uses_stream_method(self):
        fake = _Fake(ab.AgentCapabilities(pipelines=frozenset({"chat"}), stream=True),
                     events=[("delta", "x"), ("usage", {}), ("done", None)])
        ab.register_adapter("a", fake)
        ab.bind_adapter("chat", "a")
        self.assertEqual([e[0] for e in ab.stream(self._req())],
                         ["delta", "usage", "done"])

    def test_stream_image_rejected(self):
        fake = _Fake(ab.AgentCapabilities(pipelines=frozenset({"chat"}), images=False))
        ab.register_adapter("a", fake)
        ab.bind_adapter("chat", "a")
        with self.assertRaises(ValueError):
            ab.stream(self._req(messages=_img_messages()))
        self.assertEqual(fake.calls, [])


class TestDefaultAndErrors(_Base):
    def test_no_adapter_default(self):
        self.assertFalse(ab.has_adapter("chat"))
        self.assertIsNone(ab.complete(self._req()))
        self.assertIsNone(ab.stream(self._req()))

    def test_unbind_restores_default(self):
        fake = _Fake()
        ab.register_adapter("old", fake)
        ab.bind_adapter("chat", "old")
        self.assertIsNotNone(ab.complete(self._req()))
        ab.bind_adapter("chat", None)
        self.assertIsNone(ab.complete(self._req()), "解绑后必须恢复默认路径")

    def test_adapter_error_propagates_no_fallback(self):
        fake = _Fake(error=RuntimeError("agent failed"))
        ab.register_adapter("a", fake)
        ab.bind_adapter("chat", "a")
        with self.assertRaises(RuntimeError):
            ab.complete(self._req())
        with self.assertRaises(RuntimeError):
            list(ab.stream(self._req()))
        self.assertEqual(len(fake.calls), 2, "异常原样上抛，不 fallback 重试")


if __name__ == "__main__":
    unittest.main(verbosity=2)
