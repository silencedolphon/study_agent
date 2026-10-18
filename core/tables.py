"""表格结构还原：把被 OCR 读成一维的表格放回二维。

## 问题

PDF 课件里"表格"常常是**一整张截图**——文字层只有标题与页码，
表格的边框与单元格内容都在像素里。走 OCR 时每行文字被读成一个片段，
按阅读顺序拉直成一维：

```
1.0~1.5 / 胰岛素促 / 格列奈类 / 轻度增 / 未知 / 未知 / 未知 / 泌剂 / ...
```

这不是"识别错了"——每个词都识别对了，**丢掉的是它们之间的位置关系**。
于是"哪种药对体重的影响是增加"这类问题无法回答：
`增加` 与它所属的药物名之间没有任何关联。

## 为什么要检测表格线，而不是聚类文字框

先试过聚类（按 y 空白带切行、按 x 中心聚类列），实测在一张
10 行 × 9 列的表上只能还原出 7 行 × 8 列：
`体重` 与 `低血糖` 两列中心太近被并成一列，
`SGLT-2 抑制剂` 与 `GLP-1 受体激动剂` 两行间隙只有 2px 被并成一行。
**列并错比行并错更糟**——那会把两列的数据混在一起。

表格线是更可靠的判据：边线是**贯穿整格的长直线**，
比文字框的统计特征稳定得多。

## 怎么拿表格线

`page.get_drawings()` 在这类文件上拿不到（实测整页只有一个页面边框对象，
表格是截图的一部分）。所以只能从**像素**里找：
把整页渲染出来，找足够长的横向/纵向深色连续段。

用法:
    python tools/show_table_recovery.py <pdf> 70     # 演示（含前后对照）
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TableGrid:
    """一张表的网格结构。"""

    xs: list[float] = field(default_factory=list)
    ys: list[float] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return max(len(self.ys) - 1, 0)

    @property
    def n_cols(self) -> int:
        return max(len(self.xs) - 1, 0)

    def cell_of(self, x: float, y: float) -> tuple[int, int] | None:
        """点 (x, y) 落在哪个单元格。落在网格外返回 None。"""
        c = next((i for i in range(len(self.xs) - 1)
                  if self.xs[i] <= x < self.xs[i + 1]), None)
        r = next((i for i in range(len(self.ys) - 1)
                  if self.ys[i] <= y < self.ys[i + 1]), None)
        if c is None or r is None:
            return None
        return (r, c)


def _runs(flags, min_len: int) -> list[tuple[int, int]]:
    """在一维布尔序列里找连续 True 的段，返回 [(起, 止)]（止不含）。"""
    out = []
    start = None
    for i, v in enumerate(flags):
        if v and start is None:
            start = i
            continue
        if v:
            continue
        if start is None:
            continue
        if i - start >= min_len:
            out.append((start, i))
        start = None
    if start is not None and len(flags) - start >= min_len:
        out.append((start, len(flags)))
    return out


def line_positions(mask, axis: int, min_run: int) -> list[float]:
    """在细线掩码里找**长直线**的位置。

    判据是"这一行/列上存在一段足够长的连续线像素"，
    而不是"这一行列有多少线像素"。

    为什么必须是**连续段**：文字笔画也是细线（局部对比度同样高），
    但一笔只有十几像素长；表格线则是贯穿整格的长直线。
    实测那张表：真线的连续长度约 96~620px，而文字笔画在 40px 以下。
    按"总像素数"判会被密集的文字骗过，按"最长连续段"判不会。

    同一条线占几个像素宽，最后按位置合并。
    """
    import numpy as np

    a = np.asarray(mask)
    positions = []
    n = a.shape[0] if axis == 0 else a.shape[1]
    for i in range(n):
        if axis == 0:
            seq = a[i].tolist()
        else:
            seq = a[:, i].tolist()
        if not _runs(seq, min_run):
            continue
        positions.append(float(i))
    if not positions:
        return []
    out = [positions[0]]
    for p in positions[1:]:
        if p - out[-1] <= 2:
            out[-1] = (out[-1] + p) / 2
        else:
            out.append(p)
    return out


def detect_grid(img, dark: int = 150, contrast: int = 20,
                min_ratio: float = 0.45) -> TableGrid:
    """从整页图里检测表格线，返回网格。

    三步：

    1. 算**细线掩码**（见 `line_mask`）——区分"细线"与"彩色填充"。
       这一步不能省：实测那张表的单元格有红/绿填充，填充区灰度也在
       150 以下，按绝对亮度判会把整片填充当成长线，检测彻底失效。
    2. 在掩码里找**长连续段**当线条（见 `line_positions`）——
       文字笔画同样是细线，但只有十几像素长，靠长度区分。
    3. 先用较高的比例找外框，再在外框内用较低比例补内部线条。
       表头若有大跨度的合并单元格，它下面那条内部竖线只在表体出现，
       按整页比例过滤会漏掉（实测 `心衰` 与 `肾脏病` 因此被并成一列）。

    `min_run` 取"页面尺寸 × min_ratio"：实测真线的连续长度
    远大于此（表体 96~620px，阈值约 337px 会漏掉只有表体那么长的内部线），
    所以外框用 0.45、内部用 0.40，都偏向**多认一点**——
    多认一条线只是多切一列，漏一条线会把两列数据混在一起（更糟）。
    """
    import numpy as np

    a = np.asarray(img)
    if a.ndim == 3:
        gray = a.mean(axis=2)
    else:
        gray = a.astype(float)

    mask = line_mask(gray, dark=dark, contrast=contrast)
    h, w = gray.shape

    # 外框：整页比例
    ys = line_positions(mask, axis=0, min_run=int(w * min_ratio))
    xs0 = line_positions(mask, axis=1, min_run=int(h * min_ratio))
    if len(ys) < 2 or len(xs0) < 2:
        return TableGrid(xs=xs0, ys=ys)

    y0, y1 = int(ys[0]), int(ys[-1])
    x0, x1 = int(xs0[0]), int(xs0[-1])

    # 内部线条：在外框内用较低比例补
    ys2 = [
        y0 + v
        for v in line_positions(
            mask[y0: y1 + 1, x0: x1 + 1],
            axis=0,
            min_run=max(int((x1 - x0) * 0.4), 24),
        )
    ]
    xs2 = coverage_positions(mask, axis=1, min_cov=0.55, lo=y0, hi=y1 + 1)

    return TableGrid(xs=_merge_sorted(xs0 + xs2), ys=_merge_sorted(ys + ys2))


def line_mask(gray, dark: int = 150, contrast: int = 25):
    """找出"细线条"像素的掩码。

    ## 为什么不能直接用「灰度 < 阈值」

    表格单元格常有**彩色填充**（实测那张表里"增加/获益"格是红/绿底），
    填充区的灰度也在 150 以下。直接按亮度判，整片填充都是"连续深色"，
    于是每一列看起来都有几百像素长的"线条"——**线条检测彻底失效**
    （实测 x=1230 这种填充区中部的列也报出 96 像素连续深色）。

    ## 判据：局部对比度，而不是绝对亮度

    线条的本质是"**比周围暗**的细结构"，填充是"整片一样暗"。
    所以对每个像素算它与**上下各 R 像素中位数**的差：
    - 细线：本身暗、上下是亮的 → 差值大
    - 填充：本身暗、上下也一样暗 → 差值接近 0

    R 取 3：够跨过线宽（1~3px），又小于行高（实测 28px），
    不会把一个单元格的高度算进邻域。

    横线与竖线各算一次，取或——这样交叉处也能保留。
    """
    import numpy as np

    g = np.asarray(gray, dtype=np.float32)

    R = 3

    def local_med(a, axis):
        """沿 axis 取窗口 2R+1 的中位数（用排序取中，规模不大）。"""
        pad = [(0, 0)] * a.ndim
        pad[axis] = (R, R)
        p = np.pad(a, pad, mode="edge")
        idx = [slice(None)] * a.ndim
        wins = []
        for k in range(2 * R + 1):
            idx[axis] = slice(k, k + a.shape[axis])
            wins.append(p[tuple(idx)])
        stacked = np.stack(wins, axis=0)
        return np.median(stacked, axis=0)

    med_v = local_med(g, 0)
    med_h = local_med(g, 1)
    horiz = (med_v - g) >= contrast
    vert = (med_h - g) >= contrast
    both = horiz | vert

    return both & (g < dark)


def coverage_positions(mask, axis: int, min_cov: float, lo: int = 0,
                       hi: int | None = None) -> list[float]:
    """在指定范围内，用**投影覆盖率**找线条位置。

    覆盖率 = 该行/列在范围内"是线像素"的比例。

    为什么竖直（找列边界）必须用覆盖率，而不是"最长连续段"：
    内部竖线常常**只出现在一部分行**里——表头把两列合并时
    （"对心血管和肾脏的影响"横跨 ASCVD/心衰/肾脏病），
    分隔线就只在表体出现。按连续段长度判，长度不够就被滤掉，
    于是相邻两列并成一列、数据混在一起。

    覆盖率对这种情况天然成立：真列边界在范围内覆盖率接近 1
    （实测 0.68~1.00），而文字笔画只占几行、覆盖率很低。
    """
    import numpy as np

    a = np.asarray(mask)

    if axis == 0:
        if hi is None:
            hi = a.shape[1]
        sub = a[:, lo:hi]
        if sub.size:
            cov = sub.mean(axis=1)
        else:
            cov = np.array([])
        base = 0
    else:
        if hi is None:
            hi = a.shape[0]
        sub = a[lo:hi, :]
        if sub.size:
            cov = sub.mean(axis=0)
        else:
            cov = np.array([])
        base = 0

    hits = [float(i + base) for i, v in enumerate(cov) if v >= min_cov]
    return _merge_sorted(hits, tol=3.0)


def _merge_sorted(vals: list[float], tol: float = 3.0) -> list[float]:
    """把相近的线合并成一条（同一条线有几个像素宽）。"""
    if not vals:
        return []
    vs = sorted(vals)
    out = [vs[0]]
    for v in vs[1:]:
        if v - out[-1] <= tol:
            out[-1] = (out[-1] + v) / 2
        else:
            out.append(v)
    return out


def assign_cells(boxes: list[tuple], grid: TableGrid) -> dict:
    """把 OCR 框按坐标放进网格单元格。

    boxes 的元素是 (y0, x0, x1, y1, text, conf)。
    一个框可能横跨多列（合并单元格的表头，如"对心血管和肾脏的影响"），
    这时它会被放进**最左边那格**并标记跨列范围，由调用方决定怎么渲染。
    """
    cells = {}
    spans = {}
    for b in boxes:
        y0, x0, x1, y1, txt, _conf = b
        c0 = grid.cell_of(x0, (y0 + y1) / 2)
        c1 = grid.cell_of(max(x1 - 1, x0), (y0 + y1) / 2)
        if c0 is None:
            continue
        r, c = c0
        cells.setdefault((r, c), []).append(b)
        if not c1 or c1[1] <= c:
            continue
        spans[(r, c)] = c1[1] - c + 1
    text = {k: "".join(x[4] for x in sorted(v, key=lambda z: (z[0], z[1])))
            for k, v in cells.items()}
    return {"text": text, "spans": spans}


def is_table_like(result: dict, boxes: list[tuple], grid: TableGrid,
                  min_rows: int = 3, min_cols: int = 2,
                  min_multi: float = 0.5) -> bool:
    """这个网格该不该当表格处理。

    三条判据，缺一不可：

    1. 至少 3 行 2 列——低于这个规模，"还原"不比一维序列更好。
    2. **多数 OCR 框落在网格内**：否则说明检出的线不是表格线
       （可能是正文的分栏线、页眉装饰条）。
    3. **过半的行有 ≥2 个非空格**——这是最要紧的一条。
       只有文字真的按列分散排布时，"行列对应"才有意义；
       整页正文如果被误检成网格，每行通常只有一格有内容
       （因为正文是连续的一栏），这条会把它挡住。

    宁可漏判也不能误判：误判会把整页正文按错误的列切开，
    比保持一维序列糟得多。
    """
    if grid.n_rows < min_rows or grid.n_cols < min_cols:
        return False
    text = result["text"]
    placed = sum(len(v) for v in _cells_boxes(boxes, grid).values())
    if boxes and placed / len(boxes) < 0.6:
        return False
    by_row = {}
    for (r, _c), t in text.items():
        if not t.strip():
            continue
        by_row[r] = by_row.get(r, 0) + 1
    multi = sum(1 for v in by_row.values() if v >= 2)
    return multi >= 2 and multi / max(len(by_row), 1) >= min_multi


def _cells_boxes(boxes: list[tuple], grid: TableGrid) -> dict:
    out = {}
    for b in boxes:
        y0, x0, _x1, y1, _t, _c = b
        cell = grid.cell_of(x0, (y1 + y0) / 2)
        if not cell:
            continue
        out.setdefault(cell, []).append(b)
    return out


def fix_merged_header(got: dict, boxes: list[tuple],
                      grid: TableGrid) -> tuple[list[str], str]:
    """修正合并表头，返回 (各列名, 被剥离出来的上层名)。

    ## 问题

    表头若有合并单元格，它下面的子表头会和它挤在同一列。
    实测 p70 的表头：

    ```
    格(0,5) 跨3列: '对心血管和肾脏的影响ASCVD'   ← 上层名 + 第一个子名
    (空)                                        ← 心衰 丢了
    格(0,7) 跨1列: '肾脏病'
    ```

    `对心血管和肾脏的影响` 这个框横跨 x 922~1284，覆盖了第 5/6/7 三列；
    `ASCVD`（x 912~1024）的**左边界**落在第 5 列里，于是被并进同一格；
    `心衰`（x 1077~1153）的左边界落在第 6 列，那一格算法认为被合并占用了，
    于是**整格丢失**。

    ## 修法：用 x 中心重算子表头该在哪一列

    `assign_cells` 用**左边界**定位是合理的（普通单元格里文字靠左），
    但被合并表头罩住的子表头必须用**中心**——`ASCVD` 的中心 968
    落在第 5 列，`心衰` 的中心 1115 落在第 6 列，各归各位。

    判据是"同一格里出现了 y 分层"：上层名在 y≈113、子名在 y≈145，
    两者 y 差超过一格内正常行距，说明这格里有"上层 + 子层"。
    识别出来后：上层名剥离为表格标题，子名按 x 中心重新分配。
    """
    text = dict(got["text"])
    spans = dict(got["spans"])
    if grid.n_rows < 2 or grid.n_cols < 2:
        return _row_cells(text, spans, grid, 0), ''

    by_col = {}
    for b in boxes:
        y0, x0, _x1, y1, _t, _c = b
        if not (grid.ys[0] <= (y0 + y1) / 2 <= grid.ys[1]):
            continue
        cell = grid.cell_of(x0, (y0 + y1) / 2)
        if not cell:
            continue
        if cell[0] != 0:
            continue
        by_col.setdefault(cell[1], []).append(b)

    upper = ''
    upper_col = -1
    for c, items in by_col.items():
        if len(items) < 2:
            continue
        # 该列被合并表头占用（跨列）则跳过
        if int(spans.get((0, c), 1)) < 2:
            continue
        ys = sorted((b[0] + b[3]) / 2 for b in items)
        hs = [b[3] - b[0] for b in items]
        med_h = sorted(hs)[len(hs) // 2] or 20
        if ys[-1] - ys[0] <= med_h * 0.6:
            continue

        items_sorted = sorted(items, key=lambda z: (z[0], z[1]))
        top = items_sorted[0]
        if top[4] and len(top[4]) >= 2:
            upper = top[4]
            upper_col = c
        for b in items_sorted[1:]:
            cc = grid.cell_of((b[1] + b[2]) / 2, (b[0] + b[3]) / 2)
            if not cc:
                continue
            key = (0, cc[1])
            text[key] = b[4]
            spans.pop((0, c), None)

    if upper and upper_col >= 0:
        key = (0, upper_col)
        if key in text and text[key].startswith(upper):
            rest = text[key][len(upper):].strip()
            if rest:
                text[key] = rest
            else:
                text.pop(key)

    return _row_cells(text, spans, grid, 0), upper


def table_overview(boxes: list[tuple], grid: TableGrid, header: str = "",
                   max_rows: int = 20, max_chars: int = 1100) -> str:
    """生成一张表的**概览块**：每行只留"行名 + 与表头同名的列"的紧凑形式。

    ## 为什么需要它

    `table_lines()` 是**每行一块**（粒度细，问"某药的某项"才准）。
    但有些提问要**跨行聚合**——"哪些降糖药会明显降低体重"，
    答案分散在 9 行里。逐行块各自只含一种药，没有一个块能回答它。

    所以额外产出一个概览：把整张表压成一句一行，一次给全。
    两种粒度并存，细问答细、总览答总览。

    ## 格式

    概览不重复列名（逐行块已经带了），只做"行名=关键列值"的紧凑罗列，
    并保留表头，让列的顺序可读：

        [表格]常用SGLT-2抑制剂比较（3 种药 × 8 个比较项）
        比较项：剂量范围、服药时间、肾功能不全…
        达格列净片：5~10mg/d,qd；晨服，餐前或餐后；…
    """
    got = assign_cells(boxes, grid)
    text, spans = got["text"], got["spans"]

    cells_row = lambda r: _row_cells(text, spans, grid, r)  # noqa: E731

    if grid.n_rows < 2 or grid.n_cols < 2:
        return ""

    head = cells_row(0)

    labels = [h for h in head[1:] if h]

    first_col = [cells_row(r)[0] for r in range(1, grid.n_rows)]
    n_first_col = sum(1 for x in first_col if x)
    if n_first_col < 2:
        return ""

    out = [f"[表格]{header + ' ' if header else ''}"
           f"（{n_first_col} 行 × {len(labels)} 项）"]

    if labels:
        out.append("比较项：" + "、".join(labels[:14]))

    for r in range(1, min(grid.n_rows, max_rows + 1)):
        cells = cells_row(r)
        name = cells[0]
        if not name:
            continue
        vals = [v for v in cells[1:] if v]
        if not vals:
            continue
        out.append(f"{name}：" + "；".join(vals))

    s = "\n".join(out)
    if len(s) > max_chars:
        return s[:max_chars]
    return s


def _row_cells(text: dict, spans: dict, grid: TableGrid, r: int) -> list[str]:
    out = []
    c = 0
    while c < grid.n_cols:
        out.append(str(text.get((r, c), "")).strip())
        c += int(spans.get((r, c), 1))
    return out


def table_lines(boxes: list[tuple], grid: TableGrid,
                header: str = "") -> list[str]:
    """把网格渲染成**可检索的文本行**，每行一个独立字符串。

    目标是"问『哪种药对体重的影响是增加』能命中"。要命中，
    同一行里的**药物名**、**列名**、**取值**必须在同一段文字里——
    三者缺一，检索就断链。所以每行的格式是：

        二甲双胍 → 主要机制: 改善胰岛素抵抗 | HbA1c降幅: 1.0~1.5 | 体重: 轻度降低

    列名逐行重复看似冗余，但它是**自足**的代价：切块会把长表切成多块，
    重复列名保证任何一块单独被检索到时都读得懂。

    ## 表头列名的清理：前缀剥离，不做行拆分

    合并单元格会让列名被拼成 `对心血管和肾脏的影响ASCVD`
    （上层表头 + 子表头连在一起）。

    我试过"按 y 把格子里的框拆成上下两层、取下层当列名"，**结果是错的**：
    `HbA1c降幅` 这种自动折行的两行（"HbA1c" / "降幅"）被当成了
    "上层名 + 子名"，列名退化成 `降幅`，而 `HbA1c降幅` 恰恰是个术语，
    退化后搜不到。

    正确的判据不是 y，而是**内容**：合并表头的上层名会**横跨多列**
    （同一个词出现在相邻几格里），而自动折行只有一个格子里有。
    所以：先取各格拼接后的完整文字，再用"在 ≥2 个格里都出现的
    公共前缀"识别出上层名，把它从列名里剥掉、并进表格标题。
    """
    got = assign_cells(boxes, grid)
    text, spans = got["text"], got["spans"]
    head_cells, upper = fix_merged_header(got, boxes, grid)
    if not any(head_cells):
        head_cells = _row_cells(text, spans, grid, 0)

    lines = []
    if grid.n_rows >= 2:
        title_bits = [header] if header else []
        if upper:
            title_bits.append(upper)
        title = " ".join(b for b in title_bits)
        joined = " ｜ ".join(b for b in head_cells)
        if joined:
            lines.append(f"[表格]{title + ' ' if title else ''}表头：{joined}")

        for r in range(1, grid.n_rows):
            cells = _row_cells(text, spans, grid, r)
            if not any(cells):
                continue
            label = cells[0] if cells else ""
            pairs = [
                f"{head_cells[i]}: {v}"
                for i, v in enumerate(cells[1:], start=1)
                if v and i < len(head_cells) and head_cells[i]
            ]
            if not pairs:
                rest = [v for v in cells[1:] if v]
                if rest:
                    line = f"{label} → " + " | ".join(rest)
                else:
                    line = label
            else:
                if label:
                    line = f"{label} → " + " | ".join(pairs)
                else:
                    line = " | ".join(pairs)
            if not line.strip():
                continue
            lines.append(line)
        return lines

    for r in range(grid.n_rows):
        cells = [c for c in _row_cells(text, spans, grid, r) if c]
        if not cells:
            continue
        lines.append(" ｜ ".join(cells))
    return lines


def recover_overviews(pages: list[tuple], *, min_conf: float = 0.4,
                      header: str = "") -> dict[int, str]:
    """对判为表格的页生成**概览块**文本，返回 {页号: 概览}。

    与 `recover_pages` 用同一套判据，只是渲染方式不同：
    那里每行一条，这里整表压成一段。两者并存。
    """
    out = {}
    for pno, img, boxes in pages:
        if img is None or not boxes:
            continue
        try:
            kept = [b for b in boxes if b[5] >= min_conf] or boxes
            grid = detect_grid(img)
            if grid.n_rows < 3 or grid.n_cols < 2:
                continue
            got = assign_cells(kept, grid)
            if not is_table_like(got, kept, grid):
                continue
            ov = table_overview(kept, grid, header=header)
            if ov:
                out[pno] = ov
        except Exception:
            continue
    return out


def recover_pages(pages: list[tuple], *, min_conf: float = 0.4,
                  min_rows: int = 3, min_cols: int = 2,
                  header: str = "") -> dict[int, list[str]]:
    """对若干页做"表格还原"，只返回**判为表格页**的结果。

    `pages` 的元素是 `(页号, 整页图数组, OCR 结构化结果)`，
    结构化结果的每项是 `(y0, x0, x1, y1, text, conf)`。

    返回 `{页号: [渲染好的行]}`。没判为表格的页不在返回值里——
    调用方据此回退到一维序列。
    """
    out = {}
    for pno, img, boxes in pages:
        if img is None or not boxes:
            continue
        try:
            kept = [b for b in boxes if b[5] >= min_conf] or boxes
            grid = detect_grid(img)
            if grid.n_rows < min_rows or grid.n_cols < min_cols:
                continue
            got = assign_cells(kept, grid)
            if not is_table_like(got, kept, grid, min_rows=min_rows,
                                 min_cols=min_cols):
                continue
            lines = table_lines(kept, grid, header=header)
            if lines:
                out[pno] = lines
        except Exception:
            continue
    return out
