# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
# File-ID: SA-03-c0087txt   (project study_agent; keep this line)
"""教材专线：章识别 + 按章切块。**零模型调用**，纯规则。

为什么要这个模块（人的原话，2026-10-12）：

    把课件入库和教材入库分开。教材入库时，先用 OCR 识图把文字识别出来，
    然后再**按照每一章分别切块**，不要像之前一样所有东西都挤在一起。

病灶在切块层，不在 OCR 层。扫描教材走本地 OCR 后，块一律是
`kind="ocr"/"table"`（`core/extract.py:522-537`），**永不判 heading**；
于是 `core/chunk.py` 里 `_Packer.headings` 恒空，面包屑只剩书名一级。
实测 doc#91《药理学 第6版 刘晓东》867 块 / 624,239 字符，
`heading_path` 只有 1 种取值（=书名），且块跨章——ord=399（p.216-218）
一块里同时装着"第十九章 抗高血压药"的本章小结与"第二十章 治疗心力衰竭的
药物"的章首页。

本模块只做两件事，**不碰 `core/chunk.py`、不碰 `core/ingest.py`**：

1. `detect_chapters(blocks) -> Chapters`：认出每一章的起点。
2. `cut_by_chapter(blocks, chapters) -> list[Chunk]`：按章切块，块不跨章。

--------------------------------------------------------------------------
章识别的三条实证信号（全部在 doc#91 上实测，不是推测）
--------------------------------------------------------------------------
扫描教材没有 PDF 书签（doc#91 的 `get_toc()` = 0 条），只能从 OCR 文本里认。
单看「第X章」出现 394 次、整行命中 333 次，直接用会被两类前置页毁掉：

  · **编委分工页**（doc#91 的 p.7-8）："宋珏负责第15章、第40章和第41章编写"
    —— 章号在**行中间**，且一页能堆 28 处。
  · **目录页**（doc#91 的 p.8~15）："第一章绪论\n26" —— 章号**整行独立**，
    还带一个页码，形态与真正的章首页几乎一样。

本模块用**章首页锚词**把两者区分开：

    `学习目标` / `PPT` / `本章小结` / `知识拓展`

实测 doc#91 里 `学习目标` 出现 47 次、`本章小结` 50 次，与各章章首页一一对应。
真正的章首页是「第X章<章名>」紧跟锚词，而目录条目后面跟的是**页码**：

    ord=17（p.14-15，目录末尾）  ... '第四十二章治疗骨质疏松症药' <页码> ...
                                  '第一章绪论' 'PPT' '学习目标'   ← 章首页
    ord=399（p.216-218，铁证块）  '第二十章' '治疗心力衰竭的药物回微课' 'PPT'
                                  '学习目标'                     ← 章首页

第二、三条信号是**校验**，不是主判据：

  · 章号全书**严格递增**（ord=22 p.17「第一章」→ ord=860 p.448「第四十二章」）。
    用它挡掉"同一章被认两次"与"越界回跳"。
  · 正文页的**书眉**「第X章<章名>」后面跟纯页码，且该页码与块所在物理页
    有**固定偏移**（doc#91 实测恒为 13 或 14），而目录条目的页码与它所在
    物理页的差值在 +19~+385 之间乱跳。可用它确认"这一页属于第几章"。

--------------------------------------------------------------------------
未知页的归属策略
--------------------------------------------------------------------------
明确写在这里，避免下游猜：

  · 第一个章首页之前的块 —— 封面 / 版权页 / 编委页 / 前言 / 目录，一律
    归入 `Chapter(number=0, name="非正文")`，**保留内容但不出现在任何章里**。
    理由：它们不是教材正文，混进第一章会让"第一章 绪论"的检索结果里
    冒出编委名单和 CIP 数据。
  · 两个章首页之间的块 —— 归**上一章**。理由：它们物理上就排在上一章的
    正文区间内（章首页是这一章的第一页）。
  · 章首页**跨块**时（OCR 把上一章末尾与新章标题切进同一块）—— 在**行级**
    切开，前一段归上一章、后一段归新章。这是"块不跨章"能成立的关键。
  · 识别不出任何章 —— 走**降级**：整份按书名一级切（`fallback=True`），
    并如实上报，不假装成功。

--------------------------------------------------------------------------
为什么切块复用 `core/chunk.py`
--------------------------------------------------------------------------
700 / 1100 / 120 / 80 这四个参数（`core/config.py:121-124`）是检索质量的
既有约定，重写一套长度逻辑必然漂移。所以本模块**逐章调用**
`chunk.chunk_blocks()`：每个 `_Packer` 只看得到一章的块，物理上就不可能
跨章；面包屑由该章一个 `heading` 块起头，于是自然长成
「书名 > 第X章 章名」，与课件现有的「课件名 > 小节」形态对齐。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import chunk as _chunk
from .chunk import Chunk
from .extract import Block

# ---------------------------------------------------------------------------
# 章首页锚词：章首页上必定出现的栏目名。
#
# 为什么是这四个：它们是本套教材的固定板块（实测 doc#91 每章章首页都以
# 「PPT / 学习目标」起头，章末是「本章小结 / 思考题」）。目录页里不会出现
# 「学习目标」这一条（它是正文栏目，不是目录条目），所以这四个词能把
# "第X章 出现在目录里" 与 "第X章 是章首页" 分开。
#
# 只放**栏目名**，不放正文词：`本章小结` 会在章内重复出现，但它出现在
# 章首页锚窗里的概率极低（锚窗只有 4 行），实测没有误命中。
ANCHOR_WORDS: tuple[str, ...] = ("学习目标", "PPT", "本章小结", "知识拓展", "思考题")
# 锚窗行数：章首页的栏目名紧跟在章标题后面。取 4 行——实测 doc#91 里
# 「第X章<章名>」与「学习目标」之间最多隔「PPT」「微课」这类一行噪声。
ANCHOR_WINDOW = 4

# 章标题行：「第<数字>章<章名>」。章号支持阿拉伯数字与中文数字。
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
              "七": 7, "八": 8, "九": 9}
_CN_DIGITS_INV = {v: k for k, v in _CN_DIGITS.items()}
_CN_NUM = r"[0-9]{1,3}|[一二三四五六七八九十]{1,6}"
_CHAPTER_LINE = re.compile(r"^第\s*(" + _CN_NUM + r")\s*章\s*(.*)$")
# 「第X章」行首（不要章名），阿拉伯与中文写法都认
_CHAPTER_HEAD = re.compile(r"^第\s*(" + _CN_NUM + r")\s*章")
# 只有章号、没有章名的行（章名排到了下一行）
_CHAPTER_ONLY = re.compile(r"^第\s*" + _CN_NUM + r"\s*章\s*$")
# 纯页码行：书眉与目录条目后面都跟这个
_PAGE_ONLY = re.compile(r"^\d{1,3}$")
# 章名里的 OCR 噪声：书眉末尾常把「微课」图标认成「回微课」，
# 抽查 doc#91 真实章名可见「麻醉药回微课」「抗病毒药回」「胰岛素及口服降糖药回微课善」。
#
# ⚠ 「药」是**正常章名尾字**（「调血脂药」「抗高血压药」），不能当噪声删。
# 曾经把噪声写成 `(…|善|回)$`，负向后顾又设成"前面是药就不删"，
# 两头都不对：既削掉过「调血脂药与抗动脉粥样硬化药」的尾字「药」，
# 又放过了真实噪声「抗病毒药回」（实测两处都踩过）。
# 现在的划分依据是实测形态：
#   · 「回」在本套教材的章名里**从不出现**，而「…药回」「…回微课」都是
#     微课图标被误识 → 单独尾随的「回」一律删。
#   · 「善」可能是正常尾字（"…与人为善"式）→ 只有跟在「回(微课)」后面才删。
_NOISE = re.compile(r"(?:回?\s*微课.*$|书网融合.*$|PPT.*$|二维码$|扫一扫$"
                     r"|回(?![微课])|(?<=回)善$|课件$)")
# 行尾标点：判断章标题行前面是不是一个完整句子（见 _looks_like_chapter_start）
_SENT_TAIL = "。！？；!?;）)】」》…\"'"
# 行尾标点：判断章标题行前面是不是一个完整句子（见 _looks_like_chapter_start）
_SENT_TAIL = "。！？；!?;）)】」》…\"'"

# 章名跨行续写的连接词：OCR 常把长章名硬切成两行，续行以这些字开头。
# 实测 doc#91：「大环内酯类、林可霉素类」+「及多肽类抗生素」、
# 「作用于血液及造血器官」+「的药物」。取 2 行上限，避免把正文吃进章名。
_NAME_JOIN_HEAD = "及和的与等类为"
DEFAULT_BOOK_TITLE = "教材"
# 非正文章节（封面/目录/前言/无章号区）的章号
FRONT_MATTER_NUMBER = 0
FRONT_MATTER_NAME = "非正文"
# 一个切块里出现多少个**不同**章号就算"目录/索引块"，不计入跨章统计。
# 依据：doc#91 目录页单块最多抄进 20+ 个章号，而真正的正文块最多涉及
# 相邻两三章（章末小结 + 下一章章首页）。取 4 留足余量。
_TOC_LIKE_CHAPTERS = 4
# 命中章号的起页跨度超过它，就当目录/索引块，不计入跨章统计。
# 依据：doc#91 目录块跨 293~400 页，正文跨章块跨 20~36 页。
_SPAN_TOC_PAGES = 120


def _cn_to_int(s: str) -> int:
    """中文数字转整数（"四十二" -> 42）。**只处理 1~99**，够教材用。

    为什么要它：doc#91 的正文书眉用中文数字（「第二十章治疗心力衰竭的药物」），
    而编委分工页用阿拉伯数字（「负责第25章」）——按阿拉伯数字去找正文，
    一条也找不到。这个差异是实测踩出来的，不是理论风险。
    """
    s = (s or "").strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    if any(ch not in _CN_DIGITS and ch != "十" for ch in s):
        return 0
    if "十" not in s:
        return sum(_CN_DIGITS.get(ch, 0) for ch in s)
    head, _, tail = s.partition("十")
    tens = _CN_DIGITS.get(head, 1) if head else 1
    ones = _CN_DIGITS.get(tail, 0) if tail else 0
    return tens * 10 + ones


def _clean_chapter_name(name: str) -> str:
    """去掉章名里的书眉噪声（见 `_NOISE`），并压掉空白。"""
    name = (name or "").strip()
    for _ in range(3):
        new = _NOISE.sub("", name).strip()
        if new == name:
            break
        name = new
    return name.strip("　 \t·、,，.。:：-—")


def _chapter_name_multiline(lines: list[str], i: int,
                            max_extra: int = 2) -> str:
    """取章标题行 `i` 的章名，必要时**拼接后续续行**。

    为什么需要：OCR 会把长章名硬切成两行，续行丢在下一行。实测 doc#91：

        第三十一章 / 大环内酯类、林可霉素类   + 「及多肽类抗生素」
        第二十二章 / 作用于血液及造血器官     + 「的药物」
        第十二章 / 治疗中枢神经系统退行性     + 「疾病的药物」

    只取同行或次行的旧写法会让这些章名永久残缺（检索与展示都受影响）。

    续行判据（三者取其一，且行本身要"像章名"）：同行没有章名、同行章名
    不足 3 字、或同行章名以「、，及和与的」收尾。最多拼 `max_extra` 行，
    避免把正文吃进章名。
    """
    m = _CHAPTER_LINE.match(lines[i].strip())
    body = _clean_chapter_name(m.group(2) if m else "")
    parts = [body] if body else []
    for k in range(1, max_extra + 1):
        j = i + k
        if j >= len(lines):
            break
        nxt = lines[j].strip()
        if not nxt or _PAGE_ONLY.match(nxt) or _is_anchor_line(nxt):
            break
        if _CHAPTER_HEAD.match(nxt):
            break
        if len(nxt) > 24:
            break        # 太长的多半已是正文，不是章名续行
        if parts:
            cur = parts[-1]
            if len(cur) >= 3 and cur[-1] not in "、，,及和与的":
                break    # 章名已经完整
        parts.append(_clean_chapter_name(nxt))
    return _clean_chapter_name("".join(parts))


def _norm_name(name: str) -> str:
    """章名归一：去空白与标点，用于"同一章名"的比较。"""
    return "".join(ch for ch in (name or "") if not ch.isspace()
                   and ch not in "　·、，,。.：:-—_")


def _page_start(ref: str) -> int:
    m = re.match(r"^p\.(\d+)", ref or "")
    return int(m.group(1)) if m else 0


def _is_anchor_line(line: str) -> bool:
    s = (line or "").strip()
    return any(s == a or s.startswith(a) for a in ANCHOR_WORDS)


@dataclass
class Chapter:
    """一章（或一段非正文）。"""
    number: int                 # 章号；0 = 非正文
    name: str                   # 章名；非正文时是「非正文」
    start_index: int            # 在 blocks 列表中的起始下标
    end_index: int              # 结束下标（不含）
    page_start: int = 0         # 起始物理页
    page_end: int = 0           # 结束物理页
    # 章首页在块内的行号（章首页与上一章同在一个块时才有意义，见 _split_span）
    head_line: int = 0

    @property
    def is_front_matter(self) -> bool:
        return self.number <= 0

    @property
    def title(self) -> str:
        """面包屑里用的章级标题（"第X章 章名"）。非正文时给栏目名。"""
        if self.is_front_matter:
            return self.name
        return f"第{self.number}章 {self.name}".strip()


@dataclass
class Chapters:
    """`detect_chapters` 的结果。"""
    items: list[Chapter] = field(default_factory=list)
    # 识别失败时的降级标记：整份按书名一级切，不假装分出了章
    fallback: bool = False
    # 为什么降级 / 识别过程中的异常，供回执如实上报
    warnings: list[str] = field(default_factory=list)
    book_title: str = ""

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, i):
        return self.items[i]

    @property
    def body(self) -> list[Chapter]:
        """只含真正的章（不含非正文）。"""
        return [c for c in self.items if not c.is_front_matter]

    def numbers(self) -> list[int]:
        return [c.number for c in self.body]

    def at(self, number: int) -> Chapter | None:
        for c in self.items:
            if c.number == number:
                return c
        return None

    def chapter_span(self, numbers) -> int:
        """这些章号横跨多少页（用各章起页算）。用于分辨目录块与正文块。"""
        pages = [c.page_start for c in (self.at(n) for n in numbers)
                 if c is not None and c.page_start]
        if len(pages) < 2:
            return 0
        return max(pages) - min(pages)


# ---------------------------------------------------------------------------
# 章识别
# ---------------------------------------------------------------------------
def _candidates_in(block: Block) -> list[tuple[int, int, str, str]]:
    """一个块里的章标题候选：[(行号, 章号, 同行章名, 次行), ...]。

    行内出现章号**不算**（编委页的"负责第15章编写"就在这里被挡掉），
    必须是整行以「第X章」开头。
    """
    out: list[tuple[int, int, str, str]] = []
    lines = block.text.split("\n")
    for i, line in enumerate(lines):
        m = _CHAPTER_LINE.match(line.strip())
        if not m:
            continue
        num = _cn_to_int(m.group(1))
        if num <= 0:
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        out.append((i, num, m.group(2).strip(), nxt))
    return out


def _anchor_after(lines: list[str], i: int) -> bool:
    """第 i 行是章标题时，往后 `ANCHOR_WINDOW` 行内有没有章首页锚词。"""
    for j in range(i + 1, min(len(lines), i + 1 + ANCHOR_WINDOW)):
        if _is_anchor_line(lines[j]):
            return True
    return False


def _body_after(lines: list[str], from_i: int) -> bool:
    """从 `from_i` 这一行往后，块内是否还有**像正文的行**。

    正文行判据：长度 >= 24 且不是纯页码。为什么用长度——doc#91 目录区里
    短行占压倒多数（"绪论" / "26" / "第一节药理学的性质和任务"），
    偶有长行也多是"地位"/"药物"这类词条碎片，24 字能干净地分开。

    ⚠ 这个判据对**章首页在块末尾**的情形会误判（块内确实没有后续正文），
    所以它只是判据 (b) 的辅助，章首页落在下一个块的情形由
    `_next_block_starts_body` 补上。
    """
    for j in range(from_i + 1, len(lines)):
        s = lines[j].strip()
        if len(s) >= 24 and not _PAGE_ONLY.match(s):
            return True
    return False


# 前置区（封面/版权/编委/目录）在实际书里的物理页上限。
#
# 为什么需要一个页数上限：编委分工页（doc#91 的 p.7-8）里"第20章的编写，
# 在此一同表示感谢。"这种行，形态上和第 20 章章首页一模一样（章号 + 章名 +
# 后面跟着长行），只靠文本判不出来。但编委页必然在**书的最前面**，
# 而教材正文绝不可能从第 3 页就开始——这是最稳的一条硬约束。
_FRONT_MATTER_MAX_PAGE = 16


def _next_block_starts_body(blocks: list[Block], bi: int) -> bool:
    """章首页落在块 `bi` 的**末尾**时，下一个块的开头是不是正文。

    实测 doc#91 的 ord=513（p.274-275）末尾是
    `'本章小结' / '第二十五章' / '肾上腺皮质激素类药物'`，锚词在下一个块。
    这时块内 `_body_after` 必然为 False，得看下一个块。
    """
    if bi + 1 >= len(blocks):
        return False
    for line in blocks[bi + 1].text.split("\n")[:3]:
        s = line.strip()
        if len(s) >= 24 and not _PAGE_ONLY.match(s) and not _is_anchor_line(s):
            return True
    return False


def _looks_like_chapter_start(lines: list[str], i: int, num: int,
                              name: str, nxt: str,
                              has_next_body: bool = False) -> bool:
    """第 i 行像不像"章首页的章标题"。

    **(a) 后面有章首页锚词**（`ANCHOR_WINDOW` 行内出现「学习目标 / PPT /
    本章小结 / 知识拓展」）。这是与目录条目分家的主判据——目录条目后面跟的
    是**页码**，真正的章首页后面跟的是栏目名：

        ord=17（p.14-15，目录末尾接第 1 章章首页）
            ... '第四十二章治疗骨质疏松症药' / '409' / ...   ← 目录条目（后跟页码）
            '第一章绪论' / 'PPT' / '学习目标'                ← 章首页（后跟锚词）
        ord=399（p.216-218，铁证块）
            '本章小结' / '第二十章' / '治疗心力衰竭的药物回微课' / 'PPT' ← 章首页

    **(b) 章名齐备 + 后面跟着正文**。为什么需要这一条：OCR 常把章首页切在
    两个块之间，锚词落到了**下一个块**里。实测 doc#91 的 ord=513
    （p.274-275）末尾就是

        ... '微课' / '本章小结' / '第二十五章' / '肾上腺皮质激素类药物'

    锚词（'PPT'/'学习目标'）在下一个块 ord=514 里。只认 (a) 会把第 25 章的
    章首页定到 ord=514，于是 ord=513 尾部那两行**第 25 章的内容被留在
    第 24 章的切块里**（实测就是这么冒出来的跨章块）。

    `has_next_body`：章首页落在块末尾时，下一个块的开头是不是正文
    （由调用方用 `_next_block_starts_body` 算好传进来）。

    ⚠ 判据 (b) 必须带"后面跟正文"这个条件。单看"章号 + 章名齐备"会把
    **目录条目**也认进来——目录里的"第二十六章胰岛素及口服降糖药"同样齐备，
    实测会因此把章起点错定到 p.11/p.12。
    """
    if _anchor_after(lines, i):
        return True
    if _PAGE_ONLY.match((nxt or "").strip()):
        return False        # 后面跟纯页码 = 目录条目形态
    cand = _clean_chapter_name(name)
    start_at = i
    if len(cand) < 2:
        if not (nxt or "").strip() or _is_anchor_line(nxt):
            return False
        cand = _clean_chapter_name(nxt)
        start_at = i + 1
    if len(cand) < 2:
        return False
    return _body_after(lines, start_at) or has_next_body


def _name_matches_toc(name: str, toc_name: str) -> bool:
    """章名与目录里的章名是否指同一件事。

    `toc_name` 为空（目录里没抄到这一章）时一律放行——不能因为目录缺条目
    就把真章起点丢掉。
    """
    nn, nt = _norm_name(name), _norm_name(toc_name)
    if not nt:
        return True
    if not nn:
        return False
    if nn == nt or nn in nt or nt in nn:
        return True
    # OCR 会把章名截断："治疗中枢神经系统退行性" vs "治疗中枢神经系统退行性疾病的药物"
    return nn[:6] == nt[:6]


def _name_from_lines(lines: list[str], i: int) -> str:
    """章名：同行 + 必要的续行拼接（见 `_chapter_name_multiline`）。"""
    return _chapter_name_multiline(lines, i)


def _ahead_scan_blocks(blocks: list[Block], start_bi: int,
                       max_blocks: int = 6) -> list[str]:
    """章首页所在块之后若干块的文本行，供 `_complete_name_ahead` 使用。

    为什么往后看而不是只看本块：章首页上的章名常被 OCR 截断，而**同一章
    正文页的书眉**上是完整的。实测 doc#91 第 21 章：

        ord=424（章首页，p.230）'第二十一章' / '调血脂药与抗动脉粥样'   ← 被截断
        ord=428（书眉，p.232）  '第二十一章调血脂药与抗动脉粥样硬化药'  ← 完整

    只看本块拿不到「硬化药」三个字；默认取 6 个块，实测第 21 章的完整书眉
    出现在第 4 个块（ord=428），第 22 章同理。
    """
    out: list[str] = []
    for k in range(start_bi + 1, min(len(blocks), start_bi + 1 + max_blocks)):
        out.extend(l.strip() for l in blocks[k].text.split("\n"))
    return out


def _complete_name_ahead(name: str, num: int, ahead: list[str]) -> str:
    """用同一章后面出现的**更完整**章标题补全被截断的章名。

    只在"前面确实是后面那条的子串"时才补——不做模糊猜测。
    """
    nn = _norm_name(name)
    if not nn:
        return name
    for line in ahead:
        line = line.strip()
        if not line or len(line) > 40:
            continue
        m = _CHAPTER_HEAD.match(line)
        if not m or _cn_to_int(m.group(1)) != num:
            continue
        rest = _clean_chapter_name(_strip_chapter_head(line))
        nr = _norm_name(rest)
        if len(nr) > len(nn) and nn in nr:
            return rest
    return name


def _parse_toc(blocks: list[Block]) -> tuple[dict[int, str], set[int]]:
    """从目录页里抽「章号 -> 章名」，作为章名的**权威拼写**。

    为什么要它：正文书眉的章名会被 OCR 截断或掺噪声（实测 doc#91 第 4 章
    的正文章首页抽成「最」、第 21 章抽成「调血脂药与抗动脉粥样」），
    而目录条目的章名往往是完整的。目录只用来**修章名**，不用来定章起点。

    目录的识别判据：条目**整行独立**、**后跟纯页码**，且该页码与块所在
    物理页的差值不符合正文书眉的固定偏移。doc#91 实测：正文书眉的
    「物理页 − 页码」恒为 13/14，而目录条目是页码远大于物理页。

    ⚠ 必须先滤掉空行再按序看"下一行"：doc#91 的目录块里页码与条目常被
    空行隔开（`'第一章绪论'` / `''` / `'26'`），不滤空行则一条也匹配不上，
    实测 `toc` 会整个变成空字典。
    """
    toc: dict[int, str] = {}
    toc_blocks: set[int] = set()
    for bi, b in enumerate(blocks):
        lines = [l.strip() for l in b.text.split("\n") if l.strip()]
        ps = _page_start(b.ref())
        hits = 0
        for i, line in enumerate(lines):
            m = _CHAPTER_LINE.match(line)
            if not m:
                continue
            num = _cn_to_int(m.group(1))
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if num <= 0 or not _PAGE_ONLY.match(nxt):
                continue
            pg = int(nxt)
            # 正文书眉：页码比物理页小一个固定偏移；目录：页码远大于物理页。
            if ps and (ps - pg) in (13, 14):
                continue
            hits += 1
            # 目录条目的章名同样可能被 OCR 断行。目录形态是
            # `章号行 / 章名续行 / 页码行`，所以只能往**页码行之前**拼，
            # 且不能拼到另一个章号行。
            name = _clean_chapter_name(m.group(2))
            k = i + 1
            while k < len(lines) and not _PAGE_ONLY.match(lines[k]) \
                    and k - i <= 2:
                if _CHAPTER_LINE.match(lines[k]) or len(lines[k]) > 24:
                    break
                name = _clean_chapter_name(name + lines[k])
                k += 1
            if len(name) >= 3 and len(name) > len(toc.get(num, "")):
                toc[num] = name
        if hits >= 2:
            toc_blocks.add(bi)
    return toc, toc_blocks


def _pick_name(body_name: str, toc_name: str) -> str:
    """正文章名与目录章名二选一。

    两边都可能被 OCR 截断，所以规则是：

      · 能对上（互为子串或前 4 字相同）→ 取**更长**的那个，它信息更全。
        实测 doc#91 第 12 章的正文抽成「治疗中枢神经系统退行性」、目录里是
        「治疗中枢神经系统退行性疾病的药物」，取长的才对。
      · 对不上 → 取目录的（目录是印刷目录，拼写比书眉可信）。
    """
    if not body_name:
        return toc_name or ""
    if not toc_name:
        return body_name
    nb, nt = _norm_name(body_name), _norm_name(toc_name)
    if not nb or not nt:
        return body_name or toc_name
    if nb in nt or nt in nb or nb[:4] == nt[:4]:
        # 目录更长才采目录（它补全了正文章名）；等长时保留**正文**那个——
        # 实测 doc#91 的目录本身也被 OCR 截断（第 21 章目录与正文都是
        # 「调血脂药与抗动脉粥样硬」9 字，而书眉原文是 12 字），
        # 这种时候正文至少来自章首页本身，更可信。
        return toc_name if len(nt) > len(nb) else body_name
    return toc_name


# ---------------------------------------------------------------------------
# 块形态适配（卡 C-0090）
#
# 病灶：`detect_chapters` 的每一道判据都是按「**块内多行**」设计的——
#   · `_candidates_in` 取 `lines[i + 1]` 当"次行"（OCR 常把章名排到章号下一行）；
#   · `_anchor_after` 在块内后 `ANCHOR_WINDOW` 行里找章首页锚词；
#   · `_parse_toc` 要求"章号行**紧跟**纯页码行"。
# 而教材专线 `ingest_file(mode="textbook")` 传进来的是 `extract` 的**原始块**：
# 扫描教材走完 OCR 之后**一个块就是一行**（实测 doc#91：18762 个块 = 18762 行）。
# 于是 `lines` 长度恒为 1、"次行"恒为空字符串：
#   · `_parse_toc` 一条目录都解析不出（实测返回 **0 条**，章名校验因此整条失效）；
#   · 章名排在下一行的章，全被 `_looks_like_chapter_start` 判成"不像章首页"。
# 实测后果（2026-10-14 真实重跑，见 cards/C-0090/rerun_receipt.json）：
# 42 章只认出 **6 章**，第 2 章跨到 p.110、第 25 章跨到 p.452，跨章块 **9** 个
# （验收要求 0）。
#
# 为什么 C-0087 的 15 项自测没发现：`tools/c0087_textbook_fixture.json` 是从
# **旧库导出的切块后大块**（每块约 700 字、十行上下），恰好满足"块内多行"的假设。
# 夹具与真实专线喂进来的东西不是一种形态，测试便成了自证。
#
# 对策：**只做形态适配，判据一行不改**——逐行块先按页合并成段落块再进核心
# 识别，出参的 `start_index` / `head_line` 再映射回**原始 blocks** 的索引
# （`cut_by_chapter` 拿到的仍是同一批原始块，续接契约不变）。
# 已经是段落块的输入走**恒等路径**，行为逐字不变。
# ---------------------------------------------------------------------------
_ROW_BLOCK_RATIO = 1.3


def _paragraph_view(blocks: list[Block]) -> tuple[list[Block], list[tuple[int, int]], bool]:
    """逐行块 -> 按页合并的段落块。返回 `(view, spans, merged)`。

    `spans[j] = (a, b)`：`view[j]` 覆盖 `blocks[a:b]`。
    输入已经是段落块时**原样返回** `(blocks, 恒等 spans, False)`——
    判据"平均每块行数是否只有 1 行上下"，逐行块必然 ≤ 1，段落块远大于它。
    """
    if not blocks:
        return blocks, [], False
    n_lines = sum(len(b.text.split("\n")) for b in blocks)
    if n_lines > len(blocks) * _ROW_BLOCK_RATIO:
        return blocks, [(i, i + 1) for i in range(len(blocks))], False

    view: list[Block] = []
    spans: list[tuple[int, int]] = []
    i, n = 0, len(blocks)
    while i < n:
        page = blocks[i].page or 0
        j = i + 1
        if page:                     # 只合并"有页号且同页"的连续块
            while j < n and (blocks[j].page or 0) == page:
                j += 1
        view.append(Block(text="\n".join(b.text for b in blocks[i:j]),
                          page=page, para=0, kind=blocks[i].kind,
                          level=blocks[i].level, group=blocks[i].group))
        spans.append((i, j))
        i = j
    return view, spans, True


def _remap_chapters(res: Chapters, spans: list[tuple[int, int]],
                    blocks: list[Block]) -> None:
    """把在合并视图上算出的章边界映射回**原始 blocks** 的索引。

    视图里 `start_index` 是"章标题所在（合并）块的索引"、`head_line` 是
    "章标题在该块内的行号"。还原到原始块：章标题落在 `spans[j] = (a, b)` 的
    第 `head_line` 行 -> 原始下标 `a + head_line`；那个原始块只有一行，
    所以新的 `head_line = 0`。`end_index` 与 `page_end` 随之重算。
    """
    if not spans:
        return
    for c in res.items:
        j = min(max(int(c.start_index), 0), len(spans) - 1)
        a, b = spans[j]
        c.start_index = min(a + max(int(c.head_line), 0), max(a, b - 1))
        c.head_line = 0
        c.page_start = _page_start(blocks[c.start_index].ref())
    for k, c in enumerate(res.items):
        nxt = res.items[k + 1] if k + 1 < len(res.items) else None
        c.end_index = nxt.start_index if nxt is not None else len(blocks)
        last = max(c.start_index, min(c.end_index, len(blocks)) - 1)
        c.page_end = _page_end(blocks[last].ref())
    # "前置内容 N 块"这条 warning 是在合并视图上算的，块数口径换回原始块
    if res.items and res.items[0].is_front_matter:
        fm = res.items[0]
        for i, w in enumerate(res.warnings):
            if "归入「" in w:
                res.warnings[i] = (
                    f"前置内容 {fm.end_index} 块（封面/版权/编委/前言/目录）"
                    f"归入「{fm.name}」，不计入任何章")


def detect_chapters(blocks: list[Block], *,
                    book_title: str = "",
                    min_chapters: int = 3) -> Chapters:
    """认出 `blocks` 里的每一章（对外契约不变，核心见 `_detect_chapters_on`）。

    **卡 C-0090 补的形态适配**：传进来的若是**逐行块**（扫描教材 OCR 之后一个块
    就是一行），先按页合并成段落块再认章，出参索引再映射回原始 blocks；
    已经是段落块时走恒等路径，行为逐字不变。理由见上面 `_paragraph_view` 前那段。
    """
    view, spans, merged = _paragraph_view(blocks)
    res = _detect_chapters_on(view, book_title=book_title,
                              min_chapters=min_chapters)
    if merged:
        _remap_chapters(res, spans, blocks)
    return res


def _detect_chapters_on(blocks: list[Block], *,
                        book_title: str = "",
                        min_chapters: int = 3) -> Chapters:
    """认出 `blocks` 里的每一章。**零模型调用**，纯规则。

    `min_chapters`：认出的章数少于它就算识别失败，走降级
    （`fallback=True`，整份按书名一级切）。默认 3——一份真教材不可能只有
    一两章，而这么少的"章"多半是附录或专题页里的偶然命中。

    返回 `Chapters`。**不修改 `blocks`**，也不碰任何全局状态。
    """
    res = Chapters(book_title=book_title or DEFAULT_BOOK_TITLE)
    if not blocks:
        res.fallback = True
        res.warnings.append("没有任何块，无从识别章")
        return res

    toc, _toc_blocks = _parse_toc(blocks)

    # ---- 扫章首页：章号必须严格递增 ----
    # 严格递增是关键。"首次出现"不够：doc#91 的目录页（p.8~13）里
    # 第一到第四十二章全都整行出现过，按"首次"取会把 42 个章起点全按在目录上。
    #
    # 递增之外还要过一道**章名校验**（见 `_name_matches_toc`）：编委分工页
    # （p.7-8）的"第20章的编写，在此一同表示感谢。"形态上和第 20 章章首页
    # 一模一样（章号 + 后面跟着长行），只有章名能戳穿它——那句话里的"章名"
    # 是"的编写，在此一同表示感谢"，而目录里第 20 章是"治疗心力衰竭的药物"。
    found: list[tuple[int, int, int, str]] = []   # (块下标, 行号, 章号, 章名)
    last = 0
    for bi, b in enumerate(blocks):
        page = b.page or 0
        lines = b.text.split("\n")
        has_next_body = _next_block_starts_body(blocks, bi)
        for (i, num, raw, nxt) in _candidates_in(b):
            if num <= last:
                continue
            if not _looks_like_chapter_start(lines, i, num, raw, nxt,
                                             has_next_body):
                continue
            nm = _name_from_lines(lines, i)
            # 章首页上的章名常被 OCR 截断，用同一章后面书眉上的完整写法补全
            # （实测 doc#91 第 21 章：「调血脂药与抗动脉粥样」+「硬化药」）。
            nm = _complete_name_ahead(nm, num,
                                      _ahead_scan_blocks(blocks, bi) + lines)
            # 章名必须对得上目录。编委分工页的"第20章的编写，在此一同表示感谢。"
            # 形态上和第 20 章章首页一模一样（章号 + 后面跟着长行），
            # 只有章名能戳穿它：目录里第 20 章是"治疗心力衰竭的药物"。
            if not _name_matches_toc(nm, toc.get(num, "")):
                continue
            # 前置页（封面/版权/编委/目录）里认出的第 2 章及以后：只有章名
            # 与目录**逐字相同**才留。教材正文不可能从第 3 页就开始，
            # 这是最稳的一条硬约束（doc#91 的编委页正是这样被挡掉的）。
            if page and page <= _FRONT_MATTER_MAX_PAGE and num > 1:
                if _norm_name(nm) != _norm_name(toc.get(num, "")):
                    continue
            found.append((bi, i, num, nm))
            last = num
            break        # 一个块只当一次章首页，避免同页重复标题把后面吃掉

    if len(found) < min_chapters:
        # ---- 降级：不假装成功 ----
        res.fallback = True
        res.warnings.append(
            f"只认出 {len(found)} 章（少于 {min_chapters}），"
            f"降级为整份按书名一级切")
        res.items = [Chapter(number=FRONT_MATTER_NUMBER, name=FRONT_MATTER_NAME,
                             start_index=0, end_index=len(blocks))]
        return res

    # ---- 补齐章名（目录权威）----
    chapters: list[Chapter] = []
    for (bi, i, num, body_name) in found:
        name = _pick_name(body_name, toc.get(num, ""))
        if not name:
            name = f"（第{num}章）"
        chapters.append(Chapter(number=num, name=name,
                                start_index=bi, end_index=len(blocks),
                                head_line=i,
                                page_start=_page_start(blocks[bi].ref())))

    # ---- 首章之前是前置内容（封面/版权/编委/前言/目录）----
    if chapters and chapters[0].start_index > 0:
        head = Chapter(number=FRONT_MATTER_NUMBER, name=FRONT_MATTER_NAME,
                       start_index=0, end_index=chapters[0].start_index)
        chapters.insert(0, head)
        res.warnings.append(
            f"前置内容 {chapters[0].end_index} 块（封面/版权/编委/前言/目录）"
            f"归入「{FRONT_MATTER_NAME}」，不计入任何章")

    # ---- 每章的结束位置 = 下一章的开始位置 ----
    for k in range(len(chapters)):
        nxt = chapters[k + 1] if k + 1 < len(chapters) else None
        chapters[k].end_index = nxt.start_index if nxt else len(blocks)
        last_block = max(chapters[k].start_index,
                         min(chapters[k].end_index, len(blocks)) - 1)
        chapters[k].page_end = _page_end(blocks[last_block].ref())

    res.items = chapters
    return res


def _page_end(ref: str) -> int:
    m = re.match(r"^p\.(\d+)(?:-(\d+))?$", ref or "")
    if not m:
        return 0
    return int(m.group(2) or m.group(1))

# ---------------------------------------------------------------------------
# 按章切块
# ---------------------------------------------------------------------------
def _make_block(block: Block, lines: list[str], page: int) -> Block:
    """由原块的一部分行造一个新块（版面坐标与分组键照抄）。"""
    return Block(text="\n".join(lines).strip(), page=page, para=block.para,
                 kind=block.kind, level=block.level, x0=block.x0, y0=block.y0,
                 x1=block.x1, y1=block.y1, group=block.group)


def _page_of_line(lo: int, hi: int, chars_before: int, total: int) -> int:
    """块内某处（其前有 `chars_before` 个字符）属于哪一页。

    按**字符占比**在 `lo`~`hi` 之间划页，而不是按行数：OCR 块里
    行长差异很大（表格行、图注、公式），按行数会把页界划歪。

    为什么必须插值：`Block.ref()` 全靠 `page`，出处串要能被引用回链。
    块 `p.216-218` 里第 533 个字符之后属于 p.218；新块若还写 `page=216`，
    检索出来的引用就会指到没有那段字的页上——正是"出处错页"的形态。
    """
    if hi <= lo or total <= 0:
        return lo
    span = hi - lo + 1
    return min(hi, lo + (chars_before * span) // total)


def _rows_to_blocks(block: Block, rows: list[str], pspan: tuple[int, int],
                    base_chars: int) -> list[Block]:
    """把一个块的行按**页**聚成若干新块（同一页的连续行并成一块）。

    `base_chars`：这些行在原块里之前已有多少字符，用来算每行的页号。
    """
    out: list[Block] = []
    total = len(block.text)
    lo, hi = pspan
    cur: list[str] = []
    cur_page = -1
    seen = base_chars
    for line in rows:
        pg = _page_of_line(lo, hi, seen, total)
        if cur and pg != cur_page:
            out.append(_make_block(block, cur, cur_page))
            cur = []
        cur.append(line)
        cur_page = pg
        seen += len(line) + 1        # +1 = 行分隔符
    if cur:
        out.append(_make_block(block, cur, cur_page))
    return [b for b in out if b.text]


def _page_spans(blocks: list[Block]) -> dict[int, tuple[int, int]]:
    """每个块的页区间 (起页, 末页)。

    ⚠ 这里**不能**照抄 `chunk._ref_of()` 的口径（拿缓冲里末块那个块的
    `p.N` 当末页）。`_ref_of` 算的是"一个**切块**覆盖到哪一页"，而切块是
    若干**块**（Block）拼起来的，末页自然等于最后那个块的起页；本函数要算的
    是"**单个 Block** 覆盖了哪几页"，两者不是一回事。

    早先照抄的后果（实测 doc#91 第 24/25 章边界）：一个只落在 p.265 上的
    块，因为"下一个块在 p.275"被判成 `p.265-275`；章边界于是被切在块中间
    的**错误位置**，第 25 章的章首页内容被重复切进两个块
    （`p.265-275` 与 `p.275` 各一份）。

    改用**字符密度**估：单页正文字数取自整份资料的平均值
    （doc#91 实测 929 字符/块 ÷ 1.29 页/块 ≈ 720 字符/页 ⇒ 624239 字符 /
    452 页 ≈ 1381 字符/页）。实测每个块 1~2 页，这个精度足够把章边界
    切在对的位置上。
    """
    texts = [len(b.text or "") for b in blocks]
    pages = [b.page for b in blocks if b.page]
    total_chars = sum(texts)
    n_pages = (max(pages) - min(pages) + 1) if pages else 0
    per_page = (total_chars / n_pages) if n_pages > 0 else 0.0
    spans: dict[int, tuple[int, int]] = {}
    for i, b in enumerate(blocks):
        lo = b.page or 0
        if not lo:
            spans[i] = (0, 0)
            continue
        span = 1
        if per_page > 0:
            span = max(1, int(round(len(b.text or "") / per_page)) or 1)
        spans[i] = (lo, lo + span - 1)
    return spans


def _strip_source_overlap(blocks: list[Block]) -> list[Block]:
    """去掉相邻块之间的**前缀重叠**，再交给 `_Packer`。

    为什么需要：源 `chunks` 本身就是「相邻块重叠 120 字」的产物。doc#91 的
    ord=514 就是以 ord=513 的结尾开头的——

        ord=513 ... '本章小结'（p.265-274 段的结尾）
        ord=514 '(+)' / '问题：' / ... / '本章小结' / '第二十五章' / '肾上腺...'

    ord=513 归第 24 章、ord=514 归第 25 章，中间那段重复的「本章小结」于是
    同时出现在两章的切块里——`cross_chapter_chunks` 会如实把它报成跨章块
    （实测第 24/25、第 33/34 两处）。

    ⚠ 只在**相邻两块**之间去重叠，且只去"后块前缀 == 前块后缀"这种确切的
    重复。不做模糊匹配：错删正文比留下重复严重得多。
    `_Packer` 自己会在切块时重新加 120 字重叠，所以这里去掉不失信息。
    """
    if len(blocks) < 2:
        return blocks
    out = [blocks[0]]
    for b in blocks[1:]:
        prev = out[-1]
        text = b.text
        # 尝试几个重叠长度（字符级），取最长命中
        cut = 0
        limit = min(len(prev.text), len(text), 400)
        for n in range(limit, 15, -1):
            if prev.text.endswith(text[:n]):
                cut = n
                break
        if cut <= 0:
            out.append(b)
            continue
        rest = text[cut:].lstrip("\n")
        if not rest.strip():
            continue        # 整块都是重复内容，整块丢掉
        out.append(_make_block(b, rest.split("\n"), b.page))
    return out


def _trim_to_own_chapter(blocks_: list[Block], ch: Chapter,
                         all_chapters: list[Chapter]) -> list[Block]:
    """把分组里**越界的内容切掉**：从属于**后面**某章的第一个章标题行起截断。

    为什么还需要这一道（`_group_rows` 已经按章切过一次了）：切分点来自
    `detect_chapters` 认出的章首页，而 OCR 会把章首页切在两个块之间。实测
    doc#91 的第 33/34 章：第 34 章的章首页落在 ord=659 末尾 / ord=660，
    于是 ord=650（p.343-345，含第 33 章末与第 34 章初）**整块**被判给
    第 33 章，它末尾的「第三十四章 / 人工合成抗菌药物」就留在了第 33 章的
    切块里——跨章块就是这么冒出来的。

    ⚠ 块**第一行**若是本章的章标题（`_Packer` 需要它建面包屑），从第二行
    才开始找越界点；否则会把块首自己的标题误删。
    """
    later = {c.number: c for c in all_chapters if c.number > ch.number}
    if not later:
        return blocks_
    out: list[Block] = []
    for bi, b in enumerate(blocks_):
        lines = b.text.split("\n")
        start = 0
        if bi == 0 and lines and _CHAPTER_HEAD.match(lines[0].strip()):
            start = 1        # 保留块首自己那一行
        cut = None
        for i in range(start, len(lines)):
            m = _CHAPTER_HEAD.match(lines[i].strip())
            if not m:
                continue
            num = _cn_to_int(m.group(1))
            c = later.get(num)
            if c is None:
                continue
            # 确认这确实是那一章的标题（章名在同一行或后 2 行内）
            name = _norm_name(c.name)
            same = _norm_name(_strip_chapter_head(lines[i].strip()))
            window = "".join(_norm_name(w) for w in lines[i + 1:i + 3])
            if name and (name in same or name in window):
                cut = i
                break
        if cut is None or cut <= start:
            out.append(b)
            continue
        head = "\n".join(lines[:cut]).strip()
        if head:
            out.append(_make_block(b, lines[:cut], b.page))
    return out


def cut_by_chapter(blocks: list[Block], chapters: Chapters,
                   doc_meta: dict | None = None) -> list[Chunk]:
    """按章切块。**硬保证：没有任何一个切块跨章。**

    章内沿用既有参数（目标 700 / 上限 1100 / 重叠 120 / 短块下限 80，
    `core/config.py:121-124`）——做法是逐章调用 `chunk.chunk_blocks()`，
    每个 `_Packer` 只看得到一章的块，长度逻辑与课件**完全同一套代码**，
    没有任何重新实现。

    `heading_path` 形如「书名 > 第X章 章名」，与课件现有
    「课件名 > 小节」形态对齐。

    非正文（`Chapter.number == 0`）也会被切块（内容不能丢），但
    `heading_path` 只有书名一级，不会被误当成某一章。
    """
    meta = dict(doc_meta or {})
    title = (chapters.book_title or meta.get("title")
             or meta.get("book_title") or DEFAULT_BOOK_TITLE)

    groups = _group_rows(blocks, chapters)
    out: list[Chunk] = []
    for ch, group in groups:
        if not group:
            continue
        feed: list[Block] = []
        if not ch.is_front_matter:
            # 章标题当**根** heading 块（level=1），不带书名那一级。
            #
            # 为什么不让书名当根、章标题当第 2 级：`_Packer.finish()` 对
            # "只有一个文档级标题、没有任何正文"的分组会输出一个
            # `heading_path=''` 的**空壳块**（它的 `_breadcrumb()` 此时为空，
            # 且 `_same_title` 去重只认第 1 级）。实测改造后会多出 43 个这种
            # 空壳。把书名挪到后处理统一拼（见下），既避开它，也让书名在
            # 每个切块里**只出现一次**（否则每块正文前缀都会重复书名）。
            feed.append(Block(text=ch.title, page=ch.page_start or 0,
                              kind="heading", level=1))
        if not ch.is_front_matter:
            group = _trim_to_own_chapter(group, ch, chapters.body)
        feed.extend(_strip_source_overlap(group))
        for c in _chunk.chunk_blocks(feed, meta):
            out.append(c)
    # 拼上面包屑的书名一级：全部块统一加前缀，非正文块（`heading_path`
    # 为空）就直接是书名——它们本来就不属于任何一章。
    for c in out:
        c.heading_path = (f"{title} > {c.heading_path}"
                          if (c.heading_path or "").strip() else title)
    # 重排 ord：`_Packer` 每章从 0 数，拼起来会重复
    for i, c in enumerate(out):
        c.ord = i
    return out


def _chapter_starts(blocks: list[Block], chapters: Chapters
                    ) -> tuple[dict[int, list[tuple[int, int]]], Chapter]:
    """把各章的起始点摊平成「块下标 -> [(行号, 章号), ...]」。

    一个块里可能同时出现**两个**章的起始点：上一章的章末与下一章的章首页
    被 OCR 切进同一块（doc#91 的 ord=399 就是「第十九章本章小结 + 第二十章
    章首页」）。所以值是列表而不是单值。
    """
    starts: dict[int, list[tuple[int, int]]] = {}
    by_num: dict[int, Chapter] = {}
    for ch in chapters:
        if ch.is_front_matter:
            continue
        if 0 <= ch.start_index < len(blocks):
            starts.setdefault(ch.start_index, []).append((ch.head_line, ch.number))
        by_num[ch.number] = ch
    for bi in starts:
        starts[bi].sort()
    return starts, by_num


def _group_rows(blocks: list[Block], chapters: Chapters
                ) -> list[tuple[Chapter, list[Block]]]:
    """逐行把块分给各章，并保住页号。

    为什么按**行**而不是按块分配：一个块可能跨章（见 `_chapter_starts`），
    只按块分配时新章的第一块里必然带着上一章的尾巴，"块不跨章"就做不到。
    按行分配后，同一章、同一页的连续行再聚成块，于是跨章块在结构上
    **不可能产生**——不是靠概率，是靠切法。

    返回顺序与 `chapters` 一致，`ord` 由调用方重排。
    """
    spans = _page_spans(blocks)
    starts, _by_num = _chapter_starts(blocks, chapters)

    front = next((c for c in chapters if c.is_front_matter), None)
    body = [c for c in chapters if not c.is_front_matter]
    if front is not None and body:
        front_ch: Chapter | None = front
    elif body:
        front_ch = None
    else:
        front_ch = front

    order: list[Chapter] = []
    if front_ch is not None:
        order.append(front_ch)
    order.extend(body)
    buckets: dict[int, list[Block]] = {id(c): [] for c in order}

    first_body_at = body[0].start_index if body else len(blocks)
    cur: Chapter | None = front_ch if front_ch is not None else (
        body[0] if body else None)

    for bi, blk in enumerate(blocks):
        lines = blk.text.split("\n")
        ps = spans[bi]
        # 每一行之前已累计的字符数（页号按字符占比插值，见 _page_of_line）
        prefix_chars: list[int] = []
        acc = 0
        for ln in lines:
            prefix_chars.append(acc)
            acc += len(ln) + 1
        # 第一个正文章首页之前的块：整块归非正文
        if bi < first_body_at or not body:
            if cur is not None:
                buckets[id(cur)].extend(_rows_to_blocks(blk, lines, ps, 0))
            continue
        cuts = starts.get(bi) or []
        if not cuts:
            if cur is not None:
                buckets[id(cur)].extend(_rows_to_blocks(blk, lines, ps, 0))
            continue
        # 本块内有章起点：按行切开，逐段归属
        prev = 0
        for (hline, num) in cuts:
            hline = max(prev, min(hline, len(lines)))
            seg = lines[prev:hline]
            if seg and cur is not None:
                buckets[id(cur)].extend(
                    _rows_to_blocks(blk, seg, ps, prefix_chars[prev]))
            nxt = _chapter_by_number(order, num)
            if nxt is not None:
                cur = nxt
            prev = hline
        rest = lines[prev:]
        if rest and cur is not None:
            buckets[id(cur)].extend(
                _rows_to_blocks(blk, rest, ps, prefix_chars[prev]))

    return [(ch, buckets[id(ch)]) for ch in order]


def _chapter_by_number(order: list[Chapter], num: int) -> Chapter | None:
    for c in order:
        if c.number == num:
            return c
    return None


def chapter_of_page(chapters: Chapters, page: int) -> Chapter | None:
    """某一物理页属于哪一章（含非正文）。给对账与排查用。"""
    if page <= 0:
        return None
    for ch in chapters:
        if ch.page_start and ch.page_start <= page <= (ch.page_end or ch.page_start):
            return ch
    return None


def _int_to_cn(num: int) -> str:
    """整数转中文数字（42 -> "四十二"）。**只处理 1~99**，与 `_cn_to_int` 对称。

    为什么必须有它：教材正文的书眉与章首页标题用**中文数字**
    （「第十九章抗高血压药」「第二十章」），而编委分工页与部分 OCR 结果用
    阿拉伯数字。只按阿拉伯数字写正则去正文里找章标题，一条也找不到——
    这是实测踩出来的坑，不是理论风险。
    """
    if num <= 0 or num > 99:
        return str(num)
    if num < 10:
        return _CN_DIGITS_INV.get(num, str(num))
    tens, ones = divmod(num, 10)
    head = "" if tens == 1 else _CN_DIGITS_INV.get(tens, "")
    tail = "" if ones == 0 else _CN_DIGITS_INV.get(ones, "")
    return f"{head}十{tail}"


def _chapter_head_re(num: int) -> re.Pattern:
    """「第X章」行首正则，阿拉伯数字与中文数字两种写法都认（编译结果缓存）。"""
    rx = _HEAD_RE_CACHE.get(num)
    if rx is None:
        forms = {str(num), _int_to_cn(num)}
        alt = "|".join(re.escape(f) for f in
                       sorted(forms, key=len, reverse=True))
        rx = re.compile(r"^第\s*(?:" + alt + r")\s*章")
        _HEAD_RE_CACHE[num] = rx
    return rx


_HEAD_RE_CACHE: dict[int, re.Pattern] = {}


def _strip_chapter_head(line: str) -> str:
    """去掉行首的「第X章」（阿拉伯或中文写法），返回剩下的章名候选。"""
    m = _CHAPTER_HEAD.match(line)
    if not m:
        return line
    return line[m.end():]


def _chapter_names_in(chunk_text: str, chapters: Chapters) -> set[int]:
    """一个切块里出现了哪几章的章标题。

    认三种真实 OCR 形态（见 `cross_chapter_chunks`）。章名可能在下一行，
    因为 OCR 常把章号与章名断成两行。
    """
    body = chapters.body
    res: set[int] = set()
    lines = [l.strip() for l in chunk_text.split("\n")]
    for i, s in enumerate(lines):
        m = _CHAPTER_HEAD.match(s)
        if not m:
            continue
        num = _cn_to_int(m.group(1))
        if not any(c.number == num for c in body):
            continue
        ch = next(c for c in body if c.number == num)
        name = _norm_name(ch.name)
        if not name:
            res.add(num)
            continue
        # 章名在同一行，或在后 3 行内（OCR 断行 + 书眉噪声）
        same = _norm_name(_strip_chapter_head(s))
        window = "".join(_norm_name(w) for w in lines[i + 1:i + 4])
        if name in same or name in window:
            res.add(num)
    return res


def cross_chapter_chunks(chunks: list[Chunk], chapters: Chapters,
                         strict_hp: bool = False) -> list[Chunk]:
    """挑出**跨章**的切块——用来验收"跨章块 = 0"。

    判据：一个切块里同时出现**两个不同章的章标题**，它就是跨章块。

    ⚠ 三个坑（都是实测踩出来的，不是理论风险）：

    1. **中文数字**。教材正文的书眉是「第十九章抗高血压药」「第二十章」，
       只用阿拉伯数字 `第19章` 写正则会一条也匹配不到，指标恒为 0、
       对照就失去意义。见 `_chapter_head_re`。
    2. **章号与章名断成两行**。doc#91 的 ord=400（p.218）里是
       `'本章小结' / '第二十章' / '治疗心力衰竭的药物回微课'`，所以章名
       要在后 3 行内一起找。
    3. **目录页不算跨章**。目录一页就抄着全书章号，那是目录原文，不是
       "一章的正文混进了另一章"。用命中章号的**起页跨度**挡掉
       （见 `_SPAN_TOC_PAGES`）。

    `strict_hp=True` 时再要求切块带章级 `heading_path`。改造后的切块满足
    这个条件，能干净地把非正文块（封面/目录/前言，只挂书名一级）排除；
    而**改造前**的切块 `heading_path` 全是书名一级，此时必须传 False
    （默认），否则一滤就全没了、指标恒为 0。
    """
    bad: list[Chunk] = []
    for c in chunks:
        if strict_hp:
            hp = c.heading_path or ""
            if "第" not in hp or "章" not in hp:
                continue        # 非正文块，不参与这个指标
        hit = _chapter_names_in(c.text, chapters)
        # 目录/索引块：命中章号横跨半个书，不是"跨章内容"
        if len(hit) > _TOC_LIKE_CHAPTERS or chapters.chapter_span(hit) > _SPAN_TOC_PAGES:
            continue
        if len(hit) > 1:
            bad.append(c)
    return bad
