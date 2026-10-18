r"""列出现有资料及其规模，为重导入做规划。

用法: python tools/inventory.py
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, db  # noqa: E402


def main() -> None:
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, path, kind, role, title FROM documents "
        "ORDER BY role, kind, id").fetchall()
    print(f"{'id':>4} {'kind':<6} {'role':<11} {'块':>6}  标题")
    print("-" * 96)
    by: collections.Counter = collections.Counter()
    for r in rows:
        n = conn.execute("SELECT COUNT(*) AS n FROM chunks WHERE doc_id=?",
                         (r["id"],)).fetchone()["n"]
        by[(r["kind"], r["role"])] += 1
        print(f"{r['id']:>4} {r['kind']:<6} {r['role']:<11} {n:>6}  "
              f"{r['title'][:48]}")
    print("-" * 96)
    print("按 (格式, 角色) 统计:")
    for (kind, role), n in sorted(by.items()):
        print(f"  {kind:<6} {role:<11} {n:>3} 份")
    print(f"  合计 {len(rows)} 份")

    # 待重导入的范围：非教材
    target = [r for r in rows if r["role"] != "textbook"]
    print(f"\n待重导入（非教材）: {len(target)} 份")
    kinds: collections.Counter = collections.Counter(
        r["kind"] for r in target)
    print(f"  按格式: {dict(kinds)}")

    # 逐份看需要多少页识图（有图片对象的页）
    from core import figures

    tot_pages = tot_need = 0