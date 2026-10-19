"""M0-8（移动端基础）验收 —— 回退后保留的排版/移动端改动。

用户实测后 M0 的功能型新增（场景预设 / 新建知识点入口 / 错题闭环 / 产物导入看板
入口）已回退；本文件只保留**移动端基础**的静态回归：
  · ≤560px 媒体查询把表格改卡片式（行块、单元格纵向堆叠、隐藏表头）；
  · 长路径 / 代码允许断行、输入控件不顶宽；
  · 问答页自动聚焦用 `focus({preventScroll:true})`（首屏 scrollY=0）。
真实 390×844 的浏览器实测（8 视图 scrollWidth==clientWidth==390、首屏 scrollY=0）
证据见 `_work/rollback/report.md` 与 `_work/rollback/shots/`。

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
    _SRV = env.Server(tag="m0mobile")
    _SRV.start()
    r = _SRV.get("/", timeout=60)
    assert r.status == 200, f"首页返回 {r.status}"
    _PAGE = r.text


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


class TestMobile(unittest.TestCase):
    """M0-8（回退后保留）：窄屏排版与首屏不自动滚。"""

    def test_mobile_table_cards(self):
        """≤560px：表格改卡片式（block/堆叠），长串可断行——不靠横向滚动。"""
        self.assertIn("@media (max-width:560px)", _PAGE, "缺少窄屏媒体查询")
        media = _PAGE[_PAGE.index("@media (max-width:560px)"):]
        for rule in ("#view table{display:block", "#view thead{display:none",
                     "#view tr{display:block", "#view td{display:block"):
            self.assertIn(rule, media, f"窄屏表格卡片化缺规则：{rule}")
        self.assertIn("word-break:break-all", media, "长路径/代码未允许断行")
        self.assertIn("input,select,textarea{max-width:100%}", media,
                      "窄屏输入控件可能顶宽")

    def test_mobile_autofocus_no_scroll(self):
        """问答页自动聚焦用 preventScroll：首屏 scrollY=0。"""
        self.assertIn("function safeFocus(", _PAGE, "缺少防滚动聚焦 helper")
        self.assertIn("preventScroll: true", _PAGE, "focus 未带 preventScroll")
        self.assertIn("if (CHAT.msgs.length === 0) safeFocus(ta);", _PAGE,
                      "问答页首屏仍用普通 focus()（会把页面滚下去）")
        self.assertNotIn("if (CHAT.msgs.length === 0) ta.focus();", _PAGE,
                         "旧的会滚屏的 focus() 调用还在")


if __name__ == "__main__":
    unittest.main(verbosity=2)
