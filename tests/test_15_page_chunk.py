"""整页切块（2026-10-19 用户需求）单元/集成验收。

覆盖：
  · 纯函数 `ingest.build_page_chunks`：每页一块、`source_ref="p.N"` 无区间、
    图片理解并入同一块、空页跳过、heading_path 取页内第一个标题块、
    无文字但有识图的页也建块；
  · `ingest.page_chunk_enabled`：只对 pdf/pptx 非教材课件生效，开关可回退；
  · 设置中心：`config.INGEST_PAGE_CHUNK` 已注册（bool、默认 1、ingest 管线）；
  · 端到端（合成 pptx，无 AI/网络）：
      - 新模式：每页一块、无独立「p.N 图」块、不调 outline、
        不调旧切块器 `chunk_blocks`、返回 `chunk_mode="page"`；
      - 旧模式（开关关闭）：调 outline + `chunk_blocks`、返回值不含 chunk_mode；
      - 识图描述并入：`figure_desc` 有描述 → 该页块内含「【图片理解】」，
        且 `ingest_document_figures(store_chunks=False)`、不重建独立识图块。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
import numpy as np  # noqa: E402

from core import chunk as chunk_mod  # noqa: E402
from core import config, db, extract, figures, ingest  # noqa: E402
from core import outline as outline_mod  # noqa: E402

T15_DIR = env.VERIFY_DIR / "t15_private"
SYNTH_DECK = ["一、总论", "本页讲总论与三类药物的共同作用基础。" * 3,
              "二、各论", "本页讲各论与具体药物的差别。" * 3]


# ---------------------------------------------------------------- fixtures
def setUpModule():
    shutil.rmtree(T15_DIR, ignore_errors=True)
    T15_DIR.mkdir(parents=True, exist_ok=True)


def tearDownModule():
    shutil.rmtree(T15_DIR, ignore_errors=True)


def _make_pptx(path: Path, slides) -> None:
    """合成 pptx：`slides` 是「每页若干行文字」的列表（空列表 = 空白页）。"""
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10), Inches(7.5)
    for lines in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
        if not lines:
            continue
        box = slide.shapes.add_textbox(Inches(0.5), Inches(0.4),
                                       Inches(9), Inches(6.5))
        tf = box.text_frame
        tf.word_wrap = True
        for i, line in enumerate(lines):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.text = line
            if para.runs:
                para.runs[0].font.size = Pt(28 if i == 0 else 16)
    prs.save(str(path))


class _FakeEmb:
    def embed_documents(self, texts, progress=None):  # noqa: ARG002
        return np.zeros((len(texts), 512), dtype=np.float32)


def _new_conn(name: str) -> sqlite3.Connection:
    path = T15_DIR / f"{name}.db"
    for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        try:
            p.unlink()
        except OSError:
            pass
    conn = db.connect(path)
    db.init_db(conn)
    return conn


def _set_page_chunk(test: unittest.TestCase, value: int) -> None:
    """临时覆盖 config.INGEST_PAGE_CHUNK（它是模块级 __getattr__ 动态键，
    直接写 __dict__ 才会遮蔽动态取值；用法与设置中心"下一次任务生效"一致）。"""
    config.__dict__["INGEST_PAGE_CHUNK"] = value
    test.addCleanup(lambda: config.__dict__.pop("INGEST_PAGE_CHUNK", None))


class TestPageChunkPure(unittest.TestCase):
    def test_build_page_chunks_rules(self):
        """每页一块 / p.N 无区间 / 图片理解并入 / 空页跳过 / 标题取首个 heading。"""
        from core.extract import Block

        blocks = [
            Block(text="课件名", page=0, kind="heading", level=1),
            Block(text="一、总论", page=1, kind="heading"),
            Block(text="正文A", page=1),
            Block(text="正文B", page=2),
            Block(text="", page=3, kind="heading", level=2),   # 纯空页
            Block(text="p4正文", page=4),
        ]
        figs = {2: "图里是三种药对比", 5: "纯图页（没有文字层）"}
        chunks = ingest.build_page_chunks(blocks, figs)
        refs = [c.source_ref for c in chunks]
        self.assertEqual(refs, ["p.1", "p.2", "p.4", "p.5"],
                         "每页一块、空页跳过、纯图页也要建块（已并入识图文字）")
        self.assertTrue(all(re.fullmatch(r"p\.\d+", r) for r in refs),
                        f"source_ref 必须是 p.N 且无区间: {refs}")
        self.assertFalse(any("图" in r for r in refs), "不得再出现「p.N 图」独立块")
        self.assertEqual([c.ord for c in chunks], [1, 2, 3, 4])
        self.assertEqual(chunks[0].heading_path, "一、总论")
        self.assertEqual(chunks[1].heading_path, "", "无标题块时 heading_path 为空")
        self.assertIn("正文A", chunks[0].text)
        self.assertNotIn("正文B", chunks[0].text, "不得跨页合并")
        self.assertNotIn("【图片理解】", chunks[0].text, "无识图的页不追加图片段")
        self.assertIn("正文B", chunks[1].text)
        self.assertIn("【图片理解】", chunks[1].text)
        self.assertIn("三种药对比", chunks[1].text)
        self.assertEqual(chunks[3].text, "【图片理解】\n纯图页（没有文字层）")

    def test_page_chunk_enabled_matrix(self):
        """开关开：仅 pdf/pptx 非教材为 True；关：全部 False（旧分支回退）。"""
        _set_page_chunk(self, 1)
        self.assertTrue(ingest.page_chunk_enabled("pdf"))
        self.assertTrue(ingest.page_chunk_enabled("pptx"))
        self.assertFalse(ingest.page_chunk_enabled("docx"))
        self.assertFalse(ingest.page_chunk_enabled("txt"))
        self.assertFalse(ingest.page_chunk_enabled("image"))
        self.assertFalse(ingest.page_chunk_enabled("pptx", is_textbook=True),
                         "教材专线必须走旧路径")
        _set_page_chunk(self, 0)
        for kind in ("pdf", "pptx", "docx"):
            self.assertFalse(ingest.page_chunk_enabled(kind),
                             f"开关关闭时 {kind} 必须回退旧路径")

    def test_config_setting_registered(self):
        """设置中心：INGEST_PAGE_CHUNK 是 ingest 管线的 bool 开关，默认 1。"""
        spec = config._FIELDS_BY_KEY.get("INGEST_PAGE_CHUNK")
        self.assertIsNotNone(spec, "设置中心元数据里必须注册 INGEST_PAGE_CHUNK")
        self.assertEqual(spec["pipeline"], "ingest")
        self.assertEqual(spec["type"], "bool")
        self.assertEqual(int(spec["default"]), 1)
        payload = config.settings_payload()
        pipe = next(p for p in payload["pipelines"] if p["id"] == "ingest")
        item = next(f for f in pipe["fields"] if f["key"] == "INGEST_PAGE_CHUNK")
        self.assertEqual(item["type"], "bool")
        self.assertTrue(item["label"])
        # 环境变量回退（.env 写法 KB_INGEST_PAGE_CHUNK=0）；把设置文件指到
        # 不存在的临时路径，避免本机 config.local.json 里恰好有同名键干扰。
        with mock.patch.object(config, "SETTINGS_FILE",
                               T15_DIR / "no-settings.json"), \
             mock.patch.object(config, "_SETTINGS_MTIME", -1.0), \
             mock.patch.object(config, "_SETTINGS", {}), \
             mock.patch.dict(os.environ, {"KB_INGEST_PAGE_CHUNK": "0"}):
            self.assertEqual(int(config.value_of("INGEST_PAGE_CHUNK")), 0)
            self.assertFalse(ingest.page_chunk_enabled("pdf"))


class TestIngestPageChunkE2E(unittest.TestCase):
    """端到端：合成 pptx + 假嵌入 + 禁识图/禁 outline，全程不联网不花钱。"""

    def setUp(self):
        self.dir = T15_DIR / self._testMethodName
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.pptx = self.dir / "课件.pptx"
        _make_pptx(self.pptx, [SYNTH_DECK, SYNTH_DECK, [], SYNTH_DECK])

    def _run(self, conn, *, with_figures=False, patch_chunk_blocks=None):
        kwargs = {}
        ctx = []
        if patch_chunk_blocks is not None:
            ctx.append(mock.patch.object(chunk_mod, "chunk_blocks", patch_chunk_blocks))
        ctx.append(mock.patch.object(ingest, "get_embedder", lambda: _FakeEmb()))
        for c in ctx:
            c.start()
        try:
            with mock.patch.object(config, "OUTLINE_ANALYSIS", 1):
                return ingest.ingest_file(conn, self.pptx, force=True,
                                          with_figures=with_figures)
        finally:
            for c in ctx:
                c.stop()

    @staticmethod
    def _chunk_rows(conn, doc_id):
        return [dict(r) for r in conn.execute(
            "SELECT ord, text, heading_path, source_ref FROM chunks "
            "WHERE doc_id=? ORDER BY ord", (doc_id,))]

    def test_new_mode_page_chunks(self):
        """新默认模式：每页一块、不跑 outline/旧切块器、无独立识图块。"""
        _set_page_chunk(self, 1)
        conn = _new_conn(self._testMethodName)
        try:
            called = {"n": 0}

            def _boom(*a, **kw):  # pragma: no cover - 调用即失败
                called["n"] += 1
                raise AssertionError("整页切块模式不应调用旧切块器 chunk_blocks")

            with mock.patch.object(outline_mod, "analyze_deck",
                                   side_effect=AssertionError(
                                       "整页切块模式不应调用 outline 结构分析")):
                out = self._run(conn, with_figures=False, patch_chunk_blocks=_boom)
            self.assertEqual(out["chunk_mode"], "page", "新模式返回值应带 chunk_mode=page")
            rows = self._chunk_rows(conn, out["doc_id"])
            self.assertEqual(out["chunks"], len(rows))
            self.assertGreaterEqual(len(rows), 2, "有文字的两页都应各成一块")
            refs = [r["source_ref"] for r in rows]
            self.assertTrue(all(re.fullmatch(r"p\.\d+", r) for r in refs),
                            f"source_ref 必须是 p.N 无区间: {refs}")
            self.assertFalse(any("图" in r for r in refs),
                             "新模式下不得有独立的「p.N 图」块")
            self.assertEqual(len(refs), len(set(refs)), "一页只应一块")
            self.assertEqual(called["n"], 0)
            for r in rows:
                self.assertTrue(r["heading_path"], "无标题块时写库应回退到文档标题")
            doc = conn.execute("SELECT n_chunks FROM documents WHERE id=?",
                               (out["doc_id"],)).fetchone()
            self.assertEqual(int(doc["n_chunks"]), len(rows))
            self.assertEqual(out["outline_pages"], 0, "新模式不做 outline 分析")
        finally:
            conn.close()

    def test_old_mode_keeps_outline_and_chunker(self):
        """开关关闭：调 outline + 旧切块器、返回值不含 chunk_mode（逐字回退）。"""
        _set_page_chunk(self, 0)
        conn = _new_conn(self._testMethodName)
        try:
            seen = {}

            class _FakeOutline:
                pages = {1: ["旧", "路径"], 2: ["旧", "路径"]}
                warnings = []
                cost_cny = 0.0

            def _fake_analyze(*a, **kw):
                seen["analyze"] = True
                return _FakeOutline()

            def _fake_chunk_blocks(blocks, doc_meta=None, outline=None):
                seen["chunk_blocks"] = True
                seen["outline"] = outline
                return [chunk_mod.Chunk(text="OLD-PATH 正文", heading_path="旧面包屑",
                                        source_ref="p.1-2", ord=0)]

            with mock.patch.object(outline_mod, "analyze_deck",
                                   side_effect=_fake_analyze):
                out = self._run(conn, with_figures=False,
                                patch_chunk_blocks=_fake_chunk_blocks)
            self.assertNotIn("chunk_mode", out, "旧模式返回值必须与改造前一致（无 chunk_mode）")
            self.assertTrue(seen.get("analyze"), "旧模式应调用 outline.analyze_deck")
            self.assertTrue(seen.get("chunk_blocks"), "旧模式应调用 chunk_blocks")
            self.assertIsNotNone(seen.get("outline"), "旧模式应把 outline 传给切块器")
            rows = self._chunk_rows(conn, out["doc_id"])
            self.assertEqual([r["text"] for r in rows], ["OLD-PATH 正文"])
        finally:
            conn.close()

    def test_new_mode_merges_figure_text(self):
        """有图页：识图描述并入该页块（store_chunks=False），不重建识图块。"""
        _set_page_chunk(self, 1)
        conn = _new_conn(self._testMethodName)
        called = {}

        def _fake_figures(c, doc_id, path, **kw):
            called["kwargs"] = dict(kw)
            figures.ensure_schema(c)
            c.execute(
                "INSERT INTO figure_desc(doc_id, page, page_title, image_hash,"
                " model, thinking, tokens_in, tokens_out, cost_cny, n_chunks,"
                " created_at, description, no_info)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)",
                (doc_id, 2, "总论", "hash-test", "fake", 1, 0, 0, 0.0, 0,
                 0.0, "图里画了三种药的作用对比"))
            c.commit()
            return {"pages": 1, "described": 0, "reused": 1, "no_info": 0,
                    "chunks": 0, "cost_cny": 0.0, "errors": []}

        def _boom_rewrite(*a, **kw):  # pragma: no cover - 调用即失败
            raise AssertionError("整页切块模式不应重建独立识图块")

        def _boom_chunker(*a, **kw):  # pragma: no cover - 调用即失败
            raise AssertionError("整页切块模式不应调用旧切块器")

        try:
            with mock.patch.object(figures, "ingest_document_figures",
                                   side_effect=_fake_figures), \
                 mock.patch.object(ingest, "_rewrite_figure_chunks",
                                   side_effect=_boom_rewrite):
                out = self._run(conn, with_figures=True,
                                patch_chunk_blocks=_boom_chunker)
            self.assertFalse(called["kwargs"].get("store_chunks", True),
                             "新模式必须传 store_chunks=False（不单独建识图块）")
            rows = self._chunk_rows(conn, out["doc_id"])
            merged = [r for r in rows if "【图片理解】" in r["text"]]
            self.assertEqual(len(merged), 1, f"应只有有图页并入图片段: {rows}")
            self.assertEqual(merged[0]["source_ref"], "p.2")
            self.assertIn("三种药的作用对比", merged[0]["text"])
            self.assertEqual(out["figures"]["reused"], 1)
            self.assertGreaterEqual(len(rows), 2)
            self.assertEqual(out["chunk_mode"], "page")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
