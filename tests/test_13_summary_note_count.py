"""回归：`/api/summary-kp` 必须返回 `note_count`（卡 C-0056 的知识点小窗笔记计数）。

真实缺陷（用户 2026-10-19 报告）：
    知识点页 → 课件思维导图 → 点节点打开「理解与要点」小窗，底部写着「已绑定 N 条笔记」。
    刚保存一条笔记后前端就地 +1；但点「下一知识点」再切回原知识点，计数又变回 0。

根因：`api_summary_kp()` 里算了 `n_notes = len(study.notes_for(...))`，
但返回 dict 里**没有 `note_count` 这个键**（灾后重建版漏了一行）。
前端 `const nNotes = parseInt(d.note_count || 0)` 于是恒为 0，只有保存后的本地 +1 是假象。

本用例不依赖真实的 logs/ 产物：直接给 `api_summary_kp` 打桩（索引/序列/课件图/笔记查询），
只验证"返回值里带着与 notes_for 一致的计数"这一契约。
"""

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def _load_dashboard():
    spec = importlib.util.spec_from_file_location(
        "dashboard_under_test", PROJECT / "tools" / "dashboard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


dash = _load_dashboard()


class TestSummaryKpNoteCount(unittest.TestCase):
    """`note_count` 必须出现在返回里，且等于 notes_for 的条数。"""

    def setUp(self):
        self._orig = (dash._lkp_index, dash._lkp_seq, dash._slide_info,
                      dash.study.notes_for)

    def tearDown(self):
        dash._lkp_index, dash._lkp_seq, dash._slide_info, dash.study.notes_for = self._orig

    def _patch(self, notes):
        kp = {
            "id": "KP-T-1", "name": "测试知识点", "pages": [3], "sources": [],
            "material": [], "points": ["要点"], "summary": "总结", "level": 3,
            "node_path": ["第一章"], "node_name": "测试", "insufficient": False,
        }
        dash._lkp_index = lambda doc_id: {"by_id": {"KP-T-1": kp}, "meta": {}, "source": "t.json"}
        dash._lkp_seq = lambda doc_id: [{"id": "KP-T-1", "name": "测试知识点"}]
        dash._slide_info = lambda doc_id, pages, conn=None: {"deck": "", "slides": []}
        dash.study.notes_for = lambda kp_id="", doc_id=0: list(notes)

    def test_note_count_matches_notes_for(self):
        """有 1 条绑定笔记时，返回 note_count=1（切节点再切回来也不会变 0）。"""
        self._patch([{"file": "a.md"}])
        d = dash.api_summary_kp(2, "KP-T-1", None)
        self.assertIn("note_count", d, "summary-kp 返回缺少 note_count，前端会恒显示 0 条")
        self.assertEqual(d["note_count"], 1)

    def test_zero_notes_reports_zero(self):
        """没有笔记时也要显式给 0（不是缺键）。"""
        self._patch([])
        d = dash.api_summary_kp(2, "KP-T-1", None)
        self.assertEqual(d.get("note_count"), 0)


if __name__ == "__main__":
    unittest.main()
