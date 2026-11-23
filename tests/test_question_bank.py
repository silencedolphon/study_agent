"""Regressions for shared chapter ingestion and a question-only batch."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core import config, db, ingest, question_bank, question_bank_layout as layout, textbook
from core.pipeline_runtime.state import RemoteUnknownError
from tools import dashboard


class QuestionBankTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db_path = self.root / "study.db"
        self.conn = db.connect(self.db_path, immediate=True)
        self.addCleanup(self.conn.close)
        db.init_db(self.conn)

    def add(self, ident, folder, role, text, heading="", corpus="main"):
        rel = f"subjects/s/raw/{folder}/资料{ident}.txt"
        with self.conn:
            self.conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,role,corpus,status,n_chunks,content_hash) "
                              "VALUES(?,?,?,'text',?,?,?,'ok',1,?)",
                              (ident, rel, str(self.root / rel), f"资料{ident}", role, corpus, f"v{ident}"))
            self.conn.execute("INSERT INTO chunks(doc_id,ord,text,heading_path,source_ref) VALUES(?,0,?,?,'p.1')",
                              (ident, text, heading))

    def test_exam_runs_the_same_chapter_splitter_as_textbook(self):
        source = self.root / "raw" / "考试资料" / "练习册 第4版.txt"
        source.parent.mkdir(parents=True)
        source.write_text("第一章 绪论\n练习题\n1. 请介绍两种给药方式。" * 12, encoding="utf-8")
        conn = self.conn
        with patch.object(config, "ROOT", self.root), patch.object(config, "RAW_DIR", source.parent.parent), \
             patch.object(ingest, "get_embedder"), patch.object(ingest, "embed_documents_cached", return_value=[]), \
             patch.object(textbook, "cut_by_chapter", wraps=textbook.cut_by_chapter) as cut:
            result = ingest.ingest_file(conn, source)
            self.assertEqual(result["status"], "added", result)
            self.assertEqual(ingest.ingest_file(conn, source)["status"], "skipped")
        self.assertEqual(cut.call_count, 1)
        row = conn.execute("SELECT role,corpus,extra FROM documents").fetchone()
        self.assertEqual((row["role"], row["corpus"]), ("reference", "aux"))
        self.assertEqual(json.loads(row["extra"])["chunk_scheme"], ingest.TEXTBOOK_CHUNK_SCHEME)

    def test_batch_priority_chapter_provenance_and_resume(self):
        book, exam, deck = "请解释教材中的概念甲。", "请根据真题计算指标乙。", "请比较课件中的方案丙。"
        self.add(3, "课件", "courseware", deck)
        self.add(2, "考试资料", "reference", exam, "试卷 > 第十一章 多剂量给药", "aux")
        self.add(1, "教材", "textbook", book, "教材 > 第11章 多剂量给药")
        self.add(4, "学习笔记", "courseware", "综述，没有题目。")
        requests = []

        def pretend(_ctx, _prompt, args):
            text = args["chunks"][0]["text"]
            requests.append(text)
            return {"questions": [{"stem": text, "quote": text, "chapter": "1:11"}]}

        def run():
            question_bank.start(self.conn, "bank-test", {"confirmed":True, "doc_ids":[3, 2, 1]})
            for _ in range(200):
                snap = question_bank.progress("bank-test")
                if not snap["active"]:
                    self.assertEqual(snap["status"], "done", snap)
                    return
                time.sleep(.02)
            self.fail("题库任务未结束")

        with patch.object(config, "DB_PATH", self.db_path), patch.object(config, "LOG_DIR", self.root), \
             patch.object(question_bank, "_request", side_effect=pretend):
            run()
            run()
            view = question_bank.view(self.conn, "1:11", 0)
            self.assertEqual([s["id"] for s in view["sources"]], [1, 2, 3])
            self.assertEqual([x["stem"] for x in view["items"]], [book, exam, deck])
            self.assertEqual(view["groups"][0]["count"], 3)
            self.assertTrue(all(x["refs"][0]["ref"] == "p.1" for x in view["items"]))
            question_bank.assign(self.conn, view["items"][0]["id"], "")
            run()
            self.assertEqual(question_bank.view(self.conn, "", 0)["items"][0]["stem"], book)
        self.assertEqual(requests, [book, exam, deck])

    def test_removes_unverified_answer_and_marks_missing_options(self):
        text = "1. 选择适合的指标。\nA. 指标甲"
        items, rejected = question_bank._validate({"questions": [
            {"stem": "选择适合的指标。", "quote": text, "options": ["A. 指标甲", "B. 未出现"],
             "answer": "A", "answer_quote": "标准答案：A"},
            {"stem": "模型自创题目。", "quote": text},
        ]}, [{"id": 7, "ref": "slide 2", "text": text}], {}, {}, "")
        self.assertEqual(rejected, 1)
        self.assertTrue(items[0]["incomplete"])
        self.assertEqual(items[0]["answer"], "")
        self.assertEqual(items[0]["options"], ["A. 指标甲"])

    def test_docs_exposes_folder_in_unified_list(self):
        self.add(1, "考试资料", "reference", "模拟试题", corpus="aux")
        with patch.object(config, "ROOT", self.root), \
             patch.object(config, "RAW_DIR", self.root / "subjects/s/raw"):
            row, = dashboard.api_docs(self.conn)["documents"]
        self.assertEqual(row["folder"], "考试资料")

    def test_marked_sections_and_later_answers_are_matched_by_chapter_and_type(self):
        text = ("教材概述，不含练习。\n单项选择题\n1. 选择本章指标。\n多项选择题\n1. 选择所有适合指标。\n"
                "【参考答案】\n一、单项选择题\n1. B\n二、多项选择题\n1. AC\n"
                "【知识要点】\n正文内容无需送出。")
        self.add(3, "考试资料", "reference", text, "材料 > 第1章 例一", "aux")
        with self.conn:
            self.conn.execute("INSERT INTO chunks(doc_id,ord,text,heading_path,source_ref) VALUES(3,1,?,?,?)",
                              ("简答题\n1. 请解释概念乙。\n【参考答案】\n简答题\n1. 这是概念乙的原始解释。",
                               "材料 > 第2章 例二", "p.2"))
        self.conn.executescript(question_bank._SCHEMA)
        docs = question_bank._sources(self.conn)
        chapters, mapping, defaults = question_bank._catalog(self.conn, docs)
        ctx = question_bank.context.make_context("courseware_kp")
        seen = []

        def mock_extract(_ctx, _prompt, args):
            row = args["chunks"][0]
            seen.extend(chunk["text"] for chunk in args["chunks"])
            if row["mode"] == "answers":
                return {"answers": [{"answer": row["text"].partition(". ")[2], "quote": row["text"]}]}
            return {"questions": [{"stem": piece["text"].partition(". ")[2], "quote": piece["text"]}
                                  for piece in args["chunks"]]}

        with patch.object(question_bank, "_request", side_effect=mock_extract), \
             patch.object(layout, "numbered_answers", return_value=[]):
            state = {}
            self.assertEqual(question_bank._extract_doc(self.conn, ctx, docs[0], chapters, mapping, "", state), [])
        self.assertEqual(len(seen), 4)
        self.assertTrue(all("正文" not in value and "概述" not in value for value in seen))
        chapter1 = question_bank.view(self.conn, "3:1", 0)["items"]
        self.assertEqual([x["answer"] for x in chapter1], ["B", "AC"])
        chapter2, = question_bank.view(self.conn, "3:2", 0)["items"]
        self.assertEqual(chapter2["answer"], "这是概念乙的原始解释。")
        self.assertEqual(chapter2["answer_refs"][0]["ref"], "p.2")
        self.assertEqual(state["answers"], 3)

    def test_timeout_and_malformed_json_keep_other_segments_and_resume(self):
        self.add(1, "教材", "textbook", "思考题\n1. 请解释概念甲。\n第2章 示例\n"
                 "简答题\n1. 请解释概念乙。\n第3章 示例\n计算题\n1. 请计算丙。", "教材 > 第1章 示例")
        self.conn.executescript(question_bank._SCHEMA)
        docs = question_bank._sources(self.conn)
        chapters, mapping, defaults = question_bank._catalog(self.conn, docs)
        ctx = question_bank.context.make_context("courseware_kp")
        attempts = []
        recovered = False

        def mock_extract(_ctx, prompt, args):
            value = args["chunks"][0]["text"]
            attempts.append(value)
            if "甲" in value and not recovered:
                raise RemoteUnknownError("读超时")
            if "乙" in value and not prompt.endswith(question_bank._RETRY):
                return {"unexpected": []}
            return {"questions": [{"stem": value.partition(". ")[2], "quote": value}]}

        with patch.object(question_bank, "_request", side_effect=mock_extract):
            warnings = question_bank._extract_doc(self.conn, ctx, docs[0], chapters, mapping, "", {})
            self.assertEqual(len(warnings), 2)
            self.assertIn("续跑", warnings[0])
            self.assertIn("后续 2 章", warnings[1])
            self.assertEqual(len(question_bank.view(self.conn, "1:1", 0)["items"]), 0)
            self.assertEqual(len(attempts), 1)
            recovered = True
            self.assertEqual(question_bank._extract_doc(self.conn, ctx, docs[0], chapters, mapping, "", {}), [])
        self.assertEqual(len(attempts), 5)
        self.assertIn("甲", attempts[1])
        self.assertEqual(len(question_bank.view(self.conn, "1:1", 0)["items"]), 3)

    def test_answers_at_back_are_processed_and_committed_before_next_chapter(self):
        self.add(3, "考试资料", "reference", "第1章 第一部分\n简答题\n1. 请解释概念甲。\n"
                 "第2章 第二部分\n简答题\n1. 请解释概念乙。\n附录 参考答案\n"
                 "第1章 参考答案\n简答题\n1. 这是甲的原文解答。\n"
                 "第2章\n简答题参考解答\n1. 这是乙的原文解答。", corpus="aux")
        self.conn.executescript(question_bank._SCHEMA)
        doc, = question_bank._sources(self.conn)
        ctx = question_bank.context.make_context("courseware_kp")
        called = []

        def mock_extract(_ctx, _prompt, args):
            row, = args["chunks"]
            called.append((row["mode"], row["scope"]))
            value = row["text"].partition(". ")[2]
            if row["mode"] == "answers":
                return {"answers": [{"answer": value, "quote": row["text"]}]}
            if row["scope"] == "chapter-2":
                saved, = self.conn.execute("SELECT payload FROM question_bank_items")
                self.assertEqual(json.loads(saved[0])["answer"], "这是甲的原文解答。")
                self.assertFalse(self.conn.in_transaction)
            return {"questions": [{"stem": value, "quote": row["text"]}]}

        with patch.object(question_bank, "_request", side_effect=mock_extract), \
             patch.object(layout, "numbered_answers", return_value=[]):
            state = {}
            self.assertEqual(question_bank._extract_doc(self.conn, ctx, doc, {}, {}, "", state), [])
        self.assertEqual(called, [("questions", "chapter-1"), ("answers", "chapter-1"),
                                  ("questions", "chapter-2"), ("answers", "chapter-2")])
        self.assertEqual((state["chapters_done"], state["answers"]), (2, 2))

    def test_answer_heading_variants_and_inline_answers(self):
        for label in ("答案", "【参考答案】", "附录 参考解析", "习题答案及详解", "二、选择题答题参考"):
            self.assertEqual(layout.section(label)[0], "answers", label)
        raw = [{"id": 4, "heading": "", "text": "选择题\n1. 选出答案。\n答案：A\n2. 请再选择。\n参考答案：1. A 2. B"}]
        questions, answers = layout.regions(raw, "exam")
        self.assertIn("答案：A\n2. 请再选择。", questions["text"])
        self.assertEqual(answers["text"], "1. A 2. B")

    def test_ambiguous_unscoped_answers_do_not_attach_to_any_question(self):
        first = {"number": "1", "scope": "chapter-1", "type": "single"}
        second = {"number": "1", "scope": "chapter-2", "type": "single"}
        part = [{"id": 10, "ref": "p.3", "scope": "", "type": "single", "text": "1-2 BD"}]
        answers, complete = layout.local_keys(part)
        self.assertTrue(complete)
        self.assertEqual([x["answer"] for x in answers], ["B", "D"])
        layout.link_answers([first, second], answers)
        self.assertNotIn("answer", first)
        self.assertNotIn("answer", second)

    def test_numbered_answers_join_page_continuations_and_retain_both_references(self):
        base = {"mode": "answers", "scope": "chapter-2", "type": "written"}
        part = [
            {**base, "id": 1, "ref": "p.27", "text": "1. 这段原文跨页延\n"},
            {**base, "id": 2, "ref": "p.28", "text": "23\n续到下一页。\n这是没有被重复拼接的原文。"},
            {**base, "id": 3, "ref": "p.28", "text": "这是没有被重复拼接的原文。\n2. 另一个答案。\n(1) 这个步骤没有另立题号。"},
        ]
        answers = layout.numbered_answers(part)
        self.assertEqual([a["number"] for a in answers], ["1", "2"])
        self.assertEqual(answers[0]["answer"], "这段原文跨页延\n续到下一页。\n这是没有被重复拼接的原文。")
        self.assertEqual([r["chunk_id"] for r in answers[0]["answer_refs"]], [1, 2])
        self.assertIn("(1) 这个步骤", answers[1]["answer"])
        self.assertEqual(layout.numbered_answers([{**part[0], "text": "1. 原文\n3. 跳号"}]), [])

    def test_unverified_response_records_warning_and_processes_next_chapter(self):
        self.add(1, "教材", "textbook", "第1章 示例\n简答题\n1. 请解释概念甲。\n"
                 "第2章 示例\n简答题\n1. 请解释概念乙。")
        self.conn.executescript(question_bank._SCHEMA)
        doc, = question_bank._sources(self.conn)
        ctx = question_bank.context.make_context("courseware_kp")
        seen = []

        def pretend(_ctx, _prompt, args):
            row, = args["chunks"]
            seen.append(row["scope"])
            rows = [{"stem": row["text"][3:].strip(), "quote": row["text"]}]
            if row["scope"] == "chapter-1":
                rows.append({"stem": "此条无法核对", "quote": "原文未出现"})
            return {"questions": rows}

        with patch.object(question_bank, "_request", side_effect=pretend):
            state = {}
            warnings = question_bank._extract_doc(self.conn, ctx, doc, {}, {}, "", state)
        self.assertEqual(seen, ["chapter-1", "chapter-1", "chapter-2"])
        self.assertEqual(state["chapters_done"], 2)
        self.assertFalse(state.get("paused"))
        self.assertEqual(state["questions"], 2)
        self.assertIn("待复核", warnings[0])


if __name__ == "__main__":
    unittest.main()
