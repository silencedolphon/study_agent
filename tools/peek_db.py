r"""只读查看库当前状态——可在重导入运行时安全使用（不抢写锁）。

用法: python tools/peek_db.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config  # noqa: E402


def main() -> None:
    uri = f"file:{config.ROOT / 'index' / 'study.db'}?mode=ro"
    c = sqlite3.connect(uri, uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    tot = c.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    fig = c.execute("SELECT COUNT(*) AS n FROM chunks WHERE source_ref LIKE ?",
                    ("p.% 图",)).fetchone()["n"]
    tbl = c.execute("SELECT COUNT(*) AS n FROM chunks WHERE text LIKE ?",
                    ("% → %: %",)).fetchone()["n"]
    fd = c.execute("SELECT COUNT(*) AS pages, COALESCE(SUM(no_info),0) AS ni, "
                   "COALESCE(SUM(cost_cny),0) AS cost, "
                   "COALESCE(SUM(n_chunks),0) AS k FROM figure_desc").fetchone()
    print(f"块 {tot}（正文 {tot - fig} + 识图 {fig}）；疑似表格行块 {tbl}")
    pages = fd["pages"] or 1
    print(f"识图记录 {fd['pages']} 页，判无信息 {fd['ni']} 页"
          f"（{fd['ni'] / pages * 100:.0f}%），成本 ¥{fd['cost']:.4f}")
    print(f"识图页均成本 ¥{fd['cost'] / pages:.4f}")
    print()
    print("最近写入的识图块示例:")
    for r in c.execute(
            "SELECT source_ref, substr(text,1,90) AS t FROM chunks "
            "WHERE source_ref LIKE 'p.% 图' ORDER BY id DESC LIMIT 3"):
        print(f"  {r['source_ref']:<9} {r['t']}")
    print()
    print("每份资料的进度:")
    for r in c.execute(
            "SELECT d.id, d.title, d.kind, "
            "(SELECT COUNT(*) FROM figure_desc f WHERE f.doc_id=d.id) AS fp, "
            "(SELECT COALESCE(SUM(f.cost_cny),0) FROM figure_desc f "
            " WHERE f.doc_id=d.id) AS cost "
            "FROM documents d WHERE d.role!='textbook' ORDER BY d.kind, d.id"):
        mark = "✓" if r["fp"] else " "
        print(f"  {mark} doc#{r['id']:<3} {r['kind']:<5} {r['fp']:>3}页 "
              f"¥{r['cost']:>7.4f}  {r['title'][:38]}")
    c.close()


if __name__ == "__main__":
    main()