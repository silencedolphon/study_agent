"""Tests for local cross-courseware candidates and structural bridges."""
import json
import contextlib
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

import numpy as np

from core import knowledge_links, knowledge_links_ai


class KnowledgeLinksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "study.db"
        self.log = self.root / "logs"
        folder = self.log / "knowledge"
        folder.mkdir(parents=True)
        conn = sqlite3.connect(self.db)
        conn.executescript("""
          CREATE TABLE documents(id INTEGER PRIMARY KEY, title TEXT, role TEXT, status TEXT);
          CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, source_ref TEXT, text TEXT);
          INSERT INTO documents VALUES(1, '课件一', 'courseware', 'ok');
          INSERT INTO documents VALUES(2, '课件二', 'courseware', 'ok');
          INSERT INTO documents VALUES(3, '课件三', 'courseware', 'ok');
          INSERT INTO documents VALUES(4, '教材', 'textbook', 'ok');
          INSERT INTO chunks VALUES(101,1,'p.1','乙酰胆碱的信号传递过程及其基本原理，用于学习。');
          INSERT INTO chunks VALUES(102,1,'p.2','乙酰胆碱酯酶 AChE 使乙酰胆碱发生水解并终止其作用。');
          INSERT INTO chunks VALUES(201,2,'p.3','乙酰胆碱信号的药物应用和作用对象说明。');
          INSERT INTO chunks VALUES(301,3,'p.5 图','AChE 受到抑制的机器图像描述，需查看原图。');
          INSERT INTO chunks VALUES(401,4,'p.1','不应当被显示为课件结果的教材。');
        """)
        conn.commit()
        conn.close()
        self._artifact(1, [
            self._point("a", "神经传递概述", "乙酰胆碱信号与神经传递", [101, 201]),
            self._point("b", "水解终止", "乙酰胆碱酯酶 AChE 水解终止", [102]),
        ])
        self._artifact(2, [self._point("c", "传递应用", "乙酰胆碱信号与神经传递", [201])])
        self._artifact(3, [self._point("d", "酶抑制", "AChE 水解酶抑制药", [301],
                                      material=[{"chunk_id": 301, "kind": "图片理解"}])])
        self._artifact(4, [self._point("e", "教材内容", "乙酰胆碱信号", [401])])
        self.calls = []

        def fake_embed(texts):
            self.calls.append(list(texts))
            vectors = []
            for text in texts:
                title = text.split("\n", 1)[0]
                vectors.append({
                    "神经传递概述": [1, 0, 0],
                    "水解终止": [0.75, 0.66, 0],
                    "传递应用": [0.95, 0.25, 0],
                    "酶抑制": [0.30, 0.95, 0],
                    "教材内容": [1, 0, 0],
                }[title])
            return np.asarray(vectors, dtype=np.float32)

        self.embed_patch = mock.patch.object(knowledge_links.embed, "embed_documents_cached",
                                            side_effect=fake_embed)
        self.embed_patch.start()
        self.addCleanup(self.embed_patch.stop)
        knowledge_links.clear_cache()
        self.addCleanup(knowledge_links.clear_cache)

    @staticmethod
    def _point(kid, name, summary, ids, material=None):
        return {"id": kid, "name": name, "summary": summary,
                "node_path": ["课件", "同一章节", name], "pages": [1],
                "sources": [{"page": 1, "chunk_ids": ids, "figure_desc": False}],
                "material": material or []}

    def _artifact(self, doc_id, points):
        path = self.log / "knowledge" / f"{doc_id}-末级知识点总结.json"
        path.write_text(json.dumps({"meta": {"pipeline_version": "test-v1"},
                                    "structure": {"root": {}}, "knowledge_points": points},
                                   ensure_ascii=False), encoding="utf-8")

    def _find(self, kid="a", **kwargs):
        return knowledge_links.find_links(1, kid, db_path=self.db, log_dir=self.log,
                                          readable_versions={"test-v1"}, **kwargs)

    def test_direct_and_bridge_are_cross_deck_and_sources_belong_to_doc(self):
        result = self._find(limit=1)
        self.assertEqual(result["indexed_points"], 4)  # 教材不进入课件索引
        self.assertEqual(result["direct"][0]["kp_id"], "c")
        self.assertEqual(result["bridges"][0]["kp_id"], "d")
        self.assertEqual(result["bridges"][0]["bridge"]["kp_id"], "b")
        self.assertEqual([x["chunk_id"] for x in result["source"]["evidence"]], [101])
        self.assertTrue(result["bridges"][0]["evidence"][0]["image_description"])
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self._find(limit=1, allow_build=False)["reused"])
        self.assertEqual(len(self.calls), 1)

    def test_reindex_when_artifact_or_source_changes(self):
        self._find(limit=1)
        path = self.log / "knowledge" / "2-末级知识点总结.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["knowledge_points"][0]["summary"] += " 额外变化"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.assertFalse(self._find(limit=1)["reused"])
        self.assertEqual(len(self.calls), 2)
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE chunks SET text=? WHERE id=201", ("已更新且足够长的课件原始文字，用来核对来源。",))
            conn.commit()
        result = self._find(limit=1)
        self.assertFalse(result["reused"])
        self.assertIn("已更新", result["direct"][0]["evidence"][0]["excerpt"])

    def test_readonly_connections_do_not_invalidate_cache(self):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
        self._find(limit=1)
        with contextlib.closing(sqlite3.connect(self.db)) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM chunks").fetchone()
            self.assertTrue(self._find(limit=1)["reused"])
        self.assertTrue(self._find(limit=1)["reused"])
        self.assertEqual(len(self.calls), 1)

    def test_busy_rejects_only_cold_index(self):
        with self.assertRaisesRegex(ValueError, "长任务"):
            self._find(allow_build=False)
        self._find()
        self.assertTrue(self._find(allow_build=False)["reused"])

    def test_reject_stale_build_if_source_changes_during_embedding(self):
        original = knowledge_links.embed.embed_documents_cached
        def changing(texts):
            vec = original(texts)
            path = self.log / "knowledge" / "1-末级知识点总结.json"
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            return vec
        with mock.patch.object(knowledge_links.embed, "embed_documents_cached", side_effect=changing):
            with self.assertRaisesRegex(ValueError, "发生变化"):
                self._find()
        self.assertFalse(knowledge_links._CACHE)

    def test_cross_subject_same_ids_are_isolated(self):
        first = self._find(limit=1)
        other = self.root / "other"
        other.mkdir()
        db2 = other / "study.db"
        with contextlib.closing(sqlite3.connect(db2)) as conn:
            conn.executescript("""
              CREATE TABLE documents(id INTEGER PRIMARY KEY,title TEXT,role TEXT,status TEXT);
              CREATE TABLE chunks(id INTEGER PRIMARY KEY,doc_id INTEGER,source_ref TEXT,text TEXT);
              INSERT INTO documents VALUES(1,'另一个学科','courseware','ok');
              INSERT INTO chunks VALUES(101,1,'p.1','另一个学科的数据');
            """)
            conn.commit()
        log2 = other / "logs" / "knowledge"
        log2.mkdir(parents=True)
        (log2 / "1-末级知识点总结.json").write_text(json.dumps({
            "meta": {"pipeline_version": "test-v1"}, "structure": {"root": {}},
            "knowledge_points": [self._point("a", "神经传递概述", "其他学科", [101])]
        }, ensure_ascii=False), encoding="utf-8")
        second = knowledge_links.find_links(1, "a", db_path=db2, log_dir=other / "logs",
                                            readable_versions={"test-v1"})
        self.assertTrue(first["direct"])
        self.assertEqual(second["source"]["doc_title"], "另一个学科")
        self.assertEqual(second["direct"], [])
        self.assertEqual(second["bridges"], [])


class KnowledgeLinksAITest(unittest.TestCase):
    def setUp(self):
        self.ctx = SimpleNamespace(model="deepseek-flash", api_base="https://example.invalid",
            thinking=True, reasoning_effort="max", max_tokens=0, credential_ref="kp",
            pipeline="knowledge_links")
        self.source = {"doc_id": 1, "kp_id": "a", "name": "A", "version": "va",
                       "evidence": [{"excerpt": "乙酰胆碱在突触间隙被乙酰胆碱酯酶水解，随后作用终止并被回收。这一过程位于神经肌肉接头。", "sparse": False,
                                     "chunk_id": 101, "page": "p.1"}]}
        self.target = {"doc_id": 2, "kp_id": "b", "name": "B", "version": "vb",
                       "evidence": [{"excerpt": "抗胆碱酯酶药能够抑制乙酰胆碱酯酶，使乙酰胆碱较难被水解，从而延长信号作用时间。", "sparse": False,
                                     "chunk_id": 201, "page": "p.2"}]}
        self.data = {"source": self.source, "direct": [self.target], "bridges": []}
        self.payload = {"confirmed": True, "target_doc_id": 2, "target_kp_id": "b",
                        "source_version": "va", "target_version": "vb",
                        "settings_signature": knowledge_links_ai.settings_info(self.ctx)["signature"]}

    def test_no_spend_without_explicit_confirmation_or_with_stale_material(self):
        with mock.patch.object(knowledge_links_ai.chat, "chat_once") as call:
            with self.assertRaisesRegex(ValueError, "确认"):
                knowledge_links_ai.analyze(self.data, {**self.payload, "confirmed": False}, run_context=self.ctx)
            with self.assertRaisesRegex(ValueError, "已变化"):
                knowledge_links_ai.analyze(self.data, {**self.payload, "source_version": "old"}, run_context=self.ctx)
            with self.assertRaisesRegex(ValueError, "设置已变化"):
                knowledge_links_ai.analyze(self.data, {**self.payload, "settings_signature": "old"}, run_context=self.ctx)
            call.assert_not_called()

    def test_one_call_and_exact_quotes_required(self):
        raw = {"related": True, "relation": "机制关联", "explanation": "两处材料涉及同一种酶。",
               "source_quote": "乙酰胆碱酯酶水解", "target_quote": "抑制乙酰胆碱酯酶", "caution": "请结合原文。"}
        response = {"content": json.dumps(raw, ensure_ascii=False), "usage": {"completion_tokens": 10}}
        with mock.patch.object(knowledge_links_ai.chat, "chat_once", return_value=response) as call, \
             mock.patch.object(knowledge_links_ai.config, "pipeline_key", return_value=""):
            result = knowledge_links_ai.analyze(self.data, self.payload, run_context=self.ctx)
            self.assertTrue(result["related"])
            self.assertEqual(result["relation"], "机制关联")
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.kwargs["model"], "deepseek-flash")
            self.assertEqual(call.call_args.kwargs["reasoning_effort"], "max")
        raw["target_quote"] = "原文没有说的句子"
        with mock.patch.object(knowledge_links_ai.chat, "chat_once", return_value={"content": json.dumps(raw)}), \
             mock.patch.object(knowledge_links_ai.config, "pipeline_key", return_value=""):
            result = knowledge_links_ai.analyze(self.data, self.payload, run_context=self.ctx)
        self.assertFalse(result["related"])
        self.assertNotIn("explanation", result)

    def test_insufficient_source_and_invalid_json_do_not_retry(self):
        self.source["evidence"][0]["sparse"] = True
        with mock.patch.object(knowledge_links_ai.chat, "chat_once") as call:
            with self.assertRaisesRegex(ValueError, "出处不足"):
                knowledge_links_ai.analyze(self.data, self.payload, run_context=self.ctx)
            call.assert_not_called()
        self.source["evidence"][0]["sparse"] = False
        with mock.patch.object(knowledge_links_ai.chat, "chat_once", return_value={"content": "not json"}) as call, \
             mock.patch.object(knowledge_links_ai.config, "pipeline_key", return_value=""):
            result = knowledge_links_ai.analyze(self.data, self.payload, run_context=self.ctx)
        self.assertFalse(result["related"])
        self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()

