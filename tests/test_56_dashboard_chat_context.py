"""P0-G：问答 SSE 端点使用 RunContext 快照的回归（零网络/零模型）。"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
import tools.dashboard as dash  # noqa: E402
from core.pipeline_runtime.context import RunContext  # noqa: E402


class _Handler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass


class _Evidence:
    is_figure = False
    idx = 1

    def to_dict(self):
        return {"idx": 1}


class _Conn:
    def close(self):
        pass


def _ctx(**kw):
    base = dict(
        run_id="chat-test", pipeline="chat", subject_id="default",
        db_path=Path("."), log_dir=Path("."), model="ctx-model",
        api_base="https://ctx.example", thinking=True, reasoning_effort="high",
        max_tokens=0, prompt_version="", schema_version="", credential_ref="chat",
        doc_id=0,
    )
    base.update(kw)
    return RunContext(**base)


class TestChatStreamRunContext(unittest.TestCase):
    def setUp(self):
        self.handler = _Handler()
        self.captured = {}
        from core import chat as chat_mod
        self.patches = [
            mock.patch.object(dash.config, "activate", lambda *a, **k: {}),
            mock.patch.object(dash.config, "CHAT_MODEL", "legacy-model"),
            mock.patch.object(dash.config, "CHAT_REASONING_EFFORT", "legacy-effort"),
            mock.patch.object(dash.config, "value_int", side_effect=self._value_int),
            mock.patch.object(dash, "_conn", lambda: _Conn()),
            mock.patch.object(chat_mod, "gather_evidence", return_value=[_Evidence()]),
            mock.patch.object(chat_mod, "build_messages", return_value=[]),
            mock.patch.object(chat_mod, "stream_chat", side_effect=self._stream,
                              create=True),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _value_int(key, default=0):
        return {"CHAT_MAX_TOKENS": 2048, "CHAT_THINKING": 0,
                "CHAT_FIGURES": 1}.get(key, default)

    def _stream(self, *a, **kw):
        self.captured["stream_kwargs"] = kw
        yield "delta", "x"
        yield "usage", {"total_tokens": 1}

    def test_runtime_context_used_and_default_cap_preserved(self):
        from core.pipeline_runtime import context as runtime_context
        with mock.patch.object(runtime_context, "runtime_enabled", return_value=True), \
             mock.patch.object(runtime_context, "make_context", return_value=_ctx()):
            dash.api_chat_stream(self.handler, {"query": "q"})
        kw = self.captured["stream_kwargs"]
        ctx = kw["run_context"]
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.model, "ctx-model")
        self.assertEqual(ctx.max_tokens, 2048)  # cap=0 时保留端点缺省
        self.assertEqual(kw["model"], "ctx-model")
        self.assertEqual(kw["thinking"], True)
        self.assertEqual(kw["reasoning_effort"], "high")
        events = self._events()
        src = [e for e in events if e.get("type") == "sources"][0]
        self.assertEqual(src["params"]["model"], "ctx-model")
        self.assertEqual(src["params"]["max_tokens"], 2048)

    def test_runtime_disabled_uses_legacy_path(self):
        from core.pipeline_runtime import context as runtime_context
        with mock.patch.object(runtime_context, "runtime_enabled", return_value=False):
            dash.api_chat_stream(self.handler, {"query": "q"})
        kw = self.captured["stream_kwargs"]
        self.assertIsNone(kw["run_context"])
        self.assertEqual(kw["model"], "legacy-model")

    def _events(self):
        raw = self.handler.wfile.getvalue().decode("utf-8")
        out = []
        for line in raw.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
        return out


if __name__ == "__main__":
    unittest.main()
