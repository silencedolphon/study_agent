# -*- coding: utf-8 -*-
"""C-0090 等价性实测：分批调 `ocr_pdf_pages` 与一次性调用**逐块相同**吗？

第二版重跑脚本（`c0090_rerun2.py`）为了断点续跑，把 452 页拆成每批 40 页调用。
这个做法的正当性不能只靠读代码，必须实测。本脚本从 doc#91 真书里抽 8 页
（正文中间，页码 100~107）造成小 PDF，然后：

  A. 一次性 `ocr_pdf_pages(pdf, [1..8])`
  B. 分批 `ocr_pdf_pages(pdf, [1,2,3]) + ([4,5,6]) + ([7,8])`，按第二版的 `_merge` 合并

逐块比较：块数、每块的 (page, para, kind, group, text)、
以及 `meta['table_pages']` / `meta['table_overviews']` / `warnings`。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_batch_equiv.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0090")
TMP = ROOT / "_tmp_c0090"
SRC = ROOT / "raw" / "药理" / "药理学 第6版 刘晓东.pdf"


def main() -> int:
    import pymupdf
    from core import extract
    from c0090_rerun2 import _merge

    TMP.mkdir(parents=True, exist_ok=True)
    mini = TMP / "equiv_8p.pdf"
    src = pymupdf.open(str(SRC))
    out = pymupdf.open()
    out.insert_pdf(src, from_page=99, to_page=106)    # 0-based -> 第 100~107 页
    out.save(str(mini))
    out.close()
    src.close()
    print(f"抽出 8 页 -> {mini}（{mini.stat().st_size / 1048576:.1f}MB）")

    pages = [1, 2, 3, 4, 5, 6, 7, 8]
    print("\nA) 一次性 OCR ...")
    a = extract.ocr_pdf_pages(mini, pages)
    print(f"   {len(a.blocks)} 块；table_pages="
          f"{a.meta.get('table_pages')}；warnings={a.warnings}")

    print("\nB) 分批 OCR（3+3+2）...")
    parts = [extract.ocr_pdf_pages(mini, c)
             for c in ([1, 2, 3], [4, 5, 6], [7, 8])]
    for i, p in enumerate(parts):
        print(f"   批{i + 1}: {len(p.blocks)} 块 "
              f"table_pages={p.meta.get('table_pages')}")
    b = _merge(parts)
    print(f"   合并后 {len(b.blocks)} 块；table_pages="
          f"{b.meta.get('table_pages')}；warnings={b.warnings}")

    def sig(res):
        return [(x.page, x.para, x.kind, x.group, x.text) for x in res.blocks]

    sa, sb = sig(a), sig(b)
    same_blocks = sa == sb
    diff = []
    if not same_blocks:
        for i, (x, y) in enumerate(zip(sa, sb)):
            if x != y:
                diff.append({"i": i, "one_shot": x[:4], "batched": y[:4]})
                if len(diff) >= 5:
                    break
    res = {
        "mini_pdf": str(mini),
        "pages": pages,
        "A_blocks": len(a.blocks), "B_blocks": len(b.blocks),
        "blocks_identical": same_blocks,
        "table_pages_A": a.meta.get("table_pages"),
        "table_pages_B": b.meta.get("table_pages"),
        "table_pages_identical": a.meta.get("table_pages") == b.meta.get("table_pages"),
        "table_overviews_identical":
            (a.meta.get("table_overviews") or {}) == (b.meta.get("table_overviews") or {}),
        "warnings_A": a.warnings, "warnings_B": b.warnings,
        "warnings_identical": a.warnings == b.warnings,
        "first_diffs": diff,
    }
    res["equivalent"] = bool(same_blocks
                             and res["table_pages_identical"]
                             and res["table_overviews_identical"]
                             and res["warnings_identical"])
    print("\n" + "=" * 60)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    (CARD / "batch_equiv.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结论：分批与一次性 {'完全等价' if res['equivalent'] else '不等价 ⚠'}")
    return 0 if res["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
