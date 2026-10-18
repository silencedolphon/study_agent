# -*- coding: utf-8 -*-
"""卡 C-0089 探针用的**库检查**：读临时库里的教材记录，打印一行 JSON。

为什么单独一个脚本、而且不 import core：探针（Node）要在看板实例**之外**
独立核对"库里到底落了什么"，如果它跟被检查的服务共用同一套 config/连接
代码，出了偏差就看不出来。这里只用标准库 sqlite3 直连那个 .db 文件。

用法（绝对路径，不依赖 cwd）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 tools\\c0089_probe_dbcheck.py <db> [关键词]

输出（stdout 一行 JSON）：
    {"found":true,"role":"textbook","chunk_scheme":"textbook-v1","chunks":41,
     "ocr_pages":3,"figure_chunks":0,"scanned_pages":[...],
     "heading_paths":[...],"chapters":[{"label":...,"pages":"p.17-17","chunks":3}]}
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

_PAGE_RE = re.compile(r"p\.(\d+)(?:\s*-\s*(\d+))?")


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"found": False, "error": "用法: ... <db> [关键词]"}))
        return 2
    db = Path(sys.argv[1])
    kw = sys.argv[2] if len(sys.argv) > 2 else "教材"
    out: dict = {"found": False, "db": str(db)}
    if not db.exists():
        out["error"] = "库文件不存在"
        print(json.dumps(out, ensure_ascii=False))
        return 2
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT id, path, title, role, n_chunks, extra FROM documents "
            "WHERE path LIKE ?", (f"%{kw}%",)).fetchone()
        if row is None:
            print(json.dumps(out, ensure_ascii=False))
            return 0
        doc_id, path, title, role, n_chunks, extra_raw = row
        try:
            extra = json.loads(extra_raw or "{}")
        except ValueError:
            extra = {}
        rows = conn.execute(
            "SELECT heading_path, source_ref FROM chunks WHERE doc_id=?",
            (doc_id,)).fetchall()
        hps = sorted({(r[0] or "") for r in rows})
        agg: dict = {}
        for hp, ref in rows:
            parts = [x.strip() for x in (hp or "").split(">")]
            label = parts[1] if len(parts) > 1 else "（前置内容 / 未分章）"
            m = _PAGE_RE.search(ref or "")
            a = int(m.group(1)) if m else 0
            b = int(m.group(2) or a) if m else 0
            it = agg.setdefault(label, {"label": label, "page_start": 0,
                                        "page_end": 0, "chunks": 0})
            it["chunks"] += 1
            if a and (not it["page_start"] or a < it["page_start"]):
                it["page_start"] = a
            if b > it["page_end"]:
                it["page_end"] = b
        chapters = []
        for it in sorted(agg.values(), key=lambda x: x["page_start"]):
            pg = (f"p.{it['page_start']}-{it['page_end']}"
                  if it["page_start"] else "—")
            chapters.append({"label": it["label"], "pages": pg,
                             "chunks": it["chunks"]})
        out.update({
            "found": True, "id": int(doc_id), "path": path, "title": title,
            "role": role, "chunks": int(n_chunks or 0),
            "chunk_scheme": extra.get("chunk_scheme"),
            "ocr_pages": extra.get("ocr_pages"),
            "scanned_pages": extra.get("scanned_pages"),
            "figure_chunks": conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE doc_id=? AND "
                "source_ref LIKE 'p.% 图'", (doc_id,)).fetchone()[0],
            "heading_paths": hps[:8],
            "heading_kinds": len(hps),
            "chapters": chapters,
        })
    finally:
        conn.close()
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
