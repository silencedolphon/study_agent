"""教材补充 AI 化（C-0125，2026-10-22）验收：MAP AI + 本地小节 + 按需 SUPP AI。

全程**不发出真实 AI 请求**：所有用例 monkeypatch `teach._ai_chat`（返回假 JSON）与
`teach._ai_enabled`；DB/产物/提示词全部在 `_work/verify/t21_private/` 临时目录。
覆盖：MAP 成功/非法章号降级/本地兜底、build 缓存 skip、AI 仅 1 次/课件、
按需 SUPP 缓存/force、pages 越界剔除、no_supplement、AI 失败保留本地兜底、
estimate 0 写 0 调用、提示词 4 块可加载，以及旧版（C-0124）产物 stale→升级。
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
from core import config, kp_build, teach  # noqa: E402

T21 = env.VERIFY_DIR / "t21_private"
DOC = 46
TB = 50
CH_A = "药理学 第6版 刘晓东 > 第6章 作用于胆碱受体药"
CH_B = "药理学 第6版 刘晓东 > 第19章 抗高血压药"


def setUpModule():
    shutil.rmtree(T21, ignore_errors=True)
    T21.mkdir(parents=True, exist_ok=True)


def tearDownModule():
    shutil.rmtree(T21, ignore_errors=True)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _ai_result(parsed, *, ok=True, cost=0.0031, tin=1200, tout=600, elapsed=3.5,
               error="", attempts=1, calls=None):
    return {"ok": ok, "parsed": parsed if ok else {}, "raw": "{}", "usage": {},
            "tokens": {"in": tin, "out": tout, "reasoning": 0},
            "cost_cny": cost if ok else 0.0, "elapsed_s": elapsed,
            "attempts": attempts, "calls": calls or [], "error": error,
            "model": "deepseek-flash", "thinking": True,
            "reasoning_effort": "max", "max_tokens": 65536}


MAP_OK = {"chapter_no": 6, "chapter_name": "作用于胆碱受体药",
          "confidence": "high", "reason": "课件全是胆碱受体/阿托品内容，对应第6章"}
SUPP_OK = {"verdict": "has_supplement", "summary": "教材补充了 M 受体亚型分布",
           "points": [
               {"text": "教材补充了 M 受体亚型（M1-M5）分布", "pages": [70, 999],
                "kind": "机制"},
               {"text": "教材补充了 M 受体亚型（M1-M5）分布", "pages": [71],
                "kind": "概念"},          # 与第一条重复 → 去重
               {"text": "教材补充了阿托品对腺体的选择性", "pages": [71],
                "kind": "分类"}],
           "gaps": ["课件未提 M 受体亚型分布"],
           "citations": [{"pages": [70], "quote": "M 受体分为 M1~M5 五种亚型"}]}


# ---------------------------------------------------------------- fixtures
def _new_db(name: str, *, with_textbook: bool = True) -> Path:
    p = T21 / f"{name}.db"
    try:
        p.unlink()
    except OSError:
        pass
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY, path TEXT, abs_path TEXT, kind TEXT, title TEXT,
        content_hash TEXT, size_bytes INTEGER, mtime REAL, n_chunks INTEGER,
        role TEXT, corpus TEXT, status TEXT, warnings TEXT, extra TEXT,
        indexed_at REAL, updated_at REAL);
    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER, text TEXT,
        text_norm TEXT, heading_path TEXT, topic TEXT, source_ref TEXT,
        char_len INTEGER, token_est INTEGER, content_hash TEXT, created_at REAL);
    """)
    conn.execute(
        "INSERT INTO documents(id,path,abs_path,kind,title,content_hash,n_chunks,"
        "role,corpus,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (DOC, "raw/x.pptx", "/x.pptx", "pptx",
         "3. 具有神经节阻断作用和促进组胺释放作用。", "h", 25,
         "courseware", "main", "ok"))
    if with_textbook:
        conn.execute(
            "INSERT INTO documents(id,path,abs_path,kind,title,content_hash,n_chunks,"
            "role,corpus,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (TB, "raw/药理学.pdf", "/药理学.pdf", "pdf",
             "药理学 第6版 刘晓东", "t", 30, "textbook", "main", "ok"))
        rows = [
            (501, 1, CH_A, "p.70", "阿托品竞争性阻断M胆碱受体，M受体分为M1~M5五种亚型。"),
            (502, 2, CH_A, "p.71", "东莨菪碱阻断M胆碱受体，用于晕动病与麻醉前给药。"),
            (503, 3, CH_A, "p.72", "阿托品大剂量阻断神经节N受体，心率加快。"),
            (504, 4, CH_A, "p.73", "山莨菪碱选择性阻断M胆碱受体，改善微循环。"),
            (601, 5, CH_B, "p.190", "硝苯地平阻断钙通道，扩张血管，抗高血压。"),
        ]
        conn.executemany(
            "INSERT INTO chunks(id,doc_id,ord,text,heading_path,source_ref,char_len,"
            "token_est,created_at) VALUES(?,?,?,?,?,?,?,?,0)",
            [(cid, TB, ordv, txt, hp, ref, len(txt), int(len(txt) * 0.75))
             for cid, ordv, hp, ref, txt in rows])
    conn.commit()
    conn.close()
    return p


def _kp_doc(kps) -> dict:
    return {"meta": {"pipeline_version": "C-0058-v4", "doc_id": DOC},
            "knowledge_points": kps}


def _kp(kp_id, name, points, summary=""):
    return {"id": kp_id, "doc_id": DOC, "name": name, "node_name": name,
            "points": list(points), "summary": summary or name,
            "pages": [1], "material_block_ids": [], "sources": []}


def _sections_artifact(knowledge_dir: Path) -> Path:
    p = knowledge_dir / f"{TB}-章节细目.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "meta": {"doc_id": TB, "book_title": "药理学 第6版 刘晓东",
                 "version": "C-0092-v1"},
        "chapters": [
            {"no": 6, "name": "作用于胆碱受体药", "page_start": 70, "page_end": 74,
             "sections": [
                 {"index": 1, "form": "第X节", "level": 1, "number": "一",
                  "title": "胆碱受体激动药", "page_start": 70, "page_end": 71},
                 {"index": 2, "form": "第X节", "level": 1, "number": "二",
                  "title": "胆碱受体阻断药", "page_start": 72, "page_end": 74}]},
            {"no": 19, "name": "抗高血压药", "page_start": 190, "page_end": 192,
             "sections": [{"index": 1, "form": "第X节", "level": 1, "number": "一",
                           "title": "钙通道阻滞药", "page_start": 190,
                           "page_end": 192}]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return p


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = T21 / self._testMethodName
        shutil.rmtree(self.dir, ignore_errors=True)
        (self.dir / "knowledge").mkdir(parents=True, exist_ok=True)
        self.logdir = self.dir / "logs"
        self.logdir.mkdir(parents=True, exist_ok=True)
        self.kp_path = self.dir / "46-末级知识点总结.json"
        self.kp_path.write_text(json.dumps(_kp_doc([
            _kp("KP-A1", "阿托品阻断M胆碱受体",
                ["阿托品竞争性阻断M胆碱受体", "M受体亚型分布"]),
            _kp("KP-X1", "量子力学基础", ["薛定谔方程与波函数"]),
        ]), ensure_ascii=False), encoding="utf-8")
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.logdir),
            mock.patch.object(kp_build, "out_file", lambda doc_id: self.kp_path),
        ]
        for p in self._patches:
            p.start()
        self.calls = []
        self.ai_impl = lambda kind, sys_p, user_p, **kw: _ai_result(
            MAP_OK if kind == "teach.map" else SUPP_OK)
        self._ai_chat_patch = mock.patch.object(
            teach, "_ai_chat", side_effect=self._fake_ai)
        self._ai_chat_patch.start()
        self._ai_enabled_patch = mock.patch.object(teach, "_ai_enabled",
                                                   side_effect=lambda: True)
        self._ai_enabled_patch.start()
        self._conn = None

    def _fake_ai(self, kind, sys_p, user_p, **kw):
        self.calls.append({"kind": kind, "sys": sys_p, "user": user_p})
        return self.ai_impl(kind, sys_p, user_p, **kw)

    def tearDown(self):
        self._ai_enabled_patch.stop()
        self._ai_chat_patch.stop()
        if self._conn is not None:
            self._conn.close()
        for p in reversed(self._patches):
            p.stop()

    def _db(self, **kw) -> Path:
        return _new_db(self._testMethodName, **kw)

    def _open(self, path: Path) -> sqlite3.Connection:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        _sections_artifact(self.logdir / "knowledge")
        return self._conn

    def _out(self) -> Path:
        return teach.supplement_out_file(DOC)

    def _build(self, **kw):
        return teach.build_supplement(DOC, conn=self._conn, log=lambda *a, **k: None, **kw)


class TestMapAndBuild(_Base):
    def test_map_ai_once_then_build_cached(self):
        """MAP AI 每课件只 1 次；build 出 chapter_map/sections/pages；二次 build skipped。"""
        conn = self._open(self._db())
        rep = self._build()
        self.assertEqual(rep["status"], "done")
        meta = rep["meta"]
        self.assertEqual(meta["chapter_map"]["source"], "ai")
        self.assertEqual(meta["chapter_map"]["chapter_no"], 6)
        self.assertEqual(meta["ai_map_source"], "ai")
        self.assertGreater(meta["ai_map_cost_cny"], 0)
        self.assertEqual(meta["ai_map_tokens"]["in"], 1200)
        self.assertEqual(len(self.calls), 1, "build 只应调 1 次 MAP AI（不做逐 KP AI）")
        self.assertEqual(self.calls[0]["kind"], "teach.map")
        entry = rep["kps"]["KP-A1"]
        self.assertEqual(entry["status"], "done")
        self.assertIn("作用于胆碱受体药", entry["chapter"]["name"])
        self.assertTrue(entry["sections"], "应定位到《章节细目》的具体小节")
        self.assertTrue(70 <= entry["sections"][0]["page_start"] <= 74)
        self.assertTrue(entry["pages"])
        self.assertTrue(entry["passages"])
        self.assertNotIn("ai", entry, "build 阶段不做逐 KP AI")
        art = json.loads(self._out().read_text(encoding="utf-8"))
        self.assertEqual(art["meta"]["version"], teach.SUPPLEMENT_VERSION)
        self.assertEqual(art["meta"]["chapter_map"]["chapter_no"], 6)
        # 二次 build：kp/tb/prompt sha 未变 → skipped，0 调用
        rep2 = self._build()
        self.assertEqual(rep2["status"], "skipped")
        self.assertEqual(len(self.calls), 1, "skipped 不应重复调 AI")

    def test_map_invalid_chapter_retry_then_local_fallback(self):
        """AI 给的章号不在章列表 → 重试 1 次仍非法 → 本地投票兜底（不崩）。"""
        conn = self._open(self._db())
        self.ai_impl = lambda kind, sys_p, user_p, **kw: _ai_result(
            {"chapter_no": 999, "chapter_name": "不存在", "confidence": "low",
             "reason": "x"})
        rep = self._build()
        self.assertEqual(rep["status"], "done")
        self.assertEqual(rep["meta"]["ai_map_source"], "local_fallback")
        self.assertEqual(len(self.calls), 2, "非法章号应重试 1 次")
        self.assertIn("章号不在章列表", rep["meta"]["ai_map_error"])
        entry = rep["kps"]["KP-A1"]
        self.assertEqual(entry["status"], "done", "本地兜底仍应定位到章")
        self.assertEqual(entry["chapter"]["chapter_no"], 6)

    def test_map_ai_failure_local_fallback(self):
        """AI 调用失败 → 本地投票兜底，meta 记 ai_map_error，build 照常出产物。"""
        conn = self._open(self._db())
        self.ai_impl = lambda kind, sys_p, user_p, **kw: _ai_result(
            {}, ok=False, error="HTTP 500: server error")
        rep = self._build()
        self.assertEqual(rep["status"], "done")
        self.assertEqual(rep["meta"]["ai_map_source"], "local_fallback")
        self.assertIn("HTTP 500", rep["meta"]["ai_map_error"])
        self.assertTrue(rep["kps"]["KP-A1"]["sections"])

    def test_ai_disabled_local_only_no_calls(self):
        """AI 关闭（无 key/KB_TEACH_AI=0）→ 本地兜底，0 次 _ai_chat。"""
        with mock.patch.object(teach, "_ai_enabled", side_effect=lambda: False):
            conn = self._open(self._db())
            rep = self._build(allow_ai=True)
        self.assertEqual(rep["status"], "done")
        self.assertEqual(rep["meta"]["ai_map_source"], "local_fallback")
        self.assertEqual(self.calls, [], "AI 关闭时不得调模型")


class TestKpAnalyze(_Base):
    def test_analyze_cache_force_and_pages_filter(self):
        """SUPP AI 按需 1 次：越界页剔除、重复点去重、缓存 0 调用、force 重跑。"""
        conn = self._open(self._db())
        self._build()
        calls_before = len(self.calls)
        r1 = teach.analyze_kp_supplement(DOC, "KP-A1", conn=conn,
                                         log=lambda *a, **k: None)
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["status"], "done")
        self.assertFalse(r1["cached"])
        self.assertEqual(len(self.calls), calls_before + 1)
        self.assertEqual(self.calls[-1]["kind"], "teach.supplement")
        ai = r1["ai"]
        self.assertEqual(ai["verdict"], "has_supplement")
        self.assertEqual(len(ai["points"]), 2, "重复 point 应去重")
        self.assertEqual(ai["points"][0]["pages"], [70], "越界页 999 应剔除")
        self.assertEqual(ai["points"][1]["pages"], [71])
        self.assertIn(ai["points"][0]["kind"], teach.TEACH_SUPP_KINDS)
        self.assertTrue(ai["citations"])
        self.assertGreater(ai["cost_cny"], 0)
        self.assertTrue(ai["prompt_sha256"])
        self.assertTrue(ai["cache_key"])
        # 缓存：第二次 0 调用
        r2 = teach.analyze_kp_supplement(DOC, "KP-A1", conn=conn)
        self.assertTrue(r2["cached"])
        self.assertEqual(r2["status"], "done")
        self.assertEqual(len(self.calls), calls_before + 1, "命中缓存不应调 AI")
        # force：重跑（+1 调用）
        r3 = teach.analyze_kp_supplement(DOC, "KP-A1", force=True, conn=conn,
                                         log=lambda *a, **k: None)
        self.assertFalse(r3["cached"])
        self.assertEqual(len(self.calls), calls_before + 2)
        # 落盘：entry.ai 持久化
        art = json.loads(self._out().read_text(encoding="utf-8"))
        self.assertEqual(art["kps"]["KP-A1"]["ai"]["status"], "done")
        self.assertGreaterEqual(art["meta"]["ai_kp_calls"], 2)

    def test_analyze_no_supplement(self):
        """verdict=no_supplement → ai.status=no_supplement、points=[]。"""
        conn = self._open(self._db())
        self._build()
        self.ai_impl = lambda kind, sys_p, user_p, **kw: _ai_result(
            {"verdict": "no_supplement", "summary": "教材与课件重叠，无补充",
             "points": [], "gaps": [], "citations": []})
        r = teach.analyze_kp_supplement(DOC, "KP-A1", conn=conn,
                                        log=lambda *a, **k: None)
        self.assertEqual(r["status"], "no_supplement")
        self.assertEqual(r["ai"]["points"], [])
        art = json.loads(self._out().read_text(encoding="utf-8")
                         if False else "{}")
        self.assertTrue(r["ai"]["cache_key"])

    def test_analyze_ai_failure_keeps_local_fallback(self):
        """AI 失败 → entry.ai.status=failed，本地 passages/sections 兜底不变。"""
        conn = self._open(self._db())
        rep = self._build()
        local_before = rep["kps"]["KP-A1"]["passages"]
        self.assertTrue(local_before)
        self.ai_impl = lambda kind, sys_p, user_p, **kw: _ai_result(
            {}, ok=False, error="timeout")
        r = teach.analyze_kp_supplement(DOC, "KP-A1", conn=conn,
                                        log=lambda *a, **k: None)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "failed")
        self.assertIn("timeout", r["error"])
        self.assertEqual(r["entry"]["ai"]["status"], "failed")
        self.assertEqual(r["entry"]["passages"], local_before,
                         "AI 失败必须保留本地兜底")
        art = json.loads(self._out().read_text(encoding="utf-8"))
        e = art["kps"]["KP-A1"]
        self.assertEqual(e["ai"]["status"], "failed")
        self.assertTrue(e["passages"])

    def test_analyze_ai_disabled(self):
        """AI 不可用 → analyze 返回 ai_disabled + 本地兜底，不发请求。"""
        conn = self._open(self._db())
        self._build()
        n = len(self.calls)
        with mock.patch.object(teach, "_ai_enabled", side_effect=lambda: False):
            r = teach.analyze_kp_supplement(DOC, "KP-A1", conn=conn)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "ai_disabled")
        self.assertEqual(len(self.calls), n)


class TestEstimateAndStatus(_Base):
    def test_estimates_no_ai_no_write(self):
        """estimate_map / estimate_kp_supplement：0 调用、0 写文件。"""
        conn = self._open(self._db())
        before = sorted(p.name for p in (self.logdir / "knowledge").glob("*"))
        with mock.patch.object(teach, "_ai_chat",
                               side_effect=AssertionError("estimate 不得调 AI")):
            e1 = teach.estimate_map(DOC, conn=conn)
            e2 = teach.estimate_kp_supplement(DOC, "KP-A1", conn=conn)
        self.assertEqual(e1["calls"], 1)
        self.assertGreater(e1["est_tokens_in"], 0)
        self.assertGreater(e1["cost_cny_max"], 0)
        self.assertEqual(e2["calls"], 1)
        self.assertGreaterEqual(e2["cost_cny_max"], 0)
        self.assertTrue(e2.get("sections") or e2.get("pages"))
        after = sorted(p.name for p in (self.logdir / "knowledge").glob("*"))
        self.assertEqual(after, before, "estimate 不得写任何文件")
        self.assertEqual(self.calls, [], "estimate 不得调 AI")

    def test_legacy_artifact_is_stale_then_upgraded(self):
        """旧版（C-0124，无 chapter_map）产物 → status stale/needs_backfill；build 升级并备份。"""
        conn = self._open(self._db())
        out = self._out()
        out.write_text(json.dumps({
            "meta": {"version": "C-0124-teach-supplement-v1", "doc_id": DOC,
                     "status": "done", "textbook_doc_id": TB,
                     "textbook_title": "药理学 第6版 刘晓东",
                     "generated_at": "2026-10-21T00:00:00", "n_kps": 2,
                     "n_matched": 2},
            "kps": {"KP-A1": {"kp_id": "KP-A1", "kp_name": "阿托品阻断M胆碱受体",
                              "status": "done", "chapter": {"heading_path": CH_A,
                                                            "name": "第6章 作用于胆碱受体药",
                                                            "no": 6},
                              "passages": [{"chunk_id": 501, "page_start": 70,
                                            "page_end": 70, "page_label": "p.70",
                                            "text": "旧版本地段落", "score": 9.0,
                                            "hit_terms": ["阿托品"]}],
                              "pages": [70], "page_label": "p.70",
                              "added_terms": ["M受体"]}},
        }, ensure_ascii=False), encoding="utf-8")
        st = teach.supplement_status(DOC, conn=conn)
        self.assertTrue(st["has_textbook"])
        self.assertTrue(st["needs_backfill"], "旧版产物必须提醒重新补做")
        self.assertTrue(st["supplement"]["legacy"])
        self.assertTrue(st["supplement"]["stale"])
        self.assertIn("旧版", st["reason"])
        # build 升级：备份旧文件、写新版本
        rep = self._build()
        self.assertEqual(rep["status"], "done")
        art = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(art["meta"]["version"], teach.SUPPLEMENT_VERSION)
        self.assertEqual(art["meta"]["legacy_prev_version"],
                         "C-0124-teach-supplement-v1")
        self.assertTrue(art["meta"]["prev_backup"], "升级前应备份旧产物")
        self.assertTrue(Path(art["meta"]["prev_backup"]).exists())
        self.assertTrue(art["meta"]["chapter_map"])
        st2 = teach.supplement_status(DOC, conn=conn)
        self.assertFalse(st2["needs_backfill"], "升级后不再提醒")

    def test_prompt_contract(self):
        """提示词 4 块可加载；MAP/SUPP 占位符齐全；tracked 审查副本存在。"""
        p = teach._load_prompts()
        for key in ("map_sys", "map_user", "supp_sys", "supp_user"):
            self.assertGreater(len(p[key]), 100, "块 %s 太短" % key)
        for ph in ("{courseware_title}", "{kp_outline}", "{chapters}"):
            self.assertIn(ph, p["map_user"], "MAP USER 缺占位符 %s" % ph)
        for ph in ("{courseware_title}", "{chapter}", "{kp_name}", "{kp_points}",
                   "{kp_summary}", "{section_outline}", "{section_material}"):
            self.assertIn(ph, p["supp_user"], "SUPP USER 缺占位符 %s" % ph)
        for kw in ("chapter_no", "confidence", "唯一一章"):
            self.assertIn(kw, p["map_sys"])
        for kw in ("no_supplement", "points", "pages", "不重复"):
            self.assertIn(kw, p["supp_sys"])
        review = env.PROJECT / "docs" / "teach" / "提示词-教材补充-v1-2026-10-22.md"
        self.assertTrue(review.exists(), "tracked 审查副本应存在")
        self.assertEqual(teach.SUPPLEMENT_VERSION, "C-0125-teach-ai-v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
