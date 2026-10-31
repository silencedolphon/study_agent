"""task-60（GPT 修复 C/D）：凭据别名 / 硬超时 remote_unknown / SingleFlight 活性 / UI 状态字段。

覆盖（**零真实模型/网络/密钥/数据**）：
  C. RunContext 凭据别名：courseware_kp/textbook_teach→kp、
     ingest_vision/structures/ingest_outline→ingest、terms→kp、deck_chat→deck；
     make_context 不读最近管线；chat_once/stream_chat 按 canonical ref 取 key，
     假两把钥匙 + 模拟"最近激活管线=预习"，课件知识点请求必须用 KP 假钥。
  D1. 硬超时 ModelCallHang 标 remote_unknown/indeterminate，不重试；
      attempts/calls 记账与返回字段、progress/meta 可表达。
  D3. SingleFlight heartbeat+pid 活性：活着的 running 锁即使 TTL 过期也不被抢；
      心跳续租后仍 busy；持有者死 + 心跳超期才回收；force 不抢活跃。
  D4. 现有 progress/meta 字段可表达 remote_unknown/partial（不新增页面）。
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import agent_bridge, chat, config, kp_build  # noqa: E402
from core.pipeline_runtime import context as runtime_context  # noqa: E402
from core.pipeline_runtime import state as runtime_state  # noqa: E402

K = "sk-KP-fake-DO-NOT-LEAK"
P = "sk-PREVIEW-fake-DO-NOT-LEAK"


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


class TestCredentialAliases(unittest.TestCase):
    def test_mapping(self):
        cases = {
            "courseware_kp": "kp", "textbook_teach": "kp", "terms": "kp",
            "ingest_vision": "ingest", "structures": "ingest",
            "ingest_outline": "ingest", "deck_chat": "deck",
            "chat": "chat", "kp": "kp", "ingest": "ingest", "deck": "deck",
            "preview": "preview", "textbook_kp": "textbook_kp",
            "unknown_pipeline": "unknown_pipeline",
        }
        for raw, want in cases.items():
            self.assertEqual(runtime_context.canonical_credential_ref(raw), want)

    def test_make_context_canonical_ref_and_no_key(self):
        with mock.patch.object(agent_bridge, "current_pipeline",
                               side_effect=AssertionError("make_context 不得读最近管线")), \
             mock.patch.object(config, "activate",
                               side_effect=AssertionError("make_context 不得 activate 管线")):
            ctx = runtime_context.make_context("courseware_kp", subject="s", doc_id=3)
        self.assertEqual(ctx.pipeline, "courseware_kp", "审计仍保留细粒度管线名")
        self.assertEqual(ctx.credential_ref, "kp", "凭据引用必须是设置中心管线名")
        snap = ctx.public_dict()
        self.assertEqual(snap["credential_ref"], "kp")
        self.assertNotIn(K, json.dumps(snap))
        self.assertNotIn(P, json.dumps(snap))
        # 其它别名
        self.assertEqual(runtime_context.make_context("textbook_teach").credential_ref, "kp")
        self.assertEqual(runtime_context.make_context("ingest_vision").credential_ref, "ingest")
        self.assertEqual(runtime_context.make_context("structures").credential_ref, "ingest")
        self.assertEqual(runtime_context.make_context("ingest_outline").credential_ref, "ingest")
        self.assertEqual(runtime_context.make_context("deck_chat").credential_ref, "deck")

    def _fake_key(self, pipeline):
        return {"kp": K, "preview": P, "ingest": "sk-INGEST-fake"}.get(str(pipeline), "")

    def _call_and_capture(self, ctx, stream=False):
        headers = []

        def fake(req, timeout):
            headers.append(req.headers.get("Authorization"))
            if stream:
                return _Resp(lines=[b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n',
                                    b"data: [DONE]\n\n"])
            return _Resp()

        with mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "1"}), \
             mock.patch.object(config, "pipeline_key", side_effect=self._fake_key), \
             mock.patch.object(chat, "load_api_key", return_value=P), \
             mock.patch.object(agent_bridge, "complete", return_value=None), \
             mock.patch.object(agent_bridge, "stream", return_value=None), \
             mock.patch.object(urllib.request, "urlopen", side_effect=fake):
            if stream:
                list(chat.stream_chat([{"role": "user", "content": "q"}],
                                      run_context=ctx))
            else:
                chat.chat_once([{"role": "user", "content": "q"}],
                               run_context=ctx)
        return headers

    def test_chat_uses_canonical_kp_key_not_recent_preview(self):
        """GPT 3C 反例：两把钥匙 + 最近激活预习，课件知识点必须用 KP 假钥。"""
        import os as _os
        with mock.patch.dict(_os.environ, {"PIPELINE_RUNTIME": "1"}):
            with agent_bridge.pipeline_scope("preview"):     # 模拟最近激活管线=预习
                ctx = runtime_context.make_context("courseware_kp")
                headers = self._call_and_capture(ctx)
                headers_stream = self._call_and_capture(ctx, stream=True)
        for hs in (headers, headers_stream):
            self.assertTrue(hs, "没有发出请求")
            for auth in hs:
                self.assertEqual(auth, "Bearer " + K,
                                 "课件知识点用了非 KP 密钥（旧 bug：回退到最近管线）")
                self.assertNotIn(P, auth or "")

    def test_ingest_aliases_use_ingest_key(self):
        with mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "1"}):
            for name in ("ingest_vision", "structures", "ingest_outline"):
                ctx = runtime_context.make_context(name)
                hs = self._call_and_capture(ctx)
                self.assertEqual(hs, ["Bearer sk-INGEST-fake"], name)


class TestModelCallHangUnknown(unittest.TestCase):
    def test_hang_marked_unknown_indeterminate(self):
        e = kp_build.ModelCallHang("hard timeout after 1800s")
        self.assertTrue(getattr(e, "remote_unknown", False))
        self.assertTrue(getattr(e, "indeterminate", False))
        self.assertTrue(runtime_state.is_remote_unknown(e))
        self.assertTrue(runtime_state.is_unknown_failure(e))
        self.assertTrue(runtime_state.request_may_have_been_sent(e))

    def test_v2_call_hang_no_retry_and_telemetry(self):
        calls = []
        n = {"n": 0}

        def hang(*a, **kw):
            n["n"] += 1
            raise kp_build.ModelCallHang("硬超时：在飞请求未返回")

        with mock.patch.object(chat, "chat_once", side_effect=AssertionError("不该真发请求")), \
             mock.patch.object(kp_build, "model_slot",
                               return_value=contextlib.nullcontext()), \
             mock.patch.object(kp_build, "_call_with_hard_timeout", side_effect=hang):
            res = kp_build._v2_call(calls, "knowledge", "sys", "user",
                                    max_tokens=100, retry=3, log=lambda *a: None)
        self.assertEqual(n["n"], 1, "ModelCallHang 不得自动重试")
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("remote_unknown"))
        self.assertTrue(res.get("indeterminate"))
        self.assertTrue(res.get("hang"))
        self.assertEqual(res.get("status"), runtime_state.REMOTE_UNKNOWN)
        self.assertEqual(len(calls), 1, "失败也占一行 attempts")
        rec = calls[0]
        self.assertTrue(rec.get("remote_unknown"))
        self.assertTrue(rec.get("indeterminate"))
        self.assertEqual(rec.get("status"), runtime_state.REMOTE_UNKNOWN)
        self.assertIn("ModelCallHang", res["error"])

    def test_progress_and_meta_can_express_unknown(self):
        base = {"active": False, "status": "failed", "done": 3, "total": 5,
                "error": "调用失败（结果未知，可能已发出，禁止自动重试）",
                "label": "", "elapsed": 1.0}
        with mock.patch.object(kp_build.progress, "snapshot", return_value=dict(base)):
            snap = kp_build.progress_snapshot()
        self.assertTrue(snap["remote_unknown"])
        self.assertTrue(snap["indeterminate"])
        self.assertEqual(snap["status_detail"], "remote_unknown")
        self.assertFalse(snap["partial"])
        base2 = dict(base, status="partial", error="")
        with mock.patch.object(kp_build.progress, "snapshot", return_value=dict(base2)):
            snap2 = kp_build.progress_snapshot()
        self.assertTrue(snap2["partial"])
        self.assertFalse(snap2["remote_unknown"])

        calls = [{"attempt": 1, "remote_unknown": True, "indeterminate": True}]
        with mock.patch.object(kp_build, "_v2_cost",
                               return_value={"calls": 1, "calls_failed": 1,
                                             "tokens_in": 0, "tokens_out": 0,
                                             "reasoning_tokens": 0, "cost_cny": 0.0}), \
             mock.patch.object(kp_build, "_v2_coverage",
                               return_value={"pages_total": 1, "covered": 0,
                                             "uncovered": [1]}), \
             mock.patch.object(kp_build, "_v2_runs", return_value=[]):
            meta = kp_build._v2_meta(
                {"doc_id": 1, "title": "t", "deck": "d", "segments": []},
                {"path": "p", "sha256": "s", "chars": 1},
                {"path": "p", "sha256": "s", "chars": 1},
                {}, [], [], calls, status="failed", started=time.perf_counter(),
                budget=0, struct_reused=False, failed=[], err="结果未知", run_no=1,
                budget_unlimited=True)
        self.assertTrue(meta["remote_unknown"])
        self.assertTrue(meta["indeterminate"])
        self.assertEqual(meta["status_detail"], "remote_unknown")
        self.assertFalse(meta["partial"])


class TestSingleFlightLiveness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.sf = runtime_state.SingleFlight(lock_dir=self.dir, stale_after=60)

    def _acquire(self, **kw):
        base = dict(pipeline="p", subject="s", doc_id=1, stage="chapter",
                    object="ch:1", config_fingerprint="fp")
        base.update(kw)
        return self.sf.try_acquire(**base)

    def _age(self, handle, *, pid):
        path = Path(handle["path"])
        info = json.loads(path.read_text(encoding="utf-8"))
        info["created_at"] = time.time() - 999
        info["heartbeat_at"] = time.time() - 999
        info["pid"] = pid
        path.write_text(json.dumps(info), encoding="utf-8")

    def test_alive_holder_not_stolen_even_if_ttl_expired(self):
        a = self._acquire()
        self._age(a, pid=os.getpid())          # 持有者=当前进程（活）
        b = self._acquire()
        self.assertFalse(b["ok"])
        self.assertTrue(b["busy"])
        self.assertEqual(b["holder"]["pid"], os.getpid())
        # force 同样不抢活着的 running
        self.assertFalse(self._acquire(force=True)["ok"])
        self.assertTrue(self.sf.release(a))

    def test_heartbeat_renews_lease(self):
        a = self._acquire()
        self._age(a, pid=os.getpid())
        self.assertFalse(self._acquire()["ok"], "心跳前超 TTL 但 PID 活也必须 busy")
        self.assertTrue(self.sf.heartbeat(a))
        info = json.loads(Path(a["path"]).read_text(encoding="utf-8"))
        self.assertGreater(float(info["heartbeat_at"]), time.time() - 10)
        b = self._acquire()
        self.assertFalse(b["ok"], "心跳续租后仍应 busy")
        self.assertTrue(self.sf.release(a))

    def test_dead_holder_stale_reclaimed(self):
        a = self._acquire()
        self._age(a, pid=999999999)            # 不存在的进程
        b = self._acquire()
        self.assertTrue(b["ok"], "持有者死亡 + 心跳超期 → 可回收")
        self.assertNotEqual(a["token"], b["token"])
        self.assertTrue(self.sf.release(b))

    def test_dead_holder_fresh_heartbeat_not_reclaimed(self):
        a = self._acquire()
        path = Path(a["path"])
        info = json.loads(path.read_text(encoding="utf-8"))
        info["pid"] = 999999999               # 死了，但心跳还新鲜
        path.write_text(json.dumps(info), encoding="utf-8")
        b = self._acquire()
        self.assertFalse(b["ok"], "心跳未超期不回收（避免误抢刚死的短任务）")
        self.assertTrue(self.sf.release(a))

    def test_legacy_stale_without_pid_reclaimed(self):
        a = self._acquire()
        path = Path(a["path"])
        info = json.loads(path.read_text(encoding="utf-8"))
        info["created_at"] = time.time() - 999
        info["heartbeat_at"] = time.time() - 999
        info.pop("pid", None)                  # 旧格式锁：没有 pid
        path.write_text(json.dumps(info), encoding="utf-8")
        b = self._acquire()
        self.assertTrue(b["ok"], "旧格式过期锁（无 pid）可回收")
        self.assertTrue(self.sf.release(b))

    def test_heartbeat_wrong_token_rejected(self):
        a = self._acquire()
        wrong = dict(a)
        wrong["token"] = "not-mine"
        self.assertFalse(self.sf.heartbeat(wrong))
        self.assertTrue(Path(a["path"]).exists())
        self.assertTrue(self.sf.release(a))

    def test_release_still_only_own_token(self):
        a = self._acquire()
        wrong = dict(a)
        wrong["token"] = "not-mine"
        self.assertFalse(self.sf.release(wrong))
        self.assertTrue(self.sf.release(a))


if __name__ == "__main__":
    unittest.main(verbosity=2)
