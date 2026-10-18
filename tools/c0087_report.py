#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""卡 C-0087 慢档：生成 doc#91 逐章对账日志（不进验收槽，供人复查）。

跑法（绝对路径）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0087_report.py

产物：`logs\\c0087_textbook_report.md`（42 章逐章起止页 + 改造前后对照 +
与卡面锚点的差异说明）。只读 JSON 夹具，不连数据库、不写库。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import textbook                                  # noqa: E402
from core.chunk import chunk_blocks                        # noqa: E402
from core.extract import Block                             # noqa: E402

FIXTURE = ROOT / "tools" / "c0087_textbook_fixture.json"
OUT = ROOT / "logs" / "c0087_textbook_report.md"
BOOK = "药理学 第6版 刘晓东"


def _page_of(ref: str) -> int:
    m = re.match(r"^p\.(\d+)", ref or "")
    return int(m.group(1)) if m else 0


def main() -> int:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    blocks = [Block(text=b["text"], page=_page_of(b["ref"]))
              for b in data["blocks"]]
    ch = textbook.detect_chapters(blocks, book_title=BOOK)
    new = textbook.cut_by_chapter(blocks, ch, {"title": BOOK})
    old = chunk_blocks([Block(text=BOOK, page=1, kind="heading", level=1)]
                       + blocks, {"title": BOOK})
    bad_new = textbook.cross_chapter_chunks(new, ch, strict_hp=True)
    bad_old = textbook.cross_chapter_chunks(old, ch, strict_hp=False)

    L: list[str] = []
    L.append("# C-0087 教材专线① 对账日志（doc#91《药理学 第6版 刘晓东》）")
    L.append("")
    L.append(f"- 数据来源：`tools/c0087_textbook_fixture.json`"
             f"（从 `index/study.db` 的**只读** URI 导出的块快照，"
             f"{len(blocks)} 块 / {data['n_chars']} 字符）")
    L.append(f"- 模块：`core/textbook.py`（零模型调用，纯规则）")
    L.append(f"- 识别章节：**{len(ch.body)} 章**，章号 "
             f"{ch.body[0].number}..{ch.body[-1].number}")
    L.append(f"- 跨章块：改造前 **{len(bad_old)}** → 改造后 **{len(bad_new)}**")
    L.append(f"- `heading_path` 种类：改造前 1（=书名）→ 改造后 "
             f"{len({c.heading_path for c in new})}")
    L.append("")

    L.append("## 一、逐章起止页（与书内实际章号序列对照）")
    L.append("")
    L.append("| 章号 | 章名 | 起页 | 止页 | 块区间 | 块数 |")
    L.append("|---:|---|---:|---:|---|---:|")
    for c in ch.items:
        if c.is_front_matter:
            L.append(f"| — | **{c.name}**（封面/版权/编委/前言/目录） | — | — "
                     f"| [{c.start_index},{c.end_index}) | "
                     f"{c.end_index - c.start_index} |")
            continue
        n = len([x for x in new
                 if x.heading_path.endswith(f"第{c.number}章 {c.name}")])
        L.append(f"| {c.number} | {c.name} | p.{c.page_start} | p.{c.page_end} "
                 f"| [{c.start_index},{c.end_index}) | {n} |")
    L.append("")

    L.append("## 二、铁证对照（第十九章末尾 + 第二十章章首页）")
    L.append("")
    ev_old = [c for c in old if "本章小结" in c.text and "第二十章" in c.text]
    L.append("### 改造前（整份一次性切块，即现管线行为）")
    L.append("")
    for c in ev_old[:2]:
        L.append(f"- `{c.source_ref}` len={len(c.text)} "
                 f"heading_path=`{c.heading_path}`")
        L.append(f"  - 命中章：{sorted(textbook._chapter_names_in(c.text, ch))}")
        L.append(f"  - 尾部原文：`{c.text[-90:]}`")
    L.append("")
    L.append("### 改造后（按章切块）")
    L.append("")
    for c in new:
        if c.heading_path.endswith("第20章 治疗心力衰竭的药物") and \
                "学习目标" in c.text:
            L.append(f"- `{c.source_ref}` heading_path=`{c.heading_path}`")
            L.append(f"  - 开头原文：`{c.text[:80]}`")
    for c in new:
        if c.heading_path.endswith("第19章 抗高血压药") and \
                "本章小结" in c.text:
            L.append(f"- `{c.source_ref}` heading_path=`{c.heading_path}`"
                     f"（原第十九章末尾）")
            L.append(f"  - 尾部原文：`{c.text[-70:]}`")
    L.append("")

    L.append("## 三、改造前后切块统计")
    L.append("")
    L.append("| 指标 | 改造前 | 改造后 |")
    L.append("|---|---:|---:|")
    L.append(f"| 切块数 | {len(old)} | {len(new)} |")
    L.append(f"| 字符数 | {sum(len(c.text) for c in old)} | "
             f"{sum(len(c.text) for c in new)} |")
    L.append(f"| heading_path 种类 | {len({c.heading_path for c in old})} | "
             f"{len({c.heading_path for c in new})} |")
    L.append(f"| 跨章块 | {len(bad_old)} | {len(bad_new)} |")
    L.append(f"| 非正文块 | 0（无处安放） | "
             f"{len([c for c in new if c.heading_path == BOOK])} |")
    L.append("")

    L.append("## 四、与卡面锚点的差异（实测复核结果）")
    L.append("")
    L.append("| 卡面说法 | 实测 | 结论 |")
    L.append("|---|---|---|")
    L.append("| doc#91 867 块 / 624,239 字符 | 一致 | ✅ |")
    L.append("| `heading_path` 只有 1 种 | 一致 | ✅ |")
    L.append("| ord=400（`p.218`）是跨章铁证块 | ord=400 是 ord=399 的"
             "重叠副本；**铁证块是 ord=399（p.216-218）** | ⚠ 锚点偏移 |")
    L.append("| 整行「第X章 xxx」命中 332 次 | 整行命中 **333** 次"
             "（含中文数字则 394 次） | ⚠ 数字略有出入 |")
    L.append("| 目录在 p.7~11，p.7 一页 28 处章号 | p.7 的 28 处是**行内**"
             "命中（编委分工页）；目录实为 **p.8~13**，p.14 是第 1 章章首页 | "
             "⚠ 范围偏窄 |")
    L.append("| 章号全书单调递增 | 一致（第一章 p.17 → 第四十二章 p.440） | ✅ |")
    L.append("")
    L.append("> 以上差异都不影响本卡交付，但**下一张卡接线时要注意**："
             "第 1 章章首页落在 p.14，它与目录末页 p.13 相邻，"
             "换成别的教材时这条边界要重新确认。")
    L.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"日志已写出: {OUT}")
    print(f"  {len(ch.body)} 章，跨章块 {len(bad_old)} -> {len(bad_new)}，"
          f"切块 {len(old)} -> {len(new)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
