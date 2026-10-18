# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
# File-ID: SA-92-1a7c40e6   (project study_agent; keep this line)
"""预习模块 ②：预习管线（**独立管线**，卡 C-0094）。

人的原话（2026-10-14）：

> 「增加一个预习模块，选择章节后将那一章节拆成更加细的知识点（根据教材的小标题来拆），
>   之后再**开一个新的大模型**对该章节做**预习总结**，大模型依旧 deepseek flash，
>   **思考强度开 high**。……考虑一下如何缩减成本和压缩时间，但是原本的知识点总结质量
>   不能下降。该功能**默认关闭**，有选项可以打开。该功能加载后，导出的包也要有以上产物。」

大管家定下的口径（本文件逐条落地）：

1. **粒度**：整章做 **1 次**总结调用（doc#91 共 42 章 ⇒ 约 42 次调用）；
2. **独立管线分区**：模型 `deepseek-flash`、`thinking=True`、`reasoning_effort="high"`
   （注意是 high，**不是**知识点那条管线的 max）；模型/密钥/强度都能在设置中心改；
3. **节级细拆用纯正则**（卡 C-0092 的《章节细目》产物），**不叠模型调用**；
   OCR 噪声与抽不出节的章如实统计（见产物 `meta.sections_regex`）；
4. **默认关闭**：`PREVIEW_ENABLED` 默认 0，设置中心可打开；关着时启动入口拒绝并给可操作提示；
5. 成本优化**只在这条新管线上**做；`core/kp_build.py::build_v2` **一字未改**
   ——这就是"原本的知识点总结质量不能下降"的硬保证。

设计要点（为什么这样切）：

- **只读**：本模块只 `SELECT` `chunks`（含 `heading_path`，章级筛选靠它）与 `documents.title`，
  **不写任何数据库表**（与 `build_terms` 的只读风格一致）；产物只有 `logs/knowledge/<doc_id>-预习总结.json`；
- **章级 ch**：`_read_chapter_ch()` 复刻 `kp_build.read_text_pages()` 的返回字典
  （`blocks`/`cover`/`figrows`/`pages_all`/`max_page`/`n_blocks`），只是页集合换成该章——
  于是 `kp_build.render_kp_material()` 这一套既有基建可以直接吃它，材料口径与知识点管线逐字同源；
- **记账口径不破**：`_preview_call()` 产出的逐次调用记录与 `kp_build._v2_call()` **字段同构**
  （run/kind/attempt/note/model/max_tokens/thinking/reasoning_effort/chars_in/at/ok/
  elapsed_s/tokens_in/tokens_out/reasoning_tokens/cost_cny/raw_chars/error），
  并且同样走**进程级模型闸门** `kp_build.model_slot()`、同样把记录 append 进 `meta.calls`，
  汇总复用 `kp_build._v2_cost()` / `_v2_runs()`。
  ⚠ 为什么**不直接调** `_v2_call()`：它把模型名硬编码成模块全局 `kp_build.MODEL`
  （`rec["model"] = MODEL` 与 `chat_once(model=MODEL)` 两处），而预习管线必须用自己的
  模型键（设置中心的「预习 · 模型名」）。要让它跟随就得改 `core/kp_build.py` 的共享代码
  ——而红线要求那条管线**一字不改**。所以这里用"字段同构的独立实现"换"零改动"，
  并由 `tools/verify_c0094_preview.py` 用同一份假 `chat_once` 实测两者的记录字段集完全一致。
- **章级一次调用**：渲染整章材料 → 1 次调用 → 校验 → 原子落盘。中途不切段、不并发，
  所以 doc#91 全 42 章 ≈ 42 次调用（对照全份知识点管线的数百次）。
- **省钱的第二道闸**：产物已存在、版本/章号/材料 sha256 都没变且 `status=done` 时，
  `force=False` 直接 `skipped`（0 次调用、0 花费）——重复点「预习」不再花钱。

产物路径：`<当前学科 logs>/knowledge/<doc_id>-预习总结.json`
（后缀 `-预习总结.json` **既不与** `-末级知识点总结.json` **也不与** `-名词解释.json` 相撞；
顶层**没有** `knowledge_points` 键 —— `tools/dashboard.py::_kp_load_dir()` 只认那个键，
所以这份产物永远不会被扫进知识点索引，也不会出现在知识点页签的条数里）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from . import config
from . import kp_build

# ---------------------------------------------------------------- 常量
# 产物版本：**故意叫 preview_version 而不是 pipeline_version**。
#   理由：`tools/dashboard.py::_artifact_load()` 的版本白名单
#   （`_KP_READABLE_VERSIONS`，`dashboard.py:1544-1549`）是给「末级知识点总结」那套产物
#   用的；预习产物不是知识点产物，混进那个版本号体系只会让"哪个白名单管哪个文件"
#   变得含糊。这里用独立字段，看板/CLI 判"是不是本版产物"一律读它。
PREVIEW_VERSION = "C-0094-preview-v1"
PREVIEW_SUFFIX = "-预习总结.json"
PREVIEW_PROMPT_NAME = "提示词-预习总结.md"
PREVIEW_STAGES = ("读取章节", "渲染材料", "预习总结", "写入产物")

# 整章材料的硬上限（字符）：超过就先丢图片理解块，再超才按页截断——
# 两种降级都**如实写进产物 meta.material_降级字段**，绝不静默丢内容。
MATERIAL_CHARS_MAX = 180000

# 章级筛选：`chunks.heading_path` 形如 `药理学 第6版 刘晓东 > 第2章 药代动力学`。
_CH_RE = re.compile(r">\s*第\s*(\d+)\s*章\s*([^>]*)")
_CH_REF_RE = re.compile(r"^第\s*(\d+)\s*章\s*(.*)$")

# 页文本截断（与名词解释同口径：章级材料要看得见定义句，不能像结构生成那样只截 420 字）
PAGE_CHARS = 4000


# ---------------------------------------------------------------- 配置读取
def _cfg_str(key: str, default: str) -> str:
    try:
        v = str(config.value_of(key, default) or "").strip()
    except Exception:  # noqa: BLE001
        v = ""
    return v or str(default)


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(config.value_of(key, default))
    except Exception:  # noqa: BLE001
        return int(default)


def model_name() -> str:
    """这条管线自己的模型名（设置中心「AI 预习总结 · 模型名」，默认 deepseek-flash）。"""
    return _cfg_str("PREVIEW_MODEL", "deepseek-flash")


def thinking_on() -> bool:
    return bool(_cfg_int("PREVIEW_THINKING", 1))


def effort() -> str:
    """推理强度：默认 **high**（人拍板要求；不是知识点那条的 max）。"""
    return _cfg_str("PREVIEW_REASONING_EFFORT", "high")


def max_tokens() -> int:
    return max(1024, _cfg_int("PREVIEW_MAX_TOKENS", 16384))


def enabled() -> bool:
    """★ 默认**关闭**（`_FIELDS` 里 `PREVIEW_ENABLED` 的 default 是 0）。"""
    return bool(_cfg_int("PREVIEW_ENABLED", 0))


def enabled_state() -> dict:
    """关着时给入口一句**可操作**的提示（不是"失败"，是"还没打开"）。"""
    on = enabled()
    return {
        "enabled": on,
        "hint": ("预习功能已开启。" if on else
                 "预习功能**默认关闭**：请到「设置中心 → AI 预习总结」把「开启预习功能」"
                 "打开（写入本机 config.local.json 的 PREVIEW_ENABLED=1）后再来。"
                 "这条开关默认关着，是为了不让它在没人要的时候花掉调用与时间。"),
        "switch": "PREVIEW_ENABLED",
        "pipeline": "preview",
    }


# ---------------------------------------------------------------- 路径
def preview_out_file(doc_id: int, out_path: str | os.PathLike | None = None) -> Path:
    """产物路径：`<当前学科 logs>/knowledge/<doc_id>-预习总结.json`。"""
    if out_path:
        return Path(out_path)
    return config.LOG_DIR / "knowledge" / ("%d%s" % (int(doc_id), PREVIEW_SUFFIX))


def prompt_file(path: str | os.PathLike | None = None) -> Path:
    """提示词真源：显式给定 > 本学科 logs/knowledge/ > 项目根 > 随包资源目录。

    与 `kp_build._prompt_path_v2()` 同一套"本学科优先、根目录兜底"的规矩
    （提示词是**管线资产**不是学科数据；新学科一出生自己要有一份就能覆盖）。
    """
    if path:
        return Path(path)
    name = PREVIEW_PROMPT_NAME
    own = config.LOG_DIR / "knowledge" / name
    if own.exists():
        return own
    root = config.ROOT / "logs" / "knowledge" / name
    if root.exists():
        return root
    return config.BUNDLE_ROOT / "logs" / "knowledge" / name


def sections_out_file(doc_id: int) -> Path:
    """卡 C-0092 的《章节细目》（纯正则产物，零模型成本）——节级细拆就吃它。"""
    return config.LOG_DIR / "knowledge" / ("%d-章节细目.json" % int(doc_id))


def load_preview_prompts(path: str | os.PathLike | None = None) -> dict:
    """预习提示词的两个块（SYS/USER）+ 真源 sha256（记进产物 meta）。"""
    p = prompt_file(path)
    blob = p.read_bytes()
    md = blob.decode("utf-8")
    sys_p = kp_build._grab(md, "PROMPT-PREVIEW-SYS", p)
    user_p = kp_build._grab(md, "PROMPT-PREVIEW-USER", p)
    return {"sys": sys_p, "user": user_p, "sha256": hashlib.sha256(blob).hexdigest(),
            "path": str(p), "chars": len(sys_p) + len(user_p)}


# ---------------------------------------------------------------- 章（零模型成本）
def _norm_chapter_arg(chapter) -> dict:
    """把 `chapter` 归一成 `{"no": int|0, "name": str}`。

    认这几种写法：`2`（章号）/ `"2"` / `"第2章"` / `"第2章 药代动力学"` / `"药代动力学"`（章名）。
    """
    if isinstance(chapter, bool):
        chapter = int(chapter)
    if isinstance(chapter, int):
        return {"no": int(chapter), "name": ""}
    s = str(chapter or "").strip()
    m = _CH_REF_RE.match(s)
    if m:
        return {"no": int(m.group(1)), "name": m.group(2).strip()}
    if s.isdigit():
        return {"no": int(s), "name": ""}
    return {"no": 0, "name": s}


def list_chapters(doc_id: int, *, db_path: str | os.PathLike | None = None) -> dict:
    """列这份教材的章（**零模型成本**）：`chunks.heading_path` 里的 `> 第N章 …` 归章。

    返回 `{"ok", "doc_id", "title", "chapters":[{no,name,heading_path,n_blocks,
    page_start,page_end,pages,n_sections}], "sections_artifact": {...}}`。
    `n_sections` 来自卡 C-0092 的《章节细目》（读不到就为 0 并如实标注）。
    """
    db = Path(db_path) if db_path else Path(config.DB_PATH)
    conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, ord, source_ref, heading_path FROM chunks "
                            "WHERE doc_id=? ORDER BY ord, id", (int(doc_id),)).fetchall()
    finally:
        conn.close()
    if not rows:
        return {"ok": False, "doc_id": int(doc_id),
                "error": "这份资料在库里没有任何块（chunks 为空），列不出章节"}

    agg: dict = {}
    book = ""
    for cid, _od, ref, hp in rows:
        hp = str(hp or "")
        m = _CH_RE.search(hp)
        if not book and ">" in hp:
            book = hp.split(">")[0].strip()
        parsed = kp_build._parse_ref(str(ref or ""))
        if parsed is None:
            continue
        a, b, _fig = parsed
        if not m:
            continue
        no = int(m.group(1))
        d = agg.setdefault(no, {"no": no, "name": str(m.group(2) or "").strip(),
                                "heading_path": hp.strip(),
                                "n_blocks": 0, "pages": set(), "block_ids": []})
        d["n_blocks"] += 1
        d["block_ids"].append(int(cid))
        d["pages"].update(range(a, b + 1))

    detail = _load_sections_artifact(doc_id)
    by_no: dict = {}
    for c in (detail.get("chapters") or []):
        try:
            by_no[int(c.get("no"))] = c
        except (TypeError, ValueError):
            continue
    chapters = []
    for no in sorted(agg):
        d = agg[no]
        ps = sorted(d["pages"])
        art = by_no.get(no) or {}
        chapters.append({
            "no": no, "name": d["name"], "heading_path": d["heading_path"],
            "n_blocks": int(d["n_blocks"]),
            "page_start": (ps[0] if ps else 0), "page_end": (ps[-1] if ps else 0),
            "pages": ps,
            "n_sections": len([s for s in (art.get("sections") or [])
                               if isinstance(s, dict)]),
            "sections_fallback": bool(art.get("fallback")),
        })
    return {
        "ok": True, "doc_id": int(doc_id), "title": kp_build.doc_title_v2(doc_id, db_path),
        "book": book, "n_chapters": len(chapters), "chapters": chapters,
        "chapter_source": "chunks.heading_path",
        "sections_artifact": {
            "read": bool(detail.get("ok")), "path": str(sections_out_file(doc_id)),
            "version": str((detail.get("meta") or {}).get("version") or ""),
            "note": detail.get("note") or "",
        },
    }


def _load_sections_artifact(doc_id: int) -> dict:
    """读卡 C-0092 的《章节细目》（**只读、零模型调用**；读不到不报错）。"""
    p = sections_out_file(doc_id)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "chapters": [], "meta": {},
                "note": "没有可用的《章节细目》产物（%s）——节级细拆降级为空，"
                        "章级总结照跑；要补节级细目请先跑卡 C-0092 的切分器。"
                        % type(e).__name__}
    if not isinstance(raw, dict) or not isinstance(raw.get("chapters"), list):
        return {"ok": False, "chapters": [], "meta": {},
                "note": "《章节细目》产物结构不认识（缺 chapters 列表）——按降级处理"}
    return {"ok": True, "chapters": raw["chapters"],
            "meta": raw.get("meta") if isinstance(raw.get("meta"), dict) else {},
            "detection": raw.get("detection") if isinstance(raw.get("detection"), dict) else {},
            "note": "节级细拆来自该正则产物（零模型成本）"}


def locate_chapter(doc_id: int, chapter, *,
                   db_path: str | os.PathLike | None = None) -> dict:
    """定位一章：先按 `heading_path`（入库结构），找不到再退到《章节细目》的页范围。

    返回 `{"ok", "no", "name", "heading_path", "block_ids", "pages", "page_start",
    "page_end", "source"}`。两种来路都找不到就 `ok=False` + 可操作提示。
    """
    want = _norm_chapter_arg(chapter)
    listed = list_chapters(doc_id, db_path=db_path)
    if not listed.get("ok"):
        return {"ok": False, "error": listed.get("error") or "列不出章节",
                "doc_id": int(doc_id)}

    hit = None
    for c in listed["chapters"]:
        if want["no"] and int(c["no"]) == int(want["no"]):
            hit = c
            break
    if hit is None and want["name"]:
        nm = want["name"]
        for c in listed["chapters"]:
            if c["name"] == nm or c["name"].startswith(nm) or nm.startswith(c["name"]):
                hit = c
                break
    if hit is not None:
        return {"ok": True, "doc_id": int(doc_id), "no": int(hit["no"]),
                "name": hit["name"], "heading_path": hit["heading_path"],
                "block_ids": list(agg_block_ids(doc_id, int(hit["no"]), db_path=db_path)),
                "pages": list(hit["pages"]), "page_start": hit["page_start"],
                "page_end": hit["page_end"], "source": "chunks.heading_path"}

    # 降级：用《章节细目》的页范围按页筛块（heading_path 认不出章时才走这条）
    detail = _load_sections_artifact(doc_id)
    art = None
    for c in (detail.get("chapters") or []):
        try:
            if want["no"] and int(c.get("no")) == int(want["no"]):
                art = c
                break
        except (TypeError, ValueError):
            continue
    if art is None and want["name"]:
        for c in (detail.get("chapters") or []):
            if str(c.get("name") or "").strip() == want["name"]:
                art = c
                break
    if art is None:
        return {"ok": False, "doc_id": int(doc_id),
                "error": ("这份资料里找不到这一章（%s）。可用的章：%s"
                          % (chapter, "、".join("第%d章 %s" % (c["no"], c["name"])
                                                for c in listed["chapters"][:8]) or "（没有）"))}
    a = int(art.get("page_start") or 0)
    b = int(art.get("page_end") or 0)
    db = Path(db_path) if db_path else Path(config.DB_PATH)
    conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    try:
        ids = []
        for cid, ref in conn.execute("SELECT id, source_ref FROM chunks WHERE doc_id=?",
                                     (int(doc_id),)):
            parsed = kp_build._parse_ref(str(ref or ""))
            if parsed and parsed[0] <= b and parsed[1] >= a:
                ids.append(int(cid))
    finally:
        conn.close()
    return {"ok": True, "doc_id": int(doc_id), "no": int(art.get("no") or want["no"] or 0),
            "name": str(art.get("name") or want["name"] or ""),
            "heading_path": "%s > 第%s章 %s" % (listed.get("book") or "",
                                                art.get("no"), art.get("name") or ""),
            "block_ids": ids, "pages": list(range(a, b + 1)),
            "page_start": a, "page_end": b,
            "source": "章节细目.page_range",
            "fallback_note": "heading_path 里认不出这一章，退用《章节细目》的页范围筛块"}


def agg_block_ids(doc_id: int, chapter_no: int, *,
                  db_path: str | os.PathLike | None = None) -> list:
    """该章的块 id（`heading_path` 章级筛选，**零模型成本**）。"""
    db = Path(db_path) if db_path else Path(config.DB_PATH)
    conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    try:
        out = []
        for cid, hp in conn.execute("SELECT id, heading_path FROM chunks "
                                    "WHERE doc_id=? ORDER BY ord, id", (int(doc_id),)):
            m = _CH_RE.search(str(hp or ""))
            if m and int(m.group(1)) == int(chapter_no):
                out.append(int(cid))
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------- 章级 ch
def read_chapter_ch(doc_id: int, chapter, *,
                    db_path: str | os.PathLike | None = None) -> tuple:
    """构造**章级 ch**并渲染整章材料。

    返回 `(ch, info, material, inv, render_stats)`：

    - `ch` 的键与 `kp_build.read_text_pages()` **完全一致**
      （`blocks`/`cover`/`figrows`/`pages_all`/`max_page`/`n_blocks`），只是页集合换成该章
      ——于是 `kp_build.render_kp_material()` 可以直接吃它（材料口径与知识点管线同源）；
    - `info` 是 `locate_chapter()` 的结果；
    - `material` 是送进模型的整章材料；`inv` 是材料清单（→ 产物 `sources`）；
    - `render_stats` 如实记材料降级（丢图注 / 截断页）。
    """
    full = kp_build.read_text_pages(doc_id, db_path)
    info = locate_chapter(doc_id, chapter, db_path=db_path)
    if not info.get("ok"):
        return {}, info, "", [], {"ok": False}
    ids = set(int(x) for x in (info.get("block_ids") or []))
    by_id: dict = {}
    for _page, bs in full["blocks"].items():
        for b in bs:
            by_id[int(b["chunk_id"])] = b
    sel = [by_id[c] for c in ids if c in by_id]
    if not sel:
        info = dict(info, ok=False,
                    error="这一章在库里没有任何块（章级筛选命中 0 块），没有材料可总结")
        return {}, info, "", [], {"ok": False}

    blocks: dict = {}
    for b in sel:
        blocks.setdefault(int(b["page"]), []).append(b)
    for p in blocks:
        blocks[p].sort(key=lambda x: (x["ord"], x["chunk_id"]))
    cover: dict = {}
    for _p, bs in blocks.items():
        for b in bs:
            for pg in b["pages"]:
                cover.setdefault(int(pg), []).append(b["chunk_id"])
    pages = sorted(cover)
    ch = {"blocks": blocks, "cover": cover,
          "figrows": {p: v for p, v in full["figrows"].items() if p in set(pages)},
          "max_page": (pages[-1] if pages else 0), "n_blocks": len(sel),
          "pages_all": list(range(pages[0], pages[-1] + 1)) if pages else []}
    info = dict(info, pages=pages, page_start=(pages[0] if pages else 0),
                page_end=(pages[-1] if pages else 0))

    material, inv, stats = render_material(ch, info)
    return ch, info, material, inv, stats


def render_material(ch: dict, info: dict) -> tuple:
    """整章材料（复用 `kp_build.render_kp_material()` 的渲染口径）+ 两级降级。

    降级顺序（**每一步都如实记进 `render_stats`**）：
      1. 原样（含图片理解块）；
      2. 超 `MATERIAL_CHARS_MAX` → 丢掉图片理解块重渲染；
      3. 仍超 → 按页截断，记下被截断的页。
    """
    node_path = ["第%d章 %s" % (int(info.get("no") or 0), info.get("name") or "")]
    text, inv = kp_build.render_kp_material(list(ch["pages_all"]), node_path, ch)
    stats = {"ok": True, "mode": "full", "chars": len(text), "n_sources": len(inv),
             "dropped_fig_blocks": 0, "truncated_pages": []}
    if len(text) <= MATERIAL_CHARS_MAX:
        return text, inv, stats

    # 降级 2：丢图片理解块
    lean = dict(ch)
    lean["blocks"] = {p: [b for b in bs if b["kind"] != "图片理解"]
                      for p, bs in ch["blocks"].items()}
    text2, inv2 = kp_build.render_kp_material(list(ch["pages_all"]), node_path, lean)
    n_fig = sum(1 for bs in ch["blocks"].values() for b in bs if b["kind"] == "图片理解")
    stats.update({"mode": "drop_figures", "chars": len(text2), "n_sources": len(inv2),
                  "dropped_fig_blocks": n_fig})
    if len(text2) <= MATERIAL_CHARS_MAX:
        return text2, inv2, stats

    # 降级 3：按页截断（保留前面的页，记下丢掉的页）
    keep: list = []
    total = 0
    for p in ch["pages_all"]:
        piece, _inv = kp_build.render_kp_material([p], node_path, lean)
        if total + len(piece) > MATERIAL_CHARS_MAX and keep:
            break
        keep.append(p)
        total += len(piece)
    dropped = [p for p in ch["pages_all"] if p not in set(keep)]
    text3, inv3 = kp_build.render_kp_material(keep, node_path, lean)
    stats.update({"mode": "truncate_pages", "chars": len(text3),
                  "n_sources": len(inv3), "truncated_pages": dropped})
    return text3, inv3, stats


# ---------------------------------------------------------------- 节级细拆（纯正则，零模型）
def chapter_sections(doc_id: int, info: dict) -> dict:
    """本章的节级细目：**直接读卡 C-0092 的《章节细目》**，一个模型调用都不加。

    返回 `{"read", "source", "n_sections", "fallback", "sections":[…], "note"}`。
    每节保留正则产物的原字段（form/level/number/title/raw/page_start/page_end/
    line_no/parent_index/fallback），另补 `page_label` 与 `chapter_no`。
    `block_ord_*` 是 C-0092 自己的口径，这里**原样带着**但不当判据用。
    """
    detail = _load_sections_artifact(doc_id)
    if not detail.get("ok"):
        return {"read": False, "source": str(sections_out_file(doc_id)),
                "n_sections": 0, "fallback": True, "sections": [],
                "note": detail.get("note") or "没有《章节细目》产物"}
    mine = None
    for c in detail["chapters"]:
        try:
            if int(c.get("no")) == int(info.get("no") or 0):
                mine = c
                break
        except (TypeError, ValueError):
            continue
    if mine is None:
        return {"read": True, "source": str(sections_out_file(doc_id)),
                "n_sections": 0, "fallback": True, "sections": [],
                "note": "《章节细目》里没有第 %s 章（该章抽不出节，如实降级为空）"
                        % info.get("no")}
    secs = []
    for s in (mine.get("sections") or []):
        if not isinstance(s, dict):
            continue
        item = {k: s.get(k) for k in ("index", "form", "level", "number", "title",
                                      "raw", "page_start", "page_end", "line_no",
                                      "parent_index", "fallback")}
        item["chapter_no"] = int(info.get("no") or 0)
        item["page_label"] = kp_build._pages_label(
            list(range(int(s.get("page_start") or 0), int(s.get("page_end") or 0) + 1)))
        secs.append(item)
    return {"read": True, "source": str(sections_out_file(doc_id)),
            "artifact_version": str((detail.get("meta") or {}).get("version") or ""),
            "n_sections": len(secs), "fallback": bool(mine.get("fallback")),
            "sections": secs,
            "note": ("节级细拆来自卡 C-0092 的纯正则产物，**零模型调用**；"
                     "OCR 噪声随该产物的 fallback 标记一并带过来，未做美化")}


def render_sections_outline(sec: dict) -> str:
    """把节级细目渲染成一小段大纲文本（送进**那一次**章级调用，不额外花钱）。

    每节带一个 `[节 N]` 编号，模型在 `key_points[].section` 里回填这个编号 ——
    于是"要点落在哪一节"是模型**顺手**给的（同一次调用，零额外成本），
    比管线事后按页号猜准得多（一章常常只有 4 页却挤着 9 个小标题）。
    模型不填/填错时，管线仍有按页码归位的兜底。
    """
    lines = []
    for s in (sec.get("sections") or []):
        mark = "[节 %s]" % (s.get("index") or 0)
        if int(s.get("level") or 1) == 1:
            lines.append("%s %s%s（p.%s）"
                         % (mark, s.get("form") or "", s.get("title") or "",
                            s.get("page_label") or ""))
        else:
            lines.append("    %s · %s%s（p.%s）%s"
                         % (mark, s.get("form") or "", s.get("title") or "",
                            s.get("page_label") or "",
                            "（上级节 %s）" % (s.get("parent_index") or "")
                            if s.get("parent_index") else ""))
    if not lines:
        return "（这一章没有抽得出的小标题——按整章正文自己归纳节级结构）"
    return "\n".join(lines)


# ---------------------------------------------------------------- 记账（与 _v2_call 同构）
def _preview_call(calls: list, kind: str, sys_p: str, user_p: str, *, max_tokens: int,
                  retry: int, log, note: str = "", temperature: float = kp_build.TEMPERATURE,
                  run_no: int = 1, thinking: bool = True, reasoning_effort: str = "high",
                  model: str = "", timeout: int = kp_build.TIMEOUT) -> dict:
    """调一次模型，**成功与失败都逐次记账**（`calls` 直接进产物 `meta.calls`）。

    ⚠ 字段与 `core/kp_build.py::_v2_call()` **逐字同构**（那份是本项目的记账口径真源）：
    run / kind / attempt / note / model / max_tokens / thinking / reasoning_effort /
    chars_in / at / ok / elapsed_s / tokens_in / tokens_out / reasoning_tokens /
    cost_cny / raw_chars / error（另有并发分配用的 kp_no，这里恒为 1——本管线一次调用）。
    取槽同样走**进程级模型闸门** `kp_build.model_slot()`：预习的调用与知识点/名词的调用
    挤在同一把闸门前排队，"进程内在飞的模型调用数 ≤ 闸门值"这条既有保证不被破坏。

    成本兜底：JSON 解析失败且**输出被 max_tokens 打满**时，下一次尝试把 max_tokens 加倍
    （照 `filter_terms_v2()` 的既有兜底）——思考链把正文挤没是实测踩过的坑。
    """
    from . import chat  # 延迟 import：dry-run 时不需要 key

    mdl = str(model or model_name())
    last, last_idx = "", -1
    cap = max(1024, int(max_tokens))
    for attempt in range(int(retry) + 1):
        t0 = time.perf_counter()
        rec = {"run": int(run_no), "kind": kind, "attempt": attempt + 1, "note": note,
               "model": mdl, "max_tokens": int(cap),
               "thinking": bool(thinking),
               "reasoning_effort": (str(reasoning_effort) if thinking else ""),
               "chars_in": len(sys_p) + len(user_p),
               "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        try:
            with kp_build.model_slot(kind):
                r = chat.chat_once([{"role": "system", "content": sys_p},
                                    {"role": "user", "content": user_p}],
                                   model=mdl, max_tokens=cap, thinking=thinking,
                                   temperature=temperature, timeout=timeout,
                                   reasoning_effort=(reasoning_effort if thinking else None))
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
            rec.update({"ok": False, "error": last,
                        "elapsed_s": round(time.perf_counter() - t0, 1),
                        "tokens_in": 0, "tokens_out": 0, "reasoning_tokens": 0,
                        "cost_cny": 0.0, "raw_chars": 0})
            last_idx = kp_build._append_call(calls, rec, call_no=1)
            log("  ★调用失败（%s 第 %d 次）：%s" % (kind, attempt + 1, last))
            if attempt < retry:
                time.sleep(2 * (attempt + 1))
            continue
        u = r.get("usage") or {}
        usage = {"tokens_in": int(u.get("prompt_tokens") or 0),
                 "tokens_out": int(u.get("completion_tokens") or 0),
                 "total_tokens": int(u.get("total_tokens") or 0),
                 "reasoning_tokens": kp_build._reasoning_tokens(u),
                 "cost_cny": kp_build.cost_of(u)}
        raw = r["content"] or ""
        parsed = kp_build.parse_json(raw)
        err = str(parsed.get("_parse_error") or "")
        rec.update({"ok": not err, "model": r.get("model") or mdl,
                    "elapsed_s": round(time.perf_counter() - t0, 1),
                    "tokens_in": usage["tokens_in"], "tokens_out": usage["tokens_out"],
                    "reasoning_tokens": usage["reasoning_tokens"],
                    "cost_cny": usage["cost_cny"], "raw_chars": len(raw), "error": err})
        last_idx = kp_build._append_call(calls, rec, call_no=1)
        if not err:
            return {"ok": True, "parsed": parsed, "usage": usage, "raw": raw,
                    "model": rec["model"], "error": "", "call_index": last_idx,
                    "call": rec}
        last = "JSON 解析失败：%s" % err
        log("  ★输出解析失败（%s 第 %d 次）：%s" % (kind, attempt + 1, err))
        if usage["tokens_out"] >= cap * 0.98 and cap < 65536:
            cap = min(65536, cap * 2)
            log("    输出被 max_tokens(%d) 打满 → 下一次尝试放宽到 %d"
                % (rec["max_tokens"], cap))
        if attempt < retry:
            time.sleep(1)
    return {"ok": False, "parsed": {}, "usage": {}, "raw": "", "model": mdl,
            "error": last, "call_index": last_idx, "call": None}


# ---------------------------------------------------------------- 产物组装
def _clean_points(raw, pages_all: list, cover_map: dict, mat_text: str,
                  sec_list: list) -> tuple:
    """校验模型给的要点：**页码与数字必须出自本章材料**，否则整条剔除。

    判据（卡 C-0094 实测校准；三条都是"可判的硬事实"，不做风格判断）：

    1. **页码**：`pages` 里出现**本章页范围之外**的页号 ⇒ 整条剔除。
       ⚠ 合法页 = 本章的 `pages_all`（材料里逐页有 `===== 第 N 页 =====` 标题），
       **不是**"有块的页"——跨页块只归档在起始页，后续页在材料里是页标题 + 空内容，
       模型把要点挂到那些页上完全合法。首版拿"材料清单里出现过的页"当判据，
       doc#91 第1章 24 条要点被误剔 22 条（全是 [16]/[17] 这种真实页），实测踩过这个坑。
    2. **数字**：要点里 ≥2 位的数字串必须在本章材料里出现过 ⇒ 否则剔除
       （防"编个年份/剂量"这种最典型的幻觉）；
    3. **弱文本闸**：`point + detail` 与本章材料必须至少有 6 个连续字的公共片段 ⇒
       否则视为串章剔除。**故意不要求更长**：预习总结是概括性写作，
       要求"整句能在原文找到"会把正常概括全杀掉（首版把 `COPY_ABS=40` 的照抄判据
       搬过来，同样误杀）。
    """
    mat = kp_build._norm(mat_text)
    pages_ok = {int(p) for p in (pages_all or [])}
    by_page = {int(p): sorted({int(c) for c in (cover_map.get(int(p)) or [])})
               for p in (pages_all or [])}
    sec_span = [(int(s.get("page_start") or 0), int(s.get("page_end") or 0), s)
                for s in sec_list]
    sec_by_no = {int(s.get("index") or 0): s for s in sec_list}

    def _pages(raw_pages) -> tuple:
        if isinstance(raw_pages, (int, str)):
            raw_pages = [raw_pages]
        ok, bad = [], []
        for x in (raw_pages or []):
            try:
                p = int(str(x).strip())
            except (TypeError, ValueError):
                bad.append(str(x)[:20])
                continue
            (ok if p in pages_ok else bad).append(p)
        return sorted(set(ok)), bad

    def _section_of(pages: list, want) -> dict:
        """归位：**模型自己给的节号优先**（同一次调用里顺手给的，零额外成本），
        没给或给错时按页码兜底——覆盖页最多 > 层级最深 > 页范围最窄。"""
        try:
            n = int(str(want).strip())
        except (TypeError, ValueError):
            n = 0
        if n and n in sec_by_no:
            return sec_by_no[n]
        cands = []
        for a, b, s in sec_span:
            hit = [p for p in pages if a <= p <= b]
            if hit:
                cands.append((len(hit), int(s.get("level") or 1), -(b - a), s))
        if not cands:
            return {}
        cands.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
        return cands[0][3]

    keep, rejected = [], []
    for i, r in enumerate(raw or [], 1):
        if not isinstance(r, dict):
            rejected.append({"index": i, "why": "不是对象：%s" % str(r)[:40]})
            continue
        point = str(r.get("point") or r.get("name") or r.get("title") or "").strip()
        detail = str(r.get("detail") or r.get("desc") or r.get("why") or "").strip()
        if not point:
            rejected.append({"index": i, "why": "没有 point 字段"})
            continue
        pages, bad_pages = _pages(r.get("pages") or r.get("page"))
        if bad_pages:
            rejected.append({"index": i, "point": point[:40],
                             "why": "页码不在本章页范围（p.%s）里：%s"
                                    % (kp_build._pages_label(sorted(pages_ok)), bad_pages[:5])})
            continue
        nums = [n for n in re.findall(r"\d+(?:\.\d+)?", point + detail) if len(n) >= 2]
        bad_nums = [n for n in nums if n not in mat]
        if bad_nums:
            rejected.append({"index": i, "point": point[:40],
                             "why": "数字不在本章材料里：%s" % bad_nums[:5]})
            continue
        blob = kp_build._norm(point + detail)
        if len(blob) >= 12 and mat and kp_build.lcs_len(blob, mat) < 6:
            rejected.append({"index": i, "point": point[:40],
                             "why": "与本章材料没有任何 6 字以上的公共片段（疑似编造/串章）"})
            continue
        ids = []
        for p in pages:
            ids.extend(by_page.get(p) or [])
        hit = _section_of(pages, r.get("section") if r.get("section") is not None
                          else r.get("section_index"))
        keep.append({
            "no": len(keep) + 1, "point": point, "detail": detail,
            "pages": pages, "page_label": kp_build._pages_label(pages),
            "chunk_ids": sorted(set(ids)),
            "material_block_ids": sorted(set(ids)),
            "section_index": int(hit.get("index") or 0),
            "section_title": str(hit.get("title") or ""),
            "section_from_model": bool(str(r.get("section") or "").strip().isdigit()),
            "importance": str(r.get("importance") or "").strip(),
        })
    return keep, rejected


def _clean_terms_list(raw, mat_text: str) -> list:
    """关键名词：同样"必须出自本章材料"，去重后按出现顺序编号。"""
    mat = kp_build._norm(mat_text)
    out, seen = [], set()
    for r in (raw or []):
        if isinstance(r, str):
            r = {"term": r}
        if not isinstance(r, dict):
            continue
        term = str(r.get("term") or r.get("name") or "").strip()
        if not term:
            continue
        key = kp_build._norm_term_key(term)
        if key in seen:
            continue
        if mat and kp_build._norm(term) not in mat:
            continue          # 材料里根本没有这个词 → 不收（不编）
        seen.add(key)
        out.append({"term": term, "en": str(r.get("en") or "").strip(),
                    "def": str(r.get("def") or r.get("定义") or "").strip(),
                    "why": str(r.get("why") or r.get("重要性") or "").strip()})
    return out


def _preview_meta(doc_id: int, info: dict, prompt: dict, est: dict, calls: list,
                  *, started: float, status: str, err: str, inv: list,
                  render_stats: dict, sec: dict, points: list, terms: list,
                  rejected: list, material_sha: str, run_no: int,
                  skipped: bool = False) -> dict:
    """产物 `meta`：必带 doc_id/chapter/heading_path/pages + 调用账 + 材料与降级留痕。"""
    agg = kp_build._v2_cost(calls)
    run_agg = kp_build._v2_cost(calls, run=run_no)
    rt = int(agg.get("reasoning_tokens") or 0)
    to = int(agg.get("tokens_out") or 0)
    secs = sec.get("sections") or []
    with_pts = [s for s in secs if any(int(p.get("section_index") or 0) == int(s.get("index") or 0)
                                       for p in points)]
    return {
        "doc_id": int(doc_id),
        "title": est.get("title") or "",
        "chapter": "第%d章 %s" % (int(info.get("no") or 0), info.get("name") or ""),
        "chapter_no": int(info.get("no") or 0),
        "chapter_name": info.get("name") or "",
        "heading_path": info.get("heading_path") or "",
        "pages": list(info.get("pages") or []),
        "page_label": kp_build._pages_label(info.get("pages") or []),
        "page_start": int(info.get("page_start") or 0),
        "page_end": int(info.get("page_end") or 0),
        "n_blocks": int(est.get("n_blocks") or 0),
        "chapter_source": info.get("source") or "",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_card": "C-0094",
        "preview_version": PREVIEW_VERSION,
        "pipeline": ("C-0094 v1 预习总结（**独立管线分区**，core/preview.py::build_preview）："
                     "按章取块（chunks.heading_path 章级筛选，零模型成本）→ 整章渲染成"
                     "一份材料 → **整章只做 1 次**总结调用（%s / thinking=%s / "
                     "reasoning_effort=%s）→ 节级细拆直接读卡 C-0092 的《章节细目》"
                     "（纯正则，**不叠模型调用**）→ 原子落盘。"
                     "与知识点总结管线（core/kp_build.py::build_v2）**完全独立**："
                     "本管线不改那一条的任何语义。"
                     % (model_name(), thinking_on(), effort())),
        "inputs": {"read": ["chunks.id", "chunks.ord", "chunks.source_ref", "chunks.text",
                            "chunks.heading_path", "figure_desc.no_info",
                            "documents.title"],
                   "not_read": ["logs/outline/<doc_id>.json"],
                   "db_writes": [],
                   "sections_artifact": str(sections_out_file(doc_id)),
                   "material_sha256": material_sha},
        "prompt_path": prompt.get("path") or "",
        "prompt_sha256": prompt.get("sha256") or "",
        "prompts": {"chars": prompt.get("chars") or 0,
                    "blocks": {"sys": len(prompt.get("sys") or ""),
                               "user": len(prompt.get("user") or "")}},
        "model": model_name(),
        "params": {"thinking": thinking_on(), "reasoning_effort": effort(),
                   "temperature": kp_build.TEMPERATURE,
                   "temperature_note": "思考模式下 temperature 不生效（官方行为），"
                                       "保留仅为不动调用签名",
                   "max_tokens": max_tokens(),
                   "calls_per_chapter": 1,
                   "material_chars_max": MATERIAL_CHARS_MAX},
        "run_no": int(run_no),
        "calls": calls,
        "runs": kp_build._v2_runs(calls),
        "cost_cny": run_agg["cost_cny"],
        "cost_cny_all_calls": agg["cost_cny"],
        "cost_rule": ("cost_cny = **本次运行**（第 %d 轮）的 (tokens_in/1e6*%.2f + "
                      "tokens_out/1e6*%.2f) * %.1f；cost_cny_all_calls = 这份产物从头到现在的"
                      "累计（含失败轮，失败轮 token 记 0 但照样占一行）"
                      % (int(run_no), kp_build.PRICE_IN, kp_build.PRICE_OUT, kp_build.FX)),
        "usage_total": {"calls": agg["calls"], "calls_failed": agg["calls_failed"],
                        "tokens_in": agg["tokens_in"], "tokens_out": agg["tokens_out"],
                        "reasoning_tokens": rt},
        "usage_this_run": {"calls": run_agg["calls"], "calls_failed": run_agg["calls_failed"],
                           "tokens_in": run_agg["tokens_in"],
                           "tokens_out": run_agg["tokens_out"],
                           "reasoning_tokens": run_agg["reasoning_tokens"]},
        "reasoning_token_share": (round(rt / to, 4) if to else 0.0),
        "material": {"chars": int(render_stats.get("chars") or 0),
                     "mode": render_stats.get("mode") or "",
                     "dropped_fig_blocks": int(render_stats.get("dropped_fig_blocks") or 0),
                     "truncated_pages": list(render_stats.get("truncated_pages") or []),
                     "n_sources": len(inv),
                     "note": "material.mode：full=原样 / drop_figures=超限后丢图注重渲染 / "
                             "truncate_pages=仍超限按页截断（被截断的页逐页列出）"},
        "sections_regex": {"read": bool(sec.get("read")), "source": sec.get("source") or "",
                           "artifact_version": sec.get("artifact_version") or "",
                           "n_sections": int(sec.get("n_sections") or 0),
                           "fallback": bool(sec.get("fallback")),
                           "n_sections_with_points": len(with_pts),
                           "n_sections_without_points": max(0, len(secs) - len(with_pts)),
                           "points_without_section": sum(
                               1 for p in points if not int(p.get("section_index") or 0)),
                           "points_section_from_model": sum(
                               1 for p in points if p.get("section_from_model")),
                           "points_section_by_page": sum(
                               1 for p in points if not p.get("section_from_model")),
                           "note": sec.get("note") or "",
                           "cost": "0 次模型调用（纯正则产物）"},
        "n_points": len(points), "n_terms": len(terms), "n_rejected": len(rejected),
        "rejected": rejected,
        "skipped": bool(skipped),
        "elapsed_s": round(time.perf_counter() - started, 1),
        "status": status,
        "error": err,
    }


# ---------------------------------------------------------------- dry-run / 产物状态
def estimate_preview(doc_id: int, chapter, *, prompt_path=None, out_path=None,
                     db_path=None) -> dict:
    """dry-run：只读库里的文本，给出**调用次数（恒为 1）**与成本上界（不调模型、不写文件）。"""
    prompt = load_preview_prompts(prompt_path)
    ch, info, material, inv, stats = read_chapter_ch(doc_id, chapter, db_path=db_path)
    if not info.get("ok"):
        return {"doc_id": int(doc_id), "ok": False, "error": info.get("error"),
                "chapter": str(chapter)}
    sec = chapter_sections(doc_id, info)
    outline = render_sections_outline(sec)
    user_len = len(prompt["user"]) + len(material) + len(outline)
    est_in = int(user_len / kp_build.CHARS_PER_TOKEN)
    est_out = int(est_in * kp_build.REASON_RATIO) + 1200
    est_cost = round(est_in / 1e6 * kp_build.PRICE_IN * kp_build.FX
                     + est_out / 1e6 * kp_build.PRICE_OUT * kp_build.FX, 6)
    out = preview_out_file(doc_id, out_path)
    prev = kp_build._load_prev(out)
    pmeta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
    same = (str(pmeta.get("preview_version") or "") == PREVIEW_VERSION
            and int(pmeta.get("chapter_no") or 0) == int(info.get("no") or 0))
    cur_sha = hashlib.sha256((material + "\n" + outline).encode("utf-8")).hexdigest()
    fresh = bool(same and str((pmeta.get("inputs") or {}).get("material_sha256") or "")
                 == cur_sha and str(pmeta.get("status") or "") == "done")
    return {
        "doc_id": int(doc_id), "ok": True, "preview_version": PREVIEW_VERSION,
        "out_path": str(out), "exists": out.exists(),
        "title": kp_build.doc_title_v2(doc_id, db_path),
        "chapter": "第%d章 %s" % (int(info["no"]), info.get("name") or ""),
        "chapter_no": int(info["no"]), "chapter_name": info.get("name") or "",
        "heading_path": info.get("heading_path") or "",
        "chapter_source": info.get("source") or "",
        "pages": list(info.get("pages") or []),
        "page_label": kp_build._pages_label(info.get("pages") or []),
        "n_pages": len(info.get("pages") or []), "n_blocks": int(ch.get("n_blocks") or 0),
        "material_chars": len(material), "material_mode": stats.get("mode"),
        "n_sources": len(inv),
        "sections_source": "卡 C-0092《章节细目》（纯正则，0 次模型调用）",
        "n_sections": int(sec.get("n_sections") or 0),
        "sections_fallback": bool(sec.get("fallback")),
        "sections_read": bool(sec.get("read")),
        "prompt_path": prompt["path"], "prompt_sha256": prompt["sha256"],
        "model": model_name(), "thinking": thinking_on(), "reasoning_effort": effort(),
        "max_tokens": max_tokens(),
        "enabled": enabled(),
        "est_calls": 1,
        "est_calls_breakdown": {"chapter_summary": 1, "sections_regex": 0},
        "est_tokens_in": est_in, "est_tokens_out": est_out, "est_cost_cny": est_cost,
        "est_rule": ("整章 **1 次**调用（不切段、不并发）；成本上界按 "
                     "(输入 %d 字符 / %.1f 字符每 token) 与思考链 ≈ 输入 %s 倍 + 1200 正文估；"
                     "真实花费见产物 meta.cost_cny（只记真账）"
                     % (user_len, kp_build.CHARS_PER_TOKEN, kp_build.REASON_RATIO)),
        "material_sha256": cur_sha,
        "prev": {"exists": out.exists(),
                 "preview_version": str(pmeta.get("preview_version") or ""),
                 "chapter_no": int(pmeta.get("chapter_no") or 0),
                 "n_points": len([p for p in ((prev.get("summary") or {}).get("key_points")
                                              if isinstance(prev.get("summary"), dict) else [])
                                  or [] if isinstance(p, dict)]),
                 "generated_at": str(pmeta.get("generated_at") or ""),
                 "cost_cny": float(pmeta.get("cost_cny") or 0.0)},
        "skip_would_happen": fresh,
        "skip_rule": ("产物已存在、版本/章号/材料 sha256 都没变且 status=done 时，"
                      "force=False 直接跳过：**0 次调用、0 花费**"),
    }


def prev_info_preview(doc_id: int, chapter=None, out_path=None) -> dict:
    """预习产物状态（看板/CLI 判"要不要重新生成"用）。"""
    out = preview_out_file(doc_id, out_path)
    prev = kp_build._load_prev(out)
    meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
    summ = prev.get("summary") if isinstance(prev.get("summary"), dict) else {}
    return {
        "exists": out.exists(), "path": str(out),
        "preview_version": str(meta.get("preview_version") or ""),
        "is_current_version": str(meta.get("preview_version") or "") == PREVIEW_VERSION,
        "chapter": str(meta.get("chapter") or ""),
        "chapter_no": int(meta.get("chapter_no") or 0),
        "pages": list(meta.get("pages") or []),
        "n_points": len([p for p in (summ.get("key_points") or []) if isinstance(p, dict)]),
        "n_sections": int((meta.get("sections_regex") or {}).get("n_sections") or 0),
        "generated_at": str(meta.get("generated_at") or ""),
        "cost_cny": float(meta.get("cost_cny") or 0.0),
        "cost_cny_all_calls": float(meta.get("cost_cny_all_calls") or 0.0),
        "status": str(meta.get("status") or ""),
        "model": str(meta.get("model") or ""),
        "reasoning_effort": str((meta.get("params") or {}).get("reasoning_effort") or ""),
        "elapsed_s": float(meta.get("elapsed_s") or 0.0),
    }


# ---------------------------------------------------------------- 主流程
def build_preview(doc_id: int, chapter, *, prompt_path=None, out_path=None, db_path=None,
                  dry_run: bool = False, force: bool = False, ignore_switch: bool = False,
                  log=print, on_progress=None) -> dict:
    """**独立管线**：给一份教材的某一章做预习总结（整章 1 次调用）。

    流程：校验开关 → 章级取块（`heading_path`，0 成本）→ 渲染整章材料 →
    读卡 C-0092 的《章节细目》当节级骨架（0 成本）→ **1 次**总结调用 →
    逐条校验（必须出自本章材料）→ 把要点按页归位到节 → 原子落盘。

    - `dry_run=True`：只预估，不调模型、不写文件；
    - `force=False` 且产物未变（版本/章号/材料 sha256）时**直接跳过**（0 花费）；
    - `ignore_switch=True`：**只在开发/验证时用**，绕过 `PREVIEW_ENABLED`。
      看板入口（`start_background`）**永远不传它**——人要的"默认关闭"就是这么保证的；
    - 本函数**只写 `<logs/knowledge>/<doc_id>-预习总结.json` 一个文件**，
      不写任何数据库表、不碰知识点产物、不碰名词解释产物。
    """
    started = time.perf_counter()
    est = estimate_preview(doc_id, chapter, prompt_path=prompt_path,
                           out_path=out_path, db_path=db_path)
    if not est.get("ok"):
        return {"ok": False, "error": est.get("error"), "doc_id": int(doc_id),
                "chapter": str(chapter)}
    if dry_run:
        return {"ok": True, "dry_run": True, **est}

    # ★ 默认关闭：关着时**拒绝**并给可操作提示（不是静默什么都不做）
    st = enabled_state()
    if not st["enabled"] and not ignore_switch:
        return {"ok": False, "error": "预习功能未开启", "doc_id": int(doc_id),
                "chapter": str(chapter), "enabled": False,
                "hint": st["hint"], "switch": st["switch"]}

    out = Path(est["out_path"])
    if est.get("skip_would_happen") and not force:
        log("预习总结：本章产物已是最新（材料 sha256 未变）→ **跳过，0 次调用 0 花费**")
        log("产物：%s" % out)
        return {"ok": True, "status": "skipped", "out_path": str(out),
                "meta": (kp_build._load_prev(out).get("meta") or {}), "estimate": est}

    _stage(0, "读取第 %s 章" % est["chapter"], on_progress)
    prompt = load_preview_prompts(prompt_path)
    ch, info, material, inv, stats = read_chapter_ch(doc_id, chapter, db_path=db_path)
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error"), "doc_id": int(doc_id)}
    sec = chapter_sections(doc_id, info)
    outline = render_sections_outline(sec)
    material_sha = hashlib.sha256((material + "\n" + outline).encode("utf-8")).hexdigest()

    calls: list = []
    prev_meta = {}
    if out.exists():
        try:
            prev_meta = (kp_build._load_prev(out).get("meta") or {})
        except Exception:  # noqa: BLE001
            prev_meta = {}
    if not force and int(prev_meta.get("chapter_no") or 0) == int(info.get("no") or 0):
        # ⚠ 只有**同一章**才把旧账带过来（续跑口径，见 `kp_build._v2_call()` 的说明）。
        #   换章时旧产物是**另一章**的账，带过来会让"这一章的产物"显示成两章的花费。
        for _c in (prev_meta.get("calls") or []):
            if isinstance(_c, dict):
                _c = dict(_c)
                _c.setdefault("run", 1)
                calls.append(_c)
    run_no = max([int(c.get("run") or 1) for c in calls] or [0]) + 1

    log("预习总结（独立管线）：%s（doc#%d）· %s · %d 页 / %d 块 · 材料 %d 字符"
        % (est.get("title") or "", doc_id, est["chapter"], est["n_pages"],
           est["n_blocks"], len(material)))
    log("  ├ 节级细拆：%d 节（%s，**0 次模型调用**）"
        % (sec.get("n_sections") or 0,
           "来自卡 C-0092《章节细目》" if sec.get("read") else "无可用的《章节细目》→ 降级为空"))
    log("  ├ 材料模式：%s%s" % (stats.get("mode"),
                              ("（丢了 %d 个图注块）" % stats["dropped_fig_blocks"])
                              if stats.get("dropped_fig_blocks") else ""))
    log("  └ 调用：**整章 1 次**（%s / thinking=%s / reasoning_effort=%s / max_tokens=%d）"
        % (est["model"], est["thinking"], est["reasoning_effort"], est["max_tokens"]))
    if calls:
        log("  ⚠ 这份产物里已有 %d 次历史调用（%d 轮），本轮记为第 %d 轮"
            % (len(calls), len(kp_build._v2_runs(calls)), run_no))

    # ⚠ 产物路径按卡面口径就是 `<doc_id>-预习总结.json`（**不带章号**），
    #   所以换一章预习会覆盖上一章。覆盖前**自动备份**（与看板对知识点产物的做法一致：
    #   `api_kp_build_start` 在整份重写前先 copy 一份 `.bak-<时间戳>`）。
    #   同一章重跑不备份（那是同一份的迭代，留一堆 .bak 没有意义）。
    prev_backup = ""
    if out.exists() and int(prev_meta.get("chapter_no") or 0) != int(info.get("no") or 0):
        bak = out.with_name(out.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        try:
            shutil.copy2(out, bak)
            prev_backup = str(bak)
            log("  ├ 换章预习：上一章（第 %s 章）的产物已备份 → %s"
                % (prev_meta.get("chapter_no") or "?", bak.name))
        except OSError as e:
            log("  ├ ⚠ 旧产物备份失败：%s（仍继续写新产物，旧内容会被覆盖）" % e)

    status, err = "done", ""
    points: list = []
    terms: list = []
    rejected: list = []
    raw_summary: dict = {}
    _stage(1, "渲染整章材料（%d 字符）" % len(material), on_progress)
    _stage(2, "预习总结（1 次调用）", on_progress)
    try:
        user = (prompt["user"].replace("{deck}", est.get("title") or "")
                .replace("{chapter}", est["chapter"])
                .replace("{heading_path}", info.get("heading_path") or "")
                .replace("{n_pages}", str(est["n_pages"]))
                .replace("{page_label}", est.get("page_label") or "")
                .replace("{sections_outline}", outline)
                .replace("{chapter_material}", material))
        res = _preview_call(calls, "preview.chapter", prompt["sys"], user,
                            max_tokens=est["max_tokens"], retry=1, log=log,
                            note="第 %s 章整章总结（p.%s）" % (est["chapter"],
                                                              est.get("page_label") or ""),
                            run_no=run_no, thinking=est["thinking"],
                            reasoning_effort=est["reasoning_effort"],
                            model=est["model"])
        if not res["ok"]:
            raise RuntimeError("预习总结调用失败：%s" % res["error"])
        raw_summary = res["parsed"] if isinstance(res["parsed"], dict) else {}
        points, rejected = _clean_points(
            raw_summary.get("key_points") or raw_summary.get("points") or [],
            list(ch["pages_all"]), dict(ch["cover"]), material, sec.get("sections") or [])
        terms = _clean_terms_list(
            raw_summary.get("key_terms") or raw_summary.get("terms") or [], material)
        log("  ├ 模型给了 %d 条要点 → 校验后留 %d 条（剔 %d 条）"
            % (len(raw_summary.get("key_points") or raw_summary.get("points") or []),
               len(points), len(rejected)))
        if rejected and len(rejected) > len(points):
            # 剔除数超过保留数时，把模型原文的头一段打进日志——判据太严还是模型跑偏，
            # 一眼能看出来（C-0094 首版就是靠这个发现"照抄判据误杀概括"的）。
            log("    ⚠ 剔除多于保留 → 原始输出前 300 字：%s"
                % re.sub(r"\s+", " ", str(res.get("raw") or ""))[:300])
        log("  └ 关键名词 %d 个" % len(terms))
    except Exception as e:  # noqa: BLE001
        status, err = "failed", "%s: %s" % (type(e).__name__, e)
        log("★预习总结中止：%s" % err)

    summary = _build_summary(raw_summary, points, terms, material)
    sec_doc = _attach_points(sec.get("sections") or [], points)
    meta = _preview_meta(doc_id, info, prompt, est, calls, started=started, status=status,
                         err=err, inv=inv, render_stats=stats, sec=sec,
                         points=points, terms=terms, rejected=rejected,
                         material_sha=material_sha, run_no=run_no)
    meta["prev_backup"] = prev_backup
    _stage(3, "写入产物", on_progress)
    kp_build._save({"meta": meta, "sources": _sources(inv), "chapter": {
        "no": int(info.get("no") or 0), "name": info.get("name") or "",
        "heading_path": info.get("heading_path") or "",
        "page_start": meta["page_start"], "page_end": meta["page_end"],
        "page_label": meta["page_label"], "pages": list(meta["pages"]),
        "n_blocks": meta["n_blocks"], "material_chars": meta["material"]["chars"],
        "chapter_source": meta["chapter_source"],
    }, "summary": summary, "sections": sec_doc,
        "sections_stats": meta["sections_regex"]}, out)
    log("完成：状态 %s · 要点 %d 条 · 名词 %d 个 · %d 次调用 · 本次 CNY%.6f"
        % (status, len(points), len(terms), meta["usage_this_run"]["calls"],
           meta["cost_cny"]))
    log("      累计（这份产物从头到现在）：%d 次调用 · CNY%.6f · 用时 %.0fs"
        % (meta["usage_total"]["calls"], meta["cost_cny_all_calls"], meta["elapsed_s"]))
    log("产物：%s" % out)
    return {"ok": True, "status": status, "error": err, "out_path": str(out),
            "meta": meta, "estimate": est}


def _sources(inv: list) -> list:
    """顶层 `sources`：**必带** `{chunk_id, page}`（导入重映射靠这些字段名）。"""
    out, seen = [], set()
    for m in inv:
        cid, page = int(m["chunk_id"]), int(m.get("page") or 0)
        if (cid, page) in seen:
            continue
        seen.add((cid, page))
        out.append({"chunk_id": cid, "page": page, "kind": m.get("kind") or "",
                    "ref": m.get("ref") or "", "pages": list(m.get("pages") or [])})
    return out


def _build_summary(raw: dict, points: list, terms: list, material: str) -> dict:
    """产物顶层 `summary`：模型的整章概述 + 已校验的要点与名词。

    ⚠ 顶层**不放** `knowledge_points`（那是知识点产物的键，
    `dashboard.py::_kp_load_dir()` 只认它）。要点一律放在 `summary.key_points` 下。
    """
    def _s(key, *alts):
        for k in (key,) + alts:
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x or "").strip()]
        return ""

    return {
        "overview": _s("overview", "summary", "概述", "chapter_summary"),
        "study_order": _s("study_order", "order", "学习顺序"),
        "key_points": points,
        "key_terms": terms,
        "exam_focus": _s("exam_focus", "考点", "exam_points"),
        "difficulties": _s("difficulties", "难点"),
        "prerequisites": _s("prerequisites", "先修", "前置知识"),
        "questions": _s("questions", "自测题", "self_check"),
        "material_chars": len(material),
        "field_note": ("key_points 每条的 chunk_ids / material_block_ids 都由管线按页码"
                       "从本章材料清单里**自己算**，不采信模型给的块 id；"
                       "页码不在本章材料里、或带了材料里没有的数字的要点会被剔除，"
                       "逐条记进 meta.rejected"),
    }


def _attach_points(sections: list, points: list) -> list:
    """把要点按 `section_index` 归位到节（**纯本地计算，零模型调用**）。"""
    out = []
    for s in sections:
        item = dict(s)
        mine = [p for p in points
                if int(p.get("section_index") or 0) == int(s.get("index") or 0)]
        item["key_points"] = mine
        item["n_key_points"] = len(mine)
        item["chunk_ids"] = sorted({c for p in mine for c in (p.get("chunk_ids") or [])})
        item["material_block_ids"] = list(item["chunk_ids"])
        item["note"] = ("节级切分来自卡 C-0092 的《章节细目》（纯正则，零模型调用）；"
                        "key_points 是把**那一次**章级总结的要点按页归位的结果"
                        if mine else
                        "该节没有被章级总结的要点命中（可能是 OCR 噪声节，或要点落在别的节）")
        out.append(item)
    return out


# ---------------------------------------------------------------- 后台线程 + 进度（看板）
_PLOCK = threading.RLock()
_PSTATE: dict = {"running": False, "doc_id": 0, "chapter": "", "stage_index": 0,
                 "stage": "", "note": "", "cost_cny": 0.0, "calls": 0,
                 "out_path": "", "status": "", "error": "", "started_at": 0.0}


def _stage(i: int, note: str = "", on_progress=None) -> None:
    """写进程内进度（**只活在进程里，不写产物、不进库**）。"""
    with _PLOCK:
        _PSTATE["stage_index"] = max(0, min(int(i), len(PREVIEW_STAGES) - 1))
        _PSTATE["stage"] = PREVIEW_STAGES[_PSTATE["stage_index"]]
        _PSTATE["note"] = note
    if on_progress is not None:
        try:
            on_progress({"stage_index": _PSTATE["stage_index"], "stage": _PSTATE["stage"],
                         "note": note})
        except Exception:  # noqa: BLE001
            pass


def is_running() -> bool:
    with _PLOCK:
        return bool(_PSTATE["running"])


def progress_snapshot() -> dict:
    """给 `/api/preview/progress` 的快照：字段与 `/api/kp-build/progress` 同构，
    前端可以复用同一个进度条渲染函数。"""
    with _PLOCK:
        s = dict(_PSTATE)
    n = len(PREVIEW_STAGES)
    idx = int(s["stage_index"])
    active = bool(s["running"])
    done = (idx + (1 if str(s["status"]) == "done" else 0)) if active else (
        n if str(s["status"]) == "done" else 0)
    return {
        "kind": "preview", "active": active,
        "status": (s["status"] or ("running" if active else "")),
        "label": "预习总结：doc#%s %s" % (s["doc_id"] or "", s["chapter"] or ""),
        "stage": s["stage"], "stage_index": idx, "stages": list(PREVIEW_STAGES),
        "note": s["note"], "percent": int(min(n, done) * 100 / n),
        "doc_id": int(s["doc_id"] or 0), "chapter": s["chapter"] or "",
        "calls": int(s["calls"] or 0), "cost_cny": round(float(s["cost_cny"] or 0), 6),
        "elapsed": (round(time.time() - float(s["started_at"]), 1)
                    if s["started_at"] else 0.0),
        "out_path": s["out_path"] or "", "error": s["error"] or "",
        "enabled": enabled(), "model": model_name(),
        "thinking": thinking_on(), "reasoning_effort": effort(),
    }


def start_background(doc_id: int, chapter, **kw) -> dict:
    """起一个后台线程跑 `build_preview()`（看板按钮用）。同时只允许一个任务。

    **不能阻塞 HTTP 线程**（照 `kp_build.start_background()` 的既有做法）：
    预习一次几秒到几分钟，在请求里同步跑会把看板卡住——所以请求立刻返回，
    进度走 `/api/preview/progress`；而进度接口必须排在 `_lock` **之前**。
    """
    if is_running():
        return {"started": False, "error": "已有一个预习任务在跑，请等它结束（同时只允许一个）"}
    st = enabled_state()
    if not st["enabled"] and not kw.get("ignore_switch"):
        # ★ 默认关闭：**线程入口也拒绝**（看板接口、CLI、后台线程三层同一口径），
        #   并给可操作提示——不是静默什么都不做。
        return {"started": False, "error": "预习功能未开启", "enabled": False,
                "hint": st["hint"], "switch": st["switch"]}
    if kp_build.is_running():
        return {"started": False,
                "error": "有一个「整理知识点」任务在跑；两者都会长期占用模型调用，"
                         "请等它结束（或取消）后再做预习"}
    try:
        from . import kp_batch
        if kp_batch.is_running():
            return {"started": False,
                    "error": "有一个「批量整理」任务在跑；等它结束（或取消）后再做预习"}
    except Exception:  # noqa: BLE001
        pass
    with _PLOCK:
        _PSTATE.update({"running": True, "doc_id": int(doc_id),
                        "chapter": "第%d章" % int(_norm_chapter_arg(chapter).get("no") or 0)
                                   if int(_norm_chapter_arg(chapter).get("no") or 0)
                                   else str(chapter),
                        "stage_index": 0, "stage": PREVIEW_STAGES[0], "note": "",
                        "cost_cny": 0.0, "calls": 0, "out_path": "",
                        "status": "running", "error": "", "started_at": time.time()})

    def _run() -> None:
        rep: dict = {}
        try:
            rep = build_preview(doc_id, chapter, **kw)
        except Exception as e:  # noqa: BLE001
            rep = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        finally:
            meta = rep.get("meta") if isinstance(rep.get("meta"), dict) else {}
            with _PLOCK:
                _PSTATE.update({
                    "running": False,
                    "status": str(rep.get("status") or ("failed" if not rep.get("ok") else "done")),
                    "error": str(rep.get("error") or ""),
                    "out_path": str(rep.get("out_path") or ""),
                    "calls": int((meta.get("usage_this_run") or {}).get("calls") or 0),
                    "cost_cny": float(meta.get("cost_cny") or 0.0),
                    "stage_index": max(0, len(PREVIEW_STAGES) - 1),
                    "stage": PREVIEW_STAGES[-1],
                })

    th = threading.Thread(target=_run, name="preview-%d" % int(doc_id), daemon=True)
    th.start()
    return {"started": True, "doc_id": int(doc_id), "chapter": str(chapter)}
