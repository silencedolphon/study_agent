# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
# File-ID: SA-22-c0092sec   (project study_agent; keep this line)
"""教材专线 · 节级切分：从块的**正文文本**里抽出「章 → 节」结构。零模型调用。

为什么要这个模块（人的原话，2026-10-14）：

    增加一个预习模块，选择章节后将那一章节拆成**更加细的知识点（根据教材的
    小标题来拆）**，之后再开一个新的大模型对该章节做预习总结……考虑一下如何
    缩减成本和压缩时间，但是原本的知识点总结质量不能下降。

上半句的落点是"按教材小标题拆细"。但**现有数据里没有节级结构**：实测 doc#91
的 `chunks.heading_path` 只有两级（`药理学 第6版 刘晓东 > 第39章 抗恶性肿瘤药`），
882 块里 1 级 1 个 + 2 级 42 个、**零个节级**；`chunks.topic` 882 块全 NULL。

可是**节标题就躺在块正文里**——它是 OCR 出来的原文，只是没人去认。所以本模块
只做一件事：用正则把这些标题行认出来，**一个模型调用都不花**。这也是人能接受的
"缩减成本"：拆细这一步是纯规则的，钱应该花在后面那一次预习总结上。

--------------------------------------------------------------------------
四种真实形态（全部在 doc#91 上实测，块级命中数为证）
--------------------------------------------------------------------------
  · `第X节<标题>`     —— 157 块命中，节级主形态
  · `一、<标题>`      —— 272 块命中，节下一级
  · `（一）<标题>`    —— 145 块命中，再下一级
  · `1.1 <标题>`      —— 24 块命中，数字编号形态（doc#91 全部是数值/单位行，
                          见下面「误报」一节，但别的教材会用到，故保留）

`第X节` 的标题**会换行**，实测 5 处（ord=49/55/57/528/683）：

    '第二节'          ← 标题这一行是空的
    '房室模型'        ← 真正的标题在下一行
    'PPT'

所以标题为空时往后拼一行（拼的判据见 `_title_from_next_line`）。不拼的话，
第2章会凭空少掉三个节——那不是"没识别到"，是"识别到了却丢了名字"。

--------------------------------------------------------------------------
★ 目录页必须排除（不排会凭空多出一堆假节）
--------------------------------------------------------------------------
doc#91 的目录在 **p.7~14**（块 ord=5~17 共 13 块）。目录页的形态是
「节名 + 印刷页码」逐条堆叠，与正文的「节标题」逐字同形：

    ord=6  p.8-9    '28 / 一、药物反应类型 / 地位 / 28 / 二、量效关系 / …'
    ord=15 p.13     '第一节喹诺酮类药物 / 五、主要用于病因性预防的药物 / 376 …'

`第一节喹诺酮类药物` 是**抗微生物药那一章的节**，却出现在 p.13 的目录里；照单
全收会让第1章绪论凭空多出几十个节。本模块的判据是**目录条目独有的形态**：
一行是标题、紧跟的下一非空行是**纯页码**。实测这个判据在 doc#91 上：

    目录块（ord 5~17）配对数 = 9,35,28,31,22,20,33,34,36,32,31,34,24
    非目录块（ord>=18）配对数 = 0（构造性验证见 `detect_toc_blocks` 的返回证据）

⚠ 数值形态 `1.1` **不参与**目录配对：p.275 的一张表里全是 `1.0 / 0.8` 数值行，
拿它去配对会把整块正文误判成目录（实测过，块 ord=527）。目录条目是文字标题，
不需要数值形态来佐证。

--------------------------------------------------------------------------
降级策略（不假装成功）
--------------------------------------------------------------------------
· 整份识别不出任何章 —— 用 `textbook.detect_chapters` 的降级结果（章号 0），
  并把全文当作一章，产出一节、`fallback=True`，在统计里如实上报。
· 某一章抽不出任何节 —— 给**整章一节**（标题=章名，`fallback=True`），
  统计里单列 `fallback_chapters` 占比。人要知道哪些章是"整章一节"的。
· 找不到所属章的块（章区间/页区间都对不上）—— 不产出节，计 `no_chapter`。

--------------------------------------------------------------------------
与 `core/textbook.py` 的分工（本模块**不改**那个文件）
--------------------------------------------------------------------------
`textbook.py` 管**章**（含目录块识别、章首页锚词、跨章切分），本模块管**节**。
章信息优先向调用方要（`chapters=` 参数，见 `ChapterSpan`）；没给时回落到
`textbook.detect_chapters`。两处都只**读**、不写。

块内页码用与 `textbook._page_of_line` **同一口径**的字符占比插值（那个函数是
私有名，所以本模块内自带一份同口径实现，见 `_page_of_line`），**再夹成章内
单调不减**：doc#91 的块 `source_ref` 是 `p.19-30` 这种跨页串，同一章几十个块的
起始页全是 19；不插值的话节的页范围会全挤在一页上，而只插值不夹单调又会出现
"节4 在 p.15、节3 在 p.16"的回退（块间重叠所致）。所以：**页码是估算**，
产物 `detection.stats.page_note` 里如实写明，精确回链一律走
`block_ord_start/block_ord_end`（= `chunks.ord`）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .extract import Block

# ---------------------------------------------------------------------------
# 标题行形态
# ---------------------------------------------------------------------------
# 中文数字与阿拉伯数字的编号，与 `core/textbook.py` 的 `_CN_NUM` 同一套写法
_CN_NUM = r"[0-9]{1,3}|[一二三四五六七八九十]{1,6}"
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
              "七": 7, "八": 8, "九": 9}

# 「第X节<标题>」：整行以「第X节」开头。行内出现不算（正文里"见第3节"这类引用
# 很多），必须是行首——与 textbook 认章的 `_CHAPTER_LINE` 同一条纪律。
RE_JIE = re.compile(r"^第\s*(" + _CN_NUM + r")\s*节\s*(.*)$")
# 「一、<标题>」（标题可以为空——实测有 5 处「第X节」把标题甩到了下一行，
# 「一、」同理；空标题交给 `_title_from_next_line` 去拼，拼不到就丢掉）
RE_CN1 = re.compile(r"^([一二三四五六七八九十]{1,3})\s*、\s*(.*)$")
# 「（一）<标题>」：全角/半角括号都认
RE_CN2 = re.compile(r"^[（(]\s*([一二三四五六七八九十]{1,3})\s*[）)]\s*(.*)$")
# 「1.1 <标题>」：两级以内的数字编号，后面不能再跟数字或点（挡掉 1.2.3 与 1.1.1）。
# ⚠ 编号后面**必须有空白**：中文教材里数字编号与标题之间一定有空档，而数值/单位
#   行是紧贴的——实测 doc#102 的 `2.5单位/h`、`15.1型糖尿病患者应选用（`、
#   `2.5-羟色胺`（OCR 把「5-羟色胺」错序成「2.5-…」）全是靠这一条挡掉的。
RE_DOT = re.compile(r"^(\d{1,2}\.\d{1,2})(?![\d.])\s+(.*)$")
# 宽口径（**只统计、不采纳**）：行首只要是 `\d+.\d+` 就算。卡 C-0092 里实测的
# 「1.1」命中数接近这一档；摆出来是为了让人看清"宽口径有多少、严口径剩多少"。
RE_DOT_LOOSE = re.compile(r"^(\d{1,2}\.\d{1,2})(?![\d.])")
# 书眉/目录里的章行（只用于目录配对与块归属，不产出节）
RE_CHAPTER_HEAD = re.compile(r"^第\s*([0-9]{1,3})\s*章")
# 纯页码行：目录条目后面跟的就是它
RE_PAGE_ONLY = re.compile(r"^\d{1,3}$")
# 句末标点：正文句子才有，标题行不会有
RE_SENTENCE_END = re.compile(r"[。！？；]")
# 汉字
RE_CJK = re.compile(r"[\u4e00-\u9fff]")
# 标题里要洗掉的噪声符号（OCR 把分隔圆点、项目符号认成这些）
_JUNK_EDGE = re.compile(r"^[\s‧·∙・.、,，:：\-—_]+|[\s‧·∙・]+$")
# 教材栏目名：标题拼接时要跳过（doc#91 的节首页紧跟一个「PPT」标记）
ANCHOR_WORDS: tuple[str, ...] = ("学习目标", "PPT", "本章小结", "知识拓展", "思考题")

# 形态名（产物与统计里的稳定标识，不随语言改）
FORM_JIE = "第X节"
FORM_CN1 = "一、"
FORM_CN2 = "（一）"
FORM_DOT = "1.1"
FORMS: tuple[str, ...] = (FORM_JIE, FORM_CN1, FORM_CN2, FORM_DOT)
# 层级：第X节 > 一、 > （一）。数字编号形态按"x.y 的 x 是节"算，挂在节级之下。
FORM_LEVEL = {FORM_JIE: 1, FORM_CN1: 2, FORM_CN2: 3, FORM_DOT: 2}

# 标题正文的长度上限。实测 doc#91 最长的合法小标题是
# 「（一）DNA甲基转移酶（DNMT）抑制剂」20 字、「一、磷脂代谢及非留体抗炎药的
# 药理作用」18 字；而误报的正文句子都在 40 字以上。取 36 留足余量。
MAX_TITLE_CHARS = 36
# 拼接下一行时，那一行的长度上限（太长的是正文，不是标题）
MAX_JOINED_TITLE_CHARS = 24
# 一个标题至少要有这么多汉字（挡掉 "0.1" "1.9 ±0.6" "99.6" 这类数值行）
MIN_TITLE_CJK = 2


def _cn_to_int(s: str) -> int:
    """中文/阿拉伯数字编号转 int。`十`=10、`十一`=11、`二十一`=21、`3`=3。

    与 `core/textbook.py::_cn_to_int` 语义一致（本模块不 import 它，避免把
    私有名当契约）。
    """
    s = (s or "").strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    if "十" not in s:
        return _CN_DIGITS.get(s, 0)
    head, _, tail = s.partition("十")
    tens = _CN_DIGITS.get(head, 1) if head else 1
    ones = _CN_DIGITS.get(tail, 0) if tail else 0
    return tens * 10 + ones


def clean_title(text: str) -> str:
    """标题清洗：去首尾噪声符号、去尾部「微课」图标词、压缩空白。

    为什么要去「微课」：教材版面在节标题右侧印了一个微课图标，OCR 把它并进
    标题行——实测 `一、药物跨膜转运及其影响因素微课`、
    `第二节‧药物剂量与效应关系回微课`。留着它，人看到的每个标题尾巴都挂一个
    噪声词；而不清洗又会污染后续按标题做的知识点命名。

    **原文不丢**：`Section.raw` 保留未清洗的整行，产物里两个都给。
    """
    s = _JUNK_EDGE.sub("", (text or "").strip())
    s = re.sub(r"(回)?微课$", "", s)
    s = _JUNK_EDGE.sub("", s)
    return re.sub(r"\s+", "", s)


def _match_title_form(line: str) -> tuple[str, str, str] | None:
    """一行是不是标题？返回 `(形态名, 编号原文, 标题原文)`；不是则 None。

    顺序有意：先认「第X节」（最强信号），再认「一、」，最后才是「1.1」
    （最弱、最容易与数值行混淆）。
    """
    s = (line or "").strip()
    if not s:
        return None
    m = RE_JIE.match(s)
    if m:
        return FORM_JIE, m.group(1), m.group(2)
    m = RE_CN1.match(s)
    if m:
        return FORM_CN1, m.group(1), m.group(2)
    m = RE_CN2.match(s)
    if m:
        return FORM_CN2, m.group(1), m.group(2)
    m = RE_DOT.match(s)
    if m:
        return FORM_DOT, m.group(1), m.group(2)
    return None


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ChapterSpan:
    """一章的范围。`start_index/end_index` 是**块下标**（含头不含尾），
    由 `textbook.detect_chapters` 给出；调用方直接给页码时可为 -1。"""
    number: int
    name: str
    page_start: int = 0
    page_end: int = 0
    start_index: int = -1
    end_index: int = -1

    @property
    def is_front_matter(self) -> bool:
        return self.number <= 0

    @property
    def title(self) -> str:
        if self.is_front_matter:
            return self.name or "非正文"
        return f"第{self.number}章 {self.name}".strip()


@dataclass
class Section:
    """一节（或一个小标题）。`title` 是清洗后的标题，`raw` 是原文行。"""
    chapter_no: int                 # 所属章号（0 = 非正文，正常不产出）
    chapter_name: str
    index: int                      # 章内序号，1 起
    form: str                       # 第X节 / 一、 / （一） / 1.1 / 整章降级
    level: int                      # 1=节 2=一、 3=（一）；降级节为 1
    number: str                     # 编号原文（"三" / "1" / ""）
    title: str                      # 清洗后的标题
    raw: str                        # 原文行（未清洗）
    page_start: int                 # 标题所在页（块内按字符占比插值）
    page_end: int = 0               # 本节内容覆盖到的页（下一节起点/章末）
    block_ord_start: int = 0        # 标题所在块在文档内的 ord
    block_ord_end: int = 0          # 本节最后一块的 ord
    line_no: int = 0                # 标题在块内的行号（0 起）
    parent_index: int | None = None  # 上级节的章内序号（无则 None）
    fallback: bool = False          # 整章降级出来的那一节

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "form": self.form,
            "level": self.level,
            "number": self.number,
            "title": self.title,
            "raw": self.raw,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "block_ord_start": self.block_ord_start,
            "block_ord_end": self.block_ord_end,
            "line_no": self.line_no,
            "parent_index": self.parent_index,
            "fallback": self.fallback,
        }


@dataclass
class ChapterSections:
    """一章及其节清单。"""
    number: int
    name: str
    page_start: int
    page_end: int
    sections: list[Section] = field(default_factory=list)
    n_blocks: int = 0

    @property
    def fallback(self) -> bool:
        """整章一节（抽不出任何真节）。"""
        return bool(self.sections) and all(s.fallback for s in self.sections)

    @property
    def labeled_sections(self) -> list[Section]:
        """真节（非降级、且是「第X节」级）——覆盖率统计用它。"""
        return [s for s in self.sections if not s.fallback and s.level == 1]

    def to_dict(self) -> dict:
        return {
            "no": self.number,
            "name": self.name,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "n_blocks": self.n_blocks,
            "n_sections": len(self.sections),
            "fallback": self.fallback,
            "sections": [s.to_dict() for s in self.sections],
        }


@dataclass
class Sections:
    """`detect_sections` 的结果。"""
    chapters: list[ChapterSections] = field(default_factory=list)
    toc_blocks: list[int] = field(default_factory=list)     # 块下标
    toc_evidence: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    doc_id: int = 0
    book_title: str = ""
    chapter_source: str = ""        # given / textbook.detect_chapters / fallback

    # ---- 便捷视图（给下游预习模块用）----
    @property
    def sections(self) -> list[Section]:
        return [s for c in self.chapters for s in c.sections]

    @property
    def n_sections(self) -> int:
        return sum(len(c.sections) for c in self.chapters)

    def chapter(self, number: int) -> ChapterSections | None:
        for c in self.chapters:
            if c.number == number:
                return c
        return None

    def to_dict(self) -> dict:
        return {
            # ⚠ 顶层**绝不**用 `knowledge_points` 键：`tools/dashboard.py:540`
            #   的 `_kp_load_dir()` 会扫 `logs/knowledge/*.json`，只认那个键，
            #   用了它本产物就会被当成知识点产物。
            "meta": {
                "doc_id": self.doc_id,
                "book_title": self.book_title,
                "generator": "core/sections.py",
                "version": "C-0092-v1",
                "generated_at": time.time(),
                "chapter_source": self.chapter_source,
                "n_chapters": len(self.chapters),
                "n_sections": self.n_sections,
                "page_range": self.stats.get("page_range", [0, 0]),
            },
            "chapters": [c.to_dict() for c in self.chapters],
            "detection": {
                "toc_blocks": self.toc_blocks,
                "toc_evidence": self.toc_evidence,
                "stats": self.stats,
                "warnings": self.warnings,
            },
        }


# ---------------------------------------------------------------------------
# 目录页识别
# ---------------------------------------------------------------------------
def _toc_pairs_in(text: str) -> int:
    """块里有几条「标题行 + 紧跟纯页码」的目录条目。

    ⚠ 只用**文字标题**形态（第X节 / 一、 / （一） / 第X章）配对，**不用**数值
    形态 `1.1`：doc#91 的 p.275 有整块数值（`1.0 / 0.8 / 2.3`），拿数值去配对
    会把正文块误判成目录（实测块 ord=527）。目录条目是文字，不需要数值佐证。
    """
    lines = [l.strip() for l in (text or "").split("\n")]
    n = 0
    for i, s in enumerate(lines):
        if not s:
            continue
        if not (RE_JIE.match(s) or RE_CN1.match(s) or RE_CN2.match(s)
                or RE_CHAPTER_HEAD.match(s)):
            continue
        for j in range(i + 1, min(i + 3, len(lines))):
            if not lines[j]:
                continue         # 允许中间夹一个空行
            if RE_PAGE_ONLY.match(lines[j]):
                n += 1
            break
    return n


def detect_toc_blocks(blocks: Sequence[Block], *,
                      min_pairs: int = 2) -> tuple[list[int], dict]:
    """找出目录页块。返回 `(块下标列表, 证据)`，**不改任何东西**。

    判据只有一条：块内「标题行 + 紧跟纯页码」的配对数 >= `min_pairs`。
    为什么这条够用（doc#91 实测）：

        目录块 ord 5~17（p.7~14）：配对数 9~36
        正文块 ord >= 18        ：配对数 **0**

    正文页的书眉是「第X章 章名 + 页码」，节标题行后面跟的是正文，不是页码，
    所以正文永远配不出对。阈值 2 已经离两侧的真实分布很远（下一档就是 9）。
    """
    hits: list[int] = []
    detail: dict[int, int] = {}
    for bi, b in enumerate(blocks):
        n = _toc_pairs_in(b.text)
        if n >= min_pairs:
            hits.append(bi)
            detail[bi] = n
    return hits, {
        "min_pairs": min_pairs,
        "n_blocks": len(hits),
        "pairs": {str(k): v for k, v in detail.items()},
        "rule": "块内『标题行 + 紧跟纯页码』配对数 >= min_pairs（数值形态 1.1 不参与配对）",
    }


# ---------------------------------------------------------------------------
# 章归属
# ---------------------------------------------------------------------------
def _normalize_chapters(chapters: Any, blocks: Sequence[Block], *,
                        book_title: str) -> tuple[list[ChapterSpan], str, list[str]]:
    """把调用方给的章信息统一成 `list[ChapterSpan]`。

    接受四种写法（都只读）：
      · None                —— 回落到 `textbook.detect_chapters`；
      · `textbook.Chapters` —— 有 `.items`，带块下标区间（最准）；
      · `textbook.Chapter` 列表；
      · `ChapterSpan` 列表 / dict 列表 / `(章号, 章名[, 起页, 止页])` 元组列表。
    """
    warnings: list[str] = []
    if chapters is None:
        try:
            from . import textbook as _tb
        except Exception as exc:                      # pragma: no cover
            warnings.append(f"取章信息失败（import textbook）：{exc}")
            return _single_chapter(blocks, book_title), "fallback", warnings
        try:
            got = _tb.detect_chapters(blocks, book_title=book_title)
        except Exception as exc:                      # pragma: no cover
            warnings.append(f"取章信息失败（detect_chapters）：{exc}")
            return _single_chapter(blocks, book_title), "fallback", warnings
        spans = [ChapterSpan(number=c.number, name=c.name,
                             page_start=c.page_start, page_end=c.page_end,
                             start_index=c.start_index, end_index=c.end_index)
                 for c in got.items]
        src = "fallback" if getattr(got, "fallback", False) else "textbook.detect_chapters"
        warnings.extend(list(getattr(got, "warnings", []) or []))
        if not spans:
            warnings.append("detect_chapters 返回 0 章，整份按一章处理")
            return _single_chapter(blocks, book_title), "fallback", warnings
        return spans, src, warnings

    items = getattr(chapters, "items", None)
    if items is None:
        items = chapters
    out: list[ChapterSpan] = []
    for it in items:
        if isinstance(it, ChapterSpan):
            out.append(it)
            continue
        if isinstance(it, dict):
            out.append(ChapterSpan(
                number=int(it.get("number") or it.get("no") or 0),
                name=str(it.get("name") or ""),
                page_start=int(it.get("page_start") or 0),
                page_end=int(it.get("page_end") or 0),
                start_index=int(it.get("start_index", -1)),
                end_index=int(it.get("end_index", -1))))
            continue
        if hasattr(it, "number"):
            out.append(ChapterSpan(
                number=int(getattr(it, "number") or 0),
                name=str(getattr(it, "name") or ""),
                page_start=int(getattr(it, "page_start", 0) or 0),
                page_end=int(getattr(it, "page_end", 0) or 0),
                start_index=int(getattr(it, "start_index", -1)),
                end_index=int(getattr(it, "end_index", -1))))
            continue
        if isinstance(it, (tuple, list)):
            vals = list(it) + [0, 0]
            out.append(ChapterSpan(number=int(vals[0]), name=str(vals[1]),
                                   page_start=int(vals[2] or 0),
                                   page_end=int(vals[3] or 0)))
            continue
        raise TypeError(f"不认识的章信息：{it!r}")
    if not out:
        warnings.append("调用方给的章信息为空，整份按一章处理")
        return _single_chapter(blocks, book_title), "fallback", warnings
    return out, "given", warnings


def _single_chapter(blocks: Sequence[Block], book_title: str) -> list[ChapterSpan]:
    """整份当一章（最后的降级）。章号 0 = 非正文口径，但这里必须产出节，
    所以用一个专用章号 1、名字取书名，并在 warnings 里说清。"""
    pages = [b.page for b in blocks if b.page] or [0]
    return [ChapterSpan(number=1, name=book_title or "全文",
                        page_start=min(pages), page_end=max(pages),
                        start_index=0, end_index=len(blocks))]


def _headline_chapter_no(block: Block) -> int:
    """块首行的书眉章号（"第1章 绪论" → 1）。没有则 0。

    doc#91 实测：882 块里首行书眉章号与 `chunks.heading_path` 的章号
    **0 处不一致**，所以这是最可靠的块级归属信号。
    """
    first = (block.text or "").split("\n", 1)[0].strip()
    m = RE_CHAPTER_HEAD.match(first)
    return int(m.group(1)) if m else 0


def _owner_of(block_index: int, block: Block,
              spans: Sequence[ChapterSpan]) -> ChapterSpan | None:
    """块属于哪一章。优先级：块下标区间 → 首行书眉 → 页区间 → 最近的上一章。

    为什么块下标区间排第一：`textbook.detect_chapters` 的区间就是在**同一份
    blocks** 上算出来的，块级精确，不受跨章块影响；书眉与页码是给调用方直接
    传章信息（没有下标）时用的回落。
    """
    for c in spans:
        if c.start_index >= 0 and c.end_index > c.start_index \
                and c.start_index <= block_index < c.end_index:
            return c
    no = _headline_chapter_no(block)
    if no:
        for c in spans:
            if c.number == no:
                return c
    pg = block.page or 0
    if pg:
        for c in spans:
            if c.page_start and c.page_start <= pg <= (c.page_end or c.page_start):
                return c
        prev = None
        for c in spans:
            if c.page_start and c.page_start <= pg:
                prev = c
        if prev is not None:
            return prev
    return None


# ---------------------------------------------------------------------------
# 块内页码（与 core/textbook.py::_page_of_line 同口径）
# ---------------------------------------------------------------------------
def _page_of_line(lo: int, hi: int, chars_before: int, total: int) -> int:
    """块内某一行属于哪一页：按**字符占比**在 `lo`~`hi` 之间划页。

    与 `core/textbook.py:793` 同一口径（那边是私有名，本模块自带一份，两边
    理由相同）：OCR 块里的行长差异极大（表格行、图注、公式），按行数划页会把
    页界划歪；而 doc#91 的块 `source_ref` 是 `p.19-30` 这种跨页串，**不插值
    的话同一章几十个节的页码会全等于块起始页**，出处就回链不到具体页了。
    """
    if hi <= lo or total <= 0:
        return lo
    span = hi - lo + 1
    return min(hi, lo + (chars_before * span) // total)


def _line_pages(block: Block, pspan: tuple[int, int]) -> list[int]:
    """块内每一行的页号（与 `text.strip()` 后的行一一对应）。"""
    lo, hi = pspan
    total = len(block.text or "")
    out: list[int] = []
    seen = 0
    for line in (block.text or "").split("\n"):
        out.append(_page_of_line(lo, hi, seen, total))
        seen += len(line) + 1          # +1 = 行分隔符，与 textbook 一致
    return out


# ---------------------------------------------------------------------------
# 标题拼接与过滤
# ---------------------------------------------------------------------------
def _is_anchor(line: str) -> bool:
    s = (line or "").strip()
    return any(s == a or s.startswith(a) for a in ANCHOR_WORDS)


def _title_from_next_line(lines: Sequence[str], i: int) -> str:
    """「第X节」这一行没带标题时，往后拼一行。拼不动就返回空串。

    实测 doc#91 有 5 处这种形态（ord=49/55/57/528/683）：

        '第二节' / '房室模型' / 'PPT'

    拼接判据（宁可拼不到，也不能把正文吃成标题）：
      · 下一非空行存在，且不是纯页码；
      · 不是栏目名（PPT / 学习目标 / 本章小结…）；
      · 不是另一个标题行（连续两个标题说明这一行本来就没标题）；
      · 长度 2~24 字，且不含句末标点（正文句子才有）。
    """
    for j in range(i + 1, min(i + 3, len(lines))):
        s = (lines[j] or "").strip()
        if not s:
            continue
        if RE_PAGE_ONLY.match(s) or _is_anchor(s) or _match_title_form(s):
            return ""
        if not (2 <= len(s) <= MAX_JOINED_TITLE_CHARS) or RE_SENTENCE_END.search(s):
            return ""
        return s
    return ""


def _reject_reason(form: str, title: str) -> str:
    """标题不合格的原因；合格返回空串。"""
    t = title or ""
    if not t:
        return "no_title"
    if len(t) > MAX_TITLE_CHARS:
        return "too_long"
    if RE_SENTENCE_END.search(t):
        return "sentence"
    if len(RE_CJK.findall(t)) < MIN_TITLE_CJK:
        return "non_cjk"
    # 连排编号（"一、二、三、"）是行内列举，不是小标题
    if form in (FORM_CN1, FORM_CN2) and RE_CN1.match(t):
        return "list_run"
    # ★ 数字形态额外严两档：
    #   ① 标题必须以**汉字**开头——`\d+\.\d+` 与数值/单位行（`99.7%，口服…`、
    #      `3.5-HT受体激动药` 这种其实是「5-HT3受体」的错序）无法靠长度区分；
    #   ② 编号后必须有空白（在 `RE_DOT` 的正则里，见那里的注释）。
    #   代价是漏掉极少数以英文/数字起头的数字编号标题——宁可漏、不可错。
    if form == FORM_DOT and not RE_CJK.match(t[0]):
        return "dot_not_cjk_start"
    return ""


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def detect_sections(blocks: Sequence[Block],
                    chapters: Any = None, *,
                    book_title: str = "",
                    doc_id: int = 0,
                    block_pages: Sequence[tuple[int, int]] | None = None,
                    exclude_toc: bool = True) -> Sections:
    """从块正文里抽出「章 → 节」。**纯规则、零模型调用、只读入参。**

    `blocks`     —— `core.extract.Block` 列表（按文档顺序）。
    `chapters`   —— 章信息，见 `_normalize_chapters`；None 时回落到
                    `textbook.detect_chapters`。
    `book_title` —— 书名（降级时的章名、产物 meta 用）。
    `block_pages`—— 每块的 `(起始页, 结束页)`；缺省用 `(block.page, block.page)`
                    （没有跨页信息就不插值）。doc#91 从 `chunks.source_ref`
                    的 `p.19-30` 解析后传进来。
    `exclude_toc`—— 是否排除目录页块（默认 True；关掉只为对照实验，见 verify）。
    """
    blocks = list(blocks)
    spans, src, warnings = _normalize_chapters(chapters, blocks,
                                              book_title=book_title)
    if block_pages is None:
        block_pages = [(b.page or 0, b.page or 0) for b in blocks]
    elif len(block_pages) != len(blocks):
        raise ValueError(f"block_pages 长度 {len(block_pages)} 与 blocks "
                         f"{len(blocks)} 不一致")

    toc_idx, toc_evidence = (detect_toc_blocks(blocks) if exclude_toc
                             else ([], {"disabled": True}))
    toc_set = set(toc_idx)

    # 宽口径数字形态（只为把"宽/严"两档都摆在报告里，不参与采纳）
    loose_dot_blocks = 0
    loose_dot_lines = 0
    for b in blocks:
        hit = False
        for line in (b.text or "").split("\n"):
            if RE_DOT_LOOSE.match(line.strip()):
                loose_dot_lines += 1
                hit = True
        loose_dot_blocks += 1 if hit else 0

    # 统计口径：候选=形态命中；采纳=通过全部判据；其余按原因分档。
    form_stats: dict[str, dict[str, int]] = {
        f: {"candidate_lines": 0, "candidate_blocks": 0, "kept": 0,
            "rejected": 0, "joined_from_next_line": 0} for f in FORMS}
    reject_reasons: dict[str, int] = {}
    cand_blocks: dict[str, set[int]] = {f: set() for f in FORMS}

    # 章 → 已采纳的节（保持文档顺序）
    by_chapter: dict[int, list[Section]] = {}
    order_span: list[ChapterSpan] = []
    per_chapter_blocks: dict[int, int] = {}
    # 跨块重复去重表：key = (章号, 形态, 标题) → 最近一次采纳的块 ord
    last_title_by_chapter: dict[tuple[int, str, str], int] = {}
    # 页码游标（按章）：doc#91 的块是**摊派式** ref（同一章几十个块都从章首页
    # 起步、上界递增，块间还有重叠），直接采信块内插值会出现"节4 在 p.15、
    # 节3 在 p.16"这种回退。游标把它夹成单调不减——宁可保守，不可乱序。
    page_cursor: dict[int, int] = {}
    front_matter_blocks = 0
    no_chapter_blocks = 0
    toc_blocks_skipped = 0

    for bi, b in enumerate(blocks):
        lines = (b.text or "").split("\n")
        # 目录块**整块**跳过：不认节、也不认标题。它们的形态与正文逐字同形，
        # 逐行过滤挡不住（"第一节喹诺酮类药物"本身是个合法标题）。
        if bi in toc_set:
            if any(_match_title_form(l) for l in lines):
                toc_blocks_skipped += 1
            continue
        owner = _owner_of(bi, b, spans)
        if owner is None:
            no_chapter_blocks += 1
            # 找不到章也要如实统计候选，但产不出节
            for line in lines:
                got = _match_title_form(line)
                if got:
                    form_stats[got[0]]["candidate_lines"] += 1
                    cand_blocks[got[0]].add(bi)
                    form_stats[got[0]]["rejected"] += 1
                    reject_reasons["no_chapter"] = \
                        reject_reasons.get("no_chapter", 0) + 1
            continue
        if owner.is_front_matter:
            front_matter_blocks += 1
            continue
        if owner.number not in per_chapter_blocks:
            per_chapter_blocks[owner.number] = 0
            by_chapter[owner.number] = []
            order_span.append(owner)
        per_chapter_blocks[owner.number] += 1

        pages = _line_pages(b, block_pages[bi])
        for i, line in enumerate(lines):
            got = _match_title_form(line)
            if not got:
                continue
            form, number, body = got
            form_stats[form]["candidate_lines"] += 1
            cand_blocks[form].add(bi)
            title = clean_title(body)
            joined = False
            # ★ 数值形态**不拼接**下一行：`第X节`/`一、`/`（一）` 的空标题是
            #   "标题被 OCR 甩到下一行"的真实形态（doc#91 实测 5 处），而数值
            #   形态的空标题几乎必然是表格数值——实测拼出来的是
            #   '11.5'+'福辛普利'、'1.0'+'短效'、'4.0'+'泼尼松龙' 这种假标题。
            if not title and form != FORM_DOT:
                nxt = _title_from_next_line(lines, i)
                title = clean_title(nxt) if nxt else ""
                joined = bool(title)
            reason = _reject_reason(form, title)
            if not reason:
                # 跨块重复：相邻两块内容有重叠，同一个标题会被认两次
                # （实测 doc#91 有 5 组，如 ord=39/40 的「四、药物代谢」、
                # ord=432/433 的「第一节调血脂药」，距离恒为 1）。
                #
                # key 必须带**形态**：教材里"节标题"与它下面的"一、"小标题
                # 同名是正常的（第16章「第一节心律失常发生的电生理学机制」
                # 下面就有「二、心律失常发生的电生理学机制」），只按标题去重
                # 会把真实的小标题吃掉。
                key = (owner.number, form, title)
                last = last_title_by_chapter.get(key)
                if last is not None and 0 <= b.para - last <= 1:
                    reason = "dup_adjacent_block"
            if reason:
                form_stats[form]["rejected"] += 1
                form_stats[form].setdefault("reasons", {})
                form_stats[form]["reasons"][reason] = \
                    form_stats[form]["reasons"].get(reason, 0) + 1
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
                continue
            # 页码：块内插值 → 夹到本章单调不减（见 page_cursor 的说明）
            pg = pages[i] if i < len(pages) else (b.page or 0)
            pg = max(pg, page_cursor.get(owner.number, 0), b.page or 0)
            page_cursor[owner.number] = pg
            sec = Section(chapter_no=owner.number, chapter_name=owner.name,
                          index=len(by_chapter[owner.number]) + 1, form=form,
                          level=FORM_LEVEL[form], number=str(number),
                          title=title, raw=line.strip(), page_start=pg,
                          block_ord_start=b.para, block_ord_end=b.para,
                          line_no=i)
            by_chapter[owner.number].append(sec)
            last_title_by_chapter[(owner.number, form, title)] = b.para
            form_stats[form]["kept"] += 1
            if joined:
                form_stats[form]["joined_from_next_line"] = \
                    form_stats[form].get("joined_from_next_line", 0) + 1

    for f in FORMS:
        form_stats[f]["candidate_blocks"] = len(cand_blocks[f])

    # ---- 组装：节的结束页/末块、父子关系 ----
    out_chapters: list[ChapterSections] = []
    last_block_of: dict[int, tuple[int, int]] = {}
    for bi, b in enumerate(blocks):
        owner = _owner_of(bi, b, spans)
        if owner is None or owner.is_front_matter:
            continue
        # 章末页取块 ref 的**上界**（结束页）：doc#91 的块 ref 是 p.19-30 这种
        # 跨页串，用起始页会让"本章最后一节"的 page_end 落回章首页。
        lo, hi = block_pages[bi]
        last_block_of[owner.number] = (b.para, hi or lo or (b.page or 0))

    for span in sorted(order_span, key=lambda c: c.number):
        secs = by_chapter.get(span.number, [])
        chap_last_ord, chap_last_page = last_block_of.get(
            span.number, (0, span.page_end or span.page_start))
        if not secs:
            # 降级：整章一节（不假装成功，`fallback=True` 会进统计）
            secs = [Section(chapter_no=span.number, chapter_name=span.name,
                            index=1, form="整章一节", level=1, number="",
                            title=span.name or span.title, raw="",
                            page_start=span.page_start or 0,
                            page_end=chap_last_page,
                            block_ord_start=0, block_ord_end=chap_last_ord,
                            fallback=True)]
        for k, s in enumerate(secs):
            s.index = k + 1
            nxt = secs[k + 1] if k + 1 < len(secs) else None
            s.page_end = max(s.page_start, (nxt.page_start if nxt
                                            else chap_last_page) or s.page_start)
            s.block_ord_end = (nxt.block_ord_start if nxt else chap_last_ord) \
                or s.block_ord_start
            # 父子关系：按层级找最近的上级
            parent = None
            for j in range(k - 1, -1, -1):
                if secs[j].level < s.level:
                    parent = secs[j].index
                    break
            s.parent_index = parent
        out_chapters.append(ChapterSections(
            number=span.number, name=span.name,
            page_start=span.page_start or (secs[0].page_start if secs else 0),
            page_end=chap_last_page or span.page_end,
            sections=secs, n_blocks=per_chapter_blocks.get(span.number, 0)))

    n_ch = len(out_chapters)
    fb = [c.number for c in out_chapters if c.fallback]
    no_jie = [c.number for c in out_chapters if not c.labeled_sections]
    pages = [p for p in (b.page for b in blocks) if p] or [0]
    uniq_pages = sorted({p for p in pages})
    stats = {
        "n_blocks": len(blocks),
        "n_chapters": n_ch,
        "n_sections": sum(len(c.sections) for c in out_chapters),
        "n_sections_level1": sum(len(c.labeled_sections) for c in out_chapters),
        "n_sections_level2": sum(1 for c in out_chapters for s in c.sections
                                 if not s.fallback and s.level == 2),
        "n_sections_level3": sum(1 for c in out_chapters for s in c.sections
                                 if not s.fallback and s.level == 3),
        # 抽出真节的章数 = 总章数 - 降级章数（每章至少有一节，降级章全是 fallback）
        "chapters_with_sections": n_ch - len(fb),
        "fallback_chapters": fb,
        "fallback_ratio": (len(fb) / n_ch) if n_ch else 0.0,
        # 「第X节」级标题的覆盖率：抽不出节级标题的章单列（它们只有更细的
        # 「一、」「（一）」小标题，或者干脆整章降级）
        "chapters_without_level1": no_jie,
        "n_chapters_without_level1": len(no_jie),
        "coverage_level1": ((n_ch - len(no_jie)) / n_ch) if n_ch else 0.0,
        "toc_blocks": toc_idx,
        "toc_blocks_skipped_with_titles": toc_blocks_skipped,
        "front_matter_blocks": front_matter_blocks,
        "no_chapter_blocks": no_chapter_blocks,
        "forms": form_stats,
        "dot_loose": {
            "candidate_blocks": loose_dot_blocks,
            "candidate_lines": loose_dot_lines,
            "note": ("宽口径：行首 `\\d{1,2}\\.\\d{1,2}` 就算（不要求编号后有空白）。"
                     "严口径（本模块采纳用的那一档）只在 forms['1.1'] 里统计；"
                     "两档的差 = 数值/单位/表格行。"),
        },
        "reject_reasons": reject_reasons,
        "page_range": [min(uniq_pages), max(uniq_pages)],
        # 页码口径必须写在数据里，别让下游以为是印刷页精确值
        "page_source": "block_ref_interpolated_monotonic",
        "page_note": ("节页码 = 块内按字符占比插值（口径同 core/textbook."
                      "_page_of_line）后夹成章内单调不减。doc#91 的块 ref 是"
                      "整章摊派（p.19-30 这种跨页串、块间还有重叠），块内只有"
                      "一处书眉且不带页码，所以页码是**估算**、可能有 1~2 页偏差；"
                      "要找原文请用 block_ord_start/block_ord_end 回查 chunks.ord。"),
    }
    if toc_idx:
        top = sorted(toc_evidence.get("pairs", {}).values(), reverse=True)[:3]
        warnings.append(f"目录页块 {len(toc_idx)} 个已整块排除"
                        f"（配对数最高 {top}，阈值 "
                        f"{toc_evidence.get('min_pairs')}）")
    else:
        warnings.append("未发现目录页块（配对数判据无一命中）")
    if fb:
        warnings.append(f"{len(fb)} 章抽不出节，已按「整章一节」降级：{fb}")
    if no_jie and not fb:
        warnings.append(f"{len(no_jie)} 章没有「第X节」级标题"
                        f"（只有更细的小标题）：{no_jie}")
    return Sections(chapters=out_chapters, toc_blocks=toc_idx,
                    toc_evidence=toc_evidence, stats=stats, warnings=warnings,
                    doc_id=doc_id, book_title=book_title, chapter_source=src)


# ---------------------------------------------------------------------------
# 取数（只读）与落盘
# ---------------------------------------------------------------------------
_RE_REF_SPAN = re.compile(r"p\.(\d+)(?:\s*-\s*(\d+))?")


def parse_ref_span(ref: str) -> tuple[int, int]:
    """`source_ref` → `(起始页, 结束页)`；"p.19-30" → (19, 30)。"""
    m = _RE_REF_SPAN.search(ref or "")
    if not m:
        return 0, 0
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    return lo, max(lo, hi)


def load_blocks(doc_id: int, db_path: Path | str | None = None) -> tuple[
        list[Block], list[tuple[int, int]], dict]:
    """从库里读一份文档的块 → `(blocks, block_pages, doc_meta)`。

    **只读**：用 `file:…?mode=ro` 打开，物理上不可能写库（本模块没有任何
    INSERT/UPDATE/DELETE，也没有建表语句）。`heading_path` 一并取出放进
    `doc_meta["headings"]`，供调用方交叉核对章级结构。
    """
    if db_path is None:
        from . import config
        db_path = config.DB_PATH
    p = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        doc = conn.execute(
            "SELECT id, title, kind, role, n_chunks, n_chars, status "
            "FROM documents WHERE id=?", (int(doc_id),)).fetchone()
        if doc is None:
            raise KeyError(f"documents 里没有 id={doc_id}")
        rows = conn.execute(
            "SELECT ord, text, source_ref, heading_path FROM chunks "
            "WHERE doc_id=? ORDER BY ord", (int(doc_id),)).fetchall()
    finally:
        conn.close()
    blocks: list[Block] = []
    spans: list[tuple[int, int]] = []
    headings: list[str] = []
    for r in rows:
        lo, hi = parse_ref_span(r["source_ref"])
        blocks.append(Block(text=r["text"] or "", page=lo, para=int(r["ord"]),
                            kind="ocr", level=0))
        spans.append((lo, hi))
        headings.append(r["heading_path"] or "")
    meta = {"id": int(doc["id"]), "title": doc["title"] or "",
            "kind": doc["kind"] or "", "role": doc["role"] or "",
            "n_chunks": int(doc["n_chunks"] or 0),
            "n_chars": int(doc["n_chars"] or 0),
            "status": doc["status"] or "", "headings": headings}
    return blocks, spans, meta


# 产物文件名后缀。⚠ **不能**用知识点/名词解释那两个后缀：
# `tools/dashboard.py:754` _LKP_SUFFIX = "-末级知识点总结.json"、
# `core/kp_build.py:3370` TERMS_SUFFIX = "-名词解释.json"，看板会按后缀去加载
# 它们。本产物是节结构，不是知识点，另起一个后缀，互不干扰。
ARTIFACT_SUFFIX = "-章节细目.json"


def artifact_path(doc_id: int, knowledge_dir: Path | str | None = None) -> Path:
    """产物的规范路径 `logs/knowledge/<doc_id>-章节细目.json`（当前学科）。"""
    if knowledge_dir is None:
        from . import config
        knowledge_dir = Path(config.LOG_DIR) / "knowledge"
    return Path(knowledge_dir) / f"{int(doc_id)}{ARTIFACT_SUFFIX}"


def dump_sections(res: Sections, path: Path | str | None = None, *,
                  knowledge_dir: Path | str | None = None) -> Path:
    """把结果写成 JSON。返回落盘路径。**只新增这一个文件，不碰别的。**"""
    p = Path(path) if path else artifact_path(res.doc_id, knowledge_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def summarize(res: Sections, *, max_chapters: int = 0) -> str:
    """人看的清单：每章几节、节标题、页范围。`max_chapters=0` 打全部。"""
    out: list[str] = []
    chs = res.chapters if max_chapters <= 0 else res.chapters[:max_chapters]
    for c in chs:
        head = (f"第{c.number}章 {c.name}  p.{c.page_start}-{c.page_end}  "
                f"{len(c.sections)} 节"
                + ("  [降级：整章一节]" if c.fallback else ""))
        out.append(head)
        for s in c.sections:
            out.append(f"    {s.index:>2}. [{s.form}] {s.title}  "
                       f"p.{s.page_start}-{s.page_end}  "
                       f"ord {s.block_ord_start}-{s.block_ord_end}")
    return "\n".join(out)
