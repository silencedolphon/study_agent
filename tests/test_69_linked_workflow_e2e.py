"""Offline integration tests for the two linked workflows; no real data or network."""
from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import time
import types
import unittest
import urllib.request
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np
import pymupdf

from core import chat, config, db, ingest, ingest_knowledge, pipeline_pool, textbook_kp
from tools import dashboard


class TestLinkedIngestEndToEnd(unittest.TestCase):
    def _run_case(self, auto, runtime="1"):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            root = Path(td)
            raw, logs, index = root / "raw", root / "logs", root / "index"
            for path in (raw, logs, index):
                path.mkdir()
            pdf = pymupdf.open()
            page = pdf.new_page()
            page.insert_text((72, 72), "Receptor activation changes cell response. " * 3)
            pdf.new_page()  # 一个无文本页使文档标记为 partial，但第一页仍可整理。
            pdf.save(raw / "synthetic.pdf")
            pdf.close()
            dbpath = index / "test.sqlite3"
            for key, value in (("DB_PATH", dbpath), ("RAW_DIR", raw),
                               ("LOG_DIR", logs), ("INDEX_DIR", index)):
                stack.enter_context(mock.patch.object(config, key, value))
            stack.enter_context(mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": runtime}))
            stack.enter_context(mock.patch.object(urllib.request, "urlopen",
                                                   side_effect=AssertionError("network forbidden")))
            stack.enter_context(mock.patch.object(ingest, "get_embedder", return_value=object()))
            stack.enter_context(mock.patch.object(ingest, "embed_documents_cached",
                side_effect=lambda texts, **kw: np.zeros((len(texts), 16), dtype=np.float32)))
            stack.enter_context(mock.patch.object(ingest, "page_chunk_enabled", return_value=True))
            calls = []
            def fake_chat(messages, **kwargs):
                if "课程结构分析师" in messages[0]["content"]:
                    data = {"page_titles": [{"page": 1, "title": "Receptor"}],
                            "sections": [{"name": "Receptor", "pages": [1]}]}
                    calls.append("structure")
                else:
                    data = {"name": "Receptor", "summary": "Receptor activation changes cell response.",
                            "points": ["Receptors activate signalling pathways."], "pages": [1], "sources": []}
                    calls.append("knowledge")
                return {"content": json.dumps(data, ensure_ascii=False), "model": "fake",
                        "usage": {"prompt_tokens": 20, "completion_tokens": 30}}
            stack.enter_context(mock.patch.object(chat, "chat_once", side_effect=fake_chat))
            report = dashboard.api_ingest(["synthetic.pdf"], force=False, no_ocr=True,
                                          no_figures=True, db_path=dbpath, auto_kp=auto)
            self.assertEqual(report["added"], 1)
            self.assertIn("1/2", report["details"][0]["warnings"][0])
            if auto:
                self.assertEqual(report["knowledge_results"][0]["status"], "done")
                self.assertIn("不完整", report["knowledge_results"][0]["warning"])
                self.assertEqual(calls, ["structure", "knowledge"])
                artifact = logs / "knowledge" / ("1" + kp_suffix())
                data = json.loads(artifact.read_text(encoding="utf-8"))
                self.assertEqual(data["meta"]["status"], "done")
                self.assertGreater(len(data["knowledge_points"]), 0)
                again = dashboard.api_ingest(["synthetic.pdf"], force=False, no_ocr=True,
                                             no_figures=True, db_path=dbpath, auto_kp=True)
                self.assertTrue(again["knowledge_results"][0]["reused"])
                self.assertIn("复用已有结果 1 份", again["summary"])
                self.assertEqual(calls, ["structure", "knowledge"])
            else:
                self.assertEqual(report["knowledge_results"], [])
                self.assertEqual(calls, [])
                self.assertFalse((logs / "knowledge" / ("1" + kp_suffix())).exists())

    def test_usable_partial_is_automatically_summarized(self):
        self._run_case(True)

    def test_usable_partial_is_summarized_in_compatibility_mode(self):
        self._run_case(True, "0")

    def test_switch_off_does_not_start_summarization(self):
        self._run_case(False)


def kp_suffix():
    from core import kp_build
    return kp_build.KP_SUFFIX


class TestBatchLinkedProgress(unittest.TestCase):
    def test_batch_remains_running_until_linked_summary_finishes(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            root = Path(td)
            path = root / "synthetic.pptx"
            path.write_bytes(b"synthetic")
            started, release = threading.Event(), threading.Event()
            def fake_ingest(*args, **kwargs):
                pipeline_pool._ROUTER.end("done")
                return types.SimpleNamespace(details=[{"status": "added", "doc_id": 1, "chunks": 1,
                                                       "figures": {"cost_cny": 0.1}}])
            def fake_summary(*args, **kwargs):
                started.set()
                if not release.wait(5):
                    raise RuntimeError("test did not release summary")
                kwargs["on_event"]({"ev": "end", "cost_cny": 0.2})
                return {"status": "done", "reason": "知识点已整理", "cost_cny": 0.2}
            stack.enter_context(mock.patch.object(pipeline_pool, "_batches", {}))
            stack.enter_context(mock.patch.object(pipeline_pool, "_active", None))
            stack.enter_context(mock.patch.object(pipeline_pool, "install", return_value=True))
            stack.enter_context(mock.patch.object(ingest, "ingest_paths", side_effect=fake_ingest))
            stack.enter_context(mock.patch.object(ingest_knowledge, "run", side_effect=fake_summary))
            from core import embed
            stack.enter_context(mock.patch.object(embed, "get_embedder", return_value=object()))
            result = pipeline_pool.submit_batch([str(path)], workers=1, db_path=root / "db.sqlite3",
                                                log_dir=root, auto_kp=True)
            try:
                self.assertTrue(started.wait(5), "知识点整理没有启动")
                state = pipeline_pool.snapshot(result["batch_id"])
                self.assertTrue(state["running"])
                self.assertEqual(state["tasks"][0]["status"], "running")
                self.assertIn("知识点", state["tasks"][0]["stage"])
            finally:
                release.set()
            for _ in range(250):
                state = pipeline_pool.snapshot(result["batch_id"])
                if not state["running"]:
                    break
                time.sleep(0.02)
            self.assertFalse(state["running"])
            self.assertEqual(state["succeeded"], 1)
            self.assertEqual(state["tasks"][0]["kp_status"], "done")
            self.assertAlmostEqual(state["cost_cny"], 0.3)


class TestPreviewLinkedEndToEnd(unittest.TestCase):
    def tearDown(self):
        dashboard._PREVIEW_JOIN.clear()

    def _run_case(self, runtime, batch_run=False):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir()
            dbpath = root / "test.db"
            conn = db.connect(dbpath)
            try:
                db.init_db(conn)
                conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,content_hash,role,status,n_chunks) "
                             "VALUES(50,'book.pdf','book.pdf','pdf','测试教材','h','textbook','ok',2)")
                for i in (1, 2):
                    text = ("受体激动药通过结合受体产生作用，剂量和效应存在关系。" * 12) + str(i)
                    conn.execute("INSERT INTO chunks(doc_id,ord,text,text_norm,heading_path,source_ref,char_len,token_est,content_hash,created_at) "
                                 "VALUES(50,?,?,?,?,?,?,?,?,0)",
                                 (i, text, text, "测试教材 > 第1章 受体", "p.%d" % i,
                                  len(text), len(text)//2, db.sha256_text(text)))
                conn.commit()
            finally:
                conn.close()
            stack.enter_context(mock.patch.object(config, "DB_PATH", dbpath))
            stack.enter_context(mock.patch.object(config, "LOG_DIR", logs))
            stack.enter_context(mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": runtime, "PREVIEW_ENABLED": "1"}))
            stack.enter_context(mock.patch.object(urllib.request, "urlopen",
                                                   side_effect=AssertionError("network forbidden")))
            stack.enter_context(mock.patch.object(config, "pipeline_key", return_value=""))
            value_int = config.value_int
            stack.enter_context(mock.patch.object(config, "value_int", side_effect=lambda key, default=0:
                1 if key in ("PREVIEW_ENABLED", "PREVIEW_AUTO_TEXTBOOK_KP") else value_int(key, default)))
            barrier, calls = threading.Barrier(2), []
            def fake_chat(messages, **kwargs):
                kind = "textbook" if "严谨的教材学习助手" in messages[0]["content"] else "preview"
                calls.append(kind)
                barrier.wait(timeout=5)
                if kind == "textbook":
                    body = {"points": [{"title": "受体作用", "summary": "受体结合后产生细胞效应。",
                        "key_points": ["识别与激活"], "source_ids": [1],
                        "questions": [{"question": "如何产生效应？", "answer": "结合受体并激活信号。"}]}],
                        "terms": []}
                else:
                    body = {"overview": "学习受体作用。", "study_order": ["识别", "激活"],
                        "difficulties": ["剂量效应"], "exam_focus": ["受体机制"],
                        "subsections": [{"title": "受体机制", "pages": [1, 2], "why": "理解受体作用",
                            "core": [{"name": "受体", "explain": "结合后传递信号"}],
                            "pitfalls": [], "self_check": [{"q": "受体作用？", "a": "结合后产生效应"}]}]}
                return {"content": json.dumps(body, ensure_ascii=False), "model": "fake",
                        "usage": {"prompt_tokens": 20, "completion_tokens": 30}}
            stack.enter_context(mock.patch.object(chat, "chat_once", side_effect=fake_chat))
            with redirect_stdout(io.StringIO()):
                estimate = dashboard.api_preview_estimate(50, 1)
                self.assertTrue(estimate["ok"])
                book = estimate["textbook_kp"]
                if batch_run:
                    from core import textbook_batch
                    plan = textbook_batch.plan('preview', 50, [1], dashboard._book_batch_estimate)
                    result = textbook_batch.start(plan['token'], dashboard._book_batch_estimate,
                        dashboard._book_batch_launch,
                        lambda _: dashboard.api_preview_progress(), dashboard._book_batch_busy)
                else:
                    result = dashboard.api_preview_start({"doc_id": 50, "chapter": 1,
                        "auto_textbook_kp": True, "book_signature": book["signature"],
                        "book_max_calls": book["calls"], "book_max_output_tokens": book["estimated_output_cap"]})
                self.assertTrue(result["started"])
                if not batch_run:
                    self.assertEqual(result["companion_error"], "")
                for _ in range(500):
                    progress = textbook_batch.snapshot() if batch_run else dashboard.api_preview_progress()
                    if not progress.get("active"):
                        break
                    time.sleep(0.02)
                self.assertFalse(progress["active"])
                if batch_run:
                    self.assertEqual(progress['status'], 'done', progress['items'])
                    # 再次批量运行同一章：预习与教材成果均应复用，不再调用模型。
                    plan = textbook_batch.plan('preview', 50, [1], dashboard._book_batch_estimate)
                    self.assertTrue(plan['items'][0]['estimate']['skip_would_happen'])
                    self.assertEqual(plan['items'][0]['estimate']['textbook_kp']['calls'], 0)
                    result = textbook_batch.start(plan['token'], dashboard._book_batch_estimate,
                        dashboard._book_batch_launch, lambda _: dashboard.api_preview_progress(),
                        dashboard._book_batch_busy)
                    self.assertTrue(result['started'])
                    for _ in range(500):
                        progress = textbook_batch.snapshot()
                        if not progress['active']:
                            break
                        time.sleep(0.02)
                    self.assertEqual(progress['status'], 'done', progress['items'])
                else:
                    self.assertEqual((progress["status"], progress["preview_status"], progress["book_status"]),
                                     ("done", "done", "done"), progress.get("error"))
            self.assertFalse(barrier.broken, "两个管线应并行进入模型阶段")
            self.assertCountEqual(calls, ["preview", "textbook"])
            self.assertTrue((logs / "knowledge" / "50-预习总结-第1章.json").exists())
            self.assertTrue((logs / "knowledge" / "50-教材知识点-第1章.json").exists())
            self.assertFalse(textbook_kp.progress()["active"])

    def test_linked_run_with_runtime(self):
        self._run_case("1")

    def test_linked_run_with_compatibility_runtime(self):
        self._run_case("0")

    def test_batch_runs_linked_real_pipelines_with_mock_models(self):
        self._run_case("1", batch_run=True)


if __name__ == "__main__":
    unittest.main()
