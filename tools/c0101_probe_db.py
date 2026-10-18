# -*- coding: utf-8 -*-
"""卡 C-0101 只读探针：复核 documents.role 分布与教材明细（**只读，绝不写库**）。

用法：python tools\\c0101_probe_db.py [db_path]
"""
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(r"G:\study_agent\index\study.db")


def main() -> int:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(documents)")]
    print("documents columns:", ",".join(cols))
    print("has role:", "role" in cols)
    print("--- role x kind ---")
    for r in conn.execute("SELECT role, kind, count(*) n FROM documents "
                          "GROUP BY role, kind ORDER BY n DESC, role"):
        print("  role=%s kind=%s n=%d" % (r["role"], r["kind"], r["n"]))
    print("total docs:", conn.execute("SELECT count(*) c FROM documents").fetchone()["c"])
    print("--- textbook 明细 ---")
    for r in conn.execute("SELECT id,title,kind,corpus,n_chunks,role FROM documents "
                          "WHERE role='textbook' ORDER BY id"):
        print("  #%d [%s/%s] %s chunks=%d :: %s"
              % (r["id"], r["role"], r["kind"], r["corpus"], r["n_chunks"], r["title"]))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
