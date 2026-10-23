"""复习流程后端（C-0127，task-32）验收：自测挖空/熟悉度 + 三步复习数据。

全离线：临时 `logs/`、`notes/`、sqlite、KP/预习产物；**不调 AI、不碰真实数据**。
覆盖：抽一半 KP、segments/answers 一致、全对 familiarity=1、错一空→unfamiliar、
持久化与 status/flow 一致、笔记按 KP 分组+unbound、预习名词读取与空态、
无产物/伪造会话/过期会话错误、课件复习列表。
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
from core import config, kp_build, review, study, teach  # noqa: E402

T23 = env.VERIFY_DIR / "t23_private"
DOC = 46
TB = 50
KPS = [
    {"id": "KP-1", "name": "阿托品", "node_name": "阿托品",
     "summary": "阿托品竞争性阻断M胆碱受体，解除平滑肌痉挛，抑制腺体分泌。",
     "points": ["阿托品阻断M胆碱受体", "用于内脏绞痛与麻醉前给药"],
     "pages": [1], "node_path": ["胆碱受体阻断药", "阿托品"]},
    {"id": "KP-2", "name": "东莨菪碱", "node_name": "东莨菪碱",
     "summary": "东莨菪碱可阻断M胆碱受体，用于晕动病与麻醉前给药，中枢抑制作用较强。",
     "points": ["东莨菪碱中枢抑制作用强", "用于晕动病"],
     "pages": [2], "node_path": ["胆碱受体阻断药", "东莨菪碱"]},
    {"id": "KP-3", "name": "山莨菪碱", "node_name": "山莨菪碱",
     "summary": "山莨菪碱选择性阻断M胆碱受体，改善微循环，用于感染性休克。",
     "points": ["山莨菪碱改善微循环"], "pages": [3],
     "node_path": ["胆碱受体阻断药", "山莨菪碱"]},
    {"id": "KP-4", "name": "神经节阻断药", "node_name": "神经节阻断药",
     "summary": "神经节阻断药阻断NN受体，降低血压，曾用于抗高血压。",
     "points": ["阻断神经节NN受体"], "pages": [4],
     "node_path": ["N受体阻断药", "神经节阻断药"]},
    {"id": "KP-5", "name": "筒箭毒硷", "node_name": "筒箭毒硷",
     "summary": "筒箭毒硷阻断NM受体，使骨骼肌松弛，用作肌松药。",
     "points": ["筒箭毒硷阻断NM受体"], "pages": [5],
     "node_path": ["N受体阻断药", "骨骼肌松弛药"]},
    {"id": "KP-6", "name": "琥珀胆碱", "node_name": "琥珀胆碱",
     "summary": "琥珀胆碱是去极化型肌松药，与筒箭毒硷机制不同，起效快。",
     "points": ["琥珀胆碱起效快"], "pages": [6],
     "node_path": ["N受体阻断药", "骨骼肌松弛药"]},
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
        self._write_kp_artifact(KPS)
        # 课件→第6章映射（supplement meta.chapter_map，供 terms/flow 用）
        self._write_supplement_map(6, "第6章 作用于胆碱受体药", "p.70–p.82")
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.logdir),
            mock.patch.object(config, "NOTES_DIR", self.notesdir),
            mock.patch.object(kp_build, "out_file",
                              lambda doc_id: self.kp_path if int(doc_id) == DOC
                              else self.kp_path.parent / ("missing-%d.json" % int(doc_id))),
            mock.patch.object(kp_build, "doc_title_v2",
                              lambda doc_id, db_path=None: "M胆碱和N胆碱受体阻断药 · 课件"),
            # 本地兜底 map 可能开真实库；测试里显式钉成空（terms 用例走 supplement map）
            mock.patch.object(teach, "map_courseware_chapter",
                              lambda *a, **k: {"chapter_no": 0}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.conn.close()

    # ---- fixtures
    def _write_kp_artifact(self, kps) -> None:
        self.kp_path.parent.mkdir(parents=True, exist_ok=True)
        self.kp_path.write_text(json.dumps({
            "meta": {"doc_id": DOC, "deck": "测试课件", "pipeline_version": "C-0058-v4"},
            "knowledge_points": kps,
        }, ensure_ascii=False), encoding="utf-8")

    def _write_supplement_map(self, no: int, name: str, label: str) -> None:
        (self.logdir / "knowledge" / "46-教材补充.json").write_text(json.dumps({
            "meta": {"version": "C-0125-teach-ai-v1", "doc_id": DOC,
                     "chapter_map": {"chapter_no": no, "name": name,
                                     "chapter_name": name, "page_label": label,
                                     "source": "local_fallback",
                                     "textbook_doc_id": TB}},
            "kps": {},
        }, ensure_ascii=False), encoding="utf-8")

    def _write_preview_terms(self, no: int = 6, items=None) -> Path:
        p = self.logdir / "knowledge" / ("%d-预习总结-第%d章.json" % (TB, no))
        p.write_text(json.dumps({
            "meta": {"doc_id": TB, "chapter_no": no, "chapter_name": "作用于胆碱受体药",
                     "page_label": "p.70–p.82"},
            "terms": items if items is not None else [
                {"term": "胆碱受体激动药", "en": "cholinoceptor agonist",
                 "def": "能激动胆碱受体的药物", "why": "分清激动与阻断"},
                {"term": "调节痉挛", "en": "", "def": "睫状肌收缩的状态",
                 "why": "常与调节麻痹对调考"}],
        }, ensure_ascii=False), encoding="utf-8")
        return p

    @staticmethod
    def _answers_from(rep) -> dict:
        return {it["kp_id"]: [b["answer"] for b in it["segments"]
                              if b["t"] == "blank"] for it in rep["items"]}


class TestSelftest(_Base):
    def test_sample_half_segments_and_all_correct(self):
        """6 条 KP → 抽 3 条；segments/answers 数量一致；全对 familiarity=1 并持久化。"""
        rep = review.start_selftest(DOC, db_path=self.db_path, rng=random.Random(42))
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["n_total"], 6)
        self.assertEqual(rep["n_sample"], 3)
        self.assertEqual(len(rep["items"]), 3)
        self.assertEqual(len(rep["kps"]), 3)
        self.assertTrue(rep["session_id"])
        for it in rep["items"]:
            blanks = [b for b in it["segments"] if b["t"] == "blank"]
            self.assertTrue(1 <= len(blanks) <= 4, "每条 KP 应有 1~4 个空")
            self.assertEqual([b["i"] for b in blanks], list(range(len(blanks))))
            answers_by_i = {b["i"]: b["answer"] for b in blanks}
            restored = "".join(s.get("text", "") if s["t"] == "text"
                               else answers_by_i.get(s.get("i"), "")
                               for s in it["segments"])
            for b in blanks:
                self.assertTrue(b["answer"])
                self.assertIn(b["answer"], restored, "挖空答案必须能还原回原文本")
                self.assertTrue(b["hint"])
            self.assertGreater(len(restored), 4)
        # 开始自测不应写盘
        self.assertFalse(review.selftest_out_file(DOC).exists())
        # 归一化容错：空格/标点/大小写差异也能判对
        answers = {k: [(" %s ，" % a) for a in v]
                   for k, v in self._answers_from(rep).items()}
        res = review.submit_selftest(DOC, rep["session_id"], answers)
        self.assertTrue(res["ok"])
        self.assertEqual(res["familiarity"], 1.0)
        self.assertEqual(res["n_sample"], 3)
        self.assertEqual(res["n_unfamiliar"], 0)
        self.assertTrue(all(r["familiar"] for r in res["results"]))
        art = review.selftest_out_file(DOC)
        self.assertTrue(art.exists())
        data = json.loads(art.read_text(encoding="utf-8"))
        self.assertEqual(data["meta"]["version"], review.REVIEW_VERSION)
        self.assertEqual(data["meta"]["familiarity"], 1.0)
        self.assertEqual(data["meta"]["n_sample"], 3)
        st = review.selftest_status(DOC)
        self.assertTrue(st["exists"])
        self.assertEqual(st["familiarity"], 1.0)
        self.assertEqual(st["n_familiar"], 3)
        self.assertEqual(st["n_unfamiliar"], 0)
        self.assertEqual(st["n_sample"], 3, "v2 的 kps 只保留本次抽中的 KP")
        self.assertFalse(st["legacy"])
        data2 = json.loads(art.read_text(encoding="utf-8"))
        self.assertEqual(len(data2["kps"]), data2["meta"]["n_sample"])
        self.assertEqual(sum(1 for v in data2["kps"].values() if not v["familiar"]),
                         data2["meta"]["n_unfamiliar"])
        # 伪造/重复提交同一 session → 错误（提交后会话销毁）
        again = review.submit_selftest(DOC, rep["session_id"], answers)
        self.assertFalse(again["ok"])

    def test_wrong_one_blank_unfamiliar_and_flow(self):
        """任一空错 → 该 KP unfamiliar；familiarity=2/3；flow 标出 unfamiliar。"""
        rep = review.start_selftest(DOC, db_path=self.db_path, rng=random.Random(7))
        answers = self._answers_from(rep)
        # 把第一条 KP 的第一个空答错
        first = rep["items"][0]["kp_id"]
        bad = list(answers[first])
        self.assertTrue(bad)
        bad[0] = "完全错误的答案"
        answers[first] = bad
        res = review.submit_selftest(DOC, rep["session_id"], answers)
        self.assertTrue(res["ok"])
        self.assertEqual(res["n_sample"], 3)
        self.assertEqual(res["n_familiar"], 2)
        self.assertEqual(res["n_unfamiliar"], 1)
        self.assertEqual(res["unfamiliar_ids"], [first])
        by_id = {r["kp_id"]: r for r in res["results"]}
        self.assertFalse(by_id[first]["familiar"])
        self.assertLess(by_id[first]["correct"], by_id[first]["n_blanks"])
        flow = review.build_flow(self.conn, DOC)
        self.assertTrue(flow["ok"])
        self.assertEqual(len(flow["kps"]), 6)
        flags = {k["kp_id"]: k["unfamiliar"] for k in flow["kps"]}
        self.assertTrue(flags[first])
        self.assertEqual(flow["selftest"]["familiarity"], round(2 / 3, 4))
        # 扁平 list 提交也支持（按 items 顺序拼接）
        rep2 = review.start_selftest(DOC, db_path=self.db_path, rng=random.Random(9))
        flat = []
        for it in rep2["items"]:
            flat += [b["answer"] for b in it["segments"] if b["t"] == "blank"]
        res2 = review.submit_selftest(DOC, rep2["session_id"], flat)
        self.assertTrue(res2["ok"])
        self.assertEqual(res2["familiarity"], 1.0)
        # 回归（dash-fixer 现场 bug）：新一次全对后，不得再把上一次的 unfamiliar
        # 混进 status（旧 v1 曾出现 familiarity=1.0 但 n_unfamiliar=1 的自相矛盾）。
        st = review.selftest_status(DOC)
        self.assertEqual(st["familiarity"], 1.0)
        self.assertEqual(st["n_unfamiliar"], 0,
                         "全对的新会话后 status 不能残留历史 unfamiliar")
        self.assertEqual(st["unfamiliar_ids"], [])
        self.assertEqual(len(st["kps"]), 3, "kps 只保留本次结果（不合并历史）")
        data = json.loads(review.selftest_out_file(DOC).read_text(encoding="utf-8"))
        self.assertEqual(data["meta"]["version"], review.REVIEW_VERSION)
        self.assertEqual(data["meta"]["unfamiliar_ids"], [])
        self.assertEqual(len(data["kps"]), data["meta"]["n_sample"])
        flow_after = review.build_flow(self.conn, DOC)
        self.assertFalse(any(k["unfamiliar"] for k in flow_after["kps"]),
                         "全对后 flow 里也不该再有历史 unfamiliar 标记")

    def test_legacy_merged_file_status_is_self_consistent(self):
        """旧版 v1 文件（kps 合并历史、meta 是本次）→ status 按 kps 现算，不再自相矛盾。"""
        out = review.selftest_out_file(DOC)
        out.parent.mkdir(parents=True, exist_ok=True)
        kps = {("KP-%d" % i): {"familiar": True, "correct": 4, "n_blanks": 4,
                               "answers": [], "user_answers": []}
               for i in range(1, 22)}
        kps["KP-99"] = {"familiar": False, "correct": 0, "n_blanks": 4,
                        "answers": [], "user_answers": []}
        out.write_text(json.dumps({
            "meta": {"version": "C-0127-review-v1", "doc_id": DOC,
                     "n_total": 22, "n_sample": 11, "n_familiar": 11,
                     "n_unfamiliar": 0, "familiarity": 1.0,
                     "generated_at": "2026-10-23T00:00:00"},
            "kps": kps,
        }, ensure_ascii=False), encoding="utf-8")
        st = review.selftest_status(DOC)
        self.assertTrue(st["legacy"])
        self.assertEqual(st["n_sample"], 22)
        self.assertEqual(st["n_unfamiliar"], 1)
        self.assertEqual(st["unfamiliar_ids"], ["KP-99"])
        self.assertAlmostEqual(st["familiarity"], round(21 / 22, 4))
        self.assertEqual(st["n_familiar"], 21)
        # flow 与 status 同口径（不再出现"100% 但不熟 1"）
        flow = review.build_flow(self.conn, DOC)
        self.assertEqual(flow["selftest"]["n_unfamiliar"], 1)
        self.assertEqual(flow["selftest"]["unfamiliar_ids"], ["KP-99"])

    def test_session_forged_expired_and_no_artifact(self):
        """伪造/过期会话、无 KP 产物、非法 doc_id → 明确错误（不抛异常）。"""
        bad = review.submit_selftest(DOC, "deadbeefdeadbeef", {})
        self.assertFalse(bad["ok"])
        self.assertIn("过期", bad["error"])
        rep = review.start_selftest(DOC, db_path=self.db_path, rng=random.Random(1))
        review._SELFTEST[rep["session_id"]]["created"] -= review.SESSION_TTL + 10
        exp = review.submit_selftest(DOC, rep["session_id"], {})
        self.assertFalse(exp["ok"])
        self.assertIn("过期", exp["error"])
        self.assertFalse(review.start_selftest(0)["ok"])
        self.kp_path.unlink()
        self.assertFalse(review.start_selftest(DOC)["ok"])
        self.assertFalse(review.build_flow(self.conn, DOC)["ok"])


class TestFlowData(_Base):
    def test_notes_grouped_by_kp_and_unbound(self):
        """2 篇绑 KP + 1 篇无来源 → groups 正确、unbound 正确、笔记正文未改。"""
        n1 = _write_note(self.notesdir / "a.md", "阿托品笔记", "KP-1", DOC,
                         body="阿托品笔记正文")
        n2 = _write_note(self.notesdir / "b.md", "东莨菪碱笔记", "KP-2", DOC,
                         body="东莨菪碱正文")
        n3 = _write_note(self.notesdir / "c.md", "随手记", "", DOC, body="课件级正文")
        h1, h2, h3 = _sha(n1), _sha(n2), _sha(n3)
        flow = review.build_flow(self.conn, DOC)
        self.assertTrue(flow["ok"])
        self.assertEqual(flow["doc"]["id"], DOC)
        self.assertEqual(flow["doc"]["title"], "M胆碱和N胆碱受体阻断药 · 课件")
        groups = {g["kp_id"]: g for g in flow["notes"]["groups"]}
        self.assertEqual(len(groups), 6)
        self.assertEqual([g["kp_id"] for g in flow["notes"]["groups"]],
                         [k["id"] for k in KPS], "分组顺序应跟 KP 产物顺序")
        self.assertEqual(len(groups["KP-1"]["notes"]), 1)
        self.assertEqual(groups["KP-1"]["notes"][0]["title"], "阿托品笔记")
        self.assertEqual(len(groups["KP-2"]["notes"]), 1)
        self.assertEqual(groups["KP-3"]["notes"], [])
        self.assertEqual(len(flow["notes"]["unbound"]), 1)
        self.assertEqual(flow["notes"]["unbound"][0]["title"], "随手记")
        self.assertEqual(flow["notes"]["n_notes"], 3)
        self.assertEqual([_sha(n1), _sha(n2), _sha(n3)], [h1, h2, h3],
                         "读流程不得改笔记正文")
        kp1 = next(k for k in flow["kps"] if k["kp_id"] == "KP-1")
        self.assertEqual(kp1["note_count"], 1)
        self.assertIn("stages", flow)
        self.assertEqual(len(flow["stages"]), 3)

    def test_terms_preview_available_and_empty_fallback(self):
        """预习名词产物在 → terms 返回 11 条；没有 → source=none 且不崩。"""
        self.assertFalse(review.build_flow(self.conn, DOC)["terms"]["items"])
        flow0 = review.build_flow(self.conn, DOC)
        self.assertEqual(flow0["terms"]["source"], "none")
        self.assertFalse(flow0["terms"]["items"])
        self.assertEqual(flow0["terms"]["chapter"]["no"], 6)
        self._write_preview_terms(6)
        flow = review.build_flow(self.conn, DOC)
        t = flow["terms"]
        self.assertEqual(t["source"], "preview_chapter")
        self.assertEqual(t["n"], 2)
        self.assertEqual(t["items"][0]["term"], "胆碱受体激动药")
        self.assertEqual(t["items"][0]["en"], "cholinoceptor agonist")
        self.assertEqual(t["chapter"]["no"], 6)
        self.assertIn("第6章", t["chapter"]["name"])
        # 章节映射缺失时 terms 不崩、chapter 为空
        (self.logdir / "knowledge" / "46-教材补充.json").unlink()
        flow2 = review.build_flow(self.conn, DOC)
        self.assertEqual(flow2["terms"]["source"], "none")

    def test_list_courseware_reviews(self):
        """只列有 KP 产物的课件；带 selftest 摘要/terms 可用性/章节。"""
        conn = self.conn
        conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,n_chunks,"
                     "role,corpus,status) VALUES(?,?,?,?,?,?,?,?,?)",
                     (47, "raw/y.pptx", "/y.pptx", "pptx", "没有整理过的课件",
                      5, "courseware", "main", "ok"))
        conn.commit()
        self._write_preview_terms(6)
        items = review.list_courseware_reviews(conn)
        ids = [x["doc_id"] for x in items]
        self.assertEqual(ids, [DOC], "没有 KP 产物的课件应跳过")
        it = items[0]
        self.assertEqual(it["n_kps"], 6)
        self.assertFalse(it["selftest"]["exists"])
        self.assertTrue(it["terms"]["available"])
        self.assertEqual(it["terms"]["n"], 2)
        self.assertEqual(it["chapter"]["no"], 6)
        rep = review.start_selftest(DOC, db_path=self.db_path, rng=random.Random(3))
        review.submit_selftest(DOC, rep["session_id"], self._answers_from(rep))
        it2 = review.list_courseware_reviews(conn)[0]
        self.assertTrue(it2["selftest"]["exists"])
        self.assertEqual(it2["selftest"]["familiarity"], 1.0)
        self.assertEqual(it2["selftest"]["n_sample"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
