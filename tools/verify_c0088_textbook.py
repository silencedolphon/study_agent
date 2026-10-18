#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""卡 C-0088 教材专线端到端验证（快档）：`ingest_file(mode="textbook")`。

要证的五件事（对应卡面验收标准）：
  1. `mode="textbook"` 确实走教材专线（`core/textbook.py` 的章识别 + 按章切块）；
  2. 入库后 `documents.role='textbook'`，且是**显式声明**而不是标题启发式
     ——判据：教材模式下 `db.classify_role` **一次都没有被调用**（探针计数），
     而通用模式下它被调用了；
  3. `documents.extra` 里多了新键 `chunk_scheme='textbook-v1'`，**老键一个不少**；
  4. 块的面包屑是「书名 > 第X章 章名」，且**跨章块 = 0**（用库里的行重建
     `Chunk` 再复核一遍，不只信返回值）；
  5. 识图调用的 `max_calls` 已显式传入：教材模式 = `FIGURE_MAX_CALLS_PER_DOC`，
     通用模式 = `None`（与改造前"不传"逐字等价）。

块来源：`tools/c0087_textbook_fixture.json` —— **真教材 doc#91《药理学 第6版
刘晓东》的 867 个 OCR 块**只读快照（C-0087 交付物），本脚本取前 88 块
（前置内容 + 前 3 章）当"迷你教材"。
为什么不用真库里的 doc#91：那要走 452 页本地 OCR（约 2 小时），远超验收槽；
而本卡要证的是**接线**，不是 OCR 质量。

为什么要把块"喂"给管线而不是读一份 PDF：`core/extract.py::extract_pdf()` 会把
每个 text block 内的换行**合并成一行**（`_join_wrapped`），而教材专线的章识别
是**按行**工作的（书眉「第X章 章名」必须整行独立）。所以有文字层的 PDF 走不出
按行识别这条路——教材专线的目标场景是**扫描件走本地 OCR**（OCR 块保留换行，
doc#91 就是这种），本脚本用的正是这种块。这条差异已写进 result.json 的
`not_done`／`发现`，供 C-0089/C-0090 决策。

隔离：库/raw/logs/设置全部指向临时根；`KB_TESTING=1` 护栏拒绝连真库；
真库只被 `mode=ro` 只读 URI 数两次行数（验证前后各一次，必须相等）。
不联网、不调模型（识图被替身接管）、不花钱。

跑法（绝对路径，不依赖 cwd）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\verify_c0088_textbook.py

输出：结论行含 `C0088-TB-PASS` 或 `C0088-TB-FAIL`，退出码 0 / 2。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # tools/ -> 项目根
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
# 真库读数与回归脚本共用同一个函数（口径一致才可比）
from c0088_snapshot import real_db_counts as _real_db_counts       # noqa: E402

T0 = time.time()
WORK = ROOT / "_tmp_c0088_tb"
REAL_DB = ROOT / "index" / "study.db"
FIXTURE = ROOT / "tools" / "c0087_textbook_fixture.json"
CARRIER = ROOT / "_smoke_fixtures" / "formats" / "数据结构-第五章.pdf"
# 迷你教材：前 88 块（doc#91 的 0:17 是前置/目录，17:88 是第 1~3 章）
MINI_BLOCKS = 88
# 通用模式对照只需要"跑一遍老路"，块数与结论无关——用 20 块省掉一次全程嵌入
GENERIC_BLOCKS = 20
EXPECT_CHAPTERS = 3

CHECKS: list[tuple[str, bool, str]] = []
EARLY_FAILS: list[str] = []
DETAIL: list[str] = []


def add(name: str, ok: bool, detail: str = "") -> None:
    """记一项检查；失败**立刻**留痕（走 stderr，见卡面「失败项立刻打 stdout」）。"""
    ok = bool(ok)
    CHECKS.append((name, ok, detail))
    if not ok:
        msg = f"C0088-TB-FAIL 检查未过: {name}  {detail}"
        EARLY_FAILS.append(msg)
        print(msg, file=sys.stderr, flush=True)


def note(msg: str) -> None:
    DETAIL.append(msg)


def real_counts() -> dict:
    """真库只读行数（`mode=ro`）：default 库 + 当前活跃学科库。

    直接复用 `c0088_snapshot.real_db_counts`——两个脚本数"真库"的口径必须
    一模一样，否则一处说"没变"、另一处说"变了"，验收就没法看。
    """
    return _real_db_counts()


def load_mini_blocks(n: int = MINI_BLOCKS):
    """把 doc#91 的前 N 个真实块变成 `extract.Block` 列表。"""
    from core.extract import Block, ExtractResult
    from core import textbook as _tb

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw = data["blocks"][:n]
    blocks = []
    for b in raw:
        ref = str(b.get("ref") or "")
        blocks.append(Block(
            text=b["text"],
            page=_tb._page_start(ref),          # "p.216-218" -> 216
            para=int(b.get("ord") or 0),
            kind="ocr",                          # 与 doc#91 库里的 kind 一致
            level=0,
        ))
    res = ExtractResult(blocks=blocks, meta={"pages": 452, "title": "",
                                             "scanned_pages": []})
    return data, res


def main() -> int:
    # ---- 隔离：环境变量必须在 import core 之前落地（config 导入即固化）----
    for sub in ("index", "raw", "logs"):
        p = WORK / sub
        if p.exists():
            assert p.parent == WORK and WORK.name.startswith("_tmp_c0088"), p
            shutil.rmtree(p)
    raw = WORK / "raw" / "药理"
    raw.mkdir(parents=True, exist_ok=True)
    carrier = raw / "药理学 第6版 刘晓东.pdf"
    try:
        shutil.copy2(CARRIER, carrier)
    except OSError as e:      # 诊断：把两边路径与存在性打出来，别只报 WinError
        print(f"DBG copy2 失败 {type(e).__name__}: {e}")
        print(f"DBG src={CARRIER} exists={CARRIER.exists()}")
        print(f"DBG dst={carrier} parent={carrier.parent} "
              f"parent.exists={carrier.parent.exists()} "
              f"work.exists={WORK.exists()}")
        raise
    os.environ.update({
        "KB_RAW": str(WORK / "raw"),
        "KB_INDEX": str(WORK / "index"),
        "KB_LOG": str(WORK / "logs"),
        "KB_SETTINGS_FILE": str(WORK / "settings.json"),
        "KB_TESTING": "1",
        "OUTLINE_ANALYSIS": "0",
    })

    from core import config, db, extract, figures, textbook
    config.DB_PATH = WORK / "index" / "study.db"
    if Path(config.DB_PATH).resolve() == REAL_DB.resolve():
        print("C0088-TB-FAIL 隔离失败：DB_PATH 没改掉")
        return 2
    from core.chunk import Chunk
    from core.ingest import ingest_file

    before = real_counts()
    conn = db.connect(immediate=True)
    db.init_db(conn)

    data, res = load_mini_blocks()
    book = str(data.get("title") or "教材")
    note(f"块源：{FIXTURE.name} doc#{data.get('doc_id')}《{book}》前 {MINI_BLOCKS} 块")

    # ================= 1) 教材模式：一次跑通 + 三支探针 =================
    calls: dict = {"classify_role": 0, "max_calls": []}
    real_classify = db.classify_role

    def spy_classify(title, path=""):
        calls["classify_role"] += 1
        return real_classify(title, path)

    real_ingest_figs = figures.ingest_document_figures

    def spy_figs(conn_, doc_id, path, **kw):
        calls["max_calls"].append(kw.get("max_calls", "<未传>"))
        return {}                       # 不渲染、不调模型、不花钱

    db.classify_role = spy_classify
    figures.ingest_document_figures = spy_figs
    extract.extract = lambda p: res        # 用真实 doc#91 块替代"读 PDF"
    try:
        r_tb = ingest_file(conn, carrier, force=True, with_ocr=False,
                           with_figures=True, mode="textbook")
    finally:
        db.classify_role = real_classify
        figures.ingest_document_figures = real_ingest_figs

    tb = r_tb.get("textbook") or {}
    note(f"教材模式返回：status={r_tb.get('status')} chunks={r_tb.get('chunks')} "
         f"textbook={ {k: v for k, v in tb.items() if k != 'heading_path_kinds'} }")
    add("教材模式入库成功（status/chunks）",
        r_tb.get("status") in ("added", "updated") and int(r_tb.get("chunks") or 0) > 0,
        f"status={r_tb.get('status')} chunks={r_tb.get('chunks')} reason={r_tb.get('reason')}")
    add("走教材专线：返回里带 textbook 回执且章数=3、未降级",
        bool(tb) and tb.get("chapters") == EXPECT_CHAPTERS
        and tb.get("fallback") is False,
        f"textbook={tb}")
    add("教材模式：db.classify_role **未被调用**（role 是显式声明）",
        calls["classify_role"] == 0, f"被调用 {calls['classify_role']} 次")
    add("教材模式：识图调用显式传了 max_calls=配置值",
        calls["max_calls"] and calls["max_calls"][0] == int(config.FIGURE_MAX_CALLS_PER_DOC),
        f"实际收到 {calls['max_calls']}，配置值 {int(config.FIGURE_MAX_CALLS_PER_DOC)}")

    cols = []      # noqa: F841  （保留 PRAGMA 调用点，便于出问题时对照表结构）
    row = conn.execute("SELECT * FROM documents WHERE path LIKE ?",
                       ("%" + carrier.name,)).fetchone()
    if row is None:
        add("库里有这条 document", False, "没找到")
        return finish(before)
    extra = json.loads(row["extra"] or "{}")
    add("documents.role == 'textbook'（显式声明）",
        row["role"] == "textbook", f"role={row['role']!r}")
    add("documents.extra.chunk_scheme == 'textbook-v1'",
        extra.get("chunk_scheme") == "textbook-v1", f"extra={extra}")
    tb_extra_keys = set(extra)
    add("extra「只增键」：老键（来自 extract.meta）一个不少 + 新增 chunk_scheme",
        tb_extra_keys >= {"pages", "title", "scanned_pages", "chunk_scheme"},
        f"extra 键集合 {sorted(tb_extra_keys)}")
    tb_role = row["role"]
    tb_doc_id = int(row["id"])

    # 面包屑形态 + 跨章块（用库里的行重建 Chunk 复核）
    rows = list(conn.execute(
        "SELECT ord, text, heading_path, source_ref FROM chunks WHERE doc_id=? "
        "ORDER BY ord", (tb_doc_id,)))
    hps = [r["heading_path"] or "" for r in rows]
    kinds = sorted(set(hps))
    note(f"面包屑取值 {len(kinds)} 种，前 6 种：{kinds[:6]}")
    bad_hp = [h for h in hps if not (h == book or h.startswith(book + " > 第"))]
    add("每个 heading_path 都是「书名 > 第X章 章名」（或仅书名=非正文）",
        not bad_hp, f"不合形态 {len(bad_hp)} 个，例如 {bad_hp[:2]}")
    nums = sorted({int(m.group(1)) for h in hps
                   for m in [re.search(r"> 第(\d+)章", h)] if m})
    add(f"块里出现的章号恰为 1..{EXPECT_CHAPTERS}", nums == list(range(1, EXPECT_CHAPTERS + 1)),
        f"实际 {nums}")

    chapters = textbook.detect_chapters(res.blocks, book_title=book)
    rebuilt = [Chunk(text=r["text"], heading_path=r["heading_path"],
                     source_ref=r["source_ref"], ord=r["ord"]) for r in rows]
    cross = textbook.cross_chapter_chunks(rebuilt, chapters, strict_hp=True)
    add("跨章块 = 0（从库里的块重建后复核）", len(cross) == 0,
        f"跨章块 {len(cross)} 个")
    add("返回里的 cross_chapter_chunks 也报 0",
        tb.get("cross_chapter_chunks") == 0, f"{tb.get('cross_chapter_chunks')}")
    add("章的切分跨了多页（不是把整本塞进一块）",
        len(rows) > EXPECT_CHAPTERS and len({r["source_ref"] for r in rows}) > 3,
        f"块数 {len(rows)}，出处种类 {len({r['source_ref'] for r in rows})}")

    # ================= 2) 教材模式重复入库：内容未变 ⇒ 跳过，不重建块 =====
    #
    # `force=False` 且内容哈希未变 ⇒ `ingest.py` 的"内容未变"分支直接
    # skipped（一行都不写库，也就不重新嵌入 88 块）。这既是幂等证据，
    # 也证明重复走教材专线不会把 role/标记弄丢——所以这里**逐字段比
    # documents 那一行**，而不是只比一个字段。
    KEYS = ("role", "extra", "n_chunks", "n_chars", "status", "content_hash")
    snap1 = tuple(row[k] for k in KEYS)
    r_tb2 = ingest_file(conn, carrier, force=False, with_ocr=False,
                        with_figures=False, mode="textbook")
    row2 = conn.execute("SELECT * FROM documents WHERE id=?", (tb_doc_id,)).fetchone()
    snap2 = tuple(row2[k] for k in KEYS)
    add("教材模式重复入库：走「内容未变」跳过、不重建块",
        r_tb2.get("status") == "skipped", f"status={r_tb2.get('status')}")
    add("教材模式重复入库：documents 行逐字段未变（role/extra/n_chunks/…）",
        snap1 == snap2, f"{snap1} -> {snap2}")
    add("教材模式重复入库：chunk_scheme 标记仍在（没被重复入库抹掉）",
        json.loads(row2["extra"] or "{}").get("chunk_scheme") == "textbook-v1")

    # ================= 3) 通用模式同一份文件：role 仍走启发式 =============
    # 用 20 块的小块集：这一段要证的是"通用路径一行为也没变"，
    # 与块数无关，没必要再全程嵌入一次（验收槽只有 25 秒）。
    _data_g, res_g = load_mini_blocks(GENERIC_BLOCKS)
    calls2: dict = {"classify_role": 0, "max_calls": []}
    db.classify_role = lambda title, path="": (calls2.__setitem__(
        "classify_role", calls2["classify_role"] + 1) or "courseware")
    figures.ingest_document_figures = (
        lambda conn_, doc_id, path, **kw: (calls2["max_calls"].append(
            kw.get("max_calls", "<未传>")) or {}))
    extract.extract = lambda p: res_g
    try:
        r_gen = ingest_file(conn, carrier, force=True, with_ocr=False,
                            with_figures=True)      # mode 不传 = 默认 ""
    finally:
        db.classify_role = real_classify
        figures.ingest_document_figures = real_ingest_figs
    gen = conn.execute("SELECT role, extra FROM documents WHERE id=?",
                       (tb_doc_id,)).fetchone()
    gen_extra = json.loads(gen["extra"] or "{}")
    add("通用模式（mode 不传）：classify_role 被调用过（老路没变）",
        calls2["classify_role"] > 0, f"被调用 {calls2['classify_role']} 次")
    add("通用模式：role 取自启发式返回值（courseware）",
        gen["role"] == "courseware", f"role={gen['role']!r}")
    add("通用模式：识图 max_calls 收到 None（= 改造前「不传」的等价物）",
        calls2["max_calls"] == [None], f"实际收到 {calls2['max_calls']}")
    add("通用模式：没有写入 chunk_scheme 标记",
        "chunk_scheme" not in gen_extra, f"extra={gen_extra}")
    note(f"通用模式返回：chunks={r_gen.get('chunks')} status={r_gen.get('status')} "
         f"（无 textbook 键：{'textbook' not in r_gen}）")
    add("通用模式返回里**没有** textbook 键（只增不改）", "textbook" not in r_gen)

    # ================= 4) 未知 mode：不做静默降级 =================
    r_bad = ingest_file(conn, carrier, force=True, with_ocr=False,
                        with_figures=False, mode="textbok")   # 故意打错
    add("未知 mode 直接失败，不静默走老路",
        r_bad.get("status") == "failed" and "mode" in str(r_bad.get("reason")),
        f"{r_bad.get('status')} {r_bad.get('reason')}")

    conn.close()
    return finish(before, tb_diag=tb, role=tb_role)


def finish(before: dict, tb_diag: dict | None = None, role: str = "") -> int:
    """收尾：真库行数自查 + 打印结论区（结论行在前 6 行内）。"""
    after = real_counts()
    add("真库 documents/chunks 行数在本次验证前后不变",
        before == after, f"{before} -> {after}")

    ok = all(c[1] for c in CHECKS)
    log = ROOT / "logs" / "c0088" / "textbook_verify_detail.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(
        [f"[{'OK ' if o else 'FAIL'}] {n}  {d}" for n, o, d in CHECKS]
        + ["", "--- 明细 ---"] + DETAIL), encoding="utf-8")

    print(f"C0088-TB-{'PASS' if ok else 'FAIL'}  教材专线端到端（{len(CHECKS)} 项检查，"
          f"{sum(1 for c in CHECKS if c[1])} 项通过）")
    if tb_diag:
        print(f"1) mode=textbook：{tb_diag.get('chapters')} 章 / "
              f"跨章块 {tb_diag.get('cross_chapter_chunks')} / "
              f"fallback={tb_diag.get('fallback')} / "
              f"chunk_scheme={tb_diag.get('chunk_scheme')}")
    print(f"2) documents.role={role or '?'}（显式声明：教材模式下 classify_role "
          f"零调用）+ extra 新键 chunk_scheme")
    print("3) heading_path = 「书名 > 第X章 章名」；跨章块 0（库里重建复核）")
    print("4) max_calls 显式传参：教材模式=配置值，通用模式=None（与改造前等价）")
    print(f"5) 真库行数 {before} -> {after}；耗时 {time.time() - T0:.1f} s；"
          f"明细 {log}")
    for line in EARLY_FAILS:
        print(line)
    for n, o, d in CHECKS:
        if not o:
            print(f"   失败项：{n}  {d}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
