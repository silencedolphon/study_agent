"""P0-B / S03+S04 验收：remote_unknown 禁止自动重发 + 细粒度 single-flight。

零模型：fake `chat.chat_once`/`urlopen`；临时目录/DB。覆盖：
- 网络异常（RemoteDisconnected/URLError/timeout）→ RemoteUnknownError；HTTPError 保持普通失败；
- 无密钥/参数错误不是 unknown；
- kp_build._v2_call / preview._preview_call / figures._http_post 遇 unknown 只请求 1 次；
- figures 的 HTTP 429/5xx 仍可重试；
- SingleFlight：A/B、release、不同键并行、stale、force 不并行、线程并发只一个成功；
- textbook_kp.start：被锁明确报错、完成/异常释放锁、force 不并行、unknown 进后端状态。
"""
from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import chat, figures, kp_build, preview, textbook_kp  # noqa: E402
from core.pipeline_runtime import state as runtime_state  # noqa: E402


def _http_error(code=503, body=b"{}"):
    return urllib.error.HTTPError("http://x/chat/completions", code,
                                  "err", {}, mock.Mock(read=lambda: body))


class TestClassification(unittest.TestCase):
    def test_states(self):
        for name in ("planned", "running", "done", "failed", "partial",
                     "cancelled", "remote_unknown"):
            self.assertIn(name, runtime_state.RUN_STATES)

    def test_http_error_is_known_not_unknown(self):
        exc = _http_error(503)
        self.assertFalse(runtime_state.is_remote_unknown(exc))
        self.assertFalse(runtime_state.request_may_have_been_sent(exc))

    def test_network_exceptions_are_conservatively_unknown(self):
        for exc in (
            urllib.error.URLError(socket.timeout("t")),
            http.client.RemoteDisconnected("closed"),
            socket.timeout("timeout"),
            ConnectionResetError("reset"),
            BrokenPipeError("pipe"),
            TimeoutError("timed out"),
        ):
            self.assertTrue(runtime_state.request_may_have_been_sent(exc), exc)
            self.assertFalse(runtime_state.is_remote_unknown(exc))
        ru = runtime_state.RemoteUnknownError("x", cause=TimeoutError("t"))
        self.assertTrue(runtime_state.is_remote_unknown(ru))
        self.assertTrue(runtime_state.request_may_have_been_sent(ru))

    def test_pre_send_errors_are_known(self):
        for exc in (KeyError("k"), ValueError("bad"), TypeError("t"),
                    chat.ChatError("没有找到 DEEPSEEK_API_KEY")):
            self.assertFalse(runtime_state.request_may_have_been_sent(exc), exc)
            self.assertFalse(runtime_state.is_remote_unknown(exc), exc)


class TestChatUnknown(unittest.TestCase):
    def _call(self, exc):
        with mock.patch.object(chat, "load_api_key", return_value="sk-fake"), \
             mock.patch("urllib.request.urlopen", side_effect=exc):
            return chat.chat_once([{"role": "user", "content": "q"}])

    def test_network_error_raises_remote_unknown(self):
        with self.assertRaises(runtime_state.RemoteUnknownError) as ctx:
            self._call(http.client.RemoteDisconnected("closed"))
        msg = str(ctx.exception)
        self.assertIn("结果未知", msg)
        self.assertNotIn("sk-fake", msg)

    def test_urlerror_and_timeout_unknown(self):
        for exc in (urllib.error.URLError(socket.timeout("t")),
                    TimeoutError("timed out")):
            with self.assertRaises(runtime_state.RemoteUnknownError):
                self._call(exc)

    def test_http_error_is_known(self):
        with self.assertRaises(chat.ChatError) as ctx:
            self._call(_http_error(503))
        self.assertFalse(runtime_state.is_remote_unknown(ctx.exception))

    def test_missing_key_is_known(self):
        with mock.patch.object(chat, "load_api_key",
                               side_effect=chat.ChatError("没有找到 DEEPSEEK_API_KEY")):
            with self.assertRaises(chat.ChatError) as ctx:
                chat.chat_once([{"role": "user", "content": "q"}])
        self.assertFalse(runtime_state.is_remote_unknown(ctx.exception))

    def test_stream_network_error_unknown(self):
        with mock.patch.object(chat, "load_api_key", return_value="sk-fake"), \
             mock.patch("urllib.request.urlopen",
                        side_effect=http.client.RemoteDisconnected("closed")):
            with self.assertRaises(runtime_state.RemoteUnknownError):
                list(chat.stream_chat([{"role": "user", "content": "q"}]))


class TestNoAutoRetry(unittest.TestCase):
    def test_kp_v2_call_unknown_single_attempt(self):
        calls = []

        def fake_chat(*a, **kw):
            calls.append(1)
            raise http.client.RemoteDisconnected("closed")

        with mock.patch.object(chat, "chat_once", side_effect=fake_chat), \
             mock.patch.object(kp_build, "model_slot",
                               return_value=contextlib.nullcontext()), \
             mock.patch.object(kp_build, "_call_with_hard_timeout",
                               side_effect=lambda fn, timeout, kind: fn()):
            rec = kp_build._v2_call([], "knowledge", "sys", "user",
                                    max_tokens=100, retry=3,
                                    log=lambda *a: None)
        self.assertEqual(len(calls), 1, "unknown 后不得自动重试")
        self.assertFalse(rec["ok"])
        self.assertTrue(rec.get("remote_unknown"))
        self.assertIn("RemoteDisconnected", rec["error"])

    def test_preview_preview_call_unknown_single_attempt(self):
        calls = []

        def fake_chat(*a, **kw):
            calls.append(1)
            raise urllib.error.URLError(socket.timeout("t"))

        acc = []
        with mock.patch.object(chat, "chat_once", side_effect=fake_chat), \
             mock.patch.object(kp_build, "model_slot",
                               return_value=contextlib.nullcontext()):
            res = preview._preview_call(acc, "preview.chapter", "sys", "user",
                                        max_tokens=1024, retry=3,
                                        log=lambda *a: None)
        self.assertEqual(len(calls), 1, "unknown 后不得自动重试")
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("remote_unknown"))
        self.assertEqual(len(acc), 1)

    def test_figures_http_post_unknown_single_attempt(self):
        calls = []

        def fake_urlopen(req, timeout):
            calls.append(1)
            raise http.client.RemoteDisconnected("closed")

        payload = {"model": "v", "messages": [{"role": "user", "content": "x"}],
                   "stream": False}
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             mock.patch.object(figures.time, "sleep", return_value=None):
            st, body = figures._http_post("http://x/chat/completions",
                                          payload, "k", retries=4, wait=0)
        self.assertEqual(len(calls), 1, "网络异常不得再 range(retries) 重发")
        self.assertEqual(st, 0)
        self.assertIn("remote_unknown", str(body))
        self.assertIn("结果未知", str(body))

    def test_figures_http_503_retry_preserved(self):
        calls = []

        def fake_urlopen(req, timeout):
            calls.append(1)
            raise _http_error(503)

        payload = {"model": "v", "messages": [{"role": "user", "content": "x"}],
                   "stream": False}
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             mock.patch.object(figures.time, "sleep", return_value=None):
            st, _body = figures._http_post("http://x/chat/completions",
                                           payload, "k", retries=2, wait=0)
        self.assertEqual(len(calls), 3, "HTTP 503 是已知失败，保留现有重试")
        self.assertEqual(st, 503)


class TestSingleFlight(unittest.TestCase):
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

    def test_same_key_busy_then_release(self):
        a = self._acquire()
        self.assertTrue(a["ok"])
        info = self.sf.read(a["key"])
        self.assertTrue(info["token"])
        self.assertEqual(info["pid"], os.getpid())
        self.assertGreater(info["created_at"], 0)
        b = self._acquire()
        self.assertFalse(b["ok"])
        self.assertTrue(b["busy"])
        self.assertEqual(b["holder"]["token"], a["token"])
        self.assertTrue(self.sf.release(a))
        c = self._acquire()
        self.assertTrue(c["ok"])
        self.assertTrue(self.sf.release(c))

    def test_different_keys_parallel(self):
        got = [self._acquire(),
               self._acquire(stage="page"),                # stage 不同
               self._acquire(object="ch:2"),               # object 不同
               self._acquire(doc_id=2),                    # doc 不同
               self._acquire(config_fingerprint="fp2")]    # 配置不同
        for item in got:
            self.assertTrue(item["ok"], item)
        self.assertEqual(len({x["key"] for x in got}), 5, "每个维度都要进键")
        for item in got:
            self.assertTrue(self.sf.release(item))

    def test_stale_lock_reclaimed(self):
        a = self._acquire()
        path = Path(a["path"])
        info = json.loads(path.read_text(encoding="utf-8"))
        info["created_at"] = time.time() - 999
        info["heartbeat_at"] = time.time() - 999
        # D3（task-60）：新语义要求"TTL 超期 **且** 持有者已死"才回收；
        # 这里模拟进程已退出（改 pid 为不存在的值），而不是让当前进程假装死亡。
        info["pid"] = 999999999
        path.write_text(json.dumps(info), encoding="utf-8")
        b = self._acquire()
        self.assertTrue(b["ok"])
        self.assertNotEqual(a["token"], b["token"])
        self.assertTrue(self.sf.release(b))

    def test_force_never_runs_parallel_but_can_take_stale(self):
        a = self._acquire()
        self.assertFalse(self._acquire(force=True)["ok"], "force 也不抢活跃锁")
        path = Path(a["path"])
        info = json.loads(path.read_text(encoding="utf-8"))
        info["created_at"] = time.time() - 999
        info["heartbeat_at"] = time.time() - 999
        info["pid"] = 999999999            # 模拟持有者进程已退出（D3 活性判断）
        path.write_text(json.dumps(info), encoding="utf-8")
        c = self._acquire(force=True)
        self.assertTrue(c["ok"])
        self.assertTrue(self.sf.release(c))

    def test_release_only_own_token(self):
        a = self._acquire()
        wrong = dict(a)
        wrong["token"] = "not-mine"
        self.assertFalse(self.sf.release(wrong))
        self.assertTrue(Path(a["path"]).exists(), "别人的锁不能被误删")
        self.assertTrue(self.sf.release(a))

    def test_thread_concurrency_only_one_wins(self):
        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait(timeout=5)
            got = self._acquire()
            with lock:
                results.append(got)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        winners = [x for x in results if x["ok"]]
        self.assertEqual(len(winners), 1, "并发同键只能一个成功")
        self.assertEqual(sum(1 for x in results if x.get("busy")), 7)
        self.assertTrue(self.sf.release(winners[0]))


class TestTextbookSingleFlight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.logs = self.root / "logs"
        self.lock_dir = self.root / "locks"
        self.plan = {"doc_id": 50, "chapter": 6, "title": "药理学",
                     "chapter_name": "胆碱受体药", "signature": "sig",
                     "segments": []}
        self.state = {"active": False, "status": "", "doc_id": 0, "chapter": 0,
                      "done": 0, "total": 0, "error": "", "started_at": 0.0,
                      "percent": 0.0, "elapsed": 0.0, "label": "", "run_id": ""}
        self.build_fn = lambda *a, **kw: {"ok": True, "status": "done"}
        self._patches = [
            mock.patch.dict(os.environ, {"PIPELINE_LOCK_DIR": str(self.lock_dir)}),
            mock.patch.object(textbook_kp, "_STATE", self.state),
            mock.patch.object(textbook_kp.config, "LOG_DIR", self.logs),
            mock.patch.object(textbook_kp.config, "DB_PATH", self.root / "x.db"),
            mock.patch.object(textbook_kp, "_plan", return_value=self.plan),
            mock.patch.object(textbook_kp, "estimate",
                              return_value={"calls": 1, "estimated_output_cap": 0}),
            mock.patch.object(textbook_kp, "_build",
                              side_effect=lambda *a, **kw: self.build_fn(*a, **kw)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _wait_idle(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not textbook_kp.progress()["active"]:
                return True
            time.sleep(0.01)
        return not textbook_kp.progress()["active"]

    def _start(self, *, force=False):
        return textbook_kp.start(50, 6, "sig", max_calls=1,
                                 max_output_tokens=0, force=force)

    def test_busy_error_and_release_after_done(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def slow_build(*a, **kw):
            calls.append(1)
            started.set()
            release.wait(5)
            return {"ok": True, "status": "done"}

        self.build_fn = slow_build
        r1 = self._start()
        self.assertTrue(r1.get("started"), r1)
        self.assertTrue(started.wait(5))
        r2 = self._start()
        self.assertFalse(r2.get("ok"))
        self.assertTrue(r2.get("busy"))
        self.assertIn("正在生成", r2.get("error", ""))
        r3 = self._start(force=True)     # force 也不与执行者并行
        self.assertFalse(r3.get("ok"))
        release.set()
        self.assertTrue(self._wait_idle())
        self.assertEqual(calls, [1], "第二个请求不得启动执行")
        # 完成后锁已释放 → 可再次开始
        self.build_fn = lambda *a, **kw: {"ok": True, "status": "done"}
        r4 = self._start()
        self.assertTrue(r4.get("started"), r4)
        self.assertTrue(self._wait_idle())

    def test_lock_released_after_exception(self):
        def boom(*a, **kw):
            raise ValueError("boom")

        self.build_fn = boom
        r = self._start()
        self.assertTrue(r.get("started"), r)
        self.assertTrue(self._wait_idle())
        self.assertEqual(textbook_kp.progress()["status"], "failed")
        self.build_fn = lambda *a, **kw: {"ok": True, "status": "done"}
        r2 = self._start()
        self.assertTrue(r2.get("started"), "异常后锁必须释放")
        self.assertTrue(self._wait_idle())

    def test_remote_unknown_backend_status(self):
        def unknown(*a, **kw):
            raise runtime_state.RemoteUnknownError("已发出但没回包")

        self.build_fn = unknown
        r = self._start()
        self.assertTrue(r.get("started"), r)
        self.assertTrue(self._wait_idle())
        pr = textbook_kp.progress()
        self.assertEqual(pr["status"], "remote_unknown")
        self.assertIn("已发出但没回包", pr["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
