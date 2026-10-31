# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
# File-ID: SA-21-rebuilt   (project study_agent; keep this line)
"""学习看板：零依赖本地 Web UI。

⚠ **本文件是 2026-10-14 硬盘故障后的重建版**（C-0103 合成）：
  · 函数体由 `ai_decompile.py` 依据 v1.1 的 `dashboard.pyc` 字节码逐函数重建
    （169 个函数分块，成本约 ¥0.50）；
  · 头部 / import 区 / 控制台编码段 / Handler 的 5 个基础方法与 `do_GET`
    后半段路由，逐字取自恢复版（带完整中文注释）；
  · `INDEX_HTML` 的原始模板取自字节码 `co_consts`，现拆成 core/frontend 资源；
  · 8 个在恢复版里已丢失的模块级常量，值取自字节码真值（见下方标注）；
  · 与原文件的差异：函数体内**注释大量减少**（字节码不含注释）；
    `do_POST` 尾部若干分支（如 `/api/doc/delete`）未还原，见该处 TODO。

设计取舍（原注释保留）：
- 只用 Python 标准库 http.server，不引入 Flask/FastAPI，也不引入任何前端框架。
- 前端只做展示与交互，全部数据来自已有的 core 模块。
- 默认只绑定 127.0.0.1，不对外暴露。
"""
from __future__ import annotations

import datetime
import json
import mimetypes
import email
import email.policy
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import webbrowser
import zipfile                    # 卡 C-0080：只读 zip 中央目录（看包里有什么）
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


# ---------------------------------------------------------------- 控制台编码
# 卡 C-0078：把 stdout / stderr 配成"任何字符都打印得出来"。
#
# ★ 为什么必须在**入口**做（真实缺陷，不是防御性编程）：
#   `core/kp_build.py` 的 `log` 默认值就是 `print`（约 14 处签名写 `log=print`），
#   而它打的成本串里带 U+00A5（日圆符号，GBK 编不出）。exe 的 stdout 是
#   **GBK**，而 GBK **编不出** U+00A5 ⇒ `print` 当场抛
#   `UnicodeEncodeError: 'gbk' codec can't encode character '\xa5'`。
#   该异常被上层捕获后把产物 `meta.status` 写成了 `failed` —— **数据全在，
#   坏的是状态标记**，可用户每次点「AI 知识点总结」都看到"失败"。
#
# ★ 两种环境都要照顾，且**不改变源码模式下已有的正常行为**：
#   · 输出被重定向 / 被父进程管道捕获（exe 被启动器或验收脚本拉起时正是这种，
#     也正是上面那个 GBK 现场）→ 写 **UTF-8**：任何字符都编得出，
#     下游按 UTF-8 读也不会乱码；
#   · 交互终端 → **保持系统给的编码**，只把 `errors` 从 strict 换成 `replace`。
#     源码模式 `看板.cmd` 本来就带 `-X utf8`；Windows 控制台是宽字符通道，
#     Python 3.6+ 的 `stdout.encoding` 已是 utf-8。这里不硬改成 UTF-8，
#     免得真落在 GBK 代码页的窗口里把中文写成乱码。
#   两条路的共同点：**绝不再因为一个字符编不出来而抛异常**，业务状态不受污染。
def _setup_console_encoding() -> None:
    for _name in ("stdout", "stderr"):
        _st = getattr(sys, _name, None)
        if _st is None:
            continue
        try:
            if _st.isatty():
                _st.reconfigure(errors="replace")
            else:
                _st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                       # noqa: BLE001
            try:
                _st.reconfigure(errors="replace")
            except Exception:                   # noqa: BLE001
                pass


_setup_console_encoding()


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, db, extract, ingest as ingest_mod  # noqa: E402
from core import outline as outline_mod  # noqa: E402
from core import kp_build as kp_build_mod  # noqa: E402
from core import textbook_kp as textbook_kp_mod  # noqa: E402
from core import kp_batch as kp_batch_mod  # noqa: E402
from core import pipeline_pool as pool_mod  # noqa: E402
from core import progress as progress_mod  # noqa: E402
from core import search as search_mod, study  # noqa: E402
from core import share as share_mod  # noqa: E402（卡 C-0080：课件分享包）
from core import subjects as subjects_mod  # noqa: E402（卡 C-0039：多学科）

_lock = threading.Lock()


# ====================================================================
# 模块级常量（取自恢复版；每个都经过 compile() 完整性验证）
# ====================================================================

_INGEST_LOCK = threading.Lock()
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
INGEST_TIMEOUT_S = 1800
TEST_HOOKS: dict = {}
_SCHEMA_PROBE_TABLES = ("meta", "documents", "chunks", "embeddings")
_CORE_FILES = ("ingest.py", "outline.py", "chunk.py", "search.py",
               "figures.py", "extract.py", "tables.py", "db.py", "config.py")
_CODE_STAMP: float | None = None
_MINDMAP_MISSING = "该文档没有结构数据（可能入库时未做全局结构分析）"
_KNOWLEDGE_DIR_ENV = "KB_KNOWLEDGE_DIR"
_KNOWLEDGE_LOCK = threading.Lock()
_KNOWLEDGE_CACHE: dict = {}
_KNOWLEDGE_CACHE_MAX = 4
_KNOWLEDGE_DIR_ENV = "KB_KNOWLEDGE_DIR"
_KNOWLEDGE_LOCK = threading.Lock()
_KNOWLEDGE_CACHE: dict = {}
_KNOWLEDGE_CACHE_MAX = 4
_LKP_SUFFIX = "-末级知识点总结.json"
_LKP_LOCK = threading.Lock()
_LKP_CACHE: dict = {}
_LKP_CACHE_MAX = 4
_ARTIFACT_V2 = "C-0049-v2"
_ARTIFACT_NONE = "none"              # 还没整理过（产物文件都不存在）
_ARTIFACT_LEGACY = "legacy"          # 有产物但是旧格式 → 页签不显示
_ARTIFACT_INCOMPLETE = "incomplete"  # v2 但结构缺失（上次整理中断）
_ARTIFACT_V2_STATE = "v2"            # 可用：结构 + 知识点齐备
_LKP_SUFFIX = "-末级知识点总结.json"
_LKP_LOCK = threading.Lock()
_LKP_CACHE: dict = {}
_LKP_CACHE_MAX = 4
_ARTIFACT_V2 = "C-0049-v2"
_ARTIFACT_NONE = "none"              # 还没整理过（产物文件都不存在）
_ARTIFACT_LEGACY = "legacy"          # 有产物但是旧格式 → 页签不显示
_ARTIFACT_INCOMPLETE = "incomplete"  # v2 但结构缺失（上次整理中断）
_ARTIFACT_V2_STATE = "v2"            # 可用：结构 + 知识点齐备
_ARTIFACT_MESSAGE = {
    _ARTIFACT_NONE: "该课件还没有整理过知识点，点【整理知识点】生成。",
    _ARTIFACT_LEGACY: ("这份课件的产物是旧格式（入库导图派生，没有模型生成的思维导图"
                       "结构），页签不再显示。需要它就在【整理知识点】里重新整理一份；"
                       "旧文件保留、不会被删。"),
    _ARTIFACT_INCOMPLETE: "产物里没有结构树（可能上次整理中断），需要重新整理这份课件。",
}
_SLIDE_CACHE: dict = {}
_SLIDE_CACHE_MAX = 8
_SLIDE_LOCK = threading.Lock()
_REF_PAGE_RE = re.compile(r"^p\.\s*(\d+)\s*(?:-\s*(\d+)\s*)?(图)?$")
_REF_SLIDE_RE = re.compile(r"^slide\s+(\d+)\s*(?:-\s*(\d+)\s*)?$", re.I)
_REF_MAX_PAGES = 12
_KP_READABLE_VERSIONS = tuple(sorted({
    str(getattr(kp_build_mod, "PIPELINE_VERSION_V2", "") or ""),
    str(getattr(kp_build_mod, "PIPELINE_VERSION_V3", "") or ""),
    str(getattr(kp_build_mod, "PIPELINE_VERSION_V4", "") or ""),
    _ARTIFACT_V2,
} - {""}))
_KP_TERMS_IN_OWN_FILE = tuple(sorted({
    str(getattr(kp_build_mod, "PIPELINE_VERSION_V4", "") or ""),
} - {""}))
_KP_READABLE_VERSIONS = tuple(sorted({
    str(getattr(kp_build_mod, "PIPELINE_VERSION_V2", "") or ""),
    str(getattr(kp_build_mod, "PIPELINE_VERSION_V3", "") or ""),
    str(getattr(kp_build_mod, "PIPELINE_VERSION_V4", "") or ""),
    _ARTIFACT_V2,
} - {""}))
_KP_TERMS_IN_OWN_FILE = tuple(sorted({
    str(getattr(kp_build_mod, "PIPELINE_VERSION_V4", "") or ""),
} - {""}))
_KP_OPT_CACHE: dict = {"sig": None, "t": 0.0, "data": None}
_KP_OPT_TTL = 20.0   # 秒：列课件要读 40 份 outline + 真库块，别每次点击都重算
TEXTBOOK_SUBDIR = "教材"
TEXTBOOK_LOCKED_HINT = (
    "这份是教材专线产物（按章切块，chunk_scheme=textbook-v1），"
    "通用入库会用旧切块覆盖它，已跳过。"
    "要重跑请走资料栏的「教材入库」；确实想用旧切块重建，"
    "先清掉标记：UPDATE documents SET extra=json_remove(extra,'$.chunk_scheme') "
    "WHERE id=?"
)
_PAGE_REF_RE = re.compile(r"p\.(\d+)(?:\s*-\s*(\d+))?")
_SHARE_LOCK = threading.Lock()      # 导出串行：两次导出不抢同一个 .part 临时文件
_SHARE_PROG: dict = {"active": False, "stage": "", "done": 0, "total": 0,
                     "error": "", "started": 0.0, "finished": 0.0}
_SHARE_STAGE_CN = {"export": "打包课件数据", "chunks": "写切块", "embeddings": "写向量",
                   "pages": "写页图", "remove": "清理本库旧记录"}
_BUILTIN_PROMPTS = (
    ("chat_system", "知识库问答 · 系统提示词", "core/chat.py::SYSTEM_PROMPT"),
    ("chat_expand", "查询改写 · 提示词", "core/chat.py::EXPAND_PROMPT"),
    ("ingest_outline", "入库结构分析 · 提示词（头部）", "core/outline.py::PROMPT_HEAD"),
)
_BUILTIN_PROMPTS = (
    ("chat_system", "知识库问答 · 系统提示词", "core/chat.py::SYSTEM_PROMPT"),
    ("chat_expand", "查询改写 · 提示词", "core/chat.py::EXPAND_PROMPT"),
    ("ingest_outline", "入库结构分析 · 提示词（头部）", "core/outline.py::PROMPT_HEAD"),
)

# ---- 恢复版已丢失、值取自 v1.1 字节码的模块级常量 ----
MAX_NOTE_INLINE = 4000
_DECK_FILE_RE = re.compile(r'^(?:slide_\d+\.(?:png|md)|_summary\.json)(?:\.bak-.*)?$')
_DOCS_DELETE_MAX_DOCS = 200
_FIGURE_DESC_DIRNAME = 'figure_desc'
_KP_CLEAR_MAX_DOCS = 200
_KP_E1_HINTS = ('封面', '封皮', '学习目标', '掌握要求', '目录', '提纲', '致谢',
                '参考文献', 'thank you', '谢谢', '版权', '声明')
_TERMS_FIELDS = ('term', 'en', 'en_source', 'def', 'first_page', 'first_kp_id',
                 'first_kp_name', 'kp_ids', 'n_occurrences')
_WIN_BAD_CHARS = '<>:"/|?*' + chr(92)


# ====================================================================
# 前端页面（INDEX_HTML）——资源拆分至 core/frontend，运行时拼接成完整页面
# ====================================================================

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "core" / "frontend"


def _load_index_html() -> str:
    """Read the page assets once; the response stays a self-contained HTML page."""
    html = (_FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    css = (_FRONTEND_DIR / "style.css").read_text(encoding="utf-8")
    scripts = json.loads((_FRONTEND_DIR / "manifest.json").read_text(encoding="utf-8"))["scripts"]
    js = "".join((_FRONTEND_DIR / name).read_text(encoding="utf-8") for name in scripts)
    return html.replace("{{STUDY_AGENT_CSS}}", css, 1).replace(
        "{{STUDY_AGENT_JS}}", js, 1)


INDEX_HTML = _load_index_html()


# ====================================================================
# 以下 150 个顶层定义由 ai_decompile.py 从 dashboard.pyc 重建
# ====================================================================

def _schema_ready(c: sqlite3.Connection) -> bool:
    """库是不是已经建好了 —— **只读**判断（读 `sqlite_master`，不抢写锁）。

    WAL 下读快照与写者并行，所以这个探测在"入库正在密集写库"时也是亚毫秒级；
    而 `db.init_db()` 不是（它要写库，见 `_conn()` 的说明）。
    """
    row = c.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?)",
        _SCHEMA_PROBE_TABLES).fetchone()
    return int(row["n"]) == len(_SCHEMA_PROBE_TABLES)


def _conn(readonly: bool = False) -> sqlite3.Connection:
    """开一条连接，并按需把库初始化好。

    `readonly=True` 给**只读接口**用（卡 C-0041）：先做一次只读探测，库已经
    建好就直接用这条连接，**不执行 `db.init_db()`**。

    为什么必须这样：`init_db()` 里有 `executescript(SCHEMA)`（含写形式的
    `PRAGMA journal_mode=WAL`）、建表 DDL 与 meta 表三条
    `INSERT ... ON CONFLICT DO UPDATE` + commit —— **每一次都要写锁**。
    于是"只读"请求也得抢写锁。入库期间 worker 在密集写库，请求就在写锁上
    排队；更糟的是它是在 `_lock` **里面**排的，于是一个请求卡住就把整个看板
    （连根本不碰库的 `/api/subjects`）一起拖死。

    实测（2026-09-23，批量入库进行中）：`/api/stats`、`/api/docs`、
    `/api/subjects` 全部 6s 超时，任务一结束就恢复 0.34~0.45s。
    逐步定位与修复前后的对照见 `tools/verify_c0041_rootcause.py`。

    只读探测本身不写库，所以"库已建好"这条路径不再需要写锁。真的缺表
    （新库 / 空文件 / 库被换掉）时照旧走完整 `init_db()`，自动建库的行为
    一点没丢。
    """
    c = db.connect()
    if readonly:
        try:
            if _schema_ready(c):
                return c
        except sqlite3.OperationalError:
            pass            # 探测失败就老实去初始化，别把只读请求变成 500
    db.init_db(c)
    return c


def _core_mtime():
    """入库相关代码的最后修改时间（拿不到就当 0）。"""
    newest = 0.0
    base = Path(__file__).resolve().parent.parent / 'core'
    for name in _CORE_FILES:
        try:
            newest = max(newest, (base / name).stat().st_mtime)
        except OSError:
            continue
    return newest


def code_stale_seconds():
    """代码比本进程新了多少秒；0 表示进程用的就是最新代码。"""
    global _CODE_STAMP
    if _CODE_STAMP is None:
        _CODE_STAMP = _core_mtime()
    return max(0.0, _core_mtime() - _CODE_STAMP)


def stale_notice() -> str:
    """给用户的提醒文案（不新鲜时非空）。"""
    age = code_stale_seconds()
    if age <= 0:
        return ""
    return (
        "⚠ 入库相关代码在本看板启动后被改过（"
        + str(int(age // 60))
        + " 分钟前），当前进程仍在用旧逻辑。请重启看板后再操作，否则结果可能不对。"
    )


def api_stats(conn):
    return study.dashboard(conn)


def api_docs(conn):
    rows = conn.execute(
        'SELECT id, path, kind, title, role, corpus, n_chunks, n_chars, status,\n                  indexed_at, warnings\n           FROM documents ORDER BY role DESC, indexed_at DESC'
    ).fetchall()
    docs = []
    for r in rows:
        d = dict(r)
        d['rel'] = _norm_doc_path(d['path'])
        docs.append(d)
    return {'documents': docs, 'stale': stale_notice()}


def api_doc(conn, doc_id):
    row = conn.execute(
        'SELECT id, path, kind, title, corpus, n_chunks, n_chars, status, warnings FROM documents WHERE id=?',
        (doc_id,)).fetchone()
    if not row:
        return {'error': '文档不存在'}
    return {
        'document': dict(row),
        'outline': search_mod.doc_outline(conn, doc_id),
    }


def api_chunk(conn, chunk_id, before, after):
    h = search_mod.get_chunk(conn, chunk_id)
    if not h:
        return {'error': '切块不存在'}
    ctx = search_mod.neighbors(conn, h, before=before, after=after)
    return {
        'target': h.to_dict(),
        'context': [c.to_dict() for c in ctx],
    }


def api_search(conn, q, k, corpus):
    # C-0107 修 F1：第 3 个位置参数已经是 top_k（真值签名
    # search(conn, query, top_k, doc_ids, max_per_doc, min_vec_score, mode, corpus)），
    # 再传关键字 top_k= 会 TypeError（"got multiple values for argument 'top_k'"）。
    # 原件反编译（recovered-original/dashboard.py:271）的写法就是关键字形式。
    hits = search_mod.search(conn, q, top_k=k, corpus=corpus)
    return {
        "query": q,
        "corpus": corpus,
        "n": len(hits),
        "hits": [h.to_dict() for h in hits],
    }


def api_kps(conn):
    """知识点列表（M0-5：掌握度与 /api/kp?id=、/api/stats 同一口径）。

    这里**不再直接返回库里恒为 0 的 `mastery` 列**：掌握度一律由
    `study.compute_mastery(row)` 现算（与详情/总览同一函数、不写库）。
    排序也随之下沉到 Python 侧——SQL 的 `ORDER BY mastery` 排的是库列，
    换成现算之后它已经没有意义；按「掌握度升序、同名按名称」排与原来一致。
    """
    rows = conn.execute(
        'SELECT id, name, summary, subject, mastery, ease, interval_days, reps,\n                  lapses, due_at, last_review\n           FROM knowledge_points'
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['mastery'] = study.compute_mastery(r)
        n = conn.execute(
            'SELECT COUNT(*) AS n FROM kp_sources WHERE kp_id=?',
            (r['id'],)
        ).fetchone()['n']
        d['n_sources'] = int(n)
        out.append(d)
    out.sort(key=lambda d: (d['mastery'], d['name'] or ''))
    return {'knowledge_points': out}


def api_queue(conn, limit):
    return {"queue": study.due_queue(conn, limit=limit)}


def api_kp_detail(conn, kp_id):
    row = conn.execute(
        'SELECT * FROM knowledge_points WHERE id=?', (kp_id,)).fetchone()
    if not row:
        return {'error': '知识点不存在'}
    kp = dict(row)
    kp['mastery'] = study.compute_mastery(row)

    srcs = conn.execute(
        'SELECT c.id, c.source_ref, c.heading_path, c.text, d.title\n           FROM kp_sources s\n           JOIN chunks c ON c.id = s.chunk_id\n           JOIN documents d ON d.id = c.doc_id\n           WHERE s.kp_id=?',
        (kp_id,)).fetchall()
    kp['sources'] = [dict(r) for r in srcs]

    hist = conn.execute(
        'SELECT grade, correct, question, ts FROM reviews\n           WHERE kp_id=? ORDER BY ts DESC LIMIT 20',
        (kp_id,)).fetchall()
    kp['history'] = [dict(r) for r in hist]

    prereq = conn.execute(
        "SELECT k.id, k.name FROM kp_edges e\n           JOIN knowledge_points k ON k.id = e.src_id\n           WHERE e.dst_id=? AND e.kind='prereq'",
        (kp_id,)).fetchall()
    kp['prerequisites'] = [dict(r) for r in prereq]

    return {'kp': kp}


# ---------------- 预习（task-24）：教材按章 + 小节查看器 后端胶水 ----------------
# 真正的模型调用/产物读写都在 `core/preview.py`（另一位智能体并行重写）。这里只做：
#   ① 从 DB 取教材与章（零模型）；② 把产物状态归一成前端要的形状；
#   ③ 转调 preview 的新函数；后端还没就绪时**防御式降级**（不 500、不阻塞 HTTP）。
# 所有接口错误一律 200/400 带 `ok:false` + `error`（人话）。


def _preview_mod():
    from core import preview as _pm
    return _pm


def _preview_enabled_state():
    """开关状态：`{enabled, hint, switch}`；后端坏了也不 500。"""
    try:
        st = _preview_mod().enabled_state()
        if isinstance(st, dict):
            return {"enabled": bool(st.get("enabled")),
                    "hint": str(st.get("hint") or ""),
                    "switch": str(st.get("switch") or "PREVIEW_ENABLED")}
    except Exception as e:  # noqa: BLE001
        return {"enabled": False, "switch": "PREVIEW_ENABLED",
                "hint": "预习后端还没就绪（%s）。稍后回来再试。" % e}
    return {"enabled": False, "switch": "PREVIEW_ENABLED", "hint": ""}


def _preview_pages_label(ch):
    """页码范围 → `p.19–p.39`（与其它页一致的中文写法）。"""
    a, b = ch.get("page_start"), ch.get("page_end")
    if a and b and int(a) != int(b):
        return "p.%d–p.%d" % (int(a), int(b))
    if a:
        return "p.%d" % int(a)
    return ""


def _preview_artifact_candidates(doc_id, chapter_no):
    """多章产物路径候选（新命名优先，兼容旧单文件）。只读，不写盘。"""
    base = Path(config.LOG_DIR) / "knowledge"
    no = int(chapter_no or 0)
    cands = [
        base / ("%d-预习总结-第%d章.json" % (int(doc_id), no)),
    ]
    if no:
        cands += sorted(base.glob("%d-预习总结-第%d章*.json" % (int(doc_id), no)))
    cands.append(base / ("%d-预习总结.json" % int(doc_id)))
    seen, out = set(), []
    for p in cands:
        s = str(p)
        if s not in seen:
            seen.add(s)
            out.append(p)
    return out


def _preview_read_artifact_file(doc_id, chapter_no):
    """读某章产物文件并归一（后端 `read_preview_artifact` 未就绪时的兜底）。"""
    for p in _preview_artifact_candidates(doc_id, chapter_no):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        # 旧单文件是"一份一份"的：章号对不上就跳过
        if p.name.endswith("%d-预习总结.json" % int(doc_id)):
            m_ch = int(meta.get("chapter_no") or 0)
            if chapter_no and m_ch and m_ch != int(chapter_no):
                continue
        return {"data": data, "path": str(p), "meta": meta}
    return None


def _preview_empty_status():
    return {"exists": False, "status": "", "error": "", "n_subsections": 0,
            "cost_cny": 0.0, "generated_at": "", "is_current_version": False,
            "legacy": False}


def _preview_status_map(doc_id):
    """这份教材**每一章**的产物状态：`{chapter_no: {...}}`。

    优先后端 v2 的 `preview.preview_status(doc_id)`（一次扫描、返回整份教材的章号字典）；
    老版本 / 未就绪时退回逐个产物文件判定。零模型、零写入。
    """
    doc_id = int(doc_id or 0)
    fn = getattr(_preview_mod(), "preview_status", None)
    if callable(fn):
        try:
            st = fn(doc_id)
        except Exception:  # noqa: BLE001
            st = None
        if isinstance(st, dict):
            out = {}
            for k, v in st.items():
                if str(k).startswith("_") or not isinstance(v, dict):
                    continue
                try:
                    no = int(k)
                except (TypeError, ValueError):
                    continue
                exists = bool(v.get("exists"))
                out[no] = {
                    "exists": exists,
                    "status": str(v.get("status") or ("done" if exists else "")),
                    "error": str(v.get("error") or "")[:400],
                    "n_subsections": int(v.get("n_subsections")
                                         or v.get("n_sections") or 0),
                    "cost_cny": round(float(v.get("cost_cny") or 0), 6),
                    "generated_at": str(v.get("generated_at") or ""),
                    "is_current_version": bool(v.get("is_current_version")),
                    "legacy": False,
                }
            old = st.get("_old_single")
            if isinstance(old, dict) and old.get("exists"):
                no = int(old.get("chapter_no") or 0)
                if no and no not in out:
                    out[no] = {
                        "exists": True,
                        "status": str(old.get("status") or "done"),
                        "error": str(old.get("error") or "")[:400],
                        "n_subsections": 0,
                        "cost_cny": round(float(old.get("cost_cny") or 0), 6),
                        "generated_at": str(old.get("generated_at") or ""),
                        "is_current_version": bool(old.get("is_current_version")),
                        "legacy": True,
                    }
            return out
    # 兜底：按产物文件命名扫一遍（新按章文件优先）
    out = {}
    base = Path(config.LOG_DIR) / "knowledge"
    try:
        files = sorted(base.glob("%d-预习总结-第*章.json" % doc_id))
    except OSError:
        files = []
    for p in files:
        m = re.match(r"^%d-预习总结-第(\d+)章\.json$" % doc_id, p.name)
        if not m:
            continue
        hit = _preview_read_artifact_file(doc_id, int(m.group(1)))
        if not hit:
            continue
        data = hit["data"]
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        subs = data.get("subsections") if isinstance(data.get("subsections"), list) else []
        out[int(m.group(1))] = {
            "exists": True, "status": str(meta.get("status") or "done"),
            "n_subsections": len(subs),
            "cost_cny": round(float(meta.get("cost_cny") or 0), 6),
            "generated_at": str(meta.get("generated_at") or ""),
            "is_current_version": bool(meta.get("preview_version")),
            "legacy": False,
        }
    return out


def _preview_list_chapters(doc_id):
    """列章（零模型）：优先后端新 `list_chapters`，报错原样带回。"""
    fn = getattr(_preview_mod(), "list_chapters", None)
    if not callable(fn):
        return {"ok": False, "error": "预习后端还没就绪（缺 list_chapters）"}
    try:
        r = fn(int(doc_id))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "列出章节失败：%s" % e}
    if not isinstance(r, dict):
        return {"ok": False, "error": "列出章节失败：后端返回异常"}
    return r


def api_preview_options(conn):
    """教材列表（只列 role='textbook'）：选项 + 每份的章数 / 是否已有预习。"""
    en = _preview_enabled_state()
    try:
        rows = conn.execute(
            "SELECT id, title, kind, role FROM documents "
            "WHERE role='textbook' ORDER BY id"
        ).fetchall()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "读教材列表失败：%s" % e,
                "enabled": en, "textbooks": []}
    docs = []
    for r in rows:
        did = int(r["id"] if hasattr(r, "keys") else r[0])
        title = r["title"] if hasattr(r, "keys") else r[1]
        kind = r["kind"] if hasattr(r, "keys") else r[2]
        role = r["role"] if hasattr(r, "keys") else r[3]
        lc = _preview_list_chapters(did)
        chs = lc.get("chapters") or []
        st_map = _preview_status_map(did)
        has = any(v.get("exists") for v in st_map.values())
        docs.append({
            "doc_id": did, "title": str(title or ""),
            "kind": str(kind or ""), "role": str(role or "textbook"),
            "n_chapters": int(len(chs) if lc.get("ok") else 0),
            "has_preview": has,
        })
    return {"ok": True, "enabled": en, "textbooks": docs}


def api_preview_chapters(conn, doc_id):
    """章节目录（含每章产物状态）：前端左栏章节目录的数据源。"""
    doc_id = int(doc_id or 0)
    if doc_id <= 0:
        return {"ok": False, "doc_id": 0, "error": "缺少 doc_id", "chapters": []}
    lc = _preview_list_chapters(doc_id)
    if not lc.get("ok"):
        return {"ok": False, "doc_id": doc_id,
                "error": str(lc.get("error") or "这份教材列不出章节"),
                "enabled": _preview_enabled_state(), "chapters": []}
    chapters = []
    st_map = _preview_status_map(doc_id)
    for ch in (lc.get("chapters") or []):
        no = int(ch.get("no") or 0)
        item = {
            "no": no,
            "name": str(ch.get("name") or ""),
            "heading_path": str(ch.get("heading_path") or ""),
            "page_start": ch.get("page_start"),
            "page_end": ch.get("page_end"),
            "page_label": _preview_pages_label(ch),
            "n_blocks": int(ch.get("n_blocks") or 0),
            "n_sections": int(ch.get("n_sections") or 0),
            "preview": st_map.get(no) or _preview_empty_status(),
        }
        chapters.append(item)
    title = str(lc.get("title") or "")
    if not title:
        try:
            row = conn.execute("SELECT title FROM documents WHERE id=?",
                               (doc_id,)).fetchone()
            title = str((row["title"] if row else "") or "")
        except Exception:  # noqa: BLE001
            title = ""
    return {"ok": True, "doc_id": doc_id, "title": title,
            "enabled": _preview_enabled_state(), "chapters": chapters}


def api_preview_estimate(doc_id, chapter):
    """dry-run 估价（0 调用、0 写盘）：前端"先看钱再跑"的确认框数据。"""
    try:
        doc_id, chapter = int(doc_id or 0), int(chapter or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "doc_id/chapter 必须是整数"}
    fn = getattr(_preview_mod(), "estimate_preview", None)
    if not callable(fn):
        return {"ok": False, "error": "预习后端还没就绪（缺 estimate_preview）"}
    try:
        est = fn(doc_id, chapter)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "估算失败：%s" % e}
    if not isinstance(est, dict):
        return {"ok": False, "error": "估算失败：后端返回异常"}
    if est.get("ok") is False:
        return {"ok": False, "error": str(est.get("error") or "估算失败"),
                "doc_id": doc_id, "chapter": chapter}
    pages = est.get("pages") or []
    n_pages = int(est.get("n_pages") or len(pages) or 0)
    return {
        "ok": True, "doc_id": doc_id, "chapter": chapter,
        "chapter_no": int(est.get("chapter_no") or chapter),
        "chapter_name": str(est.get("chapter_name") or ""),
        "chapter": str(est.get("chapter") or ""),
        "page_label": str(est.get("page_label") or ""),
        "n_pages": n_pages,
        "calls": int(est.get("est_calls") or est.get("calls") or 1),
        "cost_cny_max": round(float(est.get("est_cost_cny")
                                    or est.get("cost_cny_max") or 0), 6),
        "est_tokens_in": int(est.get("est_tokens_in") or 0),
        "est_tokens_out": int(est.get("est_tokens_out") or 0),
        "model": str(est.get("model") or ""),
        "enabled": bool(est.get("enabled")),
        "skip_would_happen": bool(est.get("skip_would_happen")),
        "exists": bool(est.get("exists") or (est.get("prev") or {}).get("exists")),
        "est_rule": str(est.get("est_rule") or ""),
    }


def api_preview_start(payload):
    """起后台预习任务：不阻塞 HTTP；不启用/已有任务时如实返回。"""
    try:
        doc_id = int(payload.get("doc_id") or 0)
        chapter = int(payload.get("chapter") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "started": False, "error": "doc_id/chapter 必须是整数"}
    if doc_id <= 0 or chapter <= 0:
        return {"ok": False, "started": False, "error": "缺少 doc_id 或 chapter"}
    force = bool(payload.get("force"))
    fn = getattr(_preview_mod(), "start_background", None)
    if not callable(fn):
        return {"ok": False, "started": False,
                "error": "预习后端还没就绪（缺 start_background）",
                "hint": "后端智能体完成后即可在这里生成；联调由 Lead 统一做。"}
    try:
        try:
            r = fn(doc_id, chapter, force=True) if force else fn(doc_id, chapter)
        except TypeError:
            r = fn(doc_id, chapter)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "started": False, "error": "启动失败：%s" % e}
    if not isinstance(r, dict):
        return {"ok": False, "started": False, "error": "启动失败：后端返回异常"}
    return {"ok": True, "started": bool(r.get("started")),
            "error": str(r.get("error") or ""),
            "hint": str(r.get("hint") or "")}


def api_preview_progress():
    """进度快照（与 kp-build 同构，前端复用 renderProgress）。"""
    fn = getattr(_preview_mod(), "progress_snapshot", None)
    if not callable(fn):
        return {"kind": "preview", "active": False, "status": "",
                "error": "预习后端还没就绪（缺 progress_snapshot）"}
    try:
        p = fn()
    except Exception as e:  # noqa: BLE001
        return {"kind": "preview", "active": False, "status": "", "error": str(e)}
    return p if isinstance(p, dict) else {"kind": "preview", "active": False,
                                          "status": ""}


def api_preview_doc(doc_id, chapter):
    """读某章预习产物（查看器数据）：优先后端 new API，读不到用产物文件兜底。"""
    try:
        doc_id, chapter = int(doc_id or 0), int(chapter or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "doc_id/chapter 必须是整数"}
    if doc_id <= 0 or chapter <= 0:
        return {"ok": False, "error": "缺少 doc_id 或 chapter"}
    fn = getattr(_preview_mod(), "read_preview_artifact", None)
    if callable(fn):
        for args in ((doc_id, chapter), (doc_id, chapter, None)):
            try:
                r = fn(*args)
            except TypeError:
                continue
            except Exception:  # noqa: BLE001
                r = None
            if isinstance(r, dict):
                if r.get("ok") is False:
                    return {"ok": False, "error": str(r.get("error") or "读不到产物")}
                art = r.get("artifact") if isinstance(r.get("artifact"), dict) else r
                meta = art.get("meta") if isinstance(art.get("meta"), dict) else {}
                return {"ok": True, "doc_id": doc_id, "chapter_no": chapter,
                        "artifact": art,
                        "legacy": bool(r.get("legacy") or meta.get("legacy")),
                        "is_current_version": bool(r.get("is_current_version")),
                        "warnings": list(meta.get("warnings") or [])}
            break
    hit = _preview_read_artifact_file(doc_id, chapter)
    if not hit:
        return {"ok": False, "error": "这一章还没有预习产物（先点「预习本章」生成）"}
    return {"ok": True, "doc_id": doc_id, "chapter_no": chapter,
            "artifact": hit["data"], "source": hit["path"]}


def _outline_dir():
    return Path(config.LOG_DIR) / 'outline'


def _read_outline(doc_id):
    """读一份 outline 缓存；没有或坏掉都返回 {}，接口不因此报错。

    ⚠ 卡 C-0050：知识点页签已不再调用它（页签数据源 = 整理产物）。
    """
    p = _outline_dir() / f"{doc_id}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(d, dict):
        return d
    return {}


def _outline_pages(d):
    """从缓存取 {页号: 路径}。

    解析交给 `core.outline._load_pages()`：它认得旧缓存里的 `theme + leaf`
    写法（库里 v3 那 21 份就是那种），自己再写一遍等于把兼容逻辑抄丢。
    万一那边改了私有接口，退化成只读 `path`，至少不让整个接口挂掉。

    ⚠ 解析之后要补一层「**末项豁免**」。`core.outline._clean()` 会按
    **子串包含**判同层重复（`_same`：`"DNA 一级结构的两种表示法"` 被祖先
    `"一级结构"` 吃掉、`"嵌入剂的作用机制与平面芳环"` 被 `"嵌入剂"` 吃掉），
    于是那一页在树里**失去自己的落点**、变成挂到祖先页上：
      实测 doc#103 有 13 页这样，导图里只剩 65 个"页终点且无子节点"的节点，
      卡 C-0034 的 75 条末级知识点里有 10 条因此无处可挂。
    这里**只把被剔掉的末项补回来**：仅当缓存里的原路径正好是解析结果的
    **延长**（前 N 项逐字相同、只多出末尾若干项）时才替换 —— 中间层被剔
    （长度不变）或形态完全不同的情况一律不动。
    实测这一补只命中 doc#103 的 13 页，doc#89 与库里其他 38 份缓存
    **树形状逐节点不变**（改动前后快照对比过）。
    """
    try:
        out = outline_mod._load_pages(d)
    except Exception:
        out = {}
        for k, v in (d.get("pages") or {}).items():
            try:
                pno = int(k)
            except (TypeError, ValueError):
                continue
            path = v.get("path") if isinstance(v, dict) else v
            if not isinstance(path, list):
                continue
            names = [str(x).strip() for x in path if str(x or "").strip()]
            if not names:
                continue
            out[pno] = names
        return out

    for k, v in (d.get("pages") or {}).items():
        try:
            pno = int(k)
        except (TypeError, ValueError):
            continue
        cur = out.get(pno)
        path = v.get("path") if isinstance(v, dict) else v
        if not cur or not isinstance(path, list):
            continue
        names = [str(x).strip() for x in path if str(x or "").strip()]
        if len(names) > len(cur) and names[:len(cur)] == list(cur):
            out[pno] = names
    return out


def _mindmap_tree(pages):
    """把 {页号: 路径} 按**前缀**合并成嵌套树，也就是那张思维导图。

    同一层同名只留一个节点——各页路径本来就是共享前缀的。
    节点字段：name（层级名）、page（路径正好停在它的页）、
    n_pages（这棵子树覆盖多少页，点节点检索时告诉用户规模）、children。
    """
    roots = []
    idx = {}
    for pno in sorted(pages):
        kids = roots
        key = ()
        for name in pages[pno]:
            key += (name,)
            node = idx.get(key)
            if node is None:
                node = {"name": name, "page": None, "n_pages": 0, "children": []}
                idx[key] = node
                kids.append(node)
            node["n_pages"] += 1
            kids = node["children"]
        if key:
            idx[key]["page"] = pno
    return roots


def _n_nodes(tree):
    return sum(1 for _ in tree)


def _knowledge_dir() -> Path:
    """知识点 JSON 所在目录；`KB_KNOWLEDGE_DIR` 可覆盖（与 KB_RAW/KB_NOTES 同一惯例）。

    ⚠ 卡 C-0039：兜底值取 `config.LOG_DIR`（切库会变），且**切库时会把
    `KB_KNOWLEDGE_DIR` 一起清掉**（见 `core/subjects._bind`）——那个环境变量是
    "全局单库"时代的覆盖开关，留着它会让新学科的知识点写到固定目录里去。
    """
    return Path(
        os.environ.get(
            _KNOWLEDGE_DIR_ENV,
            str(Path(config.LOG_DIR) / "knowledge"),
        )
    ).expanduser()


def _kp_load_dir():
    """扫知识点目录，建 {doc_id: {"meta":…, "by_id":…, "by_name":…, "source":…}}。

    **静默降级**（照 `_read_outline()` 的容错风格）：目录不在、文件坏、字段缺，
    一律当作"没有知识点"返回 {}。知识点是锦上添花，绝不能因为它坏了让导图、
    整个 /api/mindmap 打不出图——那才是真的坏。

    `by_name` 的值是**列表**：同名可能有多条（跨父级），挂载时只有唯一命中才挂。
    """
    d = _knowledge_dir()
    try:
        files = sorted(d.glob('*.json'))
    except OSError:
        return {}

    sig_parts = []
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        sig_parts.append((p.name, st.st_mtime_ns, st.st_size))
    sig = (str(d), tuple(sig_parts))

    with _KNOWLEDGE_LOCK:
        hit = _KNOWLEDGE_CACHE.get(sig)
    if hit is not None:
        return hit

    out = {}
    for p in files:
        try:
            raw = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        kps = raw.get('knowledge_points')
        if not isinstance(kps, list):
            continue
        meta = raw.get('meta') if isinstance(raw.get('meta'), dict) else {}
        main_id = meta.get('main_doc', {}).get('id') if isinstance(meta.get('main_doc'), dict) else None
        for kp in kps:
            if not isinstance(kp, dict):
                continue
            kid = str(kp.get('id') or '').strip()
            if not kid:
                continue
            try:
                did = int(kp.get('doc_id'))
            except (TypeError, ValueError):
                try:
                    did = int(main_id)
                except (TypeError, ValueError):
                    did = 0
            if did <= 0:
                continue
            doc = out.setdefault(did, {
                'meta': meta,
                'source': p.name,
                'by_id': {},
                'by_name': {},
            })
            doc['by_id'][kid] = kp
            nm = str(kp.get('name') or '').strip()
            if not nm:
                continue
            doc['by_name'].setdefault(nm, []).append(kp)

    with _KNOWLEDGE_LOCK:
        if len(_KNOWLEDGE_CACHE) >= _KNOWLEDGE_CACHE_MAX:
            _KNOWLEDGE_CACHE.clear()
        _KNOWLEDGE_CACHE[sig] = out
    return out


def _kp_index(doc_id):
    """某文档的知识点索引；没有就是 {}（调用方按"没有知识点"处理）。"""
    try:
        return _kp_load_dir().get(int(doc_id)) or {}
    except (TypeError, ValueError):
        return {}


def _pages_label(pages):
    """页号列表压成区间文案：`[15,16,17]` → `p.15–p.17`，`[10,12,13]` → `p.10、p.12–p.13`。"""
    ps = []
    for p in pages:
        try:
            ps.append(int(p))
        except (TypeError, ValueError):
            continue

    ps = sorted(set(ps))

    if not ps:
        return ""

    spans = []
    # C-0110：重建版写的是 `s, e = ps[0]`（int 不能解包 → TypeError: cannot unpack
    # non-iterable int object）——凡有知识点的页号要走区间文案就崩，`_artifact_tree.conv`
    # 与 `_attach_kp.walk` 都调它，于是 /api/mindmap 在**有真数据**时必 500（空库看不出来）。
    # 真值：funcs.txt `_pages_label args=1 vars: pages, ps, p, spans, s, e`（没有别的变量）
    # + 本函数 docstring 的两个例子逐字对拍 ⇒ 初值就是 `s = e = ps[0]`。
    s = e = ps[0]
    for p in ps[1:]:
        if p == e + 1:
            e = p
            continue
        spans.append((s, e))
        s, e = p, p
    spans.append((s, e))

    return "、".join(
        f"p.{a}" if a == b else f"p.{a}–p.{b}"
        for a, b in spans
    )


def _kp_rule_label(kp):
    if str(kp.get('rule') or '').upper() == 'R2':
        return 'R2 兜底'
    if kp.get('has_lv4'):
        return 'R1b 有四级·多页综合'
    return 'R1a 单页'


def _kp_node_kind(depth, name, has_children, kp):
    """导图节点的知识点类型：r1 / r2 / lv4 / e1 / category / root / none。

    depth 是**不含根的层数**：根 0，一级 1，三级 3，四级 4。
    """
    if kp is not None:
        if str(kp.get("rule") or "").upper() == "R1":
            return "r1"
        return "r2"
    if depth <= 0:
        return "root"
    if depth >= 4:
        return "lv4"
    low = str(name or "").lower()
    if any(low in h for h in _KP_E1_HINTS):
        return "e1"
    if not has_children:
        if depth <= 1:
            return "e1"
        return "none"
    return "category"


def _attach_kp(tree, doc_id):
    """原地给导图节点挂知识点**摘要**（只增字段，不动 name/page/n_pages/children）。

    ⚠ 匹配用的是**节点名**：本课件（doc#89）三级节点无重名，40 个知识点实测
    唯一命中、零冲突，所以按名字挂是对的。**推广到其他章必须改用 `key`**
    （含 doc_id 与 lv2 前缀）：全库存在「同课件内同名不同父级」6 组，见
    `cards/C-0028/知识点构建方案（三级粒度）.md` §8.1——只按名字匹配会挂到错的父级。
    这里的自保是：同名多于一条时**一条都不挂**（宁可没有，也不挂错），
    真要推广就把 `by_name.get(name)` 换成按 `key` 查。

    原文只进摘要（块数/页/题数），**块原文不进树**：一份课件 456 块 ≈ 436 KB，
    塞进树接口会把首屏拖垮。详情走 `/api/mindmap/kp` 按需取。
    """
    idx = _kp_index(doc_id)
    by_name = idx.get("by_name") or {}
    stats = {"r1": 0, "r2": 0, "lv4": 0, "e1": 0, "category": 0, "root": 0, "none": 0}

    def walk(nodes: list[dict], depth: int, parent_kp: dict | None) -> int:
        # C-0110 回填：真值 = _work/rebuilt/dashboard/0028-_attach_kp.walk.py
        # （funcs.txt：_attach_kp.walk args=3 vars: nodes, depth, parent_kp, total, n, name,
        #  kids, hit, kp, kind, bks, qs, pages —— 逐字对上）
        """返回该子树挂上的知识点个数。"""
        total = 0
        for n in nodes:
            name = str(n.get("name") or "")
            kids = n.get("children") or []
            hit = by_name.get(name) or []
            kp = hit[0] if len(hit) == 1 else None
            kind = _kp_node_kind(depth, name, bool(kids), kp)
            n["kp_node_kind"] = kind
            stats[kind] = stats.get(kind, 0) + 1
            if kp is not None:
                bks = kp.get("blocks") if isinstance(kp.get("blocks"), list) else []
                qs = kp.get("questions") if isinstance(kp.get("questions"), list) else []
                pages = kp.get("content_pages") if isinstance(kp.get("content_pages"), list) else []
                n["kp_id"] = str(kp.get("id") or "")
                n["kp_name"] = str(kp.get("name") or "")
                n["kp_rule"] = str(kp.get("rule") or "")
                n["kp_rule_label"] = _kp_rule_label(kp)
                n["kp_display"] = str(kp.get("display") or "")
                n["kp_blocks"] = len(bks)
                n["kp_pages"] = sorted({int(p) for p in pages if str(p).lstrip("-").isdigit()})
                n["kp_pages_label"] = _pages_label(pages)
                n["kp_questions"] = len(qs)
                parent_kp = n
                total += 1
            elif parent_kp is not None:
                n["kp_parent_id"] = parent_kp.get("kp_id") or ""
                n["kp_parent_name"] = parent_kp.get("kp_name") or ""
            n["kp_subtree"] = walk(kids, depth + 1, parent_kp)
            total += n["kp_subtree"]
        return total

    stats["attached"] = walk(tree, 0, None)
    return stats


def api_mindmap_kp(doc_id, kp_id):
    idx = _kp_index(doc_id)
    kp = idx.get("by_id", {}).get(str(kp_id or "").strip())

    if kp is None:
        return {"error": f"文档 {doc_id} 里没有知识点「{kp_id or '(空)'}」"}

    blocks = [b for b in (kp.get("blocks") or []) if isinstance(b, dict)]
    qs = [q for q in (kp.get("questions") or []) if isinstance(q, dict)]
    pages = kp.get("content_pages") if isinstance(kp.get("content_pages"), list) else []
    self_pages = kp.get("self_pages") if isinstance(kp.get("self_pages"), list) else []

    return {
        "doc_id": doc_id,
        "kp_id": str(kp.get("id") or ""),
        "name": str(kp.get("name") or ""),
        "rule": str(kp.get("rule") or ""),
        "rule_label": _kp_rule_label(kp),
        "has_lv4": bool(kp.get("has_lv4")),
        "display": str(kp.get("display") or ""),
        "key": str(kp.get("key") or ""),
        "lv2": str(kp.get("lv2") or ""),
        "doc_title": str(kp.get("doc_title") or ""),
        "content_pages": pages,
        "self_pages": self_pages,
        "pages_label": _pages_label(pages),
        "lv4_nodes": kp.get("lv4_nodes") if isinstance(kp.get("lv4_nodes"), dict) else {},
        "n_blocks": len(blocks),
        "n_body": sum((b.get("n_body") or 0) for b in blocks),  # TODO(反编译存疑)
        "n_fig": sum((b.get("n_fig") or 0) for b in blocks),  # TODO(反编译存疑)
        "n_shared": sum((b.get("n_shared") or 0) for b in blocks),  # TODO(反编译存疑)
        "blocks": blocks,
        "questions": qs,
        "n_questions": len(qs),
        "source": str(idx.get("source") or ""),
    }


def _artifact_file(doc_id):
    """产物的**规范路径** `logs/knowledge/<id>-末级知识点总结.json`。

    `doc_id` 不是正整数就返回 None——**路径完全由整数拼出来**，前端递不进
    任何路径片段（与 `/img/slide`、`_safe_raw_path` 同一条安全思路）；
    再自证一次文件名与父目录形态，作为第二道保险。
    """
    try:
        did = int(doc_id)
    except (TypeError, ValueError):
        return None
    if did <= 0:
        return None
    kd = _knowledge_dir()
    name = "%d%s" % (did, _LKP_SUFFIX)
    p = kd / name
    if p.name != name or p.parent != kd:
        return None
    return p


def _artifact_backups(p):
    """产物的备份文件：`<产物名>.bak-*`（卡 C-0050 点名**只清这一种**）。"""
    try:
        return sorted(
            q for q in p.parent.glob(p.name + ".bak-*")
            if q.is_file()
        )
    except OSError:
        return []


def _iso_epoch(s):
    """ISO 时间串 → unix 秒（前端 `fmtTime` 只认秒）。转不了就给 None。"""
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s)).timestamp()
    except (TypeError, ValueError):
        return None


def _int_arg(v, dflt):
    """查询串里的整数参数；不是数字就退回默认值（**不抛异常**）。

    卡 C-0050：`/api/mindmap?doc_id=abc` 原来在这里 `int()` 抛 ValueError →
    HTTP 500。非法入参应当**被拒绝或如实报告**，不该 500；`doc_id=0` 的语义
    本来就是"列清单"，退回默认值即可（`/api/summary-kp` 同理，退回 0 后
    那句"文档 0 里没有知识点"就是如实的答复）。
    """
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return dflt


def _artifact_summary(raw, meta, st):
    kps = [k for k in (raw.get("knowledge_points") or []) if isinstance(k, dict)]
    return {
        "pipeline_version": str(meta.get("pipeline_version") or ""),
        "source_card": str(meta.get("source_card") or ""),
        "model": str(meta.get("model") or ""),
        "generated_at": str(meta.get("generated_at") or ""),
        "created_at": _iso_epoch(meta.get("generated_at")),
        "cost_cny": float(meta.get("cost_cny_all_calls") or 0.0),
        "n_kps": len(kps),
        "n_ready": sum(1 for k in kps if k.get("ready")),  # TODO(反编译存疑)
        "kp_total": int(meta.get("kp_total") or 0),
        "n_pages": int(st.get("n_pages") or 0),
        "n_nodes": int(st.get("n_nodes") or 0),
        "n_leaves": int(st.get("n_leaves") or 0),
        "merged_by": str(st.get("merged_by") or ""),
    }


def _artifact_load(doc_id):
    """读一份整理产物并按**版本**判状态；**任何异常都不抛**。

    返回 `{"state", "path", "name", "meta", "doc", "summary"}`：
      - `state`：`none` / `legacy` / `incomplete` / `v2`；
      - `doc`：整份产物（只有 `v2` 才非空）；
      - `summary`：列表页用的轻量字段。
    文件缺失、坏 JSON、字段缺——一律降级成对应状态，接口照常 200
    （沿用 `_read_outline()` 定下的容错风格）。
    """
    p = _artifact_file(doc_id)
    out = {
        "state": _ARTIFACT_NONE,
        "path": str(p) if p else "",
        "name": p.name if p else "",
        "meta": {},
        "doc": {},
        "summary": {},
    }
    if p is None or not p.is_file():
        return out
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out["state"] = _ARTIFACT_LEGACY
        return out
    if not isinstance(raw, dict):
        out["state"] = _ARTIFACT_LEGACY
        return out
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    out["meta"] = meta
    if str(meta.get("pipeline_version") or "") not in _KP_READABLE_VERSIONS:
        out["state"] = _ARTIFACT_LEGACY
        return out
    st = raw.get("structure") if isinstance(raw.get("structure"), dict) else {}
    if not isinstance(st.get("root"), dict):
        out["state"] = _ARTIFACT_INCOMPLETE
        out["doc"] = raw
        return out
    out["state"] = _ARTIFACT_V2_STATE
    out["doc"] = raw
    out["summary"] = _artifact_summary(raw, meta, st)
    return out


def _artifact_tree(doc, doc_id):
    """把 v2 产物的 `structure.root` 转成页签渲染用的树（含末级知识点标记）。

    节点字段与旧 `_mindmap_tree()` 的输出**对齐**（`name` / `page` / `n_pages` /
    `children`），前端 `mmNodes()` 照旧渲染；多出来的：
      - `node_id`：产物里的结构节点 id（`N03` 这种，排查时对得上产物）；
      - `pages`：该节点覆盖的页（升序）；
      - `kp_node_kind`：`root` / `category` / `leaf` / `none`（弹窗按它说话）；
      - `lkp_*`：末级知识点的**标记**（id 列表、有没有总结、页标签）。

    ⚠ 一条知识点 = 结构叶节点内**连续 ≤3 页**（卡 C-0049 §5）；叶节点超过 3 页
      会切成多条，所以一个节点可能挂**多条** kp（`lkp_ids`）——前端点开时列出来
      让人选（`lkp_multi`），不再"随便挑一条"。
    ⚠ `page` 取该节点覆盖页的**首页**：v2 的结构节点没有"路径停在这一页"这个概念
      （那是旧 outline 派生树的说法），但页签上显示一个代表页仍然有用。
    """
    kps = {
        str(k.get("id")): k
        for k in (doc.get("knowledge_points") or [])
        if isinstance(k, dict) and str(k.get("id") or "").strip()
    }
    stats = {"nodes": 0, "leaf": 0, "ready": 0, "pending": 0, "multi": 0}

    def conv(node: dict, depth: int) -> dict:
        # C-0110 回填：真值 = _work/rebuilt/dashboard/0037-_artifact_tree.conv.py（完整 58 行）
        kids = [conv(c, depth + 1) for c in (node.get("children") or []) if isinstance(c, dict)]
        pages = []
        for p in (node.get("pages") or []):
            try:
                pages.append(int(p))
            except (TypeError, ValueError):
                continue
        pages.sort()

        all_pages = set(pages)
        for c in kids:
            all_pages |= set(c.get("pages_all") or pages)

        ids = [str(x).strip() for x in (node.get("kp_ids") or []) if str(x or "").strip()]
        ready = [i for i in ids if _lkp_status(kps.get(i)) == "ready"]
        show = ready or ids

        n = {
            "name": str(node.get("name") or ""),
            "page": pages[0] if pages else None,
            "n_pages": len(all_pages),
            "pages": pages,
            "pages_all": sorted(all_pages),
            "node_id": str(node.get("id") or ""),
            "children": kids,
        }

        n["kp_node_kind"] = (
            "root" if depth == 0
            else "category" if kids
            else "leaf" if ids
            else "none"
        )
        n["lkp_role"] = "leaf" if ids else ""
        n["lkp_via"] = "structure"
        n["lkp_ids"] = show
        n["lkp_id"] = show[0] if show else ""
        n["lkp_names"] = [str((kps.get(i) or {}).get("name") or "") for i in show]
        n["lkp_n"] = len(ids)
        n["lkp_multi"] = len(ready) > 1
        n["lkp_ready"] = bool(ready)
        n["lkp_status"] = "ready" if ready else "pending" if ids else "none"

        first = kps.get(show[0]) if show else None
        if isinstance(first, dict):
            n["lkp_name"] = str(first.get("name") or "")
            n["lkp_pages_label"] = _pages_label(first.get("pages"))
            n["lkp_merged"] = len(first.get("pages") or []) > 1

        stats["nodes"] += 1
        if ids:
            stats["leaf"] += 1
            stats["ready" if ready else "pending"] += 1
            if len(ready) > 1:
                stats["multi"] += 1

        return n

    st = doc.get("structure") if isinstance(doc.get("structure"), dict) else {}
    root = st.get("root")
    tree = [conv(root, 0)] if isinstance(root, dict) else []
    return tree, stats


def _artifact_doc_list(conn):
    """页签的课件清单：以**产物**为准（卡 C-0050 人拍板"页签的一切由产物驱动"）。

    组成 = 库内全部文档 ∪ 有产物的文档（后者可能已不在库，照旧标注"已不在库"）。
    为什么要把**没整理过**的库内文档也列上：不列的话，那些课件的状态
    （未整理 / 刚被清除）在页签上根本看不见——人拍完清除，页面应当当场告诉他
    "这份回到未整理了"，而不是让这份课件从下拉框里凭空消失。
    """
    try:
        rows = conn.execute("SELECT id, title, kind FROM documents").fetchall()
    except sqlite3.Error:
        rows = []
    docs = {int(r["id"]): dict(r) for r in rows}
    idx = _lkp_load_dir()
    dids = sorted(set(docs) | set(idx))
    items = []
    for did in dids:
        d = docs.get(did) or {}
        rec = idx.get(did) or {}
        state = str(rec.get("state") or _ARTIFACT_NONE)
        sm = rec.get("summary") or {}
        item = {
            "doc_id": did,
            "title": d.get("title") or ("文档 %d" % did),
            "kind": d.get("kind") or "",
            "in_library": did in docs,
            "artifact_state": state,
            "artifact_source": str(rec.get("source") or ""),
            "artifact_message": "" if state == _ARTIFACT_V2_STATE else _ARTIFACT_MESSAGE.get(state, ""),
            "n_pages": int(sm.get("n_pages") or 0),
            "n_nodes": int(sm.get("n_nodes") or 0),
            "n_kps": int(sm.get("n_kps") or 0),
            "model": str(sm.get("model") or ""),
            "created_at": sm.get("created_at"),
            "cost_cny": float(sm.get("cost_cny") or 0.0),
        }
        items.append(item)
    decks = study.decks_for(conn, [it["doc_id"] for it in items])
    for it in items:
        d = decks.get(it["doc_id"]) or {}
        it["deck_plan_id"] = int(d.get("deck_plan_id") or 0)
        it["deck_next_due"] = d.get("deck_next_due") or ""
        it["deck_rounds_done"] = int(d.get("deck_rounds_done") or 0)
        it["deck_rounds_total"] = int(d.get("deck_rounds_total") or 0)
    # 库内文档排前面（用户多半想看库里有的资料），已不在库的历史产物排后面并标注
    items.sort(key=lambda x: (not x["in_library"], x["title"]))
    return items


def _lkp_load_dir():
    """扫整理产物，建 `{doc_id: {...}}` 索引（**只收 C-0049-v2**，卡 C-0050）。

    每项：`state` / `meta` / `source`（文件名）/ `summary`（列表页要的轻量字段）
    / `by_id`（知识点 id → 条目）/ `by_path`（结构节点路径 → 条目）/
    `by_page`（页号 → 条目列表）。
    **旧格式只留一条 `state="legacy"`**：不进任何索引、不上树（人拍板：保留
    文件、页签不显示）。静默降级照旧：目录不在、文件坏、字段缺，都当作
    "这份还没有可用的产物"。
    """
    d = _knowledge_dir()
    try:
        files = sorted(d.glob("*" + _LKP_SUFFIX))
    except OSError:
        return {}

    sig_parts = []
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        sig_parts.append((p.name, st.st_mtime_ns, st.st_size))
    sig = (str(d), "_lkp", tuple(sig_parts))

    with _LKP_LOCK:
        hit = _LKP_CACHE.get(sig)
    if hit is not None:
        return hit

    out = {}
    for p in files:
        stem = p.name[:-len(_LKP_SUFFIX)] if p.name.endswith(_LKP_SUFFIX) else ""
        if not stem.isdigit():
            continue
        did = int(stem)
        art = _artifact_load(did)
        rec = {
            "state": art["state"],
            "meta": art["meta"],
            "source": p.name,
            "summary": art["summary"],
            "by_id": {},
            "by_path": {},
            "by_page": {},
        }
        out[did] = rec
        if art["state"] != _ARTIFACT_V2_STATE:
            continue
        for kp in (art["doc"].get("knowledge_points") or []):
            if not isinstance(kp, dict):
                continue
            kid = str(kp.get("id") or "").strip()
            if not kid:
                continue
            rec["by_id"][kid] = kp

            if isinstance(kp.get("node_path"), list):
                path = kp.get("node_path")
            else:
                path = kp.get("path")
            if isinstance(path, list) and path:
                names = tuple(str(x) for x in path)
                if names:
                    rec["by_path"][names] = kp

            for _p in (kp.get("pages") or []):
                try:
                    rec["by_page"].setdefault(int(_p), []).append(kp)
                except (TypeError, ValueError):
                    continue

    with _LKP_LOCK:
        if len(_LKP_CACHE) >= _LKP_CACHE_MAX:
            _LKP_CACHE.clear()
        _LKP_CACHE[sig] = out
    return out


def _lkp_index(doc_id):
    """某文档的产物索引；没有产物也**返回一个带 `state` 的 dict**。

    ⚠ 卡 C-0050：与旧版不同，这里不再用"空 dict"表示"没有"——页签必须能分清
    `none`（没整理过）与 `legacy`（旧格式，页签不显示）两种说法，所以状态
    字段常驻（见 `_ARTIFACT_MESSAGE`）。
    """
    blank = {
        "state": _ARTIFACT_NONE,
        "meta": {},
        "source": "",
        "summary": {},
        "by_id": {},
        "by_path": {},
        "by_page": {},
    }
    try:
        return _lkp_load_dir().get(int(doc_id)) or blank
    except (TypeError, ValueError):
        return blank


def _lkp_status(kp):
    """`ready`（有总结正文）/ `pending`（有条目但正文空）/ `none`（没有条目）。"""
    if not isinstance(kp, dict):
        return "none"
    if str(kp.get("summary") or "").strip():
        return "ready"
    return "pending"


def _lkp_seq(doc_id):
    """页签树上**已有总结的末级知识点**的**先序**序列（卡 C-0036；C-0050 换源）。

    这就是人眼睛在页签树上从上往下的顺序：前端 `mmNodes()` 是先序递归渲染，
    这里的 walk 也是先序。**顺序只在这里算一次**，`/api/summary-kp` 把
    `prev_id`/`next_id` 直接发给前端——前端不许自己排。

    卡 C-0050 起树来自产物 `structure.root`（模型生成的思维导图），先序天然就是
    "模型给的章节顺序"；老实现是"按页号合并 outline 路径时首次出现的顺序"。

    只收 `lkp_ready` 的节点：它们才是弹窗里点得开、也切得过去的知识点。
    末级但"待生成"的节点没有正文，跳过去只会看到一条读取失败。

    **按知识点 id 去重**：一个结构叶节点内超过 3 页会切成多条知识点（卡 C-0049
    §5），多条挂在**同一个节点**上；而链是"知识点"的序列，不是"节点"的序列，
    不去重 `order_total` 就会虚高。位置取先序里**第一次出现**的那个节点。
    """
    art = _artifact_load(doc_id)
    if art["state"] != _ARTIFACT_V2_STATE:
        return []
    tree, _ = _artifact_tree(art["doc"], doc_id)
    out = []

    def walk(nodes: list[dict]) -> None:
        # C-0110 回填：真值 = _work/rebuilt/dashboard/0043-_lkp_seq.walk.py
        for n in nodes:
            if n.get('lkp_ready'):
                ids = n.get('lkp_ids') or []
                names = n.get('lkp_names') or []
                for i, kid in enumerate(ids):
                    out.append({
                        'id': str(kid),
                        'name': (str(names[i]) if i < len(names) else '') or str(n.get('name') or ''),
                        'page': n.get('page'),
                    })
            walk(n.get('children') or [])

    walk(tree)
    seen = set()
    uniq = []
    for x in out:
        if x["id"] in seen:
            continue
        seen.add(x["id"])
        uniq.append(x)
    return uniq


def _figure_desc_root():
    return Path(config.LOG_DIR) / _FIGURE_DESC_DIRNAME


def _doc_path_of(conn, doc_id):
    """真库 `documents.path`（查不到就空串：接口照常降级，不抛）。"""
    try:
        row = conn.execute(
            'SELECT path FROM documents WHERE id=?',
            (int(doc_id),)).fetchone()
    except Exception:
        return ''
    if row is None:
        return ''
    try:
        return str(row['path'] or '')
    except (TypeError, IndexError):
        return ''


def _deck_name_candidates(stem):
    r"""`figure_desc` 下这份课件的目录名候选（按顺序试，命中即用）。

    ① `<stem>`：常规情形。目录由 `core/figures.py::_safe_dirname(path.stem)` 拼出，
       对干净文件名它是**恒等变换**（实测 `07 Med Chem(1).pptx` → `07 Med Chem(1)\`）；
    ② `<stem>_slides`：历史遗留（`run_figure_desc.py` 时代那批 `_slides` 目录）；
    ③ `share.safe_dirname(stem)`：**文件名主干超过 60 字符**时 `_safe_dirname` 会截断，
       目录名因此与 stem 不同——不加这一条，长名课件**即使没改过名**也永远找不到
       页图（卡 C-0085 顺手补齐；判据与写入侧 `core/share.py::safe_dirname` 同源）。

    ⚠ 三者的顺序就是"优先认哪一个"：既存在 `<stem>` 又有截断名目录时，认前者。
    """
    out = [stem]
    if stem + '_slides' != stem:
        out.append(stem + '_slides')
    try:
        safe = share_mod.safe_dirname(stem)
    except Exception:
        safe = ''
    if safe and safe not in out:
        out.append(safe)
    return tuple(out)


def _deck_dir_for(doc_path):
    """`documents.path` → figure_desc 里的课件目录；推不出或不存在就 None。"""
    stem = Path(str(doc_path or "").replace("\\", "/")).stem.strip()
    if not stem or stem in (".", "..") or "/" in stem or "\\" in stem:
        return None
    root = _figure_desc_root()
    for name in _deck_name_candidates(stem):
        d = root / name
        try:
            if d.is_dir():
                return d
        except OSError:
            continue
    return None


def _slide_files(deck):
    """`{页号: 文件名}`，只认 `slide_<数字>.png`；目录 mtime 一变就重扫。"""
    try:
        st = deck.stat()
        key = (str(deck), st.st_mtime_ns)
    except OSError:
        return {}

    with _SLIDE_LOCK:
        hit = _SLIDE_CACHE.get(key)
    if hit is not None:
        return hit

    out = {}
    try:
        for p in deck.glob('slide_*.png'):
            m = re.fullmatch('slide_(\\d+)\\.png', p.name)
            if not m or not p.is_file():
                continue
            out[int(m.group(1))] = p.name
    except OSError:
        out = {}

    with _SLIDE_LOCK:
        if len(_SLIDE_CACHE) >= _SLIDE_CACHE_MAX:
            _SLIDE_CACHE.clear()
        _SLIDE_CACHE[key] = out

    return out


def _slide_info(doc_id, pages, conn=None):
    """这条知识点的各页**有没有课件图** + 课件目录名（前端据此给按钮/提示）。

    `pages` 为空（旧数据没写页）时照样返回 deck：前端能提示"这个课件有没有图缓存"。
    """
    pgs = []
    for p in pages or []:
        try:
            pgs.append(int(p))
        except (TypeError, ValueError):
            continue

    own = conn is None
    if own:
        try:
            conn = _conn(readonly=True)
        except Exception:
            return {"deck": "", "slides": []}

    try:
        deck = _deck_dir_for(_doc_path_of(conn, doc_id))
    finally:
        if own and conn is not None:
            conn.close()

    if deck is None:
        return {"deck": "", "slides": [{"page": p, "ok": False} for p in pgs]}

    files = _slide_files(deck)
    return {"deck": deck.name,
            "slides": [{"page": p, "ok": p in files} for p in pgs]}


def _slide_png_path(doc_id, page, conn=None):
    """页图路径；不满足白名单一律 None（调用方给 404，不抛异常）。"""
    try:
        page, doc_id = int(page), int(doc_id)
    except (TypeError, ValueError):
        return None

    if doc_id <= 0 or page <= 0:
        return None

    own = conn is None
    if own:
        try:
            conn = _conn(readonly=True)
        except Exception:
            return None

    try:
        deck = _deck_dir_for(_doc_path_of(conn, doc_id))
    finally:
        if own and conn is not None:
            conn.close()

    if deck is None:
        return None

    name = _slide_files(deck).get(page)
    if not name or not re.fullmatch('slide_\\d+\\.png', name):
        return None

    p = deck / name
    try:
        if p.parent.resolve() != deck.resolve() or not p.is_file():
            return None
    except OSError:
        return None

    return p


def api_summary_kp(doc_id, kp_id, conn):
    """单个**末级知识点总结**（新方案：正文就是总结，不带块原文）。

    与 `/api/mindmap` 分工：树接口只内联 `lkp_id`/`lkp_ready` 标记，
    点开节点才来这里取总结正文。数据只读自 `logs/knowledge/*.json`。

    卡 C-0036 追加三样，都是**弹窗交互要用、且只有后端算得准**的东西：
      - `prev_id`/`prev_name`/`next_id`/`next_name` + `order_index`/`order_total`：
        导图先序里的前后邻居（前端「下一知识点」照这个跳，不自己排）；
      - `slide_deck`：这份课件在 figure_desc 里的目录名（空 = 没有目录）；
      - `slides`：这条知识点的每一页**有没有**课件图（前端据此决定放图还是提示）。
    """
    idx = _lkp_index(doc_id)
    kp = idx.get("by_id", {}).get(str(kp_id or "").strip())
    if kp is None:
        return {"error": "文档 " + str(doc_id) + " 里没有末级知识点「" + str(kp_id or "(空)") + "」（总结数据可能尚未生成）"}

    pages = kp.get("pages") if isinstance(kp.get("pages"), list) else []
    srcs = [s for s in (kp.get("sources") or []) if isinstance(s, dict)]
    mat = [m for m in (kp.get("material") or []) if isinstance(m, dict)]
    usage = kp.get("usage") if isinstance(kp.get("usage"), dict) else {}

    mat_ids = []
    for m in mat:
        try:
            mat_ids.append(int(m.get("chunk_id")))
        except (TypeError, ValueError):
            pass

    fig_pages = []
    for m in mat:
        if m.get("kind") == "图片理解":
            try:
                fig_pages.append(int(m.get("page")))
            except (TypeError, ValueError):
                pass

    meta = idx.get("meta") or {}

    seq = _lkp_seq(doc_id)
    ids = [x["id"] for x in seq]
    kid = str(kp.get("id") or "")
    try:
        pos = ids.index(kid)
    except ValueError:
        pos = -1

    prev = seq[pos - 1] if pos > 0 else None
    nxt = seq[pos + 1] if 0 <= pos < len(seq) - 1 else None
    fig = _slide_info(doc_id, pages, conn)

    n_notes = len(study.notes_for(str(kp.get("id") or ""), doc_id=doc_id))

    return {
        "doc_id": doc_id,
        "kp_id": str(kp.get("id") or ""),
        "name": str(kp.get("name") or ""),
        "node_name": str(kp.get("node_name") or ""),
        "path": [str(x) for x in (kp.get("node_path") if isinstance(kp.get("node_path"), list) else kp.get("path")) or []],
        "summary": str(kp.get("summary") or ""),
        "points": [str(x) for x in (kp.get("points") or [])],
        "sources": srcs,
        "level": kp.get("level"),
        "pages": pages,
        "pages_label": _pages_label(pages),
        "material_block_ids": mat_ids,
        "n_material_blocks": int(kp.get("n_material_blocks") or len(mat)),
        "n_body_blocks": int(kp.get("n_body_blocks") or 0),
        "n_fig_blocks": int(kp.get("n_fig_blocks") or 0),
        "material_chars": int(kp.get("material_chars") or 0),
        "figure_pages": sorted(set(fig_pages)),
        "insufficient": bool(kp.get("insufficient")),
        "insufficient_reason": str(kp.get("insufficient_reason") or ""),
        "status": _lkp_status(kp),
        "model": str(kp.get("model") or ""),
        "generated_at": str(kp.get("generated_at") or ""),
        "cost_cny": float(usage.get("cost_cny") or 0.0),
        "source_card": str(meta.get("source_card") or ""),
        "prompt_source": str(meta.get("prompt_source") or ""),
        "chapter": str(meta.get("chapter") or ""),
        "source": str(idx.get("source") or ""),
        # 卡 C-0056：知识点小窗底部「已绑定 N 条笔记」读的就是这个字段。
        # 2026-10-19 修：重建版算完 n_notes 却漏了写进返回 dict，前端拿不到 →
        # 刚保存时本地 +1、切到别的知识点再切回来又变回 0。
        "note_count": int(n_notes),
        "prev_id": prev["id"] if prev else "",
        "prev_name": prev["name"] if prev else "",
        "next_id": nxt["id"] if nxt else "",
        "next_name": nxt["name"] if nxt else "",
        "order_index": pos,
        "order_total": len(seq),
        "slide_deck": fig.get("deck") or "",
        "slides": fig.get("slides") or [],
        "merged": bool(kp.get("merged")),
        "merge_reason": str(kp.get("merge_reason") or ""),
        "merge_window": kp.get("merge_window"),
        "merge_rejected_reason": str(kp.get("merge_rejected_reason") or ""),
    }


def _ref_pages(source_ref):
    """`source_ref` → 页码列表 + 解析说明（卡 C-0055）。

    实测（`subjects/有机化学` 776 块）只有三种形态：`p.N 图`（识图块，4616 块）、
    `p.N`（正文块，612 块）、`p.N-M`（跨页块，164 块）；`slide N` 是更早的
    pptx 写法，`core/chat.py::ref_label` 也认它，这里一并支持。

    ⚠ **认不出就不猜**：返回空列表 + 一句人话。猜页号会张冠李戴地放出**别的页**
    的课件图，那比"没有图"坏得多（C-0036 的同一条底线）。
    """
    s = str(source_ref or "").strip()
    out = {"pages": [], "kind": "none", "note": "", "truncated": False}
    if not s:
        out["note"] = "这条出处没有页码（source_ref 为空），定位不到课件页。"
        return out
    unit = "页"
    m = _REF_PAGE_RE.match(s)
    if m:
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        out["kind"] = "figure" if m.group(3) else ("span" if b != a else "page")
    else:
        m = _REF_SLIDE_RE.match(s)
        if not m:
            out["note"] = (
                "这条出处写的是「%s」，里面没有认得出的页码（只认 p.N / p.N-M / p.N 图 / slide N 这几种），"
                "所以定位不到课件页——与其放一张可能是别页的图，不如不放。" % s)
            return out
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        out["kind"] = "slide_span" if b != a else "slide"
        unit = "张幻灯片"
    if b < a:
        b, a = a, b
    pages = list(range(a, b + 1))
    if len(pages) > _REF_MAX_PAGES:
        out["truncated"] = True
        out["note"] = "这条出处覆盖 %d %s（%s），页号一次列不下，这里只取前 %d 页。" % (
            len(pages), unit, s, _REF_MAX_PAGES)
        pages = pages[: _REF_MAX_PAGES]
    out["pages"] = pages
    return out


def _page_kp_index(doc_id):
    """`{页号: [知识点条目, …]}` + (state, 版本, 空态说明) （卡 C-0055）。

    优先走**页签那一套**（`_lkp_index`：进程内有缓存、与页签严格同口径）。
    只有当产物是"比页签认的版本更新的**已知**版本"（眼下就是 v3）时才自己
    读一次文件兜底——否则新版本一铺开，这条链路会对所有课件失效，人点角标
    永远只看到"没有知识点"，而真相是"版本号没跟上"。

    版本判据一律是 `meta.pipeline_version` 白名单（卡 C-0049 的口号：
    **不按"有没有某个字段"猜**）；白名单外的版本退回空态，照旧明说原因。
    """
    art = _artifact_load(doc_id)
    ver = str((art.get("meta") or {}).get("pipeline_version") or "")
    if art["state"] == _ARTIFACT_V2_STATE:
        return (dict(_lkp_index(doc_id).get("by_page") or {}), "v2", ver, "")
    if art["state"] == _ARTIFACT_LEGACY and ver in _KP_READABLE_VERSIONS:
        p = _artifact_file(doc_id)
        try:
            raw = json.loads(p.read_text(encoding="utf-8")) if p else {}
        except (OSError, ValueError):
            raw = {}
        by_page = {}
        for kp in (raw.get("knowledge_points") or []):
            if not isinstance(kp, dict):
                continue
            for pg in (kp.get("pages") or []):
                try:
                    by_page.setdefault(int(pg), []).append(kp)
                except (TypeError, ValueError):
                    continue
        if by_page:
            return (by_page, "v3", ver, "")
        return ({}, "other", ver, "这份课件的产物（%s）里没有可按页查的知识点。" % (ver or "无版本号"))
    msg = _ARTIFACT_MESSAGE.get(art["state"], "")
    if art["state"] == _ARTIFACT_LEGACY and ver:
        msg = f"这份课件的产物是 {ver}（页签认的版本是 {_ARTIFACT_V2}），按页归属还读不出来。"
    return (
        {},
        "none" if art["state"] == _ARTIFACT_NONE else (
            "legacy" if art["state"] == _ARTIFACT_LEGACY else (
                "incomplete" if art["state"] == _ARTIFACT_INCOMPLETE else "other")),
        ver,
        msg or "这份课件暂时没有可按页查的知识点。",
    )


def api_page_kps(doc_id, source_ref, page):
    """出处小窗要的一页：课件图有没有 + 归属这一页的知识点有哪些（卡 C-0055）。

    入参两条路：`source_ref`（原样给，后端解析页码）或 `page`（已知页号时直接用）。
    返回里两样都在：`slide`（这一页有没有图 + 每页的 ok）与 `kps`（该页知识点，
    **一页挂多条时全部列出**）。查不到不是错误——照旧 HTTP 200 + 明确说法。
    """
    try:
        doc_id = int(doc_id)
    except (TypeError, ValueError):
        doc_id = 0

    ref = _ref_pages(source_ref)
    if not ref["pages"] and page:
        try:
            ref = {
                "pages": [int(page)],
                "kind": "page",
                "note": "",
                "truncated": False,
            }
        except (TypeError, ValueError):
            pass

    pages = ref["pages"]
    if doc_id <= 0:
        return {
            "doc_id": doc_id,
            "source_ref": str(source_ref or ""),
            "ref_kind": ref["kind"],
            "ref_note": ref["note"] or "没给 doc_id，认不出这是哪份资料。",
            "ref_truncated": False,
            "pages": [],
            "page": 0,
            "slide": {
                "deck": "",
                "slides": [],
                "page": 0,
                "ok": False,
                "n_ok": 0,
            },
            "kp_state": "none",
            "kp_version": "",
            "kp_message": "没给 doc_id。",
            "kps": [],
            "n_kps": 0,
        }

    fig = _slide_info(doc_id, pages) if pages else {"deck": "", "slides": []}
    ok_pages = [s["page"] for s in fig["slides"] if s.get("ok")]

    by_page, kp_state, kp_ver, kp_msg = _page_kp_index(doc_id)
    seen = set()
    kps = []
    for pg in pages:
        for kp in (by_page.get(pg) or []):
            kid = str(kp.get("id") or "").strip()
            if not kid or kid in seen:
                continue
            seen.add(kid)
            kp_pages = [int(x) for x in (kp.get("pages") or []) if str(x).strip().lstrip("-").isdigit()]
            kps.append({
                "kp_id": kid,
                "name": str(kp.get("name") or ""),
                "node_name": str(kp.get("node_name") or ""),
                "pages": kp_pages,
                "pages_label": _pages_label(kp_pages),
                "ready": _lkp_status(kp) == "ready",
                "insufficient": bool(kp.get("insufficient")),
                "hit_pages": [p for p in pages if p in kp_pages],
            })

    first = pages[0] if pages else 0

    return {
        "doc_id": doc_id,
        "source_ref": str(source_ref or ""),
        "ref_kind": ref["kind"],
        "ref_note": ref["note"],
        "ref_truncated": bool(ref["truncated"]),
        "pages": pages,
        "page": first,
        "slide": {
            "deck": fig["deck"],
            "slides": fig["slides"],
            "page": first,
            "ok": bool(fig["slides"][0].get("ok")) if fig["slides"] else False,
            "n_ok": len(ok_pages),
            "n_pages": len(fig["slides"]),
        },
        "kp_state": kp_state,
        "kp_version": kp_ver,
        "kp_message": kp_msg,
        "kps": kps,
        "n_kps": len(kps),
    }


def api_mindmap(conn, doc_id):
    """课件思维导图（卡 C-0050：**数据源 = 整理产物**）。

    不带 doc_id：列页签能看的课件（库内文档 ∪ 有产物的文档），每份带产物状态。
    带了 doc_id：返回该课件的结构树（产物 `structure.root`）+ 知识点标记。

    ★ 三种"显示不了"的状态都**不是错误**（照旧 HTTP 200 + 明确文案，见
    `_ARTIFACT_MESSAGE`）：`none` 还没整理过 / `legacy` 旧格式（人拍板：保留
    文件、页签不显示）/ `incomplete` 上次整理中断。前端 `api()` 见到 `error`
    字段会抛异常，那样用户看到的是"出错"，而真相是"这份还没整理"。

    ★ 兼容字段：`has_outline` 保留为 `has_structure` 的**同义别名**（老脚本读它
    不会 KeyError）；它的语义已从"有入库结构缓存"变成"有可展示的整理结构树"，
    真正的判据看 `artifact_state`。
    """
    if doc_id <= 0:
        items = _artifact_doc_list(conn)
        return {"documents": items, "n": len(items), "data_source": "artifact"}

    art = _artifact_load(doc_id)
    row = conn.execute(
        "SELECT id, title, kind, path, n_chunks, status FROM documents WHERE id=?",
        (doc_id,)).fetchone()
    doc = dict(row) if row else {}
    meta = art.get("meta") if isinstance(art.get("meta"), dict) else {}
    sm = art.get("summary") or {}
    state = str(art.get("state") or _ARTIFACT_NONE)
    ok = state == _ARTIFACT_V2_STATE
    idx = _lkp_index(doc_id)

    out = {
        "doc_id": doc_id,
        "title": doc.get("title") or ("文档 %d" % doc_id),
        "kind": doc.get("kind") or "",
        "in_library": row is not None,
        "data_source": "artifact",
        "artifact_state": state,
        "artifact_source": str(art.get("source") or art.get("name") or ""),
        "artifact_message": "" if ok else _ARTIFACT_MESSAGE.get(state, ""),
        "has_structure": ok,
        "has_outline": ok,
        "pipeline_version": str(sm.get("pipeline_version") or ""),
        "lkp_available": bool(idx.get("by_id")),
        "lkp_total": len(idx.get("by_id") or {}),
        "lkp_source": str(idx.get("source") or ""),
        "lkp_source_card": str(sm.get("source_card") or meta.get("source_card") or ""),
    }

    if not ok:
        out.update({
            "tree": [],
            "n_pages": 0,
            "n_nodes": 0,
            "message": out["artifact_message"],
            "model": "",
            "created_at": None,
            "generated_at": "",
            "cost_cny": 0.0,
            "lkp_ready": 0,
            "lkp_pending": 0,
            "lkp_leaf_nodes": 0,
            "lkp_multi_nodes": 0,
            "lkp_parent_pages": 0,
            "lkp_stats": {},
            "lkp_excluded_pages": [],
            "lkp_excluded_reason": "",
        })
        return out

    tree, stats = _artifact_tree(art["doc"], doc_id)
    out.update({
        "model": str(sm.get("model") or ""),
        "created_at": sm.get("created_at"),
        "generated_at": str(sm.get("generated_at") or ""),
        "cost_cny": float(sm.get("cost_cny") or 0.0),
        "n_pages": int(sm.get("n_pages") or 0),
        "n_nodes": _n_nodes(tree),
        "tree": tree,
        "message": "",
        "lkp_ready": stats.get("ready", 0),
        "lkp_pending": stats.get("pending", 0),
        "lkp_leaf_nodes": stats.get("leaf", 0),
        "lkp_multi_nodes": stats.get("multi", 0),
        "lkp_parent_pages": 0,
        "lkp_stats": stats,
    })

    cov = meta.get("coverage")
    if isinstance(cov, dict):
        out["lkp_coverage"] = {
            "pages_all": int(cov.get("pages_all") or cov.get("pages_total") or 0),
            "n_covered": int(cov.get("n_covered") or 0),
            "overlap_pages": [
                int(p) for p in (cov.get("overlap_pages") or [])
                if str(p).lstrip("-").isdigit()
            ],
        }

    return out


def _terms_of(art):
    """产物里的名词表；没有（旧产物 / 坏文件）就是空表——**任何情况下都不抛异常**。"""
    doc = art.get('doc')
    if not isinstance(doc, dict):
        return []
    raw = doc.get('terms')
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, dict)]
    return []


def _terms_own_file(doc_id):
    """**独立名词解释产物**（卡 C-0058）：`logs/knowledge/<doc_id>-名词解释.json`。

    返回 `(terms, meta)`；文件不在 / 坏了 / 没有 `terms` → `([], {})`。
    路径从 `kp_build` 取（`terms_out_file`），不在这里另抄一份目录口径。
    只读、不调模型、不写任何东西——与 `_artifact_load()` 同一套容错风格。
    """
    try:
        p = Path(kp_build_mod.terms_out_file(int(doc_id)))
    except Exception:
        return [], {}

    if not p.is_file():
        return [], {}

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], {}

    if not isinstance(raw, dict):
        return [], {}

    ts = raw.get("terms")
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    if isinstance(ts, list):
        return [t for t in ts if isinstance(t, dict)], meta
    return [], meta


def _terms_state(art, terms, own_meta):
    ver = str((art.get("meta") or {}).get("pipeline_version") or "")
    if terms:
        return ("ok", "")
    own_meta = own_meta or {}
    if own_meta:
        return (
            "empty",
            "该课件的名词解释产物里一个名词都没有（筛选模式 %s）：这份课件可能确实没有值得考名词解释的术语。"
            % str((own_meta.get("terms_filter") or {}).get("mode") or "—"),
        )
    state = str(art.get("state") or _ARTIFACT_NONE)
    if state == _ARTIFACT_NONE:
        return ("stale", "该课件还没有整理过知识点；名词解释是**独立功能**（`tools/terms_build.py --doc <id>`），生成之后就会显示在这里。")
    if state == _ARTIFACT_INCOMPLETE:
        return ("incomplete", "这份课件的产物不完整（可能上次整理中断）；重新整理一次就会有知识点，名词解释另用 `tools/terms_build.py` 生成。")
    if state == _ARTIFACT_LEGACY:
        return (
            "legacy",
            "这份课件的产物是旧格式（%s），页签读不出来；重新整理一次就会有知识点。"
            % (ver or "无版本号"),
        )
    # [C-0120] `in` 的两个操作数写反了：`_KP_TERMS_IN_OWN_FILE` 是**元组**
    # （`tuple(sorted({PIPELINE_VERSION_V4} - {""}))` ⇒ `("C-0058-v4",)`），
    # `ver` 是字符串。元组 in 字符串 ⇒
    #   TypeError: 'in <string>' requires string as left operand, not tuple
    # 于是**只要产物是新版（有 pipeline_version）且还没生成名词解释**，
    # `/api/terms` 就 500 —— 连不带 doc_id 的清单分支（`doc_id=0`）也一起炸，
    # 因为它是逐份调 `_terms_payload()` 的。这条缺陷在 C-0108 那轮基线里没暴露，
    # 只因为当时测完就清空了产物（`state == NONE` 在 L8398 提前 return）。
    # 判据来源：同文件 L8387 `ver = str(...pipeline_version...)`（字符串），
    # 而集合语义写在常量名里（"terms 在产物自己文件里的那些版本"）。
    if ver in _KP_TERMS_IN_OWN_FILE:
        return ("empty", "该课件还没生成名词解释。卡 C-0058 起名词解释是**独立功能**（`tools/terms_build.py --doc <id>`），产物是 `logs/knowledge/<doc_id>-名词解释.json`；生成后这里就有内容。")
    return (
        "empty",
        "该课件暂无名词解释：这份产物（%s）是名词解释功能上线前整理的，没有这一节；重新整理一次就会出现。"
        % (ver or "—"),
    )


def _terms_payload(art, doc_id):
    """一份产物的名词解释（两个入口共用；字段口径只在这里写一次）。

    卡 C-0058 起取值顺序：**独立产物优先 → 知识点产物顶层 `terms` 回落**。
    `terms_source` 如实写明这一次是从哪儿读到的（`own_file` / `kp_artifact` / 空）。
    """
    if doc_id:
        terms, own_meta = _terms_own_file(doc_id)
    else:
        terms, own_meta = [], {}
    source = "own_file" if terms else ""
    if not terms:
        terms = _terms_of(art)
        source = "kp_artifact" if terms else ""
    state, msg = _terms_state(art, terms, own_meta)
    out = {
        "terms_state": state,
        "message": msg,
        "n_terms": len(terms),
        "terms_source": source,
        "pipeline_version": str((art.get("meta") or {}).get("pipeline_version") or ""),
        "terms_pipeline_version": str(own_meta.get("pipeline_version") or ""),
        "terms_generated_at": str(own_meta.get("generated_at") or ""),
        "terms": [],
    }
    if state != "ok":
        return out
    for t in terms:
        row = {k: t.get(k) for k in _TERMS_FIELDS}
        for k in ("term", "en", "en_source", "def", "first_kp_id", "first_kp_name"):
            row[k] = str(row.get(k) or "")
        row["kp_ids"] = [str(x) for x in (row.get("kp_ids") or [])]
        for k in ("first_page", "n_occurrences"):
            try:
                row[k] = int(row.get(k) or 0)
            except (TypeError, ValueError):
                row[k] = 0
        if not row["term"]:
            continue
        out["terms"].append(row)
    out["n_terms"] = len(out["terms"])
    return out


def api_terms(conn, doc_id):
    """名词解释栏（卡 C-0053；卡 C-0058 起数据源是**独立产物优先**）。

    不带 `doc_id` 列课件清单，带了就给这一份的名词。
    只读产物，**不调模型、不写库**。"没有名词"的几种情形都照旧 HTTP 200 +
    `terms_state` + 一句人话（`message`）——前端据此给空态，不是报错。
    """
    if doc_id <= 0:
        items = []
        for d in _artifact_doc_list(conn):
            try:
                did = int(d["doc_id"])
            except (TypeError, ValueError):
                continue
            art = _artifact_load(did)
            pl = _terms_payload(art, did)
            item = dict(d)
            item.update({
                "n_terms": pl["n_terms"],
                "terms_state": pl["terms_state"],
                "terms_source": pl["terms_source"],
                "pipeline_version": pl["pipeline_version"],
            })
            items.append(item)
        return {"documents": items, "n": len(items), "data_source": "artifact"}

    art = _artifact_load(doc_id)
    row = None
    try:
        row = conn.execute(
            "SELECT id, title FROM documents WHERE id=?",
            (doc_id,)).fetchone()
    except sqlite3.Error:
        row = None

    out = {
        "doc_id": doc_id,
        "title": (str(dict(row).get("title")) if row else "") or ("文档 %d" % doc_id),
        "in_library": row is not None,
        "artifact_state": str(art.get("state") or _ARTIFACT_NONE),
        "n_kps": int((art.get("summary") or {}).get("n_kps") or 0),
    }
    out.update(_terms_payload(art, doc_id))
    return out


def _kp_options_sig():
    """缓存签名：DB 文件 + outline/knowledge 目录的文件时间戳（任一变化就重算）。

    2026-10-24 修复：原来只盯 outline + knowledge 两个目录，**新入库的课件**
    既不会写 outline（结构分析关闭/已改 v3 管线）也没有 knowledge 产物，
    签名因此不变 → 缓存 20 秒内继续返回旧清单。现在把 DB 文件本身也纳入签名，
    新文档入库（DB 大小/mtime 变化）会立即失效缓存。
    """
    parts = []
    try:
        st = Path(config.DB_PATH).stat()
        parts.append(("db", st.st_size, st.st_mtime_ns))
    except OSError:
        pass
    for d in (Path(config.LOG_DIR) / 'outline', _knowledge_dir()):
        try:
            for p in sorted(d.glob('*.json')):
                st = p.stat()
                parts.append((p.name, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(parts)


def api_kp_build_options(conn):
    global _KP_OPT_CACHE          # C-0104：函数内要写这个模块级缓存，缺声明会
    sig = _kp_options_sig()       # UnboundLocalError → /api/kp-build/options 500
    now = time.time()
    if _KP_OPT_CACHE["sig"] == sig and _KP_OPT_CACHE["data"] is not None:
        if now - _KP_OPT_CACHE["t"] < _KP_OPT_TTL:
            data = dict(_KP_OPT_CACHE["data"])
            data["cached"] = True
            data["running"] = kp_build_mod.is_running()
            return data

    docs = {int(r["id"]): dict(r) for r in conn.execute(
        "SELECT id, title, kind, role, corpus, status, n_chunks, "
        "       COALESCE(updated_at, 0) AS updated_at FROM documents").fetchall()}
    outline_ids = set()
    for p in sorted(_outline_dir().glob("*.json")):
        if p.stem.isdigit():
            outline_ids.add(int(p.stem))

    # 2026-10-24 修复：v3 知识点管线**不再依赖 logs/outline/<doc>.json**
    # （它直接读库里的文本页）。旧实现只遍历 outline 文件，导致"新入库的课件
    # 不写 outline → 在可整理清单里完全看不到"——用户实测问的就是这个。
    # 现在以 DB 里的课件为准，outline 只作为历史兜底来源：
    #   · 有 role 的按 role='courseware'；老数据没有 role 时，按 pptx/pdf 且非教材收；
    #   · 教材（role='textbook'）不算可整理课件；
    #   · 估算不通过的课件不再静默丢掉，进 excluded 并带原因，页面可提示。
    candidates = set(outline_ids)
    for did, d in docs.items():
        role = str(d.get("role") or "").strip().lower()
        kind = str(d.get("kind") or "").strip().lower()
        if role == "courseware" or (not role and kind in ("pptx", "ppt", "pdf")):
            candidates.add(did)

    items: list = []
    excluded: list = []
    for did in sorted(candidates):
        try:
            # v3 estimate 不需要 outline_path；传了也会被忽略。
            est = kp_build_mod.estimate(did)
        except Exception as e:
            excluded.append({
                "doc_id": did,
                "title": (docs.get(did) or {}).get("title") or f"文档 {did}",
                "error": f"{type(e).__name__}: {e}",
                "status": (docs.get(did) or {}).get("status") or "",
            })
            continue
        if not est.get("ok"):
            excluded.append({
                "doc_id": did,
                "title": (docs.get(did) or {}).get("title") or f"文档 {did}",
                "error": str(est.get("error") or "这份资料没有可总结的文本页"),
                "status": (docs.get(did) or {}).get("status") or "",
            })
            continue
        prev = kp_build_mod.prev_info(did)
        d = docs.get(did) or {}
        ok_done, done_note = kp_batch_mod.done_state(
            {"n_pages": est["n_pages"], "n_leaf_pages": est["n_leaf_pages"]},
            prev)
        items.append({
            "doc_id": did,
            "title": d.get("title") or est.get("title") or f"文档 {did}",
            "in_library": did in docs,
            "corpus": d.get("corpus") or "",
            "kind": d.get("kind") or "",
            "status": d.get("status") or "",
            "n_pages": est["n_pages"],
            "n_leaf_pages": est["n_leaf_pages"],
            "excluded_parent_pages": est["excluded_parent_pages"],
            "kp_min": est["kp_min"],
            "kp_max": est["kp_max"],
            "est_calls": est["est_calls"],
            "est_cost_cny": est["est_cost_cny"],
            "material_chars_total": est["material_chars_total"],
            "has_summary": bool(prev["exists"] and prev["n_kps"]),
            "n_kps": prev["n_kps"],
            "summary_source": Path(prev["path"]).name if prev["exists"] else "",
            "summary_version": prev["pipeline_version"],
            "artifact_state": str(_lkp_index(did).get("state") or _ARTIFACT_NONE),
            "summary_card": prev["source_card"],
            "summary_cost_cny": prev["cost_cny"],
            "resumable": prev["resumable"],
            "done_pages": prev["n_done_pages"],
            "pages_total": prev["pages_total"],
            "already_done": bool(ok_done),
            "done_note": done_note,
            "out_path": est["out_path"],
            "out_name": Path(est["out_path"]).name,
        })
    items.sort(key=lambda x: x["doc_id"])
    excluded.sort(key=lambda x: x["doc_id"])
    data = {
        "documents": items,
        "n": len(items),
        "excluded": excluded,
        "n_excluded": len(excluded),
        "running": kp_build_mod.is_running(),
        "progress": kp_build_mod.progress_snapshot(),
        "stages": list(kp_build_mod.KP_STAGES),
        "max_merge_pages": kp_build_mod.MAX_MERGE,
        "prompt_path": str(kp_build_mod.prompt_file()),
        "est_rule": "预估是**上界**：按每页一条（完全不合并）算调用数；合并掉的页不会单独产生一次调用，所以真实花费只会更少。",
        "write_note": "整理只写 logs/knowledge/ 下的产物文件，不写数据库任何表。",
    }
    _KP_OPT_CACHE = {"sig": sig, "t": now, "data": data}
    return data


def api_kp_build_progress():
    """整理进度快照（字段与 `/api/ingest/progress` 同构，前端复用同一个进度条）。"""
    return kp_build_mod.progress_snapshot()


# ---------------- 特性2：教材补充 / 教材页图（task-27）后端胶水 ----------------
# 真正的零模型匹配与产物读写在 `core/teach.py`（另一智能体并行新增）：
#   build_supplement / supplement_status / supplement_for_kp / page_png。
# 这里只做转调与兜底：函数缺失或抛异常 → 200/4xx JSON，绝不让页面 500。


# ---------------- 特性2/3：教材补充（含 AI）后端胶水（task-27 + task-30） ----------------
# 真正的零模型匹配 / AI 分析 / 产物读写在 `core/teach.py`（另一智能体并行新增）：
#   build_supplement / supplement_status / supplement_for_kp / page_png
#   + task-30 的 AI 字段与 analyze_kp_supplement（名字以实际落地为准，见 _teach_ai_fn）。
# 这里只做转调、字段兜底与旧版产物判定：函数缺失/异常 → 200/4xx JSON，绝不让页面 500。


def _teach_mod():
    from core import teach as _tm
    return _tm


def _teach_ai_model():
    """AI 补充用的模型名（与知识点总结同一模型；取不到给默认值）。"""
    for mod_name in ("kp_build", "preview"):
        try:
            mod = __import__("core." + mod_name, fromlist=["x"])
            for nm in ("model_name", "default_model", "kp_model", "ai_model"):
                fn = getattr(mod, nm, None)
                if callable(fn):
                    try:
                        v = fn()
                        if v:
                            return str(v)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            continue
    return "deepseek-flash"


def _teach_estimate_fallback():
    """后端还没给 estimate 时的兜底估价（1 次小调用；真实估价以后端为准）。"""
    return {"calls": 1, "cost_cny_max": 0.02, "model": _teach_ai_model(),
            "thinking": True, "effort": "high", "source": "fallback_estimate"}


def _teach_normalize_estimate(est):
    """把后端 estimate 归一：缺成本/模型时补兜底值（不丢后端的 error/ok 字段）。"""
    fb = _teach_estimate_fallback()
    if not isinstance(est, dict):
        return fb
    out = dict(est)
    try:
        cost = float(out.get("cost_cny_max") or 0)
    except (TypeError, ValueError):
        cost = 0.0
    if cost <= 0:
        out["cost_cny_max"] = fb["cost_cny_max"]
        out.setdefault("model", fb["model"])
        out.setdefault("calls", fb["calls"])
        out["estimate_is_fallback"] = True
    return out


def _teach_estimate_map(doc_id, conn=None):
    """AI 章节映射的预估（后端 estimate_map；0 模型 0 写盘）。取不到返回 None。"""
    fn = getattr(_teach_mod(), "estimate_map", None)
    if not callable(fn):
        return None
    try:
        try:
            r = fn(int(doc_id), conn=conn)
        except TypeError:
            r = fn(int(doc_id))
    except Exception:  # noqa: BLE001
        return None
    return r if isinstance(r, dict) else None


def _teach_supplement_file(doc_id):
    """读沙箱/真实 logs 里的 `<doc>-教材补充.json`（只读；坏文件返回 {}）。"""
    try:
        p = Path(config.LOG_DIR) / "knowledge" / ("%d-教材补充.json" % int(doc_id))
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _teach_derive(doc_id):
    """从产物文件里推出 UI 需要的 chapter_map / ai 概览 / 是否旧版。

    返回 `(chapter_map, ai_summary, legacy, entries)`。
    旧版产物（task-27 的纯本地格式）没有 `chapter_map`、任何 entry 也没有 `ai` 字段 →
    legacy=True，前端显示"旧版教材补充，建议重新 AI 定位"。
    """
    art = _teach_supplement_file(doc_id)
    meta = art.get("meta") if isinstance(art.get("meta"), dict) else {}
    kps = art.get("kps") if isinstance(art.get("kps"), dict) else {}
    entries = [e for e in kps.values() if isinstance(e, dict)] if kps else []
    has_ai = any(isinstance(e.get("ai"), dict) and e["ai"].get("status")
                 for e in entries)
    meta_cm = meta.get("chapter_map") if isinstance(meta.get("chapter_map"), dict) else {}
    meta_cm = dict(meta_cm or {})
    cm = dict(meta_cm)
    if not cm:
        for e in entries:
            ch = e.get("chapter") if isinstance(e.get("chapter"), dict) else {}
            if not ch and not e.get("page_label"):
                continue
            if str(e.get("status") or "") not in ("done", "no_match"):
                continue
            cm = {"chapter_no": int(ch.get("no") or 0),
                  "chapter_name": str(ch.get("name") or ""),
                  "page_label": str(e.get("page_label") or ""),
                  "source": str(e.get("source") or "local_fallback"),
                  "cost_cny": 0.0, "confidence": None}
            break
    if cm and "n_sections" not in cm:
        ns = 0
        for e in entries:
            secs = e.get("sections") if isinstance(e.get("sections"), list) else []
            ns = max(ns, len(secs))
        if not ns:
            ns = int(meta.get("n_sections") or 0)
        cm["n_sections"] = ns
    ai_sum = {
        "enabled": True,
        "model": _teach_ai_model(),
        "n_ai_ready": sum(1 for e in entries
                          if isinstance(e.get("ai"), dict)
                          and e["ai"].get("status") == "done"),
        "n_ai_missing": sum(1 for e in entries
                            if not (isinstance(e.get("ai"), dict)
                                    and e["ai"].get("status"))),
    }
    legacy = bool(entries or meta) and not meta_cm and not has_ai
    return cm, ai_sum, legacy, entries


def _teach_augment_status(doc_id, st, conn=None):
    """把后端新/旧状态归一成前端需要的顶层字段（chapter_map / ai / estimate / legacy）。"""
    if not isinstance(st, dict):
        st = {}
    st.setdefault("ok", True)
    sup = st.get("supplement") if isinstance(st.get("supplement"), dict) else {}
    ai = st.get("ai") if isinstance(st.get("ai"), dict) else {}
    aimap = ai.get("map") if isinstance(ai.get("map"), dict) else {}
    cm, ai_sum, derived_legacy, _entries = _teach_derive(doc_id)
    cm = dict(cm or {})
    # 后端 ai.map（新版）优先补章号/名称/来源/成本；artifact meta 里的 page_label/n_sections 保留
    if aimap:
        if int(aimap.get("chapter_no") or 0):
            cm["chapter_no"] = int(aimap.get("chapter_no") or 0)
        if aimap.get("chapter_name"):
            cm["chapter_name"] = str(aimap.get("chapter_name") or "")
        if aimap.get("source"):
            cm["source"] = str(aimap.get("source") or "")
        if aimap.get("confidence"):
            cm["confidence"] = str(aimap.get("confidence") or "")
        try:
            c = float(aimap.get("cost_cny") or sup.get("ai_map_cost_cny") or 0)
        except (TypeError, ValueError):
            c = 0.0
        if c > 0:
            cm["cost_cny"] = round(c, 6)
    if cm and ("chapter_no" in cm or cm.get("chapter_name")):
        st["chapter_map"] = cm
    if not ai or "n_ai_ready" not in ai:
        st["ai"] = ai_sum
    else:
        st["ai"] = ai
    if not isinstance(st.get("estimate"), dict):
        est = _teach_estimate_map(doc_id, conn=conn)
        st["estimate"] = _teach_normalize_estimate(est) if est else \
            _teach_estimate_fallback()
    legacy = bool(sup.get("legacy") or sup.get("stale")) or derived_legacy
    st["legacy"] = legacy
    if legacy:
        st["needs_backfill"] = True
        if not str(st.get("reason") or "").strip():
            st["reason"] = "旧版教材补充（纯本地匹配，无 AI 章节映射）→ 建议重新 AI 定位章节与小节"
    return st


def _teach_augment_kp(doc_id, kp_id, r):
    if not isinstance(r, dict):
        return {"ok": False, "available": False, "textbook": {}, "entry": {},
                "note": "教材补充返回异常", "estimate": _teach_estimate_fallback()}
    r.setdefault("ok", True)
    entry = r.get("entry") if isinstance(r.get("entry"), dict) else {}
    if not isinstance(entry.get("ai"), dict) and isinstance(r.get("ai"), dict) \
            and r.get("ai"):
        entry["ai"] = r["ai"]          # 新版后端把 ai 放在顶层：给前端并到 entry
    if not isinstance(entry.get("chapter"), dict) or not entry.get("chapter"):
        cm, _a, _l, _e = _teach_derive(doc_id)
        if cm:
            entry["chapter"] = cm
    r["entry"] = entry
    r["estimate"] = _teach_normalize_estimate(
        r.get("estimate") if isinstance(r.get("estimate"), dict) else
        (_teach_estimate_map(doc_id) or {}))
    if not isinstance(r.get("legacy"), bool):
        cm, _a, legacy, _e = _teach_derive(doc_id)
        r["legacy"] = bool(legacy)
    return r


def _teach_ai_fn():
    """找后端 task-30 的按需 AI 分析函数（名字以实际落地为准）。"""
    tm = _teach_mod()
    for nm in ("analyze_kp_supplement", "build_kp_supplement",
               "kp_ai_supplement", "analyze_kp"):
        fn = getattr(tm, nm, None)
        if callable(fn):
            return fn
    return None


def api_textbook_supplement_status(conn, doc_id):
    """这份课件的教材补充状态（只读、零写盘；含 AI 章节映射概览）。"""
    try:
        doc_id = int(doc_id or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "doc_id 必须是整数"}
    if doc_id <= 0:
        return {"ok": False, "error": "缺少 doc_id"}
    fn = getattr(_teach_mod(), "supplement_status", None)
    if not callable(fn):
        return {"ok": False, "error": "教材补充后端还没就绪（缺 supplement_status）"}
    try:
        try:
            r = fn(doc_id, conn=conn)
        except TypeError:
            r = fn(doc_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "读教材补充状态失败：%s" % e}
    return _teach_augment_status(doc_id, r, conn=conn)


def api_textbook_supplement_kp(conn, doc_id, kp_id):
    """某条知识点的教材补充（AI 字段 + 本地原文 + estimate；只读）。"""
    try:
        doc_id = int(doc_id or 0)
    except (TypeError, ValueError):
        doc_id = 0
    kp_id = str(kp_id or "").strip()
    if doc_id <= 0 or not kp_id:
        return {"ok": False, "available": False, "textbook": {}, "entry": {},
                "note": "缺少 doc_id 或 kp_id",
                "estimate": _teach_estimate_fallback()}
    fn = getattr(_teach_mod(), "supplement_for_kp", None)
    if not callable(fn):
        return {"ok": False, "available": False, "textbook": {}, "entry": {},
                "note": "教材补充后端还没就绪（缺 supplement_for_kp）",
                "estimate": _teach_estimate_fallback()}
    try:
        r = fn(doc_id, kp_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "available": False, "textbook": {}, "entry": {},
                "note": "读教材补充失败：%s" % e,
                "estimate": _teach_estimate_fallback()}
    return _teach_augment_kp(doc_id, kp_id, r)


def api_textbook_supplement_kp_estimate(doc_id, kp_id):
    """单条 KP 的 AI 补充估价：优先后端新函数，否则给兜底估价。"""
    try:
        doc_id = int(doc_id or 0)
    except (TypeError, ValueError):
        doc_id = 0
    kp_id = str(kp_id or "").strip()
    tm = _teach_mod()
    for nm in ("estimate_kp_supplement", "kp_supplement_estimate",
               "estimate_kp_ai"):
        fn = getattr(tm, nm, None)
        if not callable(fn):
            continue
        try:
            try:
                r = fn(doc_id, kp_id)
            except TypeError:
                r = fn(doc_id)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(r, dict):
            est = r.get("estimate") if isinstance(r.get("estimate"), dict) else r
            if isinstance(est, dict) and ("cost_cny_max" in est or "calls" in est):
                return {"ok": True, "estimate": _teach_normalize_estimate(est)}
    return {"ok": True, "estimate": _teach_estimate_fallback()}


def api_textbook_supplement_kp_build(payload):
    """按需生成单条知识点的 AI 教材补充（同步 1 次调用；不持全局 _lock）。"""
    try:
        doc_id = int(payload.get("doc_id") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "doc_id 必须是整数"}
    kp_id = str(payload.get("kp_id") or "").strip()
    if doc_id <= 0 or not kp_id:
        return {"ok": False, "error": "缺少 doc_id 或 kp_id"}
    force = bool(payload.get("force"))
    fn = _teach_ai_fn()
    if fn is None:
        return {"ok": False,
                "error": "AI 教材补充后端还没就绪（缺 analyze_kp_supplement）"}
    try:
        try:
            r = fn(doc_id, kp_id, force=force)
        except TypeError:
            r = fn(doc_id, kp_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "AI 生成失败：%s" % e}
    if not isinstance(r, dict):
        return {"ok": False, "error": "AI 生成返回异常"}
    r.setdefault("ok", True)
    r["estimate"] = _teach_normalize_estimate(
        r.get("estimate") if isinstance(r.get("estimate"), dict) else
        (_teach_estimate_map(doc_id) or {}))
    if "ai" not in r and isinstance(r.get("entry"), dict):
        r["ai"] = r["entry"].get("ai")
    return r


def api_textbook_supplement_build(payload):
    """补做教材补充（含 AI 章节映射 + 本地小节定位）：同步零/小模型调用。"""
    try:
        doc_id = int(payload.get("doc_id") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "doc_id 必须是整数"}
    if doc_id <= 0:
        return {"ok": False, "error": "缺少 doc_id"}
    force = bool(payload.get("force"))
    fn = getattr(_teach_mod(), "build_supplement", None)
    if not callable(fn):
        return {"ok": False, "error": "教材补充后端还没就绪（缺 build_supplement）"}
    try:
        r = fn(doc_id, force=force)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "生成教材补充失败：%s" % e}
    if not isinstance(r, dict):
        return {"ok": False, "error": "教材补充返回异常"}
    meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
    # 新版 build 会写 meta.chapter_map/ai_map_source；旧版没有 → 读产物补 chapter_map/ai
    cm, ai_sum, legacy, _entries = _teach_derive(doc_id)
    if not isinstance(meta.get("chapter_map"), dict) or not meta.get("chapter_map"):
        if cm:
            meta["chapter_map"] = cm
    if not isinstance(meta.get("ai"), dict):
        meta["ai"] = ai_sum
    st2 = _teach_augment_status(doc_id, {
        "has_artifact": True, "has_textbook": True,
        "supplement": {"exists": True}, "ai": meta.get("ai")}, conn=None)
    meta["chapter_map"] = st2.get("chapter_map") or meta.get("chapter_map") or {}
    meta["ai"] = st2.get("ai") or meta.get("ai") or ai_sum
    out = {"ok": bool(r.get("ok", True)), "status": r.get("status") or "",
           "doc_id": doc_id, "out_path": r.get("out_path") or "",
           "meta": meta, "error": r.get("error") or "",
           "legacy": bool(st2.get("legacy") or legacy),
           "chapter_map": st2.get("chapter_map") or {},
           "estimate": _teach_normalize_estimate(
               r.get("estimate") if isinstance(r.get("estimate"), dict) else
               (st2.get("estimate") or {}))}
    return out


def api_teach_page(doc_id, page, dpi):
    """教材某页渲染成 PNG（零模型）。

    返回 `(png_bytes, {"code", "pages", "page"})`；失败 `(None, {"error","code"})`，
    由路由层翻成 4xx/5xx JSON（与 `/img/slide` 的降级风格一致）。
    """
    try:
        doc_id, page = int(doc_id or 0), int(page or 0)
    except (TypeError, ValueError):
        return None, {"error": "doc_id/page 必须是整数", "code": 400}
    if doc_id <= 0 or page <= 0:
        return None, {"error": "缺少 doc_id 或 page", "code": 400}
    d = None
    if str(dpi or "").strip():
        try:
            d = int(dpi)
        except (TypeError, ValueError):
            return None, {"error": "dpi 必须是整数", "code": 400}
    tm = _teach_mod()
    fn = getattr(tm, "page_png", None)
    err_cls = getattr(tm, "TeachError", None)
    if not callable(fn):
        return None, {"error": "教材页图后端还没就绪（缺 page_png）", "code": 503}
    conn = None
    try:
        conn = _conn(readonly=True)
        try:
            r = fn(conn, doc_id, page, d)
        finally:
            if conn is not None:
                conn.close()
    except Exception as e:  # noqa: BLE001
        if err_cls is not None and isinstance(e, err_cls):
            return None, {"error": getattr(e, "message", str(e)),
                          "code": int(getattr(e, "code", 400) or 400)}
        return None, {"error": "教材页图失败：%s" % e, "code": 500}
    if not isinstance(r, dict) or not r.get("png"):
        return None, {"error": "教材页图渲染失败", "code": 500}
    return r["png"], {"code": 200,
                      "pages": int(r.get("pages") or 0),
                      "page": int(r.get("page") or page),
                      "dpi": int(r.get("dpi") or (d or 0))}


def _teach_after_kp_build(doc_id):
    """整理知识点成功后顺手补做教材补充（现在会自动跑 1 次 AI 章节映射；
    AI 失败/无 key 时后端本地兜底；异常吞掉，绝不影响整理状态）。"""
    try:
        from core import teach as _tm
        fn = getattr(_tm, "build_supplement", None)
        if callable(fn):
            fn(int(doc_id))
    except Exception:  # noqa: BLE001
        pass


# ---------------- 复习流程（task-33）：/api/review/* 五条路由胶水 ----------------
# core/review.py 并行交付（零 AI 调用）：只做惰性 import + 统一 JSON 降级，
# 后端函数抛错一律 {"ok": false, "error": ...}（配合 _json_auto 的 _code 不 500）。
# 路由职责（见 routes）：
#   GET  /api/review/coursewares      只读锁内 conn → list_courseware_reviews(conn)
#   GET  /api/review/flow             只读锁内 conn → build_flow(conn, doc_id)
#   GET  /api/review/selftest         锁外 → start_selftest(doc_id)（进程内 session，不写盘）
#   POST /api/review/selftest/submit  锁外 → submit_selftest(...)（原子写 logs/review/*.json）
#   GET  /api/review/selftest/status  锁外 → selftest_status(doc_id)（纯读文件）


def _review_mod():
    from core import review as _rm
    return _rm


def _review_err(what, e):
    return {"ok": False, "error": "%s：%s" % (what, e)}


def api_review_coursewares(conn):
    """复习页课件卡数据：有 KP 产物的课件 + 自测状态 + 名词数 + 映射章节。"""
    try:
        items = _review_mod().list_courseware_reviews(conn)
    except Exception as e:  # noqa: BLE001
        return _review_err("读不到复习课件列表", e)
    return {"ok": True, "items": items if isinstance(items, list) else []}


def api_review_flow(conn, doc_id):
    """三步复习数据：按导图顺序的 KP + 笔记分组 + 预习重点名词 + 自测摘要。"""
    try:
        doc_id = int(doc_id or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "doc_id 必须是整数"}
    if doc_id <= 0:
        return {"ok": False, "error": "缺少 doc_id"}
    try:
        r = _review_mod().build_flow(conn, doc_id)
    except Exception as e:  # noqa: BLE001
        return _review_err("读不到复习流程数据", e)
    if not isinstance(r, dict):
        return {"ok": False, "error": "复习流程返回异常"}
    r.setdefault("ok", True)
    return r


def api_review_selftest(doc_id):
    """开始一次自测：随机抽一半 KP + 挖空 segments（不写盘；session 在进程内存）。"""
    try:
        doc_id = int(doc_id or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "doc_id 必须是整数"}
    if doc_id <= 0:
        return {"ok": False, "error": "缺少 doc_id"}
    try:
        r = _review_mod().start_selftest(doc_id)
    except Exception as e:  # noqa: BLE001
        return _review_err("自测生成失败", e)
    if not isinstance(r, dict):
        return {"ok": False, "error": "自测返回异常"}
    return r


def api_review_selftest_submit(payload):
    """提交自测答案：判分 + 熟悉度 + 持久化（锁外调用，会原子写文件）。"""
    try:
        doc_id = int(payload.get("doc_id") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "doc_id 必须是整数"}
    session_id = str(payload.get("session_id") or "").strip()
    # task-36 / review v3：前端提交 {results:{kp_id:bool}}（true=已掌握）；
    # 兼容旧测试/旧前端仍传 answers 的调用。
    results = payload.get("results", payload.get("answers"))
    if doc_id <= 0 or not session_id:
        return {"ok": False, "error": "缺少 doc_id 或 session_id"}
    if not isinstance(results, (dict, list)):
        return {"ok": False, "error": "results 必须是对象或数组"}
    try:
        r = _review_mod().submit_selftest(doc_id, session_id, results)
    except Exception as e:  # noqa: BLE001
        return _review_err("自测提交失败", e)
    if not isinstance(r, dict):
        return {"ok": False, "error": "自测提交返回异常"}
    return r


def api_review_selftest_status(doc_id):
    """上次自测摘要（持久化文件；纯读）。"""
    try:
        doc_id = int(doc_id or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "doc_id 必须是整数"}
    if doc_id <= 0:
        return {"ok": False, "error": "缺少 doc_id"}
    try:
        r = _review_mod().selftest_status(doc_id)
    except Exception as e:  # noqa: BLE001
        return _review_err("读不到自测状态", e)
    if not isinstance(r, dict):
        return {"ok": False, "error": "自测状态返回异常"}
    r.setdefault("ok", True)
    return r


# ---------------- 名词整理（task-36）：预习重点名词 → 课件名词解释（零 AI） ----------------
# core/terms_preview.py 并行交付（零 AI）；只做惰性 import + 统一 JSON 降级。
# 路由（任务书口径 + 后端建议别名都挂，前端默认用前者）：
#   GET  /api/terms/preview/options?doc_id=  → terms_preview_status(doc_id)
#   GET  /api/terms/preview/status?doc_id=   （别名）
#   POST /api/terms/build-preview {doc_id,force?} → build_terms_from_preview(...)
#   POST /api/terms/preview/build {doc_id,force?} （别名）


def _terms_preview_mod():
    from core import terms_preview as _tp
    return _tp


def api_terms_preview_options(conn, doc_id):
    """预习名词整理入口状态：候选数 / 当前产物来源 / 是否可重做（0 AI、0 写）。"""
    try:
        doc_id = int(doc_id or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "doc_id 必须是整数"}
    if doc_id <= 0:
        return {"ok": False, "error": "缺少 doc_id"}
    try:
        r = _terms_preview_mod().terms_preview_status(doc_id, conn=conn)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "读不到预习名词状态：%s: %s" % (type(e).__name__, e)}
    if not isinstance(r, dict):
        return {"ok": False, "error": "预习名词状态返回异常"}
    r.setdefault("ok", True)
    return r


def api_terms_build_preview(payload):
    """按预习重点名词重新整理课件名词解释（0 AI、0 模型费用；覆盖前自动 .bak）。"""
    try:
        doc_id = int(payload.get("doc_id") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "doc_id 必须是整数"}
    if doc_id <= 0:
        return {"ok": False, "error": "缺少 doc_id"}
    force = bool(payload.get("force", True))
    try:
        r = _terms_preview_mod().build_terms_from_preview(
            doc_id, force=force, log=lambda *a, **k: None)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "名词整理失败：%s: %s" % (type(e).__name__, e)}
    if not isinstance(r, dict):
        return {"ok": False, "error": "名词整理返回异常"}
    r.setdefault("ok", True)
    return r
def api_kp_build_start(payload):
    """启动整理（后台线程）。返回产物路径与备份路径，前端据此提示"去哪儿看结果"。"""
    try:
        did = int(payload.get('doc_id') or 0)
    except (TypeError, ValueError):
        return {'error': 'doc_id 必须是整数'}

    if did <= 0:
        return {'error': '缺少 doc_id'}

    if kp_build_mod.is_running():
        return {'error': '已有一个整理任务在跑；同一时间只允许一个，请等它结束'}

    if kp_batch_mod.is_running():
        return {'error': '有一个批量整理任务在跑；等它结束（或取消）后再做单份整理'}

    est = kp_build_mod.estimate(did)
    if not est.get('ok'):
        return {'error': est.get('error') or '该文档不可整理'}

    force = bool(payload.get('force'))

    try:
        max_cost = float(payload.get('max_cost') or 0.8)
    except (TypeError, ValueError):
        max_cost = 0.8

    out = Path(est['out_path'])
    prev = kp_build_mod.prev_info(did)

    backup = ''
    if prev['exists'] and (force or prev['resumable']):
        bak = out.with_name(out.name + '.bak-' + time.strftime('%Y%m%d-%H%M%S'))
        try:
            shutil.copy2(out, bak)
            backup = str(bak)
        except OSError as e:
            return {'error': '备份旧产物失败，为不覆盖它已中止：' + f'{e}'}

    # C-0115：真值 pyc 的调用窗口是 `LOAD_FAST did, max_cost, force, str(out)` +
    # `KW_NAMES ('max_cost','force','out_path')` ⇒ **只有 did 是位置参**。
    # 而 `kp_build.start_background(doc_id: int, **kw)` 也只收 1 个位置参，
    # 旧写法（三个位置参）必然 `TypeError: start_background() takes 1 positional
    # argument but 3 were given` → 看板「开始整理」按钮 500。
    # task-27：整理成功后顺手补做教材补充（回调异常不影响整理；惰性 import）
    try:
        rep = kp_build_mod.start_background(
            did, max_cost=max_cost, force=force, out_path=str(out),
            on_done=_teach_after_kp_build,
        )
    except TypeError:
        # 老版 kp_build 不认 on_done：退回原调用，不因增强功能让整理起不来
        rep = kp_build_mod.start_background(
            did, max_cost=max_cost, force=force, out_path=str(out),
        )
    if not rep.get('started'):
        return {'error': rep.get('error') or '启动失败'}

    return {
        'started': True,
        'doc_id': did,
        'title': est.get('title') or '',
        'out_path': str(out),
        'out_name': out.name,
        'backup': backup,
        'max_cost': max_cost,
        'kp_min': est['kp_min'],
        'kp_max': est['kp_max'],
        'est_cost_cny': est['est_cost_cny'],
        'resume_pages': prev['n_done_pages'] if prev['resumable'] else 0,
        'progress': kp_build_mod.progress_snapshot(),
        'hint': '完成后去「资料」页 → 课件思维导图 → 点节点看总结；产物在 %s' % out.name,
    }


def api_kp_build_batch_options(conn):
    """批量整理的清单：每份的预估 + 「已完成可跳过」判定 + **合计预估**。

    复用单份那份清单（`api_kp_build_options`，含 20 秒缓存），只在这里补
    "合计"与"并发参数"——清单口径不许有第二份实现。
    """
    data = api_kp_build_options(conn)
    docs = [x for x in (data.get("documents") or []) if x.get("n_leaf_pages")]
    n_done = sum(x.get("n_done", 0) for x in docs)
    est_all = sum(x.get("est_cost_all_cny", 0) for x in docs)
    est_todo = sum(x.get("est_cost_todo_cny", 0) for x in docs)
    kp_min_todo = sum(x.get("kp_min_todo", 0) for x in docs)
    kp_max_todo = sum(x.get("kp_max_todo", 0) for x in docs)
    return {
        "documents": docs,
        "n": len(docs),
        "n_done": n_done,
        "est_cost_all_cny": round(est_all, 6),
        "est_cost_todo_cny": round(est_todo, 6),
        "kp_min_todo": kp_min_todo,
        "kp_max_todo": kp_max_todo,
        "confirm_threshold_cny": kp_batch_mod.KP_BATCH_CONFIRM_CNY,
        "max_workers_max": kp_batch_mod.KP_BATCH_MAX_WORKERS,
        "max_workers_default": kp_batch_mod.KP_BATCH_DEFAULT_WORKERS,
        "batch_running": kp_batch_mod.is_running(),
        "single_running": kp_build_mod.is_running(),
        "progress": kp_batch_mod.snapshot(),
        "stages": list(kp_batch_mod.BATCH_STAGES),
        "est_rule": data.get("est_rule") or "",
        "write_note": data.get("write_note") or "",
    }


def api_kp_build_batch_progress():
    """批量整理的聚合进度：总体 N/M + 每份状态（等待/进行中/完成/失败 + 阶段）。

    **进度分离**：这份快照完全来自 `kp_batch` 自己的 per-task 状态表，
    与 `core/progress.py` 那个入库用的全局单例没有半点关系——
    多份并发时互相覆盖的问题就是这么消掉的（卡里点名要和 C-0037 口径一致）。
    """
    return kp_batch_mod.snapshot()


def api_kp_build_batch_start(payload):
    raw = payload.get("doc_ids") or []
    doc_ids = []
    for x in raw:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in doc_ids:
            doc_ids.append(n)

    if not doc_ids:
        return {"error": "请至少勾选一份课件"}
    if len(doc_ids) > 200:
        return {"error": "一次最多 200 份"}
    if kp_build_mod.is_running():
        return {"error": "已有一个单份整理任务在跑；等它结束再做批量"}
    if kp_batch_mod.is_running():
        return {"error": "已有一个批量整理任务在跑（同一时间只允许一个）"}

    try:
        max_workers = int(payload.get("max_workers") or kp_batch_mod.KP_BATCH_DEFAULT_WORKERS)
    except (TypeError, ValueError):
        max_workers = kp_batch_mod.KP_BATCH_DEFAULT_WORKERS

    try:
        per_doc = float(payload.get("max_cost") or 0.8)
    except (TypeError, ValueError):
        per_doc = 0.8

    try:
        total_cap = float(payload.get("total_max_cost") or 0.0)
    except (TypeError, ValueError):
        total_cap = 0.0

    rep = kp_batch_mod.start(
        doc_ids,
        max_workers=max_workers,
        skip_done=bool(payload.get("skip_done", True)),
        force=bool(payload.get("force")),
        per_doc_max_cost=per_doc,
        total_max_cost=total_cap,
        confirm=bool(payload.get("confirm")),
    )

    if not rep.get("started"):
        out = {"error": rep.get("error") or "启动失败"}
        if rep.get("need_confirm"):
            out["need_confirm"] = True
            out["est_cost_cny"] = (rep.get("plan") or {}).get("est_cost_cny")
            out["confirm_threshold_cny"] = kp_batch_mod.KP_BATCH_CONFIRM_CNY
        return out

    rep["hint"] = "完成后去「资料」页 → 课件思维导图 → 点节点看总结；产物在 logs/knowledge/<doc_id>-末级知识点总结.json"
    return rep


def api_kp_build_batch_cancel(payload):
    """取消批任务：未开始的份作废；正在跑的份跑完当前这一条再收尾。"""
    return kp_batch_mod.cancel()


def api_kp_clear(payload):
    if not payload.get("confirm"):
        return {
            "need_confirm": True,
            "n_deleted": 0,
            "deleted": [],
            "not_found": [],
            "error": "清除不可逆（产物与备份都会被删，必须重新整理才能重建）；确认后带上 confirm=true 再来",
        }

    raw = payload.get("doc_ids")
    if raw is None and payload.get("doc_id") is not None:
        raw = [payload.get("doc_id")]
    if isinstance(raw, (str, int)):
        raw = [raw]
    if not (isinstance(raw, (list, tuple)) and raw):
        return {"error": "请至少勾选一份课件（doc_ids 不能为空）", "n_deleted": 0}
    if len(raw) > _KP_CLEAR_MAX_DOCS:
        return {"error": "一次最多清除 %d 份" % _KP_CLEAR_MAX_DOCS, "n_deleted": 0}

    doc_ids = []
    rejected = []
    for x in raw:
        try:
            n = int(str(x).strip())
        except (TypeError, ValueError):
            rejected.append({"value": x, "reason": "不是整数"})
            continue
        if n <= 0:
            rejected.append({"value": x, "reason": "必须是正整数（合法的 doc_id）"})
            continue
        if n not in doc_ids:
            doc_ids.append(n)

    if rejected:
        return {
            "error": "有 %d 项不是合法的 doc_id，已整批拒绝（一个文件都没删）" % len(rejected),
            "rejected": rejected,
            "n_deleted": 0,
        }

    if kp_build_mod.is_running() or kp_batch_mod.is_running():
        return {
            "error": "有整理任务正在跑：产物正在被写，等它结束再清除（否则可能把刚写出来的内容删掉）",
            "n_deleted": 0,
        }

    kd = _knowledge_dir()
    deleted = []
    not_found = []
    errors = []
    n_files = 0
    for did in doc_ids:
        p = _artifact_file(did)
        if p is None or p.parent != kd:
            errors.append({"doc_id": did, "error": "产物路径不合法，已跳过"})
            continue

        files = []
        if p.is_file():
            files.append(p)
        files += _artifact_backups(p)

        if not files:
            not_found.append({"doc_id": did, "path": str(p)})
            continue

        gone = []
        for f in files:
            if f.parent != kd or f.name == p.name:
                pass
            elif not f.name.startswith(p.name + ".bak-"):
                errors.append({"doc_id": did, "file": f.name, "error": "文件名不符合产物/备份形态，已跳过"})
                continue
            try:
                f.unlink()
                gone.append(f.name)
            except OSError as e:
                errors.append({"doc_id": did, "file": f.name, "error": str(e)})

        if gone:
            n_files += len(gone)
            deleted.append({"doc_id": did, "files": gone, "n": len(gone)})
        else:
            not_found.append({"doc_id": did, "path": str(p)})

    return {
        "ok": not errors,
        "n_docs": len(doc_ids),
        "n_deleted": n_files,
        "deleted": deleted,
        "not_found": not_found,
        "errors": errors,
        "state_after": _ARTIFACT_NONE,
        "note": "已清除整理产物（思维导图结构 + 理解 + 要点）与备份；这批课件回到「未整理」，必须重新整理才能重建。logs/outline/ 与数据库一个字都没动。",
    }


def _safe_raw_path(rel: str) -> Path:
    """把用户给的相对路径限制在 raw/ 之内。

    必须防目录穿越：rel 里出现 .. 或绝对路径时一律拒绝，
    否则网页端就能删除 raw/ 之外的任意文件。
    """
    raw_root = Path(config.RAW_DIR).resolve()
    s = (rel or "").strip()
    if not s or s in (".", ".."):
        raise ValueError("路径为空")

    parts = [p for p in s.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError(f"路径含非法段，只能操作 raw/ 内的文件: {rel}")
    p = (raw_root / Path(*parts)).resolve()
    if p == raw_root:
        raise ValueError(f"路径指向 raw/ 本身，必须指定具体文件: {rel}")
    if raw_root not in p.parents:
        raise ValueError(f"路径越界，只能操作 raw/ 内的文件: {rel}")
    return p


def _rel_to_raw(p):
    r"""相对 raw/ 的路径，统一用正斜杠。

    注意：ingest 存进 documents.path 的是**相对项目根**且带反斜杠的路径
    （如 raw\demo\x.txt），而这里返回的是相对 raw\ 的正斜杠路径（demo/x.txt）。
    两边格式不同，比较前必须归一化，否则已入库的文件会被当成未入库。
    """
    raw_root = Path(config.RAW_DIR).resolve()
    try:
        return p.resolve().relative_to(raw_root).as_posix()
    except ValueError:
        return p.name


def _norm_doc_path(doc_path):
    r"""把 documents.path 归一化成"相对 raw/ 的正斜杠路径"。

    documents.path 有两种形态：
      - ingest 存的：相对**项目根** + 反斜杠，如 raw\demo\x.txt
      - 已是相对 raw\ 的：如 demo/x.txt

    做法是算出 raw 相对项目根的前缀（真实使用下就是 "raw"）再去掉它，
    而不是硬编码 "raw" 或盲目剥掉第一段。硬编码会在测试把 RAW_DIR
    指向别处时全部归一化失败；盲目剥第一段又会把本来就已经相对 raw 的
    路径削掉一层（这两个坑都踩过，表现分别是"已入库显示成未入库"
    和"删文件时路径少一层导致找不到文件"）。
    """
    s = (doc_path or "").replace("\\", "/").lstrip("/")
    if not s:
        return ""
    root = Path(config.ROOT).resolve()
    try:
        prefix = Path(config.RAW_DIR).resolve().relative_to(root).as_posix()
    except ValueError:
        prefix = ""
    if prefix:
        if s == prefix:
            return ""
        if s.startswith(prefix + "/"):
            return s[len(prefix) + 1:]
    return s


def _raw_rel_prefix():
    """raw/ 相对**项目根**的前缀（真实使用下就是 "raw"）；raw 不在项目根下时返回 ""。

    存在的理由：`_rel_to_raw()` 返回的是**相对 raw/** 的路径（如 `药理题库/x.docx`），
    而 `db.classify_corpus()` 判的是**相对项目根**的路径（与 documents.path 同口径，
    靠 `raw/药理题库` 这种前缀判定）。不把这一层拼回去，`药理题库/x.docx` 不含任何
    AUX_DIRS 前缀，会被整批判成 main——这正是方案 §7.4 点名的那个易错细节。
    """
    try:
        return Path(config.RAW_DIR).resolve().relative_to(
            Path(config.ROOT).resolve()).as_posix()
    except ValueError:
        return ""


def api_raw_list(conn):
    """列出 raw/ 下的文件，并标注是否已入库、内容是否已变化、属于哪个库。"""
    raw_root = Path(config.RAW_DIR)
    # C-0107 修 F4：Path.mkdir 只有 (mode, parents, exist_ok) —— 前两个位置参数
    # 已占位，再传 parents= 关键字即 TypeError（同 F1 的重复传参）。
    raw_root.mkdir(parents=True, exist_ok=True)

    rel_prefix = _raw_rel_prefix()

    def _corpus_of(rel: str) -> str:
        # C-0109：原实现被重建丢成 `...`（空函数体，返回 None），于是每个文件项
        # 的 corpus 都是 null，前端按库过滤（(f.corpus||'main') === 页签）全判成
        # main——辅库文件会串进主库列表。按真值回填：
        #   _work/rebuilt/dashboard/0075-api_raw_list._corpus_of.py
        #   def _corpus_of(rel): return db.classify_corpus(f"{rel_prefix}/{rel}" if rel_prefix else rel)
        # 必须把 raw/ 相对项目根的前缀拼回去：_rel_to_raw() 给的是**相对 raw/** 的
        # 路径，classify_corpus() 判的却是**相对项目根**的 AUX_DIRS 前缀
        # （理由见 _raw_rel_prefix() 的 docstring）。
        return db.classify_corpus(f'{rel_prefix}/{rel}' if rel_prefix else rel)

    def _counts(sub: list[dict]) -> dict:
        # C-0109：原实现同样被重建丢成 `...`，返回 None —— 这就是资料页红字
        # 「Cannot read properties of null (reading 'unsupported')」的根因：
        # /api/raw 的 counts 与 counts_by_corpus.* 全成了 null，前端
        # viewDocs() 里 `const c = (raw.counts_by_corpus||{})[DOCS_TAB] || raw.counts`
        # 的 c 于是为 null，而空库时 pending=0，模板里**第一个**读到的属性正是
        # c.unsupported（"（未入库 N · 有变 N · 已入库 N）"那一行之前的角标分支），
        # 报错信息遂停在 'unsupported' 上。
        # 真值依据：_work/rebuilt/dashboard/0076-api_raw_list._counts.py 的四个键，
        # 且字节码字符串常量（_work/strings/dashboard.strings.txt 的
        # [api_raw_list._counts.<genexpr>] 四段）恰为 state×3 + new/changed/indexed
        # + supported，与下面逐字对齐（键名用的就是文件项自己的 'state'）。
        # 「不支持」的真实语义 = extract.detect_kind() 判成 unknown，即
        # info['supported'] 为假（见本函数上文 supported 的赋值），不是写死 0。
        # 旧版 Office（legacy）现在可入库（入库时自动转换），不再计入这一项。
        return {
            'new':         sum(1 for f in sub if f['state'] == 'new'),
            'changed':     sum(1 for f in sub if f['state'] == 'changed'),
            'indexed':     sum(1 for f in sub if f['state'] == 'indexed'),
            'unsupported': sum(1 for f in sub if not f['supported']),
        }

    indexed = {}
    for r in conn.execute(
            'SELECT id, path, content_hash, status, n_chunks, indexed_at FROM documents'):
        indexed[_norm_doc_path(r['path'])] = dict(r)

    files = []
    for f in sorted(raw_root.rglob('*')):
        if f.is_file() and not f.name.startswith(('~$', '.')) and f.suffix != '.part':
            rel = _rel_to_raw(f)
            kind = extract.detect_kind(f)
            try:
                st = f.stat()
            except OSError:
                continue
            info = {
                'name': f.name,
                'rel': rel,
                'corpus': _corpus_of(rel),
                'size': st.st_size,
                'mtime': st.st_mtime,
                'kind': kind,
                # 旧版 Office（.doc/.ppt/.xls）可入库：入库时会先自动转成 OOXML
                # （core/extract.py::ensure_ooxml）。只有 unknown 才是真正不支持。
                'supported': kind != 'unknown',
                'doc_id': None,
                'state': 'new',
            }
            hit = indexed.get(rel)
            if hit:
                info['doc_id'] = hit['id']
                info['n_chunks'] = hit['n_chunks']
                try:
                    same = db.sha256_file(f) == hit['content_hash']
                except OSError:
                    same = False
                info['state'] = 'indexed' if same else 'changed'
            if kind == 'legacy':
                # 旧格式的"入库结果"记在转换产物上：把源文件显示成已入库，
                # 否则每次刷新都会提示它有文件待入库。
                dest = extract.legacy_dest(f)
                dest_rel = _rel_to_raw(dest) if dest.is_file() else ''
                dhit = indexed.get(dest_rel) if dest_rel else None
                try:
                    fresh = bool(dhit) and dest.stat().st_mtime >= st.st_mtime
                except OSError:
                    fresh = False
                if dhit and fresh:
                    info['doc_id'] = dhit['id']
                    info['n_chunks'] = dhit['n_chunks']
                    info['state'] = 'indexed'
                    info['note'] = f'已自动转换为 {dest.name} 并入库（源文件保留）'
                else:
                    info['note'] = (f'旧格式：入库时自动转换为 {dest.name}'
                                    f'（需本机 Office；源文件保留）')
            files.append(info)

    on_disk = {f['rel'] for f in files}

    orphans = [
        {**v, 'rel': k, 'corpus': _corpus_of(k)}
        for k, v in indexed.items() if k not in on_disk
    ]

    return {
        'raw_dir': str(raw_root),
        'folders': sorted({
            str(Path(f['rel']).parent.as_posix())
            for f in files if '/' in f['rel']
        }),
        'folders_by_corpus': {
            c: sorted({
                str(Path(f['rel']).parent.as_posix())
                for f in files if f['corpus'] == c and '/' in f['rel']
            })
            for c in ('main', 'aux')
        },
        'files': files,
        'orphans': orphans,
        'counts': _counts(files),
        'counts_by_corpus': {
            c: _counts([f for f in files if f['corpus'] == c])
            for c in ('main', 'aux')
        },
    }


def _safe_filename(name):
    """清洗文件名，避免路径穿越与非法字符。"""
    name = Path(name.replace("\\", "/")).name
    name = re.sub('[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "unnamed"


def _parse_multipart_stream(handler, fields, dest_resolver):
    """流式解析 multipart/form-data，文件直接落盘，不把整个请求读进内存。

    为什么自己写而不是用标准库：
    - `email` 方案需要先把整个请求体读成 bytes，多文件批量上传会把内存撑爆
      （用户那个 645MB 的目录正是这种情况）。
    - `cgi.FieldStorage` 本来最合适，但 **Python 3.13 已把 cgi 模块整体移除**，
      本机正是 3.13，import 直接失败。

    所以这里按 multipart 的格式逐行读取：文件内容边收边写盘，
    内存占用只与单行长度有关，与文件大小无关。

    dest_resolver 是一个无参函数，返回文件应写入的目录。
    它在**遇到第一个文件时**才被调用——此时排在文件之前的普通字段
    （例如 folder）已经写进 fields 了，所以能拿到正确的目标目录。
    前端据此把 folder 放在文件之前发送。

    返回已写入的文件信息列表；普通字段直接写进传入的 fields。
    """
    import uuid

    ctype = handler.headers.get('Content-Type', '')
    length = int(handler.headers.get('Content-Length') or 0)
    if length <= 0:
        raise ValueError('请求体为空')
    if length > MAX_UPLOAD_BYTES:
        raise ValueError(
            f'上传体积过大：{length / 1048576:.0f} MB，HTTP 上传上限 '
            f'{MAX_UPLOAD_BYTES // 1048576} MB。\n看板在本机运行，大文件请改用「从本机路径导入」，那条路不经过 HTTP、没有大小限制。')

    m = re.search('boundary="?([^";]+)"?', ctype)
    if not m:
        raise ValueError('请求不是合法的 multipart/form-data（缺少 boundary）')
    delim = b'--' + m.group(1).strip().encode('latin-1')
    end_mark = delim + b'--'

    stream = handler.rfile
    saved = []

    def read_line() -> bytes:
        return stream.readline()

    # 跳到第一个分界
    while True:
        line = read_line()
        if not line:
            raise ValueError('请求体里找不到 multipart 分界')
        if line.rstrip(b'\r\n') == delim:
            break

    while True:
        headers = {}
        while True:
            line = read_line()
            if not line:
                return saved
            if line in (b'\r\n', b'\n'):
                break
            if b':' in line:
                k, _, v = line.decode('latin-1').partition(':')
                headers[k.strip().lower()] = v.strip()

        disp = headers.get('content-disposition', '')
        fn = None
        fm = re.search('filename="([^"]*)"', disp)
        if fm:
            fn = fm.group(1)
        nm = re.search('name="([^"]*)"', disp)
        field_name = nm.group(1) if nm else ''

        if fn:
            try:
                fn = fn.encode('latin-1').decode('utf-8')
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass

        if fn is None:
            # 普通字段：整段读进内存
            buf = bytearray()
            while True:
                line = read_line()
                if not line:
                    break
                stripped = line.rstrip(b'\r\n')
                if stripped == delim or stripped == end_mark:
                    break
                buf += line
            fields[field_name] = buf.decode('utf-8', errors='replace').rstrip('\r\n')
            if stripped == end_mark:
                return saved
            continue

        # 文件字段：边收边写盘
        target_dir = dest_resolver()
        target_dir.mkdir(parents=True, exist_ok=True)
        clean = _safe_filename(fn)
        dest = target_dir / clean
        if dest.exists():
            stem, suf = dest.stem, dest.suffix
            for i in range(1, 10000):
                cand = target_dir / f'{stem}_{i}{suf}'
                if not cand.exists():
                    dest = cand
                    break
            else:
                dest = target_dir / f'{stem}_{uuid.uuid4().hex[:8]}{suf}'

        written = 0
        tail = 0
        with open(dest, 'wb') as out:
            while True:
                line = read_line()
                if not line:
                    break
                stripped = line.rstrip(b'\r\n')
                if stripped == delim or stripped == end_mark:
                    if tail:
                        out.truncate(written - tail)
                        written -= tail
                    break
                out.write(line)
                written = out.tell()
                tail = len(line) - len(stripped)

        saved.append({
            'name': dest.name,
            'rel': _rel_to_raw(dest),
            'size': written,
            'kind': extract.detect_kind(dest),
            'renamed': dest.name != clean,
        })

        if stripped == end_mark:
            return saved


def api_upload(handler):
    """接收浏览器上传，写入 raw/。不自动入库——先让用户确认放进哪里。

    UI 入口：资料页「添加资料 → 拖拽入库」（卡 C-0083）。把文件拖进拖放区
    后，前端用 FormData/XHR 打的就是这个接口。C-0001 移除了旧的「浏览器上传」
    按钮，但函数与 `/api/upload` 路由一直留着，拖拽正好整条复用——
    **没有为拖拽另写一套上传通道**。

    注意这只是"小批量"通道。几百 MB 的文件请走 api_import_path：
    浏览器/连接层自己会在几百 MB 上 "fail to fetch"（用户实测 645MB），
    抬 MAX_UPLOAD_BYTES 救不了这种失败。
    """
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        raise ValueError("请求体为空")
    if length > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"上传体积过大：{length / 1048576:.0f} MB，HTTP 上传上限 "
            f"{MAX_UPLOAD_BYTES // 1048576} MB。\n请改用「从本机路径导入」——看板在本机运行，可以直接读取磁盘上的文件夹，没有大小限制。"
        )

    folder_box = {}

    def resolve_dir() -> Path:
        # C-0110 回填：真值 = _work/rebuilt/dashboard/0081-api_upload.resolve_dir.py
        # （funcs.txt 同条 args=0 vars: want, sub 逐字对上）
        want = (folder_box.get('folder', '') or '').strip()
        sub = _safe_filename(want) if want else ''
        if sub in ('.', '..'):
            sub = ''
        if sub:
            return Path(config.RAW_DIR) / sub
        return Path(config.RAW_DIR)

    saved = _parse_multipart_stream(handler, folder_box, resolve_dir)
    if not saved:
        raise ValueError("没有收到文件")
    return {"saved": saved, "dir": str(resolve_dir())}


def api_import_path(src, sub, recursive, dry_run):
    """从本机路径导入文件到 raw/。

    这是大文件的正确通道：看板跑在本机，不需要把文件通过 HTTP 传一遍。
    直接读磁盘 -> 拷进 raw/，没有大小限制、不吃内存、也不需要浏览器参与。

    dry_run 只列出将要导入什么，便于先确认。
    """
    if not src or not src.strip():
        raise ValueError('请填写本机路径')
    p = Path(src.strip().strip('"').strip("'"))
    if not p.exists():
        raise ValueError(f'路径不存在: {p}')

    sub_clean = _safe_filename(sub) if sub else ''
    if sub_clean in ('.', '..'):
        sub_clean = ''
    dest_dir = Path(config.RAW_DIR) / sub_clean if sub_clean else Path(config.RAW_DIR)

    files = []
    if p.is_file():
        files = [p]
    else:
        it = p.rglob('*') if recursive else p.glob('*')
        files = sorted(f for f in it if f.is_file())

    planned = []
    total_bytes = 0
    for f in files:
        kind = extract.detect_kind(f)
        try:
            size = f.stat().st_size
        except OSError:
            continue
        total_bytes += size
        planned.append({
            'name': f.name,
            'size': size,
            'kind': kind,
            # 旧版 Office 可入库（导入后由入库管线自动转换），只有 unknown 不支持
            'supported': kind != 'unknown',
            'note': ('旧格式：入库时自动转换为 OOXML（需本机 Office）'
                     if kind == 'legacy' else ''),
        })

    if dry_run:
        return {
            'dry_run': True,
            'source': str(p),
            'dest': str(dest_dir),
            'count': len(planned),
            'total_bytes': total_bytes,
            'files': planned,
        }

    dest_dir.mkdir(parents=True, exist_ok=True)
    copied, skipped, renamed = 0, 0, 0
    errors = []
    for f in files:
        try:
            dest = dest_dir / _safe_filename(f.name)
            if dest.exists():
                stem, suf = dest.stem, dest.suffix
                found = None
                for i in range(1, 10000):
                    cand = dest_dir / f"{stem}_{i}{suf}"
                    if not cand.exists():
                        found = cand
                        break
                if found is None:
                    skipped += 1
                    continue
                dest = found
                renamed += 1
            shutil.copy2(str(f), str(dest))
            copied += 1
        except OSError as e:
            errors.append(f"{f.name}: {e}")

    return {
        'source': str(p),
        'dest': str(dest_dir),
        'copied': copied,
        'renamed': renamed,
        'skipped': skipped,
        'errors': errors[:20],
        'total_bytes': total_bytes,
    }


def api_ingest_batch_plan(payload, conn):
    """批量入库的预估：待处理几份、要识图几页、大概花多少钱。

    只读文件元信息与库里的登记信息，不调模型，所以可以在确认框里实时算一次。
    """
    paths = payload.get("paths") or []
    if not paths:
        return {"error": "没有选择文件"}
    nf = payload.get("no_figures")
    return pool_mod.plan_files(
        paths,
        force=bool(payload.get("force")),
        with_figures=None if nf is None else not bool(nf),
        workers=payload.get("workers"),
        conn=conn,
    )


def api_ingest_batch_start(payload):
    notice = stale_notice()
    if notice:
        return {"error": notice + "（本次批量入库已拒绝，避免用旧逻辑空跑一整批）", "stale": True}
    paths = payload.get("paths") or []
    if not paths:
        return {"error": "没有选择文件"}
    nf = payload.get("no_figures")
    return pool_mod.submit_batch(
        paths,
        force=bool(payload.get("force")),
        with_ocr=not bool(payload.get("no_ocr")),
        with_figures=None if nf is None else not bool(nf),
        workers=payload.get("workers"),
    )


def api_ingest_batch_retry(payload):
    notice = stale_notice()
    if notice:
        return {
            notice + "（重试已拒绝，避免用旧逻辑空跑）": True,
            "error": "stale",
        }
    bid = str(payload.get("batch_id") or "")
    if not bid:
        return {"error": "缺少 batch_id"}
    return pool_mod.retry_failed(bid, workers=payload.get("workers"))


def api_ingest_batch_cancel(payload):
    """取消批量入库（卡 C-0041）：未开始的份作废，正在跑的份跑完当前文件。

与 `/api/kp-build/batch/cancel`（卡 C-0038）**同一个风格**：这里一行业务
逻辑都不重复实现，直接调执行器的 `cancel()`，语义全在
`core/pipeline_pool.py::cancel` 里：

  - `pending` → `cancelled`，且不会再启动（`_run_one` 开头还要挡一道）；
  - 正在跑的份**不硬杀**（手里有未提交的写事务与写了一半的文档，
    硬杀只会留半条记录，钱也退不回来），跑完当前这一份再收尾；
  - **幂等**：重复取消、取消不存在的批次都不抛异常（第二次 `cancelled=0`）。

`batch_id` 可以省略 —— 省略就是"当前批次"（与 `/progress`、`snapshot()`
同口径）。返回 `{"batch_id","cancelled","pending","running","found",
"already_cancelled","finished","total","note"}`，前端拿 `cancelled`
（本次作废的份数）与 `note` 直接提示用户。

★ 这个入口**不能占 `_lock`、也不能碰库**：它必须在入库正忙、写锁被 worker
占着的时候也能立刻进来。否则就回到本卡要修的那个坑——"想停却停不下来"，
2026-09-22 深夜用户只能杀看板进程来止损。路由里因此与
`/api/kp-build/batch/cancel` 并列放在写路径之外（见 `do_POST`）。
"""
    return pool_mod.cancel(str(payload.get("batch_id") or "") or None)


def api_ingest(paths, force, no_ocr, no_figures, db_path=None):
    """把 raw/ 下的文件入库（走 `ingest_paths`，与命令行同一条管线）。

    ⚠ **这是唯一会持续很久的操作，可能远超一次 HTTP 请求的合理时长。**
    两个耗时来源：
    - 嵌入是 CPU 计算（几十个文件几分钟）
    - **整页识图要逐页调视觉模型**，实测约 25 秒/页；
      一份 78 页的 PDF 导出件因此要 30 分钟以上

    所以 `no_figures=True` 是必要逃生口：只想把文字快速入库时关掉识图。
    批量入库仍建议走命令行 `tools/reimport_all.py`——它按资料提交、
    逐份打印进度，中断可续。

    Web 层在锁内串行执行，避免并发写库。
    db_path 只供测试注入，正常调用留空即用配置里的库。
    """
    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        if paths:
            targets = [_safe_raw_path(p) for p in paths]
        else:
            targets = [Path(config.RAW_DIR)]
        missing = [str(t) for t in targets if not t.exists()]
        if missing:
            return {"error": f"路径不存在: {missing}"}
        rep = ingest_mod.ingest_paths(
            conn, targets,
            force=force,
            with_ocr=not no_ocr,
            verbose=False,
            with_figures=not no_figures,
        )
        notice = stale_notice()
        summary = rep.summary()
        if notice:
            summary = f"{notice}\n{summary}"
        return {
            "summary": summary,
            "stale": bool(notice),
            "added": rep.added,
            "updated": rep.updated,
            "skipped": rep.skipped,
            "failed": rep.failed,
            "needs_ocr": rep.needs_ocr,
            "unsupported": rep.unsupported,
            "chunks": rep.chunks,
            "details": [d for d in rep.details if d["status"] != "skipped"][:50],
        }
    finally:
        conn.close()


# ------------------------------------------------- 教材入库专线（卡 C-0089 恢复 / 本卡 C-0122）
#
# ## 为什么教材要单独一个入口
#
# 教材是**长任务**（452 页本地 OCR 实测约 2 小时），不能混进「入库全部待处理
# 文件」或并发批量池：那两条路会在用户没预料的时候跑上一两个小时，而且
# **按课件口径切块**，把按章切块覆盖掉。
#
# ## 这条线做什么（core 层早就完整，缺的一直是入口）
#
#   1. 文件拷进 `raw\教材\`（目录只影响 corpus 归类，不决定切块）；
#   2. **本地免费 OCR**（`extract.ocr_pdf_pages` + rapidocr，2~16 秒/页，
#      **不调视觉大模型**）——`with_figures` 默认 False 就是这个意思；
#   3. `core/textbook.py` **按章切块**（书眉认章 + 章内二次切块）；
#   4. 入库并打标记：`role='textbook'` + `extra.chunk_scheme='textbook-v1'`，
#      由 `ingest_file(mode=TEXTBOOK_MODE)` 写入（见 core/ingest.py）。
#
# ## 回归与恢复（C-0121 查清、C-0122 施工）
#
# `/api/ingest/textbook` 与 `api_ingest_textbook()` 自 C-0104（10/14 21:15）
# 那次 dashboard.py 重建起整体丢失（当前文件里 0 处引用），CLI 也没有
# `--mode` ⇒ 教材专线在 core 层完好、却**没有任何用户可达的入口**。
# 本卡只补入口，不碰切块口径；恢复的依据是
# `tools/dashboard.py.bak-c0103-20261014-205957`
# （路由 L5959-5971、api_ingest_textbook 函数体 L3145-3285、前端 L8285-8468）。


def _inside_dir(path: Path, parent: Path) -> bool:
    """`path` 是否落在 `parent` 目录内（含相等）。"""
    try:
        p = Path(path).resolve()
        q = Path(parent).resolve()
    except OSError:
        return False
    return p == q or q in p.parents


def _path_key(p) -> str:
    """路径比较用的规范键（绝对 + 小写）。

    Windows 路径大小写不敏感，而"同一份文件"在不同入口里可能写成
    `D:\\study_agent\\raw\\教材\\a.pdf` 或 `d:\\Study_Agent\\RAW\\教材\\A.PDF`。
    """
    try:
        return str(Path(p).resolve()).lower()
    except OSError:
        return str(p).lower()


def _page_span(ref: str) -> tuple[int, int]:
    r"""`source_ref`（p.216-218 / p.17 / p.5 图）-> (起页, 止页)。"""
    m = _PAGE_REF_RE.search(ref or "")
    if not m:
        return 0, 0
    a = int(m.group(1))
    return a, int(m.group(2) or a)


def _doc_row_of_file(conn, path: Path):
    """按磁盘路径找 `documents` 行（教材回执要 doc_id 才能读章分区）。

    先按"相对项目根"的规范化路径精确匹配（这是 `_upsert_doc` 写进去的口径），
    再退回按文件名匹配——旧版 Office 会被转换成另一个文件名入库，
    那时库里记的 path 与用户给的原路径不是同一个。
    """
    try:
        want = ingest_mod._rel(Path(path).resolve()).replace("\\", "/")
    except Exception:                                     # noqa: BLE001
        want = str(path).replace("\\", "/")
    rows = conn.execute(
        "SELECT id, path, title, role, n_chunks FROM documents").fetchall()
    for r in rows:
        if str(r["path"] or "").replace("\\", "/") == want:
            return dict(r)
    for r in rows:
        if Path(str(r["path"] or "")).name == Path(path).name:
            return dict(r)
    return None


def _chapters_of_doc(conn, doc_id: int) -> list[dict]:
    """把这份资料的**章级分区**从库里读回来（教材入库回执用）。

    判据只用库里既有的两个字段，**不新增任何写入**：
      · `chunks.heading_path` 的第二段就是章——教材专线的面包屑是
        「书名 > 第X章 章名 > …」，由 `core/textbook.py` 的切块写出来；
      · `chunks.source_ref` 里的 `p.N` 是块的出处页码，用来算章的页范围。

    返回 `[{label, chapter_no, page_start, page_end, chunks}]`，
    章号解析复用 `core.textbook._cn_to_int`（与认章用的是同一个解析，
    中文数字与阿拉伯数字都能认）。
    """
    from core.textbook import _cn_to_int
    agg: dict[str, dict] = {}
    for r in conn.execute("SELECT heading_path, source_ref FROM chunks "
                          "WHERE doc_id=?", (doc_id,)):
        parts = [p.strip() for p in str(r["heading_path"] or "").split(">")]
        label = parts[1] if len(parts) > 1 else ""
        if not label:
            # 只有一级面包屑时，它自己可能就是章
            label = parts[0] if parts and re.match(r"^第\s*\S{1,6}\s*章", parts[0]) else ""
        if not label:
            continue
        m = re.match(r"^第\s*(\S{1,6}?)\s*章", label)
        no = 0
        if m:
            try:
                no = int(_cn_to_int(m.group(1)))
            except Exception:                             # noqa: BLE001
                no = 0
        a, b = _page_span(r["source_ref"] or "")
        rec = agg.setdefault(label, {"label": label, "chapter_no": no,
                                     "page_start": 0, "page_end": 0, "chunks": 0})
        rec["chunks"] += 1
        if a:
            rec["page_start"] = (a if not rec["page_start"]
                                 else min(rec["page_start"], a))
            rec["page_end"] = max(rec["page_end"], b)
    return sorted(agg.values(), key=lambda x: (x["chapter_no"] or 0, x["label"]))


def api_ingest_textbook(src: str = "", paths: list[str] | None = None,
                        force: bool = False, with_figures: bool = False,
                        db_path=None) -> dict:
    """教材入库专线（卡 C-0089）：拷进 raw\\教材\\ → 本地 OCR → 按章切块 → 入库。

    **一条龙**（人 2026-10-12 拍板：不做逐步确认）：点一次按钮就把它跑完。
    作为交换，回执必须**如实列出**识别到的章（章号 + 章名 + 页范围）与块数
    变化；章识别降级（少于 3 章）也照实报 `fallback=true`，不假装成功。

    - `with_figures` 默认 **False**：教材专线走的是**本地免费 OCR**
      （`extract.ocr_pdf_pages` + rapidocr，实测 2~16 秒/页、不花钱），
      **不调视觉大模型**。要叠识图得显式传 True，而那是按页计费的。
    - `force=False` 时内容未变会走"跳过"（幂等）；重跑老教材要显式 force。
      重跑会重建块的 id ⇒ 引用它的知识点出处会被 CASCADE 清掉。
    - 长任务、串行：路由占 `_INGEST_LOCK`，一次一份。

    `src` 给本机路径（文件或文件夹，与「方式一」同一条拷贝通道）；
    `paths` 给 `raw/` 相对路径（给脚本/验收用，不重复拷贝）。
    """
    if not str(src or "").strip() and not paths:
        return {"error": "请填写教材文件或文件夹的本机路径"}
    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        sub_dir = Path(config.RAW_DIR) / TEXTBOOK_SUBDIR
        notes: list[str] = []
        files: list[Path] = []
        if paths:
            try:
                targets = [_safe_raw_path(p) for p in paths]
            except ValueError as e:
                return {"error": str(e)}
            missing = [str(t) for t in targets if not t.exists()]
            if missing:
                return {"error": f"路径不存在: {missing}"}
            files = ingest_mod.iter_files(targets)
        else:
            s = str(src).strip().strip('"').strip("'")
            p = Path(s)
            if not p.exists():
                return {"error": f"路径不存在: {p}"}
            if _inside_dir(p, sub_dir):
                # 人直接把路径指到落地目录：**不重复拷贝**——否则
                # `api_import_path` 会按同名规则再拷一份 `xxx_1.pdf`，
                # 同一本书就会在库里出现两条记录。
                files = ingest_mod.iter_files([p])
                notes.append(f"源已在 raw\\{TEXTBOOK_SUBDIR}\\ 内，未重复拷贝")
            else:
                before = ({_path_key(f) for f in ingest_mod.iter_files([sub_dir])}
                          if sub_dir.exists() else set())
                try:
                    imp = api_import_path(s, sub=TEXTBOOK_SUBDIR,
                                          recursive=True, dry_run=False)
                except ValueError as e:
                    return {"error": f"拷贝失败：{e}"}
                if imp.get("errors"):
                    return {"error": "拷贝失败：" + "；".join(imp["errors"][:3])}
                after = (ingest_mod.iter_files([sub_dir])
                         if sub_dir.exists() else [])
                files = [f for f in after if _path_key(f) not in before]
                notes.append(
                    f"已拷进 raw\\{TEXTBOOK_SUBDIR}\\：拷贝 {imp.get('copied', 0)} 个"
                    f"、跳过 {imp.get('skipped', 0)} 个（同名自动加序号）")
        if not files:
            return {"error": f"没有可入库的教材文件（raw\\{TEXTBOOK_SUBDIR}\\ 里"
                             f"没有新落地的文件）"}

        docs_out: list[dict] = []
        n_ok = n_skip = n_fail = 0
        chunks_before_all = chunks_after_all = 0
        t_all = time.perf_counter()
        for i, f in enumerate(files, start=1):
            f = Path(f).resolve()
            rel = ingest_mod._rel(f)
            row0 = _doc_row_of_file(conn, f)
            before_n = int(row0["n_chunks"] or 0) if row0 else 0
            chunks_before_all += before_n
            # 进度生命周期的口径与 `ingest_paths` 一致（看板进度条靠它）
            progress_mod.begin(f.name, files_total=len(files))
            t0 = time.perf_counter()
            try:
                r = ingest_mod.ingest_file(
                    conn, f, force=force, with_ocr=True,
                    with_figures=with_figures, verbose=False,
                    mode=ingest_mod.TEXTBOOK_MODE)
            except Exception as e:                        # noqa: BLE001
                # 一份炸掉不该毁掉整次入库：记下来接着跑下一份，
                # 与 `ingest_paths` 对单份异常的处理口径一致。
                r = {"path": rel, "status": "failed",
                     "reason": f"{type(e).__name__}: {e}"}
            progress_mod.file_done(i)
            dt = time.perf_counter() - t0
            row1 = _doc_row_of_file(conn, f)
            after_n = int(row1["n_chunks"] or 0) if row1 else 0
            chunks_after_all += after_n
            tb = r.get("textbook") or {}
            chs = (_chapters_of_doc(conn, int(row1["id"]))
                   if (row1 is not None and after_n) else [])
            st = r.get("status")
            if st in ("added", "updated"):
                n_ok += 1
            elif st == "skipped":
                n_skip += 1
            else:
                n_fail += 1
            docs_out.append({
                "path": rel,
                "title": (row1["title"] if row1 is not None else f.stem),
                "status": st, "reason": r.get("reason") or "",
                "chunks_before": before_n, "chunks_after": after_n,
                "seconds": round(dt, 1),
                "role": (row1["role"] if row1 is not None else ""),
                "book_title": tb.get("book_title") or "",
                "chapters_n": tb.get("chapters"),
                "fallback": tb.get("fallback"),
                "cross_chapter_chunks": tb.get("cross_chapter_chunks"),
                "chapters": chs,
                "warnings": list(r.get("warnings") or [])[:5],
            })
        progress_mod.end("done" if not n_fail else "failed",
                         error=(f"{n_fail} 份失败" if n_fail else ""))

        notice = stale_notice()
        n_ch = sum(len(d["chapters"]) for d in docs_out)
        summary = (f"教材专线：{len(docs_out)} 份（入库 {n_ok} / 跳过 {n_skip} / "
                   f"失败 {n_fail}），章级分区 {n_ch} 个，块数 "
                   f"{chunks_before_all} → {chunks_after_all}，耗时 "
                   f"{(time.perf_counter() - t_all) / 60:.1f} 分钟")
        if notice:
            summary = f"{notice}\n{summary}"
        return {
            "summary": summary, "stale": bool(notice),
            "dest": str(sub_dir), "notes": notes,
            "added": n_ok, "skipped": n_skip, "failed": n_fail,
            "chapters": n_ch,
            "chunks_before": chunks_before_all,
            "chunks_after": chunks_after_all,
            "with_figures": bool(with_figures), "force": bool(force),
            "docs": docs_out,
        }
    finally:
        conn.close()


def api_delete_doc(conn, doc_id, delete_file, purge_orphans, dry_run):
    """删除文档。

    - 默认只删索引，不动 raw/ 里的文件——文件删了就找不回来了，
      而索引随时可以重建。只有显式 delete_file=true 才连文件一起删。
    - 默认 purge_orphans=True：删完顺手清理"因此失去全部出处"的知识点，
      以及挂在这些知识点下的复习记录与依赖边。
      不清理就会留下看着还在、实则无原文支撑的孤儿数据：
      用户删完资料后仍看到 17 个知识点、13 条依赖边、35 条复习记录活着。
    - dry_run=True 只报告影响范围，不实际删除，供前端二次确认用。
    """
    row = conn.execute(
        "SELECT id, path, title, n_chunks FROM documents WHERE id=?",
        (doc_id,)).fetchone()
    if not row:
        return {"error": f"文档 {doc_id} 不存在"}

    rel = row["path"]
    kp_linked = conn.execute(
        "SELECT COUNT(DISTINCT s.kp_id) AS n FROM kp_sources s\n           JOIN chunks c ON c.id = s.chunk_id WHERE c.doc_id=?",
        (doc_id,)).fetchone()["n"]

    if dry_run:
        doomed = [
            int(r["kp_id"])
            for r in conn.execute(
                "SELECT DISTINCT s.kp_id FROM kp_sources s\n                   JOIN chunks c ON c.id = s.chunk_id\n                   JOIN knowledge_points k ON k.id = s.kp_id\n                   WHERE c.doc_id = ? AND COALESCE(k.had_sources, 0) = 1\n                     AND NOT EXISTS (\n                       SELECT 1 FROM kp_sources s2\n                       JOIN chunks c2 ON c2.id = s2.chunk_id\n                       WHERE s2.kp_id = s.kp_id AND c2.doc_id <> ?\n                     )",
                (doc_id, doc_id))
        ]
        prev = cleanup_orphans(conn, dry_run=True)
        if doomed:
            names = [
                r["name"]
                for r in conn.execute(
                    f"SELECT name FROM knowledge_points WHERE id IN ({','.join('?' * len(doomed))})",
                    doomed)
            ]
        else:
            names = []
        prev["will_lose_all_sources"] = len(doomed)
        prev["will_lose_names"] = names[:20]
        prev["orphan_kps"] = prev["orphan_kps"] + len(doomed)
        prev["names"] = (prev.get("names") or []) + names[:20]
        return {
            "dry_run": True,
            "deleted_doc_id": doc_id,
            "title": row["title"],
            "path": rel,
            "chunks_removed": row["n_chunks"],
            "kp_unlinked": int(kp_linked),
            "orphan_preview": prev,
        }

    conn.execute(
        "DELETE FROM kp_sources WHERE chunk_id IN\n           (SELECT id FROM chunks WHERE doc_id=?)",
        (doc_id,))
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.commit()

    purge = {}
    if purge_orphans:
        purge = cleanup_orphans(conn)

    removed_file = None
    file_err = None
    if delete_file:
        try:
            p = _safe_raw_path(_norm_doc_path(rel))
            if not p.is_file():
                file_err = f"文件不存在，仅移除了索引: {_norm_doc_path(rel)}"
            else:
                p.unlink()
                removed_file = _norm_doc_path(rel)
                try:
                    if p.parent != Path(config.RAW_DIR).resolve() and not any(p.parent.iterdir()):
                        p.parent.rmdir()
                except OSError:
                    pass
        except (ValueError, OSError) as e:
            file_err = f"{type(e).__name__}: {e}"

    return {
        "deleted_doc_id": doc_id,
        "title": row["title"],
        "path": rel,
        "chunks_removed": row["n_chunks"],
        "kp_unlinked": int(kp_linked),
        "purged": purge,
        "file_removed": removed_file,
        "file_error": file_err,
    }


def api_delete_raw(rel):
    """删除 raw/ 里尚未入库的文件。已入库的走 api_delete_doc（要一并清索引）。"""
    # C-0107：空/非法的 rel 是**入参问题**，这里直接回错误回执，别让
    # `_safe_raw_path()` 抛 ValueError 变成 500（与 `_int_arg` 那条
    # "非法入参应当被拒绝或如实报告，不该 500"是同一约定）。
    if not str(rel or "").strip():
        return {"error": "缺少 rel（相对 raw 目录的路径）"}
    p = _safe_raw_path(rel)
    if not p.is_file():
        return {"error": "文件不存在: " + rel}
    size = p.stat().st_size
    p.unlink()

    try:
        raw_root = Path(config.RAW_DIR).resolve()
        if p.parent != raw_root and not any(p.parent.iterdir()):
            p.parent.rmdir()
    except OSError:
        pass

    return {"deleted": rel, "size": size}


def _doc_stem(doc_path) -> str:
    """`documents.path` → 文件名主干（figure_desc 的目录名由它清洗而来）。"""
    return Path(str(doc_path or "").replace("\\", "/")).stem.strip()


def _artifact_terms_file(doc_id):
    """独立名词解释产物的路径（**口径不在此处另写一份**，从 `kp_build` 取）。

    与 `_artifact_file()`（末级知识点总结）同一套自证风格：`doc_id` 不是正整数
    就返回 None，路径完全由整数拼出来，前端递不进任何路径片段。
    """
    try:
        did = int(doc_id)
    except (TypeError, ValueError):
        return None
    if did <= 0:
        return None
    try:
        p = Path(kp_build_mod.terms_out_file(did))
    except Exception:
        return None
    kd = _knowledge_dir()
    if p.parent != kd:
        return None
    if p.name != "%d%s" % (did, kp_build_mod.TERMS_SUFFIX):
        return None
    return p


def _outline_file(doc_id):
    """`logs/outline/<doc_id>.json`（入库结构缓存）的规范路径。"""
    try:
        did = int(doc_id)
    except (TypeError, ValueError):
        return None
    if did <= 0:
        return None
    d = _outline_dir()
    p = d / ('%d.json' % did)
    if p.parent != d or p.name != ('%d.json' % did):
        return None
    return p


def _deck_dirs_for(doc_path):
    """本文档在 `logs/figure_desc/` 下的页图目录（**复用 `_deck_dir_for` 的判据**）。

    两个候选名 `<stem>` 与 `<stem>_slides` 就是 `_deck_dir_for` 里那一对——
    同一口径，避免"页签读得到图、删除却找不到目录"这种漂移。
    """
    stem = _doc_stem(doc_path)
    if not stem or stem in (".", "..") or "/" in stem or "\\" in stem:
        return []
    root = _figure_desc_root()
    out = []
    for name in _deck_name_candidates(stem):
        d = root / name
        try:
            if d.is_dir():
                out.append(d)
        except OSError:
            continue
    return out


def _deck_files(d):
    """页图目录里**允许删**的文件（顶层文件 + 一层子目录里的同类文件）。

    只收 `slide_<数字>.png` / `slide_<数字>.md` / `_summary.json` 三种形态；
    其余文件（人手工放进去的、渲染中间产物…）**不动**，交给调用方如实报告。
    """
    root = _figure_desc_root()
    out = []
    try:
        children = sorted(d.iterdir())
    except OSError:
        return []
    for c in children:
        try:
            if c.is_file() and _DECK_FILE_RE.match(c.name):
                out.append(c)
            elif c.is_dir() and c.parent == d and root in c.parents:
                for f in sorted(c.iterdir()):
                    if f.is_file() and _DECK_FILE_RE.match(f.name):
                        out.append(f)
        except OSError:
            continue
    return out


def _doc_artifacts(doc_id, doc_path):
    r"""这份资料在文件系统上的**全部产物**（只列出，不删）。

    返回 `{"files": [...], "dirs": [...], "bytes": n}`：
      · `files`：产物文件 + 它们的 `.bak-*` 备份（logs\knowledge\ 与 logs\outline\）；
      · `dirs` ：能确定**该整目录属于本文档**的页图目录（白名单文件之外的残留会让
                 调用方保留目录本身，见 `_purge_artifacts`）。
    任何一项都不存在时安静跳过——**不报错、不中止**（幂等：重跑或文件已被手工
    删掉的情形都必须能走完）。
    """
    files = []
    seen = set()

    def _add(p: "Path | None") -> "None":
        if p is None:
            return
        if p in seen:
            return
        seen.add(p)
        if p.exists():
            files.append(p)

    _add(_artifact_file(doc_id))
    _add(_artifact_terms_file(doc_id))
    _add(_outline_file(doc_id))

    dirs = _deck_dirs_for(doc_path)
    total = 0
    for p in files:
        try:
            total += p.stat().st_size
        except OSError:
            pass

    for d in dirs:
        for f in _deck_files(d):
            try:
                total += f.stat().st_size
            except OSError:
                pass

    return {"files": files, "dirs": dirs, "bytes": total}


def _purge_artifacts(doc_id, doc_path):
    """真删文件系统产物。**幂等、容错**：单个文件删不掉只记一笔，不影响整批。

    顺序不能反（这里踩过一次，改前 bug 的表现是"页图目录一个文件都没删，却报告
    「有 7 个页图文件删不掉」"）：**先删白名单文件，再看目录里还剩什么**。
    反过来先看剩什么再删，判断永远命中"还剩着"，于是删除那一步根本走不到。
    文件全删净之后目录里若**一个东西都不剩**才 `rmdir`；还有别的东西（人手工
    放进去的、渲染中间产物）就**保留目录**并在 `decks_kept` 里写明原因。
    """
    plan = _doc_artifacts(doc_id, doc_path)
    removed = []
    failed = []
    bytes_freed = 0
    for p in plan['files']:
        try:
            n = p.stat().st_size
        except OSError:
            n = 0
        try:
            p.unlink()
            bytes_freed += n
            removed.append(str(p))
        except FileNotFoundError:
            continue
        except OSError as e:
            failed.append({'path': str(p), 'error': f'{type(e).__name__}: {e}'})

    decks_gone = []
    decks_kept = []
    for d in plan['dirs']:
        left_over = []
        for f in _deck_files(d):
            try:
                n = f.stat().st_size
            except OSError:
                n = 0
            try:
                f.unlink()
                bytes_freed += n
                removed.append(str(f))
            except FileNotFoundError:
                continue
            except OSError as e:
                failed.append({'path': str(f), 'error': f'{type(e).__name__}: {e}'})
                left_over.append(f.name)

        if left_over:
            decks_kept.append({'dir': str(d), 'reason': '仍有页图文件未能删除', 'kept': left_over[:20]})
            continue

        try:
            left = sorted(x for x in d.iterdir())
        except OSError as e:
            failed.append({'path': str(d), 'error': f'{type(e).__name__}: {e}'})
            continue

        if left:
            decks_kept.append({'dir': str(d), 'reason': '目录里还有非页图文件', 'kept': left[:20]})
            continue

        try:
            d.rmdir()
            decks_gone.append(str(d))
        except OSError as e:
            failed.append({'path': str(d), 'error': f'{type(e).__name__}: {e}'})

    return {
        'files_removed': removed,
        'n_files': len(removed),
        'dirs_removed': decks_gone,
        'dirs_kept': decks_kept,
        'bytes_freed': bytes_freed,
        'errors': failed,
    }


def _artifact_paths_pretty(doc_id, doc_path):
    r"""预览/回执里给人看的产物清单（相对项目根的短路径，便于肉眼核对）。

    返回 `(文件, 页图目录)` 两串：
      · 文件 = `logs\knowledge\` 与 `logs\outline\` 下**这一份资料自己的**文件
        （产物 + 备份）。逐份可枚举，所以这里如实列出。
      · 页图目录 = `logs\figure_desc\<deck>\`。**只报目录**、不逐张列 png——
        一份课件几百张页图，列全了会把确认框撑爆；回执里给"目录 + 文件数"足够核对。
    """
    plan = _doc_artifacts(doc_id, doc_path)
    root = Path(config.ROOT).resolve()
    files = [_rel_display(p, root) for p in plan["files"]]
    decks = []
    for d in plan["dirs"]:
        n = len(_deck_files(d))
        decks.append("%s（%d 个页图文件）" % (_rel_display(d, root), n))
    return files, decks


def _rel_display(p, root):
    try:
        return str(p.resolve().relative_to(root)).replace("\\", "/")
    except (ValueError, OSError):
        return str(p)


def _validate_doc_ids(payload):
    """整批先校验：任何一项不是正整数 → **整批拒绝**（一个都不删）。

    与 `api_kp_clear` 逐条同规矩：混着删一半会让人不知道到底动了什么。
    入参不是对象时也照"拒绝 + 说清原因"办，不去猜调用方想干什么。
    """
    if not isinstance(payload, dict):
        return [], {"error": '入参必须是对象（如 {"doc_ids":[1,2],"confirm":true}）', "n_deleted": 0}

    raw = payload.get("doc_ids")
    if raw is None and payload.get("doc_id") is not None:
        raw = [payload.get("doc_id")]

    if isinstance(raw, (str, int)):
        raw = [raw]

    if not (isinstance(raw, (list, tuple)) and raw):
        return [], {"error": '请至少勾选一份资料（doc_ids 不能为空）', "n_deleted": 0}

    if len(raw) > _DOCS_DELETE_MAX_DOCS:
        return [], {"error": '一次最多删除 %d 份' % _DOCS_DELETE_MAX_DOCS, "n_deleted": 0}

    doc_ids = []
    rejected = []
    for x in raw:
        try:
            n = int(str(x).strip())
        except (TypeError, ValueError):
            rejected.append({"value": x, "reason": '不是整数'})
            continue

        if n <= 0:
            rejected.append({"value": x, "reason": '必须是正整数（合法的 doc_id）'})
            continue

        if n in doc_ids:
            continue

        doc_ids.append(n)

    if rejected:
        return [], {
            "error": '有 %d 项不是合法的 doc_id，已整批拒绝（一份都没删）' % len(rejected),
            "rejected": rejected,
            "n_deleted": 0,
        }

    return doc_ids, None


def api_delete_docs(conn, payload):
    r"""**批量真删**：库记录 + 向量块 + 知识点产物 +（可选）raw 原文件，一起消失。

入参：`{"doc_ids": [1,2], "delete_file": true, "purge_orphans": true,
        "dry_run": false, "confirm": true}`

三条纪律（逐条照 `api_kp_clear` 与 `_subject_running` 的既有规矩）：
  1. **整批先校验**：混进任何非法 doc_id → 整批拒绝，一个都不删；
  2. **必须 confirm**：`confirm` 不为真 → 返回 `need_confirm`，同样一个都不删
     （接口能被直接 curl，前端那个确认框不算闸门）；
  3. **与运行中任务互斥**：入库/整理正在跑时拒绝——它们正按**当时那份文档**
     读写切块与产物，边跑边删会让产物落到已删文档上、或删掉正在写的产物。

容错与幂等：
  · 某个 doc_id 已不在库 → 不算失败，进 `not_in_library`，**仍旧补删它的产物文件**
    （这正是清理历史幽灵条目的路径）；
  · 某个产物文件不存在 → 安静跳过，绝不让整批半途而废；
  · 单个文件删不掉（被占用等）→ 记进 `artifact_errors`，其余照删。

⚠ **已知边界（如实说明，不假装能做到）**：已不在库的 doc_id 只能清掉
  `logs\knowledge\` 与 `logs\outline\` 下**按 doc_id 命名**的那几个产物文件；
  它的页图目录 `logs\figure_desc\<课件>\` **推不出来**——目录名来自
  `documents.path` 的文件名，而库里那一行已经没有了（页签上它的 title 也
  只剩「文档 N」）。这类目录留在磁盘上（`_artifact_doc_list` 不读它，
  所以不会长出幽灵页签），要清就按目录名手工删。

与单份删除的关系：库那一段**就是** `api_delete_doc`（不复制它的逻辑，
免得两条路越走越远）；本函数只额外补文件系统那一步。
"""
    if not isinstance(payload, dict):
        return {"error": '入参必须是对象（如 {"doc_ids":[1,2],"confirm":true}）', "n_deleted": 0}

    dry_run = bool(payload.get("dry_run"))
    if not dry_run and not payload.get("confirm"):
        return {"need_confirm": True, "n_deleted": 0, "deleted": [], "error": '批量真删不可逆（库记录、向量块、知识点产物、raw 原文件都会消失）；确认后带上 confirm=true 再来'}

    doc_ids, bad = _validate_doc_ids(payload)
    if bad:
        return bad

    if not dry_run:
        running = _subject_running()
        if running:
            return {"error": '有任务正在跑（%s）：切块与产物此刻正在被读写，等它结束再删（否则可能删掉刚写出来的内容，或把产物落到已删文档上）' % running, "n_deleted": 0}

    delete_file = bool(payload.get("delete_file"))
    purge_orphans = bool(payload.get("purge_orphans", True))

    rows = {int(r["id"]): dict(r) for r in conn.execute("SELECT id, title, path FROM documents")}

    items = []
    for did in doc_ids:
        row = rows.get(did)
        items.append({
            "doc_id": did,
            "title": row["title"] if row else "",
            "path": row["path"] if row else "",
            "in_library": row is not None,
        })

    if dry_run:
        out = []
        for it in items:
            if it["in_library"]:
                prev = api_delete_doc(conn, it["doc_id"], delete_file=delete_file, purge_orphans=purge_orphans, dry_run=True)
            else:
                prev = {"error": "文档 %d 不在库" % it["doc_id"]}
            plan = _doc_artifacts(it["doc_id"], it["path"])
            files, decks = _artifact_paths_pretty(it["doc_id"], it["path"])

            prev["doc_id"] = it["doc_id"]
            prev["in_library"] = it["in_library"]
            if not prev.get("title"):
                prev["title"] = it["title"] or ("文档 %d" % it["doc_id"])
            prev["artifacts_will_remove"] = files
            prev["decks_will_remove"] = decks
            prev["artifacts_bytes"] = int(plan["bytes"])
            out.append(prev)

        tot_kp = sum((o.get("deleted_kps") or 0) for o in out)
        return {
            "dry_run": True,
            "n_docs": len(doc_ids),
            "documents": out,
            "delete_file": delete_file,
            "purge_orphans": purge_orphans,
            "artifacts_total": sum(len(o.get("artifacts_will_remove") or []) for o in out),
            "decks_total": sum(len(o.get("decks_will_remove") or []) for o in out),
            "artifacts_bytes_total": sum(int(o.get("artifacts_bytes") or 0) for o in out),
            "orphan_kps_total": tot_kp,
            "note": '预览只算不删；确认后请带 confirm=true 重发一次。',
        }

    deleted = []
    not_in_library = []
    artifact_errors = []
    n_chunks = n_files = n_bytes = 0
    purged = {"deleted_kps": 0, "reviews": 0, "edges": 0, "mistakes_unlinked": 0}

    for it in items:
        did = it["doc_id"]
        rec = {
            "doc_id": did,
            "title": it["title"] or ("文档 %d" % did),
            "in_library": bool(it["in_library"]),
        }

        if not it["in_library"]:
            not_in_library.append(did)
            rec["not_in_library"] = True
        else:
            r = api_delete_doc(conn, did, delete_file=delete_file, purge_orphans=purge_orphans, dry_run=False)
            rec.update({
                "title": r.get("title"),
                "path": r.get("path"),
                "chunks_removed": int(r.get("chunks_removed") or 0),
                "file_removed": r.get("file_removed"),
                "file_error": r.get("file_error"),
            })
            n_chunks += int(r.get("chunks_removed") or 0)
            for k in purged:
                purged[k] += int((r.get("purged") or {}).get(k) or 0)

        art = _purge_artifacts(did, it["path"])
        rec["artifacts_removed"] = art["files_removed"] + art["dirs_removed"]
        rec["artifacts_n"] = art["n_files"] + len(art["dirs_removed"])
        rec["artifacts_bytes"] = art["bytes_freed"]
        rec["artifacts_kept"] = art["dirs_kept"]
        n_files += rec["artifacts_n"]
        n_bytes += art["bytes_freed"]
        if art["errors"]:
            artifact_errors += [dict(e, doc_id=did) for e in art["errors"]]
            rec["artifact_errors"] = art["errors"]
        deleted.append(rec)

    nfile_total = sum((d.get("artifacts_n") or 0) for d in deleted)

    return {
        "ok": not artifact_errors,
        "n_docs": len(doc_ids),
        "n_deleted": len(doc_ids) - len(not_in_library),
        "not_in_library": not_in_library,
        "delete_file": delete_file,
        "chunks_removed": n_chunks,
        "purged": purged,
        "artifacts_n": n_files,
        "artifacts_bytes": n_bytes,
        "artifact_errors": artifact_errors,
        "files_removed": nfile_total,
        "deleted": deleted,
        "note": '库记录 / 切块 / 向量 / 识图记录 / 知识点引用 与四处文件系统产物（末级知识点总结+备份、名词解释、结构缓存、页图目录）一并不留' + ('，raw\\ 里的原文件也已永久删除' if delete_file else '；raw\\ 里的原文件**保留**（本次没勾「连原文件一起删」）'),
    }


def _rename_error(msg, extra=None, out=None):
    """重命名失败的统一回执（`ok:false` + 一句能直接给用户看的原因）。

    [C-0107 修] 重建版写成 `(msg, extra, out)` 三个**必填**参数，而调用点用的是
    `_rename_error(msg)` 与 `_rename_error(msg, doc_id=doc_id)` 两种形式 ——
    前者少传参数直接 TypeError，后者用关键字也不匹配。真值
    `dashboard.funcs.txt::L3585` 是 `_rename_error  args=1  vars: msg, extra, out`
    ⇒ 只有 `msg` 必填；`extra` 是"附加到回执里的字段"（dict）；`out` 保留形参
    位置但不参与构造（重建版里它被重新赋值、从未被读）。
    故：定义补默认值，并把调用点那几处 `doc_id=` / `rolled_back=` 关键字改回
    真值的 `extra` 位置参 dict（同文件内 C-0107 的配套改动）。
    """
    out = {"ok": False, "error": msg}
    out.update(extra or {})
    return out


def _rename_running_gate():
    """改名与长任务互斥：返回拒绝回执，空闲时返回 None（照 `api_subjects_switch`）。

    [C-0107 修] 重建版写成 `_rename_running_gate(running)`（必填参数），
    而三个调用点都是 `_rename_running_gate()` 无参调用 —— 真值
    `dashboard.funcs.txt::L3592` 是 `args=0  vars: running`，`running` 只是
    **函数内部**的局部变量（下面这行自己取 `_subject_running()`）。
    不修的话，三条重命名路径（/api/rename/raw、/api/doc/rename、
    /api/artifact/rename）一旦走到这一步就 TypeError。
    """
    running = _subject_running()
    if not running:
        return None
    return {
        "ok": False,
        "_code": 409,
        "error": "有任务正在跑（%s）：切块与产物此刻正在被读写，等它结束再改名（否则会把产物写到旧名字上，或让改名后的文件与库记录对不上）" % running,
    }


def _clean_new_name(name, field, max_chars=32):
    """通用名字校验：空白折叠、拒绝非法字符、限长。不合法抛 `ValueError`（原话给前端）。

    与 `core/subjects.py::validate_name` 同一口径（空白折叠 + 非法字符**直接拒绝**），
    只是这里同时服务于标题与文件名，所以长度上限做成参数。刻意**不**静默删掉非法
    字符：`a/b` 被悄悄写成 `ab` 会让人以为自己改错了。
    """
    if not isinstance(name, str):
        raise ValueError("%s不能为空" % field)
    s = re.sub("[\\s\\u3000]+", " ", name.strip())
    if not s:
        raise ValueError("%s不能为空" % field)
    bad = sorted(c for c in s if c in _WIN_BAD_CHARS or ord(c) < 32)
    if bad:
        raise ValueError(
            f"{field}不能包含这些字符：" + " ".join(str(c) for c in bad)
        )
    if s in (".", "..") or s.endswith("."):
        raise ValueError("%s不能是点开头/点结尾（Windows 会吃掉结尾的点）" % field)
    if len(s) > int(max_chars):
        raise ValueError(
            "%s太长（最多 %d 字，当前 %d 字）" % (field, int(max_chars), len(s))
        )
    return s


def _doc_row_of_rel(conn, rel_posix):
    """按"相对 raw/ 的正斜杠路径"找 documents 行（与 `/api/raw` 同口径）。"""
    want = (rel_posix or "").replace("\\", "/")
    for r in conn.execute("SELECT id, path, abs_path, title, kind, content_hash FROM documents"):
        if _norm_doc_path(r["path"]) == want:
            return dict(r)
    return None


def api_rename_raw(conn, payload):
    """重命名 raw 目录里的原文件，并把**两份副本 + 页图目录**一起搬过去。

    入参：`{"rel": "药理/x.pptx", "new_name": "y.pptx",
            "dry_run": false, "confirm": true}`

    为什么必须连带改（少改一处的后果，全是实测口径）：
      · `documents.path`（UNIQUE）/ `abs_path` 是磁盘文件名的副本 ⇒ 只搬文件不改库，
        看板立刻列出一条"索引里有、raw 里没有"的孤儿；且 `ingest` 用
        `WHERE path=?` 认人 ⇒ 重入会**新建一条 doc 行（新 doc_id）**，旧 doc_id 的
        整理产物与页图当场全部失联。
      · 页图目录名由**文件名主干**决定（`core/figures.py::_safe_dirname(path.stem)`）
        ⇒ 目录不跟着改名，看板页图立刻全部消失（`_deck_dir_for` 找不到目录、
        `/img/slide` 给 404）。**数据没丢**，重渲染能恢复——但不该让人白等一次。

    刻意**不允许改扩展名**：`kind` 是入库时按扩展名判的（`extract.detect_kind`），
    把 `.pptx` 改成 `.txt` 会让这条资料的下次入库走另一条解析路径。要换格式请重新
    导入，不要在这儿改名。

    执行顺序：**磁盘 → 库 → 页图目录**；任一步失败就逆序回滚（含把文件搬回去），
    回执里如实写明回滚结果。
    """
    if not isinstance(payload, dict):
        return _rename_error('入参必须是对象（如 {"rel":"a/b.pptx","new_name":"c.pptx","confirm":true}）')

    rel = str(payload.get("rel") or "").strip()
    if not rel:
        return _rename_error("缺少 rel（相对 raw 目录的路径）")

    try:
        src = _safe_raw_path(rel)
    except ValueError as e:
        return _rename_error(str(e))

    if not src.is_file():
        return _rename_error("文件不存在：%s" % rel)

    rel_posix = _rel_to_raw(src)
    old_full = src.name
    try:
        clean = _clean_new_name(payload.get("new_name", ""), "新文件名", max_chars=100)
    except ValueError as e:
        return _rename_error(str(e))

    if Path(clean).name != clean or clean in (".", ".."):
        return _rename_error("新名字只能是文件名，不能带路径：%s" % clean)

    old_suffix = src.suffix
    new_suffix = Path(clean).suffix
    if new_suffix.casefold() != old_suffix.casefold():
        return _rename_error("新名字必须保留扩展名 %s（改扩展名会让入库类型失效）" % (old_suffix or "（无）"))

    if clean == old_full:
        return _rename_error("新名字与原名相同，没有要改的")

    dst = src.with_name(clean)
    if dst.exists():
        return _rename_error("目标已存在，不覆盖：%s" % clean)

    if "/" in rel_posix:
        new_rel_posix = rel_posix.rsplit("/", 1)[0] + "/" + clean
    else:
        new_rel_posix = clean

    row = _doc_row_of_rel(conn, rel_posix)
    old_path = str(row["path"]) if row else ""
    old_abs = str(row["abs_path"]) if row else ""

    new_path = ""
    if row:
        new_path = os.path.join(os.path.dirname(old_path.replace("/", os.sep)), clean)
        dup = conn.execute(
            "SELECT id FROM documents WHERE path=? AND id<>?",
            (new_path, int(row["id"]))).fetchone()
        if dup:
            return _rename_error(
                "库里已有一条记录指向 %s（doc#%d）：改名会让两条撞上 path 的 UNIQUE 约束"
                % (new_path, int(dup["id"])))

    old_decks = _deck_dirs_for(old_path or rel_posix)
    new_deck_name = share_mod.safe_dirname(Path(clean).stem)
    deck_plan = [
        {"from": str(d), "to": str(d.with_name(new_deck_name))}
        for d in old_decks
    ]

    preview = {
        "ok": True,
        "dry_run": True,
        "doc_id": int(row["id"]) if row else None,
        "title": str(row["title"]) if row else "",
        "rel": {"from": rel_posix, "to": new_rel_posix},
        "file": {"from": str(src), "to": str(dst)},
        "db": {
            "before": {"path": old_path, "abs_path": old_abs},
            "after": {"path": new_path, "abs_path": str(dst)},
            "updated": bool(row),
        },
        "figure_desc": deck_plan,
        "note": "原文件改名会同时改 ① 磁盘文件名 ② documents.path/abs_path ③ logs/figure_desc 下的页图目录；内容哈希不变，重入不会新建 doc 行。",
    }

    if bool(payload.get("dry_run")):
        return preview

    gate = _rename_running_gate()
    if gate:
        return gate

    if not payload.get("confirm"):
        return {
            "ok": False,
            "need_confirm": True,
            "doc_id": preview["doc_id"],
            "preview": preview,
            "error": "重命名会同时改磁盘文件名、库记录与页图目录；确认后带 confirm=true 再来",
        }

    did = int(row["id"]) if row else None
    deck_moved = []
    db_done = False
    try:
        os.replace(src, dst)
        if did is not None:
            conn.execute(
                "UPDATE documents SET path=?, abs_path=? WHERE id=?",
                (new_path, str(dst), did))
            conn.commit()
            db_done = True
        for d in old_decks:
            nd = d.with_name(new_deck_name)
            if nd.exists():
                deck_moved.append({
                    "from": str(d),
                    "to": str(nd),
                    "moved": False,
                    "reason": "目标目录已存在，保留原目录不合并",
                })
                continue
            d.rename(nd)
            deck_moved.append({"from": str(d), "to": str(nd), "moved": True})
    except (OSError, sqlite3.Error) as e:
        back = []
        for rec in reversed(deck_moved):
            if not rec.get("moved"):
                continue
            try:
                Path(rec["to"]).rename(Path(rec["from"]))
                back.append("页图目录已搬回 " + rec["from"])
            except OSError as e2:
                back.append(f"页图目录搬回失败 {rec['from']}：{e2}")
        if db_done:
            try:
                conn.execute(
                    "UPDATE documents SET path=?, abs_path=? WHERE id=?",
                    (old_path, old_abs, did))
                conn.commit()
                back.append("库记录已还原 " + old_path)
            except sqlite3.Error as e2:
                back.append("库记录还原失败：%s" % e2)
        try:
            if dst.exists() and not src.exists():
                os.replace(dst, src)
                back.append("文件名已还原 " + str(src))
        except OSError as e2:
            back.append("文件名还原失败：%s" % e2)
        return _rename_error(
            "重命名失败，已尽力回滚：" + "；".join(back) + ": " + str(e),
            {"rolled_back": back, "rel": rel_posix, "new_name": clean})

    cleared = _reset_subject_caches()
    out = dict(preview)
    out.update({
        "dry_run": False,
        "confirmed": True,
        "figure_desc": deck_moved,
        "cleared": cleared,
    })
    out["note"] = preview["note"] + "（本次已实际执行）"
    return out


def api_rename_doc_title(conn, payload):
    """重命名**课件标题**（`documents.title`）。不碰文件、不碰产物文件、不改 doc_id。

    入参：`{"doc_id": 103, "title": "药物化学 第五章", "dry_run": false, "confirm": true}`

    ⚠ 影响范围（实测结论，回执里原样给出）：产物**内容**里的课件名是**生成时**写进去的
      （`core/kp_build.py::doc_title` → `meta.deck` 与结构树根名），改名**不会**回头改
      它们 ⇒ 不重跑整理时，思维导图的根名仍是旧名。想让产物也立刻换名，用
      「重命名产物标题」（`api_rename_artifact_title`：只改名字、不重跑、不花一分钱）。
      另外 `documents.role` 是**入库时**算的（`db.classify_role`），本函数**不重算**
      —— 标题里出现"教材"两个字不会把它变成教材，那要走重新入库。
    """
    if not isinstance(payload, dict):
        return _rename_error('入参必须是对象（如 {"doc_id":103,"title":"新名字"}）')

    try:
        doc_id = int(payload.get("doc_id") or 0)
    except (TypeError, ValueError):
        return _rename_error("doc_id 必须是整数")

    if doc_id <= 0:
        return _rename_error("缺少 doc_id")

    row = conn.execute(
        "SELECT id, path, title FROM documents WHERE id=?", (doc_id,)
    ).fetchone()
    if not row:
        return _rename_error("文档 %d 不存在（可能已被删除）" % doc_id)

    old = str(row["title"] or "")
    art = _artifact_load(doc_id)
    deck_now = ""
    if art.get("state") == _ARTIFACT_V2_STATE:
        st = (art.get("doc") or {}).get("structure") or {}
        root = st.get("root") if isinstance(st.get("root"), dict) else {}
        deck_now = str(root.get("name") or "")

    preview = {
        "ok": True,
        "dry_run": True,
        "doc_id": doc_id,
        "path": str(row["path"]),
        "title": {"from": old, "to": str(payload.get("title") or old)},
        "artifact_state": str(art.get("state") or ""),
        "artifact_deck": deck_now,
        "artifact_will_change": False,
        "note": "只改库里这一列。整理产物**内容**里的课件名（思维导图根名 meta.deck）是生成时写进去的，不会跟着变——要它立刻变，用「重命名产物标题」。",
    }

    if bool(payload.get("dry_run")):
        return preview

    try:
        clean = _clean_new_name(payload.get("title", ""), field="课件标题")
    except ValueError as e:
        return _rename_error(str(e), {"doc_id": doc_id})

    gate = _rename_running_gate()
    if gate:
        return gate

    if not payload.get("confirm"):
        return {
            "ok": False,
            "need_confirm": True,
            "doc_id": doc_id,
            "preview": preview,
            "error": "改课件标题会写库；确认后带 confirm=true 再来",
        }

    if clean == old:
        return _rename_error("新标题与原标题相同，没有要改的", {"doc_id": doc_id})

    try:
        conn.execute("UPDATE documents SET title=? WHERE id=?", (clean, doc_id))
        conn.commit()
    except sqlite3.Error as e:
        return _rename_error(
            f"写库失败：{type(e).__name__}: {e}", {"doc_id": doc_id}
        )

    cleared = _reset_subject_caches()
    out = dict(preview)
    out.update({
        "dry_run": False,
        "confirmed": True,
        "cleared": cleared,
        "title": {"from": old, "to": clean},
        "note": preview["note"] + "（本次已实际执行）",
    })
    return out


def _artifact_title_spots(raw):
    """产物里那个"课件标题"的四处落点 + 当前值（改前/改后对照就靠它）。"""
    st = raw.get("structure") if isinstance(raw.get("structure"), dict) else {}
    root = st.get("root") if isinstance(st.get("root"), dict) else {}
    rr = st.get("raw_root") if isinstance(st.get("raw_root"), dict) else {}
    nodes = st.get("nodes") if isinstance(st.get("nodes"), list) else []
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    first = nodes[0] if nodes and isinstance(nodes[0], dict) else {}
    return {
        "structure.root.name": root.get("name"),
        "structure.raw_root.name": rr.get("name"),
        "structure.nodes[0].name": first.get("name"),
        "meta.deck": meta.get("deck"),
    }


def api_rename_artifact_title(conn, payload):
    if not isinstance(payload, dict):
        return _rename_error('入参必须是对象（如 {"doc_id":103,"name":"新标题"}）')
    try:
        doc_id = int(payload.get("doc_id") or 0)
    except (TypeError, ValueError):
        return _rename_error("doc_id 必须是整数")
    if doc_id <= 0:
        return _rename_error("缺少 doc_id")
    art = _artifact_load(doc_id)
    state = str(art.get("state") or "")
    if state != _ARTIFACT_V2_STATE:
        return _rename_error(
            f"这份课件没有可改名的整理产物（状态：{state or 'none'}"
            f"{_ARTIFACT_MESSAGE.get(state, '')}）。",
            {"doc_id": doc_id, "artifact_state": state})
    p = _artifact_file(doc_id)
    if p is None or not p.is_file():
        return _rename_error("产物文件找不到：%s" % (p or "路径非法"), {"doc_id": doc_id})
    raw = art.get("doc") or {}
    before = _artifact_title_spots(raw)
    cur = str(before.get("structure.root.name") or "")
    preview = {
        "ok": True,
        "dry_run": True,
        "doc_id": doc_id,
        "artifact": {"path": str(p), "state": state},
        "name": {"from": cur, "to": str(payload.get("name") or cur)},
        "spots_before": before,
        "note": "改的是产物 JSON 里的四处课件名（root / raw_root / nodes[0] / meta.deck）；节点 id 与知识点关联一个字都不动，产物文件名也不变。",
    }
    if bool(payload.get("dry_run")):
        return preview
    try:
        clean = _clean_new_name(payload.get("name", ""), field="产物标题")
    except ValueError as e:
        return _rename_error(str(e), {"doc_id": doc_id})
    gate = _rename_running_gate()
    if gate:
        return gate
    if not payload.get("confirm"):
        return {
            "ok": False,
            "need_confirm": True,
            "doc_id": doc_id,
            "preview": preview,
            "error": "改产物标题会重写产物文件（先自动备份一份）；确认后带 confirm=true 再来",
        }
    if cur == clean:
        return _rename_error("新标题与产物里的当前标题相同，没有要改的", {"doc_id": doc_id})
    st = raw.get("structure") if isinstance(raw.get("structure"), dict) else None
    if st is None:
        return _rename_error("产物里没有 structure（可能上次整理中断），请重新整理", {"doc_id": doc_id})
    changed = []
    root = st.get("root")
    if isinstance(root, dict):
        root["name"] = clean
        changed.append("structure.root.name")
    rr = st.get("raw_root")
    if isinstance(rr, dict):
        rr["name"] = clean
        changed.append("structure.raw_root.name")
    nodes = st.get("nodes")
    if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict):
        nodes[0]["name"] = clean
        changed.append("structure.nodes[0].name")
    meta = raw.get("meta")
    if isinstance(meta, dict):
        meta["deck"] = clean
        changed.append("meta.deck")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = p.with_name(p.name + ".bak-rename-" + stamp)
    try:
        shutil.copy2(p, bak)
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except (OSError, ValueError) as e:
        try:
            if bak.is_file() and not p.is_file():
                os.replace(bak, p)
        except OSError:
            pass
        return _rename_error(f"写产物失败：{type(e).__name__}: {e}", {"doc_id": doc_id})
    cleared = _reset_subject_caches()
    after = _artifact_title_spots(raw)
    out = dict(preview)
    out.update({
        "dry_run": False,
        "confirmed": True,
        "cleared": cleared,
        "spots_after": after,
        "changed": changed,
        "backup": str(bak),
        "note": preview["note"] + ("（本次已实际执行；旧产物备份在 %s）" % bak.name),
    })
    return out


def cleanup_orphans(conn, dry_run=False, expected_kp=None):
    """清理失去全部出处的知识点，以及挂在它们下面的复习记录与依赖边。

    为什么必须做这件事：
    知识点的出处（kp_sources）指向 chunks，而 chunks 随文档级联删除。
    删掉资料后，知识点会变成"没有任何原文支撑"的孤儿——它挂在总览与列表里
    看着还在，但已经无法用检索验证，也没法回链原文。
    早期版本的删除只摘掉了 kp_sources 的引用，没清理这些孤儿，
    于是用户删完资料后仍看到 17 个知识点、13 条依赖边、35 条复习记录，
    错题也还指着已经不存在的知识点。

    dry_run 只统计不删除，用于删除前告知影响范围。
    expected_kp 用于排除"当前操作正要删除的知识点"。
    """
    orphan_ids = [
        int(r["id"])
        for r in conn.execute(
            "SELECT k.id FROM knowledge_points k\n               WHERE COALESCE(k.had_sources, 0) = 1\n                 AND NOT EXISTS (SELECT 1 FROM kp_sources s WHERE s.kp_id = k.id)"
        )
    ]
    if expected_kp is not None:
        orphan_ids = [i for i in orphan_ids if i != int(expected_kp)]
    if not orphan_ids:
        return {
            "orphan_kps": 0,
            "deleted_kps": 0,
            "reviews": 0,
            "edges": 0,
            "mistakes_unlinked": 0,
            "names": [],
        }
    names = [
        r["name"]
        for r in conn.execute(
            f"{'SELECT name FROM knowledge_points WHERE id IN ('}{','.join('?' * len(orphan_ids))}{')'}",
            orphan_ids,
        )
    ]
    ph = ",".join("?" * len(orphan_ids))
    n_rev = conn.execute(
        f"{'SELECT COUNT(*) AS n FROM reviews WHERE kp_id IN ('}{ph}{')'}",
        orphan_ids,
    ).fetchone()["n"]
    n_edge = conn.execute(
        f"{'SELECT COUNT(*) AS n FROM kp_edges\n            WHERE src_id IN ('}{ph}{') OR dst_id IN ('}{ph}{')'}",
        orphan_ids + orphan_ids,
    ).fetchone()["n"]
    n_mis = conn.execute(
        f"{'SELECT COUNT(*) AS n FROM mistakes WHERE kp_id IN ('}{ph}{')'}",
        orphan_ids,
    ).fetchone()["n"]
    if not dry_run:
        conn.execute(
            f"{'UPDATE mistakes SET kp_id=NULL WHERE kp_id IN ('}{ph}{')'}",
            orphan_ids,
        )
        conn.execute(
            f"{'DELETE FROM reviews WHERE kp_id IN ('}{ph}{')'}",
            orphan_ids,
        )
        conn.execute(
            f"{'DELETE FROM kp_edges WHERE src_id IN ('}{ph}{') OR dst_id IN ('}{ph}{')'}",
            orphan_ids + orphan_ids,
        )
        conn.execute(
            f"{'DELETE FROM kp_sources WHERE kp_id IN ('}{ph}{')'}",
            orphan_ids,
        )
        conn.execute(
            f"{'DELETE FROM knowledge_points WHERE id IN ('}{ph}{')'}",
            orphan_ids,
        )
        conn.commit()
    return {
        "orphan_kps": len(orphan_ids),
        "deleted_kps": 0 if dry_run else len(orphan_ids),
        "reviews": int(n_rev),
        "edges": int(n_edge),
        "mistakes_unlinked": int(n_mis),
        "names": names[:20],
        "dry_run": dry_run,
    }


def delete_all_notes():
    """清空 notes/ 目录。笔记是助手回写的结论，属独立产物，
    只有用户明确要求时才清；因此单列一个操作而不是挂在删资料上。"""
    notes_dir = Path(config.NOTES_DIR)
    if not notes_dir.exists():
        return 0
    n = 0
    for p in notes_dir.glob('*.md'):
        try:
            p.unlink()
            n += 1
        except OSError:
            continue
    return n


def api_cleanup(dry_run, include_notes, include_mistakes):
    """一键清理：孤儿知识点 + 其复习记录与依赖边，可选清空笔记与错题。

错题默认**不删**：它是一条独立的练习记录，删资料不等于想放弃错题本。
但用户若确实想一并清空，勾选即可——早期版本把它写死为"保留"，
用户删完资料后看到错题还在，只能再来要求处理。
"""
    conn = db.connect()
    db.init_db(conn)
    try:
        r = cleanup_orphans(conn, dry_run=dry_run)
        if include_notes:
            n_notes = len(study.list_notes())
            r["notes"] = n_notes if dry_run else delete_all_notes()
        if include_mistakes:
            n_mis = conn.execute(
                "SELECT COUNT(*) AS n FROM mistakes").fetchone()["n"]
            if dry_run:
                r["mistakes"] = int(n_mis)
            else:
                r["mistakes"] = delete_mistakes(conn, None, False, all_=True)["deleted"]
        r["documents"] = conn.execute(
            "SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        return r
    finally:
        conn.close()


def delete_mistakes(conn, ids, only_resolved, all_):
    """删除错题。三种模式，必须显式指定其一。

    刻意**不**提供"参数为空就删全部"的行为：空 ids 表示"什么都没选"，
    把它当成"删全部"是危险的——实测一次 `{"ids": []}` 就把错题清空了。
    清空必须由调用方显式传 all_=True。
    """
    if ids:
        ph = ",".join("?" * len(ids))
        cur = conn.execute(f"DELETE FROM mistakes WHERE id IN ({ph})", ids)
    elif only_resolved:
        cur = conn.execute("DELETE FROM mistakes WHERE resolved=1")
    elif all_:
        cur = conn.execute("DELETE FROM mistakes")
    else:
        return {
            "deleted": 0,
            "remaining": int(conn.execute("SELECT COUNT(*) AS n FROM mistakes").fetchone()["n"]),
            "error": "未指定删除范围（需要 ids / only_resolved / all_ 之一）",
        }
    conn.commit()
    left = conn.execute("SELECT COUNT(*) AS n FROM mistakes").fetchone()["n"]
    return {"deleted": cur.rowcount, "remaining": int(left)}


def api_chat_status():
    """检查问答助手是否可用（密钥是否就绪、模型名）。"""
    from core import chat as chat_mod
    out = {
        "ok": False,
        "model": config.CHAT_MODEL,
        "key": False,
        "error": "",
        "base": config.CHAT_BASE_URL,
        "thinking": bool(config.value_int("CHAT_THINKING", 0)),
        "reasoning_effort": config.CHAT_REASONING_EFFORT,
    }
    try:
        if not chat_mod.load_api_key():
            raise RuntimeError("没有找到可用的 API 密钥")
        out["key"] = True
        out["ok"] = True
    except Exception as e:
        out["error"] = str(e)
    return out


def api_firstrun(conn):
    """首次运行体检（卡 C-0071）：库空不空？密钥配没配？

    **只读、不联网、不调模型、不花钱**。它存在的唯一理由，是让看板在
    **第一屏**就把"下一步该干什么"说清楚——「空库 + 没配密钥」是新用户
    最可能撞上的组合，而原来那一屏只有一串 0 和一条红字，看上去像程序坏了。

    三条硬规矩：
      · 只**呈报事实**（文档数、凭据链是否可用），不替用户改任何东西；
      · 密钥缺失只说**哪几条管线不可用**，绝不把离线功能说成不能用
        ——浏览资料、检索本地库、看知识点与笔记都不需要密钥；
      · 路径一律取自 `config` 的**真实取值**（切了学科、或打包成 exe
        数据根会变），前端只负责显示，不自己拼盘符、不写死用户名。
    """
    n = int(conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"])
    return {
        "docs": n,
        "empty": n == 0,
        "credential": config.credential_ready(),
        "raw_dir": str(config.RAW_DIR),
        "db_path": str(config.DB_PATH),
        "settings_file": str(config.SETTINGS_FILE),
        "env_example": str(config.ROOT / ".env.example"),
    }


def _prompt_slots():
    """设置中心「提示词」页的可编辑槽位：文件名由 kp_build 的**路径函数**给出。

    ⚠ 卡 C-0113：重建版这里写成 `Path(getattr(kp_build_mod, fn))`——**漏了调用括号**，
    于是 `Path` 收到的是一个 function 对象，CPython 当场抛
    `TypeError: argument should be a str or an os.PathLike object where
    __fspath__ returns a str, not 'function'`。三个槽位**全部** ok=False、
    既看不到提示词正文也没法编辑（用户的实测反馈："大模型调用中均没有提示词"）。
    真值判据：这三个名字在 `core/kp_build.py` 里都是 `def ...() -> Path`（路径函数，
    见 L2115/L2119/L3646 附近的定义），必须**调用**才拿到 Path；
    同文件别处取路径的写法也都是调用形式（如 `kp_build_mod.prompt_file()`）。
    """
    out = []
    for sid, label, role, fn in (
        ('kp', '知识点总结', '逐条生成末级知识点', 'default_kp_prompt_file_v2'),
        ('struct', '思维导图结构', '把整份课件画成结构树', 'default_struct_prompt_file'),
        ('terms', '名词解释', '名词的提炼与筛选（独立功能）', 'default_terms_prompt_file'),
    ):
        try:
            p = Path(getattr(kp_build_mod, fn)())
            d = config.read_prompt_text(p)
            out.append({
                "id": sid,
                "label": label,
                "role": role,
                "ok": bool(d.get("ok")),
                "path": str(p),
                "name": p.name,
                "sha256": d.get("sha256", ""),
                "chars": d.get("chars", 0),
                "mtime": d.get("mtime", 0),
                "error": d.get("error", ""),
            })
        except Exception as e:
            out.append({
                "id": sid,
                "label": label,
                "role": role,
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
            })
    return out


def _builtin_prompt_text(bid):
    from core import chat as chat_mod
    if bid == 'chat_system':
        return ('知识库问答 · 系统提示词', chat_mod.SYSTEM_PROMPT)
    if bid == 'chat_expand':
        return ('查询改写 · 提示词', chat_mod.EXPAND_PROMPT)
    if bid == 'ingest_outline':
        return ('入库结构分析 · 提示词（头部）', outline_mod.PROMPT_HEAD)
    return ('', '')


def api_settings():
    data = config.settings_payload()
    data['credential'] = config.credential_ready()
    data['prompts'] = _prompt_slots()
    data['builtin_prompts'] = []
    for bid, label, source in _BUILTIN_PROMPTS:
        _l, text = _builtin_prompt_text(bid)
        data['builtin_prompts'].append({
            'id': bid,
            'label': label,
            'source': source,
            'chars': len(text) if text else '',
        })
    data['no_prompt_pipelines'] = ['deck']
    return data


def api_settings_save(payload):
    """保存设置：部分更新，密钥留空＝不改（绝不把已有密钥覆盖成空）。"""
    values = payload.get('values')
    patch = values if isinstance(values, dict) else {k: v for k, v in payload.items() if k != 'values'}
    rep = config.save_settings(patch)
    api_settings()['settings'] = rep
    return rep


def api_settings_reset(payload):
    keys = payload.get('keys')
    rep = config.reset_settings(
        [str(k) for k in keys] if isinstance(keys, list) else None
    )
    api_settings()['settings'] = rep
    return rep


def api_settings_prompt(name, builtin):
    if builtin:
        label, text = _builtin_prompt_text(builtin)
        if not label:
            return {"error": "未知的内建提示词：%s" % builtin}
        # ⚠ 卡 C-0113：重建版这里写成
        #   `next((s for s in _BUILTIN_PROMPTS if s.get("id") == builtin), "")`
        # ——`_BUILTIN_PROMPTS` 的元素是**三元组** `(id, label, source)`（见 L184/L189），
        # 元组没有 `.get`，于是每次点「查看」内建提示词都
        # `AttributeError: 'tuple' object has no attribute 'get'` → HTTP 500。
        # 真值判据：同文件 `api_settings()`（L10735 附近）读同一张表用的是
        # `for bid, label, source in _BUILTIN_PROMPTS` —— **解包三元组**才是这张表的
        # 既定形状；所以这里必须按元组取第三个元素，而不是当字典查 'id' 键。
        source = next((s[2] for s in _BUILTIN_PROMPTS if s[0] == builtin), "")
        return {
            "ok": True,
            "builtin": builtin,
            "label": label,
            "text": text,
            "source": source,
            "chars": len(text or ""),
            "editable": False,
            "note": "这段提示词内建在代码里，本版不支持从窗口编辑。",
        }
    slots = _prompt_slots()
    slot = next((s for s in slots if s.get("id") == name), None)
    if slot is None:
        return {"error": "提示词不存在：%s" % name}
    d = config.read_prompt_text(slot.get("path") or "")
    if not d.get("ok"):
        return {"error": d.get("error") or "读不到提示词", **slot}
    return {
        "ok": True,
        "slot": slot["id"],
        "label": slot["label"],
        "path": d["path"],
        "sha256": d["sha256"],
        "chars": d["chars"],
        "text": d["text"],
        "editable": True,
    }


def api_settings_prompt_save(payload):
    """保存提示词真源（改前自动 .bak），回报新旧 sha256。

    ⚠ 卡 C-0113：重建版这里用 `next((s for s in slots if s.get('name') == name), None)`
    去匹配 `payload['name']`。但**前端传的是槽位 id，不是文件名**：
    `setPromptsHtml()` 的按钮是 `promptOpen('kp'|'struct'|'terms')`（见 L6193 附近），
    `promptSave(slotId)` 又把它原样放进 `{name: slotId}`（见 L6397 附近），
    而 `_prompt_slots()` 生成的字典里 `id='kp'`、`name='提示词-知识点总结-v2.md'`
    —— 两者**永远不相等**，于是每一次保存都返回
    `{'error': '提示词不存在：kp'}`，提示词改不动。
    真值判据：`api_settings_prompt(name, builtin)`（本文件 L10765 附近）取槽位用的是
    `s.get("id") == name` —— **同一个参数、同一种匹配**，保存这条必须与它一致。
    仍然接受 `name` 等于文件名（兼容按文件名调的脚本）。
    """
    name = str(payload.get('name') or '')
    slots = _prompt_slots()
    slot = next((s for s in slots
                 if s.get('id') == name or s.get('name') == name), None)
    if slot is None:
        return {'error': '提示词不存在：%s' % name}
    rep = config.save_prompt_text(slot.get('path') or '', payload.get('text') or '')
    rep['slot'] = slot['id']
    rep['prompts'] = _prompt_slots()
    return rep


def _sse(obj):
    # C-0110：重建版把 json.dumps 的**关键字参数**写成了位置参数
    # （`json.dumps(obj, False, str, ensure_ascii=False, default=str)`），py3.13 实测
    # TypeError: dumps() takes 1 positional argument but 3 positional arguments
    # (and 2 keyword-only arguments) were given —— 于是任何 SSE 帧都发不出去
    # （deck 答疑的 emit 正是走这个函数；chat 答疑那条是 inline 自己拼的，不受影响）。
    # 真值语义不变：中文不转义（ensure_ascii=False）、非 JSON 类型兜底 str（default=str）。
    return ("data: " + json.dumps(obj, ensure_ascii=False, default=str) + "\n\n").encode("utf-8")


def api_chat_stream(handler, payload):
    """流式问答：SSE 推事件。

    事件类型：
      sources —— 检索到的证据（先发，让用户立刻看到出处）
      delta   —— 回答的文本增量
      done    —— 结束，带用量统计
      error   —— 出错
    """
    from core import chat as chat_mod

    config.activate('chat')

    model = str(config.CHAT_MODEL or '').strip() or chat_mod.CHAT_MODEL
    max_tokens = config.value_int('CHAT_MAX_TOKENS', 2048)
    thinking = bool(config.value_int('CHAT_THINKING', 0))
    effort = str(config.CHAT_REASONING_EFFORT or '').strip() or None
    use_figures = bool(config.value_int('CHAT_FIGURES', 1))

    # P0-G：问答请求也走入口快照；PIPELINE_RUNTIME=0 时保持旧全局路径。
    # 问答的 2048 是“端点缺省上限”，不是设置中心 token cap：当 cap=0 时把该
    # 缺省值写进 context，避免 RunContext 把“无上限”误解成“不发送”。
    run_ctx = None
    try:
        from core.pipeline_runtime import context as runtime_context
        if runtime_context.runtime_enabled():
            import dataclasses
            run_ctx = runtime_context.make_context("chat")
            model = run_ctx.model
            thinking = bool(run_ctx.thinking)
            effort = run_ctx.reasoning_effort or None
            if int(run_ctx.max_tokens or 0) > 0:
                max_tokens = int(run_ctx.max_tokens)
            elif int(max_tokens or 0) > 0:
                run_ctx = dataclasses.replace(run_ctx, max_tokens=int(max_tokens))
    except Exception:  # noqa: BLE001 —— 快照失败不阻断问答，回旧路径
        run_ctx = None

    query = (payload.get('query') or '').strip()
    history = payload.get('history') or []
    doc_ids = payload.get('doc_ids') or None
    if doc_ids:
        doc_ids = [int(x) for x in doc_ids]
    per_kind = int(payload.get('per_kind') or chat_mod.DEFAULT_PER_KIND)

    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Accel-Buffering', 'no')
    handler.end_headers()

    def emit(ev):
        handler.wfile.write(('data: ' + json.dumps(ev, ensure_ascii=False) + '\n\n').encode('utf-8'))  # TODO(反编译存疑)
        handler.wfile.flush()  # TODO(反编译存疑)

    conn = None
    try:
        if not query:
            emit({'type': 'error', 'message': '问题为空'})
            return
        with _lock:
            conn = _conn()
            ev = chat_mod.gather_evidence(conn, query, per_kind=per_kind, doc_ids=doc_ids)

        if not use_figures:
            ev = [e for e in ev if not e.is_figure]
            for i, e in enumerate(ev, 1):
                e.idx = i

        emit({'type': 'sources', 'evidence': [e.to_dict() for e in ev],
              'params': {'model': model, 'thinking': thinking,
                         'reasoning_effort': effort if thinking else '',
                         'max_tokens': max_tokens, 'figures': use_figures}})

        if not ev:
            emit({'type': 'delta',
                  'text': '知识库里没有检索到与该问题相关的内容。请换个说法，或在「资料」页确认相关课件/教材已入库。'})
            emit({'type': 'done', 'usage': {}, 'elapsed': 0.0})
            return

        msgs = chat_mod.build_messages(query, ev, history)
        t0 = time.perf_counter()
        for kind, data in chat_mod.stream_chat(msgs, model=model, max_tokens=max_tokens,
                                               thinking=thinking, reasoning_effort=effort,
                                               run_context=run_ctx):
            if kind == 'delta':
                emit({'type': 'delta', 'text': data})
            elif kind == 'usage':
                emit({'type': 'done', 'usage': data, 'elapsed': time.perf_counter() - t0})
    except Exception as e:
        try:
            emit({'type': 'error', 'message': f'{type(e).__name__}: {e}'})
        except Exception:
            pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def api_mistakes(conn):
    rows = conn.execute(
        'SELECT m.*, k.name AS kp_name, d.title AS doc_title\n           FROM mistakes m\n           LEFT JOIN knowledge_points k ON k.id = m.kp_id\n           LEFT JOIN documents d ON d.id = m.doc_id\n           ORDER BY m.resolved ASC, m.created_at DESC LIMIT 200'
    ).fetchall()
    return {
        "mistakes": [dict(r) for r in rows],
        "stats": study.mistake_stats(conn),
    }


def api_notes(conn):
    return {"notes": study.list_notes()}


def api_note(name):
    """读一篇笔记：`content` 是**原文全文**，`body` 是剥掉"机器加的壳"的正文。

    卡 C-0082：页面显示与编辑都用 `body`（只有人自己写进去的那部分，见
    `core/study.py::note_body`），但 `content` 的语义**一个字都不改**——
    笔记页签、`tools/verify_c0052_quick.py`、分享导出都按原文算。
    取全文（不是 `api_notes_by` 那种截断版）是刻意的：知识点窗口改笔记要拿它
    回写，拿截断的正文回写会把尾部永久砍掉（卡 C-0082 的硬约束）。
    """
    want = str(name or "").strip()
    notes = study.list_notes()
    if want:
        low = want.lower()
        for n in notes:
            # 前端两种入口分别传 file 全名与 title；stem 兼容旧链接
            candidates = (n["file"], n["title"], n["file"].removesuffix(".md"))
            if want in candidates or low in [c.lower() for c in candidates]:
                text = Path(n["path"]).read_text(encoding="utf-8", errors="replace")
                return {"note": n, "content": text, "body": study.note_body(text)}
    return {"error": "笔记不存在"}


def _doc_label(conn, doc_id):
    """doc#N 的人可读名（取不到就退回 `doc#N`——绝不替它编一个标题）。"""
    if not doc_id:
        return ''
    try:
        r = conn.execute(
            "SELECT title FROM documents WHERE id=?",
            (int(doc_id),)).fetchone()
    except Exception:
        r = None
    t = str(r['title']).strip() if r and r['title'] else ''
    return t or f"doc#{doc_id}"


def api_note_save_kp(conn, payload):
    kp_id = str(payload.get('kp_id') or '').strip()
    body = str(payload.get('body') or '').strip()
    if not kp_id:
        return {'error': '缺少 kp_id：知识点笔记必须绑定一条知识点'}
    if not body:
        return {'error': '笔记内容为空'}
    doc_id = int(payload.get('doc_id') or 0)
    kp_name = str(payload.get('kp_name') or '').strip() or kp_id
    doc_title = str(payload.get('doc_title') or '').strip() or _doc_label(conn, doc_id)
    title = str(payload.get('title') or '').strip() or f'{kp_name} · 知识点笔记'
    label = f'{doc_title} · 知识点 {kp_name}' if doc_title else f'知识点 {kp_name}'
    refs = [str(x).strip() for x in (payload.get('refs') or []) if str(x).strip()]
    # C-0107 修：这里原来把 extra 的字段当成位置参一路排开
    # （`True, 'knowledge_point', kp_id, doc_id or None, label, extra={...}`），
    # 而 `save_note` 的真值签名只有 8 个参数
    # （`study.funcs.txt::L586`：`save_note args=8 vars: conn, title, body, tags,
    # source_refs, kind, extra, unique, …`）—— 第 7 个位置参**就是** `extra`，
    # 再传 `extra=` 关键字即 "got multiple values for argument 'extra'"
    # （与 F1 同一个错法）。按真值把 extra 放回第 7 位、unique 放第 8 位。
    p = study.save_note(
        conn, title, body, ['知识点笔记'],
        refs, 'note',
        {'origin': 'knowledge_point', 'kp_id': kp_id, 'doc_id': doc_id or None,
         'source_label': label},
        True,
    )
    return {
        'path': p,
        'file': Path(p).name,
        'title': title,
        'kp_id': kp_id,
        'doc_id': doc_id,
        'source_label': label,
    }


def api_note_save_deck(conn, payload):
    doc_id = int(payload.get("doc_id") or 0)
    q = str(payload.get("question") or "").strip()
    a = str(payload.get("answer") or "").strip()
    if not doc_id:
        return {"error": "缺少 doc_id：课件问答笔记要记下是哪份课件"}
    if not a:
        return {"error": "回答为空，没有可记的内容"}
    doc_title = str(payload.get("doc_title") or "").strip() or _doc_label(conn, doc_id)
    title = str(payload.get("title") or "").strip() or (
        f"{doc_title} · AI答疑：{q[:24]}" if q else f"{doc_title} · AI答疑"
    )
    body = f"**问**：{q}\n\n**答**：\n\n{a}" if q else a
    refs = [str(x).strip() for x in (payload.get("refs") or []) if str(x).strip()]
    # C-0107 修：同 api_note_save_kp —— 第 7 个位置参就是 `extra`，
    # 原来的 `True, extra={...}` 会 "got multiple values for argument 'extra'"。
    p = study.save_note(
        conn, title, body, ["课件答疑"],
        refs, "note",
        {
            "origin": "deck_chat",
            "doc_id": doc_id,
            "source_label": f"{doc_title} · 课件 AI 答疑",
            "question": q[:200],
        },
        True,
    )
    return {
        "path": p,
        "file": Path(p).name,
        "title": title,
        "doc_id": doc_id,
        "source_label": f"{doc_title} · 课件 AI 答疑",
    }


def api_notes_by(kp_id, doc_id):
    """按来源反查笔记：知识点窗口的「查看笔记」与笔记页签的过滤都用它。

    与 `/api/notes` 的区别是**带正文**：知识点小窗里点开一条笔记不该再跳一次
    网络（那里是全页唯一的小窗，弹出去看原文会把小窗挤掉），一次拿全更连贯。
    课件问答的笔记可能很长，所以正文按 `MAX_NOTE_INLINE` 截断并标 `truncated`
    ——要看全文去「笔记」页签（那里读的是完整原文）。

    卡 C-0082：另带一份 `body`（剥掉 front-matter / 自动标题 /「## 出处」的
    正文，小窗显示与进入编辑态都用它），同样按 `MAX_NOTE_INLINE` 截断并标
    `body_truncated`。⚠ 这一份**不能拿去回写**——它可能被截断，编辑必须先走
    `GET /api/note` 取全文（见前端 `mmNoteEditOpen`）。
    """
    notes = study.notes_for(kp_id=kp_id, doc_id=doc_id)
    out = []
    for n in notes:
        try:
            text = Path(n["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        body = study.note_body(text)
        out.append({
            **n,
            "content": text[:MAX_NOTE_INLINE],
            "truncated": len(text) > MAX_NOTE_INLINE,
            "body": body[:MAX_NOTE_INLINE],
            "body_truncated": len(body) > MAX_NOTE_INLINE,
        })
    return {"notes": out, "kp_id": kp_id, "doc_id": doc_id}


def _scope_only(ev, doc_id):
    """只留 doc_id 等于这份课件的证据（卡 C-0052 的硬约束）。

    ⚠ **卡 C-0057 起本函数已无调用方**：人拍板课件答疑改走独立管线
    （不检索、不用提示词、思考强度最高），那条路上根本没有"证据"这个东西。
    保留它不删，是因为 `tools/verify_c0052_quick.py` 还在直接 import 它做
    范围过滤的自检；知识库问答链路 `/api/chat/stream` 与它无关。

    它当年的用途（供读代码的人理解为什么曾有这一层）：
    `core/chat.py::gather_evidence` 在"一条都没召回"时会回退成全库检索
    （`if not picked:` 那一支），那样回答就会引到别的资料——"问这份课件、
    答的却是另一本书"比答不上来坏得多。所以范围外的证据一律丢掉。
    """
    want = int(doc_id)
    return [e for e in ev if int(e.doc_id) == want]


def api_deck_chat_stream(handler, payload):
    """课件级 AI 答疑（卡 C-0057：**独立管线**，不再复用知识库问答那条路）。

    人拍板（2026-09-29 原话）：「每份课件的AI答疑不要和整个知识库的AI问答
    混用，开一条新的管线用于每份课件的答疑。这条管线不用提示词，思考强度开到
    最高。」照此，本函数里：

      · **没有检索**——不调 `gather_evidence` / `search`，也没有相关度打分或
        范围过滤。整份课件的内容按页序直接进上下文
        （`core/chat.py::build_deck_context`），装不下才保序等距抽样并如实报；
      · **没有提示词**——不调 `build_messages`、不拼 `SYSTEM_PROMPT`、
        不读 `logs/knowledge/` 下的提示词文件，messages 里**没有 system 角色**；
      · **思考强度最高**——`thinking=True` + `reasoning_effort="max"`，
        连同模型与密钥都由 `config.DECK_CHAT_*` 单独配（`chat.deck_cfg()`）。

    与知识库问答（`api_chat_stream`，逐字未动）是两条互不影响的链路。

    SSE 事件：`scope` / `delta` / `done` / `error`。
    与 C-0052 相比**不再发 `sources`**：这条管线没有"检索到的片段"可言，
    硬造一批出来只会骗人。`scope` 改成本次上下文的实测自述——模型 /
    思考强度 / 送出多少 token / 预算多少 / 是否截断，供界面与验证核对。
    """
    from core import chat as chat_mod

    config.activate('deck')

    doc_id = int(payload.get('doc_id') or 0)
    query = (payload.get('query') or '').strip()
    history = payload.get('history') or []

    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Accel-Buffering', 'no')
    handler.end_headers()

    def emit(obj):
        # C-0110 回填：真值 = _work/rebuilt/dashboard/0135-api_deck_chat_stream.emit.py
        # （funcs.txt 同条 args=1 vars: obj 逐字对上；与兄弟链路 api_chat_stream.emit 的
        #  真值 0125 同构。重建版把它写成 def emit(event, data) + 两参数调用，与字节码
        #  args=1 及 _sse(obj) 的单参数签名都冲突 → 一并按真值对齐成单参数。）
        # 同卡并发会话另把 _sse 的 json.dumps 位置参数改回关键字形式（见 _sse 处注释），
        # 端到端 SSE 已实测可发（POST /api/deck-chat/stream 收到 data: 帧）。
        handler.wfile.write(_sse(obj))
        handler.wfile.flush()

    conn = None
    try:
        if not doc_id:
            emit({'type': 'error', 'message': '缺少 doc_id：课件答疑必须指定一份课件'})
            return
        if not query:
            emit({'type': 'error', 'message': '问题为空'})
            return
        cfg = chat_mod.deck_cfg()
        with _lock:
            conn = _conn(readonly=True)
            ctx = chat_mod.build_deck_context(conn, doc_id, cfg=cfg)

        # C-0110：事件名就是前端分发用的 obj.type（真值前端 dashboard.html.txt 的
        # deckChatSend 判的是 obj.type === 'scope'/'delta'/'done'/'error'；重建版把事件名
        # 丢进了第一个位置参数、dict 里写成语义类别 pipeline/text/usage/message →
        # 单参 emit 之后前端 4 个分支**一个都不命中**）。语义等价、只对齐键值。
        emit({'type': 'scope', 'pipeline': 'deck_direct', **ctx.to_dict()})
        if not ctx.chunks_used:
            emit({'type': 'delta',
                  'text': f'《{ctx.doc_title}》这份课件在库里还没有正文块（可能还没入库，或解析还没跑完）。课件答疑不检索、只读这一份课件的全文，所以没有内容就没法回答。'})
            emit({'type': 'done', 'usage': {}, 'elapsed': 0.0})
            return

        t0 = time.perf_counter()
        for kind, data in chat_mod.stream_deck_chat(ctx, query, history, cfg=cfg):
            if kind == 'delta':
                emit({'type': 'delta', 'text': data})
            elif kind == 'usage':
                emit({'type': 'done', 'usage': data,
                      'elapsed': time.perf_counter() - t0})
    except Exception as e:
        try:
            emit({'type': 'error', 'message': f'{type(e).__name__}: {e}'})
        except Exception:
            pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def api_review(conn, kp_id, grade, question, answer):
    """复习评分（前端知识点窗口的 0..5 分按钮）：写 `reviews` 证据链 + 更新 SM-2。

    [C-0107 修] 重建版写的是
    `study.schedule(conn, kp_id, grade, question, answer, question=question, answer=answer)`
    —— 两处错：
      ① 第 4/5 个**位置参**已经是 question/answer，又重复传 `question=`/`answer=`
         关键字（与 F1 同一个 "got multiple values for argument" 错法）；
      ② 重建后的 `core/study.py::schedule()` 只有 `(conn, kp_id, grade)`，
         写 `reviews` 表的职责拆到了 `record_review()`。
    真值里这本是一个函数（`study.funcs.txt::L98`：
    `schedule args=7 vars: conn, kp_id, grade, question, answer, user_note, correct`），
    所以这里改走 `record_review()`（它内部调 `schedule()` 并把 question/answer
    落进 `reviews`）。
    返回体补上前端 toast 读的三个键（`name` / `due_in_days` / `mastery`，
    见 dashboard 前端 JS 的 `${r.name}：${r.due_in_days} 天后复习（掌握度 ${pct(r.mastery)}%）`）。
    """
    row = conn.execute("SELECT * FROM knowledge_points WHERE id=?",
                       (int(kp_id),)).fetchone()
    if row is None:
        return {"error": "知识点不存在: %d" % int(kp_id)}
    st = study.record_review(conn, int(kp_id), int(grade),
                             question=question or "", answer=answer or "")
    due = float(st.get("due_at") or 0.0)
    now_row = conn.execute("SELECT * FROM knowledge_points WHERE id=?",
                           (int(kp_id),)).fetchone()
    return {
        **st,
        "name": row["name"],
        "mastery": study.compute_mastery(now_row) if now_row else 0.0,
        "due_in_days": max(0, int(round((due - db.now()) / study.DAY))) if due else 0,
    }


def api_deck_calendar(conn, ym):
    """某月的课件复习日程（月历数据源）。只读，走只读连接。"""
    return study.deck_calendar(conn, ym)


def api_deck_enroll(conn, doc_id):
    """把一份课件录入遗忘曲线（**幂等**：重复点击返回 already=True，不重建计划）。

    `subject` 只作展示快照——跨学科隔离靠"每个学科一个 study.db"，不靠这个字段。
    """
    try:
        subject = subjects_mod.current()
    except Exception:
        subject = ""
    return study.enroll_deck(conn, doc_id, subject=subject)


def api_deck_mark(conn, plan_id, round_, state):
    """标记某一轮的状态（默认 done），返回更新后的计划与"下次复习日"。"""
    if plan_id <= 0 or round_ <= 0:
        return {"ok": False, "error": '缺少 plan_id / round'}
    return study.deck_mark(conn, plan_id, round_, state)


def _subject_running() -> str:
    """有长任务在跑时**不许切库**；返回正在跑的任务名（空串=空闲）。

    入库/整理都是几十分钟级的长任务，启动时就已经按**当时那个库**定好了
    路径与连接。跑到一半换库，轻则产物落到别的学科，重则两个学科的数据
    互相污染——这比"切不了"严重得多，所以这里宁可拒绝。
    """
    try:
        if progress_mod.snapshot().get("active"):
            return "入库"
    except Exception:
        pass

    try:
        if pool_mod.is_running():
            return "批量入库"
    except Exception:
        pass

    try:
        if kp_build_mod.is_running():
            return "知识点整理"
    except Exception:
        pass

    try:
        if kp_batch_mod.is_running():
            return "批量整理"
    except Exception:
        return ""

    return ""


def _reset_subject_caches():
    """切库后清掉**进程内**缓存，并返回各项清了多少条（写进响应，便于核对）。

    清单与理由（都是"上一個库的内容"）：
    - `_KNOWLEDGE_CACHE`：知识点 JSON 目录扫描结果。它的 key 里带目录签名，
      理论上换了目录自然 miss，但**旧条目会一直占着内存**，且同名人肉核对时
      容易看着旧数据以为是新库的，所以直接清。
    - `_LKP_CACHE`：末级知识点总结目录扫描结果，同上。
    - `_SLIDE_CACHE`：课件页图 `{页号: 文件名}` 目录扫描结果，key 是 deck 目录，
      换库后目录也换了；同样清掉释放内存。
    - `_KP_OPT_CACHE`：`/api/kp-build/options` 的 20 秒 TTL 缓存。它缓存的是
      **可整理课件清单 + 预估条数/花费**，是纯当前库数据，**必须**清——
      留着它，切库后第一次点「一键整理」会看到上一个库的清单。
    - `_code_mtime`/`_CODE_STAMP`（代码指纹）与库无关，**不清**。
    - `core.embed._engine`（嵌入模型单例）**绝不清**：跨学科共享模型是
      设计目标，切库就该零加载开销。
    """
    n = {"knowledge": 0, "lkp": 0, "slide": 0, "kp_opt": 0}
    with _KNOWLEDGE_LOCK:
        n["knowledge"] = len(_KNOWLEDGE_CACHE)
        _KNOWLEDGE_CACHE.clear()
    with _LKP_LOCK:
        n["lkp"] = len(_LKP_CACHE)
        _LKP_CACHE.clear()
    with _SLIDE_LOCK:
        n["slide"] = len(_SLIDE_CACHE)
        _SLIDE_CACHE.clear()
    if _KP_OPT_CACHE.get("data") is not None:
        n["kp_opt"] = 1
    _KP_OPT_CACHE["sig"] = None
    _KP_OPT_CACHE["t"] = 0.0
    _KP_OPT_CACHE["data"] = None
    return n


def _share_prog_reset():
    # [C-0108 实测修正] 真值字节码（`_work/built/__pycache__/dashboard.cpython-313.pyc`
    # 的 `_share_prog_reset`：`_SHARE_PROG; update; BUILD_TUPLE 7; BUILD_MAP 0;
    # DICT_MERGE 1; CALL_FUNCTION_EX`）与重建版**完全一致**，而同一份真值里
    # `_SHARE_PROG` 只在模块级 L153 被赋值过一次、值是 `dict`（`BUILD_CONST_KEY_MAP 7`
    # 七个键 active/stage/done/total/error/started/finished）⇒ `dict.update()` 只接受
    # 一个位置参数，这行必然抛 `TypeError: update expected at most 1 argument, got 7`。
    # 实测后果：`api_share_export` 的第一行就是它，于是导出/导入 100% 失败
    # （`grep` 全部调用点确认；C-0080 的浏览器验收只覆盖了界面与「导出失败」提示路径）。
    # 即：**这是真值自带的缺陷，不是重建漂移**。卡 C-0108 的验收要求分享包路径真跑通，
    # 故按 `_SHARE_PROG` 的真值类型（dict）把调用改成 dict 语义，并保留真值的七个键
    # 与取 `<module>` 真值常量的 `'准备中'`。
    _SHARE_PROG.update({
        'active': True,
        'stage': '准备中',
        'done': 0,
        'total': 0,
        'error': '',
        'started': time.time(),
        'finished': 0.0,
    })


def _share_prog(stage, done, total):
    """交给 `core/share` 当 progress 回调。

    **绝不能抛异常**：这个回调是在写 zip / 写库的主循环里被调的，它一炸，
    整次导出（或导入）就跟着崩——进度条的价值远小于交付本身。
    """
    try:
        # [C-0108] 同 `_share_prog_reset`：`_SHARE_PROG` 是真值里的 dict，
        # dict.update 只吃一个位置参数；这两行原来传了「位置参 + 第二个位置参」，
        # 每次都抛 TypeError 并被下面的 except 吞掉 ⇒ 进度条永远停在「准备中」。
        _SHARE_PROG.update({
            "stage": _SHARE_STAGE_CN.get(stage, stage),
            "done": int(done),
            "total": int(total),
        })
    except Exception:
        return None


def _share_prog_done(err=""):
    # [C-0108] 同上：dict.update 只吃一个位置参数（原来传了 False/err 两个位置参 +
    # time=/active= 两个关键字），改回 dict 语义。
    _SHARE_PROG.update({
        "active": "error" if err else "finished",
        "error": err if err else "",
        "finished": time.time(),
    })


def api_share_progress():
    """进度快照：只读一个进程内字典，不碰数据库、不进任何锁。"""
    d = dict(_SHARE_PROG)
    if d.get('started'):
        d['elapsed'] = round(
            max(0.0, (d.get('finished') or time.time()) - d['started']),
            1)
    return d


def _share_target(slug):
    """把「目标学科」解析成 `core/share` 要的那几条路径。

    `slug` 为空（或就是当前学科）→ 用 config 现值，与顶栏显示的库一致；
    否则走 `subjects.paths_of`——它按目录**现算**，既不切库、也不改任何全局
    状态，所以「导入到别的学科」不会把用户正在用的那个库换掉。
    """
    root = Path(config.ROOT)
    if not slug or slug == subjects_mod.current():
        cur = subjects_mod.current_paths()
        return {
            "slug": subjects_mod.current(),
            "root": root,
            "db_path": cur["DB_PATH"],
            "log_dir": cur["LOG_DIR"],
            "raw_dir": cur["RAW_DIR"],
        }
    p = subjects_mod.paths_of(slug)
    return {
        "slug": slug,
        "root": root,
        "db_path": p["DB_PATH"],
        "log_dir": p["LOG_DIR"],
        "raw_dir": p["RAW_DIR"],
    }


def _share_source_file(tgt, doc):
    """课件在本机的源文件（回退规则与 `share.export_doc` 逐字一致）。"""
    src = Path(str(doc.get("abs_path") or ""))
    if src.is_file():
        return src
    alt = Path(tgt["root"]) / str(doc.get("path") or "")
    if alt.is_file():
        return alt
    return None


def _share_page_files(tgt, doc):
    src = _share_source_file(tgt, doc)
    stem = src.name if src is not None else Path(str(doc.get("path") or "")).name
    pdir = Path(tgt["log_dir"]) / "figure_desc" / share_mod.safe_dirname(Path(stem).stem)
    if not pdir.is_dir():
        return []
    return sorted(pdir.glob("slide_*.png"))


def _share_zip_entries(zip_path):
    """包里每个条目的字节数（只读 zip 中央目录，不解压、不吃内存）。"""
    out = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for i in zf.infolist():
                out.append({
                    "name": i.filename,
                    "bytes": int(i.compress_size),
                    "raw_bytes": int(i.file_size),
                })
    except (OSError, zipfile.BadZipFile):
        return []
    return sorted(out, key=lambda x: x["name"])


def api_share_packages():
    r"""列出 `share\` 里已有的包——「选一个包」比手打路径可靠。"""
    d = Path(config.ROOT) / share_mod.SHARE_DIRNAME
    out = []
    if d.is_dir():
        for f in sorted(d.glob('*.zip')):
            try:
                st = f.stat()
            except OSError:
                continue
            out.append({
                "name": f.name,
                "path": str(f),
                "bytes": st.st_size,
                "mtime": st.st_mtime,
            })
    return {"ok": True, "dir": str(d), "packages": out}


def api_share_reveal(payload):
    """在本机文件管理器里打开包所在目录（卡面要的"一键打开所在文件夹"）。

    看板是**本机**服务，所以这一步就是本机行为，不涉及上传/下载。
    打不开时如实返回原因并带上路径，让人自己复制——不假装成功。
    """
    p = Path(str(payload.get("path") or ""))
    if not p.exists():
        return {"error": "路径不存在：%s" % p}
    d = p if p.is_dir() else p.parent
    opener = getattr(os, "startfile", None)
    if opener is None:
        return {"error": "本系统没有 os.startfile，请手动打开：%s" % d}
    try:
        opener(str(d))
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}（可手动打开：{d}）"}
    return {"ok": True, "dir": str(d)}


def api_share_estimate(payload):
    """导出前的体积预估（**只读**，一个字节都不写）。

    为什么需要：勾上「带原课件」「带识图页图」能让包从 1 MB 涨到几十 MB，
    用户在按下导出之前就该看到代价。口径是**未压缩字节**，再按 deflate 的
    经验压缩比给一个区间——**不假装精确**：文本 jsonl 能压到两三成，
    png 与原课件基本压不动，所以区间给得宽，并如实标注是预估。
    """
    doc_id = int(payload.get('doc_id') or 0)
    try:
        tgt = _share_target('')
    except subjects_mod.SubjectError as e:
        return {'error': str(e)}

    if not Path(tgt['db_path']).is_file():
        return {'error': '当前库还没有建好（%s 不存在）' % tgt['db_path']}
    conn = db.connect(tgt['db_path'])
    try:
        doc = conn.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        if doc is None:
            return {'error': '库里没有 id=%d 的课件（可能已被移除）' % doc_id}
        row = dict(doc)
        n_chunk = conn.execute(
            'SELECT COUNT(*) n, COALESCE(SUM(LENGTH(CAST(text AS BLOB))),0) b FROM chunks WHERE doc_id=?',
            (doc_id,)).fetchone()
        n_emb = conn.execute(
            'SELECT COUNT(*) n, COALESCE(SUM(LENGTH(e.vec)),0) b FROM chunks c JOIN embeddings e ON e.chunk_id=c.id WHERE c.doc_id=?',
            (doc_id,)).fetchone()
        n_fig = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(LENGTH(COALESCE(description,''))+LENGTH(COALESCE(thinking,''))),0) b FROM figure_desc WHERE doc_id=?",
            (doc_id,)).fetchone()
    finally:
        conn.close()

    know = []
    for suffix in share_mod.KNOW_SUFFIXES:
        kf = Path(tgt['log_dir']) / 'knowledge' / ('%d%s' % (doc_id, suffix))
        if not kf.is_file():
            continue
        know.append({'name': kf.name, 'bytes': kf.stat().st_size})

    pages = _share_page_files(tgt, row)
    src = _share_source_file(tgt, row)
    orig_b = src.stat().st_size if src is not None else 0
    pages_b = sum(p.stat().st_size for p in pages)

    emb_b64 = (int(n_emb['b']) + 2) // 3 * 4
    parts = {
        'chunks': int(n_chunk['b']) + emb_b64,
        'embeddings': int(n_fig['b']) + sum(k['bytes'] for k in know),
        'figure_desc': orig_b,
        'knowledge': pages_b,
        'original': orig_b,
        'pages': pages_b,
    }
    comp = (parts['chunks'] + parts['embeddings'] + parts['figure_desc']
            + parts['knowledge'] + 20480)

    def est(extra: int) -> list[int]:
        return [comp + extra, int((comp + extra) * 0.25)]

    return {
        'ok': True,
        'doc_id': doc_id,
        'title': row.get('title') or row.get('path'),
        'kind': row.get('kind'),
        'n_chunks': int(n_chunk['n']),
        'n_embeddings': int(n_emb['n']),
        'n_figures': int(n_fig['n']),
        'n_pages': len(pages),
        'has_original': src is not None,
        'original_name': src.name if src is not None else '',
        'parts': parts,
        'knowledge_files': know,
        'raw_base': comp,
        'raw_with_original': comp + orig_b,
        'raw_with_pages': comp + pages_b,
        'raw_with_both': comp + orig_b + pages_b,
        'est_base': est(0),
        'est_with_original': est(orig_b),
        'est_with_pages': est(pages_b),
        'est_with_both': est(orig_b + pages_b),
        'note': '预估口径：未压缩字节 × deflate 经验压缩比（文本约两三成，原文件与页图几乎压不动），所以给的是区间',
    }


def api_share_export(payload):
    doc_id = int(payload.get("doc_id") or 0)
    with_original = bool(payload.get("with_original"))
    with_pages = bool(payload.get("with_pages"))
    note = str(payload.get("note") or "")[:200]
    try:
        tgt = _share_target("")
    except subjects_mod.SubjectError as e:
        return {"error": str(e)}

    _share_prog_reset()
    t0 = time.time()
    try:
        with _SHARE_LOCK:
            res = share_mod.export_doc(
                doc_id, None,
                db_path=tgt["db_path"], log_dir=tgt["log_dir"],
                raw_dir=tgt["raw_dir"], root=tgt["root"],
                with_original=with_original, with_pages=with_pages,
                exported_by=str(payload.get("by") or ""), note=note,
                progress=_share_prog,
            )
    except share_mod.ShareError as e:
        _share_prog_done(str(e))
        return {"error": str(e), "kind": "share", "elapsed": round(time.time() - t0, 1)}
    except Exception as e:
        msg = f"导出失败：{type(e).__name__}: {e}"
        _share_prog_done(msg)
        return {msg: round(time.time() - t0, 1)}  # TODO(反编译存疑)
    _share_prog_done()
    z = Path(res["zip"])
    return {
        "ok": True,
        "zip": res["zip"],
        "dir": str(z.parent),
        "name": z.name,
        "bytes": int(res["bytes"]),
        "counts": res["counts"],
        "includes": res["includes"],
        "embedding": res["embedding"],
        "n_pages": int(res["n_pages"]),
        "original_bytes": int(res["original_bytes"]),
        "pages_bytes": int(res["pages_bytes"]),
        "warnings": res["warnings"],
        "elapsed": round(time.time() - t0, 1),
        "cmd": 'python tools\\share.py import --zip "%s"' % res["zip"],
    }


def api_share_inspect(payload):
    """看包信息：包里有什么、能不能导入这个库、有没有冲突。**全程只读。**

    这里刻意**不调用** `import_package(dry_run=True)`：那条路会先 `db.init_db()`
    写一下目标库（幂等，但仍是写）。看包是「只看不动」的动作，所以模型一致性
    与冲突都由本函数**只读地**判：前者比 `local_embedding_identity`，后者查
    `documents.content_hash`——与 `import_package` 真正那道闸同一判据。
    结构完整性交给 `inspect_package(deep=True)`（逐条 sha256 核对）。
    """
    zip_arg = str(payload.get("zip") or "").strip().strip('"').strip("'")
    if not zip_arg:
        return {"error": "请先填写分享包路径"}
    zp = Path(zip_arg).expanduser()
    if not zp.is_file():
        return {
            "error": "找不到分享包：%s（看板在本机运行，填本机完整路径即可）" % zp,
            "kind": "package",
        }
    try:
        info = share_mod.inspect_package(zp, deep=True)
    except share_mod.ShareError as e:
        return {"error": str(e), "kind": "package"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "kind": "package"}

    try:
        tgt = _share_target(str(payload.get("subject") or ""))
    except subjects_mod.SubjectError as e:
        return {"error": str(e), "kind": "subject"}

    man = info.get("manifest") or {}
    src = man.get("source") or {}
    emb = man.get("embedding") or {}
    counts = man.get("counts") or {}
    doc = info.get("doc") or {}
    entries = _share_zip_entries(zp)
    dbp = Path(tgt["db_path"])
    out = {
        "ok": True,
        "zip": str(zp),
        "bytes": int(info["bytes"]),
        "format": man.get("format"),
        "format_version": man.get("format_version"),
        "created_at": man.get("created_at"),
        "exported_by": man.get("exported_by") or "",
        "note": man.get("note") or "",
        "source": {
            "subject": src.get("subject"),
            "doc_id": src.get("doc_id"),
            "title": src.get("title"),
            "kind": src.get("kind"),
            "path": src.get("path"),
        },
        "title": doc.get("title") or src.get("title"),
        "content_hash": doc.get("content_hash"),
        "file_name": doc.get("file_name"),
        "embedding": {
            "model": emb.get("model"),
            "dim": emb.get("dim"),
        },
        "counts": counts,
        "chunks_n": int((info.get("counts") or {}).get("total") or 0),
        "embeddings_n": (info.get("embeddings") or {}).get("total"),
        "knowledge": info.get("knowledge") or [],
        "includes": info.get("includes") or {},
        "n_entries": info.get("n_entries"),
        "entries": entries[:12],
        "entry_total": len(entries),
        "target": {
            "slug": tgt["slug"],
            "db": str(dbp),
            "db_exists": dbp.is_file(),
            "raw": str(tgt["raw_dir"]),
        },
        "warnings": info.get("warnings") or [],
        "importable": True,
        "reason": "",
        "reason_kind": "",
        "conflict": None,
        "local_embedding": None,
        "est_seconds": max(2, int(round(int(counts.get("chunks") or 0) / 300.0)) + 1),
    }

    if not dbp.is_file():
        out["db_note"] = "目标库还没建好，第一次导入会自动建库"
        return out

    conn = db.connect(dbp)
    try:
        lm, ld = share_mod.local_embedding_identity(conn)
        out["local_embedding"] = {"model": lm, "dim": ld}
        try:
            pm, pd = str(emb.get("model") or ""), int(emb.get("dim") or 0)
        except (TypeError, ValueError):
            pm, pd = "", 0
        if (pm, pd) != (lm, ld):
            out["importable"] = False
            out["reason_kind"] = "model"
            out["reason"] = (
                "模型不一致：包里的向量用的是 %s（dim=%d），你当前库用的是 %s（dim=%d），导入会被拒绝——**不会**自动重算（重算要调用嵌入模型、要花钱）。\n两条出路：① 让导出方用 %s（dim=%d）重新导出；② 让导出方勾选「包含原课件文件」重导一次，你在本机重新入库（用你自己的本地嵌入模型，不产生接口费用）。"
                % (pm, pd, lm, ld, lm, ld)
            )
        ch = doc.get("content_hash")
        if ch:
            hit = conn.execute(
                "SELECT id,title,path FROM documents WHERE content_hash=? ORDER BY id LIMIT 1",
                (ch,),
            ).fetchone()
            if hit is not None:
                out["conflict"] = {
                    "doc_id": int(hit["id"]),
                    "title": hit["title"],
                    "path": hit["path"],
                }
    finally:
        conn.close()
    return out


def api_share_import(payload):
    """把一个分享包导进目标学科库。**零模型调用、零费用**（C-0079 保证）。

    冲突三选一（卡面要求）：skip（默认，不动本库已有的那份）/ duplicate
    （再导一份副本，同 content_hash 会出现两行，用于对比来源）/ overwrite
    （**先全量校验新包，通过了才删掉本库那一份**，然后导入）。
    覆盖的这个顺序不能反：反了就是一个坏包先把用户手里的资料删掉再报错。
    """
    zip_arg = str(payload.get("zip") or "").strip().strip('"').strip("'")
    if not zip_arg:
        return {"error": "请先填写分享包路径"}
    zp = Path(zip_arg).expanduser()
    if not zp.is_file():
        return {"error": "找不到分享包：%s" % zp, "kind": "package"}
    on_conflict = str(payload.get("on_conflict") or "skip")
    if on_conflict not in ("skip", "duplicate", "overwrite"):
        return {"error": "未知的冲突处理方式：%s" % on_conflict}
    try:
        tgt = _share_target(str(payload.get("subject") or ""))
    except subjects_mod.SubjectError as e:
        return {"error": str(e), "kind": "subject"}
    _share_prog_reset()
    t0 = time.time()
    removed = None
    try:
        with _INGEST_LOCK:
            if on_conflict == "overwrite" and Path(tgt["db_path"]).is_file():
                # 先全量校验新包（dry_run=True），通过了才删本库那一份
                dry = share_mod.import_package(
                    str(zp),
                    db_path=tgt["db_path"], log_dir=tgt["log_dir"],
                    raw_dir=tgt["raw_dir"], root=tgt["root"],
                    dry_run=True,
                )
                hit = dry.get("conflict")
                if hit:
                    conn = db.connect(tgt["db_path"])
                    try:
                        ch = conn.execute(
                            "SELECT content_hash FROM documents WHERE id=?",
                            (int(hit["doc_id"]),),
                        ).fetchone()["content_hash"]
                        ids = [
                            int(r["id"])
                            for r in conn.execute(
                                "SELECT id FROM documents WHERE content_hash=? ORDER BY id",
                                (ch,),
                            )
                        ]
                        removed = {"deleted_doc_ids": [], "chunks_removed": 0}
                        for did in ids:
                            # C-0107 修：真值里这条调用是带三个关键字的
                            # （rebuilt/dashboard/0101-api_delete_docs.py：
                            # `api_delete_doc(conn, did, delete_file=…,
                            # purge_orphans=…, dry_run=False)`），重建版漏成了
                            # `api_delete_doc(conn, did)` ⇒ TypeError。
                            # 这里是分享导入的"同内容文档去重"：只移除本机索引
                            # （原文件来自包、不在本机），孤儿清理由外层统一负责。
                            one = api_delete_doc(conn, did, delete_file=False,
                                                 purge_orphans=False, dry_run=False)
                            removed["deleted_doc_ids"].append(int(one["deleted_doc_id"]))
                            removed["chunks_removed"] += int(one["chunks_removed"])
                    finally:
                        conn.close()
                    _share_prog("remove", 1, 1)
            res = share_mod.import_package(
                str(zp),
                db_path=tgt["db_path"], log_dir=tgt["log_dir"],
                raw_dir=tgt["raw_dir"], root=tgt["root"],
                duplicate=(on_conflict == "duplicate"),
                progress=_share_prog,
            )
    except share_mod.ShareError as e:
        _share_prog_done(str(e))
        return {
            "error": str(e),
            "kind": "share",
            "overwritten": removed,
            "elapsed": round(time.time() - t0, 1),
        }
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        _share_prog_done(msg)
        return {
            "error": "导入失败：" + msg,
            "overwritten": removed,
            "elapsed": round(time.time() - t0, 1),
        }
    _share_prog_done()
    took = round(time.time() - t0, 1)
    out = {
        "ok": True,
        "status": res.get("status"),
        "elapsed": took,
        "zip": res.get("zip"),
        "package_sha256": res.get("package_sha256"),
        "target": {"slug": tgt["slug"], "db": str(tgt["db_path"])},
        "overwritten": removed,
        "warnings": res.get("warnings") or [],
        "model_calls": int(res.get("model_calls") or 0),
        "cost_cny": float(res.get("cost_cny") or 0.0),
    }
    if res.get("status") == "skipped":
        out.update({
            "doc_id": res.get("doc_id"),
            "title": res.get("existing_title"),
            "reason": res.get("reason"),
            "hint": res.get("hint"),
        })
        return out
    out.update({
        "doc_id": res.get("doc_id"),
        "title": res.get("title"),
        "path": res.get("path"),
        "file": res.get("file"),
        "counts": res.get("counts"),
        "knowledge": res.get("knowledge"),
        "remap": res.get("remap"),
        "fts": res.get("fts"),
        "source": res.get("source"),
    })
    return out


def api_subjects():
    """学科清单 + 当前学科 + **当前真实路径**（db/raw/notes/logs）。

    路径是原样吐出去的：人要能一眼核对"我现在到底在哪个库"，这也正是卡里
    「路径隔离可证」那一条要的证据，而不是让人去猜。
    """
    d = subjects_mod.info()
    d["busy"] = _subject_running()
    d["ok"] = True
    return d


def api_subjects_create(payload):
    """新建学科：建目录 + 建**空库**（完整 schema），随后**直接切过去**。

    为什么直接切：用户点「新建」的意图就是马上用它；且新库必然是空的，
    不存在"以为还在旧库"的误操作风险（前端会把新学科名显示在顶栏）。
    """
    busy = _subject_running()
    if busy:
        return {"error": "有任务正在跑（%s），跑完再新建学科——中途换库会让产物落到别的学科" % busy, "_code": 409}
    entry = subjects_mod.create(payload.get("name", ""))
    cleared = _reset_subject_caches()
    subjects_mod.switch(entry["slug"])
    d = subjects_mod.info()
    d["ok"] = True
    d["created"] = entry
    d["cleared"] = cleared
    return d


def api_subjects_switch(payload):
    busy = _subject_running()
    if busy:
        return {
            "error": "有任务正在跑（%s），跑完再切学科——中途换库会让产物落到别的学科" % busy,
            "_code": 409,
        }
    slug = payload.get("slug", "")
    try:
        entry = subjects_mod.switch(slug)
    except subjects_mod.SubjectError as e:
        return {"error": str(e), "_code": 400}
    cleared = _reset_subject_caches()
    d = subjects_mod.info()
    d["ok"] = True
    d["switched"] = entry
    d["cleared"] = cleared
    return d


def api_subjects_rename(payload):
    """重命名学科库（卡 C-0085）：只改**显示名**，slug 目录与 5 条路径一个字都不动。

    入参：`{"slug": "药物化学", "name": "药物化学（2026）"}`

    为什么敢于只改 name（实测过的解耦点）：
      · slug 是 `create()` 时按当时的名字算一次的（`subjects.slugify`），此后与 name
        **解耦**；`list_subjects()` 扫 `subjects/<slug>/` 目录 + 读各自 `subject.json`
        的 `name`，`paths_of(slug)` 按 slug 现算 ⇒ 目录名与显示名不一致**不影响任何
        功能**；
      · 反过来，搬目录要连带改 `subject.json.layout` 的 5 条绝对路径与注册表 slug，
        漏一处就留下误导性记录（卡面风险 ⑤ 明令建议不搬）。

    默认学科（slug=default）也能改名：它没有 `subject.json`，名字落在注册表
    `logs/subjects.json` 的 `default_name` 一格（见 `core/subjects.py::rename`）。
    改名后清一次进程内缓存并回吐**新的**学科快照，前端拿它重画顶栏。
    """
    if not isinstance(payload, dict):
        return {"error": "入参必须是对象（如 {\"slug\":\"药物化学\",\"name\":\"新名字\"}）", "_code": 400}
    busy = _subject_running()
    if busy:
        return {"error": "有任务正在跑（%s），跑完再重命名学科——改名后前端要重扫学科列表与当前库" % busy, "_code": 409}
    entry = subjects_mod.rename(str(payload.get("slug") or "").strip(), payload.get("name", ""))
    cleared = _reset_subject_caches()
    d = subjects_mod.info()
    d["ok"] = True
    d["renamed"] = entry.get("renamed")
    d["entry"] = entry
    d["busy"] = ""
    d["cleared"] = cleared
    return d


class Handler(BaseHTTPRequestHandler):
    server_version = 'study_agent-dashboard'

    def log_message(self, fmt, *args):  # 静音默认访问日志
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(),
                   "application/json; charset=utf-8")

    def _json_auto(self, obj) -> None:
        """发响应，并认 `_code` 这个约定字段（如切库撞上长任务时的 409）。

        `_code` 只用于选 HTTP 状态码，剥掉后不进 JSON——不然前端会看到一个
        莫名其妙的 `_code` 字段。
        """
        code = 200
        if isinstance(obj, dict) and isinstance(obj.get("_code"), int):
            code = obj.pop("_code")
        self._json(obj, code)

    def _slide(self, doc_id, page) -> None:
        """一张课件页图（卡 C-0036）。

        只放行 `logs/figure_desc/<deck>/slide_<页号>.png`：路径由后端按
        `documents.path` 推出来，`page` 只当**查表用的页号**，绝不拼进文件名。
        找不到图给 404 + JSON 说明（不是 500）——"这页没缓存图"是正常状态。
        """
        try:
            p = _slide_png_path(int(doc_id), int(page))
        except (TypeError, ValueError):
            return self._json({"error": "doc_id/page 必须是整数"}, 400)
        if p is None:
            return self._json({"error": "该页没有课件图缓存"}, 404)
        try:
            body = p.read_bytes()
        except OSError as e:
            return self._json({"error": f"读图失败：{e}"}, 500)
        self._send(200, body, "image/png")

    def _teach_page(self, doc_id, page, dpi) -> None:
        """教材某页 PNG（task-27）：只放行 role=textbook 的 PDF；
        失败一律 JSON 4xx/5xx（不是 500 空响应），与 /img/slide 同风格。"""
        png, info = api_teach_page(doc_id, page, dpi)
        if png is None:
            return self._json({"error": info.get("error") or "教材页图不可用"},
                              int(info.get("code") or 500))
        self._send(200, png, "image/png")

    def do_GET(self):
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path
        try:
            if path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.encode("utf-8"),
                           "text/html; charset=utf-8")
                return None

            # ---------------- 进度 / 分享 ----------------
            if path == "/api/ingest/progress":
                return self._json(progress_mod.snapshot())
            if path == "/api/kp-build/progress":
                return self._json(api_kp_build_progress())
            # 预习进度（task-24）：与 kp-build 同构；必须进锁之前，否则生成中读不到进度
            if path == "/api/preview/progress":
                return self._json(api_preview_progress())
            if path == "/api/textbook-kp/progress":
                return self._json(textbook_kp_mod.progress())
            if path == "/api/textbook-kp/due":
                try:
                    return self._json_auto(textbook_kp_mod.due_chapters())
                except (OSError, sqlite3.Error) as e:
                    return self._json_auto({"ok": False, "error": str(e)})
            if path in ("/api/textbook-kp/estimate", "/api/textbook-kp/doc"):
                doc_id = _int_arg(qs.get("doc_id"), 0)
                chapter = _int_arg(qs.get("chapter"), 0)
                if doc_id <= 0 or chapter <= 0:
                    return self._json_auto({"ok": False, "error": "请选择教材和章节"})
                try:
                    if path.endswith("/estimate"):
                        result = textbook_kp_mod.estimate(doc_id, chapter, force=qs.get("force") == "1")
                    else:
                        result = textbook_kp_mod.read_summary(doc_id, chapter)
                    return self._json_auto(result)
                except (ValueError, OSError, sqlite3.Error) as e:
                    return self._json_auto({"ok": False, "error": str(e)})
            # ---------------- 复习流程（task-33，锁外：纯文件/进程内） ----------------
            if path == "/api/review/selftest":
                return self._json_auto(api_review_selftest(
                    _int_arg(qs.get("doc_id"), 0)))
            if path == "/api/review/selftest/status":
                return self._json_auto(api_review_selftest_status(
                    _int_arg(qs.get("doc_id"), 0)))
            if path == "/api/ingest/batch/progress":
                return self._json(pool_mod.snapshot(qs.get("batch") or None))
            if path == "/api/kp-build/batch/progress":
                return self._json(api_kp_build_batch_progress())
            if path == "/api/share/progress":
                return self._json_auto(api_share_progress())
            if path == "/api/share/packages":
                return self._json_auto(api_share_packages())

            # ---------------- 图片 ----------------
            if path == "/img/slide":
                return self._slide(qs.get("doc_id") or 0, qs.get("page") or 0)

            # ---------------- 教材页图（task-27） ----------------
            # PDF 渲染约 0.1~0.2 秒且 core/teach 自带渲染锁：
            # 不进全局 _lock，避免挡在入库/整理后面。
            if path == "/api/teach-page":
                return self._teach_page(qs.get("doc_id") or 0,
                                        qs.get("page") or 0,
                                        qs.get("dpi") or "")

            # ---------------- 页面知识点 ----------------
            if path == "/api/page-kps":
                return self._json(api_page_kps(
                    _int_arg(qs.get("doc_id"), 0),
                    qs.get("source_ref", ""),
                    _int_arg(qs.get("page"), 0),
                ))

            # ---------------- 设置 ----------------
            if path == "/api/settings":
                return self._json_auto(api_settings())
            if path == "/api/settings/prompt":
                return self._json_auto(api_settings_prompt(
                    unquote(qs.get("name", "")),
                    unquote(qs.get("builtin", "")),
                ))

            with _lock:
                conn = _conn(readonly=True)
                try:
                    if path == "/api/stats":
                        return self._json(api_stats(conn))
                    if path == "/api/docs":
                        return self._json(api_docs(conn))
                    if path == "/api/doc":
                        return self._json(api_doc(conn, _int_arg(qs.get("id"), 0)))
                    if path == "/api/chunk":
                        return self._json(api_chunk(
                            conn,
                            _int_arg(qs.get("id"), 0),
                            _int_arg(qs.get("before"), 1),
                            _int_arg(qs.get("after"), 1),
                        ))
                    if path == "/api/search":
                        return self._json(api_search(
                            conn, qs.get("q", ""),
                            _int_arg(qs.get("k"), 8),
                            qs.get("corpus", "main"),
                        ))
                    if path == "/api/kps":
                        return self._json(api_kps(conn))
                    if path == "/api/kp":
                        return self._json(api_kp_detail(conn, _int_arg(qs.get("id"), 0)))
                    if path == "/api/queue":
                        return self._json(api_queue(
                            conn,
                            _int_arg(qs.get("limit"), 20),
                        ))
                    if path == "/api/deck-calendar":
                        return self._json(api_deck_calendar(conn, qs.get("ym", "")))
                    if path == "/api/mistakes":
                        return self._json(api_mistakes(conn))
                    # ---------------- 预习（task-24） ----------------
                    # 章节/产物状态都来自只读 DB + 产物文件，零模型调用。
                    if path == "/api/preview/options":
                        return self._json(api_preview_options(conn))
                    if path == "/api/preview/chapters":
                        return self._json(api_preview_chapters(
                            conn, _int_arg(qs.get("doc_id"), 0)))
                    if path == "/api/preview/estimate":
                        return self._json(api_preview_estimate(
                            qs.get("doc_id"), qs.get("chapter")))
                    if path == "/api/preview/doc":
                        return self._json(api_preview_doc(
                            qs.get("doc_id"), qs.get("chapter")))
                    # ---------------- 教材补充（task-27） ----------------
                    # 只读产物文件 + 教材章分组，零模型、零写盘。
                    if path == "/api/textbook-supplement/status":
                        return self._json(api_textbook_supplement_status(
                            conn, _int_arg(qs.get("doc_id"), 0)))
                    if path == "/api/textbook-supplement/kp":
                        return self._json(api_textbook_supplement_kp(
                            conn, _int_arg(qs.get("doc_id"), 0),
                            qs.get("kp_id", "")))
                    if path == "/api/textbook-supplement/kp/estimate":
                        return self._json(api_textbook_supplement_kp_estimate(
                            _int_arg(qs.get("doc_id"), 0),
                            qs.get("kp_id", "")))
                    # ---------------- 复习流程（task-33）：课件卡 / 三步数据（只读 DB） ----------------
                    if path == "/api/review/coursewares":
                        return self._json_auto(api_review_coursewares(conn))
                    if path == "/api/review/flow":
                        return self._json_auto(api_review_flow(
                            conn, _int_arg(qs.get("doc_id"), 0)))
                    # ---------------- 名词整理（task-36）：预习名词入口状态（只读） ----------------
                    if path == "/api/terms/preview/options" or path == "/api/terms/preview/status":
                        return self._json_auto(api_terms_preview_options(
                            conn, _int_arg(qs.get("doc_id"), 0)))
                    if path == "/api/mindmap":
                        return self._json(api_mindmap(
                            conn,
                            _int_arg(qs.get("doc_id"), 0),
                        ))
                    if path == "/api/terms":
                        return self._json(api_terms(
                            conn,
                            _int_arg(qs.get("doc_id"), 0),
                        ))
                    if path == "/api/kp-build/options":
                        return self._json(api_kp_build_options(conn))
                    if path == "/api/kp-build/batch/options":
                        return self._json(api_kp_build_batch_options(conn))
                    if path == "/api/mindmap/kp":
                        return self._json(api_mindmap_kp(
                            _int_arg(qs.get("doc_id"), 0), qs.get("kp_id", "")))
                    # 末级知识点**总结**（新方案，卡 C-0032）：点开末级节点时才取。
                    # 与上面的 /api/mindmap/kp（旧方案，带块原文）并存，互不影响。
                    if path == "/api/summary-kp":
                        # 卡 C-0050：`doc_id` 非数字同样退回 0 → 接口如实回
                        # "文档 0 里没有末级知识点…"，不再是 500。
                        return self._json(api_summary_kp(
                            _int_arg(qs.get("doc_id"), 0), qs.get("kp_id", ""), conn))
                    if path == "/api/raw":
                        return self._json(api_raw_list(conn))
                    if path == "/api/chat/status":
                        return self._json(api_chat_status())
                    # 卡 C-0071：首次运行体检（空库 / 无密钥）——首屏引导横幅用它。
                    # 只读、进 `_lock`（要读文档数），毫秒级，不碰模型。
                    if path == "/api/firstrun":
                        return self._json(api_firstrun(conn))
                    if path == "/api/notes":
                        return self._json(api_notes(conn))
                    # 按来源反查笔记（卡 C-0052）：知识点窗口的「查看笔记」用它
                    if path == "/api/notes/by_kp":
                        return self._json(api_notes_by(
                            qs.get("kp_id", ""), _int_arg(qs.get("doc_id"), 0)))
                    if path == "/api/note":
                        return self._json(api_note(unquote(qs.get("name", ""))))
                    # 多学科（卡 C-0039）：学科清单 + 当前学科 + **当前真实路径**。
                    # 刻意放在 `_lock` 里：切库要改 5 个 config 路径，不是原子操作，
                    # 在锁内读才能保证看到的是完整的某一套路径，不会读到"切一半"。
                    if path == "/api/subjects":
                        return self._json_auto(api_subjects())
                finally:
                    conn.close()
            self._json({"error": "未知路径"}, 404)
        except Exception as e:
            import traceback
            traceback.print_exc()          # C-0104 临时诊断：500 原因要看服务端
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        """C-0107：post 处理链的任何未捕获异常都回 500 + JSON 错误体。

        此前整个 do_POST 没有 try/except —— 任何异常（符号缺失、签名不符…）
        都让 BaseHTTPRequestHandler 直接断开连接，客户端只看到
        RemoteDisconnected，**拿不到任何原因**（C-0106 findings.F2 实证）。
        这里对齐 do_GET 的兜底做法：traceback.print_exc() + 500 JSON。

        分派体原样保留在 _do_post（缩进不变，不做重排以免引入新错）。
        """
        try:
            self._do_post()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()       # 500 原因要看服务端
            try:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            except Exception:           # noqa: BLE001
                pass                    # 响应头可能已发出（SSE 流），不再二次发送

    def _do_post(self):
            u = urlparse(self.path)
            ctype = self.headers.get("Content-Type", "")

            # 上传：走 multipart，单独处理
            if u.path == "/api/upload":
                with _lock:
                    return self._json(api_upload(self))

            # 流式对话：直接透传（SSE），不能包 JSON
            if u.path == "/api/chat/stream":
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
                # 流式接口自己写响应，不走 _json
                return api_chat_stream(self, payload)

            # 卡 C-00xx：卡组对话流式接口，同上
            if u.path == "/api/deck-chat/stream":
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
                return api_deck_chat_stream(self, payload)

            # 其余接口统一先解析 JSON 体
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._json({"error": "请求体不是合法 JSON: " + str(e)}, 400)

            # 验收扩展点（卡 C-0084）：默认空表，生产路径不受影响
            if TEST_HOOKS:
                hook = TEST_HOOKS.get(u.path)
                if hook is not None:
                    with _lock:
                        return self._json(hook(payload, _conn()))

            # ---------------- 多学科（卡 C-0039） ----------------
            if u.path in ("/api/subjects/create", "/api/subjects/switch", "/api/subjects/rename"):
                # 切库/建库必须与入库互斥，且要拿 _lock
                with _lock:
                    if u.path.endswith("/create"):
                        return self._json_auto(api_subjects_create(payload))
                    if u.path.endswith("/rename"):
                        # 改名不切库，但同样要串行
                        return self._json_auto(api_subjects_rename(payload))
                    return self._json_auto(api_subjects_switch(payload))

            # ---------------- 设置 ----------------
            if u.path == "/api/settings":
                return self._json_auto(api_settings_save(payload))
            if u.path == "/api/settings/reset":
                return self._json_auto(api_settings_reset(payload))
            if u.path == "/api/settings/prompt":
                return self._json_auto(api_settings_prompt_save(payload))

            # ---------------- 知识点构建 ----------------
            if u.path == "/api/kp-build":
                config.activate("kp")
                return self._json(api_kp_build_start(payload))
            if u.path == "/api/kp-build/batch":
                config.activate("kp")
                return self._json(api_kp_build_batch_start(payload))
            if u.path == "/api/kp-build/batch/cancel":
                return self._json(api_kp_build_batch_cancel(payload))
            if u.path == "/api/kp-build/clear":
                return self._json(api_kp_clear(payload))

            # ---------------- 预习（task-24） ----------------
            # 起后台线程跑 core/preview.py，HTTP 立刻返回；进度走 /api/preview/progress。
            if u.path == "/api/preview/start":
                return self._json(api_preview_start(payload))

            if u.path == "/api/textbook-kp/start":
                if payload.get("confirmed") is not True:
                    return self._json_auto({"ok": False, "error": "请先确认预计调用量"})
                try:
                    result = textbook_kp_mod.start(
                        int(payload.get("doc_id") or 0), int(payload.get("chapter") or 0),
                        str(payload.get("signature") or ""), force=payload.get("force") is True,
                        max_calls=int(payload.get("max_calls") or 0),
                        max_output_tokens=int(payload.get("max_output_tokens") or 0))
                    return self._json_auto(result)
                except (ValueError, OSError, sqlite3.Error) as e:
                    return self._json_auto({"ok": False, "error": str(e)})

            if u.path == "/api/textbook-kp/review":
                if not isinstance(payload.get("remembered"), bool):
                    return self._json_auto({"ok": False, "error": "请选择自评结果"})
                try:
                    result = textbook_kp_mod.record_review(
                        int(payload.get("doc_id") or 0), int(payload.get("chapter") or 0),
                        str(payload.get("point_id") or ""), payload["remembered"])
                    return self._json_auto(result)
                except (ValueError, OSError, sqlite3.Error) as e:
                    return self._json_auto({"ok": False, "error": str(e)})

            # ---------------- 教材补充：补做（task-27） ----------------
            # 同步、零模型、可能数秒；不持全局 _lock（build 内部只读 DB + 原子写产物）。
            if u.path == "/api/textbook-supplement/build":
                return self._json(api_textbook_supplement_build(payload))

            # ---------------- 教材补充：单条 KP 的按需 AI 分析（task-30） ----------------
            # 同步 1 次模型调用（5~20 秒）；不持全局 _lock，避免挡住其它请求。
            if u.path == "/api/textbook-supplement/kp/build":
                return self._json(api_textbook_supplement_kp_build(payload))

            # ---------------- 复习流程（task-33）：自测提交（锁外，原子写） ----------------
            if u.path == "/api/review/selftest/submit":
                return self._json_auto(api_review_selftest_submit(payload))

            # ---------------- 名词整理（task-36）：按预习重点名词重新整理（锁外，0 AI、写产物） ----------------
            if u.path == "/api/terms/build-preview" or u.path == "/api/terms/preview/build":
                return self._json_auto(api_terms_build_preview(payload))

            # ---------------- 批量入库 ----------------
            if u.path == "/api/ingest/batch/plan":
                with _lock:
                    # 只读探测：库已建好就不抢写锁（卡 C-0041）
                    conn = _conn(readonly=True)
                    try:
                        return self._json(api_ingest_batch_plan(payload, conn))
                    finally:
                        conn.close()
            if u.path == "/api/ingest/batch":
                config.activate("ingest")
                return self._json(api_ingest_batch_start(payload))
            if u.path == "/api/ingest/batch/retry":
                return self._json(api_ingest_batch_retry(payload))
            if u.path == "/api/ingest/batch/cancel":
                return self._json(api_ingest_batch_cancel(payload))

            # 单份入库：同步长任务，用独立锁，不阻塞只读接口（卡 C-0041）
            if u.path == "/api/ingest":
                config.activate("ingest")
                with _INGEST_LOCK:
                    return self._json(api_ingest(
                        payload.get("paths") or None,
                        bool(payload.get("force")),
                        bool(payload.get("no_ocr")),
                        bool(payload.get("no_figures")),
                    ))

            # 教材入库专线（卡 C-0089；C-0122 从重建中恢复）：与上面**并列**、
            # **同一把 `_INGEST_LOCK`**。教材是长任务（452 页本地 OCR ≈ 2 小时），
            # 串行是硬要求，所以它排在这里，而不是进并发批量池
            # （`core/pipeline_pool.py`）。与 `/api/ingest` 唯一的口径差别是
            # `mode="textbook"`——由 `api_ingest_textbook` 显式传给 `ingest_file`。
            if u.path == "/api/ingest/textbook":
                config.activate("ingest")      # 与单份入库同一条管线的配置
                with _INGEST_LOCK:
                    return self._json(api_ingest_textbook(
                        src=str(payload.get("src") or ""),
                        paths=payload.get("paths") or None,
                        force=bool(payload.get("force")),
                        with_figures=bool(payload.get("with_figures")),
                    ))

            # ---------------- 清理 ----------------
            if u.path == "/api/cleanup":
                with _lock:
                    return self._json(api_cleanup(
                        bool(payload.get("dry_run", True)),
                        bool(payload.get("include_notes")),
                        bool(payload.get("include_mistakes")),
                    ))

            # ---------------- 按路径导入 ----------------
            if u.path == "/api/import":
                with _lock:
                    return self._json(api_import_path(
                        payload.get("src", ""),
                        payload.get("sub", "") or "",
                        bool(payload.get("recursive", True)),
                        bool(payload.get("dry_run")),
                    ))

            # ---------------- 分享包（卡 C-0080） ----------------
            if u.path == "/api/share/estimate":
                return self._json_auto(api_share_estimate(payload))
            if u.path == "/api/share/export":
                return self._json_auto(api_share_export(payload))
            if u.path == "/api/share/inspect":
                return self._json_auto(api_share_inspect(payload))
            if u.path == "/api/share/import":
                return self._json_auto(api_share_import(payload))
            if u.path == "/api/share/reveal":
                return self._json_auto(api_share_reveal(payload))
            # ---------------- 写库类接口（C-0107 真值回填） ----------------
            # 从这里往下的分派在重建时随字节码截断一起丢了（原 TODO 就在这行）。
            # 补回来的依据有两条：
            #   ① 真值 `dashboard.funcs.txt::L5496` 的 do_POST 局部变量表里有
            #      `conn` / `kp_id` / `mid` / `x` / `p` —— 只有下面这些分支才会
            #      产生这些名字，说明它们在原件里确实存在；
            #   ② 原件反编译片段（`recovered-original/dashboard.py` 的两段
            #      do_POST）给出了分派形式（路径 → 调哪个 api_*）。
            # 每一分支都走本文件里**已经实现**的 api_* / core 函数，没有一条是
            # `{"ok": True}` 式的空壳。
            with _lock:
                conn = _conn()
                try:
                    # 移除索引（保留原文件）与批量真删：都要写库，必须串行
                    if u.path == "/api/doc/delete":
                        return self._json(api_delete_doc(
                            conn, int(payload.get("doc_id", 0)),
                            delete_file=bool(payload.get("delete_file")),
                            purge_orphans=bool(payload.get("purge_orphans", True)),
                            dry_run=bool(payload.get("dry_run"))))
                    if u.path == "/api/docs/delete":
                        return self._json_auto(api_delete_docs(conn, payload))
                    if u.path == "/api/raw/delete":
                        return self._json(api_delete_raw(payload.get("rel", "")))
                    # 重命名三条（卡 C-0085）：要动磁盘 + 改库 + 搬页图目录，
                    # 同样在 `_lock` 里串行；互斥闸在各函数内部（撞长任务给 409）
                    if u.path == "/api/rename/raw":
                        return self._json_auto(api_rename_raw(conn, payload))
                    if u.path == "/api/doc/rename":
                        return self._json_auto(api_rename_doc_title(conn, payload))
                    if u.path == "/api/artifact/rename":
                        return self._json_auto(api_rename_artifact_title(conn, payload))
                    # 复习评分
                    if u.path == "/api/review":
                        return self._json(api_review(
                            conn, int(payload.get("kp_id", 0)),
                            int(payload.get("grade", -1)),
                            payload.get("question", ""), payload.get("answer", "")))
                    # 手工新建知识点（知识点页签的表单）
                    if u.path == "/api/kp":
                        x = str(payload.get("name") or "").strip()
                        if not x:
                            return self._json({"error": "知识点名称不能为空"}, 400)
                        kp_id = study.upsert_kp(
                            conn, x, str(payload.get("summary") or ""),
                            str(payload.get("subject") or ""),
                            payload.get("chunk_ids") or [])
                        return self._json({"ok": True, "kp_id": int(kp_id)})
                    # 删除错题：ids / only_resolved / all 三选一（见 delete_mistakes）
                    if u.path == "/api/mistake/delete":
                        return self._json(delete_mistakes(
                            conn, payload.get("ids") or [],
                            bool(payload.get("only_resolved")),
                            bool(payload.get("all"))))
                    # 记入笔记（知识点窗口 / 课件答疑）
                    if u.path == "/api/note/kp":
                        return self._json_auto(api_note_save_kp(conn, payload))
                    if u.path == "/api/note/deck":
                        return self._json_auto(api_note_save_deck(conn, payload))
                    # 课件级遗忘曲线（卡 C-0061）：录入 / 标记。
                    # C-0108 补：真值 `dashboard.funcs.txt::L5496` 的 do_POST 局部
                    # 变量表里有 `conn`（本块已取），且 `api_deck_enroll(conn, doc_id)`
                    # / `api_deck_mark(conn, plan_id, round_, state)` 就在本文件里、
                    # 之前因 `core/study.py::enroll_deck` / `deck_mark` 整段丢失
                    # 而没有可调对象（C-0107 如实留成 404）。两个 core 函数已按字节码
                    # 真值补回（见 core/study.py 的 [C-0108 真值回填]），这里补分派。
                    if u.path == "/api/deck-enroll":
                        return self._json(api_deck_enroll(
                            conn, int(payload.get("doc_id", 0))))
                    if u.path == "/api/deck-mark":
                        return self._json(api_deck_mark(
                            conn, int(payload.get("plan_id", 0)),
                            int(payload.get("round", 0)),
                            str(payload.get("state") or "done")))
                finally:
                    conn.close()

            # 就地改笔记正文（卡 C-0082）：只动 `notes/` 下的文件、不碰库，
            # 所以**刻意不占 `_lock`**——入库占锁几十分钟也不该挡人改一篇笔记。
            if u.path == "/api/note/update":
                return self._json_auto(study.update_note_body(
                    str(payload.get("file") or ""), payload.get("body") or "",
                    payload.get("mtime")))

            # 仍未还原的分支（写进 result.json 的遗留清单，不是静默丢弃）：
            # `/api/subjects` 的 POST 语义无真值证据（前端目前也没有 POST 调用点）。
            # 它现在会落到下面这行 404「未知路径」（与 do_GET 同款），
            # 而不是"连接被重置"或一直挂着不回。
            #
            # 注：`/api/deck-enroll`、`/api/deck-mark` 已在上面补上真分派
            # （卡 C-0108：`core/study.py::enroll_deck` / `deck_mark` 按字节码真值补回）。
            self._json({"error": "未知路径"}, 404)


def main():
    import argparse

    ap = argparse.ArgumentParser(description='学习看板（本地 Web UI）')
    ap.add_argument('--port', type=int, default=8756)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--no-open', action='store_true', help='不要自动打开浏览器')
    args = ap.parse_args()

    subj = subjects_mod.restore()
    config.ensure_dirs()

    hooks = config.install_hooks()
    print(f"[设置中心] 运行时钩子：{hooks.get('installed') or '已装过'}"
          f"  配置文件 {config.SETTINGS_FILE}", flush=True)

    conn = _conn()
    info = db.init_db(conn)
    n_docs = conn.execute('SELECT COUNT(*) AS n FROM documents').fetchone()['n']
    conn.close()

    url = f"http://{args.host}:{args.port}/"
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"学习看板已启动: {url}")
    print(f"  学科: {subj['name']}（{subj['slug']}）")
    print(f"  资料库: {config.DB_PATH}")
    print(f"  文档数: {n_docs} | FTS5: {'开' if info['fts5'] else '关'}"
          f" | tokenizer: {info['tokenizer']}")
    if info.get('migrations'):
        for m in info['migrations']:
            print(f"  迁移: {m}")
    print('  按 Ctrl+C 停止')
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
    finally:
        srv.server_close()
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
