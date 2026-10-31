"""task-40：设置中心「AI 教材知识点」+ 全管线 token 上限（0=无上限）+ 教材知识点进度条。

前端资产拆分后（core/frontend/*.js 由 tools/dashboard.py 运行时拼接）做**静态断言**：
  · 95_settings.js：SET_GROUPS/SET_INTRO 含 textbook_kp；通用 pane 能渲染新管线；
    setPromptsHtml 对 textbook_kp 走"内置提示词"只读卡、**不会**落到 ingest_outline；
    所有 `*_MAX_TOKENS` 字段（无论后端是否 advanced）统一抽到「Token 上限」卡片、
    默认可见，0 显示「0 = 无上限」，保存/搜索仍走 data-k/data-orig 不变。
  · 52_textbook_knowledge.js：进度条 .pbar + percent/done/total/elapsed 文案、
    结束/失败状态、bookPoll 1.5s 刷新、BOOKKP.awaiting 保留；
    开始确认框读设置的 model/effort/max cap（0 = 无上限）。
  · style.css 有进度条与 token 提示样式；manifest.json 加载这两个脚本。
不真实调 AI、不启动服务、不改后端。
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FE = ROOT / "core" / "frontend"


def read(name):
    return (FE / name).read_text(encoding="utf-8")


class TestSettingsTextbookKp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = read("95_settings.js")

    def test_group_and_intro(self):
        for probe in (
            "SET_GROUPS", "textbook_kp",
            "'chat', 'kp', 'preview', 'textbook_kp', 'deck'",
            "AI 教材知识点" if False else "设置教材按章整理知识点",
            "textbook_kp: '设置教材按章整理知识点",
        ):
            self.assertIn(probe, self.js, f"设置分组/文案缺少：{probe}")

    def test_prompts_not_ingest_outline(self):
        self.assertIn("if (p.id === 'textbook_kp')", self.js)
        self.assertIn("这条管线使用内置提示词", self.js)
        # 内置提示词只读卡要在"fallback 到 ingest_outline"之前早退
        pos_kp = self.js.index("if (p.id === 'textbook_kp')")
        pos_fallback = self.js.index("['ingest_outline']")
        self.assertLess(pos_kp, pos_fallback, "textbook_kp 必须在 ingest_outline 兜底之前早退")

    def test_token_cap_visible_and_zero_means_unlimited(self):
        for probe in (
            "SET_TOKEN_RE", "_MAX_TOKENS$", "setIsTokenField", "setTokenFields",
            "0 = 无上限", "setTokenNoteHtml",
            "Token 上限（0 = 无上限）", "SET_TOKEN_CARD_NOTE",
            "setRows(tokenFs)", "tokenKeys",
        ):
            self.assertIn(probe, self.js, f"token 上限展示缺少：{probe}")
        # 通用 pane：token 字段先抽出，再算 conn/sw/adv（不藏在 advanced 里）
        generic = self.js[self.js.index("function setPaneHtml(p){"):]
        generic = generic[:generic.index("function setNavHtml(")]
        self.assertIn("const tokenFs = setTokenFields(p);", generic)
        self.assertIn("const adv = others.filter(f => f.advanced);", generic)
        self.assertLess(generic.index("tokenFs"), generic.index("const adv"))
        # 入库 pane 也必须把 ingest 下的 token 字段统一成卡片、且不进「其它参数」
        ingest = self.js[self.js.index("function setIngestPaneHtml(p){"):self.js.index("function setPaneHtml(")]
        self.assertIn("setTokenFields(p)", ingest)
        self.assertIn("!tokenKeys.has(f.key)", ingest)

    def test_save_search_unchanged(self):
        for probe in ("setCollect", "data-k", "data-orig", "setSearchApply",
                      "setCounter", "项未保存"):
            self.assertIn(probe, self.js, f"保存/搜索/未保存计数缺少：{probe}")


class TestTextbookKpProgress(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = read("52_textbook_knowledge.js")
        cls.css = read("style.css")

    def test_progress_bar_and_text(self):
        for probe in (
            "bookPercent", "bookFmtElapsed", "bookProgressHtml", "bookProgressMatch",
            '<div class="pbar active"><i style="width:${percent}%"></i></div>',
            "%</b> · ${done}/${total || 0} 段 · 已用", "data-book-progress",
            "已用", "percent", "elapsed", "p.label",
        ):
            self.assertIn(probe, self.js, f"进度条缺少：{probe}")

    def test_done_failed_and_poll(self):
        for probe in (
            "p.status === 'done'", "p.status === 'failed'",
            "book-kp-progress done", "上次未完成",
            "setTimeout(bookPoll, 1500)", "BOOKKP.awaiting",
            "/api/textbook-kp/progress", "/api/textbook-kp/estimate",
            "/api/textbook-kp/start",
        ):
            self.assertIn(probe, self.js, f"进度状态/轮询缺少：{probe}")

    def test_start_uses_settings(self):
        for probe in (
            "est.model", "est.reasoning_effort", "estimated_output_cap",
            "0 = 无上限", "capTxt", "max_output_tokens:est.estimated_output_cap",
        ):
            self.assertIn(probe, self.js, f"开始确认框/设置字段缺少：{probe}")

    def test_css_and_manifest(self):
        for probe in (".book-kp-progress .pbar", ".set-tok-note",
                      ".book-kp-progress-text"):
            self.assertIn(probe, self.css, f"样式缺少：{probe}")
        scripts = json.loads(read("manifest.json"))["scripts"]
        for name in ("95_settings.js", "52_textbook_knowledge.js"):
            self.assertIn(name, scripts, f"manifest 未加载：{name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
