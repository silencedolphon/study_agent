# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-12-b8997aba   (project study_agent; keep this line)
#
# 重建注（卡 C-0117）：本文件在 2026-10-14 的源码重建中**整份丢失**，重建清单
# 也没列它，于是 `core/extract.py` 里的 `from . import layout` 一执行就抛
# ImportError，**一切 PPTX/PPT 入库 100% 失败**
# （看板红字：cannot import name 'layout' from 'core'）。
#
# 本版由**打包字节码真值**逐条还原，不是照调用点猜的：
#   C:\Users\Lenovo\StudyDashboard.exe_extracted\PYZ.pyz_extracted\core\layout.pyc
#   （15,135 字节 / CPython 3.13；出自 v1.1 发布包，故障前的完整时间点）
#   · 函数名、参数名、局部变量名、常量、控制流**与字节码逐条等价**；
#   · docstring 逐字取自字节码的常量表 —— 那是原版真值，不是重写；
#   · 行内 `#` 注释**不在字节码里、无法恢复**。本版新增的注释一律以
#     「重建注：」开头，便于日后与其它来源（会话记录、人工记忆）对照。
"""版面重排：把"按文档顺序抽出来的一维文字"还原成二维阅读顺序。

## 解决什么问题

幻灯片里**图上的标签是几十个独立的小形状**，而形状的文档顺序与视觉顺序
毫无关系。实测一份课件的通路图页有 60 个形状、21 个含文字，文档顺序是：

    Na+ / NA / Ca2+ / Ca2+ / COMT / 去甲肾上腺素能神经的突触传递
    / MAO / 无活性 / 代谢物 / 尿 / 酪氨酸 / 酪氨酸 / Na+ / 多巴 ...

而图上实际是一条从左到右的通路：

    酪氨酸 → 多巴 → 多巴胺 → NA → …

照文档顺序切块，正文就成了"一行一个词竖着堆下来"，
检索时既读不出关系，引用也没法看。

## 怎么修

形状带 `left/top/width/height`，坐标是现成的，只是前面一直没用。
判据（实测 182 个形状定出来的）：

| | 图上标签 | 正文形状 |
|---|---|---|
| 高度 | ≤ 0.9 英寸 | ≥ 0.9 英寸 |
| 宽度 | 多在 1.3 英寸内 | 多在 1.7 英寸以上 |
| 段落数 | 1 | 常 ≥ 3 |

分类后把标签**按行聚类**（同一视觉行的标签合成一行，用 ` | ` 连接），
行按 y 排序、行内按 x 排序。这样还原出"图上有哪几行标签"，
比一维序列可读得多，也保留了同行的并列关系。

## 边界

这**不是**真正的图结构理解——它还原不出箭头方向与流程先后。
那部分靠整页识图（`core/figures.py`）补。这里只保证文字**可读、不乱序**。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import Block

# 重建注：下面五个阈值就是模块 docstring 那张表的取值（实测 182 个形状定出来的）。
# 名字与数值都取自字节码常量表，原样保留。
LABEL_MAX_H = 0.9
LABEL_MAX_W = 1.6
LABEL_MAX_PARAS = 2
LABEL_MAX_CHARS = 24
# 重建注：又高又窄（h/w ≥ 3）的一律不算标签 —— 那是侧边竖排标题。
VERTICAL_TITLE_RATIO = 3.0

_AXIS_ONLY = re.compile(r'^[\s\d\w,.\-+*/=()%～~、;:|]+$')
_HAS_DIGIT = re.compile(r'\d')
_HAS_CJK = re.compile(r'[\u4e00-\u9fff]')


def is_axis_noise(line: str) -> bool:
    """这一行是不是坐标轴刻度（该丢），而不是内容（该留）。"""
    # 重建注：三个条件是"且"的关系 —— 含中文的必留（内容）；不含数字的必留
    # （刻度至少带数字）；剩下的只有当整行**全部**由刻度字符组成时才算噪声。
    if _HAS_CJK.search(line):
        return False
    if not _HAS_DIGIT.search(line):
        return False
    return bool(_AXIS_ONLY.match(line))


def _norm(s: str) -> str:
    """比较两个标签是否同一个时用：忽略空白。"""
    return re.sub(r'\s+', '', s or '')


EMU_PER_INCH = 914400.0


def emu_to_inch(v) -> float:
    """把 pptx 的 EMU 坐标换算成英寸；取不到值就返回 0。"""
    try:
        return float(v or 0) / EMU_PER_INCH
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Region:
    """一个带坐标的文字区域。"""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    nparas: int = 1

    @property
    def w(self) -> float:
        # 重建注：**不夹到 0** —— 与字节码一致（直接 x1 - x0）。
        # pptx 的形状不会有负宽高，夹不夹等价；改了反而与真值不一致。
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def yc(self) -> float:
        """垂直中心。行聚类用它而不是 top：同行标签的 top 会因基线差几像素。"""
        return (self.y0 + self.y1) / 2

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


def is_label(r: Region) -> bool:
    """这个区域像"图上的标签"还是"正文文字"。

    判据是**尺寸 + 段落数 + 字数**三者，不是单一维度：
    有些正文条目本身就短（"1. 生物电传递"），但它所在的形状宽而多段；
    有些标签带两段（"无活性\\n代谢物"），所以段落数阈值放到 2。
    """
    if r.nparas > LABEL_MAX_PARAS:
        return False
    if len(r.text) > LABEL_MAX_CHARS:
        return False
    # 重建注：竖排标题要**先排除**，否则它又窄又高、宽度落在标签区间里，
    # 会被当成图注，页标题就被吞进图注行。
    if r.h > 0 and r.w > 0 and (r.h / r.w) >= VERTICAL_TITLE_RATIO:
        return False
    return r.h <= LABEL_MAX_H and r.w <= LABEL_MAX_W


def rows_of(labels: list[Region], tol_ratio: float = 0.55) -> list[list[Region]]:
    """把标签按视觉行聚类。

    判定"同一行"用**垂直中心距离**而不是 top 值：
    同行的标签常因字体基线不同而有几像素的 top 差，
    而不同行的间距通常大于行高。容差取标签高度中位数的一个比例，
    这样大小标签混排时也不会把两行并成一行。
    """
    if not labels:
        return []
    hs = sorted(x.h for x in labels)
    # 重建注：`or 0.3` 兜的是"中位高度算出来是 0"（形状没给尺寸）——取 0.3 英寸
    # 当默认行高，免得容差塌成 0、每个标签各成一行。
    med_h = hs[len(hs) // 2] or 0.3
    tol = max(med_h * tol_ratio, 0.12)

    ordered = sorted(labels, key=lambda r: r.yc)
    rows: list[list[Region]] = []
    for r in ordered:
        placed = False
        for row in rows:
            # 重建注：拿**行内已放元素的 yc 均值**当这一行的基准，不是首元素、
            # 也不是 min —— 行里既有大标签又有小标签时均值更稳。
            ref = sum(x.yc for x in row) / len(row)
            if abs(r.yc - ref) <= tol:
                row.append(r)
                placed = True
                break
        if not placed:
            rows.append([r])
    for row in rows:
        row.sort(key=lambda r: r.xc)
    rows.sort(key=lambda row: sum(r.yc for r in row) / len(row))
    return rows


def reflow_labels(labels: list[Region],
                  joiner: str = ' | ',
                  mark: str = '【图注】') -> list[Block]:
    """把标签重排成"一行一个 Block"，行内用 joiner 连接。"""
    out: list[Block] = []
    for row in rows_of(labels):
        parts: list[str] = []
        for r in row:
            t = r.text.strip().replace('\n', ' ')
            if not t:
                continue
            # 重建注：相邻重复的标签只留一个（PPT 里同一文字常有重叠副本）。
            if parts and parts[-1] == t:
                continue
            parts.append(t)
        if not parts:
            continue
        line = joiner.join(parts)
        # 重建注：纯刻度行直接丢——它不是内容（判据见 is_axis_noise）。
        if is_axis_noise(line):
            continue
        x0 = min(r.x0 for r in row)
        y0 = min(r.y0 for r in row)
        x1 = max(r.x1 for r in row)
        y1 = max(r.y1 for r in row)
        out.append(Block(text=f"{mark}{line}", kind='body', level=0,
                         x0=x0, y0=y0, x1=x1, y1=y1))
    return out


def split_page(regions: list[Region]) -> tuple[list[Region], list[Region]]:
    """把一页的区域分成 (常规文字, 图上标签)。

    只有**标签数量达到 3 个**时才认为这页真有图注需要重排。
    一两个小形状更可能是页码、脚标这类零碎，重排反而打乱正常顺序。
    """
    labels = [r for r in regions if is_label(r)]
    rest = [r for r in regions if not is_label(r)]
    if len(labels) < 3:
        return regions, []
    return rest, labels


def order_regions(regions: list[Region],
                  skip: list[Region] | None = None) -> list[Block]:
    """按视觉阅读顺序重排一页的全部文字区域，**不丢任何区域**。

    规则：
    - 大区域（正文、标题框）各自成块，按 (top, left) 排序；
    - 小标签按视觉行聚类，同一行的合成一行（用 ` | ` 连接）——
      幻灯片上"N1受体 / N2受体 / M受体"这类并列标签本就在同一行，
      合成一行才读得出并列关系。

    为什么只重排、不删减：先前按尺寸把形状分流成"正文/图注"两类再分别处理，
    结果一个正文本框被判成图注后，连"先取标题再取正文"的分支一起绕过，
    实测整页正文消失（p6 只剩标题）。排序对两类都成立，删减不是。

    `skip` 是要排除的区域（用**对象本身**，不是下标）：被选作标题的那个区域
    已单独成块，正文里不该再有。为什么不用下标——`rows_of` 会把同一行的
    多个区域**合并**成一个块，输出下标与输入下标根本不对应（踩过这个坑）。
    """
    if skip:
        # 重建注：skip 先按 `id()` 剔一遍（快、且不受 dataclass 相等语义影响）；
        # 末尾还有一遍按**归一化文本**的兜底剔除，两遍都不能少（见下）。
        skip_ids = {id(r) for r in skip}
        regions = [r for r in regions if id(r) not in skip_ids]
    if not regions:
        return []

    labels = [r for r in regions if is_label(r)]
    hard = [r for r in regions if not is_label(r)]

    raw: list[Block] = []
    # 正文/标题这类大区域：一个区域一个块，文本原样（含换行）。
    for r in hard:
        raw.append(Block(text=r.text, kind='body', level=0,
                         x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1))

    if len(labels) >= 3:
        # 标签够多，才值得按视觉行重排（理由同 split_page）。
        for row in rows_of(labels):
            parts: list[str] = []
            for r in row:
                t = r.text.strip().replace('\n', ' ')
                if not t:
                    continue
                if parts and parts[-1] == t:
                    continue
                parts.append(t)
            if not parts:
                continue
            line = ' | '.join(parts)
            if is_axis_noise(line):
                continue
            # 重建注：整行的包围盒（min/max）当块坐标，跟正文块一个口径。
            raw.append(Block(text=line, kind='body', level=0,
                             x0=min(r.x0 for r in row),
                             y0=min(r.y0 for r in row),
                             x1=max(r.x1 for r in row),
                             y1=max(r.y1 for r in row)))
    else:
        # 标签不足 3 个：不聚类，各自成块（顺序交给最后那一次排序）。
        for r in labels:
            raw.append(Block(text=r.text, kind='body', level=0,
                             x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1))

    # 重建注：`round(y0, 2)` 是刻意的 —— 同一视觉行里几个块的 y0 常差
    # 千分之几英寸（浮点换算来的），不取整会让它们的先后变成噪声。
    raw.sort(key=lambda b: (round(b.y0, 2), b.x0))

    # 重建注：第二遍——按**归一化文本**剔除与 skip 同文的块。为什么第一遍按
    # id() 还不够：标题区域被 `is_label` 判成标签后参与聚类，可能与同一行
    # 其它标签**合并成一个新块**，那个新块的对象跟标题不是同一个，id() 拦不住。
    skip_text = {_norm(r.text) for r in skip} if skip else set()
    out: list[Block] = []
    for b in raw:
        t = _norm(b.text)
        if not t or t in skip_text:
            continue
        # 重建注：相邻（排完序后）完全同文的块只留一个，去掉重叠形状产生的副本。
        if out and _norm(out[-1].text) == t:
            continue
        out.append(b)
    return out
