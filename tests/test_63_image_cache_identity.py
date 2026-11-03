"""Offline image-cache regressions using temporary one-page PDFs."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf
from core import config, db, figure_cache, figures, share
from tools import dashboard


class ImageCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / "logs"
        self.patcher = mock.patch.object(config, "LOG_DIR", self.log, create=True)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _pdf(self, directory, content):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "same.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), content, fontsize=20)
        doc.save(str(path))
        doc.close()
        return path

    def test_same_name_and_new_version_cannot_overwrite_each_other(self):
        one = self._pdf(self.root / "one", "first molecule")
        two = self._pdf(self.root / "two", "second molecule")
        dir1 = figures.document_image_dir(one, 10)
        dir2 = figures.document_image_dir(two, 11)
        self.assertNotEqual(dir1, dir2)
        first, _ = figures.render_pages_cached(one, dir1, kind="pdf")
        second, _ = figures.render_pages_cached(two, dir2, kind="pdf")
        first_bytes = first[1].read_bytes()
        self.assertNotEqual(first_bytes, second[1].read_bytes())
        self.assertEqual(first[1].read_bytes(), first_bytes)

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE documents(id INTEGER, path TEXT, content_hash TEXT)")
        conn.executemany("INSERT INTO documents VALUES(?,?,?)", [
            (10, str(one), figures._src_fingerprint(one)),
            (11, str(two), figures._src_fingerprint(two)),
        ])
        self.assertEqual(dashboard._slide_png_path(10, 1, conn), first[1])
        self.assertEqual(dashboard._slide_png_path(11, 1, conn), second[1])
        # A changed source must never silently display a previous version.
        conn.execute("UPDATE documents SET content_hash=? WHERE id=10", ("a" * 64,))
        self.assertIsNone(dashboard._slide_png_path(10, 1, conn))
        self.assertEqual(first[1].read_bytes(), first_bytes)

    def test_verified_legacy_cache_is_copied_but_original_preserved(self):
        src = self._pdf(self.root / "source", "diagram")
        legacy = self.log / "figure_desc" / figures._safe_dirname(src.stem)
        slides, _ = figures.render_pages_cached(src, legacy, kind="pdf")
        original = slides[1].read_bytes()
        outdir = figures.document_image_dir(src, 33)
        self.assertEqual(figures._adopt_verified_legacy_renders(src, outdir), 1)
        self.assertEqual((outdir / "slide_01.png").read_bytes(), original)
        self.assertEqual(slides[1].read_bytes(), original)
        reused, info = figures.render_pages_cached(src, outdir, kind="pdf")
        self.assertTrue(info["cache_hit"])
        self.assertEqual(reused[1].read_bytes(), original)
        other = self._pdf(self.root / "different", "different source")
        self.assertEqual(figures._adopt_verified_legacy_renders(
            other, figures.document_image_dir(other, 34)), 0)

    def test_share_round_trip_includes_document_version_pages(self):
        src = self._pdf(self.root / "source", "shared molecule")
        source_db = self.root / "source.db"
        conn = db.connect(source_db)
        db.init_db(conn)
        digest = db.sha256_file(src)
        conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,content_hash,"
                     "n_chunks,n_chars,status,role) VALUES(1,?,?,?,?,?,1,20,'ok','courseware')",
                     ("raw/same.pdf", str(src), "pdf", "same", digest))
        conn.execute("INSERT INTO chunks(doc_id,ord,text,source_ref,content_hash) "
                     "VALUES(1,1,?,?,?)", ("test material", "p.1", "b" * 64))
        conn.execute("INSERT INTO embeddings(chunk_id,model,dim,vec) VALUES(1,?,?,?)",
                     (config.EMBED_MODEL_REPO, config.EMBED_DIM, b"\x00" * (4 * config.EMBED_DIM)))
        conn.commit()
        conn.close()
        outdir = figures.document_image_dir(src, 1)
        images, _ = figures.render_pages_cached(src, outdir, kind="pdf")
        package = self.root / "document.zip"
        share.export_doc(1, package, db_path=source_db, with_original=True,
                         with_pages=True, log_dir=self.log, root=self.root,
                         raw_dir=self.root / "source")
        destination = self.root / "destination"
        destination.mkdir()
        imported = share.import_package(package, db_path=destination / "study.db",
                                        log_dir=destination / "logs", root=destination,
                                        raw_dir=destination / "raw")
        self.assertEqual(imported["counts"]["pages"], 1)
        page_dir = figure_cache.directory(destination / "logs", imported["doc_id"], digest)
        self.assertEqual((page_dir / "slide_01.png").read_bytes(), images[1].read_bytes())

    def test_invalid_cache_identity_is_rejected(self):
        self.assertIsNone(figure_cache.directory(self.log, "../1", "a" * 64))
        self.assertIsNone(figure_cache.directory(self.log, 1, "../bad"))


if __name__ == "__main__":
    unittest.main()
