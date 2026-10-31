"""task-42：教材知识点「窗口阅读」（左侧目录 + 教材页图/页码）前端验收。

静态断言 core/frontend/52_textbook_knowledge.js 与 style.css：
  · 每个知识点卡保留内联复习/回忆，同时有「📖 窗口阅读」入口；
  · 窗口复用全页唯一 evPop + .mmkp-shell/.mmkp-side/.mmkp-main/.ph/.pbody/.pf/
    .kpslide/.kppages 结构；外层 mmkp-has-side、dataset.kpPop='1' 居中；
  · 左侧目录列出本章全部知识点 + p.x、当前高亮、点击同窗切换；
  · 中间标题/章节/摘要/要点/记忆提示/易错点 + 页码 chips + `/api/teach-page`
    dpi=110 大图（dpi=150 原尺寸）+ 页码 + 原文摘录（chunk 可点）+ 上一/下一知识点；
  · 无页码/图失败给人话提示；图片 load/切页/切知识点后 evPopPlace 重算；
  · 缺失 page 字段时从 source_ref/ref/source_ids(→/api/chunk) 兜底解析 p.N。
不真实调 AI、不启服务。
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FE = ROOT / "core" / "frontend"


def read(name):
    return (FE / name).read_text(encoding="utf-8")


class TestTextbookPopup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = read("52_textbook_knowledge.js")
        cls.css = read("style.css")

    def test_inline_entry_kept(self):
        for probe in (
            "book-point", "data-book-source", "data-book-review",
            "还没记住", "记住了", "data-book-window",
            "📖 窗口阅读", "bookOpenWindow(",
        ):
            self.assertIn(probe, self.js, f"内联入口/复习被破坏：{probe}")

    def test_reuses_evpop_shell(self):
        for probe in (
            "evPopEl()", "evPopMount(null, bookWindowShellHtml())", "dataset.kpPop = '1'",
            "mmkp-shell", "mmkp-side", "mmkp-main", "mmkp-has-side",
            'class="ph"', 'class="pbody"', 'class="pf"',
            "kpslide", "kppages", "kppage",
            "bookWindowShellHtml", "bookWindowBodyHtml", "bookWindowPaint",
            "bookWindowClose",
        ):
            self.assertIn(probe, self.js, f"窗口结构缺少：{probe}")
        # 居中/重定位：打开、切知识点、切页、图片 load 后都要 evPopPlace
        self.assertGreaterEqual(self.js.count("evPopPlace(null)"), 4,
                                "evPopPlace 调用偏少（切页/图片 load 后要重算）")

    def test_side_directory(self):
        for probe in (
            "bookkp-side", "本章知识点", "bookkp-dir", "data-bookkp-idx",
            "bookmarkClickDir", "mms-kp", "mms-pg",
            "bookWindowStep", "bookkp-prev", "bookkp-next",
            "上一知识点", "下一知识点", "第 ${BOOKPOP.idx + 1} / ${n} 条",
        ):
            self.assertIn(probe, self.js, f"左侧目录/上下条缺少：{probe}")

    def test_textbook_pages_and_images(self):
        for probe in (
            "/api/teach-page?doc_id=", "bookPageUrl(",
            "bookPageUrl(page, 110)", "bookPageUrl(page, 150)",
            "bookkp-pageimg", "bookkp-openfull", "bookkp-pages",
            "bookkp-pagelabel", "bookkp-pageerr", "bookkp-nopages",
            "bookPageLabel", "当前 p.${page}",
            "教材原文", "原文摘录", "原文出处", "bookkp-chunk", "openChunk(",
        ):
            self.assertIn(probe, self.js, f"教材页图/页码/摘录缺少：{probe}")
        # 无页码/图失败的人话提示
        for probe in ("暂时读不到", "解析不到教材页码", "不影响上面的总结与复习"):
            self.assertIn(probe, self.js, f"缺失败降级文案：{probe}")

    def test_missing_field_fallback(self):
        for probe in (
            "bookPointPagesSync", "bookParsePages", "bookAddPages",
            "page_start", "page_end", "source_ref", "refs",
            "source_ids", "bookResolvePointPages", "/api/chunk?id=",
            "BOOKPOP.pages", "BOOKPOP.chunkPages",
        ):
            self.assertIn(probe, self.js, f"页码兜底缺少：{probe}")
        # 至少能从 p.N / p.N-M 解析
        self.assertIn(r"p\.?\s*(\d{1,4})", self.js)

    def test_css(self):
        for probe in (".book-point-actions", ".bookkp-pageerr", ".bookkp-nopages",
                      ".bookkp-quote", ".bookkp-openfull", ".bookkp-sec"):
            self.assertIn(probe, self.css, f"样式缺少：{probe}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
