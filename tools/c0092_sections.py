# -*- coding: utf-8 -*-
# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
# File-ID: SA-22-c0092sec-tool   (project study_agent; keep this line)
"""卡 C-0092 施工脚本：在真实教材 doc#91 上抽「章 → 节」，落产物。

    venv\\Scripts\\python.exe -X utf8 tools\\c0092_sections.py [--doc 91]

**零模型调用、零写库**：全程只读 SQL（`file:…?mode=ro`），产物只有
`logs/knowledge/<doc_id>-章节细目.json` 一个文件。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, sections as sec          # noqa: E402


def db_row_counts(db_path: Path) -> dict:
    """documents / chunks 行数（只读）——用于"跑前跑后不变"的自证。"""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("documents", "chunks", "knowledge_points",
                          "kp_sources", "embeddings")}
    finally:
        conn.close()


def headings_of(db_path: Path, doc_id: int) -> Counter:
    """库内既有的章级结构（`chunks.heading_path` 二级）——交叉核对用。"""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT heading_path FROM chunks WHERE doc_id=?",
                            (doc_id,)).fetchall()
    finally:
        conn.close()
    c: Counter = Counter()
    for (h,) in rows:
        parts = (h or "").split(" > ")
        c[parts[1] if len(parts) > 1 else "(无章)"] += 1
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", type=int, default=91, help="documents.id（默认 91）")
    ap.add_argument("--out", type=str, default="", help="产物路径（默认按规范）")
    ap.add_argument("--log", type=str, default="", help="报告日志路径")
    args = ap.parse_args()

    db = Path(config.DB_PATH)
    before = db_row_counts(db)
    print(f"库 = {db}")
    print(f"跑前行数 = {before}")

    t0 = time.time()
    blocks, spans, meta = sec.load_blocks(args.doc, db)
    t1 = time.time()
    print(f"读块：{len(blocks)} 块（{t1 - t0:.2f}s），文档 = #{meta['id']} "
          f"{meta['title']!r} role={meta['role']} status={meta['status']}")

    res = sec.detect_sections(blocks, chapters=None, book_title=meta["title"],
                              doc_id=meta["id"], block_pages=spans)
    t2 = time.time()
    print(f"抽节：{res.n_sections} 节 / {len(res.chapters)} 章（{t2 - t1:.2f}s），"
          f"章来源 = {res.chapter_source}")

    out = Path(args.out) if args.out else sec.artifact_path(meta["id"])
    sec.dump_sections(res, out)
    print(f"产物 = {out}（{out.stat().st_size} 字节）")

    after = db_row_counts(db)
    print(f"跑后行数 = {after}   {'（不变 ✓）' if before == after else '★ 变了！'}")

    st = res.stats
    L: list[str] = []
    L.append("=" * 78)
    L.append(f"C-0092 节级切分报告 · doc#{meta['id']} {meta['title']}")
    L.append("=" * 78)
    L.append(f"块 {st['n_blocks']} 个（前置页 {st['front_matter_blocks']} 个）"
             f"  页范围 p.{st['page_range'][0]}-{st['page_range'][1]}")
    L.append(f"章 {st['n_chapters']} 个 / 节 {st['n_sections']} 条"
             f"（节级 {st['n_sections_level1']}、二级 {st['n_sections_level2']}、"
             f"三级 {st['n_sections_level3']}）")
    L.append(f"「第X节」级覆盖率 = {st['coverage_level1']:.1%}"
             f"（{st['n_chapters_without_level1']} 章没有节级标题）")
    L.append(f"降级（整章一节）= {len(st['fallback_chapters'])} 章，"
             f"占比 {st['fallback_ratio']:.1%}  章号={st['fallback_chapters']}")
    L.append(f"目录页块 = {len(res.toc_blocks)} 个（块下标 {res.toc_blocks}）")
    L.append("")
    L.append("---- 四种形态：候选 → 采纳 / 拒绝 ----")
    for form in sec.FORMS:
        d = st["forms"][form]
        L.append(f"  {form:<7} 候选行 {d['candidate_lines']:>4}（{d['candidate_blocks']} 块）"
                 f"  采纳 {d['kept']:>4}  拒绝 {d['rejected']:>4}"
                 f"  误报率 {(d['rejected'] / d['candidate_lines'] if d['candidate_lines'] else 0):.1%}")
    L.append(f"  拒绝原因分布 = {st['reject_reasons']}")
    L.append(f"  『1.1』宽口径（行首 \\d+.\\d+ 就算）= "
             f"{st['dot_loose']['candidate_blocks']} 块 / "
             f"{st['dot_loose']['candidate_lines']} 行；"
             f"严口径（编号后有空白）才进上面的候选")
    L.append("")
    L.append("---- 章 → 节 清单 ----")
    L.append(sec.summarize(res))
    L.append("")
    L.append("---- 与库内 heading_path 交叉核对 ----")
    hp = headings_of(db, meta["id"])
    L.append(f"  chunks.heading_path 二级取值 {len(hp)} 种，"
             f"非正文块 {hp.get('(无章)', 0)} 个")
    mine = {f"第{c.number}章 {c.name}": c.number for c in res.chapters}
    L.append(f"  本模块章名 {len(mine)} 个；与库内章名集合"
             f"{'一致 ✓' if set(mine) == {k for k in hp if k != '(无章)'} else '不一致 ★'}")
    L.append("")
    L.append("---- warnings ----")
    for w in res.warnings:
        L.append(f"  · {w}")

    text = "\n".join(L)
    print("\n" + text)
    log = Path(args.log) if args.log else (Path(config.LOG_DIR) / "c0092-report.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(text + "\n", encoding="utf-8")
    print(f"\n报告日志 = {log}")
    print(f"总耗时 {time.time() - t0:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
