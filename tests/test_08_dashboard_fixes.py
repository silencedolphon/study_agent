"""M0 仪表盘闭环验收（M0-2 新建知识点入口 / M0-4 错题闭环 / M0-5 掌握度口径）。

覆盖三件事的**用户路径**：
  · M0-2：空库 0 知识点时，/api/kp 新建 → /api/kps 可见 → 今日复习队列出现该点；
    前端页面里「＋ 新建知识点」入口与表单字段确实被渲染（空态 + 列表两个分支）。
  · M0-4：POST /api/mistake 200 且 mistakes 表 +1、非法参数 400 且不落库；
    评分 0–2 自动记错题（auto_mistake_id）且该 KP 间隔归零、重复评分各记一条；
    页面里知识点详情有「记一道错题」、错题空态/复习说明指向真实入口、评分 toast
    会告知自动记错题（不再指向不存在的控件）。
  · M0-5：同一知识点在 /api/kps、/api/kp?id=、/api/graph、/api/stats 四处
    mastery 一致（差 ≤0.01）；列表按掌握度升序；库列 mastery 保持不动（不写库）。

隔离：全部通过 `tests/env.py` 在 `_work/verify/` 的沙箱 + 数据副本上跑，
不读写真实 `index/ subjects/ raw/ notes/ logs/`。
"""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None          # 主库实例（有 3 份文档/切块，用于错题与掌握度链路）
_EMPTY = None        # 空库实例（0 知识点，用于 M0-2 的"空库建成"验收）
_MAIN_DB = None
_EMPTY_DB = None


def setUpModule():
    global _SRV, _EMPTY, _MAIN_DB, _EMPTY_DB
    env.prepare(clean_data=True)
    _MAIN_DB = env.DATA_DIR / "main.db"
    _EMPTY_DB = env.DATA_DIR / "empty.db"
    _SRV = env.Server(tag="m0dash")
    _SRV.start()
    _EMPTY = env.Server(db_path=_EMPTY_DB, tag="m0dash-empty")
    _EMPTY.start()


def tearDownModule():
    for srv in (_SRV, _EMPTY):
        if srv is not None:
            srv.stop()


def _get(srv, path, **params):
    st, obj = srv.get_json(path, timeout=180, **params)
    assert st == 200, f"GET {path} 返回 {st}: {obj}"
    return obj or {}


def _post(srv, path, payload):
    st, obj = srv.post_json(path, payload, timeout=180)
    return st, (obj or {})


def _create_kp(srv, name, **extra):
    st, obj = _post(srv, "/api/kp", {"name": name, "summary": "M0 验收",
                                    "subject": "药理学", **extra})
    assert st == 200 and obj.get("ok"), f"创建知识点失败: {st} {obj}"
    return int(obj["kp_id"])


def _page_html(srv):
    r = srv.get("/", timeout=60)
    assert r.status == 200, f"首页返回 {r.status}"
    return r.text


def _mistake_count(srv):
    return len(_get(srv, "/api/mistakes")["mistakes"])


class TestM0CreateKp(unittest.TestCase):
    """M0-2：空库也能从「知识点」页新建知识点并进入复习流。"""

    def test_empty_library_create_kp(self):
        """空库 0 KP：建一条 → 列表可见 → 今日队列出现（status=new）→ 同名 upsert。"""
        self.assertEqual(_get(_EMPTY, "/api/kps")["knowledge_points"], [],
                         "空库夹具应当是 0 个知识点")
        st, r = _post(_EMPTY, "/api/kp", {"name": "M0-2-空库新建知识点",
                                          "subject": "药理学",
                                          "summary": "从知识点页表单建的第一条"})
        self.assertEqual(st, 200)
        self.assertTrue(r.get("ok"), f"新建失败: {r}")
        kp_id = int(r["kp_id"])

        kps = _get(_EMPTY, "/api/kps")["knowledge_points"]
        self.assertIn(kp_id, {k["id"] for k in kps},
                      "新建的知识点应立刻出现在 /api/kps")
        queue = _get(_EMPTY, "/api/queue", limit=50)["queue"]
        item = next((q for q in queue if q["id"] == kp_id), None)
        self.assertIsNotNone(item, "新建未复习的知识点应进入今天的复习队列")
        self.assertEqual(item["status"], "new")
        # 同名再建走 upsert，不产生第二个知识点
        st, same = _post(_EMPTY, "/api/kp", {"name": "M0-2-空库新建知识点"})
        self.assertEqual(st, 200)
        self.assertEqual(int(same["kp_id"]), kp_id)

    def test_frontend_entry_rendered(self):
        """页面里两个分支都渲染「＋ 新建知识点」，且复用 kpForm() 的四个字段。"""
        page = _page_html(_EMPTY)
        self.assertIn('id="kp-create-toggle"', page, "缺少「＋ 新建知识点」按钮")
        self.assertIn("＋ 新建知识点", page, "按钮文案丢失")
        self.assertIn("kpCreateCard()", page, "新建卡片函数未渲染")
        self.assertIn("bindKpCreate()", page, "新建卡片未绑定点击事件")
        # 空态与列表两个分支都要有这个入口（viewKps 里调用两次）
        start = page.index("async function viewKps()")
        end = page.index("// ---------------- 批量整理知识点", start)
        view_kps = page[start:end]
        self.assertEqual(view_kps.count("kpCreateCard()"), 2,
                         "知识点页的空态与列表分支都应渲染新建卡片")
        self.assertEqual(view_kps.count("bindKpCreate()"), 2,
                         "知识点页的两个分支都应绑定新建卡片")
        # 表单字段：名称（必填）/ 科目 / 摘要 / 出处 chunk_id
        start = page.index("function kpForm()")
        form = page[start:page.index("function bindKpForm()", start)]
        for field in ('id="kp-name"', 'id="kp-subject"', 'id="kp-summary"',
                      'id="kp-chunks"', "必填"):
            self.assertIn(field, form, f"新建表单缺少字段 {field}")
        self.assertIn("/api/kp", page, "前端未调用 POST /api/kp")
        self.assertIn("go('kps')", page, "保存后应刷新知识点页")


class TestM0Mistake(unittest.TestCase):
    """M0-4：错题写入闭环（API + 评分自动记 + 前端真实入口）。"""

    def test_post_mistake_ok_and_invalid(self):
        """POST /api/mistake：200 + mistakes 表 +1；非法参数一律 400 且不落库。"""
        kp_id = _create_kp(_SRV, "M0-4-手工错题知识点")
        n0 = _mistake_count(_SRV)
        st, r = _post(_SRV, "/api/mistake", {
            "question": "地西泮的作用机制是什么？",
            "my_answer": "不知道",
            "right_answer": "增强 GABA 的抑制效应",
            "reason": "概念混淆",
            "kp_id": kp_id,
        })
        self.assertEqual(st, 200, f"POST /api/mistake 应 200，实际 {st}: {r}")
        self.assertTrue(r.get("ok"))
        mid = int(r["mistake_id"])
        self.assertEqual(_mistake_count(_SRV), n0 + 1, "错题应 +1")
        m = next(x for x in _get(_SRV, "/api/mistakes")["mistakes"] if x["id"] == mid)
        self.assertEqual(m["question"], "地西泮的作用机制是什么？")
        self.assertEqual(m["kp_id"], kp_id)
        self.assertEqual(m["reason"], "概念混淆")

        bad_cases = [
            ({}, "缺少 question"),
            ({"question": "  "}, "question 为空白"),
            ({"question": "x", "kp_id": "abc"}, "kp_id 不是整数"),
            ({"question": "x", "chunk_id": "abc"}, "chunk_id 不是整数"),
            ({"question": "x", "reason": "随便写的错因"}, "错因不在 6 个标签内"),
            ({"question": "x", "kp_id": 999999}, "kp_id 不存在"),
        ]
        for body, why in bad_cases:
            st, obj = _post(_SRV, "/api/mistake", body)
            self.assertEqual(st, 400, f"{why} 应返回 400，实际 {st}: {obj}")
            self.assertIn("error", obj, f"{why} 应带 error 说明")
        self.assertEqual(_mistake_count(_SRV), n0 + 1, "非法请求不应写进错题表")

    def test_review_wrong_auto_mistake(self):
        """评分 0–2 自动记错题（auto_mistake_id）→ 间隔归零；≥3 不记；重复评分各记一条。"""
        kp_id = _create_kp(_SRV, "M0-4-评分自动错题知识点")
        # 先正常复习一次，让间隔/掌握度有非零基线
        st, ok = _post(_SRV, "/api/review",
                       {"kp_id": kp_id, "grade": 4, "question": "先答对一次"})
        self.assertEqual(st, 200)
        self.assertIsNone(ok.get("auto_mistake_id"), "评分 4 不应自动记错题")

        n0 = _mistake_count(_SRV)
        st, r = _post(_SRV, "/api/review", {
            "kp_id": kp_id, "grade": 2, "question": "苯二氮卓类的作用机制？",
        })
        self.assertEqual(st, 200, f"评分接口返回 {st}: {r}")
        self.assertIn("auto_mistake_id", r, "评分 0–2 的响应应带 auto_mistake_id")
        mid = r["auto_mistake_id"]
        self.assertIsNotNone(mid, "评分 2 应自动记一条错题")
        self.assertEqual(_mistake_count(_SRV), n0 + 1, "评分 2 后错题应 +1")

        m = next(x for x in _get(_SRV, "/api/mistakes")["mistakes"] if x["id"] == mid)
        self.assertEqual(m["kp_id"], kp_id)
        self.assertEqual(m["reason"], "记忆遗忘", "自动错题默认错因应为记忆遗忘")
        self.assertEqual(m["question"], "苯二氮卓类的作用机制？")
        self.assertEqual(m["resolved"], 0)

        kp = _get(_SRV, "/api/kp", id=kp_id)["kp"]
        self.assertEqual(kp["interval_days"], 0, "答错后该知识点间隔应归零")
        self.assertGreaterEqual(kp["lapses"], 1, "答错应记一次遗忘")
        self.assertEqual(r["mastery"], kp["mastery"],
                         "评分响应的 mastery 应与详情同口径")

        # 没给题面时用知识点名称；重复评分允许各记一条（不去重）
        st, r2 = _post(_SRV, "/api/review", {"kp_id": kp_id, "grade": 0})
        self.assertEqual(st, 200)
        self.assertNotEqual(r2["auto_mistake_id"], mid, "重复答错应各自新增一条错题")
        m2 = next(x for x in _get(_SRV, "/api/mistakes")["mistakes"]
                  if x["id"] == r2["auto_mistake_id"])
        self.assertIn("M0-4-评分自动错题知识点", m2["question"],
                      "没有题面时错题题目应回落到知识点名称")

        # 评分 ≥3 不记错题（也不改错题数）
        before = _mistake_count(_SRV)
        st, r3 = _post(_SRV, "/api/review", {"kp_id": kp_id, "grade": 3})
        self.assertEqual(st, 200)
        self.assertIsNone(r3.get("auto_mistake_id"))
        self.assertEqual(_mistake_count(_SRV), before, "评分 ≥3 不应产生错题")

    def test_frontend_mistake_entry(self):
        """前端：知识点详情有「记一道错题」、空态/复习说明指向真实入口、toast 告知自动记。"""
        page = _page_html(_SRV)
        self.assertIn("记一道错题", page, "页面缺少「记一道错题」按钮")
        self.assertIn("function openMistakeForm(", page, "缺少记错题表单")
        self.assertIn("/api/mistake", page, "前端未调用 POST /api/mistake")
        self.assertIn("auto_mistake_id", page, "评分 toast 未处理自动记错题的返回值")
        self.assertIn("已记入错题本", page, "缺少「已记入错题本」的 toast 文案")
        # 旧文案：让不存在的"助手记录"/"助手生成"入口（P0-A/P0-D）
        self.assertNotIn("复习答错时让助手记录", page,
                         "错题空态仍指向不存在的助手入口")
        self.assertNotIn("让助手从资料中生成新的知识点", page,
                         "复习空态仍指向不存在的助手入口")
        self.assertIn("＋ 记一道错题", page, "错题页缺少手工入口")
        self.assertIn("＋ 新建知识点", page, "复习空态应指向真实的「＋ 新建知识点」")
        # 评分说明要说明 0–2 会进错题本
        self.assertIn("自动记入错题本", page)


class TestM0Mastery(unittest.TestCase):
    """M0-5：掌握度四处同口径、列表排序、不写库列。"""

    def test_mastery_consistent_across_surfaces(self):
        """评分 5 后 /api/kps、/api/kp?id=、/api/graph、/api/stats 四处差值 ≤0.01。"""
        kp_id = _create_kp(_SRV, "M0-5-掌握度口径知识点")
        st, r = _post(_SRV, "/api/review", {"kp_id": kp_id, "grade": 5})
        self.assertEqual(st, 200, f"评分接口返回 {st}: {r}")
        self.assertGreater(r["mastery"], 0.0, "评分 5 后掌握度应大于 0")

        m_review = r["mastery"]
        m_list = next(k["mastery"] for k in _get(_SRV, "/api/kps")["knowledge_points"]
                      if k["id"] == kp_id)
        m_detail = _get(_SRV, "/api/kp", id=kp_id)["kp"]["mastery"]
        graph = _get(_SRV, "/api/graph")
        m_graph = next(n["mastery"] for n in graph["nodes"] if n["id"] == kp_id)
        stats = _get(_SRV, "/api/stats")
        m_stats = next((w["mastery"] for w in (stats.get("weakest") or [])
                        if w["id"] == kp_id), None)
        self.assertIsNotNone(m_stats, "/api/stats 的 weakest 应包含刚评分的知识点")

        values = {"review": m_review, "kps": m_list, "kp_detail": m_detail,
                  "graph": m_graph, "stats": m_stats}
        spread = max(values.values()) - min(values.values())
        self.assertLessEqual(spread, 0.01,
                             f"同一 KP 掌握度四处口径不一致（差 {spread:.4f}）：{values}")
        self.assertGreater(m_list, 0.0,
                           "/api/kps 不再返回库里恒为 0 的 mastery 列")
        self.assertGreater(m_graph, 0.0, "/api/graph 节点掌握度应同步")

        # 不写库：库列 mastery 保持原值（0），接口是"现算"而不是回写
        with sqlite3.connect(str(_MAIN_DB)) as c:
            col = c.execute("SELECT mastery FROM knowledge_points WHERE id=?",
                            (kp_id,)).fetchone()[0]
        self.assertEqual(float(col or 0.0), 0.0,
                         "M0-5 要求只读现算、不写库；库列 mastery 不应被改动")

    def test_kps_sorted_by_mastery(self):
        """列表按掌握度升序：未复习(0) 在前，评分过的在后；整体单调不减。"""
        zero_id = _create_kp(_SRV, "M0-5-未复习知识点")
        high_id = _create_kp(_SRV, "M0-5-已掌握知识点")
        _post(_SRV, "/api/review", {"kp_id": high_id, "grade": 5})
        kps = _get(_SRV, "/api/kps")["knowledge_points"]
        masters = [float(k["mastery"]) for k in kps]
        self.assertEqual(masters, sorted(masters), "知识点列表应按掌握度升序")
        pos = {k["id"]: i for i, k in enumerate(kps)}
        self.assertLess(pos[zero_id], pos[high_id],
                        "未复习(0%) 应排在已复习(>0%) 之前")
        self.assertEqual(float(kps[pos[zero_id]]["mastery"]), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
