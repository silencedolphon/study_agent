"""GPT 独立验收修复 A/E/F/I 回归：混合课件页 / 页图缓存完整性 / data_revision / 视觉 key / 504。"""
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
from core import config, db, figures, preview  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        import shutil
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = Path(self.tmp)
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.logs),
            mock.patch.object(config, "FIGURE_RENDER_DPI", 110),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def _png(self, page):
        p = self.root / ("slide_%02d.png" % page)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"png")
        return p


class TestMixedPages(_Base):
    def _res(self):
        blocks = [
            SimpleNamespace(page=1, kind="heading", text="第一页标题"),
            SimpleNamespace(page=1, kind="text", text="第一页正文"),
            SimpleNamespace(page=3, kind="text", text="第三页正文 OCR 追加"),
        ]
        return SimpleNamespace(meta={"title": "混合PPT", "page_count": 3,
                                     "slides": 3}, blocks=blocks)

    def test_page_pre_fills_all_pages(self):
        from core import ingest
        pre = ingest._page_pre_from_res(self._res(), self.root / "x.pptx")
        self.assertEqual([p["page"] for p in pre["pages"]], [1, 2, 3])
        self.assertEqual(pre["pages"][1]["title"], "")
        self.assertEqual(pre["pages"][1]["body"], "")
        self.assertIn("OCR 追加", pre["pages"][2]["body"])
        self.assertEqual(pre["page_count"], 3)

    def test_extract_pages_pre_branch_fills_empty_pages(self):
        pre = {"deck_title": "T", "page_count": 3,
               "pages": [{"page": 1, "title": "a", "body": "x"},
                         {"page": 3, "title": "b", "body": "z"}]}
        with mock.patch.object(figures, "reuse_extract_enabled", return_value=True):
            _, pages = figures.extract_pages(self.root / "x.pptx", "pptx",
                                             fill_empty_pages=True,
                                             pre_extracted=pre)
        self.assertEqual([p["page"] for p in pages], [1, 2, 3])
        self.assertEqual(pages[1]["body"], "")
        ctxs = figures.build_contexts(pages, "T")
        self.assertIn(2, ctxs, "纯图片页必须有视觉上下文")

    def test_ocr_context_differs_from_raw_extract(self):
        pre = {"deck_title": "T", "page_count": 1,
               "pages": [{"page": 1, "title": "t", "body": "OCR 追加的正文"}]}
        with mock.patch.object(figures, "reuse_extract_enabled", return_value=True):
            _, pages = figures.extract_pages(self.root / "x.pdf", "pdf",
                                             fill_empty_pages=True,
                                             pre_extracted=pre)
        self.assertIn("OCR 追加的正文", pages[0]["body"],
                      "复用路径必须保留入库实际 OCR 文本（不是重新提取的原始页）")

    def test_fake_visual_plan_includes_textless_page(self):
        conn = db.connect(self.root / "t.db", immediate=True)
        db.init_db(conn)
        conn.execute("INSERT INTO documents(id, path, abs_path, kind, title, "
                     "content_hash, role, n_chunks, n_chars, status) VALUES("
                     "50,'x.pptx','x.pptx','pptx','T','h','courseware',0,0,'ok')")
        conn.commit()
        pre = {"deck_title": "T", "page_count": 3, "pages": [
            {"page": 1, "title": "a", "body": "x"},
            {"page": 2, "title": "", "body": ""},
            {"page": 3, "title": "c", "body": "z"}]}
        seen = []

        def fake_gated(img, page_ctx, thinking, gate, inflight, run_context=None):
            seen.append(page_ctx.page)
            return figures.FigureResult(True, text="这一页有足够长的描述内容" * 3), None

        imgs = {p: self._png(p) for p in (1, 2, 3)}
        try:
            with mock.patch.object(figures, "extract_pages",
                                   return_value=("T", pre["pages"])), \
                 mock.patch.object(figures, "render_pages_cached",
                                   return_value=(imgs, {"cache_hit": False})), \
                 mock.patch.object(figures, "_probe_gated", side_effect=fake_gated):
                stats = figures.ingest_document_figures(
                    conn, 50, self.root / "x.pptx", kind="pptx",
                    only_with_images=False, store_chunks=False, workers=1)
        finally:
            conn.close()
        self.assertIn(2, seen, "纯图片页必须真的进入识图候选/调用")
        self.assertEqual(sorted(seen), [1, 2, 3])
        self.assertEqual(stats["described"], 3)


class TestRenderCache(_Base):
    def _manifest(self, pages):
        return {"src_fingerprint": "fp", "backend": figures.RENDER_BACKEND_VERSION,
                "kind": "pdf", "width": 1600, "dpi": 110,
                "pages": {str(p): Path(png).name for p, png in pages.items()}}

    def test_full_request_missing_page_renders_only_missing(self):
        out = self.root / "out"; out.mkdir()
        p1, p3 = out / "slide_01.png", out / "slide_03.png"
        p1.write_bytes(b"1"); p3.write_bytes(b"3")
        (out / figures.RENDER_CACHE_META).write_text(
            json.dumps(self._manifest({1: p1, 3: p3})), encoding="utf-8")
        rendered = []

        def fake_render(src, outdir, kind="", width=1600, dpi=None, pages=None):
            rendered.append(pages)
            outdir.mkdir(parents=True, exist_ok=True)
            made = {}
            for p in (pages or []):
                f = outdir / ("slide_%02d.png" % p); f.write_bytes(b"x"); made[p] = f
            return made

        with mock.patch.object(figures, "_src_fingerprint", return_value="fp"), \
             mock.patch.object(figures, "_expected_page_numbers",
                               return_value=[1, 2, 3]), \
             mock.patch.object(figures, "render_pages", side_effect=fake_render):
            slides, info = figures.render_pages_cached(self.root / "x.pdf", out, "pdf")
        self.assertEqual(rendered, [[2]], "只补渲染缺失页")
        self.assertEqual(sorted(slides), [1, 2, 3])
        self.assertFalse(info["cache_hit"], "缺页不能报完整命中")
        self.assertEqual(info["rendered"], [2])
        self.assertTrue(p1.exists() and p3.exists(), "旧页图不能删除")

    def test_selected_pages_not_polluted_by_old_dir(self):
        out = self.root / "out"; out.mkdir()
        for p in (1, 2, 3):
            (out / ("slide_%02d.png" % p)).write_bytes(b"x")
        def fake_render(src, outdir, kind="", width=1600, dpi=None, pages=None):
            made = {}
            for p in (pages or []):
                f = outdir / ("slide_%02d.png" % p); f.write_bytes(b"x"); made[p] = f
            return made
        with mock.patch.object(figures, "_expected_page_numbers",
                               return_value=[1, 2, 3]), \
             mock.patch.object(figures, "render_pages", side_effect=fake_render):
            slides, info = figures.render_pages_cached(
                self.root / "x.pdf", out, "pdf", pages=[2])
        self.assertEqual(sorted(slides), [2], "pages=[2] 不能返回目录旧页")

    def test_partial_first_then_full_detects_missing(self):
        out = self.root / "out"; out.mkdir()
        p2 = out / "slide_02.png"; p2.write_bytes(b"2")
        (out / figures.RENDER_CACHE_META).write_text(
            json.dumps(self._manifest({2: p2})), encoding="utf-8")
        rendered = []
        def fake_render(src, outdir, kind="", width=1600, dpi=None, pages=None):
            rendered.append(pages)
            result = {}
            for p in (pages or []):
                f = outdir / ("slide_%02d.png" % p); f.write_bytes(b"x"); result[p] = f
            return result
        with mock.patch.object(figures, "_src_fingerprint", return_value="fp"), \
             mock.patch.object(figures, "_expected_page_numbers",
                               return_value=[1, 2, 3]), \
             mock.patch.object(figures, "render_pages", side_effect=fake_render):
            slides, info = figures.render_pages_cached(self.root / "x.pdf", out, "pdf")
        self.assertEqual(sorted(rendered[0]), [1, 3])
        self.assertFalse(info["cache_hit"])
        self.assertEqual(sorted(slides), [1, 2, 3])

    def test_pptx_selected_pages_filter_old_png(self):
        script = self.root / "render.ps1"; script.write_text("x")
        out = self.root / "pptx_out"; out.mkdir()
        for p in (1, 2, 3):
            (out / ("slide_%02d.png" % p)).write_bytes(b"x")
        with mock.patch.object(figures, "_render_script", return_value=script), \
             mock.patch("subprocess.run",
                        return_value=SimpleNamespace(stdout="OK 2", stderr="")):
            res = figures.render_pages(self.root / "x.pptx", out, "pptx", pages=[2])
        self.assertEqual(sorted(res), [2], "假 COM 路径也不能返回目录全部旧 PNG")


class TestRevisionBumps(_Base):
    def _db(self):
        conn = db.connect(self.root / "r.db", immediate=True)
        db.init_db(conn)
        conn.execute("INSERT INTO documents(id, path, abs_path, kind, title, "
                     "content_hash, role, n_chunks, n_chars, status) VALUES("
                     "50,'t.pdf','t.pdf','pdf','T','h','textbook',1,10,'ok')")
        conn.execute("INSERT INTO chunks(id, doc_id, ord, text, heading_path, "
                     "source_ref) VALUES(1,50,1,'x','T > 第1章 总论','p.1')")
        figures.ensure_schema(conn)
        conn.execute("INSERT INTO figure_desc(doc_id, page, page_title, image_hash, "
                     "description, no_info, n_chunks) VALUES(50,1,'t','h','desc',0,1)")
        conn.commit()
        return conn

    def test_mark_no_info_bumps_revision(self):
        conn = self._db()
        try:
            before = db.data_revision(conn)
            figures._mark_no_info(conn, 50, 1)
            self.assertGreater(db.data_revision(conn), before,
                               "no_info 更新必须同事务 bump")
            self.assertEqual(conn.execute(
                "SELECT no_info FROM figure_desc WHERE doc_id=50 AND page=1"
            ).fetchone()[0], 1)
            conn.commit()
        finally:
            conn.close()

    def test_clear_document_figures_bumps_revision(self):
        conn = self._db()
        try:
            conn.execute("INSERT INTO chunks(doc_id, ord, text, source_ref) "
                         "VALUES(50,2,'图','p.1 图')")
            conn.commit()
            before = db.data_revision(conn)
            n = figures.clear_document_figures(conn, 50)
            self.assertEqual(n, 1)
            self.assertGreater(db.data_revision(conn), before)
        finally:
            conn.close()

    def test_delete_invalidates_chapters_cache(self):
        import tools.dashboard as dash
        conn = self._db()
        try:
            db.bump_data_revision(conn)
            conn.commit()
            preview.clear_preview_caches()
            before = preview.list_chapters(50, db_path=self.root / "r.db")
            self.assertTrue(before.get("ok"), before)
            dash.api_delete_doc(conn, 50, delete_file=False,
                                purge_orphans=False, dry_run=False)
            after = preview.list_chapters(50, db_path=self.root / "r.db")
            self.assertFalse(after.get("ok"), "删除后缓存必须失效，不能返回旧章节")
        finally:
            conn.close()

    def test_dashboard_delete_bumps_revision_and_preview_misses(self):
        import tools.dashboard as dash
        conn = self._db()
        try:
            before = db.data_revision(conn)
            res = dash.api_delete_doc(conn, 50, delete_file=False,
                                      purge_orphans=False, dry_run=False)
            self.assertEqual(res.get("deleted_doc_id"), 50)
            self.assertGreater(db.data_revision(conn), before,
                               "删除资料必须同事务 bump revision")
            self.assertFalse(conn.execute(
                "SELECT 1 FROM documents WHERE id=50").fetchone())
        finally:
            conn.close()


class TestVisualKeyAndHttp(_Base):
    def test_visual_key_includes_cap(self):
        ctx = figures.PageContext(page=1, body="x")
        a = figures.visual_cache_key(ctx, "h", max_tokens=1024)
        b = figures.visual_cache_key(ctx, "h", max_tokens=4096)
        c = figures.visual_cache_key(ctx, "h", max_tokens=1024, mode="zero_text")
        self.assertNotEqual(a, b, "改变输出 cap 不能复用旧描述")
        self.assertNotEqual(a, c, "模式变化必须进 key")

    def test_http_504_unknown_single_attempt(self):
        import http.client
        calls = []
        def fake(req, timeout):
            calls.append(1)
            raise __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
                "http://x", 504, "gateway", {}, mock.Mock(read=lambda: b"timeout"))
        payload = {"model": "v", "messages": [], "max_tokens": 1}
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
             mock.patch.object(config, "apply_token_cap",
                               side_effect=lambda p, pipe: dict(p)), \
             mock.patch.object(figures.time, "sleep", return_value=None):
            st, body = figures._http_post("http://x/chat/completions", payload,
                                          "k", retries=4, wait=0)
        self.assertEqual(len(calls), 1, "504 只能 1 次请求")
        self.assertIn("remote_unknown", str(body))

    def test_http_503_still_retries(self):
        from core import agent_bridge
        import urllib.error
        calls = []
        def fake(req, timeout):
            calls.append(1)
            raise urllib.error.HTTPError("http://x", 503, "busy", {},
                                         mock.Mock(read=lambda: b"busy"))
        payload = {"model": "v", "messages": [], "max_tokens": 1}
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
             mock.patch.object(agent_bridge, "complete", return_value=None), \
             mock.patch.object(config, "apply_token_cap",
                               side_effect=lambda p, pipe: dict(p)), \
             mock.patch.object(figures.time, "sleep", return_value=None):
            st, body = figures._http_post("http://x", payload, "k",
                                          retries=1, wait=0)
        self.assertEqual(len(calls), 2, "503 是已知失败，保留重试")


if __name__ == "__main__":
    unittest.main(verbosity=2)
