"""task-36：复习 UI v2（结构路径自测 + 已掌握按钮 + 三步窗口化 + 悬浮窗 + 名词整理入口）。

覆盖（task-33 的复习功能仍然覆盖，已按 v3 契约更新）：
  · 复习页：课件卡（熟悉度=已掌握/抽样）、月历「开始复习」；
  · 自测 v2：结构路径 branch 树、展开总结、已掌握/未掌握单选、全部选完才能提交、
    POST `{results:{kp_id:bool}}`、结果页熟悉度（已掌握/抽样）、进入三步；
  · 三步 v2：Step1 直接开现有知识点窗口（mmOpenKp）、Step2 笔记窗口、Step3 名词窗口，
    每步可跳过；悬浮窗 继续/下一个/跳过本步/结束，切页签后「继续」重开当前步窗口；
  · 页签保持：快照恢复（0ms 可见、不闪不消失）、holdUntil、静默刷新、主动关闭/评分不弹；
  · 名词整理入口：/api/terms/preview/options + /api/terms/preview/build（及别名），
    有预习→候选数/来源/0 AI 按钮；无预习→人话；构建后刷新 /api/terms；
  · 原有 5 条 /api/review/* 路由、笔记分组、其它页面不回归；不真实调 AI。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None
_PAGE = ""
_SRC = ""
ROOT = Path(__file__).resolve().parents[1]


def setUpModule():
    global _SRV, _PAGE, _SRC
    env.prepare(clean_data=True)
    _SRV = env.Server(tag="reviewuiv2")
    _SRV.start()
    r = _SRV.get("/", timeout=60)
    assert r.status == 200, f"首页返回 {r.status}"
    _PAGE = r.text
    _SRC = (ROOT / "tools" / "dashboard.py").read_text(encoding="utf-8")


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


class TestReviewHome(unittest.TestCase):
    def test_courseware_card_and_month_entry(self):
        for probe in (
            "课件复习流程", "rv-cw", "rv-cw-act", "rv-badge",
            "已自测 · 熟悉度", "已掌握", "未自测", "开始自测", "继续复习", "重新自测",
            "renderReviewHome", "reviewCoursewaresHtml", "bindReviewCoursewares",
            'data-act="review"', "revStartFromMonth", "开始复习",
            "到期复习队列", "deckCalendarCard", "遗忘曲线复习",
        ):
            self.assertIn(probe, _PAGE, f"复习首页缺少：{probe}")


class TestSelftestV2(unittest.TestCase):
    def test_structure_path_and_master_buttons(self):
        for probe in (
            "revStartSelftest", "renderReviewSelftest", "submitReviewSelftest",
            "reviewBranchOf", "reviewPathHtml", "reviewSummaryHtml",
            "rv-path", "rv-pnode", "rv-sum", "展开总结",
            "rv-master", "rv-mbtn", "已掌握", "未掌握",
            "标记自己能否复述/掌握", "已掌握条数 / 抽样条数", "已标记",
            "rv-st-submit", "rv-st-flow",
        ):
            self.assertIn(probe, _PAGE, f"自测 v2 缺少：{probe}")
        for old in ("reviewSegmentsHtml", 'input class="rv-blank"', "data-answer="):
            self.assertNotIn(old, _PAGE, f"旧挖空实现应已移除：{old}")

    def test_submit_uses_results(self):
        self.assertIn("results: results", _PAGE)
        self.assertIn("body: JSON.stringify({doc_id: REV.docId, session_id: d.session_id, results: results})",
                      _PAGE)


class TestFlowWindows(unittest.TestCase):
    def test_windowed_steps(self):
        for probe in (
            "revStartFlow", "renderReviewFlow", "renderReviewDone",
            "revOpenStepWindow", "revOpenNotesWindow", "revOpenTermWindow",
            "revCloseReviewWindows", "mmOpenKp(kp.kp_id, kp.name, null, false, REV.docId)",
            "第二步 · 整份课件的笔记", "第三步 · 预习重点名词",
            "revStepCn", "'跳过' + revStepCn(REV.step)",
            "rv-note-next", "rv-term-show", "显示解释", "openNote(",
            "① 知识点", "② 笔记", "③ 重点名词", "本次复习完成",
        ):
            self.assertIn(probe, _PAGE, f"三步窗口化缺少：{probe}")

    def test_float_reopens_current_step(self):
        for probe in (
            "revFloatUpdate", "revContinue", "revFloatNext", "revSkipStep",
            "revCurrentKp", "revCurrentTerm",
            "继续", "下一个", "下一步", "'跳过' + revStepCn(REV.step)",
            "Step ' + REV.step + '/3'", "重开当前步窗口",
            "study_agent_review_flow_v1", "localStorage", "reviewBoot",
        ):
            self.assertIn(probe, _PAGE, f"悬浮窗 v2 缺少：{probe}")


class TestTabStatePersistence(unittest.TestCase):
    def test_snapshot_restore_no_flash(self):
        for probe in (
            "UI_PERSIST", "uiSnapshotPopup", "uiShowPopupSnapshot", "uiClearSnapshot",
            "popupSnap", "holdUntil", "uiRefreshMmPopup", "uiRefreshDbPopup",
            "uiRestoreScroll", "uiSaveScroll", "keepNavClose",
            "closest('#nav')", "uiRestoreScroll();", "await uiRestorePopup();",
            "同步**先摆回快照", "不会自己弹回来",
        ):
            self.assertIn(probe, _PAGE, f"页签保持缺少：{probe}")
        # go() 顺序：先抓快照/摆快照，再渲染视图；渲染完成后恢复滚动+静默刷新
        go = _PAGE[_PAGE.index("async function go(name){"):_PAGE.index("// ---------------- 问答助手")]
        self.assertIn("uiShowPopupSnapshot();", go)
        self.assertLess(go.index("uiShowPopupSnapshot();"), go.index("VIEWS[name].render()"))
        self.assertIn("await uiRestorePopup();", go)

    def test_popup_params_recorded(self):
        for probe in (
            "uiRememberPopup({type: 'mm'", "uiRememberPopup({type: 'db'",
            "uiSnapshotModal", "uiForgetPopup", "MMPOP.docId", "mmOpenKp", "openKp",
        ):
            self.assertIn(probe, _PAGE, f"弹窗持久化缺少：{probe}")


class TestTermsPreviewEntry(unittest.TestCase):
    def test_terms_entry_ui(self):
        for probe in (
            'id="term-preview"', "refreshTermsPreview", "buildTermsPreview",
            "按预习重点名词重新整理（0 AI）", "预习第", "候选",
            "该教材章节还没有预习总结", "当前来源：", "0 AI",
            "/api/terms/preview/options", "/api/terms/build-preview",
            "TERMS.preview",
        ):
            self.assertIn(probe, _PAGE, f"名词整理入口缺少：{probe}")

    def test_terms_routes_source(self):
        for probe in (
            '"/api/terms/preview/options"', '"/api/terms/preview/status"',
            '"/api/terms/build-preview"', '"/api/terms/preview/build"',
            "def api_terms_preview_options(", "def api_terms_build_preview(",
            "terms_preview_status(doc_id, conn=conn)",
            "build_terms_from_preview(",
            "_terms_preview_mod",
        ):
            self.assertIn(probe, _SRC, f"名词整理路由/胶水缺少：{probe}")


class TestReviewRoutes(unittest.TestCase):
    def test_source_routes_and_contract(self):
        for probe in (
            '"/api/review/coursewares"', '"/api/review/flow"',
            '"/api/review/selftest"', '"/api/review/selftest/submit"',
            '"/api/review/selftest/status"',
            "def api_review_coursewares(", "def api_review_flow(",
            "def api_review_selftest(", "def api_review_selftest_submit(",
            "def api_review_selftest_status(",
            "list_courseware_reviews(conn)", "build_flow(conn, doc_id)",
            "start_selftest(doc_id)", "submit_selftest(doc_id, session_id, results)",
            "selftest_status(doc_id)",
            'results = payload.get("results"',
        ):
            self.assertIn(probe, _SRC, f"路由/胶水缺少：{probe}")
        pos = _SRC.index('if u.path == "/api/review/selftest/submit":')
        around = _SRC[max(0, pos - 260):pos + 220]
        self.assertNotIn("with _lock", around, "submit 不应持全局 _lock")
        pos2 = _SRC.index('if u.path == "/api/terms/build-preview"')
        around2 = _SRC[max(0, pos2 - 300):pos2 + 240]
        self.assertNotIn("with _lock", around2, "build-preview 不应持全局 _lock")

    def test_api_runtime_degrades(self):
        st, d = _SRV.get_json("/api/review/coursewares", timeout=60)
        self.assertEqual(st, 200)
        self.assertIn("ok", d)
        self.assertIn("items", d)
        for path, params in (
            ("/api/review/flow", {"doc_id": 0}),
            ("/api/review/selftest", {"doc_id": 0}),
            ("/api/review/selftest/status", {"doc_id": 0}),
            ("/api/terms/preview/options", {"doc_id": 0}),
            ("/api/terms/preview/status", {"doc_id": 0}),
        ):
            st, d = _SRV.get_json(path, timeout=60, **params)
            self.assertIn(st, (200, 400), f"{path} 不能 500")
            self.assertIn("ok", d)
            self.assertFalse(d.get("ok"), f"{path} 错参应如实失败")
        for path in ("/api/review/selftest/submit", "/api/terms/build-preview",
                     "/api/terms/preview/build"):
            payload = {"doc_id": 0, "session_id": "", "answers": {}, "results": {}}
            st, d = _SRV.post_json(path, timeout=60, payload=payload)
            self.assertIn(st, (200, 400), f"{path} 不能 500")
            self.assertFalse(d.get("ok"))


class TestOtherFeaturesIntact(unittest.TestCase):
    def test_nav_and_other_pages(self):
        for lb in ("问答", "总览", "复习", "预习", "资料", "知识点", "错题", "笔记"):
            self.assertIn(lb, _PAGE, f"导航缺失：{lb}")
        for probe in ("mmkp-tree", "mmkp-teach", "viewPreview", "viewMistakes"):
            self.assertIn(probe, _PAGE, f"其它页面功能丢失：{probe}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
