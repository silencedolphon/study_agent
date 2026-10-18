# C-0054 实测 6：D 问 —— 转换产物能否被现有 PPTX 链路直接吃下
import sys
from pathlib import Path

ROOT = Path(r"G:\study_agent")
sys.path.insert(0, str(ROOT))
from core import extract, figures  # noqa: E402

SRC_PPT = ROOT / "subjects" / "分析化学" / "raw" / "Chapter12(上课用).ppt"
CONV = ROOT / "_c0054_tmp" / "pptconv" / "Chapter12(上课用).pptx"

print("== 1) detect_kind ==")
print("  .ppt  ->", extract.detect_kind(SRC_PPT))
print("  .pptx ->", extract.detect_kind(CONV))
print("  .ppt 在 SUPPORTED_EXT 里?", SRC_PPT.suffix.lower() in extract.SUPPORTED_EXT)

print("\n== 2) 现状：extract(.ppt) 的结果 ==")
r0 = extract.extract(SRC_PPT)
print("  blocks =", len(r0.blocks), "| warnings =", r0.warnings)

print("\n== 3) 转换产物：extract(.pptx) 的结果 ==")
r1 = extract.extract(CONV)
print("  blocks =", len(r1.blocks), "| chars =", r1.char_count,
      "| warnings =", r1.warnings)
pages = sorted({b.page for b in r1.blocks if b.page > 0})
print(f"  出现的页号 {pages[0]}~{pages[-1]}（{len(pages)} 页有块）")
print("  meta:", {k: v for k, v in r1.meta.items() if k in ("title", "page_count")})
for b in r1.blocks[:6]:
    print(f"    p{b.page} kind={b.kind} :: {b.text[:60]!r}")

print("\n== 4) 现有 PPTX 专属能力对转换产物是否可用 ==")
hm = figures.page_has_image(CONV, "pptx")
print("  page_has_image(pptx) ->",
      "None（无判据）" if hm is None else
      f"{len(hm)} 页，其中有图 {sum(1 for v in hm.values() if v)} 页"
      f"（这决定能否走'按形状过滤'省调用）")
print("  page_has_image(.ppt) ->", figures.page_has_image(SRC_PPT, "pptx"))
deck, stexts = figures.extract_pages(CONV, "pptx")
print(f"  extract_pages(pptx) -> deck={deck!r}, 页数={len(stexts)}")
print("    首页:", {k: (v[:40] if isinstance(v, str) else v)
                   for k, v in (stexts[0] if stexts else {}).items()})
