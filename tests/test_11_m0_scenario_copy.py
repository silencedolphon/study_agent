"""M0-6a（文案/空态清理）验收 —— 回退后保留的排版/文案改动。

用户实测后 M0 的功能型新增（场景预设 M0-1、新建知识点入口 M0-2、错题闭环 M0-4、
导入看板入口 M0-6b）已回退；**M0-6a 的文案/排版/空态 CTA/摘要截断保留**。
覆盖：
  · 用户可见的开发者术语被清掉（旧文案断言消失 + 首页真实渲染文本零命中）；
  · 空态仍有真实可点主按钮（图谱/笔记/复习/总览/名词/导图；错题/知识点页的
    功能型按钮已随回退移除）；
  · 需要保留的高级说明收进 `<details>` 折叠区；知识点列表摘要 `.kpsum` 2 行截断；
  · 回退负向断言：已撤销的入口/场景/导入按钮不再出现在页面里。

隔离：用 `tests/env.py` 的沙箱服务 + 数据副本；关掉 AI 的两条长链路不触发（不调模型）。
页面文字判断分两层：静态断言（源码里的旧文案与折叠区/CTA 结构）+ 首页真实渲染文本。
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None
_PAGE = ""
_PAGE_CODE = ""

# 曾经出现在用户可见区域的开发者术语（M0-6a 要清掉的原文；出现即失败）
OLD_DEV_SENTENCES = [
    "不再读入库导图",                       # 知识点页批量整理卡
    "产物只写到 logs/knowledge/",            # 同上
    "写入：logs/knowledge/",                 # 批量整理弹窗汇总
    "卡 C-0058 起是",                        # 名词解释卡标题
    "tools/terms_build.py --doc",            # 名词解释空态
    "抗上下文刷新的长期记忆",                # 总览/笔记空态
    "让助手从资料中生成新的知识点",          # 复习空态（指向不存在的入口）
    "复习答错时让助手记录",                  # 错题空态（指向不存在的入口）
    "（卡 C-0084）",                         # 资料页删除说明
]

# 这些是用户可见文本里绝不该出现的模式（首页真实渲染文本检查用）
FORBIDDEN_PATTERNS = ["logs/", "tools/", "卡 C-", "doc_id", ".json",
                      "<doc_id>", "抗上下文刷新", "terms_build"]


def _strip_js_comments(page: str) -> str:
    """去掉内嵌 JS 的 `//` 行注释（注释里的旧术语不算用户可见文案）。

    只去行注释，不去块注释：JS 里块注释的边界与模板字符串里的 `/`、`*`
    容易误判，行注释足够覆盖"旧文案只存在于注释里"的情形。
    """
    m = re.search(r"<script>([\s\S]*)</script>", page)
    if not m:
        return page
    js = re.sub(r"^\s*//.*$", "", m.group(1), flags=re.M)
    return page[:m.start()] + js + page[m.end():]


def setUpModule():
    global _SRV, _PAGE, _PAGE_CODE
    env.prepare(clean_data=True)
    _SRV = env.Server(tag="m0ui")
    _SRV.start()
    r = _SRV.get("/", timeout=60)
    assert r.status == 200, f"首页返回 {r.status}"
    _PAGE = r.text
    _PAGE_CODE = _strip_js_comments(_PAGE)


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


def _visible_text(dom: str) -> str:
    """DOM → 可见文本近似：去掉 script/style/注释与标签。"""
    dom = re.sub(r"<script[\s\S]*?</script>", " ", dom)
    dom = re.sub(r"<style[\s\S]*?</style>", " ", dom)
    dom = re.sub(r"<!--[\s\S]*?-->", " ", dom)
    dom = re.sub(r"<[^>]+>", " ", dom)
    return re.sub(r"\s+", " ", dom)


class TestCopyCleanup(unittest.TestCase):
    """M0-6a：文案/空态清理。"""

    def test_old_dev_sentences_removed(self):
        """曾经在页面上的开发者术语/死链文案，一个都不许回来（忽略 JS 注释）。"""
        for old in OLD_DEV_SENTENCES:
            self.assertNotIn(old, _PAGE_CODE, f"页面里仍有旧文案：{old!r}")
        # 高级说明必须是折叠的（保留信息但默认收起），不能又平铺回页面
        self.assertIn('<details class="adv">', _PAGE_CODE,
                      "高级说明应放进 <details class=\"adv\"> 折叠区")
        # 设置项的说明文字要经过 humanize 过滤（后端元数据里带模块名/文件名）
        self.assertIn("function humanize(", _PAGE_CODE, "缺少设置说明的术语过滤函数")
        for anchor in ("setFieldHtml", "setPaneHtml"):
            fn = _PAGE_CODE[_PAGE_CODE.index("function " + anchor):
                            _PAGE_CODE.index("function ", _PAGE_CODE.index("function " + anchor) + 10)]
            self.assertIn("humanize(", fn, f"{anchor} 未过滤开发者术语")
        # 设置窗口本体：密钥说明是固定人话（不再渲染后端元数据），面板走过滤后的 setPaneHtml
        self.assertIn("API 密钥只保存在本机", _PAGE_CODE)
        self.assertIn("setPaneHtml(cur)", _PAGE_CODE)

    def test_empty_state_cta_buttons(self):
        """空态仍有真实可点主按钮（id 存在且绑了真实动作）。"""
        ctas = {
            "nt-go-kps": "去「知识点」页（笔记空态）",
            "rv-go-kps": "去「知识点」页（复习空态）",
            "ov-go-review": "去「复习」开始（总览空态）",
            "term-go-build": "去整理这份课件（名词解释空态）",
            "mm-go-build": "去整理（思维导图未整理）",
        }
        for cid, why in ctas.items():
            self.assertIn(f'id="{cid}"', _PAGE, f"缺少按钮：{why}")
        for action in ("go('kps')", "go('review')", "openKpBuild("):
            self.assertIn(action, _PAGE, f"空态按钮没有绑定真实动作：{action}")
        # 知识点页的空态主按钮是「选择课件批量整理」（新建入口已回退）
        self.assertIn('id="kp-build-open"', _PAGE)

    def test_removed_m0_entries_gone(self):
        """回退负向断言：场景 / 新建知识点 / 记错题 / 导入按钮都不再出现。"""
        for gone in ('id="scene-badge"', "SCENE_KEY", "sceneSettingsHtml(",
                     'id="kp-create-toggle"', "kpCreateCard(",
                     'id="kp-add-mistake"', "openMistakeForm(",
                     'id="mk-new"', 'id="kp-import-open"', "kpImportOpen(",
                     "/api/kp-import/preview"):
            self.assertNotIn(gone, _PAGE, f"已回退的入口又出现了：{gone}")

    def test_overview_rendered_text_clean(self):
        """真实渲染的首页（M0 前默认问答页）可见文本：开发者术语 0 命中。"""
        dom = env.edge_dump_dom(_SRV.url, timeout=90, virtual_time_ms=6000)
        text = _visible_text(dom)
        self.assertGreater(len(text), 100, f"首页渲染文本过短，DOM 可能没渲染：{text[:120]}")
        hits = [p for p in FORBIDDEN_PATTERNS if p in text]
        self.assertEqual(hits, [], f"首页可见文本仍有开发者术语：{hits}")
        # 回退后默认首屏 = 问答页，且不再有场景标识
        self.assertNotIn('id="scene-badge"', dom, "场景标识应已回退")

    def test_kp_summary_truncated(self):
        """知识点列表摘要 2 行截断（视觉截断、详情仍看全文）。"""
        self.assertIn(".kpsum{display:-webkit-box;-webkit-line-clamp:2", _PAGE)
        start = _PAGE.index("async function viewKps()")
        end = _PAGE.index("// ---------------- 批量整理知识点", start)
        self.assertIn('<div class="small muted kpsum">', _PAGE[start:end],
                      "知识点列表没有使用 kpsum 截断类")
        # 后端仍返回全文（截断只在视觉层，点开详情能看全文）
        kp_id = int(_SRV.post_json("/api/kp", {
            "name": "M0-6a-长摘要验收", "subject": "药理学",
            "summary": "很长的摘要" * 40})[1]["kp_id"])
        kps = _SRV.get_json("/api/kps")[1]["knowledge_points"]
        kp = next(k for k in kps if k["id"] == kp_id)
        self.assertGreater(len(kp["summary"] or ""), 80, "后端摘要应保持全文")


if __name__ == "__main__":
    unittest.main(verbosity=2)
