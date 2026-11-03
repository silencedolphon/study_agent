"""P1-B 验收：入库一次解析（S05）+ 页图/视觉缓存（S06）。全部 fake，不启 PowerPoint/模型。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import db, extract, figures  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._patches = [mock.patch.dict(os.environ, {
            "INGEST_REUSE_EXTRACT": "1", "FIGURE_CACHE_V2": "1"})]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _db(self):
        path = self.root / "t.db"
        conn = db.connect(path, immediate=True)
        db.init_db(conn)
        conn.execute("INSERT INTO documents(path, abs_path, kind, title, content_hash, "
                     "n_chunks, n_chars, status) VALUES('x.pptx','x.pptx','pptx','T','h',1,1,'ok')")
        conn.commit()
        return conn

    def _fake_doc(self):
        return SimpleNamespace(meta={"title": "T"}, blocks=[
            SimpleNamespace(page=1, kind="heading", text="标题"),
            SimpleNamespace(page=1, kind="text", text="OCR 追加正文"),
        ])


class TestExtractReuse(_Base):
    def test_pre_extracted_avoids_second_extract(self):
        calls = []
        pre = {"deck_title": "T", "pages": [{"page": 1, "title": "h", "body": "OCR 正文"}]}
        with mock.patch.object(extract, "extract_pdf", side_effect=lambda p: calls.append(1)):
            title, pages = figures.extract_pages(self.root / "x.pdf", "pdf",
                                                 pre_extracted=pre)
        self.assertEqual(calls, [], "有预提取页时不得再 extract")
        self.assertEqual(title, "T")
        self.assertEqual(pages[0]["body"], "OCR 正文")

    def test_without_pre_extracted_uses_old_path(self):
        calls = []
        fake = SimpleNamespace(meta={"title": "T"}, blocks=[])
        def fake_extract(p):
            calls.append(1)
            return fake
        with mock.patch.object(extract, "extract_pdf", side_effect=fake_extract):
            figures.extract_pages(self.root / "x.pdf", "pdf")
        self.assertEqual(calls, [1], "旧调用方仍走原 extract 路径")

    def test_switch_off_reextracts(self):
        calls = []
        def fake_extract(p):
            calls.append(1)
            return SimpleNamespace(meta={"title": "T"}, blocks=[])
        with mock.patch.dict(os.environ, {"INGEST_REUSE_EXTRACT": "0"}), \
             mock.patch.object(extract, "extract_pdf", side_effect=fake_extract):
            figures.extract_pages(self.root / "x.pdf", "pdf",
                                  pre_extracted={"deck_title": "T", "pages": []})
        self.assertEqual(calls, [1], "INGEST_REUSE_EXTRACT=0 回退旧双解析")

    def test_ocr_text_kept_in_visual_context(self):
        pre = {"deck_title": "T", "pages": [
            {"page": 1, "title": "标题", "body": "OCR 追加正文"}]}
        _, pages = figures.extract_pages(self.root / "x.pdf", "pdf",
                                         pre_extracted=pre)
        ctxs = figures.build_contexts(pages, "T")
        self.assertIn("OCR 追加正文", ctxs[1].to_prompt())

    def test_ingest_page_pre_from_res_keeps_ocr(self):
        from core import ingest
        pre = ingest._page_pre_from_res(self._fake_doc(), self.root / "x.pptx")
        self.assertEqual(pre["deck_title"], "T")
        self.assertEqual(pre["pages"][0]["body"], "OCR 追加正文")


class _FakePix:
    width = 1
    height = 1
    samples = b"\x00\x00\x00"


class _FakePage:
    def get_pixmap(self, dpi=110):
        return _FakePix()


class _FakeDoc:
    page_count = 3
    def __init__(self, *a, **kw):
        self.loaded = []
    def load_page(self, i):
        self.loaded.append(i + 1)
        return _FakePage()
    def close(self):
        pass


class TestRenderPages(_Base):
    def test_empty_list_never_renders(self):
        with mock.patch.object(figures, "render_pages", wraps=figures.render_pages):
            self.assertEqual(figures.render_pages(self.root / "x.pdf", self.root / "out",
                                                  "pdf", pages=[]), {})
        self.assertFalse((self.root / "out").exists() and any((self.root / "out").iterdir()))

    def test_none_all_and_selective_and_missing(self):
        docs = []
        class M:
            @staticmethod
            def open(path):
                d = _FakeDoc(); docs.append(d); return d
        fake_mod = SimpleNamespace(open=M.open)
        with mock.patch.dict(sys.modules, {"pymupdf": fake_mod}):
            allp = figures.render_pages(self.root / "x.pdf", self.root / "o1",
                                        "pdf", pages=None)
            sel = figures.render_pages(self.root / "x.pdf", self.root / "o2",
                                       "pdf", pages=[3])
            miss = figures.render_pages(self.root / "x.pdf", self.root / "o3",
                                        "pdf", pages=[99])
        self.assertEqual(sorted(allp), [1, 2, 3])
        self.assertEqual(sorted(sel), [3])
        self.assertEqual(miss, {}, "明确传入缺页集合 → 不产出该页")
        self.assertEqual(docs[0].loaded, [1, 2, 3])


class TestPageCache(_Base):
    def test_full_cache_hit_zero_render_and_file_change_invalidates(self):
        src = self.root / "x.pdf"
        src.write_bytes(b"v1")
        out = self.root / "out"
        calls = []
        def fake_render(src_, outdir, kind="", width=1600, dpi=None, pages=None):
            calls.append(pages)
            outdir.mkdir(parents=True, exist_ok=True)
            made = {}
            for p in (pages or [1, 2]):
                f = outdir / ("slide_%02d.png" % p)
                f.write_bytes(b"img")
                made[int(p)] = f
            return made
        with mock.patch.object(figures, "render_pages", side_effect=fake_render):
            slides1, info1 = figures.render_pages_cached(src, out, "pdf")
            slides2, info2 = figures.render_pages_cached(src, out, "pdf")
            src.write_bytes(b"v2")
            slides3, info3 = figures.render_pages_cached(src, out, "pdf")
        self.assertFalse(info1["cache_hit"])
        self.assertTrue(info2["cache_hit"], "完整缓存命中不得再渲染")
        self.assertEqual(info2["rendered"], [])
        self.assertFalse(info3["cache_hit"], "源文件变化必须失效")

    def test_legacy_dir_still_renders_without_deleting(self):
        src = self.root / "x.pdf"; src.write_bytes(b"v")
        out = self.root / "old"; out.mkdir()
        old = out / "slide_01.png"; old.write_bytes(b"old")
        calls = []
        def fake_render(src_, outdir, kind="", width=1600, dpi=None, pages=None):
            calls.append(1)
            return {1: old}
        with mock.patch.object(figures, "render_pages", side_effect=fake_render):
            figures.render_pages_cached(src, out, "pdf")
        self.assertEqual(calls, [1], "无 manifest 的旧目录按旧行为渲染")
        self.assertTrue(old.exists(), "旧页图不得删除")

    def test_switch_off_always_renders(self):
        src = self.root / "x.pdf"; src.write_bytes(b"v")
        out = self.root / "out"
        calls = []
        def fake_render(src_, outdir, kind="", width=1600, dpi=None, pages=None):
            calls.append(1); outdir.mkdir(parents=True, exist_ok=True)
            f = outdir / "slide_01.png"; f.write_bytes(b"i"); return {1: f}
        with mock.patch.object(figures, "render_pages", side_effect=fake_render):
            figures.render_pages_cached(src, out, "pdf")
            with mock.patch.dict(os.environ, {"FIGURE_CACHE_V2": "0"}):
                figures.render_pages_cached(src, out, "pdf")
        self.assertEqual(len(calls), 2, "FIGURE_CACHE_V2=0 回退旧渲染")


class TestVisualCache(_Base):
    def test_key_changes_and_legacy_compat(self):
        ctx = figures.PageContext(page=1, body="OCR 正文")
        k1 = figures.visual_cache_key(ctx, "h", thinking=True)
        self.assertNotEqual(k1, figures.visual_cache_key(ctx, "h", thinking=False))
        ctx2 = figures.PageContext(page=1, body="不同正文")
        self.assertNotEqual(k1, figures.visual_cache_key(ctx2, "h", thinking=True))
        out = self.root / "out"; out.mkdir()
        self.assertEqual(figures.visual_cache_state(out, 1, k1), "legacy",
                         "旧缓存缺元数据 → 继续按旧规则读")
        figures.write_visual_meta(out, 1, k1)
        self.assertEqual(figures.visual_cache_state(out, 1, k1), "match")
        self.assertEqual(figures.visual_cache_state(out, 1, "other"), "mismatch")
        # 元数据不落正文
        raw = (out / "slide_01.visual.json").read_text(encoding="utf-8")
        self.assertNotIn("OCR 正文", raw)


class TestIngestDocumentFiguresReuse(_Base):
    def test_uses_pre_extracted_and_no_paid_calls(self):
        (self.root / "x.pptx").write_bytes(b"fake-source-for-fingerprint")
        conn = self._db()
        self.addCleanup(conn.close)
        pre = {"deck_title": "T", "pages": [{"page": 1, "title": "h",
                                             "body": "OCR 正文"}]}
        with mock.patch.object(extract, "extract_pptx",
                               side_effect=AssertionError("不应二次解析")), \
             mock.patch.object(figures, "render_pages_cached",
                               return_value=({}, {"cache_hit": False})), \
             mock.patch.object(figures, "_probe_page",
                               side_effect=AssertionError("不应调视觉模型")):
            stats = figures.ingest_document_figures(
                conn, 1, self.root / "x.pptx", kind="pptx",
                pre_extracted=pre, store_chunks=False)
        self.assertTrue(stats.get("extract_reused"))
        self.assertEqual(stats["described"], 0)
        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
