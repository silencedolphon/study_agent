# -*- coding: utf-8 -*-
"""C-0090 预演：**只用改造前的旧块**反推章清单，算一遍跨章块数。

为什么要这一步（而不是等重跑完再算）：
  · 主口径是"用重跑后 `core/textbook.py` 自己识别出的 Chapters"当尺子，
    它是权威的；
  · 但那条路要等 96 分钟的 OCR 跑完。本脚本用**旧块文本里出现的章标题行**
    自己反推一份 Chapters，先算出一个**独立估计**。两条路算出来的数字若
    量级一致，说明"改造前跨章块 > 0"这个结论不依赖某一份 Chapters。

判据一行不改：`core/textbook.py::cross_chapter_chunks(strict_hp=False)`。

只读。用法：
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_before_cross.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import chunk as chunk_mod          # noqa: E402
from core import textbook as tb              # noqa: E402

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0090")


def main() -> int:
    d = json.loads((CARD / "baseline_doc91.json").read_text(encoding="utf-8"))
    chunks = []
    for c in d["chunks"]:
        chunks.append(chunk_mod.Chunk(
            text=str(c.get("text") or ""),
            heading_path=str(c.get("heading_path") or ""),
            source_ref=str(c.get("source_ref") or ""),
            ord=int(c.get("ord") or 0),
            meta={"chunk_id": c.get("id")}))

    # ---- 从旧块文本反推章名（按章号投票，取最常见的那个归一化写法）----
    votes: dict[int, Counter] = {}
    pages: dict[int, list[int]] = {}
    for c in chunks:
        a, b = 0, 0
        m = re.search(r"p\.(\d+)(?:\s*-\s*(\d+))?", c.source_ref or "")
        if m:
            a = int(m.group(1))
            b = int(m.group(2) or m.group(1))
        for ln in (c.text or "").split("\n"):
            s = ln.strip()
            mm = tb._CHAPTER_LINE.match(s)
            if not mm:
                continue
            num = tb._cn_to_int(mm.group(1))
            name = tb._norm_name(tb._NOISE.sub("", mm.group(2) or "").strip())
            if not name or len(name) < 2:
                continue
            votes.setdefault(num, Counter())[name] += 1
            if a:
                pages.setdefault(num, []).append(a)

    items = []
    for num in sorted(votes):
        name = votes[num].most_common(1)[0][0]
        ps = sorted(pages.get(num) or [0])
        items.append(tb.Chapter(number=num, name=name, start_index=0,
                                end_index=0, page_start=ps[0] if ps else 0,
                                page_end=ps[-1] if ps else 0))
    chapters = tb.Chapters(items=items, fallback=False, warnings=[],
                           book_title="药理学 第6版 刘晓东")
    print(f"从旧块反推出的章：{len(chapters.body)} 个")
    print("  章号 章名（前 8 个）:",
          [(c.number, c.name) for c in chapters.body[:8]])

    bad = tb.cross_chapter_chunks(chunks, chapters, strict_hp=False)
    print(f"\n改造前跨章块（strict_hp=False，判据=块内出现两个不同章标题）：{len(bad)}")
    for c in bad[:12]:
        nums = sorted(tb._chapter_names_in(c.text, chapters))
        print(f"  ord={c.ord} {c.source_ref} 章号={nums}")
        print(f"     {c.text[:150]!r}")
    # 目录块的量级（会被 cross_chapter_chunks 的目录判据挡掉，这里只报出来）
    toc_like = [c for c in chunks
                if len(tb._chapter_names_in(c.text, chapters)) > tb._TOC_LIKE_CHAPTERS]
    print(f"\n正文里像目录的块（命中 > {tb._TOC_LIKE_CHAPTERS} 个章号，"
          f"已被判据排除）：{len(toc_like)} 个，ord={[c.ord for c in toc_like][:16]}")
    multi = Counter(len(tb._chapter_names_in(c.text, chapters)) for c in chunks)
    print(f"每块命中的章号个数分布 = {dict(sorted(multi.items()))}")

    out = {"chapters_from_baseline": len(chapters.body),
           "chapter_names": [{"no": c.number, "name": c.name} for c in chapters.body],
           "cross_before_est": len(bad),
           "cross_before_ords": [c.ord for c in bad],
           "toc_like_ords": [c.ord for c in toc_like],
           "hit_count_dist": {str(k): v for k, v in sorted(multi.items())}}
    (CARD / "before_cross_estimate.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
