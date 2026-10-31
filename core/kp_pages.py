"""纯页码 / 页列表 / 结构分页工具（S16 模块拆分第二步+）。

这些函数不读 DB、不依赖 config、不触发模型，只做字符串/整数转换；从
`core/kp_build.py` 抽出，`kp_build` 继续以旧名字导出，旧调用方和旧测试不需要改。

口径与抽出前逐字一致：
- `source_ref` 支持 `p.3`、`p.3 图`、`p.1-2`；认不出返回 None；
- 页列表标签只看连续性，不改顺序（内部排序去重）；
- 模型返回的 pages 支持 `[1,2]`、`"1-3"`、`"1、2"` 等写法；
- 结构分页按「页数/字符数双阈值，谁先到就切」，单页超阈值独占一段。
"""
from __future__ import annotations

import re

REF_RE = re.compile(r"^p\.(\d+)(?:-(\d+))?(\s*图)?$")


def parse_ref(ref: str):
    """解析 `source_ref` → (起始页, 结束页, 是否识图块)；认不出来返回 None。"""
    m = REF_RE.match((ref or "").strip())
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2) or a)
    if b < a:
        a, b = b, a
    return a, b, bool(m.group(3))


def pages_label(pages) -> str:
    """页列表 → `1-3、7` 这样的紧凑标签（只看连续性，不改顺序）。"""
    ps = sorted({int(x) for x in (pages or [])})
    if not ps:
        return ""
    out, a, b = [], ps[0], ps[0]
    for x in ps[1:]:
        if x == b + 1:
            b = x
            continue
        out.append(str(a) if a == b else "%d-%d" % (a, b))
        a = b = x
    out.append(str(a) if a == b else "%d-%d" % (a, b))
    return "、".join(out)


def page_list(raw):
    """把模型给的 pages 读成升序去重整数列表（认 `[1,2]` / `"1-3"` / `"1、2"`）。"""
    out = []
    if isinstance(raw, (int, float)):
        raw = [raw]
    if isinstance(raw, str):
        parts = re.split('[、,，;；\\s]+', raw.strip())
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        return []
    for x in parts:
        if isinstance(x, (int, float)):
            out.append(int(x))
            continue
        s = str(x or '').strip()
        m = re.match('^p?\\.?(\\d+)\\s*[-~—]\\s*p?\\.?(\\d+)$', s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out.extend(range(min(a, b), max(a, b) + 1))
            continue
        for t in re.findall('\\d+', s):
            out.append(int(t))
    return sorted({x for x in out if x > 0})


def page_text_for_structure(p: int, ch: dict, cap: int = 420) -> str:
    """该页喂给结构模型的文本（截断到 `cap` 字符，截断处标 `……`）。"""
    bs = sorted(ch["blocks"].get(p) or [], key=lambda x: (x["ord"], x["chunk_id"]))
    parts = []
    for b in bs:
        t = re.sub(r"\s+", " ", b["text"] or "").strip()
        if t:
            parts.append("〔%s〕%s" % (b["kind"], t))
    s = " ".join(parts)
    if len(s) > cap:
        s = s[:cap] + "……"
    return s


def plan_structure_segments(ch: dict, *, page_cap: int = 420,
                            seg_pages: int = 12, seg_chars: int = 6000) -> list:
    """把全页切成若干段（map 的输入）：页数与字符数双阈值，谁先到就切。

    返回 [{index, a, b, pages, chars}]（升序、无缝覆盖 `pages_all`）。
    """
    pages = list(ch["pages_all"])
    if not pages:
        return []
    segs: list = []
    cur: list = []
    cur_chars = 0
    for p in pages:
        t = page_text_for_structure(p, ch, page_cap)
        n = len(t)
        if cur and (len(cur) >= seg_pages
                    or cur_chars + n > seg_chars):
            segs.append(cur)
            cur, cur_chars = [], 0
        cur.append(p)
        cur_chars += n
    if cur:
        segs.append(cur)
    return [{"index": i + 1, "a": s[0], "b": s[-1], "pages": list(s),
             "chars": sum(len(page_text_for_structure(p, ch, page_cap)) for p in s)}
            for i, s in enumerate(segs)]
