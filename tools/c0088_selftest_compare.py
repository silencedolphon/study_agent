#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""比对仪自检（卡 C-0088）：正向（自比相等）+ 负向（注入差异必须被抓到）。

为什么要有这个脚本：`verify_c0088_regression.py` 的结论是"两份快照逐字段
相同"。如果比对逻辑写错了（例如过滤集合写成了正斜杠、结果过滤出空集），
它**也会**报"相同"——那就成了假通过。本脚本用 6 项**故意注入的差异**
反过来考比对仪：抓到 = 合格，抓不到 = 比对仪坏了。

跑法（绝对路径，不依赖 cwd）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0088_selftest_compare.py

输出：结论行含 `C0088-SELFTEST-PASS` 或 `C0088-SELFTEST-FAIL`，退出码 0 / 2。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # tools/ -> 项目根
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from c0088_snapshot import compare                     # noqa: E402

BASE = ROOT / "logs" / "c0088" / "baseline_full.json"
OK = True


def check(name: str, got_same: bool, expect_same: bool, diffs: list[str]) -> None:
    global OK
    good = (got_same == expect_same)
    OK = OK and good
    first = diffs[0] if diffs else ""
    if len(first) > 150:
        first = first[:150] + "…"
    print(f"{'ok ' if good else 'BAD'} {name}: same={got_same}（期望 {expect_same}）"
          + (f"  -> {first}" if first else ""))


def main() -> int:
    global OK
    if not BASE.exists():
        print(f"C0088-SELFTEST-FAIL 缺基线：{BASE}")
        return 2
    base = json.loads(BASE.read_text(encoding="utf-8"))

    same, diffs, scope = compare(base, copy.deepcopy(base), None)
    check("自比（正向）", same, True, diffs)

    cases = [
        ("改 chunk.text", lambda m: m["chunks"][3].__setitem__(
            "text", m["chunks"][3]["text"] + "X")),
        ("改 documents.role", lambda m: m["documents"][0].__setitem__(
            "role", "textbook")),
        ("改入口返回值 chunks", lambda m: m["samples"][0]["ingest_file_force"]
            .__setitem__("chunks", 99)),
        ("改 embeddings.vec_sha256", lambda m: m["embeddings"][0].__setitem__(
            "vec_sha256", "0" * 64)),
        ("少一行 chunk", lambda m: m["chunks"].pop(5)),
        ("改 heading_path", lambda m: m["chunks"][2].__setitem__(
            "heading_path", "假的 > 面包屑")),
    ]
    for name, mutate in cases:
        m = copy.deepcopy(base)
        mutate(m)
        same, diffs, _ = compare(base, m, None)
        check(f"负向注入：{name}", same, False, diffs)

    # 防"过滤成空集"式假通过：比对范围里的行数必须非零
    _, _, scope = compare(base, copy.deepcopy(base), None)
    print("比对范围：")
    for s in scope:
        print("   ", s)
    empty = [s for s in scope if "chunks 0 行" in s or "embeddings 0 行" in s]
    if empty:
        OK = False
        print(f"BAD 比对范围有 0 行（比对是空集，没有证明力）：{empty}")

    print(f"C0088-SELFTEST-{'PASS' if OK else 'FAIL'}")
    return 0 if OK else 2


if __name__ == "__main__":
    sys.exit(main())
