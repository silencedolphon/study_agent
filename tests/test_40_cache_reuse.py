"""P1-A 验收：data_revision（WAL 安全）+ 章节/材料缓存 + 向量复用。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
import numpy as np  # noqa: E402

from core import config, db, embed, preview  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dbpath = self.root / "t.db"
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.logs),
            mock.patch.object(config, "DB_PATH", self.dbpath),
            mock.patch.dict(os.environ, {"PREVIEW_CHAPTER_FAST": "1"}),
        ]
        for p in self._patches:
            p.start()
        preview.clear_preview_caches()
        embed.clear_embed_cache()
        conn = db.connect(self.dbpath, immediate=True)
        db.init_db(conn)
        self._write_conn = conn
        self._insert(conn, doc_id=50, page=1, text="第一章 总论 alpha",
                     heading="药理学 > 第1章 总论", ref="p.1")
        self._insert(conn, doc_id=50, page=2, text="第一章 总论 beta",
                     heading="药理学 > 第1章 总论", ref="p.2")
        db.bump_data_revision(conn)
        conn.commit()

    def tearDown(self):
        try:
            for p in self._patches:
                p.stop()
        finally:
            try:
                self._write_conn.close()
            except Exception:
                pass
        preview.clear_preview_caches()
        embed.clear_embed_cache()

    @staticmethod
    def _insert(conn, *, doc_id, page, text, heading, ref):
        rel = "d/%d.pdf" % doc_id
        row = conn.execute("SELECT id FROM documents WHERE path=?", (rel,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO documents(id, path, abs_path, kind, title, content_hash, "
                "n_chunks, n_chars, status) VALUES(?,?,?,?,?,?,?,?,?)",
                (int(doc_id), rel, rel, "pdf", "药理学", "h", 2, 10, "ok"))
        doc_pk = int(doc_id)
        conn.execute(
            "INSERT INTO chunks(doc_id, ord, text, text_norm, heading_path, "
            "source_ref, char_len, token_est, content_hash, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (doc_pk, page, text, text.replace(" ", ""), heading, ref,
             len(text), len(text) // 2, db.sha256_text(text), 0.0))

    def _add_chunk(self, text="新增内容 gamma"):
        conn = db.connect(self.dbpath, immediate=True)
        try:
            self._insert(conn, doc_id=50, page=3, text=text,
                         heading="药理学 > 第1章 总论", ref="p.3")
            db.bump_data_revision(conn)
            conn.commit()
        finally:
            conn.close()


class TestDataRevision(_Base):
    def test_missing_is_zero_and_bump_increments(self):
        conn = db.connect(self.dbpath)
        try:
            conn.execute("DELETE FROM meta WHERE key=?",
                         (db.DATA_REVISION_KEY,))
            conn.commit()
            self.assertEqual(db.data_revision(conn), 0)
            self.assertEqual(db.bump_data_revision(conn), 1)
            self.assertEqual(db.bump_data_revision(conn), 2)
        finally:
            conn.close()

    def test_wal_main_file_mtime_unchanged_but_revision_changes(self):
        self._write_conn.commit()
        before_mtime = self.dbpath.stat().st_mtime_ns
        before_rev = db.data_revision(self._write_conn)
        self._write_conn.execute(
            "INSERT INTO chunks(doc_id, ord, text, heading_path, source_ref, "
            "content_hash) VALUES(50, 9, 'wal write', '药理学 > 第1章', 'p.9', 'x')")
        self.assertEqual(db.bump_data_revision(self._write_conn), before_rev + 1)
        self._write_conn.commit()          # 保持 WAL 未 checkpoint
        after_mtime = self.dbpath.stat().st_mtime_ns
        self.assertEqual(before_mtime, after_mtime,
                         "WAL 提交后主库 mtime 不应作为失效依据")
        self.assertEqual(db.data_revision(self._write_conn), before_rev + 1)


class TestListChaptersCache(_Base):
    def test_cache_hit_deepcopy_and_invalidation(self):
        calls = {"n": 0}
        real = preview._list_chapters_uncached

        def wrapped(*a, **kw):
            calls["n"] += 1
            return real(*a, **kw)

        with mock.patch.object(preview, "_list_chapters_uncached", side_effect=wrapped):
            r1 = preview.list_chapters(50, db_path=self.dbpath)
            r2 = preview.list_chapters(50, db_path=self.dbpath)
        self.assertTrue(r1["ok"])
        self.assertEqual(calls["n"], 1, "同 revision 应命中缓存")
        self.assertIsNot(r1, r2)
        r1["chapters"][0]["name"] = "被调用方改坏"
        r3 = preview.list_chapters(50, db_path=self.dbpath)
        self.assertNotEqual(r3["chapters"][0]["name"], "被调用方改坏",
                            "缓存必须 deepcopy，调用方不能污染")
        self._add_chunk()
        r4 = preview.list_chapters(50, db_path=self.dbpath)
        self.assertEqual(sum(c["n_blocks"] for c in r4["chapters"]), 3,
                         "revision 变化必须让缓存失效并看到新块")

    def test_sections_artifact_signature_invalidates(self):
        art = self.logs / "knowledge" / "50-章节细目.json"
        art.parent.mkdir(parents=True)
        calls = {"n": 0}
        real = preview._load_sections_artifact

        def wrapped(doc_id):
            calls["n"] += 1
            return real(doc_id)

        with mock.patch.object(preview, "_load_sections_artifact", side_effect=wrapped):
            preview.list_chapters(50, db_path=self.dbpath)
            preview.list_chapters(50, db_path=self.dbpath)
            self.assertEqual(calls["n"], 1)
            art.write_text(json.dumps({"chapters": []}), encoding="utf-8")
            preview.list_chapters(50, db_path=self.dbpath)
            self.assertGreaterEqual(calls["n"], 2, "sections 产物变化必须失效")


class TestChapterMaterialCache(_Base):
    def test_material_cache_deepcopy_and_revision_invalidation(self):
        calls = {"n": 0}
        real = preview._assemble_chapter

        def wrapped(*a, **kw):
            calls["n"] += 1
            return real(*a, **kw)

        with mock.patch.object(preview, "_assemble_chapter", side_effect=wrapped):
            r1 = preview.read_chapter_ch(50, 1, db_path=self.dbpath)
            r2 = preview.read_chapter_ch(50, 1, db_path=self.dbpath)
        self.assertTrue(r1[2])
        self.assertEqual(calls["n"], 1, "同 revision + block hash 应命中材料缓存")
        self.assertIsNot(r1, r2)
        r1[1]["name"] = "被调用方改坏"
        r3 = preview.read_chapter_ch(50, 1, db_path=self.dbpath)
        self.assertNotEqual(r3[1].get("name"), "被调用方改坏",
                            "材料缓存必须 deepcopy")
        self._add_chunk()
        r4 = preview.read_chapter_ch(50, 1, db_path=self.dbpath)
        self.assertIn("新增内容 gamma", r4[2],
                      "revision 变化必须让材料缓存失效并看到新块")

    def test_fast_and_slow_same_output(self):
        preview.clear_preview_caches()
        with mock.patch.dict(os.environ, {"PREVIEW_CHAPTER_FAST": "1"}):
            fast = preview.read_chapter_ch(50, 1, db_path=self.dbpath)
        preview.clear_preview_caches()
        with mock.patch.dict(os.environ, {"PREVIEW_CHAPTER_FAST": "0"}):
            slow = preview.read_chapter_ch(50, 1, db_path=self.dbpath)
        self.assertEqual(fast[2], slow[2], "fast/slow 材料输出必须一致")
        self.assertEqual(fast[1].get("no"), slow[1].get("no"))


class _FakeEmb:
    def __init__(self, dim=4):
        self.dim = dim
        self.calls = 0

    def embed_documents(self, texts, batch_size=16, progress=False):
        self.calls += 1
        return np.array([[float(len(t)), 1.0, 2.0, 3.0] for t in texts],
                        dtype="float32")


class TestEmbedReuse(_Base):
    def test_same_text_forward_once(self):
        fake = _FakeEmb()
        texts = ["甲", "乙"]
        v1 = embed.embed_documents_cached(texts, embedder=fake)
        v2 = embed.embed_documents_cached(texts, embedder=fake)
        self.assertEqual(fake.calls, 1, "同文本二次入库应 0 次 ONNX 前向")
        np.testing.assert_array_equal(v1, v2)
        self.assertEqual(v1.shape, (2, 4))
        self.assertEqual(embed.embed_cache_stats()["hit"], 2)

    def test_different_model_or_dim_never_mixed(self):
        fake = _FakeEmb(dim=4)
        embed.embed_documents_cached(["甲"], embedder=fake)
        self.assertEqual(fake.calls, 1)
        with mock.patch.object(config, "EMBED_MODEL_REPO", "other-model"):
            embed.embed_documents_cached(["甲"], embedder=fake)
        self.assertEqual(fake.calls, 2, "不同模型不得命中同一缓存")
        fake2 = _FakeEmb(dim=8)
        embed.embed_documents_cached(["乙"], embedder=fake2)
        self.assertEqual(fake2.calls, 1)

    def test_old_vectors_readable(self):
        vec = np.arange(4, dtype="float32")
        blob = embed.to_blob(vec)
        np.testing.assert_array_equal(embed.from_blob(blob, dim=4), vec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
