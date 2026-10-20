"""设置中心改版（task-21）验收：只改前端展示/交互，后端契约与保存语义不变。

覆盖（静态断言，页面即前端源码）：
  · 导航：5 条管线分「AI 能力 / 资料」两组、每项状态点、标题动态「⚙ 设置中心 · N 条管线」、
    不再出现"四条管线"；
  · 入库页重排：「入库方式」卡片 + 整页切块开关置顶 + 动态模式横幅、识图卡片+说明、
    「AI 结构分析（旧模式专用）」折叠 + "仅当整页切块关闭时生效" + 灰化、连接与密钥、
    提示词说明改为"旧模式"；全部字段靠 data-k 统一渲染（不丢字段/不改 key）；
  · 其它管线：连接与密钥 → 参数与开关 → 高级（折叠）→ 提示词 的卡片化；
  · 功能增强：左栏搜索（命中高亮/无匹配提示/不清 DOM）、footer 未保存计数 + 无改动
    禁用保存、切页确认 / Esc / 密钥留空 / 保存后 firstrun 刷新等语义保持；
  · 排版：宽 min(1180px,94vw) / 高 min(86vh,820px)、左栏 220px、<760px 近全屏 +
    横向 chips + 单列 + footer 换行；
  · 接口侧：/api/settings 后端契约未变（5 条管线、字段 key/type/advanced 原样）。
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
    _SRV = env.Server(tag="settingsui")
    _SRV.start()
    r = _SRV.get("/", timeout=60)
    assert r.status == 200, f"首页返回 {r.status}"
    _PAGE = r.text


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


class TestSettingsNav(unittest.TestCase):
    """导航与信息架构。"""

    def test_dynamic_title_and_groups(self):
        # 标题动态：N 由数据算，不再是写死的"四条管线"
        self.assertIn("<h2>⚙ 设置中心 · ' + pipes.length + ' 条管线", _PAGE)
        self.assertNotIn("四条管线", _PAGE, "页面里仍有过期的\"四条管线\"文案")
        self.assertIn("只保存在本机 · 保存后立即生效", _PAGE)
        # 两组导航
        self.assertIn("const SET_GROUPS", _PAGE)
        self.assertIn("AI 能力", _PAGE)
        self.assertIn("{title: '资料'", _PAGE)
        self.assertIn("ids: ['chat', 'kp', 'deck', 'preview']", _PAGE)

    def test_pipeline_status_dots(self):
        # 每项右侧状态点，按 /api/settings 的 secret 字段 set 状态判断；取不到就不显示
        self.assertIn("function setPipeKeyState(", _PAGE)
        self.assertIn("f.type === 'secret'", _PAGE)
        self.assertIn("sn-dot", _PAGE)
        self.assertIn("sec.set ? 'on' : 'off'", _PAGE)


class TestIngestLayout(unittest.TestCase):
    """入库页重排（最关键）。"""

    def test_mode_card_first_and_banner(self):
        self.assertIn("function setIngestPaneHtml(", _PAGE)
        self.assertIn("INGEST_PAGE_CHUNK", _PAGE)
        self.assertIn("入库方式", _PAGE)
        # 开关置顶：入库方式卡片里第一个 data-k 就是它（浏览器实测也验过）
        self.assertIn("setFieldHtml(pageChunk)", _PAGE)
        # 两种模式的动态横幅文案
        self.assertIn("当前：整页切块 · 每页一块", _PAGE)
        self.assertIn("不跑 AI 结构分析、不建独立识图块", _PAGE)
        self.assertIn("当前：旧模式 · AI 结构分析 + 跨页语义切块 + 独立识图块", _PAGE)
        self.assertIn("function setIngestModePaint(", _PAGE)

    def test_vision_and_structure_cards(self):
        # 识图卡片：6 个字段 + 说明
        for key in ("DEEPSEEK_VISION_MODEL", "FIGURE_DESC", "FIGURE_THINKING",
                    "FIGURE_REASONING_EFFORT", "FIGURE_RENDER_DPI",
                    "FIGURE_MAX_CALLS_PER_DOC"):
            self.assertIn(key, _PAGE, f"识图卡片缺少字段 {key}")
        self.assertIn("pptx 只对有图页调用；PDF 暂按原口径全页", _PAGE)
        # 结构分析：默认折叠 + 仅旧模式生效 + 当前不生效徽标 + 仍可改可保存
        self.assertIn("AI 结构分析（旧模式专用）", _PAGE)
        self.assertIn("仅当整页切块关闭时生效", _PAGE)
        self.assertIn("当前不生效", _PAGE)
        self.assertIn("details class=\"set-adv set-adv-in\"", _PAGE)
        for key in ("INGEST_MODEL", "OUTLINE_ANALYSIS", "OUTLINE_THINKING"):
            self.assertIn(key, _PAGE, f"结构分析卡片缺少字段 {key}")
        self.assertIn("开关值仍可改、可保存", _PAGE)
        # 提示词说明
        self.assertIn("旧模式的结构分析提示词；整页切块模式不使用", _PAGE)

    def test_no_field_dropped(self):
        """所有字段统一经 setFieldHtml（data-k=f.key）渲染；未知字段有兜底卡片。"""
        self.assertIn('\'data-k="\' + f.key + \'"\'', _PAGE,
                      "字段没有统一用 data-k 渲染（可能丢字段/改 key）")
        self.assertIn("其它参数", _PAGE, "没有未知字段的兜底分组")
        self.assertIn("const rest = (p.fields || []).filter(f => !used.has(f.key))", _PAGE)


class TestGenericPipelines(unittest.TestCase):
    """其它管线卡片化：连接与密钥 → 参数与开关 → 高级 → 提示词。"""

    def test_card_order(self):
        idx_conn = _PAGE.index("setCard('连接与密钥'")
        idx_sw = _PAGE.index("setCard('参数与开关'")
        idx_adv = _PAGE.index("高级参数（")
        idx_prompt = _PAGE.index("setCard('提示词'")
        self.assertLess(idx_conn, idx_sw, "卡片顺序应为 连接与密钥 → 参数与开关")
        self.assertLess(idx_sw, idx_adv)
        self.assertLess(idx_adv, idx_prompt)
        # 原来的分组逻辑保留：str/secret 进连接；其余非高级进参数；advanced 进折叠
        self.assertIn("(f.type === 'str' || f.type === 'secret')", _PAGE)
        self.assertIn("f.advanced", _PAGE)
        # 提示词区域没有删（kp 的 3 个提示词卡 + 内建提示词入口仍在）
        self.assertIn("setPromptsHtml(p)", _PAGE)
        self.assertIn("promptOpen(", _PAGE)
        self.assertIn("promptOpenBuiltin(", _PAGE)


class TestFrontendEnhancements(unittest.TestCase):
    """搜索 / 未保存计数 / 原有语义保持。"""

    def test_search(self):
        self.assertIn('id="set-q"', _PAGE, "左栏缺少搜索框")
        self.assertIn("function setSearchApply(", _PAGE)
        self.assertIn("set-qinfo", _PAGE)
        self.assertIn("没有匹配项", _PAGE)
        self.assertIn("data-search", _PAGE)
        self.assertIn("row.classList.toggle('hit'", _PAGE, "命中没有高亮")
        # 搜索不能影响保存：隐藏但不删 DOM（setCollect 仍读到隐藏字段）
        self.assertIn("row.style.display = ok ? '' : 'none'", _PAGE)
        self.assertIn("不删 DOM", _PAGE)

    def test_unsaved_counter_and_save_guard(self):
        self.assertIn('id="set-unsaved"', _PAGE)
        self.assertIn("function setCounter(", _PAGE)
        self.assertIn("项未保存", _PAGE)
        self.assertIn("无未保存改动", _PAGE)
        self.assertIn("btn.disabled = (n === 0) || !!SET_BUSY", _PAGE)
        # footer 保留 保存 / 本区恢复默认 / 重新读取 / 状态文案
        self.assertIn('id="set-save"', _PAGE)
        self.assertIn("本区恢复默认", _PAGE)
        self.assertIn("重新读取", _PAGE)
        self.assertIn('id="setstat"', _PAGE)

    def test_original_semantics_kept(self):
        # 切页未保存确认
        self.assertIn("confirmBox('有未保存的改动'", _PAGE)
        # 密钥留空语义
        self.assertIn("留空＝不改动", _PAGE)
        self.assertIn("留空表示不改动", _PAGE)
        # 保存后 firstrun 横幅刷新
        self.assertIn("loadFirstrun(true)", _PAGE)
        # Esc / 点外关闭仍走原来的全局绑定（没被这次改版改掉）
        self.assertIn("if (e.key === 'Escape' && $('#setmodal').classList.contains('on'))",
                      _PAGE)
        self.assertIn("if (e.target && e.target.id === 'setmodal') closeSettings();", _PAGE)


class TestResponsiveLayout(unittest.TestCase):
    """宽屏加宽 / 窄屏近全屏 + 横向 chips + 单列 + footer 换行。"""

    def test_wide_size(self):
        self.assertIn("width:min(1180px,94vw)", _PAGE)
        self.assertIn("max-height:min(86vh,820px)", _PAGE)
        self.assertIn(".set-nav{flex:0 0 220px", _PAGE)
        self.assertIn(".set-pane{flex:1;min-width:0;overflow:auto", _PAGE)
        self.assertIn(".set-foot{display:flex", _PAGE)

    def test_narrow_breakpoint(self):
        self.assertIn("@media (max-width:760px)", _PAGE)
        self.assertIn(".setbox{width:94vw;height:92vh;max-height:92vh", _PAGE)
        self.assertIn(".set-nav{flex:0 0 auto;flex-direction:row;align-items:center;overflow-x:auto",
                      _PAGE)
        self.assertIn(".srow{grid-template-columns:1fr", _PAGE)
        self.assertIn(".set-foot .sstat{flex:1 1 100%", _PAGE)
        # 390 无横向溢出的兜底：长串断行 + 控件限宽
        self.assertIn("word-break:break-word", _PAGE)
        self.assertIn("max-width:440px", _PAGE)


class TestBackendContractUnchanged(unittest.TestCase):
    """后端 /api/settings 契约不变：5 条管线、字段 key/type/advanced 原样。"""

    def test_api_settings_shape(self):
        st, d = _SRV.get_json("/api/settings", timeout=60)
        self.assertEqual(st, 200, f"/api/settings 返回 {st}")
        pipes = d.get("pipelines") or []
        self.assertEqual([p["id"] for p in pipes], ["chat", "kp", "ingest", "deck", "preview"])
        for p in pipes:
            self.assertTrue(p.get("fields"), f"{p['id']} 没有字段")
            for f in p["fields"]:
                for key in ("key", "type", "label"):
                    self.assertIn(key, f, f"{p['id']} 字段缺 {key}")
        # 入库模式开关仍在字段里，且默认开着（整页切块）
        ingest = next(p for p in pipes if p["id"] == "ingest")
        mode = next(f for f in ingest["fields"] if f["key"] == "INGEST_PAGE_CHUNK")
        self.assertEqual(mode["type"], "bool")
        self.assertEqual(str(mode["value"]), "1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
