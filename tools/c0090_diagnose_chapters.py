# -*- coding: utf-8 -*-
"""C-0090 诊断：教材专线在 doc#91 上**只认出 6 章**（应为 42 章）到底卡在哪一步。

重跑回执（`cards/C-0090/rerun_receipt.json`）显示：
    chapters_n = 6，fallback = false，cross_chapter_chunks = 9
    （第2章 p.21-110、第10章 p.111-251、第25章 p.281-452 —— 章边界明显错乱）

`detect_chapters` 有五道闸，本脚本用**落盘的 12 批真实 OCR 块**（`_tmp_c0090/ocr_batch_*.pkl`，
正是重跑时用过的同一批数据）逐条统计每个候选章标题被哪一道挡下，
不重跑 OCR、不改任何 core 代码。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_diagnose_chapters.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core import textbook as tb          # noqa: E402
from c0090_rerun2 import _merge          # noqa: E402

TMP = ROOT / "_tmp_c0090"
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0090")
BOOK = "药理学 第6版 刘晓东"


def main() -> int:
    import pickle

    files = sorted(TMP.glob("ocr_batch_*.pkl"))
    print(f"加载 {len(files)} 批落盘 OCR 数据 ...")
    parts = [pickle.loads(f.read_bytes()) for f in files]
    ocr = _merge(parts)
    blocks = ocr.blocks
    print(f"合并后块数 = {len(blocks)}（逐行块），"
          f"页范围 {min(b.page for b in blocks)}~{max(b.page for b in blocks)}")

    # ---- 0. 与重跑结果对齐：直接跑一次 detect_chapters ----
    ch = tb.detect_chapters(blocks, book_title=BOOK)
    print(f"\n[detect_chapters] 章数={len(ch.body)} fallback={ch.fallback}")
    print(f"  warnings={list(ch.warnings)}")
    print("  认出的章：" + ", ".join(f"{c.number}:{c.name}(p{c.page_start}-{c.page_end})"
                                     for c in ch.body))

    # ---- 1. 目录解析 ----
    toc, toc_blocks = tb._parse_toc(blocks)
    print(f"\n[目录] _parse_toc 得到 {len(toc)} 条，来自 {len(toc_blocks)} 个块")
    print("  前 12 条：" + json.dumps({k: toc[k] for k in sorted(toc)[:12]},
                                      ensure_ascii=False))

    # ---- 2. 逐条闸门统计 ----
    stat = Counter()
    blocked_detail: dict[str, list] = {}
    last = 0
    cand_total = 0
    first_seen: dict[int, int] = {}
    for bi, b in enumerate(blocks):
        page = b.page or 0
        lines = b.text.split("\n")
        has_next_body = tb._next_block_starts_body(blocks, bi)
        for (i, num, raw, nxt) in tb._candidates_in(b):
            cand_total += 1
            if num not in first_seen:
                first_seen[num] = page
            if num <= last:
                stat["闸1_章号未递增"] += 1
                continue
            if not tb._looks_like_chapter_start(lines, i, num, raw, nxt,
                                                has_next_body):
                stat["闸2_不像章首页"] += 1
                blocked_detail.setdefault("闸2_不像章首页", []).append(
                    (num, page, raw[:40], lines[i][:50]))
                continue
            nm = tb._name_from_lines(lines, i)
            nm = tb._complete_name_ahead(
                nm, num, tb._ahead_scan_blocks(blocks, bi) + lines)
            toc_name = toc.get(num, "")
            if not tb._name_matches_toc(nm, toc_name):
                stat["闸3_章名对不上目录"] += 1
                blocked_detail.setdefault("闸3_章名对不上目录", []).append(
                    (num, page, nm[:40], (toc_name or "(目录无此章)")[:40]))
                continue
            if page and page <= tb._FRONT_MATTER_MAX_PAGE and num > 1:
                if tb._norm_name(nm) != tb._norm_name(toc_name):
                    stat["闸4_前置页且非逐字"] += 1
                    continue
            stat["通过"] += 1
            first_seen[num] = page
            last = num
            break

    print(f"\n[闸门统计] 候选总数 = {cand_total}")
    for k, v in stat.most_common():
        print(f"   {k}: {v}")

    print(f"\n[正文里整行『第X章』出现过的章号] 共 "
          f"{len([n for n in first_seen if 1 <= n <= 60])} 个")
    print("   " + str(sorted(n for n in first_seen if 1 <= n <= 60)))

    # ---- 3. 被闸3 挡下的典型（这是最可疑的一道）----
    d3 = blocked_detail.get("闸3_章名对不上目录", [])
    print(f"\n[闸3 典型] 共 {len(d3)} 条，前 15 条（章号 / 页 / 正文认出的章名 / 目录里的章名）")
    for row in d3[:15]:
        print(f"   第{row[0]}章 p.{row[1]}  正文={row[2]!r}  目录={row[3]!r}")

    d2 = blocked_detail.get("闸2_不像章首页", [])
    print(f"\n[闸2 典型] 共 {len(d2)} 条，前 10 条")
    for row in d2[:10]:
        print(f"   第{row[0]}章 p.{row[1]}  raw={row[2]!r}  line={row[3]!r}")

    out = {
        "blocks": len(blocks),
        "chapters_n": len(ch.chapters if hasattr(ch, "chapters") else ch.body),
        "fallback": ch.fallback,
        "gate_stat": dict(stat),
        "toc_n": len(toc),
        "toc_sample": {str(k): toc[k] for k in sorted(toc)[:20]},
        "candidate_chapter_numbers": sorted(n for n in first_seen if 1 <= n <= 60),
        "gate3_samples": [{"num": r[0], "page": r[1], "body_name": r[2],
                           "toc_name": r[3]} for r in d3[:30]],
        "gate2_samples": [{"num": r[0], "page": r[1], "raw": r[2], "line": r[3]}
                          for r in d2[:20]],
    }
    (CARD / "diagnose_chapters.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写入 {CARD / 'diagnose_chapters.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
