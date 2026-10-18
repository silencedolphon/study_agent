# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。     
# File-ID: SA-14-5b541d5e   (project study_agent; keep this line)
"""全局结构分析：让大模型看完整份课件，画出一张**思维导图**，
并给出每一页在导图中的完整路径。

## 为什么需要它（用户实测反馈）

> 有些页面的信息要上下好几页一起读。某一页写某类药物的总体药理作用，
> 下一页分写一条具体作用，再下一页又分写一条。
> 问"该类药物的药理作用"时，只返回写总体的那一页，**其他页丢失**。

根因是**逐页局部推断**：只看一页的形状几何，判不出
"p.26 是否属于「巴比妥类」这一节"——它有自己标题、正文也不含药名。
**这个判断是语义级的，只能靠看完整份课件。**

## 关键：要的是**父子关系**，不是并列的名字

用户的原始描述：

> P26 的标题是「巴比妥类的药理作用与临床应用」，P27 是「巴比妥类的镇静催眠作用」。
> 我问 AI「巴比妥类的药理作用」时，检索不到 P27 的内容。
> 根本原因是 P26 和 P27 之间的**结构信息丢失了**。

p.27「镇静催眠作用」是 p.26「药理作用与临床应用」**底下的一项**。
如果给两页各起一个并列的名字，这一层从属关系就没了，p.27 再也搜不出来。
所以模型必须输出**路径**：

    p.26 → ["镇静催眠抗焦虑药", "巴比妥类", "药理作用与临床应用"]
    p.27 → ["镇静催眠抗焦虑药", "巴比妥类", "药理作用与临床应用", "镇静催眠作用"]

p.27 的路径以 p.26 的路径为**前缀**——这就是"结构信息"本身。
检索「巴比妥类的药理作用」时，p.27 的面包屑里含有这串词，才会被一起带出来。

（早先那版让模型给 `theme` + `leaf` 两段，同节各页被压成了**兄弟**，
正是用户指出的问题。旧缓存仍能读，见 `_path_of`。）

## 缓存

按**课件内容哈希**缓存到 `logs/outline/<doc_id>.json`。
模型输出有波动，重导入时必须复用同一份结果，
否则面包屑会变、`kp_sources` 的 `chunk_id` 引用会错位。
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

# 每页喂给模型的字符预算。做层级判断不需要全文——这也是它便宜的原因
# （一份 37 页课件约 4600 输入 token，实测 CNY0.0085）。
BODY_CHARS = 220
FIG_CHARS = 260
# 单份课件的页数上限。超过就分批（当前全库最大 105 页，一次放得下）。
MAX_PAGES_PER_CALL = 120

# 缓存版本。**改提示词或改输出格式时必须 +1。**
#
# 为什么不能只靠内容哈希：哈希算的是"喂进去的页面内容"，与提示词无关。
# 实测把输出从 `theme + leaf`（同节各页平级）改成完整 `path`（父子路径）之后，
# 哈希没变，于是旧缓存被原样复用——读回来的还是平级结构，
# 用户要的"p.27 挂在 p.26 底下"完全没生效，而且**不报任何错**。
#
# v3：提示词加了"任意两页路径不许完全相同"的自查规则
#     （开推理时模型会把节名当页名，实测 5 页共用一条路径）。
# v4：光靠提示词没根治（实测 8 份资料的重复率超 10%，最差 29/56 页），
#     于是加了一层**本地修补**：重复路径的页用原文标题补末级。
CACHE_VERSION = 4

# 全局结构分析的**提示词头部**（本模块最重要的那段真源文本）。
#
# [C-0107 真值回填] 重建版把这个模块级常量整个丢了，于是
# `tools/dashboard.py::_builtin_prompt_text('ingest_outline')` 抛
# `AttributeError: module 'core.outline' has no attribute 'PROMPT_HEAD'`，
# 连带 GET /api/settings 与 POST /api/settings 双双 500。
#
# 判定依据（两份互相印证，方向一致：**是 core 侧丢了，不是 dashboard 写错名**）：
#   1. dashboard 侧真值 `_work/rebuilt/dashboard/0117-_builtin_prompt_text.py`
#      逐字写着 `return ('入库结构分析 · 提示词（头部）', outline_mod.PROMPT_HEAD)`；
#   2. 字节码字符串表 `_work/strings/outline.strings.txt` 里这个模块有一个
#      `[<module>] len=1243` 的模块级字符串常量，内容正是"以下是各页内容："结尾的
#      提示词头部。下面的文本**逐字取自该常量**，未做任何改写。
PROMPT_HEAD = """你在为一份课件建立**思维导图式的层级结构**，供检索使用。

**第一步（在脑子里做）**：通读下面所有页，画出这份课件的**层级树**。
树要表达的是"**这一页讲的内容，是上一页哪一部分的展开**"。

**第二步**：为每一页给出它在树里的**完整路径**——从根节点一直到这一页自己，
例如 ["镇静催眠抗焦虑药", "巴比妥类", "药理作用与临床应用", "镇静催眠作用"]。
路径的最后一级就是这一页的名字。

**最关键的一条规则**（这是本次任务的全部目的）：
> 如果某一页的内容是**前一页（或前面某一页）标题下的一项**，
> 它就必须是那一页的**子节点**，路径以那一页的路径为前缀。

举例，课件里连续几页是这样写的：

    p.26 标题：药理作用与临床应用     正文：普遍性中枢抑制作用，随剂量增加而增强……
    p.27 标题：镇静催眠作用          正文：小剂量镇静、中剂量催眠……
    p.28 标题：抗惊厥、抗癫痫        正文：小儿高热、破伤风……

那么正确的路径是（注意 p.27/p.28 是 p.26 的**子节点**，不是它的兄弟）：

    p.26 → ["课件名", "巴比妥类", "药理作用与临床应用"]
    p.27 → ["课件名", "巴比妥类", "药理作用与临床应用", "镇静催眠作用"]
    p.28 → ["课件名", "巴比妥类", "药理作用与临床应用", "抗惊厥抗癫痫与麻醉"]

**为什么必须这样**：检索时读者问"某类药的药理作用"，只有让后面几页
**继承**「药理作用与临床应用」这一层，它们才会被一起找出来。
把它们写成并列的兄弟，这一层结构信息就丢了，后面几页再也搜不到。

**其余规则**：
- 层级名必须取自各页出现过的标题或正文用词，**不要自己发明章节名**。
- 同一层内并列的内容（例如"体内过程""不良反应""药物相互作用"）应当同级，
  不要硬塞进彼此下面；只有**真正被展开的那一项**才作为父节点。
- 最长 5 级。路径的**最后一级是这一页自己的名字**（6~14 字），
  兄弟页之间不应重复。
- **任意两页的路径都不允许完全相同。** 输出前请自查一遍：
  如果你的结果里有两页的路径一字不差，说明后一页的"本页名字"没有写出来
  （把节名当成了页名），请补上它自己的名字。
  共用路径会让这两页在检索里再也分不开——这正是要避免的。
- **按下面出现的顺序，一页输出一条，一页都不能漏、也不能多。**
- 不要输出页码（页码由程序按顺序对应，你写页码反而会错位）。
- 不要输出解释，只输出 JSON。

输出格式（`pages` 的条数与顺序必须与下面各页**严格一致**）：
{"pages": [{"path": ["大类", "节", "本页名"]}, ...]}

以下是各页内容：
"""


@dataclass
class DeckOutline:
    """一份课件的全局结构（一张思维导图）。"""
    doc_id: int
    deck: str = ""
    # {页号: 这一页在导图中的**完整路径**}，从根节点到这一页自己。
    # 例：p.27 → ["镇静催眠抗焦虑药", "巴比妥类", "药理作用与临床应用",
    #              "镇静催眠作用"]
    # 路径的父子关系就是"结构信息"本身：p.27 以 p.26 的路径为前缀，
    # 检索"巴比妥类的药理作用"时它才会被一起带出来。
    pages: dict[int, list[str]] = field(default_factory=dict)
    model: str = ""
    cost_cny: float = 0.0
    usage: dict = field(default_factory=dict)
    from_cache: bool = False
    warnings: list[str] = field(default_factory=list)

    def path(self, page: int) -> list[str]:
        """这一页在导图中的路径（已清洗：去空、同层去重）。"""
        return _clean(self.pages.get(page) or [])

    def breadcrumb(self, page: int) -> str:
        """这一页的面包屑 = 它在导图中的完整路径。

        去重是实测踩出来的：模型常把课件名重复成两级
        （`镇静催眠抗焦虑药 > 镇静催眠抗焦虑药 > …`），
        因为页面上到处都印着课件名。
        """
        return " > ".join(self.path(page))

    def topic(self, page: int) -> str:
        """这一页所属的"节" = 路径**去掉最后一级**（本页自己的名字）。

        用途是跨页分组：`breadcrumb()` 到页，`topic()` 到节。
        接了导图之后，同一主题下各页的 topic 会呈现**父子关系**而不是
        完全相等——例如

            p.26 药理作用与临床应用 → topic「… > 巴比妥类」
            p.27 镇静催眠作用       → topic「… > 巴比妥类 > 药理作用与临床应用」

        所以检索层判"是不是同一主题"必须用**父子/兄弟关系**而不是相等
        （见 `core/search.py` 的 `_same_theme`）。

        **路径只剩一级时返回那一级，不能返回空**：这种页是某一节的
        **开篇页**（路径就停在节名上，比如 p.25 只有「… > 巴比妥类」）。
        返回空会让它退化成老判据、与同节其他页对不上，
        实测它因此被排除在主题链之外，往前补类属页时永远补不到它。

        模型对**课件名要不要占第一级**并不稳定（实测同一份课件里，
        有的页写「镇静催眠抗焦虑药 > 巴比妥类」，有的页只写「中枢兴奋药」），
        所以这里统一去掉与课件名同义的第一级。
        """
        parts = self.path(page)
        if self.deck and parts and _same(parts[0], self.deck):
            parts = parts[1:]
        if len(parts) <= 1:
            return " > ".join(parts)
        return " > ".join(parts[:-1])


def _same_theme(a: str, b: str) -> bool:
    """两条面包屑是不是"同一主题"（父子/兄弟关系，不是相等）。

    调用方：`core/search.py` 的跨页分组。判据就是**前缀包含**：
    p.27 的 topic 以 p.26 的 topic 为前缀 ⇒ 同一节。

    [重建] 原函数头在恢复版里丢了（只剩函数体，被上面 `topic()` 未闭合的
    docstring 吞掉），这里补上 `def` 行与文档。函数体两行**逐字保留**自恢复版。

    [C-0114] 字节码真值里**本模块没有这个名字**：真值用的是下面的 `_same()`
    （`DeckOutline.topic()` 调它做「课件名那一级要不要去掉」的判定）。
    此函数全项目已确认 0 引用（`core/search.py` 有它自己的同名实现），
    保留只为不破坏恢复期可能的外部引用；新代码请用 `_same()`。
    """
    na = (a or "").strip()
    nb = (b or "").strip()
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


# ---------------------------------------------------------------- 路径清洗
#
# [C-0114 真值回填] `_clean` / `_same` 在恢复版里整段丢失（文件被截断在
# `_pages_input` 中间），于是 `DeckOutline.path()` 一调就
# `NameError: name '_clean' is not defined`。下面两个函数**逐字取自字节码**
# （`_work/strings/outline.funcs.txt` 的函数清单 + 逐条反汇编核对），
# 生成器表达式里的判据 `_same(p, x)` 也按反汇编的参数顺序还原。

def _clean(parts):
    """清洗一层路径：去空白、去同层重复，保留顺序。"""
    out = []
    for x in (parts or []):
        x = (x if isinstance(x, str) else str(x)).strip()
        if not x or any(_same(p, x) for p in out):
            continue
        out.append(x)
    return out


def _same(a: str, b: str) -> bool:
    """两个层级名是不是同一个（忽略空白、括号、大小写）。"""
    norm = lambda s: re.sub(r"[\s（）()·、,，]", "", s or "").lower()  # noqa: E731
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


# ---------------------------------------------------------------- 输入构造

def pages_from_result(res, figure_texts: dict[int, str] | None = None
                      ) -> list[dict]:
    """从**提取结果**（内存里）构造每页输入，不依赖数据库。

    为什么不从库里读：全局分析要排在识图**之后**（识图摘要含关键主题信息，
    如"这页是三种药的对比表"），而那时正文块还没写库。
    所以输入直接来自提取结果 + 识图结果。

    `res` 是 `extract.ExtractResult`，`figure_texts` 是 `{页号: 识图文本}`。
    """
    titles: dict[int, str] = {}
    body: dict[int, list[str]] = {}
    for b in res.blocks:
        if not b.page:
            continue
        if b.kind == "heading":
            if b.level == 2 and b.text.strip():
                titles.setdefault(b.page, b.text.strip())
            continue
        if b.text.strip():
            body.setdefault(b.page, []).append(b.text)
    figs = figure_texts or {}
    out: list[dict] = []
    for pno in sorted(set(body) | set(titles) | set(figs)):
        out.append({
            "page": pno,
            "title": titles.get(pno, ""),
            "body": re.sub(r"\s+", " ", "\n".join(body.get(pno, [])))[:BODY_CHARS],
            "figure": re.sub(r"\s+", " ", figs.get(pno, ""))[:FIG_CHARS],
        })
    return out


def _pages_input(conn, doc_id: int, deck: str, src: Path) -> list[dict]:
    """每页的「标题 + 正文开头 + 识图摘要」。

    识图摘要必须带上——那里面常有"这页是三种药的对比表"这类
    正文里没有的主题信息。
    """
    from . import extract as _ex

    titles: dict[int, str] = {}
    if src.suffix.lower() == ".pptx":
        try:
            res = _ex.extract(src)
            for b in res.blocks:
                if b.kind == "heading" and b.level == 2 and b.text.strip():
                    titles.setdefault(b.page, b.text.strip())
        except Exception:  # noqa: BLE001
            pass

    body: dict[int, list[str]] = {}
    figs: dict[int, list[str]] = {}
    for r in conn.execute(
            """SELECT source_ref, text FROM chunks WHERE doc_id=? ORDER BY ord""",
            (doc_id,)):
        ref = r["source_ref"] or ""
        m = re.match(r"p\.(\d+)", ref)
        if not m:
            continue
        pno = int(m.group(1))
        (figs if "图" in ref else body).setdefault(pno, []).append(r["text"] or "")

    out: list[dict] = []
    for pno in sorted(set(body) | set(figs)):
        out.append({
            "page": pno,
            "title": titles.get(pno, ""),
            "body": re.sub(r"\s+", " ", "\n".join(body.get(pno, [])))[:BODY_CHARS],
            "figure": re.sub(r"\s+", " ", "\n".join(figs.get(pno, [])))[:FIG_CHARS],
        })
    return out


# ------------------------------------------------------------ 提示词 / 解析 / 缓存
#
# [C-0114 真值回填] 下面从 `build_prompt` 到 `analyze_deck` 全部丢失过
# （文件被截断在 `_pages_input` 中间）。每一段都按 `core/outline.pyc` 的
# 逐函数反汇编还原：字符串常量逐字取自 `_work/strings/outline.strings.txt`，
# 控制流（含 try/except 的形态、重试次数、缓存判据）按字节码逐步对齐。

def build_prompt(deck, pages):
    lines = [
        PROMPT_HEAD,
        f"课件名称：{deck}",
        f"共 {len(pages)} 页。",
        "",
    ]
    for pg in pages:
        lines.append(f"--- p.{pg['page']} ---")
        lines.append(f"标题：{pg['title'] or '（无）'}")
        lines.append(f"正文：{pg['body']}")
        if pg["figure"]:
            lines.append(f"插图说明：{pg['figure']}")
        lines.append("")
    return "\n".join(lines)


def _parse(raw, pages):
    """解析模型输出，**按列表顺序**对回页码。

    ## 为什么不信任模型给的页码

    实测同一份课件跑两次，模型给出的页码对不上
    （v1 里 p.2 是「剂量依赖性作用谱」，v2 里 p.2 变成「概念与镇静催眠作用」）——
    它会把封面页合并掉、或跳过空白页，于是**后面所有页全部错位**。
    面包屑挂错页比没有面包屑更糟：检索会指向错误的幻灯片。

    所以提示词里明确要求"不要输出页码"，这里按数组顺序对回真实页码；
    条数不一致时记 warning（宁可少挂几页，也不挂错页）。
    """
    warn = []
    s = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    data = json.loads(s)
    items = data.get("pages") or []
    out = {}

    real = [p["page"] for p in pages]
    if len(items) != len(real):
        warn.append(
            f"模型给了 {len(items)} 条，实际 {len(real)} 页，按顺序对应前 "
            f"{min(len(items), len(real))} 页"
        )
    for pno, item in zip(real, items):
        out[pno] = _path_of(item)
    return out, warn


def _path_of(item) -> list[str]:
    """把模型给的一条结果读成路径。

    模型偶尔会换写法，实测见过三种，都要认：
    - `{"path": ["A", "B"]}`：**要求**的格式；
    - `["A", "B"]`：直接把数组放在 `pages` 里，没包一层对象
      （实测报 `'list' object has no attribute 'get'` 才发现）；
    - `{"theme": [...], "leaf": "..."}`：早先那版的两段式，
      缓存里的旧数据也是这种，照样读得出来。
    """
    if isinstance(item, list):
        return _clean(item)
    if isinstance(item, str):
        return _clean([x.strip() for x in re.split(r"[>＞]", item) if x.strip()])
    if not isinstance(item, dict):
        return []
    raw = item.get("path")
    if isinstance(raw, str):
        raw = [x.strip() for x in re.split(r"[>＞]", raw) if x.strip()]
    if isinstance(raw, list) and raw:
        return _clean(raw)
    if not raw and not item.get("theme") and not item.get("leaf"):
        for v in item.values():
            if isinstance(v, str) and v.strip():
                return _clean([v])
            if isinstance(v, list) and v:
                return _clean(v)
    theme = item.get("theme") or []
    if isinstance(theme, str):
        theme = [x.strip() for x in re.split(r"[>＞]", theme) if x.strip()]
    parts = list(theme)
    leaf = str(item.get("leaf") or "").strip()
    if leaf:
        parts.append(leaf)
    return _clean(parts)


def _fill_duplicate_paths(pages: dict[int, list[str]],
                          titles: dict[int, str]) -> int:
    """给**共用同一条路径**的页补上原文标题作末级。返回补了几页。

    模型有时会把"节名"直接当成"页名"，于是同一节的好几页拿到一模一样的路径
    （实测 doc#73 是 56 页里 29 页重复、doc#81 是 91 页里 19 页）。
    这些页在检索里再也分不开——面包屑是它们唯一的区分手段。

    为什么用**原文标题**而不是让模型重编一个：标题是页面上真实写着的东西
    （`extract` 已经从版面里提出来了），比模型的二手概括可靠，而且不花钱。

    补不了的情况（页面本来没有标题、或标题与路径里某一级重复）**保持原样**：
    宁可留着重复路径，也不造一个假页名出来。

    ⚠ 提示词里那条"任意两页路径不许完全相同"的规则**没有根治**这个问题
    （实测 8 份资料的重复率仍超 10%），所以这里必须有一层代码兜底。
    """
    dup = collections.Counter(tuple(p) for p in pages.values())
    fixed = 0
    for key, cnt in dup.items():
        if cnt <= 1 or not key:
            continue
        for pno in [p for p in sorted(pages) if tuple(pages[p]) == key]:
            t = (titles.get(pno) or "").strip()
            if t and not any(_same(x, t) for x in key):
                pages[pno] = list(key) + [t]
                fixed += 1
    return fixed


def _load_pages(d):
    """从缓存读出 {页号: 路径}。

    旧缓存存的是 `{"theme": [...], "leaf": "..."}`，照样能读
    （`_path_of` 认这种写法）——这样已有的分析结果不用重花钱。
    """
    out = {}
    for k, v in (d.get("pages") or {}).items():
        try:
            pno = int(k)
        except (TypeError, ValueError):
            continue
        out[pno] = _path_of(v)
    return out


def _cache_path(doc_id):
    d = config.LOG_DIR / "outline"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{doc_id}.json"


def _content_hash(pages):
    blob = json.dumps(
        [{k: p[k] for k in ("page", "title", "body", "figure")} for p in pages],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def analyze_deck(conn, doc_id, src, deck, pages=None, *, force=False,
                 verbose=False):
    """对一份课件做全局结构分析。结果按内容哈希缓存。

    `pages` 可直接传入（来自 `pages_from_result`），此时不读库——
    入库流程要的就是这条：识图完成后、写库之前做分析。

    失败**不抛异常**，只记 warning，由调用方决定是否降级——
    "增强步骤不能让主流程挂掉"这条原则在识图、表格上都守过。
    """
    out = DeckOutline(doc_id=doc_id, deck=deck)
    if pages is None:
        pages = _pages_input(conn, doc_id, deck, src)
    if not pages:
        out.warnings.append("没有页面数据，跳过全局分析")
        return out
    if len(pages) > MAX_PAGES_PER_CALL:
        out.warnings.append(
            f"共 {len(pages)} 页，超过单次上限 {MAX_PAGES_PER_CALL}"
            f"，只分析前 {MAX_PAGES_PER_CALL} 页"
        )
        pages = pages[:MAX_PAGES_PER_CALL]

    h = _content_hash(pages)
    cache = _cache_path(doc_id)
    if cache.exists() and not force:
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            if d.get("content_hash") == h and d.get("pages") \
                    and int(d.get("v") or 0) == CACHE_VERSION:
                out.pages = _load_pages(d)
                out.model = d.get("model", "")
                out.from_cache = True
                if verbose:
                    print(f"      全局分析：复用缓存（{len(out.pages)} 页）",
                          flush=True)
                return out
        except Exception:  # noqa: BLE001
            pass

    from . import chat as _chat

    prompt = build_prompt(deck, pages)
    t0 = time.perf_counter()

    # 开推理时，思考 token 会吃掉 max_tokens，正文可能被截断成空。
    # 所以第一次给足预算；若空输出，翻倍重试一次。
    thinking = bool(int(getattr(config, "OUTLINE_THINKING", 1)))
    budget = int(getattr(config, "OUTLINE_MAX_TOKENS", 32768))

    el = 0.0
    parsed = {}
    pwarn = []
    res = {}
    for attempt in range(2):
        tokens = budget * (attempt + 1)
        t0 = time.perf_counter()
        try:
            res = _chat.chat_once(
                [{"role": "user", "content": prompt}],
                model=_chat.CHAT_MODEL, max_tokens=tokens,
                thinking=thinking, temperature=0,
            )
        except Exception as e:  # noqa: BLE001
            out.warnings.append(f"全局分析调用失败: {type(e).__name__}: {e}")
            return out
        el += time.perf_counter() - t0
        content = (res.get("content") or "").strip()
        if content:
            try:
                parsed, pwarn = _parse(content, pages)
                break
            except Exception as e:  # noqa: BLE001
                why = f"输出解析失败: {type(e).__name__}: {e}"
        else:
            why = "输出为空"
        if attempt == 0 and thinking:
            u0 = res.get("usage") or {}
            out.warnings.append(
                f"{why}（推理占满 {u0.get('completion_tokens', 0)} token）"
                f"，放大到 {budget * 2} 重试"
            )
            continue
        out.warnings.append(f"全局分析{why}")
        return out
    out.warnings.extend(pwarn)

    u = res.get("usage") or {}
    ti = int(u.get("prompt_tokens") or 0)
    to = int(u.get("completion_tokens") or 0)
    # 单价（CNY / 百万 token）：输入 0.15、输出 0.6，汇率 7.1（与字节码常量一致）。
    out.cost_cny = round(
        ti / 1000000.0 * 0.15 * 7.1 + to / 1000000.0 * 0.6 * 7.1, 4
    )
    out.model = res.get("model", "")
    out.usage = {
        "prompt_tokens": ti,
        "completion_tokens": to,
        "elapsed": round(el, 1),
    }

    want = {p["page"] for p in pages}
    missing = sorted(want - set(parsed))
    titles = {p["page"]: (p.get("title") or "").strip() for p in pages}

    # 模型漏页时，用上一页的路径兜底（宁可重复，也不要空）。
    last = []
    for pno in sorted(want):
        p = parsed.get(pno) or []
        if not p and last:
            p = list(last)
        elif p:
            last = p
        out.pages[pno] = p

    if missing:
        out.warnings.append(
            f"模型漏了 {len(missing)} 页: {missing[:12]}"
        )

    fixed = _fill_duplicate_paths(out.pages, titles)
    if fixed:
        out.warnings.append(
            f"用原文标题给 {fixed} 页补了页名（模型给了重复路径）"
        )

    # 层级被拍平的检测：达到 3 级的页数不足 30% 就告警。
    deep = sum(1 for p in out.pages.values() if len(p) >= 3)
    if out.pages and deep < len(out.pages) * 0.3:
        out.warnings.append(
            f"只有 {deep}/{len(out.pages)} 页达到 3 级，"
            f"层级可能被拍平了（跨页结构信息会丢失）"
        )

    # 重复路径检测：同一路径被多页共用，说明模型把节名当成了页名。
    dup = collections.Counter(tuple(v) for v in out.pages.values())
    shared = [k for k, c in dup.items() if c > 1]
    if shared:
        n_page = sum(dup[k] for k in shared)
        sample = "；".join(" / ".join(k) for k in shared[:2])
        out.warnings.append(
            f"{n_page} 页共用 {len(shared)} 条路径"
            f"（这些页没有自己的名字）: {sample}"
        )

    try:
        cache.write_text(
            json.dumps(
                {
                    "v": CACHE_VERSION,
                    "doc_id": doc_id,
                    "deck": deck,
                    "model": out.model,
                    "content_hash": h,
                    "cost_cny": out.cost_cny,
                    "usage": out.usage,
                    "created_at": time.time(),
                    "pages": {str(k): {"path": v}
                              for k, v in out.pages.items()},
                },
                ensure_ascii=False, indent=1,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass
    if verbose:
        print(
            f"      全局分析：{len(out.pages)} 页，"
            f"{out.usage.get('elapsed')}s，CNY{out.cost_cny:.4f}",
            flush=True,
        )
    return out