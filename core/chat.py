# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-02-675e389c   (project study_agent; keep this line)
"""知识库问答：检索 → 组织证据 → 调用大模型作答（带出处）。

设计要点（这几条决定了回答可不可信）：

1. **检索结果必须先落地成"证据块"再交给模型**，每条都带稳定编号 [1][2]…
   模型只能引用这些编号，前端也能据此把编号还原成可点击的原文。
   不让模型自己编引用格式——它编出来的页码无法验证。

2. **幻灯片与教材分开检索**。用户明确要求"在 PPT 第几页 + 在教材第几页"。
   混在一起按分数排序时，块数多的那份（药理学教材 867 块）会霸屏，
   课件往往一条都进不来。所以分两组各取若干，再合并。

3. **出处标签按资料类型渲染**：PPTX 的 p.12 是"第12张幻灯片"，
   PDF 的 p.12 是"第12页"，两者含义不同，不能都写"p.12"。

4. **提示词强制"没检索到就说没有"**。这套系统的立命之本是可验证，
   宁可答"资料里没有"，也不能编。

模型：`deepseek-flash`（用户所指的 V4.1-Flash，API 里就是这个 ID）。
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# 允许直接 `python core/chat.py "问题"` 自测：此时没有包上下文，
# 需要把项目根加进 sys.path 并用绝对导入
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core import config, db  # type: ignore
    from core import search as search_mod  # type: ignore
else:
    from . import config, db, search as search_mod

try:
    from .pipeline_runtime import context as pipeline_context
except Exception:  # noqa: BLE001  （单文件自测模式）
    from core.pipeline_runtime import context as pipeline_context

# DeepSeek 官方端点与模型
DEEPSEEK_BASE = "https://api.deepseek.com"
CHAT_MODEL = "deepseek-flash"

# 凭据位置（DSH 统一管理，密钥不落在项目里）
#
# 卡 C-0071：这里原来还硬编码了作者本机的一条绝对路径
# （`C:\Users\<作者>\.dsh\.credentials.yaml`）。三个理由把它删掉：
#   1. 对别人毫无用处 —— 人家的用户名不是这个，那一项永远不会命中；
#   2. 它让"没有密钥"这一**状态本身无法复现**：把 HOME 隔离到空目录后，
#      这个写死的路径照样能读到本机凭据，于是"未配密钥"的界面永远测不出来；
#   3. 公开源码里不该出现作者的用户名。
# ⚠ 本机行为**逐字不变**：`Path.home()` 就是 `C:\Users\<作者>`，
#   下面这一项与删掉的那一项指向同一个文件。
CRED_PATHS = [
    Path.home() / ".dsh" / ".credentials.yaml",
]

# 单条证据最多注入多少字符。块本身约 700 字，这里留出余量但不放任长块霸占上下文。
MAX_CHUNK_CHARS = 1400
# 默认每组检索条数
DEFAULT_PER_KIND = 4
# 历史轮数（1 轮 = 一问一答）
MAX_HISTORY_TURNS = 4


class ChatError(RuntimeError):
    pass


# ---------------- 凭据 ----------------
def load_api_key() -> str:
    """从 DSH 凭据文件读 DeepSeek key。

    刻意不把 key 写进项目 .env：两份密钥容易不一致，
    而且项目目录更容易被复制/分享出去。
    """
    import os
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env and env.startswith("sk-"):
        return env.strip()
    for p in CRED_PATHS:
        if not p.exists():
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"DEEPSEEK_API_KEY\s*:\s*['\"]?(sk-[A-Za-z0-9_\-\.]{8,})", txt)
        if m:
            return m.group(1)
    raise ChatError(
        "没有找到 DEEPSEEK_API_KEY。请在 "
        f"{CRED_PATHS[0]} 里配置，或设置同名环境变量。")


# ---------------- 出处标签 ----------------
def ref_label(doc_kind: str, source_ref: str,
              file_kind: str | None = None) -> str:
    """把 source_ref 渲染成人能读懂的定位词。

    **两种"p."含义不同**：pptx 里的 `p.12` 是**第 12 张幻灯片**，
    pdf 里的才是**第 12 页**。混用会让人翻错地方。

    判断依据是**文件格式**（file_kind），不是教学角色——
    讲课用 PDF 导出件虽然是"课件"，但它的 p.12 仍是第 12 页。
    为兼容旧调用，file_kind 缺省时退回用 doc_kind 猜。
    """
    if not source_ref:
        return ""
    s = source_ref.strip()
    fk = (file_kind or doc_kind or "").lower()
    is_slide = fk in ("pptx", "ppt")

    def _span(a: str, b: str | None, unit: str) -> str:
        if not b:
            return f"第{a}{unit}"
        try:
            ia, ib = int(a), int(b)
            if ia > ib:          # 颠倒的区间：交换，保持可读
                ia, ib = ib, ia
            if ia == ib:
                return f"第{ia}{unit}"
            return f"第{ia}–{ib}{unit}"
        except ValueError:
            return f"第{a}–{b}{unit}"

    m = re.match(r"^p\.(\d+)(?:-(\d+))?$", s)
    if m:
        return _span(m.group(1), m.group(2), "张幻灯片" if is_slide else "页")
    m = re.match(r"^slide\s+(\d+)(?:-(\d+))?$", s)
    if m:
        return _span(m.group(1), m.group(2), "张幻灯片")
    m = re.match(r"^para\s+(\d+)(?:-(\d+))?$", s)
    if m:
        return _span(m.group(1), m.group(2), "段")
    return s


# ---------------- 证据组织 ----------------
@dataclass
class Evidence:
    idx: int
    chunk_id: int
    doc_id: int
    doc_title: str
    doc_kind: str            # 角色：textbook / courseware
    heading_path: str
    source_ref: str
    text: str
    doc_file_kind: str = ""  # 文件格式：pdf / pptx / docx，决定"页"还是"幻灯片"
    vec_score: float = 0.0
    kw_score: float = 0.0
    confidence: str = "ok"
    # 这条是"邻页参考"（检索命中块的相邻块），**相关性未经验证**。
    # 单独标出来是为了在提示词里另成一组、并写明不确定，
    # 避免模型把"同一文档的邻近页"默认当成相关（见 chat.gather_evidence）。
    is_extra: bool = False

    def label(self) -> str:
        """形如《药理学 第6版》第 48 页 · 第九章 镇静催眠药"""
        parts = [f"《{self.doc_title}》"]
        loc = ref_label(self.doc_kind, self.source_ref, self.doc_file_kind)
        if loc:
            parts.append(loc)
        if self.heading_path:
            # 面包屑首段常与文档标题重复，去掉
            head = self.heading_path
            if head.startswith(self.doc_title):
                head = head[len(self.doc_title):].lstrip(" >")
            if head:
                parts.append(head)
        return " · ".join(parts)

    @property
    def role_label(self) -> str:
        return {"textbook": "教材", "courseware": "课件"}.get(
            self.doc_kind, "资料")

    @property
    def is_figure(self) -> bool:
        """这条证据是不是**视觉模型对插图的解读**。

        依据是 `heading_path` 里带"图示说明"（`core/figures.py` 的切块约定），
        而不是 `source_ref` 里的"图"字——后者会误判正文里带"图"字的引用，
        前者是识图块专属的可检索标记。

        为什么要单独认出来：识图块的文字读起来**很像教科书正文**
        （"本页把三大不良反应并列列出……"），用户与模型都分不清
        它是原文还是解读。解读含推断成分，必须显式标注。
        """
        return "图示说明" in (self.heading_path or "")

    def to_dict(self) -> dict:
        return {
            "idx": self.idx,
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "doc_kind": self.doc_kind,
            "role_label": self.role_label,
            "is_figure": self.is_figure,
            "heading_path": self.heading_path,
            "source_ref": self.source_ref,
            "location": ref_label(self.doc_kind, self.source_ref,
                                  self.doc_file_kind),
            "label": self.label(),
            "text": self.text,
            "vec_score": round(self.vec_score, 4),
            "kw_score": round(self.kw_score, 4),
            "confidence": self.confidence,
        }


def _primary_kind(conn, doc_id: int) -> str:
    """资料的**角色**：textbook（教材）/ courseware（课件）。

    刻意不用文件的 kind（pdf/pptx）来分组。用户的实际构成是
    "只有《药理学 第6版》是教材，其余全是课件"——而那些讲课用 PDF
    导出件（`1-卢-抗高血压药 2025.pdf` 之类）本质是课件。
    按扩展名分组会把它们错划到"教材"一侧，于是回答里
    "课件没找到"的结论就是错的。
    """
    return _doc_info(conn, doc_id)[0]


def _doc_info(conn, doc_id: int) -> tuple[str, str]:
    """返回 (角色, 文件格式)。

    角色决定"是课件还是教材"，文件格式决定出处措辞是"页"还是"幻灯片"。
    两者必须分开：讲课用 PDF 是**课件**，但它的 p.12 是**第 12 页**。
    """
    r = conn.execute("SELECT role, kind FROM documents WHERE id=?",
                     (doc_id,)).fetchone()
    if not r:
        return "courseware", ""
    file_kind = (r["kind"] or "").lower()
    role = (r["role"] or "").strip()
    if role not in ("textbook", "courseware", "reference"):
        # 旧库没有 role 时退回按扩展名猜，至少不崩
        role = "courseware" if file_kind == "pptx" else "textbook"
    return role, file_kind


def _is_textbook(conn, doc_id: int) -> bool:
    return _primary_kind(conn, doc_id) == "textbook"


# ---------------- 查询改写（补语义鸿沟） ----------------
EXPAND_PROMPT = """你是检索助手。用户会用口语提问，而资料（药理学课件与教材）用的是专业术语。
请把问题转成检索线索。

要求：
1. terms：从问题中提取**专业术语**，中英文都要，最多 8 个。
   例如"阿司匹林怎么止痛的" → ["阿司匹林","aspirin","环氧合酶","COX","前列腺素","解热镇痛"]
   若问题本身没有专业名词，就给最可能出现在资料里的学科词汇。
2. queries：写 2–3 个**不同措辞**的检索式，向资料的语言靠拢，每条不超过 20 字。
   例如"阿司匹林的作用机制" →
   ["阿司匹林 药理作用 环氧合酶抑制","aspirin COX 不可逆抑制","阿司匹林 解热镇痛 机制"]

只输出 JSON，不要解释，不要代码块：
{"terms": ["..."], "queries": ["..."]}"""


def expand_query(query: str) -> dict:
    """用模型把口语问题转成检索线索。失败时降级为不扩展，不影响主流程。"""
    try:
        msgs = [{"role": "system", "content": EXPAND_PROMPT},
                {"role": "user", "content": query}]
        r = chat_once(msgs, max_tokens=400, thinking=False, timeout=60,
                      temperature=0.0)
        txt = (r.get("content") or "").strip()
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return {"terms": [], "queries": []}
        data = json.loads(m.group(0))
        terms = [str(t).strip() for t in (data.get("terms") or [])
                 if str(t).strip()][:8]
        queries = [str(q).strip() for q in (data.get("queries") or [])
                   if str(q).strip()][:3]
        return {"terms": terms, "queries": queries}
    except Exception:
        return {"terms": [], "queries": []}


def _take_hits(picked: list, seen: set, hits) -> None:
    for h in hits:
        if h.chunk_id in seen:
            continue
        seen.add(h.chunk_id)
        picked.append(h)


def gather_evidence(conn, query: str, per_kind: int = DEFAULT_PER_KIND,
                    split_kinds: bool = True,
                    doc_ids: list[int] | None = None,
                    expand: bool = True) -> list[Evidence]:
    """检索并组织证据。

    做三件事来对抗"口语提问 vs 术语原文"的语义鸿沟：

    1. **术语增强的关键词检索**。FTS/LIKE 路对整句口语几乎无能为力，
       但对"阿司匹林""环氧合酶"这类术语命中率很高。
       所以先用模型抽出术语，再拿术语单独跑一遍。
       实测：查"阿司匹林的作用机制是什么"时，直接检索召回不到教材
       第162页的"【药理作用】阿司匹林对COX具有不可逆抑制作用"，
       而加上术语 `COX` / `环氧合酶` 就能命中原句。

    2. **查询改写**。让模型把口语问题改写成贴近资料措辞的检索式，
       跑语义路。

    3. **幻灯片与文档分组检索**。混在一起时块数多的教材会霸屏
       （药理学教材 867 块），课件往往一条都进不来。

    expand=False 可关闭改写（离线/省钱场景）。
    """
    picked: list[search_mod.Hit] = []
    seen: set[int] = set()

    # 查询改写（一次额外的大模型调用）与基础检索互不依赖，可以重叠。
    # 串行时实测要 7.6 秒才推出第一个事件，用户盯着空白等太久。
    #
    # 注意：只有**大模型调用**能放到工作线程，检索必须留在主线程——
    # sqlite3 的连接对象默认不允许跨线程使用（check_same_thread），
    # 在工作线程里用主线程的 conn 是不安全的。
    import concurrent.futures as _cf
    tips: dict = {"terms": [], "queries": []}
    pool = _cf.ThreadPoolExecutor(max_workers=1) if expand else None
    fut = pool.submit(expand_query, query) if pool else None

    def search(query_text: str, k: int, mode: str = "hybrid",
               ids: list[int] | None = None):
        try:
            # corpus="main" 是问答链路的**红线**（需求 2B）：教材与课件是唯一
            # 可依据的证据来源，试题与资料笔记（aux）不得进入回答。
            # 这里写死而不依赖 search() 的默认值——即使将来默认值被改宽，
            # 问答链路仍然安全。全部 7 处问答检索都经过这个闭包。
            return search_mod.search(conn, query_text, top_k=k,
                                     doc_ids=ids, mode=mode, corpus="main")
        except Exception:
            return []

    # 基础的全库检索先跑：既拿到无约束的基线，也顺带把嵌入模型热起来。
    # 放在改写结果回来之前跑，把等待时间填满。
    if expand:
        _take_hits(picked, seen,
                   search(query, max(per_kind * 2, 8), ids=doc_ids))

    if fut is not None:
        try:
            tips = fut.result(timeout=90) or tips
        except Exception:
            tips = {"terms": [], "queries": []}
        finally:
            pool.shutdown(wait=False)
    terms: list[str] = tips.get("terms") or []
    alt_queries: list[str] = tips.get("queries") or []

    if not split_kinds:
        _take_hits(picked, seen, search(query, per_kind * 2, ids=doc_ids))
        for q in alt_queries:
            _take_hits(picked, seen, search(q, per_kind, ids=doc_ids))
    else:
        # 按**角色**分组补齐：课件（含讲课用 PDF）与教材都要有代表。
        #
        # 这里对每个组各跑一次主查询，是为了"两类资料都进结果"。
        # 为什么不干脆全库取一次再按角色筛：筛选只在已召回的候选里挑，
        # 若全库结果被块数最多的教材占满（教材 867 块、课件合计 1465 块），
        # 课件可能一条都没被召回，"筛"就无从谈起。
        #
        # 但**改写式不按组重复**——早先写在分组循环里，同样的检索式跑了两遍，
        # 实测一次提问要跑 10 次检索、耗时 6.9 秒。
        # 向量检索是 O(块数) 的全量矩阵乘，次数直接乘在延迟上。
        # 分组用的 doc_id 也必须限定在 main：这两个查询原来不带库条件，
        # 会把 aux（试题/资料笔记）的 doc_id 也取进来，于是下面 `ids=g`
        # 的分组检索会把试题当"课件"召回（需求 2B 的红线）。
        cw_docs = [int(r["id"]) for r in conn.execute(
            "SELECT id FROM documents WHERE COALESCE(corpus,'main')='main' "
            "AND COALESCE(role,'courseware')<>'textbook'")]
        tb_docs = [int(r["id"]) for r in conn.execute(
            "SELECT id FROM documents WHERE COALESCE(corpus,'main')='main' "
            "AND role='textbook'")]
        if doc_ids:
            cw_docs = [d for d in doc_ids if not _is_textbook(conn, d)]
            tb_docs = [d for d in doc_ids if _is_textbook(conn, d)]

        for g in (cw_docs, tb_docs):
            if g:
                _take_hits(picked, seen, search(query, per_kind, ids=g))

        # 改写式只取前两条：第三条的边际收益很低，而每次都要付一次全量检索。
        for q in alt_queries[:2]:
            _take_hits(picked, seen, search(q, max(3, per_kind), ids=doc_ids))

        if not picked:
            _take_hits(picked, seen, search(query, per_kind * 2))

    # 术语增强的关键词检索：把术语拼成 OR 查询单独跑一遍关键词路。
    # 这是把"口语问 → 术语原文"接上的关键一步。
    if terms:
        kw_query = " ".join(terms[:5])
        for mode in ("keyword", "hybrid"):
            hits = search(kw_query, max(3, per_kind), mode=mode, ids=doc_ids)
            _take_hits(picked, seen, hits)
            # 命中足够就不再追加，避免低分噪声挤掉高相关项
            if len(picked) >= per_kind * 3:
                break

    # 按分数重排：术语路可能捞回低分噪声，压到后面
    picked.sort(key=lambda h: (-(h.rrf or 0.0), -h.vec_score))

    # 收敛证据量。术语增强那一轮容易多捞，17 条会把输入推到 4300 tokens，
    # 而其中大半是低分噪声。这里做两件事：
    #   1. 总数封顶
    #   2. 保证两类资料都留得住（否则封顶会把课件全挤掉，用户就看不到"PPT 第几页"）
    picked = _cap_evidence(conn, picked, max_total=10, per_kind_floor=2)

    # 邻页参考：给最靠前的几个命中补上同文档的相邻块，**标注相关性未验证**。
    #
    # 起因（用户实测反馈）：连续几页讲同一主题时，只有第一页出现类别关键词，
    # 后几页检索不到，回答因此不完整。其中一类页面（有自己标题、
    # 但正文是上一页主题的展开）**机械规则判不出相关性**——
    # 只能把材料递给模型，由它读上下文决定。
    #
    # 这些块不占证据配额（`is_extra=True`），在提示词里单独成组。
    # 放在 `_cap_evidence` **之后**：否则它们会挤占正常命中的名额。
    try:
        picked = picked + search_mod.expand_neighbors(conn, picked)
    except Exception:  # noqa: BLE001
        pass                      # 补全失败不影响主流程

    ev: list[Evidence] = []
    for i, h in enumerate(picked, 1):
        role, file_kind = _doc_info(conn, h.doc_id)
        text = h.text
        if len(text) > MAX_CHUNK_CHARS:
            text = text[:MAX_CHUNK_CHARS] + " …（后续略）"
        ev.append(Evidence(
            idx=i, chunk_id=h.chunk_id, doc_id=h.doc_id,
            doc_title=h.doc_title, doc_kind=role,
            heading_path=h.heading_path, source_ref=h.source_ref,
            text=text, doc_file_kind=file_kind,
            vec_score=h.vec_score, kw_score=h.kw_score,
            confidence=h.confidence,
            is_extra=bool(getattr(h, "is_extra", False)),
        ))
    return ev


def _cap_evidence(conn, hits: list, max_total: int = 10,
                  per_kind_floor: int = 2) -> list:
    """限制证据总条数，同时保证每类资料至少留 floor 条。

    用户明确要求"既给 PPT 第几页，也给教材第几页"，
    所以不能单纯按分数截断——教材块多分高时会把课件全挤掉。

    **自动补全的同主题续页不占名额**（`is_extra`）：它们是为了让一次命中
    读得完整而补进来的（见 `search.expand_continuations`），
    若与检索命中一起抢配额，会反过来挤掉别的资料——
    那就把"补全"变成了"挤占"。
    """
    extra = [h for h in hits if getattr(h, "is_extra", False)]
    hits = [h for h in hits if not getattr(h, "is_extra", False)]
    if len(hits) <= max_total:
        return hits + extra
    tb, cw = [], []
    for h in hits:
        (tb if _is_textbook(conn, h.doc_id) else cw).append(h)

    keep: list = []
    # 先按 floor 各保底
    keep.extend(cw[:per_kind_floor])
    keep.extend(tb[:per_kind_floor])
    if len(keep) >= max_total:
        pos = {id(h): i for i, h in enumerate(hits)}
        keep = keep[:max_total]
        keep.sort(key=lambda h: pos.get(id(h), 999))
        return keep + extra
    # 剩余名额按原顺序（已按分数排好）补
    chosen = {id(x) for x in keep}
    for h in hits:
        if len(keep) >= max_total:
            break
        if id(h) not in chosen:
            keep.append(h)
            chosen.add(id(h))
    # 恢复分数顺序（保底挑选打乱了顺序），续页补全块留在最后
    pos = {id(h): i for i, h in enumerate(hits)}
    keep.sort(key=lambda h: pos.get(id(h), 999))
    return keep + extra


# ---------------- 提示词 ----------------
# 下面这段提示词按「学习看板 AI 问答」的两条需求改过（卡 C-0014），改动理由记在这里：
#
# 1) **弱化"资料残缺"**：原文要求"答不上来就说明可能需要补充哪类资料"，
#    实测被模型当成了固定收尾模板，几乎每轮都追加"资料不全、建议补充××"，
#    把"某一点没覆盖"放大成"资料有缺陷"，对用户是纯噪音。
#    现改为：片段没覆盖到就直接说没有、就此打住；**只有整份片段为空**时才提示补资料。
#    底线原封不动——"片段里没有就说没有""绝不允许自己编"照旧保留。
#
# 2) **末尾加"知识点联想"**：用户希望答完疑问后能顺势接着问下去。
#    但"联想发散"天生有编造风险，会撞上本文件的立命之本（结论必须有出处，
#    见文件头设计要点 4：宁可答"资料里没有"，也不能编）。所以联想被严格限定为
#    "对**本次已检索片段**的重新组织与引导提问"：素材只依据检索片段、
#    强制标注"非资料原文"、不得出现片段外的新事实、检索为空时干脆不写这一段。
#    宁可联想得少，也不能让联想变成第二个知识来源。
#
# 提示词里下面这些词被 tools/smoke_test.py 的契约断言盯着，别顺手删：
# "只能依据"/"绝不允许"/"核对编号"/"课件"/"教材"/"图示说明"/"不要写成"。
SYSTEM_PROMPT = """你是一个基于用户本地学习资料作答的助手。资料分两类：
**课件**（PPT/讲课幻灯，定位单位是"第N张幻灯片"）
**教材**（教科书，定位单位是"第N页"）。

**还要注意混在片段里的第三种东西：`图示说明`。**
那是**视觉模型对课件插图的解读**（来源行会标出「图示说明」，
出处形如 `第N张幻灯片 图`）。它的价值是补上文字层没有的结构信息
（箭头方向、通路走向、解剖位置），但它**含推断成分**，不等于书上原话。

**铁律：只能依据下面提供的【资料片段】回答。**
- 片段里没有的内容，直接说"知识库里没有找到相关内容"，然后就此打住：
  **不要**据此判断"资料残缺"、也不要每轮都提示去补充资料——某一点没覆盖是常态，
  不是资料的缺陷。只有当**整份【资料片段】为空**（提示"没有检索到任何相关片段"）时，
  才补一句"这类内容目前还没入库，可能需要补充××类资料"。
  绝不允许用你自己的知识补充、推测或编造。
- 每一个事实性陈述后面必须标注来源编号，格式 `[1]`、`[2][3]`。
  编号必须来自【资料片段】，不要自己编号。
- **引用前先核对编号对应的资料名**。写"课件未提到"或"教材未提到"这类判断前，
  必须确认你引的那几条确实是那个资料——编号串错会让结论完全相反。
- 用户常需要知道"课件第几页、教材第几页"。若同一知识点在两类资料里都有，
  **分别指出两处**，写清各自位置与原文要点；若只有一类有，明确说另一类没有。
- 如果片段之间存在不一致，明确指出来，不要擅自取舍。
- 引用原文时用引号标出，不要改写后当作原文。
- **不要把「图示说明」当成原文引用。** 引用它时写成
  "课件第N张幻灯片的图示说明指出……"，不要写成"书上写……"。
  它与原文冲突时以原文为准，并指出这个冲突。

回答要求：
- **用中文思考和回答。** 推理过程与最终回答都用中文。
  资料里出现的英文术语（`Trigger Zone`、`COX`）照原样保留，
  但解释、组织、结论一律用中文——这份知识库是中文复习资料。
- 结构清晰（可用小标题、列表）。
- 先直接回答问题，再分「课件依据」与「教材依据」给出出处。
- 如果检索到的片段明显不相关（置信度低），要说明"这次检索可能不准"，
  并建议换个说法或指定资料。
- 回答的**最后**再补一段"知识点联想"，帮用户顺着已检索到的资料继续问下去，
  写法按下面【知识点联想】的五条硬约束来。

【知识点联想】每次回答的最后一段，五条都是硬约束，不得变通：
1. 素材**只依据**本次检索到的【资料片段】：可以重新组织、对比、串联片段里已经出现的
   名词、机制、分类与相互关系，但**不得引入片段之外的任何知识**（包括你自己知道的
   结论、指南、临床经验）。片段里没有的事实，一个字都不许写。
2. 该段以 `### 相关知识点联想` 为小标题，并先写明"以下为基于上述资料的延伸梳理，
   非资料原文；如需展开请追问"，与前面的正式回答显式区分开，
   让读者不可能把它当成资料原文。
3. 该段**不要求标引用编号**（它是组织与引导，不是新事实）；但不编号不等于放宽边界，
   同样**不得出现资料里不存在的新事实**。
4. 段末用 **2-4 个问句**引导继续提问，问句只能指向上面片段覆盖到的相邻知识点
   （同一机制的下游环节、同一类药的另一种、同一张幻灯片上的另一个标签），
   **不要**问片段里从未出现过的东西——那等于暗示资料里有其实没有的内容。
5. 若本次**没有检索到任何片段**（提示"没有检索到任何相关片段"），**不要写这一段**，
   不要编联想，直接说明资料里没有相关内容并结束。
联想的作用是帮用户把已经检索到的资料用起来、接着追问下去，不是展示你自己的知识。
"""


def build_messages(query: str, ev: list[Evidence],
                   history: list[dict] | None = None) -> list[dict]:
    """组装发给模型的消息。

    证据按**课件 / 教材分组**呈现。混在一起时模型容易把编号记串，
    实测出现过"课件片段"引到教材页码、并据此得出"课件未写机制"的反向结论。
    分组 + 显式标注资料类型能显著降低这类错误。
    """
    if ev:
        # 按角色分组：课件 vs 教材。混在一起时模型容易记串编号，
        # 实测出现过把教材页码引成"课件片段"、并据此得出反向结论的情况。
        #
        # 「邻页参考」再单独一组（`is_extra`）：它们是为补全上下文而附上的
        # **相邻页**，与问题的相关性**未经验证**，必须让模型知道这一点。
        slides = [e for e in ev if e.doc_kind != "textbook" and not e.is_extra]
        books = [e for e in ev if e.doc_kind == "textbook" and not e.is_extra]
        nbrs = [e for e in ev if e.is_extra]
        sections: list[str] = []
        for title, group in (("课件片段", slides), ("教材片段", books)):
            if not group:
                continue
            blocks = []
            for e in group:
                # 识图块显式标出——它的文字很像原文，不标会被当成书上原话
                tag = "\n〔以下是对插图的解读，含推断成分，非原文〕" \
                    if e.is_figure else ""
                blocks.append(f"[{e.idx}] 来源：{e.label()}{tag}\n原文：\n{e.text}")
            sections.append(f"### {title}\n\n" + "\n\n---\n\n".join(blocks))
        if nbrs:
            blocks = [f"[{e.idx}] 来源：{e.label()}\n原文：\n{e.text}"
                      for e in nbrs]
            sections.append(
                "### 邻页参考（**与问题是否相关未经验证**）\n\n"
                "以下是上面某些片段的**相邻页**，附在这里是为了补上下文"
                "（课件常常连续几页讲同一主题，只有第一页出现主题词）。\n"
                "**请自行判断是否与问题相关**：相关就用并注明出处，"
                "不相关就忽略，**不要**因为它们在同一份资料里就当作依据。\n\n"
                + "\n\n---\n\n".join(blocks))
        evidence_text = "\n\n=====\n\n".join(sections)
        user = (f"【资料片段】（编号仅供引用，请按编号对应的资料名分别说明）\n\n"
                f"{evidence_text}\n\n【问题】\n{query}")
    else:
        user = ("【资料片段】\n（没有检索到任何相关片段）\n\n"
                f"【问题】\n{query}")

    msgs: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or [])[-MAX_HISTORY_TURNS * 2:]:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            # 历史里的回答带 [n] 编号，但不带当轮片段，容易让模型混淆；
            # 截短即可，不必清洗编号
            msgs.append({"role": role, "content": content[:1500]})
    msgs.append({"role": "user", "content": user})
    return msgs


# ---------------- 调用 ----------------
def chat_once(messages: list[dict], model: str = CHAT_MODEL,
              max_tokens: int = 2048, thinking: bool = False,
              timeout: int = 180, temperature: float = 0.3,
              reasoning_effort: str | None = None,
              api_key: str | None = None, api_base: str | None = None,
              response_format: dict | None = None,
              run_context=None) -> dict:
    """非流式调用。thinking=False 省 token（实测输出从 188 降到 49）。

    卡 C-0052 新增 `reasoning_effort`：**思考强度**，官方取值
    `"none" | "low" | "high" | "max"`（默认 high）。它只会被**原样透传**成
    payload 的 `reasoning_effort` 字段，本函数不做校验、不改写——
    取值错了由服务端报错，不在这里静默降级。

    输出上限（2026-10-31 起）：由**当前管线**的设置项中央控制——cap>0 覆盖
    `max_tokens`，cap==0（默认，无上限）时请求里**不发送** `max_tokens`。
    传进来的 `max_tokens` 参数只是历史调用方的值，不再直接决定 HTTP 请求。

    两条实测口径（2026-09-27，`deepseek-flash`，官方端点）：
      - 只在 `thinking=True` 时才发这个字段：思考关掉时它没有作用，
        服务端也不保证接受，所以 `thinking=False` 时给了也不发。
      - ⚠ **思考模式会吃掉大量 completion token**（实测 8 页材料一条知识点：
        `reasoning_tokens=10638`，而 `completion_tokens=11196`）。
        调用方必须把 `max_tokens` 放大到远超正文长度，否则正文会被思维链挤没。
      - ⚠ 思考模式下 `temperature` **不生效**（传了不报错、被忽略），
        参数保留只是为了不动既有调用方。
    """
    from . import agent_bridge
    # P0-A（2026-10-31）：有 run_context 时**全部用冻结快照**（model/base/thinking/
    # effort/max_tokens），不再读取 config.token_cap() 或当前管线；
    # PIPELINE_RUNTIME=0 时忽略 context，走旧全局路径（旧行为逐字不变）。
    ctx = run_context if (run_context is not None
                          and pipeline_context.runtime_enabled()) else None
    if ctx is not None:
        pipeline = str(ctx.pipeline)
        model = str(ctx.model)
        thinking = bool(ctx.thinking)
        reasoning_effort = ctx.reasoning_effort if thinking else None
        effective_max = int(ctx.max_tokens or 0)
        api_base = str(ctx.api_base or "")
    else:
        # ★ 输出上限统一由「设置 → 各管线 token 上限」中央控制
        # （config.TOKEN_CAP_KEYS；0 = 无上限 = 请求里不发送 max_tokens）。
        # 调用方传进来的 max_tokens 只是历史参数，实际生效值以当前管线 cap 为准。
        pipeline = agent_bridge.current_pipeline()
        cap = config.token_cap(pipeline)
        effective_max = cap if cap > 0 else 0
    request = agent_bridge.AgentRequest(
        pipeline=pipeline, messages=messages, model=model,
        max_tokens=int(effective_max), thinking=bool(thinking),
        reasoning_effort=reasoning_effort,
        temperature=temperature, timeout=timeout, response_format=response_format)
    adapted = agent_bridge.complete(request)
    if adapted is not None:
        return adapted
    if ctx is not None:
        # 凭据只按引用取；取不到再走原凭据链；key 不写日志/异常。
        key = (api_key
               or config.pipeline_key(str(ctx.credential_ref or ctx.pipeline))
               or load_api_key())
    else:
        key = api_key or load_api_key()
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
    }
    if thinking is not None:
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
    if reasoning_effort and thinking:
        payload["reasoning_effort"] = str(reasoning_effort)
    if response_format is not None:
        payload["response_format"] = response_format
    if ctx is not None:
        if effective_max > 0:
            payload["max_tokens"] = int(effective_max)
    else:
        config.apply_token_cap(payload, pipeline)
    req = urllib.request.Request(
        f"{(api_base or DEEPSEEK_BASE).rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise ChatError(f"模型接口返回 HTTP {e.code}：{detail}") from None
    except Exception as e:
        raise ChatError(f"调用模型失败：{type(e).__name__}: {e}") from None
    elapsed = time.perf_counter() - t0
    choice = (body.get("choices") or [{}])[0]
    return {
        "content": (choice.get("message") or {}).get("content", ""),
        "model": body.get("model"),
        "usage": body.get("usage") or {},
        "elapsed": elapsed,
        "finish_reason": choice.get("finish_reason") or "",
    }


def stream_chat(messages: list[dict], model: str = CHAT_MODEL,
                max_tokens: int = 2048, thinking: bool = False,
                timeout: int = 300, temperature: float = 0.3,
                reasoning_effort: str | None = None,
                api_key: str | None = None, run_context=None):
    """流式调用，逐段 yield (kind, data)。

    kind: "delta" 文本增量 | "usage" 用量 | "done" 结束

    `reasoning_effort` 与 `chat_once()` 同一口径（见那里的说明）：
    只在 `thinking=True` 时原样透传。

    `api_key`（卡 C-0057 新增，**默认 None＝行为与从前逐字相同**）：
    课件答疑管线有自己的密钥配置（`config.DECK_CHAT_API_KEY`），要给就把
    key 传进来；不传仍走原来的 `load_api_key()` 凭据链，知识库问答不受影响。
    """
    from . import agent_bridge
    # P0-A：有 run_context 时用冻结快照（与 chat_once 同一口径；stream 也覆盖），
    # 不再读 config.token_cap()/当前管线；PIPELINE_RUNTIME=0 走旧全局路径。
    ctx = run_context if (run_context is not None
                          and pipeline_context.runtime_enabled()) else None
    if ctx is not None:
        pipeline = str(ctx.pipeline)
        model = str(ctx.model)
        thinking = bool(ctx.thinking)
        reasoning_effort = ctx.reasoning_effort if thinking else None
        effective_max = int(ctx.max_tokens or 0)
        stream_base = str(ctx.api_base or DEEPSEEK_BASE)
    else:
        pipeline = agent_bridge.current_pipeline()
        cap = config.token_cap(pipeline)
        effective_max = cap if cap > 0 else 0
        stream_base = DEEPSEEK_BASE
    request = agent_bridge.AgentRequest(
        pipeline=pipeline, messages=messages, model=model,
        max_tokens=int(effective_max), thinking=bool(thinking),
        reasoning_effort=reasoning_effort,
        temperature=temperature, timeout=timeout, stream=True)
    events = agent_bridge.stream(request)
    if events is not None:
        saw_usage = saw_done = False
        for event in events:
            if not isinstance(event, (tuple, list)) or len(event) != 2 or event[0] not in ("delta", "usage", "done"):
                raise ChatError("适配器返回了无法识别的流事件")
            kind, data = event
            if kind == "usage":
                saw_usage = True
            if kind == "done":
                if not saw_usage:
                    yield "usage", {}
                saw_done = True
            yield kind, data
            if saw_done:
                break
        if not saw_done:
            if not saw_usage:
                yield "usage", {}
            yield "done", None
        return
    if ctx is not None:
        key = (api_key
               or config.pipeline_key(str(ctx.credential_ref or ctx.pipeline))
               or load_api_key())
    else:
        key = api_key or load_api_key()
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
    }
    if ctx is not None:
        if effective_max > 0:
            payload["max_tokens"] = int(effective_max)
    else:
        config.apply_token_cap(payload, pipeline)
    if thinking is not None:
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
    if reasoning_effort and thinking:
        payload["reasoning_effort"] = str(reasoning_effort)
    req = urllib.request.Request(
        f"{stream_base.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise ChatError(f"模型接口返回 HTTP {e.code}：{detail}") from None
    except Exception as e:
        raise ChatError(f"调用模型失败：{type(e).__name__}: {e}") from None

    usage = {}
    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("usage"):
                usage = obj["usage"]
            for ch in obj.get("choices") or []:
                delta = (ch.get("delta") or {}).get("content")
                if delta:
                    yield "delta", delta
    finally:
        try:
            resp.close()
        except Exception:
            pass
    yield "usage", usage
    yield "done", None


# ================ 课件级 AI 答疑：独立管线（卡 C-0057） ================
#
# 人拍板（2026-09-29 原话）：「每份课件的AI答疑不要和整个知识库的AI问答混用，
# 开一条新的管线用于每份课件的答疑。这条管线不用提示词，思考强度开到最高。」
#
# 这条管线与上面那条（gather_evidence → build_messages → stream_chat）**是两条
# 路**。三条硬区别写在下面；改这条不会碰到那条（`/api/chat/stream` 逐字未变）：
#
#   1. **不检索**：一个检索函数都不调（不 gather_evidence、不 search、
#      不 expand_query），也没有相似度打分、没有命中过滤、没有 _scope_only。
#      整份课件的内容按页序直接进上下文；装不下才按篇幅抽样（保序等距，
#      见 build_deck_context），并如实报告送出多少、一共多少。
#   2. **不用提示词**：不拼 `SYSTEM_PROMPT`，不读 `logs/knowledge/` 下任何
#      提示词文件（那几个 md 是知识点整理管线的资产，与本管线无关），
#      messages 里**没有 system 角色**。消息里唯一的固定文字是"这段是课件、
#      这段是问题"两个方括号标签——那是**消息结构**（否则模型分不清哪段是
#      资料、哪段是提问），不是可维护的提示词模板。逐字清单见 deck_messages()。
#   3. **思考强度最高**：thinking=True + reasoning_effort="max"（默认值，
#      可用 core/config.py 的 DECK_CHAT_* 一组键单独改）。
#
# 配置读取**只有一个入口** `deck_cfg()`：需求 6 的设置窗口接它即可，
# 不要再散落 os.environ / config 直读。

# token 估算口径：与 `core/ingest.py` 写 `chunks.token_est` 用的同一个系数
# （0.75 token/字符），这样库里已有的 token_est 与这里现算的值一致。
TOKEN_PER_CHAR = 0.75
# 计价口径同 `core/kp_build.py` / `core/outline.py`：非高峰 $0.15/1M 入、
# $0.60/1M 出，汇率 7.1。只用于把用量翻译成人能核对的钱，不参与调度。
DECK_PRICE_IN = 0.15
DECK_PRICE_OUT = 0.60
DECK_FX = 7.1


def deck_cost_cny(usage: dict) -> float:
    """把一次调用的 usage 换算成人民币（口径同 kp_build/outline）。"""
    ti = float((usage or {}).get("prompt_tokens") or 0)
    to = float((usage or {}).get("completion_tokens") or 0)
    return round((ti / 1e6 * DECK_PRICE_IN + to / 1e6 * DECK_PRICE_OUT) * DECK_FX, 6)


def deck_cfg() -> dict:
    """课件答疑管线的**唯一配置入口**（需求 6 的设置窗口读这里）。

    每次调用现读 `core.config` 的属性，不在这里缓存——配置改了不用改代码。
    （注意 `.env` 本身只在进程启动时读一次，见 `core/config.py::_load_env`。）
    """
    return {
        "model": config.DECK_CHAT_MODEL,
        # 空字符串＝没单独配，交给 load_api_key() 的老凭据链
        "api_key": config.DECK_CHAT_API_KEY or "",
        "reasoning_effort": config.DECK_CHAT_REASONING_EFFORT,
        "thinking": bool(config.DECK_CHAT_THINKING),
        "max_context_tokens": int(config.DECK_CHAT_MAX_CONTEXT_TOKENS),
        "max_tokens": int(config.DECK_CHAT_MAX_TOKENS),
        "figure_share": float(config.DECK_CHAT_FIGURE_SHARE),
    }


@dataclass
class DeckBlock:
    """进上下文的一段课件内容：正文块或整页图的识别描述。"""
    ref: str                      # p.3 / p.1-2 / slide 5 / para 3（原样来自库）
    text: str
    tokens: int
    figure: bool = False


@dataclass
class DeckContext:
    """一次课件答疑实际送出去的上下文：内容 + 参数 + 截断实况。

    它是**给界面和验证看的自述**（"这次到底送了多少、有没有截断"），
    所以每个字段都必须是真的：算错一个数，人就核对不上。
    """
    doc_id: int = 0
    doc_title: str = ""
    kind: str = ""
    text: str = ""
    chunks_total: int = 0
    chunks_used: int = 0
    figures_total: int = 0
    figures_used: int = 0
    tokens_est: int = 0
    tokens_budget: int = 0
    truncated: bool = False
    strategy: str = ""
    # 本次调用参数（管道自己声明，避免界面各处再猜一遍）
    model: str = CHAT_MODEL
    reasoning_effort: str = "max"
    thinking: bool = True

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d.pop("text", None)          # 正文可能有几万字，别塞进 SSE 事件
        return d


def _tok(text: str) -> int:
    return int(len(text or "") * TOKEN_PER_CHAR)


def _even_fit(items: list, budget: int, cost) -> tuple[list, int]:
    """把 items **保序等距**抽到总花费不超过 budget。

    为什么是等距抽样而不是砍尾巴：课件答疑最典型的用法是"总结这份课件"，
    砍尾巴会让后半份课件永远进不了上下文，而等距抽样对每一页都留代表。
    返回 (留下的 items（保持原顺序）, 丢掉几条)。
    """
    total = sum(cost(x) for x in items)
    if total <= budget or not items:
        return list(items), 0
    n = len(items)
    step = max(1.0, total / max(1, budget))
    for _ in range(64):                      # 有界循环：step 只增，必然收敛
        picked, i = [], 0.0
        while i < n:
            picked.append(items[int(i)])
            i += step
        if sum(cost(x) for x in picked) <= budget:
            return picked, n - len(picked)
        step *= 1.12
    return items[:1], n - 1                  # 兜底：预算小到装不下两块时留第一块


def _body_blocks(conn, doc_id: int) -> list[DeckBlock]:
    """该课件的全部正文块（按文档内顺序 ord）。

    **只按 doc_id 取，不做任何相关度筛选**——这是"不检索"的具体含义。
    """
    rows = conn.execute(
        "SELECT source_ref, text, token_est FROM chunks WHERE doc_id=? "
        "ORDER BY ord", (int(doc_id),)).fetchall()
    out = []
    for r in rows:
        ref = (r["source_ref"] or "").strip()
        text = (r["text"] or "").strip()
        if not text:
            continue
        tok = r["token_est"]
        out.append(DeckBlock(ref=ref, text=text,
                             tokens=int(tok) if tok else _tok(text)))
    return out


def _figure_blocks(conn, doc_id: int) -> list[DeckBlock]:
    """该课件的整页图识别描述（按页序），no_info / 空描述的不算。"""
    rows = conn.execute(
        "SELECT page, description FROM figure_desc WHERE doc_id=? "
        "AND COALESCE(no_info,0)=0 AND COALESCE(description,'')<>'' "
        "ORDER BY page", (int(doc_id),)).fetchall()
    out = []
    for r in rows:
        text = (r["description"] or "").strip()
        if not text:
            continue
        out.append(DeckBlock(ref="p.%s 页图识别" % r["page"], text=text,
                             tokens=_tok(text), figure=True))
    return out


def build_deck_context(conn, doc_id: int, cfg: dict | None = None) -> DeckContext:
    """把**整份课件**装进一个 token 预算里（不检索、不打分、不筛相关性）。

    装法：正文块优先，页图识别描述吃剩余额度（且最多占总预算
    `cfg["figure_share"]`——图注是补充，实测某些课件它的字符数与正文相当，
    不限份额会把正文挤出去）。两类都超预算时各自**保序等距抽样**。

    ⚠ 抽样是**有损**的，所以结果里的 `chunks_total/used`、`truncated`、
    `strategy` 必须原样报到界面上：让人一眼看出"这次答的是全份还是抽样"。
    """
    cfg = cfg or deck_cfg()
    budget = max(1, int(cfg["max_context_tokens"]))
    share = min(max(float(cfg["figure_share"]), 0.0), 1.0)

    row = conn.execute("SELECT title, kind FROM documents WHERE id=?",
                       (int(doc_id),)).fetchone()
    ctx = DeckContext(
        doc_id=int(doc_id),
        doc_title=(row["title"] if row else "") or ("doc#%d" % doc_id),
        kind=(row["kind"] if row else "") or "",
        tokens_budget=budget,
        model=cfg["model"], reasoning_effort=cfg["reasoning_effort"],
        thinking=bool(cfg["thinking"]),
    )

    bodies = _body_blocks(conn, doc_id)
    figs = _figure_blocks(conn, doc_id)
    ctx.chunks_total, ctx.figures_total = len(bodies), len(figs)

    # 图注先分预算，正文吃剩下的（正文是主体）
    fig_budget = int(budget * share)
    figs, _ = _even_fit(figs, fig_budget, lambda b: b.tokens)
    used_fig = sum(b.tokens for b in figs)
    bodies, _ = _even_fit(bodies, max(1, budget - used_fig), lambda b: b.tokens)

    ctx.chunks_used, ctx.figures_used = len(bodies), len(figs)

    # 拼正文：每段前面挂库里的原始定位（p.3 / p.1-2 / slide 5 / para 3），
    # 这样模型引页码时用的是库里的坐标，人也能照着翻。
    segs: list[str] = []
    for b in bodies:
        segs.append(("[%s] %s" % (b.ref, b.text)) if b.ref else b.text)
    head_body = "\n\n".join(segs)
    tail_fig = "\n\n".join("[%s] %s" % (b.ref, b.text) for b in figs)
    ctx.text = (head_body + ("\n\n" + tail_fig if tail_fig else "")).strip()

    ctx.tokens_est = sum(b.tokens for b in bodies) + used_fig
    ctx.truncated = (ctx.chunks_used < ctx.chunks_total
                     or ctx.figures_used < ctx.figures_total)
    if ctx.truncated:
        ctx.strategy = (
            "已截断（保序等距抽样）：正文 %d/%d 块、页图识别 %d/%d 页；"
            "每段前的方括号是库里的页码定位"
            % (ctx.chunks_used, ctx.chunks_total,
               ctx.figures_used, ctx.figures_total))
    else:
        ctx.strategy = ("全量（未截断）：正文 %d 块、页图识别 %d 页"
                        % (ctx.chunks_used, ctx.figures_used))
    return ctx


def deck_messages(ctx: DeckContext, query: str,
                  history: list[dict] | None = None) -> list[dict]:
    """课件答疑的消息体。**没有 system 消息**，也不读任何提示词文件。

    实际发出去的结构**逐字**如下（这是全部固定文字，没有别的东西）：

        messages[0]        role=user   ← 无论有没有历史，最后一条都是它
          【课件内容】《<标题>》（doc#<id>，共 <n> 块）
          ← 未截断时是这个表头
          【课件内容】《<标题>》（doc#<id>，共 <n> 块；为放进上下文，本次按顺序
          等距抽样送出 <m> 块，未送出的块不在下面）
          ← 截断时换成这个（如实说明少了什么，不静默）
          <空行>
          [p.1] <第 1 段原文>
          <空行>
          [p.2] <第 2 段原文>
          …（每个正文段一行方括号定位 + 原文，段间空行）
          …（若有页图识别，接在最后，定位是 [p.<页> 页图识别]）
          <空行>
          【问题】
          <用户的问题>

        messages[1..]      role=user / assistant 交替 ← 只有多轮对话才有，
                           内容是**前几轮的原话与回答**（各截 1500 字），
                           不再重复课件内容（课件只在最后一条里给一次）

    除了 `【课件内容】` / `【问题】` 两个标签和括号里的实况说明，这里
    **没有任何指令性文字**——这正是人说的"这条管线不用提示词"。
    """
    m = ctx
    head = "【课件内容】《%s》（doc#%s，共 %d 块" % (
        m.doc_title, m.doc_id, m.chunks_total)
    if m.truncated:
        head += ("；为放进上下文，本次按顺序等距抽样送出 %d 块正文与 %d 页图识别，"
                 "未送出的块不在下面" % (m.chunks_used, m.figures_used))
    head += "）"

    msgs: list[dict] = []
    for h in (history or [])[-MAX_HISTORY_TURNS * 2:]:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            # 历史里的回答可能很长，截短即可（与 build_messages 同一口径）
            msgs.append({"role": role, "content": content[:1500]})
    msgs.append({"role": "user",
                 "content": "%s\n\n%s\n\n【问题】\n%s" % (head, m.text, query)})
    return msgs


def stream_deck_chat(ctx: DeckContext, query: str,
                     history: list[dict] | None = None,
                     cfg: dict | None = None):
    """课件答疑的流式调用。yield (kind, data)：

        "ctx"   —— 先把本次实际送出的上下文参数如实交出去（界面/验证据此核对）
        "delta" / "usage" / "done" —— 与 stream_chat() 同一口径

    参数全部来自 `deck_cfg()`（可单独配）：模型、密钥、思考强度、输出上限。
    """
    cfg = cfg or deck_cfg()
    yield "ctx", ctx
    msgs = deck_messages(ctx, query, history)
    from . import agent_bridge
    with agent_bridge.pipeline_scope("deck_chat"):
        for kind, data in stream_chat(
                msgs, model=cfg["model"], max_tokens=int(cfg["max_tokens"]),
                thinking=bool(cfg["thinking"]),
                reasoning_effort=cfg["reasoning_effort"],
                api_key=(cfg["api_key"] or None)):
            yield kind, data


# ---------------- 一站式问答（非流式，供 MCP/CLI 用） ----------------
def answer(conn, query: str, per_kind: int = DEFAULT_PER_KIND,
           history: list[dict] | None = None, thinking: bool = False,
           doc_ids: list[int] | None = None) -> dict:
    ev = gather_evidence(conn, query, per_kind=per_kind, doc_ids=doc_ids)
    msgs = build_messages(query, ev, history)
    r = chat_once(msgs, thinking=thinking)
    return {
        "query": query,
        "answer": r["content"],
        "evidence": [e.to_dict() for e in ev],
        "model": r["model"],
        "usage": r["usage"],
        "elapsed": r["elapsed"],
        "no_evidence": not ev,
    }


def demo() -> None:
    """命令行自测：跑一个问题看完整链路。"""
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "阿司匹林的作用机制是什么"
    conn = db.connect()
    db.init_db(conn)
    print(f"问题：{q}\n")
    ev = gather_evidence(conn, q)
    print(f"检索到 {len(ev)} 条证据：")
    for e in ev:
        print(f"  [{e.idx}] {e.label()}  (vec={e.vec_score:.3f} kw={e.kw_score:.2f})")
    print()
    r = answer(conn, q)
    print("回答：")
    print(r["answer"])
    print()
    print(f"模型 {r['model']}  耗时 {r['elapsed']:.1f}s  用量 {r['usage']}")
    conn.close()


if __name__ == "__main__":
    demo()
