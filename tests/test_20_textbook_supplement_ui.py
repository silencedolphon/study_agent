"""task-27：知识点小窗右侧教材补充栏 + 导图栏「补做教材补充」提醒（前端验收）。

覆盖：
  · 小窗三段式结构：左树 + 正文 + 右侧 `<aside id="mmkp-teach">`；窄屏「📖 教材」抽屉；
  · 右侧栏数据/状态：教材名、章名/页码、≤3 张教材页图（/api/teach-page 懒加载）、
    补充段落（页码/文本/命中词）、教材补充点 chips、底部仅供参考；无教材/需补做/无匹配
    三种人话状态；
  · 导图栏提醒：needs_backfill 时横幅 + 「补做教材补充」按钮（POST build → 就绪）；
    已有产物不打扰、无教材不显示；
  · 后端胶水：4 个路由（status/kp/build/teach-page 二进制），整理完成 on_done 回调；
    参数错误/未就绪一律 JSON 200/4xx，不 500；
  · 原有功能不退化：小窗左树/正文/笔记/上一/下一/课件按钮仍在，其它页签不变。
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
    _SRV = env.Server(tag="teachui")
    _SRV.start()
    r = _SRV.get("/", timeout=60)
    assert r.status == 200, f"首页返回 {r.status}"
    _PAGE = r.text


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


class TestPopupTeachSidebar(unittest.TestCase):
    """知识点小窗右侧教材补充栏。"""

    def test_three_column_structure(self):
        for probe in (
            '<aside class="mmkp-teach" id="mmkp-teach">',
            'class="mmkp-teachbtn" id="mmkp-teachbtn"',
            'mmkp-has-teach',
            'mm-teach-open',
            'function mmTeachPaint(',
            'function mmTeachToggle(',
            'function mmTeachClose(',
            'mmTeachPaint(d, el);',
            "// 特性2：右侧「教材补充」栏",
        ):
            self.assertIn(probe, _PAGE, f"缺少右侧栏实现：{probe}")
        # 原有左树 / 正文 / 底部控件一个不少
        for probe in ('id="mmkp-tree"', 'id="mmkp-body"', 'id="mmkp-prev"',
                      'id="mmkp-next"', 'id="mmkp-note"', 'id="mmkp-notes"',
                      'id="mmkp-search"', 'id="mmkp-deck"'):
            self.assertIn(probe, _PAGE, f"原有小窗控件丢失：{probe}")

    def test_content_parts(self):
        for probe in (
            "教材补充", "mteach-pages", "mteach-pg", "mteach-tx",
            "mteach-hits", "mteach-term", "mteach-foot",
            "补充段落", "教材补充点",
            'img loading="lazy"',
            "/api/teach-page?doc_id=",
            "dpi=110", "dpi=150",
            "按教材章节切块匹配，仅供参考",
        ):
            self.assertIn(probe, _PAGE, f"右侧栏缺少内容块：{probe}")

    def test_three_status_branches(self):
        for probe in (
            "本学科尚未入库教材，教材补充暂不可用",
            "教材已入库，可到导图栏补做教材补充",
            "去导图栏补做",
            "这条知识点在教材里没有匹配到可补充的段落",
        ):
            self.assertIn(probe, _PAGE, f"缺少状态文案：{probe}")
        # 数据来源：只读接口（零模型）
        self.assertIn("/api/textbook-supplement/kp?doc_id=", _PAGE)
        self.assertIn("/api/textbook-supplement/status?doc_id=", _PAGE)

    def test_responsive_rules(self):
        self.assertIn("@media (min-width:1280px)", _PAGE)
        self.assertIn("@media (max-width:1279px)", _PAGE)
        self.assertIn(".evpop.mmkp-has-side.mmkp-has-teach{width:min(1280px", _PAGE)
        self.assertIn("width:min(340px,86vw)", _PAGE)
        self.assertIn(".evpop .mmkp-main .ph{position:relative;z-index:3", _PAGE,
                      "窄屏抽屉展开时头部（关闭按钮）必须压在抽屉上方")
        self.assertIn("padding-top:54px", _PAGE)


class TestMindmapBackfill(unittest.TestCase):
    """课件思维导图栏的「补做教材补充」提醒。"""

    def test_banner_and_build(self):
        for probe in (
            "function mmTeachBackfill(",
            "function mmTeachBuild(",
            "mmTeachBackfill(MINDMAP.docId);",
            "mmteach-banner",
            "mmteach-ready",
            "检测到教材《",
            "补做教材补充",
            "教材补充已就绪：",
            "不改动原知识点",
            "/api/textbook-supplement/build",
            "force: false",
        ):
            self.assertIn(probe, _PAGE, f"缺少导图栏提醒实现：{probe}")

    def test_banner_conditions(self):
        # 只在有 KP 产物 + 有教材 + needs_backfill 时显示；无教材/无产物不打扰
        for probe in ("!st.has_artifact", "!st.has_textbook", "st.needs_backfill",
                      "MMBACK.busy"):
            self.assertIn(probe, _PAGE, f"横幅触发条件不完整：{probe}")


class TestBackendGlue(unittest.TestCase):
    """dashboard.py 新增路由（源码静态）与错误降级（运行时）。"""

    def test_routes_present(self):
        src = (Path(__file__).resolve().parents[1] / "tools" / "dashboard.py"
               ).read_text(encoding="utf-8")
        for probe in (
            "/api/textbook-supplement/status",
            "/api/textbook-supplement/kp",
            "/api/textbook-supplement/build",
            "/api/teach-page",
            "def api_textbook_supplement_status(",
            "def api_textbook_supplement_kp(",
            "def api_textbook_supplement_build(",
            "def api_teach_page(",
            "def _teach_page(self, doc_id, page, dpi)",
            'self._send(200, png, "image/png")',
            "on_done=_teach_after_kp_build",
            "def _teach_after_kp_build(",
        ):
            self.assertIn(probe, src, f"缺少后端胶水：{probe}")
        # status/kp 在只读锁那组；teach-page 在图片路由（不进全局锁）
        self.assertIn('if path == "/api/teach-page":', src)
        self.assertIn('if path == "/api/textbook-supplement/status":', src)
        self.assertIn('if u.path == "/api/textbook-supplement/build":', src)

    def test_status_endpoint(self):
        st, d = _SRV.get_json("/api/textbook-supplement/status", timeout=60, doc_id=1)
        self.assertEqual(st, 200)
        self.assertIn("ok", d)
        for key in ("has_artifact", "has_textbook", "textbooks", "needs_backfill"):
            self.assertIn(key, d, f"状态接口缺字段 {key}")
        # 参数错误也是 200 JSON
        st, d = _SRV.get_json("/api/textbook-supplement/status", timeout=60, doc_id=0)
        self.assertEqual(st, 200)
        self.assertFalse(d.get("ok"))

    def test_kp_endpoint(self):
        st, d = _SRV.get_json("/api/textbook-supplement/kp", timeout=60,
                              doc_id=1, kp_id="KP-1")
        self.assertEqual(st, 200)
        self.assertIn("available", d)
        st, d = _SRV.get_json("/api/textbook-supplement/kp", timeout=60, doc_id=0, kp_id="")
        self.assertEqual(st, 200)
        self.assertFalse(d.get("ok"))

    def test_build_endpoint(self):
        st, d = _SRV.post_json("/api/textbook-supplement/build", timeout=60,
                               payload={"doc_id": 0})
        self.assertEqual(st, 200)
        self.assertFalse(d.get("ok"))
        st, d = _SRV.post_json("/api/textbook-supplement/build", timeout=120,
                               payload={"doc_id": 999999})
        self.assertEqual(st, 200, "不存在的课件也不能 500")
        self.assertFalse(d.get("ok"))

    def test_teach_page_errors(self):
        st, d = _SRV.get_json("/api/teach-page", timeout=60, doc_id=0, page=0)
        self.assertEqual(st, 400, "非法参数必须是 400 JSON")
        self.assertIn("error", d)
        st, d = _SRV.get_json("/api/teach-page", timeout=60, doc_id=999999, page=1)
        self.assertIn(st, (400, 403, 404), "不存在的教材要 4xx JSON，不能 500")
        self.assertIn("error", d)


class TestOtherPagesIntact(unittest.TestCase):
    def test_nav_and_other_features(self):
        labels = ["问答", "总览", "复习", "预习", "资料", "知识点", "错题", "笔记"]
        for lb in labels:
            self.assertIn(lb, _PAGE, f"其它页签丢失：{lb}")
        # 上一个特性（右教材栏）之外，小窗笔记/上一/下一等交互仍绑定
        for probe in ("function mmNoteEdit(", "async function mmNextKp(",
                      "async function mmPrevKp(", "function mmToggleDeck("):
            self.assertIn(probe, _PAGE, f"原有功能丢失：{probe}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
