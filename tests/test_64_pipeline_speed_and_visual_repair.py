"""Offline regressions for visual recovery, safe parallelism and JSON mode."""
from __future__ import annotations

from contextlib import closing

import io
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

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import agent_bridge, chat, config, db, figures, ingest, kp_batch, kp_build, visual_repair


def _rich():
    return (figures.NO_INFO_MARKER + "不适用，本页有化学结构。\n"
            "【本页作用】" + "这是一份具有真实药物结构的比较说明。" * 4 + "\n"
            "【图的内容】" + "图中包含芳香环、氨基和连接侧链。" * 4 + "\n"
            "【结构说明】" + "具体结构信息和化学键需要保留。" * 4)


def _context(base="https://api.deepseek.com", model="deepseek-flash"):
    return SimpleNamespace(pipeline="courseware_kp", credential_ref="kp", model=model,
                           api_base=base, thinking=True, reasoning_effort="max", max_tokens=0)


class NoInfoClassification(unittest.TestCase):
    def test_real_no_info_and_contradictory_structured_reply(self):
        self.assertTrue(figures.is_no_info(figures.NO_INFO_MARKER + "本页只是纯文字，没有图示。"))
        self.assertFalse(figures.is_no_info(_rich()))
        other = (figures.NO_INFO_MARKER + "虽然结构属于正文，但仍提供解析。\n"
                 "【图的内容】" + "图里是一组化学结构及对应名称。" * 4 + "\n"
                 "【结构说明】" + "描述不同分子的环和连接方式。" * 4)
        self.assertFalse(figures.is_no_info(other))
        self.assertNotIn(figures.NO_INFO_MARKER, figures.clean_contradictory_no_info(other))

    def test_old_flag_is_rechecked_not_blindly_trusted(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE figure_desc(doc_id INTEGER,page INTEGER,description TEXT,no_info INTEGER)")
        conn.executemany("INSERT INTO figure_desc VALUES(?,?,?,?)", [
            (1, 1, _rich(), 1), (1, 2, figures.NO_INFO_MARKER + "纯文字。", 1)])
        got = ingest._figure_texts(conn, 1, skip_no_info=True)
        self.assertEqual(set(got), {1})
        self.assertIn("结构说明", got[1])
        self.assertNotIn(figures.NO_INFO_MARKER, got[1])


class StructuredOutput(unittest.TestCase):
    def _call(self, ctx, adapter=False, content='{"ok":true}', retry=0):
        sent = []
        def fake(messages, **kw):
            sent.append(kw)
            return {"content": content, "model": kw["model"], "usage": {}, "finish_reason": "stop"}
        with mock.patch.object(chat, "chat_once", side_effect=fake), \
             mock.patch.object(agent_bridge, "has_adapter", return_value=adapter):
            result = kp_build._v2_call([], "knowledge", "Return JSON object", "JSON example {}",
                                       max_tokens=0, retry=retry, log=lambda *_: None,
                                       run_context=ctx)
        return sent, result

    def test_official_deepseek_uses_json_mode_and_preserves_reasoning(self):
        sent, result = self._call(_context())
        self.assertTrue(result["ok"])
        self.assertEqual(sent[0]["response_format"], {"type": "json_object"})
        self.assertEqual((sent[0]["model"], sent[0]["reasoning_effort"]), ("deepseek-flash", "max"))
        self.assertTrue(result["call"]["json_mode"])

    def test_other_backends_and_agent_keep_prior_compatibility(self):
        sent, result = self._call(_context("https://example.invalid"))
        self.assertTrue(result["ok"])
        self.assertNotIn("response_format", sent[0])
        sent, result = self._call(_context(), adapter=True)
        self.assertTrue(result["ok"])
        self.assertNotIn("response_format", sent[0])
        with mock.patch.dict(os.environ, {"KP_JSON_MODE": "0"}):
            sent, result = self._call(_context())
        self.assertNotIn("response_format", sent[0])

    def test_empty_json_mode_response_is_still_checked_and_retried(self):
        calls = []
        def fake(messages, **kw):
            calls.append(kw)
            return {"content": "  " if len(calls) == 1 else '{"ok":true}',
                    "model": kw["model"], "usage": {}, "finish_reason": "stop"}
        with mock.patch.object(chat, "chat_once", side_effect=fake), \
             mock.patch.object(agent_bridge, "has_adapter", return_value=False), \
             mock.patch.object(kp_build.time, "sleep", return_value=None):
            result = kp_build._v2_call([], "knowledge", "JSON", "JSON", max_tokens=0,
                                       retry=1, log=lambda *_: None, run_context=_context())
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(x["response_format"] == {"type": "json_object"} for x in calls))


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "study.db"
        conn = db.connect(self.path)
        db.init_db(conn)
        conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,content_hash,role,n_chars) "
                     "VALUES(1,'x.pdf','x.pdf','pdf','T','h','courseware',4)")
        conn.execute("INSERT INTO chunks(id,doc_id,ord,text,text_norm,heading_path,source_ref,char_len,content_hash) "
                     "VALUES(1,1,1,'原文数据','原文数据','标题','p.1',4,'h')")
        conn.execute("INSERT INTO embeddings(chunk_id,model,dim,vec) VALUES(1,?,?,?)",
                     (config.EMBED_MODEL_REPO, config.EMBED_DIM, bytes(4 * config.EMBED_DIM)))
        conn.execute("INSERT INTO figure_desc(doc_id,page,image_hash,description,no_info) VALUES(1,1,'h',?,1)", (_rich(),))
        conn.commit()
        conn.close()

    class FakeEmbedder:
        def embed_documents(self, texts):
            return np.full((len(texts), config.EMBED_DIM), 0.05, dtype=np.float32)

    def test_atomic_repair_keeps_ids_updates_fts_and_is_idempotent(self):
        preview = visual_repair.repair_database(self.path, [1])
        self.assertEqual(preview['count'], 1)
        self.assertFalse(preview['applied'])
        result = visual_repair.repair_database(self.path, [1], apply=True,
                                               backup_path=self.root/'before.db',
                                               embedder=self.FakeEmbedder())
        self.assertEqual((result['count'], result['model_calls']), (1, 0))
        self.assertTrue(result['applied'])
        with closing(sqlite3.connect(self.path)) as c:
            row = c.execute("SELECT id,text FROM chunks").fetchone()
            self.assertEqual(row[0], 1)
            self.assertIn("原文数据", row[1])
            self.assertIn("【图片理解】", row[1])
            self.assertNotIn(figures.NO_INFO_MARKER, row[1])
            self.assertEqual(c.execute("SELECT no_info FROM figure_desc").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid=1", ('"图片理解"',)).fetchone()[0], 1)
            self.assertTrue(c.execute("SELECT value FROM meta WHERE key='visual_repair:1'").fetchone())
            self.assertEqual(c.execute("PRAGMA quick_check").fetchone()[0], 'ok')
        with closing(sqlite3.connect(self.root/'before.db')) as c:
            self.assertEqual(c.execute("SELECT text FROM chunks").fetchone()[0], '原文数据')
        self.assertEqual(visual_repair.repair_database(self.path, [1])['count'], 0)
        self.assertTrue(kp_build.structure_source_changed({'raw_root': {}}, {}, 1, self.path))
        done, _ = kp_batch.done_state({'source_changed': True}, {'exists': True, 'is_current_version': True})
        self.assertFalse(done)

    def test_changed_input_aborts_without_overwriting_newer_edits(self):
        path = self.path
        class RacingEmbedder:
            def embed_documents(self, texts):
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute("UPDATE chunks SET text='用户的新内容' WHERE id=1")
                    conn.commit()
                return np.ones((len(texts), config.EMBED_DIM), dtype=np.float32)
        with self.assertRaises(visual_repair.VisualRepairError):
            visual_repair.repair_database(path, [1], apply=True, backup_path=self.root/'before.db',
                                          embedder=RacingEmbedder())
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute('SELECT text FROM chunks').fetchone()[0], '用户的新内容')
            self.assertEqual(conn.execute('SELECT no_info FROM figure_desc').fetchone()[0], 1)
            self.assertIsNone(conn.execute("SELECT value FROM meta WHERE key='visual_repair:1'").fetchone())


class ParallelVision(unittest.TestCase):
    def test_32_pages_really_use_8_but_do_not_exceed_shared_gate(self):
        original_limit = kp_build.model_gate_limit()
        kp_build.configure_model_gate(8)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                src = root/'same.pdf'
                src.write_bytes(b'fake-pdf-source')
                imgs = {}
                for page in range(1,33):
                    image = root/('image-%02d.png' % page)
                    image.write_bytes(b'fake-image-' + bytes([page]))
                    imgs[page] = image
                db_path = root/'study.db'
                conn = db.connect(db_path)
                try:
                    db.init_db(conn)
                    conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,content_hash,role) "
                                 "VALUES(1,'x','x','pdf','t','h','courseware')")
                    conn.commit()
                    context = SimpleNamespace(pipeline='ingest_vision', credential_ref='ingest',
                        model='deepseek-flash', api_base='https://api.deepseek.com',
                        thinking=True, reasoning_effort='high', max_tokens=0)
                    pages = [{'page':n,'title':'标题','body':'有正文'} for n in imgs]
                    state = {'active':0,'peak':0,'calls':0}
                    lock = threading.Lock()
                    def fake_post(*args, **kwargs):
                        with lock:
                            state['active'] += 1
                            state['peak'] = max(state['peak'], state['active'])
                        try:
                            time.sleep(0.04)
                            return 200, {'model':'fake','usage':{},'choices':[{'message':{'content':'可靠图示内容和结构说明。' * 8}}]}
                        finally:
                            with lock:
                                state['active'] -= 1
                                state['calls'] += 1
                    with mock.patch.object(config,'LOG_DIR',root/'logs'), \
                         mock.patch.object(figures,'render_pages_cached',return_value=(imgs,{})), \
                         mock.patch.object(figures,'vision_api_key',return_value='fake'), \
                         mock.patch.object(figures,'_http_post_unthrottled',side_effect=fake_post):
                        stats = figures.ingest_document_figures(
                            conn,1,src,kind='pdf',only_with_images=False,store_chunks=False,
                            pre_extracted={'deck_title':'t','pages':pages},run_context=context)
                    self.assertEqual(stats['workers'],8)
                    self.assertEqual((state['peak'],state['calls'],stats['described']), (8,32,32))
                    self.assertLessEqual(kp_build.model_gate_stats()['peak_inflight'],8)
                finally:
                    conn.close()
        finally:
            kp_build.configure_model_gate(original_limit)

    def test_new_waiter_fails_promptly_when_all_slots_are_timed_out(self):
        original_limit = kp_build.model_gate_limit()
        kp_build.configure_model_gate(1)
        release = threading.Event()
        entered = threading.Event()
        result = []
        def slow():
            entered.set()
            release.wait(3)
        def first():
            try:
                with kp_build.model_slot('first',timeout=1):
                    kp_build._call_with_hard_timeout(slow,timeout=0.08,kind='first')
            except kp_build.ModelCallHang:
                result.append('unknown')
        worker = threading.Thread(target=first)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            start=time.monotonic()
            with self.assertRaises(kp_build.ModelSlotTimeout):
                with kp_build.model_slot('second',timeout=3):
                    self.fail('slot should remain held')
            self.assertLess(time.monotonic()-start,1.2)
            self.assertEqual(result,['unknown'])
            self.assertEqual(kp_build.model_gate_stats()['inflight'],1)
        finally:
            release.set()
            worker.join(2)
            deadline=time.monotonic()+2
            while kp_build.model_gate_stats()['inflight'] and time.monotonic()<deadline:
                time.sleep(0.01)
            kp_build.configure_model_gate(original_limit)


if __name__ == '__main__':
    unittest.main()