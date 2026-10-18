# C-0054 实测 3：C 问的独立交叉验证
# 对**渲染出来的 PNG** 做本地 OCR（rapidocr，离线、零费用、不联网、不调视觉模型），
# 再与库中 source_ref='p.N' 的块文本比对 —— 像素通道 vs 库文本通道的一致性
import sqlite3
import sys
from pathlib import Path

ROOT = Path(r"G:\study_agent")
sys.path.insert(0, str(ROOT))
from core import extract as _ex  # noqa: E402

DB = ROOT / "index" / "study.db"
DOC_ID = 85
TMP = ROOT / "_c0054_tmp" / "render85"

conn = sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)
conn.row_factory = sqlite3.Row


def norm(s):
    return "".join(ch for ch in (s or "") if ch.isalnum())


def cover(needle, hay, w=6):
    n, h = norm(needle), norm(hay)
    if len(n) < w:
        return float(n in h) if n else 0.0
    segs = [n[i:i + w] for i in range(0, len(n) - w + 1, 3)]
    return sum(1 for s in segs if s in h) / max(len(segs), 1)


engine = _ex._get_ocr()
print("OCR 引擎:", type(engine).__name__ if engine else "None（未安装）")
if engine is None:
    raise SystemExit("无 OCR，跳过")

import pymupdf  # noqa: E402
doc = pymupdf.open(str(ROOT / "raw" / "药理" / "抗心律失常药.pdf"))

for p in (11, 20, 50):
    png = TMP / f"slide_{p:02d}.png"
    boxes = _ex._ocr_boxes(engine, str(png))
    lines = _ex._drop_ui_noise([b[4] for b in boxes])
    ocr_txt = "\n".join(lines)
    print("=" * 78)
    print(f"## p.{p}  图 = {png.name}（{png.stat().st_size} B）")
    print(f"   OCR 行数 = {len(lines)}；OCR 文本前 160 字：")
    print("   " + ocr_txt[:160].replace("\n", " / "))
    # 库中该页的正文块
    for r in conn.execute("SELECT id, source_ref, text FROM chunks WHERE doc_id=?"
                          " AND source_ref=?", (DOC_ID, f"p.{p}")):
        c = cover(r["text"], ocr_txt)
        others = {}
        for q in (p - 1, p + 1):
            row = conn.execute("SELECT text FROM chunks WHERE doc_id=? AND source_ref=?",
                               (DOC_ID, f"p.{q}")).fetchone()
            if row:
                others[f"p.{q}"] = round(cover(row["text"], ocr_txt), 2)
        print(f"   ← 库中块 id={r['id']} ref='{r['source_ref']}'：与**本页图 OCR** "
              f"覆盖率 = {c:.2f}")
        print(f"      同一张图 OCR 与相邻页块的覆盖率：{others}（应显著更低）")
    # 图块（识图产物）里出现的词，看图上 OCR 能不能找到 —— 反向印证
    gs = [dict(r) for r in conn.execute(
        "SELECT text FROM chunks WHERE doc_id=? AND source_ref=?", (DOC_ID, f"p.{p} 图"))]
    if gs:
        key = ["窦房结", "不应期", "动作电位", "奎尼丁", "金鸡纳", "胃肠道", "有效不应期"]
        found = [k for k in key if k in ocr_txt]
        print(f"   OCR 命中关键词 {found}（关键词取自图块描述："
              f"{[k for k in key if any(k in g['text'] for g in gs)]}）")
    # pymupdf 该页文本层的对照
    print(f"   PDF p.{p} 文本层前 90 字："
          f"{doc.load_page(p - 1).get_text().strip().replace(chr(10), ' / ')[:90]!r}")
doc.close()
conn.close()
