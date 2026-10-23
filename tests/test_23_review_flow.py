"""复习流程后端（C-0128-review-v3，task-35）验收：结构路径自测 + 三步复习数据。

全离线：临时 logs/notes/sqlite/KP 产物；**不调 AI、不碰真实数据**。
覆盖：抽一半 KP、items 结构路径（branch/node_path/summary、无挖空）、
已掌握/未掌握提交、熟悉度自洽、旧 v1 文件 legacy 兼容、笔记分组、预习名词、课件列表。
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, kp_build, review, teach  # noqa: E402

T23 = env.VERIFY_DIR / "t23_private"
DOC = 46
TB = 50
KPS = [
    {"id": "KP-1", "name": "阿托品", "node_name": "阿托品",
     "summary": "阿托品竞争性阻断M胆碱受体，解除平滑肌痉挛，抑制腺体分泌。",
     "points": ["阿托品阻断M胆碱受体", "用于内脏绞痛与麻醉前给药"],
     "pages": [1], "node_path": ["胆碱受体阻断药", "M受体阻断药", "阿托品"]},
    {"id": "KP-2", "name": "东莨菪碱", "node_name": "东莨菪碱",
     "summary": "东莨菪碱可阻断M胆碱受体，用于晕动病与麻醉前给药。",
     "points": ["东莨菪碱中枢抑制作用强", "用于晕动病"],
     "pages": [2], "node_path": ["胆碱受体阻断药", "M受体阻断药", "东莨菪碱"]},
    {"id": "KP-3", "name": "山莨菪碱", "node_name": "山莨菪碱",
     "summary": "山莨菪碱选择性阻断M胆碱受体，改善微循环，用于感染性休克。",
     "points": ["山莨菪碱改善微循环"], "pages": [3],
     "node_path": ["胆碱受体阻断药", "M受体阻断药", "山莨菪碱"]},
    {"id": "KP-4", "name": "神经节阻断药", "node_name": "神经节阻断药",
     "summary": "神经节阻断药阻断NN受体，降低血压，曾用于抗高血压。",
     "points": ["阻断神经节NN受体"], "pages": [4],
     "node_path": ["N受体阻断药", "神经节阻断药"]},
    {"id": "KP-5", "name": "筒箭毒硷", "node_name": "筒箭毒硷",
     "summary": "筒箭毒硷阻断NM受体，使骨骼肌松弛，用作肌松药。",
     "points": ["筒箭毒硷阻断NM受体"], "pages": [5],
     "node_path": ["N受体阻断药", "骨骼肌松弛药", "筒箭毒硷"]},
    {"id": "KP-6", "name": "琥珀胆碱", "node_name": "琥珀胆碱",
     "summary": "琥珀胆碱是去极化型肌松药，与筒箭毒硷机制不同，起效快。",
     "points": ["琥珀胆碱起效快"], "pages": [6],
     "node_path": ["N受体阻断药", "骨骼肌松弛药", "琥珀胆碱"]},
]


def setUpModule():
    shutil.rmtree(T23, ignore_errors=True)
    T23.mkdir(parents=True, exist_ok=True)


def tearDownModule():
    shutil.rmtree(T23, ignore_errors=True)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY, path TEXT, abs_path TEXT, kind TEXT, title TEXT,
        content_hash TEXT, size_bytes INTEGER, mtime REAL, n_chunks INTEGER,
        role TEXT, corpus TEXT, status TEXT, warnings TEXT, extra TEXT,
        indexed_at REAL, updated_at REAL);
    """)
    conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,n_chunks,"
                 "role,corpus,status) VALUES(?,?,?,?,?,?,?,?,?)",
                 (DOC, "raw/x.pptx", "/x.pptx", "pptx",
                  "M胆碱和N胆碱受体阻断药 · 课件", 25, "courseware", "main", "ok"))
    conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,n_chunks,"
                 "role,corpus,status) VALUES(?,?,?,?,?,?,?,?,?)",
                 (TB, "raw/药理学.pdf", "/药理学.pdf", "pdf",
                  "药理学 第6版 刘晓东", 882, "textbook", "main", "ok"))
    conn.commit()
    return conn


def _write_note(path: Path, title: str, kp_id: str = "", doc_id: int = 0,
                body: str = "笔记正文") -> Path:
    lines = ["---", "title: %s" % title, "kind: note", "created: 1792401161",
             "tags: []"]
    if kp_id:
        lines.append("kp_id: %s" % kp_id)
    if doc_id:
        lines.append("doc_id: %d" % int(doc_id))
        lines.append("source_label: 课件 · 知识点 %s" % kp_id)
    lines += ["---", "", "# " + title, "", body, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = T23 / self._testMethodName
        shutil.rmtree(self.dir, ignore_errors=True)
        self.logdir = self.dir / "logs"
        self.notesdir = self.dir / "notes"
        (self.logdir / "knowledge").mkdir(parents=True, exist_ok=True)
        self.notesdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "study.db"
        self.conn = _make_db(self.db_path)
        self.kp_path = self.logdir / "knowledge" / "46-末级知识点总结.json"
        self.kp_path.write_text(json.dumps({
            "meta": {"doc_id": DOC, "deck": "测试课件", "pipeline_version": "C-0058-v4"},
            "knowledge_points": KPS,
        }, ensure_ascii=False), encoding="utf-8")
        self._write_supplement_map(6, "第6章 作用于胆碱受体药", "p.70–p.82")
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.logdir),
            mock.patch.object(config, "NOTES_DIR", self.notesdir),
            mock.patch.object(kp_build, "out_file",
                              lambda doc_id: self.kp_path if int(doc_id) == DOC
                              else self.kp_path.parent / ("missing-%d.json" % int(doc_id))),
            mock.patch.object(kp_build, "doc_title_v2",
                              lambda doc_id, db_path=None: "M胆碱和N胆碱受体阻断药 · 课件"),
            mock.patch.object(teach, "map_courseware_chapter",
                              lambda *a, **k: {"chapter_no": 0}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.conn.close()

    def _write_supplement_map(self, no: int, name: str, label: str) -> None:
        (self.logdir / "knowledge" / "46-教材补充.json").write_text(json.dumps({
            "meta": {"version": "C-0125-teach-ai-v1", "doc_id": DOC,
                     "chapter_map": {"chapter_no": no, "name": name,
                                     "chapter_name": name, "page_label": label,
                                     "source": "local_fallback",
                                     "textbook_doc_id": TB}},
            "kps": {},
        }, ensure_ascii=False), encoding="utf-8")

    def _write_preview_terms(self, no: int = 6) -> Path:
        p = self.logdir / "knowledge" / ("%d-预习总结-第%d章.json" % (TB, no))
        p.write_text(json.dumps({
            "meta": {"doc_id": TB, "chapter_no": no, "chapter_name": "作用于胆碱受体药",
                     "page_label": "p.70–p.82"},
            "terms": [
                {"term": "胆碱受体激动药", "en": "cholinoceptor agonist",
                 "def": "能激动胆碱受体的药物", "why": "分清激动与阻断"},
                {"term": "调节痉挛", "en": "", "def": "睫状肌收缩的状态",
                 "why": "常与调节麻痹对调考"}],
        }, ensure_ascii=False), encoding="utf-8")
        return p

    def _start(self, seed: int = 42):
        return review.start_selftest(DOC, db_path=self.db_path, rng=random.Random(seed))

    @staticmethod
    def _results(rep, mastered=None):
        mastered = mastered or {}
        return {it["kp_id"]: bool(mastered.get(it["kp_id"], True))
                for it in rep["items"]}


class TestSelftestV3(_Base):
    def test_sample_half_structural_items_and_all_mastered(self):
        """6 KP→抽 3；items 是结构路径题面（无挖空）；全掌握 familiarity=1 并持久化。"""
        rep = self._start()
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["n_total"], 6)
        self.assertEqual(rep["n_sample"], 3)
        self.assertEqual(len(rep["items"]), 3)
        self.assertTrue(rep["session_id"])
        names = {k["id"]: k["name"] for k in KPS}
        for it in rep["items"]:
            self.assertIn("branch", it)
            self.assertIn("node_path", it)
            self.assertTrue(it["summary"])
            self.assertNotIn("segments", it, "v3 不再返回挖空题面")
            self.assertNotIn("answers", it)
            self.assertTrue(any(b["name"] == it["name"] for b in it["branch"]),
                            "branch 必须包含 KP 自身")
            self.assertEqual(it["branch"][-1]["name"], it["name"])
            self.assertGreaterEqual(len(it["branch"]), 2, "至少父级→自身")
            self.assertEqual([b["level"] for b in it["branch"]],
                             list(range(1, len(it["branch"]) + 1)))
            src = next(k for k in KPS if k["id"] == it["kp_id"])
            self.assertEqual(it["node_path"], src["node_path"])
            self.assertEqual(it["name"], names[it["kp_id"]])
        self.assertFalse(review.selftest_out_file(DOC).exists())
        res = review.submit_selftest(DOC, rep["session_id"], self._results(rep))
        self.assertTrue(res["ok"])
        self.assertEqual(res["familiarity"], 1.0)
        self.assertEqual(res["n_mastered"], 3)
        self.assertEqual(res["n_unmastered"], 0)
        self.assertEqual(res["unfamiliar_ids"], [])
        art = review.selftest_out_file(DOC)
        self.assertTrue(art.exists())
        data = json.loads(art.read_text(encoding="utf-8"))
        self.assertEqual(data["meta"]["version"], review.REVIEW_VERSION)
        self.assertEqual(data["meta"]["n_mastered"], 3)
        self.assertEqual(len(data["kps"]), 3, "v3 kps 只保留本次抽样")
        for v in data["kps"].values():
            self.assertTrue(v["mastered"])
            self.assertNotIn("answers", v, "v3 不再持久化挖空答案")
        st = review.selftest_status(DOC)
        self.assertTrue(st["exists"])
        self.assertEqual(st["familiarity"], 1.0)
        self.assertEqual(st["n_mastered"], 3)
        self.assertTrue(next(iter(st["kps"].values()))["mastered"])
        self.assertFalse(st["legacy"])
        again = review.submit_selftest(DOC, rep["session_id"], self._results(rep))
        self.assertFalse(again["ok"])

    def test_unmastered_marks_and_flow(self):
        """一个未掌握 → familiarity=2/3、unfamiliar；新一次全掌握后不留历史标记。"""
        rep = self._start(7)
        first = rep["items"][0]["kp_id"]
        res = review.submit_selftest(DOC, rep["session_id"],
                                     self._results(rep, {first: False}))
        self.assertEqual(res["n_mastered"], 2)
        self.assertEqual(res["n_unmastered"], 1)
        self.assertEqual(res["unfamiliar_ids"], [first])
        self.assertAlmostEqual(res["familiarity"], round(2 / 3, 4))
        flow = review.build_flow(self.conn, DOC)
        flags = {k["kp_id"]: (k["mastered"], k["unfamiliar"]) for k in flow["kps"]}
        self.assertEqual(flags[first], (False, True))
        self.assertEqual(flow["selftest"]["n_unmastered"], 1)
        rep2 = self._start(9)
        res2 = review.submit_selftest(DOC, rep2["session_id"], self._results(rep2))
        self.assertEqual(res2["familiarity"], 1.0)
        st = review.selftest_status(DOC)
        self.assertEqual(st["n_unmastered"], 0)
        self.assertEqual(st["unfamiliar_ids"], [])
        self.assertEqual(len(st["kps"]), 3)
        flow2 = review.build_flow(self.conn, DOC)
        self.assertFalse(any(k["unfamiliar"] for k in flow2["kps"]))

    def test_session_forged_expired_and_no_artifact(self):
        """伪造/过期会话、无 KP 产物、非法 doc_id → 明确错误（不抛异常）。"""
        bad = review.submit_selftest(DOC, "deadbeefdeadbeef", {})
        self.assertFalse(bad["ok"])
        self.assertIn("过期", bad["error"])
        rep = self._start(1)
        review._SELFTEST[rep["session_id"]]["created"] -= review.SESSION_TTL + 10
        exp = review.submit_selftest(DOC, rep["session_id"], {})
        self.assertFalse(exp["ok"])
        self.assertIn("过期", exp["error"])
        self.assertFalse(review.start_selftest(0)["ok"])
        self.kp_path.unlink()
        self.assertFalse(review.start_selftest(DOC)["ok"])
        self.assertFalse(review.build_flow(self.conn, DOC)["ok"])

    def test_legacy_v1_file_is_readable(self):
        """旧 v1/v2 文件（familiar/answers 时代）→ status 按 mastered 推算 + legacy 标记。"""
        out = review.selftest_out_file(DOC)
        out.parent.mkdir(parents=True, exist_ok=True)
        kps = {("KP-%d" % i): {"familiar": True, "correct": 4, "n_blanks": 4}
               for i in range(1, 22)}
        kps["KP-99"] = {"familiar": False, "correct": 0, "n_blanks": 4}
        out.write_text(json.dumps({
            "meta": {"version": "C-0127-review-v2", "doc_id": DOC,
                     "n_total": 22, "n_sample": 11, "n_mastered": 11,
                     "n_familiar": 11, "familiarity": 1.0,
                     "generated_at": "2026-10-23T00:00:00"},
            "kps": kps,
        }, ensure_ascii=False), encoding="utf-8")
        st = review.selftest_status(DOC)
        self.assertTrue(st["legacy"])
        self.assertEqual(st["n_sample"], 22)
        self.assertEqual(st["n_mastered"], 21)
        self.assertEqual(st["n_unmastered"], 1)
        self.assertEqual(st["unfamiliar_ids"], ["KP-99"])
        self.assertAlmostEqual(st["familiarity"], round(21 / 22, 4))
        self.assertFalse(st["kps"]["KP-99"]["mastered"])
        flow = review.build_flow(self.conn, DOC)
        self.assertIsNone(next((k for k in flow["kps"]
                                if k["kp_id"] == "KP-99"), None))
        self.assertEqual(flow["selftest"]["n_unmastered"], 1)

    def test_notes_terms_and_coursewares(self):
        """笔记按 KP 分组 + unbound；预习名词；课件列表 selftest 字段为 mastered 口径。"""
        _write_note(self.notesdir / "a.md", "阿托品笔记", "KP-1", DOC, body="阿托品正文")
        _write_note(self.notesdir / "b.md", "东莨菪碱笔记", "KP-2", DOC,
                    body="东莨菪碱正文")
        _write_note(self.notesdir / "c.md", "课件级笔记", "", DOC, body="课件级正文")
        self._write_preview_terms(6)
        flow = review.build_flow(self.conn, DOC)
        self.assertTrue(flow["ok"])
        groups = {g["kp_id"]: g for g in flow["notes"]["groups"]}
        self.assertEqual(len(flow["notes"]["groups"]), 6)
        self.assertEqual([g["kp_id"] for g in flow["notes"]["groups"]],
                         [k["id"] for k in KPS])
        self.assertEqual(len(groups["KP-1"]["notes"]), 1)
        self.assertEqual(len(flow["notes"]["unbound"]), 1)
        self.assertEqual(flow["terms"]["source"], "preview_chapter")
        self.assertEqual(flow["terms"]["n"], 2)
        self.assertEqual(flow["terms"]["chapter"]["no"], 6)
        self.assertEqual([s["key"] for s in flow["stages"]], ["kps", "notes", "terms"])
        items = review.list_courseware_reviews(self.conn)
        self.assertEqual([x["doc_id"] for x in items], [DOC])
        it = items[0]
        self.assertEqual(it["n_kps"], 6)
        self.assertFalse(it["selftest"]["exists"])
        rep = self._start(3)
        review.submit_selftest(DOC, rep["session_id"], self._results(rep))
        it2 = review.list_courseware_reviews(self.conn)[0]
        self.assertTrue(it2["selftest"]["exists"])
        self.assertEqual(it2["selftest"]["familiarity"], 1.0)
        self.assertEqual(it2["selftest"]["n_mastered"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
