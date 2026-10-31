# -*- coding: utf-8 -*-
"""S08B 验收：教材补充章候选 / 名词预习候选的共享材料进程内缓存（零模型/临时库）。

覆盖（16 条）：
- teach `_chapter_candidates`：命中统计 / deepcopy / data_revision 失效 / 签名随行变化 /
  revision=0 不缓存 / `SHARED_MATERIAL_CACHE=0` 回退 / clear / 旧实现逐字一致 /
  空结果不缓存 / 键含 db_path（两库不串味）/ 无教材旧行为；
- terms `_preview_artifact`：命中 / deepcopy / artifact sha 变化失效 / revision 失效 /
  开关回退 / 缺失与损坏旧路径 / 键含 doc+chapter / 无教材旧行为；
- 统计与清理接口。

红线：全程临时 SQLite 库 + 临时 `config.LOG_DIR`；**不碰真实 index/logs/notes，
不调模型，不写任何真实产物**。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, teach, terms_preview  # noqa: E402


# ---------------------------------------------------------------- fixtures
def make_db(path: Path, *, revision: int = 7, chapters: int = 6,
            blocks_per_chapter: int = 4, doc_id: int = 50,
            textbook: bool = True, with_chunks: bool = True) -> None:
    """建一个最小可用库：meta/documents/chunks（只含被缓存逻辑读取的列）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE documents(id INTEGER PRIMARY KEY, title TEXT, path TEXT,"
            " abs_path TEXT, kind TEXT, role TEXT, corpus TEXT,"
            " n_chunks INTEGER, size_bytes INTEGER);"
            "CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER,"
            " ord INTEGER, heading_path TEXT, text TEXT);")
        if revision:
            conn.execute("INSERT INTO meta VALUES('data_revision', ?)", (str(revision),))
        conn.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)",
            (int(doc_id), "药理学 第6版", "raw/教材/tb.pdf", "/tmp/tb.pdf",
             "pdf", "textbook" if textbook else "courseware", "main",
             chapters * blocks_per_chapter if with_chunks else 0, 12345))
        if with_chunks:
            cid = 1
            for h in range(1, chapters + 1):
                for _ in range(blocks_per_chapter):
                    conn.execute(
                        "INSERT INTO chunks(id,doc_id,ord,heading_path,text) VALUES(?,?,?,?,?)",
                        (cid, int(doc_id), cid - 1,
                         "药理学 第6版 > 第%d章 测试章%d" % (h, h), "正文%d" % cid))
                    cid += 1
        conn.commit()
    finally:
        conn.close()


def open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect("file:%s?mode=ro" % Path(path).as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def set_revision(path: Path, value: int) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("UPDATE meta SET value=? WHERE key='data_revision'", (str(value),))
        conn.commit()
    finally:
        conn.close()


def add_chapter_chunk(path: Path, chapter_no: int, name: str) -> None:
    conn = sqlite3.connect(str(path))
    try:
        max_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM chunks").fetchone()[0]
        conn.execute(
            "INSERT INTO chunks(id,doc_id,ord,heading_path,text) VALUES(?,?,?,?,?)",
            (max_id + 1, 50, max_id, "药理学 第6版 > 第%d章 %s" % (chapter_no, name), "x"))
        conn.commit()
    finally:
        conn.close()


class _Base(unittest.TestCase):
    """临时库 + 临时 LOG_DIR + 每例清缓存。"""

    def setUp(self):
        import shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="s08b-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        self.logs = self.tmp / "logs"
        (self.logs / "knowledge").mkdir(parents=True)
        self._patches = [
            mock.patch.dict(os.environ, {"SHARED_MATERIAL_CACHE": "1"}),
            mock.patch.object(config, "LOG_DIR", self.logs),
        ]
        for p in self._patches:
            p.start()
        teach.clear_shared_material_cache()
        self.addCleanup(teach.clear_shared_material_cache)

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    # -- 通用
    def _db(self, name="tb.sqlite", **kw) -> Path:
        db = self.tmp / name
        make_db(db, **kw)
        return db

    def _tb(self, db: Path) -> tuple:
        conn = open_ro(db)
        try:
            return conn, teach.pick_textbook(conn)
        except Exception:
            conn.close()
            raise

    def _preview(self, chapter_no: int, terms, doc_id: int = 50) -> Path:
        p = self.logs / "knowledge" / ("%d-预习总结-第%d章.json" % (doc_id, chapter_no))
        p.write_text(json.dumps({"terms": terms, "meta": {"chapter": chapter_no}},
                                ensure_ascii=False), encoding="utf-8")
        return p


# ---------------------------------------------------------------- 教材章候选
class TestTeachChapterCache(_Base):
    def test_hit_and_stats(self):
        """同库同 revision 第二次命中；旧实现结果逐字一致。"""
        db = self._db()
        conn, tb = self._tb(db)
        try:
            cold = teach._chapter_candidates(conn, tb)
            stats_mid = teach.shared_material_cache_stats()
            warm = teach._chapter_candidates(conn, tb)
            stats_end = teach.shared_material_cache_stats()
        finally:
            conn.close()
        self.assertEqual(cold, warm)
        self.assertEqual(cold, teach._chapter_candidates_uncached(open_ro(db), tb))
        self.assertEqual(len(cold), 6)
        self.assertEqual(stats_mid["store"], 1)
        self.assertEqual(stats_end["hit"], 1)
        self.assertGreaterEqual(stats_end["size"], 1)

    def test_deepcopy(self):
        """调用方修改返回值不污染缓存。"""
        db = self._db()
        conn, tb = self._tb(db)
        try:
            first = teach._chapter_candidates(conn, tb)
            first.append({"heading_path": "污染", "name": "污染", "no": 0, "n_blocks": 0})
            first[0]["name"] = "被改"
            second = teach._chapter_candidates(conn, tb)
        finally:
            conn.close()
        self.assertEqual(len(second), 6)
        self.assertNotIn("污染", [c["name"] for c in second])
        self.assertNotEqual(second[0]["name"], "被改")

    def test_invalidates_on_data_revision(self):
        """data_revision 变化 → 失效并读到新章（WAL 下主库 mtime 不变也安全）。"""
        db = self._db()
        conn, tb = self._tb(db)
        try:
            before = teach._chapter_candidates(conn, tb)
        finally:
            conn.close()
        add_chapter_chunk(db, 9, "新章")
        set_revision(db, 8)
        conn = open_ro(db)
        try:
            after = teach._chapter_candidates(conn, tb)
        finally:
            conn.close()
        self.assertEqual(len(before), 6)
        self.assertEqual(len(after), 7)
        self.assertTrue(any("第9章" in c["name"] for c in after))

    def test_signature_changes_with_rowing(self):
        """内容变但 revision 没变时，教材行签名仍能让键变化（兜底失效）。"""
        db = self._db()
        conn, tb = self._tb(db)
        try:
            teach._chapter_candidates(conn, tb)
        finally:
            conn.close()
        tb2 = dict(tb, n_chunks=int(tb["n_chunks"]) + 1)
        conn = open_ro(db)
        try:
            out = teach._chapter_candidates(conn, tb2)
            stats = teach.shared_material_cache_stats()
        finally:
            conn.close()
        self.assertEqual(len(out), 6)
        self.assertGreaterEqual(stats["miss"], 1, "签名变化应视为新键（miss）")

    def test_revision_zero_not_cached(self):
        """revision 缺失/0：不读不写缓存，逐字走旧查询。"""
        db = self._db(revision=0)
        conn, tb = self._tb(db)
        try:
            a = teach._chapter_candidates(conn, tb)
            b = teach._chapter_candidates(conn, tb)
            plain = teach._chapter_candidates_uncached(conn, tb)
            stats = teach.shared_material_cache_stats()
        finally:
            conn.close()
        self.assertEqual(a, b)
        self.assertEqual(a, plain)
        self.assertEqual(stats["hit"], 0)
        self.assertEqual(stats["store"], 0)
        self.assertEqual(stats["size"], 0)

    def test_switch_off(self):
        """SHARED_MATERIAL_CACHE=0：全部走旧路径，不产生命中/存储。"""
        db = self._db()
        with mock.patch.dict(os.environ, {"SHARED_MATERIAL_CACHE": "0"}):
            conn, tb = self._tb(db)
            try:
                a = teach._chapter_candidates(conn, tb)
                b = teach._chapter_candidates(conn, tb)
                stats = teach.shared_material_cache_stats()
            finally:
                conn.close()
        self.assertEqual(a, b)
        self.assertEqual(stats["hit"], 0)
        self.assertEqual(stats["store"], 0)
        self.assertEqual(stats["size"], 0)
        self.assertFalse(stats["enabled"])

    def test_clear(self):
        """clear 清空缓存与统计，下一次重新 miss。"""
        db = self._db()
        conn, tb = self._tb(db)
        try:
            teach._chapter_candidates(conn, tb)
            teach.clear_shared_material_cache()
            empty = teach.shared_material_cache_stats()
            teach._chapter_candidates(conn, tb)
            after = teach.shared_material_cache_stats()
        finally:
            conn.close()
        self.assertEqual(empty["size"], 0)
        self.assertEqual(empty["hit"], 0)
        self.assertGreaterEqual(after["miss"], 1)

    def test_empty_result_not_cached(self):
        """教材没有 chunks：旧空结果不缓存（不把"暂时没章"固化）。"""
        db = self._db(with_chunks=False)
        conn, tb = self._tb(db)
        try:
            out = teach._chapter_candidates(conn, tb)
            stats = teach.shared_material_cache_stats()
        finally:
            conn.close()
        self.assertEqual(out, [])
        self.assertEqual(stats["store"], 0)
        self.assertEqual(stats["size"], 0)
        self.assertGreaterEqual(stats["skip"], 1)

    def test_key_contains_db_path(self):
        """两本临时库同名 doc/revision：缓存键含 db_path，不串味。"""
        db_a = self._db("a.sqlite", chapters=3)
        db_b = self._db("b.sqlite", chapters=5)
        conn_a, tb_a = self._tb(db_a)
        conn_b, tb_b = self._tb(db_b)
        try:
            out_a = teach._chapter_candidates(conn_a, tb_a)
            out_b = teach._chapter_candidates(conn_b, tb_b)
            again_a = teach._chapter_candidates(conn_a, tb_a)
            again_b = teach._chapter_candidates(conn_b, tb_b)
        finally:
            conn_a.close()
            conn_b.close()
        self.assertEqual(len(out_a), 3)
        self.assertEqual(len(out_b), 5)
        self.assertEqual(out_a, again_a)
        self.assertEqual(out_b, again_b)
        self.assertEqual(teach.shared_material_cache_stats()["size"], 2)

    def test_no_textbook_old_behavior(self):
        """库里没有教材：pick_textbook 返回 None，缓存层不崩。"""
        db = self._db(textbook=False)
        conn = open_ro(db)
        try:
            self.assertIsNone(teach.pick_textbook(conn))
        finally:
            conn.close()


# ---------------------------------------------------------------- 名词预习候选
class TestTermsPreviewCache(_Base):
    TERMS = [{"term": "乙酰胆碱", "en": "ACh", "def": "递质", "why": "重要"},
             {"term": "阿托品", "en": "atropine", "def": "M阻断", "why": "解毒"}]

    def _chapter(self, no=5, name="测试章5"):
        return {"no": no, "name": name}

    def test_hit_and_stats(self):
        """同 artifact/同 revision：第二次命中且结果一致。"""
        db = self._db()
        self._preview(5, self.TERMS)
        conn = open_ro(db)
        try:
            cold = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
            stats_mid = teach.shared_material_cache_stats()
            warm = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
            stats_end = teach.shared_material_cache_stats()
        finally:
            conn.close()
        self.assertEqual(cold, warm)
        self.assertEqual([t["term"] for t in cold[1]], ["乙酰胆碱", "阿托品"])
        self.assertEqual(stats_mid["store"], 1)
        self.assertEqual(stats_end["hit"], 1)

    def test_deepcopy(self):
        db = self._db()
        self._preview(5, self.TERMS)
        conn = open_ro(db)
        try:
            first = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
            first[1].append({"term": "污染"})
            first[1][0]["term"] = "被改"
            second = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
        finally:
            conn.close()
        self.assertEqual([t["term"] for t in second[1]], ["乙酰胆碱", "阿托品"])

    def test_invalidates_on_artifact_sha(self):
        """artifact 内容变（revision 不变）→ sha 变化 → 失效读到新词。"""
        db = self._db()
        self._preview(5, self.TERMS)
        conn = open_ro(db)
        try:
            before = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
            self._preview(5, [{"term": "新词"}])
            after = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
        finally:
            conn.close()
        self.assertEqual([t["term"] for t in before[1]], ["乙酰胆碱", "阿托品"])
        self.assertEqual([t["term"] for t in after[1]], ["新词"])

    def test_invalidates_on_data_revision(self):
        """data_revision 变化 → 失效（即使 artifact 没变）。"""
        db = self._db()
        self._preview(5, self.TERMS)
        conn = open_ro(db)
        try:
            terms_preview._preview_artifact(51, self._chapter(), conn=conn)
            miss_before = teach.shared_material_cache_stats()["miss"]
        finally:
            conn.close()
        set_revision(db, 99)
        conn = open_ro(db)
        try:
            again = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
            stats = teach.shared_material_cache_stats()
        finally:
            conn.close()
        self.assertEqual([t["term"] for t in again[1]], ["乙酰胆碱", "阿托品"])
        self.assertGreater(stats["miss"], miss_before)

    def test_switch_off(self):
        """SHARED_MATERIAL_CACHE=0：走旧路径，不产生缓存。"""
        db = self._db()
        self._preview(5, self.TERMS)
        with mock.patch.dict(os.environ, {"SHARED_MATERIAL_CACHE": "0"}):
            conn = open_ro(db)
            try:
                a = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
                b = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
                stats = teach.shared_material_cache_stats()
            finally:
                conn.close()
        self.assertEqual(a, b)
        self.assertEqual(stats["hit"], 0)
        self.assertEqual(stats["store"], 0)
        self.assertEqual(stats["size"], 0)

    def test_missing_and_corrupt_artifact_old_path(self):
        """artifact 缺失/损坏：旧返回（空 terms + 教材信息），不崩、不缓存坏数据。"""
        db = self._db()
        conn = open_ro(db)
        try:
            miss = terms_preview._preview_artifact(51, {"no": 99, "name": "无"}, conn=conn)
            self.assertEqual(miss[1], [])
            self.assertEqual(miss[2].get("textbook_doc_id"), 50)
            size_after_missing = teach.shared_material_cache_stats()["size"]
            bad = self._preview(5, self.TERMS)
            bad.write_text("{不是 JSON", encoding="utf-8")
            corrupt = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
            self.assertEqual(corrupt[1], [])
            size_after_corrupt = teach.shared_material_cache_stats()["size"]
        finally:
            conn.close()
        self.assertEqual(size_after_missing, 0)
        self.assertEqual(size_after_corrupt, 0)

    def test_key_contains_doc_and_chapter(self):
        """不同章各用各的 artifact；同一 artifact 不同 doc_id 也分开缓存。"""
        db = self._db()
        self._preview(5, [{"term": "第五章词"}])
        self._preview(6, [{"term": "第六章词"}])
        conn = open_ro(db)
        try:
            c5 = terms_preview._preview_artifact(51, {"no": 5, "name": "c5"}, conn=conn)
            c6 = terms_preview._preview_artifact(51, {"no": 6, "name": "c6"}, conn=conn)
            c5b = terms_preview._preview_artifact(51, {"no": 5, "name": "c5"}, conn=conn)
            other_doc = terms_preview._preview_artifact(52, {"no": 5, "name": "c5"},
                                                        conn=conn)
        finally:
            conn.close()
        self.assertEqual([t["term"] for t in c5[1]], ["第五章词"])
        self.assertEqual([t["term"] for t in c6[1]], ["第六章词"])
        self.assertEqual(c5, c5b)
        self.assertEqual([t["term"] for t in other_doc[1]], ["第五章词"])
        self.assertGreaterEqual(teach.shared_material_cache_stats()["size"], 3)

    def test_matches_uncached_implementation(self):
        """缓存路径与旧实现在同一输入上逐字一致（含 preview_meta）。"""
        db = self._db()
        p = self._preview(5, self.TERMS)
        data = json.loads(p.read_text(encoding="utf-8"))
        data["meta"] = {"chapter": 5, "model": "fake"}
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        conn = open_ro(db)
        try:
            cached = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
            teach.clear_shared_material_cache()
            plain = terms_preview._preview_artifact_uncached(51, self._chapter(),
                                                             conn=conn)
        finally:
            conn.close()
        self.assertEqual(cached, plain)

    def test_no_textbook_old_path(self):
        """库里没有教材：旧返回 (空路径, [], {})，缓存层不崩。"""
        db = self._db(textbook=False)
        conn = open_ro(db)
        try:
            out = terms_preview._preview_artifact(51, self._chapter(), conn=conn)
        finally:
            conn.close()
        self.assertEqual(out[0], Path(""))
        self.assertEqual(out[1], [])
        self.assertEqual(out[2], {})

    def test_cache_clear_entrypoint(self):
        """名词侧 clear 入口与 teach 共用同一缓存存储。"""
        db = self._db()
        self._preview(5, self.TERMS)
        conn = open_ro(db)
        try:
            terms_preview._preview_artifact(51, self._chapter(), conn=conn)
            self.assertGreaterEqual(teach.shared_material_cache_stats()["size"], 1)
            terms_preview.clear_preview_artifact_cache()
            self.assertEqual(teach.shared_material_cache_stats()["size"], 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
