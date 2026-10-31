"""纯页码 / 页列表工具（S16 模块拆分第二步）。

这些函数不读 DB、不依赖 config、不触发模型，只做字符串/整数转换；从
`core/kp_build.py` 抽出，`kp_build` 继续以 `_parse_ref/_pages_label/_page_list`
别名导出，旧调用方和旧测试不需要改。

口径与抽出前逐字一致：
- `source_ref` 支持 `p.3`、`p.3 图`、`p.1-2`；认不出返回 None；
- 页列表标签只看连续性，不改顺序（内部排序去重）；
- 模型返回的 pages 支持 `[1,2]`、`"1-3"`、`"1、2"` 等写法。
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
