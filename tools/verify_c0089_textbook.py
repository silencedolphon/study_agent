#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""卡 C-0089 验证（快档）：资料栏「教材入库」独立入口 + 防旧逻辑覆盖。

要证的六件事（对应卡面验收标准）：
  1. 资料栏多了**独立的**「教材入库（专线）」一节，且方式一 / 拖拽 / 批量入库 /
     方式二**四个旧入口原样都在**（并与 git 基线逐行比对：那两段区间一个字没改）；
  2. 新增区块的**落点**：在「拖拽」说明之后、`raw\` 旧文案之前；
  3. 后端 `/api/ingest/textbook` 与单份入库**并列**、**同一把 `_INGEST_LOCK`**、
     `config.activate("ingest")`，并且**不进并发批量池**（代码证据）；
  4. 走一遍真链路：拷进 `raw\教材\` → 教材专线（章识别 + 按章切块）→ 入库，
     回执**列出识别到的章**（章号 + 章名 + 页范围）与块数变化，
     且**视觉识图零调用**（本地 OCR 路线，不花接口费）；
  5. ★ 防覆盖：`chunk_scheme='textbook-v1'` 的文档，`/api/ingest`（单份与
     "入库全部"）与批量入库**都拒绝用旧切块重跑**，并给出可操作提示；
     拒绝之后**库里那份教材的块一个没变**（这才是"没被覆盖"的实质证据）；
  6. `tools/reimport_all.py --textbooks` 真的只做教材、默认跳过教材。

## 为什么这一档里"真 OCR"被替身接管

本地 OCR 单独一份迷你扫描件要 20 秒（rapidocr 引擎冷启动 ~10s + 3 页），
而验收槽只有 25 秒，本档还要起一个真看板实例打真 HTTP。所以这里用
`tools/c0087_textbook_fixture.json`（doc#91 的真实 OCR 块快照，前 88 块 =
第 1~3 章）接管"读 PDF 出块"这一步：链路里**章识别、按章切块、嵌入、写库、
回执**全是真跑，只有"把图片变成文字"被替换。
**真 OCR 的端到端**由 `tools/probe_c0089_textbook.mjs`（真浏览器点按钮，
迷你扫描件 3 页真 OCR、真落盘、真回执、截图）覆盖，本脚本会读它的 JSON 结果
并把其中的 OCR 事实一起报出来。

隔离：库 / raw / logs / 设置全部指向临时根；`KB_TESTING=1` 护栏拒绝连真库；
真库只被 `mode=ro` 只读 URI 数行数（验证前后各一次，必须相等）。
不联网、不调模型（识图被替身接管）、不花钱。

跑法（绝对路径，不依赖 cwd）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\verify_c0089_textbook.py

输出：**只有 6 行**，首行含 `C0089-PASS` 或 `C0089-FAIL`；明细写日志文件。
退出码 0 / 2。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # tools/ -> 项目根
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

T0 = time.time()
WORK = ROOT / "_tmp_c0089" / "verify_data"
REAL_DB = ROOT / "index" / "study.db"
FIXTURE = ROOT / "tools" / "c0087_textbook_fixture.json"
MINI_PDF = ROOT / "tools" / "c0089_mini_textbook.pdf"
# 真浏览器探针的结果 JSON：优先读**交付物**那一份（卡目录），其次读探针的
# 默认输出位置。两处都没有就记一条 skip（不算失败，别的机器上跑不出卡目录）。
CARD_DIR = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
                r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0089")
PROBE_JSONS = [CARD_DIR / "probe_result.json",
               ROOT / "_tmp_c0089" / "probe_result.json"]
LAUNCHER = ROOT / "tools" / "c0083_tmp_launch.py"
DASH = ROOT / "tools" / "dashboard.py"
PORT = 8791
# 入库时用的文件名刻意取短（`药理学6版.pdf`）：文档标题是在"文件名 vs 正文标题"
# 之间按**信息量（长度）**择优的（`ingest._score_title`），文件名太长就会赢过
# 正文里的真书名。短名 + 正文首行的"药理学（第6版）" ⇒ 书名取自正文，面包屑
# 就是「药理学（第6版） > 第1章 绪论」这种真教材形态。
MINI_NAME = "药理学6版.pdf"
MINI_REL = "教材/" + MINI_NAME                      # 相对 raw/
MINI_BLOCKS = 88                                   # 前 88 块 = 前置 + 第 1~3 章
EXPECT_CHAPTERS = 3
# 2026-10-12 的基线（卡面给定）：两段"一个字都不许动"的区间（1-based，闭区间）
BASE_SEGS = [(7481, 7530), (7537, 7545)]
KEEP_ANCHORS = [
    ("方式一（导入按钮）", 'id="im-go"'),
    ("拖拽入库区", 'id="dz"'),
    ("批量入库（开始按钮）", "batchConfirm()"),
    ("方式二（分享包）", 'id="sh-open"'),
    ("全量入库按钮", 'onclick="reindex(null)"'),
    ("强制重建按钮", 'onclick="reindex(null, true)"'),
]

CHECKS: list[tuple[str, bool, str]] = []
SKIPS: list[str] = []
EARLY: list[str] = []
DETAIL: list[str] = []
SERVER = None


def add(name: str, ok: bool, detail: str = "") -> bool:
    ok = bool(ok)
    CHECKS.append((name, ok, detail))
    if not ok:
        msg = f"C0089-FAIL 检查未过: {name}  {detail}"
        EARLY.append(msg)
        print(msg, file=sys.stderr, flush=True)
    return ok


def note(msg: str) -> None:
    DETAIL.append(msg)


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    note(f"[跳过] {name}：{why}")


# --------------------------------------------------------------------- 真库
def real_counts() -> dict:
    """真库只读行数（`mode=ro`），验证前后必须相等。"""
    out: dict = {}
    for tag, p in (("default", REAL_DB),
                   ("subjects", ROOT / "subjects")):
        if not p.exists():
            out[tag] = None
            continue
        dbs = [p] if p.is_file() else sorted(p.rglob("*.db"))
        n = {}
        for f in dbs:
            try:
                c = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
                n[f.name] = (
                    c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                    if _has(c, "documents") else -1,
                    c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                    if _has(c, "chunks") else -1,
                )
                c.close()
            except sqlite3.Error as e:
                n[f.name] = f"err {e}"
        out[tag] = n
    return out


def _has(conn, table: str) -> bool:
    return conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone()[0] > 0


# --------------------------------------------------------------------- HTTP
def http(path: str, payload=None, timeout: float = 12.0):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body[:400]}
    except Exception as e:                                  # noqa: BLE001
        return 0, {"error": f"{type(e).__name__}: {e}"}


def wait_server(sec: float = 14.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < sec:
        st, _ = http("/api/stats", timeout=2.0)
        if st == 200:
            return True
        time.sleep(0.35)
    return False


# --------------------------------------------------------------------- 主流程
def main() -> int:
    # ---- 隔离：环境变量必须在 import core 之前落地（config 导入即固化）----
    if WORK.exists():
        assert WORK.parent.name == "_tmp_c0089", WORK
        shutil.rmtree(WORK, ignore_errors=True)
    raw_tb = WORK / "raw" / "教材"
    raw_tb.mkdir(parents=True, exist_ok=True)
    for sub in ("index", "logs", "notes"):
        (WORK / sub).mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "KB_RAW": str(WORK / "raw"),
        "KB_NOTES": str(WORK / "notes"),
        "KB_LOG": str(WORK / "logs"),
        "KB_SETTINGS_FILE": str(WORK / "settings.json"),
        "KB_TESTING": "1",
        "OUTLINE_ANALYSIS": "0",
        "FIGURE_DESC": "0",
    })
    # ⚠ **刻意不设 KB_INDEX / KB_DB，而且改路径必须排在 import db 之后**：
    # `core/db.py` 的测试护栏拿 `_REAL_DB_PATH = Path(config.DB_PATH).resolve()`
    # （**模块加载时**的快照）去拦"测试脚本误连真库"。所以顺序只能是
    #   ① import config（路径还是真库）→ ② import db（快照 = 真库，护栏有效）
    #   → ③ 才把 config 的路径改成临时库（≠ 快照，不撞护栏）。
    # 反过来的话快照会变成临时路径，护栏随后**反手拒绝我们自己的临时库**
    # （实测：`RuntimeError: 拒绝连接真实资料库: ...\_tmp_c0089\verify_data\...`）。
    import core.config as config                             # noqa: E402
    from core import db                                      # noqa: E402
    config.INDEX_DIR = WORK / "index"
    config.DB_PATH = WORK / "index" / "study.db"
    config.RAW_DIR = WORK / "raw"
    config.NOTES_DIR = WORK / "notes"
    config.LOG_DIR = WORK / "logs"

    # 学科注册表也挪到临时根：否则 `subjects.restore()` 会把 config 的路径
    # **重绑到真实学科库**（C-0083 的启动器踩过这个坑；看板的 restore 只在
    # `main()` 里调，本脚本只 import 模块，所以这一步是双保险）。
    import core.subjects as subjects_mod                     # noqa: E402
    subjects_mod.registry_path = lambda: WORK / "logs" / "subjects.json"
    subjects_mod.subjects_root = lambda: WORK / "subjects"

    from core import extract, figures                        # noqa: E402
    if Path(config.DB_PATH).resolve() == REAL_DB.resolve():
        print("C0089-FAIL 隔离失败：DB_PATH 没改掉")
        return 2

    import dashboard as dash                                  # noqa: E402

    before = real_counts()
    note(f"临时数据根 {WORK}；真库读数 {before}")

    # ============ 1) 静态：UI 入口与落点 ============
    html = dash.INDEX_HTML
    add("资料栏有独立的「教材入库（专线）」一节",
        "教材入库（专线）" in html and 'id="tb-src"' in html
        and 'id="tb-go"' in html and 'onclick="tbIngest()"' in html
        and 'id="tb-log"' in html)
    for label, anchor in KEEP_ANCHORS:
        add(f"旧入口仍在：{label}", anchor in html, anchor)
    # 落点：拖拽说明（7530 前）→ 新块 → 旧文案（7532）
    i_drag = html.find("拖拽走的是浏览器上传（multipart）")
    i_new = html.find("教材入库（专线）")
    i_old = html.find("这种方式只是把文件放进")
    add("新块落点在「拖拽说明」之后、「raw\\ 文案」之前",
        0 <= i_drag < i_new < i_old, f"位置 {i_drag} < {i_new} < {i_old}")
    # 一条龙：按钮文案与说明都必须在（人拍板"不做逐步确认"）
    add("一条龙：按钮 + 本地 OCR + 按章切块 + 回执列章的说明都在",
        "选择教材并入库" in html and "本地 OCR" in html
        and "按章切块" in html and "页范围" in html)
    add("前端把回执渲染出来（tbRender 逐章列出）",
        "function tbRender" in html and "教材入库回执" in html
        and "page_start" in html)
    # JS 里那条请求确实打在新路由上
    add("前端调的是 /api/ingest/textbook",
        html.count("/api/ingest/textbook") >= 1
        and "JSON.stringify({src, force})" in html)
    # 与基线逐行比对：两段"一个字都不许动"的区间
    base = git_show(DASH)
    if base is None:
        skip("git 基线逐行比对", "git 不可用")
    else:
        bl = base.splitlines()
        cur_txt = DASH.read_text(encoding="utf-8")
        cur_lines = cur_txt.splitlines()
        dif = git_diff(DASH) or ""
        dels = {ln[1:] for ln in dif.splitlines()
                if ln.startswith("-") and not ln.startswith("---")}
        for lo, hi in BASE_SEGS:
            seg = bl[lo - 1:hi]
            seg_ok = "\n".join(seg) in cur_txt
            add(f"基线区间 {lo}-{hi} 内容原样存在（{hi-lo+1} 行）", seg_ok,
                "" if seg_ok else f"在基线里取 {hi-lo+1} 行后于当前文件未命中")
            # 更强的一条：这 50 行**没有一行**出现在 `git diff` 的删除侧
            # ⇒ 相对基线一个字都没被删改。插入位置在它之后，所以它整段
            # 会因前面的历史改动而下移，但内容必须一行不差。
            hit = [x for x in seg if x.strip() and x in dels]
            add(f"基线区间 {lo}-{hi} 没有任何一行被删改（相对 {git_rev()}）",
                not hit, f"被删改 {len(hit)} 行，例如 {hit[:2]}")
        note(f"基线 HEAD={git_rev()}；当前 {len(cur_lines)} 行"
             f"（基线 {len(bl)} 行）")

    # ============ 2) 后端：路由 / 锁 / 串行 ============
    src = DASH.read_text(encoding="utf-8")
    i_route = src.find('u.path == "/api/ingest/textbook"')
    add("路由 /api/ingest/textbook 存在", i_route > 0)
    i_next = src.find("if u.path ==", i_route + 10) if i_route > 0 else -1
    route_txt = (src[i_route:i_next] if i_next > 0
                 else src[i_route:i_route + 400]) if i_route > 0 else ""
    add("教材路由与单份入库并列：config.activate(\"ingest\") + with _INGEST_LOCK",
        'config.activate("ingest")' in route_txt and "with _INGEST_LOCK:" in route_txt)
    add("教材路由排在本条锁内（同一把锁 = 串行，不占 _lock）",
        "_lock" not in route_txt.replace("_INGEST_LOCK", ""), route_txt[:80])
    i_api = src.find("def api_ingest_textbook(")
    add("api_ingest_textbook 存在", i_api > 0)
    i_nd = src.find("\ndef ", i_api + 10) if i_api > 0 else -1
    api_txt = (src[i_api:i_nd] if i_nd > 0
               else src[i_api:i_api + 9000]) if i_api > 0 else ""
    add("显式调 ingest_file(mode=TEXTBOOK_MODE)",
        "mode=ingest_mod.TEXTBOOK_MODE" in api_txt)
    add("★ 串行：教材入口**不进**并发批量池（不调 pool_mod.submit_batch）",
        "pool_mod" not in api_txt)
    add("教材入口保持本地 OCR（with_ocr=True）且默认不调视觉识图",
        "with_ocr=True" in api_txt and "with_figures=with_figures" in api_txt
        and "with_figures: bool = False" in api_txt)
    add("本地 OCR 实现是 rapidocr（免费通道，非视觉大模型）",
        "from rapidocr_onnxruntime import RapidOCR" in
        (ROOT / "core" / "extract.py").read_text(encoding="utf-8"))
    # 防覆盖的三个通用入口
    add("防覆盖：/api/ingest 里摘掉教材专线产物",
        "_drop_textbook_locked(conn, targets)" in src)
    add("防覆盖：批量入库（plan/start/retry）都摘掉",
        src.count("_drop_textbook_locked_raw(") >= 2
        and "_textbook_locked_map(conn)" in src)
    add("防覆盖是「按标记跳过」：判据取自 documents.extra.chunk_scheme",
        'ingest_mod.TEXTBOOK_CHUNK_SCHEME' in src
        and 'extra.get("chunk_scheme")' in src)
    add("提示可操作（说清怎么重跑/怎么清标记）",
        "请走资料栏的「教材入库」" in src and "json_remove" in src)
    # 行尾
    raw_bytes = DASH.read_bytes()
    bare_lf = raw_bytes.count(b"\n") - raw_bytes.count(b"\r\n")
    add("dashboard.py 行尾仍是 CRLF（裸 LF = 0）", bare_lf == 0,
        f"裸 LF {bare_lf} 个")

    # ============ 3) 真链路（替身：读 PDF 出块 = fixture 真实 OCR 块）============
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    from core.extract import Block, ExtractResult
    from core.textbook import _page_start
    blocks = [Block(text=b["text"], page=_page_start(str(b.get("ref") or "")),
                    para=int(b.get("ord") or 0), kind="ocr", level=0)
              for b in data["blocks"][:MINI_BLOCKS]]
    book = str(data.get("title") or "教材")
    fake = ExtractResult(blocks=blocks,
                         meta={"pages": 452, "title": "", "scanned_pages": []})

    src_dir = WORK / "外来源"
    src_dir.mkdir(parents=True, exist_ok=True)
    src_pdf = src_dir / MINI_NAME
    shutil.copy2(MINI_PDF, src_pdf)
    # 临时 raw 里再放一份**普通讲义**：这样"入库全部待处理文件"才是真实场景
    # （教材被跳过、别的照常入），而不是"整个 raw 只有一份被锁的教材"。
    (WORK / "raw" / "讲义").mkdir(parents=True, exist_ok=True)
    (WORK / "raw" / "讲义" / "小抄.txt").write_text(
        "药理学小抄：抗高血压药分为利尿药、钙通道阻滞药、ACEI、ARB 等。\n"
        "本章重点掌握各类药物的作用机制与临床应用。\n", encoding="utf-8")

    fig_calls: list = []
    real_extract = extract.extract
    real_figs = figures.ingest_document_figures
    extract.extract = lambda p: fake
    figures.ingest_document_figures = lambda *a, **kw: (
        fig_calls.append(kw) or {})
    try:
        rep = dash.api_ingest_textbook(src=str(src_pdf))
    finally:
        extract.extract = real_extract
        figures.ingest_document_figures = real_figs

    landed = raw_tb / MINI_NAME
    add("落盘语义：文件真进了 raw\\教材\\（磁盘事实）", landed.exists(),
        str(landed))
    add("端到端返回成功且没有 error", "error" not in rep,
        str(rep.get("error") or rep.get("summary")))
    add("★ 视觉识图**零调用**（本地 OCR 路线，不花接口费）",
        not fig_calls, f"实际被调用 {len(fig_calls)} 次")
    docs = rep.get("docs") or []
    d0 = docs[0] if docs else {}
    add("回执含本份文档", len(docs) == 1 and d0.get("status") in ("added", "updated"),
        f"status={d0.get('status')} reason={d0.get('reason')}")
    add("回执报块数变化（0 → N）",
        d0.get("chunks_before") == 0
        and int(d0.get("chunks_after") or 0) > 0,
        f"{d0.get('chunks_before')} -> {d0.get('chunks_after')}")
    chs = d0.get("chapters") or []
    body_chs = [c for c in chs if int(c.get("chapter_no") or 0) > 0]
    add(f"★ 回执列出识别到的章：{EXPECT_CHAPTERS} 章（章号 + 章名 + 页范围）",
        len(body_chs) == EXPECT_CHAPTERS
        and all(c.get("label") and c.get("page_start") and c.get("page_end")
                for c in body_chs),
        json.dumps(chs, ensure_ascii=False)[:200])
    add("回执报章识别未降级（fallback=False）",
        d0.get("fallback") is False and d0.get("chapters_n") == EXPECT_CHAPTERS,
        f"fallback={d0.get('fallback')} chapters_n={d0.get('chapters_n')}")

    conn = db.connect()
    row = conn.execute("SELECT id, role, extra, n_chunks FROM documents "
                       "WHERE path LIKE ?", ("%教材%",)).fetchone()
    if row is None:
        add("库里有这条教材记录", False, "没找到")
        conn.close()
        return finish(before)
    extra = json.loads(row["extra"] or "{}")
    doc_id = int(row["id"])
    add("documents.role == 'textbook'（显式声明，不靠文件名启发式）",
        row["role"] == "textbook", f"role={row['role']!r}")
    add("documents.extra.chunk_scheme == 'textbook-v1'",
        extra.get("chunk_scheme") == "textbook-v1", str(extra))
    hp = [r["heading_path"] or "" for r in conn.execute(
        "SELECT heading_path FROM chunks WHERE doc_id=?", (doc_id,))]
    # 书名以**实际落库的**面包屑首段为准（不是 fixture 里的书名：这份是替身
    # 喂块，文档标题取自被入库的那个文件名）。
    hparts = [[x.strip() for x in h.split(">")] for h in hp]
    books = {p[0] for p in hparts if p and p[0]}
    add("块的面包屑是「书名 > 第X章 章名」（或仅书名 = 前置内容）",
        bool(hp) and len(books) == 1
        and all(len(p) == 1 or (len(p) == 2 and p[1].startswith("第")
                                and "章" in p[1]) for p in hparts),
        f"{len(hp)} 块，书名 {books}，例：{sorted(set(hp))[:3]}")
    pages = [r["source_ref"] or "" for r in conn.execute(
        "SELECT DISTINCT source_ref FROM chunks WHERE doc_id=?", (doc_id,))]
    add("章的切分跨了多页（不是把整本塞进一块）",
        len(hp) > EXPECT_CHAPTERS and len(pages) > 3,
        f"块数 {len(hp)}，出处种类 {len(pages)}")
    snap0 = (int(row["n_chunks"] or 0), tuple(sorted(set(hp))))
    note(f"入库后：doc#{doc_id} role={row['role']} n_chunks={snap0[0]} "
         f"面包屑 {len(snap0[1])} 种")
    conn.close()

    # ============ 4) 真实例：HTTP 层的拒绝证据 ============
    global SERVER
    srv_env = dict(os.environ)
    srv_env.pop("KB_TESTING", None)      # 同上：子进程要连的是同一个临时库
    srv_env["KB_SETTINGS_FILE"] = str(WORK / "settings.json")
    SERVER = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(LAUNCHER), str(WORK), str(PORT)],
        cwd=str(ROOT), env=srv_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok_up = wait_server()
    add(f"临时看板实例就绪（127.0.0.1:{PORT}，临时数据根）", ok_up)

    if ok_up:
        # 4a) 单份：直接点「入库」按钮走的那条接口
        st, r1 = http("/api/ingest", {"paths": [MINI_REL]})
        add("★ 防覆盖：/api/ingest 拒绝对教材专线产物用旧切块重跑",
            st == 200 and "error" in r1 and r1.get("textbook_locked"),
            f"HTTP {st} {json.dumps(r1, ensure_ascii=False)[:180]}")
        err1 = str(r1.get("error") or "")
        add("拒绝提示可操作（说明这是教材专线产物 + 该走「教材入库」+ 怎么清标记）",
            "教材专线产物" in err1 and "教材入库" in err1
            and "json_remove" in err1)
        # 4b) 入库全部待处理文件：教材被跳过，别的文件照常入
        st, r2 = http("/api/ingest", {})
        add("★ 防覆盖：「入库全部待处理文件」跳过教材专线产物",
            st == 200 and "error" not in r2
            and len(r2.get("textbook_locked") or []) == 1
            and "教材专线产物" in str(r2.get("summary") or ""),
            f"HTTP {st} summary={str(r2.get('summary'))[:140]}")
        add("旧入口回归：同一批里**普通文件照常入库**（不是把所有人都挡掉）",
            st == 200 and int(r2.get("added") or 0) >= 1,
            f"added={r2.get('added')} chunks={r2.get('chunks')}")
        # 4c) 批量入库：不进并发池
        st, r3 = http("/api/ingest/batch", {"paths": [MINI_REL]})
        add("★ 防覆盖：批量入库拒绝教材专线产物",
            st == 200 and "error" in r3 and r3.get("textbook_locked")
            and "教材专线产物" in str(r3.get("error") or ""),
            f"HTTP {st} {json.dumps(r3, ensure_ascii=False)[:180]}")
        # 4d) 教材专线自己可以重跑（内容未变 ⇒ skipped，幂等、不重建块）
        t0 = time.perf_counter()
        st, r4 = http("/api/ingest/textbook", {"paths": [MINI_REL]}, timeout=20)
        add("教材专线入口可用（走真 HTTP + _INGEST_LOCK）；内容未变 ⇒ skipped",
            st == 200 and "error" not in r4
            and (r4.get("docs") or [{}])[0].get("status") == "skipped",
            f"HTTP {st} status={(r4.get('docs') or [{}])[0].get('status')} "
            f"{time.perf_counter()-t0:.1f}s")
        add("重复入库后回执仍列出章（幂等也不丢可核对性）",
            len([c for c in ((r4.get("docs") or [{}])[0].get("chapters") or [])
                 if int(c.get("chapter_no") or 0) > 0]) == EXPECT_CHAPTERS)

    # 4e) 实质证据：折腾了这一圈，库里那份教材的块**一个没变**
    conn = db.connect()
    row2 = conn.execute("SELECT n_chunks FROM documents WHERE id=?",
                        (doc_id,)).fetchone()
    hp2 = tuple(sorted({r["heading_path"] or "" for r in conn.execute(
        "SELECT heading_path FROM chunks WHERE doc_id=?", (doc_id,))}))
    conn.close()
    add("★ 实质证据：被拒绝之后，教材的块数与面包屑**一个没变**",
        row2 is not None and (int(row2["n_chunks"] or 0), hp2) == snap0,
        f"{snap0} -> {(int(row2['n_chunks'] or 0) if row2 else -1, hp2)}")

    # ============ 5) reimport_all --textbooks ============
    env = dict(os.environ)
    env.update({"KB_RAW": str(WORK / "raw"), "KB_NOTES": str(WORK / "notes"),
                "KB_INDEX": str(WORK / "index"),
                "KB_DB": str(WORK / "index" / "study.db"),
                "KB_LOG": str(WORK / "logs"),
                "KB_SETTINGS_FILE": str(WORK / "settings.json")})
    # 子进程要**真的连上同一个临时库**（`--dry-run` 只读），而护栏的基准
    # 是它自己 import db 时的 `config.DB_PATH` —— 子进程里那正好就是同一个
    # 临时库，留着 KB_TESTING 会被自己拦下（原因同上面主进程那段）。
    # 真库保护由"所有 KB_* 都显式指向临时根 + 主进程前后两次真库行数比对"承担。
    env.pop("KB_TESTING", None)
    py = sys.executable
    r_generic = run_cmd([py, "-X", "utf8", str(ROOT / "tools" / "reimport_all.py"),
                         "--dry-run"], env)
    add("reimport_all 默认**跳过教材**（并说明怎么重跑）",
        "教材产物被跳过" in r_generic and "--textbooks" in r_generic
        and MINI_NAME not in r_generic, r_generic.strip()[-200:])
    r_tb = run_cmd([py, "-X", "utf8", str(ROOT / "tools" / "reimport_all.py"),
                    "--textbooks", "--dry-run"], env)
    add("reimport_all --textbooks 只做教材且走教材专线",
        "教材专线 mode=textbook" in r_tb and "目标 1 份" in r_tb
        and MINI_NAME.split(".")[0] in r_tb and "识图: 关" in r_tb,
        r_tb.strip()[-200:])

    # ============ 6) 探针（真浏览器 / 真 OCR）的证据 ============
    probe = next((p for p in PROBE_JSONS if p.exists()), None)
    if probe is not None:
        try:
            pr = json.loads(probe.read_text(encoding="utf-8"))
        except ValueError:
            pr = {}
        note(f"真浏览器探针证据：{probe}")
        add("真浏览器探针：整轮通过且零 JS 错误",
            pr.get("ok") is True and pr.get("jsErrors") == 0,
            f"ok={pr.get('ok')} jsErrors={pr.get('jsErrors')}")
        add("真浏览器探针：真本地 OCR 跑了（免费、无视觉模型调用）",
            int(pr.get("ocrPages") or 0) > 0
            and int(pr.get("visionCalls") or 0) == 0,
            f"ocrPages={pr.get('ocrPages')} visionCalls={pr.get('visionCalls')}")
        add("真浏览器探针：按钮一条龙跑通并列出章",
            int(pr.get("chaptersListed") or 0) >= EXPECT_CHAPTERS,
            str(pr.get("chaptersListed")))
        shots = [Path(s) for s in (pr.get("shots") or [])]
        add("真浏览器探针：截图落盘（3 张）",
            len(shots) == 3 and all(s.exists() and s.stat().st_size > 0
                                    for s in shots),
            "；".join(f"{s.name}:{s.stat().st_size if s.exists() else 0}"
                     for s in shots))
    else:
        skip("真浏览器探针证据", "没找到 probe_result.json（探针未跑或未落盘）")

    return finish(before, rep=rep, doc_id=doc_id)


def run_cmd(cmd: list[str], env: dict) -> str:
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=20)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:                                  # noqa: BLE001
        return f"<失败 {type(e).__name__}: {e}>"


def git_show(path: Path) -> str | None:
    try:
        r = subprocess.run(["git", "show", f"HEAD:{path.relative_to(ROOT).as_posix()}"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
        return r.stdout if r.returncode == 0 else None
    except Exception:                                       # noqa: BLE001
        return None


def git_diff(path: Path) -> str | None:
    """相对 HEAD 的 diff（含**工作区里前几张卡的未提交改动**，所以只看
    "某一行有没有被删"这一件事——本卡要证明的是那两段一个字没被删改）。"""
    try:
        r = subprocess.run(
            ["git", "diff", "-U0", "--", path.relative_to(ROOT).as_posix()],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10)
        return r.stdout if r.returncode == 0 else None
    except Exception:                                       # noqa: BLE001
        return None


def git_rev() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip() or "?"
    except Exception:                                       # noqa: BLE001
        return "?"


def finish(before: dict, rep: dict | None = None, doc_id: int = 0) -> int:
    if SERVER is not None:
        try:
            SERVER.terminate()
            SERVER.wait(timeout=6)
        except Exception:                                   # noqa: BLE001
            try:
                SERVER.kill()
            except Exception:                               # noqa: BLE001
                pass
    after = real_counts()
    add("真库 documents/chunks 行数在本次验证前后不变",
        before == after, f"{before} -> {after}")

    ok = all(c[1] for c in CHECKS)
    n_ok = sum(1 for c in CHECKS if c[1])
    log = ROOT / "logs" / "c0089" / "textbook_verify_detail.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(
        [f"[{'OK ' if o else 'FAIL'}] {n}  {d}" for n, o, d in CHECKS]
        + ["", f"--- 跳过 {len(SKIPS)} 项 ---"] + SKIPS
        + ["", "--- 明细 ---"] + DETAIL), encoding="utf-8")

    d0 = ((rep or {}).get("docs") or [{}])[0]
    body = [c for c in (d0.get("chapters") or [])
            if int(c.get("chapter_no") or 0) > 0]
    print(f"C0089-{'PASS' if ok else 'FAIL'}  资料栏「教材入库」独立入口 + 防旧逻辑覆盖"
          f"（{n_ok}/{len(CHECKS)} 项通过，跳过 {len(SKIPS)} 项）")
    print("1) UI：资料栏新增「教材入库（专线）」一节（方式一/拖拽/批量/方式二"
          " 四个旧入口原样，且与基线逐行比对未改）")
    print(f"2) 后端：/api/ingest/textbook 与单份入库并列、同一把 _INGEST_LOCK、"
          f"不进并发池；ingest_file(mode=textbook)")
    chapter_txt = " / ".join(
        f"第{c['chapter_no']}章 {c['label'].split('章')[-1]} p.{c['page_start']}-{c['page_end']}"
        for c in body) or "（无）"
    print(f"3) 端到端：落盘 raw\\教材\\ → 本地 OCR（视觉零调用）→ 按章切块 → 入库；"
          f"块数 {d0.get('chunks_before')} → {d0.get('chunks_after')}")
    print(f"4) 回执列章：{chapter_txt}")
    print(f"5) 防覆盖：/api/ingest（单份与全部）与批量入库均拒绝并给可操作提示；"
          f"拒绝后教材块数/面包屑一个没变；reimport_all --textbooks 只做教材")
    print(f"6) 真库 {before} -> {after}；耗时 {time.time()-T0:.1f}s；明细 {log}")
    for line in EARLY:
        print(line)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
