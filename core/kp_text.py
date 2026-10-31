"""只读页面/识图文本读取（S16 模块拆分第四步）。

从 `core/kp_build.py` 抽出：
- `_figure_rows`：读 figure_desc，表缺失时安全返回空；
- `read_text_pages_by_ids`：只读指定 chunk 集合的页结构，用于章级材料快速路径。

两者都只读 DB、不写表、不调模型；`kp_build` 继续导出旧名字，调用方不需要改。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from . import config
from .kp_pages import parse_ref


def figure_rows(conn, doc_id, cols):
    """读该文档的识图行；表不存在时返回空。"""
    try:
        return conn.execute(
            'SELECT %s FROM figure_desc WHERE doc_id=?' % cols,
            (int(doc_id),),
        ).fetchall()
    except sqlite3.OperationalError as e:
        if 'no such table' not in str(e):
            raise
        return []


def read_text_pages_by_ids(doc_id: int, chunk_ids,
                           db_path: str | os.PathLike | None = None) -> dict:
    """只读取指定 chunk 集合的文本页结构（章级读取的快速路径）。

    返回结构与 `read_text_pages()` 相同，但只包含给定块：
      - `blocks`/`cover`/`figrows`/`max_page`/`n_blocks`/`pages_all`；
      - 块解析规则、跨页覆盖、图片理解标记与整本读取完全一致；
      - 任何给定块解析不出页码仍会报错，绝不静默丢块。
    """
    wanted = sorted({int(x) for x in (chunk_ids or []) if int(x) > 0})
    if not wanted:
        return {"blocks": {}, "cover": {}, "figrows": {}, "max_page": 0,
                "n_blocks": 0, "pages_all": []}
    db = Path(db_path) if db_path else Path(config.DB_PATH)
    conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        blocks: dict = {}
        cover: dict = {}
        bad = []
        n_blocks = 0
        for start in range(0, len(wanted), 500):
            part = wanted[start:start + 500]
            marks = ",".join("?" for _ in part)
            sql = ("SELECT id, ord, source_ref, text FROM chunks "
                   "WHERE doc_id=? AND id IN (%s) ORDER BY ord, id" % marks)
            for r in conn.execute(sql, (int(doc_id), *part)):
                cid = int(r["id"])
                ref = (r["source_ref"] or "").strip()
                parsed = parse_ref(ref)
                if parsed is None:
                    bad.append((cid, ref))
                    continue
                a, b, is_fig = parsed
                t = r["text"] or ""
                blocks.setdefault(a, []).append({
                    "chunk_id": cid, "ord": int(r["ord"] or 0),
                    "kind": "图片理解" if is_fig else "正文块",
                    "ref": ("p.%d" % a if a == b else "p.%d-%d" % (a, b))
                           + (" 图" if is_fig else ""),
                    "page": a, "pages": list(range(a, b + 1)),
                    "chars": len(t), "text": t})
                for p in range(a, b + 1):
                    cover.setdefault(p, []).append(cid)
                n_blocks += 1
        figrows: dict = {}
        for r in figure_rows(conn, doc_id, "page, no_info"):
            page = int(r["page"])
            if page in cover:
                figrows[page] = {"no_info": int(r["no_info"] or 0)}
    finally:
        conn.close()
    if bad:
        raise RuntimeError("有 %d 个块的 source_ref 解析不出页码（拒绝静默丢块）：%s"
                           % (len(bad), bad[:5]))
    max_page = max(cover) if cover else 0
    return {"blocks": blocks, "cover": cover, "figrows": figrows,
            "max_page": max_page, "n_blocks": n_blocks,
            "pages_all": list(range(1, max_page + 1))}
