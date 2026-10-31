# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-08-9f8540be   (project study_agent; keep this line)
"""整章语境下的图片理解。

## 与"识别文字"的区别

单纯转写文字的价值有限——实测带文字层的导出件里，图上文字 85% 已在库中。
真正不可替代的是**结构、通路、解剖位置、图层关系**，以及**这一页在整章中的位置**。

所以这里做两件事：
1. 先构建**整份资料的骨架**（章节脉络、每页标题、本页正文、前后页标题），
   让模型知道"这一页在讲什么、它是为了说明什么"再去看图。
2. 提示词要求输出**结构理解**而非逐字转写，并强制区分
   "图中明确标注的" 与 "你的推断"。

## 设计要点

- **按页渲染整图**，不逐张发嵌入图：很多"图"是同一图形的组成部件，
  单发会丢整体语义；且调用数从数百降到页数级别。
- **缓存按图片内容哈希**：同一页重复处理不重复计费。
- **失败留占位、不阻塞**：描述失败就在该位置记 [图片未描述]，
  文字照常入库。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import config, db
from .pipeline_runtime import context as runtime_context

# ---------------------------------------------------------------- 提示词

SYSTEM_STYLE_PROMPT = """你在为一个药理学知识库撰写"图示说明"。

**你的读者**：正在复习这门课的学生。他手上有课件原文，但课件里的图
（解剖图、通路图、机制图、表格）他看不懂或记不住。

请结合本页正文，补充文字层没有提取到的图中文字和下列信息：
- 图的**结构与方向**：箭头的指向、通路的走向、层级从属关系、流程的先后
- **空间关系**：某结构在什么位置、与什么相邻、谁覆盖谁
- **对应关系**：表格里哪一列对应哪一列、颜色/线型代表什么
- **与文字的关系**：这张图补充了文字未表达的什么信息
- **化学结构式**：如实记录可辨认的原子、键、环、取代基及连接关系；
  对看不清的键或立体构型标注不确定，不要根据药名猜测或补全。

**必须遵守**：
1. 把"图中确实标注的"与"你的推断"分开写。推断必须标明是推断。
2. 看不清的内容写"[看不清]"，不要猜、不要补全。
3. 如果这页只是装饰性照片，直接写"装饰性图片，无知识点"。
4. **全程用中文思考、用中文输出。**
   推理过程与最终答案都用中文——这份说明是给中文复习资料用的，
   中间夹英文会让下游的检索与阅读都变差。
   （图上的英文标注照抄原文，不要翻译，例如 `Trigger Zone (=Hillock)`。）
"""

OUTPUT_FORMAT = """
请按以下格式输出（每项都要有内容，没有就写"无"）：

【本页作用】这一页在整章中承担什么角色（引入概念/对比/展示机制/给数据/总结）
【图的内容】图里画的是什么（一句话点明类型与主题）
【结构说明】箭头方向、通路走向、层级关系、空间位置——这是重点
【对应关系】表格/图例中各项的对应（没有表格就写"无"）
【图中文字】只列**结构关键处**的标注（如节点名、轴标签、关键数值），
            不要把整页文字抄一遍
【补充信息】这张图补充了正文文字未表达的什么
【不确定处】哪些内容你没看清或属于推断
"""

# ---------------------------------------------------------------- 零文字层课件（卡 C-0066）
#
# ## 为什么另写一套提示词，而不是复用上面那套
#
# 上面那套（SYSTEM_STYLE_PROMPT）的核心指令是
# **"不要逐字转写图上的文字——课件文字层里已经有了，重复没有价值"**，
# 它是为**有文字层**的资料写的。而"图片型课件"恰好相反：实测
# `第五章 药物分布.pptx` 47 页、8.96 MB、`ppt/media` 48 个 PNG、
# 提取结果 **0 块 / 0 字符**——那些字库里一个字都没有。
#
# 对这类资料，直接复用上面那套提示词会把**唯一的信源**丢在模型自己的
# "省字"里，而且**不报错、看不出来**：出来的描述读着挺顺，内容却是空的。
# （本项目已经踩过一次同类坑：识图描述被静默过滤，整份内容消失。）
#
# 所以零文字层走这套：完整转写 + 结构说明 + 表格逐格转写。
ZERO_TEXT_SYSTEM_PROMPT = """你在为一个药理学知识库整理一页没有可提取文字的课件。

**背景（这条决定你该怎么做）**：本页没有提取到文字，页面内容可能来自图片、
矢量图或嵌入对象。本页能否被检索到，取决于你现在写下的内容。

**最要紧的一条：这一页上的文字必须全部转写出来**——标题、正文条目、
列表、表格、公式、图例、坐标轴标签、页脚，一个都不要漏。
⚠ **绝对不要**输出"本页无知识性图示""装饰性图片，无知识点"这类判断：
"这页没有图"在这里**不等于**"这页没有内容"——本页内容可能都在图里；
如果直接说"没内容"，
这一页就在知识库里彻底消失了。
（哪怕这页看起来只是"本章要求""目录""小结""谢谢"，那也是必须转写的正文。）

**另外两件事**：
1. **说明图的结构**：箭头的指向、通路的走向、层级的从属、空间位置、
   颜色或线型代表什么。
2. **说明这一页在整份课件里的位置**：它在引入概念、做对比、
   展示机制、给数据，还是在总结。

**必须遵守**：
1. 把"图里确实写着的"与"你的推断"分开。推断必须明确标注为推断。
2. 看不清的写"[看不清]"，**不要猜、不要补全**。
3. 遇到表格**逐格转写**，并写清**行、列表头分别是什么**
   （哪一列对应哪一列）——表格是提问最密集的内容。
4. 全程用中文思考、用中文输出（图上的英文标注照抄原文，不要翻译）。
"""

# 零文字层课件的**重试提示**：第一次输出被模型判成"这页没图"时补上它。
#
# 为什么必须在代码层兜住（实测，不是理论风险）：2 页冒烟里，
# p1 正常转写了 2322 字，而 p2（章节首页，"本章要求"列表）模型回了
# `【本页无知识性图示】……看图学不到正文之外的药理学知识`，**只写 68 字**，
# 那一页的文字**一个字都没进库**——而它明明有内容。
# 提示词里已经写了"不要输出这类判断"，模型仍会犯；
# 所以判据要落在代码上（`is_no_info` 为真即视为不合格输出并重试），
# 提示词只是把重试这一次说清楚。
# （这里用字面量而不是 `NO_INFO_MARKER`：那个常量定义在本段**之后**，
#   模块加载时取不到。两处的字面量必须一致，改一处就要改两处。）
ZERO_TEXT_RETRY_HINT = """**上一轮你回答了"【本页无知识性图示】"，请重新检查**：
本页没有提取到文字，页面上的内容可能是唯一信源。
请重新看这一页，**把页面上所有文字逐条转写到【页面文字】里**
（标题、列表、表格、页脚、小字注释都要），再写其它小节：
哪怕这页在你看来只是"本章要求""目录"或封面，那些字也必须转写出来、
一个字都不能少。"""


ZERO_TEXT_OUTPUT_FORMAT = """
请按以下格式输出（每项都要有内容，没有就写"无"）：

【本页作用】这一页在整份课件中承担什么角色（引入概念/对比/展示机制/给数据/总结）
【页面文字】把这一页上的文字**完整转写**：标题、每一条正文、表格每一格、
            公式、图例、标注。这是检索的唯一来源，**不要概括、不要省略**。
【图的内容】图里画的是什么（一句话点明类型与主题）
【结构说明】箭头方向、通路走向、层级关系、空间位置——这是重点
【对应关系】表格/图例中各项的对应（没有表格就写"无"）
【补充信息】这一页给出了哪些文字之外的信息
【不确定处】哪些内容你没看清或属于推断
"""

# 「这页的图没有信息量」的**机器可读标记**。
#
# 为什么要做成标记而不是让下游猜：这是**过滤的依据**。
# 一份 78 页的导出件里，纯文字页占了相当一部分——它们的"图"只是
# 模板水印或装饰照片，识图读出来的东西文字层早就有了，等于白花钱。
#
# 为什么让模型来判而不是代码来判：试过三条路，前两条都不行。
#   - 结构法（数 pptx 的 PICTURE 形状）：对 pptx 有效，对 PDF 失效——
#     导出件整页都是图片对象，78 页无一例外。
#   - 像素法（整页 PNG 的边缘密度/色彩数/分块峰值）：pptx 上能分
#     （纯文字页 edge ≤0.013，含图页 ≥0.024），**PDF 上六个指标都分不开**
#     （最大间隙仅 1.38~1.49，两组之间没有空隙）。
#   - 模型判定：它本来就在输出里主动写"装饰性图片，无知识点"，
#     而且判得很准。零额外成本，直接采纳。
NO_INFO_MARKER = "【本页无知识性图示】"

NO_INFO_HINT = f"""
**关于"图没有信息量"的情况**：如果这一页的图只是装饰（背景纹理、照片、
图标、校徽），或者图上的信息在【本页正文文字】里已经全部有了、
看图学不到任何新东西，那么**第一行就写**{NO_INFO_MARKER}
并在后面用一句话说明原因，**不要**再展开下面的七项。
这一条很重要：下游要靠这个标记决定"这页不必做识图"，写错了会浪费成本。
"""


@dataclass
class PageContext:
    """一页的完整上下文，用于让模型在整章语境下看图。"""
    page: int
    title: str = ""
    body: str = ""                     # 本页正文
    prev_titles: list[str] = field(default_factory=list)
    next_titles: list[str] = field(default_factory=list)
    deck_title: str = ""
    deck_outline: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        lines = []
        if self.deck_title:
            lines.append(f"【教材/课件名称】{self.deck_title}")
        if self.deck_outline:
            lines.append("【整份课件的章节脉络】")
            for t in self.deck_outline:
                lines.append(f"  - {t}")
        lines.append("")
        lines.append(f"【当前是第 {self.page} 页】")
        if self.title:
            lines.append(f"【本页标题】{self.title}")
        if self.prev_titles:
            lines.append(f"【上一页】{self.prev_titles[-1]}")
        if self.next_titles:
            lines.append(f"【下一页】{self.next_titles[0]}")
        if self.body:
            # 正文截断，避免占满上下文；图示说明才是重点
            body = self.body if len(self.body) <= 1200 else self.body[:1200] + " …"
            lines.append(f"【本页正文文字】\n{body}")
        return "\n".join(lines)


# ---------------------------------------------------------------- 并发闸门（卡 C-0064）
#
# ## 为什么要并发
#
# 下面 `ingest_document_figures` 的逐页循环是**一次接一次地等模型**：实测中位
# 19.4 秒/页（C-0062 报告从 2696 页历史产物反推），78 页课件光识图就 25 分钟，
# 占人感受到的「一份课件 40 多分钟」的一半。而页与页之间**没有任何数据依赖**
# （各自渲染自己那页、各自描述自己那页），是教科书式的可并行。
# C-0062 的压测实测：4 路拿 3.51×、8 路 6.88×，且**输入 token 逐次全等**
# ——并发不涨成本。
#
# ## 并发放在哪一层（本卡的全部设计都围绕这一句）
#
# **只把"网络等待"放进线程池，写库仍然只有主线程一个写者。**
#
# 具体做法是把一页的处理切成两半：
#   - `_probe_page`：读图 → base64 → 调视觉模型（必要时重试一次空输出）。
#     **纯网络，不碰数据库、不写文件** ⇒ 可以安全地并发。
#   - `_commit` / `_settle`：写 `figure_desc`、写 md、切块、嵌入。
#     **只在主线程跑** ⇒ sqlite 连接不跨线程，写者恒为 1 个。
#
# 为什么不采用"每个 worker 自建连接、各写各的"：
#   1. sqlite3 的连接默认**不允许跨线程**使用；worker 要写库就得自己
#      `db.connect(immediate=True)` 一整套，那就是**第二套写库基建**——
#      本卡明确要求复用既有的、不要另造；
#   2. 写者恒为 1 个时 `database is locked` **在结构上不可能发生**，这比
#      "多写者 + busy_timeout 排队"更容易证明；
#   3. 逐页短事务（卡 C-0040）原样保留：`_commit` / `_settle` 里每条写语句
#      后面立刻 `conn.commit()`，持锁仍是毫秒级，且不随页数增长。
#
# **等价性**：两条路径（串行 / 并发）共用同一对 `_probe_page` / `_commit`，
# 唯一差别是前者在调用栈里跑、后者在线程池里跑。写进库的每个字节因此与
# 改造前同源——`workers=1` 时更是逐字一样的执行序列（安全回退开关）。
#
# ## 分层：识图并发 N、嵌入**不设锁**（C-0062 建议的"嵌入串行 1"实测被否决）
#
# C-0062 报告 §8 建议"识图并发 4、嵌入串行 1"，理由是 onnxruntime 吃满多核。
# 本卡把这条建议实现出来并实测，结论**相反**：加锁反而更慢（32 次单条嵌入：
# 加锁 0.84s/CPU 5.55s vs 不加锁 0.29s/CPU 1.62s；4 份课件并发入库端到端：
# 加锁 9.39s vs 不加锁 6.90s，慢 36%）。原因是本机 ONNX 在"一次一条短文本"
# 这个规模下用不满多核，而识图 worker 只等网络、不吃 CPU。
# 所以**不加嵌入锁**——完整数据与推理见 `_store_figure_chunks` 上方那段注释。
#
# ## 与既有并发基建的关系
#
# `core/pipeline_pool.py` 管的是**多份课件之间**的并发（默认 2、上限 4）。
# 本卡加的是**单份课件内部**的逐页并发，两者是**乘法叠加**：批量 4 份 ×
# 每份 4 页 = 最多 16 路在飞。C-0062 压测里 16 路虽未被限流，但那是 56 次
# 调用的短时边界，不能当生产常态。所以本卡再加一道**进程级闸门**
# （`net_gate()`，默认 8 路）：无论外面套了几层并发，同时打出去的识图请求
# 数不超过 `KB_FIGURE_NET_LIMIT`。

DEFAULT_PAGE_WORKERS = 4
_PAGE_WORKERS_ENV = "KB_FIGURE_WORKERS"
_NET_LIMIT_ENV = "KB_FIGURE_NET_LIMIT"
DEFAULT_NET_LIMIT = 8


def _clamp_workers(n) -> int:
    """把页级并发数夹到 `pipeline_pool` 的 1..MAX_WORKERS。

    **不在这里另写一个上限**：`pipeline_pool.MAX_WORKERS`（=4）是"入库并发"
    这件事在项目里的唯一定义，多份之间与单份内部共用同一个数，改一处同时
    生效，不会出现"批量允许 4、单份允许 8"这种口径分叉。
    """
    from . import pipeline_pool as _pp
    try:
        return int(_pp.clamp_workers(int(n)))
    except (TypeError, ValueError):
        return int(_pp.clamp_workers(_pp.DEFAULT_WORKERS))


def page_workers(n=None) -> int:
    """本次逐页识图用几路并发。

    优先级：显式入参 > 环境变量 `KB_FIGURE_WORKERS` > 默认 4。
    `1` 表示**完全走改造前的串行路径**（安全回退开关）。
    """
    if n is None:
        v = os.environ.get(_PAGE_WORKERS_ENV)
        try:
            n = int(v) if v not in (None, "") else DEFAULT_PAGE_WORKERS
        except (TypeError, ValueError):
            n = DEFAULT_PAGE_WORKERS
    return max(1, _clamp_workers(n))


def _net_limit() -> int:
    try:
        n = int(os.environ.get(_NET_LIMIT_ENV, DEFAULT_NET_LIMIT))
    except (TypeError, ValueError):
        n = DEFAULT_NET_LIMIT
    return max(1, min(64, n))


_gate_lock = threading.Lock()
_net_gate: threading.Semaphore | None = None
_net_gate_size = 0


def net_gate() -> threading.Semaphore:
    """**进程级**识图闸门：同一进程内所有在飞的识图调用共享它。

    它挡的是"乘法叠加"：`pipeline_pool` 的批量 4 份 × 每份逐页 4 路 = 16 路。
    无论外面套了几层并发，同时打出去的识图请求数不超过 `KB_FIGURE_NET_LIMIT`
    （默认 8）。闸门是模块级单例，惰性构造；改了上限会重建。
    """
    global _net_gate, _net_gate_size
    size = _net_limit()
    with _gate_lock:
        if _net_gate is None or _net_gate_size != size:
            _net_gate = threading.Semaphore(size)
            _net_gate_size = size
        return _net_gate


class _Inflight:
    """在飞计数：给验收留"同时打出去几路"的证据（当前值 + 峰值）。"""

    def __init__(self) -> None:
        self._lk = threading.Lock()
        self.now = 0
        self.peak = 0

    def enter(self) -> None:
        with self._lk:
            self.now += 1
            if self.now > self.peak:
                self.peak = self.now

    def exit(self) -> None:
        with self._lk:
            self.now -= 1


# ---- 关于"嵌入串行 1"（C-0062 报告 §8 的建议）：本卡**实测后不采纳** ----
#
# 报告的理由是 `pipeline_pool.py:74` 写的"onnxruntime 内部本来就吃满多核，
# 并发只会互相抢核"。本卡把这条建议真做出来并实测了，结论是**不成立**：
#
#   同一台机器、同一份代码，32 次单条嵌入（与 `_store_figure_chunks` 里完全
#   同一种调用姿势，4 线程）：
#     加锁串行：墙钟 0.84s / 进程 CPU 5.55s
#     放开并发：墙钟 0.29s / 进程 CPU 1.62s
#   端到端（4 份课件同时入库、每份逐页并发 4，挂桩、同一台机器）：
#     加锁 9.39s vs 不加锁 6.90s —— **加锁反而慢 36%**。
#
# 为什么：本机 onnxruntime 在"一次一条短文本"这个规模下用不满多核（每条几十
# 毫秒，线程池唤醒/同步的开销占比很大），真正的并行度只能靠多个调用线程给；
# 而且识图阶段的 CPU 本来就在等网络（逐页并发的 worker 只做 HTTP、不做计算），
# 嵌入并不与谁抢核。
#
# 所以**不加嵌入锁**：少一把全局锁、少一处串行化，逻辑更简单，实测更快。
# 若换机器/换模型后真出现"嵌入抢占识图"，再加闸门也不迟——那时应当按实测
# 重定并发数，而不是照搬一个假设。
#
# 记在这里而不是只写在报告里，是因为下一个人读这段代码时最需要的正是
# "为什么不加锁"这个结论与它的证据。


# ---------------------------------------------------------------- 提取与渲染

def _blank_page_texts(res) -> list[dict]:
    """按 PDF 页数或 PPT 页数补出只有页号的空页语境。

    页数取 `extract` 层已经读出来的 `meta["pages"]`（`extract_pdf` 里就是
    `doc.page_count`），拿不到就退回 `page_count`；两个都拿不到时返回空列表
    ——那种情况下确实是"无页可处理"，由调用方留下可读原因，绝不静默。
    """
    meta = getattr(res, "meta", None) or {}
    n = 0
    for k in ("pages", "page_count", "slides"):
        try:
            n = int(meta.get(k) or 0)
        except (TypeError, ValueError):
            n = 0
        if n:
            break
    return [{"page": p, "title": "", "body": ""} for p in range(1, n + 1)]


INGEST_REUSE_EXTRACT_ENV = "INGEST_REUSE_EXTRACT"


def reuse_extract_enabled() -> bool:
    """S05 开关：默认开；=0 时回退旧"重新 extract_pages"双解析。"""
    return str(os.environ.get(INGEST_REUSE_EXTRACT_ENV, "1")).strip().lower() not in (
        "0", "false", "off", "no")


def _normalize_pre_pages(pre_extracted) -> tuple[str, list[dict]]:
    """把 ingest 已提取的页数据规范化成 extract_pages 的返回值（纯数据、不可变副本）。"""
    if isinstance(pre_extracted, dict):
        title = str(pre_extracted.get("deck_title") or "")
        pages = pre_extracted.get("pages") or []
    else:  # 兼容 (deck_title, pages) 二元组
        title, pages = str(pre_extracted[0] or ""), pre_extracted[1] or []
    out = []
    for rec in pages:
        if not isinstance(rec, dict):
            continue
        try:
            page = int(rec.get("page") or 0)
        except (TypeError, ValueError):
            continue
        if page <= 0:
            continue
        out.append({"page": page, "title": str(rec.get("title") or ""),
                    "body": str(rec.get("body") or "")})
    out.sort(key=lambda x: x["page"])
    return title, out


def extract_pages(src: Path, kind: str = "",
                  fill_empty_pages: bool = False, *,
                  pre_extracted=None) -> tuple[str, list[dict]]:
    """可选 `pre_extracted`：ingest 已提取的页数据（含 OCR 追加文本）。

    传入且开关开启时**不再调用 extract_pdf/extract_pptx**（一次解析）；
    未传入/开关关闭时旧路径逐字不变。失败回退旧路径由调用方兜底。
    """
    """取每页的标题与正文，作为整章语境的素材。

    直接复用 extract 层，保证"语境"与"入库的文字"同源同口径——
    否则语境里说的和库里存的不是一回事，排查问题时会互相矛盾。

    kind: "pptx" | "pdf"，留空按扩展名判断。

    `fill_empty_pages`：对没有提取到文字的 PDF/PPT 页补出仅含页号的语境。

    为什么需要：扫描版/图片型 PDF 的正文字是**本地 OCR** 出来的，而 OCR 结果
    只有上层（`core/ingest.py`）手里有，不经过本函数 ⇒ 这里抽出空列表 ⇒
    `build_contexts` 一页都建不出来 ⇒ `ingest_document_figures` 整份跳过识图，
    而**有文字层的资料却是逐页全送**。补出页号之后，"整页识图"这一步对
    零文字层 PDF 与有文字层 PDF 完全一致——用户要的正是这个一致
    （"我想要 PDF 的入库和 pptx 的入库是一样的"）。

    补出来的记录 `title`/`body` 都是空串，`PageContext` 仍然带着页号、
    课件名与前后页关系，足以让模型"看着这页图说话"。

    默认 **False**：既有调用方（`tools/run_figure_desc.py`、pptx 路径、
    有文字层的 PDF）的行为**逐字不变**——只有"文字层整体为空"这一种情形
    会走补页，且只在调用方显式要求时。
    """
    if pre_extracted is not None and reuse_extract_enabled():
        title, pages = _normalize_pre_pages(pre_extracted)
        return (title or src.stem), pages

    from . import extract as _ex

    kind = kind or ("pdf" if src.suffix.lower() == ".pdf" else "pptx")
    if kind == "pdf":
        res = _ex.extract_pdf(src)
        deck_title = (res.meta.get("title") or "").strip() or src.stem
        # PDF 提取出的 block 带 page，按页归拢
        by_page: dict[int, dict] = {}
        for b in res.blocks:
            if b.page <= 0:
                continue
            rec = by_page.setdefault(b.page, {"page": b.page, "title": "", "body": []})
            if b.kind == "heading" and not rec["title"]:
                rec["title"] = b.text
            else:
                rec["body"].append(b.text)
        # PDF 里很多页没有标题块，用该页首个非空短行当标题，
        # 否则整章脉络会出现大段空白，语境就失效了
        for p, rec in by_page.items():
            if not rec["title"] and rec["body"]:
                first = rec["body"][0].strip()
                if 2 <= len(first) <= 60 and "\n" not in first:
                    rec["title"] = first
        out = [{"page": p, "title": by_page[p]["title"],
                "body": "\n".join(by_page[p]["body"])}
               for p in sorted(by_page)]
        if fill_empty_pages:
            existing = {page["page"] for page in out}
            out.extend(page for page in _blank_page_texts(res) if page["page"] not in existing)
            out.sort(key=lambda page: page["page"])
        return deck_title, out

    res = _ex.extract_pptx(src)
    deck_title = (res.meta.get("title") or src.stem).strip()
    by_page = {}
    for b in res.blocks:
        if b.page <= 0:
            continue
        rec = by_page.setdefault(b.page, {"page": b.page, "title": "", "body": []})
        if b.kind == "heading" and not rec["title"]:
            rec["title"] = b.text
        else:
            rec["body"].append(b.text)
    out = [{"page": p, "title": by_page[p]["title"],
            "body": "\n".join(by_page[p]["body"])}
           for p in sorted(by_page)]
    if fill_empty_pages:
        existing = {page["page"] for page in out}
        out.extend(page for page in _blank_page_texts(res) if page["page"] not in existing)
        out.sort(key=lambda page: page["page"])
    return deck_title, out


# ---------------------------------------------------------------- 无 Office 时的降级（卡 C-0069）
#
# ## 这一节解决什么
#
# pptx 的整页渲染**只能**走本机 PowerPoint 的 COM（见 `render_pages` 的理由），
# 而看板是要分发给别人的——别人机器上很可能**根本没装 Office**。
# 本卡之前，那条路上的失败是一句英文 COM 原文：
#
#     COM 渲染失败: FAIL New-Object : Retrieving the COM class factory for
#     component with CLSID {...} failed due to the following error: 80040154
#
# 用户看不懂，也不知道下一步该干什么。
#
# ## 三条不能破的约定（本卡红线）
#
# 1. **不崩**：检测不到 Office 只影响 pptx 渲染这一条支路；
#    pdf 走 pymupdf、有文字层的资料走解析器，都与 Office 无关，照常可用。
# 2. **不静默**：渲染失败一定在 `status`/`warnings` 里留一句人话，
#    绝不悄悄少一批图注。
# 3. **可操作**：提示必须说清"装什么"或"怎么绕开"。
RENDER_ENV = "KB_RENDER_SCRIPT"

# 装 Office 时 PowerPoint COM 组件注册的 ProgID。**只用来判"这台机器有没有
# PowerPoint"，命不中也不改变任何流程**——调用方拿它选文案，不拿它当开关
# （详见 `render_backend_problem` 文档里"不预防性禁用"那条）。
_PPT_PROGID = "PowerPoint.Application"


def _render_script() -> Path:
    """pptx 渲染脚本的位置。

    `KB_RENDER_SCRIPT` 可覆盖（诊断、或者换一个渲染后端时用）。
    不设时算出来的路径与卡 C-0069 之前**逐字相同**。
    """
    custom = os.environ.get(RENDER_ENV, "").strip()
    if custom:
        return Path(custom).expanduser()
    return (Path(__file__).resolve().parent.parent
            / "tools" / "render_pptx_slides.ps1")


class RenderBackendMissing(RuntimeError):
    """渲染脚本/渲染后端本身就不在这台机器上（不是"这次渲染失败了"）。

    单独一个类型，是为了让调用方能把它与"PowerPoint 偶发拒绝服务"分开：
    前者重试多少次都一样，**立刻失败才对**；后者要重试。
    它是 `RuntimeError` 的子类，所以老调用方（只认 `except Exception`）
    行为逐字不变。
    """


def have_powerpoint() -> bool | None:
    """本机有没有注册 PowerPoint COM。`None` = 判不出来（非 Windows / 注册表读不到）。

    **只看注册表，不真的 `New-Object -ComObject`**：后者会启动一个
    PowerPoint 进程，代价大且可能弹窗。
    """
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, _PPT_PROGID):
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return None


def render_backend_problem(err: str = "") -> str:
    """渲染失败时给用户的**可操作**说明。卡 C-0069 的交付物就是这一段话。

    ## 为什么不用 `have_powerpoint()` 做"预防性禁用"

    想过"没装 PowerPoint 就干脆不渲染、直接给提示"，**放弃了**：
    `New-Object -ComObject PowerPoint.Application` 在不少机器上能起来
    （WPS 也注册了这个 ProgID、Office 装在别处、注册表被权限挡住读不到），
    预防性禁用会把**本来能用的机器**变成不能用。所以判据只用来**选文案**：
    渲染是真的失败了，再据此告诉用户"你为什么失败、下一步做什么"。
    """
    no_ppt = have_powerpoint() is False
    if "渲染脚本不存在" in err:
        why = ("这台机器上没有找到 pptx 渲染脚本"
               "（安装包不完整，tools/render_pptx_slides.ps1 缺失）")
    elif no_ppt:
        why = "这台机器上**没有装 Microsoft PowerPoint**"
    else:
        why = ("pptx 整页渲染失败（这台机器上读到了 PowerPoint 的注册信息，"
               "所以也可能是 PowerPoint 正被别的程序占用、弹了对话框，"
               "或者是 WPS 之类的替代组件不支持自动化）")
    # 单行输出：这句话会被存进 `documents.warnings`（JSON 数组，看板逐条显示），
    # 里面带换行只会把展示弄乱。三条路用 "1) 2) 3)" 排在后面即可。
    return (f"{why}。pptx 的整页图**只能**由本机 PowerPoint 导出（要的是"
            "字体、图层、SmartArt 与原件一致）。三条路任选一条："
            "1) 在这台机器上装好 Microsoft Office/PowerPoint 后重新入库；"
            "2) 用 PowerPoint 把这份课件**另存为 PDF**，再放进 raw 重新入库"
            "（PDF 渲染走内置引擎，完全不需要 Office）；"
            "3) 如果这份资料本身有文字层，正文照常入库、只有图注会缺，"
            "装好 Office 后再重新入库即可补齐。")


def render_pages(src: Path, outdir: Path, kind: str = "",
                 width: int = 1600, dpi: int | None = None,
                 pages: list[int] | None = None) -> dict[int, Path]:
    """把资料渲染成整页 PNG，返回 {页号: 图片路径}。

    为什么渲染整页而不是逐张取嵌入图：
    - 很多"图"是同一图形的组成部件（多面板图、图表元素），单发会丢整体语义
    - 调用数从数百降到页数级别
    - 页面上文字与图的相对位置本身就带信息

    pptx 走 PowerPoint COM（忠实原样：字体/图层/SmartArt 都保留）；
    pdf 走 pymupdf 渲染——**这两个后端互不相干**，所以没装 Office 时
    只有 pptx 那条路受影响（卡 C-0069 的红线）。
    """
    kind = kind or ("pdf" if src.suffix.lower() == ".pdf" else "pptx")
    if pages is not None:
        pages = [int(p) for p in pages]
        if not pages:
            return {}                    # 显式 [] = 不导出（绝不退化为全部）
    outdir.mkdir(parents=True, exist_ok=True)
    from . import progress as _pg

    if kind == "pdf":
        import pymupdf
        from PIL import Image
        dpi = dpi or getattr(config, "FIGURE_RENDER_DPI", 110)
        doc = pymupdf.open(str(src))
        want = (list(range(1, doc.page_count + 1)) if pages is None else pages)
        result: dict[int, Path] = {}
        for i, p in enumerate(want, start=1):
            if p < 1 or p > doc.page_count:
                continue
            # 渲染是识图阶段里第一段耗时（几十页要几十秒），
            # 不报进度的话进度条会一直停在阶段起点不动。
            _pg.tick(i, total=len(want), note=f"渲染整页图 {i}/{len(want)}")
            pix = doc.load_page(p - 1).get_pixmap(dpi=dpi)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            f = outdir / f"slide_{p:02d}.png"
            im.save(f, "PNG")
            result[p] = f
        doc.close()
        return result

    # pptx：调 PowerShell COM 脚本。
    # 这一步**没法报中间进度**——脚本一次性把整份课件导出成 PNG，
    # 中间不回调（实测 46 页约 25 秒）。所以至少说清楚在干什么，
    # 否则进度条停在这里会让人以为卡死了。
    #
    # ★ 脚本先做**存在性检查**（卡 C-0069）：不存在时立刻抛
    # `RenderBackendMissing`，不再白开一次 PowerShell——更关键的是，
    # 零文字层通道对 pptx 有"失败重试 4 次"的兜底（`RENDER_TRIES`），
    # 没有这个类型的话，"这台机器没装渲染脚本"会被重试 4 遍、白等几十秒。
    script = _render_script()
    if not script.is_file():
        raise RenderBackendMissing(f"渲染脚本不存在: {script}")
    import subprocess
    _pg.tick(0, note="导出整页图（PowerPoint 渲染中，几十秒）")
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(script), "-Pptx", str(src), "-Out", str(outdir),
           "-Width", str(width)]
    if pages is not None:
        cmd += ["-Pages", ",".join(str(p) for p in pages)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if "OK" not in out:
        raise RuntimeError(f"COM 渲染失败: {out[:300]}")
    return {int(p.stem.split("_")[1]): p
            for p in sorted(outdir.glob("slide_*.png"))}


FIGURE_CACHE_V2_ENV = "FIGURE_CACHE_V2"
RENDER_CACHE_META = ".render_cache.json"
RENDER_BACKEND_VERSION = "render-v2"


def figure_cache_v2_enabled() -> bool:
    return str(os.environ.get(FIGURE_CACHE_V2_ENV, "1")).strip().lower() not in (
        "0", "false", "off", "no")


def _src_fingerprint(src: Path) -> str:
    try:
        import hashlib as _h
        return _h.sha256(src.read_bytes()).hexdigest()[:32]
    except OSError:
        return ""


def _render_meta_path(outdir: Path) -> Path:
    return Path(outdir) / RENDER_CACHE_META


def _read_render_meta(outdir: Path) -> dict:
    try:
        raw = _render_meta_path(outdir).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_render_meta(outdir: Path, meta: dict) -> None:
    try:
        _render_meta_path(outdir).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def render_pages_cached(src: Path, outdir: Path, kind: str = "",
                        width: int = 1600, dpi: int | None = None,
                        pages: list[int] | None = None) -> tuple[dict, dict]:
    """页图缓存：整页缓存命中 0 次渲染；文件/后端/尺寸变化或缺页才补渲染。

    返回 `({页号: 图片路径}, {"cache_hit": bool, "rendered": [...], "reused": n})`。
    旧目录没有 manifest → 按旧行为渲染（不删除任何旧页图）。
    """
    kind = kind or ("pdf" if src.suffix.lower() == ".pdf" else "pptx")
    dpi_eff = (dpi if dpi is not None else
               (getattr(config, "FIGURE_RENDER_DPI", 110) if kind == "pdf" else 0))
    if not figure_cache_v2_enabled():
        slides = render_pages(src, outdir, kind=kind, width=width, dpi=dpi,
                              pages=pages)
        return slides, {"cache_hit": False, "rendered": sorted(slides),
                        "reused": 0, "mode": "legacy"}
    fp = _src_fingerprint(src)
    meta = _read_render_meta(outdir)
    same = bool(fp and meta.get("src_fingerprint") == fp
                and meta.get("backend") == RENDER_BACKEND_VERSION
                and str(meta.get("kind")) == kind
                and int(meta.get("width") or 0) == int(width)
                and int(meta.get("dpi") or 0) == int(dpi_eff))
    cached: dict[int, Path] = {}
    if same:
        for key, rel in (meta.get("pages") or {}).items():
            try:
                page = int(key)
            except (TypeError, ValueError):
                continue
            path = Path(outdir) / str(rel)
            if path.is_file():
                cached[page] = path
    want = pages
    need = (sorted(cached) if want is None else [p for p in want
                                                 if int(p) > 0 and int(p) not in cached])
    if want is None and same and cached:
        need = []
    slides = dict(cached)
    rendered: list[int] = []
    if need or not cached:
        fresh = render_pages(src, outdir, kind=kind, width=width, dpi=dpi,
                             pages=(want if want is not None else None)
                             if not cached else need)
        slides.update(fresh)
        rendered = sorted(fresh)
    pages_map = {str(p): Path(path).name for p, path in sorted(slides.items())}
    _write_render_meta(outdir, {
        "src_fingerprint": fp, "backend": RENDER_BACKEND_VERSION, "kind": kind,
        "width": int(width), "dpi": int(dpi_eff), "pages": pages_map,
        "adopted_legacy": bool(meta and not same)})
    if pages is not None:
        slides = {p: slides[p] for p in pages if p in slides}
    return slides, {"cache_hit": bool(cached and not rendered),
                    "rendered": rendered, "reused": len(cached),
                    "mode": "v2"}


VISUAL_CACHE_VERSION = "visual-v2"


def visual_cache_key(ctx, image_hash: str, *, thinking: bool = True,
                     model: str = "", api_base: str = "",
                     reasoning_effort: str = "", response_format: str = "json_object",
                     prompt_version: str = VISUAL_CACHE_VERSION) -> str:
    """视觉缓存扩展键：页图 hash + 实际页上下文(含 OCR 文本) + 提示词/模型/推理设置/输出格式。"""
    import hashlib as _h
    try:
        prompt = ctx.to_prompt() if hasattr(ctx, "to_prompt") else str(ctx)
    except Exception:  # noqa: BLE001
        prompt = str(ctx)
    raw = "\n".join([
        str(prompt_version), str(image_hash), prompt,
        "thinking=%s" % bool(thinking),
        "model=%s" % str(model), "base=%s" % str(api_base),
        "effort=%s" % str(reasoning_effort),
        "format=%s" % str(response_format),
    ])
    return _h.sha256(raw.encode("utf-8")).hexdigest()


def _visual_meta_path(outdir: Path, page: int) -> Path:
    return Path(outdir) / ("slide_%02d.visual.json" % int(page))


def write_visual_meta(outdir: Path, page: int, key: str) -> None:
    try:
        _visual_meta_path(outdir, page).write_text(
            json.dumps({"version": VISUAL_CACHE_VERSION, "key": key},
                       ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def visual_cache_state(outdir: Path, page: int, key: str) -> str:
    """'match' / 'mismatch' / 'legacy'（旧缓存没有元数据 → 继续按旧规则读）。"""
    path = _visual_meta_path(outdir, page)
    if not path.exists():
        return "legacy"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        old = str((data or {}).get("key") or "")
    except (OSError, ValueError):
        return "legacy"
    return "match" if old == key else "mismatch"


def _fig_skip(stats: dict, reason: str) -> None:
    """记一条"整页识图被跳过"的可读原因（卡 C-0122）。

    为什么必须写两处：

    - `stats["skipped_reason"]`：本卡新增的**只增**字段（老键一个不动），
      看板与入库报告可以直接读，一眼看出"这一轮的 0 页是为什么"；
    - `stats["errors"]`：既有出口。`core/ingest.py` 会把它的内容拼进入库报告的
      `warnings`（"识图失败 N 页: …"），用户才**真的看得到**。不占这个通道的话，
      `skipped_reason` 只躺在统计字典里——那还是静默。

    ⚠ 只在"真的没有一页可识图"时调用。正常路径（有页可处理）一条都不加，
    入库报告的 `warnings` 因此与改造前逐字相同。
    """
    if not stats.get("skipped_reason"):
        stats["skipped_reason"] = reason
    stats["errors"].append(reason)


def ingest_document_figures(conn, doc_id: int, path: Path, kind: str = "",
                            *, thinking: bool = True,
                            only_with_images: bool = True,
                            max_calls: int | None = None,
                            verbose: bool = False,
                            workers: int | None = None,
                            store_chunks: bool = True,
                            pre_extracted=None, run_context=None) -> dict:
    """**完整识图管线**：渲染 → 逐页描述 → 写入库（含向量）。

    这是"入库"的一部分，由 `core.ingest.ingest_file` 调用，
    不再需要手工跑三个脚本。

    ## 顺序（每一步都有理由）

    1. **先渲染整页图**。判"这页有没有信息"必须先有图；
       而渲染很便宜，可以全渲染再过滤。
    2. **pptx 按形状过滤**：`page_has_image` 免费且准，实测能省约六成调用。
       PDF 没有可靠判据（导出件整页都是图片对象），返回 None，不假装过滤。
    3. **有描述就复用**：按 `(doc_id, page, image_hash)` 查 `figure_desc`。
       图片内容没变就不重复计费——重跑一次入库不该再花一遍钱。
    4. **逐页调视觉模型**（整章语境 + 推理模式）。卡 C-0064 起这一步**并发**，
       见文件上方「并发闸门」那段：网络在线程池里跑，写库仍在主线程。
    5. **写入 `chunks` + `embeddings`**，按【】小节切块，
       每块带"图示说明"与 `p.N 图` 标记（与正文块可区分）。
       「本页无知识性图示」的页**不入库**——文字层已覆盖，存进去只占检索名额。

     `workers`：逐页并发数，默认取 `page_workers()`（环境变量
     `KB_FIGURE_WORKERS`，上限与 `pipeline_pool.MAX_WORKERS` 同口径）。
     **`1` = 改造前的串行行为**（逐字回退）。

     `store_chunks`（**2026-10-19 新增，默认 True = 旧行为逐字不变**）：
     False 时**只识别/复用描述、写 `figure_desc` 缓存与 md，不建独立识图块**
     （不写 `chunks`/`embeddings`、不改 `n_chunks`、不跑 stale 清理）；
     供 `core/ingest.py` 的「整页切块」模式把描述并进**每页那一块**，
     避免同一页内容在库里出现两份。识图页过滤（`only_with_images` +
     `page_has_image`，仅 pptx 有可靠判据）与缓存复用逻辑完全不变。

    返回统计字典（供入库报告汇总）。除了老字段（pages/described/reused/
    no_info/chunks/cost_cny/errors/stale_dropped），本卡新增三个**只增不改**
    的观测字段：`workers`（实际并发数）、`net_limit`（进程级闸门）、
    `peak_inflight`（实测同时在飞峰值）——供验收核对"有没有真的并发"。
    """
    from . import db

    # ★ 先把上游的未决事务落盘（卡 C-0040）。
    #
    # 调用方 `core/ingest.py::ingest_file` 刚做过 `clear_document_figures` 的
    # DELETE。若不在这里提交，这个写事务会跨过下面的整份解析 + **整页渲染**
    # （实测数十秒），把 SQLite 的写锁白占住；并发的另一个 worker 只要在这段
    # 窗口里写库，就会等到 `busy_timeout` 耗尽后抛 `database is locked`。
    conn.commit()

    # P0-F：视觉入库入口只建一次 context；PIPELINE_RUNTIME=0 不建。
    run_ctx = (run_context if run_context is not None
               else (runtime_context.make_context("ingest_vision", doc_id=doc_id)
                     if runtime_context.runtime_enabled() else None))
    kind = kind or ("pdf" if path.suffix.lower() == ".pdf" else "pptx")
    stats = {"pages": 0, "described": 0, "reused": 0, "no_info": 0,
             "chunks": 0, "cost_cny": 0.0, "errors": [],
             "workers": 0, "net_limit": 0, "peak_inflight": 0,
             # ★ 只增字段（卡 C-0122）：这一轮为什么一页都没识图。
             # 空串 = 正常跑过（或页数为 0 属于正常）；非空 = 一句人话的原因。
             "skipped_reason": ""}

    # S05：有 ingest 已提取页数据时不再二次 extract；OCR 追加文本已包含在页 body 里。
    deck_title, stexts = extract_pages(path, kind, fill_empty_pages=True,
                                       pre_extracted=pre_extracted)
    stats["extract_reused"] = bool(pre_extracted is not None and reuse_extract_enabled())
    ctxs = build_contexts(stexts, deck_title)
    if not ctxs:
        # ★ 不静默（卡 C-0122 修的就是这一处）。
        #
        # 改造前这里是一句光秃秃的 `return stats`：返回全 0 统计、`errors` 为空、
        # **一条 warning 都没有**。调用方（`core/ingest.py`）因此无从知道
        # "这份资料的整页识图一页都没跑"——用户看到的是一份入库成功、却没有
        # 任何图注的资料，且没有任何线索指向原因。C-0121 实测到的正是它：
        # 零文字层 PDF 识图 0 次（静默），有文字层 PDF 逐页全送。
        #
        # 上面的 `fill_empty_pages=True` 已经堵住了"零文字层 PDF 拿不到页语境"
        # 这条主路；走到这里只剩一种情形：**连页数都读不出来**
        # （文件坏 / 解析器不认 / 页数为 0）。那确实无页可处理，
        # 但必须说清为什么、下一步干什么。
        _fig_skip(stats, (
            f"没有一页可识图：读不出这份资料的任何一页（kind={kind}，"
            f"文字层与页数都取不到），整页识图无从下手——这一轮 0 页被送进"
            f"视觉模型。若这是扫描/图片型资料，装好本地 OCR"
            f"（pip install rapidocr-onnxruntime）后重跑；否则先用阅读器"
            f"确认文件没坏。"))
        return stats

    # `config.LOG_DIR` 而不是 `config.ROOT / "logs"`（卡 C-0039）：
    # 默认学科下两者**逐字相同**；切到别的学科后 LOG_DIR 指向该学科的 logs/，
    # 渲染出来的页图才不会串库。
    outdir = config.LOG_DIR / "figure_desc" / _safe_dirname(path.stem)
    # 把上一轮的整页图搬进本轮目录，再渲染。
    #
    # 为什么必须搬：复用判定是"库里的 image_hash == 磁盘上这张图的哈希"，
    # 而**上一轮的图在另一个目录里**（早期是 `logs/<名字>_pages`，
    # 由 run_figure_desc.py 渲染）。目录不同 → 磁盘上没图 → 哈希永远对不上 →
    # **复用从未生效**，每次重导入都重新调一遍视觉模型。
    # 实测 doc#60 因此白跑了 8 页（¥0.1656），doc#89 那 78 页要 ¥1.3345。
    #
    # 搬过来之后哈希即可对上：同一份 PDF 用同一 dpi 渲染是确定性的。
    _adopt_previous_renders(path, outdir)

    try:
        slides, render_info = render_pages_cached(path, outdir, kind=kind)
        stats["render_cache"] = render_info
    except Exception as e:  # noqa: BLE001
        # ★ 不静默（卡 C-0069）：渲染失败 = 这一轮的**图注全缺**，
        # 用户必须知道为什么、以及怎么办。`stats["errors"]` 会被
        # `core/ingest.py` 收进入库报告的 `warnings`（"识图失败 N 页: …"）。
        # 正文照常入库——图注是补充，不是这份资料的正文。
        stats["errors"].append(
            f"整页渲染失败: {type(e).__name__}: {e}；"
            + render_backend_problem(f"{type(e).__name__}: {e}"))
        return stats

    want = sorted(slides)
    if only_with_images:
        hm = page_has_image(path, kind)
        if hm:                      # PDF 返回 None，此时不过滤
            want = [p for p in want if hm.get(p, True)]
    if max_calls:
        want = want[:max_calls]
    stats["pages"] = len(want)
    if not want:
        # 渲染"成功"却一页图都没产出：同样是"0 页识图"，同样不许静默（卡 C-0122）。
        # 这里**不 return**：继续往下走，空循环之后仍会执行
        # `_drop_stale_figure_chunks`，与改造前的执行路径逐字一致，
        # 只是多留一句人话的原因。
        _fig_skip(stats, (
            f"没有一页可识图：整页渲染没有产出任何页图（kind={kind}，"
            f"渲染目录 {outdir}），这一轮 0 页被送进视觉模型。"
            f"pdf 走 pymupdf 渲染、pptx 走本机 PowerPoint——"
            f"若是 pptx，先确认这台机器装了 Office（见 render_backend_problem() "
            f"给的两条绕开办法）。"))
    # 渲染完了，接下来是逐页识图（最慢的一段）。这里先把"要跑几页"报出去，
    # 后面的 tick 才有分母；一页都不用跑（全部复用）时也让人一眼看到。
    from . import progress as _pg0
    _pg0.tick(0, total=len(want),
              note=(f"待识图 {len(want)} 页" if want else "无需识图（全部命中缓存）"))

    table = _figure_table(conn, doc_id)
    now = db.now()
    handled: set[int] = set()
    usable_pages: set[int] = set()

    # ---- 逐页规划（主线程串行读库，不做任何模型调用）----
    #
    # 复用判定集中在这一步，是因为它必须在**提交识图任务之前**定下来：
    # 一旦同一页被提交两次，模型就会被调两次——这正是本卡最不能出现的回归
    # （重复计费）。规划在主线程串行做，worker 只拿"已经确定要识图"的页，
    # 所以并发下不存在"两路同时判同一页"的竞态，`reused` 计数也不会被并发搅乱。
    plan: list[dict] = []
    for p in want:
        img = slides.get(p)
        ctx = ctxs.get(p)
        if not img or not ctx:
            plan.append({"page": p, "kind": "skip"})
            continue
        try:
            ihash = image_hash(img)
        except Exception:  # noqa: BLE001
            plan.append({"page": p, "kind": "skip"})
            continue
        # 图片没变、且描述正文还完整 → 直接复用，不重复计费
        row = table.get(p)
        textless = not (ctx.title or "").strip() and not (ctx.body or "").strip()
        cached_body = (row or {}).get("description") or ""
        cache_ok = len(cached_body.strip()) >= 30 or (not textless and is_no_info(cached_body))
        vkey = visual_cache_key(
            ctx, ihash, thinking=(bool(run_ctx.thinking) if run_ctx is not None else thinking),
            model=(str(run_ctx.model) if run_ctx is not None else ""),
            api_base=(str(run_ctx.api_base) if run_ctx is not None else ""),
            reasoning_effort=(str(run_ctx.reasoning_effort) if run_ctx is not None else ""))
        vstate = visual_cache_state(outdir, p, vkey)
        if (row and row["image_hash"] == ihash and cache_ok
                and not (textless and is_no_info(cached_body))
                and vstate != "mismatch"):
            plan.append({"page": p, "kind": "reuse", "img": img, "ctx": ctx,
                         "ihash": ihash,
                         "title": row.get("page_title") or ctx.title,
                         "body": row["description"]})
        else:
            plan.append({"page": p, "kind": "describe", "img": img, "ctx": ctx,
                         "ihash": ihash, "title": ctx.title,
                         "visual_key": vkey})

    done_n = 0

    def _tick() -> None:
        # 报页级进度给看板的进度条（识图是整条管线里最慢的一步：
        # 实测约 25 秒/页，一份 26 页课件要十几分钟，没有进度就没法等）。
        # ⚠ `from . import progress` **必须在函数内**：批量入库时
        # `pipeline_pool.install()` 换掉的正是 `core` 包上的这个名字，
        # 顶层导入会把它钉死在真模块上，并发批量的进度就串台了。
        nonlocal done_n
        from . import progress as _pg
        done_n += 1
        _pg.tick(done_n, total=len(want),
                 note=f"第 {done_n}/{len(want)} 页"
                      + (f"（已复用 {stats['reused']} 页）" if stats["reused"] else ""))

    def _commit(item: dict, r: FigureResult, r2: FigureResult | None) -> str:
        """把一页的识图结果落库，返回最终采用的描述正文。

        **只在主线程调用**（写库路径不跨线程）。它是"识图之后"的全部动作，
        与改造前 `else:` 分支里的语句逐句对应——顺序、提交点、md 写入时机
        都没动，所以并发版的产物与串行版同源。
        """
        if not r.ok:
            stats["errors"].append(f"p{item['page']}: {r.error[:80]}")
            return ""
        p = item["page"]
        body = r.text or ""
        title = item["ctx"].title
        item["title"] = title
        stats["described"] += 1
        stats["cost_cny"] += r.cost_cny
        textless = not (item["ctx"].title or "").strip() and not (item["ctx"].body or "").strip()

        def invalid(text):
            return len((text or "").strip()) < 30 or (textless and is_no_info(text or ""))

        chosen = r
        if invalid(body):
            stats["empty_retried"] = stats.get("empty_retried", 0) + 1
            if r2 is not None:
                stats["cost_cny"] += r2.cost_cny
            if r2 is not None and r2.ok and not invalid(r2.text):
                chosen = r2
                body = r2.text
            else:
                stats.setdefault("still_empty", []).append(p)
                stats["errors"].append(f"p{p}: 识图没有返回可用内容，请重试")
                return ""
        # Only reusable, validated responses go into the cache.
        _save_figure_row(conn, doc_id, p, title, item["ihash"], chosen, now)
        conn.commit()
        if item.get("visual_key"):
            write_visual_meta(outdir, p, item["visual_key"])
        (outdir / f"slide_{p:02d}.md").write_text(
            f"# p{p} · {title}\n\n{body}", encoding="utf-8")
        if verbose:
            print(f"      识图 p{p}: {len(body)} 字 ¥{chosen.cost_cny:.4f} {chosen.elapsed:.0f}s", flush=True)
        return body

    def _settle(item: dict, body: str) -> None:
        """一页的**写库尾段**（复用页与识图页走同一条路）。

        **只在主线程调用。** 每页末尾都 `conn.commit()`，事务粒度仍是"单页"。
        """
        p = item["page"]
        if not body.strip():
            conn.commit()          # 本页未决写就此落盘，见下方循环末尾的说明
            return
        if is_no_info(body):
            _mark_no_info(conn, doc_id, p)
            stats["no_info"] += 1
            conn.commit()          # 同上：不留未决事务
            return

        usable_pages.add(p)

        if not store_chunks:
            # 整页切块模式（core/ingest.py::ingest_file）：描述正文只留在
            # figure_desc 做缓存，由调用方把它并进「每页一块」的正文块，
            # **不单独建 `p.N 图` 识图块**（否则同一页内容库里会有两份）。
            stats["merged_only"] = stats.get("merged_only", 0) + 1
            conn.commit()
            return

        n = _store_figure_chunks(conn, doc_id, deck_title, p, item["title"],
                                 body, now)
        stats["chunks"] += n
        _set_figure_chunks(conn, doc_id, p, item["ihash"], n)
        handled.add(p)
        # ★ 每页一个**短事务**（卡 C-0040）。
        #
        # 原实现只在整个循环结束后 commit 一次，而循环体每轮要调一次视觉模型
        # （实测约 25 秒/页）。于是这一个写事务持有写锁 100~240 秒，远超
        # `busy_timeout`（30 秒）——并发的另一个 worker 在这段窗口里写库必然
        # 等到超时后拿到 `database is locked`。用户的 16 份课件正是这样 5 份
        # 全败，且失败前每份都已经烧掉 134~243 秒的识图费用。
        # 逐页提交后单次持锁降到毫秒级。
        #
        # 代价与等价性：事务粒度由"整份"变成"单页"。中断时已识别的页会留在库
        # 里——这不是损失而是收益：`_save_figure_row` 本来就是 upsert，重跑时
        # 按 `image_hash` 命中缓存、不重复付费。而"先 clear 后 ingest"的旧数据
        # 丢失风险并未增加：失败路径上 `_write_chunks` 会把该文档的块全部删掉
        # 重建，旧识图块本来也留不下。
        #
        # 卡 C-0064 并发化之后这一条**一个字都不用改**：写库仍在主线程串行，
        # 只是每次提交之间隔着的是"别的页在网络等待"，而不是自己那页的。
        conn.commit()

    _W = page_workers(workers)
    stats["workers"] = _W
    stats["net_limit"] = _net_limit()
    gate = net_gate()
    inflight = _Inflight()

    if _W <= 1:
        # ---- 串行路径：与改造前**逐字同序**（`workers=1` 是安全回退开关）----
        for item in plan:
            _tick()
            if item["kind"] == "skip":
                continue
            if item["kind"] == "reuse":
                stats["reused"] += 1
                _settle(item, item["body"])
                continue
            r, r2 = _probe_gated(item["img"], item["ctx"], thinking,
                                 gate, inflight, run_context=run_ctx)
            _settle(item, _commit(item, r, r2))
    else:
        # ---- 并发路径 ----
        #
        # 只有 `_probe_page`（纯网络）进线程池；`_commit` / `_settle` 全在
        # 主线程 ⇒ sqlite 连接不跨线程、**写者恒为 1 个**。
        # `as_completed` 按**完成顺序**收结果：哪页先识完就先落库，所以中断时
        # 已完成的页尽可能多（逐页短事务的意义正在这里）。
        ex = ThreadPoolExecutor(max_workers=_W, thread_name_prefix="fig-page")
        try:
            # 复用页与跳过页不需要模型，当场处理（与串行路径共用同一段代码）
            for item in plan:
                if item["kind"] == "describe":
                    continue
                _tick()
                if item["kind"] == "reuse":
                    stats["reused"] += 1
                    _settle(item, item["body"])
            futs = {ex.submit(_probe_gated, item["img"], item["ctx"], thinking,
                              gate, inflight, run_context=run_ctx): item
                    for item in plan if item["kind"] == "describe"}
            for fut in as_completed(futs):
                item = futs[fut]
                _tick()
                try:
                    r, r2 = fut.result()
                except Exception as e:  # noqa: BLE001
                    stats["errors"].append(
                        f"p{item['page']}: {type(e).__name__}: {e}")
                    continue
                _settle(item, _commit(item, r, r2))
        finally:
            # 中断 / 异常时不再等在飞的页：它们只是网络调用、**不写库**
            # （写库只在主线程），放弃它们无损；已识完的页早已逐页提交在库里
            # ——这正是本卡要保住的中断安全。
            ex.shutdown(wait=False, cancel_futures=True)

    stats["peak_inflight"] = inflight.peak
    stats["usable_pages"] = sorted(usable_pages)
    # ★ 兜底不静默（卡 C-0122）：有页要处理、却一页都没落地，且没有任何一条
    # 错误 —— 说明它们全在"规划"阶段被判成了 skip（页图或页语境取不到）。
    # 这也是"整页识图没干活"，同样必须留下原因。
    if (want and not stats["described"] and not stats["reused"]
            and not stats["no_info"] and not stats["errors"]):
        _fig_skip(stats, (
            f"没有一页被识图：{len(want)} 页在规划阶段全部被判为不可处理"
            f"（页图或页语境取不到），0 页被送进视觉模型。"))
    # 本轮没处理、但库里还留着块的页（课件改版后那页没图了）→ 清掉。
    # `store_chunks=False` 时本次一个识图块都不写，也就没有"哪些页处理过"
    # 可言；调用方（整页切块）会用 `_write_chunks` 整体重建该文档的块。
    stats["stale_dropped"] = (
        _drop_stale_figure_chunks(conn, doc_id, handled) if store_chunks else 0)
    conn.commit()
    return stats


# ---------------------------------------------------------------- 零文字层课件（卡 C-0066）

# 整页渲染的重试次数与间隔。**只给零文字层通道用**（见下）。
RENDER_TRIES = 4
RENDER_WAIT = 8.0


def _render_pages_with_retry(path: Path, outdir: Path, kind: str,
                             tries: int = RENDER_TRIES,
                             wait: float = RENDER_WAIT
                             ) -> tuple[dict[int, Path], str]:
    """渲染整页图，失败就重试几次。返回 `({页号: 路径}, 最后一次的错误)`。

    ## 为什么要重试（实测，不是理论风险）

    PowerPoint/Office 的 COM 自动化是**单例**：同一用户会话里
    `New-Object -ComObject PowerPoint.Application` 拿到的是**同一个实例**。
    卡 C-0065 的"旧版 Office 自动转换"走的就是这条 COM（它自己的注释里
    已经写明"多线程同时 `Presentations.Open` 会撞出 *Call was rejected by
    callee*"）。两件事同时发生时，本函数会间歇性失败：

        实测 `COM 渲染失败: FAIL Presentations.Open : Failed.`

    —— 同一份文件、同一条命令，在对方空闲时 100% 成功（47 张页图已落盘）。

    对**既有识图路径**（`ingest_document_figures`）这种偶发失败只是"少一页
    图注"；但对**零文字层课件**，渲染失败就是**整份资料一个字都进不去**。
    所以这条通道自己扛住，而不是指望永远没人跟它抢 COM。

    ⚠ 例外（卡 C-0069）：`RenderBackendMissing`（脚本本身不在）**不重试**。
    重试的前提是"这次运气不好"，而脚本缺失重试多少次都是同一个结果——
    实测那是 4 次 × 8 秒的纯白等，用户看到的是"卡住不动"。

    ## 等价性

    只在**失败**时重试；第一次就成功时，行为与直接调 `render_pages` 逐字相同
    （既有路径因此一个字节都没动——`ingest_document_figures` 仍然直接调
    `render_pages`，不经过这里）。
    """
    last = ""
    n = max(1, int(tries))
    for i in range(n):
        try:
            slides = render_pages(path, outdir, kind=kind)
        except RenderBackendMissing as e:
            # 后端不在 → 重试无意义，立刻把原因交出去（调用方据此给可操作提示）。
            return {}, f"{type(e).__name__}: {e}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            slides = {}
        if slides:
            return slides, ""
        if i + 1 < n:
            time.sleep(wait)
    return {}, (last or "整页渲染没有产出任何页图")


def ingest_pages_as_blocks(conn, doc_id: int, path: Path, kind: str = "",
                           *, n_pages: int = 0, verbose: bool = False,
                           workers: int | None = None,
                           limit: int | None = None,
                           thinking: bool = True, run_context=None) -> dict:
    """**图片型课件**专用：整页渲染 → 逐页识图 → 产出**正文块**。

    ## 它解决的是什么

    有些课件整页就是一张图、全片**没有一个字的文字层**（实测
    `第五章 药物分布.pptx`：47 页、8.96 MB、`ppt/media` 48 个 PNG、
    提取结果 0 块 / 0 字符）。这类文件在 `core/ingest.py` 里会被
    "0 块即失败"那一判直接落 `failed`，而把它变成文字的能力
    （`render_pages` + `describe_page`）本就在同一份代码里——
    只是排在失败点**之后**，永远轮不到。本函数就是把这个死锁打开。

    ## 与 `ingest_document_figures` 的分工（为什么不改那一个）

    两者共用同一套渲染、缓存与并发闸门，差别只在**产出去向**：

      - `ingest_document_figures`：产出 `figure_desc` 行 + `p.N 图` 识图块。
        它服务的是**已经有文字层**的资料，图是补充。
      - 本函数：把每页描述**当成正文块**（`page=N`、`kind="vision"`）返回给
        入库管线切成检索块——因为对这份资料来说，这些描述**就是全部内容**。
        同时仍然写 `figure_desc` 行：那是"重跑不重复计费"的唯一依据。

    刻意**不去改** `ingest_document_figures` 的循环：那个函数被 C-0040 与
    C-0064 两轮打磨过（逐页短事务、并发闸门、空输出重试），动它的风险远大于
    复用它带来的收益。本函数只在"渲染 → 逐页调用 → 写 figure_desc"
    这三件事上与它同源。

    ## 续跑（中断后重跑只补缺页）

    - 每页写完 `figure_desc` **立刻提交**（与 C-0040 同一口径：持锁毫秒级）；
    - 复用判据是 `(doc_id, page)` 行上的 `image_hash` 与磁盘上那张页图一致
      ⇒ 已识别的页命中缓存、**不再计费**；
    - 调用方（`core/ingest.py`）的跳过判据含 `n_chunks > 0`，所以失败过的
      资料下次还会被处理，不会被"内容未变"永久卡死。

    ## 参数

    - `n_pages`：调用方已知的总页数（写进提示词与进度文案）；0 表示用渲染出的页数。
    - `limit`：只处理前 N 页。**只给验证/试跑用**，生产路径不传（默认全跑）。
    - `workers`：逐页并发数，默认 `page_workers()`（环境变量 `KB_FIGURE_WORKERS`）。
      `1` = 完全串行。

    ## 返回

    `{"blocks": [...], "stats": {...}, "error": "", "error_kind": ""}`

    `error_kind` 取值：`no_key`（没有密钥）/ `render`（渲染失败，通常是没装
    PowerPoint 或 PDF 坏）/ `all_failed`（模型全部失败）。调用方据此给出
    **可操作**的提示——这一条是卡 C-0066 的明确要求：不要静默变空库。

    ⚠ 卡 C-0069 起，`render` 时的 `error` 不只是原始报错，末尾还拼了
    `render_backend_problem()` 的**可操作建议**（装什么 / 怎么绕开），
    调用方**原样展示即可**，不要再自己编第二遍解释。
    """
    from . import db, extract as _ex, progress as _pg

    run_ctx = (run_context if run_context is not None
               else (runtime_context.make_context("ingest_vision", doc_id=doc_id)
                     if runtime_context.runtime_enabled() else None))
    kind = kind or ("pdf" if path.suffix.lower() == ".pdf" else "pptx")
    stats: dict = {
        "pages": 0, "described": 0, "reused": 0, "no_info": 0, "chunks": 0,
        "cost_cny": 0.0, "errors": [], "workers": 0, "net_limit": 0,
        "peak_inflight": 0, "tokens_in": 0, "tokens_out": 0, "empty_retried": 0,
        "still_empty": [], "failed_pages": [],
        "model": "", "zero_text": True, "elapsed": 0.0, "outdir": "",
        "blocks": 0, "chars": 0,
    }
    out: dict = {"blocks": [], "stats": stats, "error": "", "error_kind": ""}

    # ★ 先把上游的未决事务落盘（与 `ingest_document_figures` 同一口径）。
    # 调用方刚写过 `documents` 行，那条写语句已经拿到写锁；不在这里提交，
    # 写锁就会跨过下面整份渲染 + 逐页模型调用（几十秒~几十分钟）。
    conn.commit()

    if not load_deepseek_key():
        out["error"] = "没有读到可用的识图密钥（DEEPSEEK_API_KEY）"
        out["error_kind"] = "no_key"
        return out

    outdir = config.LOG_DIR / "figure_desc" / _safe_dirname(path.stem)
    stats["outdir"] = str(outdir)
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    # 把历史渲染结果搬进本轮目录（目录不同 → 哈希永远对不上 → 复用从不生效）
    _adopt_previous_renders(path, outdir)

    t0 = time.perf_counter()
    _W = page_workers(workers)
    stats["workers"] = _W
    stats["net_limit"] = _net_limit()

    # 渲染是这段里第一块耗时（pptx 走 PowerPoint COM，47 页约 25 秒，
    # 中间没有回调），所以先把"在干什么"报出去，否则进度条停在这里像卡死。
    _pg.stage("识别图片",
              note="零文字层课件：先把每页渲染成图片（PowerPoint/PDF 渲染中，"
                   "几十秒到一两分钟；渲染失败会自动重试）")
    slides, render_err = _render_pages_with_retry(path, outdir, kind)
    if not slides:
        # ★ 渲染失败也要说人话（卡 C-0069）：把"为什么失败、下一步做什么"
        # 直接拼进 error，调用方（`core/ingest.py::_zero_text_vision_pass`）
        # 原样展示给用户，不再让英文 COM 原文当唯一的解释。
        out["error"] = f"{render_err}；{render_backend_problem(render_err)}"
        out["error_kind"] = "render"
        stats["errors"].append(f"整页渲染失败: {render_err}")
        stats["elapsed"] = time.perf_counter() - t0
        return out

    want = sorted(slides)
    if limit:
        try:
            want = want[: max(1, int(limit))]
        except (TypeError, ValueError):
            pass
    stats["pages"] = len(want)

    deck = clean_deck_title(path.stem) or path.stem
    total_txt = f"共 {int(n_pages)} 页" if n_pages else f"共 {len(slides)} 页"
    # 每页约 20 秒（C-0064 实测中位 19.4 秒），按并发折算给用户一个预期。
    est_min = max(1, int((len(want) * 20.0 / max(1, _W)) // 60) + 1)
    _pg.tick(0, total=len(want),
             note=(f"零文字层课件：逐页识图转文字，{len(want)} 页 / {_W} 路并发，"
                   f"预计 {est_min} 分钟"))

    table = _figure_table(conn, doc_id)
    now = db.now()
    blocks: list = []

    # ---- 逐页规划（主线程串行读库，不做任何模型调用）----
    # 复用判定必须在**提交识图任务之前**定下来：同一页被提交两次就是重复计费。
    # 这段与 `ingest_document_figures` 的规划段是同一个口径。
    plan: list[dict] = []
    for p in want:
        img = slides.get(p)
        if not img or not img.exists():
            plan.append({"page": p, "kind": "skip"})
            continue
        try:
            ihash = image_hash(img)
        except Exception:  # noqa: BLE001
            plan.append({"page": p, "kind": "skip"})
            continue
        # 页标题留空：这份资料本来就没有文字层，硬凑一个标题只会误导。
        # 页数写进"课件名称"，让模型知道自己在一份多长的课件里。
        ctx = PageContext(page=p, title="", deck_title=f"{deck}（{total_txt}）")
        row = table.get(p)
        if (row and row["image_hash"] == ihash
                and (row.get("description") or "").strip()):
            plan.append({"page": p, "kind": "reuse", "img": img, "ctx": ctx,
                         "ihash": ihash, "body": row["description"]})
        else:
            plan.append({"page": p, "kind": "describe", "img": img, "ctx": ctx,
                         "ihash": ihash})

    def _charge(r: "FigureResult") -> None:
        u = r.usage or {}
        stats["cost_cny"] += float(r.cost_cny or 0.0)
        stats["tokens_in"] += int(u.get("prompt_tokens", 0) or 0)
        stats["tokens_out"] += int(u.get("completion_tokens", 0) or 0)
        if r.model:
            stats["model"] = r.model

    def _accept_reuse(item: dict) -> None:
        body = (item.get("body") or "").strip()
        if body:
            blocks.append(_ex.Block(text=body, page=int(item["page"]), para=1,
                                    kind="vision"))

    def _land(item: dict, r: "FigureResult", r2: "FigureResult | None") -> None:
        """把一页的识图结果落库并转成正文块。**只在主线程调用**（写者恒为 1）。"""
        p = int(item["page"])
        if not r.ok:
            stats["errors"].append(f"p{p}: {(r.error or '')[:120]}")
            stats["failed_pages"].append(p)
            return
        body = r.text or ""
        stats["described"] += 1
        _charge(r)
        usable = _usable_page_text(body)
        if not usable:
            # ★ **不合格的输出不落库**（或落成空描述）。
            #
            # 复用的判据是"image_hash 一致 **且 description 非空**"。若把一段
            # "本页无知识性图示"（实测 68 字）原样存进去，下次重跑就会命中复用、
            # 这一页**永远不再重试**——那正是最难查的一类问题：库里有个看起来
            # 正常的文档，某一页的内容却永远是空的。
            r.text = ""
        _save_figure_row(conn, doc_id, p, "", item["ihash"], r, now)
        conn.commit()          # ★ 每页一短事务（卡 C-0040 口径）
        # md 里留**模型的原话**（而不是落库的那份），人工排查时要看它到底说了什么
        _write_page_md(outdir, p, body)
        if verbose:
            print(f"      识图转文字 p{p}: {len(body)} 字 "
                  f"¥{r.cost_cny:.4f} {r.elapsed:.0f}s", flush=True)

        # 输出不合格（空、过短、或"本页无知识性图示"）时重试一次——
        # 重试请求已经在 `_probe` 里发出去了（带纠正提示），这里只管记账与落库。
        #
        # 空输出的成因是推理 token 与回答 token 共用 max_tokens，超长时
        # 回答一个字都写不出来，且**不报错**；"无知识性图示"则是模型把
        # "这页没图"当成了"这页没内容"（实测 p2 就是这样丢的）。
        # 对零文字层课件，这两种都等于**那一页整页丢失**，不能只记个警告。
        if not usable:
            stats["empty_retried"] += 1
            got = False
            if r2 is not None:
                _charge(r2)
                if r2.ok and _usable_page_text(r2.text):
                    body = r2.text
                    got = True
                    _save_figure_row(conn, doc_id, p, "", item["ihash"], r2, now)
                    conn.commit()
                    _write_page_md(outdir, p, body)
                    if verbose:
                        print(f"      ↻ p{p} 输出不合格已重试成功（{len(body)} 字 "
                              f"¥{r2.cost_cny:.4f}）", flush=True)
            if not got:
                stats["still_empty"].append(p)
        if _usable_page_text(body):
            blocks.append(_ex.Block(text=body, page=p, para=1, kind="vision"))
        else:
            stats["errors"].append(
                f"p{p}: 识图没能转出文字（输出过短，或模型判成"
                f"「本页无知识性图示」；重试后仍然如此）")
            stats["failed_pages"].append(p)

    done_n = 0

    def _tick() -> None:
        # ⚠ `from . import progress` 必须在函数内：批量入库时
        # `pipeline_pool.install()` 换掉的正是 `core` 包上的这个名字。
        nonlocal done_n
        from . import progress as _pg2
        done_n += 1
        _pg2.tick(done_n, total=len(want),
                  note=f"第 {done_n}/{len(want)} 页"
                       + (f"（已复用 {stats['reused']} 页）"
                          if stats["reused"] else ""))

    gate = net_gate()
    inflight = _Inflight()
    # 零文字层课件的输出要装下整页文字转写，8192 撑不住；**被截断的描述是
    # 静默残缺**（后半页的字就没了），比重跑一次贵得多。所以第一次调用就用
    # 既有的"重试上限"（`FIGURE_RETRY_MAX_TOKENS`，默认 32768）——
    # 上限大不额外花钱，只有真生成出来的 token 才计费。
    caps = int(getattr(config, "FIGURE_RETRY_MAX_TOKENS", 32768))

    def _probe(item: dict) -> tuple[FigureResult, FigureResult | None]:
        """一页的**纯网络**部分（只在 worker 里跑，不碰库、不写文件）。

        与 `_probe_page` 的差别只有两点，都是零文字层特有的：
        1. 第一次调用就用大上限（`caps`）且带零文字层提示词；
        2. "不合格"的判据除"空/过短"之外，**还包括模型自报"本页无知识性图示"**
           ——对没有文字层的课件，那句话等于"这页不要了"（实测丢过一整页），
           所以补一句纠正提示再要一次。
        """
        img, ctx = item["img"], item["ctx"]
        with gate:
            inflight.enter()
            try:
                r = describe_page(img, ctx, thinking=thinking, max_tokens=caps,
                                  zero_text=True, run_context=run_ctx)
                if r.ok and _usable_page_text(r.text):
                    return r, None
                return r, describe_page(img, ctx, thinking=thinking,
                                        max_tokens=caps, zero_text=True,
                                        extra_hint=ZERO_TEXT_RETRY_HINT,
                                        run_context=run_ctx)
            finally:
                inflight.exit()

    if _W <= 1:
        # ---- 串行路径（`workers=1` 时是安全回退开关）----
        for item in plan:
            _tick()
            if item["kind"] == "skip":
                continue
            if item["kind"] == "reuse":
                stats["reused"] += 1
                _accept_reuse(item)
                continue
            r, r2 = _probe(item)
            _land(item, r, r2)
    else:
        # ---- 并发路径：只有 `_probe`（纯网络）进线程池 ----
        ex = ThreadPoolExecutor(max_workers=_W, thread_name_prefix="zero-page")
        try:
            for item in plan:
                if item["kind"] == "describe":
                    continue
                _tick()
                if item["kind"] == "reuse":
                    stats["reused"] += 1
                    _accept_reuse(item)
            futs = {ex.submit(_probe, item): item
                    for item in plan if item["kind"] == "describe"}
            for fut in as_completed(futs):
                item = futs[fut]
                _tick()
                try:
                    r, r2 = fut.result()
                except Exception as e:  # noqa: BLE001
                    stats["errors"].append(f"p{item['page']}: "
                                           f"{type(e).__name__}: {e}")
                    stats["failed_pages"].append(int(item["page"]))
                    continue
                _land(item, r, r2)
        finally:
            # 中断时不等在飞的页：它们只是网络调用、**不写库**（写库只在主线程），
            # 放弃无损；已识完的页早已逐页提交在库里。
            ex.shutdown(wait=False, cancel_futures=True)

    stats["peak_inflight"] = inflight.peak
    blocks.sort(key=lambda b: (b.page, b.para))
    stats["blocks"] = len(blocks)
    stats["chars"] = sum(len(b.text) for b in blocks)
    stats["elapsed"] = time.perf_counter() - t0
    out["blocks"] = blocks
    if not blocks:
        out["error"] = (stats["errors"][0] if stats["errors"]
                        else "识图没有产出任何内容")
        out["error_kind"] = "all_failed"
    try:
        conn.commit()
    except sqlite3.Error:
        pass
    return out


def _write_page_md(outdir: Path, page: int, body: str) -> None:
    """把一页的识图输出另存一份 md（人工查看用；真源在 `figure_desc`）。"""
    try:
        (outdir / f"slide_{page:02d}.md").write_text(
            f"# p{page} · 第 {page} 页\n\n{body}", encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- 逐页识图（可并发）

def _probe_page(img: Path, ctx: PageContext, thinking: bool,
                run_context=None) -> tuple[FigureResult, FigureResult | None]:
    """一页的**纯网络**部分：识图（必要时按更大的上限重试一次空输出）。

    ⚠ 这里**不碰数据库、不写文件**——所以它可以安全地放进线程池。
    写库与写 md 都由调用方（主线程）做，见 `ingest_document_figures` 里的
    `_commit`。**本次并发改造的全部内容就是这一件事**：把纯网络函数搬进
    线程池，落库路径一个字节都没动——逐页等价性由此成立，而不是靠事后比对。

    返回 `(第一次结果, 重试结果或 None)`。重试也在这里做（而不是回主线程），
    这样一次线程池任务里最多两次网络等待，主线程始终只做毫秒级的写库。
    """
    textless = not (ctx.title or "").strip() and not (ctx.body or "").strip()
    r = (describe_page(img, ctx, thinking=thinking, zero_text=True,
                       run_context=run_context) if textless
         else describe_page(img, ctx, thinking=thinking,
                            run_context=run_context))
    if not r.ok or (len((r.text or "").strip()) >= 30 and not (textless and is_no_info(r.text or ""))):
        return r, None
    # 半成品页才重试：判据与改造前逐字相同（`len(body.strip()) < 30`）。
    # 阈值取 `config.FIGURE_RETRY_MAX_TOKENS`，与 `describe_page` 的默认值
    # 同源，不在这里另写一个数。
    caps = getattr(config, "FIGURE_RETRY_MAX_TOKENS", 32768)
    if textless:
        return r, describe_page(img, ctx, thinking=thinking, max_tokens=caps,
                                zero_text=True, extra_hint=ZERO_TEXT_RETRY_HINT,
                                run_context=run_context)
    return r, describe_page(img, ctx, thinking=thinking, max_tokens=caps,
                            run_context=run_context)


def _probe_gated(img: Path, ctx: PageContext, thinking: bool,
                 gate: threading.Semaphore,
                 inflight: "_Inflight", run_context=None,
                 ) -> tuple[FigureResult, FigureResult | None]:
    """线程池里的入口：过**进程级闸门** → 记在飞 → 调模型。

    闸门是给"批量 4 份 × 每份 4 页 = 16 路"这种乘法叠加收口的（见文件上方
    「并发闸门」小节）。`_Inflight` 只用来留证据：`stats["peak_inflight"]`
    就是验收要的"同时在飞几路"。
    """
    with gate:
        inflight.enter()
        try:
            return _probe_page(img, ctx, thinking, run_context=run_context)
        finally:
            inflight.exit()


def _adopt_previous_renders(src: Path, outdir: Path) -> int:
    """把以前渲染过的整页图搬进本轮输出目录，供哈希比对复用。

    历史上有两处渲染位置：
    - `logs/<文件名前 20 字>_pages`（`run_figure_desc.py` 的老输出）
    - `logs/figure_desc/<清理后文件名>`（本目录）

    只搬**本目录还没有的**那些页，不动已有文件。
    """
    import shutil

    stem20 = src.stem[:20]
    cands = [config.LOG_DIR / f"{stem20}_pages"]
    # 新目录若已经是旧运行的目标，也顺带纳入（同目录则无需搬）
    moved = 0
    outdir.mkdir(parents=True, exist_ok=True)
    for d in cands:
        if not d.exists() or d.resolve() == outdir.resolve():
            continue
        for f in sorted(d.glob("slide_*.png")):
            dest = outdir / f.name
            if dest.exists():
                continue
            try:
                shutil.copy2(str(f), str(dest))
                moved += 1
            except OSError:
                continue
    return moved


def _safe_dirname(name: str) -> str:
    """把文件名变成安全的目录名（去掉 Windows 不允许的字符）。"""
    return re.sub(r'[<>:"/\\|?*]', "_", name)[:60]


def _figure_table(conn, doc_id: int) -> dict[int, dict]:
    """库中已有的识图记录 {页号: 行}。

    描述正文来自 `figure_desc.description`（见 `ensure_schema` 的说明）——
    复用判定要同时看 `image_hash` 与正文是否还在。
    """
    out: dict[int, dict] = {}
    try:
        ensure_schema(conn)
        for r in conn.execute(
                "SELECT * FROM figure_desc WHERE doc_id=?", (doc_id,)):
            out[int(r["page"])] = dict(r)
    except Exception:  # noqa: BLE001
        return {}
    return out


def _save_figure_row(conn, doc_id: int, page: int, title: str, ihash: str,
                     r: "FigureResult", now: float) -> None:
    """写入/更新一页的识图记录（含描述正文，供下次复用）。"""
    u = r.usage or {}
    conn.execute(
        """INSERT INTO figure_desc(doc_id, page, page_title, image_hash,
               model, thinking, tokens_in, tokens_out, cost_cny, n_chunks,
               created_at, description, no_info)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(doc_id, page) DO UPDATE SET
               page_title=excluded.page_title, image_hash=excluded.image_hash,
               model=excluded.model, thinking=excluded.thinking,
               tokens_in=excluded.tokens_in, tokens_out=excluded.tokens_out,
               cost_cny=excluded.cost_cny, created_at=excluded.created_at,
               description=excluded.description, no_info=excluded.no_info""",
        (doc_id, page, title, ihash, r.model, 1,
         int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)),
         float(r.cost_cny), 0, now, r.text, 0))


def _set_figure_chunks(conn, doc_id: int, page: int, ihash: str,
                       n: int) -> None:
    conn.execute(
        "UPDATE figure_desc SET n_chunks=? WHERE doc_id=? AND page=?",
        (n, doc_id, page))


def _mark_no_info(conn, doc_id: int, page: int) -> None:
    conn.execute(
        "UPDATE figure_desc SET n_chunks=0, no_info=1 "
        "WHERE doc_id=? AND page=?", (doc_id, page))


def _store_figure_chunks(conn, doc_id: int, deck_title: str, page: int,
                         title: str, body: str, now: float,
                         crumb: str = "", topic: str = "") -> int:
    """把一页的识图输出切块写库（含向量）。返回写入块数。

    `crumb` 不为空时**直接用它当面包屑**，覆盖内部按页号拼的那套。
    用途：接了全局结构分析（`core/outline.py`）之后，
    识图块也要挂在分析出的主题路径下，才能与正文块同组、一起被检索到。

    `topic` 是所属"节"路径，交给检索层做跨页分组（见 db 里 chunks.topic）。
    """
    from . import db
    from .embed import get_embedder, to_blob

    # ★ 落盘要排在**一切 CPU 工作之前**（卡 C-0040 第三轮复核补做）。
    #
    # 这个函数被两类调用方夹在写语句后面：
    #   - `core/ingest.py::_rewrite_figure_chunks` 刚按 `source_ref` 删过旧块；
    #   - `core/figures.py::ingest_document_figures` 刚写过 `figure_desc` 行。
    # 在 `isolation_level="IMMEDIATE"` 的连接上那条写语句已经持有写锁，
    # 而下面 `split_description`（长描述的正则切分）与逐块 ONNX 推理都是
    # **CPU 时间**——把它们留在写事务里，持锁时长就随描述长度与块数增长。
    # 实测（真实 34 页课件 + 36 页 pptx、workers=2、进程内事务时间线）：
    # 一个 2.77s 的写事务正是卡在"DELETE 已执行、`split_description` 还没跑完"
    # 这段里。提到最前面之后，写事务里只剩 INSERT。
    #
    # 另外这也修掉一个"提前 return 把锁带走"的路径：`chunks` 为空时本函数
    # 直接 `return 0`，改之前那个 return 会把上游的未决写事务原样留给调用方
    # （调用方的下一个 commit 可能隔着几十秒的模型调用）。
    try:
        conn.commit()
    except sqlite3.Error:
        pass

    chunks = split_description(body, page, deck_title, title)
    if not chunks:
        return 0

    # ★ 先把向量**全部算完**，再开始写库（卡 C-0040 打回补做）。
    #
    # 原实现是"写一条 chunks → 算这条的向量 → 再写 embeddings"，于是
    # **ONNX 推理落在写事务内部**：调用方的连接是 `isolation_level="IMMEDIATE"`
    # （见 `core/pipeline_pool.py::task_connect`），第一条 INSERT 就拿到了写锁，
    # 而 `embed_documents` 每次要几百毫秒；调用方
    # `core/ingest.py::_rewrite_figure_chunks` 又是整个循环才 commit 一次
    # ⇒ 15~32 页的识图块重挂会**连续持锁十几到二十几秒**
    # （大管家探针实测 max_hold=20.58s，距 busy_timeout(30s) 只剩 31% 余量；
    # 页数再翻倍就是用户最初看到的 `database is locked`）。
    #
    # 改成"先算后写"之后，写事务里只剩 INSERT，持锁降到毫秒级，
    # 且**与页数/块数无关**。
    #
    # 等价性：写进库的块与向量与原来逐字相同（同一个 emb_text、同一个模型），
    # 只是把"边写边算"变成"先算完再写"；向量计算不依赖库里的任何状态，
    # 所以顺序可换。失败语义也不变：某条算不出来时照样只跳过它的向量
    # （原来那条例外分支也是 pass），块本身照写。
    #
    # （上游未决事务的落盘已经在函数最开头做过——放在最前面，是为了把
    #  `split_description` 的 CPU 时间也一起排除在写事务之外，见那里的说明。）
    plan: list[tuple] = []
    for c in chunks:
        head = crumb or c.heading_path
        emb_text = f"{head}\n【{c.section}】{c.text}"
        vec = None
        try:
            # 嵌入**故意不加锁**：实测并发比"串行 1"快（见文件上方那段注释）。
            vec = get_embedder().embed_documents([emb_text])[0]
        except Exception:  # noqa: BLE001
            vec = None
        plan.append((c, head, emb_text, vec))

    n = 0
    for c, head, emb_text, vec in plan:
        cur = conn.execute(
            """INSERT INTO chunks(doc_id, ord, text, text_norm, heading_path,
                   topic, source_ref, char_len, token_est, content_hash,
                   created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, 9000 + page * 10 + len(c.section), c.text,
             db.normalize_for_match(emb_text), head, topic or None, c.source_ref,
             len(c.text), int(len(c.text) * 0.75), db.sha256_text(c.text), now))
        cid = int(cur.lastrowid)
        if vec is not None:
            conn.execute(
                "INSERT INTO embeddings(chunk_id, model, dim, vec) "
                "VALUES(?,?,?,?)",
                (cid, config.EMBED_MODEL_REPO, int(vec.shape[0]), to_blob(vec)))
        n += 1
    return n


def clear_document_figures(conn, doc_id: int) -> int:
    """删掉某文档的**识图块**（重建正文时调用）。

    ⚠ **不删 `figure_desc` 里的记录**——那里面存着描述正文，
    是"图片没变就复用、不重复计费"的**唯一依据**。

    这里踩过一个坑：原来连 `figure_desc` 行一起删，而调用顺序是
    「先 clear → 再 ingest」，于是 `ingest_document_figures` 查缓存时表已空，
    **复用永远不成立、每次都重新调一遍模型**。实测 doc#89 那 78 页
    因此白花了 ¥1.04（本该 ¥0）。

    所以清理范围故意只到"块"这一层。带着旧描述的孤儿行不影响正确性：
    1. 图片没了 → 该页不在 `want` 里 → 没有块用得上那条描述；
    2. 图片还在 → 哈希一致 → 正是想要的复用；
    3. 缓存里有、本轮没处理的页 → 由 `ingest_document_figures` 末尾
       按"本轮处理过的页"反查并删掉它们的块（见 `_drop_stale_figure_chunks`）。
    """
    # ★ 一条 SQL 删完，不要逐条 DELETE（卡 C-0040 第三轮复核补做）。
    #
    # 原来是一行一块地 `DELETE FROM chunks WHERE id=?`：34 页课件的识图块
    # 有两百条上下，每条都要走一次语句准备 + `chunks_ad` 触发器里的
    # trigram 索引删除，整个写事务就摊到秒级。实测（真实课件、进程内
    # 事务时间线）这里是 **1.14s** 的持锁窗口。
    # 换成一条 `DELETE ... WHERE doc_id=? AND source_ref LIKE ?` 之后，
    # 集合完全相同（下面那行 SELECT 取的正是这个集合），但只有一次语句
    # 准备、一次触发器派发循环。
    n = 0
    try:
        cur = conn.execute(
            "DELETE FROM chunks WHERE doc_id=? AND source_ref LIKE ?",
            (doc_id, "p.% 图"))
        n = int(cur.rowcount or 0)
        conn.commit()
    except Exception:  # noqa: BLE001
        pass
    return n


def _drop_stale_figure_chunks(conn, doc_id: int,
                              processed_pages: set[int]) -> int:
    """删掉"本轮没处理、但库里还留着"的识图块。

    场景：课件的某一页原来有图（做过识图描述），改版后那页没了。
    缓存里那条描述不用删（见 `clear_document_figures`），
    但它对应的**块**必须清掉，否则库里会留着一段指向不存在内容的描述。
    """
    n = 0
    try:
        rows = conn.execute(
            "SELECT id, source_ref FROM chunks WHERE doc_id=? AND "
            "source_ref LIKE ?", (doc_id, "p.% 图")).fetchall()
        for r in rows:
            ref = r["source_ref"] or ""
            try:
                page = int(ref.lstrip("p.").split(" ")[0])
            except (ValueError, IndexError):
                continue
            if page not in processed_pages:
                conn.execute("DELETE FROM chunks WHERE id=?", (int(r["id"]),))
                n += 1
    except Exception:  # noqa: BLE001
        pass
    return n


# ---------------------------------------------------------------- 摘要输出

def build_deck_outline(slides_text: list[dict], deck_title: str = "") -> list[str]:
    """从每页文字里抽出标题，组成整份资料的章节脉络。

    标题判定刻意简单：取每页首个非空行，长度合理即为标题。
    复杂判定交给 extract 层做；这里只要一个"够用的目录"。
    """
    out: list[str] = []
    for s in slides_text:
        t = (s.get("title") or "").strip()
        if t:
            out.append(f"p{s['page']}: {t[:50]}")
    return out


def build_contexts(slides_text: list[dict], deck_title: str = "",
                   include_body: bool = True) -> dict[int, PageContext]:
    """把逐页文字转成 {页号: PageContext}。"""
    outline = build_deck_outline(slides_text, deck_title)
    titles = {s["page"]: (s.get("title") or "").strip() for s in slides_text}
    pages = sorted(titles)
    ctxs: dict[int, PageContext] = {}
    for i, p in enumerate(pages):
        ctxs[p] = PageContext(
            page=p,
            title=titles[p],
            body=(slides_text[i].get("body") or "") if include_body else "",
            prev_titles=[titles[pages[j]] for j in range(max(0, i - 2), i)],
            next_titles=[titles[pages[j]]
                         for j in range(i + 1, min(len(pages), i + 3))],
            deck_title=deck_title,
            deck_outline=outline,
        )
    return ctxs


# ---------------------------------------------------------------- 调用

def _http_post(url: str, payload: dict, key: str, timeout: int = 300,
               retries: int = 4, wait: float = 4.0,
               run_context=None) -> tuple[int, dict | str]:
    from . import agent_bridge
    ctx = (run_context if run_context is not None
           and runtime_context.runtime_enabled() else None)
    if ctx is not None:
        # P0-F：AgentRequest/payload/URL 全部用快照；不再读中央 token cap/当前管线。
        payload = dict(payload)
        payload["model"] = str(ctx.model)
        payload["thinking"] = {"type": "enabled" if ctx.thinking else "disabled"}
        if int(ctx.max_tokens or 0) > 0:
            payload["max_tokens"] = int(ctx.max_tokens)
        else:
            payload.pop("max_tokens", None)
        if ctx.thinking and ctx.reasoning_effort:
            payload["reasoning_effort"] = str(ctx.reasoning_effort)
        else:
            payload.pop("reasoning_effort", None)
        base = str(ctx.api_base or "").rstrip("/")
        if base:
            url = base + "/chat/completions"
        pipeline = str(ctx.pipeline)
        req_model = str(ctx.model)
        req_max = int(ctx.max_tokens or 0)
        req_think = bool(ctx.thinking)
        req_eff = str(ctx.reasoning_effort) if req_think else None
    else:
        payload = config.apply_token_cap(payload, agent_bridge.current_pipeline())
        pipeline = agent_bridge.current_pipeline()
        req_model = str(payload.get("model") or "")
        req_max = int(payload.get("max_tokens") or 0)
        req_think = (payload.get("thinking") or {}).get("type") != "disabled"
        req_eff = payload.get("reasoning_effort")
    request = agent_bridge.AgentRequest(
        pipeline=pipeline, messages=payload.get("messages") or [],
        model=req_model, max_tokens=req_max,
        thinking=req_think, reasoning_effort=req_eff,
        temperature=float(payload.get("temperature", 0.3)), timeout=timeout)
    adapted = agent_bridge.complete(request)
    if adapted is not None:
        return 200, {"model": adapted["model"], "usage": adapted["usage"],
                     "choices": [{"message": {"content": adapted["content"]}}]}
    last: tuple[int, dict | str] = (0, "未执行")
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            # 服务端有响应 = 已知失败；429/5xx 仍按旧策略重试（P0-B 允许）。
            body = e.read().decode("utf-8", "replace")
            last = (e.code, body[:400])
            if e.code not in (429, 503, 502, 504):
                return last
        except Exception as e:
            # P0-B：发送后网络超时/断连/没回包 → 结果未知，**不再 range(retries) 重发**；
            # 其它发送后异常（如响应解析失败）同样不重发，避免重复计费。
            from .pipeline_runtime import state as runtime_state
            if runtime_state.request_may_have_been_sent(e):
                return (0, "remote_unknown: %s: %s（结果未知，禁止自动重试）"
                        % (type(e).__name__, e))
            return (0, f"{type(e).__name__}: {e}")
        if attempt < retries:
            time.sleep(wait * (attempt + 1))
    return last


def load_deepseek_key() -> str | None:
    r"""DeepSeek key 从 DSH 凭据读取，不在项目里重复存放。

    卡 C-0071：原来这里写死了作者本机的绝对路径
    （`C:\Users\<作者>\.dsh\.credentials.yaml`）。改成**通用的家目录位置**，
    与 `core/chat.py::CRED_PATHS` 用同一套规矩。本机行为逐字不变
    （`Path.home()` 就是那个目录、同一个文件），别人机器上不再探测无关路径。
    """
    import os
    v = os.environ.get("DEEPSEEK_API_KEY")
    if v:
        return v
    for cred in (Path.home() / ".dsh" / ".credentials.yaml",):
        if not cred.exists():
            continue
        m = re.search(r"DEEPSEEK_API_KEY\s*:\s*['\"]?(sk-[A-Za-z0-9_\-\.]{8,})",
                      cred.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1)
    return None


@dataclass
class FigureResult:
    ok: bool
    text: str = ""
    error: str = ""
    usage: dict = field(default_factory=dict)
    elapsed: float = 0.0
    cost_cny: float = 0.0
    model: str = ""
    cached: bool = False


# DeepSeek 官方价（USD / 1M tokens）
_PRICE = {"peak": {"miss": 0.30, "hit": 0.006, "out": 1.20},
          "off_peak": {"miss": 0.15, "hit": 0.003, "out": 0.60}}


def price_of(usage: dict, when: str = "off_peak", usd_cny: float = 7.1) -> float:
    p = _PRICE[when]
    miss = usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens", 0))
    hit = usage.get("prompt_cache_hit_tokens", 0)
    out = usage.get("completion_tokens", 0)
    usd = (miss / 1e6) * p["miss"] + (hit / 1e6) * p["hit"] + (out / 1e6) * p["out"]
    return usd * usd_cny


def image_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 过滤

def is_no_info(text: str) -> bool:
    """这份描述是不是模型明确声明的「本页无知识性图示」。

    **只认显式标记**，不做关键词猜测。

    为什么不做关键词猜测（试过，放弃了）：模型常写"本页不是机制图，而是……"，
    后面却从图里读出了实质内容（如 p54 的药代动力学柱状图、
    p12 的胰岛素原二硫键走向）。按关键词过滤会把这类页误杀——
    实测宽松规则在 104 页里命中 46 页，其中很多页其实有真内容；
    收严到"描述 ≤300 字且明确说没图"又变成 0 页（描述普遍 400~800 字）。
    两头都不成立，说明**关键词不是可靠判据**。

    代价与取舍：标记上线前已跑完的 104 页不会被过滤（它们没有标记）。
    这是**故意**的——宁可多花下一轮的钱，也不要静默丢掉真内容。
    要过滤老结果就重跑一遍（带上标记），标记会自己生效。
    """
    return NO_INFO_MARKER in (text or "")


def _usable_page_text(text: str, min_chars: int = 30) -> bool:
    """零文字层课件里，一页的识图输出算不算"真的拿到了内容"（卡 C-0066）。

    两条判据缺一不可：
      1. 长度 ≥ `min_chars`（30 与既有识图"空输出"的判据是同一个数）；
      2. **不是**模型自报的「本页无知识性图示」。

    第 2 条是这条通道特有的，来自实测：2 页冒烟里 p2 是章节首页
    （"本章要求"列表），模型回了 `【本页无知识性图示】…看图学不到正文之外的
    药理学知识`、只写 68 字——**那一页的文字一个字都没进库**。
    对有文字层的资料，这个标记是"这页别再花钱"的正确信号；
    对没有文字层的课件，它等于"这页不要了"。所以这里当**不合格**处理：
    触发一次带纠正提示的重试（见 `ZERO_TEXT_RETRY_HINT`），
    并且**不把这份没内容的输出存进缓存**（否则下次重跑会命中复用、永远修不好）。
    """
    t = (text or "").strip()
    return len(t) >= min_chars and not is_no_info(t)


def page_has_image(src: Path, kind: str = "") -> dict[int, bool] | None:
    """逐页判断"这页有没有图片对象"。

    返回 `{页号: 有图片}`；**该格式没有可靠判据时返回 None**。

    图片、图表、表格、嵌入对象、自由曲线、连线和组合矢量图都作为候选。
    宁可多送少量装饰性图形，也不要在送到识图模型之前丢掉结构式。

    ⚠ **PDF 返回 None，不要拿它过滤**：讲课用的导出件里整页内容都是图片
    对象，实测 78 页**无一例外**都有图，这个判据在 PDF 上区分度为 0。
    返回 None 而不是"全 True"，是为了让调用方能把"没判据"与
    "判过了、都有图"区分开——前者不该声称自己过滤了。
    PDF 的过滤只能靠模型自报的「本页无知识性图示」（见 `is_no_info`）。
    """
    kind = kind or ("pdf" if src.suffix.lower() == ".pdf" else "pptx")
    if kind != "pptx":
        return None
    try:
        from pptx import Presentation
    except ImportError:
        return None

    try:
        prs = Presentation(str(src))
    except Exception:
        return None  # 判不出来就保守地不按页过滤
    out: dict[int, bool] = {}

    def walk(shapes, acc: dict) -> None:
        for s in shapes:
            st = getattr(s, "shape_type", None)
            v = int(st) if st is not None else -1
            if v == 6:                       # GROUP：递归进去
                walk(s.shapes, acc)
                continue
            # OLE 对象常承载 ChemDraw 化学结构；FREEFORM 常用于绘制键和环。
            if v in (2, 3, 5, 7, 9, 10, 11, 13, 15, 20, 21, 22, 23, 24):
                acc["n"] += 1
                continue
            if getattr(s, "has_table", False) or getattr(s, "has_chart", False):
                acc["n"] += 1
                continue
            if v == 1:                       # AUTO_SHAPE（含手绘示意件）
                acc["shape"] += 1
                try:
                    # 单个多边形、箭头等也可能表示结构；普通矩形多用于排版。
                    if int(s.auto_shape_type) not in (1, 5):
                        acc["n"] += 1
                except (AttributeError, TypeError, ValueError):
                    pass

    for i, slide in enumerate(prs.slides, start=1):
        acc = {"n": 0, "shape": 0}
        walk(slide.shapes, acc)
        # 多个基础形状也可能构成结构图；单个矩形通常是版式装饰。
        out[i] = acc["n"] >= 1 or acc["shape"] >= 2
    return out


# ---------------------------------------------------------------- 入库

# 识图输出的小节标题。切块时按这些标记切，保证每块语义自足。
SECTION_HEADS = ["本页作用", "图的内容", "结构说明", "对应关系",
                 "图中文字", "补充信息", "不确定处"]

# 哪些小节适合作为**检索单元**。
# "不确定处"刻意不进库：搜到一段"这里我没看清"对复习没有帮助，
# 反而会污染检索结果。它的价值在于提示可信度，已随正文保留在 md 文件里。
#
# "图示说明"是**无小节标记时的兜底名**，必须留在白名单里——
# 曾经漏了它，导致"找不到小节就退化为整块"这条兜底逻辑产出后
# 又被这个白名单过滤掉，整份内容被静默丢弃（已由测试抓住）。
INDEXABLE_SECTIONS = {"本页作用", "图的内容", "结构说明", "对应关系",
                      "图中文字", "补充信息", "图示说明"}


def clean_deck_title(raw: str) -> str:
    """把课件名清理成适合做面包屑的短名。

    原始文件名常带教师名、日期、版本号（如
    "第三章 图论-课件张三20240301"），直接当面包屑会冗长重复。
    取第一段并去掉常见后缀即可。
    """
    t = (raw or "").strip()
    if not t:
        return ""
    # 去掉扩展名残留
    t = re.sub(r"\.(pptx?|pdf)$", "", t, flags=re.I)
    # 以 "-课件" / "_课件" / "-第X版" 等为界取前段
    t = re.split(r"[-_—](?:课件|讲义|教案|第\s*[0-9一二三四五六七八九十]+\s*版)",
                 t)[0]
    # 去掉尾部的日期/编号串（8 位数字）
    t = re.sub(r"[-_\s]*\d{6,}\s*$", "", t)
    return t.strip() or (raw or "").strip()


@dataclass
class FigureChunk:
    """识图输出切出的可检索片段。"""
    section: str          # 小节名，如"结构说明"
    text: str             # 小节正文
    page: int
    heading_path: str     # 面包屑：课件名 > p.21 图示说明
    source_ref: str       # 出处标注


def split_description(text: str, page: int, deck_title: str,
                      page_title: str = "") -> list[FigureChunk]:
    """把一页的识图输出切成若干可检索片段。

    为什么按【】小节切而不是整页一块：
    整页输出 1000~1500 字，超过块上限（1100），而且检索时一个块只能提供
    一个语义单元。按小节切后，"结构说明"与"补充信息"分别可检索，
    精度更高。

    实测的小节是固定集合（见 SECTION_HEADS）。找不到小节时退化为整块。
    """
    # 先按【xxx】切
    parts = re.split(r"【([^】]{1,12})】", text or "")
    sections: list[tuple[str, str]] = []
    # parts = [前言, 标题1, 正文1, 标题2, 正文2, ...]
    if len(parts) >= 3:
        i = 1
        while i + 1 < len(parts):
            name = parts[i].strip()
            body = parts[i + 1].strip()
            if body:
                sections.append((name, body))
            i += 2
    if not sections:
        body = (text or "").strip()
        if not body:
            return []
        sections = [("图示说明", body)]

    ref = f"p.{page} 图" if page else "图"
    deck_title = clean_deck_title(deck_title)
    head = f"{deck_title} > p.{page} 图示说明" if deck_title else f"p.{page} 图示说明"
    if page_title:
        head = f"{deck_title} > p.{page} {page_title[:24]} > 图示说明"
    out: list[FigureChunk] = []
    for name, body in sections:
        # "不确定处"不进检索库：搜到"这里我没看清"对复习没有帮助，
        # 只会污染结果。它的价值是提示可信度，已随正文保留在 md 文件里。
        if name not in INDEXABLE_SECTIONS:
            continue
        # 过长的小节再按句切开，避免超上限
        for piece in _split_to_limit(body, 1000):
            out.append(FigureChunk(section=name, text=piece, page=page,
                                   heading_path=head, source_ref=ref))
    return out


def _split_to_limit(text: str, limit: int) -> list[str]:
    """过长文本按句末标点切成不超过 limit 的片段。"""
    if len(text) <= limit:
        return [text]
    sents = re.split(r"(?<=[。！？；\n])", text)
    out: list[str] = []
    cur = ""
    for s in sents:
        if cur and len(cur) + len(s) > limit:
            out.append(cur)
            cur = s
        else:
            cur += s
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out if x.strip()]


def ensure_schema(conn) -> None:
    """建识图表与索引（幂等），并补齐后加的列。

    ⚠ 卡 C-0077 起这里**委托**给 `core/db.py::ensure_figure_schema()`：
    同一份 DDL 只留一处，`db.init_db()` 与识图路径不可能再漂。

    为什么必须这样：这张表原来**只有这里**会建，而本函数只在识图入库这条路上
    被调用 —— 于是"建完库、还没做过识图入库"的空库里没有 `figure_desc`，
    而 `core/kp_build.py`（看板「AI 知识点总结」）与 `core/chat.py`
    （整份课件问答）都会直接 `SELECT ... FROM figure_desc`：全新用户第一次点
    就是 `OperationalError: no such table: figure_desc`（实测 500）。
    现在建表搬进了 `init_db()`，识图路径这一侧继续调用（保持不变：识图可能
    压根没跑过 `init_db()`，如 `tools/ingest_figures.py`）。

    历史说明（描述正文为什么必须落库）：md 在 `logs/` 下，是**中间产物**，
    会被清理、也会因换机器而缺失。而"图片没变就复用旧描述、不重复计费"
    这条逻辑必须在重导入时可靠成立——只靠 md 就随时可能退化成重跑
    （doc#89 那批 ¥1.3345）。所以正文落库，md 只作为人工查看的副本。
    补 `description` / `no_info` 两列的 ALTER 现在也在 `db` 那边。
    """
    db.ensure_figure_schema(conn)


def load_descriptions(outdir: Path) -> dict[int, tuple[str, str, bool]]:
    """读回已生成的识图 md，返回 {页号: (标题, 正文, 是否无信息)}。

    第三个值 `no_info` 是**过滤依据**：为真说明这页的图没有信息量，
    入库时跳过（文字层已经覆盖过了，存进去只会占检索名额）。
    """
    out: dict[int, tuple[str, str, bool]] = {}
    for f in sorted(outdir.glob("slide_*.md")):
        m = re.match(r"slide_(\d+)\.md$", f.name)
        if not m:
            continue
        page = int(m.group(1))
        raw = f.read_text(encoding="utf-8", errors="replace")
        # 首行是 "# p21 · 标题"
        title = ""
        lines = raw.splitlines()
        if lines and lines[0].startswith("# "):
            head = lines[0][2:]
            if "·" in head:
                title = head.split("·", 1)[1].strip()
            lines = lines[1:]
        body = "\n".join(lines).strip()
        out[page] = (title, body, is_no_info(body))
    return out


def describe_page(img_path: Path, ctx: PageContext,
                  model: str | None = None,
                  thinking: bool = True,
                  max_tokens: int | None = None,
                  zero_text: bool = False,
                  extra_hint: str = "",
                  run_context=None) -> FigureResult:
    """在整章语境下描述一页的图。

    thinking=True 时开启推理模式——用户要求尝试。
    代价是输出 token 增加（推理 token 计入输出），实测约 +40%~100%。

    `zero_text=True` 换成**零文字层课件的提示词**（卡 C-0066）：
    那份资料没有文字层，图上文字就是唯一信源，所以要"完整转写"而不是
    "别抄文字"。**默认 False，逐字等同于改造前的行为**——有文字层的资料
    走的仍是原来那套提示词。

    `extra_hint` 追加在提示词末尾（为空时**逐字不变**）。零文字层通道用它
    在重试时补一句"上一轮你说这页没图，但这份课件没有文字层"。
    """
    from . import agent_bridge
    vctx = (run_context if run_context is not None
            and runtime_context.runtime_enabled() else None)
    key = load_deepseek_key()
    if not key and not agent_bridge.has_adapter(
            str(vctx.pipeline) if vctx is not None else "ingest_vision"):
        return FigureResult(False, error="没有 DEEPSEEK_API_KEY")
    if vctx is not None:
        model = str(vctx.model)
        thinking = bool(vctx.thinking)
        max_tokens = int(vctx.max_tokens or 0)
    else:
        model = model or getattr(config, "DEEPSEEK_VISION_MODEL", "deepseek-flash")
        # 0 = 无上限：不显式写 max_tokens（真正的 cap 在 _http_post 里按管线统一应用）。
        max_tokens = int(max_tokens) if max_tokens is not None else \
            int(config.token_cap("ingest_vision"))

    b64 = base64.b64encode(img_path.read_bytes()).decode()
    if zero_text:
        prompt = (f"{ZERO_TEXT_SYSTEM_PROMPT}\n{ctx.to_prompt()}\n"
                  f"{ZERO_TEXT_OUTPUT_FORMAT}")
    else:
        prompt = (f"{SYSTEM_STYLE_PROMPT}\n{NO_INFO_HINT}\n"
                  f"{ctx.to_prompt()}\n{OUTPUT_FORMAT}")
    if extra_hint:
        prompt = f"{prompt}\n{extra_hint}"
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        "stream": False,
    }
    if max_tokens > 0:
        payload["max_tokens"] = max_tokens
    if not thinking:
        payload["thinking"] = {"type": "disabled"}

    t0 = time.perf_counter()
    with agent_bridge.pipeline_scope("ingest_vision"):
        st, body = _http_post(f"{config.DEEPSEEK_BASE_URL}/chat/completions",
                              payload, key or "", run_context=vctx)
    el = time.perf_counter() - t0
    if st != 200 or not isinstance(body, dict):
        return FigureResult(False, error=f"HTTP {st}: {body}", elapsed=el)
    usage = body.get("usage", {}) or {}
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return FigureResult(True, text=content, usage=usage, elapsed=el,
                        cost_cny=price_of(usage), model=body.get("model", model))
