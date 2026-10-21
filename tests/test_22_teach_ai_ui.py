"""task-30：教材补充 AI 卡（右栏）+ 导图横幅 AI 定位文案（前端验收）。

覆盖：
  · 右栏 AI 卡：未生成 estimate（1 次调用/上界/模型）+「生成 AI 补充」；已生成
    verdict/summary/points(text+pages+kind)/gaps/citations 折叠/成本与耗时/重新生成；
    no_supplement / failed 状态与「教材原文（参考）」本地兜底折叠；
  · 旧版纯本地产物：显示"旧版教材补充，建议重新 AI 定位"→ 按钮走 build；
  · 顶部整份课件定位章（AI/本地兜底小字）+ 页图；
  · 导图横幅：needs_backfill/旧版 → "AI 定位章节（约 ≤¥X）"文案 + 按钮；
    已有产物 → 就绪行显示章节/小节数/可逐条生成 AI；
  · 路由胶水：GET kp 返回 estimate、GET kp/estimate、POST kp/build（同步不持全局锁）、
    /api/textbook-supplement/build 返回 meta.chapter_map；on_done 仍接整理回调；
  · 原有功能（左树/正文/页图/任务-27 的本地补充）不退化；错误一律 200/4xx JSON 不 500。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None
_PAGE = ""


def setUpModule():
    global _SRV, _PAGE
    env.prepare(clean_data=True)
    _SRV = env.Server(tag="teachaiui")
    _SRV.start()
    r = _SRV.get("/", timeout=60)
    assert r.status == 200, f"首页返回 {r.status}"
    _PAGE = r.text


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


class TestAiSidebar(unittest.TestCase):
    """右侧栏 AI 卡。"""

    def test_card_structure(self):
        for probe in (
            "mteach-ai", "mteach-ai-badge", "mteach-ai-est", "mteach-ai-act",
            "mteach-cm", "mteach-legacy", "mteach-local", "mteach-cites",
            "mteach-point", "mteach-kind", "mteach-gaps", "mteach-ai-foot",
            "function mmTeachGenerate(", "function mmTeachLegacyRebuild(",
            "function mteachAiCard(", "function mteachChapterLine(",
            "function mteachReadyLine(",
            'id="mteach-gen"', 'id="mteach-rebuild"',
        ):
            self.assertIn(probe, _PAGE, f"缺少 AI 卡实现：{probe}")

    def test_estimate_and_actions(self):
        for probe in (
            "预计", "次调用", "成本上界", "模型", "生成 AI 补充", "重新生成",
            "重试生成", "确认后才调用外接大模型",
            "/api/textbook-supplement/kp/build",
            "confirmBox(force ? '重新生成 AI 教材补充？' : '生成 AI 教材补充？'",
            "doc_id: docId, kp_id: kpId, force: !!force",
        ):
            self.assertIn(probe, _PAGE, f"缺少 AI 卡交互：{probe}")

    def test_status_states(self):
        for probe in (
            "AI 已生成", "AI：无需补充", "生成失败",
            "AI 判断教材对应小节没有需要补充的内容",
            "课件没讲", "教材出处", "本地「教材原文（参考）」仍可在下方查看",
            "旧版教材补充（纯本地匹配，无 AI 章节映射）",
            "重新 AI 定位并补做",
        ):
            self.assertIn(probe, _PAGE, f"缺少状态文案：{probe}")

    def test_local_fold_and_chapter_map(self):
        for probe in (
            "教材原文（参考）", "本地匹配，点开看", "mteach-local",
            "整份课件定位：", "AI 定位", "本地兜底", "映射成本",
        ):
            self.assertIn(probe, _PAGE, f"缺少本地兜底/章节映射：{probe}")


class TestBackfillBanner(unittest.TestCase):
    """导图栏横幅 AI 文案。"""

    def test_banner_copy(self):
        for probe in (
            "可先 AI 定位本章并补做教材补充",
            "AI 定位章节并补做",
            "重新 AI 定位并补做",
            "旧版",
            "约 ",
            "不改动原知识点",
            "本地小节：",
            "可逐条生成 AI 补充",
            "mteachReadyLine(",
            "章节：",
        ):
            self.assertIn(probe, _PAGE, f"缺少横幅文案：{probe}")


class TestBackendGlue(unittest.TestCase):
    """dashboard.py 新路由与错误降级（源码静态 + 运行时）。"""

    def test_source_routes(self):
        src = (Path(__file__).resolve().parents[1] / "tools" / "dashboard.py"
               ).read_text(encoding="utf-8")
        for probe in (
            "def api_textbook_supplement_kp_build(",
            "def api_textbook_supplement_kp_estimate(",
            "def _teach_augment_status(",
            "def _teach_derive(",
            "def _teach_ai_fn(",
            'if u.path == "/api/textbook-supplement/kp/build":',
            'if path == "/api/textbook-supplement/kp/estimate":',
            "def _teach_normalize_estimate(",
            "def _teach_estimate_map(",
            "on_done=_teach_after_kp_build",
            '"legacy"',
            "chapter_map",
            "n_ai_ready",
        ):
            self.assertIn(probe, src, f"缺少后端胶水：{probe}")
        # kp/build 在 POST 路由里、不包 with _lock（同步 5-20 秒）
        pos = src.index('if u.path == "/api/textbook-supplement/kp/build":')
        around = src[pos:pos + 200]
        self.assertIn("api_textbook_supplement_kp_build(payload)", around)
        self.assertNotIn("with _lock", around)

    def test_status_has_ai_fields(self):
        st, d = _SRV.get_json("/api/textbook-supplement/status", timeout=60, doc_id=1)
        self.assertEqual(st, 200)
        self.assertIn("legacy", d)
        self.assertIn("estimate", d)
        self.assertIn("ai", d)
        self.assertIn("n_ai_ready", d["ai"])
        self.assertIn("calls", d["estimate"])

    def test_kp_returns_estimate(self):
        st, d = _SRV.get_json("/api/textbook-supplement/kp", timeout=60,
                              doc_id=1, kp_id="KP-1")
        self.assertEqual(st, 200)
        self.assertIn("estimate", d)
        self.assertIn("cost_cny_max", d["estimate"])

    def test_kp_estimate_route(self):
        st, d = _SRV.get_json("/api/textbook-supplement/kp/estimate", timeout=60,
                              doc_id=1, kp_id="KP-1")
        self.assertEqual(st, 200)
        self.assertIn("estimate", d)
        self.assertIn("calls", d["estimate"])

    def test_kp_build_invalid_no_model_call(self):
        st, d = _SRV.post_json("/api/textbook-supplement/kp/build", timeout=60,
                               payload={"doc_id": 0, "kp_id": ""})
        self.assertEqual(st, 200)
        self.assertFalse(d.get("ok"))
        # 缺 kp_id：也必须 200 JSON（不触发模型）
        st, d = _SRV.post_json("/api/textbook-supplement/kp/build", timeout=60,
                               payload={"doc_id": 1})
        self.assertEqual(st, 200)
        self.assertFalse(d.get("ok"))

    def test_build_augmented(self):
        st, d = _SRV.post_json("/api/textbook-supplement/build", timeout=120,
                               payload={"doc_id": 0})
        self.assertEqual(st, 200)
        self.assertFalse(d.get("ok"))


class TestOtherFeaturesIntact(unittest.TestCase):
    def test_popup_and_old_sidebar(self):
        for probe in ('id="mmkp-tree"', 'id="mmkp-body"', 'id="mmkp-teach"',
                      "mmTeachPaint", "/api/teach-page?doc_id=",
                      "mteach-pg", "mteach-ps"):
            self.assertIn(probe, _PAGE, f"原有功能丢失：{probe}")
        for lb in ("问答", "总览", "复习", "预习", "资料", "知识点", "错题", "笔记"):
            self.assertIn(lb, _PAGE, f"导航缺失：{lb}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
