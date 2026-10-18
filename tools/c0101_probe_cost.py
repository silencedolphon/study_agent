# -*- coding: utf-8 -*-
"""卡 C-0101 只读计时探针：量一量 list_chapters 的单份与全库代价（**只读**）。

用法：<venv>\\Scripts\\python.exe tools\\c0101_probe_cost.py
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KB_ROOT", str(ROOT))

import sqlite3  # noqa: E402

from core import config, preview as preview_mod  # noqa: E402


def main() -> int:
    db = Path(config.DB_PATH)
    print("DB:", db)
    conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    ids = [int(r["id"]) for r in conn.execute(
        "SELECT id FROM documents ORDER BY id")]
    roles = {int(r["id"]): (r["role"], r["kind"]) for r in conn.execute(
        "SELECT id, COALESCE(role,'courseware') role, kind FROM documents")}
    nchunks = {int(r["id"]): int(r["n_chunks"] or 0) for r in conn.execute(
        "SELECT id, n_chunks FROM documents")}

    print("docs:", len(ids), "sum(n_chunks):", sum(nchunks.values()))

    t0 = time.time()
    r91 = preview_mod.list_chapters(91)
    t91 = time.time() - t0
    print("list_chapters(91): ok=%s n_chapters=%s  %.3fs"
          % (r91.get("ok"), r91.get("n_chapters"), t91))
    t0 = time.time()
    r102 = preview_mod.list_chapters(102)
    t102 = time.time() - t0
    print("list_chapters(102): ok=%s n_chapters=%s  %.3fs"
          % (r102.get("ok"), r102.get("n_chapters"), t102))
    for did in (55, 56, 73):
        r = preview_mod.list_chapters(did)
        print("  courseware #%d: ok=%s n_chapters=%s err=%s"
              % (did, r.get("ok"), r.get("n_chapters"), r.get("error")))

    t0 = time.time()
    for did in ids:
        preview_mod.list_chapters(did)
    tall = time.time() - t0
    print("list_chapters x %d docs: %.3fs" % (len(ids), tall))

    # 批量方案（一条 SQL）计时
    t0 = time.time()
    rows = conn.execute("SELECT doc_id, source_ref, heading_path FROM chunks "
                        "WHERE doc_id IN (%s)" % ",".join("?" * len(ids)),
                        ids).fetchall()
    tscan = time.time() - t0
    print("batch scan %d chunk rows: %.3fs" % (len(rows), tscan))
    re_ = preview_mod._CH_RE
    from core import kp_build  # noqa: E402
    t0 = time.time()
    aggall: dict = {}
    for r in rows:
        hp = str(r["heading_path"] or "")
        m = re_.search(hp)
        if not m:
            continue
        if kp_build._parse_ref(str(r["source_ref"] or "")) is None:
            continue
        aggall.setdefault(int(r["doc_id"]), set()).add(int(m.group(1)))
    treg = time.time() - t0
    print("regex pass: %.3fs" % treg)
    print("batch n_chapters: 91=%s 102=%s 55=%s"
          % (len(aggall.get(91, ())), len(aggall.get(102, ())),
             len(aggall.get(55, ()))))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
