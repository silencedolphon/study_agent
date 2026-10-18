"""M0-6a（文案/空态清理）+ M0-1（学习场景骨架）验收。

覆盖：
  · M0-6a：用户可见的开发者术语被清掉（旧文案断言消失 + 首页真实渲染文本零命中）；
    每个空态都有一个**真实可点**主按钮（图谱/笔记/复习/知识点/错题/名词/导图）；
    需要保留的高级说明收进 `<details>` 折叠区；知识点列表摘要用 `.kpsum` 2 行截断。
  · M0-1：设置中心有 A/B/C 学习场景三选一；localStorage 持久化；A/B 首屏默认总览、
    C 首屏默认复习；C 精简导航（隐藏图谱/笔记）；切回 A 恢复全部 8 个页签。

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
        self.assertIn("sceneSettingsHtml()", _PAGE_CODE)

    def test_empty_state_cta_buttons(self):
        """每个空态都有真实可点主按钮（id 存在且绑了真实动作）。"""
        ctas = {
            "kp-create-toggle": "新建知识点（知识点页空态）",
            "mk-new": "＋ 记一道错题（错题页）",
            "gr-go-kps": "去「知识点」新建（图谱空态）",
            "nt-go-kps": "去「知识点」写笔记（笔记空态）",
            "rv-go-kps": "去「知识点」新建（复习空态）",
            "ov-go-review": "去「复习」开始（总览空态）",
            "term-go-build": "去整理这份课件（名词解释空态）",
            "mm-go-build": "去整理（思维导图未整理）",
        }
        for cid, why in ctas.items():
            self.assertIn(f'id="{cid}"', _PAGE, f"缺少按钮：{why}")
        for action in ("go('kps')", "go('review')", "openMistakeForm(", "openKpBuild("):
            self.assertIn(action, _PAGE, f"空态按钮没有绑定真实动作：{action}")

    def test_overview_rendered_text_clean(self):
        """真实渲染的首页（场景 A 默认总览）可见文本：开发者术语 0 命中。"""
        dom = env.edge_dump_dom(_SRV.url, timeout=90, virtual_time_ms=6000)
        text = _visible_text(dom)
        self.assertGreater(len(text), 100, f"首页渲染文本过短，DOM 可能没渲染：{text[:120]}")
        hits = [p for p in FORBIDDEN_PATTERNS if p in text]
        self.assertEqual(hits, [], f"首页可见文本仍有开发者术语：{hits}")
        # 空态主按钮在真实渲染里可见可用（总览的"最薄弱知识点"空态）
        self.assertIn('id="ov-go-review"', dom, "总览空态主按钮未渲染")

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


class TestScenePreset(unittest.TestCase):
    """M0-1：学习场景骨架（A 日常 / B 备考 / C 冲刺）。"""

    def test_scene_definitions(self):
        """三种场景定义齐全；A/B 首屏总览，C 首屏复习且精简导航。"""
        self.assertIn("const SCENE_KEY = 'kb_study_scene'", _PAGE,
                      "场景持久化应使用 localStorage 键 kb_study_scene")
        self.assertIn("localStorage.getItem(SCENE_KEY)", _PAGE)
        self.assertIn("localStorage.setItem(SCENE_KEY", _PAGE)
        for code in ("A:", "B:", "C:"):
            self.assertIn(code, _PAGE, f"缺少场景 {code}")
        self.assertIn("defaultView: 'overview'", _PAGE)
        self.assertIn("defaultView: 'review'", _PAGE)
        # C 场景精简导航：隐藏图谱与笔记（2 项），保留问答/资料/知识点/复习
        c_start = _PAGE.index("C: {name: '冲刺'")
        c_block = _PAGE[c_start:_PAGE.index("};", c_start)]
        self.assertNotIn("'graph'", c_block, "C 场景应隐藏图谱")
        self.assertNotIn("'notes'", c_block, "C 场景应隐藏笔记")
        for keep in ("'review'", "'assistant'", "'docs'", "'kps'"):
            self.assertIn(keep, c_block, f"C 场景应保留 {keep}")
        # A 场景保留全部功能
        a_start = _PAGE.index("A: {name: '日常'")
        a_block = _PAGE[a_start:_PAGE.index("},", a_start)]
        for view in ("'overview'", "'assistant'", "'docs'", "'review'", "'kps'",
                     "'graph'", "'mistakes'", "'notes'"):
            self.assertIn(view, a_block, f"A 场景应保留 {view}")

    def test_scene_boot_and_switch(self):
        """启动按场景默认页进；顶栏有场景标识；设置中心有 A/B/C 三选一。"""
        self.assertIn("go(SCENES[SCENE].defaultView);", _PAGE,
                      "启动没有按场景默认视图进首屏")
        self.assertIn('id="scene-badge"', _PAGE, "顶栏缺少场景标识")
        self.assertIn("function cycleScene()", _PAGE, "顶栏场景标识未绑定切换")
        self.assertIn("function navViews()", _PAGE, "导航未按场景过滤/排序")
        self.assertIn("function sceneSettingsHtml()", _PAGE,
                      "设置中心缺少学习场景三选一")
        for code in ("A · 日常", "B · 备考", "C · 冲刺"):
            self.assertIn(code, _PAGE, f"设置中心缺少场景按钮：{code}")
        # 切换场景时若当前页被隐藏，回到该场景默认页（不丢功能、不空白）
        self.assertIn("cfg.views.indexOf(CURRENT) < 0", _PAGE,
                      "切换场景后应处理被隐藏的当前页")


if __name__ == "__main__":
    unittest.main(verbosity=2)
