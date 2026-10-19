"""特性1：知识点小窗左侧思维导图栏（只增不改）验收。

覆盖：
  · 结构：小窗渲染出 `<aside id="mmkp-tree">`；新函数 mmSideData/mmSideTree/
    mmSidebarBind/mmSidebarPaint 存在；当前知识点按 kp_id 高亮；点左侧叶子复用
    mmOpenKp 就地切内容（不关窗）；同一 doc 的导图数据按缓存只取一次；
  · 响应式：宽屏常驻（.evpop.mmkp-has-side 加宽）、<900px 默认收起 + 「☰ 导图」
    抽屉按钮、头部压在抽屉上方保证关闭按钮可点；
  · 原有功能不退化：小窗「上一/下一知识点、记入笔记、查看笔记、在资料库中检索、
    展开当页课件」与 Esc/点外关闭机制全部仍在；
  · 降级：沙箱没有整理产物时 /api/mindmap 正常返回 has_structure=false，
    前端有"一句人话 + 自动收起"的路径，右侧正文不受影响。

隔离：用 `tests/env.py` 的沙箱服务 + 数据副本，不碰真实数据。
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
    _SRV = env.Server(tag="mmside")
    _SRV.start()
    r = _SRV.get("/", timeout=60)
    assert r.status == 200, f"首页返回 {r.status}"
    _PAGE = r.text


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


class TestKpSidebar(unittest.TestCase):
    """特性1：左侧导图栏（新增量）。"""

    def test_sidebar_markup_and_functions(self):
        """左侧栏渲染入口 + 数据/高亮/跳转/缓存函数都在。"""
        for probe in (
            '<aside class="mmkp-side" id="mmkp-tree">',   # 小窗左侧栏容器
            "function mmSideData(",                        # 按 doc 取导图（带缓存）
            "function mmSideTree(",                        # 递归渲染导图
            "function mmSidebarBind(",                     # 左侧叶子点击绑定
            "function mmSidebarPaint(",                    # 渲染 + 高亮 + 定位
            "/api/mindmap?doc_id=",                        # 复用的既有接口
            "mms-kp",                                      # 可点知识点节点类
        ):
            self.assertIn(probe, _PAGE, f"缺少左侧栏实现：{probe}")
        # 高亮：按 d.kp_id 与本条知识点 id 匹配
        self.assertIn("String(id) === String(curId) ? ' on'", _PAGE,
                      "左侧栏没有按 kp_id 高亮当前知识点的逻辑")
        # 点击叶子 = 复用 mmOpenKp 就地换右侧内容（不关窗、不新开浮层）
        self.assertIn("mmOpenKp(kpId, a.dataset.name || kpId, MMPOP.anchor, false, docId)",
                      _PAGE, "左侧叶子没有接到既有 mmOpenKp 跳转入口")
        # 每次右侧重渲染后重新挂载（小窗内容重建会带走左侧栏）
        self.assertIn("mmSidebarPaint(d, el);", _PAGE)
        # 缓存：同一 doc 只请求一次（存 Promise，点不同节点不重复请求）
        self.assertIn("if (!MMSIDE.cache[docId]){", _PAGE)
        self.assertIn("MMSIDE.cache[docId] = api('/api/mindmap?doc_id='", _PAGE)
        # 多知识点节点（lkp_ids>1）在左侧就地展开子项，不改变既有行为
        self.assertIn("ids.length > 1", _PAGE)

    def test_responsive_rules(self):
        """宽屏常驻 / 窄屏收起 + 抽屉按钮；抽屉压不遮关闭按钮。"""
        self.assertIn(".evpop.mmkp-has-side{width:min(940px", _PAGE,
                      "宽屏没有给左侧栏加宽")
        self.assertIn("@media (max-width:899px)", _PAGE, "缺少窄屏断点")
        self.assertIn(".evpop .mmkp-toggle{display:inline-flex", _PAGE,
                      "窄屏缺少「☰ 导图」切换按钮样式")
        self.assertIn(".evpop.mmkp-has-side.mm-tree-open .mmkp-side{display:block}", _PAGE,
                      "抽屉展开规则缺失")
        self.assertIn("width:min(260px,82vw)", _PAGE, "抽屉宽度未按窄屏收敛")
        self.assertIn(".evpop .mmkp-main .ph{position:relative;z-index:3", _PAGE,
                      "头部没有压在抽屉上方（关闭按钮会被遮挡）")
        # 左侧与右侧各自独立滚动
        self.assertIn(".evpop .mmkp-side{display:none;flex:0 0 224px;min-height:0;overflow:auto",
                      _PAGE, "左侧栏没有独立滚动")
        self.assertIn(".evpop .pbody{overflow:auto", _PAGE, "右侧正文滚动样式被改坏")

    def test_existing_popup_controls_kept(self):
        """原有小窗控件与关闭机制一个都不许少（只增不改）。"""
        for cid in ("mmkp-prev", "mmkp-next", "mmkp-note", "mmkp-notes",
                    "mmkp-search", "mmkp-deck", "mmkp-more"):
            self.assertIn(f'id="{cid}"', _PAGE, f"原有小窗控件丢失：{cid}")
        for fn in ("async function mmNextKp(", "async function mmPrevKp(",
                   "function mmNoteEdit(", "function mmNoteList(",
                   "function evPopClose(", "function evPopMount(",
                   "function mmKpScrollGuard("):
            self.assertIn(fn, _PAGE, f"原有小窗函数丢失：{fn}")
        # Esc / 点外关闭保持原机制（没有被左侧栏改写）
        self.assertIn("document.addEventListener('keydown', evPopEsc)", _PAGE)
        self.assertIn("document.addEventListener('click', evPopOutside, true)", _PAGE)
        # 右侧导图点节点仍走原入口（没有为了左栏改掉原行为）
        self.assertIn("mmOpenKpList(n, el)", _PAGE)

    def test_degrades_without_artifact(self):
        """沙箱无整理产物：/api/mindmap 正常返回空态，前端有一句人话+收起路径。"""
        st, d = _SRV.get_json("/api/mindmap", timeout=60, doc_id=2)
        self.assertEqual(st, 200, f"/api/mindmap 返回 {st}")
        self.assertIsInstance(d, dict)
        self.assertIn("has_structure", d)
        self.assertFalse(d.get("has_structure"), "沙箱没有产物时不应假装有结构树")
        self.assertIn("这份课件还没有可显示的整理导图", _PAGE,
                      "左侧栏缺少「取不到/无产物」时的人话降级")
        self.assertIn("classList.remove('mmkp-has-side', 'mm-tree-open')", _PAGE,
                      "无产物时没有收起的路径")


if __name__ == "__main__":
    unittest.main(verbosity=2)
