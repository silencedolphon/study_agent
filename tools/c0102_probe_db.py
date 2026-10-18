# -*- coding: utf-8 -*-
"""卡 C-0102 只读探查：教材/课件在真库里的样子 + 路径安全闸的判据。

**只读**：sqlite 用 `mode=ro&immutable=1`，不改真库一个字节；只打印，不写文件。

用法：python tools\\c0102_probe_db.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "index" / "study.db"


def main() -> int:
    uri = "file:%s?mode=ro&immutable=1" % DB.as_posix()
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    print("库：%s（%d MB）" % (DB, DB.stat().st_size // 1024 // 1024))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)")]
    print("documents 列：%s" % cols)
    print("\n== role=textbook ==")
    for r in conn.execute("SELECT id,title,path,role,corpus,n_chunks FROM documents "
                          "WHERE role='textbook' ORDER BY id"):
        p = Path(str(r["path"]))
        print("  doc#%s %s | corpus=%s n_chunks=%s | 文件存在=%s 大小=%sMB"
              % (r["id"], r["title"], r["corpus"], r["n_chunks"], p.exists(),
                 (p.stat().st_size // 1024 // 1024) if p.exists() else "-"))
        print("        path=%s" % p)
    print("\n== 各 role 计数 ==")
    for r in conn.execute("SELECT role, COUNT(*) n FROM documents GROUP BY role "
                          "ORDER BY n DESC"):
        print("  %-14s %d" % (r["role"], r["n"]))
    print("\n== 有知识点产物的课件（logs/knowledge） ==")
    kd = ROOT / "logs" / "knowledge"
    fs = sorted(kd.glob("*-末级知识点总结.json"))
    print("  产物文件 %d 个" % len(fs))
    for f in fs:
        try:
            did = int(f.name.split("-")[0])
        except ValueError:
            continue
        r = conn.execute("SELECT id,title,role,corpus,path FROM documents WHERE id=?",
                         (did,)).fetchone()
        print("  %s -> 库里的 doc#%s %s" % (f.name, did, (r["title"] if r else "（不在库里）")))
    print("\n== doc#103 ==")
    r = conn.execute("SELECT * FROM documents WHERE id=103").fetchone()
    if r:
        for k in r.keys():
            v = r[k]
            if isinstance(v, str) and len(v) > 120:
                v = v[:120] + "…"
            print("  %s = %s" % (k, v))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
