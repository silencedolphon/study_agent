# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。     
# File-ID: SA-93-c0093tch   (project study_agent; keep this line)
"""教材挂载后端（卡 C-0093）：知识点 → 教材原文；教材按需单页渲染。

人的原话（2026-10-14）：「在知识点管线开新功能：总结了知识点后，能够根据知识点
联系到教材上的内容并可以挂载到知识点窗口上。知识点窗口有按钮能够打开新的页面
显示教材对应页。知识点窗口里要点分一个框，在要点框的旁边再有一个框是教材对总结
的知识点内容的补充。」

**本模块只做后端能力，一行前端都不碰**（知识点窗口的"要点框 + 教材补充框 +
打开教材页按钮"由后续 UI 卡统一做，避免与正在跑的界面改造方案冲突）。
两条能力：

  ① `kp_textbook()`：给一条末级知识点，回两样：
     · `material`          —— 这条知识点**自己的原料块原文**
                              （产物 `material[].chunk_id` → `chunks.text`）；
     · `textbook.passages` —— **当前学科库里的教材**中与这条知识点相关的原文段落
                              （带页码 / `source_ref` / 章名 / 命中的词）。
  ② `page_png()`：把教材的指定页渲染成 PNG（PyMuPDF，**零模型调用**）。

## 实测锚点（2026-10-14，默认库=药理；复核过卡面给的每一条）

- **教材** = `documents` 里 `role='textbook'` 且 `kind='pdf'`：doc#91
  《药理学 第6版 刘晓东》，538.8 MB / 452 页。它是**扫描件、没有文字层**
  （PyMuPDF 在 p.1 / p.12 / p.100 上 `get_text()` 全为空串），库里的 882 块
  来自入库时的识图通道——所以"教材原文"只能从 `chunks.text` 取，
  **不能**指望现开 PDF 抽文本。
- ⚠ 产物 `material[].chunk_id` **不是教材块**，它指向**这条知识点所属文档自己**
  的块：实测 `66-末级知识点总结.json` 的 chunk#25957 属 doc#66（课件
  `3抗癫痫药和抗惊厥药.pptx`，`role=courseware`）。默认库里**没有任何一条**
  知识点的 material 指向教材 doc#91（教材块 id 区间是 35077..35958，产物里
  一个都没有）。所以"教材补充框"不能只靠 material —— 必须再在教材里定位一次，
  这正是本模块 ① 的第二件事。
- 教材块的 `source_ref` 是**页码范围**（`p.202-214`；实测 882 块里页跨度中位数
  4~5 页，只有 54 块是单页），只能当"大致位置"用；要精确看到某一页得靠
  `page_png` 渲染出来让人自己翻。返回里如实标注 `page_exact: false`。
- 教材块有 `heading_path`（43 个去重值），形如
  `药理学 第6版 刘晓东 > 第19章 抗高血压药` —— **章名可用**，本模块拿它做
  章级加成（知识点讲"癫痫"时，`第11章 抗癫痫药` 那一章的块整体提权）。
- 单页渲染实测（doc#91）：打开文档 1~2 ms（PyMuPDF 惰性打开，不读全文件）、
  `get_pixmap(dpi=110)` 41~50 ms、写盘 55~191 ms；冷路径（每次重开+渲染+写盘）
  77~118 ms。零模型成本。**不缓存也能用**，但仍做磁盘缓存：同一页第二次请求
  只要读文件（个位数毫秒）。

## 安全边界（本卡最重要的红线）

`page_png()` **只放行教材类 PDF**，且路径一律从库里按 `doc_id` 取：
  1. 前端只能给 `doc_id` 与 `page`，**不接受任何路径片段**；
  2. 该 doc 必须是 `role='textbook'` **且** `kind='pdf'`；
  3. 取到的路径 `resolve()` 之后必须落在 KB 根目录（`config.ROOT`）之内
     —— 库里的 `abs_path` 万一被写成 `..\\..\\Windows\\...` 也照样被拒；
  4. 后缀必须是 `.pdf`，文件必须存在；
  5. 页码必须落在 `1..page_count`，越界给**可操作**提示（"该教材共 452 页"）。
任何一条不满足都**不抛异常**，返回 `{"ok": False, "code": …, "error": …}`，
由路由层转成 4xx/5xx——与 `/img/slide` 的降级风格一致。

## 为什么不用 `core/figures.render_pages()`

它就是 PyMuPDF 的 pdf 分支（同一个引擎、同一个 DPI 默认值），本模块的
`_render_png()` 与它逐字同源，只差两点：① 它每次 `_pg.tick()` 写全局进度快照，
而"点开教材某一页"不该去动入库进度条；② 它的落盘名是 `slide_<页号>.png`，
那是识图通道的私有约定，教材页图不该混进 `logs/figure_desc/`。
所以这里另写 10 行，**`core/figures.py` 一个字节都没改**。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
from pathlib import Path

from . import config
from .pipeline_runtime import context as runtime_context

# ---------------- 常量 ----------------

# 页图缓存目录：`<当前学科 LOG_DIR>/teach_pages/<doc_id>/p<页号>@<dpi>.png`。
# 落在**当前学科**目录下（切库后自然跟着走），与 `logs/figure_desc/` 分开。
TEACH_PAGE_DIRNAME = "teach_pages"
# 渲染分辨率：识图通道默认 110，教材是扫描件、小字多，这里默认给 150
# （实测 1240x1753、每页 0.4~1.1 MB；110 是 910x1285、0.26~0.7 MB）。
TEACH_DPI = 150
TEACH_DPI_MIN, TEACH_DPI_MAX = 72, 300
# 缓存文件数上限：教材最长 452 页（150 DPI 下每页 0.4~4.4 MB，多数 ~1 MB），
# 520 = 整本都能缓存、再留一点余量；超过就按 mtime 从旧到新删。
# ⚠ 最坏情况这个目录会占 ~0.5 GB（整本都被翻过一遍）——这是有意为之：
# 课本页图重渲染一次 0.1~0.2 秒，人连着翻页时不该每页都等。
TEACH_CACHE_MAX_FILES = 520

# 教材段落：默认给 3 段，最多 8 段（界面一次也放不下更多）。
TEACH_DEFAULT_PASSAGES = 3
TEACH_MAX_PASSAGES = 8
# 分数相对截断：低于"最佳段落 × 这个比例"的一律不返回。
# 没有它的时候实测会出现"前两段是第11章抗癫痫药、第三段跳到第38章抗寄生虫药"
# 这种噪声（只因为共用了一个"神经功能紊乱"）。宁可少给一段，也不给看着像错的。
TEACH_MIN_SCORE_RATIO = 0.4
# 跨章门槛：不在最佳段落那一章里的，要拿到"最佳段落 × 这个比例"才进榜
# （同章 0.4、跨章 0.6，两道一起把"蹭词"的段落挡在外面）。
TEACH_CROSS_CHAPTER_RATIO = 0.6
# 原料块：默认回 12 块，最多 50 块（实测一条知识点的 material 多在 10 块上下）。
TEACH_DEFAULT_MATERIAL = 12
TEACH_MAX_MATERIAL = 50
# 单块正文与一次响应的总字数上限（返回给界面看，不是给模型读的）。
TEACH_BLOCK_CHARS = 1200
TEACH_TOTAL_CHARS = 16000
# 同一章最多选几段：避免三段全是同一章相邻的块（内容重复、看着像 bug）。
TEACH_MAX_PER_CHAPTER = 2

# 渲染串行锁：同一时刻只渲染一页。**不是** dashboard 的全局 `_lock`
# （那会把"点一页教材"排到几十分钟的入库后面去，见卡面红线）。
_RENDER_LOCK = threading.Lock()
# PDF 页数缓存：{(路径, size, mtime_ns): 页数}。打开 538 MB 的 PDF 实测只要
# 1~2 ms，缓存只是省掉重复打开，顺带让"越界拒绝"不必每次开文件。
_PC_CACHE: dict = {}
_PC_LOCK = threading.Lock()
_PC_CACHE_MAX = 16


class TeachError(Exception):
    """带 HTTP 状态码的拒绝/失败（调用方直接把它翻译成响应，不必猜异常类型）。"""

    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = str(message)
        self.code = int(code)


# ---------------- 教材类文档（当前学科库） ----------------

def textbook_docs(conn: sqlite3.Connection) -> list[dict]:
    """当前库里**可以当教材**的 PDF：`role='textbook'` 且 `kind='pdf'`。

    排序：main 库优先（教材正文），其次块数多的在前。
    多学科下这个查询天然只看**当前库**：`conn` 是切库后重绑的 `config.DB_PATH`
    开出来的连接（见 `core/subjects.py::_bind`）。
    """
    try:
        rows = conn.execute(
            """
            SELECT id, title, path, abs_path, kind,
                   COALESCE(role,'courseware')   AS role,
                   COALESCE(corpus,'main')       AS corpus,
                   COALESCE(n_chunks,0)          AS n_chunks,
                   COALESCE(size_bytes,0)        AS size_bytes
            FROM documents
            WHERE COALESCE(role,'courseware')='textbook' AND kind='pdf'
            ORDER BY (COALESCE(corpus,'main')<>'main'), n_chunks DESC, id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def pick_textbook(conn: sqlite3.Connection, tb_doc_id: int = 0) -> dict | None:
    """挑一本教材：给了 `tb_doc_id` 就认它（仍要过 `resolve_textbook_pdf` 的闸），
    没给就取 `textbook_docs()` 的第一本。没有教材返回 None。"""
    docs = textbook_docs(conn)
    if not docs:
        return None
    if tb_doc_id:
        for d in docs:
            if int(d["id"]) == int(tb_doc_id):
                return d
        return None
    return docs[0]


def _kb_root() -> Path:
    """KB 根目录（可写数据根）。`subjects\\<学科>\\raw\\...` 也在它下面，
    所以判据只有一条：解析后的真实路径必须是它的子路径。"""
    try:
        return Path(config.ROOT).resolve()
    except OSError:  # pragma: no cover - 极端情况（磁盘掉线）
        return Path(config.ROOT)


def resolve_textbook_pdf(conn: sqlite3.Connection, doc_id) -> tuple[Path, dict]:
    """`doc_id` → 教材 PDF 的真实路径。**这是本卡最重要的安全闸**。

    五道检查（顺序即"拒绝的优先级"，错误文案都写成人能照着做的话）：
      ① doc_id 是正整数；
      ② 库里有这一行；
      ③ `role='textbook'` 且 `kind='pdf'`（★ 只放行教材类 PDF）；
      ④ 路径 `resolve()` 后落在 KB 根目录内（防 `..\\..` 目录穿越，
         也防库里存了一条指向系统目录的绝对路径）；
      ⑤ 后缀 `.pdf` 且文件真的在。

    失败一律抛 `TeachError`（403 = 不许 / 404 = 查无此文档 / 400 = 参数错）。
    """
    try:
        did = int(doc_id)
    except (TypeError, ValueError):
        raise TeachError("doc_id 必须是整数", 400) from None
    if did <= 0:
        raise TeachError("doc_id 必须是正整数", 400)

    row = conn.execute(
        """
        SELECT id, title, path, abs_path, kind,
               COALESCE(role,'courseware') AS role,
               COALESCE(corpus,'main')     AS corpus,
               COALESCE(n_chunks,0)        AS n_chunks
        FROM documents WHERE id=?
        """,
        (did,),
    ).fetchone()
    if row is None:
        raise TeachError(f"库里没有 id={did} 的资料", 404)
    info = dict(row)

    if str(info.get("role") or "") != "textbook":
        raise TeachError(
            f"「{info.get('title') or info.get('path') or did}」不是教材类资料"
            "（role=%s）——本接口只放行教材类 PDF" % (info.get("role") or "未知"),
            403)
    if str(info.get("kind") or "").lower() != "pdf":
        raise TeachError(
            f"「{info.get('title') or did}」不是 PDF（kind=%s）——"
            "只有 PDF 教材能按页渲染" % (info.get("kind") or "未知"), 403)

    raw = str(info.get("abs_path") or "").strip() or str(info.get("path") or "").strip()
    if not raw:
        raise TeachError(f"库里的资料 {did} 没有记录文件路径", 404)
    p = Path(raw)
    if not p.is_absolute():
        p = Path(config.ROOT) / raw
    try:
        real = p.resolve()
    except OSError:
        raise TeachError(f"路径无法解析：{raw}", 400) from None
    root = _kb_root()
    if real != root and root not in real.parents:
        raise TeachError(
            "拒绝：该资料在库里的路径落在资料库目录之外（疑似被改过），"
            "教材页渲染只读资料库内的文件", 403)
    if real.suffix.lower() != ".pdf":
        raise TeachError(f"拒绝：不是 .pdf 文件（{real.name}）", 403)
    if not real.is_file():
        raise TeachError(
            f"教材源文件不在了：{real}（可能被移动或删除；重新入库即可恢复）", 404)
    return real, info


# ---------------- 按需单页渲染（PyMuPDF，零模型调用） ----------------

def pdf_page_count(src: Path) -> int:
    """PDF 页数（打不开返回 0）。按 (路径, size, mtime) 缓存。"""
    try:
        st = src.stat()
        key = (str(src), int(st.st_size), int(st.st_mtime_ns))
    except OSError:
        return 0
    with _PC_LOCK:
        hit = _PC_CACHE.get(key)
    if hit is not None:
        return int(hit)
    n = 0
    try:
        import pymupdf
        d = pymupdf.open(str(src))
        try:
            n = int(d.page_count)
        finally:
            d.close()
    except Exception:  # noqa: BLE001 - 打不开就当 0，由调用方给"读不了"的提示
        n = 0
    with _PC_LOCK:
        if len(_PC_CACHE) >= _PC_CACHE_MAX:
            _PC_CACHE.clear()
        _PC_CACHE[key] = n
    return n


def _render_png(src: Path, page: int, dpi: int) -> bytes:
    """渲染一页为 PNG 字节（与 `core/figures.py::render_pages` 的 pdf 分支同源）。

    直接 `pix.tobytes("png")` 出字节，不绕 PIL：少一次解码/编码，
    实测比 `Image.frombytes(...).save()` 那条路略快，且不需要临时文件。
    """
    import pymupdf
    doc = pymupdf.open(str(src))
    try:
        if page < 1 or page > doc.page_count:
            raise TeachError(f"页码越界：{page}（该教材共 {doc.page_count} 页）", 400)
        pix = doc.load_page(page - 1).get_pixmap(dpi=dpi)
        return pix.tobytes("png")
    finally:
        doc.close()


def page_cache_path(doc_id: int, page: int, dpi: int) -> Path:
    """页图缓存落点：`<当前学科 LOG_DIR>/teach_pages/<doc_id>/p<页号>@<dpi>.png`。

    dpi 进文件名：以后改了默认分辨率，旧图不会被当成新图复用（宁可重渲染）。
    """
    return (Path(config.LOG_DIR) / TEACH_PAGE_DIRNAME / str(int(doc_id))
            / f"p{int(page):04d}@{int(dpi)}.png")


def _prune_cache(d: Path) -> int:
    """缓存目录超过上限就按 mtime 从旧到新删（粗粒度 LRU）。返回删除数。"""
    try:
        files = [p for p in d.glob("*.png") if p.is_file()]
    except OSError:
        return 0
    if len(files) <= TEACH_CACHE_MAX_FILES:
        return 0
    staged = []
    for p in files:
        try:
            staged.append((p.stat().st_mtime_ns, p))
        except OSError:
            continue
    staged.sort()
    removed = 0
    for _, p in staged[:len(staged) - TEACH_CACHE_MAX_FILES]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def page_png(conn: sqlite3.Connection, doc_id, page, dpi=None) -> dict:
    """教材某一页的 PNG（**零模型调用**）。返回 dict，拒绝时抛 `TeachError`。

    返回：`{"png": bytes, "page": n, "pages": 总页数, "cache": "hit|miss",
            "ms": 本次耗时毫秒, "dpi": dpi, "doc_id": …, "title": …, "path": …}`
    """
    src, info = resolve_textbook_pdf(conn, doc_id)     # ★ 五道安全闸在这里
    try:
        pg = int(page)
    except (TypeError, ValueError):
        raise TeachError("page 必须是整数", 400) from None
    try:
        d = int(dpi) if dpi not in (None, "", 0) else TEACH_DPI
    except (TypeError, ValueError):
        raise TeachError("dpi 必须是整数", 400) from None
    if d < TEACH_DPI_MIN or d > TEACH_DPI_MAX:
        raise TeachError(f"dpi 需在 {TEACH_DPI_MIN}~{TEACH_DPI_MAX} 之间（给了 {d}）", 400)

    total = pdf_page_count(src)
    if total <= 0:
        raise TeachError(f"这本教材读不出页数（文件可能损坏）：{src.name}", 500)
    if pg < 1 or pg > total:
        # 可操作提示：把合法范围直接写出来，界面照着改就行。
        raise TeachError(
            f"页码越界：教材「{info.get('title') or src.name}」共 {total} 页，"
            f"page 需在 1~{total} 之间（给了 {pg}）", 400)

    t0 = time.perf_counter()
    cache = page_cache_path(info["id"], pg, d)
    png: bytes | None = None
    if cache.is_file():
        try:
            data = cache.read_bytes()
            if data:
                png, cache_hit = data, "hit"
        except OSError:
            png = None
    if png is None:
        cache_hit = "miss"
        # 同一页并发请求：串行化"检查→渲染→写盘"，避免同一页渲染两遍。
        # 这把锁只围住渲染（~0.2 秒），不碰 dashboard 的全局 `_lock`。
        with _RENDER_LOCK:
            if cache.is_file():                      # 等锁期间别人已经渲染好了
                try:
                    data = cache.read_bytes()
                    if data:
                        png, cache_hit = data, "hit"
                except OSError:
                    png = None
            if png is None:
                png = _render_png(src, pg, d)
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    tmp = cache.with_suffix(".png.tmp")
                    tmp.write_bytes(png)
                    tmp.replace(cache)               # 原子替换：读方永不见半个文件
                    _prune_cache(cache.parent)
                except OSError:
                    pass                             # 写不进缓存不影响这次返回
    return {
        "doc_id": int(info["id"]),
        "title": str(info.get("title") or ""),
        "path": str(src),
        "page": pg,
        "pages": int(total),
        "dpi": int(d),
        "cache": cache_hit,
        "cache_path": str(cache),
        "ms": int((time.perf_counter() - t0) * 1000),
        "png": png,
    }


# ---------------- 知识点 → 原文块（material） ----------------

def _clip(text: str, n: int = TEACH_BLOCK_CHARS) -> tuple[str, bool]:
    t = str(text or "")
    if len(t) <= n:
        return t, False
    return t[:n], True


def material_blocks(conn: sqlite3.Connection, kp: dict, limit: int = 0) -> dict:
    """这条知识点的原料块原文：`material[].chunk_id` → `chunks.text`。

    逐条带回归属（哪份资料、什么角色、出处、页）——**产物里的 chunk_id 可能
    根本不在当前库**（实测 `103-末级知识点总结.json` 的 446 个块 id 在本库
    一个都没有，那份产物来自别的学科库）。这种条目照样列出，标
    `in_library: false`，不报错也不假装有原文。
    """
    mat = [m for m in (kp.get("material") or []) if isinstance(m, dict)]
    ids: list[int] = []
    for m in mat:
        try:
            cid = int(m.get("chunk_id"))
        except (TypeError, ValueError):
            continue
        if cid not in ids:
            ids.append(cid)
    cap = int(limit) if limit else TEACH_DEFAULT_MATERIAL
    cap = max(1, min(cap, TEACH_MAX_MATERIAL))
    shown = ids[:cap]

    rows: dict[int, sqlite3.Row] = {}
    if shown:
        q = ",".join("?" * len(shown))
        for r in conn.execute(
                f"""
                SELECT c.id, c.doc_id, c.ord, c.text, c.heading_path, c.source_ref,
                       c.char_len, d.title, d.path,
                       COALESCE(d.role,'courseware') AS role,
                       COALESCE(d.corpus,'main')     AS corpus
                FROM chunks c LEFT JOIN documents d ON d.id=c.doc_id
                WHERE c.id IN ({q})
                """, shown):
            rows[int(r["id"])] = r

    out: list[dict] = []
    used = 0
    for m in mat:
        try:
            cid = int(m.get("chunk_id"))
        except (TypeError, ValueError):
            continue
        if cid not in shown or any(x["chunk_id"] == cid for x in out):
            continue
        r = rows.get(cid)
        text, cut = _clip(r["text"] if r is not None else "")
        if used + len(text) > TEACH_TOTAL_CHARS:
            text, cut = text[:max(0, TEACH_TOTAL_CHARS - used)], True
        used += len(text)
        item = {
            "chunk_id": cid,
            "in_library": r is not None,
            "kind": str(m.get("kind") or ""),
            "ref": str(m.get("ref") or ""),
            "page": m.get("page"),
            "pages": [int(x) for x in (m.get("pages") or [])
                      if str(x).strip().lstrip("-").isdigit()],
            "chars": int(m.get("chars") or 0),
            "text": text,
            "text_truncated": bool(cut),
        }
        if r is not None:
            item.update({
                "doc_id": int(r["doc_id"]),
                "doc_title": str(r["title"] or ""),
                "doc_path": str(r["path"] or ""),
                "doc_role": str(r["role"] or ""),
                "doc_corpus": str(r["corpus"] or ""),
                "ord": int(r["ord"] or 0),
                "source_ref": str(r["source_ref"] or ""),
                "heading_path": str(r["heading_path"] or ""),
                "cite": _cite(r["title"], r["heading_path"], r["source_ref"]),
            })
        else:
            item["note"] = "这个块不在当前学科库里（产物来自别的库或已被重新入库）"
        out.append(item)
    return {
        "blocks": out,
        "n_material": len(ids),
        "n_returned": len(out),
        "limit": cap,
        "truncated": len(ids) > len(shown),
    }


def _cite(title, heading_path, source_ref) -> str:
    """人类可读引用串：`书名 › 第19章 抗高血压药 › p.202-214`（口径同 search.build_cite）。"""
    parts = [str(x).strip() for x in (title, heading_path, source_ref) if str(x or "").strip()]
    head = parts[1] if len(parts) > 1 else ""
    if head and parts and head.startswith(str(parts[0])):
        head = head[len(str(parts[0])):].lstrip(" >").strip()
        parts = [parts[0]] + ([head] if head else []) + parts[2:]
    return " › ".join(parts)


# ---------------- 知识点 → 教材段落 ----------------

# 中文 2~6 字窗口里，落在这些词上的基本都是套话，不带来区分度（也不该拿分）。
_STOP_TERMS = {
    "药物", "作用", "临床", "应用", "机制", "特点", "包括", "主要", "以及",
    "可以", "具有", "产生", "导致", "治疗", "患者", "增加", "减少", "用于",
    "通过", "影响", "相关", "一般", "常见", "不同", "以下", "上述", "因此",
    "目前", "进行", "出现", "表现", "属于", "称为", "分为", "由于", "如果",
    "需要", "必须", "注意", "重要", "大量", "明显", "显著", "程度", "水平",
    "情况", "方面", "内容", "重点", "介绍", "概述", "定义", "概念", "分类",
    "本章", "本节", "课件", "封面", "标题", "框架", "小结", "总结", "复习",
    "举例", "如下", "其中", "此外", "同时", "但是", "而且", "或者", "一些",
}
_ASCII_TERM = re.compile(r"[A-Za-z][A-Za-z0-9\-_]{2,}")
_HAN_SEG = re.compile(r"[\u4e00-\u9fff]{2,}")
_CHAPTER_NO = re.compile(r"第\s*([0-9]{1,3})\s*章")
_MAX_TERMS = 320


def _kp_query_text(kp: dict, mat: dict | None = None) -> str:
    """把一条知识点压成"用来找教材"的查询文本。

    来源按信息量排序：名称 → 结构节点名 → 要点 → 摘要 → 原料块正文（前几块）。
    长度封顶 2400 字：再长也只是重复同一批词，白花时间。
    """
    parts: list[str] = []
    for k in ("name", "node_name"):
        v = str(kp.get(k) or "").strip()
        if v:
            parts.append(v)
    pts = [str(x).strip() for x in (kp.get("points") or []) if str(x or "").strip()]
    parts.extend(pts[:12])
    sm = str(kp.get("summary") or "").strip()
    if sm:
        parts.append(sm[:600])
    if mat:
        for b in (mat.get("blocks") or [])[:3]:
            if b.get("kind") == "正文块" and b.get("text"):
                parts.append(str(b["text"])[:400])
    return "\n".join(parts)[:2400]


def _terms(text: str, limit: int = _MAX_TERMS) -> list[str]:
    """抽候选词：英文/数字术语原样留，中文按**所有 2~6 字窗口**切（长窗口优先）。

    为什么不用分词器：项目里没有引入结巴之类的依赖，而中文没有词边界，
    固定步长切会切出"的胞体位"这种跨词碎片（`core/search.py::_like_windows`
    的注释里已经踩过这个坑）。这里沿用同一条判据：生成所有连续窗口，
    再用 IDF + 停用词把噪声压下去。
    """
    out: list[str] = []
    seen: set = set()

    def push(t: str) -> None:
        t = t.strip()
        if not t or t in seen:
            return
        seen.add(t)
        out.append(t)

    for m in _ASCII_TERM.finditer(text):
        push(m.group(0))
    for seg in _HAN_SEG.findall(text):
        for size in (6, 5, 4, 3, 2):
            if len(seg) < size:
                continue
            for i in range(len(seg) - size + 1):
                w = seg[i:i + size]
                if w in _STOP_TERMS:      # 套话：2 字与更长的都一视同仁地丢
                    continue
                push(w)
                if len(out) >= limit:
                    return out
    return out


def _chapter_of(heading_path: str) -> str:
    """从 `药理学 第6版 刘晓东 > 第19章 抗高血压药` 里取出最后一级当章名。

    教材轴的这一段只有"书名"（前言、目录那些块），取不到章名就返回空串——
    空章照样参与打分，只是拿不到章级加成。
    """
    parts = [p.strip() for p in str(heading_path or "").split(">") if p.strip()]
    if not parts:
        return ""
    return parts[-1]


def textbook_passages(conn: sqlite3.Connection, tb: dict, kp: dict,
                      mat: dict | None = None, limit: int = 0) -> dict:
    """在教材里找这条知识点的原文段落（**零模型调用**：纯词项 IDF 打分）。

    打分（全程可解释，`why` 里逐条写出来）：
      ① 词项权重 `w = (词长-1) × log((N+1)/(df+0.5))`——长词、稀有词更值钱；
      ② 块得分 = 命中词权重之和（同一个词在块里出现多次不重复计分，
         否则"长块"天然占便宜）；
      ③ 章名加成：命中词出现在某一章名里（如知识点讲"癫痫"、教材有
         `第11章 抗癫痫药`），该章所有块 `+0.6×Σw`——这是最有效的一条线索；
      ④ 章号加成：知识点文本里写了"第19章"，与教材章号对上再加一档。

    返回 `{"passages": [...], "n_blocks": ..., "terms": [...], "matched": ...,
            "score_floor": ..., "cross_floor": ..., "note": ...}`。
    """
    try:
        blocks = conn.execute(
            """
            SELECT id, ord, text, heading_path, source_ref
            FROM chunks WHERE doc_id=? ORDER BY ord
            """, (int(tb["id"]),)).fetchall()
    except sqlite3.OperationalError:
        blocks = []
    N = len(blocks)
    out = {"passages": [], "n_blocks": N, "terms": [], "matched": 0}
    if not N:
        out["note"] = ("这本教材在库里没有任何切块——"
                       "（教材是扫描件，正文块由识图/OCR 通道产生）重新入库即可。")
        return out

    text = _kp_query_text(kp, mat)
    terms = _terms(text)
    if not terms:
        out["note"] = "这条知识点没有可用的文本（名称/要点/原文都是空的），无法在教材里定位。"
        return out

    # 一趟扫完：每个块命中了哪些词（打分用）+ 每个词出现在几个块里（IDF 用）。
    # 两趟分开写时实测要 150~180 ms，合并后约 80~100 ms。
    block_texts = [str(b["text"] or "") for b in blocks]
    block_hits: list[list[str]] = []
    df: dict = {}
    for bt in block_texts:
        hits = [t for t in terms if t in bt]
        block_hits.append(hits)
        for t in hits:
            df[t] = df.get(t, 0) + 1

    # 只留"出现过、但不过分普遍"的词：太普遍（>55% 的块都有）的词没有区分度。
    keep = [(t, d) for t, d in ((t, df.get(t, 0)) for t in terms)
            if 0 < d <= max(2, int(N * 0.55))]
    if not keep:
        out["note"] = "教材里没有出现这条知识点的任何关键词（可能这份教材与它不同源）。"
        return out

    weights: dict = {}
    for t, d in keep:
        weights[t] = (len(t) - 1) * math.log((N + 1) / (d + 0.5))
    # 权重最高的词排前面（`why` 里按这个顺序展示）
    keep.sort(key=lambda kv: -weights[kv[0]])
    out["terms"] = [{"term": t, "df": d, "weight": round(weights[t], 3)}
                    for t, d in keep[:12]]

    # ---- 章级加成 ----
    chapters: set = set()
    for b in blocks:
        ch = _chapter_of(b["heading_path"])
        if ch:
            chapters.add(ch)
    ch_bonus: dict = {}
    for ch in chapters:
        if not ch:
            continue
        s = sum(weights[t] for t, _ in keep if t in ch)
        if s:
            ch_bonus[ch] = 0.6 * s
    # 章号线索：知识点文本里点名的"第N章"
    want_nos = {m for m in _CHAPTER_NO.findall(text)}
    for ch in chapters:
        m = _CHAPTER_NO.search(ch)
        if m and m.group(1) in want_nos:
            ch_bonus[ch] = ch_bonus.get(ch, 0.0) + 3.0
    # 章名与知识点名称的**直接重合**（如"抗高血压"）也算一档，权重低但稳
    name = str(kp.get("name") or "")
    for ch in chapters:
        hits = [t for t, _ in keep if len(t) >= 3 and t in ch and t in name]
        if hits:
            ch_bonus[ch] = ch_bonus.get(ch, 0.0) + 0.8 * sum(weights[t] for t in hits)

    scored: list[tuple[float, int, str]] = []
    for i, b in enumerate(blocks):
        ch = _chapter_of(b["heading_path"])
        hits = [(t, weights[t]) for t in block_hits[i] if t in weights]
        if not hits and ch not in ch_bonus:
            continue
        score = sum(w for _, w in hits) + ch_bonus.get(ch, 0.0)
        if score <= 0:
            continue
        scored.append((score, int(b["id"]), ch))
    if not scored:
        out["note"] = "教材里没有段落与这条知识点匹配上。"
        return out
    scored.sort(key=lambda x: (-x[0], x[1]))

    # 相对截断（两道，都为了"宁可少给一段，也不给看着像错的"）：
    #   ① 同一章内：低于"最佳段落 × 0.4"的丢掉；
    #   ② 跨章：要进榜得高到"最佳段落 × 0.6"——实测癫痫那条知识点会在第 38 章
    #      抗寄生虫药里蹭到一段（只因共用"神经功能紊乱"），跨章门槛能把它挡掉，
    #      而真正相关的同章段落一段不少。
    best_score, _best_cid, best_ch = scored[0]
    floor = best_score * TEACH_MIN_SCORE_RATIO
    cross_floor = best_score * TEACH_CROSS_CHAPTER_RATIO
    scored = [x for x in scored
              if (x[2] == best_ch and x[0] >= floor)
              or (x[2] != best_ch and x[0] >= cross_floor)]

    cap = int(limit) if limit else TEACH_DEFAULT_PASSAGES
    cap = max(1, min(cap, TEACH_MAX_PASSAGES))
    by_id = {int(b["id"]): b for b in blocks}
    idx_of = {int(b["id"]): i for i, b in enumerate(blocks)}
    per_ch: dict = {}
    picked: list[dict] = []
    total_chars = 0
    for score, cid, ch in scored:
        if len(picked) >= cap:
            break
        if per_ch.get(ch, 0) >= TEACH_MAX_PER_CHAPTER:
            continue
        b = by_id.get(cid)
        if b is None:
            continue
        txt, cut = _clip(b["text"])
        if total_chars + len(txt) > TEACH_TOTAL_CHARS:
            txt, cut = txt[:max(0, TEACH_TOTAL_CHARS - total_chars)], True
        total_chars += len(txt)
        ref = str(b["source_ref"] or "")
        pgs = [int(x) for x in re.findall(r"p\.(\d+)", ref)] or []
        lo = min(pgs) if pgs else 0
        hi = max(pgs) if len(pgs) > 1 else lo
        hits = sorted(((t, w) for t, w in
                       ((t, weights[t]) for t in block_hits[idx_of[cid]]
                        if t in weights)),
                      key=lambda kv: -kv[1])[:6]
        picked.append({
            "chunk_id": cid,
            "ord": int(b["ord"] or 0),
            "chapter": ch,
            "heading_path": str(b["heading_path"] or ""),
            "source_ref": ref,
            "page": lo,
            "page_last": hi,
            "pages": list(range(lo, hi + 1)) if lo else [],
            "page_label": (f"p.{lo}" if lo == hi or not lo else f"p.{lo}–p.{hi}"),
            # ⚠ 教材块的 source_ref 是**页码范围**（实测中位数 4~5 页），
            # 起始页只是"这一块大致从哪页开始"，不是精确页码。
            "page_exact": bool(lo and hi == lo),
            "score": round(score, 3),
            "why": [f"命中「{t}」" for t, _ in hits] + (
                [f"章名匹配：{ch}"] if ch in ch_bonus else []),
            "cite": _cite(tb.get("title"), b["heading_path"], ref),
            "text": txt,
            "text_truncated": bool(cut),
        })
        per_ch[ch] = per_ch.get(ch, 0) + 1
    out["passages"] = picked
    out["matched"] = len(scored)
    out["best_chapter"] = best_ch
    out["score_floor"] = round(floor, 3)
    out["cross_floor"] = round(cross_floor, 3)
    out["min_score_ratio"] = TEACH_MIN_SCORE_RATIO
    out["cross_chapter_ratio"] = TEACH_CROSS_CHAPTER_RATIO
    out["truncated"] = len(scored) > len(picked)
    return out


# ---------------- 对外主入口 ----------------

def kp_textbook(conn: sqlite3.Connection, doc_id: int, kp_id: str,
                kp: dict | None = None, limit: int = 0, material_limit: int = 0,
                tb_doc_id: int = 0) -> dict:
    """知识点 → （自己的原料块原文）+（教材里对应的原文段落）。

    `kp` 由调用方（看板 `_lkp_index`）传入，避免 core 反向依赖 dashboard；
    传 None 时由 `kp_id` 在本库产物里找——本模块不自己读产物文件，
    找不到就如实说"没有这条知识点"。
    """
    out: dict = {
        "doc_id": int(doc_id or 0),
        "kp_id": str(kp_id or ""),
        "kp_name": "",
        "node_name": "",
        "status": "ok",
        "message": "",
        "material": {"blocks": [], "n_material": 0, "n_returned": 0, "limit": 0,
                     "truncated": False},
        "textbook": {},
    }
    if kp is None:
        out["status"] = "no_kp"
        out["message"] = (f"文档 {doc_id} 里没有末级知识点「{kp_id or '(空)'}」"
                          "（总结数据可能尚未生成，或这条知识点来自别的学科库）。")
        return out
    out["kp_name"] = str(kp.get("name") or "")
    out["node_name"] = str(kp.get("node_name") or "")
    out["points"] = [str(x) for x in (kp.get("points") or [])]
    out["summary"] = str(kp.get("summary") or "")

    mat = material_blocks(conn, kp, limit=material_limit or TEACH_DEFAULT_MATERIAL)
    out["material"] = mat

    tb = pick_textbook(conn, tb_doc_id)
    if tb is None:
        out["status"] = "no_textbook"
        out["message"] = ("当前学科库里没有教材类资料（role=textbook 的 PDF），"
                          "教材补充框暂时没有内容可挂。把教材 PDF 放进 raw 并入库即可。")
        return out

    doc_info = textbook_docs(conn)
    total_pages = 0
    try:
        real, _info = resolve_textbook_pdf(conn, tb["id"])
        total_pages = pdf_page_count(real)
    except TeachError:
        total_pages = 0

    tp = textbook_passages(conn, tb, kp, mat=mat, limit=limit)
    out["textbook"] = {
        "doc_id": int(tb["id"]),
        "title": str(tb.get("title") or ""),
        "path": str(tb.get("path") or ""),
        "corpus": str(tb.get("corpus") or "main"),
        "n_chunks": int(tb.get("n_chunks") or 0),
        "pages": total_pages,
        "dpi": TEACH_DPI,
        # 界面拿这个模板拼 `<img src=...>`；page 由所选段落带出（后端不猜）。
        "page_url": f"/api/teach-page?doc_id={int(tb['id'])}&page={{page}}",
        "n_candidates": tp.get("n_blocks", 0),
        "matched_blocks": tp.get("matched", 0),
        "best_chapter": str(tp.get("best_chapter") or ""),
        "score_floor": tp.get("score_floor"),
        "cross_floor": tp.get("cross_floor"),
        "passages": tp.get("passages") or [],
        "truncated": bool(tp.get("truncated")),
        "terms": tp.get("terms") or [],
        "note": str(tp.get("note") or ""),
        "other_textbooks": [
            {"doc_id": int(d["id"]), "title": str(d.get("title") or ""),
             "corpus": str(d.get("corpus") or "main"),
             "n_chunks": int(d.get("n_chunks") or 0)}
            for d in doc_info if int(d["id"]) != int(tb["id"])
        ],
        "source_note": ("教材块的 source_ref 是页码范围（教材按节切块，实测中位数 4~5 页），"
                        "起始页只是大致位置；要精确看某一页请用 page_url 渲染。"),
    }
    if not out["textbook"]["passages"]:
        out["status"] = "no_match"
        out["message"] = (out["textbook"]["note"]
                          or "教材里没有找到与这条知识点匹配的段落。")
    else:
        ps = out["textbook"]["passages"]
        out["message"] = (f"教材「{out['textbook']['title']}」里命中 {len(ps)} 段"
                          f"（p.{ps[0]['page']} 起），按词项与章名自动匹配，"
                          "仅供参考，请以原文为准。")
    return out


# ==========================================================================
# 教材补充（C-0124，2026-10-21）：知识点 ↔ 教材，按**章节切块**匹配 + 每课件产物
# ==========================================================================
# 用户需求（2026-10-21）：
#   1) 知识点窗口右侧显示教材对应页；
#   2) 以知识点总结为基础判断教材是否有可补充内容，有则补进侧栏；
#      教材内容必须**按章节切块的库**里找、**不允许整本扫描**；
#   3) 知识点整理时触发；无教材走原管线；之后教材入库 → 提醒"补做教材补充"，
#      只补教材、**不动原知识点**。
#
# 设计口径（全部零模型调用、只读 DB、只写一个 JSON 产物）：
#   · 先按 `chunks.heading_path` 分组出**章候选**（只读 heading_path，不读块正文）；
#   · 每条 KP 用词项给章名打分，只选**一章**；0 分返回 None（**禁止回退整本扫描**）；
#   · 只 `SELECT ... WHERE doc_id=? AND heading_path=?` 取所选章的块做段落打分；
#   · 产物 `logs/knowledge/<doc_id>-教材补充.json`，原子写；
#   · 旧 `textbook_passages` / `kp_textbook` 一个字节未改（旧接口/旧流程继续可用）。
SUPPLEMENT_VERSION = "C-0125-teach-ai-v1"
SUPPLEMENT_SUFFIX = "-教材补充.json"
SUPPLEMENT_MAX_PASSAGES = 3
SUPPLEMENT_MAX_ADDED_TERMS = 8
SUPPLEMENT_MAX_PAGES = 8
SUPPLEMENT_TERMS_MAX = 320


def supplement_out_file(doc_id: int) -> Path:
    """教材补充产物：`<当前学科 logs>/knowledge/<doc_id>-教材补充.json`。"""
    return config.LOG_DIR / "knowledge" / ("%d%s" % (int(doc_id), SUPPLEMENT_SUFFIX))


def _fmt_pages(pages) -> str:
    """页列表 → `p.1–p.3、p.7` 文案（与 build_kp 产物里的 page_label 同形）。"""
    ps = sorted({int(x) for x in (pages or []) if str(x).strip().isdigit()})
    if not ps:
        return ""
    out, a, b = [], ps[0], ps[0]
    for x in ps[1:]:
        if x == b + 1:
            b = x
            continue
        out.append(f"p.{a}" if a == b else f"p.{a}–p.{b}")
        a = b = x
    out.append(f"p.{a}" if a == b else f"p.{a}–p.{b}")
    return "、".join(out)


def _page_span_label(lo: int, hi: int) -> str:
    if not lo:
        return ""
    return f"p.{lo}" if (not hi or hi == lo) else f"p.{lo}–p.{hi}"


_REF_PAGE_RE = re.compile(r"p\.(\d+)(?:\s*-\s*(\d+))?")


def _ref_span(ref) -> tuple[int, int] | None:
    """`p.70` / `p.70-82` / `p.70–p.82` → (70, 70) / (70, 82)。"""
    m = _REF_PAGE_RE.search(str(ref or ""))
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2) or a)
    return (min(a, b), max(a, b))


# ==========================================================================
# S08B：共享材料进程内缓存（教材章候选 / 名词预习候选）
#
# 背景（docs/优化全路径-2026-10-31.md §S08B）：教材补充映射与名词整理会在
# 一次界面操作里多次调用 `_chapter_candidates()` / `_preview_artifact()`，
# 每次都重新 `SELECT ... GROUP BY heading_path`（实测 882 块教材约 4.3~5.5ms）
# 或重新读 45KB 预习 JSON。这些数据**只读、不依赖模型**，且已由 S08 引入了
# 事务内递增的 `meta.data_revision`，所以可以安全地做**进程内缓存**：
#
# 失效口径（与 S08 一致，不看 DB 文件 mtime/size——WAL 提交时主文件可能不变）：
#   · 键里必须含 **db_path + data_revision**；revision 缺失/0 → **不缓存**
#     （旧库没写 revision 时行为逐字等于改造前，安全回退）；
#   · 再叠加数据自身签名：教材章候选用 documents 行的稳定字段
#     （id/title/path/n_chunks/size_bytes），名词预习候选用 artifact sha256；
#   · 因此"内容变但 revision 没变"的极端情况也能被签名兜住。
#
# 两条红线：
#   1. **只缓存只读数据**（章候选列表 / 已存在的预习 JSON 归一化结果），
#      不缓存任何模型输出/付费结果，不写新产物；
#   2. **返回 deepcopy**：调用方改返回值不会污染缓存。
#
# 开关：环境变量 `SHARED_MATERIAL_CACHE=0`（或 false/off/no）→ 全部走旧路径
# （不读不写缓存，也不改统计）。默认开。
# ==========================================================================
_SHARED_CACHE_LOCK = threading.RLock()
_SHARED_CACHE: dict = {}
_SHARED_CACHE_MAX = 64
_SHARED_CACHE_STATS = {"hit": 0, "miss": 0, "store": 0, "skip": 0, "evict": 0}
# 文件内容 sha 的记忆表：键=(绝对路径, mtime_ns, size) → sha24。
# 为什么不直接每次读文件算 sha：名词预习 artifact 约 42KB，实测 sha256 一次中位
# **0.77ms**，比"重新解析 JSON"（0.43ms）还贵——不记忆的话缓存热路径反而更慢。
# 记忆键用文件自身的 mtime_ns+size，文件一改 mtime 就变 → 自动重算；这是"记忆哈希"
# 而非"用 mtime 当失效判据"（缓存键里放的仍是真正的 sha256 值）。
_SHARED_FILE_SIG_CACHE: dict = {}
_SHARED_FILE_SIG_MAX = 128
SHARED_CACHE_ENV = "SHARED_MATERIAL_CACHE"
_SHARED_CACHE_OFF = ("0", "false", "off", "no", "disabled")


def shared_material_cache_enabled() -> bool:
    """S08B 共享材料缓存是否启用（`SHARED_MATERIAL_CACHE=0` 可整体回退）。"""
    raw = os.environ.get(SHARED_CACHE_ENV)
    if raw is None:
        try:
            raw = str(config.value_of(SHARED_CACHE_ENV, "1"))
        except Exception:  # noqa: BLE001  （配置中心没有这个键时走默认）
            raw = "1"
    return str(raw or "").strip().lower() not in _SHARED_CACHE_OFF


def _shared_cache_note(name: str, n: int = 1) -> None:
    with _SHARED_CACHE_LOCK:
        _SHARED_CACHE_STATS[name] = int(_SHARED_CACHE_STATS.get(name, 0)) + int(n)


def shared_material_cache_note(name: str, n: int = 1) -> None:
    """给统计计数 +n（测试/报告用；只影响统计，不影响缓存行为）。"""
    _shared_cache_note(name, n)


def shared_material_cache_get(key):
    """读缓存并返回 deepcopy；未命中返回 None（miss 计数 +1）。"""
    with _SHARED_CACHE_LOCK:
        val = _SHARED_CACHE.get(key)
        if val is None:
            _SHARED_CACHE_STATS["miss"] += 1
            return None
        _SHARED_CACHE_STATS["hit"] += 1
        return copy.deepcopy(val)


def shared_material_cache_put(key, value) -> None:
    """存缓存（存 deepcopy，LRU 上限 64：超了从最早插入的丢）。value=None 不存。"""
    if value is None:
        return
    with _SHARED_CACHE_LOCK:
        _SHARED_CACHE[key] = copy.deepcopy(value)
        _SHARED_CACHE_STATS["store"] += 1
        while len(_SHARED_CACHE) > _SHARED_CACHE_MAX:
            _SHARED_CACHE.pop(next(iter(_SHARED_CACHE)))
            _SHARED_CACHE_STATS["evict"] += 1


def clear_shared_material_cache() -> None:
    """清空共享材料缓存（测试/诊断用；同时重置统计与文件哈希记忆）。"""
    with _SHARED_CACHE_LOCK:
        _SHARED_CACHE.clear()
        _SHARED_FILE_SIG_CACHE.clear()
        for k in _SHARED_CACHE_STATS:
            _SHARED_CACHE_STATS[k] = 0


def shared_material_cache_stats() -> dict:
    """缓存现状：{enabled, size, max, hit, miss, store, skip, evict, file_sig_size}。"""
    with _SHARED_CACHE_LOCK:
        out = {k: int(v) for k, v in _SHARED_CACHE_STATS.items()}
        out.update({"enabled": shared_material_cache_enabled(),
                    "size": len(_SHARED_CACHE), "max": _SHARED_CACHE_MAX,
                    "file_sig_size": len(_SHARED_FILE_SIG_CACHE)})
        return out


def shared_material_db_path(conn: sqlite3.Connection) -> str:
    """从连接拿主库文件路径（缓存键的一部分）；拿不到返回 ""（此时仍可用）。

    `PRAGMA database_list` 给的已经是绝对路径；**不做 `resolve()`**（Windows 上
    `resolve()` 要走一次文件系统，实测比缓存命中本身还贵）。路径写法不同只会
    产生"多一个缓存键"，不会命中错数据。
    """
    try:
        for row in conn.execute("PRAGMA database_list").fetchall():
            # row = (seq, name, file)
            name = row["name"] if hasattr(row, "keys") else row[1]
            path = row["file"] if hasattr(row, "keys") else row[2]
            if str(name) == "main":
                if not path:
                    return ""
                p = Path(str(path))
                return str(p if p.is_absolute() else p.absolute())
    except sqlite3.Error:
        pass
    return ""


def shared_material_data_revision(conn: sqlite3.Connection) -> int:
    """读 S08 的 `meta.data_revision`；缺失/损坏返回 0（缓存据此安全回退）。"""
    from . import db as _db

    try:
        return int(_db.data_revision(conn))
    except Exception:  # noqa: BLE001
        return 0


def shared_material_file_sha24(path: Path) -> str:
    """文件内容 sha256 前 24 位；不存在/读失败返回 ""（"" 表示"不缓存"）。

    内容哈希按 `(路径, mtime_ns, size)` 记忆：文件未变时不做第二次 IO；
    文件一变 mtime 就变 → 自动重算。**缓存键里放的仍是真正的 sha256 值**，
    mtime 只用于决定"要不要重算哈希"，不充当失效判据本身。
    """
    try:
        p = Path(path)
        st = os.stat(p)                       # 只 stat 一次（Windows 上每次 ~0.05ms）
        if not stat.S_ISREG(st.st_mode):
            return ""
    except OSError:
        return ""
    memo_key = (str(p if p.is_absolute() else p.absolute()),
                int(getattr(st, "st_mtime_ns", 0)), int(st.st_size))
    with _SHARED_CACHE_LOCK:
        cached = _SHARED_FILE_SIG_CACHE.get(memo_key)
    if cached is not None:
        return cached
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        sha = h.hexdigest()[:24]
    except OSError:
        return ""
    with _SHARED_CACHE_LOCK:
        _SHARED_FILE_SIG_CACHE[memo_key] = sha
        while len(_SHARED_FILE_SIG_CACHE) > _SHARED_FILE_SIG_MAX:
            _SHARED_FILE_SIG_CACHE.pop(next(iter(_SHARED_FILE_SIG_CACHE)))
    return sha


def _textbook_cache_signature(tb: dict) -> str:
    """教材数据签名（不含正文）：id/title/path/n_chunks/size_bytes 的稳定哈希。

    这些字段都来自 `documents` 行（`textbook_docs()` 的 SELECT 结果），取用零成本；
    真正的失效主判据仍是 `data_revision`，本签名兜"revision 没变但教材被替换"的兜底。
    """
    parts = [str(tb.get("id") or ""), str(tb.get("title") or ""),
             str(tb.get("path") or ""), str(tb.get("n_chunks") or ""),
             str(tb.get("size_bytes") or "")]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _chapter_candidates(conn: sqlite3.Connection, tb: dict) -> list:
    """教材的**章候选**（S08B：带进程内缓存，返回 deepcopy）。

    缓存键 = 教材 doc_id + db_path + data_revision + 教材行签名；
    revision 缺失/0 时不缓存（逐字走旧查询）；空结果不缓存（避免把
    "暂时没读到章"固化）；调用方修改返回值不会影响下一次命中。
    实际查询在 `_chapter_candidates_uncached()`（旧实现逐字保留）。
    """
    if not isinstance(tb, dict) or not tb.get("id"):
        return []
    key = None
    if shared_material_cache_enabled():
        try:
            rev = shared_material_data_revision(conn)
            if rev > 0:
                key = ("teach_chapters", int(tb["id"]),
                       shared_material_db_path(conn), int(rev),
                       _textbook_cache_signature(tb))
        except Exception:  # noqa: BLE001  （任何异常都退回旧路径，不因缓存而失败）
            key = None
        if key is not None:
            hit = shared_material_cache_get(key)
            if hit is not None:
                return hit
    result = _chapter_candidates_uncached(conn, tb)
    if key is not None:
        if result:
            shared_material_cache_put(key, result)
        else:
            _shared_cache_note("skip")
    return result


def _chapter_candidates_uncached(conn: sqlite3.Connection, tb: dict) -> list:
    """教材的**章候选**（旧实现，逐字保留；S08B 缓存未命中时走这里）。

    **只读 heading_path 分组计数，不读任何块正文**（整本扫描的红线也适用于这里）。
    取 heading_path 最后一段为章名并解析章号；书名级（front matter，只有书名一段）
    不算章，直接跳过——它们参与匹配会把知识点吸到"前言/目录"上去。
    """
    try:
        rows = conn.execute(
            "SELECT heading_path, COUNT(*) AS n FROM chunks WHERE doc_id=? "
            "GROUP BY heading_path ORDER BY MIN(ord)",
            (int(tb["id"]),),
        ).fetchall()
    except sqlite3.Error:
        return []
    tb_title = str(tb.get("title") or "")
    out: list = []
    for r in rows:
        hp = str(r["heading_path"] or "")
        name = _chapter_of(hp)
        if not name or name == tb_title or ">" not in hp:
            continue
        m = _CHAPTER_NO.search(name) or _CHAPTER_NO.search(hp)
        out.append({"heading_path": hp, "name": name,
                    "no": int(m.group(1)) if m else 0,
                    "n_blocks": int(r["n"] or 0)})
    return out


def _match_chapter_for_kp(chapters: list, kp: dict, doc_title: str = ""):
    """把一条知识点匹配到**一章**（零模型）。

    打分：候选词 = `_kp_query_text(kp)` + 课件标题 `doc_title` 的词项；
    词项权重 `w=(词长-1)×log((K+1)/(df+0.5))`（K=章数，df=出现在几个章名里），
    只来自课件标题的词项 ×0.5 降权；章命中得分 = Σw + 章号命中 +3.0
    + 知识点名称与章名 ≥3 字词重合 ×0.8。**最高分为 0/无命中 → None**，
    调用方必须按 no_match 处理，**不得回退整本扫描**。
    """
    if not chapters:
        return None
    kp_text = _kp_query_text(kp)
    title_text = str(doc_title or "").strip()
    text = (kp_text + "\n" + title_text).strip()
    terms = _terms(text, limit=SUPPLEMENT_TERMS_MAX)
    if not terms:
        return None
    kp_terms = set(_terms(kp_text, limit=SUPPLEMENT_TERMS_MAX))
    names = [str(c.get("name") or "") for c in chapters]
    K = len(chapters)
    weights: dict = {}
    df: dict = {}
    for t in terms:
        d = sum(1 for nm in names if t in nm)
        if d <= 0:
            continue
        df[t] = d
        # 过于普遍的词（出现在一半以上章名里）没有区分度
        if d > max(2, int(K * 0.5)):
            continue
        w = (len(t) - 1) * math.log((K + 1) / (d + 0.5))
        if t not in kp_terms:
            w *= 0.5          # 只来自课件标题的噪声词降权
        weights[t] = w
    if not weights:
        return None
    want_nos = {str(m) for m in _CHAPTER_NO.findall(kp_text)}
    kp_name = str(kp.get("name") or "")
    best = None
    for c in chapters:
        nm = str(c.get("name") or "")
        hits = [t for t in weights if t in nm]
        if not hits:
            continue
        score = sum(weights[t] for t in hits)
        no = int(c.get("no") or 0)
        if no and str(no) in want_nos:
            score += 3.0      # 知识点文本里点名"第N章"
        name_hits = [t for t in hits if len(t) >= 3 and t in kp_name]
        if name_hits:
            score += 0.8 * sum(weights[t] for t in name_hits)
        if best is None or score > best["score"]:
            best = dict(c, score=round(score, 3),
                        hit_terms=sorted(hits, key=lambda t: -weights[t])[:6])
    return best


def _chapter_passages(conn: sqlite3.Connection, tb: dict, chapter_heading: str,
                      terms: list, limit: int = 0) -> list:
    """**只查所选章**的教材块并打分（零模型）。

    SQL 固定为 `WHERE doc_id=? AND heading_path=?`——**不 SELECT 整本教材**。
    打分口径与旧 `textbook_passages` 同源（词项 IDF、相对截断 0.4、每块去重计分），
    只是分母换成"本章块数"，且不再跨章。
    """
    try:
        rows = conn.execute(
            "SELECT id, ord, text, heading_path, source_ref FROM chunks "
            "WHERE doc_id=? AND heading_path=? ORDER BY ord",
            (int(tb["id"]), str(chapter_heading)),
        ).fetchall()
    except sqlite3.Error:
        return []
    if not rows:
        return []
    N = len(rows)
    block_texts = [str(r["text"] or "") for r in rows]
    df: dict = {}
    for bt in block_texts:
        for t in terms:
            if t in bt:
                df[t] = df.get(t, 0) + 1
    keep = [(t, d) for t, d in df.items()
            if 0 < d <= max(2, int(N * 0.55))]
    if not keep:
        return []
    weights = {t: (len(t) - 1) * math.log((N + 1) / (d + 0.5)) for t, d in keep}
    scored: list = []
    per_block_hits: list = []
    for i, bt in enumerate(block_texts):
        hits = [t for t in weights if t in bt]
        per_block_hits.append(hits)
        if not hits:
            continue
        s = sum(weights[t] for t in hits)
        if s > 0:
            scored.append((s, i))
    if not scored:
        return []
    scored.sort(key=lambda x: (-x[0], x[1]))
    best = scored[0][0]
    floor = best * TEACH_MIN_SCORE_RATIO
    cap = int(limit) if limit else SUPPLEMENT_MAX_PASSAGES
    cap = max(1, min(cap, TEACH_MAX_PASSAGES))
    out: list = []
    for score, i in scored:
        if len(out) >= cap or score < floor:
            break
        r = rows[i]
        txt, _cut = _clip(r["text"] or "")
        span = _ref_span(r["source_ref"])
        lo, hi = span if span else (0, 0)
        hits = sorted(per_block_hits[i], key=lambda t: -weights.get(t, 0.0))[:6]
        out.append({
            "chunk_id": int(r["id"]),
            "page_start": lo,
            "page_end": hi,
            "page_label": _page_span_label(lo, hi),
            "text": txt,
            "score": round(score, 3),
            "hit_terms": hits,
        })
    return out


def _supplement_added_terms(passages: list, kp_text: str) -> list:
    """教材段落里出现、而 KP 文本里没有的词（≤8，长词优先；供侧栏"可补充"提示）。"""
    kp_norm = re.sub(r"\s+", "", str(kp_text or "")).lower()
    seen: list = []
    for p in passages or []:
        for t in _terms(str(p.get("text") or "")):
            tn = re.sub(r"\s+", "", t).lower()
            if not tn or tn in kp_norm or t in seen:
                continue
            seen.append(t)
    seen.sort(key=lambda t: (-len(t), t))
    return seen[:SUPPLEMENT_MAX_ADDED_TERMS]


def _atomic_write_json(data: dict, out: Path) -> None:
    """原子写：临时文件 + `os.replace`（中断不会留半截 JSON）。"""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out)


def _open_db(db_path=None):
    """只读连接（`mode=ro`，物理上不可能写库）。"""
    from . import config as _config
    p = Path(db_path) if db_path else Path(_config.DB_PATH)
    conn = sqlite3.connect("file:" + p.as_posix() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _doc_title(conn, doc_id: int) -> str:
    try:
        row = conn.execute("SELECT title FROM documents WHERE id=?",
                           (int(doc_id),)).fetchone()
        return str((row["title"] if row else "") or "")
    except sqlite3.Error:
        return ""


def _load_supplement(out: Path) -> dict:
    try:
        raw = json.loads(Path(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


# ==========================================================================
# C-0125（2026-10-22）：教材补充 AI 混合管线
# ==========================================================================
# 用户反馈：C-0124 纯本地的教材补充"排版好但结果差"，要求改用**外接 AI**
# （与知识点总结管线同一个模型）并改提示词。三步（能本地的绝不调模型）：
#
#   ① MAP：课件标题 + KP 大纲 → 教材**唯一章节**（AI，每课件 1 次，小输入）
#   ② LOCAL：每条 KP → 预习《章节细目》里的具体小节 + 页码（本地词项，0 AI）
#   ③ SUPP：按 KP **按需** AI 分析"教材可补充内容"（打开/点击才调用，带缓存）
#
# 所有 AI 调用都记账（tokens/cost/elapsed/attempts）；AI 不可用/失败自动降级本地，
# 保留 C-0124 的 passages/页图口径（旧字段继续可读）。旧版产物（C-0124）识别为
# stale（needs_backfill=true），升级时先备份 `.bak-<ts>`，不静默覆盖。
TEACH_PROMPT_NAME = "提示词-教材补充.md"
TEACH_AI_ENV = "KB_TEACH_AI"
TEACH_MAP_PROMPT_MAX_KPS = 60
TEACH_MAP_JSON_HINT = 4096
TEACH_SUPP_MATERIAL_MAX = 12000
TEACH_SUPP_POINTS_MAX = 5
TEACH_SUPP_CITATIONS_MAX = 3
TEACH_SUPP_GAPS_MAX = 5
TEACH_SUPP_SECTIONS_MAX = 2
TEACH_SUPP_KINDS = ("概念", "机制", "分类", "临床", "注意", "例子")
TEACH_AI_MARKER_ENV = "VERIFY_REFRESH_FIXTURES"   # run_all 测试套件的自我保护标记


# ---------------------------------------------------------------- 提示词
def teach_prompt_file(path=None) -> Path:
    """提示词真源：显式给定 > 本学科 logs/knowledge/ > 项目根 > 随包资源目录。"""
    if path:
        return Path(path)
    name = TEACH_PROMPT_NAME
    own = config.LOG_DIR / "knowledge" / name
    if own.exists():
        return own
    root = config.ROOT / "logs" / "knowledge" / name
    if root.exists():
        return root
    return config.BUNDLE_ROOT / "logs" / "knowledge" / name


def _load_prompts(path=None) -> dict:
    """逐字提取 4 个提示词块（MAP-SYS/USER、SUPP-SYS/USER）+ sha256。

    文件缺失/缺块 → 抛 `TeachError`；调用方在构建时捕获并降级本地（不崩）。
    """
    p = teach_prompt_file(path)
    try:
        blob = p.read_bytes()
    except OSError as e:
        raise TeachError("教材补充提示词真源不可读：%s（%s）" % (p, e), 500) from None
    md = blob.decode("utf-8", "replace")
    out = {}
    for tag, key in (("PROMPT-TEACH-MAP-SYS", "map_sys"),
                     ("PROMPT-TEACH-MAP-USER", "map_user"),
                     ("PROMPT-TEACH-SUPP-SYS", "supp_sys"),
                     ("PROMPT-TEACH-SUPP-USER", "supp_user")):
        try:
            out[key] = _kp_build()._grab(md, tag, p)
        except SystemExit as e:
            raise TeachError("教材补充提示词缺块 %s：%s" % (tag, e), 500) from None
    out.update({"path": str(p), "sha256": hashlib.sha256(blob).hexdigest(),
                "chars": sum(len(out[k]) for k in
                             ("map_sys", "map_user", "supp_sys", "supp_user"))})
    return out


# ---------------------------------------------------------------- 配置 / 记账
def _kp_build():
    """延迟 import kp_build（本模块在 dashboard import 链上，别在顶层拖重依赖）。"""
    from . import kp_build as _kb
    return _kb


def _config_value(key: str, default):
    try:
        return config.value_of(key, default)
    except Exception:  # noqa: BLE001
        return default


def _kp_model() -> str:
    return str(_config_value("KP_BUILD_MODEL", "deepseek-flash") or "deepseek-flash").strip() \
        or "deepseek-flash"


def _kp_thinking() -> bool:
    try:
        return bool(config.value_int("KP_BUILD_THINKING", 1))
    except Exception:  # noqa: BLE001
        return True


def _kp_effort() -> str:
    return str(_config_value("KP_BUILD_REASONING_EFFORT", "max") or "max").strip() or "max"


def _kp_max_tokens() -> int:
    try:
        return max(1024, config.value_int("KP_BUILD_MAX_TOKENS", 65536))
    except Exception:  # noqa: BLE001
        return 65536


def _snapshot_params(run_context=None) -> dict:
    """P0-E：有 context 时返回快照参数；否则返回全局设置（旧路径）。"""
    ctx = (run_context if run_context is not None
           and runtime_context.runtime_enabled() else None)
    if ctx is not None:
        return {"ctx": ctx, "model": str(ctx.model),
                "thinking": bool(ctx.thinking),
                "effort": str(ctx.reasoning_effort or ""),
                "max_tokens": int(ctx.max_tokens or 0)}
    return {"ctx": None, "model": _kp_model(), "thinking": _kp_thinking(),
            "effort": _kp_effort(), "max_tokens": _kp_max_tokens()}


def _ai_enabled() -> bool:
    """教材补充 AI 是否可用：有 key + 可 import chat + 未被环境/测试套件关闭。

    `KB_TEACH_AI=0` 可显式关闭；`VERIFY_REFRESH_FIXTURES` 是 `tests/run_all.py`
    在**测试进程**里设置的标记（生产/看板进程不会设置）——测试套件必须 0 真实请求，
    所以这里把它当作"AI 不可用"（新用例会 monkeypatch `_ai_enabled` 测 AI 路径）。
    """
    v = os.environ.get(TEACH_AI_ENV, "").strip().lower()
    if v in ("0", "false", "off", "no"):
        return False
    if os.environ.get(TEACH_AI_MARKER_ENV):
        return False
    try:
        from . import chat
        chat.load_api_key()
        return True
    except Exception:  # noqa: BLE001
        return False


def _cost_of(usage) -> float:
    """与知识点总结管线同一价格口径（kp_build.PRICE_IN/PRICE_OUT/FX）。"""
    return float(_kp_build().cost_of(usage or {}))


def _ai_chat(kind: str, sys_p: str, user_p: str, *, max_tokens: int | None = None,
             log=None, run_context=None) -> dict:
    """调 1 次（失败重试 1 次）并按知识点管线口径记账；**成功/失败都返回 calls 明细**。

    返回 `{ok, parsed, raw, usage, tokens:{in,out,reasoning}, cost_cny, elapsed_s,
    attempts, calls:[{attempt,ok,tokens_in,tokens_out,cost_cny,elapsed_s,error}],
    error, model, thinking, reasoning_effort, max_tokens}`。
    """
    from . import chat
    kb = _kp_build()
    # P0-E：有 context 时 model/max/thinking/effort 全用冻结快照，不再读 teach 全局设置。
    snap = _snapshot_params(run_context)
    model = snap["model"]
    thinking = snap["thinking"]
    effort = snap["effort"]
    mt = snap["max_tokens"]
    calls: list = []
    last = ""
    for attempt in range(2):
        t0 = time.perf_counter()
        rec = {"attempt": attempt + 1, "ok": False, "tokens_in": 0,
               "tokens_out": 0, "reasoning_tokens": 0, "cost_cny": 0.0,
               "elapsed_s": 0.0, "error": ""}
        try:
            from . import agent_bridge
            with agent_bridge.pipeline_scope("textbook_teach"):
                r = chat.chat_once(
                    [{"role": "system", "content": sys_p},
                     {"role": "user", "content": user_p}],
                    model=model, max_tokens=mt, thinking=thinking,
                    reasoning_effort=(effort if thinking else None),
                    run_context=snap["ctx"])
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
            rec["error"] = last
            rec["elapsed_s"] = round(time.perf_counter() - t0, 1)
            if log:
                log("  ★教材补充 AI 调用失败（%s 第 %d 次）：%s" % (kind, attempt + 1, last))
            calls.append(rec)
            if attempt == 0:
                time.sleep(1)
            continue
        usage = r.get("usage") or {}
        rec.update({
            "ok": True,
            "tokens_in": int(usage.get("prompt_tokens") or 0),
            "tokens_out": int(usage.get("completion_tokens") or 0),
            "reasoning_tokens": int(kb._reasoning_tokens(usage) or 0),
            "cost_cny": _cost_of(usage),
            "elapsed_s": round(time.perf_counter() - t0, 1),
        })
        calls.append(rec)
        raw = r.get("content") or ""
        parsed = kb.parse_json(raw)
        if isinstance(parsed, dict) and not parsed.get("_parse_error"):
            ti = sum(int(c["tokens_in"]) for c in calls)
            to = sum(int(c["tokens_out"]) for c in calls)
            return {"ok": True, "parsed": parsed, "raw": raw, "usage": usage,
                    "tokens": {"in": ti, "out": to,
                               "reasoning": sum(int(c["reasoning_tokens"]) for c in calls)},
                    "cost_cny": round(sum(float(c["cost_cny"]) for c in calls), 6),
                    "elapsed_s": round(sum(float(c["elapsed_s"]) for c in calls), 1),
                    "attempts": len(calls), "calls": calls, "error": "",
                    "model": model, "thinking": thinking,
                    "reasoning_effort": effort, "max_tokens": mt}
        last = str(parsed.get("_parse_error") or "模型输出不是 JSON 对象")
        if log:
            log("  ★教材补充 AI 输出解析失败（%s 第 %d 次）：%s" % (kind, attempt + 1, last))
        if attempt == 0:
            time.sleep(1)
    return {"ok": False, "parsed": {}, "raw": "", "usage": {},
            "tokens": {"in": 0, "out": 0, "reasoning": 0},
            "cost_cny": 0.0,
            "elapsed_s": round(sum(float(c["elapsed_s"]) for c in calls), 1),
            "attempts": len(calls), "calls": calls, "error": last or "未知错误",
            "model": model, "thinking": thinking, "reasoning_effort": effort,
            "max_tokens": mt}


# ---------------------------------------------------------------- 章级元数据
def _courseware_kps(doc_id: int):
    """读课件 KP 产物 → `(path, kps, sha256, mtime)`；无产物返回 `(path, [], "", 0)`。"""
    kb = _kp_build()
    path = kb.out_file(doc_id)
    doc = kb._load_prev(path)
    kps = [r for r in (doc.get("knowledge_points") or []) if isinstance(r, dict)]
    sha = mtime = ""
    try:
        mtime = float(path.stat().st_mtime)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        pass
    return path, kps, sha, mtime


def _chapter_page_ranges(conn, tb) -> dict:
    """`{heading_path: (page_start, page_end)}`（只读 heading_path/source_ref，不读正文）。"""
    try:
        rows = conn.execute(
            "SELECT heading_path, source_ref FROM chunks WHERE doc_id=? ORDER BY ord",
            (int(tb["id"]),)).fetchall()
    except sqlite3.Error:
        return {}
    out: dict = {}
    for r in rows:
        hp = str(r["heading_path"] or "")
        span = _ref_span(r["source_ref"])
        if not span:
            continue
        lo, hi = span
        cur = out.get(hp)
        out[hp] = (min(cur[0], lo), max(cur[1], hi)) if cur else (lo, hi)
    return out


def _chapter_map_entry(chapters, ranges, no: int, **extra) -> dict:
    for c in chapters:
        if int(c.get("no") or 0) == int(no):
            rng = ranges.get(c["heading_path"]) or (0, 0)
            pages = list(range(rng[0], rng[1] + 1)) if rng[0] else []
            return dict({
                "chapter_no": int(c["no"]), "name": str(c.get("name") or ""),
                "chapter_name": str(c.get("name") or ""),
                "heading_path": str(c.get("heading_path") or ""),
                "page_start": rng[0], "page_end": rng[1],
                "page_label": _fmt_pages(pages), "pages": pages,
                "n_blocks": int(c.get("n_blocks") or 0),
            }, **extra)
    return {}


def _kp_outline_text(kps: list, limit: int = TEACH_MAP_PROMPT_MAX_KPS) -> str:
    lines = []
    for kp in kps[:limit]:
        name = str(kp.get("name") or kp.get("node_name") or "").strip()
        pts = [str(x).strip() for x in (kp.get("points") or [])][:3]
        seg = "；".join(p for p in pts if p)[:150]
        if name:
            lines.append("- %s%s" % (name, ("：" + seg) if seg else ""))
    if len(kps) > limit:
        lines.append("- …（共 %d 条知识点，已截断）" % len(kps))
    return "\n".join(lines) or "（这份课件没有知识点名称）"


def _chapters_text(chapters: list, ranges: dict, limit: int = 80) -> str:
    lines = []
    for c in chapters[:limit]:
        rng = ranges.get(c["heading_path"]) or (0, 0)
        label = _page_span_label(rng[0], rng[1]) if rng[0] else ""
        lines.append("第%d章 %s%s" % (int(c.get("no") or 0), c.get("name") or "",
                                      (" · %s" % label) if label else ""))
    if len(chapters) > limit:
        lines.append("…（共 %d 章，已截断）" % len(chapters))
    return "\n".join(lines)


# ---------------------------------------------------------------- ① MAP：课件 → 章
def _map_cache_valid(cm: dict, tb, kp_sha: str, prompt_sha: str) -> bool:
    if not isinstance(cm, dict) or not int(cm.get("chapter_no") or 0):
        return False
    if int(cm.get("textbook_doc_id") or 0) != int(tb["id"]):
        return False
    if str(cm.get("kp_artifact_sha256") or "") != str(kp_sha):
        return False
    if prompt_sha and str(cm.get("prompt_sha256") or "") != str(prompt_sha):
        return False
    return True


def _cache_map_into_file(out: Path, cm: dict, meta_patch: dict | None = None) -> None:
    """把 chapter_map 回写进已存在的 supplement 文件（不存在就不写，build 会带上）。"""
    if not Path(out).exists():
        return
    data = _load_supplement(out)
    if not data:
        return
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta["chapter_map"] = cm
    if meta_patch:
        meta.update(meta_patch)
    data["meta"] = meta
    _atomic_write_json(data, Path(out))


def _local_chapter_vote(chapters: list, kps: list, doc_title: str) -> dict:
    """本地兜底：所有 KP 各自 `_match_chapter_for_kp` → 章投票（票数优先、总分次之）。"""
    votes: dict = {}
    order: list = []
    for kp in kps:
        hit = _match_chapter_for_kp(chapters, kp, doc_title)
        if not hit:
            continue
        key = hit["heading_path"]
        if key not in votes:
            votes[key] = {"chapter": hit, "votes": 0, "score": 0.0}
            order.append(key)
        votes[key]["votes"] += 1
        votes[key]["score"] += float(hit.get("score") or 0.0)
    if not votes:
        return {"chapter_no": 0}
    best = max(order, key=lambda k: (votes[k]["votes"], votes[k]["score"]))
    return votes[best]["chapter"]


def map_courseware_chapter(doc_id: int, *, force: bool = False,
                           allow_ai: bool = True, out_path=None, db_path=None,
                           conn: sqlite3.Connection | None = None,
                           prompt_path=None, log=print,
                           run_context=None) -> dict:
    """① 课件 → 教材**唯一章节**（AI 1 次；已有缓存/不可用时 0 调用）。

    - 已有 `meta.chapter_map` 且教材 id / KP 产物 sha / 提示词 sha 未变 → 直接复用（cached）；
    - AI 可用：MAP 1 次调用；返回章号不在章列表 → 重试 1 次，仍不合法 → 本地投票兜底；
    - AI 不可用/调用失败/提示词缺失 → 本地投票（`source="local_fallback"`），不阻塞。
    返回字典带 `source(ai|local_fallback)`、`cost_cny/tokens/elapsed_s/error`。
    """
    own = conn is None
    conn = conn or _open_db(db_path)
    try:
        kp_path, kps, kp_sha, _kp_mtime = _courseware_kps(doc_id)
        if not kps:
            return {"ok": False, "chapter_no": 0, "source": "none",
                    "error": "这份课件还没有末级知识点产物（先整理知识点）"}
        tb = pick_textbook(conn)
        if tb is None:
            return {"ok": False, "chapter_no": 0, "source": "none",
                    "error": "当前学科库里没有教材类资料（role=textbook 的 PDF）"}
        chapters = _chapter_candidates(conn, tb)
        ranges = _chapter_page_ranges(conn, tb)
        if not chapters:
            return {"ok": False, "chapter_no": 0, "source": "none",
                    "error": "教材没有可用的章级 heading_path（未按章节切块），无法映射"}
        out = Path(out_path) if out_path else supplement_out_file(doc_id)
        prompts, prompt_sha, prompt_error = {}, "", ""
        try:
            prompts = _load_prompts(prompt_path)
            prompt_sha = prompts["sha256"]
        except TeachError as e:
            prompt_error = str(e)

        # 已有缓存（0 调用）
        if not force:
            prev = _load_supplement(out)
            pm = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
            cm = pm.get("chapter_map")
            if isinstance(cm, dict) and _map_cache_valid(cm, tb, kp_sha, prompt_sha):
                return dict(cm, ok=True, cached=True, source=str(cm.get("source") or "cache"),
                            cost_cny=0.0, tokens={"in": 0, "out": 0}, elapsed_s=0.0)

        ai_ok = bool(allow_ai and _ai_enabled() and prompts)
        err = prompt_error
        if ai_ok:
            title = _doc_title(conn, doc_id)
            user = (prompts["map_user"]
                    .replace("{courseware_title}", title)
                    .replace("{kp_outline}", _kp_outline_text(kps))
                    .replace("{chapters}", _chapters_text(chapters, ranges)))
            res = _ai_chat("teach.map", prompts["map_sys"], user, log=log,
                           run_context=run_context)
            for attempt in range(2):
                if attempt:                    # 章号不合法 → 重试 1 次
                    res = _ai_chat("teach.map", prompts["map_sys"], user, log=log,
                           run_context=run_context)
                if not res.get("ok"):
                    err = str(res.get("error") or "AI 调用失败")
                    break
                parsed = res.get("parsed") or {}
                try:
                    no = int(str(parsed.get("chapter_no")).strip())
                except (TypeError, ValueError):
                    err = "AI 返回的 chapter_no 不是整数：%r" % parsed.get("chapter_no")
                    continue
                if not any(int(c.get("no") or 0) == no for c in chapters):
                    err = "AI 返回的章号不在章列表：%r" % no
                    continue
                cm = _chapter_map_entry(
                    chapters, ranges, no,
                    source="ai",
                    confidence=str(parsed.get("confidence") or "").strip().lower()
                    if str(parsed.get("confidence") or "").strip().lower()
                    in ("high", "medium", "low") else "medium",
                    reason=str(parsed.get("reason") or "").strip()[:120],
                    chapter_name_ai=str(parsed.get("chapter_name") or "").strip()[:60],
                    textbook_doc_id=int(tb["id"]),
                    textbook_title=str(tb.get("title") or ""),
                    kp_artifact=str(kp_path), kp_artifact_sha256=kp_sha,
                    prompt_sha256=prompt_sha,
                    cached=False,
                    cost_cny=float(res.get("cost_cny") or 0.0),
                    tokens=res.get("tokens") or {"in": 0, "out": 0},
                    elapsed_s=float(res.get("elapsed_s") or 0.0),
                    attempts=int(res.get("attempts") or 1),
                    error="")
                _cache_map_into_file(out, cm, {"ai_map_cost_cny": cm["cost_cny"],
                                               "ai_map_source": "ai"})
                return dict(cm, ok=True)

        # ---- 本地投票兜底（AI 关闭/失败/提示词缺失）----
        title = _doc_title(conn, doc_id)
        hit = _local_chapter_vote(chapters, kps, title)
        if not hit:
            return {"ok": False, "chapter_no": 0, "source": "local_fallback",
                    "error": err or "本地词项投票也没有命中任何章",
                    "cost_cny": 0.0, "tokens": {"in": 0, "out": 0}, "elapsed_s": 0.0}
        cm = _chapter_map_entry(
            chapters, ranges, int(hit["no"]),
            source="local_fallback", confidence="low",
            reason=("本地词项投票兜底（AI %s）：本章在本轮 KP 投票中票数最高，"
                    "共 %d 章候选" % ("可用但未采用" if ai_ok else "不可用/已关闭",
                                     len(chapters))),
            chapter_name_ai="", textbook_doc_id=int(tb["id"]),
            textbook_title=str(tb.get("title") or ""),
            kp_artifact=str(kp_path), kp_artifact_sha256=kp_sha,
            prompt_sha256=prompt_sha, cached=False, cost_cny=0.0,
            tokens={"in": 0, "out": 0}, elapsed_s=0.0, attempts=0,
            error=err or "")
        _cache_map_into_file(out, cm, {"ai_map_source": "local_fallback"})
        if log and err:
            log("  ⚠ 教材补充章映射降级本地：%s" % err)
        return dict(cm, ok=True)
    finally:
        if own and conn is not None:
            conn.close()


# ---------------------------------------------------------------- ② LOCAL：KP → 小节
def _load_sections_detail(tb_id: int) -> dict:
    """读教材的《章节细目》产物（C-0092 纯正则产物，0 AI）。"""
    p = config.LOG_DIR / "knowledge" / ("%d-章节细目.json" % int(tb_id))
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "chapters": [], "path": str(p)}
    if not isinstance(raw, dict) or not isinstance(raw.get("chapters"), list):
        return {"ok": False, "chapters": [], "path": str(p)}
    return {"ok": True, "chapters": raw["chapters"], "path": str(p)}


def _local_kp_sections(conn, tb, chapter: dict, kp: dict) -> dict:
    """② 本地（0 AI）：把一条 KP 定位到教材章内的 1~2 个具体小节 + 页码范围 + 原文摘录。

    优先《章节细目》的小节标题做词项 IDF 打分；无产物/标题 0 分时退到
    `_chapter_passages` 的章内段落页范围当"伪小节"（仍只查该章）。
    `section_material` 只取选中页范围的教材块原文（≤12000 字，带 `（p.起-止）`）。
    """
    heading = str(chapter.get("heading_path") or "")
    ch_no = int(chapter.get("chapter_no") or chapter.get("no") or 0)
    kp_text = _kp_query_text(kp)
    terms = _terms(kp_text, limit=SUPPLEMENT_TERMS_MAX)
    kp_name = str(kp.get("name") or "")
    detail = _load_sections_detail(tb["id"])
    secs: list = []
    if detail.get("ok"):
        mine = None
        for c in detail["chapters"]:
            try:
                if int(c.get("no") or 0) == ch_no:
                    mine = c
                    break
            except (TypeError, ValueError):
                continue
        secs = [s for s in ((mine or {}).get("sections") or []) if isinstance(s, dict)]
    picked: list = []
    if secs and terms:
        K = len(secs)
        titles = [str(s.get("title") or "") for s in secs]
        weights: dict = {}
        for t in terms:
            df = sum(1 for nm in titles if t in nm)
            if 0 < df <= max(2, int(K * 0.5)):
                weights[t] = (len(t) - 1) * math.log((K + 1) / (df + 0.5))
        scored = []
        for s in secs:
            title = str(s.get("title") or "")
            hits = [t for t in weights if t in title]
            score = sum(weights[t] for t in hits)
            if kp_name and title and len(title) >= 2 and (kp_name in title or title in kp_name):
                score += 0.8
            if score > 0:
                scored.append((score, s, hits))
        scored.sort(key=lambda x: -x[0])
        for score, s, hits in scored[:TEACH_SUPP_SECTIONS_MAX]:
            a = int(s.get("page_start") or 0)
            b = int(s.get("page_end") or a or 0)
            picked.append({"index": int(s.get("index") or 0),
                           "title": str(s.get("title") or ""),
                           "level": int(s.get("level") or 1),
                           "page_start": a, "page_end": b,
                           "page_label": _page_span_label(a, b),
                           "score": round(float(score), 3),
                           "hit_terms": hits[:6]})
    note = ""
    if not picked:
        # 退路：章内段落（仍只查该章）的页码范围当伪小节
        passages = _chapter_passages(conn, tb, heading, terms,
                                     limit=TEACH_SUPP_SECTIONS_MAX)
        for i, p in enumerate(passages, 1):
            a, b = int(p.get("page_start") or 0), int(p.get("page_end") or 0)
            picked.append({"index": i, "title": "（教材 %s 段落）" % p.get("page_label"),
                           "level": 1, "page_start": a, "page_end": b,
                           "page_label": _page_span_label(a, b),
                           "score": float(p.get("score") or 0),
                           "hit_terms": p.get("hit_terms") or []})
        note = ("《章节细目》标题没有匹配 → 用本章词项得分最高的段落页范围兜底"
                if passages else
                "《章节细目》与章内段落都没有与这条 KP 匹配的内容（不出 AI 补充）")
    pages: list = []
    for s in picked:
        for pg in range(int(s.get("page_start") or 0),
                        int(s.get("page_end") or 0) + 1):
            if pg and pg not in pages:
                pages.append(pg)
    pages = sorted(pages)[:SUPPLEMENT_MAX_PAGES]
    # 教材原文摘录：只取选中页范围、只查该章
    material = ""
    chunk_ids: list = []
    try:
        rows = conn.execute(
            "SELECT id, text, source_ref FROM chunks WHERE doc_id=? AND heading_path=? "
            "ORDER BY ord", (int(tb["id"]), heading)).fetchall()
    except sqlite3.Error:
        rows = []
    parts = []
    for r in rows:
        span = _ref_span(r["source_ref"])
        if span and not (set(range(span[0], span[1] + 1)) & set(pages)):
            continue
        lo, hi = span if span else (0, 0)
        txt = re.sub(r"\s+", " ", str(r["text"] or "")).strip()
        if not txt:
            continue
        part = "（%s）%s" % (_page_span_label(lo, hi) or "p.?", txt)
        if len(material) + len(part) > TEACH_SUPP_MATERIAL_MAX:
            note = (note + "；" if note else "") + "原文摘录已达 %d 字上限，后续块被截断" \
                % TEACH_SUPP_MATERIAL_MAX
            break
        parts.append(part)
        chunk_ids.append(int(r["id"]))
        material = "\n".join(parts)
    return {"sections": picked, "pages": pages, "page_label": _fmt_pages(pages),
            "section_material": material, "section_material_chars": len(material),
            "chunk_ids": chunk_ids, "note": note or
            ("本地定位到：%s" % "、".join(s["title"] for s in picked) if picked else "")}


# ---------------------------------------------------------------- ③ SUPP：按需 AI
def _kp_sha(kp: dict) -> str:
    return hashlib.sha256(json.dumps(kp, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def _supp_cache_key(prompt_sha: str, kp: dict, tb_id: int, chapter_no: int,
                    pages: list) -> str:
    blob = json.dumps({"v": SUPPLEMENT_VERSION, "prompt": prompt_sha,
                       "kp": _kp_sha(kp), "tb": int(tb_id),
                       "ch": int(chapter_no), "pages": sorted(int(x) for x in pages)},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def estimate_map(doc_id: int, *, db_path=None, conn: sqlite3.Connection | None = None,
                 run_context=None,
                 prompt_path=None) -> dict:
    """MAP 调用预估（0 模型、0 写文件）。"""
    own = conn is None
    conn = conn or _open_db(db_path)
    try:
        _p, kps, kp_sha, _m = _courseware_kps(doc_id)
        tb = pick_textbook(conn)
        chapters = _chapter_candidates(conn, tb) if tb else []
        ranges = _chapter_page_ranges(conn, tb) if tb else {}
        try:
            prompts = _load_prompts(prompt_path)
        except TeachError:
            prompts = {}
        user_len = len((prompts.get("map_user") or "")) + len(_kp_outline_text(kps)) + \
            len(_chapters_text(chapters, ranges))
        kb = _kp_build()
        est_in = int(user_len / max(1.0, kb.CHARS_PER_TOKEN))
        est_out = min(_snapshot_params(run_context)["max_tokens"], int(est_in * kb.REASON_RATIO) + 400)
        cost = round(est_in / 1e6 * kb.PRICE_IN * kb.FX
                     + est_out / 1e6 * kb.PRICE_OUT * kb.FX, 6)
        return {"calls": 1, "est_tokens_in": est_in, "est_tokens_out": est_out,
                "cost_cny_max": cost,
                "model": _snapshot_params(run_context)["model"],
                "thinking": _snapshot_params(run_context)["thinking"],
                "reasoning_effort": _snapshot_params(run_context)["effort"],
                "max_tokens": _snapshot_params(run_context)["max_tokens"],
                "ai_enabled": _ai_enabled(),
                "prompt_sha256": str(prompts.get("sha256") or ""),
                "rule": ("MAP：1 次小输入调用（课件标题+%d 条 KP 大纲+%d 章）；"
                         "按 %.2f 字符/token、输出≈输入×%.1f+400（上限 max_tokens）估"
                         % (len(kps), len(chapters), kb.CHARS_PER_TOKEN,
                            kb.REASON_RATIO))}
    finally:
        if own and conn is not None:
            conn.close()


def estimate_kp_supplement(doc_id: int, kp_id, *, db_path=None,
                           run_context=None,
                           conn: sqlite3.Connection | None = None,
                           prompt_path=None) -> dict:
    """单条 KP 的 SUPP 调用预估（0 模型、0 写文件；本地定位可现算）。"""
    own = conn is None
    conn = conn or _open_db(db_path)
    try:
        _p, kps, _kp_sha_val, _m = _courseware_kps(doc_id)
        kp = next((k for k in kps if str(k.get("id") or "") == str(kp_id)), None)
        if kp is None:
            return {"calls": 1, "ok": False, "error": "这份课件里没有这条知识点 %s" % kp_id}
        tb = pick_textbook(conn)
        if tb is None:
            return {"calls": 1, "ok": False, "error": "当前学科库没有教材"}
        out = supplement_out_file(doc_id)
        data = _load_supplement(out)
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        cm = meta.get("chapter_map") if isinstance(meta.get("chapter_map"), dict) else {}
        if not cm:
            chapters = _chapter_candidates(conn, tb)
            ranges = _chapter_page_ranges(conn, tb)
            hit = _local_chapter_vote(chapters, kps, _doc_title(conn, doc_id))
            cm = _chapter_map_entry(chapters, ranges, int(hit.get("no") or 0)) if hit else {}
        if not cm:
            return {"calls": 1, "ok": False,
                    "error": "无法定位章节（教材未按章切块或 KP 与教材无关）"}
        sec = _local_kp_sections(conn, tb, cm, kp)
        try:
            prompts = _load_prompts(prompt_path)
        except TeachError:
            prompts = {}
        title = _doc_title(conn, doc_id)
        user = (prompts.get("supp_user") or "")
        for ph, val in (("{courseware_title}", title),
                        ("{chapter}", "第%s章 %s" % (cm.get("chapter_no"), cm.get("chapter_name"))),
                        ("{kp_name}", str(kp.get("name") or "")),
                        ("{kp_points}", "\n".join("- %s" % p for p in (kp.get("points") or [])[:8])),
                        ("{kp_summary}", str(kp.get("summary") or "")[:600]),
                        ("{section_outline}", "\n".join(
                            "- %s（%s）" % (s.get("title"), s.get("page_label"))
                            for s in sec.get("sections") or [])),
                        ("{section_material}", sec.get("section_material") or "")):
            user = user.replace(ph, val)
        kb = _kp_build()
        est_in = int((len(user) + len(prompts.get("supp_sys") or ""))
                     / max(1.0, kb.CHARS_PER_TOKEN))
        est_out = min(_snapshot_params(run_context)["max_tokens"], int(est_in * kb.REASON_RATIO) + 600)
        cost = round(est_in / 1e6 * kb.PRICE_IN * kb.FX
                     + est_out / 1e6 * kb.PRICE_OUT * kb.FX, 6)
        return {"ok": True, "calls": 1, "doc_id": int(doc_id), "kp_id": str(kp_id),
                "est_tokens_in": est_in, "est_tokens_out": est_out,
                "cost_cny_max": cost,
                "model": _snapshot_params(run_context)["model"],
                "thinking": _snapshot_params(run_context)["thinking"],
                "reasoning_effort": _snapshot_params(run_context)["effort"],
                "max_tokens": _snapshot_params(run_context)["max_tokens"],
                "ai_enabled": _ai_enabled(),
                "prompt_sha256": str(prompts.get("sha256") or ""),
                "sections": sec.get("sections") or [],
                "pages": sec.get("pages") or [],
                "material_chars": sec.get("section_material_chars") or 0,
                "cached": False,
                "rule": ("SUPP：1 次按需调用（该 KP + %d 字教材摘录）；"
                         "按 %.2f 字符/token、输出≈输入×%.1f+600（上限 max_tokens）估"
                         % (sec.get("section_material_chars") or 0,
                            kb.CHARS_PER_TOKEN, kb.REASON_RATIO))}
    finally:
        if own and conn is not None:
            conn.close()


def _backup_supplement(out: Path) -> str:
    """旧产物备份 `.bak-<ts>`（旧版升级/force 重写前；失败不阻断）。"""
    if not Path(out).exists():
        return ""
    bak = Path(out).with_name(Path(out).name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    try:
        shutil.copy2(out, bak)
        return str(bak)
    except OSError:
        return ""


def build_supplement(doc_id: int, *, out_path=None, force: bool = False,
                     allow_ai: bool = True, db_path=None,
                     conn: sqlite3.Connection | None = None, prompt_path=None,
                     log=print, run_context=None) -> dict:
    """混合管线：AI 章映射（1 次/课件，可缓存）→ 本地逐 KP 小节/页码 → 写产物。

    **此处不做 per-KP AI**（省钱/省时；按需分析见 `analyze_kp_supplement`）。
    """
    own = conn is None
    conn = conn or _open_db(db_path)
    try:
        kp_path, kps, kp_sha, kp_mtime = _courseware_kps(doc_id)
        if not kps:
            return {"ok": False, "status": "no_kps", "doc_id": int(doc_id),
                    "out_path": str(out_path or supplement_out_file(doc_id)),
                    "error": "这份课件还没有末级知识点产物（先整理知识点再补教材）"}
        out = Path(out_path) if out_path else supplement_out_file(doc_id)
        # P0-E：真正 AI 入口只建一次 context；PIPELINE_RUNTIME=0 不建。
        run_ctx = (run_context if run_context is not None
                   else (runtime_context.make_context("textbook_teach", doc_id=doc_id)
                         if runtime_context.runtime_enabled() else None))
        doc_title = _doc_title(conn, doc_id)
        tb = pick_textbook(conn)
        prompts, prompt_sha, prompt_error = {}, "", ""
        try:
            prompts = _load_prompts(prompt_path)
            prompt_sha = prompts["sha256"]
        except TeachError as e:
            prompt_error = str(e)

        prev_data = _load_supplement(out) if out.exists() else {}
        prev_meta = prev_data.get("meta") if isinstance(prev_data.get("meta"), dict) else {}
        prev_version = str(prev_meta.get("version") or "")
        legacy = bool(out.exists()) and (
            prev_version != SUPPLEMENT_VERSION
            or str(prev_meta.get("status") or "") not in ("no_textbook",)
            and not isinstance(prev_meta.get("chapter_map"), dict))
        if out.exists() and not force and not legacy:
            same = (
                int(prev_meta.get("textbook_doc_id") or 0)
                == int(tb["id"] if tb else 0)
                and str(prev_meta.get("kp_artifact_sha256") or "") == str(kp_sha)
                and abs(float(prev_meta.get("kp_artifact_mtime") or 0.0)
                        - float(kp_mtime or 0.0)) < 1e-6
                and str(prev_meta.get("prompt_sha256") or "") == str(prompt_sha)
            )
            if same:
                return {"ok": True, "status": "skipped", "out_path": str(out),
                        "meta": prev_meta, "doc_id": int(doc_id)}

        prev_backup = ""
        if out.exists() and (legacy or force):
            prev_backup = _backup_supplement(out)

        generated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        meta = {
            "version": SUPPLEMENT_VERSION,
            "doc_id": int(doc_id),
            "doc_title": doc_title,
            "status": "done",
            "textbook_doc_id": int(tb["id"]) if tb else 0,
            "textbook_title": str(tb.get("title") or "") if tb else "",
            "kp_artifact": str(kp_path),
            "kp_artifact_mtime": kp_mtime,
            "kp_artifact_sha256": kp_sha,
            "prompt_path": str(prompts.get("path") or ""),
            "prompt_sha256": prompt_sha,
            "prompt_error": prompt_error,
            "ai_enabled": _ai_enabled(),
            "ai_model": _snapshot_params(run_ctx)["model"],
            "chapter_map": {},
            "ai_map_source": "",
            "ai_map_cost_cny": 0.0,
            "ai_map_tokens": {"in": 0, "out": 0},
            "ai_map_elapsed_s": 0.0,
            "ai_map_error": "",
            "legacy_prev_version": prev_version if legacy else "",
            "prev_backup": prev_backup,
            "generated_at": generated_at,
            "updated_at": generated_at,
            "created_at": time.time(),
            "n_kps": len(kps),
            "n_matched": 0,
            "n_no_match": 0,
            "note": "",
        }
        if tb is None:
            meta["status"] = "no_textbook"
            meta["note"] = ("当前学科库里没有教材类资料（role=textbook 的 PDF）。"
                            "把教材 PDF 放进 raw 并入库后，在课件栏点「补做教材补充」即可。")
            _atomic_write_json({"meta": meta, "kps": {}}, out)
            if log:
                log("教材补充：doc#%d 无教材 → 已写 no_textbook 状态（0 次模型调用）"
                    % int(doc_id))
            return {"ok": True, "status": "no_textbook", "out_path": str(out),
                    "meta": meta, "doc_id": int(doc_id), "kps": {}}

        # ① AI 章映射（缓存命中则 0 调用；失败降级本地）
        cm = map_courseware_chapter(doc_id, force=force, allow_ai=allow_ai,
                                    out_path=out, db_path=db_path, conn=conn,
                                    prompt_path=prompt_path, log=log,
                                    run_context=run_ctx)
        meta["chapter_map"] = cm if isinstance(cm, dict) else {}
        meta["ai_map_source"] = str((cm or {}).get("source") or "")
        meta["ai_map_cost_cny"] = float((cm or {}).get("cost_cny") or 0.0)
        meta["ai_map_tokens"] = (cm or {}).get("tokens") or {"in": 0, "out": 0}
        meta["ai_map_elapsed_s"] = float((cm or {}).get("elapsed_s") or 0.0)
        meta["ai_map_error"] = str((cm or {}).get("error") or "")
        if not int((cm or {}).get("chapter_no") or 0):
            meta["status"] = "no_match"
            meta["note"] = ("课件→教材章映射失败/无匹配（%s）；没有可补充的教材章节"
                            % (meta["ai_map_error"] or "无教材章候选"))
            _atomic_write_json({"meta": meta, "kps": {}}, out)
            return {"ok": True, "status": "no_match", "out_path": str(out),
                    "meta": meta, "doc_id": int(doc_id), "kps": {}}

        chapter = {"chapter_no": int(cm.get("chapter_no") or 0),
                   "name": str(cm.get("name") or cm.get("chapter_name") or ""),
                   "chapter_name": str(cm.get("name") or cm.get("chapter_name") or ""),
                   "heading_path": str(cm.get("heading_path") or ""),
                   "page_start": int(cm.get("page_start") or 0),
                   "page_end": int(cm.get("page_end") or 0),
                   "source": str(cm.get("source") or "")}
        kps_map: dict = {}
        n_matched = n_no = 0
        for i, kp in enumerate(kps, 1):
            kp_id = str(kp.get("id") or ("KP-%d" % i))
            kp_name = str(kp.get("name") or kp.get("node_name") or "")
            kp_text = _kp_query_text(kp)
            sec = _local_kp_sections(conn, tb, chapter, kp)
            terms = _terms(kp_text, limit=SUPPLEMENT_TERMS_MAX)
            passages = _chapter_passages(conn, tb, chapter["heading_path"], terms,
                                         limit=SUPPLEMENT_MAX_PASSAGES)
            sel_pages = set(sec.get("pages") or [])
            if sel_pages:
                scoped = [p for p in passages
                          if set(range(int(p.get("page_start") or 0),
                                       int(p.get("page_end") or 0) + 1)) & sel_pages]
                passages = scoped or passages
            status = "done" if (sec.get("sections") or passages) else "no_match"
            if status == "done":
                n_matched += 1
            else:
                n_no += 1
            kps_map[kp_id] = {
                "kp_id": kp_id, "kp_name": kp_name, "status": status,
                "chapter": dict(chapter),
                "sections": sec.get("sections") or [],
                "pages": sec.get("pages") or [],
                "page_label": sec.get("page_label") or "",
                "section_material_chars": int(sec.get("section_material_chars") or 0),
                "material_preview": (sec.get("section_material") or "")[:600],
                "passages": passages,
                "added_terms": _supplement_added_terms(passages, kp_text),
                "note": ("教材：" + (sec.get("note") or "")) if status == "done"
                        else "教材：本地未定位到小节/段落（可稍后再试或换教材）",
            }
        meta["n_matched"] = n_matched
        meta["n_no_match"] = n_no
        meta["status"] = "done" if n_matched else "no_match"
        meta["note"] = ("教材「%s」→ 第%d章 %s（%s）；%d 条 KP 中 %d 条定位到小节/段落"
                        % (meta["textbook_title"], chapter["chapter_no"],
                           chapter["chapter_name"], meta["ai_map_source"],
                           len(kps_map), n_matched))
        _atomic_write_json({"meta": meta, "kps": kps_map}, out)
        if log:
            log("教材补充：doc#%d · 教材「%s」→ 第%d章 %s（%s，CNY%.6f）· "
                "%d 条 KP → 定位 %d / 无匹配 %d（per-KP AI 未调用）"
                % (int(doc_id), meta["textbook_title"], chapter["chapter_no"],
                   chapter["chapter_name"], meta["ai_map_source"],
                   meta["ai_map_cost_cny"], len(kps_map), n_matched, n_no))
        return {"ok": True, "status": meta["status"], "out_path": str(out),
                "meta": meta, "doc_id": int(doc_id), "kps": kps_map}
    finally:
        if own and conn is not None:
            conn.close()


def _scale_pages(pages, lo: int, hi: int) -> list:
    """过滤到章节页范围（越界剔除），去重升序。"""
    out = []
    for x in pages or []:
        try:
            n = int(str(x).strip())
        except (TypeError, ValueError):
            continue
        if lo <= n <= hi and n not in out:
            out.append(n)
    return sorted(out)[:SUPPLEMENT_MAX_PAGES]


def analyze_kp_supplement(doc_id: int, kp_id, *, force: bool = False,
                          allow_ai: bool = True, out_path=None, db_path=None,
                          conn: sqlite3.Connection | None = None,
                          prompt_path=None, log=print, run_context=None) -> dict:
    """③ 按需 AI：分析某条 KP 在教材对应小节里"有什么可补充的"（带缓存）。

    - 无 supplement 文件/旧版（C-0124）/缺该 KP → 先本地 build 升级（不重复 AI 章映射）；
    - AI 不可用 → `{ok:false,status:"ai_disabled"}`，保留本地 sections/passages 兜底；
    - AI 失败 → `entry.ai.status="failed"` + error，local 兜底不变；
    - 缓存键 = prompt sha + KP sha + 教材 id + 章号 + 小节页范围；未变且非 force → cached（0 调用）。
    """
    own = conn is None
    conn = conn or _open_db(db_path)
    try:
        out = Path(out_path) if out_path else supplement_out_file(doc_id)
        run_ctx = (run_context if run_context is not None
                   else (runtime_context.make_context("textbook_teach", doc_id=doc_id)
                         if runtime_context.runtime_enabled() else None))
        data = _load_supplement(out)
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        if (not data or str(meta.get("version") or "") != SUPPLEMENT_VERSION
                or not isinstance(meta.get("chapter_map"), dict)):
            build_supplement(doc_id, out_path=out, force=True, allow_ai=allow_ai,
                             db_path=db_path, conn=conn, prompt_path=prompt_path,
                             log=log, run_context=run_ctx)
            data = _load_supplement(out)
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        if not data:
            return {"ok": False, "status": "no_supplement_file",
                    "error": "没有教材补充产物，且本地构建失败"}
        if str(meta.get("status") or "") in ("no_textbook",):
            return {"ok": False, "status": "no_textbook",
                    "error": "当前学科库没有教材：先把教材 PDF 入库，再补做教材补充"}
        kps_map = data.get("kps") if isinstance(data.get("kps"), dict) else {}
        entry = kps_map.get(str(kp_id))
        if not entry:
            build_supplement(doc_id, out_path=out, force=True, allow_ai=allow_ai,
                             db_path=db_path, conn=conn, prompt_path=prompt_path,
                             log=log, run_context=run_ctx)
            data = _load_supplement(out)
            kps_map = data.get("kps") if isinstance(data.get("kps"), dict) else {}
            entry = kps_map.get(str(kp_id))
        if not entry:
            return {"ok": False, "status": "no_kp",
                    "error": "教材补充产物里没有这条 KP（可能产物生成于它之前）"}
        chapter = entry.get("chapter") if isinstance(entry.get("chapter"), dict) else {}
        lo, hi = int(chapter.get("page_start") or 0), int(chapter.get("page_end") or 0)
        _p, kps, _sha, _m = _courseware_kps(doc_id)
        kp = next((k for k in kps if str(k.get("id") or "") == str(kp_id)), None)
        if kp is None:
            return {"ok": False, "status": "no_kp", "error": "课件产物里没有这条 KP"}
        tb = pick_textbook(conn)
        if tb is None:
            return {"ok": False, "status": "no_textbook",
                    "error": "当前学科库没有教材"}
        if not (allow_ai and _ai_enabled()):
            return {"ok": False, "status": "ai_disabled",
                    "error": "教材补充 AI 未启用（没有可用密钥，或 KB_TEACH_AI=0）",
                    "entry": entry, "fallback": entry.get("passages") or []}
        prompts = _load_prompts(prompt_path)
        sec = _local_kp_sections(conn, tb, chapter, kp)
        chapter_label = "第%d章 %s" % (int(chapter.get("chapter_no") or 0),
                                      str(chapter.get("chapter_name") or ""))
        kp_points = "\n".join("- %s" % str(p).strip()
                              for p in (kp.get("points") or [])[:8] if str(p).strip())
        sec_outline = "\n".join("- %s（%s）" % (s.get("title"), s.get("page_label"))
                                for s in (sec.get("sections") or []))
        user = (prompts["supp_user"]
                .replace("{courseware_title}", _doc_title(conn, doc_id))
                .replace("{chapter}", chapter_label)
                .replace("{kp_name}", str(kp.get("name") or ""))
                .replace("{kp_points}", kp_points or "（课件没有列要点）")
                .replace("{kp_summary}", str(kp.get("summary") or "")[:600])
                .replace("{section_outline}", sec_outline or "（本地未匹配到小节）")
                .replace("{section_material}", sec.get("section_material") or ""))
        cache_key = _supp_cache_key(prompts["sha256"], kp, int(tb["id"]),
                                    int(chapter.get("chapter_no") or 0),
                                    sec.get("pages") or [])
        old_ai = entry.get("ai") if isinstance(entry.get("ai"), dict) else {}
        if (not force and str(old_ai.get("cache_key") or "") == cache_key
                and str(old_ai.get("status") or "") in ("done", "no_supplement")):
            return {"ok": True, "status": str(old_ai.get("status")), "cached": True,
                    "entry": entry, "ai": old_ai, "doc_id": int(doc_id),
                    "kp_id": str(kp_id), "cost_cny": 0.0}
        res = _ai_chat("teach.supplement", prompts["supp_sys"], user, log=log,
                       run_context=run_ctx)
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        base = {"model": res.get("model"), "cost_cny": round(float(res.get("cost_cny") or 0), 6),
                "tokens": res.get("tokens") or {"in": 0, "out": 0},
                "elapsed_s": float(res.get("elapsed_s") or 0.0),
                "attempts": int(res.get("attempts") or 0),
                "prompt_sha256": prompts["sha256"], "cache_key": cache_key,
                "generated_at": generated_at, "section_pages": sec.get("pages") or []}
        if not res.get("ok"):
            entry["ai"] = dict(base, status="failed",
                               error=str(res.get("error") or "AI 调用失败"),
                               verdict="", summary="", points=[], gaps=[], citations=[])
            _update_supplement_entry(out, meta, kp_id, entry, ai_cost_delta=base["cost_cny"])
            return {"ok": False, "status": "failed", "error": entry["ai"]["error"],
                    "entry": entry, "ai": entry["ai"],
                    "fallback": entry.get("passages") or []}
        parsed = res.get("parsed") or {}
        verdict = str(parsed.get("verdict") or "").strip()
        if verdict not in ("has_supplement", "no_supplement"):
            verdict = "no_supplement"
        points, seen = [], set()
        for p in (parsed.get("points") or []):
            if not isinstance(p, dict):
                continue
            text = str(p.get("text") or "").strip()[:160]
            if not text:
                continue
            key = re.sub(r"\s+", "", text)
            if key in seen:
                continue
            seen.add(key)
            pages = _scale_pages(p.get("pages"), lo, hi)
            kind = str(p.get("kind") or "").strip()
            if kind not in TEACH_SUPP_KINDS:
                kind = "概念"
            points.append({"text": text[:120], "pages": pages, "kind": kind})
            if len(points) >= TEACH_SUPP_POINTS_MAX:
                break
        gaps = [str(g).strip()[:120] for g in (parsed.get("gaps") or [])
                if str(g or "").strip()][:TEACH_SUPP_GAPS_MAX]
        citations = []
        for c in (parsed.get("citations") or []):
            if not isinstance(c, dict):
                continue
            quote = str(c.get("quote") or "").strip()[:80]
            if not quote:
                continue
            citations.append({"pages": _scale_pages(c.get("pages"), lo, hi),
                              "quote": quote})
            if len(citations) >= TEACH_SUPP_CITATIONS_MAX:
                break
        summary = str(parsed.get("summary") or "").strip()[:160]
        if verdict == "no_supplement":
            points = []
        status = "done" if verdict == "has_supplement" else "no_supplement"
        entry["ai"] = dict(base, status=status, verdict=verdict, summary=summary,
                           points=points, gaps=gaps, citations=citations, error="")
        _update_supplement_entry(out, meta, kp_id, entry, ai_cost_delta=base["cost_cny"])
        if log:
            log("教材补充 AI：doc#%d %s → %s（%d 点 · CNY%.6f · %.1fs）"
                % (int(doc_id), kp_id, status, len(points), base["cost_cny"],
                   base["elapsed_s"]))
        return {"ok": True, "status": status, "cached": False, "entry": entry,
                "ai": entry["ai"], "doc_id": int(doc_id), "kp_id": str(kp_id),
                "cost_cny": base["cost_cny"]}
    finally:
        if own and conn is not None:
            conn.close()


def _update_supplement_entry(out: Path, meta: dict, kp_id, entry: dict,
                             *, ai_cost_delta: float = 0.0) -> None:
    """把某条 KP 的 `ai` 写回 supplement 文件（保留其余条目/本地兜底）。"""
    data = _load_supplement(out)
    kps_map = data.get("kps") if isinstance(data.get("kps"), dict) else {}
    kps_map[str(kp_id)] = entry
    data["kps"] = kps_map
    meta = dict(meta)
    meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta["ai_kp_calls"] = int(meta.get("ai_kp_calls") or 0) + 1
    meta["ai_kp_cost_cny"] = round(float(meta.get("ai_kp_cost_cny") or 0.0)
                                   + float(ai_cost_delta or 0.0), 6)
    data["meta"] = meta
    _atomic_write_json(data, out)


# ---------------------------------------------------------------- 状态 / 读取
def supplement_status(doc_id: int, *, db_path=None,
                      conn: sqlite3.Connection | None = None) -> dict:
    """这份课件的「教材补充」状态（零模型、零写入；供课件栏提醒用）。

    新增：旧版产物（C-0124，无 `chapter_map`）会被标成 `stale=true` 且
    `needs_backfill=true`（reason 明说"旧版产物，需 AI 重新定位"）。
    `ai` 摘要给前端显示 AI 开关/章映射来源/已分析条数。
    """
    own = conn is None
    conn = conn or _open_db(db_path)
    try:
        from . import kp_build as _kp
        kp_path = _kp.out_file(doc_id)
        has_artifact = kp_path.exists()
        kp_mtime = kp_path.stat().st_mtime if has_artifact else 0.0
        books = textbook_docs(conn)
        textbooks = [{"doc_id": int(d["id"]), "title": str(d.get("title") or ""),
                      "corpus": str(d.get("corpus") or "main"),
                      "n_chunks": int(d.get("n_chunks") or 0)} for d in books]
        cur_tb_id = int(books[0]["id"]) if books else 0
        out = supplement_out_file(doc_id)
        exists = out.exists()
        meta: dict = {}
        data: dict = {}
        if exists:
            data = _load_supplement(out)
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        version = str(meta.get("version") or "")
        chapter_map = meta.get("chapter_map") if isinstance(meta.get("chapter_map"), dict) else {}
        legacy = bool(exists) and (
            version != SUPPLEMENT_VERSION
            or (str(meta.get("status") or "") not in ("no_textbook",)
                and not chapter_map))
        sup = {
            "exists": bool(exists),
            "status": str(meta.get("status") or ""),
            "version": version,
            "legacy": legacy,
            "stale": legacy,
            "generated_at": str(meta.get("generated_at") or ""),
            "n_kps": int(meta.get("n_kps") or 0),
            "n_matched": int(meta.get("n_matched") or 0),
            "textbook_doc_id": int(meta.get("textbook_doc_id") or 0),
            "ai_map_source": str(meta.get("ai_map_source") or ""),
            "ai_map_cost_cny": float(meta.get("ai_map_cost_cny") or 0.0),
        }
        kps_map = data.get("kps") if isinstance(data.get("kps"), dict) else {}
        n_ai_ready = sum(1 for e in kps_map.values()
                         if isinstance(e, dict) and isinstance(e.get("ai"), dict)
                         and str((e.get("ai") or {}).get("status") or "")
                         in ("done", "no_supplement"))
        n_ai_missing = max(0, len(kps_map) - n_ai_ready)
        reasons: list = []
        if has_artifact and books:
            if not exists:
                reasons.append("还没有教材补充产物")
            elif legacy:
                reasons.append("旧版产物（C-0124 纯本地，无 AI 章节映射/小节）→ "
                               "需要重新 AI 定位/补做")
            elif sup["status"] == "no_textbook":
                reasons.append("上次补做时还没有教材")
            elif sup["status"] == "failed":
                reasons.append("上次补做失败")
            elif sup["textbook_doc_id"] and int(sup["textbook_doc_id"]) != cur_tb_id:
                reasons.append("教材已经换成 doc#%d" % cur_tb_id)
            elif not chapter_map:
                reasons.append("产物缺 AI 章节映射 → 需要重新定位")
            elif float(meta.get("kp_artifact_mtime") or 0.0) + 1e-6 < float(kp_mtime):
                reasons.append("KP 产物比教材补充新（整理过之后还没补）")
        needs = bool(reasons)
        if not has_artifact:
            reason = "还没有末级知识点产物（先整理知识点）"
        elif not books:
            reason = "当前学科库里没有教材；有教材入库后会提醒补做"
        elif needs:
            reason = "；".join(reasons) + " → 建议补做教材补充"
        elif sup["status"] == "no_match":
            reason = "已补做过：教材里没有与这些知识点足够匹配的章节/小节"
        else:
            reason = "教材补充已是最新"
        return {
            "ok": True, "doc_id": int(doc_id),
            "has_artifact": bool(has_artifact),
            "artifact_mtime": kp_mtime,
            "has_textbook": bool(books),
            "textbooks": textbooks,
            "supplement": sup,
            "needs_backfill": needs,
            "reason": reason,
            "ai": {
                "enabled": _ai_enabled(),
                "model": _kp_model(),
                "thinking": _kp_thinking(),
                "reasoning_effort": _kp_effort(),
                "map": {
                    "exists": bool(chapter_map),
                    "source": str(chapter_map.get("source") or
                                  meta.get("ai_map_source") or ""),
                    "chapter_no": int(chapter_map.get("chapter_no") or 0),
                    "chapter_name": str(chapter_map.get("chapter_name") or ""),
                    "confidence": str(chapter_map.get("confidence") or ""),
                    "cost_cny": float(meta.get("ai_map_cost_cny") or 0.0),
                    "cached_at": str(chapter_map.get("cached_at") or ""),
                },
                "n_ai_ready": n_ai_ready,
                "n_ai_missing": n_ai_missing,
                "ai_kp_calls": int(meta.get("ai_kp_calls") or 0),
                "ai_kp_cost_cny": round(float(meta.get("ai_kp_cost_cny") or 0.0), 6),
            },
        }
    finally:
        if own and conn is not None:
            conn.close()


def supplement_for_kp(doc_id: int, kp_id, *, run_context=None) -> dict:
    """读某条 KP 的教材补充（**不自动生成**；前端侧栏用）。

    返回 `{ok, available, textbook, entry, ai, estimate, legacy, needs_rebuild, note}`：
    `entry` 是本地兜底（chapter/sections/pages/passages），`ai` 是按需 AI 结果（可能有），
    `estimate` 是"点一下 AI 补充要花多少"的预估（0 调用、0 写入）。
    """
    out = supplement_out_file(doc_id)
    data = _load_supplement(out)
    if not data:
        return {"ok": True, "available": False, "textbook": {}, "entry": {},
                "ai": {}, "legacy": False, "needs_rebuild": True,
                "estimate": estimate_kp_supplement(doc_id, kp_id),
                "note": "还没有教材补充产物：先整理知识点，或在课件栏点「补做教材补充」"}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    version = str(meta.get("version") or "")
    chapter_map = meta.get("chapter_map") if isinstance(meta.get("chapter_map"), dict) else {}
    legacy = bool(version != SUPPLEMENT_VERSION
                  or (str(meta.get("status") or "") not in ("no_textbook",)
                      and not chapter_map))
    textbook = {"doc_id": int(meta.get("textbook_doc_id") or 0),
                "title": str(meta.get("textbook_title") or "")}
    kps = data.get("kps") if isinstance(data.get("kps"), dict) else {}
    entry = kps.get(str(kp_id) if kp_id is not None else "") or {}
    ai = entry.get("ai") if isinstance(entry.get("ai"), dict) else {}
    available = bool(entry) and str(entry.get("status") or "") == "done"
    if not entry:
        note = "这条知识点在教材补充产物里没有记录（产物可能生成于它之前）"
    elif legacy:
        note = "这是旧版（C-0124 纯本地）教材补充，建议先点「重新 AI 定位/补做」再看"
    elif ai:
        note = str(ai.get("summary") or ai.get("error") or entry.get("note") or "")
    else:
        note = str(entry.get("note") or "还没做过 AI 补充分析（点按钮按需生成）")
    try:
        estimate = estimate_kp_supplement(doc_id, kp_id)
    except Exception as e:  # noqa: BLE001
        estimate = {"ok": False, "calls": 1,
                    "error": "%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "available": available, "textbook": textbook,
            "entry": entry, "ai": ai, "legacy": bool(legacy),
            "needs_rebuild": bool(legacy),
            "estimate": estimate, "note": note}
