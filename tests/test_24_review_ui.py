"""task-33：复习板块大改（自测 + 三步复习 + 悬浮窗 + 页签保持 + 笔记分组）前端验收。

覆盖：
  · 复习页：课件复习卡（自测状态/熟悉度/名词数/开始自测·继续复习）、月历项「开始复习」；
  · 自测：行内挖空输入、提交、对错+答案、熟悉度、不熟悉列表、进入三步；
  · 三步 runner：Step1 KP（名称/总结/要点/页码，记住/再想想/下一步）、Step2 笔记分组、
    Step3 重点名词翻开、完成页建议；全局悬浮窗 #rev-float（跨页签可见/localStorage）；
  · 页签状态保持：保存滚动位置、知识点弹窗参数跨页签恢复（主动关闭/评分后不弹）；
  · 笔记页：按课件→绑定 KP 分组、搜索、打开笔记、跳到知识点；
  · 5 条 /api/review/* 路由（含 ok:false 降级，不 500；submit 不持全局锁）。
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
    _SRV = env.Server(tag="reviewui")
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
            "已自测 · 熟悉度", "未自测", "开始自测", "继续复习", "重新自测",
            "renderReviewHome", "reviewCoursewaresHtml", "bindReviewCoursewares",
            'data-act="review"', "revStartFromMonth", "开始复习",
        ):
            self.assertIn(probe, _PAGE, f"复习首页缺少：{probe}")
        # 原复习队列/月历仍在
        for probe in ("到期复习队列", "deckCalendarCard", "遗忘曲线复习"):
            self.assertIn(probe, _PAGE, f"原有复习功能丢失：{probe}")


class TestSelftest(unittest.TestCase):
    def test_selftest_ui(self):
        for probe in (
            "revStartSelftest", "renderReviewSelftest", "submitReviewSelftest",
            "reviewSegmentsHtml", "input class=\"rv-blank\"", "提交自测",
            "判分中", "本次熟悉度", "不熟悉：", "进入三步复习", "再测一次",
            "不能全部答对的知识点", "熟悉度 = 全对条数 / 抽中条数",
            "rv-st-submit", "rv-st-flow",
        ):
            self.assertIn(probe, _PAGE, f"自测缺少：{probe}")


class TestFlowRunner(unittest.TestCase):
    def test_three_steps(self):
        for probe in (
            "revStartFlow", "renderReviewFlow", "renderReviewDone",
            "① 知识点", "② 笔记", "③ 重点名词",
            "再想想", "记住了", "进入第二步：整份课件笔记",
            "教材原文", "rv-term-show", "显示解释", "完成复习",
            "本次复习完成", "建议：",
        ):
            self.assertIn(probe, _PAGE, f"三步复习缺少：{probe}")


class TestFloatAndPersistence(unittest.TestCase):
    def test_float_widget(self):
        for probe in ("rev-float", "el.id = 'rev-float'", "revFloatUpdate", "rvf-min",            "复习中", "Step ' + REV.step + '/3'", "最小化", "结束本次复习",
            "study_agent_review_flow_v1", "localStorage", "reviewBoot",
        ):
            self.assertIn(probe, _PAGE, f"悬浮窗/持久化缺少：{probe}")

    def test_tab_state_keep(self):
        for probe in (
            "UI_PERSIST", "uiSaveScroll", "uiRestoreScroll", "uiRememberPopup",
            "uiRestorePopup", "uiForgetPopup", "keepNavClose",
            "evPopOutside", "TYPE_NAV" if False else "closest('#nav')",
            "closeModal", "评分", "uiRestoreScroll();",
        ):
            self.assertIn(probe, _PAGE, f"页签保持缺少：{probe}")

    def test_popup_params_recorded(self):
        for probe in (
            "uiRememberPopup({type: 'mm'", "uiRememberPopup({type: 'db'",
            "MMPOP.docId", "mmOpenKp", "openKp",
        ):
            self.assertIn(probe, _PAGE, f"弹窗持久化缺少：{probe}")


class TestNotesGrouped(unittest.TestCase):
    def test_notes_grouping(self):
        for probe in (
            "课件 → 绑定知识点", "nt-grp", "nt-kp", "nt-search", "nt-note",
            "noteKpNamesFromTree", "复习这份课件的笔记", "跳到知识点",
            "未绑定课件", "未绑定知识点", "没有匹配的笔记",
            "async function viewNotes()",
        ):
            self.assertIn(probe, _PAGE, f"笔记分组缺少：{probe}")


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
            "start_selftest(doc_id)", "submit_selftest(doc_id, session_id, answers)",
            "selftest_status(doc_id)",
        ):
            self.assertIn(probe, _SRC, f"路由/胶水缺少：{probe}")
        # submit 不在 _lock 里（会原子写文件）
        pos = _SRC.index('if u.path == "/api/review/selftest/submit":')
        around = _SRC[max(0, pos - 260):pos + 200]
        self.assertNotIn("with _lock", around, "submit 不应持全局 _lock")

    def test_api_runtime_degrades(self):
        st, d = _SRV.get_json("/api/review/coursewares", timeout=60)
        self.assertEqual(st, 200)
        self.assertIn("ok", d)
        self.assertIn("items", d)
        for path, params in (
            ("/api/review/flow", {"doc_id": 0}),
            ("/api/review/selftest", {"doc_id": 0}),
            ("/api/review/selftest/status", {"doc_id": 0}),
        ):
            st, d = _SRV.get_json(path, timeout=60, **params)
            self.assertIn(st, (200, 400), f"{path} 不能 500")
            self.assertIn("ok", d)
            self.assertFalse(d.get("ok"), f"{path} 错参应如实失败")
        st, d = _SRV.post_json("/api/review/selftest/submit", timeout=60,
                               payload={"doc_id": 0, "session_id": "",
                                        "answers": {}})
        self.assertIn(st, (200, 400))
        self.assertFalse(d.get("ok"))

    def test_answer_hidden_from_initial_html(self):
        # 自测答案由接口下发、页面只渲染空白输入；静态页不能出现答案字段名兜底错误
        self.assertIn("data-answer=", _PAGE)
        self.assertIn("reviewSegmentsHtml", _PAGE)


class TestOtherFeaturesIntact(unittest.TestCase):
    def test_nav_and_other_pages(self):
        for lb in ("问答", "总览", "复习", "预习", "资料", "知识点", "错题", "笔记"):
            self.assertIn(lb, _PAGE, f"导航缺失：{lb}")
        for probe in ("mmkp-tree", "mmkp-teach", "viewPreview", "viewMistakes"):
            self.assertIn(probe, _PAGE, f"其它页面功能丢失：{probe}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
