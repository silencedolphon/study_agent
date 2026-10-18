# C-0054 实测：B 问（历史 PDF 补图 = 重渲染 + 哈希复用，零模型费用）
#           C 问（页码联动：slide_N.png 与 source_ref=p.N 的块是否同页）
# 只读产品代码、只写 _c0054_tmp 临时目录，不碰 logs/
import hashlib
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(r"G:\study_agent")
sys.path.insert(0, str(ROOT))
from core import figures  # noqa: E402

PDF = ROOT / "raw" / "药理" / "抗心律失常药.pdf"
DOC_ID = 85
TMP = ROOT / "_c0054_tmp" / "render85"
DB = ROOT / "index" / "study.db"


def ro():
    c = sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def norm(s: str) -> str:
    """规范化：只留中日韩/字母/数字，去掉空白与标点。"""
    return "".join(ch for ch in (s or "") if ch.isalnum())


sep = "=" * 78
print(sep)
print("## 阶段 1：B 问 —— 用产品函数 figures.render_pages 重建 doc#85 的整页图")
print(f"   源文件: {PDF}")
print(f"   dpi = {getattr(figures.config, 'FIGURE_RENDER_DPI', '?')}")
print(f"   输出目录(临时): {TMP}")
TMP.mkdir(parents=True, exist_ok=True)
for old in TMP.glob("*.png"):
    old.unlink()

t0 = time.perf_counter()
slides = figures.render_pages(PDF, TMP, kind="pdf")
el = time.perf_counter() - t0
print(f"   [产物] 渲染页数 = {len(slides)}，耗时 = {el:.2f} 秒"
      f"（{el / max(len(slides), 1):.3f} 秒/页）")
sizes = sorted(slides)
print(f"   首页 = {slides[sizes[0]].name}，末页 = {slides[sizes[-1]].name}")
print(f"   目录内 png 实际个数 = {len(list(TMP.glob('slide_*.png')))}")

conn = ro()
lib = {int(r["page"]): r["image_hash"]
       for r in conn.execute("SELECT page, image_hash FROM figure_desc WHERE doc_id=85")}
print(f"   库内 figure_desc 行数 (doc_id=85) = {len(lib)}，页号范围 "
      f"{min(lib)}~{max(lib)}" if lib else "   库内无记录")

hit, miss, nomatch = [], [], []
for p, f in sorted(slides.items()):
    h = hashlib.sha256(f.read_bytes()).hexdigest()
    ref = lib.get(p)
    if ref is None:
        nomatch.append(p)
    elif ref == h:
        hit.append(p)
    else:
        miss.append((p, ref[:16], h[:16]))
print(f"   [哈希比对] 与库内 image_hash 一致 = {len(hit)} 页")
print(f"              不一致 = {len(miss)} 页 {miss[:5]}")
print(f"              库内无该页记录 = {len(nomatch)} 页 {nomatch[:5]}")
only_lib = sorted(set(lib) - set(slides))
print(f"              库内有记录但本轮没渲染 = {len(only_lib)} 页 {only_lib[:5]}")
print(f"   [结论] 零模型费用重建可行 = {len(miss) == 0}")
r = conn.execute("SELECT SUM(cost_cny) c FROM figure_desc WHERE doc_id=85").fetchone()
print(f"   [省下的钱] doc#85 库内 figure_desc.cost_cny 合计 = ¥{r['c']}")

print()
print(sep)
print("## 阶段 2：C 问 —— 图与 source_ref=p.N 的块是否同一页")
import pymupdf  # noqa: E402

doc = pymupdf.open(str(PDF))
print(f"   PDF 总页数 (doc.page_count) = {doc.page_count}")
DPI = int(getattr(figures.config, "FIGURE_RENDER_DPI", 110))


def page_text(p: int) -> str:
    return doc.load_page(p - 1).get_text()


def chunk_texts(p: int):
    return [dict(r) for r in conn.execute(
        "SELECT id, source_ref, text FROM chunks WHERE doc_id=? AND source_ref=?",
        (DOC_ID, f"p.{p}"))]


def cover(needle: str, hay: str) -> float:
    """needle 的字符有多少比例出现在 hay 里（滑窗无关的粗覆盖率）。"""
    n, h = norm(needle), norm(hay)
    if not n:
        return 0.0
    # 用 12 字滑窗切片统计命中率
    W = 12
    if len(n) <= W:
        return 1.0 if n in h else 0.0
    segs = [n[i:i + W] for i in range(0, len(n) - W + 1, W)]
    ok = sum(1 for s in segs if s in h)
    return ok / len(segs)


for p in (1, 11, 20, 50, 80):
    pg = doc.load_page(p - 1)
    txt = pg.get_text()
    pix = pg.get_pixmap(dpi=DPI)
    out = TMP / f"probe_p{p:02d}.png"
    pix.save(str(out))
    rect = pg.rect
    print(f"\n   --- p.{p} ---")
    print(f"   渲染源: doc.load_page({p - 1})  [0-based] → 像素 {pix.width}x{pix.height},"
          f" 页面尺寸 {rect.width:.1f}x{rect.height:.1f}pt")
    print(f"   该图 sha256(前16) = {hashlib.sha256(out.read_bytes()).hexdigest()[:16]}"
          f" | 磁盘 png 大小 {out.stat().st_size} B")
    print(f"   同页文本前 70 字: {txt.strip().replace(chr(10), ' / ')[:70]!r}")
    cs = chunk_texts(p)
    if not cs:
        print(f"   库中 source_ref='p.{p}' 的块: 无")
    for c in cs:
        cv = cover(c["text"], txt)
        # 反证：同一段文本在相邻页的覆盖率
        nb = {q: cover(c["text"], page_text(q))
              for q in (p - 1, p + 1) if 1 <= q <= doc.page_count}
        nbs = "，".join(f"p.{q}: {v:.2f}" for q, v in nb.items())
        print(f"   块 id={c['id']} ref='{c['source_ref']}' len={len(c['text'])}"
              f" → 本页覆盖率 {cv:.2f}（相邻页 {nbs or '无'}）")
        print(f"      块文本前 70 字: {c['text'].strip().replace(chr(10), ' / ')[:70]!r}")
    gs = [dict(r) for r in conn.execute(
        "SELECT id, source_ref, substr(text,1,60) t FROM chunks"
        " WHERE doc_id=? AND source_ref=?", (DOC_ID, f"p.{p} 图"))]
    for g in gs:
        print(f"   图块 id={g['id']} ref='{g['source_ref']}' :: {g['t']!r}")

print()
print(sep)
print("## 阶段 1 附：figure_desc 的 page_titles 与 PDF 页标题是否同页（抽 p.20）")
for r in conn.execute("SELECT page, page_title, substr(image_hash,1,16) h FROM figure_desc"
                      " WHERE doc_id=85 AND page IN (11,20,50) ORDER BY page"):
    print(f"   figure_desc p{r['page']} title={r['page_title']!r} hash={r['h']}")
    print(f"     PDF p{r['page']} 文本前 60: "
          f"{page_text(int(r['page'])).strip().replace(chr(10),' / ')[:60]!r}")
doc.close()
conn.close()
print(sep)
print("实测完成。临时产物在", TMP)
