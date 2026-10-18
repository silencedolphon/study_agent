# -*- coding: utf-8 -*-
"""C-0090 改造前基线快照：把 doc#91 的 867 块原样 dump 出来。

只读。产物写到**卡目录**（不写 %TEMP%），重跑之后还能复查"改造前长什么样"：

    baseline_doc91.json      {文档行, 全部块(文本全量), 统计}
    baseline_doc91_前14块.txt 人眼核对用的前置内容（封面/版权/编者/目录）

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_snapshot.py [doc_id] [输出目录]
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
DB = ROOT / "index" / "study.db"
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0090")

# 中文数字章标题（与 core/textbook.py 同一套写法，这里只用于**统计**旧块）
CN = "一二三四五六七八九十百零〇两"
HEAD_RE = re.compile(rf"第\s*([{CN}\d]{{1,6}})\s*章")


def main() -> int:
    doc_id = int(sys.argv[1]) if len(sys.argv) > 1 else 91
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else CARD
    outdir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    d = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if d is None:
        print(f"doc#{doc_id} 不存在")
        return 1
    cols = [r[1] for r in conn.execute("PRAGMA table_info(chunks)")]
    rows = conn.execute(
        f"SELECT * FROM chunks WHERE doc_id=? ORDER BY ord", (doc_id,)).fetchall()

    chunks = []
    for r in rows:
        item = {k: r[k] for k in r.keys() if k not in ("vec", "embedding")}
        item["len"] = len(str(r["text"] or ""))
        chunks.append(item)

    lens = [c["len"] for c in chunks]
    hps = {}
    for c in chunks:
        hp = c.get("heading_path") or ""
        hps[hp] = hps.get(hp, 0) + 1

    # 章标题命中统计（纯文本判据，改造前的粗略跨章基线）
    hit_chunks = 0
    multi_chunks = 0
    for c in chunks:
        nums = set(HEAD_RE.findall(str(c.get("text") or "")))
        if nums:
            hit_chunks += 1
        if len(nums) > 1:
            multi_chunks += 1

    stat = {
        "doc_id": doc_id,
        "doc": {k: d[k] for k in d.keys()},
        "chunks_cols": cols,
        "n_chunks": len(chunks),
        "chars_total": sum(lens),
        "len_min": min(lens) if lens else 0,
        "len_max": max(lens) if lens else 0,
        "len_mean": round(statistics.fmean(lens), 1) if lens else 0,
        "len_median": statistics.median(lens) if lens else 0,
        "heading_path_kinds": len(hps),
        "heading_path_counts": hps,
        "text_hit_head_chunks": hit_chunks,
        "text_multi_head_chunks": multi_chunks,
        "snapshot_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (outdir / "baseline_doc91.json").write_text(
        json.dumps({"stat": stat, "chunks": chunks}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    lines = []
    for c in chunks[:16]:
        lines.append(f"===== ord={c.get('ord')} ref={c.get('source_ref')} "
                     f"len={c['len']} hp={c.get('heading_path')!r} =====")
        lines.append(str(c.get("text") or ""))
        lines.append("")
    (outdir / "baseline_doc91_前16块.txt").write_text("\n".join(lines),
                                                      encoding="utf-8")

    print(json.dumps(stat, ensure_ascii=False, indent=2)[:2000])
    print(f"\n写入 {outdir / 'baseline_doc91.json'}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
