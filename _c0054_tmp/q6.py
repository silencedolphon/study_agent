# C-0054 实测 4：B 问第二份样本 + 18 份无图 PDF 的补图代价统计
# 只读 PDF / 只写 _c0054_tmp；不碰 logs/、不碰数据库
import hashlib
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(r"G:\study_agent")
sys.path.insert(0, str(ROOT))
from core import figures  # noqa: E402
import pymupdf  # noqa: E402

DB = ROOT / "index" / "study.db"
conn = sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

# 默认库里 kind=pdf 且 figure_desc 无记录、或磁盘无 png 的文档
rows = conn.execute("""
  SELECT d.id, d.path, d.kind, COALESCE(f.n,0) fits, COALESCE(f.c,0) fcost
  FROM documents d LEFT JOIN (
     SELECT doc_id, COUNT(*) n, SUM(cost_cny) c FROM figure_desc GROUP BY doc_id
  ) f ON f.doc_id = d.id
  WHERE d.kind='pdf' ORDER BY d.id""").fetchall()
print("== 默认库 PDF 文档：figure_desc 记录数 / 磁盘 png 数 / 页数 ==")
tot_pages = 0
todo = []
for r in rows:
    p = ROOT / str(r["path"])
    stem = p.stem
    deck = ROOT / "logs" / "figure_desc" / figures._safe_dirname(stem)
    npng = len(list(deck.glob("slide_*.png"))) if deck.is_dir() else -1
    try:
        doc = pymupdf.open(str(p))
        pc = doc.page_count
        doc.close()
    except Exception as e:
        pc = f"打开失败 {type(e).__name__}"
    mark = ""
    if npng <= 0:
        mark = "  <== 磁盘无页图"
        todo.append((int(r["id"]), p, pc, r["fits"], r["fcost"]))
    if isinstance(pc, int):
        tot_pages += pc
    print(f"  doc#{r['id']:>3} 页数={pc!s:>5} fig记录={r['fits']:>3} 磁盘png={npng:>4} "
          f"已花=¥{(r['fcost'] or 0):.4f}{mark} :: {stem[:40]}")
print(f"  默认库 PDF 总页数 = {tot_pages}")
print(f"  磁盘无页图的 PDF = {len(todo)} 份，合计页数 = "
      f"{sum(t[2] for t in todo if isinstance(t[2], int))}")

print()
print("=" * 78)
print("## 第二份样本实测：doc#89（胰岛素和口服降糖药-课件童擎一20241203，注释里那份'¥1.3345'）")
tgt = [t for t in todo if t[0] == 89]
if not tgt:
    print("  doc#89 不在待补名单里，跳过")
else:
    did, pdf, pc, fits, fcost = tgt[0]
    out = ROOT / "_c0054_tmp" / "render89"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    t0 = time.perf_counter()
    slides = figures.render_pages(pdf, out, kind="pdf")
    el = time.perf_counter() - t0
    lib = {int(r["page"]): r["image_hash"] for r in conn.execute(
        "SELECT page, image_hash FROM figure_desc WHERE doc_id=?", (did,))}
    hit = miss = 0
    for pg, f in sorted(slides.items()):
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        if lib.get(pg) == h:
            hit += 1
        elif pg in lib:
            miss += 1
            print(f"    不一致 p{pg}")
    nbytes = sum(f.stat().st_size for f in out.glob("slide_*.png"))
    print(f"  源文件: {pdf.name}（PDF 页数 {pc}）")
    print(f"  渲染 {len(slides)} 页，耗时 {el:.2f} 秒（{el / len(slides):.3f} 秒/页）")
    print(f"  产物总体积 {nbytes / 1048576:.1f} MB（{nbytes / len(slides) / 1024:.0f} KB/页）")
    print(f"  与库内 image_hash 一致 = {hit} / {len(lib)}，不一致 = {miss}")
    print(f"  该份库内已花 cost_cny 合计 = ¥{fcost:.4f} —— 重建页图不动这笔钱")
    print(f"  产物目录（临时）: {out}")

print()
print("=" * 78)
print("## 补图代价外推（按实测 0.1375 秒/页 与 0.42 MB/页）")
n = sum(t[2] for t in todo if isinstance(t[2], int))
print(f"  待补 PDF {len(todo)} 份、{n} 页")
print(f"  → 一次性渲染耗时约 {n * 0.1375 / 60:.1f} 分钟（单线程、本地 CPU、零模型调用）")
print(f"  → 磁盘新增约 {n * 0.42:.0f} MB")
print(f"  → 视觉模型调用 = 0 次，费用 = ¥0")
conn.close()
