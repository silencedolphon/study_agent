# -*- coding: utf-8 -*-
"""知识点总结提示词 v5：写后自检 / audit 留痕（卡 C-0126）。

用户 2026-10-23 的要求：课件里的图片有时是错的，前面的识图模型推理强度不高会把
错误信息写进库里；知识点总结不能顺着错材料走。提示词里加"写完后自检"：
先用材料核对依据，再用模型自己的学科知识库核对；冲突时以公认知识为准，
并用 audit 留痕（corrected / omitted / flagged）。

本文件只检查**提示词资产与解析兼容性**，不调用任何模型、不写任何产物。
"""

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import kp_build  # noqa: E402

TRACKED = ROOT / "docs" / "prompts" / "提示词-知识点总结-v5-2026-10-23.md"
RUNTIME = ROOT / "logs" / "knowledge" / "提示词-知识点总结-v2.md"


class TestKpPromptAudit(unittest.TestCase):
    def test_tracked_copy_is_review_copy(self):
        """docs/prompts 里的 v5 副本必须存在，且含自检与 audit 契约。"""
        self.assertTrue(TRACKED.is_file(), f"缺少 v5 审查副本：{TRACKED}")
        text = TRACKED.read_text(encoding="utf-8")
        self.assertIn("写完后自检", text)
        self.assertIn("audit", text)
        self.assertIn("source_suspects", text)
        self.assertIn("corrected", text)
        self.assertIn("omitted", text)
        self.assertIn("flagged", text)
        # 仍然是可被 kp_build 逐字提取的代码块结构
        self.assertIn("<!-- PROMPT-SYS-BEGIN -->", text)
        self.assertIn("<!-- PROMPT-SYS-END -->", text)
        self.assertIn("<!-- PROMPT-USER-BEGIN -->", text)
        self.assertIn("<!-- PROMPT-USER-END -->", text)

    def test_tracked_matches_runtime_when_present(self):
        """运行时真源存在时必须与审查副本逐字节一致（避免两套口径）。"""
        if not RUNTIME.is_file():
            self.skipTest("运行时提示词不存在（脱离 KB 根运行）")
        a = hashlib.sha256(RUNTIME.read_bytes()).hexdigest()
        b = hashlib.sha256(TRACKED.read_bytes()).hexdigest()
        self.assertEqual(a, b, "运行时提示词与 docs/prompts 审查副本不一致")

    def test_loader_includes_audit_and_sha(self):
        """kp_build 真的把自检段加载进 system，并算出 sha。"""
        path = kp_build.prompt_file_v2()
        if not Path(path).is_file():
            self.skipTest("运行时提示词不存在")
        p = kp_build.load_kp_prompts_v2(path)
        sys_p = p.get("sys") or ""
        self.assertIn("写完后自检", sys_p)
        self.assertIn("audit", sys_p)
        self.assertIn("source_suspects", sys_p)
        self.assertIn("insufficient", sys_p)
        self.assertEqual(p["sha256"],
                         hashlib.sha256(Path(path).read_bytes()).hexdigest())

    def test_parse_accepts_audit_and_backward_compatible(self):
        """输出解析：带 audit 的新格式能读；旧格式（无 audit）也照常能读。"""
        new = json.dumps({
            "name": "M受体阻断药",
            "summary": "…",
            "points": ["…"],
            "pages": [70, 71],
            "sources": [{"page": "70-71", "chunk_ids": [1], "figure_desc": False}],
            "audit": {"checked": True,
                      "corrections": ["把'激动'改为'阻断'"],
                      "source_suspects": [{"issue": "图片理解把阻断写成激动",
                                           "basis": "阿托品为M受体阻断药",
                                           "action": "corrected"}]},
            "insufficient": False, "insufficient_reason": "",
        }, ensure_ascii=False)
        r = kp_build.parse_json(new)
        self.assertEqual(r.get("name"), "M受体阻断药")
        self.assertTrue(r.get("audit", {}).get("checked"))
        self.assertEqual(len(r["audit"]["corrections"]), 1)

        old = json.dumps({"name": "旧产物", "summary": "…", "points": ["…"],
                          "pages": [1], "sources": [],
                          "insufficient": False, "insufficient_reason": ""},
                         ensure_ascii=False)
        r2 = kp_build.parse_json(old)
        self.assertEqual(r2.get("name"), "旧产物")
        self.assertNotIn("audit", r2)


if __name__ == "__main__":
    unittest.main()
