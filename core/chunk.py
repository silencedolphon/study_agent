# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。     
# File-ID: SA-03-c3fc4015   (project study_agent; keep this line)
"""语义切块。

原则（这几条直接决定检索质量，不要随意改）：
1. 优先在语义边界切：Markdown/Word 标题、幻灯片、段落。绝不按固定字数硬切。
2. 中文按句末标点切，不用空格分词。
3. 每个块都带标题面包屑（如 "第二章 > 2.3 极限"），检索和展示都靠它定位。
4. 每个块都带出处区间（p.3-4 / slide 7），引用可回链。
5. 相邻块之间保留少量字符重叠，避免答案正好跨在切口上。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config
from .extract import Block

# 中文与英文句末标点
_SENT_END = re.compile(r"(?<=[。！？；!?;])\s*|(?<=[.!?])(?=\s)")


@dataclass
class Chunk:
    text: str
    heading_path: str
    source_ref: str
    ord: int
    meta: dict = field(default_factory=dict)


# 幻灯片类资料的块下限，远低于连续文本。
#
# 起因是一次真实的信息遗漏：一份 64 页课件里 297 个文本块有 293 个短于 80 字符
# （中位数仅 13 字符——幻灯片的行本来就这么短），按连续文本的 80 字下限过滤后
# 直接丢掉 47 个块 / 806 字符，其中包括
# "N1受体：神经节兴奋，肾上腺髓质分泌↑"、
# "α2受体：……突触前膜负反馈，血小板聚集"这类概念性结论。
# 幻灯片的 20 字就是一条完整知识，不能按正文的尺子量。
SLIDE_MIN_CHARS = 8


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_END.split(text)]
    return [p for p in parts if p]


def _hard_split_oversized(text: str, limit: int) -> list[str]:
    """单句超过上限时的兜底：先按标点断，再按长度断。中文没有空格，只能按字符。"""
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    for seg in re.split(r"(?<=[，,、：:）)】」》])", text):
        if not seg:
            continue
        if len(seg) <= limit:
            pieces.append(seg)
            continue
        for i in range(0, len(seg), limit):
            pieces.append(seg[i : i + limit])
    return pieces


def _tail_overlap(text: str, overlap: int) -> str:
    """取结尾若干字符作为下一块的前缀重叠，尽量从句子边界开始。"""
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    for i, ch in enumerate(tail):
        if ch in "。！？；!?;\n":
            return tail[i + 1 :].lstrip()
    return tail.lstrip()


def _ref_of(blocks: list[Block]) -> str:
    """把一组块压缩成一个出处区间字符串。"""
    refs = [b for b in blocks if b.ref()]
    if not refs:
        return ""
    first, last = refs[0], refs[-1]
    if first.page and last.page:
        return f"p.{first.page}" if first.page == last.page else f"p.{first.page}-{last.page}"
    if first.para and last.para:
        return f"para {first.para}" if first.para == last.para else f"para {first.para}-{last.para}"
    # 首尾出处的**类型不一致**（一头只有页号、另一头只有段号）时，
    # 不能只交回首块那一个：那等于把后半段内容的定位整段丢掉，
    # 引用会指到前一处——正是"出处标成上一页"的形态。
    # 但这里**只做加法**：末块有页号就把它一起圈进区间，没有就维持原样。
    if last.page:
        return _ref_covering(first.ref(), [last.page])
    return first.ref()


def _ref_covering(ref: str, pages: list[int] | None = None) -> str:
    """把出处 `ref` 扩成"至少覆盖 `pages` 这些页"的区间。

    为什么需要它：**一个切块的正文里可能混着别的页的文字**。
    标题块不进缓冲，要等到下一个正文块出现时才被拼进正文开头
    （见 `_Packer._pending_heads`），于是那个块的正文同时含
    "p.5 的标题"和"p.9 的正文"。出处若只按缓冲里的块算（p.9），
    按它去翻原文根本找不到那几个字——出处错了页。

    同页时原样返回，所以**不跨页的普通块、表格块与改动前完全一致**。
    """
    if not pages:
        return ref
    lo, hi = min(pages), max(pages)
    m = re.match(r"^p\.(\d+)(?:-(\d+))?$", (ref or "").strip())
    if not m:
        # 没有出处时，直接用这些页当区间；
        # 非页码式的出处（para/slide）不掺和，免得把两套坐标混成一个串。
        if not (ref or "").strip():
            return f"p.{lo}" if lo == hi else f"p.{lo}-{hi}"
        return ref
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else a
    if b < a:
        a, b = b, a
    a, b = min(a, lo), max(b, hi)
    return f"p.{a}" if a == b else f"p.{a}-{b}"


def _same_group(a: Chunk, b: Chunk) -> bool:
    """两个块是否属于同一分组。

    分组键的作用是"**边界**"：表格按行分组后，行与行之间不合并，
    于是每行自成一块。为什么需要它——一张 9 行的表若被合并，
    会变成一整块 700+ 字，想要"某一种药的某一项"就会捞回整张表。

    没有分组键（绝大多数块）时一律视为同组，与旧行为完全一致。
    """
    ga = a.meta.get("group", "") if a.meta else ""
    gb = b.meta.get("group", "") if b.meta else ""
    if not ga and not gb:
        return True
    return ga == gb


def _same_title(a: str, b: str) -> bool:
    """两个标题是否指同一件事（用于去掉大纲第 1 级与文档标题的重复）。

    只做"规范化后完全相等"的判断，不做包含/相似匹配：宁可漏去重
    （结果只是多一层同名面包屑），也不能误删真正的层级。
    """
    norm = lambda s: "".join(
        ch for ch in (s or "") if not ch.isspace() and ch not in "　·、，,。.：:-—_"
    )
    na, nb = norm(a), norm(b)
    return bool(na) and na == nb


class _Packer:
    def __init__(self, doc_meta: dict | None = None,
                 outline=None) -> None:
        self.chunks: list[Chunk] = []
        self.buf: list[Block] = []
        self.headings: list[tuple[int, str]] = []
        self.source_ref = ""
        self._last_source_ref = ""
        # 尚未附着到任何切块的标题文字（见 _flush 的说明）。
        # 存 (页号, 文字)，**页号必须跟着一起存**：
        # 只存文字的话，这些字被拼进"后面某一页"的块时，出处只能按那个块的
        # 缓冲算，标题自己那一页就丢了。实测一份课件 p.5/p.7/p.8 的标题文字
        # 全被记在 p.6/p.9 的块上，按出处翻原文找不到那几个字。
        # 页号 0 表示来源未知（没有页号的老管线）。
        self._pending_heads: list[tuple[int, str]] = []
        # 当前面包屑（self.headings）对应的页号。
        # 只用于给"面包屑叶被写进正文前缀"这件事标出处（见 _flush）：
        # 面包屑的叶标题是从某一页的标题块来的，正文里出现它，
        # 那一页就必须被出处覆盖。
        self._heading_page = 0
        # 当前页是不是"续页"（无页主题、内容属于上一页主题）。
        # 存页号而不是布尔值：切块器按块推进，留着页号便于排查。
        self._continuation_from = 0
        # 资料形态：幻灯片用更低的块下限（见 SLIDE_MIN_CHARS）。
        # 由提取层通过 doc_meta 告知，例如 pptx 会写 slides 字段。
        self.doc_meta: dict = doc_meta or {}
        # 全局结构分析的结果（见 core/outline.py）。
        #
        # 有它时，**面包屑由它决定**，不再靠"提取层逐页推断的标题"——
        # 后者是局部判断，判不出"p.26 属于「巴比妥类」这一节"，
        # 导致跨页内容各挂各的主题、检索一次找不全（用户实测反馈）。
        self.outline = outline
        # 当前块所属的"节"路径（outline 提供；老管线恒为空）
        self._topic = ""

    # ---- 标题面包屑 ----
    def _update_heading(self, level: int, text: str) -> None:
        level = max(1, level)
        self.headings = [(lv, t) for lv, t in self.headings if lv < level]
        self.headings.append((level, text))

    def _outline_stack(self, page: int) -> list[tuple[int, str]] | None:
        """算出这一页该用的 heading 栈。**只读，不改任何状态。**

        返回 None = 这一页没有大纲数据（或整页只归到课件名一级），
        调用方应当走原来的层级逻辑。

        为什么要把"算"和"用"分开：`_flush()` 必须用**上一页**的面包屑
        收尾缓冲，所以只能先 flush 再改 `self.headings`。早先把两步揉在
        一个函数里、并在 flush 之前调用，结果上一页的内容全挂上了这一页的
        主题——实测 doc#64 的 41 个正文块里 36 个整体偏移一页。
        """
        if self.outline is None or not page:
            return None
        try:
            crumb = self.outline.breadcrumb(page)
        except Exception:  # noqa: BLE001
            return None
        parts = [p.strip() for p in (crumb or "").split(">") if p.strip()]
        if not parts:
            return None
        # 拆成逐级 heading：第 1 级保留文档标题，其余按序排下去。
        # 这样 `_breadcrumb()` 拼出来就是完整路径。
        kept = [h for h in self.headings if h[0] == 1]
        rest = list(parts)
        if kept:
            # 大纲第 1 级通常就是课件名，和文档标题同级同义；同名则跳过，
            # 避免「镇静催眠抗焦虑药 > 镇静催眠抗焦虑药 > …」这种重复路径。
            if _same_title(kept[0][1], rest[0]):
                rest = rest[1:]
                if not rest:
                    return None    # 整页只归到课件名一级 → 交给续页逻辑
            stack = list(kept)
        else:
            # 没有文档标题时用大纲第 1 级顶上，其余顺延（不能再排一遍第 1 级）
            stack = [(1, rest[0])]
            rest = rest[1:]
        for i, p in enumerate(rest, start=2):
            stack.append((min(i, 6), p))
        return stack

    def _outline_topic(self, page: int) -> str:
        """这一页所属的"节"路径（见 db.chunks.topic 的注释）。"""
        try:
            return (self.outline.topic(page) if self.outline is not None else "") or ""
        except Exception:  # noqa: BLE001
            return ""

    def _switch_to_outline(self, page: int) -> bool:
        """把面包屑切到 `page` 这一页的主题。返回 False 表示这页没有大纲数据。

        ⚠ **顺序是这个函数的全部要点**：`_flush()` 的职责是用**上一页**的
        面包屑收尾缓冲里的内容，所以必须"先 flush、后改 `self.headings`"。
        反过来写就会让上一页的内容挂上这一页的主题——这是实测过的真实缺陷，
        不是理论风险。老代码的两个分支（普通标题、续页标记）本来就是
        "先 flush 再改"，这里只是把同一条约定照搬过来。
        """
        stack = self._outline_stack(page)
        if stack is None:
            return False
        self._flush()
        self.headings = stack
        # 面包屑换到这一页了：叶标题从此属于 `page`（flush 已在上一步做完，
        # 用的是旧面包屑对应的页，见 _heading_page 的说明）。
        self._heading_page = page
        self._topic = self._outline_topic(page)
        # 接了导图之后**不存在"续页"**：每一页都在导图里有自己的路径。
        # 必须清掉续页状态，否则一旦某页被老逻辑标成续页，
        # 后面每一页都会被追加 `〔续〕`
        # （实测巴比妥类整段的面包屑都变成了 `… > 巴比妥类〔续〕`）。
        self._continuation_from = 0
        return True

    def _use_outline(self, page: int) -> bool:
        """这一页有全局结构分析结果时，用它的路径当面包屑。

        返回 True 表示已接管（调用方不要再走原来的层级逻辑）。

        为什么让 outline 完全接管而不是"当父级拼上原标题"：
        原标题对**续页**是空或错的（试点里 p.27 只剩 `… > 巴比妥类`），
        而 outline 对续页会继承上一页的叶标题，信息更完整。

        ⚠️ 本方法当前无调用点（历史遗留）；实际路径是 `add()` 在块边界直接调用 `_switch_to_outline()`。
        """
        return self._switch_to_outline(page)
        return True

    def _breadcrumb(self) -> str:
        return " > ".join(t for _, t in self.headings)

    # ---- 缓冲 ----
    def _meta_of(self) -> dict:
        """当前块要带的元信息（分组键 + 节路径）。"""
        meta: dict = {}
        if self.buf and self.buf[0].group:
            meta["group"] = self.buf[0].group
        if self._topic:
            meta["topic"] = self._topic
        return meta

    def _buf_text(self) -> str:
        return "\n".join(b.text for b in self.buf).strip()

    def _flush(self, suffix: str = "") -> None:
        text = self._buf_text()
        if not text:
            self.buf = []
            return
        ref = _ref_of(self.buf) or self.source_ref
        head = self._breadcrumb()
        # 续页标记：本页没有自己的主题，内容属于上一页的主题。
        #
        # 为什么要写进面包屑而不是只存 meta：检索层要能**直接看出来**，
        # 才好在命中相邻页时判断"它们是不是同一主题"。
        # 存 meta 的话要加表列、要在检索时 join，代价大得多。
        # 对读者也是有用信息：一眼看出这页是上一页的继续。
        cont = self.buf and self._continuation_from
        if cont and head:
            head = f"{head}〔续〕"
        # 把所属标题写进切块正文开头。
        #
        # 为什么要这么做：标题块本身不进 buf，只进面包屑，
        # 于是**标题文字不会出现在任何切块的正文里**。
        # 对正文型的书这不是问题（标题只是导航），
        # 但对幻灯片型 PDF 是**实质缺漏**——那些资料里标题就是内容本身。
        # 实测 23 份课件的封面与学习目标页整页缺失。
        leaf = head.split(" > ")[-1].strip() if head else ""
        prefix = ""
        # 前缀文字来自哪几页。标题文字**不一定属于当前缓冲那一页**：
        # 它可能来自更早的页（见 _pending_heads），被推迟到这里才写进正文。
        # 出处必须把这些页一起圈进去，否则引用的页码上没有这段字。
        prefix_pages: list[int] = []
        if len(leaf) >= 4 and leaf not in text:
            prefix = leaf + "\n"
            if self._heading_page:
                prefix_pages.append(self._heading_page)
        # 尚未附着到任何块的标题文字。列表而非单值：连续多个孤立标题
        # （大纲行就是这种形态）时，不能只保留最后一个。
        # 不做这一步，这些文字只会出现在面包屑里，正文中完全找不到——
        # 实测 23 份课件因此缺了封面、学习目标、章节大纲。
        if self._pending_heads:
            # 文档级标题（面包屑首段）**不在这里消费**：它的文字已经完整
            # 出现在 heading_path 首段里，正文再写一遍是冗余，而且会把它的
            # 页号（通常是封面 p.1）拖进本块的出处区间——"p.26" 因此变成
            # "p.1-26"，从"这一页"撑成"第 1 到 26 页"，等于没标出处。
            # 留给 finish() 兜底单独成块，出处正好是它自己的页。
            _root = self.headings[0][1] if self.headings else ""
            used = [(p, h) for p, h in self._pending_heads
                    if h and h not in text and h not in prefix and h != _root]
            if used:
                prefix = "\n".join(h for _, h in used) + "\n" + prefix
                prefix_pages += [p for p, _ in used if p]
            self._pending_heads = [x for x in self._pending_heads
                                   if x[1] == _root]
        if prefix_pages:
            # 同页时 _ref_covering 原样返回，不跨页的块行为不变
            ref = _ref_covering(ref, prefix_pages)
        self.chunks.append(
            Chunk(text=prefix + text + suffix, heading_path=head,
                  source_ref=ref, ord=len(self.chunks),
                  # group 是"分组键"：不同组的块不合并（见 Block.group）。
                  # 取缓冲里第一个块的组——一组内部本来就同组。
                  meta=self._meta_of())
        )
        self._last_source_ref = ref
        self.buf = []

    def _emit_oversized(self, block: Block) -> None:
        """单块超过 hard limit：按句切，尽量填满但不超过 max。"""
        sents = _split_sentences(block.text)
        cur: list[str] = []
        cur_len = 0
        for s in sents:
            for piece in _hard_split_oversized(s, config.CHUNK_MAX_CHARS):
                if cur_len + len(piece) > config.CHUNK_MAX_CHARS and cur:
                    self.chunks.append(
                        Chunk(text="".join(cur), heading_path=self._breadcrumb(),
                              source_ref=block.ref() or self.source_ref,
                              ord=len(self.chunks), meta=self._meta_of())
                    )
                    cur, cur_len = [], 0
                cur.append(piece)
                cur_len += len(piece)
        if cur:
            self.buf.append(Block(text="".join(cur), page=block.page,
                                  para=block.para, kind=block.kind))
            self._flush()

    def add(self, block: Block) -> None:
        if block.kind == "heading":
            # ---- 有全局结构分析时：面包屑由它接管 ----
            #
            # 每个"页起始"标记（标题块，含空标题的续页标记）都触发一次；
            # `_switch_to_outline` 会按页号查出该页的完整路径并设成面包屑，
            # **并且它内部是"先 flush 再改面包屑"**——顺序反了就会让上一页的
            # 内容挂上这一页的主题（见该函数的注释）。
            # 同一页的后续块不改面包屑，所以一页仍只成一块。
            #
            # 空标题在**没有** outline 时才走下面的"续页继承"分支。
            if (self.outline is not None and block.page
                    and self._switch_to_outline(block.page)):
                if block.ref():
                    self.source_ref = block.ref()
                # 标题文字仍要进正文：outline 的叶标题可能与原文标题不同，
                # 原文标题是"这一页上写了什么"，不能因为改了面包屑就丢掉。
                leaf = block.text.strip()
                if len(leaf) >= 4 and all(leaf != h for _, h in self._pending_heads):
                    self._pending_heads.append((block.page, leaf))
                return

            # **空标题 = 续页标记**：新的一页开始了，但本页没有自己的主题，
            # 内容属于上一页的主题（见 extract._is_slide_continuation）。
            #
            # 为什么不能"什么都不做"：那样 p.27/28 的正文会和 p.26 挤进
            # 同一个切块，跨页边界就丢了，出处区间也变成含糊的一大段。
            # 所以要收尾上一块（保住每页的出处），但**不动面包屑**——
            # 这正是"继承主题"的意思。
            if not block.text.strip():
                self._flush()
                if block.ref():
                    self.source_ref = block.ref()
                # 记下"从这页起是续页"。检索层据此判断哪些相邻块
                # 属于同一主题（见 search 的区段扩展）。
                self._continuation_from = block.page
                return
            self._flush()
            lvl = block.level or 1
            self._update_heading(lvl, block.text)
            self._continuation_from = 0
            # 面包屑的叶换成本页的标题了（只在有页号时更新：pptx 的文档级
            # 标题块 page=0，不能把已知的页号清掉）。
            if block.page:
                self._heading_page = block.page
            # 这一页没有 outline 数据（走了老逻辑），节路径不能沿用上一页的
            self._topic = ""
            # 标题文字必须留在正文里。它不进 buf，所以记下来由 _flush 附加到
            # 下一个块；若此后一直没有内容，finish() 会让它单独成块。
            # 不这样做的话，"第三章 药物效应动力学"、"I. 高血压及血压调节机理"
            # 这类文字只能在面包屑里找到，正文中完全缺失。
            leaf = block.text.strip()
            if len(leaf) >= 4 and all(leaf != h for _, h in self._pending_heads):
                self._pending_heads.append((block.page, leaf))
            if block.ref():
                self.source_ref = block.ref()
            return

        # ---- 正文块 ----
        #
        # 页号变了也要切主题：extract 只把**页面上有标题行**的页标成 heading，
        # 纯图页、表格页、只有几行字的页没有标题块，只靠 heading 触发的话
        # 它们会一直挂着上一页的主题（outline 其实给每一页都判了主题）。
        # 判据用"目标面包屑与当前面包屑是否相同"，不再另存一个当前页号——
        # 面包屑本身就编码了"现在挂在哪一页的主题下"。
        if self.outline is not None and block.page:
            stack = self._outline_stack(block.page)
            if stack is not None and \
                    " > ".join(t for _, t in stack) != self._breadcrumb():
                self._switch_to_outline(block.page)

        if len(block.text) > config.CHUNK_MAX_CHARS:
            self._flush()
            self._emit_oversized(block)
            return

        # 标题变化时先收尾上一块
        new_bc = self._breadcrumb()
        if self.buf and self.chunks and self.chunks[-1].heading_path != new_bc:
            self._flush()

        # 分组变化时也要收尾：不同组的块不能进同一个缓冲。
        # 没有这一步，表格的首行会和表格前的正文挤在一块，
        # 而后续各行又被 group 挡住——结果是"首行并进正文、其余各自成块"，
        # 表格的第一行就丢了独立性（实测就是这样）。
        if self.buf and block.group and self.buf[-1].group != block.group:
            self._flush()

        cur_len = len(self._buf_text())
        if self.buf and cur_len + len(block.text) > config.CHUNK_MAX_CHARS:
            full = self._buf_text()
            self._flush()
            tail = _tail_overlap(full, config.CHUNK_OVERLAP_CHARS)
            if tail:
                self.buf.append(Block(text=tail, page=block.page, para=0))
        elif self.buf and cur_len >= config.CHUNK_TARGET_CHARS:
            full = self._buf_text()
            self._flush()
            tail = _tail_overlap(full, config.CHUNK_OVERLAP_CHARS)
            if tail:
                self.buf.append(Block(text=tail, page=block.page, para=0))

        self.buf.append(block)
        if block.ref():
            self.source_ref = block.ref()

    def finish(self) -> list[Chunk]:
        self._flush()
        # 末尾若仍有未附着的文档级标题（例如整份资料只有标题、没有正文，
        # 或标题后面再没有内容），单独成块，保证它不丢。
        if self._pending_heads:
            heads = [h for _, h in self._pending_heads]
            # 只剩余**文档级标题**时直接丢弃，不为它单独造块。
            #
            # 为什么可以丢：它的文字已经完整出现在所有块的 `heading_path`
            # 首段里，而 `chunks_fts` 是 `fts5(text, heading_path, …)`
            # ——这一列本来就在索引里，检索不会因此漏字。
            # 为什么必须丢：单独成块时它带的是**末尾那一页**的面包屑
            # （`self._breadcrumb()`），与该页的块同名，于是被
            # `_merge_fragments` 第一轮"同标题路径合并"吞掉，
            # `_merge_ref("p.27", "p.1")` 把出处撑成 "p.1-27"——
            # "这一页"变成"第 1 到 27 页"，等于没标。
            # 全文只有标题、没有正文时才保留它（否则整份资料会消失）。
            _root = self.headings[0][1] if self.headings else ""
            if heads == [_root] and self.chunks:
                self._pending_heads = []
            else:
                head_pages = [p for p, _ in self._pending_heads if p]
                # 出处按**这些标题自己的页**算。老代码直接沿用 self.source_ref
                # （= 上一个块的页），末尾那几页的标题因此被标成上一页的出处——
                # 正是"出处标成上一页"的形态。只有页号缺失时才退回老写法。
                ref = _ref_covering("", head_pages)
                if not ref or len(head_pages) < len(self._pending_heads):
                    ref = _ref_covering(self.source_ref, head_pages) or self.source_ref
                self.chunks.append(
                    Chunk(text="\n".join(heads),
                          heading_path=self._breadcrumb(),
                          source_ref=ref,
                          ord=len(self.chunks), meta=self._meta_of())
                )
                self._pending_heads = []
        # 幻灯片类资料用远低的块下限（见 SLIDE_MIN_CHARS 的说明）。
        # 判断依据是提取层给出的 doc_meta.slides。
        from_slides = bool(self.doc_meta.get("slides"))
        min_chars = SLIDE_MIN_CHARS if from_slides else config.CHUNK_MIN_CHARS

        kept = self._merge_fragments(self.chunks, min_chars=min_chars)

        # 兜底：**任何有实质内容的块都不能被静默丢弃**。
        # 幻灯片类资料整体可能都很短，此时宁可全部保留也不清空
        # （否则整份资料会从检索里消失）。
        nonempty = [c for c in kept if c.text.strip()]
        if not nonempty:
            nonempty = kept
        for i, c in enumerate(nonempty):
            c.ord = i
        return nonempty

    @staticmethod
    def _merge_fragments(chunks: list[Chunk], min_chars: int | None = None
                         ) -> list[Chunk]:
        """合并相邻小块，避免检索碎片化，同时保证不丢内容。

        四轮合并：

        第一轮合并**标题路径相同**的相邻小块。子标题密集的文档（教材、
        大纲式笔记）里每个小节只有一两句话，会被切成上百字的碎块，
        检索时谁也拼不出完整答案。标题不同的不跨并——跨标题合并会让
        引用指错地方。

        第二轮处理第一轮救不回来的：**任何短于下限的块都不允许原样留下**，
        先把短块并进**前一个块（允许跨标题）**，并不了才自成一块。
        这一轮是必要的，因为幻灯片每页标题都不同，第一轮的"同标题"
        条件永远不成立——实测一份 64 页课件因此丢掉了 47 个块 / 806 字符，
        包括"N1受体：神经节兴奋，肾上腺髓质分泌↑"这类完整结论。

        第三轮：仍过短的块**向前并进下一个块**。这一轮不能省，因为第二轮
        只能并进前驱，若短块正好排在开头（幻灯片的表格页常常首块就是碎片），
        它没有前驱可并，就会被丢。

        第四轮（收尾）：末尾若仍留有短块，并进前一块。
        """
        if min_chars is None:
            min_chars = config.CHUNK_MIN_CHARS

        merged: list[Chunk] = []
        for c in chunks:
            if (merged
                    and merged[-1].heading_path == c.heading_path
                    and _same_group(merged[-1], c)
                    and len(merged[-1].text) < config.CHUNK_TARGET_CHARS
                    and len(merged[-1].text) + len(c.text) + 1 <= config.CHUNK_MAX_CHARS):
                prev = merged[-1]
                prev.text = prev.text + "\n" + c.text
                # 出处区间取并集，保持可回链
                if c.source_ref and c.source_ref != prev.source_ref:
                    prev.source_ref = _merge_ref(prev.source_ref, c.source_ref)
            else:
                merged.append(Chunk(text=c.text, heading_path=c.heading_path,
                                    source_ref=c.source_ref, ord=len(merged),
                                    meta=dict(c.meta)))

        # 第二轮：吞掉过短的邻居，避免内容被下限过滤掉
        out: list[Chunk] = []
        for c in merged:
            if out and len(c.text) < min_chars \
                    and _same_group(out[-1], c) \
                    and len(out[-1].text) + len(c.text) + 1 <= config.CHUNK_MAX_CHARS:
                prev = out[-1]
                prev.text = prev.text + "\n" + c.text
                if c.source_ref and c.source_ref != prev.source_ref:
                    prev.source_ref = _merge_ref(prev.source_ref, c.source_ref)
            else:
                out.append(c)

        # 第三轮：仍过短的，向前并进**下一个**块。
        # 这一轮不能省：上一轮只能把短块并进"前一个"块，
        # 若短块正好排在开头（幻灯片的表格页常常首块就是碎片），
        # 它没有前驱可并，就会被丢。实测一份课件因此仍缺 78 行，
        # 都是表格里的零散单元格（"窦房结"、"β1,β2"、"心率↑"）。
        final: list[Chunk] = []
        for c in out:
            if (final and len(final[-1].text) < min_chars
                    and _same_group(final[-1], c)
                    and len(final[-1].text) + len(c.text) + 1 <= config.CHUNK_MAX_CHARS):
                prev = final[-1]
                prev.text = prev.text + "\n" + c.text
                if c.source_ref and c.source_ref != prev.source_ref:
                    prev.source_ref = _merge_ref(prev.source_ref, c.source_ref)
            else:
                final.append(c)

        # 第四轮（收尾）：末尾若仍留有短块，并进前一块
        tail_fixed: list[Chunk] = []
        for c in final:
            if (tail_fixed and len(c.text) < min_chars
                    and _same_group(tail_fixed[-1], c)
                    and len(tail_fixed[-1].text) + len(c.text) + 1 <= config.CHUNK_MAX_CHARS):
                prev = tail_fixed[-1]
                prev.text = prev.text + "\n" + c.text
                if c.source_ref and c.source_ref != prev.source_ref:
                    prev.source_ref = _merge_ref(prev.source_ref, c.source_ref)
            else:
                tail_fixed.append(c)
        return tail_fixed


def _merge_ref(a: str, b: str) -> str:
    """把两个出处串并成区间形式，如 p.3 + p.5 -> p.3-5。

    合并小块时用它保住可回链：两块各自只覆盖一部分页，
    合出来的块必须覆盖两边的并集，否则引用会指到没有那段字的页上。
    """
    if not a:
        return b
    if not b or a == b:
        return a
    for prefix in ("p.", "slide ", "para "):
        if a.startswith(prefix) and b.startswith(prefix):
            ta, tb = a[len(prefix):], b[len(prefix):]
            start = ta.split("-")[0]
            end = tb.split("-")[-1]
            # **区间取并集**：合并后的块同时含 a、b 两边的文字，出处就必须
            # 覆盖两边，否则引用会指到没有那段字的页上。
            #
            # 起点、终点都要按"两端"取，而不是"a 的起点 + b 的终点"：
            #  · `a` 可能已经是区间（前几轮合并留下的），终点只取 b 的就会把
            #    a 已圈住的尾页丢掉（p.9-11 + p.10 → p.9-10，p.11 没人覆盖）；
            #  · `b` 可能整体早于 `a`（短块"向前并进下一个块"，见
            #    `_merge_fragments` 第三轮），起点只取 a 的会让区间**倒序**
            #    ——实测库中真实存在 `p.49-4` / `p.81-1` / `p.93-1` 这类出处
            #    （如 chunk 18484），按它翻原文根本找不到那段字，正是"出处错页"。
            a_end = ta.split("-")[-1]      # a 是单页时，它的终点就是它自己
            b_start = tb.split("-")[0]     # b 是单页时，它的起点就是它自己
            try:
                if float(a_end) > float(end):
                    end = a_end
            except ValueError:
                pass
            try:
                if float(b_start) < float(start):
                    start = b_start
            except ValueError:
                pass
            try:
                if float(start) == float(end):
                    return f"{prefix}{start}"
            except ValueError:
                pass
            return f"{prefix}{start}-{end}"
    return f"{a}, {b}"


def chunk_blocks(blocks: list[Block], doc_meta: dict | None = None,
                 outline=None) -> list[Chunk]:
    """把提取出的 blocks 切成检索用的块。

    doc_meta 传入提取层的元数据（如幻灯片张数），用于判断资料形态——
    幻灯片要用远低的块下限，否则短行会被整批丢弃（见 SLIDE_MIN_CHARS）。

    outline 传入全局结构分析的结果（`core/outline.py`）。
    有它时**面包屑由它决定**：它看完整份课件才判断"哪几页属于同一主题"，
    而提取层只能逐页局部推断，判不出跨页归属（用户实测反馈的根因）。
    为 None 时退回原有的逐页推断，行为与改动前一致。
    """
    packer = _Packer(doc_meta or {}, outline=outline)
    for b in blocks:
        packer.add(b)
    return packer.finish()