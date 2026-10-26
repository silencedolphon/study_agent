# -*- coding: utf-8 -*-
"""预习生成失败/卡住的回归（2026-10-27）。

用户现场：doc50 第16章预习卡了很久，最后产物 status=failed，错误
`RemoteDisconnected`；前一次调用输出 16382 token 被 `max_tokens=16384` 打满、
JSON 截断。修复三件事并用本文件锁住：

1. 默认 `PREVIEW_MAX_TOKENS` 从 16384 提到 32768（给思考链+正文留余量）；
2. 等模型槽位最多 300s（`PREVIEW_SLOT_TIMEOUT`），超时立即失败、不再无时限排队；
3. 网络失败/解析失败最多 3 次尝试；解析失败且输出打满时自动加倍 token 上限。

全部用假 `chat_once` / 假 model_slot，**不调真实模型、不写真实数据**。
"""

import contextlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import chat, kp_build, preview  # noqa: E402


class _FakeResponse:
    def __init__(self, content, prompt_tokens=100, completion_tokens=50):
        self.content = content
        self.raw = content
        self.model = "fake-model"
        self.usage = {"prompt_tokens": prompt_tokens,
                      "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens}


class TestPreviewRobustness(unittest.TestCase):
    def test_default_max_tokens_raised(self):
        """默认 32768；不给用户改配置也能容纳第16章那种思考链。"""
        self.assertGreaterEqual(preview.max_tokens(), 32768)
        self.assertGreaterEqual(preview.model_slot_wait_timeout(), 30)

    def test_slot_timeout_no_retry(self):
        """等模型槽位超时：只尝试 1 次，立刻返回可读错误。"""
        calls = []

        class _Boom:
            def __enter__(self):
                raise kp_build.ModelSlotTimeout("等模型槽位超时（测试）")

            def __exit__(self, *a):
                return False

        with mock.patch.object(preview.kp_build, "model_slot",
                               return_value=_Boom()), \
                mock.patch.object(chat, "chat_once") as fake:
            res = preview._preview_call(
                calls, "preview.chapter", "sys", "user",
                max_tokens=1024, retry=2, log=lambda *a: None)
        self.assertFalse(res["ok"])
        self.assertIn("ModelSlotTimeout", res["error"])
        self.assertEqual(len(calls), 1, "等槽超时不应再重试")
        fake.assert_not_called()

    def test_parse_truncation_doubles_cap(self):
        """首次输出打满且 JSON 坏 → 第二次 max_tokens 加倍（1024→2048）。"""
        calls = []
        seq = [
            {"content": '{"a": 1', "model": "fake-model",
             "usage": {"prompt_tokens": 100, "completion_tokens": 1024}},   # 坏 JSON / 打满
            {"content": '{"a": 1}', "model": "fake-model",
             "usage": {"prompt_tokens": 100, "completion_tokens": 5}},
        ]
        with mock.patch.object(chat, "chat_once", side_effect=seq), \
                mock.patch.object(kp_build, "model_slot",
                                  return_value=contextlib.nullcontext()):
            res = preview._preview_call(
                calls, "preview.chapter", "sys", "user",
                max_tokens=1024, retry=1, log=lambda *a: None)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual([c["max_tokens"] for c in calls], [1024, 2048])

    def test_network_error_retries_three_attempts(self):
        """网络错误不再只试 1 次；最多 3 次后给出可读错误。"""
        calls = []
        err = chat.ChatError("调用模型失败：RemoteDisconnected")
        with mock.patch.object(chat, "chat_once", side_effect=err), \
                mock.patch.object(kp_build, "model_slot",
                                  return_value=contextlib.nullcontext()):
            res = preview._preview_call(
                calls, "preview.chapter", "sys", "user",
                max_tokens=1024, retry=2, log=lambda *a: None)
        self.assertFalse(res["ok"])
        self.assertEqual(len(calls), 3)
        self.assertIn("RemoteDisconnected", res["error"])

    def test_status_includes_error(self):
        """preview_status 必须把失败原因交给 UI（章卡才能显示重试原因）。"""
        import inspect
        src = inspect.getsource(preview.preview_status)
        self.assertIn('"error"', src)
        self.assertIn("meta.get(\"error\")", src)


if __name__ == "__main__":
    unittest.main()
