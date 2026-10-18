# -*- coding: utf-8 -*-
"""C-0090 检索效果对照探针：同一批真实问题，改造前 / 改造后各跑一次。

判据（卡面第 4 条「检索效果对照」+「串味是否改善」）：

  · 命中与出处：`cite`（= 书名 › heading_path › source_ref）改造前后差在哪；
  · **串味**：命中块里是否同时出现**两个不同章的章标题** —— 一个块里塞着
    第十九章的正文和第二十章的标题，答案页就会把下一章的东西一起端上来。
    判据与 `core/textbook.py::cross_chapter_chunks` 同源（同一套章标题正则）。

用法（**重跑前**必须先用 --tag before 留基线）：
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_search_probe.py --tag before
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_search_probe.py --tag after
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0090")

# 三个真实问题：全部取自本库的真实题目（doc#99/#100 题库、doc#91 章末复习题），
# 覆盖三个互不相邻的章，便于看出"串味"是从哪一章漏到哪一章。
QUERIES = [
    {"id": "Q1", "q": "抗高血压药合理用药的原则有哪些？",
     "from": "doc#91 第十九章章末复习题（方案文档 §2.2 的跨章铁证就在这一段）",
     "expect": "第十九章 抗高血压药"},
    {"id": "Q2", "q": "阿托品的用途有哪些？",
     "from": "doc#100《题库A》ord=24 选择题 26 题",
     "expect": "第八章 胆碱受体阻断药"},
    {"id": "Q3", "q": "糖皮质激素类药物的不良反应有哪些？",
     "from": "doc#100《题库A》/ doc#91 第二十五章相关内容",
     "expect": "第二十五章 肾上腺皮质激素类药物"},
]

CN = "一二三四五六七八九十百零〇两"
HEAD_RE = re.compile(rf"第\s*([{CN}\d]{{1,6}})\s*章(?:\s*[\u4e00-\u9fff]{{2,20}})?")
TOC_LIKE = 14          # 一页里出现这么多章号 = 目录页，不算串味


def cn2int(s: str) -> int:
    if s.isdigit():
        return int(s)
    mp = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9}
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        return (mp.get(a, 1) if a else 1) * 10 + (mp.get(b, 0) if b else 0)
    return mp.get(s, 0)


def heads_in(text: str) -> list[tuple[int, str]]:
    out = []
    for m in HEAD_RE.finditer(text or ""):
        out.append((cn2int(m.group(1)), m.group(0).strip()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="before", choices=["before", "after"])
    ap.add_argument("--topk", type=int, default=6)
    a = ap.parse_args()

    from core import db as dbmod
    from core import search as search_mod

    conn = dbmod.connect()
    res = {"tag": a.tag, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "top_k": a.topk,
           "db": str(dbmod.DB_PATH if hasattr(dbmod, "DB_PATH") else ""),
           "queries": []}
    total_cross = 0
    for item in QUERIES:
        t0 = time.perf_counter()
        hits = search_mod.search(conn, item["q"], top_k=a.topk, corpus="main")
        dt = time.perf_counter() - t0
        rec = {"id": item["id"], "q": item["q"], "from": item["from"],
               "expect": item["expect"], "seconds": round(dt, 1), "hits": []}
        cross = 0
        for i, h in enumerate(hits, 1):
            hd = heads_in(h.text)
            nums = sorted({n for n, _ in hd})
            is_toc = len(nums) > TOC_LIKE
            is_cross = (len(nums) > 1) and not is_toc
            if is_cross and h.doc_id == 91:
                cross += 1
            d = h.to_dict()
            rec["hits"].append({
                "rank": i, "doc_id": h.doc_id, "doc_title": h.doc_title,
                "source_ref": h.source_ref, "heading_path": h.heading_path,
                "cite": h.cite(), "chunk_id": h.chunk_id, "ord": h.ord,
                "vec_score": round(h.vec_score, 4), "rrf": round(h.rrf, 5),
                "is_extra": h.is_extra, "confidence": h.confidence,
                "chapter_nums": nums, "chapter_heads": [t for _, t in hd][:6],
                "is_cross_chapter": bool(is_cross), "is_toc_like": bool(is_toc),
                "text_head": (h.text or "")[:150].replace("\n", "⏎"),
            })
        rec["cross_chapter_hits_of_doc91"] = cross
        total_cross += cross
        res["queries"].append(rec)
        print(f"\n=== {item['id']} {item['q']}  （{dt:.1f}s）===")
        for h in rec["hits"]:
            flag = "★跨章" if h["is_cross_chapter"] else ""
            print(f"  {h['rank']}. [{h['doc_id']}] {h['cite']} "
                  f"v={h['vec_score']} rrf={h['rrf']} {flag}")
            print(f"      {h['text_head'][:110]}")
        print(f"  ⇒ doc#91 跨章命中 {cross} / {len(rec['hits'])}")
    res["cross_chapter_hits_total"] = total_cross
    CARD.mkdir(parents=True, exist_ok=True)
    dst = CARD / f"search_{a.tag}.json"
    dst.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[{a.tag}] doc#91 跨章命中合计 = {total_cross}")
    print(f"写入 {dst}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
