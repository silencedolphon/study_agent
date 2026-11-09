"""Offline regression tests: vector slides, chapter summaries and adapter routing."""
from __future__ import annotations

import io
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches

from core import agent_bridge, chat, figures, ingest, kp_build, preview, textbook_kp


class TestVectorSlides(unittest.TestCase):
    def test_vector_ole_and_mixed_textless_pages(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "slides.pptx"
            prs = Presentation()
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1)).text = "纯文字"
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            builder = slide.shapes.build_freeform(0, 0, scale=10000)
            builder.add_line_segments([(10, 0), (10, 10), (0, 10)])
            builder.convert_to_shape(Inches(1), Inches(1))
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            icon = io.BytesIO()
            Image.new("RGB", (20, 20), "white").save(icon, "PNG")
            icon.seek(0)
            slide.shapes.add_ole_object(io.BytesIO(b"fake embedded structure"), "ChemDraw.Document",
                                        Inches(1), Inches(1), Inches(2), Inches(2), icon_file=icon)
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            slide.shapes.add_shape(MSO_SHAPE.HEXAGON, Inches(1), Inches(1), Inches(2), Inches(2))
            prs.save(path)
            self.assertEqual(figures.page_has_image(path), {1: False, 2: True, 3: True, 4: True})
            _, pages = figures.extract_pages(path, fill_empty_pages=True)
            self.assertEqual([p["page"] for p in pages], [1, 2, 3, 4])
            chunks = ingest.build_page_chunks([], {2: "识图提取的化学结构"})
            self.assertEqual(chunks[0].source_ref, "p.2")
            self.assertIn("识图提取的化学结构", chunks[0].text)

    def test_textless_page_retries_no_info_with_transcription_prompt(self):
        calls = []
        def fake(img, ctx, **kw):
            calls.append(kw)
            if len(calls) == 1:
                return figures.FigureResult(True, text=figures.NO_INFO_MARKER + "无图" * 20)
            return figures.FigureResult(True, text="页面上的化学结构和标注。" * 6)
        with mock.patch.object(figures, "describe_page", side_effect=fake):
            first, second = figures._probe_page(Path("unused.png"), figures.PageContext(page=2), True)
        self.assertTrue(figures.is_no_info(first.text))
        self.assertIsNotNone(second)
        self.assertTrue(all(c["zero_text"] for c in calls))
        self.assertIn("extra_hint", calls[1])


class FakeAgent:
    def __init__(self):
        self.calls = []
        self.fail_at = 0

    def complete(self, request):
        self.calls.append(request)
        if len(self.calls) == self.fail_at:
            raise RuntimeError("模拟中断")
        text = request.messages[-1]["content"]
        match = re.search(r"【出处 (\d+)", text if isinstance(text, str) else "")
        source = int(match.group(1)) if match else 1
        result = {"points": [{"title": "机制", "summary": "按照教材解释机制。",
                              "key_points": ["关键步骤"], "memory_tip": "联系因果理解",
                              "pitfall": "注意条件", "source_ids": [source, 999999],
                              "questions": [{"question": "机制是什么？", "answer": "按教材的因果链回答。"}]}]}
        return {"content": json.dumps(result, ensure_ascii=False), "usage": {"prompt_tokens": 10, "completion_tokens": 20}}


class TestTextbookPipeline(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "test.db"
        conn = sqlite3.connect(self.db)
        conn.executescript("""
          CREATE TABLE documents(id INTEGER PRIMARY KEY, title TEXT, role TEXT);
          CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER,
                              source_ref TEXT, heading_path TEXT, text TEXT);
          INSERT INTO documents VALUES(1, '测试教材', 'textbook');
          INSERT INTO documents VALUES(2, '测试课件', 'courseware');
        """)
        conn.execute("INSERT INTO chunks VALUES(1,1,1,'p.1','测试教材 > 第1章 基础',?)", ("甲" * 17000,))
        conn.execute("INSERT INTO chunks VALUES(2,1,2,'p.2','测试教材 > 第1章 基础',?)", ("乙" * 3000,))
        conn.commit()
        conn.close()
        self.agent = FakeAgent()
        agent_bridge.register_adapter("test-textbook", self.agent)
        agent_bridge.bind_adapter("textbook_kp", "test-textbook")
        self.addCleanup(agent_bridge.bind_adapter, "textbook_kp", None)

    def test_resume_review_and_confirmed_call_limit(self):
        plan = textbook_kp._plan(1, 1, db_path=self.db)
        est = textbook_kp.estimate(1, 1, db_path=self.db, log_dir=self.root)
        self.assertEqual(est["segments"], 2)
        self.assertEqual(est["chars"], 20000)
        self.assertEqual(est["model"], "deepseek-flash")
        self.assertEqual(est["reasoning_effort"], "max")
        self.agent.fail_at = 2
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network forbidden")), \
             mock.patch.object(textbook_kp.config, "pipeline_key", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "模拟中断"):
                textbook_kp._build(plan, force=False, db_path=self.db, log_dir=self.root, max_calls=2)
            state = textbook_kp.estimate(1, 1, db_path=self.db, log_dir=self.root)
            self.assertEqual((state["completed_segments"], state["calls"]), (1, 1))
            self.agent.fail_at = 0
            textbook_kp._build(plan, force=False, db_path=self.db, log_dir=self.root, max_calls=1)
        art = textbook_kp.read_summary(1, 1, log_dir=self.root, db_path=self.db)
        self.assertTrue(art["exists"])
        self.assertFalse(art["pending"])
        self.assertFalse(art["stale"])
        self.assertEqual(len(art["attempts"]), 3)
        self.assertEqual(art["usage_unreported"], 1)
        self.assertEqual(len(art["points"]), 2)
        self.assertTrue(all(999999 not in p["source_ids"] for p in art["points"]))
        self.assertTrue(all(r.pipeline == "textbook_kp" and r.model == "deepseek-flash"
                            and r.thinking and r.reasoning_effort == "max" for r in self.agent.calls))
        # 2026-10-31 新语义：max_tokens 由设置中的管线 cap 中央控制，0 = 无上限。
        self.assertTrue(all(r.max_tokens == textbook_kp.max_output_tokens()
                            and r.response_format == {"type": "json_object"}
                            for r in self.agent.calls))
        self.assertFalse(hasattr(self.agent.calls[0], "api_key"))
        point = art["points"][0]["id"]
        first = textbook_kp.record_review(1, 1, point, True, log_dir=self.root)
        self.assertEqual(first["review"]["due"], (date.today() + timedelta(days=1)).isoformat())
        self.assertEqual(textbook_kp.due_chapters(db_path=self.db, log_dir=self.root)["due"], 1)
        retry = textbook_kp.record_review(1, 1, point, False, log_dir=self.root)
        self.assertEqual(retry["review"]["stage"], 0)
        with self.assertRaises(ValueError):
            textbook_kp.record_review(1, 1, "unknown", True, log_dir=self.root)
        self.agent.fail_at = len(self.agent.calls) + 1
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network forbidden")), \
             mock.patch.object(textbook_kp.config, "pipeline_key", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "模拟中断"):
                textbook_kp._build(plan, force=True, db_path=self.db, log_dir=self.root, max_calls=2)
        saved = textbook_kp.read_summary(1, 1, log_dir=self.root, db_path=self.db)
        self.assertTrue(saved["exists"] and saved["pending"])
        self.assertEqual(len(saved["points"]), 2)
        remaining = textbook_kp.estimate(1, 1, db_path=self.db, log_dir=self.root)
        self.assertGreaterEqual(remaining["calls"], 1)
        self.assertEqual(remaining["calls"], remaining["segments"] - remaining["completed_segments"])
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE chunks SET text='已修改' WHERE id=1")
        conn.commit()
        conn.close()
        self.assertTrue(textbook_kp.read_summary(1, 1, log_dir=self.root, db_path=self.db)["stale"])

    def test_reject_changed_material_wrong_role_and_call_limit(self):
        with self.assertRaises(ValueError):
            textbook_kp._plan(2, 1, db_path=self.db)
        plan = textbook_kp._plan(1, 1, db_path=self.db)
        with mock.patch.object(textbook_kp.config, "DB_PATH", self.db), \
             mock.patch.object(textbook_kp.config, "LOG_DIR", self.root):
            self.assertFalse(textbook_kp.start(1, 1, "stale", max_calls=100)["ok"])
            self.assertFalse(textbook_kp.start(1, 1, plan["signature"], max_calls=0)["ok"])
            # 2026-10-31 新语义：默认 0 = 无上限，start 不拒绝；
            # 这里显式把管线 cap 设成正数，验证"确认值小于实际上限"时仍会拒绝。
            with mock.patch.object(textbook_kp, "max_output_tokens",
                                   return_value=65536):
                result = textbook_kp.start(1, 1, plan["signature"], max_calls=2,
                                           max_output_tokens=32768)
            self.assertFalse(result["ok"])
            self.assertIn("输出上限", result["error"])

        self.assertEqual(self.agent.calls, [])

    def test_existing_signature_is_compatible_with_old_output_cap(self):
        plan = textbook_kp._plan(1, 1, db_path=self.db)
        old = hashlib.sha256(json.dumps(
            [textbook_kp.VERSION, 1, 1, plan["title"], plan["chapter_name"],
             [s["hash"] for s in plan["segments"]], textbook_kp.MODEL,
             textbook_kp.EFFORT, 16384], ensure_ascii=False).encode("utf-8")).hexdigest()
        self.assertEqual(plan["signature"], old)
        _, pending, _ = textbook_kp._paths(1, 1, self.root)
        completed = {plan["segments"][0]["hash"]: {"points": [{"title": "已完成"}]}}
        textbook_kp._write(pending, {"signature": old, "parts": completed,
                                     "attempts": [{"ok": False, "usage": {"completion_tokens": 16384}}]})
        est = textbook_kp.estimate(1, 1, db_path=self.db, log_dir=self.root)
        self.assertEqual((est["completed_segments"], est["calls"]), (1, 1))
        # cap=0（默认无上限）→ estimated_output_cap=0；设正 cap 后才按段数估算。
        self.assertEqual(est["estimated_output_cap"],
                         textbook_kp.max_output_tokens() * est["calls"])

    def test_truncated_or_blank_response_stops_without_automatic_retry(self):
        plan = textbook_kp._plan(1, 1, db_path=self.db)
        examples = [
            ({"content": '{"points":[]}', "finish_reason": "length",
              "usage": {"completion_tokens": 65536}}, "达到"),
            ({"content": "   ", "finish_reason": "stop",
              "usage": {"completion_tokens": 2500}}, "空白"),
            ({"content": '{"points":[', "finish_reason": "",
              "usage": {"completion_tokens": 65536}}, "截断"),
        ]
        for response, expected in examples:
            with self.subTest(expected=expected), \
                 mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network forbidden")), \
                 mock.patch.object(textbook_kp.config, "pipeline_key", return_value=""), \
                 mock.patch.object(self.agent, "complete", return_value=response) as call:
                with self.assertRaisesRegex(textbook_kp.SegmentOutputError, expected):
                    textbook_kp._call(plan["segments"][0], plan)
                self.assertEqual(call.call_count, 1)
                request = call.call_args.args[0]
                self.assertEqual((request.max_tokens, request.response_format),
                                 (textbook_kp.max_output_tokens(),
                                  {"type": "json_object"}))


class TestAgentBoundary(unittest.TestCase):
    def test_json_mode_and_finish_reason_in_http_request(self):
        payloads = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self):
                return json.dumps({"model": "deepseek-flash", "choices": [{"finish_reason": "length",
                    "message": {"content": '{"points":['}}],
                    "usage": {"completion_tokens": 65536}}).encode()
        def fake_urlopen(req, timeout):
            payloads.append(json.loads(req.data))
            return Response()
        with mock.patch.object(chat, "load_api_key", return_value="fake"), \
             mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            result = chat.chat_once([{"role": "user", "content": "JSON"}], model="deepseek-flash",
                                    max_tokens=65536, thinking=True, reasoning_effort="max",
                                    response_format={"type": "json_object"})
        self.assertEqual(result["finish_reason"], "length")
        self.assertEqual(payloads[0]["response_format"], {"type": "json_object"})
        self.assertEqual((payloads[0]["thinking"], payloads[0]["reasoning_effort"]),
                         ({"type": "enabled"}, "max"))

    def test_chat_stream_and_visual_adapter_without_key_or_network(self):
        fake = FakeAgent()
        agent_bridge.register_adapter("test-bridge", fake)
        for pipeline in ("chat", "ingest_vision"):
            agent_bridge.bind_adapter(pipeline, "test-bridge")
            self.addCleanup(agent_bridge.bind_adapter, pipeline, None)
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network forbidden")), \
             mock.patch.object(chat, "load_api_key", side_effect=AssertionError("key not needed")):
            r = chat.chat_once([{"role": "user", "content": "测试"}])
            self.assertIn("points", r["content"])
            events = list(chat.stream_chat([{"role": "user", "content": "测试"}]))
            self.assertEqual([x[0] for x in events], ["delta", "usage", "done"])
            with tempfile.TemporaryDirectory() as td, mock.patch.object(figures, "load_deepseek_key", return_value=None):
                image = Path(td) / "slide.png"
                Image.new("RGB", (2, 2), "white").save(image)
                result = figures.describe_page(image, figures.PageContext(page=1))
                self.assertTrue(result.ok)
        self.assertEqual(fake.calls[-1].pipeline, "ingest_vision")
        self.assertEqual(fake.calls[-1].messages[0]["content"][1]["type"], "image_url")

    def test_knowledge_and_preview_use_distinct_routes(self):
        fake = FakeAgent()
        agent_bridge.register_adapter("test-pipelines", fake)
        for pipeline in ("courseware_kp", "preview"):
            agent_bridge.bind_adapter(pipeline, "test-pipelines")
            self.addCleanup(agent_bridge.bind_adapter, pipeline, None)
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network forbidden")):
            a = kp_build._v2_call([], "knowledge", "system", "user", max_tokens=2000,
                                  retry=0, log=lambda *_: None)
            b = preview._preview_call([], "preview.chapter", "system", "user", max_tokens=2000,
                                      retry=0, log=lambda *_: None)
        self.assertTrue(a["ok"] and b["ok"])
        self.assertEqual([x.pipeline for x in fake.calls], ["courseware_kp", "preview"])


if __name__ == "__main__":
    unittest.main()
