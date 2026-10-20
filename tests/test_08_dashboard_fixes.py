"""M0 保留项验收（M0-5 掌握度口径）。

用户实测后 M0 的"功能型新增"已回退（新建知识点入口 / 错题闭环 / 场景 / 导入入口 /
检索规则），**掌握度口径修复保留**。本文件只覆盖保留下来的那部分：

  · 同一知识点在 `/api/kps`、`/api/kp?id=`、`/api/graph`、`/api/stats` 四处
    mastery 一致（差 ≤0.01）；
  · `/api/kps` 列表按掌握度升序（未复习 0 在前）；
  · 库列 mastery 不被写（接口是现算，不写库）。

隔离：全部通过 `tests/env.py` 在 `_work/verify/` 的沙箱 + 数据副本上跑，
不读写真实 `index/ subjects/ raw/ notes/ logs/`。
"""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None
_MAIN_DB = None


def setUpModule():
    global _SRV, _MAIN_DB
    env.prepare(clean_data=True)
    _MAIN_DB = env.DATA_DIR / "main.db"
    _SRV = env.Server(tag="m0mastery")
    _SRV.start()


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


def _get(path, **params):
    st, obj = _SRV.get_json(path, timeout=180, **params)
    assert st == 200, f"GET {path} 返回 {st}: {obj}"
    return obj or {}


def _post(path, payload):
    st, obj = _SRV.post_json(path, payload, timeout=180)
    return st, (obj or {})


def _create_kp(name, **extra):
    st, obj = _post("/api/kp", {"name": name, "summary": "M0 验收",
                                "subject": "药理学", **extra})
    assert st == 200 and obj.get("ok"), f"创建知识点失败: {st} {obj}"
    return int(obj["kp_id"])


class TestM0Mastery(unittest.TestCase):
    """M0-5（回退后保留）：掌握度多处同口径、列表排序、不写库列。"""

    def test_mastery_consistent_across_surfaces(self):
        """评分 5 后 /api/kps、/api/kp?id=、/api/stats 三处差值 ≤0.01（图谱页已下线）。"""
        kp_id = _create_kp("M0-5-掌握度口径知识点")
        st, r = _post("/api/review", {"kp_id": kp_id, "grade": 5})
        self.assertEqual(st, 200, f"评分接口返回 {st}: {r}")
        self.assertGreater(r["mastery"], 0.0, "评分 5 后掌握度应大于 0")

        m_review = r["mastery"]
        m_list = next(k["mastery"] for k in _get("/api/kps")["knowledge_points"]
                      if k["id"] == kp_id)
        m_detail = _get("/api/kp", id=kp_id)["kp"]["mastery"]
        stats = _get("/api/stats")
        m_stats = next((w["mastery"] for w in (stats.get("weakest") or [])
                        if w["id"] == kp_id), None)
        self.assertIsNotNone(m_stats, "/api/stats 的 weakest 应包含刚评分的知识点")

        values = {"review": m_review, "kps": m_list, "kp_detail": m_detail,
                  "stats": m_stats}
        spread = max(values.values()) - min(values.values())
        self.assertLessEqual(spread, 0.01,
                             f"同一 KP 掌握度多处口径不一致（差 {spread:.4f}）：{values}")
        self.assertGreater(m_list, 0.0,
                           "/api/kps 不再返回库里恒为 0 的 mastery 列")

        # 不写库：库列 mastery 保持原值（0），接口是"现算"而不是回写
        with sqlite3.connect(str(_MAIN_DB)) as c:
            col = c.execute("SELECT mastery FROM knowledge_points WHERE id=?",
                            (kp_id,)).fetchone()[0]
        self.assertEqual(float(col or 0.0), 0.0,
                         "M0-5 要求只读现算、不写库；库列 mastery 不应被改动")

    def test_kps_sorted_by_mastery(self):
        """列表按掌握度升序：未复习(0) 在前，评分过的在后；整体单调不减。"""
        zero_id = _create_kp("M0-5-未复习知识点")
        high_id = _create_kp("M0-5-已掌握知识点")
        _post("/api/review", {"kp_id": high_id, "grade": 5})
        kps = _get("/api/kps")["knowledge_points"]
        masters = [float(k["mastery"]) for k in kps]
        self.assertEqual(masters, sorted(masters), "知识点列表应按掌握度升序")
        pos = {k["id"]: i for i, k in enumerate(kps)}
        self.assertLess(pos[zero_id], pos[high_id],
                        "未复习(0%) 应排在已复习(>0%) 之前")
        self.assertEqual(float(kps[pos[zero_id]]["mastery"]), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
