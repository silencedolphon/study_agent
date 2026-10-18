# C-0054 只读取证 2：chunks.source_ref 形态 + figure_desc 哈希样本
import sqlite3, re
DB = r"G:\study_agent\index\study.db"
conn = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
print("== chunks 表结构 ==")
print([r[1] for r in conn.execute("PRAGMA table_info(chunks)")])
print("\n== doc#85 的 source_ref 分布（前 25 种）==")
rows = conn.execute(
    "SELECT source_ref, COUNT(*) n FROM chunks WHERE doc_id=85 GROUP BY source_ref"
    " ORDER BY source_ref LIMIT 25").fetchall()
for r in rows:
    print(f"  {r['source_ref']!r} x{r['n']}")
print("\n== doc#85 正文块（非图）样本 ==")
for r in conn.execute(
        "SELECT id, source_ref, substr(text,1,90) t, kind FROM chunks"
        " WHERE doc_id=85 AND source_ref NOT LIKE '%图%' LIMIT 6"):
    print(f"  id={r['id']} ref={r['source_ref']!r} kind={r['kind']!r} :: {r['t']!r}")
print("\n== doc#85 图块样本 ==")
for r in conn.execute(
        "SELECT id, source_ref, substr(text,1,80) t FROM chunks"
        " WHERE doc_id=85 AND source_ref LIKE '%图%' LIMIT 4"):
    print(f"  id={r['id']} ref={r['source_ref']!r} :: {r['t']!r}")
print("\n== doc#85 figure_desc 前 5 行（page/image_hash 前 16 位/cost）==")
for r in conn.execute(
        "SELECT page, substr(image_hash,1,16) h, n_chunks, no_info, cost_cny,"
        " length(description) dl FROM figure_desc WHERE doc_id=85 ORDER BY page LIMIT 5"):
    print(f"  p{r['page']:>3} hash={r['h']} n_chunks={r['n_chunks']} no_info={r['no_info']}"
          f" cost={r['cost_cny']} desc_len={r['dl']}")
print("\n== doc#85 成本合计 ==")
r = conn.execute("SELECT COUNT(*) n, SUM(cost_cny) c, MAX(page) mp FROM figure_desc"
                 " WHERE doc_id=85").fetchone()
print(f"  行数={r['n']} 成本合计=¥{r['c']} 最大页={r['mp']}")
print("\n== doc#85 documents 行 ==")
r = conn.execute("SELECT * FROM documents WHERE id=85").fetchone()
print("  path=", r["path"], "| abs_path=", r["abs_path"], "| kind=", r["kind"], "| status=", r["status"])
print("  warnings=", str(r["warnings"])[:300])
