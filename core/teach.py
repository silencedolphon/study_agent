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

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import config

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
SUPPLEMENT_VERSION = "C-0124-teach-supplement-v1"
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


def _chapter_candidates(conn: sqlite3.Connection, tb: dict) -> list:
    """教材的**章候选**：`SELECT heading_path, COUNT(*) GROUP BY heading_path`。

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
        ref = str(r["source_ref"] or "")
        pgs = [int(x) for x in re.findall(r"p\.(\d+)", ref)] or []
        lo = min(pgs) if pgs else 0
        hi = max(pgs) if len(pgs) > 1 else lo
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


def build_supplement(doc_id: int, *, out_path=None, force: bool = False,
                     db_path=None, conn: sqlite3.Connection | None = None,
                     log=print) -> dict:
    """给一份**课件的 KP 产物**生成 `教材补充.json`（零模型、只读 DB、原子写）。

    流程（对应用户需求 1/2/3）：

    1. 读 `kp_build.out_file(doc_id)` 的知识点产物；没有 → `no_kps`（不写文件）；
    2. `pick_textbook(conn)`：**没有教材** → 写 `status="no_textbook"` 状态文件
       （meta + 空 `kps`），供"教材入库后提醒补做"判断；
    3. 有教材：`_chapter_candidates`（只读 heading_path 分组）→ 逐条 KP
       `_match_chapter_for_kp`（只挑**一章**，0 分即 no_match）→
       `_chapter_passages`（**只查所选章**）→ 写每条的 chapter/pages/passages/
       added_terms；**不修改原知识点产物**（只读它、记 sha/mtime 进 meta）；
    4. 幂等：非 force 且已有产物、版本/教材 id/KP 产物 mtime 都没变 → `skipped`；
    5. 原子写；不写数据库、不调模型。

    返回 `{ok, status, out_path, meta, kps?}`；`no_kps` 时 `ok=False`。
    """
    own = conn is None
    conn = conn or _open_db(db_path)
    try:
        from . import kp_build as _kp
        kp_path = _kp.out_file(doc_id)
        kp_doc = _kp._load_prev(kp_path)
        kps_raw = kp_doc.get("knowledge_points") if isinstance(kp_doc, dict) else None
        if not isinstance(kps_raw, list) or not kps_raw:
            return {"ok": False, "status": "no_kps", "doc_id": int(doc_id),
                    "out_path": str(out_path or supplement_out_file(doc_id)),
                    "error": "这份课件还没有末级知识点产物（先整理知识点再补教材）"}
        kp_mtime = kp_path.stat().st_mtime if kp_path.exists() else 0.0
        kp_sha = ""
        try:
            kp_sha = hashlib.sha256(kp_path.read_bytes()).hexdigest()
        except OSError:
            kp_sha = ""

        out = Path(out_path) if out_path else supplement_out_file(doc_id)
        doc_title = _doc_title(conn, doc_id)
        tb = pick_textbook(conn)

        # ---- 幂等短路（非 force）----
        if out.exists() and not force:
            prev = _kp._load_prev(out)
            pm = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
            same = (
                str(pm.get("version") or "") == SUPPLEMENT_VERSION
                and int(pm.get("textbook_doc_id") or 0) == int(tb["id"] if tb else 0)
                and abs(float(pm.get("kp_artifact_mtime") or 0.0) - float(kp_mtime)) < 1e-6
            )
            if same:
                return {"ok": True, "status": "skipped", "out_path": str(out),
                        "meta": pm, "doc_id": int(doc_id)}
            prev = {}

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
            "generated_at": generated_at,
            "created_at": time.time(),
            "n_kps": len(kps_raw),
            "n_matched": 0,
            "n_no_match": 0,
            "note": "",
        }
        kps_map: dict = {}

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

        chapters = _chapter_candidates(conn, tb)
        meta["textbook_chapters"] = len(chapters)
        matched, no_match = 0, 0
        for i, kp in enumerate([r for r in kps_raw if isinstance(r, dict)], 1):
            kp_id = str(kp.get("id") or ("KP-%d" % i))
            kp_name = str(kp.get("name") or kp.get("node_name") or "")
            kp_text = _kp_query_text(kp)
            entry = {"kp_id": kp_id, "kp_name": kp_name, "status": "no_match",
                     "chapter": {}, "pages": [], "page_label": "",
                     "passages": [], "added_terms": [],
                     "note": ""}
            chapter = _match_chapter_for_kp(chapters, kp, doc_title)
            if chapter is None:
                entry["note"] = ("找不到对应章（词项在章名上 0 分）——按规则不回退整本扫描，"
                                 "这条先不给教材补充")
                no_match += 1
                kps_map[kp_id] = entry
                continue
            terms = _terms(kp_text, limit=SUPPLEMENT_TERMS_MAX)
            passages = _chapter_passages(conn, tb, chapter["heading_path"], terms,
                                         limit=SUPPLEMENT_MAX_PASSAGES)
            if not passages:
                entry["chapter"] = {"heading_path": chapter["heading_path"],
                                    "name": chapter["name"], "no": int(chapter["no"]),
                                    "score": chapter.get("score"),
                                    "hit_terms": chapter.get("hit_terms") or []}
                entry["note"] = "已匹配到章，但本章内没有词项得分 ≥ 相对截断线的段落"
                no_match += 1
                kps_map[kp_id] = entry
                continue
            pages: list = []
            for p in passages:
                for pg in range(int(p.get("page_start") or 0),
                                int(p.get("page_end") or 0) + 1):
                    if pg and pg not in pages:
                        pages.append(pg)
            pages = sorted(pages)[:SUPPLEMENT_MAX_PAGES]
            entry.update({
                "status": "done",
                "chapter": {"heading_path": chapter["heading_path"],
                            "name": chapter["name"], "no": int(chapter["no"]),
                            "score": chapter.get("score"),
                            "hit_terms": chapter.get("hit_terms") or []},
                "pages": pages,
                "page_label": _fmt_pages(pages),
                "passages": passages,
                "added_terms": _supplement_added_terms(passages, kp_text),
                "note": ("教材「%s」%s 命中 %d 段（%s）。教材按章切块，"
                         "段落页码范围见 page_label；只做定位，不改原知识点。"
                         % (tb.get("title") or "", chapter["name"],
                            len(passages), _fmt_pages(pages))),
            })
            matched += 1
            kps_map[kp_id] = entry

        meta["n_matched"] = matched
        meta["n_no_match"] = no_match
        meta["status"] = "done" if matched else "no_match"
        meta["note"] = ("教材「%s」：%d 条 KP 中 %d 条匹配到章/段落，%d 条无匹配"
                        % (tb.get("title") or "", len(kps_map), matched, no_match))
        _atomic_write_json({"meta": meta, "kps": kps_map}, out)
        if log:
            log("教材补充：doc#%d · 教材「%s」· %d 条 KP → 匹配 %d / 无匹配 %d"
                "（%d 章候选，0 次模型调用）"
                % (int(doc_id), tb.get("title") or "", len(kps_map), matched,
                   no_match, len(chapters)))
        return {"ok": True, "status": meta["status"], "out_path": str(out),
                "meta": meta, "doc_id": int(doc_id), "kps": kps_map}
    finally:
        if own and conn is not None:
            conn.close()


def supplement_status(doc_id: int, *, db_path=None,
                      conn: sqlite3.Connection | None = None) -> dict:
    """这份课件的「教材补充」状态（零模型、零写入；供课件栏提醒用）。

    返回 `{ok, doc_id, has_artifact, artifact_mtime, has_textbook,
    textbooks:[{doc_id,title,...}], supplement:{exists,status,generated_at,n_kps,
    n_matched,textbook_doc_id}, needs_backfill, reason}`。

    `needs_backfill`：有 KP 产物 + 有教材 +（无补充产物 / 产物是 no_textbook /
    比 KP 产物旧 / 教材换了一本）→ True。`no_match` 不算 needs（已补过，只是没命中）。
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
        if exists:
            data = _kp._load_prev(out)
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        sup = {
            "exists": bool(exists),
            "status": str(meta.get("status") or ""),
            "generated_at": str(meta.get("generated_at") or ""),
            "n_kps": int(meta.get("n_kps") or 0),
            "n_matched": int(meta.get("n_matched") or 0),
            "textbook_doc_id": int(meta.get("textbook_doc_id") or 0),
            "version": str(meta.get("version") or ""),
        }
        reasons: list = []
        if has_artifact and books:
            if not exists:
                reasons.append("还没有教材补充产物")
            elif sup["status"] == "no_textbook":
                reasons.append("上次补做时还没有教材")
            elif sup["status"] == "failed":
                reasons.append("上次补做失败")
            elif sup["textbook_doc_id"] and int(sup["textbook_doc_id"]) != cur_tb_id:
                reasons.append("教材已经换成 doc#%d" % cur_tb_id)
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
            reason = "已补做过：教材里没有与这些知识点足够匹配的章节/段落"
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
        }
    finally:
        if own and conn is not None:
            conn.close()


def supplement_for_kp(doc_id: int, kp_id) -> dict:
    """读某条 KP 的教材补充（**不自动生成**；前端侧栏用）。

    返回 `{ok, available, textbook:{doc_id,title}, entry, note}`；
    没有产物/没有这条 KP 时 `available=False` 并给一句人话 note。
    """
    out = supplement_out_file(doc_id)
    data = _load_supplement(out)
    if not data:
        return {"ok": True, "available": False,
                "textbook": {}, "entry": {},
                "note": "还没有教材补充产物：先整理知识点，或在课件栏点「补做教材补充」"}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    textbook = {"doc_id": int(meta.get("textbook_doc_id") or 0),
                "title": str(meta.get("textbook_title") or "")}
    kps = data.get("kps") if isinstance(data.get("kps"), dict) else {}
    entry = kps.get(str(kp_id) if kp_id is not None else "") or {}
    available = bool(entry) and str(entry.get("status") or "") == "done"
    if not entry:
        note = "这条知识点在教材补充产物里没有记录（产物可能生成于它之前）"
    elif not available:
        note = str(entry.get("note") or "教材里没有找到可补充的匹配段落")
    else:
        note = str(entry.get("note") or "")
    return {"ok": True, "available": available, "textbook": textbook,
            "entry": entry, "note": note}


def _load_supplement(out: Path) -> dict:
    try:
        raw = json.loads(Path(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}
