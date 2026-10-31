"""P2-A 验收：版本化 stage cache / 崩溃恢复 / 原子切换（零模型/临时库）。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, textbook_kp  # noqa: E402
from core.pipeline_runtime import cache as stage_cache  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        import shutil
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = Path(self.tmp)
        self.db = self.root / "stage_cache.sqlite"
        self.logs = self.root / "logs"
        self._patches = [
            mock.patch.dict(os.environ, {
                "STAGE_CACHE": "1", "STAGE_CACHE_FILE": str(self.db),
                "PIPELINE_RUNTIME": "1"}),
            mock.patch.object(config, "LOG_DIR", self.logs),
        ]
        for p in self._patches:
            p.start()
        self.ctx = SimpleNamespace(run_id="run-x", pipeline="textbook_kp",
                                   subject_id="默认", doc_id=50, max_tokens=0,
                                   model="deepseek-flash",
                                   api_base="https://api.deepseek.com",
                                   thinking=True, reasoning_effort="max",
                                   credential_ref="textbook_kp")

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _plan(self):
        return {"doc_id": 50, "chapter": 6, "title": "药理学",
                "chapter_name": "胆碱受体药", "signature": "sig-1",
                "config_fingerprint": "cfg-1",
                "n_blocks": 2, "chars": 100,
                "segments": [{"hash": "h1", "source_ids": [1], "material": "m"}]}

    @staticmethod
    def _part():
        return {"points": [{"id": "p1", "title": "t", "summary": "s",
                            "key_points": [], "memory_tip": "", "pitfall": "",
                            "questions": [], "source_ids": [1]}],
                "usage": {}, "partial": False, "invalid_points": 0,
                "finish_reason": "stop"}


class TestBasic(_Base):
    def test_path_default_and_override(self):
        self.assertEqual(stage_cache.cache_path(), self.db)
        with mock.patch.dict(os.environ, {"STAGE_CACHE_FILE": ""}):
            self.assertEqual(stage_cache.cache_path(),
                             self.logs / "runtime" / "stage_cache.sqlite")

    def test_begin_run_same_key_reuses_one_row(self):
        a = stage_cache.begin_run(pipeline="textbook_kp", subject="默认",
                                  doc_id=50, config_fingerprint="cfg")
        b = stage_cache.begin_run(pipeline="textbook_kp", subject="默认",
                                  doc_id=50, config_fingerprint="cfg")
        self.assertTrue(a["created"])
        self.assertFalse(b["created"])
        self.assertEqual(a["run_id"], b["run_id"])
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        finally:
            conn.close()

    def test_concurrent_begin_only_one_run(self):
        results = []
        barrier = threading.Barrier(8)
        lock = threading.Lock()

        def worker():
            barrier.wait(timeout=5)
            got = stage_cache.begin_run(pipeline="p", subject="s", doc_id=1,
                                        config_fingerprint="c")
            with lock:
                results.append(got)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(len({r["run_id"] for r in results}), 1)
        self.assertEqual(sum(1 for r in results if r["created"]), 1)


class TestStepFingerprint(_Base):
    def _step(self, run_id):
        stage_cache.put_step(run_id, "plan", "sig-1", config_fingerprint="cfg-1",
                             input_fingerprint="in-1", counts={"segments": 1})
        return run_id

    def test_hit_requires_version_config_input(self):
        run_id = self._step(stage_cache.begin_run(pipeline="p", doc_id=1,
                                                  config_fingerprint="cfg-1")["run_id"])
        hit = stage_cache.get_step(run_id, "plan", "sig-1",
                                   config_fingerprint="cfg-1",
                                   input_fingerprint="in-1")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["counts"]["segments"], 1)
        self.assertIsNone(stage_cache.get_step(run_id, "plan", "sig-1",
                                               config_fingerprint="cfg-2",
                                               input_fingerprint="in-1"))
        self.assertIsNone(stage_cache.get_step(run_id, "plan", "sig-1",
                                               config_fingerprint="cfg-1",
                                               input_fingerprint="in-2"))
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE steps SET cache_version='stage-cache-v0' WHERE run_id=?",
                         (run_id,))
            conn.commit()
        finally:
            conn.close()
        self.assertIsNone(stage_cache.get_step(run_id, "plan", "sig-1",
                                               config_fingerprint="cfg-1",
                                               input_fingerprint="in-1"),
                          "版本不匹配必须 miss")

    def test_old_cache_without_version_is_miss_and_kept(self):
        run_id = stage_cache.begin_run(pipeline="p", doc_id=1,
                                       config_fingerprint="c")["run_id"]
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO steps(run_id, stage, step_key, status, cache_version, "
                "config_fingerprint, input_fingerprint, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, "plan", "x", "done", None, "c", "i", time.time(), time.time()))
            conn.commit()
        finally:
            conn.close()
        self.assertIsNone(stage_cache.get_step(run_id, "plan", "x",
                                               config_fingerprint="c",
                                               input_fingerprint="i"))
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0], 1,
                             "旧 cache 不删除、不自动重跑")
        finally:
            conn.close()


class TestCrashRecovery(_Base):
    def test_stale_running_only(self):
        running = stage_cache.begin_run(pipeline="p", doc_id=1,
                                        config_fingerprint="a")["run_id"]
        done = stage_cache.begin_run(pipeline="p", doc_id=2,
                                     config_fingerprint="a")["run_id"]
        unknown = stage_cache.begin_run(pipeline="p", doc_id=3,
                                        config_fingerprint="a")["run_id"]
        stage_cache.finish_run(done, "done")
        stage_cache.finish_run(unknown, "remote_unknown")
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE runs SET heartbeat_at=0 WHERE run_id IN (?,?,?)",
                         (running, done, unknown))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(stage_cache.mark_stale_runs(stale_after=1), 1)
        self.assertEqual(stage_cache.get_run(running)["status"], "stale")
        self.assertEqual(stage_cache.get_run(done)["status"], "done")
        self.assertEqual(stage_cache.get_run(unknown)["status"], "remote_unknown")
        self.assertFalse(stage_cache.retry_allowed("remote_unknown"))
        self.assertFalse(stage_cache.retry_allowed("done"))
        self.assertTrue(stage_cache.retry_allowed("stale"))
        self.assertTrue(stage_cache.retry_allowed("failed"))

    def test_half_written_output_is_miss_and_atomic_switch(self):
        run_id = stage_cache.begin_run(pipeline="p", doc_id=1,
                                       config_fingerprint="c")["run_id"]
        missing = self.root / "missing.json"
        stage_cache.put_step(run_id, "write", "k", config_fingerprint="c",
                             input_fingerprint="i", output_ref=str(missing))
        self.assertIsNone(stage_cache.get_step(run_id, "write", "k",
                                               config_fingerprint="c",
                                               input_fingerprint="i"))
        out = self.root / "artifact.json"
        out.write_text("old", encoding="utf-8")
        with self.assertRaises(ValueError):
            with stage_cache.staged_output(out) as tmp:
                tmp.write_text("bad", encoding="utf-8")
                raise ValueError("boom")
        self.assertEqual(out.read_text(encoding="utf-8"), "old", "失败不碰旧目标")
        self.assertFalse(list(self.root.glob("artifact.json.tmp-*")))
        stage_cache.write_atomic(out, "new")
        self.assertEqual(out.read_text(encoding="utf-8"), "new")
        stage_cache.put_step(run_id, "write", "k2", config_fingerprint="c",
                             input_fingerprint="i", output_ref=str(out))
        self.assertIsNotNone(stage_cache.get_step(run_id, "write", "k2",
                                                  config_fingerprint="c",
                                                  input_fingerprint="i"))

    def test_no_sensitive_content(self):
        run_id = stage_cache.begin_run(pipeline="p", doc_id=1,
                                       config_fingerprint="c")["run_id"]
        secret = "sk-super-secret"
        body = "教材正文绝不应落盘"
        stage_cache.put_step(run_id, "plan", "k", config_fingerprint="c",
                             input_fingerprint="i",
                             counts={"segments": 2, "secret": secret,
                                     "body": body, "ok": True})
        raw = self.db.read_bytes()
        self.assertNotIn(secret.encode(), raw)
        self.assertNotIn(body.encode(), raw)
        hit = stage_cache.get_step(run_id, "plan", "k", config_fingerprint="c",
                                   input_fingerprint="i")
        self.assertEqual(hit["counts"].get("segments"), 2)


class TestSwitchOffAndIntegration(_Base):
    def test_switch_off_no_db(self):
        with mock.patch.dict(os.environ, {"STAGE_CACHE": "0"}):
            self.assertFalse(stage_cache.enabled())
            self.assertFalse(stage_cache.begin_run(pipeline="p")["enabled"])
            self.assertFalse(self.db.exists(), "STAGE_CACHE=0 不建库")

    def test_textbook_build_plan_step_and_hit(self):
        plan = self._plan()
        calls = []

        def fake_call(seg, pl, run_context=None):
            calls.append(1)
            return self._part()

        with mock.patch.object(textbook_kp, "_call", side_effect=fake_call):
            r1 = textbook_kp._build(plan, force=True, db_path=self.root / "x.db",
                                    log_dir=self.logs, max_calls=5,
                                    run_context=self.ctx)
            r2 = textbook_kp._build(plan, force=True, db_path=self.root / "x.db",
                                    log_dir=self.logs, max_calls=5,
                                    run_context=self.ctx)
        self.assertFalse(r1["plan_cache_hit"])
        self.assertTrue(r2["plan_cache_hit"], "同指纹 plan 阶段应命中缓存")
        self.assertIsInstance(r1["run_id"], str)
        self.assertTrue(r1["run_id"])
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT status FROM runs").fetchone()[0], "done")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
