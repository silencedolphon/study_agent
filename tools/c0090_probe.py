# -*- coding: utf-8 -*-
"""C-0090 只读探测：doc#91 现状、四个计数、扫描页规模、章节识别预演。

只读，不写库。用法：
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_probe.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db as dbmod  # noqa: E402

DB = ROOT / "index" / "study.db"


def counts(conn) -> dict:
    out = {}
    for t in ("knowledge_points", "kp_sources", "mistakes", "reviews"):
        try:
            out[t] = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        except sqlite3.Error as e:
            out[t] = f"ERR:{e}"
    return out


def main() -> int:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    print(f"DB = {DB}  size={DB.stat().st_size}")
    print("== 四个计数 ==")
    print(json.dumps(counts(conn), ensure_ascii=False))

    print("\n== documents 表结构 ==")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)")]
    print("  " + ", ".join(cols))

    print("\n== doc#91 ==")
    d = conn.execute("SELECT * FROM documents WHERE id=91").fetchone()
    if d is None:
        print("doc 91 不存在")
        return 1
    for k in d.keys():
        v = d[k]
        if k == "extra" and v:
            try:
                v = json.dumps(json.loads(v), ensure_ascii=False)[:400]
            except Exception:
                pass
        print(f"  {k} = {v}")

    print("\n== doc#91 块统计 ==")
    st = conn.execute(
        "SELECT COUNT(*) n, MIN(LENGTH(text)) lo, MAX(LENGTH(text)) hi,"
        " AVG(LENGTH(text)) avg, SUM(LENGTH(text)) tot"
        " FROM chunks WHERE doc_id=91").fetchone()
    print(f"  块数={st['n']} 最短={st['lo']} 最长={st['hi']} 均值={st['avg']:.1f} 总字符={st['tot']}")
    hp = conn.execute(
        "SELECT COUNT(DISTINCT heading_path) FROM chunks WHERE doc_id=91").fetchone()[0]
    print(f"  heading_path 种类数 = {hp}")
    print("  heading_path 取值：")
    for r in conn.execute(
            "SELECT heading_path, COUNT(*) c FROM chunks WHERE doc_id=91"
            " GROUP BY heading_path ORDER BY c DESC LIMIT 12"):
        print(f"    [{r['c']:>4}] {r['heading_path']!r}")
    print("  前 14 块 ord/source_ref/前 60 字：")
    for r in conn.execute(
            "SELECT ord, source_ref, heading_path, SUBSTR(text,1,60) t FROM chunks"
            " WHERE doc_id=91 ORDER BY ord LIMIT 14"):
        print(f"    ord={r['ord']:<4} {r['source_ref']:<10} {r['t']!r}")

    print("\n== 全库文档一览（只看不动）==")
    for r in conn.execute(
            "SELECT id, kind, role, status, n_chunks, path FROM documents ORDER BY id"):
        print(f"  #{r['id']:<4} {r['kind']:<6} {str(r['role']):<10}"
              f" {str(r['status']):<8} n={r['n_chunks']:<5} {r['path']}")

    print("\n== 其它学科库 ==")
    for sub in sorted((ROOT / "subjects").iterdir()):
        dbf = sub / "index" / "study.db"
        if dbf.exists():
            c2 = sqlite3.connect(str(dbf))
            c2.row_factory = sqlite3.Row
            n_doc = c2.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            n_ch = c2.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            kp = counts(c2)
            print(f"  {sub.name}: docs={n_doc} chunks={n_ch} {kp}"
                  f" mtime={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(dbf.stat().st_mtime))}")
            c2.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
