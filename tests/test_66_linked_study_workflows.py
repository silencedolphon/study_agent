"""离线验收联动、手动对应和有界并发；所有模型调用均为替身。"""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from core import config, db, deck_chapter, ingest_knowledge, preview, textbook_kp
from tests import test_60_gptfix_textbook as fixture
from tools import dashboard


class TestTerms(unittest.TestCase):
    def test_terms_are_grounded_prioritized_and_sources_checked(self):
        items = [
            {"term": "受体", "en": "", "definition": "识别信号", "source_ids": [1]},
            {"term": "半衰期", "en": "half-life", "definition": "浓度降半所需时间",
             "source_ids": [2, 999]},
            {"term": "不存在", "en": "half-life", "definition": "杜撰"},
            {"term": "分布", "en": "imaginary", "definition": "药物分散", "source_ids": 1},
        ]
        result = textbook_kp._clean_terms(items, [1, 2], "半衰期 half-life；受体；分布")
        self.assertEqual([x["term"] for x in result], ["半衰期", "受体", "分布"])
        self.assertEqual(result[0]["source_ids"], [2])
        self.assertEqual(result[-1]["en"], "")
        self.assertEqual(result[-1]["source_scope"], "segment")
        merged = textbook_kp._merge_terms(result + [dict(result[0], source_ids=[1])])
        self.assertEqual(merged[0]["source_ids"], [2, 1])


class TestManualLink(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dbpath = self.root / "test.db"
        self.conn = db.connect(self.dbpath)
        self.addCleanup(self.conn.close)
        db.init_db(self.conn)
        self.conn.executemany(
            "INSERT INTO documents(id,path,abs_path,kind,title,content_hash,role,status) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [(1, "deck.pptx", "deck.pptx", "pptx", "课件", "a", "courseware", "ok"),
             (2, "book.pdf", "book.pdf", "pdf", "教材", "b", "textbook", "ok")])
        self.conn.commit()
        self.chapters = {"ok": True, "chapters": [{"no": 3, "name": "受体"}]}

    def test_bind_context_clear_and_subject_isolation(self):
        with mock.patch.object(preview, "list_chapters", return_value=self.chapters), \
             mock.patch.object(preview, "read_preview_artifact", return_value={
                 "ok": True, "artifact": {"summary": {"difficulties": ["难点"], "exam_focus": ["考点"]}}}), \
             mock.patch.object(textbook_kp, "read_summary", return_value={
                 "exists": True, "terms": [{"term": "受体", "en": "receptor", "definition": "识别信号"}]}):
            deck_chapter.save(self.conn, 1, 2, 3, log_dir=self.root, db_path=self.dbpath)
            data = deck_chapter.get(self.conn, 1, log_dir=self.root, db_path=self.dbpath)
            self.assertEqual(data["binding"]["chapter"], 3)
            self.assertEqual(data["difficulties"], ["难点"])
            self.assertEqual(data["exam_focus"], ["考点"])
            self.assertEqual(data["terms"][0]["en"], "receptor")
            self.assertIsNone(deck_chapter.get(self.conn, 1, log_dir=self.root / "other",
                                               db_path=self.dbpath)["binding"])
            with self.assertRaises(ValueError):
                deck_chapter.save(self.conn, 1, 2, 4, log_dir=self.root, db_path=self.dbpath)
            deck_chapter.save(self.conn, 1, 0, 0, log_dir=self.root, db_path=self.dbpath)
            self.assertIsNone(deck_chapter.get(self.conn, 1, log_dir=self.root,
                                               db_path=self.dbpath)["binding"])


class TestIngestJoin(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE documents(id INTEGER PRIMARY KEY, role TEXT, status TEXT)")
        self.conn.execute("INSERT INTO documents VALUES(1,'courseware','ok')")
        self.conn.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, text TEXT)")

    def test_completed_is_reused_and_incomplete_is_reported(self):
        with mock.patch.object(ingest_knowledge.kp_build, "estimate", return_value={"ok": True}), \
             mock.patch.object(ingest_knowledge.kp_build, "prev_info", return_value={"exists": True}), \
             mock.patch.object(ingest_knowledge.kp_batch, "done_state", return_value=(True, "已有")), \
             mock.patch.object(ingest_knowledge.kp_build, "build") as call:
            result = ingest_knowledge.run(self.conn, {"status": "skipped", "doc_id": 1}, log_dir=self.root)
            self.assertEqual(result["status"], "skipped")
            call.assert_not_called()
        result = ingest_knowledge.run(self.conn, {"status": "partial", "doc_id": 1}, log_dir=self.root)
        self.assertEqual(result["status"], "failed")
        self.assertIn("未提取到可用内容", result["reason"])

    def test_usable_partial_courseware_is_summarized_with_warning(self):
        self.conn.execute("UPDATE documents SET status='partial' WHERE id=1")
        self.conn.execute("INSERT INTO chunks VALUES(1, 1, '可用的课件文字')")
        with mock.patch.object(ingest_knowledge.kp_build, "estimate", return_value={"ok": True}), \
             mock.patch.object(ingest_knowledge.kp_build, "prev_info", return_value={"exists": False}), \
             mock.patch.object(ingest_knowledge.kp_batch, "done_state", return_value=(False, "")), \
             mock.patch.object(ingest_knowledge.kp_build, "build", return_value={
                 "ok": True, "status": "done", "meta": {"usage_total_this_run": {"cost_cny": 0.12}}}) as call:
            result = ingest_knowledge.run(self.conn, {"status": "added", "doc_id": 1}, log_dir=self.root)
        self.assertEqual(result["status"], "done")
        self.assertIn("不完整", result["warning"])
        self.assertAlmostEqual(result["cost_cny"], 0.12)
        call.assert_called_once()

    def test_old_summary_is_backed_up_and_failure_reported(self):
        path = self.root / "knowledge" / ("1" + ingest_knowledge.kp_build.KP_SUFFIX)
        path.parent.mkdir()
        path.write_text("old", encoding="utf-8")
        with mock.patch.object(ingest_knowledge.kp_build, "estimate", return_value={"ok": True}), \
             mock.patch.object(ingest_knowledge.kp_build, "prev_info", return_value={"exists": True}), \
             mock.patch.object(ingest_knowledge.kp_batch, "done_state", return_value=(False, "")), \
             mock.patch.object(ingest_knowledge.kp_build, "build",
                               return_value={"ok": True, "status": "failed", "error": "网络中断"}):
            result = ingest_knowledge.run(self.conn, {"status": "added", "doc_id": 1}, log_dir=self.root)
        self.assertEqual(result["status"], "failed")
        self.assertIn("网络中断", result["reason"])
        self.assertEqual(Path(result["backup"]).read_text(encoding="utf-8"), "old")

    def test_manual_summarization_is_blocked_while_single_ingest_runs(self):
        with dashboard._INGEST_LOCK:
            single = dashboard.api_kp_build_start({"doc_id": 1})
            batch = dashboard.api_kp_build_batch_start({"doc_ids": [1]})
        self.assertIn("入库正在进行", single["error"])
        self.assertIn("入库正在进行", batch["error"])

    def test_ingest_progress_switches_to_knowledge_stage(self):
        with dashboard._INGEST_LOCK, \
             mock.patch.object(dashboard.kp_build_mod, "is_running", return_value=True), \
             mock.patch.object(dashboard.kp_build_mod, "progress_snapshot", return_value={
                 "active": True, "kind": "kp_build", "stage": "写知识点", "percent": 60}):
            result = dashboard.api_ingest_progress()
        self.assertEqual(result["kind"], "kp_build")
        self.assertEqual(result["percent"], 60)


class TestPreviewJoin(unittest.TestCase):
    def tearDown(self):
        dashboard._PREVIEW_JOIN.clear()

    def test_preview_chapter_includes_textbook_result_status(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(config, "LOG_DIR", Path(td)), \
             mock.patch.object(dashboard, "_preview_list_chapters", return_value={
                 "ok": True, "title": "教材", "chapters": [{"no": 1}, {"no": 2}]}), \
             mock.patch.object(dashboard, "_preview_status_map", return_value={}), \
             mock.patch.object(config, "value_int", return_value=1):
            final, pending, _ = textbook_kp._paths(5, 1)
            final.parent.mkdir(parents=True)
            final.write_text(json.dumps({"status": "done", "points": [{"id": "p"}]}), encoding="utf-8")
            pending.write_text("{}", encoding="utf-8")
            result = dashboard.api_preview_chapters(mock.Mock(), 5)
        self.assertTrue(result["auto_textbook_kp"])
        self.assertEqual(result["chapters"][0]["textbook_summary"],
                         {"exists": True, "pending": True, "status": "done"})
        self.assertFalse(result["chapters"][1]["textbook_summary"]["exists"])

    def test_confirmation_and_partial_failure_are_visible(self):
        module = mock.Mock()
        module.start_background.return_value = {"started": True}
        module.progress_snapshot.return_value = {"active": False, "status": "done", "doc_id": 5,
                                                 "chapter": "第1章", "percent": 100}
        book_est = {"signature": "sig", "calls": 2}
        with mock.patch.object(dashboard, "_preview_mod", return_value=module), \
             mock.patch.object(config, "value_int", return_value=1), \
             mock.patch.object(textbook_kp, "estimate", return_value=book_est), \
             mock.patch.object(textbook_kp, "progress", return_value={"active": False}), \
             mock.patch.object(textbook_kp, "start", return_value={"started": False, "error": "模型不可用"}):
            self.assertFalse(dashboard.api_preview_start({"doc_id": 5, "chapter": 1})["started"])
            module.start_background.assert_not_called()
            result = dashboard.api_preview_start({"doc_id": 5, "chapter": 1, "book_signature": "sig",
                                                  "book_max_calls": 2, "auto_textbook_kp": True})
            self.assertTrue(result["started"])
            self.assertEqual(result["companion_error"], "模型不可用")
            progress = dashboard.api_preview_progress()
            self.assertEqual(progress["status"], "failed")
            self.assertIn("模型不可用", progress["error"])
            # 完成后再次轮询（或刷新页面）仍能看到是哪一条联动失败。
            again = dashboard.api_preview_progress()
            self.assertEqual(again["book_status"], "failed")
            self.assertIn("模型不可用", again["error"])

    def test_join_waits_for_both_and_duplicate_start_keeps_progress(self):
        module = mock.Mock()
        preview_state = {"active": True, "status": "running", "doc_id": 5,
                         "chapter": "第1章", "percent": 30}
        book_state = {"active": False, "status": "", "doc_id": 5,
                      "chapter": 1, "started_at": 42, "percent": 0}
        module.start_background.side_effect = [{"started": True}, {"started": False, "error": "已有任务"}]
        module.progress_snapshot.side_effect = lambda: dict(preview_state)
        def start_book(*args, **kwargs):
            book_state.update(active=True, status="running", percent=20)
            return {"started": True}
        payload = {"doc_id": 5, "chapter": 1, "book_signature": "sig",
                   "book_max_calls": 2, "auto_textbook_kp": True}
        with mock.patch.object(dashboard, "_preview_mod", return_value=module), \
             mock.patch.object(config, "value_int", return_value=1), \
             mock.patch.object(textbook_kp, "estimate", return_value={"signature": "sig", "calls": 2}), \
             mock.patch.object(textbook_kp, "progress", side_effect=lambda: dict(book_state)), \
             mock.patch.object(textbook_kp, "start", side_effect=start_book) as start:
            self.assertTrue(dashboard.api_preview_start(payload)["started"])
            self.assertTrue(dashboard.api_preview_progress()["active"])
            book_state.update(active=False, status="done", percent=100)
            self.assertFalse(dashboard.api_preview_start(payload)["started"])
            joined = dashboard.api_preview_progress()
            self.assertTrue(joined["active"])
            self.assertEqual(joined["book_status"], "done")
            preview_state.update(active=False, status="done", percent=100)
            final = dashboard.api_preview_progress()
            self.assertEqual((final["status"], final["preview_status"], final["book_status"]),
                             ("done", "done", "done"))
            self.assertEqual(dashboard.api_preview_progress(), final)
            start.assert_called_once()

    def test_disabled_switch_starts_only_preview(self):
        module = mock.Mock()
        module.start_background.return_value = {"started": True}
        with mock.patch.object(dashboard, "_preview_mod", return_value=module), \
             mock.patch.object(config, "value_int", return_value=0), \
             mock.patch.object(textbook_kp, "start") as start:
            result = dashboard.api_preview_start({"doc_id": 5, "chapter": 1,
                                                  "auto_textbook_kp": False})
        self.assertTrue(result["started"])
        start.assert_not_called()
        module.start_background.assert_called_once_with(5, 1)


class TestBoundedConcurrency(fixture._Base):
    def test_three_segments_run_together_and_failures_are_drained(self):
        plan = self._plan(chars=80)
        barrier = threading.Barrier(3)
        lock = threading.Lock()
        state = {"active": 0, "maximum": 0, "started": 0}

        def fake_call(segment, plan, run_context=None):
            with lock:
                state["started"] += 1
                index = state["started"]
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            try:
                if index <= 3:
                    barrier.wait(timeout=3)
                if index == 1:
                    raise RuntimeError("模拟失败")
                time.sleep(0.02)
                return fixture._good(idx=index)
            finally:
                with lock:
                    state["active"] -= 1

        with mock.patch.dict("os.environ", {"TEXTBOOK_KP_WORKERS": "3"}), \
             mock.patch.object(textbook_kp, "_call", side_effect=fake_call):
            with self.assertRaisesRegex(RuntimeError, "模拟失败"):
                textbook_kp._build(plan, force=False, db_path=self.dbpath,
                                   log_dir=self.logs, max_calls=len(plan["segments"]))
        self.assertEqual(state["maximum"], 3)
        self.assertEqual(state["started"], 3)
        self.assertEqual(state["active"], 0)
        pending = json.loads(self._paths()[1].read_text(encoding="utf-8"))
        self.assertEqual(len(pending["parts"]), 2)
        self.assertEqual(len(pending["attempts"]), 3)


if __name__ == "__main__":
    unittest.main()
