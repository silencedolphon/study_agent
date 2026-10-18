# -*- coding: utf-8 -*-
"""C-0090 改造前后同尺对照（卡面第 3 条）。

**同一把尺子**：`core/textbook.py::cross_chapter_chunks`（C-0087 卡定义的
官方判据，一行不改）+ **同一份 Chapters**（从重跑后库里的章级 heading_path
反推：章号 / 章名 / 页范围，全都是 `core/textbook.py` 自己写进去的）。
差别只有一个：改造前的块 heading_path 只有书名一级，所以必须
`strict_hp=False`（否则一滤就全没了）；改造后的块带章级面包屑，用
`strict_hp=True`（把封面/目录这类非正文块干净排除）。两个口径都会打印。

输出：
    compare.json        全部机器可读数据
    对照表片段.md        表格与真实样本（供报告直接引用）

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_compare.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import chunk as chunk_mod          # noqa: E402
from core import textbook as tb              # noqa: E402

DB = ROOT / "index" / "study.db"
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0090")
DOC_ID = 91
WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return WS.sub("", s or "")


# --------------------------------------------------------------------------
# 数据来源
# --------------------------------------------------------------------------
def load_baseline() -> tuple[dict, list[chunk_mod.Chunk]]:
    """改造前的块：来自重跑前 dump 的 baseline_doc91.json。"""
    d = json.loads((CARD / "baseline_doc91.json").read_text(encoding="utf-8"))
    out = []
    for c in d["chunks"]:
        out.append(chunk_mod.Chunk(
            text=str(c.get("text") or ""),
            heading_path=str(c.get("heading_path") or ""),
            source_ref=str(c.get("source_ref") or ""),
            ord=int(c.get("ord") or 0),
            meta={"chunk_id": c.get("id"), "topic": c.get("topic")}))
    return d["stat"], out


def load_now(conn) -> tuple[dict, list[chunk_mod.Chunk]]:
    """改造后的块：来自库里 doc#91 的当前内容。"""
    rows = conn.execute(
        "SELECT id, ord, text, heading_path, source_ref, topic FROM chunks"
        " WHERE doc_id=? ORDER BY ord", (DOC_ID,)).fetchall()
    out = []
    for r in rows:
        out.append(chunk_mod.Chunk(
            text=str(r["text"] or ""),
            heading_path=str(r["heading_path"] or ""),
            source_ref=str(r["source_ref"] or ""),
            ord=int(r["ord"] or 0),
            meta={"chunk_id": int(r["id"]), "topic": r["topic"]}))
    return {"n": len(rows)}, out


def load_chapters(conn) -> tb.Chapters:
    """从库里已写入的章级 heading_path 反推 Chapters（章号/章名/页范围）。

    这就是 `core/textbook.py` 自己写进去的东西，没有第二份真相：
    `Chapter.title` = f'第{number}章 {name}'，而页范围来自各块的 source_ref。
    """
    agg: dict[int, dict] = {}
    for r in conn.execute(
            "SELECT heading_path, source_ref, COUNT(*) n FROM chunks"
            " WHERE doc_id=? GROUP BY heading_path, source_ref", (DOC_ID,)):
        hp = str(r["heading_path"] or "")
        parts = [x.strip() for x in hp.split(">")]
        if len(parts) < 2:
            continue
        m = re.match(r"^第\s*(\d+)\s*章\s*(.*)$", parts[1])
        if not m:
            continue
        no, name = int(m.group(1)), m.group(2).strip()
        a, b = _span(str(r["source_ref"] or ""))
        it = agg.setdefault(no, {"number": no, "name": name,
                                 "page_start": 0, "page_end": 0, "chunks": 0})
        it["chunks"] += int(r["n"] or 0)
        if a and (not it["page_start"] or a < it["page_start"]):
            it["page_start"] = a
        if b > it["page_end"]:
            it["page_end"] = b
    items = [tb.Chapter(number=v["number"], name=v["name"], start_index=0,
                        end_index=0, page_start=v["page_start"],
                        page_end=v["page_end"])
             for _, v in sorted(agg.items())]
    return tb.Chapters(items=items, fallback=False, warnings=[],
                       book_title="药理学 第6版 刘晓东")


def _span(ref: str) -> tuple[int, int]:
    m = re.search(r"p\.(\d+)(?:\s*-\s*(\d+))?", ref or "")
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2) or m.group(1))


def chapter_table(conn) -> list[dict]:
    """每章块数一览（章号 + 章名 + 页范围 + 块数）——给人直接核对的东西。"""
    agg: dict[str, dict] = {}
    for r in conn.execute(
            "SELECT heading_path, source_ref, COUNT(*) n, MIN(ord) o0,"
            " MAX(ord) o1 FROM chunks WHERE doc_id=?"
            " GROUP BY heading_path, source_ref", (DOC_ID,)):
        hp = str(r["heading_path"] or "")
        parts = [x.strip() for x in hp.split(">")]
        label = parts[1] if len(parts) > 1 else "（前置内容 / 未分章）"
        m = re.search(r"第\s*(\d+)\s*章", label)
        no = int(m.group(1)) if m else 0
        a, b = _span(str(r["source_ref"] or ""))
        it = agg.setdefault(label, {"label": label, "chapter_no": no,
                                    "page_start": 0, "page_end": 0, "chunks": 0,
                                    "ord_min": 10 ** 9, "ord_max": -1})
        it["chunks"] += int(r["n"] or 0)
        it["ord_min"] = min(it["ord_min"], int(r["o0"] or 0))
        it["ord_max"] = max(it["ord_max"], int(r["o1"] or 0))
        if a and (not it["page_start"] or a < it["page_start"]):
            it["page_start"] = a
        if b > it["page_end"]:
            it["page_end"] = b
    return sorted(agg.values(), key=lambda x: (0 if x["chapter_no"] <= 0 else 1,
                                               x["chapter_no"],
                                               x["page_start"]))


# --------------------------------------------------------------------------
# 定位章标题在块内的位置（做样本配对用）
# --------------------------------------------------------------------------
def head_positions(text: str, chapters: tb.Chapters) -> list[tuple[int, int]]:
    """返回 [(行号, 章号), ...]：符合 `_chapter_names_in` 判据的章标题行。"""
    body = {c.number: c for c in chapters.body}
    lines = [l.strip() for l in (text or "").split("\n")]
    out = []
    for i, s in enumerate(lines):
        m = tb._CHAPTER_HEAD.match(s)
        if not m:
            continue
        num = tb._cn_to_int(m.group(1))
        if num not in body:
            continue
        name = tb._norm_name(body[num].name)
        if not name:
            out.append((i, num))
            continue
        same = tb._norm_name(tb._strip_chapter_head(s))
        window = "".join(tb._norm_name(w) for w in lines[i + 1:i + 4])
        if name in same or name in window:
            out.append((i, num))
    return out


def find_chunk_with(chunks: list[chunk_mod.Chunk], needle: str):
    n = norm(needle)
    if len(n) < 8:
        return None
    for c in chunks:
        if n in norm(c.text):
            return c
    return None


def main() -> int:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    doc = conn.execute("SELECT * FROM documents WHERE id=?", (DOC_ID,)).fetchone()
    if doc is None:
        print("doc#91 不在库里")
        return 1
    try:
        extra = json.loads(doc["extra"] or "{}")
    except ValueError:
        extra = {}

    st_before, before = load_baseline()
    _, after = load_now(conn)
    chapters = load_chapters(conn)
    chs_table = chapter_table(conn)

    cross_before_loose = tb.cross_chapter_chunks(before, chapters,
                                                 strict_hp=False)
    cross_before_strict = tb.cross_chapter_chunks(before, chapters,
                                                  strict_hp=True)
    cross_after_strict = tb.cross_chapter_chunks(after, chapters,
                                                 strict_hp=True)
    cross_after_loose = tb.cross_chapter_chunks(after, chapters,
                                                strict_hp=False)

    # 宽松口径不要求章级面包屑，于是**目录页/编委页**（一页抄着全书章号）
    # 也会被判成"跨章"——改造前这些块与正文同挂书名，改造后它们被单独
    # 归到「非正文」分区。所以改造后的宽松口径必须再分一刀：
    #   · 有章级面包屑的（真正文块）跨章 = 0 才是要验收的那一条；
    #   · 无章级面包屑的（封面/版权/编委/前言/目录）本来就不属于任何一章。
    def _has_chapter_hp(c) -> bool:
        return bool(re.search(r"第\s*\d+\s*章", c.heading_path or ""))

    after_loose_body = [c for c in cross_after_loose if _has_chapter_hp(c)]
    after_loose_front = [c for c in cross_after_loose if not _has_chapter_hp(c)]
    before_loose_body = [c for c in cross_before_loose if c.ord > 17]
    before_loose_front = [c for c in cross_before_loose if c.ord <= 17]

    def dist(chs):
        L = [len(c.text) for c in chs]
        if not L:
            return {}
        return {"n": len(L), "chars": sum(L), "min": min(L), "max": max(L),
                "mean": round(statistics.fmean(L), 1),
                "median": statistics.median(L)}

    hp_before = {}
    for c in before:
        hp_before[c.heading_path] = hp_before.get(c.heading_path, 0) + 1
    hp_after = {}
    for c in after:
        hp_after[c.heading_path] = hp_after.get(c.heading_path, 0) + 1

    # ---- 样本配对：旧跨章块 -> 新库里承接它前后两段的两块 ----
    samples = []
    for c in cross_before_loose:
        pos = head_positions(c.text, chapters)
        if len(pos) < 2:
            continue
        lines = c.text.split("\n")
        cut = pos[1][0]
        seg_a = "\n".join(lines[:cut]).strip()
        seg_b = "\n".join(lines[cut:]).strip()
        if len(norm(seg_a)) < 12 or len(norm(seg_b)) < 12:
            continue
        tail_a = norm(seg_a)[-30:]
        head_b = norm(seg_b)[:30]
        na = find_chunk_with(after, tail_a)
        nb = find_chunk_with(after, head_b)
        samples.append({
            "old": {"ord": c.ord, "chunk_id": c.meta.get("chunk_id"),
                    "source_ref": c.source_ref, "heading_path": c.heading_path,
                    "chapter_nums": [n for _, n in pos],
                    "text": c.text},
            "split_line": cut,
            "seg_a": seg_a, "seg_b": seg_b,
            "new_a": ({"ord": na.ord, "chunk_id": na.meta.get("chunk_id"),
                       "source_ref": na.source_ref,
                       "heading_path": na.heading_path, "text": na.text}
                      if na else None),
            "new_b": ({"ord": nb.ord, "chunk_id": nb.meta.get("chunk_id"),
                       "source_ref": nb.source_ref,
                       "heading_path": nb.heading_path, "text": nb.text}
                      if nb else None),
            "split_ok": bool(na and nb and na.heading_path != nb.heading_path),
        })

    # ---- 前置内容（封面/版权/编委/前言/目录）落点 ----
    front = [{"ord": c.ord, "chapter_no": 0, "heading_path": c.heading_path,
              "source_ref": c.source_ref, "len": len(c.text),
              "text_head": c.text[:80].replace("\n", "⏎")}
             for c in after
             if not re.search(r"第\s*\d+\s*章", c.heading_path or "")]
    front_before = [{"ord": c.ord, "heading_path": c.heading_path,
                     "source_ref": c.source_ref, "len": len(c.text),
                     "text_head": c.text[:80].replace("\n", "⏎")}
                    for c in before if c.ord < 16]

    out = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "doc": {"id": DOC_ID, "title": doc["title"], "role": doc["role"],
                "status": doc["status"], "n_chunks": doc["n_chunks"],
                "n_chars": doc["n_chars"],
                "chunk_scheme": extra.get("chunk_scheme"),
                "extra_keys": sorted(extra.keys())},
        "chapters": {"n": len(chapters.body), "fallback": chapters.fallback,
                     "list": [{"no": c.number, "name": c.name,
                               "page_start": c.page_start,
                               "page_end": c.page_end} for c in chapters.body]},
        "before": {"stat": st_before, "dist": dist(before),
                   "heading_path_kinds": len(hp_before),
                   "heading_paths": hp_before,
                   "cross_loose": len(cross_before_loose),
                   "cross_strict": len(cross_before_strict),
                   "cross_loose_ords": [c.ord for c in cross_before_loose],
                   "cross_loose_front": len(before_loose_front),
                   "cross_loose_body": len(before_loose_body),
                   "cross_loose_body_ords": [c.ord for c in before_loose_body]},
        "after": {"dist": dist(after),
                  "heading_path_kinds": len(hp_after),
                  "heading_paths_top": dict(sorted(hp_after.items(),
                                                   key=lambda kv: -kv[1])[:8]),
                  "cross_loose": len(cross_after_loose),
                  "cross_strict": len(cross_after_strict),
                  "cross_loose_front": len(after_loose_front),
                  "cross_loose_front_ords": [c.ord for c in after_loose_front],
                  "cross_loose_body": len(after_loose_body),
                  "cross_loose_body_ords": [c.ord for c in after_loose_body]},
        "chapter_table": chs_table,
        "samples": samples,
        "front_after": front,
        "front_before": front_before,
    }
    (CARD / "compare.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 控制台摘要（结论行在前）----
    print(f"C0090 对照：改造前 {len(before)} 块 / 改造后 {len(after)} 块；"
          f"跨章块 {len(cross_before_loose)} -> {len(cross_after_strict)}")
    print(f"  heading_path 种类：{len(hp_before)} -> {len(hp_after)}"
          f"（章数 {len(chapters.body)}）")
    print(f"  块长：{dist(before)}")
    print(f"        {dist(after)}")
    print(f"  cross_chapter_chunks strict_hp=False: 前 {len(cross_before_loose)}"
          f" / 后 {len(cross_after_loose)}")
    print(f"  cross_chapter_chunks strict_hp=True : 前 {len(cross_before_strict)}"
          f" / 后 {len(cross_after_strict)}")
    print(f"  宽松口径再分一刀：改造前 正文 {len(before_loose_body)} / 前置 "
          f"{len(before_loose_front)}；改造后 正文 {len(after_loose_body)} / 前置 "
          f"{len(after_loose_front)}（正文跨章 {len(before_loose_body)} -> "
          f"{len(after_loose_body)}）")
    print(f"  前置内容块（无章级面包屑）：改造后 {len(front)} 块")
    print(f"  样本配对 {len(samples)} 组，其中分开成功 "
          f"{sum(1 for s in samples if s['split_ok'])} 组")
    print(f"写入 {CARD / 'compare.json'}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
