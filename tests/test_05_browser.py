"""浏览器冒烟：Edge headless 加载隔离实例首页，验证 JS 真正执行、主界面 DOM 渲染。

判据（不只看 200）：`<nav id="nav">` 在源码里是空的，导航按钮由前端 JS 调用 API 后
动态渲染——dump 出来的 DOM 里出现 `data-v="assistant"` / 八个页签名，才说明 JS 跑通。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None
_DOM = None
_SHOT = None


def setUpModule():
    global _SRV, _DOM, _SHOT
    env.prepare(clean_data=True)
    _SRV = env.Server(tag="browser")
    _SRV.start()
    _SHOT = env.ARTIFACT_DIR / "browser-home.png"
    try:
        _DOM = env.edge_dump_dom(_SRV.url + "/", virtual_time_ms=9000, screenshot=_SHOT)
    except Exception as e:  # noqa: BLE001
        _DOM = f"__EDGE_ERROR__ {type(e).__name__}: {e}"


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


class TestBrowser(unittest.TestCase):
    def test_edge_dom(self):
        """Edge headless dump-dom：标题 + JS 动态渲染的主界面导航。"""
        self.assertFalse(_DOM.startswith("__EDGE_ERROR__"), _DOM[:500])
        self.assertGreater(len(_DOM), 50_000, "渲染后的 DOM 体积异常小")
        self.assertIn("<title>学习看板 · study_agent</title>", _DOM)
        # 静态 HTML 里 nav 是空的；这些标记只有 JS 执行后才会出现
        self.assertIn('data-v="assistant"', _DOM, "导航未渲染：前端 JS 可能报错/未执行")
        for label in ("问答", "总览", "复习", "图谱", "资料", "知识点", "错题", "笔记"):
            self.assertIn(label, _DOM, f"主界面缺页签：{label}")
        # 学科切换器与搜索框也来自页面骨架 + JS 填充
        self.assertIn('id="subj-name"', _DOM)
        self.assertIn('id="q"', _DOM)
        # 前端确实在打 API（说明页面主流程动了）
        self.assertIn("/api/", _DOM)

    def test_edge_screenshot(self):
        """Edge headless 截图落盘（证据文件）。"""
        self.assertTrue(_SHOT.exists(), f"截图未生成：{_SHOT}")
        self.assertGreater(_SHOT.stat().st_size, 5_000, "截图文件过小，可能渲染失败")
        with open(_SHOT, "rb") as f:
            head = f.read(8)
        self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n", "截图不是 PNG")


if __name__ == "__main__":
    unittest.main(verbosity=2)
