"""预习页（task-24）：图谱页签 → 预习页签 + 教材章节目录 + 左树右文查看器。

覆盖：
  · 删图谱：VIEWS 无 graph、无 viewGraph/api_graph、`/api/graph` 返回 404；
    新页签「预习」在导航里（其余 7 个页面一个不少）；
  · 预习后端 API：`/api/preview/options|chapters|estimate|doc|progress` 正常返回
    （空/错参数也给 JSON，不 500）；`start` 在不启用/未就绪时如实返回不启动；
  · 页面结构与关键交互：章节目录（可展开/收起、状态徽标、每章按钮）、左树右文
    查看器（面包屑、why/core/lecture/pitfalls/self_check answers 折叠、出处 chip 可点）、
    生成流程（estimate → 确认框 → start → 800ms 轮询 progress）、未开启横幅/空态、
    390 响应式（单栏 + 返回章节）；
  · 不碰其它页面：图谱相关用户入口全部消失，其它测试文件不动（旧测试的失败由 Lead 统一改）。
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
    _SRV = env.Server(tag="previewui")
    _SRV.start()
    r = _SRV.get("/", timeout=60)
    assert r.status == 200, f"首页返回 {r.status}"
    _PAGE = r.text


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


class TestGraphRemoved(unittest.TestCase):
    """删掉图谱页签与 API（用户要求：换成预习页签）。"""

    def test_no_graph_view_or_api_in_page(self):
        self.assertNotIn("graph:    {label:'图谱'", _PAGE, "VIEWS 里还有图谱页签")
        self.assertNotIn("async function viewGraph(", _PAGE, "viewGraph 函数没删")
        self.assertIn("preview:  {label:'预习',  render: viewPreview},", _PAGE,
                      "预习页签没有放在图谱原来的位置")
        self.assertNotIn("/api/graph", _PAGE, "前端还有 /api/graph 调用")
        self.assertNotIn("def api_graph(", _PAGE, "api_graph 后端函数没删")
        # 其它 7 个页面还在
        for pid in ("assistant", "overview", "review", "docs", "kps", "mistakes", "notes"):
            self.assertIn(f"{pid}:", _PAGE, f"页面 {pid} 被误删")

    def test_graph_api_404(self):
        status, body = _SRV.get_json("/api/graph", timeout=60)
        self.assertEqual(status, 404, "删掉的 /api/graph 应统一返回 404 JSON")
        self.assertIn("error", body)


class TestPreviewFrontend(unittest.TestCase):
    """预习页前端：目录 / 查看器 / 生成流程 / 响应式。"""

    def test_view_and_data_flow(self):
        for probe in (
            "async function viewPreview(",
            "function pvRender(",
            "function pvNormArtifact(",
            "async function pvEnsureArt(",
            "async function pvStart(",
            "function pvWatchProgress(",
            "renderProgress(p, '#pv-progress')",
            "setInterval(tick, 800)",
            "const r = await api('/api/preview/start'",
            "await api('/api/preview/estimate?doc_id='",
            "await api('/api/preview/doc?doc_id='",
        ):
            self.assertIn(probe, _PAGE, f"预览页缺少实现：{probe}")

    def test_chapter_list_and_buttons(self):
        # 章节目录：可收起、状态徽标、三种按钮
        for cls in ("pv-wrap", "pv-side", "pv-chap", "pv-chap-head", "pv-badge",
                    "pv-sec", "pv-main", "pv-crumb", "pv-qa", "pv-back"):
            self.assertIn(cls, _PAGE, f"缺少查看器元素/样式 {cls}")
        for txt in ("章节目录", "已预习", "未预习", "生成中", "失败",
                    "预习本章", "重新预习", "查看预习", "章概览",
                    "这一小节要解决什么", "核心概念", "老师会怎么讲", "易错点",
                    "自测（答案默认收起）", "出处", "本章关键名词"):
            self.assertIn(txt, _PAGE, f"缺少文案：{txt}")
        # 自测答案放 details（默认收起）
        self.assertIn("details class=\"pv-qa\"", _PAGE)

    def test_viewer_content_and_sources(self):
        for key in ("subsections", "summary", "overview", "study_order",
                    "difficulties", "prerequisites", "exam_focus",
                    "self_check", "pitfalls", "lecture", "core"):
            self.assertIn(key, _PAGE, f"查看器没有读取字段 {key}")
        # 页/块出处 chip 复用 openChunk
        self.assertIn("openChunk(", _PAGE)
        self.assertIn("pv-chip", _PAGE)

    def test_generate_flow_estimate_and_progress(self):
        self.assertIn("预计 ", _PAGE)
        self.assertIn("次调用", _PAGE)
        self.assertIn("成本上界", _PAGE)
        self.assertIn("章 ", _PAGE)
        self.assertIn("confirmBox(force ? '重新预习这一章？' : '现在预习这一章？'", _PAGE,
                      "生成前没有费用确认框")
        self.assertIn("确认后才开始调用外接大模型", _PAGE)
        self.assertIn("progress", _PAGE)
        # 生成中按钮 disabled
        self.assertIn("PV.prog && PV.prog.active) ? 'disabled'", _PAGE)

    def test_empty_states_and_settings_jump(self):
        self.assertIn("还没有教材入库", _PAGE)
        self.assertIn("导入教材", _PAGE)
        self.assertIn("这一章还没预习", _PAGE)
        self.assertIn("预习功能未开启", _PAGE)
        self.assertIn("去设置开启", _PAGE)
        self.assertIn("SET_TAB = 'preview'", _PAGE)
        self.assertIn("openSettings()", _PAGE)

    def test_responsive_rules(self):
        self.assertIn("@media (max-width:900px)", _PAGE)
        self.assertIn(".pv-wrap{flex-direction:column}", _PAGE)
        self.assertIn(".pv-mob-hide-main .pv-main{display:none}", _PAGE)
        self.assertIn(".pv-mob-hide-side .pv-side{display:none}", _PAGE)
        self.assertIn(".pv-back{display:none}", _PAGE)
        self.assertIn(".pv-wrap.pv-mob-hide-side .pv-back{display:inline-block}", _PAGE)
        # 长文本限宽可读
        self.assertIn("word-break:break-word", _PAGE)


class TestPreviewApi(unittest.TestCase):
    """新增的预习后端路由（dashboard.py 内，不阻塞 HTTP、错误不 500）。"""

    def test_options(self):
        st, d = _SRV.get_json("/api/preview/options", timeout=60)
        self.assertEqual(st, 200)
        self.assertIn("ok", d)
        if d.get("ok"):
            self.assertIn("enabled", d)
            self.assertIn("enabled", d["enabled"])
            self.assertIn("textbooks", d)
            self.assertIsInstance(d["textbooks"], list)

    def test_chapters_bad_and_ok(self):
        st, d = _SRV.get_json("/api/preview/chapters", timeout=60, doc_id=0)
        self.assertEqual(st, 200)
        self.assertFalse(d.get("ok"))
        self.assertIn("error", d)
        st, d = _SRV.get_json("/api/preview/chapters", timeout=60, doc_id=999999)
        self.assertEqual(st, 200, "不存在的教材也不能 500")
        self.assertFalse(d.get("ok"))

    def test_estimate_bad_and_progress(self):
        st, d = _SRV.get_json("/api/preview/estimate", timeout=60, doc_id=0, chapter=0)
        self.assertEqual(st, 200)
        self.assertFalse(d.get("ok"))
        st, d = _SRV.get_json("/api/preview/progress", timeout=60)
        self.assertEqual(st, 200)
        self.assertIsInstance(d, dict)
        self.assertIn("active", d)

    def test_doc_bad(self):
        st, d = _SRV.get_json("/api/preview/doc", timeout=60, doc_id=0, chapter=0)
        self.assertEqual(st, 200)
        self.assertFalse(d.get("ok"))
        self.assertIn("error", d)

    def test_start_refuses_without_enabled(self):
        st, d = _SRV.post_json("/api/preview/start", timeout=60,
                               payload={"doc_id": 0, "chapter": 0})
        self.assertEqual(st, 200, "start 接口不能 500")
        self.assertFalse(d.get("started"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
