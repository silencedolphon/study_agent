# C-0054 实测 7：D 问端到端 —— 转换产物能否走完 文本提取 + 整页渲染（页图）
import sys
import time
from pathlib import Path

ROOT = Path(r"G:\study_agent")
sys.path.insert(0, str(ROOT))
from core import figures  # noqa: E402

CONV = ROOT / "_c0054_tmp" / "pptconv" / "Chapter12(上课用).pptx"
OUT = ROOT / "_c0054_tmp" / "render_conv"

print("== extract_pages（整份骨架：标题 + 每页文本）==")
deck, stexts = figures.extract_pages(CONV, "pptx")
print(f"  deck={deck!r} 页数={len(stexts)}")
for s in stexts[:3]:
    print(f"    p{s['page']} title={s['title'][:36]!r} body={len(s['body'])} 字")

print("\n== render_pages（整页 PNG，走 PowerPoint COM）==")
OUT.mkdir(parents=True, exist_ok=True)
for f in OUT.glob("*.png"):
    f.unlink()
t0 = time.perf_counter()
slides = figures.render_pages(CONV, OUT, kind="pptx")
el = time.perf_counter() - t0
pngs = sorted(OUT.glob("slide_*.png"))
mb = sum(f.stat().st_size for f in pngs) / 1048576
print(f"  render_pages 返回 {len(slides)} 页，目录内 slide_*.png = {len(pngs)} 张")
print(f"  耗时 {el:.2f} 秒，总体积 {mb:.2f} MB")
print(f"  页号范围 {min(slides)}~{max(slides)}；文件名样本 "
      f"{[p.name for p in pngs[:3]]} ... {pngs[-1].name if pngs else '-'}")
