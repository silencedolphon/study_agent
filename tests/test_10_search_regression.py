# -*- coding: utf-8 -*-
"""M0-7 · 检索 10 题回归集（真实药理课件主库的**只读副本**）。

背景
----
三类学生评测（`docs/三类学生评测报告-2026-10-18.md` §4 P0-B）实证了 3 个
"会让学生背错药"的检索问题：

1. 问「镇静催眠药按化学结构分哪几类」→ 检索/问答答"资料里没有"，而 p.5
   《镇静催眠药分类》明明存在（chunk 1382）；
2. 搜「苯二氮卓类 作用机制」→ Top-1 是 p.29 巴比妥类机制页，目标 p.16
   （chunk 1393）连前 8 都没有；
3. 对比题漏掉 p.18「增加 Cl⁻ 通道开放的频率」（chunk 1395）。

本用例把 10 道高频药学题固化下来（query + 期望页 + 判定方式），
直接调 `core.search.search()`（与 `/api/search`、`chat.gather_evidence`
同一条代码路径），防止 `core/search.py` 再退化。

数据隔离
--------
真实主库 `index/study.db` **只读**：用 sqlite3 backup API 复制到
`_work/m0/search/test.db`（只读连接打开），断言全部在副本上做；
真实库在用例运行前后 sha256 不变（run_all.py 的隔离核对会复核）。
磁盘上没有副本时，`setUpModule` 会从真实库重新 backup 一份。

判定方式
--------
- `top1`：期望 chunk 必须是第 1 名；
- `top3` / `topk`：期望 chunk 落在前 3 / 前 len(hits) 名；
- `cover`（对比题）：`cover` 里每一组期望 chunk 至少各命中 1 条；
- `must_not_top1_chunk_ids`：明确的"错误答案页"不得当 Top-1（如 p.29 巴比妥
  机制页不能顶掉 p.16 BDZ 机制页）。

跑法
----
    venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_10
    venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_search   # 整组检索用例
"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(PROJECT))

import env  # noqa: E402  （复用"真实库只读快照"的既有实现 env.copy_sqlite）

WORK_DIR = PROJECT / "_work" / "m0" / "search"
TEST_DB = WORK_DIR / "test.db"
REAL_DB = PROJECT / "index" / "study.db"
QUESTIONS_JSON = WORK_DIR / "questions.json"
TOP_K = 8

# ---------------------------------------------------------------------------
# 10 题回归集：query / 意图 / 期望（chunk id 以 _work/m0/search/test.db 副本为准，
# 副本由 index/study.db backup 而来；doc_id/page 是给人读的出处）。
# ---------------------------------------------------------------------------
QUESTIONS = [
    {
        "id": "Q01",
        "query": "镇静催眠药按化学结构分哪几类",
        "intent": "classification",
        "expect": {
            "judge": "top1",
            "chunk_ids": [1382],
            "doc_id": 41,
            "page": "p.5",
            "heading_contains": "镇静催眠药分类",
            "note": "p.5《镇静催眠药分类》（苯二氮卓类/巴比妥类/其他），评测 P0-B 实证此页存在却答不出来",
        },
    },
    {
        "id": "Q02",
        "query": "苯二氮卓类的作用机制",
        "intent": "mechanism",
        "expect": {
            "judge": "top1",
            "chunk_ids": [1393],
            "doc_id": 41,
            "page": "p.16",
            "heading_contains": "BDZ增强GABA抑制",
            "must_not_top1_chunk_ids": [1406],
            "note": "p.16 BDZ 增强 GABA 抑制（增加 Cl⁻ 通道开放频率）；Top-1 不得是 p.29 巴比妥机制",
        },
    },
    {
        "id": "Q03",
        "query": "苯二氮卓类和巴比妥类的作用机制有什么区别",
        "intent": "compare",
        "expect": {
            "judge": "cover",
            "cover": [
                {"label": "BDZ p.16 机制", "chunk_ids": [1393]},
                {"label": "BDZ p.18 增加 Cl- 通道开放频率", "chunk_ids": [1395]},
                {"label": "巴比妥 p.29 机制", "chunk_ids": [1406]},
            ],
            "note": "对比题：两侧各至少 1 条，且覆盖 p.16 机制与 p.18 增加 Cl⁻ 通道开放频率",
        },
    },
    {
        "id": "Q04",
        "query": "癫痫持续状态首选什么药",
        "intent": "first-line",
        "expect": {
            "judge": "top3",
            "chunk_ids": [1388, 1928, 1938],
            "doc_ids": [41, 44],
            "note": "正确答案=地西泮 i.v.；p.11 / p.35 / p.45 任一条进前 3",
        },
    },
    {
        "id": "Q05",
        "query": "抗精神病药的作用机制",
        "intent": "mechanism",
        "expect": {
            "judge": "top3",
            "chunk_ids": [2130],
            "doc_id": 47,
            "page": "p.17",
            "heading_contains": "典型抗精神病药作用机制",
            "note": "p.17 典型抗精神病药作用机制（阻滞 D2 受体）",
        },
    },
    {
        "id": "Q06",
        "query": "胆碱受体激动剂分为哪几类",
        "intent": "classification",
        "expect": {
            "judge": "topk",
            "chunk_ids": [1679, 1727],
            "doc_ids": [45, 43],
            "note": "doc45 p.12 两种乙酰胆碱受体（M/N）/ doc43 p.59 受体激动药分类列举",
        },
    },
    {
        "id": "Q07",
        "query": "新斯的明的作用和临床应用",
        "intent": "drug-effect",
        "expect": {
            "judge": "topk",
            "chunk_ids": [2080, 2081],
            "doc_id": 48,
            "note": "doc48 p.9 新斯的明【药理作用】/ p.10【临床应用】",
        },
    },
    {
        "id": "Q08",
        "query": "地西泮的不良反应",
        "intent": "adverse-reaction",
        "expect": {
            "judge": "top1",
            "chunk_ids": [1397],
            "doc_id": 41,
            "page": "p.20",
            "heading_contains": "不良反应与依赖",
            "must_not_top1_chunk_ids": [1409],
            "note": "p.20 地西泮【不良反应】；Top-1 不得是 p.32 巴比妥类不良反应/呼吸抑制",
        },
    },
    {
        "id": "Q09",
        "query": "巴比妥类过量为什么会导致呼吸抑制",
        "intent": "mechanism",
        "expect": {
            "judge": "topk",
            "chunk_ids": [1409, 1410],
            "doc_id": 41,
            "note": "p.32 大剂量明显抑制呼吸中枢 / p.33 中毒表现（深度昏迷、高度呼吸抑制）",
        },
    },
    {
        "id": "Q10",
        "query": "M胆碱受体阻断药有哪些",
        "intent": "classification",
        "expect": {
            "judge": "topk",
            "chunk_ids": [1841, 1843],
            "doc_id": 46,
            "note": "doc46 p.1 胆碱受体阻断药（M 受体阻断药=阿托品等）/ p.3 阿托品",
        },
    },
]

_CONN: "sqlite3.Connection | None" = None


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------
def _ensure_test_db() -> None:
    """_work/m0/search/test.db 不存在时，从真实主库 backup 一份（只读源）。"""
    if TEST_DB.exists():
        return
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    if not REAL_DB.exists():
        raise FileNotFoundError(
            f"缺少真实主库（应只读）：{REAL_DB}；无法构建检索回归副本")
    env.copy_sqlite(REAL_DB, TEST_DB)      # 内部用 file:...?mode=ro 打开源库


def _search_mod():
    """延迟 import：只有本模块用例真正执行时才加载 numpy/onnxruntime。"""
    from core import search as search_mod
    return search_mod


def setUpModule() -> None:  # noqa: N802 （unittest 钩子）
    global _CONN
    _ensure_test_db()
    _CONN = sqlite3.connect(f"file:{TEST_DB.as_posix()}?mode=ro", uri=True)
    _CONN.row_factory = sqlite3.Row
    _search_mod().search(_CONN, "地西泮", top_k=1)      # 预热嵌入模型


def tearDownModule() -> None:  # noqa: N802
    global _CONN
    if _CONN is not None:
        _CONN.close()
        _CONN = None


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def _rank_of(hits, chunk_ids) -> int:
    ids = {int(x) for x in chunk_ids}
    for i, h in enumerate(hits, 1):
        if h.chunk_id in ids:
            return i
    return 0


def _fallback_rank(hits, expect: dict) -> int:
    """精确 chunk 没命中时按 doc + 页/标题兜底判（防止将来重入库 chunk id 漂移）。"""
    doc_ids = {int(x) for x in (expect.get("doc_ids") or [])}
    if expect.get("doc_id"):
        doc_ids.add(int(expect["doc_id"]))
    page = (expect.get("page") or "").strip()
    heading = (expect.get("heading_contains") or "").strip()
    for i, h in enumerate(hits, 1):
        if doc_ids and h.doc_id not in doc_ids:
            continue
        if page and page not in (h.source_ref or ""):
            continue
        if heading and heading not in (h.heading_path or ""):
            continue
        if doc_ids or page or heading:
            return i
    return 0


def judge(question: dict, hits) -> dict:
    """按 expect 判定；返回 {pass, judge, rank, bad_top1, cover}。"""
    expect = question.get("expect") or {}
    kind = expect.get("judge") or "topk"
    rank = _rank_of(hits, expect.get("chunk_ids") or [])
    if rank == 0:
        rank = _fallback_rank(hits, expect)
    top1 = hits[0].chunk_id if hits else 0
    bad_top1 = top1 in {int(x) for x in (expect.get("must_not_top1_chunk_ids") or [])}
    if kind == "cover":
        cover = []
        for item in expect.get("cover") or []:
            r = _rank_of(hits, item.get("chunk_ids") or [])
            cover.append({"label": item.get("label") or "", "rank": r})
        ok = all(c["rank"] > 0 for c in cover) and not bad_top1
        return {"pass": ok, "judge": kind, "rank": min(
            [c["rank"] for c in cover] or [0]), "bad_top1": bad_top1, "cover": cover}
    if kind == "top1":
        ok = rank == 1 and not bad_top1
    elif kind == "top3":
        ok = 1 <= rank <= 3 and not bad_top1
    else:
        ok = 1 <= rank <= len(hits) and not bad_top1
    return {"pass": ok, "judge": kind, "rank": rank, "bad_top1": bad_top1}


def _format(q: dict, hits, verdict: dict) -> str:
    """失败时打出 Top-N 的 chunk/页/标题/加权理由，便于定位。"""
    lines = [f"{q['id']} 查询：{q['query']}", f"  判定：{verdict}"]
    for i, h in enumerate(hits, 1):
        lines.append(f"  #{i} chunk={h.chunk_id} doc={h.doc_id} {h.source_ref} "
                     f"标题={h.heading_path} why={h.why}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
class TestSearchRegression(unittest.TestCase):
    """10 题检索回归（真实药理课件主库只读副本）。"""

    def _check(self, qid: str) -> None:
        q = next(q for q in QUESTIONS if q["id"] == qid)
        hits = _search_mod().search(_CONN, q["query"], top_k=TOP_K, corpus="main")
        verdict = judge(q, hits)
        self.assertTrue(verdict["pass"], _format(q, hits, verdict))

    # ---- 逐题 ----------------------------------------------------------
    def test_search_regression_q01_classification(self):
        """Q01 分类题必须召回 p.5《镇静催眠药分类》且为 Top-1。"""
        self._check("Q01")

    def test_search_regression_q02_bdz_mechanism(self):
        """Q02 BDZ 作用机制 Top-1 必须是 p.16 BDZ 增强 GABA 抑制。"""
        self._check("Q02")

    def test_search_regression_q03_compare(self):
        """Q03 对比题两侧都要有代表，覆盖 p.16 与 p.18。"""
        self._check("Q03")

    def test_search_regression_q04_status_epilepticus(self):
        """Q04 癫痫持续状态首选（地西泮）进前 3。"""
        self._check("Q04")

    def test_search_regression_q05_antipsychotic_mechanism(self):
        """Q05 抗精神病药作用机制（阻滞 D2）进前 3。"""
        self._check("Q05")

    def test_search_regression_q06_cholinoceptor_agonist(self):
        """Q06 胆碱受体激动剂分类页命中。"""
        self._check("Q06")

    def test_search_regression_q07_neostigmine(self):
        """Q07 新斯的明药理作用/临床应用页命中。"""
        self._check("Q07")

    def test_search_regression_q08_diazepam_adr(self):
        """Q08 地西泮不良反应页必须是 Top-1（不是巴比妥不良反应页）。"""
        self._check("Q08")

    def test_search_regression_q09_barbiturate_respiratory(self):
        """Q09 巴比妥类过量致呼吸抑制页命中。"""
        self._check("Q09")

    def test_search_regression_q10_m_blocker(self):
        """Q10 M 胆碱受体阻断药分类页命中。"""
        self._check("Q10")

    # ---- 契约（保持三种模式与 corpus 隔离语义） --------------------------
    def test_search_regression_modes_and_corpus(self):
        """hybrid/vector/keyword 三模式与 corpus/doc_ids/top_k 语义不变。"""
        search = _search_mod().search
        q = "镇静催眠药按化学结构分哪几类"
        hybrid = search(_CONN, q, top_k=5)
        self.assertTrue(1 <= len(hybrid) <= 5)
        vector = search(_CONN, q, top_k=5, mode="vector")
        self.assertTrue(1 <= len(vector) <= 5)
        self.assertTrue(all(h.kw_score == 0 for h in vector))
        keyword = search(_CONN, q, top_k=5, mode="keyword")
        self.assertTrue(1 <= len(keyword) <= 5)
        self.assertTrue(all(h.vec_score == 0 for h in keyword))
        # corpus 隔离：副本只有 main 库，查 aux 必须 0 命中，查 all/main 有命中
        self.assertEqual(search(_CONN, q, top_k=5, corpus="aux"), [])
        self.assertGreaterEqual(len(search(_CONN, q, top_k=5, corpus="all")), 1)
        self.assertGreaterEqual(len(search(_CONN, q, top_k=5, corpus="main")), 1)
        self.assertEqual(search(_CONN, "", top_k=5), [])
        by_doc = search(_CONN, "地西泮", top_k=8, doc_ids=[41])
        self.assertTrue(by_doc and all(h.doc_id == 41 for h in by_doc))

    def test_search_regression_fixture_pages(self):
        """副本库的期望页仍在原位（数据漂移时给出明确失败原因）。"""
        rows = {int(r["id"]): r for r in _CONN.execute(
            "SELECT id, doc_id, source_ref, heading_path FROM chunks "
            "WHERE id IN (1382,1393,1395,1397,1406,1409,1679,1727,1841,2080,2081,2130)")}
        want = {
            1382: (41, "p.5", "分类"),
            1393: (41, "p.16", "BDZ"),
            1395: (41, "p.18", ""),
            1406: (41, "p.29", ""),
            1397: (41, "p.20", "不良反应"),
            1409: (41, "p.32", "呼吸"),
            2130: (47, "p.17", "机制"),
        }
        for cid, (doc_id, page, kw) in want.items():
            self.assertIn(cid, rows, f"副本库缺 chunk {cid}；真实库结构已变，需重拍 test.db")
            r = rows[cid]
            self.assertEqual(int(r["doc_id"]), doc_id, f"chunk {cid} 所属文档变了")
            self.assertIn(page, r["source_ref"] or "", f"chunk {cid} 页码变了")
            if kw:
                self.assertIn(kw, r["heading_path"] or "", f"chunk {cid} 标题变了")

    def test_search_regression_questions_json_in_sync(self):
        """_work/m0/search/questions.json 与本文件内嵌清单保持一致（防漂移）。"""
        if not QUESTIONS_JSON.exists():
            self.skipTest("无 questions.json（改前/改后跑分工具用的机器可读副本）")
        data = json.loads(QUESTIONS_JSON.read_text(encoding="utf-8"))
        by_id = {q["id"]: q for q in data["questions"]}
        self.assertEqual(size := set(by_id), {q["id"] for q in QUESTIONS},
                         f"题号不一致：json={size}")
        for q in QUESTIONS:
            self.assertEqual(by_id[q["id"]]["query"], q["query"],
                             f"{q['id']} query 与 questions.json 不一致")
            # note 只是给人读的解释，允许两边措辞不同；判定条件必须一致
            self.assertEqual(
                {k: v for k, v in by_id[q["id"]]["expect"].items() if k != "note"},
                {k: v for k, v in q["expect"].items() if k != "note"},
                f"{q['id']} expect 与 questions.json 不一致")
        self.assertEqual(int(data.get("top_k") or TOP_K), TOP_K)


if __name__ == "__main__":
    unittest.main(verbosity=2)
