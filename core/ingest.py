# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-09-12a737f3   (project study_agent; keep this line)
"""摄入管线：文件 -> 提取 -> 切块 -> 嵌入 -> 入库。

幂等性：以源文件内容 sha256 判重。改过的文件会重建该文档的所有切块与向量，
没改的直接跳过。因此重复对整个 raw/ 目录跑 ingest 是安全且廉价的。

两种切块模式（2026-10-19，`config.INGEST_PAGE_CHUNK`，默认开）：

- **整页切块**（默认，`page_mode`）：pdf/pptx **非教材课件**每页一块，有图页的
  识图文字并入同一块；不跑 AI 结构分析（`core/outline.py`）、不建独立识图块。
- **旧模式**（开关关闭 / 教材专线 / 非课件资料）：逐字走改造前的
  识图 → AI 结构分析 → `chunk_blocks` → 独立识图块重挂，一行未改。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import chunk as chunk_mod
from . import config, db, extract, progress
from .embed import embed_documents_cached, get_embedder, to_blob

# 估算 token 数：中文约 1 字 ≈ 0.7 token，英文约 0.25 token/字符。
# 取 0.75 作为偏保守的混合估计，用于判断是否超出嵌入模型上限。
TOKEN_PER_CHAR = 0.75

# ---------------------------------------------------------------- 教材专线
# 卡 C-0088：把教材入库与课件入库**分开**（人的原话见
# `docs/教材入库改造方案-2026-10-12.md` 第一节）。
#
# 这一节只有三个常量与一条分流，**没有第二套管线**：教材照旧走
# 提取 → （扫描页）本地 OCR → 切块 → 嵌入 → 写库，唯一的分岔在切块那一步——
# 教材按章切（`core/textbook.py`），课件按老路切（`core/chunk.py`）。
#
# 契约（下一张卡 C-0089 的看板按钮 / `/api/ingest/textbook` 按这个调用）：
#   · `ingest_file(..., mode="textbook")` 进教材专线；
#   · **`mode=""`（默认）逐字走原路**——命令行、看板 `/api/ingest`、
#     批量池、`reimport_all`、share 导入的行为一个字都不变；
#   · 教材专线入库后显式写 `documents.role='textbook'`（不靠标题启发式，
#     见 `_upsert_doc` 的 `role_override`）；
#   · `documents.extra` 里打新键 `chunk_scheme="textbook-v1"`（**只增键**），
#     目的是让通用入口下次认出这份资料是教材专线切的、跳过它，
#     防止"入库全部待处理文件"用旧切块把按章切好的块覆盖掉
#     （方案文档第四节·补 第 1 条，最危险的坑）。
TEXTBOOK_MODE = "textbook"
TEXTBOOK_ROLE = "textbook"
TEXTBOOK_CHUNK_SCHEME = "textbook-v1"

# ---------------------------------------------------------------- 整页切块
# 2026-10-19 用户需求：「课件入库不要再让大模型拆结构；**每一页切一块**；
# 页里有图片就把识图信息和文字**合并进同一块**。」
#
# 实现方式：只做**加法/分支**——`ingest_file` 里多一条 `page_mode` 分支，
# 默认开启（`config.INGEST_PAGE_CHUNK`，设置中心可关）。关掉时旧分支
# （outline 分析 + `chunk_blocks` + 独立识图块）**逐字回退**，一行未改。
#
# 边界（本卡红线）：只对 kind in ("pdf","pptx") 且非教材课件生效；
# 教材专线（mode="textbook"）与非课件资料（txt/md/docx/图片…）不受影响。
PAGE_CHUNK_IMAGE_HEAD = "【图片理解】"


def page_chunk_enabled(kind: str, is_textbook: bool = False) -> bool:
    """这份资料是否走「整页切块」模式（默认开、可回退）。

    纯判断、无副作用：只对 pdf/pptx 非教材课件返回 True；
    `config.INGEST_PAGE_CHUNK`（设置中心「整页切块」开关，环境变量
    `KB_INGEST_PAGE_CHUNK`）关掉时对任何资料都返回 False → 旧分支逐字回退。
    """
    if is_textbook or kind not in ("pdf", "pptx"):
        return False
    return bool(getattr(config, "INGEST_PAGE_CHUNK", 1))


def _first_heading(blocks: list) -> str:
    """一页里第一个非空标题块（没有就给空串，写库时回退文档标题）。"""
    for b in blocks or []:
        if getattr(b, "kind", "") == "heading":
            t = (getattr(b, "text", "") or "").strip()
            if t:
                return t
    return ""


def build_page_chunks(blocks, fig_text) -> list:
    """整页切块：**每页一块**，图片理解并入同一块（纯函数，无 IO/无模型调用）。

    规则（与用户需求逐条对应）：

    - 页内文本 = 该页**全部非空 block 按原顺序**用 "\\n" 连接（保留标题/正文/
      表格行/OCR 行原文，不重排、不跨页合并）；
    - 该页有识图文字（`fig_text[页号]`，来自 `figure_desc` 缓存/本轮识图）→
      在正文后追加 `【图片理解】` 段；
    - `source_ref = "p.N"`（**无区间**）；`heading_path` = 该页第一个标题块
      （无则空串；`_write_chunks` 会按既有逻辑回退到文档标题）；
    - **纯空页跳过**（文字与识图都为空）；块数 = 有内容页数；
    - 本函数不做 outline、不建独立识图块——这两件事只属于旧分支。
    """
    by_page: dict[int, list] = {}
    for b in (blocks or []):
        try:
            p = int(getattr(b, "page", 0) or 0)
        except (TypeError, ValueError):
            continue
        if p > 0:
            by_page.setdefault(p, []).append(b)
    figs = fig_text or {}
    fig_pages: set[int] = set()
    for k in figs:
        try:
            fig_pages.add(int(k))
        except (TypeError, ValueError):
            continue
    out: list = []
    for p in sorted(set(by_page) | fig_pages):
        page_blocks = by_page.get(p) or []
        parts = [(getattr(b, "text", "") or "").strip() for b in page_blocks]
        body = "\n".join(t for t in parts if t)
        fig = str(figs.get(p) or figs.get(str(p)) or "").strip()
        if fig:
            body = (body + "\n\n" + PAGE_CHUNK_IMAGE_HEAD + "\n" + fig).strip()
        if not body:
            continue
        out.append(chunk_mod.Chunk(
            text=body,
            heading_path=_first_heading(page_blocks),
            source_ref=f"p.{p}",
            ord=len(out) + 1,
            meta={"page": p},
        ))
    return out


@dataclass
class IngestReport:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    unsupported: int = 0
    needs_ocr: int = 0      # 已入库但内容不完整，装了 OCR 才能解析
    chunks: int = 0
    # 这一批**实际写进库**的文档按库归属计数：{'main': 5, 'aux': 2}。
    # 为什么要有：入库结束时用户和助手都要知道"刚才进的是哪个库"——
    # 否则题库入完在看板默认页签里看不到它，会被当成入库失败。
    added_by_corpus: dict[str, int] = field(default_factory=dict)
    details: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"新增 {self.added}", f"更新 {self.updated}", f"跳过 {self.skipped}"]
        if self.needs_ocr:
            parts.append(f"待 OCR {self.needs_ocr}")
        if self.failed:
            parts.append(f"失败 {self.failed}")
        if self.unsupported:
            parts.append(f"不支持 {self.unsupported}")
        s = " / ".join(parts) + f"，共写入 {self.chunks} 个切块"
        if self.added_by_corpus:
            s += "（主库 {} 份 / 辅库 {} 份）".format(
                self.added_by_corpus.get("main", 0),
                self.added_by_corpus.get("aux", 0))
        return s


def is_junk_title(t: str) -> bool:
    """判断一个标题是否是导出工具留下的垃圾（供外部判断是否需要修正）。"""
    t = _clean_title(t)
    return (not t) or bool(_JUNK_TITLE.match(t))


def migrate_titles(conn: sqlite3.Connection, apply: bool = True) -> dict:
    """修正已入库文档的标题。

    为什么需要单独迁移：标题是在解析时由 _title_of 算出来的，
    而修正标题不需要重新解析文件——重新解析 36 个文件（其中一本 452 页
    扫描教材要 OCR 两小时）只为了改个标题，代价完全不成比例。

    这里沿用与 _title_of 相同的判断：在"文件名"与"已存标题"里取更长者。
    不能简单地"一律改用文件名"——实测那样会把
    "第29章 肾上腺皮质激素类药物" 换成文件里章号写错的
    "第二十五章　肾上腺皮质激素2025"，反而更差。
    """
    changes: list[dict] = []
    for r in conn.execute("SELECT id, path, title FROM documents"):
        p = Path(r["path"])
        stem = _clean_title(p.stem)
        old = (r["title"] or "").strip()

        # 情况一：带导出前缀 -> 只清洗前缀，保留后面的主题内容。
        # 这种标题里的"2 卢 心绞痛2025"是有用的，直接换成文件名反而丢信息。
        if _has_export_prefix(old):
            cleaned = _clean_title(old)
            if cleaned and cleaned != old:
                changes.append({"id": int(r["id"]), "old": old,
                                "new": cleaned, "how": "去前缀"})
            continue

        # 情况二：标题确实不可用 -> 换成文件名。
        # 不可用指：含糊到无法辨识（"概论"）、垃圾（"Background"）、
        # 或只是章节号（"第二十七章"）。
        # 这里刻意不做审美优化——目标是让 8 个叫"概论"的文档能被认出来，
        # 而不是把每个标题都调到最优（那是无底洞，收效甚微）。
        if old and not is_vague_title(old) and not _JUNK_TITLE.match(old) \
                and not _looks_like_section_number(old):
            continue
        if not stem or _JUNK_TITLE.match(stem) or is_vague_title(stem):
            continue
        if stem == old:
            continue
        changes.append({"id": int(r["id"]), "old": old, "new": stem,
                        "how": "改用文件名"})
    if apply and changes:
        for c in changes:
            conn.execute("UPDATE documents SET title=? WHERE id=?",
                         (c["new"], c["id"]))
        conn.commit()
    return {"changed": len(changes), "applied": bool(apply),
            "details": changes[:60]}


def iter_files(paths: list[Path], recursive: bool = True) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            out.extend(sorted(f for f in it if f.is_file()))
        elif p.is_file():
            out.append(p)
    # 排除临时文件与隐藏文件
    return [f for f in out if not f.name.startswith(("~$", ".")) and f.suffix != ".part"]


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(config.ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


# 这些"标题"是导出工具留下的产物，不是真标题。
# 实测用户资料里有 17/36 个文档标题是 "概论"、"Background"、
# "Microsoft PowerPoint - 1 高血压ppt2025" 之类，在列表里完全无法分辨是哪份资料。
_JUNK_TITLE = re.compile(
    r"^(microsoft\s+powerpoint|powerpoint(\s+演示文稿)?|slide\s*\d*|"
    r"presentation|untitled|document\d*|background|"
    r"desktop|新建|未命名|文档\d*|演示文稿\d*)\s*[-–—:：]?\s*$",
    re.I,
)


# 这些标题过于笼统，脱离上下文无法辨识是哪份资料，应当用文件名替代。
# 实测用户资料里有 8 个文档标题都是"概论"，在列表里完全分不出谁是谁。
_VAGUE_TITLES = {
    "概论", "绪论", "前言", "引言", "概述", "总论", "导论", "目录", "正文",
    "background", "introduction", "overview", "contents", "index",
}


def _looks_like_section_number(t: str) -> bool:
    """判断标题是否只是个章节号（"第二十七章"、"第3章"），没有主题内容。"""
    return bool(re.match(
        r"^\s*第\s*[0-9一二三四五六七八九十百]+\s*[章节讲部篇]\s*$", t or ""))


def _has_export_prefix(t: str) -> bool:
    """标题是否带着导出工具留下的前缀，如 "Microsoft PowerPoint - 2 卢 心绞痛2025"。

    前缀本身不含内容信息，但后面的主题是有用的，所以是"清洗"而不是"丢弃"。
    """
    return bool(re.match(
        r"^\s*microsoft\s+powerpoint\s*[-–—:：]\s*\S", t or "", re.I))


def is_vague_title(t: str) -> bool:
    """标题是否含糊到无法辨识是哪份资料（"概论"、"前言"、"Background"）。

    判定用**包含**而不是相等：真实文件名常带序号或后缀，
    例如 "01-概论.md" 的词干是 "01-概论"，不等于"概论"，
    用相等判断就会漏过。所以要剥掉序号/标点后再比对词表。

    刻意不做"太短就算含糊"这类长度启发式：
    真实的课题名可以很短（"镇痛药"只有 3 字却完全可用），
    长度启发式会把它们误判。宁可漏判也不要误判。
    """
    t = _clean_title(t).strip()
    if not t:
        return True
    low = t.lower()
    if low in _VAGUE_TITLES:
        return True
    core = re.sub(r"^[\s\d\-_、.]+", "", low)
    core = re.sub(r"[\s\d\-_、.]+$", "", core)
    return core in _VAGUE_TITLES


def _clean_title(t: str) -> str:
    """去掉标题里的导出工具前缀与首尾空白。"""
    t = (t or "").strip()
    # "Microsoft PowerPoint - 1 高血压ppt2025" -> "1 高血压ppt2025"
    t = re.sub(r"^microsoft\s+powerpoint\s*[-–—:：]\s*", "", t, flags=re.I)
    return t.strip()


def _score_title(t: str) -> tuple:
    """给候选标题打分，用于在"文件名"与"正文标题"之间择优。

    比较两件事：
    1. 有效字符数（长度近似信息量），但**先剔除尾部的年份**——
       "pharmacodynamic 202509" 里的 202509 不代表主题信息。
    2. 中英混排视为不利：同长度下纯中文标题更可读。

    必须承认这只是启发式，有它的边界：
      "胰岛素及口服降血糖药"(正文) 与 "胰岛素和口服降糖药-课件童擎一20241203"(文件名)
      两者都对，只是后者更长。这类差别不值得继续调参——
      真正要解决的是"概论"这种无法辨识的标题，而不是让每个标题都最优。
    """
    t = (t or "").strip()
    core = re.sub(r"[\s\-_]*\d{4,8}[\s\-_]*$", "", t)     # 尾部年份/编号
    core = re.sub(r"^\s*\d+[\s\-_、.]*", "", core)         # 开头序号
    n = len(core) or 1
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in t)
    has_latin = any(ch.isascii() and ch.isalpha() for ch in t)
    return (n, 0 if (has_cjk and has_latin) else 1, len(t))


def _contains_term(a: str, b: str) -> bool:
    """a 与 b 是否有明显的包含关系（用于避免"更长"只是加了噪声）。"""
    a, b = a.strip(), b.strip()
    if not a or not b:
        return False
    return a in b or b in a


def _title_of(path: Path, res: extract.ExtractResult) -> str:
    """决定文档标题。

    候选有三个，都可能有价值也都有可能是垃圾：
    - 文件名：人写的，通常最有辨识度（"抗心律失常药.pdf"），
      但也可能只是编号，或章号写错（"第二十五章　肾上腺皮质激素"）
    - 正文标题：作者写的，但可能只是"概论"、"一、心脏"，脱离上下文无意义
    - 元数据：经常是导出工具留下的 "Microsoft PowerPoint - xxx"、"Background"

    先剔除明显是垃圾的候选，再在剩下的里按 _score_title 择优。
    实测单靠"文件名优先"或"更长者胜"都会错：
      "第29章 肾上腺皮质激素类药物" 不应被换成章号写错的文件名
      "pharmacodynamic 202509" 不应赢过 "第三章 药物效应动力学"
    所以要按信息量评分，而不是定序。
    """
    stem = _clean_title(path.stem)

    heading = ""
    for b in res.blocks:
        if b.kind == "heading" and b.level <= 2 and 2 <= len(b.text) <= 120:
            heading = b.text.strip()
            break

    meta = _clean_title(res.meta.get("title") or "")

    cands = [c for c in (heading, stem, meta)
             if c and not _JUNK_TITLE.match(c) and len(c) > 1]
    if not cands:
        return stem or heading or meta or path.name
    # 分数相同时按列表顺序取第一个，即优先正文标题、其次文件名、最后元数据
    return max(cands, key=_score_title)


def ingest_file(conn: sqlite3.Connection, path: Path, force: bool = False,
                with_ocr: bool = True, verbose: bool = False,
                with_figures: bool | None = None,
                mode: str = "") -> dict:
    """处理单个文件，返回处理结果字典。

    `with_figures`：是否做**整页识图**（图片理解）。默认取
    `config.FIGURE_DESC`（`.env` 的开关）。识图是花钱的步骤，
    所以单独一个开关，且只在**文本确实入库成功**之后才跑
    （否则会给一份没进库的资料白花识图钱）。

    `mode`（卡 C-0088）：`""`（默认）= 逐字走改造前的原路；`"textbook"` =
    教材专线（按章切块 + 显式 `role='textbook'` + `chunk_scheme` 标记）。
    参数放在最后且带默认值，所以**既有调用方一个都不用改**（它们不传，
    走的就是原路）。只认这两个取值，其余取值直接报错而不是静默走老路——
    打错一个字母就悄悄按课件入库、把按章切块的事瞒下来，是最不该有的失败方式。
    """
    path = path.resolve()
    _mode = (mode or "").strip().lower()
    if _mode not in ("", TEXTBOOK_MODE):
        return {"path": _rel(path), "status": "failed",
                "reason": f"未知的 mode={mode!r}：只认 ''（默认，按课件入库）"
                          f"与 '{TEXTBOOK_MODE}'（教材专线）。"
                          f"打错的 mode 不做静默降级。"}
    # 教材专线：本函数内**只影响下面标注了「教材专线」的四处**，其余每一行
    # 与改造前逐字相同（回归对照见 tools/verify_c0088_regression.py）。
    is_textbook = (_mode == TEXTBOOK_MODE)
    # 教材专线写库时要附带的两个权威声明（空值 = 走改造前的原路）
    tb_role = TEXTBOOK_ROLE if is_textbook else ""
    tb_extra = ({"chunk_scheme": TEXTBOOK_CHUNK_SCHEME} if is_textbook else None)
    tb_diag: dict = {}
    kind = extract.detect_kind(path)

    # 转换/切分两个"前置改造"段落共用：哪一段生效就由哪一段填。
    # （放在这里是因为 legacy 的转换必须排在下面那个 `unsupported` 分支**之前**。）
    split_note = ""
    rel_override = ""
    convert_note = ""
    legacy_src = ""
    legacy_reason = ""

    # ---- 旧版 Office（.ppt/.doc/.xls）：先转成 OOXML，再按普通文件走 ----
    #
    # 与下面"一页拼了多张幻灯片的 PDF"是**同一个模式**，理由也一样：
    # 转换必须发生在**提取之前**，后面所有步骤（提取/识图/分析/切块）拿到的
    # 都是转换后的 OOXML。原文件**不在库里留记录**——入库记的是 `.pptx`，
    # 下次全量重导入时原 `.ppt` 会被再检测一次、再次指向同一个 `.pptx`，
    # 于是不会把同一份资料索引两遍。
    #
    # 与切分那段唯一的差别：这里**不删也不改源文件**（传 `-KeepOriginal`），
    # 产物与源文件同目录并存。切分产出的是"同一份内容的另一种排布"，
    # 而旧格式源文件删掉就没了——用户手上可能只有这一份。
    if kind == "legacy":
        try:
            # 转换是本地 COM 操作（不花钱），但一份实测 7~41 秒，不报进度的话
            # 看板上的进度条会停在原地让人以为卡死——与上面那段"切分"同一写法。
            progress.stage("转换旧版 Office",
                           note=f"{path.suffix} -> "
                                f"{extract.legacy_dest(path).suffix}（本机 Office 转换）")
            _conv = extract.ensure_ooxml(path, verbose=verbose)
        except Exception as e:  # noqa: BLE001
            # 转换只是"让旧格式能用"的手段：真出意外也绝不让它挡住整批，
            # 退回原路径按 unsupported 报出可操作的原因。
            _conv = {"ok": False, "dest": "", "status": "failed",
                     "note": f"{type(e).__name__}: {e}"}
        if _conv.get("ok") and _conv.get("dest"):
            legacy_src = _rel(path)
            path = Path(_conv["dest"]).resolve()
            kind = extract.detect_kind(path)
            rel_override = _rel(path)
            convert_note = (f"{legacy_src} 是旧版 Office 格式，"
                            f"已自动转换为 {path.name}"
                            f"（{'本次转换' if _conv.get('converted') else '复用已有产物'}，"
                            f"源文件保留）")
            if verbose:
                print(f"    {convert_note}", flush=True)
        else:
            legacy_reason = str(_conv.get("note") or "转换失败")

    def _tag(d: dict) -> dict:
        """给返回结果注明来源：这份资料是由哪份旧格式文件转来的。

        审计用（例如 `tools/reimport_all.py` 的日志、`ingest_runs.detail`）。
        非 legacy 走的是恒等路径——同一个 dict 原样返回，
        `.pptx/.pdf/.docx/.txt/图片` 的返回结构因此**逐字不变**。
        """
        if legacy_src:
            d.setdefault("converted_from", legacy_src)
        return d

    if kind in ("unknown", "legacy"):
        res = extract.extract(path)
        base = res.warnings[0] if res.warnings else "不支持的类型"
        if legacy_reason:
            # 转换没成功：`legacy_reason` 本身已经是**可操作**的说明
            # （`core/extract.py::ensure_ooxml` 里拼的"装 Office 或另存为 OOXML"
            # 两条路，卡 C-0069），这里只补一句提取器为什么也不认它。
            # 不静默跳过：status=failed 会进入库报告的失败计数。
            return _tag({"path": _rel(path), "status": "failed",
                         "reason": f"{legacy_reason}；{base}"})
        return _tag({"path": _rel(path), "status": "unsupported", "reason": base})

    # ---- 一页拼了多张幻灯片的 PDF：先切开，再按普通 PDF 走 ----
    #
    # 这种课件（实测"卢"系列）一页 A4 排 2×3 共 6 张 PPT：文字会被串成一段、
    # 一块里混着 6 张幻灯片、出处只能标到 `p.1`、识图把 6 张缩略图一起喂给模型。
    # 所以**在提取之前**就换成切好的版本，后面所有步骤（提取/识图/分析/切块）
    # 拿到的都是"一页一张"的正常 PDF。
    #
    # 原文件**不在库里留记录**——入库的是 `xxx_slides.pdf`，
    # 这样下次全量重导入时原文件会被再检测一次、再次指向切割版，不会重复索引。
    if kind == "pdf" and not path.stem.endswith("_slides"):
        try:
            from . import multippt as _mp
            if _mp.needs_split(path):
                dst = path.with_name(path.stem + "_slides.pdf")
                if not dst.exists():
                    progress.stage("切分多页幻灯片", note="一页里拼了多张 PPT")
                    _r = _mp.split(path, dst)
                    split_note = f"原 PDF 是'一页多张幻灯片'，已切出 {_r.get('slides', 0)} 张"
                else:
                    split_note = "原 PDF 是'一页多张幻灯片'，使用已切好的版本"
                path = dst.resolve()
                rel_override = _rel(path)
                if verbose:
                    print(f"    {split_note} -> {path.name}", flush=True)
        except Exception as e:  # noqa: BLE001
            # 切分只是增强：出问题就按原文件走，绝不让它挡住入库。
            split_note = ""
            if verbose:
                print(f"    多页幻灯片检测/切分失败，按原文件处理: "
                      f"{type(e).__name__}: {e}", flush=True)

    if with_figures is None:
        with_figures = bool(getattr(config, "FIGURE_DESC", 1))
    # 「整页切块」模式（2026-10-19）：只对 pdf/pptx 非教材课件生效，默认开。
    # 这里只读一次配置，整份文件的判断口径就固定下来（设置中心改值下次入库才生效，
    # 与其它设置项"下一次任务按新值跑"的既定口径一致）。
    page_mode = page_chunk_enabled(kind, is_textbook)

    content_hash = db.sha256_file(path)
    rel = rel_override or _rel(path)
    row = conn.execute(
        "SELECT id, content_hash, status, n_chunks FROM documents WHERE path=?",
        (rel,)
    ).fetchone()
    # "内容未变"才跳过——但**上次没成功入库的不算**（n_chunks=0）。
    # 否则一次失败会永久卡住这份资料：内容没变 → 永远跳过 → 再也没机会重试
    # （实测一本 452 页扫描教材因 OCR 报错失败后，修好了也重跑不了）。
    if (row and row["content_hash"] == content_hash and not force
            and int(row["n_chunks"] or 0) > 0):
        return _tag({"path": rel, "status": "skipped", "reason": "内容未变",
                     "doc_id": int(row["id"])})

    try:
        res = extract.extract(path)
    except extract.ExtractError as e:
        return _tag({"path": rel, "status": "failed", "reason": str(e)})
    except Exception as e:  # 解析器内部错误不应中断整批
        return _tag({"path": rel, "status": "failed",
                     "reason": f"{type(e).__name__}: {e}"})
    progress.stage("提取文字",
                   note=f"{len(res.blocks)} 块 / {res.char_count} 字符")

    # 图片型课件（零文字层）通道的统计（卡 C-0066）。非该路径时恒为空 dict，
    # 于是下面所有既有分支的行为**逐字不变**。
    zero_vision_stats: dict = {}

    # 扫描版 PDF：补一次 OCR
    scanned = res.meta.get("scanned_pages") or []
    if scanned and with_ocr and kind == "pdf":
        try:
            ocr_res = extract.ocr_pdf_pages(path, scanned)
            if ocr_res.blocks:
                res.blocks.extend(ocr_res.blocks)
                res.meta["ocr_pages"] = len(scanned)
            res.warnings.extend(ocr_res.warnings)
        except Exception as e:
            res.warnings.append(f"OCR 失败: {type(e).__name__}: {e}")

    if not res.blocks:
        # 关键区分：**OCR 报错**与"需要 OCR 但还没装"不是一回事。
        # 前者是异常，重跑就有机会成功；后者是环境缺失，重跑也没用。
        #
        # 早期实现不加区分，一律写入 partial 记录，于是失败的运行会在库里
        # 留下 content_hash 相同的记录，**后续重跑全被当成"内容未变"跳过**，
        # 用户即使修好了问题也永远重试不了（实测：一本 452 页扫描教材
        # 因 OCR 报 NameError 失败后，重跑直接被跳过）。
        # 所以 OCR 报错时不落库，让它下次能被重试。
        ocr_broken = any("OCR 失败" in w for w in res.warnings)
        if ocr_broken:
            return _tag({"path": rel, "status": "failed",
                         "reason": "; ".join(res.warnings)
                                   + "（未写入索引，修好后可重新入库）"})

        # ---- ★ 图片型课件（零文字层）：整页识图转文字（卡 C-0066）----
        #
        # 上面这一判在改造前是**终点**：提取到 0 块就落 failed、写"未提取到任何
        # 文本"。而"把每页图片变成文字"的能力（core/figures.py 的 render_pages +
        # describe_page）排在**这个失败点之后**——于是整页就是一张图、全片没有
        # 文字层的课件（实测「第五章 药物分布.pptx」47 页 / 8.96 MB / 0 字符）
        # 永远走不到那一步：能力明明在，却被一个判空分支挡死。
        #
        # 现在改成：**先真的试着把它读成文字**；读不出来再失败，
        # 且失败时说的是人话（为什么失败、怎么修、要不要花钱）。
        # 触发条件按卡的边界收窄到 pdf/pptx：其它类型（docx/txt/图片）保持
        # 改造前的语义与文案，一个字都不动。
        zero = None
        if kind in ("pdf", "pptx"):
            zero = _zero_text_vision_pass(conn, rel, path, kind, res, content_hash,
                                          with_figures=with_figures, verbose=verbose)
        if zero is not None:
            if zero["blocks"]:
                res.blocks = zero["blocks"]
                zero_vision_stats = dict(zero["stats"])
                res.meta["zero_text_vision"] = _zero_text_meta(zero_vision_stats)
                res.warnings.extend(zero["warnings"])
                progress.stage(
                    "提取文字",
                    note=f"零文字层课件：识图转出 {len(res.blocks)} 页文字 / "
                         f"{res.char_count} 字符")
                if verbose:
                    print(f"    零文字层课件 → 识图转文字："
                          f"{zero_vision_stats.get('described', 0)} 页新识别 / "
                          f"{zero_vision_stats.get('reused', 0)} 页复用缓存 / "
                          f"花费 ¥{float(zero_vision_stats.get('cost_cny') or 0):.4f}",
                          flush=True)
            else:
                # 硬失败：把可操作的原因同时写进 documents.warnings（看板会显示）
                # 与返回值的 reason（命令行 / 入库报告会显示）。
                res.warnings.append(zero["reason"])
                _upsert_doc(conn, rel, path, kind, path.stem, content_hash,
                            res, 0, 0, "failed",
                            role_override=tb_role, extra_patch=tb_extra)
                conn.commit()
                return _tag({"path": rel, "status": "failed",
                             "reason": zero["reason"], "zero_text": True,
                             "figures": dict(zero["stats"])})
        else:
            # 不适用（不是 pdf/pptx，或连页数都读不出来）：保持改造前的语义。
            status = "partial" if res.warnings else "failed"
            _upsert_doc(conn, rel, path, kind, path.stem, content_hash, res, 0, 0, status,
                        role_override=tb_role, extra_patch=tb_extra)
            conn.commit()
            return _tag({"path": rel, "status": status,
                         "reason": "; ".join(res.warnings) or "未提取到任何文本"})

    title = _title_of(path, res)
    n_pages = _page_count(res)

    # 先确保 `documents` 里有一行：识图（写 figure_desc）与全局分析都要 doc_id，
    # 而这两步都发生在切块之前——那时还不知道块数/字数。
    # **只新建、不覆盖**已有的行：中途失败也不会把一份正常资料改成"0 块"。
    doc_id = _ensure_doc_row(conn, rel, path, kind, title, content_hash)
    conn.commit()

    # ---- 整页识图（图片理解）----
    #
    # ⚠ 识图**排在切块之前**，这是刻意的顺序：
    # 识图描述里有"这页是三种药的对比表""这一页承接上一页的机制"这类
    # 正文里根本没有的结构线索，全局分析（下一步）要用它们。
    # 顺序反过来的话，模型只能看到干巴巴的文字，判不出页与页的关系。
    #
    # 代价与兜底：识图失败**不影响入库**——下一步拿不到描述就只用文字分析，
    # 再不行就退回原来的逐页推断（见 core/outline.py 的降级说明）。
    # 识图本身有缓存（图片哈希未变不重复调模型），重导入不会重复付费。
    fig_text: dict[int, str] = {}
    fig_errors: list = []
    # 识图的统计字典。**必须原样放进返回值的 `figures` 键**——
    # `tools/reimport_all.py` 与 `tools/find_missing_overviews.py` 都按
    # `r["figures"]["cost_cny"]` 这种结构取数。早先重构时把 `figures`
    # 换成了"重建了多少块"这个 int，那两个工具当场 AttributeError
    # （实测全量重导入在第一份就崩了）。要加新信息就新增键，别改老键的类型。
    fig_stats: dict = dict(zero_vision_stats)
    # 零文字层通道（卡 C-0066）已经把这一批页渲染过、也识过图了，
    # 所以那种情况下**不再跑这一段**：
    #   1. 它对零文字层文件本来就什么也不做——`extract_pages()` 抽不出任何
    #      文字 ⇒ `ctxs` 为空 ⇒ `ingest_document_figures` 立刻返回空统计；
    #   2. 而它开头会先 `render_pages()`（pptx 走 PowerPoint COM，几十秒）
    #      并调用 `clear_document_figures()`——白渲染一次不算，还平白多碰一次
    #      删块语句。本卡的红线明确要求不许碰既有识图产物，那就更不该走进去。
    # 有文字层的资料（`zero_vision_stats` 恒为空）走的仍是**原来的每一行**。
    if with_figures and kind in ("pdf", "pptx") and not zero_vision_stats:
        from . import figures as _fig

        progress.stage("识别图片", note="逐页调用视觉模型")
        try:
            _fig.clear_document_figures(conn, doc_id)
            # ★ 立刻提交（卡 C-0040）：这条 DELETE 是写语句，在
            # `isolation_level="IMMEDIATE"` 的连接上它已经拿到了写锁。
            # 不提交的话，写锁会一直握到识图结束——中间隔着整份解析、整页渲染
            # 与逐页模型调用（实测 134~243 秒），远超 busy_timeout，并发的
            # 另一个 worker 必然 `database is locked`。
            conn.commit()
            # ★ `max_calls` 显式传参（卡 C-0088 的防御性修复）。
            #
            # 病灶：`config.FIGURE_MAX_CALLS_PER_DOC`（默认 200，"每份最多为
            # 多少页花钱调视觉模型"）在改造前**全项目无人传参** ——
            # `figures.ingest_document_figures(max_calls=None)` 于是取 None、
            # `if max_calls:` 不成立、`want` 不截断。也就是说这个设置在设置
            # 中心里改多少遍都没有任何效果（死设置），而 452 页教材一旦叠上
            # 视觉识图就会**真的发 452 次调用**（方案文档 §2.3 的陷阱）。
            #
            # 取值口径（**这是本卡最容易踩错的一步**）：
            #   · 教材专线 → 传配置值（默认 200），雷就此拆掉；
            #   · 通用路径 → 传 `None`，即 `ingest_document_figures` 自己的
            #     默认值，与改造前"不传"**逐字等价**。
            # 为什么通用路径不能直接传配置值：那会把 >200 页课件的识图
            # 从"全部页"静默截成"前 200 页"——是行为变更，直接违反本卡
            # 第一硬要求（`mode=""` 逐字段不变）。所以这里**始终显式传参**，
            # 只是通用路径的取值恒等于默认值。
            max_calls = (int(config.FIGURE_MAX_CALLS_PER_DOC or 0)
                         if is_textbook else None)
            fs = _fig.ingest_document_figures(
                conn, doc_id, path, kind=kind, verbose=verbose,
                max_calls=max_calls,
                # ★ 整页切块模式：identified 描述**不单独建 p.N 图 块**，
                # 随后由 build_page_chunks 并进该页那一块（旧模式传 True，逐字不变）。
                store_chunks=not page_mode,
                # S05：把**已提取（含 OCR 追加）**的页文本传给识图，避免二次 extract。
                pre_extracted=_page_pre_from_res(res, path))
            if page_mode:
                # 只进日志，不改返回结构：让人一眼看出这一轮没建独立识图块。
                progress.stage("识别图片", note="有图页识图（描述并入整页块）")
            if is_textbook:
                tb_diag["figure_max_calls"] = max_calls
            fig_stats = dict(fs or {})
            fig_errors = list(fs.get("errors") or [])
            fig_text = _figure_texts(conn, doc_id, skip_no_info=page_mode)
            if "usable_pages" in fs:
                usable_pages = set(fs["usable_pages"])
                fig_text = {page: text for page, text in fig_text.items() if page in usable_pages}
        except Exception as e:  # noqa: BLE001
            fig_errors = [f"{type(e).__name__}: {e}"]
            # 同上：异常可能落在"写语句之后、commit 之前"，那时写锁还在手里，
            # 而后面紧接着是"分析结构"（要调模型，几十秒）。回滚掉，
            # 别让一个未决事务把锁带出这个 except。
            try:
                conn.rollback()
            except sqlite3.Error:
                pass

    # ---- AI 全局结构分析（画思维导图 → 给每页定路径）----
    #
    # 输入 = 每一页的标题 + 正文开头 + 识图摘要，**带上页号**。
    # 输出 = 每一页在导图中的完整路径，切块时当面包屑用。
    # 失败/关闭时 outline 为 None，切块退回原来的逐页推断，行为与改动前一致。
    outline = None
    ol_warn: list[str] = []
    # ★ 教材专线不跑这一步（卡 C-0088）。
    #
    # 理由：教材的分层来自**它自己的章节**，不需要模型去猜（`_should_analyze`
    # 的 docstring 早就这么写了）；而教材专线的切块由 `core/textbook.py` 按章
    # 完成，**根本不读 outline**。跑一次 analyze_deck 再把它丢掉，等于白花一次
    # 模型调用的钱、还多一段不确定的输出。`not is_textbook` 在通用路径上是
    # 短路为真的常量，所以这一行对改造前的行为没有任何影响。
    # ★ 教材专线不跑这一步（卡 C-0088）；**整页切块模式也不跑**（2026-10-19：
    # 用户明确要求"不再用大模型做结构拆分"）。两处都是短路常量，旧分支行为不变。
    if (not is_textbook) and (not page_mode) and _should_analyze(kind, n_pages):
        progress.stage("分析结构", note="大模型看完整份课件、画思维导图")
        try:
            from . import outline as _ol
            pin = _ol.pages_from_result(res, fig_text)
            outline = _ol.analyze_deck(conn, doc_id, path, title,
                                       pages=pin, verbose=verbose)
            ol_warn = list(outline.warnings or [])
            if not outline.pages:
                outline = None
        except Exception as e:  # noqa: BLE001
            ol_warn = [f"全局结构分析失败: {type(e).__name__}: {e}"]
            outline = None

    # ---- 切块：**教材与课件在这里分家**（卡 C-0088 的分岔点）----
    #
    # 改造前这里只有一行 `chunk_mod.chunk_blocks(...)`。现在按 `mode` 二选一：
    #   · 通用路径（`mode=""`）：**原样**调用，一行未改；
    #   · 教材专线：`core/textbook.py` 先认章（书眉 + 章号单调 + 目录交叉校验，
    #     零模型调用），再**逐章**调用同一个 `chunk.chunk_blocks()` ——
    #     长度参数（700/1100/120/80）与课件完全同一套代码，没有任何重写，
    #     只是每个 `_Packer` 只看得到一章的块，于是块在结构上不可能跨章。
    if is_textbook:
        from . import textbook as _tb

        progress.stage("识别章节", note="书眉 + 章号单调（零模型调用）")
        chapters = _tb.detect_chapters(res.blocks, book_title=title)
        chunks = _tb.cut_by_chapter(res.blocks, chapters, doc_meta=res.meta)
        # 如实记账：章数、降级与否、跨章块数。方案文档第四节第 4 条要求
        # "识别失败的文档如实降级并报告，不假装成功"——降级时
        # `fallback=True`，切块退回"整份书名一级"，这里照实写出来。
        tb_diag.update({
            "mode": TEXTBOOK_MODE,
            "chunk_scheme": TEXTBOOK_CHUNK_SCHEME,
            "book_title": chapters.book_title,
            "chapters": len(chapters.body),
            "fallback": bool(chapters.fallback),
            "notes": list(chapters.warnings),
        })
        if chapters.fallback:
            # 降级后所有块的 `heading_path` 都只有书名一级，跨章块这个指标
            # 无从谈起（判据要靠章级面包屑）——报 None，不报一个好看的 0。
            tb_diag["cross_chapter_chunks"] = None
        else:
            tb_diag["cross_chapter_chunks"] = len(
                _tb.cross_chapter_chunks(chunks, chapters, strict_hp=True))
        # 教材块的面包屑形状（验收用）：取前若干种，便于人一眼核对
        tb_diag["heading_path_kinds"] = sorted(
            {c.heading_path for c in chunks})[:6]
    elif page_mode:
        # ★ 整页切块（2026-10-19，默认开）：**每页一块**，识图文字并入同一块；
        # 不做 AI 结构分析（outline 恒为 None）、不建独立识图块。
        # 旧模式（开关关闭/教材专线/非课件）继续走下面那条**一行未改**的老路。
        progress.stage("整页切块", note="每页一块（识图并入文字，不做结构分析）")
        chunks = build_page_chunks(res.blocks, fig_text)
    else:
        chunks = chunk_mod.chunk_blocks(res.blocks, doc_meta=res.meta,
                                        outline=outline)
    progress.stage("切块嵌入", note=f"{len(chunks)} 块")
    if not chunks:
        _upsert_doc(conn, rel, path, kind, title, content_hash, res, 0, 0, "failed",
                    role_override=tb_role, extra_patch=tb_extra)
        conn.commit()
        return _tag({"path": rel, "status": "failed", "reason": "切块后为空"})

    # 嵌入文本 = 标题面包屑 + 正文。
    # 为什么要把标题一起嵌入：用户提问经常直接对应小节标题
    # （问"怎么让很深的网络训起来"，最该命中的就是「3.1.2 解决方案」）。
    # 实测只嵌入正文时，含答案的块相似度仅 0.30，挤不进前 5。
    # 存库的 text 仍然只是正文，标题单独存在 heading_path，展示与引用不受影响。
    vecs = None
    try:
        emb = get_embedder()
        texts = [f"{c.heading_path}\n{c.text}" if c.heading_path else c.text
                 for c in chunks]
        if verbose:
            print(f"    嵌入 {len(texts)} 块 ...", flush=True)
        vecs = embed_documents_cached(texts, embedder=emb, progress=verbose)
    except FileNotFoundError as e:
        return _tag({"path": rel, "status": "failed", "reason": str(e)})

    n_chars = sum(len(c.text) for c in chunks)

    progress.stage("写入库", note=f"{len(chunks)} 块")
    doc_id = _upsert_doc(conn, rel, path, kind, title, content_hash, res,
                         len(chunks), n_chars,
                         "ok" if not res.warnings else "partial",
                         role_override=tb_role, extra_patch=tb_extra)
    _write_chunks(conn, doc_id, title, chunks, res, path, vecs=vecs)
    conn.commit()

    warn = list(res.warnings) + ol_warn
    if tb_diag.get("notes"):
        # 教材专线的降级/异常照实上报（"不假装成功"，方案文档第四节第 4 条）
        warn.extend(f"教材专线：{n}" for n in tb_diag["notes"])
    if split_note:
        warn.append(split_note)
    if convert_note:
        warn.append(convert_note)
    if fig_errors:
        # ★ 区分"整页渲染失败"与"某几页识图失败"（卡 C-0069）。
        #
        # 渲染失败时 `stats["errors"]` 里**只有一条**，报成
        # "识图失败 1 页"会让人以为只是某一页运气不好——而真相是
        # 这份资料的**图注全缺**（实测 475 页的课件也只报"1 页"）。
        # 有文字层的资料正文已入库，这里只补一句"为什么、怎么办"。
        if len(fig_errors) == 1 and "整页渲染失败" in str(fig_errors[0]):
            warn.append(f"识图跳过（图注全缺）：{fig_errors[0]}")
        else:
            warn.append(f"识图失败 {len(fig_errors)} 页: {fig_errors[0]}")

    out = _tag({
        "path": rel, "status": "updated" if row else "added", "doc_id": doc_id,
        "chunks": len(chunks), "chars": n_chars, "kind": kind,
        "warnings": warn,
        # `figures` 保持是**统计字典**（调用方按这个结构取 cost_cny 等）
        "figures": fig_stats,
        "outline_pages": len(outline.pages) if outline is not None else 0,
        "outline_cost": outline.cost_cny if outline is not None else 0.0,
    })
    if zero_vision_stats:
        # 这份资料的正文字是**识图转出来的**（卡 C-0066）。显式标出来，
        # 免得下游把"全是 vision 块"当成异常；成本另见 figures.cost_cny。
        out["zero_text"] = True
        out["vision_blocks"] = int(zero_vision_stats.get("blocks") or 0)
    if page_mode:
        # 整页切块模式（2026-10-19）：**只增键**供对照/验收；
        # 旧模式（开关关闭 / 教材专线 / 非课件）返回值逐字段不变。
        out["chunk_mode"] = "page"
    if tb_diag:
        # 教材专线的回执（卡 C-0088）：章数 / 是否降级 / 跨章块数 / 面包屑形状。
        # **只增键**，且只在教材模式出现——通用路径的返回值逐字段不变。
        out["textbook"] = tb_diag

    # ---- 识图块按导图路径重挂 ----
    #
    # 识图是在全局分析**之前**跑的（那时还没有路径），所以它的块当时挂的是
    # 老面包屑；`_write_chunks` 又把该文档的块全部删掉重建了正文块，
    # 识图块也一并没了。这里按新路径重建一次，让正文块与识图块挂在
    # **同一个节点**下，检索时才会一起被捞出来。
    #
    # ★ 整页切块模式**不重挂**（2026-10-19）：识图文字已经并进每页那一块，
    # 库里不该再出现独立的 `p.N 图` 识图块（用户需求原话）。
    if fig_text and not page_mode:
        try:
            out["figures_rewritten"] = _rewrite_figure_chunks(
                conn, doc_id, title, fig_text, outline)
        except Exception as e:  # noqa: BLE001
            out["warnings"] = warn + [f"识图块重建失败: {type(e).__name__}: {e}"]

    return out


def _page_count(res) -> int:
    """这份资料有多少页（拿不到就返回 0）。"""
    meta = getattr(res, "meta", None) or {}
    for k in ("slides", "page_count", "pages"):
        try:
            n = int(meta.get(k) or 0)
            if n:
                return n
        except (TypeError, ValueError):
            continue
    pages = {getattr(b, "page", 0) for b in getattr(res, "blocks", [])}
    return max(pages) if pages else 0


def _zero_text_meta(stats: dict) -> dict:
    """零文字层通道的记账（卡 C-0066）：写进 `documents.extra`，也是成本凭证。

    `calls` 是**真正打出去的模型请求数**：`described`（首次调用就成功的页数）
    ＋ `empty_retried`（空输出后重试的次数）。命中缓存的页（`reused`）
    **不计入**——它们一个请求都没发，这正是"第二次跑不再花钱"的由来。
    """
    keys = ("pages", "described", "reused", "failed_pages", "cost_cny",
            "tokens_in", "tokens_out", "empty_retried", "model", "workers",
            "net_limit", "peak_inflight", "elapsed", "blocks", "chars",
            "outdir")
    out = {k: stats.get(k) for k in keys if stats.get(k) is not None}
    out["calls"] = (int(stats.get("described") or 0)
                    + int(stats.get("empty_retried") or 0))
    out["zero_text"] = True
    return out


def _zero_text_vision_pass(conn, rel: str, path: Path, kind: str,
                           res: extract.ExtractResult, content_hash: str,
                           with_figures: bool, verbose: bool) -> dict | None:
    """图片型课件（提取到 0 块）→ 逐页识图转文字。**返回 None 表示不适用。**

    调用点在 `ingest_file` 的 `if not res.blocks:` 里，所以**只有"一个字都
    提取不到"的资料会走这里**——有文字层的资料一个字都不受影响。

    返回 `{"blocks", "stats", "warnings", "reason"}`：

      - `blocks` 非空 → 通道成功，调用方拿它当正文块（`page=N`、`kind="vision"`）
        继续走原来的切块/嵌入/写库；
      - `blocks` 为空、`reason` 非空 → 硬失败，`reason` 是**可操作**的说明
        （为什么失败、怎么修、要不要花钱）。这条是卡 C-0066 的硬要求：
        不许再让用户看到误导性的"未提取到任何文本"；
      - 返回 None → 不适用（不是 pdf/pptx，或连页数都读不出来），
        调用方保持改造前的语义与文案。

    渲染与逐页调用全部复用 `core/figures.py`（`render_pages` / `describe_page`
    与既有的 `image_hash` 缓存、进程级并发闸门），页图落在既有的
    `<LOG_DIR>/figure_desc/<清洗后的课件名>/slide_NN.png` 约定上。
    """
    from . import figures as _fig

    n_pages = _page_count(res)
    if n_pages <= 0:
        return None
    # 先给用户一句话说清"这是什么情况"：这份文件没坏，它只是没有文字层。
    head = (f"这份课件共 {n_pages} 页且**全部是图片、没有文字层**"
            f"（提取到 0 个字，不是文件损坏）。")

    if not with_figures:
        return {"blocks": [], "stats": {}, "warnings": [],
                "reason": head + "识图（图片理解）当前是关着的，所以没有内容可入库。"
                                 "打开它就能自动把每页图片转成文字："
                                 "设置 → 入库 → 开启识图（或 .env 里 FIGURE_DESC=1），"
                                 "然后重新入库这份文件。"}

    title = _title_of(path, res)
    doc_id = _ensure_doc_row(conn, rel, path, kind, title, content_hash)
    conn.commit()

    r = _fig.ingest_pages_as_blocks(conn, doc_id, path, kind=kind,
                                    n_pages=n_pages, verbose=verbose)
    stats = dict(r.get("stats") or {})
    stats["doc_id"] = doc_id
    blocks = list(r.get("blocks") or [])

    if blocks:
        warnings: list[str] = []
        failed = list(stats.get("failed_pages") or [])
        if failed:
            shown = "、".join(str(p) for p in failed[:12])
            warnings.append(
                f"零文字层课件：识图转文字 {len(blocks)}/{n_pages} 页成功，"
                f"失败 {len(failed)} 页（第 {shown}"
                f"{' 等' if len(failed) > 12 else ''} 页）——"
                f"那几页这次没能入库；重跑只会补这几页，已识别的页有缓存、不重复花钱。")
        return {"blocks": blocks, "stats": stats, "warnings": warnings,
                "reason": ""}

    err = r.get("error") or "未知原因"
    kind_err = r.get("error_kind") or ""
    if kind_err == "no_key":
        reason = (head + "把每页图片转成文字要调识图模型，"
                  "而现在**没有读到可用的 API 密钥**。"
                  "配好密钥后重新入库即可：设置 → 入库 → API 密钥"
                  "（或环境变量 DEEPSEEK_API_KEY）。")
    elif kind_err == "render":
        # `err` 里已经带了 `core/figures.py::render_backend_problem()` 的
        # **可操作**说明（为什么失败 + 三条路，卡 C-0069），这里只补一句
        # 这份资料自身的处境：它是"一个字都提取不到"才走到这里来的。
        reason = (head + f"走识图通道时**整页渲染失败**：{err}")
    else:
        reason = (head + f"识图通道跑了 {int(stats.get('pages') or 0)} 页，"
                  f"但一页都没成功：{err}。"
                  "已识别的页有缓存，修好之后重跑**不会重复花钱**。")
    return {"blocks": [], "stats": stats, "warnings": [], "reason": reason}


def _should_analyze(kind: str, n_pages: int) -> bool:
    """这份资料要不要做全局结构分析。

    只对**课件**做：它是为"连续几页讲同一主题"设计的，而教材本身就有
    章节层级，不需要模型去猜；教材动辄几百页，也放不进一次调用。
    """
    if not getattr(config, "OUTLINE_ANALYSIS", 1):
        return False
    if kind not in ("pdf", "pptx"):
        return False
    if not n_pages or n_pages > int(getattr(config, "OUTLINE_MAX_PAGES", 120)):
        return False
    return True


def _figure_texts(conn, doc_id: int, *, skip_no_info: bool = False) -> dict[int, str]:
    """按页取回识图描述正文（`figure_desc` 表，不依赖切块）。

    `skip_no_info`（2026-10-19，默认 False = 旧行为逐字不变）：整页切块模式下
    跳过「本页无知识性图示」的页——那些页的文字层已经够了，把模型自报的
    "没有图示"再拼进每页正文只会污染检索；旧模式仍按原样返回（它后续会走
    `is_no_info` 分支，语义不变）。
    """
    out: dict[int, str] = {}
    try:
        rows = conn.execute(
            "SELECT page, description, no_info FROM figure_desc WHERE doc_id=? "
            "ORDER BY page", (doc_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return out
    for r in rows:
        d = (r["description"] or "").strip()
        if not d:
            continue
        if skip_no_info and int(r["no_info"] or 0):
            continue
        pno = int(r["page"])
        out[pno] = (out.get(pno, "") + "\n" + d).strip()
    return out


def _rewrite_figure_chunks(conn, doc_id: int, deck: str,
                           figtext: dict[int, str], outline) -> int:
    """按导图路径重建识图块（先删旧的，再按路径写回）。返回重建块数。"""
    from . import figures as _fig

    body_of = dict(figtext or {})
    if not body_of:
        return 0
    now = db.now()
    n = 0
    for pno in sorted(body_of):
        body = (body_of[pno] or "").strip()
        if not body:
            continue
        deletable = conn.execute(
            "SELECT id FROM chunks WHERE doc_id=? AND source_ref=?",
            (doc_id, f"p.{pno} 图")).fetchall()
        if deletable:
            # ★ 删完**立刻**落盘（卡 C-0040 第三轮复核补做）。
            #
            # 下面 `outline.breadcrumb/topic` 与 `_store_figure_chunks`（要跑
            # `split_description` 与逐块 ONNX 推理）都是 CPU 时间；把它们留在
            # "删旧块"这个写事务里，持锁时长就随描述长度增长。
            # 实测（真实 34 页课件、workers=2、进程内事务时间线）这里出现过
            # **2.77s** 的写锁窗口：事务里 DELETE 早就执行完了，
            # 时间全花在等 `_store_figure_chunks` 返回。
            # 先落盘之后，写锁只覆盖 DELETE 本身。
            #
            # 取舍：拆成"删 → 写"两个事务，中断时可能只删不写。
            # 这与本卡"每页一短事务"的取舍同一口径，且不会留脏数据：
            # 下次重跑本函数会按同一个 `source_ref` 再删一遍再写。
            conn.executemany("DELETE FROM chunks WHERE id=?",
                             [(int(old["id"]),) for old in deletable])
            db.bump_data_revision(conn)   # GPT-F：删识图块与 bump 同一事务
            conn.commit()
        head = outline.breadcrumb(pno) if outline is not None else ""
        topic = outline.topic(pno) if outline is not None else ""
        n += _fig._store_figure_chunks(
            conn, doc_id, deck, pno, head.split(" > ")[-1] if head else "",
            body, now, crumb=head, topic=topic)
        # ★ 每页一个**短事务**（卡 C-0040 打回补做）。
        #
        # 原来整个循环只在末尾 commit 一次，而每轮都要给这一页算一次 ONNX 向量
        # ⇒ 15~32 页全部落在**同一个写事务**里，写锁被连续持有十几到二十几秒
        # （大管家真实路径探针实测 max_hold=20.58s；busy_timeout 是 30s，
        # 余量只剩 31%，页数一多就是用户最初看到的 `database is locked`）。
        # 逐页提交后，单次持锁只剩 INSERT 的毫秒级，且与页数无关。
        #
        # 取舍（与识图段"每页一短事务"同一口径）：中断时库里留下的是
        # "已经重挂好的那几页"，而不是"一页都没有"。这不是损失：
        # `_store_figure_chunks` 写的是带 `source_ref` 的识图块，下次重跑时
        # 本函数会先按 `source_ref` 删掉旧的再写新的（上面的 DELETE），
        # 所以重复执行既不会重复计数，也不会留下脏块。
        conn.commit()
    # 兜底：循环里一页都没写（body 全空）时，也别把上游的未决事务带走
    conn.commit()
    return n


def _ensure_doc_row(conn, rel: str, path: Path, kind: str, title: str,
                    content_hash: str) -> int:
    """确保 `documents` 里有一行并返回 doc_id。**只新建，不覆盖已有的行。**

    为什么不能用 `_upsert_doc`：识图需要 doc_id，而这一步发生在切块之前，
    那时还不知道块数/字数。若此时把已有行改成"0 块"，一旦中途失败，
    一份本来正常的资料在库里就变成了"空文档"。

    corpus 这里也**显式写入**（同一份 rel 判定，与 `_upsert_doc` 结果必然一致）：
    这一行会立刻 commit，若后续识图/切块中途抛异常就留在库里，
    届时靠 `DEFAULT 'main'` 的话，一份 aux 资料会被静默记成 main。
    """
    row = conn.execute("SELECT id FROM documents WHERE path=?", (rel,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO documents(path, abs_path, kind, title, content_hash, "
        "status, corpus, indexed_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (rel, str(path), kind, title, content_hash, "partial",
         db.classify_corpus(rel), db.now(), db.now()))
    return int(cur.lastrowid)


def _write_chunks(conn, doc_id: int, title: str, chunks: list,
                  res, path: Path, vecs=None) -> int:
    """把一个文档的切块写进库（先删旧的，再插新的 + 向量）。返回写入块数。

    注意：它会把该文档**所有**块删掉重建，识图块也不例外——
    所以调用方在全局结构分析之后要按新路径把识图块重挂一遍
    （见 `ingest_file` 末尾与 `_rewrite_figure_chunks`）。

    `vecs` 为 None 时现场计算嵌入（调用方没算过的话）。
    """
    now = db.now()
    # ★ 删旧块必须排在"现场嵌入"**之后**（卡 C-0040 打回补做）：
    # vecs 为 None 时要在这里批量跑一次 ONNX 推理（几百毫秒~数秒），
    # 而 `DELETE` 是写语句、在 `isolation_level="IMMEDIATE"` 的连接上会立刻
    # 拿到写锁 ⇒ 先删就等于"在写事务里跑推理"，与
    # `figures.py::_store_figure_chunks` 是同一个坑。
    # 正常路径（`ingest_file` 已经把 vecs 算好传进来）不会走到这里。
    if vecs is None and chunks:
        try:
            emb = get_embedder()
            texts = [f"{c.heading_path}\n{c.text}" if c.heading_path else c.text
                     for c in chunks]
            vecs = embed_documents_cached(texts, embedder=emb)
        except Exception:  # noqa: BLE001
            vecs = None
    conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    n = 0
    for i, c in enumerate(chunks):
        # 面包屑兜底：无标题的文档（如 .txt 笔记）用文档标题占位，
        # 保证每个切块都有可用于定位与展示的 heading_path。
        heading = c.heading_path or title
        # 节路径（AI 全局分析给出）：检索层做跨页分组用。老管线没有，写 NULL。
        topic = (c.meta or {}).get("topic") or None
        cur = conn.execute(
            """
            INSERT INTO chunks(doc_id, ord, text, text_norm, heading_path, topic,
                               source_ref, char_len, token_est, content_hash,
                               created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (doc_id, c.ord, c.text, db.normalize_for_match(c.text), heading,
             topic, c.source_ref, len(c.text),
             int(len(c.text) * TOKEN_PER_CHAR), db.sha256_text(c.text), now),
        )
        cid = int(cur.lastrowid)
        if vecs is not None and i < len(vecs):
            conn.execute(
                "INSERT INTO embeddings(chunk_id, model, dim, vec) VALUES(?,?,?,?)",
                (cid, config.EMBED_MODEL_REPO, int(vecs.shape[1]), to_blob(vecs[i])),
            )
        n += 1
    # P1-A / S08：chunks 写入成功的同一事务内推进应用数据版本（失败不提交即不 bump）。
    db.bump_data_revision(conn)
    conn.commit()
    return n


def _upsert_doc(conn: sqlite3.Connection, rel: str, path: Path, kind: str,
                title: str, content_hash: str, res: extract.ExtractResult,
                n_chunks: int, n_chars: int, status: str,
                role_override: str = "", extra_patch: dict | None = None) -> int:
    """写 `documents` 那一行。

    `role_override` / `extra_patch`（卡 C-0088，教材专线专用；两个都为空时
    本函数的每一步与改造前**逐字相同**）：

      · `role_override` 非空 → **显式声明**角色，不再问 `db.classify_role`
        的标题启发式。方案的已定项原话是"让『教材入库』这个按钮本身成为
        权威声明，而不是靠文件名里有没有『教材/第N版』字样去猜"。
      · `extra_patch` → 合并进 `documents.extra`，**只增键、不动任何老键**。
        教材专线用它打 `chunk_scheme="textbook-v1"`。
    """
    now = db.now()
    st = path.stat()
    extra = {k: v for k, v in res.meta.items() if k != "pdf_metadata"}
    if extra_patch:
        extra.update(extra_patch)
    # 角色（教材/课件）在摄入时就判定并存下来。
    # 判据是"教学角色"而不是文件扩展名——讲课用的 PDF 导出件也属课件。
    # 教材专线走 `role_override`（显式声明），通用路径仍按老判据算。
    role = role_override or db.classify_role(title, rel)
    # 库归属（main/aux）同样在摄入时判定并**显式写入**。
    #
    # 为什么不能靠 ALTER TABLE 时那个 `DEFAULT 'main'`：
    # 依赖默认值的话，新增的 aux 文档会**静默变成 main**——试题漏进 AI 问答
    # 且没人会发现。role 列踩过同一个坑，解法就是"判定规则放 db.py、
    # 回填与写入调同一个函数"，corpus 完全照抄这个范式。
    corpus = db.classify_corpus(rel)
    row = conn.execute("SELECT id FROM documents WHERE path=?", (rel,)).fetchone()
    if row:
        conn.execute(
            """
            UPDATE documents SET abs_path=?, kind=?, title=?, content_hash=?,
                size_bytes=?, mtime=?, n_chunks=?, n_chars=?, status=?, role=?,
                corpus=?, warnings=?, extra=?, indexed_at=?, updated_at=? WHERE id=?
            """,
            (str(path), kind, title, content_hash, st.st_size, st.st_mtime,
             n_chunks, n_chars, status, role, corpus,
             json.dumps(res.warnings, ensure_ascii=False),
             json.dumps(extra, ensure_ascii=False, default=str), now, now,
             int(row["id"])),
        )
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO documents(path, abs_path, kind, title, content_hash, size_bytes,
            mtime, n_chunks, n_chars, status, role, corpus, warnings, extra,
            indexed_at, updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (rel, str(path), kind, title, content_hash, st.st_size, st.st_mtime,
         n_chunks, n_chars, status, role, corpus,
         json.dumps(res.warnings, ensure_ascii=False),
         json.dumps(extra, ensure_ascii=False, default=str), now, now),
    )
    return int(cur.lastrowid)


def ingest_paths(conn: sqlite3.Connection, paths: list[Path], force: bool = False,
                 with_ocr: bool = True, verbose: bool = False,
                 with_figures: bool | None = None) -> IngestReport:
    files = iter_files(paths)
    rep = IngestReport()
    started = db.now()
    if verbose:
        print(f"共 {len(files)} 个文件", flush=True)
    for i, f in enumerate(files, start=1):
        if verbose:
            print(f"[{i}/{len(files)}] {f.name}", flush=True)
        # 进度的生命周期在这里管：`ingest_paths` 是所有入口的必经之路
        # （看板按钮、reimport_all、命令行都走它），在这里 begin/end
        # 各入口就都自动有了进度，不用每个入口各写一遍。
        progress.begin(f.name, files_total=len(files))
        try:
            r = ingest_file(conn, f, force=force, with_ocr=with_ocr,
                            verbose=verbose, with_figures=with_figures)
        except Exception as e:  # noqa: BLE001
            # 入库抛异常时也要收尾，否则看板上的进度条会永远停在"进行中"。
            progress.end("failed", error=f"{type(e).__name__}: {e}")
            raise
        progress.file_done(i)
        rep.details.append(r)
        st = r["status"]
        if st == "added":
            rep.added += 1
            rep.chunks += int(r.get("chunks") or 0)
        elif st == "updated":
            rep.updated += 1
            rep.chunks += int(r.get("chunks") or 0)
        elif st == "skipped":
            rep.skipped += 1
        elif st == "unsupported":
            rep.unsupported += 1
        elif st == "partial":
            # 已写入能提取到的部分，但不是硬失败：缺 OCR 或提取不完整
            rep.needs_ocr += 1
            rep.chunks += int(r.get("chunks") or 0)
        else:
            rep.failed += 1
        if st in ("added", "updated", "partial"):
            # 库归属按 path 现算：与 `_upsert_doc` 写库时用的是同一个函数、
            # 同一个 rel，所以报告里的数字与库里的 corpus 必然一致。
            # 只统计真正写进库的（skipped/unsupported/failed 不入库，不计）。
            cp = db.classify_corpus(r.get("path") or "")
            rep.added_by_corpus[cp] = rep.added_by_corpus.get(cp, 0) + 1
        if verbose and st not in ("skipped",):
            note = r.get("reason") or f"{r.get('chunks', 0)} 块"
            print(f"    -> {st}: {note}", flush=True)
            for w in r.get("warnings") or []:
                print(f"    !! {w}", flush=True)

    conn.execute(
        """
        INSERT INTO ingest_runs(started_at, ended_at, added, updated, skipped,
                                failed, detail)
        VALUES(?,?,?,?,?,?,?)
        """,
        (started, db.now(), rep.added, rep.updated, rep.skipped,
         rep.failed + rep.needs_ocr,
         json.dumps(rep.details, ensure_ascii=False, default=str)),
    )
    conn.commit()
    # 全部跑完。**失败也要收尾**——否则看板上的进度条会一直转下去。
    progress.end("done" if not rep.failed else "failed",
                 error=(f"{rep.failed} 个文件失败" if rep.failed else ""))
    return rep


def _page_pre_from_res(res, path: Path) -> dict:
    """S05：把 ingest 已提取的页文本（含 OCR 追加）整理成 figures.extract_pages 口径。

    纯数据副本，不共享可变 extract 对象；未传/开关关闭时 figures 侧回退旧路径。

    ⚠ **混合课件的纯图片页必须补出上下文**：只按 blocks 分组会漏掉"第 1 页文字+
    图片、第 2 页只有图片、第 3 页文字"里的第 2 页（extract 对无文字页不产 block）。
    figures 的旧路径用 `fill_empty_pages=True` 补页；本函数按 `res.meta` 的
    `page_count/slides/pages` 同样补出 1..N，所有页都进视觉上下文（空 title/body）。
    这不会改变 OCR 口径：body 仍来自实际入库 blocks（含 OCR 追加文本）。
    """
    meta = getattr(res, "meta", {}) or {}
    title = str(meta.get("title") or path.stem).strip() or path.stem
    by_page: dict = {}
    max_page = 0
    for b in getattr(res, "blocks", []) or []:
        try:
            page = int(getattr(b, "page", 0) or 0)
        except (TypeError, ValueError):
            continue
        if page <= 0:
            continue
        max_page = max(max_page, page)
        rec = by_page.setdefault(page, {"page": page, "title": "", "body": []})
        text = str(getattr(b, "text", "") or "")
        if getattr(b, "kind", "") == "heading" and not rec["title"]:
            rec["title"] = text
        else:
            rec["body"].append(text)
    total = max_page
    for key in ("page_count", "slides", "pages"):
        raw = meta.get(key)
        if raw is None:
            continue
        if isinstance(raw, (list, tuple, dict)):
            n = len(raw)
        else:
            try:
                n = int(raw or 0)
            except (TypeError, ValueError):
                n = 0
        total = max(total, n)
    pages = []
    for p in range(1, total + 1):
        rec = by_page.get(p) or {"page": p, "title": "", "body": []}
        pages.append({"page": p, "title": rec["title"],
                      "body": "\n".join(rec["body"])})
    return {"deck_title": title, "page_count": total, "pages": pages}
