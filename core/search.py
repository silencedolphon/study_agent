# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
# File-ID: SA-07-rebuilt-search   (project study_agent; keep this line)
"""混合检索：向量路 + 关键词路（FTS5 trigram / LIKE 降级），RRF 融合。

⚠ **本文件是重建版**（2026-10-14 硬盘故障后）。原文件 1355 行，会话记录只留下 471 行
（分布在 3 段里）；缺失的**头部**、`_keyword_search` 的 **LIKE 主体**、`search()` 的
**融合后半/元数据取回/配额截断**由大管家按恢复出的注释、调用点与实测描述重写。
**凡重写处都带 `[重建]` 标记**；`StudyDashboard-v1.1` 的 `.pyc` 已可对照校正
（`_work/strings/search.funcs.txt` 与 `search.strings.txt`），本文件是当时的可用兜底。

设计要点（原注释保留下来，都是踩过的坑）：
- 向量路给语义召回，关键词路保精确术语；两路 RRF 融合，避免纯向量的不可解释性。
- 库隔离（corpus）必须**下推到三条路的 SQL**，不能事后过滤 —— 见 `_corpus_filter`。
- 标题加权只认**叶子标题**，不认整条面包屑（实测正文前3率 57.1% → 85.7%）。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

import numpy as np

from . import config, db
from .embed import from_blob, get_embedder

# ---------------- 常量 ----------------
# RRF 融合常数：名次第 i 位贡献 1/(K+i+1)。60 是 RRF 的常用取值。
RRF_K = 60
# 关键词占优阈值：关键词路最高分达到它就认为"查询是精确术语"。
# [重建] 原值未留痕；0.9 与注释里"接近满分"的说法一致。
KW_DOMINANCE = 0.9
# 标题叶子命中的最短公共片段与加权系数。
# [重建] 由原注释反推：boost 上限 +0.024 = HEADING_BOOST * 6 ⇒ 0.004。
HEADING_MIN_MATCH = 2
HEADING_BOOST = 0.004
# 表格块加权：概览行（`… 行 × …`）权重高于普通表格行。
# [重建] 具体数值未留痕，量级参照 HEADING_BOOST（最大 +0.024）。
OVERVIEW_MARK = "概览"
OVERVIEW_BOOST = 0.008
ROW_BOOST = 0.003

# LIKE 路的窗口上限（与 `_like_windows` 默认 max_windows 同源，显式便于调参）
LIKE_MAX_WINDOWS = 24

# ⚠ 转义相关**不能写进 f-string 的表达式里**：`f"... ESCAPE '\\' ..."`
# 在 Python ≤3.11 是 SyntaxError（3.12 才放宽）。本项目要求 3.9+ ⇒ 提到模块级普通字符串。
_ESCAPE_CHAR = "\\"
ESCAPE_CLAUSE = " ESCAPE '" + _ESCAPE_CHAR + "'"
LIKE_WILDCARDS = ("\\", "%", "_")


def _like_escape(term: str) -> str:
    """把查询词里的 LIKE 通配符转义掉（`%` `_` 是通配符，不能原样进模式串）。"""
    out = term
    for ch in LIKE_WILDCARDS:
        out = out.replace(ch, _ESCAPE_CHAR + ch)
    return out


# ---------------- 引用串 ----------------
def build_cite(title: str, heading_path: str, source_ref: str) -> str:
    """人类可读的引用串，形如 `教材.pdf › 第二章 > 2.3 极限 › p.12`。

    [重建] 函数头与文档串丢失：恢复版第 1 行就是本函数的**函数体**
    （`parts = parts[1:]`），说明原实现先按 `>` 切面包屑再决定是否去掉首层。
    这里按同义重写：面包屑首层与文档标题重复时去掉，避免 `A › A > B` 的冗余。
    """
    parts = [p.strip() for p in (heading_path or "").split(">") if p.strip()]
    title = (title or "").strip()
    if parts and title and (parts[0] == title or parts[0].startswith(title)):
        parts = parts[1:]
    head = " > ".join(parts)
    out = [p for p in (title, head, source_ref or "") if p]
    return " › ".join(out)


@dataclass
class Hit:
    chunk_id: int
    doc_id: int
    doc_path: str
    doc_title: str
    ord: int
    text: str
    heading_path: str
    source_ref: str
    vec_score: float = 0.0
    kw_score: float = 0.0
    rrf: float = 0.0
    why: list[str] = field(default_factory=list)
    confidence: str = "ok"     # ok | low，见 _flag_confidence
    # 这条是"同主题续页自动补全"带进来的，不是检索命中的。
    # 单独标出来有两个用处：① 证据配额不该因为它挤掉别的资料；
    # ② 前端/模型能看出这条是补充而非命中（见 expand_continuations）。
    is_extra: bool = False
    # 所属"节"路径（AI 全局分析入库时写入，见 db.chunks.topic）。
    # 老管线的块为空 → 跨页分组退回"面包屑去掉〔续〕"的老判据。
    topic: str = ""

    def cite(self) -> str:
        """人类可读的引用串，形如 教材.pdf › 第二章 > 2.3 极限 › p.12"""
        return build_cite(self.doc_title or self.doc_path,
                          self.heading_path, self.source_ref)

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "path": self.doc_path,
            "title": self.doc_title,
            "ord": self.ord,
            "ref": self.source_ref,
            "heading": self.heading_path,
            "cite": self.cite(),
            "vec_score": round(self.vec_score, 4),
            "kw_score": round(self.kw_score, 4),
            "rrf": round(self.rrf, 5),
            "why": self.why,
            "confidence": self.confidence,
            "is_extra": self.is_extra,
            "text": self.text,
        }


# ---------------- 关键词路 ----------------
# 词元里允许连字符与内部数字：`5-HT`、`IL-6`、`HbA1c` 这类术语必须保持完整。
# 早期正则 `[A-Za-z0-9_]+` 会把 `5-HT的胞体位于何处` 切成 `5`、`HT`、
# `的胞体位于何处`——前两个都短于 MIN_TERM_LEN 被丢弃，
# 只剩一个语料里不存在的长短语，于是这条查询在关键词路上**完全失效**
# （实测返回 0 条，而正确答案就在库里）。
_FTS_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]*|[\u4e00-\u9fff]")
# FTS 表用 trigram 分词器，查询词至少 3 字符才能命中；更短的走 LIKE 降级
MIN_TERM_LEN = 3
# LIKE 是子串匹配，不依赖 trigram 索引，短词也能用（太短噪声过大）
MIN_LIKE_LEN = 2


def _phrases(text: str) -> list[str]:
    """把查询切成候选短语：连续的汉字串、以及英文/数字词。

    连字符内部的 ASCII 串不再拆开（`5-HT` 保持完整）。
    """
    tokens = _FTS_TOKEN.findall(text)
    out: list[str] = []
    buf = ""
    for t in tokens:
        if len(t) == 1 and "\u4e00" <= t <= "\u9fff":
            buf += t
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.append(t)
    if buf:
        out.append(buf)
    return out


def _like_windows(phrase: str, min_len: int = MIN_LIKE_LEN,
                  max_len: int = 6, max_windows: int = 24) -> list[str]:
    """把一段短语切成短窗口，供 LIKE 兜底使用。

    为什么需要：用户查询里常带"的胞体位于何处"这种**整段自然语言短语**，
    语料里当然没有这个串，整段匹配必然失败；
    切成短窗口后才能让"胞体""位于"这类实词命中。

    中文没有词边界，所以**不能用固定步长切**——实测 `range(0, n, 2)`
    切出的是 "的胞体位""体位于何" 这种跨越词界的无意义片段，
    一个都命中不了。改为生成**所有** 2~4 字的连续窗口：
    有意义的词必然落在其中，代价是窗口数变多，
    由 IDF 加权与覆盖率门槛压住噪声。

    非汉字短语（如 `5-HT`）本身就有区分度，原样使用。
    """
    if not phrase or len(phrase) < min_len:
        return []
    if not all("\u4e00" <= ch <= "\u9fff" for ch in phrase):
        return [phrase] if len(phrase) >= min_len else []
    if len(phrase) <= max_len:
        return [phrase]
    out: list[str] = []
    seen: set[str] = set()
    # 优先长窗口：长窗口区分度高，先占配额
    for size in (4, 3, 2):
        for i in range(len(phrase) - size + 1):
            w = phrase[i:i + size]
            if w not in seen:
                seen.add(w)
                out.append(w)
            if len(out) >= max_windows:
                return out
    return out


def _fts_query(text: str) -> str:
    """构造 FTS5 MATCH 查询串。

    用 OR 连接候选短语以提高召回，再由 RRF 融合与配额控制精度。
    注意过滤掉短于 3 字符的短语：trigram 索引无法匹配它们，
    留着只会让整条 MATCH 更嘈杂而无收益。
    """
    seen: list[str] = []
    for p in _phrases(text):
        p = p.strip()
        if len(p) < MIN_TERM_LEN:
            continue
        if p.lower() not in {s.lower() for s in seen}:
            seen.append(p)
    return " OR ".join(f'"{p}"' for p in seen[:24])


# ---------------- 库隔离（corpus） ----------------
# 需求 2B 的红线：**试题与资料笔记（aux）不得进入 AI 问答 / MCP / 看板搜索**。
#
# 为什么把过滤下推到"路"这一层，而不是复用现成的 `doc_ids`：
#   `doc_ids` 是**候选截断之后**才生效的筛选（见 search() 里
#   `ordered[:max(top_k*3, 20)]` 之后那一步）。用它做库隔离就成了后过滤——
#   aux 的块先按分数挤进候选、把 main 的块挤出截断线，再被丢掉，埋下
#   "aux 越大、main 越差"的慢性病（方案 §1.5；chat.py 的既有注释也已承认
#   该缺陷）。所以库隔离必须在**取候选时**就生效：向量路、FTS 路、LIKE 路
#   各自的 SQL 里都要带这个条件，一处都不能漏。
#
# `corpus` 锚在 `documents` 级：一份文档不可能同属两库，因此
# `expand_neighbors` / `expand_continuations`（按 `c.doc_id=?` 取块）
# 与识图块都**自动跟随**命中所属的库，不需要额外参数（方案 §3.3）。
def _corpus_filter(corpus: "str | list[str] | None",
                   alias: str = "d") -> tuple[str, list[str]]:
    """把 corpus 参数编译成 (SQL 条件片段, 参数表)。

    - `"main"` / `"aux"`：只要该库；
    - `["main","aux"]` / `None` / `"all"`：不限库（与分库前的行为一致）；
    - 其它字符串按原样比较——写错库名只会 0 命中，不会意外放开。

    `COALESCE(...,'main')` 是对旧库 NULL 值的兜底（DEFAULT 'main' 之外的第二道）。
    """
    if corpus is None:
        return "", []
    if isinstance(corpus, str):
        if corpus.strip().lower() in ("all", "*"):
            return "", []
        want = [corpus]
    else:
        want = [str(x) for x in corpus]
    if not want:
        return "0", []          # 空集合：什么都不看，但绝不放开过滤
    ph = ",".join("?" * len(want))
    return "COALESCE(" + alias + ".corpus,'main') IN (" + ph + ")", want


def _normalize(scores: dict[int, float]) -> dict[int, float]:
    """把一路的原始分线性拉到 0~1。单命中给 1.0（无跨度可分）。

    注意方向：本函数按 **原始分越小越好** 归一（bm25 的语义）。
    关键词 LIKE 路的覆盖率是 **越大越好**，必须用 `_normalize_high`。
    """
    if not scores:
        return {}
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    return {k: 1.0 - (v - lo) / span for k, v in scores.items()}


def _normalize_high(scores: dict[int, float]) -> dict[int, float]:
    """把"越大越好"的原始分线性拉到 0~1。单命中/全相等时给 1.0。

    [M0-7 R1] 关键词 LIKE 路的分数是"命中窗口数"（见 `_keyword_search`），
    覆盖率越高越可信 ⇒ 必须正向归一。重建版误用了 `_normalize`（bm25 方向），
    结果**命中窗口最多的块反而得 0 分被丢弃、只命中一个词碎片的块得 1.0**，
    这是此前"关键词路把 generic 词块顶到 Top-1"的主因之一。
    """
    if not scores:
        return {}
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    span = hi - lo
    if span <= 0:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / span for k, v in scores.items()}


def _keyword_search(conn: sqlite3.Connection, query: str, limit: int,
                    used_fts: bool,
                    corpus: "str | list[str] | None" = "main"
                    ) -> dict[int, float]:
    """返回 {chunk_id: 归一化分数}。

    FTS 与 LIKE 两条路都跑：FTS/trigram 善长 3 字符以上的子串，
    短词（CNN、AI、熵）只有 LIKE 能兜住，两者合并取较高分。

    `corpus` 与 `search()` 同义（默认 "main"），两条路与 IDF 统计
    各自带库过滤——**FTS 与 LIKE 都必须过滤**，漏掉 LIKE 路
    （它不是 FTS，最容易被忽略）会让关键词检索漏出 aux。
    """
    scores: dict[int, float] = {}
    cwhere, cparams = _corpus_filter(corpus)
    cfilter = " AND " + cwhere if cwhere else ""

    if used_fts:
        q = _fts_query(query)
        if q:
            sql = ("SELECT chunks_fts.rowid AS cid, bm25(chunks_fts) AS s"
                   " FROM chunks_fts"
                   " JOIN chunks c ON c.id = chunks_fts.rowid"
                   " JOIN documents d ON d.id = c.doc_id"
                   " WHERE chunks_fts MATCH ?" + cfilter
                   + " ORDER BY s LIMIT ?")
            try:
                rows = conn.execute(sql, (q, *cparams, limit)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                # bm25 越小越相关，转成越大越好的分数
                vals = [float(r["s"]) for r in rows]
                lo, hi = min(vals), max(vals)
                span = (hi - lo) or 1.0
                for r in rows:
                    scores[int(r["cid"])] = 1.0 - (float(r["s"]) - lo) / span

    # LIKE 兜底：短词、分词器不支持、或 FTS 无命中时都靠它。
    # 走 text_norm（已去空白），这样 PDF 硬折行造成的 "n0 =n2" 也能被
    # 查询 "n0 = n2" 命中。
    #
    # 关键补充：把长汉字短语再切成短窗口。
    # 用户会输入"5-HT的胞体位于何处"这种整段自然语言，
    # 语料里不可能有这个串，整段匹配必然 0 命中；
    # 切成"胞体""位于"这类短窗口后才能捞到正确答案。
    raw_terms: list[str] = []
    for ph in _phrases(query):
        raw_terms.extend(_like_windows(ph) or [ph])
    terms = [t for t in raw_terms if len(t) >= MIN_LIKE_LEN]
    # **只保留极大窗口**：被更长窗口包含的那些是白来的碎片，要去掉。
    #
    # 起因是一次端到端实测：问"达格列净的剂量范围是多少"，
    # 而答案就在库里（`剂量范围 → 达格列净片: 5~10mg/d,gd`），
    # 关键词路却给它 **0 分**。查下来是选择性判据被一个垃圾窗口卡住：
    #
    #   wmax = 8.785 来自 `列净的`——这是 `格列净` + `的` 的跨词碎片，
    #   因为"格列净"出现在很多药名里、后面常跟"的"，它在语料里**出现得很少**，
    #   于是 IDF 权重特别高。选择性门槛被它抬到 8.785 × 0.75 = 6.589，
    #   而真正有区分度的 `剂量范围` 只有 5.792，**过不了门槛被剔除**。
    #
    # 判据：`达格列`/`格列净` 是 `达格列净` 的子串、`剂量范`/`量范围`
    # 是 `剂量范围` 的子串——它们不带来任何新信息，只会污染权重。
    # 去掉之后 wmax 从 8.785 降到 6.475，门槛跟着降到 4.86，正确的词就过了。
    #
    # 注意只去掉**真子串**（严格更短），长度相同的窗口之间不互相吞。
    def _is_sub(t: str, others: list[str]) -> bool:
        return any(t != o and len(t) < len(o) and t in o for o in others)

    maximal = [t for t in terms if not _is_sub(t, terms)]
    terms = maximal or terms

    # ---------------- [重建] LIKE 主体 ----------------
    # 原文在此处被截断（恢复版第 255 行 `terms = maximal or terms` 之后直接跳到
    # `def search(`）。依原注释重写：**必须带库过滤**；走 `text_norm`（缺列时退回
    # `text`）；通配符转义；用"命中的极大窗口数"作为分数，再归一化。
    #
    # 与 FTS 的关系：同一路的两条子路径**取较高分**，不叠加（叠加会让
    # 既进 FTS 又进 LIKE 的块分数虚高，压掉真正的精确命中）。
    #
    # [M0-7 R3] LIKE 兜底同样要搜 `heading_path`：FTS 的索引列就是
    # (text, heading_path)，而重建版的 LIKE 只搜正文 —— 于是"标题里有、正文里
    # 没写"的页面在关键词路**完全不存在**（实测 1393「BDZ增强GABA抑制」的
    # 祖先标题写着「作用机制」，正文却没有"作用机制"四个字，关键词分直接为 0）。
    # 命中标题按 1 个窗口计分（与正文命中同权，标题本就是块的一部分）。
    if terms:
        covers: dict[int, int] = {}
        for t in terms[:LIKE_MAX_WINDOWS]:
            pat = "%" + _like_escape(t) + "%"
            rows = []
            for col in ("text_norm", "text"):
                sql = ("SELECT c.id AS cid FROM chunks c"
                       " JOIN documents d ON d.id = c.doc_id"
                       " WHERE c." + col + " LIKE ?" + ESCAPE_CLAUSE + cfilter
                       + " LIMIT ?")
                try:
                    rows = conn.execute(sql, (pat, *cparams, limit * 4)).fetchall()
                    break
                except sqlite3.OperationalError:
                    rows = []          # 该列不存在（老库没有 text_norm）
            for r in rows:
                cid = int(r["cid"])
                covers[cid] = covers.get(cid, 0) + 1
            # [M0-7 R3] 标题路（FTS 有、LIKE 原来漏掉的那一半）
            hsql = ("SELECT c.id AS cid FROM chunks c"
                    " JOIN documents d ON d.id = c.doc_id"
                    " WHERE c.heading_path LIKE ?" + ESCAPE_CLAUSE + cfilter
                    + " LIMIT ?")
            try:
                hrows = conn.execute(hsql, (pat, *cparams, limit * 4)).fetchall()
            except sqlite3.OperationalError:
                hrows = []
            for r in hrows:
                cid = int(r["cid"])
                covers[cid] = covers.get(cid, 0) + 1

        if covers:
            # 覆盖率越高越可信：命中 2 个以上极大窗口的块，分数再抬一档
            weighted = {cid: float(n) * (1.0 + (n - 1) * 0.5)
                        for cid, n in covers.items()}
            # [M0-7 R1] 命中窗口数越大越好 → 正向归一（不能用 bm25 方向的 _normalize）
            for cid, s in _normalize_high(weighted).items():
                if s > scores.get(cid, 0.0):
                    scores[cid] = s

    return scores


# ---------------- 标题与表格加权 ----------------
def _heading_overlap(query: str, leaf: str) -> int:
    """查询与**叶子标题**的重叠长度（最长公共连续片段的字符数）。

    [重建] 原实现未留痕。用最直观的最长公共连续子串，
    与调用点用法一致（`ov >= HEADING_MIN_MATCH`、`boost = HEADING_BOOST*min(ov,6)`）。
    """
    q = query or ""
    s = leaf or ""
    if not q or not s:
        return 0
    best = 0
    for i in range(len(q)):
        for j in range(len(s)):
            k = 0
            while (i + k < len(q) and j + k < len(s) and q[i + k] == s[j + k]):
                k += 1
            if k > best:
                best = k
    return best


_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def _is_table_row(text: str) -> bool:
    """是不是"表格行"块（`| a | b |` 形态）。

    [重建] 原实现未留痕；按仓库里表格块的既有形态重写。
    """
    if not text:
        return False
    for ln in text.strip().splitlines()[:2]:
        if _ROW_RE.match(ln or ""):
            return True
    return False


# ---------------- [M0-7 R2] 标题匹配：实体词 > 通用意图词 ----------------
# 旧行为：查询与**叶子标题**的最长公共片段 >=2 就加分（上限 +0.024）。
# 问题：「作用机制/分类/区别/不良反应」这类词每章都有，谁标题里带它们谁就涨分，
# 于是"苯二氮卓类 作用机制"会把 p.29 巴比妥机制页、p.81 米氮平机制页顶上来，
# 而真正的目标页（p.16）标题里没有"作用机制"四个字，一分都拿不到
# （三类学生评测 P0-B：搜"苯二氮卓类 作用机制" top1 是巴比妥类 p.29）。
# 新规则：药名/类名等**实体词**命中（叶子或整条面包屑）才按长度加权；
# 通用意图词只给固定小额，且仅用于"同一实体下挑出机制/分类那一页"。
_GENERIC_INTENT_WORDS = (
    "作用机制", "作用机理", "药理作用", "临床应用", "不良反应", "作用", "机制", "机理",
    "分类", "分型", "区别", "比较", "对比", "不同", "首选", "哪几类", "有哪些", "哪些",
    "几类", "什么", "为什么", "如何", "怎样", "剂量", "特点", "注意", "过量", "中毒",
    "解救", "应用", "治疗", "导致", "引起", "的", "和", "与", "及", "或",
    "了", "吗", "呢", "是",
)
# 意图词：与上表重合，但这里只关心"这一页是不是问的那种页"，越具体越靠前
_INTENT_WORDS = (
    "作用机制", "作用机理", "机制", "机理", "分类", "分型", "区别", "不良反应",
    "首选", "临床应用", "用途", "中毒", "解救", "过量", "剂量", "是什么", "为什么",
)
# 通用意图词命中的固定加分（与实体命中的 HEADING_BOOST*len 分开，量级更小）
INTENT_BOOST = 0.006
# 标题加权总上限（实体 + 意图），与旧版 0.024 同量级、只多一个意图小额
HEADING_BOOST_MAX = 0.030


# 分类题意图：`按化学结构分哪几类/分为哪几类/分类/分型` 是问法而非实体。
_CLASSIFICATION_RE = re.compile(r"按.{0,10}?分|分(?:哪|几)?类|分为|分型|哪几类|几类|类型")
_CLASSIFICATION_MARKS = ("分类", "分型", "种类", "类型", "分为")
# 分类题里"标题含分类标记"的额外加分：专门把 p.5《镇静催眠药分类》这类
# "分类页"从同实体的普通页里挑出来（评测 P0-B 的"按化学结构分哪几类"）。
CLASSIFICATION_BOOST = 0.012
# 机制题（问作用机制/机理）里"标题就是机制动作页"的额外加分：
# p.16 的叶子是「BDZ增强GABA抑制」——既点名药物类（BDZ）又写了机制动作（增强/抑制），
# 比同节的「GABAA受体亚单位」「GABAA受体结构图」更像"机制答案页"。
MECHANISM_TITLE_BONUS = 0.010
_MECHANISM_INTENT = ("作用机制", "作用机理", "机制", "机理", "原理")
_ACTION_WORDS = ("增强", "抑制", "阻断", "开放", "促进", "激活", "激动", "拮抗",
                 "降低", "增加", "减少", "释放", "超极化", "通透")
# 实体词别名：同一药物/类在课件里可能写中文全名、拉丁缩写或异体字
# （p.6 原文就写着"苯二氮卓类（Benzodiazepine，BDZ）"，图注里还有"苯二氮䓬"）。
# 标题里只出现缩写时，不能因为"字面不同"就丢掉实体加权。
_ENTITY_ALIASES = {
    "苯二氮卓类": ("BDZ", "Benzodiazepine", "苯二氮䓬"),
    "苯二氮䓬类": ("BDZ", "Benzodiazepine", "苯二氮卓"),
    "巴比妥类": ("barbiturate", "barbiturates", "巴比妥"),
    "地西泮": ("Diazepam", "安定"),
    "新斯的明": ("neostigmine",),
    "抗精神病药": ("antipsychotic",),
    "胆碱受体": ("cholinoceptor", "cholinergic"),
}


def _is_classification_query(query: str) -> bool:
    return bool(_CLASSIFICATION_RE.search(query or ""))


def _specific_terms(query: str) -> "list[str]":
    """查询里去掉通用意图词后的"实体部分"（药名/类名/症状名），供标题加权。

    例：
      "镇静催眠药按化学结构分哪几类" -> ["镇静催眠药"]
      "苯二氮卓类的作用机制"         -> ["苯二氮卓类"]
      "苯二氮卓类和巴比妥类的作用机制有什么区别" -> ["苯二氮卓类", "巴比妥类"]
    """
    q = query or ""
    # 分类题的"按化学结构分…"是问法（化学结构不是要检索的实体名），先整段去掉
    q = re.sub(r"按[^，,。？?\s]{0,10}?分", " ", q)
    for w in sorted(_GENERIC_INTENT_WORDS, key=len, reverse=True):
        q = q.replace(w, " ")
    q = re.sub(r"[\s的了吗呢是有和与及或，,。？?！!、]", " ", q)
    return [p for p in q.split() if len(p) >= 2]


def _specific_query(query: str) -> str:
    """`_specific_terms` 的拼接形式（给最长公共片段用）。"""
    return " ".join(_specific_terms(query))


def _entity_aliases(term: str) -> "list[str]":
    """实体词 + 它的别名（中文全名/拉丁缩写/异体字）。"""
    out = [term]
    low = term.lower()
    for k, vals in _ENTITY_ALIASES.items():
        if k in term or k.lower() in low:
            out.extend(v for v in vals if v not in out)
    return out


def _entity_in_text(term: str, text: str) -> int:
    """实体词（含别名）在标题文本里的命中长度；没命中返回 0。"""
    best = _heading_overlap(term, text)
    if best >= len(term):
        return best
    for alias in _entity_aliases(term)[1:]:
        if alias in (text or ""):
            best = max(best, len(alias))
    return best


def _heading_boost(query: str, leaf: str, path: str) -> "tuple[float, list[str]]":
    """[M0-7 R2] 标题加权：实体词优先，通用意图词只给固定小额。

    返回 (加分, why 说明)。加分上限 HEADING_BOOST_MAX。
    与旧实现的差别：
      · 实体词命中**整条面包屑**也算（p.16 的目标页叶子是「BDZ增强GABA抑制」，
        药名「苯二氮卓类」与意图词「作用机制」都在祖先标题里），且认别名（BDZ）；
      · 只重合通用词时不再按长度给分，避免"任何机制页"都涨分；
      · 分类题：标题含「分类/分型」再给一笔固定加分（把分类页挑出来）；
      · 机制题：标题同时含"实体 + 机制动作词"再给一笔（把机制答案页挑出来）。
    """
    spec = _specific_query(query)
    boost = 0.0
    why: list[str] = []
    if spec:
        ov_leaf = _heading_overlap(spec, leaf)
        ov_path = _heading_overlap(spec, path)
        # 别名感知：标题里可能只有 BDZ / Benzodiazepine
        hit_leaf = ov_leaf
        for term in _specific_terms(query):
            hit_leaf = max(hit_leaf, _entity_in_text(term, leaf))
        if hit_leaf >= HEADING_MIN_MATCH:
            boost += HEADING_BOOST * min(hit_leaf, 6)
            why.append(f"标题命中实体「{leaf[:24]}」")
        elif ov_path >= HEADING_MIN_MATCH:
            boost += HEADING_BOOST * min(ov_path, 6)
            why.append(f"章节归属命中实体「{path.split('>')[-2].strip()[:24] if '>' in path else path[:24]}」")
    # 分类意图：标题里就写着「分类/分型」的页，才配当"分几类"的答案页
    if _is_classification_query(query) and any(m in (leaf or "") for m in _CLASSIFICATION_MARKS):
        boost += CLASSIFICATION_BOOST
        why.append("分类页加权")
    # 机制意图：标题里既有实体又有机制动作词（如「BDZ增强GABA抑制」）
    if any(w in (query or "") for w in _MECHANISM_INTENT) and spec:
        leaf_has_entity = any(
            _entity_in_text(t, leaf) >= HEADING_MIN_MATCH for t in _specific_terms(query)
        )
        if leaf_has_entity and any(a in (leaf or "") for a in _ACTION_WORDS):
            boost += MECHANISM_TITLE_BONUS
            why.append("机制动作页加权")
    intent = next((w for w in _INTENT_WORDS if w in (query or "")), "")
    if intent and (intent in (leaf or "") or intent in (path or "")):
        boost += INTENT_BOOST
        why.append(f"意图词命中「{intent}」")
    return min(boost, HEADING_BOOST_MAX), why


# ---------------- [M0-7 R4] 识图块与"正文优先" ----------------
# v1.1 真值里有 `_is_figure_hit`（审计 gap-report §5.2 列为重建丢失函数），
# 重建版把它连同识图排序一起简化掉了。这里按 source_ref 的 "图" 后缀识别。
_TEXT_INTENT_WORDS = (
    "作用机制", "机制", "机理", "分类", "分型", "区别", "不良反应", "首选", "中毒",
    "解救", "临床应用", "用途", "有哪些", "哪些", "是什么", "为什么", "如何", "怎样",
)
_FIGURE_QUERY_WORDS = ("图", "图示", "图注", "结构图", "看图", "长什么样", "画的是")
# 问"机制/分类/不良反应"这类正文题时，识图块（视觉模型解读）降权，
# 让"正文页"优先；问"图/结构图/长什么样"时不降权（那时图就是答案）。
FIGURE_TEXT_PENALTY = 0.012


def _is_figure_hit(source_ref: str) -> bool:
    """是不是识图块：`source_ref` 形如 `p.15 图`（视觉模型对页图的解读）。"""
    ref = (source_ref or "").strip()
    return bool(ref) and "图" in ref


def _prefers_text(query: str) -> bool:
    """问句要的是正文（机制/分类/不良反应…）而不是图。"""
    q = query or ""
    if any(w in q for w in _FIGURE_QUERY_WORDS):
        return False
    return any(w in q for w in _TEXT_INTENT_WORDS)


# ---------------- [M0-7 R5] 对比题：A 和/与 B 的区别 ----------------
# 整句检索（尤其两实体同属一份课件时）容易只召回其中一侧：实测
# "苯二氮卓类和巴比妥类的作用机制有什么区别" 前 8 条里全是 BDZ 侧，
# 巴比妥机制页 p.29 只排到第 4，而 BDZ 的机制页 p.16 被单文档配额挤掉。
_COMPARE_MARKS = ("区别", "不同", "比较", "对比", "异同", "差异", "vs", "VS", "Vs")
# 每个实体保底代表命中数：2 条只够"各有一条"，但同实体下"机制页/机制要点页"
# 常是相邻两页（p.16 机制 + p.18 增加 Cl- 通道开放频率），取 3 条才能都覆盖。
COMPARE_REPS_PER_ENTITY = 3


def _compare_entities(query: str) -> "list[str]":
    """对比题里参与对比的实体列表；不是对比题返回 []。"""
    q = query or ""
    if not any(m in q for m in _COMPARE_MARKS):
        return []
    ents = _specific_terms(q)
    return ents if len(ents) >= 2 else []


# ---------------- 主检索 ----------------
def search(conn: sqlite3.Connection, query: str, top_k: int = 8,
           doc_ids: list[int] | None = None, max_per_doc: int = 4,
           min_vec_score: float | None = None, mode: str = "hybrid",
           corpus: "str | list[str] | None" = "main") -> list[Hit]:
    """混合检索。

    mode: hybrid | vector | keyword
    min_vec_score: 向量相似度下限，低于此值不进候选。
        默认取 config.MIN_VEC_SCORE（0.35），用于避免库中无相关内容时
        返回一批"看似相似"的无关碎片——那会诱导模型据此编答案。
    corpus: 只看哪个库（main=教材+课件 / aux=试题+资料笔记）。
        **默认 "main"**：AI 问答、MCP、命令行、看板搜索全部安全默认，
        漏接线的调用点只会"看不到 aux"，而不会让试题泄漏进问答。
        传 `"aux"` 单看辅库；传 `"all"` / `None` / `["main","aux"]`
        则不限库（与分库前的行为一致），供知识点构建与诊断脚本显式使用。
        过滤在**取候选时**下推到向量/FTS/LIKE 三条路，不是事后筛。
        与 `doc_ids` 正交：后者仍是 chat.py 的"课件/教材分组"配额手段。
    """
    query = (query or "").strip()
    if not query:
        return []
    if min_vec_score is None:
        min_vec_score = config.MIN_VEC_SCORE

    use_fts = db.get_meta(conn, "fts5", "0") == "1"
    pool = max(top_k * 6, 40)

    # 库隔离条件：三条路（向量 / FTS / LIKE）+ 元数据取回共用一个片段。
    cwhere, cparams = _corpus_filter(corpus)
    cfilter = " WHERE " + cwhere if cwhere else ""
    cand_filter = " AND " + cwhere if cwhere else ""

    # --- 向量路 ---
    vec_rank: list[tuple[int, float]] = []
    if mode in ("hybrid", "vector"):
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM embeddings WHERE model=?",
            (config.EMBED_MODEL_REPO,),
        ).fetchone()
        if row and row["n"] > 0:
            qv = get_embedder().embed_query(query)
            rows = conn.execute(
                "SELECT e.chunk_id AS cid, e.vec AS vec"
                " FROM embeddings e"
                " JOIN chunks c ON c.id = e.chunk_id"
                " JOIN documents d ON d.id = c.doc_id"
                + cfilter,
                cparams,
            ).fetchall()
            if rows:
                ids = [int(r["cid"]) for r in rows]
                mat = np.vstack([from_blob(r["vec"]) for r in rows])
                sims = mat @ qv  # 都已 L2 归一化，点积即余弦
                order = np.argsort(-sims)

                # 绝对下限：过滤掉与查询明显无关的尾部
                passed: list[tuple[int, float]] = []
                for i in order:
                    s = float(sims[i])
                    if s < min_vec_score:
                        break  # 已按相似度降序，后面只会更低
                    passed.append((ids[int(i)], s))

                # 相对截断：以"首个通过绝对下限的结果"为基准，
                # 丢弃明显低于它的结果。
                # 为什么需要这一步：实测查 "TCP 为什么需要三次握手" 时，
                # 真正相关的 "TCP 与 UDP 的对比" 只有 0.356，
                # 而不相关的深度学习段落却有 0.473 —— 绝对阈值无法区分两者，
                # 相对基准才能把无关项剔掉。
                if passed:
                    base = passed[0][1]
                    cutoff = max(min_vec_score, base * config.RELVEC_RATIO)
                    passed = [(cid, s) for cid, s in passed if s >= cutoff]
                vec_rank = passed[:pool]

    # --- 关键词路 ---
    kw_scores = (_keyword_search(conn, query, pool, use_fts, corpus)
                 if mode in ("hybrid", "keyword") else {})
    kw_rank = sorted(kw_scores.items(), key=lambda kv: -kv[1])[:pool]

    # 关键词占优：查询是精确术语或公式时，关键词路会给出接近满分的命中。
    # 实测查 "n0 = n2 + 1" 时，关键词路精确命中，向量路却额外带进 5 条
    # 相似度 0.35~0.44 的无关块，此时应让关键词命中优先。
    #
    # 但**不能只保留关键词命中**：同一查询下，正文里没写 "TCP" 字面量的
    # 「TCP 三次握手」那一页会被关键词路漏掉，却正是向量路的第一名（0.72）。
    # 早先的写法只看 kw_scores，结果把正确答案整个丢了。
    #
    # 正确做法是重排而非过滤：把强关键词命中提到各自 list 的前面，
    # 让 RRF 自然给它们更高的名次权重，同时不丢掉向量路的第一名。
    strong: set[int] = set()
    if kw_scores and max(kw_scores.values()) >= KW_DOMINANCE:
        kwmax = max(kw_scores.values())
        strong = {c for c, s in kw_scores.items()
                  if s >= kwmax * config.RELKW_RATIO}
        vec_rank = ([x for x in vec_rank if x[0] in strong]
                    + [x for x in vec_rank if x[0] not in strong])
        kw_rank = ([x for x in kw_rank if x[0] in strong]
                   + [x for x in kw_rank if x[0] not in strong])

    # --- RRF 融合 ---
    # vec_map 保存未经"关键词占优"重排的完整向量分，
    # 用于最终展示（why 里的相似度）。
    vec_map = dict(vec_rank)
    fused: dict[int, float] = {}
    for rank, (cid, _s) in enumerate(vec_rank):
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, (cid, _s) in enumerate(kw_rank):
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    if not fused:
        return []

    ordered = [cid for cid, _ in sorted(fused.items(), key=lambda kv: -kv[1])]

    # ---------------- [重建] 元数据取回 + 加权 + 配额 + 截断 ----------------
    # 恢复版从第 366 行的融合循环之后开始缺失（缩进突然跳到 `why: list[str] = []`），
    # 426-431 行还留着一段**错位的列表推导残片**（`Hit(...) for r in rows` 悬空）。
    # 依残片与设计注释重写：
    #   1) 一次 SQL 取回候选块的元数据（**共用同一个库过滤片段**）；
    #   2) 标题只认叶子 + 表格块加权；
    #   3) 标题加权会改变顺序 ⇒ 重排后再施加单文档配额并截断；
    #   4) `doc_ids` 在此处生效（与原注释一致：它是候选截断后的手段）。
    meta_ids = ordered[:max(top_k * 3, 20)]
    ph = ",".join("?" * len(meta_ids))
    rows = conn.execute(
        "SELECT c.id AS cid, c.doc_id AS doc_id, c.ord AS ord, c.text AS text,"
        "       c.heading_path AS heading_path, c.source_ref AS source_ref,"
        "       COALESCE(c.topic,'') AS topic,"
        "       d.path AS dpath, d.title AS dtitle"
        " FROM chunks c"
        " JOIN documents d ON d.id = c.doc_id"
        " WHERE c.id IN (" + ph + ")" + cand_filter,
        (*meta_ids, *cparams),
    ).fetchall()
    rmap = {int(r["cid"]): r for r in rows}

    allowed = {int(x) for x in (doc_ids or []) if int(x) > 0}

    hits: list[Hit] = []
    for cid in ordered:
        r = rmap.get(cid)
        if r is None:
            continue          # 被库过滤挡掉（双保险）
        did = int(r["doc_id"])
        if allowed and did not in allowed:
            continue
        score = fused.get(cid, 0.0)

        why: list[str] = []
        if cid in vec_map:
            why.append(f"语义相似度 {vec_map[cid]:.3f}")
        if cid in kw_scores:
            why.append(f"关键词命中 {kw_scores[cid]:.2f}")
        head = r["heading_path"] or ""
        boost = 0.0
        # [M0-7 R2] 标题加权改成"实体词优先、通用意图词小额"（见 _heading_boost）。
        # 旧版是"查询与叶子标题任意 >=2 字公共片段 → 最多 +0.024"，于是
        # "作用机制/不良反应"这类通用词让一堆无关页涨分（p.29 巴比妥机制页、
        # p.81 米氮平机制页都因标题带"作用机制"被顶上来）。
        # 保留旧注释里的关键实测结论：**整条面包屑当普通重合用会变噪声放大器**
        # （问「抗高血压药分为哪几类」时 "> (五) 血管扩张药" 因祖先层含"抗高血压"
        # 被误加权；正文前3率 57.1% → 只认叶子后 85.7%）。所以：
        #   · **实体词（药名/类名）**按长度加权，且允许沿面包屑认亲——p.16 目标页
        #     叶子是「BDZ增强GABA抑制」，药名"苯二氮卓类"只出现在祖先层，不认就永远漏；
        #   · **通用意图词**只给固定小额 +0.006（叶子或祖先层都算），它顶多把
        #     "同一实体下的机制/分类页"往前挪半格，不会像旧版按长度加权那样一发
        #     就是 +0.024，让任何带"作用机制"标题的无关页翻上来。
        leaf = head.split(">")[-1].strip() if head else ""
        hb, hwhy = _heading_boost(query, leaf, head)
        if hb:
            boost += hb
            why.extend(hwhy)
        # 表格块加权（见 OVERVIEW_BOOST / ROW_BOOST 的说明）
        body = r["text"] or ""
        if OVERVIEW_MARK in body and "行 ×" in body:
            boost += OVERVIEW_BOOST
            why.append("表格概览块加权")
        elif _is_table_row(body):
            boost += ROW_BOOST
            why.append("表格行加权")
        # [M0-7 R4] 问机制/分类/不良反应这类正文题时，识图块降权（正文页优先）。
        # 识图块是视觉模型对页图的解读，适合"看图"类问题，但不该把 p.16 正文
        # 机制页挤出 Top-1（评测 P0-B 的另一半根因）。
        if _prefers_text(query) and _is_figure_hit(r["source_ref"] or ""):
            boost -= FIGURE_TEXT_PENALTY
            why.append("正文优先：识图块降权")
        hits.append(
            Hit(
                chunk_id=cid, doc_id=did, doc_path=r["dpath"],
                doc_title=r["dtitle"] or r["dpath"], ord=int(r["ord"]),
                text=r["text"], heading_path=head,
                source_ref=r["source_ref"] or "",
                vec_score=vec_map.get(cid, 0.0), kw_score=kw_scores.get(cid, 0.0),
                rrf=score + boost, why=why, topic=r["topic"] or "",
            )
        )

    # 标题加权会改变顺序，重排后再施加单文档配额并截断
    hits.sort(key=lambda h: -h.rrf)
    capped: list[Hit] = []
    per_doc: dict[int, int] = {}
    for h in hits:
        if per_doc.get(h.doc_id, 0) >= max_per_doc:
            continue
        per_doc[h.doc_id] = per_doc.get(h.doc_id, 0) + 1
        capped.append(h)
        if len(capped) >= top_k:
            break

    # ---------------- [M0-7 R5] 对比题：两个实体分别检索，各保底 3 条代表命中 ----------------
    # 触发条件：查询同时含对比标记（区别/不同/比较/vs…）和至少两个实体词。
    # 做法：整句结果不动，只把每个实体单独检索的前 3 条"代表命中"提到前面
    # （已在前面的自然去重），然后**重新施加单文档配额**——否则代表命中会
    # 被同一文档的其它块用配额挤掉（1498 之前 p.16 就是这么消失的）。
    # 递归安全：代表用的实体查询里实体已被剔掉，不会再被识别成对比题。
    entities = _compare_entities(query)
    if entities and mode in ("hybrid", "vector"):
        reps: list[Hit] = []
        seen_rep: set[int] = set()
        for ent in entities:
            ent_q = query
            for e in entities:
                ent_q = ent_q.replace(e, " ")
            ent_q = (ent + " " + ent_q).strip()
            try:
                e_hits = search(conn, ent_q, top_k=top_k, doc_ids=doc_ids,
                                max_per_doc=max_per_doc,
                                min_vec_score=min_vec_score, mode=mode,
                                corpus=corpus)
            except Exception:  # noqa: BLE001
                e_hits = []
            picked_n = 0
            for h in e_hits:
                if h.chunk_id in seen_rep:
                    continue
                h.why = list(h.why) + [f"对比题实体「{ent}」代表命中"]
                reps.append(h)
                seen_rep.add(h.chunk_id)
                picked_n += 1
                if picked_n >= COMPARE_REPS_PER_ENTITY:
                    break
        if reps:
            merged: list[Hit] = []
            seen: set[int] = set()
            for h in reps + list(capped):
                if h.chunk_id in seen:
                    continue
                seen.add(h.chunk_id)
                merged.append(h)
            capped2: list[Hit] = []
            per_doc2: dict[int, int] = {}
            for h in merged:
                if per_doc2.get(h.doc_id, 0) >= max_per_doc:
                    continue
                per_doc2[h.doc_id] = per_doc2.get(h.doc_id, 0) + 1
                capped2.append(h)
                if len(capped2) >= top_k:
                    break
            capped = capped2
    return capped


def expand_neighbors(conn: sqlite3.Connection, hits: list[Hit],
                     limit_per_hit: int = 1) -> list[Hit]:
    """给靠前的命中补上**同文档相邻块**，标 `is_extra=True`。

    为什么需要（原设计注释，用户实测反馈驱动）：
      连续几页讲同一主题时，只有第一页出现类别关键词，后几页检索不到，
      回答因此不完整。其中一类页面（有自己标题、但正文是上一页主题的展开）
      **机械规则判不出相关性**——只能把材料递给模型，由它读上下文决定。

    约定（调用方 `chat.gather_evidence` 依赖这两条）：
      1. `is_extra=True`：这些块**不占证据配额**（见 `chat._cap_evidence`），
         在提示词里单独成组并写明"相关性未经验证"；
      2. 只补**相邻 ord**（`ord±1`），不跨越整个文档。
    调用点在 `picked = picked + search_mod.expand_neighbors(conn, picked)`，
    且整段被 try/except 包着 —— 即失败了也不影响主流程。

    [重建] 原实现未留痕（只在注释里留下设计与约束）。这里按上述约束重写。
    """
    if not hits:
        return []
    out: list[Hit] = []
    seen = {h.chunk_id for h in hits}
    for h in hits:
        if getattr(h, "is_extra", False):
            continue                      # 不给补全块再补全，避免链式扩散
        rows = conn.execute(
            "SELECT c.id AS cid, c.ord AS ord, c.text AS text,"
            "       c.heading_path AS heading_path, c.source_ref AS source_ref,"
            "       COALESCE(c.topic,'') AS topic,"
            "       d.path AS dpath, d.title AS dtitle"
            " FROM chunks c JOIN documents d ON d.id = c.doc_id"
            " WHERE c.doc_id=? AND c.id<>? AND c.ord BETWEEN ? AND ?"
            " ORDER BY ABS(c.ord - ?) LIMIT ?",
            (h.doc_id, h.chunk_id, h.ord - 1, h.ord + 1, h.ord, limit_per_hit),
        ).fetchall()
        for r in rows:
            cid = int(r["cid"])
            if cid in seen:
                continue
            seen.add(cid)
            out.append(Hit(
                chunk_id=cid, doc_id=h.doc_id, doc_path=r["dpath"],
                doc_title=r["dtitle"] or r["dpath"], ord=int(r["ord"]),
                text=r["text"], heading_path=r["heading_path"] or "",
                source_ref=r["source_ref"] or "",
                rrf=0.0, why=["邻页参考（相关性未验证）"],
                is_extra=True, topic=r["topic"] or "",
            ))
    return out


def get_chunk(conn: sqlite3.Connection, chunk_id: int) -> "Hit | None":
    """按 id 取一个块（带上所属文档的路径与标题）；不存在返回 None。

    [C-0107 真值回填] 重建版把它丢了（`/api/chunk` 因此
    `AttributeError: module 'core.search' has no attribute 'get_chunk'`）。
    字节码真值 `search.funcs.txt::L1279` 写着
    `get_chunk  args=2  vars: conn, chunk_id, r`，SQL 取自
    `search.strings.txt` 中标 `[get_chunk]` 的常量
    （`c.id AS cid, c.doc_id, c.ord, c.text, c.heading_path, c.source_ref,
    c.topic AS topic, d.path AS dpath, d.title AS dtitle ... WHERE c.id=?`）。
    这是**补回丢失的函数**，不是给 `expand_neighbors` 起别名——两者语义不同
    （本函数按块 id 精确取一条，`expand_neighbors` 是按命中补相邻块）。
    """
    r = conn.execute(
        """SELECT c.id AS cid, c.doc_id, c.ord, c.text, c.heading_path, c.source_ref,
               COALESCE(c.topic,'') AS topic, d.path AS dpath, d.title AS dtitle
        FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE c.id=?""",
        (int(chunk_id),)).fetchone()
    if r is None:
        return None
    return Hit(
        chunk_id=int(r["cid"]), doc_id=int(r["doc_id"]),
        doc_path=r["dpath"] or "", doc_title=r["dtitle"] or r["dpath"] or "",
        ord=int(r["ord"] or 0), text=r["text"] or "",
        heading_path=r["heading_path"] or "", source_ref=r["source_ref"] or "",
        topic=r["topic"] or "",
    )


def neighbors(conn: sqlite3.Connection, hit: Hit, before: int = 1,
              after: int = 1) -> list[Hit]:
    """命中块的相邻上下文（同文档 `ord` 区间，含命中块自己），按 `ord` 升序。

    [C-0107 真值回填] `/api/chunk` 的上下文来自本函数（真值
    `search.funcs.txt::L1296`：`neighbors  args=4  vars: conn, hit, before,
    after, lo, hi, rows, r`），重建版同样丢了。区间端点由 `hit.ord` 加减
    `before`/`after` 得到，SQL 取自 `search.strings.txt` 中标 `[neighbors]`
    的常量（`WHERE c.doc_id=? AND c.ord BETWEEN ? AND ? ORDER BY c.ord`）。
    """
    lo = max(0, int(hit.ord) - int(before))
    hi = int(hit.ord) + int(after)
    rows = conn.execute(
        """SELECT c.id AS cid, c.doc_id, c.ord, c.text, c.heading_path, c.source_ref,
               COALESCE(c.topic,'') AS topic, d.path AS dpath, d.title AS dtitle
        FROM chunks c JOIN documents d ON d.id = c.doc_id
        WHERE c.doc_id=? AND c.ord BETWEEN ? AND ? ORDER BY c.ord""",
        (int(hit.doc_id), lo, hi)).fetchall()
    out: list[Hit] = []
    for r in rows:
        out.append(Hit(
            chunk_id=int(r["cid"]), doc_id=int(r["doc_id"]),
            doc_path=r["dpath"] or "", doc_title=r["dtitle"] or r["dpath"] or "",
            ord=int(r["ord"] or 0), text=r["text"] or "",
            heading_path=r["heading_path"] or "", source_ref=r["source_ref"] or "",
            topic=r["topic"] or "",
        ))
    return out


def _same_theme(a: str, b: str) -> bool:
    """"同一主题"判据：两条面包屑是**父子/兄弟**关系，不是相等。

    证据来自 `core/outline.py::DeckOutline.topic()` 的文档（它明写"见
    `core/search.py` 的 `_same_theme`"）：接了导图之后，同节各页的 topic
    呈父子关系——p.26 是「… > 巴比妥类」、p.27 是「… > 巴比妥类 > 药理作用」。
    所以这里必须按**前缀包含**判，相等判据会把它们当成两节。

    [重建] 原实现未留痕（只留下"见 outline.topic()"的线索）。
    """
    na = (a or "").strip()
    nb = (b or "").strip()
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def expand_continuations(conn: sqlite3.Connection, hits: list[Hit],
                         max_extra: int = 2) -> list[Hit]:
    """同主题**续页**补全：把命中页后面紧邻的、同一节的块补进来。

    为什么需要（用户实测反馈，原文见 `core/outline.py` 模块文档）：
      p.26 写某类药的总体药理作用，p.27 分写其中一条。问"该类药的药理作用"
      时只召回 p.26，**p.27 丢失**。p.27 有自己标题、正文不含药名，
      逐页局部推断判不出从属关系——直到有了全局导图（topic 呈父子关系）。
      所以这里用 `_same_theme` 判"p.27 是否属于 p.26 那一节"。

    与 `expand_neighbors` 的分工：
      · `expand_neighbors`：纯**位置相邻**（ord±1），不判主题，标注"相关性未验证"；
      · `expand_continuations`：判**同主题**（topic 前缀关系）后顺延，可信度更高。
    两者都标 `is_extra=True`：**不占证据配额**（见 `chat._cap_evidence`），
    否则"补全"会变成"挤占别的资料"。

    [重建] 原实现未留痕（`chat._cap_evidence` 的文档引用了它，说明它确实存在）。
    """
    if not hits or max_extra <= 0:
        return []
    out: list[Hit] = []
    seen = {h.chunk_id for h in hits}
    for h in hits:
        if getattr(h, "is_extra", False):
            continue                      # 不给补全块再补全
        base_topic = h.topic or ""
        rows = conn.execute(
            "SELECT c.id AS cid, c.ord AS ord, c.text AS text,"
            "       c.heading_path AS heading_path, c.source_ref AS source_ref,"
            "       COALESCE(c.topic,'') AS topic,"
            "       d.path AS dpath, d.title AS dtitle"
            " FROM chunks c JOIN documents d ON d.id = c.doc_id"
            " WHERE c.doc_id=? AND c.ord > ? ORDER BY c.ord LIMIT ?",
            (h.doc_id, h.ord, max_extra * 4),
        ).fetchall()
        added = 0
        for r in rows:
            if added >= max_extra:
                break
            cid = int(r["cid"])
            if cid in seen:
                continue
            cand_topic = r["topic"] or ""
            # 同主题判据优先用 topic；老库没有 topic 时退回面包屑去掉末级
            if base_topic or cand_topic:
                if not _same_theme(base_topic, cand_topic):
                    continue
            else:
                hb = (h.heading_path or "")
                cb = (r["heading_path"] or "")
                if not _same_theme(hb.rsplit(">", 1)[0].strip(),
                                   cb.rsplit(">", 1)[0].strip()):
                    continue
            seen.add(cid)
            added += 1
            out.append(Hit(
                chunk_id=cid, doc_id=h.doc_id, doc_path=r["dpath"],
                doc_title=r["dtitle"] or r["dpath"], ord=int(r["ord"]),
                text=r["text"], heading_path=r["heading_path"] or "",
                source_ref=r["source_ref"] or "",
                rrf=0.0, why=["同主题续页（导图父子关系）"],
                is_extra=True, topic=cand_topic,
            ))
    return out


def doc_outline(conn: sqlite3.Connection, doc_id: int) -> list[dict]:
    """文档大纲：只回标题路径与块区间，是最省 token 的"先看目录再决定读哪节"。

    面包屑里的文档标题前缀会被剥掉，让大纲更像目录而不是一串重复前缀。
    """
    row = conn.execute("SELECT title FROM documents WHERE id=?", (doc_id,)).fetchone()
    title = (row["title"] if row else "") or ""

    rows = conn.execute(
        """
        SELECT id, ord, heading_path, source_ref, char_len,
               substr(text, 1, 160) AS preview
        FROM chunks WHERE doc_id=? ORDER BY ord
        """,
        (doc_id,),
    ).fetchall()
    out: list[dict] = []
    seen: dict[str, dict] = {}
    for r in rows:
        hp = r["heading_path"] or "(无标题)"
        if title and hp.startswith(title):
            hp = hp[len(title):].lstrip(" >").strip() or "(正文)"
        if hp not in seen:
            entry = {"heading": hp, "first_chunk": int(r["id"]), "ords": [int(r["ord"])],
                     "refs": [r["source_ref"] or ""], "chars": int(r["char_len"] or 0),
                     "preview": r["preview"]}
            seen[hp] = entry
            out.append(entry)
        else:
            seen[hp]["ords"].append(int(r["ord"]))
            seen[hp]["chars"] += int(r["char_len"] or 0)
            if r["source_ref"] and r["source_ref"] not in seen[hp]["refs"]:
                seen[hp]["refs"].append(r["source_ref"])
    for e in out:
        e["n_chunks"] = len(e["ords"])
        e["ref_range"] = (e["refs"][0] if len(e["refs"]) == 1
                          else f"{e['refs'][0]}~{e['refs'][-1]}" if e["refs"] else "")
    return out
