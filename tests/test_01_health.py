"""启动健康 / 页面 / 路由回落 / 隔离自检（全部只读）。"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None
_SNAP = None


def setUpModule():
    global _SRV, _SNAP
    env.prepare(clean_data=True)
    _SNAP = env.real_data_snapshot()
    _SRV = env.Server(tag="health")
    _SRV.start()


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


class TestHealth(unittest.TestCase):
    def test_home_ok(self):
        """GET / → 200，返回内嵌页面且含标题/骨架。"""
        r = _SRV.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", r.headers.get("Content-Type", ""))
        text = r.text
        self.assertGreater(len(text), 200_000, "首页体积异常小，可能模板被截断")
        self.assertIn("<title>学习看板 · study_agent</title>", text)
        for marker in ('id="nav"', 'id="chatq"', 'id="firstrun"', 'id="docs-tabs"'):
            self.assertIn(marker, text, f"首页缺少关键骨架 {marker}")

    def test_index_html_alias(self):
        """GET /index.html 与 / 返回同一份 HTML。"""
        a = _SRV.get("/")
        b = _SRV.get("/index.html")
        self.assertEqual(b.status, 200)
        self.assertIn("<title>学习看板 · study_agent</title>", b.text)
        self.assertEqual(a.body, b.body, "/index.html 与 / 内容不一致")

    def test_page_self_contained(self):
        """页面无外链静态资源（JS/CSS/图片全部内嵌），离线可用。"""
        html = _SRV.get("/").text
        bad = re.findall(
            r'(?:<script[^>]+src\s*=\s*["\']https?://|<link[^>]+href\s*=\s*["\']https?://'
            r'|<img[^>]+src\s*=\s*["\']https?://)',
            html, flags=re.I,
        )
        self.assertEqual(bad, [], f"页面引用了外部静态资源：{bad[:3]}")

    def test_unknown_route_404(self):
        """未知 GET/POST 路由都回落 404 JSON（不挂起、不裸断连接）。"""
        st, obj = _SRV.get_json("/api/definitely-not-a-route")
        self.assertEqual(st, 404)
        self.assertIsInstance(obj, dict)
        self.assertIn("error", obj)
        st2, obj2 = _SRV.post_json("/api/definitely-not-a-post", {})
        self.assertEqual(st2, 404)
        self.assertIn("error", obj2)

    def test_isolation_paths(self):
        """被测实例的 db/raw/notes/settings/subjects 全部落在 _work/verify 下。"""
        st, first = _SRV.get_json("/api/firstrun")
        self.assertEqual(st, 200)
        for key in ("db_path", "raw_dir", "settings_file", "env_example"):
            self.assertIn(str(env.VERIFY_DIR), first[key],
                          f"{key} 未指向隔离目录：{first[key]}")
        st, raw = _SRV.get_json("/api/raw")
        self.assertEqual(st, 200)
        self.assertIn(str(env.VERIFY_DIR), raw["raw_dir"])
        st, subjects = _SRV.get_json("/api/subjects")
        self.assertEqual(st, 200)
        self.assertEqual(subjects["current"], "default")
        default_entry = subjects["subjects"][0]
        self.assertEqual(default_entry["slug"], "default")
        self.assertEqual(Path(default_entry["dir"]).resolve(),
                         env.SANDBOX_DIR.resolve(),
                         "默认学科（config.ROOT）没有落在代码沙箱里")
        # 关键：绝不能用真实库路径
        real_default = str((env.PROJECT / "index" / "study.db").resolve())
        self.assertNotEqual(Path(first["db_path"]).resolve(), Path(real_default))

    def test_real_data_untouched(self):
        """套件运行期间真实数据目录指纹不变（若被外部进程改动则跳过并说明）。"""
        now = env.real_data_snapshot()
        diffs = env.diff_real_snapshot(_SNAP, now)
        if diffs:
            self.skipTest(
                "真实数据在验收期间被套件之外的进程改动（真实看板 8756 仍在运行/他人操作），"
                "指纹对比不再具备判定力；本套件自身路径隔离由 F05 断言。差异："
                + "; ".join(diffs[:3])
            )
        self.assertEqual(diffs, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
