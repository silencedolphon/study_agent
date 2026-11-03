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

import copy
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
from . import db as db_mod
from . import kp_build
from .pipeline_runtime import context as runtime_context

# ---------------------------------------------------------------- 常量
# 产物版本：**故意叫 preview_version 而不是 pipeline_version**。
#   理由：`tools/dashboard.py::_artifact_load()` 的版本白名单
#   （`_KP_READABLE_VERSIONS`，`dashboard.py:1544-1549`）是给「末级知识点总结」那套产物
#   用的；预习产物不是知识点产物，混进那个版本号体系只会让"哪个白名单管哪个文件"
#   变得含糊。这里用独立字段，看板/CLI 判"是不是本版产物"一律读它。
PREVIEW_VERSION = "C-0110-preview-v2"
PREVIEW_VERSION_LEGACY = "C-0094-preview-v1"
PREVIEW_SCHEMA = "preview-v2"
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
    """整章单次调用的上限。

    2026-10-27 从 16384 提到 32768：第16章实测输出 16382/16384 token 全被
    思考链+正文吃满，正文 JSON 在第184行被截断；''_preview_call'' 虽会自动
    加倍重试，但首发就截断会白花一次钱、也更容易撞上网络异常。
    """
    return max(1024, _cfg_int("PREVIEW_MAX_TOKENS", 32768))


def model_slot_wait_timeout() -> float:
    """等模型槽位的上限（秒）。0 = 不限。

    默认 300 秒：预习是"人点一次等结果"的交互任务，不该为了等一个挂起的
    模型槽位让页面看起来卡几十分钟。超时后本步直接失败并给出可操作提示。
    """
    try:
        return max(0.0, float(_cfg_int("PREVIEW_SLOT_TIMEOUT", 300)))
    except (TypeError, ValueError):
        return 300.0


def enabled() -> bool:
    """★ 默认**开启**（2026-10-20 用户要求可用；`_FIELDS` 的 default 已同步为 1）。"""
    return bool(_cfg_int("PREVIEW_ENABLED", 1))


def enabled_state() -> dict:
    """关着时给入口一句**可操作**的提示（不是"失败"，是"还没打开"）。"""
    on = enabled()
    return {
        "enabled": on,
        "hint": ("预习功能已开启。" if on else
                 "预习功能已被关闭：请到「设置中心 → AI 预习总结」把「开启预习功能」"
                 "打开（写入本机 config.local.json 的 PREVIEW_ENABLED=1）后再来。"
                 "2026-10-20 起该开关默认开启；关着时入口会直接拒绝，不会静默花钱。"),
        "switch": "PREVIEW_ENABLED",
        "pipeline": "preview",
    }


# ---------------------------------------------------------------- 路径
def preview_out_file(doc_id: int, out_path: str | os.PathLike | None = None) -> Path:
    """产物路径：`<当前学科 logs>/knowledge/<doc_id>-预习总结.json`。"""
    if out_path:
        return Path(out_path)
    return config.LOG_DIR / "knowledge" / ("%d%s" % (int(doc_id), PREVIEW_SUFFIX))


def preview_out_file_per_chapter(doc_id: int, chapter_no: int) -> Path:
    """**按章产物路径**：`<logs/knowledge>/<doc_id>-预习总结-第<N>章.json`。

    多章并存、互不覆盖；`out_path` 显式传参时由调用方决定路径。
    旧单文件 `<doc_id>-预习总结.json` 只保留**只读兼容**（读取时兜底）。
    """
    return (config.LOG_DIR / "knowledge"
            / ("%d-预习总结-第%d章.json" % (int(doc_id), int(chapter_no))))


def legacy_preview_out_file(doc_id: int) -> Path:
    """旧单文件产物（C-0094-v1）的路径，**只用于只读兼容**。"""
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


def _list_chapters_uncached(doc_id: int, *, db_path: str | os.PathLike | None = None,
                            _conn=None) -> dict:
    """列这份教材的章（**零模型成本**）：`chunks.heading_path` 里的 `> 第N章 …` 归章。

    返回 `{"ok", "doc_id", "title", "chapters":[{no,name,heading_path,n_blocks,
    page_start,page_end,pages,n_sections}], "sections_artifact": {...}}`。
    `n_sections` 来自卡 C-0092 的《章节细目》（读不到就为 0 并如实标注）。

    `_conn`：内部参数（公共 API 兼容）。给定时复用同一条只读连接/事务快照，
    不再自开连接、也不关闭它（GPT-G 的章级一致读）。
    """
    own = _conn is None
    if own:
        db = Path(db_path) if db_path else Path(config.DB_PATH)
        conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    else:
        conn = _conn
    try:
        rows = conn.execute("SELECT id, ord, source_ref, heading_path FROM chunks "
                            "WHERE doc_id=? ORDER BY ord, id", (int(doc_id),)).fetchall()
    finally:
        if own:
            conn.close()
    if not rows:
        return {"ok": False, "doc_id": int(doc_id),
                "error": "这份资料在库里没有任何块（chunks 为空），列不出章节"}

    agg: dict = {}
    bad_refs: list[dict] = []
    book = ""
    for cid, _od, ref, hp in rows:
        hp = str(hp or "")
        m = _CH_RE.search(hp)
        if not book and ">" in hp:
            book = hp.split(">")[0].strip()
        parsed = kp_build._parse_ref(str(ref or ""))
        if parsed is None:
            # 不能把坏 source_ref 当成“这章没有块”：旧 read_text_pages 会明确
            # 报错“拒绝静默丢块”。记录给 locate/read_chapter_ch，快速路径据此
            # 回退旧路径并保留同样的错误语义。
            bad_refs.append({"id": int(cid), "ref": str(ref or ""),
                             "heading_path": hp})
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
            # 章级定位的快速路径：`locate_chapter` 直接复用，避免第二次整表扫描。
            # 这是后端内部/兼容字段；前端忽略未知字段即可。
            "block_ids": list(d["block_ids"]),
            "n_sections": len([s for s in (art.get("sections") or [])
                               if isinstance(s, dict)]),
            "sections_fallback": bool(art.get("fallback")),
        })
    if _conn is not None:
        # 快照连接读标题（不另开连接，避免"标题来自新库、章节来自旧快照"的混搭）
        try:
            tr = _conn.execute("SELECT title FROM documents WHERE id=?",
                               (int(doc_id),)).fetchone()
            title = str((tr[0] if tr else "") or "").strip()
        except sqlite3.Error:
            title = ""
        title = title or ("文档 %d" % int(doc_id))
    else:
        title = kp_build.doc_title_v2(doc_id, db_path)
    return {
        "ok": True, "doc_id": int(doc_id), "title": title,
        "book": book, "n_chapters": len(chapters), "chapters": chapters,
        "invalid_refs": bad_refs,
        "chapter_source": "chunks.heading_path",
        "sections_artifact": {
            "read": bool(detail.get("ok")), "path": str(sections_out_file(doc_id)),
            "version": str((detail.get("meta") or {}).get("version") or ""),
            # 缺产物时**不在这里写文件**（list 是零写入路径）；真正 build 时会自动
            # 用 core.sections 纯本地生成（0 次模型调用，见 ensure_sections_artifact）。
            "would_auto_build": not bool(detail.get("ok")),
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
                   db_path: str | os.PathLike | None = None,
                   _conn=None) -> dict:
    """定位一章：先按 `heading_path`（入库结构），找不到再退到《章节细目》的页范围。

    返回 `{"ok", "no", "name", "heading_path", "block_ids", "pages", "page_start",
    "page_end", "source"}`。两种来路都找不到就 `ok=False` + 可操作提示。

    `_conn`：内部参数（GPT-G）。给定时，章节目录与兜底页范围扫描都在这条
    只读连接的事务快照里做，函数不关闭它；公共调用方不需要传。
    """
    want = _norm_chapter_arg(chapter)
    listed = list_chapters(doc_id, db_path=db_path, _conn=_conn)
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
        # 快速路径：list_chapters 已经把本章 block_ids 聚合好了；不要再扫一遍
        # 全表。旧接口/旧调用若没有该字段，保留 agg_block_ids 兜底。
        ids = hit.get("block_ids")
        if not isinstance(ids, list):
            ids = agg_block_ids(doc_id, int(hit["no"]), db_path=db_path,
                                _conn=_conn)
        return {"ok": True, "doc_id": int(doc_id), "no": int(hit["no"]),
                "name": hit["name"], "heading_path": hit["heading_path"],
                "block_ids": list(ids),
                "pages": list(hit["pages"]), "page_start": hit["page_start"],
                "page_end": hit["page_end"], "source": "chunks.heading_path",
                "invalid_refs": list(listed.get("invalid_refs") or [])}

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
    own = _conn is None
    if own:
        db = Path(db_path) if db_path else Path(config.DB_PATH)
        conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    else:
        conn = _conn
    try:
        ids = []
        for cid, ref in conn.execute("SELECT id, source_ref FROM chunks WHERE doc_id=?",
                                     (int(doc_id),)):
            parsed = kp_build._parse_ref(str(ref or ""))
            if parsed and parsed[0] <= b and parsed[1] >= a:
                ids.append(int(cid))
    finally:
        if own:
            conn.close()
    return {"ok": True, "doc_id": int(doc_id), "no": int(art.get("no") or want["no"] or 0),
            "name": str(art.get("name") or want["name"] or ""),
            "heading_path": "%s > 第%s章 %s" % (listed.get("book") or "",
                                                art.get("no"), art.get("name") or ""),
            "block_ids": ids, "pages": list(range(a, b + 1)),
            "page_start": a, "page_end": b,
            "source": "章节细目.page_range",
            "invalid_refs": list(listed.get("invalid_refs") or []),
            "fallback_note": "heading_path 里认不出这一章，退用《章节细目》的页范围筛块"}


def agg_block_ids(doc_id: int, chapter_no: int, *,
                  db_path: str | os.PathLike | None = None,
                  _conn=None) -> list:
    """该章的块 id（`heading_path` 章级筛选，**零模型成本**）。

    `_conn`：内部参数（GPT-G）——给定时复用同一条只读连接/快照，不自开、不关闭。
    """
    own = _conn is None
    if own:
        db = Path(db_path) if db_path else Path(config.DB_PATH)
        conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    else:
        conn = _conn
    try:
        out = []
        for cid, hp in conn.execute("SELECT id, heading_path FROM chunks "
                                    "WHERE doc_id=? ORDER BY ord, id", (int(doc_id),)):
            m = _CH_RE.search(str(hp or ""))
            if m and int(m.group(1)) == int(chapter_no):
                out.append(int(cid))
        return out
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------- 章级 ch
def _assemble_chapter(full: dict, info: dict) -> tuple:
    """把整本/指定块的页结构组装成章级 ch 并渲染材料。

    这是 `read_chapter_ch` 的纯内存后半段：整本慢路径与章级快速路径共用，
    保证两条路径的 ch/material/inv/stats 口径逐字一致。
    """
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


def _read_chapter_ch_slow(doc_id: int, chapter, *,
                          db_path: str | os.PathLike | None = None,
                          _conn=None) -> tuple:
    """旧路径（整本读取后筛章）：保留作回滚/对照用。

    `_conn`：内部参数（GPT-G）。给定时用**同一条快照连接**枚举全量块 id 再读正文
    （等价于整本读取，但不会在定位与正文之间读到另一个快照），不关闭它。
    """
    if _conn is not None:
        ids = [int(r[0]) for r in _conn.execute(
            "SELECT id FROM chunks WHERE doc_id=? ORDER BY ord, id", (int(doc_id),))]
        full = kp_build.read_text_pages_by_ids(doc_id, ids, db_path, _conn=_conn)
        info = locate_chapter(doc_id, chapter, db_path=db_path, _conn=_conn)
    else:
        full = kp_build.read_text_pages(doc_id, db_path)
        info = locate_chapter(doc_id, chapter, db_path=db_path)
    if not info.get("ok"):
        return {}, info, "", [], {"ok": False}
    return _assemble_chapter(full, info)


def _read_chapter_ch_uncached(doc_id: int, chapter, *,
                              db_path: str | os.PathLike | None = None,
                              _conn=None) -> tuple:
    """构造**章级 ch**并渲染整章材料。

    返回 `(ch, info, material, inv, render_stats)`：

    - `ch` 的键与 `kp_build.read_text_pages()` **完全一致**
      （`blocks`/`cover`/`figrows`/`pages_all`/`max_page`/`n_blocks`），只是页集合换成该章
      ——于是 `kp_build.render_kp_material()` 可以直接吃它（材料口径与知识点管线同源）；
    - `info` 是 `locate_chapter()` 的结果；
    - `material` 是送进模型的整章材料；`inv` 是材料清单（→ 产物 `sources`）；
    - `render_stats` 如实记材料降级（丢图注 / 截断页）。

    默认走**章级快速路径**：`locate_chapter` 先给出本章 block_ids，只查这些块，
    不再把整本书（doc#50 约 900 块）读进内存。设置 `PREVIEW_CHAPTER_FAST=0`
    可回退到旧的整本读取路径（用于对照/回滚）。
    """
    if (os.environ.get("PREVIEW_CHAPTER_FAST") or "1").strip() == "0":
        return _read_chapter_ch_slow(doc_id, chapter, db_path=db_path, _conn=_conn)
    info = locate_chapter(doc_id, chapter, db_path=db_path, _conn=_conn)
    if not info.get("ok"):
        return {}, info, "", [], {"ok": False}
    # 旧路径 read_text_pages 遇到任何坏 source_ref 会明确报错；快速路径若只
    # 用 list_chapters 的 block_ids，会静默绕过这些块。存在坏页码时回退旧路径，
    # 保留“拒绝静默丢块”的安全语义（S00A-2）。
    if info.get("invalid_refs"):
        return _read_chapter_ch_slow(doc_id, chapter, db_path=db_path, _conn=_conn)
    full = kp_build.read_text_pages_by_ids(
        doc_id, list(info.get("block_ids") or []), db_path, _conn=_conn)
    return _assemble_chapter(full, info)


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
                  model: str = "", timeout: int = kp_build.TIMEOUT,
                  run_context=None) -> dict:
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

    # P0-C：有 RunContext 时 model/max_tokens/thinking/effort 全用冻结快照；
    # 无 context / PIPELINE_RUNTIME=0 逐字旧路径。
    ctx = (run_context if run_context is not None
           and runtime_context.runtime_enabled() else None)
    if ctx is not None:
        mdl = str(ctx.model)
        thinking = bool(ctx.thinking)
        reasoning_effort = str(ctx.reasoning_effort) if thinking else ""
        cap = int(ctx.max_tokens or 0)
    else:
        mdl = str(model or model_name())
        thinking = bool(thinking)
        cap = max(1024, int(max_tokens))
    last, last_idx = "", -1
    for attempt in range(int(retry) + 1):
        t0 = time.perf_counter()
        rec = {"run": int(run_no), "kind": kind, "attempt": attempt + 1, "note": note,
               "model": mdl, "max_tokens": int(cap),
               "thinking": bool(thinking),
               "reasoning_effort": (str(reasoning_effort) if thinking else ""),
               "chars_in": len(sys_p) + len(user_p),
               "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        try:
            with kp_build.model_slot(kind, timeout=model_slot_wait_timeout()):
                from . import agent_bridge
                with agent_bridge.pipeline_scope("preview"):
                    r = kp_build._call_with_hard_timeout(
                        lambda: chat.chat_once([{"role": "system", "content": sys_p},
                                               {"role": "user", "content": user_p}],
                                              model=mdl, max_tokens=cap, thinking=thinking,
                                              temperature=temperature, timeout=timeout,
                                              reasoning_effort=(reasoning_effort if thinking else None),
                                              run_context=ctx),
                        timeout=kp_build.call_hard_timeout(), kind=kind)
        except kp_build.ModelSlotTimeout as e:
            # 等槽位超时：说明有调用挂起没归还。再重试只会再等一轮，直接放弃。
            last = "%s: %s" % (type(e).__name__, e)
            rec.update({"ok": False, "error": last,
                        "elapsed_s": round(time.perf_counter() - t0, 1),
                        "tokens_in": 0, "tokens_out": 0, "reasoning_tokens": 0,
                        "cost_cny": 0.0, "raw_chars": 0})
            last_idx = kp_build._append_call(calls, rec, call_no=1)
            log("  ★等模型槽位超时（%s 第 %d 次），不再重试：%s"
                % (kind, attempt + 1, last))
            break
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
            rec.update({"ok": False, "error": last,
                        "elapsed_s": round(time.perf_counter() - t0, 1),
                        "tokens_in": 0, "tokens_out": 0, "reasoning_tokens": 0,
                        "cost_cny": 0.0, "raw_chars": 0})
            last_idx = kp_build._append_call(calls, rec, call_no=1)
            log("  ★调用失败（%s 第 %d 次）：%s" % (kind, attempt + 1, last))
            # P0-B：发送后结果未知 → 只允许一次请求，不自动重试。
            from .pipeline_runtime import state as runtime_state
            if runtime_state.is_unknown_failure(e):
                rec.update({"remote_unknown": True, "indeterminate": True,
                            "cost_unknown": True, "status": runtime_state.REMOTE_UNKNOWN})
                log("  ★remote_unknown：结果未知，禁止自动重试（P0-B）")
                return {"ok": False, "parsed": {}, "usage": {}, "raw": "",
                        "model": mdl, "error": last, "call_index": last_idx,
                        "call": rec, "remote_unknown": True}
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
        if cap > 0 and usage["tokens_out"] >= cap * 0.98 and cap < 65536:
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
                     % (meta_model, meta_think, meta_effort)),
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
    """dry-run：只读库 → **调用次数与成本上界**（不调模型、不写文件）。

    v2（2026-10-20）变化：

    - 产物是**按章**文件 `logs/knowledge/<doc>-预习总结-第<N>章.json`；
      `out_path` 默认就是它，另给 `out_path_per_chapter`（同值）与
      `old_out_path`（旧单文件，只读兼容）。
    - **缺《章节细目》时不写文件**：只把 `sections_would_build=True` 报出来；
      真跑 `build_chapter_preview()` 时才会用 `core.sections` 纯本地生成
      （0 次模型调用，见 `ensure_sections_artifact`）。
    - 调用数恒为 **1 次/章**：无小节细分调用、无结构分析调用。
    """
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
    no = int(info.get("no") or 0)
    legacy = legacy_preview_out_file(doc_id)
    # 章号 0（理论上不该出现：定位不到章时 info.ok 已是 False）→ 退回旧单文件路径，
    # 绝不生成 `第0章` 这种怪文件名。
    path_per = preview_out_file_per_chapter(doc_id, no) if no else legacy
    out = Path(out_path) if out_path else path_per
    prev = kp_build._load_prev(out)
    prev_from_legacy = False
    if not prev and legacy.exists():
        lp = kp_build._load_prev(legacy)
        lm = lp.get("meta") if isinstance(lp.get("meta"), dict) else {}
        if int(lm.get("chapter_no") or 0) == no:
            prev, prev_from_legacy = lp, True
    pmeta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
    summ = prev.get("summary") if isinstance(prev.get("summary"), dict) else {}
    n_points = len(prev.get("subsections") or [])
    if not n_points:
        n_points = len([x for x in ((summ.get("key_points") or [])
                                    if isinstance(summ.get("key_points"), list) else [])
                        if isinstance(x, dict)])
    same = (str(pmeta.get("preview_version") or "") == PREVIEW_VERSION
            and int(pmeta.get("chapter_no") or 0) == no)
    cur_sha = hashlib.sha256((material + "\n" + outline).encode("utf-8")).hexdigest()
    fresh = bool(same and str((pmeta.get("inputs") or {}).get("material_sha256") or "")
                 == cur_sha and str(pmeta.get("status") or "") == "done")
    return {
        "doc_id": int(doc_id), "ok": True, "preview_version": PREVIEW_VERSION,
        "preview_schema": PREVIEW_SCHEMA,
        "out_path": str(out),
        "out_path_per_chapter": str(path_per),
        "old_out_path": str(legacy),
        "exists": out.exists() or prev_from_legacy,
        "title": kp_build.doc_title_v2(doc_id, db_path),
        "chapter": "第%d章 %s" % (no, info.get("name") or ""),
        "chapter_no": no, "chapter_name": info.get("name") or "",
        "heading_path": info.get("heading_path") or "",
        "chapter_source": info.get("source") or "",
        "pages": list(info.get("pages") or []),
        "page_label": kp_build._pages_label(info.get("pages") or []),
        "n_pages": len(info.get("pages") or []), "n_blocks": int(ch.get("n_blocks") or 0),
        "n_sections": int(sec.get("n_sections") or 0),
        "sections_read": bool(sec.get("read")),
        "sections_fallback": bool(sec.get("fallback")),
        "sections_would_build": not bool(sec.get("read")),
        "sections_source": ("卡 C-0092《章节细目》（纯正则，0 次模型调用）"
                            if sec.get("read") else
                            "缺《章节细目》→ build 时用 core.sections 纯本地生成"
                            "（0 次模型调用，不伪造节）"),
        "material_chars": len(material), "material_mode": stats.get("mode"),
        "n_sources": len(inv),
        "prompt_path": prompt["path"], "prompt_sha256": prompt["sha256"],
        "model": model_name(), "thinking": thinking_on(), "reasoning_effort": effort(),
        "max_tokens": max_tokens(),
        "enabled": enabled(),
        "est_calls": 1,
        "est_calls_breakdown": {"chapter_summary": 1, "sections_regex": 0},
        "est_tokens_in": est_in, "est_tokens_out": est_out, "est_cost_cny": est_cost,
        "est_rule": ("整章 **1 次**调用（不切段、不并发；小节细分是同一份输入里的事，"
                     "不额外调用）；成本上界按 (输入 %d 字符 / %.1f 字符每 token) 与"
                     "思考链 ≈ 输入 %s 倍 + 1200 正文估；真实花费见产物 meta.cost_cny"
                     % (user_len, kp_build.CHARS_PER_TOKEN, kp_build.REASON_RATIO)),
        "material_sha256": cur_sha,
        "prev": {"exists": out.exists() or prev_from_legacy,
                 "path": str(out),
                 "preview_version": str(pmeta.get("preview_version") or ""),
                 "chapter_no": int(pmeta.get("chapter_no") or 0),
                 "n_points": n_points,
                 "n_subsections": len(prev.get("subsections") or []),
                 "generated_at": str(pmeta.get("generated_at") or ""),
                 "cost_cny": float(pmeta.get("cost_cny") or 0.0),
                 "from_legacy_single": bool(prev_from_legacy)},
        "skip_would_happen": fresh,
        "skip_rule": ("同一章产物已存在、版本/章号/材料 sha256 都没变且 status=done 时，"
                      "force=False 直接跳过：**0 次调用、0 花费**"),
    }


def prev_info_preview(doc_id: int, chapter=None, out_path=None) -> dict:
    """预习产物状态（看板/CLI 判"要不要重新生成"用）。零模型、零写入。

    `chapter` 省略 → 汇总所有按章产物；给定章 → 只报该章（优先按章文件，
    回退旧单文件；字段与旧版尽量兼容）。
    """
    if chapter is None or (isinstance(chapter, str) and not chapter.strip()):
        st = preview_status(doc_id)
        arts = {k: v for k, v in st.items() if not str(k).startswith("_")}
        total = round(sum(float(v.get("cost_cny") or 0) for v in arts.values()), 6)
        return {"exists": bool(arts), "path": str(config.LOG_DIR / "knowledge"),
                "chapters": arts, "n_chapters_with_artifact": len(arts),
                "preview_version": PREVIEW_VERSION, "preview_schema": PREVIEW_SCHEMA,
                "cost_cny": total, "is_current_version": bool(arts),
                "chapter": "", "chapter_no": 0, "pages": [], "n_points": 0,
                "n_subsections": sum(int(v.get("n_subsections") or 0)
                                     for v in arts.values()),
                "n_sections": 0, "generated_at": "", "cost_cny_all_calls": total,
                "status": "done" if arts else "", "model": model_name(),
                "reasoning_effort": effort(), "elapsed_s": 0.0}
    want = _norm_chapter_arg(chapter)
    no = int(want.get("no") or 0)
    if not no:
        info = locate_chapter(doc_id, chapter)
        no = int(info.get("no") or 0) if info.get("ok") else 0
    out = Path(out_path) if out_path else preview_out_file_per_chapter(doc_id, no)
    prev = kp_build._load_prev(out)
    if not prev and no:
        read = read_preview_artifact(doc_id, no)
        if read.get("ok"):
            art = read.get("artifact") or {}
            prev = art
            out = Path(read.get("path") or out)
    meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
    summ = prev.get("summary") if isinstance(prev.get("summary"), dict) else {}
    n_points = len([x for x in ((summ.get("key_points") or [])
                                if isinstance(summ.get("key_points"), list) else [])
                    if isinstance(x, dict)])
    return {
        "exists": bool(prev), "path": str(out),
        "preview_version": str(meta.get("preview_version") or ""),
        "is_current_version": str(meta.get("preview_version") or "") == PREVIEW_VERSION,
        "legacy": bool(prev) and str(meta.get("preview_version") or "") != PREVIEW_VERSION,
        "chapter": str(meta.get("chapter") or ""),
        "chapter_no": int(meta.get("chapter_no") or no),
        "pages": list(meta.get("pages") or []),
        "n_points": n_points,
        "n_subsections": len(prev.get("subsections") or []),
        "n_sections": int((meta.get("sections_regex") or {}).get("n_sections") or 0)
                      or int(meta.get("n_sections") or 0),
        "generated_at": str(meta.get("generated_at") or ""),
        "cost_cny": float(meta.get("cost_cny") or 0.0),
        "cost_cny_all_calls": float(meta.get("cost_cny_all_calls") or 0.0),
        "status": str(meta.get("status") or ""),
        "error": str(meta.get("error") or "")[:400],
        "model": str(meta.get("model") or ""),
        "reasoning_effort": str((meta.get("params") or {}).get("reasoning_effort") or ""),
        "elapsed_s": float(meta.get("elapsed_s") or 0.0),
    }


# ---------------------------------------------------------------- 主流程
def build_preview(doc_id: int, chapter, *, prompt_path=None, out_path=None, db_path=None,
                  dry_run: bool = False, force: bool = False, ignore_switch: bool = False,
                  log=print, on_progress=None) -> dict:
    """旧名保留（CLI `tools/preview_build.py` 与既有调用方继续可用）。

    2026-10-20 起转调 `build_chapter_preview()`：**按章产物、每章 1 次调用、
    小节细分**。参数/返回结构尽量与旧版兼容（`out_path` 显式给定时照旧写到该路径）。
    """
    return build_chapter_preview(
        doc_id, chapter, prompt_path=prompt_path, out_path=out_path, db_path=db_path,
        dry_run=dry_run, force=force, ignore_switch=ignore_switch,
        log=log, on_progress=on_progress)


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
            rep = build_chapter_preview(doc_id, chapter, **kw)
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


# ==========================================================================
# v2（2026-10-20 按用户要求重做）：按章多产物 + 小节细分 + 新提示词/schema
# ==========================================================================
# 设计口径：
#   · 每章一份产物 `logs/knowledge/<doc_id>-预习总结-第<N>章.json`，多章并存；
#     旧单文件 `<doc_id>-预习总结.json` 只做**只读兼容**；
#   · 节级细目缺产物时用 core/sections.py 纯正则本地生成（0 次模型调用）；
#   · 每章仍然**只做 1 次模型调用**（小节细分在这一次调用里完成）；
#   · 模型 schema 见 `logs/knowledge/提示词-预习总结.md`（v2 重写，含旧 key_points 兼容兜底）。
MAX_SUBSECTIONS = 30
EXPECT_SUBSECTIONS = (3, 10)          # 提示词建议范围（只用于 warning，不硬卡）


def _list_str(value) -> list:
    """把任意输入折成"去空白的字符串列表"（v2 parser 用，绝不抛）。"""
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x or "").strip()]
    s = str(value or "").strip()
    return [s] if s else []


def ensure_sections_artifact(doc_id: int, db_path: str | os.PathLike | None = None,
                             *, dry_run: bool = False, run_context=None) -> dict:
    """确保卡 C-0092 的《章节细目》存在；缺则用 core.sections **纯本地**生成。

    **0 次模型调用**（纯正则）。行为契约：

    - 已存在 → 直接复用，返回 `created=False / read=True`，**不读库、不写文件**；
    - 缺产物 + `dry_run=False` → `core.sections.load_blocks()` → `detect_sections()`
      → `dump_sections()` 写到 `logs/knowledge/<doc>-章节细目.json`；
    - 缺产物 + `dry_run=True` → **不写任何文件**，只返回 `would_generate=True`；
    - 抽不出任何节（OCR 噪声/正则失败）→ **如实返回 note、不伪造节、不写空产物**；
    - 本地生成失败 → `ok=False` + `error`（不阻断章级预习：小节可让模型自拟）。
    """
    p = sections_out_file(doc_id)
    existing = _load_sections_artifact(doc_id)
    if existing.get("ok"):
        n = sum(len([x for x in (c.get("sections") or []) if isinstance(x, dict)])
                for c in existing["chapters"])
        return {"ok": True, "created": False, "read": True, "path": str(p),
                "n_sections": int(n), "n_chapters": len(existing["chapters"]),
                "source": "artifact", "note": existing.get("note") or ""}
    if dry_run:
        return {"ok": True, "created": False, "read": False, "dry_run": True,
                "path": str(p), "n_sections": 0, "n_chapters": 0,
                "source": "missing", "would_generate": True,
                "note": ("缺《章节细目》产物；真跑 build 时会用 core.sections 纯本地生成"
                         "（0 次模型调用）。dry-run 不写任何文件。")}
    try:
        from . import sections as _sections
        blocks, spans, meta = _sections.load_blocks(int(doc_id), db_path)
        title = str(meta.get("title") or "")
        res = _sections.detect_sections(blocks, None, book_title=title,
                                        doc_id=int(doc_id), block_pages=spans)
        n_sec = int(getattr(res, "n_sections", 0) or 0)
        n_ch = len(getattr(res, "chapters", []) or [])
        if n_sec <= 0:
            note = ("纯本地切分抽不出任何节（%d 块 / %d 章）——如实留空，不伪造节；"
                    "章级预习照常可做（小节由模型按内容逻辑自拟）"
                    % (len(blocks), n_ch))
            warns = list(getattr(res, "warnings", []) or [])
            if warns:
                note += "；切分告警：" + "；".join(str(w) for w in warns[:2])
            return {"ok": True, "created": False, "read": False, "path": str(p),
                    "n_sections": 0, "n_chapters": n_ch, "source": "generated_empty",
                    "note": note}
        _sections.dump_sections(res, p)
        return {"ok": True, "created": True, "read": False, "path": str(p),
                "n_sections": n_sec, "n_chapters": n_ch, "source": "generated",
                "warnings": list(getattr(res, "warnings", []) or []),
                "note": "已用 core.sections 纯本地生成《章节细目》（0 次模型调用）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "created": False, "read": False, "path": str(p),
                "error": "%s: %s" % (type(e).__name__, e),
                "note": "本地生成《章节细目》失败；章级预习仍可做（小节由模型自拟）"}


def _clean_subsections(raw, pages_all, cover_map, mat_text, warnings) -> list:
    """校验模型给的 `subsections`：页码 ⊆ 本章 / 标题必填 / 空小节丢弃 / 去重 / ≤30。

    - `pages` 越界（出现本章页范围外的页号）→ **该小节 pages 清空**并记 warning；
    - `title` 空 → 丢弃；
    - `why/core/lecture/pitfalls/self_check` 全空 → 视为空小节丢弃；
    - 同名小节（归一化后）只留第一个；最多 `MAX_SUBSECTIONS` 个；
    - **不采信模型给的 chunk_ids**：出处由管线按 pages 从本章材料里自己算。
    """
    pages_ok = {int(p) for p in (pages_all or [])}
    by_page = {int(p): sorted({int(c) for c in (cover_map.get(int(p)) or [])})
               for p in (pages_all or [])}
    out, seen = [], set()
    for i, r in enumerate(raw or [], 1):
        if not isinstance(r, dict):
            warnings.append("小节 %d 不是对象，已丢弃" % i)
            continue
        title = str(r.get("title") or r.get("name") or "").strip()
        if not title:
            warnings.append("小节 %d 没有 title，已丢弃" % i)
            continue
        key = kp_build._norm_term_key(title)
        if key in seen:
            warnings.append("小节「%s」重复，已去重" % title[:20])
            continue
        raw_pages = r.get("pages")
        if isinstance(raw_pages, (int, str)):
            raw_pages = [raw_pages]
        pages, bad = [], []
        for x in (raw_pages or []):
            try:
                n = int(str(x).strip())
            except (TypeError, ValueError):
                bad.append(str(x)[:10])
                continue
            if n in pages_ok:
                if n not in pages:
                    pages.append(n)
            else:
                bad.append(n)
        if bad:
            warnings.append("小节「%s」页码不在本章范围（%s）→ pages 已清空"
                            % (title[:20], bad[:5]))
            pages = []
        pages.sort()
        core = []
        for c in (r.get("core") or []):
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or c.get("term") or "").strip()
            if not name:
                continue
            core.append({"name": name,
                         "explain": str(c.get("explain") or c.get("detail") or "").strip()})
        pitfalls = _list_str(r.get("pitfalls") or r.get("易错点"))
        self_check = []
        for q in (r.get("self_check") or r.get("questions") or []):
            if isinstance(q, dict):
                qq = str(q.get("q") or q.get("question") or "").strip()
                aa = str(q.get("a") or q.get("answer") or "").strip()
                if qq:
                    self_check.append({"q": qq, "a": aa})
            elif str(q or "").strip():
                self_check.append({"q": str(q).strip(), "a": ""})
        why = str(r.get("why") or r.get("position") or "").strip()
        lecture = str(r.get("lecture") or r.get("how_teacher_teaches") or "").strip()
        if not any([why, core, lecture, pitfalls, self_check]):
            warnings.append("小节「%s」没有任何内容字段（why/core/lecture/…），已丢弃"
                            % title[:20])
            continue
        ids = []
        for p in pages:
            ids.extend(by_page.get(p) or [])
        out.append({
            "no": len(out) + 1, "title": title, "pages": pages,
            "page_label": kp_build._pages_label(pages),
            "why": why, "core": core, "lecture": lecture,
            "pitfalls": pitfalls, "self_check": self_check,
            "chunk_ids": sorted(set(ids)),
            "material_block_ids": sorted(set(ids)),
        })
        seen.add(key)
        if len(out) >= MAX_SUBSECTIONS:
            warnings.append("小节超过 %d 个，只保留前 %d 个"
                            % (MAX_SUBSECTIONS, MAX_SUBSECTIONS))
            break
    if out and not (EXPECT_SUBSECTIONS[0] <= len(out) <= EXPECT_SUBSECTIONS[1]):
        warnings.append("小节数 %d 不在提示词建议的 %d~%d 个范围内（不阻断，仅提示）"
                        % (len(out), EXPECT_SUBSECTIONS[0], EXPECT_SUBSECTIONS[1]))
    return out


def _subsections_from_key_points(points, pages_all, cover_map, sections, warnings) -> list:
    """**旧 schema 兜底**：模型只给了 `key_points` 时，按 section/pages 归组成小节。"""
    pages_ok = {int(p) for p in (pages_all or [])}
    by_page = {int(p): sorted({int(c) for c in (cover_map.get(int(p)) or [])})
               for p in (pages_all or [])}
    groups: dict = {}
    order: list = []
    for r in (points or []):
        if not isinstance(r, dict):
            continue
        name = str(r.get("point") or r.get("name") or r.get("title") or "").strip()
        if not name:
            continue
        raw_pages = r.get("pages")
        if isinstance(raw_pages, (int, str)):
            raw_pages = [raw_pages]
        pages = []
        for x in (raw_pages or []):
            try:
                n = int(str(x).strip())
            except (TypeError, ValueError):
                continue
            if n in pages_ok and n not in pages:
                pages.append(n)
        pages.sort()
        sec_no = 0
        try:
            sec_no = int(str(r.get("section") if r.get("section") is not None
                             else r.get("section_index") or "0").strip())
        except (TypeError, ValueError):
            sec_no = 0
        gkey = ("sec", sec_no) if sec_no else ("page", pages[0] if pages else 0)
        if gkey not in groups:
            groups[gkey] = {"title": "", "pages": [], "core": []}
            order.append(gkey)
        g = groups[gkey]
        g["core"].append({"name": name,
                          "explain": str(r.get("detail") or r.get("desc") or "").strip()})
        g["pages"] = sorted(set(g["pages"]) | set(pages))
    by_sec = {int(s.get("index") or 0): s for s in (sections or [])
              if isinstance(s, dict)}
    out = []
    for gkey in order:
        g = groups[gkey]
        sec = by_sec.get(int(gkey[1])) if gkey[0] == "sec" else None
        title = str((sec or {}).get("title") or "").strip()
        if not title:
            title = "小节 %d" % (len(out) + 1)
        ids = []
        for p in g["pages"]:
            ids.extend(by_page.get(p) or [])
        out.append({"no": len(out) + 1, "title": title, "pages": g["pages"],
                    "page_label": kp_build._pages_label(g["pages"]),
                    "why": "", "core": g["core"], "lecture": "",
                    "pitfalls": [], "self_check": [],
                    "chunk_ids": sorted(set(ids)),
                    "material_block_ids": sorted(set(ids)),
                    "from_legacy_key_points": True})
        if len(out) >= MAX_SUBSECTIONS:
            break
    if out:
        warnings.append("模型给的是旧 schema key_points（没有 subsections）→ 已按"
                        " section/页码归组兜底生成 %d 个小节（why/lecture/pitfalls 为空）"
                        % len(out))
    return out


def _build_summary_v2(raw: dict, material: str) -> dict:
    """章级 summary（前端查看器顶部）：overview / study_order / difficulties /
    prerequisites / exam_focus（+ 旧字段兼容 questions）。"""
    raw = raw if isinstance(raw, dict) else {}
    overview = str(raw.get("overview") or raw.get("summary") or "").strip()
    if len(overview) > 2000:
        overview = overview[:2000].rstrip() + "…"
    return {
        "overview": overview,
        "study_order": _list_str(raw.get("study_order") or raw.get("order")),
        "difficulties": _list_str(raw.get("difficulties") or raw.get("难点")),
        "prerequisites": _list_str(raw.get("prerequisites") or raw.get("先修")),
        "exam_focus": _list_str(raw.get("exam_focus") or raw.get("考点")),
        "questions": _list_str(raw.get("questions") or raw.get("自测题")),
        "material_chars": len(material),
    }


def _v2_meta(doc_id, info, est, prompt, calls, *, started, status, err, inv,
             render_stats, sec, subs, terms, warnings, material_sha, run_no,
             sections_source, parser, prev_backup, skipped=False,
             run_context=None) -> dict:
    """产物 meta（前端查看器 + 验收都要用；字段见任务契约）。"""
    agg = kp_build._v2_cost(calls)
    run_agg = kp_build._v2_cost(calls, run=run_no)
    ctx = (run_context if run_context is not None
           and runtime_context.runtime_enabled() else None)
    if ctx is not None:
        meta_model = str(ctx.model)
        meta_think = bool(ctx.thinking)
        meta_effort = str(ctx.reasoning_effort or "")
        meta_cap = int(ctx.max_tokens or 0)
    else:
        meta_model, meta_think = model_name(), thinking_on()
        meta_effort, meta_cap = effort(), max_tokens()
    return {
        "preview_version": PREVIEW_VERSION,
        "preview_schema": PREVIEW_SCHEMA,
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
        "status": status, "error": err,
        "model": meta_model, "thinking": meta_think,
        "reasoning_effort": meta_effort, "max_tokens": meta_cap,
        "prompt_path": prompt.get("path") or "",
        "prompt_sha256": prompt.get("sha256") or "",
        "usage_this_run": {"calls": run_agg["calls"],
                           "calls_failed": run_agg["calls_failed"],
                           "tokens_in": run_agg["tokens_in"],
                           "tokens_out": run_agg["tokens_out"],
                           "reasoning_tokens": run_agg["reasoning_tokens"]},
        "usage_total": {"calls": agg["calls"], "calls_failed": agg["calls_failed"],
                        "tokens_in": agg["tokens_in"], "tokens_out": agg["tokens_out"],
                        "reasoning_tokens": agg["reasoning_tokens"]},
        "cost_cny": run_agg["cost_cny"],
        "cost_cny_all_calls": agg["cost_cny"],
        "n_subsections": len(subs or []),
        "n_terms": len(terms or []),
        "sections_source": sections_source,   # artifact | generated | none
        "parser": parser,                    # v2_subsections | legacy_key_points
        "warnings": list(warnings or []),
        "created_at": time.time(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_no": int(run_no), "calls": calls, "runs": kp_build._v2_runs(calls),
        "params": {"thinking": meta_think, "reasoning_effort": meta_effort,
                   "temperature": kp_build.TEMPERATURE,
                   "max_tokens": meta_cap, "calls_per_chapter": 1,
                   "max_subsections": MAX_SUBSECTIONS,
                   "expect_subsections": list(EXPECT_SUBSECTIONS)},
        "inputs": {"read": ["chunks.id", "chunks.ord", "chunks.source_ref",
                            "chunks.text", "chunks.heading_path",
                            "figure_desc.no_info", "documents.title"],
                   "not_read": ["chunks.topic"], "db_writes": [],
                   "sections_artifact": str(sections_out_file(doc_id)),
                   "material_sha256": material_sha},
        "material": {"chars": int(render_stats.get("chars") or 0),
                     "mode": render_stats.get("mode") or "",
                     "dropped_fig_blocks": int(render_stats.get("dropped_fig_blocks") or 0),
                     "truncated_pages": list(render_stats.get("truncated_pages") or []),
                     "n_sources": len(inv)},
        "sections_regex": {"read": bool(sec.get("read")),
                           "source": sec.get("source") or "",
                           "n_sections": int(sec.get("n_sections") or 0),
                           "fallback": bool(sec.get("fallback")),
                           "note": sec.get("note") or "",
                           "cost": "0 次模型调用（纯正则/本地生成）"},
        "skipped": bool(skipped), "prev_backup": prev_backup,
        "elapsed_s": round(time.perf_counter() - started, 1),
        "pipeline": ("C-0110 预习总结 v2（独立管线分区，core/preview.py::"
                     "build_chapter_preview）：按章取块（0 成本）→ 缺《章节细目》时"
                     "core.sections 纯本地生成（0 模型调用）→ 整章 **1 次**调用"
                     "（%s / thinking=%s / reasoning_effort=%s）→ 新提示词要求先细分"
                     "小节再逐小节写 → 原子落盘**按章**产物。"
                     % (model_name(), thinking_on(), effort())),
    }


def build_chapter_preview(doc_id: int, chapter, *, prompt_path=None, out_path=None,
                          db_path=None, dry_run: bool = False, force: bool = False,
                          ignore_switch: bool = False, log=print,
                          on_progress=None) -> dict:
    """**按章预习总结（v2，2026-10-20）**：1 次模型调用，先细分子节再逐节写。

    流程：dry-run（纯预估，0 写入）→ 开关校验 → 缺《章节细目》时本地生成（0 模型）
    → 整章材料 + 节级大纲 → **1 次**调用 → 解析/校验新 schema（pages 越界清空、
    空小节丢弃、去重、≤30 节；旧 key_points 兜底）→ 原子写
    `logs/knowledge/<doc_id>-预习总结-第<N>章.json`（同章重跑先备份 `.bak-<ts>`，
    换章不覆盖其它章）。

    返回 `{"ok", "status", "error", "out_path", "meta", "estimate"}`；
    与旧 `build_preview()` 的调用方兼容（旧名已改为转调本函数）。
    """
    started = time.perf_counter()
    if dry_run:
        est = estimate_preview(doc_id, chapter, prompt_path=prompt_path,
                               out_path=out_path, db_path=db_path)
        if not est.get("ok"):
            return {"ok": False, "error": est.get("error"), "dry_run": True,
                    "doc_id": int(doc_id), "chapter": str(chapter)}
        return {"ok": True, "dry_run": True, **est}

    # ★ 开关：关着时**拒绝**并给可操作提示（默认已开启，仍可手动关闭）
    st = enabled_state()
    if not st["enabled"] and not ignore_switch:
        return {"ok": False, "error": "预习功能未开启", "doc_id": int(doc_id),
                "chapter": str(chapter), "enabled": False,
                "hint": st["hint"], "switch": st["switch"]}

    # ★ 先确保《章节细目》（本地正则，0 模型调用；dry-run 不会走到这里）
    # P0-C：同一任务入口只建一次 context；PIPELINE_RUNTIME=0 不建（旧路径）。
    run_ctx = (runtime_context.make_context("preview", doc_id=doc_id)
               if runtime_context.runtime_enabled() else None)
    sec_build = ensure_sections_artifact(doc_id, db_path=db_path,
                                         run_context=run_ctx)
    est = estimate_preview(doc_id, chapter, prompt_path=prompt_path,
                           out_path=out_path, db_path=db_path)
    if not est.get("ok"):
        return {"ok": False, "error": est.get("error"), "doc_id": int(doc_id),
                "chapter": str(chapter)}
    if run_ctx is not None:
        est = dict(est)
        est.update({"model": str(run_ctx.model),
                    "thinking": bool(run_ctx.thinking),
                    "reasoning_effort": str(run_ctx.reasoning_effort or ""),
                    "max_tokens": int(run_ctx.max_tokens or 0)})
    out = Path(est["out_path"])

    if est.get("skip_would_happen") and not force:
        log("预习总结：第 %s 章产物已是最新（材料 sha256 未变）→ 跳过，0 次调用 0 花费"
            % est["chapter"])
        log("产物：%s" % out)
        return {"ok": True, "status": "skipped", "out_path": str(out),
                "meta": (kp_build._load_prev(out).get("meta") or {}),
                "estimate": est, "sections_build": sec_build}

    _stage(0, "读取第 %s 章" % est["chapter"], on_progress)
    prompt = load_preview_prompts(prompt_path)
    ch, info, material, inv, stats = read_chapter_ch(doc_id, chapter, db_path=db_path)
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error"), "doc_id": int(doc_id)}
    sec = chapter_sections(doc_id, info)
    outline = render_sections_outline(sec)
    material_sha = hashlib.sha256((material + "\n" + outline).encode("utf-8")).hexdigest()
    sections_source = ("none" if int(sec.get("n_sections") or 0) <= 0 else
                       ("generated" if sec_build.get("created") else "artifact"))

    calls: list = []
    prev_meta = kp_build._load_prev(out).get("meta") or {}
    if not force and int(prev_meta.get("chapter_no") or 0) == int(info.get("no") or 0):
        for _c in (prev_meta.get("calls") or []):
            if isinstance(_c, dict):
                _c = dict(_c)
                _c.setdefault("run", 1)
                calls.append(_c)
    run_no = max([int(c.get("run") or 1) for c in calls] or [0]) + 1
    if int(sec.get("n_sections") or 0) <= 0:
        log("  ⚠ 本章没有可用的《章节细目》小节（%s）→ 小节由模型按内容逻辑自拟"
            % (sec_build.get("note") or "无产物"))

    # 同章重跑：先备份旧产物（不同章是不同文件，天然互不覆盖）
    prev_backup = ""
    if out.exists():
        bak = out.with_name(out.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        try:
            shutil.copy2(out, bak)
            prev_backup = str(bak)
        except OSError as e:
            log("  ⚠ 旧产物备份失败：%s（仍继续写新产物）" % e)

    log("预习总结 v2（按章）：%s（doc#%d）· %s · %d 页 / %d 块 · 材料 %d 字符"
        % (est.get("title") or "", doc_id, est["chapter"], est["n_pages"],
           est["n_blocks"], len(material)))
    log("  ├ 节级细目：%d 节（%s，0 次模型调用）"
        % (sec.get("n_sections") or 0,
           {"artifact": "复用已有《章节细目》", "generated": "本地正则新生成",
            "none": "没有可用细目 → 小节自拟"}.get(sections_source, "")))
    log("  └ 调用：**整章 1 次**（%s / thinking=%s / reasoning_effort=%s / max_tokens=%d）"
        % (est["model"], est["thinking"], est["reasoning_effort"], est["max_tokens"]))

    status, err = "done", ""
    raw: dict = {}
    subs: list = []
    terms: list = []
    warnings: list = []
    parser = "v2_subsections"
    _stage(1, "渲染整章材料（%d 字符）" % len(material), on_progress)
    _stage(2, "预习总结（1 次调用，先细分小节）", on_progress)
    try:
        user = (prompt["user"].replace("{deck}", est.get("title") or "")
                .replace("{chapter}", est["chapter"])
                .replace("{heading_path}", info.get("heading_path") or "")
                .replace("{n_pages}", str(est["n_pages"]))
                .replace("{page_label}", est.get("page_label") or "")
                .replace("{sections_outline}", outline)
                .replace("{chapter_material}", material))
        res = _preview_call(calls, "preview.chapter", prompt["sys"], user,
                            max_tokens=est["max_tokens"], retry=2, log=log,
                            note="第 %s 章预习（p.%s）" % (est["chapter"],
                                                          est.get("page_label") or ""),
                            run_no=run_no, thinking=est["thinking"],
                            reasoning_effort=est["reasoning_effort"],
                            model=est["model"], run_context=run_ctx)
        if not res["ok"]:
            raise RuntimeError("预习总结调用失败：%s" % res["error"])
        raw = res["parsed"] if isinstance(res["parsed"], dict) else {}
        subs = _clean_subsections(raw.get("subsections") or [],
                                  list(ch.get("pages_all") or []), dict(ch["cover"]),
                                  material, warnings)
        if not subs and (raw.get("key_points") or raw.get("points")):
            parser = "legacy_key_points"
            subs = _subsections_from_key_points(
                raw.get("key_points") or raw.get("points") or [],
                list(ch.get("pages_all") or []), dict(ch["cover"]),
                sec.get("sections") or [], warnings)
        terms = _clean_terms_list(raw.get("key_terms") or raw.get("terms") or [], material)
        log("  ├ 小节：模型 %d 个 → 校验后 %d 个（parser=%s，丢弃/清空见 warnings）"
            % (len(raw.get("subsections") or []), len(subs), parser))
        log("  └ 关键名词 %d 个 · 章级 overview %d 字"
            % (len(terms), len(str(raw.get("overview") or ""))))
        if not subs:
            warnings.append("模型没有给出可用小节（也没有旧 key_points 可兜底）——"
                            "产物会带 status=done 但 subsections 为空，可 force 重跑")
    except Exception as e:  # noqa: BLE001
        status, err = "failed", "%s: %s" % (type(e).__name__, e)
        log("★预习总结中止：%s" % err)

    summary = _build_summary_v2(raw, material)
    meta = _v2_meta(doc_id, info, est, prompt, calls, started=started, status=status,
                    err=err, inv=inv, render_stats=stats, sec=sec, subs=subs,
                    terms=terms, warnings=warnings, material_sha=material_sha,
                    run_no=run_no, sections_source=sections_source, parser=parser,
                    prev_backup=prev_backup, run_context=run_ctx)
    artifact = {
        "meta": meta,
        "chapter": {"no": int(info.get("no") or 0), "name": info.get("name") or "",
                    "heading_path": info.get("heading_path") or "",
                    "page_start": meta["page_start"], "page_end": meta["page_end"],
                    "page_label": meta["page_label"], "pages": list(meta["pages"]),
                    "n_blocks": meta["n_blocks"],
                    "chapter_source": meta["chapter_source"]},
        "summary": summary,
        "subsections": subs,
        "terms": terms,
        "sources": _sources(inv),
    }
    _stage(3, "写入产物", on_progress)
    kp_build._save(artifact, out)
    log("完成：状态 %s · 小节 %d 个 · 名词 %d 个 · %d 次调用 · 本次 CNY%.6f"
        % (status, len(subs), len(terms), meta["usage_this_run"]["calls"],
           meta["cost_cny"]))
    log("产物（按章）：%s" % out)
    return {"ok": True, "status": status, "error": err, "out_path": str(out),
            "meta": meta, "estimate": est, "sections_build": sec_build,
            "artifact": {"chapter": artifact["chapter"], "summary": summary,
                         "n_subsections": len(subs), "n_terms": len(terms)}}


def preview_status(doc_id: int) -> dict:
    """扫这份教材的**按章**预习产物（+ 旧单文件只读兼容），零模型、零写入。

    返回 `{chapter_no(int): {exists,path,status,generated_at,cost_cny,
    n_subsections,chapter_name,pages,preview_version,is_current_version}}`；
    另带 `_schema` / `_old_single` 两个下划线键（不会被章号误用）。
    """
    d = config.LOG_DIR / "knowledge"
    out: dict = {"_schema": PREVIEW_SCHEMA, "_preview_version": PREVIEW_VERSION}
    pat = re.compile(r"^%d-预习总结-第(\d+)章\.json$" % int(doc_id))
    try:
        files = sorted(d.glob("%d-预习总结-第*章.json" % int(doc_id)))
    except OSError:
        files = []
    for p in files:
        m = pat.match(p.name)
        if not m:
            continue
        no = int(m.group(1))
        data = kp_build._load_prev(p)
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        subs = data.get("subsections") if isinstance(data.get("subsections"), list) else []
        out[no] = {
            "exists": True, "path": str(p),
            "status": str(meta.get("status") or ""),
            "error": str(meta.get("error") or "")[:400],
            "generated_at": str(meta.get("generated_at") or ""),
            "created_at": meta.get("created_at"),
            "cost_cny": float(meta.get("cost_cny") or 0.0),
            "n_subsections": len(subs),
            "chapter_name": str(meta.get("chapter_name") or ""),
            "pages": list(meta.get("pages") or []),
            "page_label": str(meta.get("page_label") or ""),
            "preview_version": str(meta.get("preview_version") or ""),
            "is_current_version": (str(meta.get("preview_version") or "")
                                   == PREVIEW_VERSION),
            "sections_source": str(meta.get("sections_source") or ""),
        }
    legacy = legacy_preview_out_file(doc_id)
    if legacy.exists():
        data = kp_build._load_prev(legacy)
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        no = int(meta.get("chapter_no") or 0)
        out["_old_single"] = {
            "exists": True, "path": str(legacy), "chapter_no": no,
            "status": str(meta.get("status") or ""),
            "preview_version": str(meta.get("preview_version") or ""),
            "is_current_version": (str(meta.get("preview_version") or "")
                                   == PREVIEW_VERSION),
            "generated_at": str(meta.get("generated_at") or ""),
            "cost_cny": float(meta.get("cost_cny") or 0.0),
            "migrated": no in out,
        }
    return out


def read_preview_artifact(doc_id: int, chapter) -> dict:
    """读一章的预习产物（前端查看器用；零模型、零写入）。

    返回 `{"ok", "artifact": {meta, chapter, summary, subsections, terms, sources},
    "path", "legacy", "is_current_version"}`；找不到 → `{"ok": False, "error"}`。
    旧单文件（C-0094-v1）若章号匹配也会被读取，并在 `legacy=True` + warning 里标出来。
    """
    want = _norm_chapter_arg(chapter)
    no = int(want.get("no") or 0)
    if not no:
        info = locate_chapter(doc_id, chapter)
        if not info.get("ok"):
            return {"ok": False, "error": info.get("error") or "找不到这一章",
                    "doc_id": int(doc_id), "chapter": str(chapter)}
        no = int(info.get("no") or 0)
    path = preview_out_file_per_chapter(doc_id, no)
    legacy_used = False
    if not path.exists():
        legacy = legacy_preview_out_file(doc_id)
        data = kp_build._load_prev(legacy)
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        if data and int(meta.get("chapter_no") or 0) == no:
            path, legacy_used = legacy, True
    data = kp_build._load_prev(path)
    if not data:
        return {"ok": False, "error": "没有第 %d 章的预习产物（请先生成）" % no,
                "doc_id": int(doc_id), "chapter_no": no,
                "expected_path": str(preview_out_file_per_chapter(doc_id, no))}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    version = str(meta.get("preview_version") or "")
    is_current = version == PREVIEW_VERSION
    if is_current or isinstance(data.get("subsections"), list):
        artifact = {
            "meta": meta,
            "chapter": data.get("chapter") if isinstance(data.get("chapter"), dict) else {},
            "summary": data.get("summary") if isinstance(data.get("summary"),
                                                          dict) else {},
            "subsections": data.get("subsections")
            if isinstance(data.get("subsections"), list) else [],
            "terms": data.get("terms") if isinstance(data.get("terms"), list) else [],
            "sources": data.get("sources") if isinstance(data.get("sources"), list) else [],
        }
    else:
        # 旧产物（v1）：summary.key_points + sections[].key_points → 转成查看器可读的 subsections
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        sections = data.get("sections") if isinstance(data.get("sections"), list) else []
        subs = []
        warns = ["这是旧版（%s）产物，已按查看器结构做只读转换；"
                 "重新生成后会写入 v2 按章产物" % (version or "v1")]
        for s in sections:
            if not isinstance(s, dict):
                continue
            pts = [p for p in (s.get("key_points") or []) if isinstance(p, dict)]
            if not pts:
                continue
            subs.append({
                "no": len(subs) + 1,
                "title": str(s.get("title") or ("小节 %d" % (len(subs) + 1))),
                "pages": list(s.get("pages") or []),
                "page_label": kp_build._pages_label(s.get("pages") or []),
                "why": "", "lecture": "", "pitfalls": [], "self_check": [],
                "core": [{"name": str(p.get("point") or ""),
                          "explain": str(p.get("detail") or "")} for p in pts],
                "chunk_ids": sorted({c for p in pts for c in (p.get("chunk_ids") or [])}),
            })
        if not subs:
            subs = _subsections_from_key_points(
                summary.get("key_points") or [], meta.get("pages") or [],
                {}, sections, warns)
        artifact = {
            "meta": dict(meta, legacy=True,
                         warnings=list(meta.get("warnings") or []) + warns),
            "chapter": data.get("chapter") if isinstance(data.get("chapter"),
                                                         dict) else {},
            "summary": summary,
            "subsections": subs,
            "terms": summary.get("key_terms") if isinstance(
                summary.get("key_terms"), list) else [],
            "sources": data.get("sources") if isinstance(data.get("sources"), list) else [],
        }
    return {"ok": True, "artifact": artifact, "path": str(path),
            "legacy": bool(legacy_used or not is_current),
            "is_current_version": bool(is_current)}


# ---------------- 章节目录/材料缓存（P1-A / S08） ----------------
_PREVIEW_CACHE_LOCK = threading.RLock()
_CHAPTER_LIST_CACHE: dict = {}
_CHAPTER_MATERIAL_CACHE: dict = {}
_PREVIEW_CACHE_MAX = 64


def clear_preview_caches() -> None:
    with _PREVIEW_CACHE_LOCK:
        _CHAPTER_LIST_CACHE.clear()
        _CHAPTER_MATERIAL_CACHE.clear()


def _cache_get(cache: dict, key):
    with _PREVIEW_CACHE_LOCK:
        val = cache.get(key)
        return copy.deepcopy(val) if val is not None else None


def _cache_put(cache: dict, key, val) -> None:
    with _PREVIEW_CACHE_LOCK:
        cache[key] = copy.deepcopy(val)
        while len(cache) > _PREVIEW_CACHE_MAX:
            cache.pop(next(iter(cache)))


def _file_signature(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:24]
    except OSError:
        return "0"


def _db_revision(db_path: Path) -> int:
    try:
        if not db_path.exists():
            return 0
        conn = sqlite3.connect("file:" + db_path.as_posix() + "?mode=ro", uri=True)
        try:
            return int(db_mod.data_revision(conn))
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return 0


def _db_revision_conn(conn) -> int:
    """从**已打开的快照连接**读 data_revision（GPT-G：不要另开连接读版本）。"""
    try:
        return int(db_mod.data_revision(conn))
    except (sqlite3.Error, AttributeError, TypeError, ValueError):
        return 0


def _chapter_block_sig(db_path: Path, block_ids, _conn=None) -> str:
    ids = sorted({int(x) for x in (block_ids or [])})
    if not ids:
        return ""
    own = _conn is None
    if own:
        try:
            conn = sqlite3.connect("file:" + db_path.as_posix() + "?mode=ro", uri=True)
        except (sqlite3.Error, OSError):
            return ""
    else:
        conn = _conn
    try:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            "SELECT id, COALESCE(content_hash, '') FROM chunks WHERE id IN (%s)"
            % marks, ids).fetchall()
    except (sqlite3.Error, OSError):
        return ""
    finally:
        if own:
            conn.close()
    rows = sorted((int(a), str(b or "")) for a, b in rows)
    return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()[:24]


def list_chapters(doc_id: int, *, db_path: str | os.PathLike | None = None,
                  _conn=None) -> dict:
    """带进程内缓存的章节目录；键含 db_path+doc+data_revision+sections 产物签名。

    `_conn`：内部参数（公共 API 兼容）。给定时，revision 与章节行都从**同一条只读
    连接/事务快照**读取（GPT-G：不另开连接读版本），函数不关闭它。
    """
    db = Path(db_path) if db_path else Path(config.DB_PATH)
    rev = _db_revision_conn(_conn) if _conn is not None else _db_revision(db)
    key = None
    if rev > 0:
        key = (str(db.resolve()), int(doc_id), int(rev),
               _file_signature(sections_out_file(doc_id)))
        hit = _cache_get(_CHAPTER_LIST_CACHE, key)
        if hit is not None:
            return hit
    result = _list_chapters_uncached(doc_id, db_path=db_path, _conn=_conn)
    if key is not None and result.get("ok"):
        _cache_put(_CHAPTER_LIST_CACHE, key, result)
    return result


def read_chapter_ch(doc_id: int, chapter, *,
                    db_path: str | os.PathLike | None = None) -> tuple:
    """带章节材料缓存；键含 doc/chapter/data_revision/本章 block ids+content hash。

    GPT-G（2026-11-01）：**定位（locate/list）与正文读取共享同一个只读事务快照**。
    以前 `locate_chapter` 与 `read_text_pages_by_ids` 各开一条连接：若两步之间别的
    进程重入库提交，会读到"章节目录是旧的、正文是新的"的半章（甚至静默缺块）。
    现在先开一条只读连接并 `BEGIN`（WAL 下固定读快照），在该快照里读 revision、
    定位、算 block 指纹、读正文；读完关闭（隐式回滚）。因此不会出现 ok=True 的半章，
    也不需要"读到一半发现变了再重试"（重试可能追加付费，本项目不做）。

    返回 deepcopy，调用方改返回值不会污染缓存；rev=0/无块时不缓存（安全回退）。
    """
    db = Path(db_path) if db_path else Path(config.DB_PATH)
    conn = None
    try:
        conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
        except sqlite3.Error:
            pass
        rev = _db_revision_conn(conn)
        info = locate_chapter(doc_id, chapter, db_path=db_path, _conn=conn)
        key = None
        if rev > 0 and info.get("ok") and info.get("block_ids"):
            key = (str(db.resolve()), int(doc_id), int(info.get("no") or 0),
                   int(rev), _chapter_block_sig(db, info.get("block_ids"), _conn=conn))
            hit = _cache_get(_CHAPTER_MATERIAL_CACHE, key)
            if hit is not None:
                return hit
        result = _read_chapter_ch_uncached(doc_id, chapter, db_path=db_path,
                                           _conn=conn)
        if key is not None and result and isinstance(result, tuple) and result[2]:
            _cache_put(_CHAPTER_MATERIAL_CACHE, key, result)
        return result
    finally:
        if conn is not None:
            conn.close()
