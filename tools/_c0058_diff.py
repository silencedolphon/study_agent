# -*- coding: utf-8 -*-
"""逐字比对：名词真源里的筛选两块 vs C-0052 旧真源（施工用，只读）。"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

ROOT = Path(r"G:\study_agent")
OUT = ROOT / "logs" / "_c0058_diff.txt"


def blocks(p: Path) -> list[str]:
    md = p.read_text(encoding="utf-8")
    return re.findall(r"```text\r?\n(.*?)```", md, re.S)


def main() -> int:
    bak = sorted((ROOT / "logs" / "knowledge").glob(
        "提示词-知识点总结-v2.md.bak-before-c0058-*"))[-1]
    new = ROOT / "logs" / "knowledge" / "提示词-名词解释.md"
    old = blocks(bak)
    now = blocks(new)
    lines = ["旧真源 %s：%d 块" % (bak.name, len(old)),
             "新真源 %s：%d 块" % (new.name, len(now))]
    for i, b in enumerate(old, 1):
        lines.append("  OLD%d len=%d head=%r" % (i, len(b), b[:24]))
    for i, b in enumerate(now, 1):
        lines.append("  NEW%d len=%d head=%r" % (i, len(b), b[:24]))
    for tag in ("你是资深教师", "课件：{deck}"):
        o = [b for b in old if b.startswith(tag)]
        n = [b for b in now if b.startswith(tag)]
        lines.append("tag=%r 旧 %d 个 / 新 %d 个" % (tag, len(o), len(n)))
        if o and n:
            same = o[0] == n[0]
            lines.append("  逐字相同：%s（旧 %d 字符 / 新 %d 字符）"
                         % (same, len(o[0]), len(n[0])))
            if not same:
                lines.extend("  " + x for x in difflib.unified_diff(
                    o[0].splitlines(), n[0].splitlines(), "old", "new",
                    lineterm="", n=1))
    # 偏移量检查（空白差异一眼看出来）
    o_sys = [b for b in old if b.startswith("你是资深教师")]
    n_sys = [b for b in now if b.startswith("你是资深教师")]
    if o_sys and n_sys:
        lines.append("strip 后相同：%s" % (o_sys[0].strip() == n_sys[0].strip()))
        lines.append("rstrip 后相同：%s" % (o_sys[0].rstrip() == n_sys[0].rstrip()))
        lines.append("旧尾部 %r" % o_sys[0][-30:])
        lines.append("新尾部 %r" % n_sys[0][-30:])
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
