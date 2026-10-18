# -*- coding: utf-8 -*-
"""卡 C-0089 夹具：造一份**迷你扫描教材 PDF**，供「教材入库」专线端到端验证。

## 为什么要造一份，而不是用库里那份真教材（doc#91《药理学 第6版 刘晓东》）

那份是 452 页真扫描件，本地 OCR 要跑约 2 小时（实测 16 秒/页），
远超验收槽；而本卡要证的是**接线**——按钮 → 拷进 `raw\\教材\\` → 本地 OCR
→ 按章切块 → 入库 → 回执列章。

## 这份夹具长什么样（19 页，实测 189KB）

```
p.1 - p.16   前置页：**有文字层**（前言/使用说明，每页 >100 字）
p.17         扫描图：第1章绪论 + 两行正文      ← 走本地 OCR
p.18         扫描图：第2章药物代谢动力学 + 两行正文
p.19         扫描图：第3章药物效应动力学 + 两行正文
```

三条刻意如此的设计，都是被 `core/textbook.py` 的真实规则逼出来的
（不是随手写的，改之前先看 `detect_chapters`）：

1. **章标题不写字间空格**（`第1章绪论`）。实测写成 `第1章 绪论` 时，
   OCR 会把标题拆成两块（`第3章` / `药物效应动力学`），而章识别要的是
   "**整行**以第X章开头"（`_CHAPTER_LINE`）⇒ 少认一章 ⇒ 降级。
2. **每章标题后面必须跟一行 ≥24 字的正文**。`_looks_like_chapter_start`
   的判据 (b) 是"章名齐备 + 后面跟着正文"（`_next_block_starts_body`：
   下一块开头有 ≥24 字的行）。OCR 的输出是"一行一块"，所以这行正文
   就是判据的落点——少了它，一章都认不出来（实测：0 章 → 降级）。
3. **第 2、3 章必须落在 p.17 之后**。`_FRONT_MATTER_MAX_PAGE = 16` 那条
   硬约束规定"前置页（≤16 页）里认出的第 2 章及以后，只有章名与目录逐字
   相同才留"——而本夹具没有目录页 ⇒ 放在前面会被整章丢掉。

前置 16 页刻意用**文字层**而不是空白扫描页：OCR 是这条链路上最慢的一段
（实测 1.9 秒/页），16 页空白扫描件要白等 30 秒。内容页仍然是**真图片、
真 OCR**——免费、不调视觉模型的本地 rapidocr。

用法（绝对路径，不依赖 cwd）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 tools\\c0089_make_textbook.py <目标.pdf>
    ... tools\\c0089_make_textbook.py <目标.pdf> --check     # 顺带自检（要跑 OCR，约 8 秒）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 迷你教材的正文：三章，章名与 doc#91 的真章名同源（绪论/药物代谢动力学/
# 药物效应动力学），读起来像一本真教材的开头。
CHAPTERS = [
    (1, "绪论"),
    (2, "药物代谢动力学"),
    (3, "药物效应动力学"),
]
BODY1 = "药理学是研究药物与机体（包括病原体）相互作用规律及其机制的一门医学基础学科。"
BODY2 = "本章介绍基本概念与研究方法，为后续各章的学习打下必要的基础。"
FRONT_PAGES = 16
# 前置页文字：每页 >100 字（`extract._SCAN_MIN_CHARS_PER_PAGE` 是 30），
# 所以这些页**不会**被判成扫描页、不会进 OCR。
# 首行刻意就是**书名**：入库时的文档标题由正文标题行推出来（`ingest._title_of`），
# 而它同时是要写进每块面包屑的"书名"那一段（「书名 > 第X章 章名」）。
# 第一版把首行写成了"…· 前置 1"，结果面包屑变成「药理学（第6版）· 前置 1 > …」。
FRONT_TEXT = (
    "本页为前言与使用说明。本书供高等医药院校药学类专业教学使用，"
    "全书按章编排，每章含学习目标、正文、本章小结与思考题。"
    "书中药名以《中华人民共和国药典》与通用名为准，"
    "剂量供教学参考，临床应用请遵医嘱。"
)
FRONT_TITLE = "药理学（第6版）"


def build(dest: Path, front_pages: int = FRONT_PAGES) -> dict:
    """造出这份夹具，返回自述信息（页数、大小）。"""
    import pymupdf

    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()

    # ---- 前置页：文字层（不 OCR）----
    # ⚠ 书名标题行**只出现在第 1 页**。第一版每页都印一遍，结果每个前置块都带
    # 一层"标题"面包屑，教材专线再加书名前缀 ⇒ 库里出现「书名 > 书名 > 书名」
    # 这种三层路径（真教材的前置页不会每页都印一次书名）。
    for i in range(1, front_pages + 1):
        p = doc.new_page(width=595, height=842)
        y = 150
        if i == 1:
            p.insert_text((64, 80), FRONT_TITLE, fontname="china-s", fontsize=24)
            p.insert_text((64, 118), "主编 刘晓东　人民卫生出版社",
                          fontname="china-s", fontsize=11)
            y = 156
        p.insert_text((64, y), FRONT_TEXT, fontname="china-s", fontsize=11)

    # ---- 内容页：先在有文字层的临时文档里排好，再**渲染成图片**插回去 ----
    # 这一步是这份夹具的全部意义：得到的是"没有文字层的图片页"，
    # 只有本地 OCR 才能把它变成文字（真教材的扫描件就是这个形态）。
    tmp = pymupdf.open()
    for num, name in CHAPTERS:
        p = tmp.new_page(width=595, height=842)
        p.insert_text((72, 110), f"第{num}章{name}",
                      fontname="china-s", fontsize=22)
        p.insert_text((72, 170), BODY1, fontname="china-s", fontsize=13)
        p.insert_text((72, 200), BODY2, fontname="china-s", fontsize=13)
    for i in range(tmp.page_count):
        pix = tmp.load_page(i).get_pixmap(dpi=150)
        page = doc.new_page(width=595, height=842)
        # JPEG 而不是 PNG：150dpi 的中文页 PNG 一页 4.7MB，JPEG 只要几十 KB，
        # 而 OCR 对这点压缩噪声不敏感（实测识别结果一致）。
        page.insert_image(pymupdf.Rect(0, 0, 595, 842),
                          stream=pix.tobytes("jpg", jpg_quality=88))
    tmp.close()
    doc.save(str(dest))
    n = doc.page_count
    doc.close()
    return {"path": str(dest), "pages": n, "front_pages": front_pages,
            "scan_pages": [n - len(CHAPTERS) + 1 + k
                           for k in range(len(CHAPTERS))],
            "chapters": [f"第{a}章{b}" for a, b in CHAPTERS],
            "size_bytes": dest.stat().st_size}


def check(dest: Path) -> int:
    """自检：本地 OCR → 章识别，打印结果（真跑 OCR，约 8 秒）。"""
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from core import extract, textbook

    t0 = time.time()
    res = extract.extract(dest)
    scanned = list((res.meta or {}).get("scanned_pages") or [])
    print(f"[1] extract {time.time()-t0:.1f}s 块={len(res.blocks)} "
          f"scanned={scanned}")
    if scanned:
        t0 = time.time()
        ocr = extract.ocr_pdf_pages(dest, scanned)
        print(f"[2] 本地 OCR {len(scanned)} 页 {time.time()-t0:.1f}s "
              f"块={len(ocr.blocks)}（{time.time()-t0:.1f}s / {len(scanned)} 页）")
        for w in (ocr.warnings or [])[:4]:
            print("    !! " + str(w))
        res.blocks.extend(ocr.blocks)
    ch = textbook.detect_chapters(res.blocks, book_title="药理学（第6版）")
    print(f"[3] 章识别：{len(ch.body)} 章 fallback={ch.fallback} "
          f"warnings={ch.warnings}")
    for c in ch.items:
        print(f"    #{c.number} {c.name!r} p.{c.page_start}-{c.page_end}")
    ok = (not ch.fallback) and len(ch.body) == len(CHAPTERS)
    print(("C0089-FIXTURE-OK" if ok else "C0089-FIXTURE-BAD")
          + f"  章数 {len(ch.body)}/{len(CHAPTERS)}")
    return 0 if ok else 2


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("用法: python tools\\c0089_make_textbook.py <目标.pdf> [--check]")
        return 2
    dest = Path(args[0]).resolve()
    info = build(dest)
    print(f"已生成 {info['path']}")
    print(f"  {info['pages']} 页（前置 {info['front_pages']} 页文字层 + "
          f"扫描页 {info['scan_pages']}），{info['size_bytes']/1024:.0f} KB")
    print(f"  章：{' / '.join(info['chapters'])}")
    if "--check" in sys.argv:
        return check(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
