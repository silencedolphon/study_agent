"""列出全部资料并按内容哈希找重复入库。"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db  # noqa: E402


def main() -> None:
    conn = db.connect()
    db.init_db(conn)
    rows = list(conn.execute(
        "SELECT id, path, title, kind, n_chunks, content_hash "
        "FROM documents ORDER BY kind, id"))
    print(f"=== 全部 {len(rows)} 份资料 ===")
    for r in rows:
        print(f"  #{r['id']:<3} [{r['kind']:<4}] {r['n_chunks']:>4}块  "
              f"{r['title'][:46]}")

    byh = defaultdict(list)
    for r in rows:
        byh[r["content_hash"]].append(r)
    dups = {h: v for h, v in byh.items() if len(v) > 1}
    print(f"\n=== 内容完全相同的重复文档: {len(dups)} 组 ===")
    for h, v in dups.items():
        ids = ", ".join(f"#{x['id']} {x['title'][:26]}" for x in v)
        print(f"  {ids}")
        for x in v:
            print(f"      {x['path']}")
    conn.close()


if __name__ == "__main__":
    main()