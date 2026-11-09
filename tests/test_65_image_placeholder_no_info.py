"""Regression tests for picture placeholders and contradictory no-info replies."""
from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image
from pptx import Presentation
from pptx.oxml.xmlchemy import OxmlElement
from pptx.oxml.ns import qn
from pptx.util import Inches

from core import config, db, figures, ingest


SINGLE_STRUCTURE = (figures.NO_INFO_MARKER + "\n【结构说明】"
    "图中显示一个苯环连接哌嗪基团，并通过醚键与侧链相连；另有羟基和羧基取代。")
UNRESOLVED_STRUCTURE = (figures.NO_INFO_MARKER +
    "本页由6个化学结构式与活性数值组成，其核心信息已在正文中给出，因此不展开。")


def make_deck(path: Path) -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "只有标题的文本占位符"
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    buf = io.BytesIO()
    Image.new("RGB", (24, 20), "white").save(buf, "PNG")
    image_bytes = buf.getvalue()
    slide.shapes.add_picture(io.BytesIO(image_bytes), Inches(1), Inches(1))
    slide = prs.slides.add_slide(prs.slide_layouts[8])
    slide.shapes.title.text = "图片占位符"
    slide.placeholders[1].insert_picture(io.BytesIO(image_bytes))
    slide = prs.slides.add_slide(prs.slide_layouts[8])
    slide.shapes.title.text = "未填充的占位符"
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    group.shapes.add_picture(io.BytesIO(image_bytes), Inches(1), Inches(1))
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    placeholder = slide.shapes.title
    placeholder.text = "带图片填充的占位符"
    _, rel_id = slide.part.get_or_add_image_part(io.BytesIO(image_bytes))
    fill = OxmlElement("a:blipFill")
    blip = OxmlElement("a:blip")
    blip.set(qn("r:embed"), rel_id)
    fill.append(blip)
    stretch = OxmlElement("a:stretch")
    stretch.append(OxmlElement("a:fillRect"))
    fill.append(stretch)
    placeholder._element.spPr.append(fill)
    prs.save(path)


class PicturePlaceholderTests(unittest.TestCase):
    def test_picture_placeholder_is_detected_without_enabling_text_placeholders(self):
        with tempfile.TemporaryDirectory() as td:
            deck = Path(td) / "slides.pptx"
            make_deck(deck)
            shape = Presentation(deck).slides[2].placeholders[1]
            self.assertEqual(int(shape.shape_type), 14)
            self.assertTrue(shape._element.tag.endswith("}pic"))
            filled = Presentation(deck).slides[5].shapes.title
            self.assertEqual(int(filled.shape_type), 14)
            self.assertTrue(filled._element.tag.endswith("}sp"))
            self.assertTrue(filled._element.xpath(".//a:blip"))
            self.assertEqual(figures.page_has_image(deck),
                             {1: False, 2: True, 3: True, 4: False, 5: True, 6: True})

    def test_placeholder_reaches_vision_and_single_section_reaches_page_chunk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            deck = root / "slides.pptx"
            make_deck(deck)
            image = root / "page.png"
            Image.new("RGB", (20, 20), "white").save(image)
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            db.init_db(conn)
            conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,content_hash,role) "
                         "VALUES(1,'test.pptx','test.pptx','pptx','test','hash','courseware')")
            conn.commit()
            ctx = SimpleNamespace(pipeline="ingest_vision", credential_ref="ingest",
                                  model="fake", api_base="https://example.invalid",
                                  thinking=True, reasoning_effort="high", max_tokens=0)
            fake = (figures.FigureResult(True, text=SINGLE_STRUCTURE, model="fake"), None)
            with mock.patch.object(config, "LOG_DIR", root / "logs"), \
                 mock.patch.object(figures, "render_pages_cached", return_value=({3: image}, {})), \
                 mock.patch.object(figures, "_probe_gated", return_value=fake) as probe:
                stats = figures.ingest_document_figures(
                    conn, 1, deck, kind="pptx", workers=1, store_chunks=False,
                    pre_extracted={"deck_title": "测试", "pages": [
                        {"page": 3, "title": "结构", "body": "正文"}]},
                    run_context=ctx)
            self.assertEqual(stats["pages"], 1)
            self.assertEqual(stats["described"], 1)
            self.assertEqual(stats["no_info"], 0)
            self.assertEqual(stats["errors"], [])
            self.assertEqual(probe.call_count, 1)
            texts = ingest._figure_texts(conn, 1, skip_no_info=True)
            self.assertIn(3, texts)
            self.assertIn("苯环", texts[3])
            self.assertNotIn(figures.NO_INFO_MARKER, texts[3])
            chunk = ingest.build_page_chunks([], texts)[0]
            self.assertEqual(chunk.source_ref, "p.3")
            self.assertIn("苯环", chunk.text)
            self.assertIn("【图片理解】", chunk.text)


class NoInfoTests(unittest.TestCase):
    def test_single_substantive_section_is_kept(self):
        samples = [SINGLE_STRUCTURE,
                   figures.NO_INFO_MARKER + "\n【结构说明】苯环与羟基相连。",
                   figures.NO_INFO_MARKER + "\n【图示说明】苯环与羟基相连。",
                   figures.NO_INFO_MARKER + "\n【图的内容】没有箭头，但图中可见苯环和羧基相连。",
                   figures.NO_INFO_MARKER + "\n【结构说明】本页只有文字和苯环通过醚键连接的结构式。",
                   figures.NO_INFO_MARKER + "\n【图的内容】本页无图示并包含一个苯环连接羟基的结构式。",
                   figures.NO_INFO_MARKER + "图中可见苯环，苯环通过醚键连接侧链。"]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertFalse(figures.is_no_info(sample))
                self.assertNotIn(figures.NO_INFO_MARKER,
                                 figures.clean_contradictory_no_info(sample))

    def test_plain_and_sectioned_genuine_no_info_remain_filtered(self):
        samples = [figures.NO_INFO_MARKER + "本页只是纯文字，没有图示。",
                   figures.NO_INFO_MARKER + "\n【图的内容】本页只有普通文字，没有图示或化学结构。",
                   figures.NO_INFO_MARKER + "\n【结构说明】无。"]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(figures.is_no_info(sample))

    def test_structure_admission_is_retried_without_retrying_plain_text(self):
        self.assertTrue(figures._no_info_needs_retry(UNRESOLVED_STRUCTURE))
        self.assertTrue(figures._no_info_needs_retry(figures.NO_INFO_MARKER +
            "图中显示多个化学结构式，这些只是结构展示，没有额外知识。"))
        self.assertFalse(figures._no_info_needs_retry(
            figures.NO_INFO_MARKER + "本页没有化学结构式，只有普通文字。"))
        self.assertFalse(figures._no_info_needs_retry(
            figures.NO_INFO_MARKER + "本页展示普通文字，没有化学结构式。"))
        self.assertFalse(figures._no_info_needs_retry(SINGLE_STRUCTURE))
        ctx = figures.PageContext(page=74, title="药物代谢")
        responses = [figures.FigureResult(True, text=UNRESOLVED_STRUCTURE),
                     figures.FigureResult(True, text=SINGLE_STRUCTURE)]
        with mock.patch.object(figures, "describe_page", side_effect=responses) as describe:
            first, second = figures._probe_page(Path("unused.png"), ctx, True)
        self.assertEqual(describe.call_count, 2)
        self.assertEqual(first.text, UNRESOLVED_STRUCTURE)
        self.assertEqual(second.text, SINGLE_STRUCTURE)
        self.assertIn("extra_hint", describe.call_args_list[1].kwargs)

    def test_cached_structure_admission_is_not_reused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            deck = root / "slides.pptx"
            make_deck(deck)
            image = root / "page.png"
            Image.new("RGB", (20, 20), "white").save(image)
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            db.init_db(conn)
            conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,content_hash,role) "
                         "VALUES(1,'test.pptx','test.pptx','pptx','test','hash','courseware')")
            conn.execute("INSERT INTO figure_desc(doc_id,page,image_hash,description,no_info) "
                         "VALUES(1,3,?,?,1)", (figures.image_hash(image), UNRESOLVED_STRUCTURE))
            conn.commit()
            ctx = SimpleNamespace(pipeline="ingest_vision", credential_ref="ingest",
                                  model="fake", api_base="https://example.invalid",
                                  thinking=True, reasoning_effort="high", max_tokens=0)
            with mock.patch.object(config, "LOG_DIR", root / "logs"), \
                 mock.patch.object(figures, "render_pages_cached", return_value=({3: image}, {})), \
                 mock.patch.object(figures, "_probe_gated", return_value=(
                     figures.FigureResult(True, text=SINGLE_STRUCTURE, model="fake"), None)) as probe:
                stats = figures.ingest_document_figures(
                    conn, 1, deck, kind="pptx", workers=1, store_chunks=False,
                    pre_extracted={"deck_title": "测试", "pages": [
                        {"page": 3, "title": "结构", "body": "正文"}]}, run_context=ctx)
            self.assertEqual((stats["described"], stats["reused"], probe.call_count), (1, 0, 1))
            self.assertEqual(stats["errors"], [])
            self.assertIn("苯环", ingest._figure_texts(conn, 1, skip_no_info=True)[3])

    def test_genuine_no_info_cache_remains_reusable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            deck = root / "slides.pptx"
            make_deck(deck)
            image = root / "page.png"
            Image.new("RGB", (20, 20), "white").save(image)
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            db.init_db(conn)
            conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,content_hash,role) "
                         "VALUES(1,'test.pptx','test.pptx','pptx','test','hash','courseware')")
            plain = figures.NO_INFO_MARKER + "本页只是纯文字，没有图示或化学结构。"
            conn.execute("INSERT INTO figure_desc(doc_id,page,image_hash,description,no_info) "
                         "VALUES(1,3,?,?,1)", (figures.image_hash(image), plain))
            conn.commit()
            ctx = SimpleNamespace(pipeline="ingest_vision", credential_ref="ingest",
                                  model="fake", api_base="https://example.invalid",
                                  thinking=True, reasoning_effort="high", max_tokens=0)
            with mock.patch.object(config, "LOG_DIR", root / "logs"), \
                 mock.patch.object(figures, "render_pages_cached", return_value=({3: image}, {})), \
                 mock.patch.object(figures, "_probe_gated") as probe:
                stats = figures.ingest_document_figures(
                    conn, 1, deck, kind="pptx", workers=1, store_chunks=False,
                    pre_extracted={"deck_title": "测试", "pages": [
                        {"page": 3, "title": "结构", "body": "正文"}]}, run_context=ctx)
            self.assertEqual((stats["reused"], stats["described"], stats["no_info"]), (1, 0, 1))
            probe.assert_not_called()

    def test_unresolved_retry_reports_failure_and_preserves_old_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            deck = root / "slides.pptx"
            make_deck(deck)
            image = root / "page.png"
            Image.new("RGB", (20, 20), "white").save(image)
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            db.init_db(conn)
            conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,content_hash,role) "
                         "VALUES(1,'test.pptx','test.pptx','pptx','test','hash','courseware')")
            conn.execute("INSERT INTO figure_desc(doc_id,page,image_hash,description,no_info) "
                         "VALUES(1,3,?,?,1)", (figures.image_hash(image), UNRESOLVED_STRUCTURE))
            conn.commit()
            ctx = SimpleNamespace(pipeline="ingest_vision", credential_ref="ingest",
                                  model="fake", api_base="https://example.invalid",
                                  thinking=True, reasoning_effort="high", max_tokens=0)
            first = figures.FigureResult(True, text=UNRESOLVED_STRUCTURE, model="fake")
            retry = figures.FigureResult(True, text=figures.NO_INFO_MARKER +
                                         "本页只是纯文字，没有图示。", model="fake")
            with mock.patch.object(config, "LOG_DIR", root / "logs"), \
                 mock.patch.object(figures, "render_pages_cached", return_value=({3: image}, {})), \
                 mock.patch.object(figures, "_probe_gated", return_value=(first, retry)):
                stats = figures.ingest_document_figures(
                    conn, 1, deck, kind="pptx", workers=1, store_chunks=False,
                    pre_extracted={"deck_title": "测试", "pages": [
                        {"page": 3, "title": "结构", "body": "正文"}]}, run_context=ctx)
            self.assertEqual(stats["reused"], 0)
            self.assertTrue(any("复核" in error for error in stats["errors"]))
            self.assertEqual(conn.execute("SELECT description FROM figure_desc WHERE page=3").fetchone()[0],
                             UNRESOLVED_STRUCTURE)
            self.assertNotIn(3, stats["usable_pages"])

    def test_cached_wrong_flag_does_not_suppress_substantive_section(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE figure_desc(doc_id INTEGER,page INTEGER,description TEXT,no_info INTEGER)")
        conn.executemany("INSERT INTO figure_desc VALUES(1,?,?,1)", [
            (1, SINGLE_STRUCTURE),
            (2, figures.NO_INFO_MARKER + "\n【图的内容】本页只有普通文字，没有图示。")])
        result = ingest._figure_texts(conn, 1, skip_no_info=True)
        self.assertEqual(set(result), {1})
        self.assertIn("苯环", result[1])


if __name__ == "__main__":
    unittest.main()