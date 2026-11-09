"""task-39 验收：教材知识点接入设置 + 全管线 token 上限（0=无上限）+ 进度百分比。

全离线：不真实调 AI；chat/figures/textbook_kp 的 HTTP 都被假 urlopen / 假 chat_once 截住。
重点覆盖：
  1) settings_payload 含 textbook_kp 管线与 6 个字段；所有 token 字段默认 0；
  2) config.token_cap / apply_token_cap（0 删除 max_tokens、>0 写入）；
  3) chat_once / stream_chat 按当前管线 cap 发请求（默认不发送 max_tokens）；
  4) figures._http_post 按 ingest_vision/structures cap 应用；
  5) AgentRequest.max_tokens=0 表示无上限；
  6) textbook_kp 模型/端点/密钥/推理/上限从设置读，estimate/start/meta 适配 0；
  7) progress 的 percent/elapsed/label。
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import agent_bridge, chat, config, figures, textbook_kp  # noqa: E402

TOKEN_KEYS = sorted(set(config.TOKEN_CAP_KEYS.values()))


class _Resp:
    status = 200

    def __init__(self, body: dict):
        self._body = json.dumps(body, ensure_ascii=False).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _ok_body(content="{}"):
    return {"model": "deepseek-flash", "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class _Isolated(unittest.TestCase):
    """隔离真实 config.local.json / 环境变量，避免机器上的历史设置污染默认值断言。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings_file = self.root / "config.local.json"
        self._patches = [
            mock.patch.object(config, "SETTINGS_FILE", self.settings_file),
            mock.patch.object(config, "_SETTINGS", {}),
            mock.patch.object(config, "_SETTINGS_MTIME", -1.0),
            mock.patch.object(config, "_LAST_PIPELINE", "chat"),
        ]
        for p in self._patches:
            p.start()
        # 环境变量里若有同名 token 键，会盖过 built-in default；测试期间摘掉再恢复。
        self._env_backup = {k: os.environ.get(k) for k in TOKEN_KEYS}
        for k in TOKEN_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for p in reversed(self._patches):
            p.stop()

    def _set(self, **kv):
        res = config.save_settings({k: str(v) for k, v in kv.items()})
        self.assertTrue(res.get("ok"), res)


# ---------------------------------------------------------------- 设置/字段
class TestSettingsAndCaps(_Isolated):
    def test_settings_payload_has_textbook_pipeline_and_defaults(self):
        payload = config.settings_payload()
        by_id = {p["id"]: p for p in payload["pipelines"]}
        self.assertIn("textbook_kp", by_id)
        tb = by_id["textbook_kp"]
        self.assertEqual(tb["label"], "AI 教材知识点")
        keys = [f["key"] for f in tb["fields"]]
        self.assertEqual(keys, ["TEXTBOOK_KP_MODEL", "TEXTBOOK_KP_BASE_URL",
                                "TEXTBOOK_KP_API_KEY", "TEXTBOOK_KP_THINKING",
                                "TEXTBOOK_KP_REASONING_EFFORT",
                                "TEXTBOOK_KP_MAX_TOKENS"])
        bykey = {f["key"]: f for f in tb["fields"]}
        self.assertEqual(bykey["TEXTBOOK_KP_MODEL"]["value"], "deepseek-flash")
        self.assertEqual(bykey["TEXTBOOK_KP_BASE_URL"]["value"],
                         "https://api.deepseek.com")
        self.assertEqual(bykey["TEXTBOOK_KP_THINKING"]["value"], 1)
        self.assertEqual(bykey["TEXTBOOK_KP_REASONING_EFFORT"]["value"], "max")
        self.assertEqual(bykey["TEXTBOOK_KP_MAX_TOKENS"]["value"], 0)
        self.assertEqual(bykey["TEXTBOOK_KP_API_KEY"]["set"], False)
        # 所有 token 上限字段默认 0（0=无上限）
        values = config.settings_values()
        for key in TOKEN_KEYS:
            self.assertIn(key, config._FIELDS_BY_KEY, "%s 字段缺失" % key)
            self.assertEqual(values[key], 0, "%s 默认应为 0（无上限）" % key)
            self.assertEqual(int(config._FIELDS_BY_KEY[key]["default"]), 0)
            self.assertNotIn(config._FIELDS_BY_KEY[key].get("advanced"), (True,),
                             "%s 不应藏进高级里（要在设置页可见）" % key)

    def test_token_cap_and_apply(self):
        self.assertEqual(config.token_cap("chat"), 0)
        self.assertEqual(config.token_cap("unknown_pipeline"), 0)
        payload = {"model": "x", "max_tokens": 999}
        self.assertEqual(config.apply_token_cap(payload, "chat"),
                         {"model": "x"})
        self._set(CHAT_MAX_TOKENS=123)
        self.assertEqual(config.token_cap("chat"), 123)
        out = config.apply_token_cap({"model": "x"}, "chat")
        self.assertEqual(out["max_tokens"], 123)
        # 负值/0 都按无上限处理
        self._set(CHAT_MAX_TOKENS=0)
        self.assertEqual(config.token_cap("chat"), 0)
        # 每条管线的映射都存在
        for pipe, key in config.TOKEN_CAP_KEYS.items():
            self.assertIn(key, config._FIELDS_BY_KEY, "%s→%s" % (pipe, key))

    def test_pipeline_scope_caps_are_distinct(self):
        self._set(PREVIEW_MAX_TOKENS=77, KP_BUILD_MAX_TOKENS=55,
                  TERMS_MAX_TOKENS=33,
                  DECK_CHAT_MAX_TOKENS=44)
        self.assertEqual(config.token_cap("preview"), 77)
        self.assertEqual(config.token_cap("courseware_kp"), 55)
        self.assertEqual(config.token_cap("kp"), 55)
        self.assertEqual(config.token_cap("terms"), 33)
        self.assertEqual(config.token_cap("deck_chat"), 44)


# ---------------------------------------------------------------- 中央 HTTP 调用
class TestChatCentralCap(_Isolated):
    def _capture_chat(self, **call_kw):
        payloads = []
        with mock.patch.object(chat, "load_api_key", return_value="fake"), \
             mock.patch.object(urllib.request, "urlopen",
                               side_effect=lambda req, timeout: (
                                   payloads.append(json.loads(req.data))
                                   or _Resp(_ok_body()))):
            chat.chat_once([{"role": "user", "content": "q"}], **call_kw)
        return payloads[-1]

    def test_chat_once_default_sends_no_max_tokens(self):
        self.assertNotIn("max_tokens", self._capture_chat())
        self._set(CHAT_MAX_TOKENS=314)
        self.assertEqual(self._capture_chat()["max_tokens"], 314)

    def test_chat_once_respects_current_pipeline_scope(self):
        self._set(PREVIEW_MAX_TOKENS=271)
        with agent_bridge.pipeline_scope("preview"):
            p = self._capture_chat()
        self.assertEqual(p["max_tokens"], 271)
        # 回到 chat 管线：默认无上限
        self.assertNotIn("max_tokens", self._capture_chat())

    def test_stream_chat_payload_has_no_max_tokens_by_default(self):
        payloads = []

        def fake_urlopen(req, timeout):
            payloads.append(json.loads(req.data))
            body = b"data: {\"choices\":[{\"delta\":{\"content\":\"x\"}}]}\n\n"
            body += b"data: [DONE]\n\n"

            class R:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def __iter__(self): return iter(body.splitlines(keepends=True))
                def read(self): return b""
            return R()

        with mock.patch.object(chat, "load_api_key", return_value="fake"), \
             mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            list(chat.stream_chat([{"role": "user", "content": "q"}]))
        self.assertNotIn("max_tokens", payloads[-1])

    def test_agent_request_reports_cap_or_zero(self):
        reqs = []

        class Adapter:
            def complete(self, request):
                reqs.append(request)
                return {"content": "{}", "model": request.model, "usage": {},
                        "elapsed": 0.0}

        fake = Adapter()
        agent_bridge.register_adapter("t39", fake)
        self.addCleanup(agent_bridge.bind_adapter, "chat", None)
        agent_bridge.bind_adapter("chat", "t39")
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=AssertionError("no network")):
            chat.chat_once([{"role": "user", "content": "q"}])
            self.assertEqual(reqs[-1].max_tokens, 0, "0 = 无上限")
            self._set(CHAT_MAX_TOKENS=88)
            chat.chat_once([{"role": "user", "content": "q"}])
            self.assertEqual(reqs[-1].max_tokens, 88)

    def test_figures_http_post_applies_vision_and_structure_caps(self):
        payloads = []

        def fake_urlopen(req, timeout):
            payloads.append(json.loads(req.data))
            return _Resp(_ok_body())

        self._set(FIGURE_MAX_TOKENS=4096, STRUCTURE_MAX_TOKENS=2048)
        base = {"model": "v", "messages": [{"role": "user", "content": "x"}],
                "max_tokens": 9999}
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            with agent_bridge.pipeline_scope("ingest_vision"):
                figures._http_post("http://x/chat/completions", dict(base), "k")
            with agent_bridge.pipeline_scope("structures"):
                figures._http_post("http://x/chat/completions", dict(base), "k")
            # 默认（无 cap 的管线）：字段被删掉
            with agent_bridge.pipeline_scope("ingest_outline"):
                figures._http_post("http://x/chat/completions", dict(base), "k")
        self.assertEqual(payloads[0]["max_tokens"], 4096)
        self.assertEqual(payloads[1]["max_tokens"], 2048)
        self.assertNotIn("max_tokens", payloads[2])


# ---------------------------------------------------------------- textbook_kp
class TestTextbookKpSettings(_Isolated):
    def _plan(self, n=2):
        return {"doc_id": 1, "chapter": 6, "title": "药理学", "chapter_name": "胆碱受体药",
                "signature": "sig", "segments": [
                    {"hash": "s%d" % i, "material": "m%d" % i, "source_ids": [i + 1]}
                    for i in range(n)], "chars": 100, "n_blocks": n}

    def test_estimate_uses_settings_and_zero_cap(self):
        self._set(TEXTBOOK_KP_MODEL="model-x", TEXTBOOK_KP_REASONING_EFFORT="high",
                  TEXTBOOK_KP_MAX_TOKENS=0)
        plan = self._plan(2)
        with mock.patch.object(textbook_kp, "_plan", return_value=plan), \
             mock.patch.object(config, "DB_PATH", self.root / "x.db"), \
             mock.patch.object(config, "LOG_DIR", self.root / "logs"):
            est = textbook_kp.estimate(1, 6, log_dir=self.root / "logs")
        self.assertEqual(est["model"], "model-x")
        self.assertEqual(est["reasoning_effort"], "high")
        self.assertEqual(est["max_tokens"], 0)
        self.assertEqual(est["estimated_output_cap"], 0, "0 = 无上限")
        self._set(TEXTBOOK_KP_MAX_TOKENS=1000)
        with mock.patch.object(textbook_kp, "_plan", return_value=plan), \
             mock.patch.object(config, "DB_PATH", self.root / "x.db"), \
             mock.patch.object(config, "LOG_DIR", self.root / "logs"):
            est2 = textbook_kp.estimate(1, 6, log_dir=self.root / "logs")
        self.assertEqual(est2["max_tokens"], 1000)
        self.assertEqual(est2["estimated_output_cap"], 2000, "2 段 × 1000")

    def test_call_reads_settings_model_base_key_and_cap(self):
        self._set(TEXTBOOK_KP_MODEL="m-kp", TEXTBOOK_KP_BASE_URL="https://kb.example/v1",
                  TEXTBOOK_KP_THINKING=0, TEXTBOOK_KP_REASONING_EFFORT="low",
                  TEXTBOOK_KP_MAX_TOKENS=0)
        captured = {}

        def fake_chat(messages, **kw):
            captured.update(kw)
            point = {"title": "t", "summary": "s", "key_points": ["k"],
                     "memory_tip": "", "pitfall": "",
                     "questions": [{"question": "q", "answer": "a"}],
                     "source_ids": [1]}
            return {"content": json.dumps({"points": [point]}, ensure_ascii=False),
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    "model": kw.get("model"), "elapsed": 0.0, "finish_reason": ""}

        seg = {"hash": "s0", "material": "m", "source_ids": [1]}
        plan = self._plan(1)
        with mock.patch.object(config, "pipeline_key", return_value="key-x"), \
             mock.patch.object(textbook_kp.chat, "chat_once", side_effect=fake_chat), \
             mock.patch.object(textbook_kp.kp_build, "model_slot",
                               return_value=contextlib.nullcontext()), \
             mock.patch.object(textbook_kp.kp_build, "_call_with_hard_timeout",
                               side_effect=lambda fn, timeout, kind: fn()):
            textbook_kp._call(seg, plan)
        self.assertEqual(captured["model"], "m-kp")
        self.assertEqual(captured["api_base"], "https://kb.example/v1")
        self.assertEqual(captured["api_key"], "key-x")
        self.assertEqual(captured["thinking"], False)
        self.assertIsNone(captured["reasoning_effort"], "推理关时不发强度")
        self.assertEqual(captured["max_tokens"], 0, "0 = 无上限")
        self._set(TEXTBOOK_KP_MAX_TOKENS=777)
        with mock.patch.object(config, "pipeline_key", return_value="key-x"), \
             mock.patch.object(textbook_kp.chat, "chat_once", side_effect=fake_chat), \
             mock.patch.object(textbook_kp.kp_build, "model_slot",
                               return_value=contextlib.nullcontext()), \
             mock.patch.object(textbook_kp.kp_build, "_call_with_hard_timeout",
                               side_effect=lambda fn, timeout, kind: fn()):
            textbook_kp._call(seg, plan)
        self.assertEqual(captured["max_tokens"], 777)

    def test_build_artifact_meta_and_progress_percent(self):
        self._set(TEXTBOOK_KP_MODEL="m-meta", TEXTBOOK_KP_REASONING_EFFORT="high",
                  TEXTBOOK_KP_MAX_TOKENS=0)
        plan = self._plan(2)
        logs = self.root / "logs"
        fake_point = {"id": "p1", "title": "t", "summary": "s", "key_points": [],
                      "memory_tip": "", "pitfall": "", "questions": [],
                      "source_ids": [1]}

        def fake_call(segment, plan_, run_context=None):
            return {"points": [dict(fake_point)], "usage": {"prompt_tokens": 1,
                    "completion_tokens": 1}, "model": "m-meta", "thinking": True,
                    "reasoning_effort": "high", "output_cap": 0, "finish_reason": ""}

        with mock.patch.object(textbook_kp, "_call", side_effect=fake_call):
            result = textbook_kp._build(plan, force=True, db_path=self.root / "x.db",
                                        log_dir=logs, max_calls=2)
        self.assertEqual(result["status"], "done")
        final = textbook_kp._read(textbook_kp._paths(1, 6, logs)[0])
        self.assertEqual(final["model"], "m-meta")
        self.assertEqual(final["thinking"], True)
        self.assertEqual(final["reasoning_effort"], "high")
        self.assertEqual(final["max_tokens"], 0)
        self.assertEqual(final["output_cap"], 0)
        pr = textbook_kp.progress()
        self.assertEqual((pr["done"], pr["total"]), (2, 2))
        self.assertEqual(pr["percent"], 100.0)
        self.assertGreaterEqual(pr["elapsed"], 0.0)

    def test_start_allows_zero_output_cap_and_progress_contract(self):
        plan = self._plan(2)
        with mock.patch.object(textbook_kp, "_plan", return_value=plan), \
             mock.patch.object(config, "DB_PATH", self.root / "x.db"), \
             mock.patch.object(config, "LOG_DIR", self.root / "logs"), \
             mock.patch.object(textbook_kp, "estimate",
                               return_value={"calls": 2, "estimated_output_cap": 0}), \
             mock.patch.object(textbook_kp, "_build",
                               return_value={"ok": True, "status": "done"}) as b:
            r = textbook_kp.start(1, 6, "sig", max_calls=2, max_output_tokens=0)
            self.assertTrue(r.get("started"), r)
            for _ in range(100):
                if not textbook_kp.progress()["active"]:
                    break
                import time as _t
                _t.sleep(0.01)
        pr = textbook_kp.progress()
        self.assertEqual(pr["status"], "done")
        self.assertEqual(pr["percent"], 100.0)
        self.assertTrue(pr["label"].startswith("第6章"))
        self.assertEqual(pr["doc_id"], 1)
        self.assertEqual(pr["chapter"], 6)
        for key in ("active", "status", "doc_id", "chapter", "done", "total",
                    "percent", "elapsed", "label", "error"):
            self.assertIn(key, pr, "progress 契约字段缺 %s" % key)
        self.assertTrue(b.called)

    def test_progress_percent_and_elapsed_calc(self):
        with mock.patch.object(textbook_kp, "_STATE", {
                "active": True, "status": "running", "doc_id": 1, "chapter": 6,
                "done": 1, "total": 4, "error": "", "started_at":
                    __import__("time").time() - 3.2, "percent": 0.0,
                "elapsed": 0.0, "label": "第6章 胆碱受体药"}), \
             mock.patch.object(textbook_kp, "_LOCK", __import__("threading").RLock()):
            pr = textbook_kp.progress()
        self.assertEqual(pr["percent"], 25.0)
        self.assertGreaterEqual(pr["elapsed"], 3.0)
        self.assertEqual(pr["label"], "第6章 胆碱受体药")


class TestDocsAndSemantics(unittest.TestCase):
    def test_docs_agent_adapter_mentions_zero_means_unlimited(self):
        text = (env.PROJECT / "docs" / "agent-adapter.md").read_text(encoding="utf-8")
        self.assertIn("max_tokens", text)
        self.assertIn("0", text)
        self.assertTrue("无上限" in text or "不限" in text
                        or "无显式上限" in text,
                        "文档需说明 max_tokens=0 表示无上限")


if __name__ == "__main__":
    unittest.main(verbosity=2)
