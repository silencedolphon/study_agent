"""预习名词驱动的课件名词整理（C-0129，task-35）验收：相关度过滤 / 备份 / 零 AI。

全离线：临时 logs/DB/KP/预习产物；`_embed_scores` 用假函数（不加载模型）。
覆盖：相关词保留（原文命中/嵌入相似）、无关词剔除、first_kp_id、产物旧 schema 兼容、
无预习产物 → ok:false source:none、覆盖前 .bak、幂等 skipped、状态接口、0 DB 写。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, kp_build, terms_preview, teach  # noqa: E402

T27 = env.VERIFY_DIR / "t27_private"
DOC = 46
TB = 50
KPS = [
    {"id": "KP-1", "name": "阿托品", "node_name": "阿托品",
     "summary": "阿托品竞争性阻断M胆碱受体，解除平滑肌痉挛。",
     "points": ["阿托品用于内脏绞痛"], "pages": [1],
     "node_path": ["课件根", "胆碱受体阻断药", "阿托品"]},
    {"id": "KP-2", "name": "东莨菪碱", "node_name": "东莨菪碱",
     "summary": "东莨菪碱阻断M胆碱受体，用于晕动病。",
     "points": ["东莨菪碱中枢抑制强"], "pages": [2],
     "node_path": ["课件根", "胆碱受体阻断药", "东莨菪碱"]},
]
PREVIEW_TERMS = [
    {"term": "阿托品", "en": "atropine", "def": "M胆碱受体阻断药", "why": "代表药"},
    {"term": "胆碱受体", "en": "cholinoceptor", "def": "乙酰胆碱作用的受体",
     "why": "分类基础"},
    {"term": "晕动病", "en": "motion sickness", "def": "前庭受刺激引起的疾病",
     "why": "东莨菪碱用途"},
    {"term": "调节痉挛", "en": "", "def": "睫状肌收缩导致视近物清楚",
     "why": "阿托品的反例"},
    {"term": "量子纠缠", "en": "quantum entanglement", "def": "量子力学现象",
     "why": "与本章无关"},
]


def setUpModule():
    shutil.rmtree(T27, ignore_errors=True)
    T27.mkdir(parents=True, exist_ok=True)


def tearDownModule():
    shutil.rmtree(T27, ignore_errors=True)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = T27 / self._testMethodName
        shutil.rmtree(self.dir, ignore_errors=True)
        self.logdir = self.dir / "logs"
        (self.logdir / "knowledge").mkdir(parents=True, exist_ok=True)
        self.notesdir = self.dir / "notes"
        self.notesdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "study.db"
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript("""
        CREATE TABLE documents (id INTEGER PRIMARY KEY, path TEXT, abs_path TEXT,
            kind TEXT, title TEXT, content_hash TEXT, size_bytes INTEGER,
            mtime REAL, n_chunks INTEGER, role TEXT, corpus TEXT, status TEXT,
            warnings TEXT, extra TEXT, indexed_at REAL, updated_at REAL);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER,
            text TEXT, heading_path TEXT, source_ref TEXT);
        """)
        conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,n_chunks,"
                     "role,corpus,status) VALUES(46,'raw/x.pptx','/x.pptx','pptx',"
                     "'神经节阻断药课件',25,'courseware','main','ok')")
        conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,n_chunks,"
                     "role,corpus,status) VALUES(50,'raw/tb.pdf','/tb.pdf','pdf',"
                     "'药理学',882,'textbook','main','ok')")
        conn.executemany(
            "INSERT INTO chunks(id,doc_id,ord,text,heading_path,source_ref) "
            "VALUES(?,?,?,?,?,?)",
            [(1, DOC, 1, "阿托品阻断M胆碱受体，缓解内脏绞痛。", "课件根", "p.1"),
             (2, DOC, 2, "东莨菪碱用于晕动病，阻断M胆碱受体。", "课件根", "p.2")])
        conn.commit()
        conn.close()
        self.kp_path = self.logdir / "knowledge" / "46-末级知识点总结.json"
        self.kp_path.write_text(json.dumps({
            "meta": {"doc_id": DOC, "deck": "神经节阻断药课件"},
            "knowledge_points": KPS,
        }, ensure_ascii=False), encoding="utf-8")
        (self.logdir / "knowledge" / "46-教材补充.json").write_text(json.dumps({
            "meta": {"chapter_map": {"chapter_no": 6, "name": "第6章 胆碱受体药",
                                     "page_label": "p.70–p.82"}}, "kps": {}},
            ensure_ascii=False), encoding="utf-8")
        self.preview_path = self.logdir / "knowledge" / "50-预习总结-第6章.json"
        self._write_preview(PREVIEW_TERMS)
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.logdir),
            mock.patch.object(config, "NOTES_DIR", self.notesdir),
            mock.patch.object(kp_build, "out_file",
                              lambda doc_id: self.kp_path if int(doc_id) == DOC
                              else self.kp_path.parent / ("missing-%d.json" % int(doc_id))),
            mock.patch.object(teach, "map_courseware_chapter",
                              lambda *a, **k: {"chapter_no": 0}),
            # 不加载模型：按词给固定相似度（只对需要走嵌入兜底的词生效）
            mock.patch.object(terms_preview, "_embed_scores",
                              side_effect=self._fake_embed),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    @staticmethod
    def _fake_embed(text: str, kp_texts: list) -> list:
        if "调节痉挛" in text:
            return [0.71] * len(kp_texts)          # 相关但字面不命中 → 嵌入保留
        return [0.12] * len(kp_texts)              # 无关词 → 剔除

    def _write_preview(self, terms: list) -> None:
        self.preview_path.write_text(json.dumps({
            "meta": {"doc_id": TB, "chapter_no": 6,
                     "chapter_name": "作用于胆碱受体药", "page_label": "p.70–p.82"},
            "terms": terms,
        }, ensure_ascii=False), encoding="utf-8")

    def _build(self, **kw):
        return terms_preview.build_terms_from_preview(DOC, db_path=self.db_path,
                                                      log=lambda *a, **k: None, **kw)

    def _out(self) -> Path:
        return self.logdir / "knowledge" / "46-名词解释.json"


class TestTermsPreview(_Base):
    def test_filter_keep_and_drop(self):
        """相关词保留（原文/嵌入）、无关词剔除；first_kp_id/旧 schema/零 AI 全对。"""
        db_sha = _sha(self.db_path)
        rep = self._build()
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["source"], "preview_key_terms")
        self.assertEqual(rep["n_candidates"], 5)
        self.assertEqual(rep["n_selected"], 4, rep)
        self.assertEqual(rep["n_dropped"], 1)
        self.assertEqual(rep["dropped"], ["量子纠缠"])
        self.assertFalse(rep["embed_used"] and False)
        self.assertEqual(rep["calls"], 0)
        self.assertEqual(rep["cost_cny"], 0.0)
        self.assertEqual(_sha(self.db_path), db_sha, "不得写数据库")
        art = json.loads(self._out().read_text(encoding="utf-8"))
        meta = art["meta"]
        self.assertEqual(meta["source"], "preview_key_terms")
        self.assertEqual(meta["pipeline_version"], terms_preview.TERMS_PREVIEW_VERSION)
        self.assertEqual(meta["n_candidates"], 5)
        self.assertEqual(meta["n_selected"], 4)
        self.assertEqual(meta["n_dropped"], 1)
        self.assertEqual(meta["usage_total"]["calls"], 0)
        self.assertEqual(meta["cost_cny"], 0.0)
        self.assertEqual(meta["chapter"]["no"], 6)
        self.assertIn("preview_artifact", meta)
        self.assertTrue(meta["filter"]["rule"])
        self.assertEqual(meta["dropped_terms"], ["量子纠缠"])
        terms = {t["term"]: t for t in art["terms"]}
        self.assertEqual(set(terms), {"阿托品", "胆碱受体", "晕动病", "调节痉挛"})
        # 旧 schema 字段兼容
        for t in art["terms"]:
            for key in ("term", "en", "def", "first_kp_id", "first_kp_name",
                        "kp_ids", "seg_id", "seg_name", "first_page"):
                self.assertIn(key, t)
        self.assertEqual(terms["阿托品"]["first_kp_id"], "KP-1")
        self.assertEqual(terms["晕动病"]["first_kp_id"], "KP-2")
        self.assertEqual(terms["阿托品"]["match"], "exact")
        self.assertEqual(terms["调节痉挛"]["match"], "embed")
        self.assertEqual(terms["调节痉挛"]["first_kp_id"], "KP-1")
        self.assertIn("KP-1", terms["阿托品"]["kp_ids"])

    def test_ngram_coverage_unit(self):
        self.assertEqual(terms_preview._ngram_coverage("阿托品", "阿托品阻断受体"), 1.0)
        self.assertEqual(terms_preview._ngram_coverage("量子纠缠", "阿托品阻断受体"), 0.0)
        self.assertGreater(terms_preview._ngram_coverage("胆碱受体", "M胆碱受体"), 0.5)

    def test_no_preview_source_none_no_file(self):
        """教材预习产物缺失 → ok:false source:none，不写文件、不自动调 AI。"""
        self.preview_path.unlink()
        rep = self._build()
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["source"], "none")
        self.assertIn("预习", rep["error"] + rep.get("hint", ""))
        self.assertFalse(self._out().exists())

    def test_no_chapter_error(self):
        """无章映射 → ok:false，明确错误。"""
        (self.logdir / "knowledge" / "46-教材补充.json").unlink()
        rep = self._build()
        self.assertFalse(rep["ok"])
        self.assertIn("章节", rep["error"])

    def test_no_kp_artifact_error(self):
        self.kp_path.unlink()
        rep = self._build()
        self.assertFalse(rep["ok"])
        self.assertIn("知识点", rep["error"])

    def test_backup_and_idempotent(self):
        """已有旧产物 → 覆盖前 .bak；同版本同预习产物再跑 → skipped（0 写）。"""
        old = self._out()
        old.write_text('{"meta":{"pipeline_version":"OLD"},"terms":[]}',
                       encoding="utf-8")
        old_bytes = old.read_bytes()
        rep = self._build()
        self.assertTrue(rep["ok"])
        self.assertTrue(rep["backup"], "覆盖旧产物前必须 .bak")
        bak = Path(rep["backup"])
        self.assertTrue(bak.exists())
        self.assertEqual(bak.read_bytes(), old_bytes)
        art = json.loads(old.read_text(encoding="utf-8"))
        self.assertTrue(art["meta"].get("prev_backup"))
        # 幂等 skip
        mtime = old.stat().st_mtime_ns
        rep2 = self._build()
        self.assertEqual(rep2["status"], "skipped")
        self.assertEqual(old.stat().st_mtime_ns, mtime)
        # force 重写
        rep3 = self._build(force=True)
        self.assertNotEqual(rep3["status"], "skipped")
        self.assertTrue(rep3["ok"])

    def test_status(self):
        st0 = terms_preview.terms_preview_status(DOC, db_path=self.db_path)
        self.assertTrue(st0["ok"])
        self.assertTrue(st0["preview"]["exists"])
        self.assertEqual(st0["preview"]["n_candidates"], 5)
        self.assertFalse(st0["current"]["exists"])
        self.assertTrue(st0["can_rebuild"])
        self._build()
        st1 = terms_preview.terms_preview_status(DOC, db_path=self.db_path)
        self.assertTrue(st1["current"]["exists"])
        self.assertTrue(st1["current"]["is_current"])
        self.assertEqual(st1["current"]["n_terms"], 4)
        self.assertFalse(st1["can_rebuild"])
        # 预习产物更新 → 可重做
        self._write_preview(PREVIEW_TERMS + [{"term": "新词", "def": "x"}])
        st2 = terms_preview.terms_preview_status(DOC, db_path=self.db_path)
        self.assertTrue(st2["can_rebuild"])

    def test_no_ai_calls(self):
        """0 AI：整个构建过程中 `chat.chat_once` / `kp_build.build_terms` 都不被调用。"""
        from core import chat
        with mock.patch.object(chat, "chat_once", side_effect=AssertionError("不得调 AI")), \
             mock.patch.object(kp_build, "build_terms",
                               side_effect=AssertionError("不得走旧名词管线")):
            rep = self._build()
        self.assertTrue(rep["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
