r"""查识图复用为什么失效——比较库中记录的哈希与磁盘上图片的哈希。

用法: python tools/diag_figure_reuse.py --doc-id 60
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, db, figures  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-id", type=int, default=0)
    args = ap.parse_args()

    conn = db.connect()
    q = ("SELECT id, title, path, kind FROM documents "
         "WHERE role!='textbook'")
    params: tuple = ()
    if args.doc_id:
        q += " AND id=?"
        params = (args.doc_id,)
    for doc in conn.execute(q + " ORDER BY id", params):
        did = int(doc["id"])
        rows = conn.execute(
            "SELECT page, image_hash, n_chunks, no_info, "
            "LENGTH(COALESCE(description,'')) AS dlen, cost_cny "
            "FROM figure_desc WHERE doc_id=? ORDER BY page LIMIT 8", (did,)
        ).fetchall()
        if not rows:
            continue
        p = Path(doc["path"])
        if not p.is_absolute():
            p = config.ROOT / p
        outdir = config.ROOT / "logs" / "figure_desc" / figures._safe_dirname(
            p.stem)
        print(f"\ndoc#{did} {doc['title'][:34]}  ({doc['kind']})")
        print(f"  渲染目录: {outdir}  存在={outdir.exists()}")
        n_desc = conn.execute(
            "SELECT COUNT(*) AS n FROM figure_desc WHERE doc_id=? AND "
            "COALESCE(description,'')!=''", (did,)).fetchone()["n"]
        n_all = conn.execute("SELECT COUNT(*) AS n FROM figure_desc "
                             "WHERE doc_id=?", (did,)).fetchone()["n"]
        print(f"  描述正文: {n_desc}/{n_all} 页有")
        for r in rows:
            f = outdir / f"slide_{int(r['page']):02d}.png"
            disk = figures.image_hash(f)[:16] if f.exists() else "(无图)"
            dbh = (r["image_hash"] or "")[:16]
            same = "同" if disk == dbh else "❌不同"
            print(f"    p{int(r['page']):>3} 库={dbh} 盘={disk} {same}  "
                  f"描述{r['dlen']}字 块{r['n_chunks']} 无信息{r['no_info']}")
    conn.close()


if __name__ == "__main__":
    main()