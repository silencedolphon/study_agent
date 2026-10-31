"""S16 第二步：纯页码工具拆到 core/kp_pages.py 的回归。

只验证“搬运不改变口径 + 旧入口仍可用”，不调模型、不写真实数据。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import kp_pages, kp_build  # noqa: E402


class TestKpPagesSplit(unittest.TestCase):
    def test_legacy_names_are_same_objects(self):
        self.assertIs(kp_build._parse_ref, kp_pages.parse_ref)
        self.assertIs(kp_build._pages_label, kp_pages.pages_label)
        self.assertIs(kp_build._page_list, kp_pages.page_list)
        self.assertIs(kp_build._REF_RE, kp_pages.REF_RE)

    def test_parse_ref_formats(self):
        self.assertEqual(kp_pages.parse_ref("p.3"), (3, 3, False))
        self.assertEqual(kp_pages.parse_ref("p.3 图"), (3, 3, True))
        self.assertEqual(kp_pages.parse_ref("p.5-2"), (2, 5, False))
        self.assertIsNone(kp_pages.parse_ref("p.abc"))
        self.assertIsNone(kp_pages.parse_ref(""))

    def test_pages_label(self):
        self.assertEqual(kp_pages.pages_label([7, 1, 2, 3]), "1-3、7")
        self.assertEqual(kp_pages.pages_label([]), "")
        self.assertEqual(kp_pages.pages_label([2]), "2")

    def test_page_list_formats(self):
        self.assertEqual(kp_pages.page_list([3, 1, 3]), [1, 3])
        self.assertEqual(kp_pages.page_list("1-3、7"), [1, 2, 3, 7])
        self.assertEqual(kp_pages.page_list("p.2~4"), [2, 3, 4])
        self.assertEqual(kp_pages.page_list(None), [])
        self.assertEqual(kp_pages.page_list("abc"), [])

    def test_preview_uses_legacy_alias(self):
        from core import preview
        self.assertEqual(preview.kp_build._parse_ref("p.4 图"), (4, 4, True))

    def test_structure_functions_are_same_objects(self):
        self.assertIs(kp_build.page_text_for_structure,
                      kp_pages.page_text_for_structure)
        self.assertIs(kp_build.plan_structure_segments,
                      kp_pages.plan_structure_segments)

    def test_page_text_and_structure_segments(self):
        ch = {"blocks": {
            1: [{"kind": "正文块", "text": "甲", "ord": 1, "chunk_id": 10}],
            2: [{"kind": "图片理解", "text": "乙" * 500, "ord": 2, "chunk_id": 11}],
        }, "pages_all": [1, 2, 3]}
        self.assertEqual(kp_pages.page_text_for_structure(1, ch), "〔正文块〕甲")
        self.assertTrue(kp_pages.page_text_for_structure(2, ch).endswith("……"))
        segs = kp_pages.plan_structure_segments(ch, page_cap=420,
                                                seg_pages=12, seg_chars=6000)
        self.assertEqual([s["pages"] for s in segs], [[1, 2, 3]])
        self.assertEqual(segs[0]["a"], 1)
        self.assertEqual(segs[0]["b"], 3)
        self.assertEqual(kp_pages.plan_structure_segments(
            {"blocks": {}, "pages_all": []}), [])


if __name__ == "__main__":
    unittest.main()
