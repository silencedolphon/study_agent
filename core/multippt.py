# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-13-7d97883c   (project study_agent; keep this line)
r"""一页拼了多张幻灯片的 PDF：检测 + 切分。

## 起因（用户实测反馈）

> 文件名开头带"卢"的 pdf 有点奇怪，它是好多张 PPT 放在一页组合成的，
> 这对识别和切块不是很友好……我的想法是在得到一份 pdf 时**先检测**是否
> 一页集成了多张 PPT 图片，如果没有就走原本的管线；如果有就需要专门有一个
> 程序把集成在上面的多张 PPT 切割下来，专门编好页码，然后再走原本的管线。

实测 `1-卢-抗高血压药 2025.pdf`：A4 纵向一页排 **2 列 × 3 行共 6 张 PPT**，
12 页装完一个章节。而正常导出的课件是 720x540 一页一张。

**不切开的后果**：
- 文字按 y 坐标串成一段，标题与正文的层级关系全没了；
- 切块按"页"走，一块里混着 6 张幻灯片的内容；
- 出处只能标 `p.1`，指不回到底是哪一张；
- 识图把 6 张缩略图一起喂给模型，字小图糊。

## 怎么检测

**不用"图片对象分组"**：一页里确实有多个图片对象，但它们是**幻灯片内部的小图**
（实测第 1 页 6 个图对象，尺寸 57x75、39x89、142x142……散落在各张幻灯片内部），
不是"一张幻灯片一个图"。按图片分组会切得乱七八糟。

改用**几何分割**：幻灯片在页面上是网格排列的，格子之间是干净的空白带。
把页面渲染成位图，逐行/逐列数非白像素，全白的行/列就是格子边界。
页眉（右上角日期）与页码（右下角）落在格子之外，不会破坏空白带。

**抽样检测**：只看前 `SAMPLE_PAGES` 页。这种资料**每一页都是多张**，
抽 3 页足够，不必为了判定把几百页全渲染一遍。

## 怎么切

`show_pdf_page` 把源页的某个矩形**贴**到新页上——是矢量搬运，不是截屏，
所以**不会二次损失清晰度**（用户抱怨的"放大看了上面的图片也失真了"
正是截图式做法的毛病）。

新 PDF 的页码 = 幻灯片序号（1..N），按**从左到右、从上到下**排；
同时写一份 `*.pages.tsv` 映射表（新页号 → 原第几页的第几格），
以后要回溯原始文件时用得上。
"""
from __future__ import annotations

from pathlib import Path

from . import config

# 判定"这一行是空白"：非白像素少于页面宽度的这个比例。
# 取 0.5%——页眉的几个字约占 3%~5%，不会被误判成空白；格子之间是真空白（0%）。
BLANK_RATIO = 0.005
# 空白带至少要多宽才算格子边界（占页面高度的比例），
# 太小会把幻灯片内部的自然行距误判成边界。
MIN_GAP_RATIO = 0.008
# 切出来的格子，宽高比要落在这个范围里才认（覆盖 4:3=1.33、16:9=1.78，留裕量）。
ASPECT_RANGE = (0.9, 2.4)
# 一页至少切出这么多格才算"多张幻灯片"。
MIN_CELLS = 4
# 抽样看前几页。
SAMPLE_PAGES = 5
# 抽样页里**至少这么大比例**是多张，才认定整份资料是这种。
#
# ⚠ 判据必须是"**多数**页是多张"，不能是"有一页是多张就切"。
# 实测踩过：`You-7-抗病毒药-抗结核药.pdf` 只有**第 1 页**是一张
# 列了六个分类框的目录页（被当成 6 格），其余 90 页都是正常单张，
# 却因此被判成多幻灯片资料，整份被切、产出一堆只有「抗」「结核」「物」
# 一个字的碎片页。**误切比不切更糟**——它会把一份正常资料毁掉。
MULTI_PAGE_RATIO = 0.6


DETECT_DPI = 100


def _runs(mask) -> list[tuple[int, int]]:
    """把一串 True 归并成 [(起, 止), ...]（止不含）。"""
    out: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def analyze_page(page, dpi: int = DETECT_DPI) -> dict:
    """判断一页是不是"多张幻灯片拼在一起"，并给出各格子的像素坐标。"""
    import numpy as np
    import pymupdf  # noqa: F401  （确保依赖在，错误信息更清楚）
    from PIL import Image

    pix = page.get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    gray = np.asarray(img.convert("L"))
    H, W = gray.shape
    ink = gray < 250
    blank_row = ink.sum(axis=1) < max(1, int(W * BLANK_RATIO))
    blank_col = ink.sum(axis=0) < max(1, int(H * BLANK_RATIO))

    y_sep = [(a + b) // 2 for a, b in _runs(blank_row)
             if b - a >= max(2, int(H * MIN_GAP_RATIO))]
    x_sep = [(a + b) // 2 for a, b in _runs(blank_col)
             if b - a >= max(2, int(W * MIN_GAP_RATIO))]

    def bands(seps: list[int], total: int) -> list[tuple[int, int]]:
        edges = [0] + sorted(seps) + [total]
        return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]

    cells = []
    for (y0, y1) in bands(y_sep, H):
        for (x0, x1) in bands(x_sep, W):
            if (y1 - y0) < H * 0.05 or (x1 - x0) < W * 0.05:
                continue
            if ink[y0:y1, x0:x1].sum() < (y1 - y0) * (x1 - x0) * 0.01:
                continue        # 空格子
            ar = (x1 - x0) / max(1, (y1 - y0))
            cells.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1,
                          "aspect": round(ar, 2),
                          "ok_aspect": ASPECT_RANGE[0] <= ar <= ASPECT_RANGE[1]})
    return {"size": (W, H), "y_sep": y_sep, "x_sep": x_sep,
            "cells": cells, "scale": dpi / 72.0}


def page_cells(page, dpi: int = DETECT_DPI) -> list[dict]:
    """这一页上各幻灯片的**pt 坐标**（按阅读顺序）。空列表 = 不是多张。"""
    r = analyze_page(page, dpi=dpi)
    good = [c for c in r["cells"] if c["ok_aspect"]]
    if len(good) < MIN_CELLS:
        return []
    good.sort(key=lambda c: (c["y0"], c["x0"]))
    s = r["scale"]
    return [{"x0": c["x0"] / s, "y0": c["y0"] / s,
             "x1": c["x1"] / s, "y1": c["y1"] / s} for c in good]


def detect(path: Path, sample: int = SAMPLE_PAGES) -> dict:
    """抽样检测一份 PDF 是不是"一页多张幻灯片"。

    返回 {multi: 是否, pages: {页号: [格子]}, total, cells, checked,
          checked_multi: 抽样的几页里有几页是多张}。

    **判据是"多数抽样页是多张"**（`MULTI_PAGE_RATIO`）——见该常量的说明：
    只看"有没有一页是多张"会把只有一张分类目录页的正常资料误切掉。
    """
    import pymupdf

    doc = pymupdf.open(str(path))
    try:
        total = doc.page_count
        pages: dict[int, list[dict]] = {}
        checked = min(sample, total)
        for pno in range(checked):
            cs = page_cells(doc.load_page(pno))
            if cs:
                pages[pno] = cs
        # 多数抽样页都是多张才算——避免"一页目录页"把整份资料带偏
        multi = len(pages) >= max(1, checked * MULTI_PAGE_RATIO)
        return {"multi": multi, "pages": pages, "total": total,
                "cells": sum(len(v) for v in pages.values()),
                "checked": checked, "checked_multi": len(pages),
                "path": str(path)}
    finally:
        doc.close()


def split(src: Path, dst: Path | None = None, *,
          dpi: int = DETECT_DPI, verbose: bool = False) -> dict:
    """切开整份 PDF，写出**一页一张**的新 PDF + 页码映射表。

    不是"一页多张"的资料会原样返回 `{"ok": False}`，调用方据此走原管线。
    """
    import pymupdf

    src = Path(src)
    info = detect(src)
    if not info["multi"]:
        return {"ok": False, "reason": "不是'一页多张幻灯片'的 PDF",
                "total": info["total"],
                "checked_multi": info.get("checked_multi", 0),
                "checked": info.get("checked", 0)}
    dst = Path(dst) if dst else src.with_name(src.stem + "_slides.pdf")

    doc = pymupdf.open(str(src))
    try:
        out = pymupdf.open()
        mapping: list[tuple[int, int, int]] = []   # (新页号, 原页号, 页内第几格)
        n = 0
        for pno in range(doc.page_count):
            cells = page_cells(doc.load_page(pno), dpi=dpi)
            if not cells:
                # 不是多张的页（例如纯文字页）原样带过去，不丢内容
                sp = doc.load_page(pno)
                np_ = out.new_page(width=sp.rect.width, height=sp.rect.height)
                np_.show_pdf_page(np_.rect, doc, pno)
                n += 1
                mapping.append((n, pno + 1, 0))
                continue
            for i, c in enumerate(cells, start=1):
                r = pymupdf.Rect(c["x0"], c["y0"], c["x1"], c["y1"])
                np_ = out.new_page(width=r.width, height=r.height)
                np_.show_pdf_page(np_.rect, doc, pno, clip=r)
                n += 1
                mapping.append((n, pno + 1, i))
        dst.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(dst), garbage=3, deflate=True)
        out.close()
    finally:
        doc.close()

    mp = dst.with_suffix(".pages.tsv")
    mp.write_text("新页号\t原PDF页\t原页内第几格\n"
                  + "\n".join(f"{a}\t{b}\t{c or '-'}" for a, b, c in mapping),
                  encoding="utf-8")
    if verbose:
        print(f"  切出 {n} 张幻灯片（原 {info['total']} 页）-> {dst.name}")
    return {"ok": True, "out": str(dst), "slides": n, "map": str(mp),
            "total": info["total"]}


def needs_split(path: Path) -> bool:
    """要不要切开（拿不到结论时按"不用"处理，绝不让检测挡住入库）。"""
    if not getattr(config, "SPLIT_MULTISLIDE", 1):
        return False
    if Path(path).suffix.lower() != ".pdf":
        return False
    try:
        return bool(detect(Path(path))["multi"])
    except Exception:  # noqa: BLE001
        # 检测只是增强：出任何问题都当作"普通 PDF"，走原管线。
        return False