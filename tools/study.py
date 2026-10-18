"""study_agent 命令行入口。

这是模型与人类共用的唯一入口：
- skill 路径：模型通过 shell 调 `study.py <cmd>`，当前会话立即生效
- tool 路径：DSH 原生插件包一层，调用同一套后端
因此所有命令都支持 --json，方便程序化消费；默认输出为人可读格式。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
            # 被重定向/被管道捕获（exe 被启动器或验收脚本拉起时正是这种，也正是
            # GBK 编不出 U+00A5 的现场）→ 一律 UTF-8：任何字符都编得出。
            if not _st.isatty():
                _st.reconfigure(encoding="utf-8", errors="replace")
            else:
                # 交互终端 → 保持系统给的编码，只把 errors 从 strict 换成 replace，
                # 免得真落在 GBK 代码页的窗口里把中文写成乱码（源码模式本来就带 -X utf8）。
                _st.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass          # 老 Python / 特殊流：宁可不动，也不能在这里抛
    # [重建] 原函数体在 for 头之后被截断。以上按函数上方那段长注释的设计补齐：
    # 两种环境都照顾，且**绝不再因为一个字符编不出来而抛异常**（否则业务状态
    # 会被污染成 failed —— 那正是卡 C-0078 的真实缺陷）。


# ---------------- 依赖 core ----------------
# [重建] 原文件的导入区整段丢失（`cmd_init` 直接用 `config` / `db`，`cmd_search`
# 直接用 `search_mod`，说明原来就是从这里导入的）。按各命令的实际引用补：
#   config/db/ingest/search_mod/study —— 覆盖 init/check/ingest/search/show/docs/
#   stats/review/kp/mistake/note/read 全部命令。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, db, ingest, study          # noqa: E402
from core import progress as progress_mod           # noqa: E402（教材专线的进度生命周期）
from core import search as search_mod               # noqa: E402

# 示例里展示的解释器路径（epilog 用）
PY = "venv\\Scripts\\python.exe"


def out(payload, as_json: bool, text: str = "") -> None:
    """统一出口：JSON 给程序消费，文本给人看（所有命令都支持 --json）。"""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    elif text:
        print(text)


# ---------------- 命令实现 ----------------
def cmd_init(args) -> int:
    config.ensure_dirs()
    conn = db.connect(args.db)
    info = db.init_db(conn)
    payload = {
        "db": str(config.DB_PATH),
        "root": str(config.ROOT),
        "fts5": info["fts5"],
        "dirs": [str(d) for d in (config.RAW_DIR, config.NOTES_DIR,
                                  config.INDEX_DIR, config.MODEL_DIR)],
    }
    out(payload, args.json_flag,
        f"已初始化\n  数据库: {payload['db']}\n  FTS5 全文索引: "
        f"{'启用' if info['fts5'] else '不可用（降级为 LIKE）'}\n"
        f"  资料目录: {config.RAW_DIR}")
    conn.close()
    return 0


def cmd_check(args) -> int:
    """自检：模型、数据库、依赖是否就绪。"""
    result = {"root": str(config.ROOT), "python": sys.version.split()[0], "checks": []}

    def add(name: str, ok: bool, detail: str) -> None:
        result["checks"].append({"name": name, "ok": ok, "detail": detail})

    for mod in ("numpy", "onnxruntime", "tokenizers", "pymupdf", "PIL"):
        try:
            m = __import__(mod)
            add(f"依赖 {mod}", True, getattr(m, "__version__", "ok"))
        except ImportError:
            add(f"依赖 {mod}", False, "未安装")

    try:
        import docx  # noqa: F401
        add("依赖 python-docx", True, "ok")
    except ImportError:
        add("依赖 python-docx", False, "未安装（Word 课件无法解析）")
    try:
        import pptx  # noqa: F401
        add("依赖 python-pptx", True, "ok")
    except ImportError:
        add("依赖 python-pptx", False, "未安装（PPT 课件无法解析）")
    try:
        import rapidocr_onnxruntime  # noqa: F401
        add("OCR rapidocr", True, "ok")
    except ImportError:
        add("OCR rapidocr", False, "未安装（图片与扫描版 PDF 无法解析）")

    model = config.MODEL_DIR / config.EMBED_ONNX_FILE
    add("嵌入模型文件", model.exists(), str(model))
    add("tokenizer.json", (config.MODEL_DIR / "tokenizer.json").exists(),
        str(config.MODEL_DIR / "tokenizer.json"))

    if model.exists() and (config.MODEL_DIR / "tokenizer.json").exists():
        try:
            from core.embed import get_embedder
            emb = get_embedder()
            v = emb.embed_query("自检")
            add("嵌入推理", True, f"维度 {v.shape[0]}，模长 {float((v**2).sum())**0.5:.4f}")
        except Exception as e:
            add("嵌入推理", False, f"{type(e).__name__}: {e}")

    try:
        conn = db.connect(args.db)
        info = db.init_db(conn)
        n = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        add("数据库", True, f"{config.DB_PATH}，{n} 个文档，FTS5={'开' if info['fts5'] else '关'}")
        conn.close()
    except Exception as e:
        add("数据库", False, f"{type(e).__name__}: {e}")

    ok_all = all(c["ok"] for c in result["checks"])
    if args.json_flag:
        out(result, True)
    else:
        for c in result["checks"]:
            print(f"  [{'OK  ' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
        print(f"\n总体: {'全部就绪' if ok_all else '有项目未就绪'}")
    return 0 if ok_all else 1


def _ingest_textbook(conn, targets, *, force: bool = False,
                     with_ocr: bool = True, verbose: bool = False):
    """教材专线：逐份走 `ingest_file(mode="textbook")`（**按章切块**）。

    ## 为什么不复用 `ingest_paths`

    那条批量入口**不认识 mode** —— 它对每份文件都按课件口径切块。所以这里
    按**同一套口径**自己走一遍循环：进度生命周期（`progress.begin/file_done/end`）、
    `ingest_runs` 落库、报告结构（`IngestReport`）都与 `ingest_paths` 逐项一致，
    差别只有一处：每份都显式带 `mode=TEXTBOOK_MODE`。

    ## 这条线做什么（卡 C-0088 的 mode 分流）

    按章切块（`core/textbook.py`：书眉认章 + 章内二次切块）+
    显式 `role='textbook'` + `extra.chunk_scheme='textbook-v1'` +
    跳过全局结构分析（那一步要调模型花钱）+ 识图调用上限取配置值。
    OCR 仍是**本地免费**的那一套（rapidocr，2~16 秒/页，不调视觉大模型）。

    ⚠ 教材是长任务（452 页实测约 2 小时）且**串行**：一次一份，
    不要拿它去跑一批零散课件（那些走默认的课件口径）。
    """
    files = ingest.iter_files([Path(t) for t in targets])
    rep = ingest.IngestReport()
    started = db.now()
    if verbose:
        print(f"教材专线：共 {len(files)} 个文件"
              f"（按章切块 · 本地 OCR · 不调视觉模型）", flush=True)
    for i, f in enumerate(files, start=1):
        if verbose:
            print(f"[{i}/{len(files)}] {f.name}", flush=True)
        progress_mod.begin(f.name, files_total=len(files))
        try:
            r = ingest.ingest_file(conn, f, force=force, with_ocr=with_ocr,
                                   verbose=verbose, mode=ingest.TEXTBOOK_MODE)
        except Exception as e:                            # noqa: BLE001
            # 入库抛异常也要收尾，否则看板上的进度条会永远停在"进行中"。
            progress_mod.end("failed", error=f"{type(e).__name__}: {e}")
            raise
        progress_mod.file_done(i)
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
            cp = db.classify_corpus(r.get("path") or "")
            rep.added_by_corpus[cp] = rep.added_by_corpus.get(cp, 0) + 1
        if verbose and st != "skipped":
            tb = r.get("textbook") or {}
            note = r.get("reason") or f"{r.get('chunks', 0)} 块"
            if tb:
                note += (f"｜章 {tb.get('chapters')} 个"
                         f"（降级={tb.get('fallback')}）"
                         f"、跨章块 {tb.get('cross_chapter_chunks')}")
            print(f"    -> {st}: {note}", flush=True)
            for w in r.get("warnings") or []:
                print(f"    !! {w}", flush=True)

    conn.execute(
        """
        INSERT INTO ingest_runs(started_at, ended_at, added, updated, skipped,
                                failed, detail)
        VALUES (?,?,?,?,?,?,?)
        """,
        (started, db.now(), rep.added, rep.updated, rep.skipped,
         rep.failed + rep.needs_ocr,
         json.dumps(rep.details, ensure_ascii=False, default=str)),
    )
    conn.commit()
    progress_mod.end("done" if not rep.failed else "failed",
                     error=(f"{rep.failed} 个文件失败" if rep.failed else ""))
    return rep


def cmd_ingest(args) -> int:
    config.ensure_dirs()
    conn = db.connect(args.db)
    db.init_db(conn)
    targets = [Path(p) for p in args.paths] if args.paths else [config.RAW_DIR]
    missing = [str(t) for t in targets if not t.exists()]
    if missing:
        print(f"路径不存在: {missing}", file=sys.stderr)
        conn.close()
        return 2
    # 教材专线（卡 C-0122 恢复 CLI 入口）：`--mode textbook` 时逐份走
    # `ingest_file(mode="textbook")` —— 按章切块，且显式声明 role='textbook'。
    # 默认仍是原来的课件口径，**不传 `--mode` 的行为逐字不变**。
    textbook = (args.mode == "textbook")
    if not args.json_flag:
        print(f"摄入目标: {', '.join(str(t) for t in targets)}")
        print(f"OCR: {'开' if not args.no_ocr else '关'}"
              + ("　口径: 教材专线（按章切块）" if textbook else ""))
        print()
    if textbook:
        rep = _ingest_textbook(conn, targets, force=args.force,
                               with_ocr=not args.no_ocr,
                               verbose=not args.json_flag)
    else:
        rep = ingest.ingest_paths(conn, targets, force=args.force,
                                  with_ocr=not args.no_ocr,
                                  verbose=not args.json_flag)
    if args.json_flag:
        payload = {"summary": rep.summary(), "added": rep.added,
                   "updated": rep.updated,
                   "skipped": rep.skipped, "failed": rep.failed,
                   "needs_ocr": rep.needs_ocr, "unsupported": rep.unsupported,
                   "chunks": rep.chunks, "details": rep.details}
        if textbook:
            # 教材专线的回执（只增键，且只在 `--mode textbook` 出现）：
            # 章数 / 是否降级 / 跨章块数，供验收与脚本直接取用。
            payload["mode"] = ingest.TEXTBOOK_MODE
            payload["textbook"] = [d.get("textbook") or {}
                                   for d in rep.details]
        out(payload, True)
    else:
        print(f"\n{rep.summary()}")
        problems = [d for d in rep.details if d["status"] in ("failed", "partial", "unsupported")]
        if problems:
            print("\n需要注意:")
            for d in problems[:20]:
                print(f"  - {d['path']}: {d.get('reason', '')}")
    conn.close()
    # 只有硬失败才算失败；待 OCR 是可恢复状态，不影响退出码
    return 0 if rep.failed == 0 else 1


def cmd_search(args) -> int:
    conn = db.connect(args.db)
    db.init_db(conn)
    # 默认 corpus="main"（教材+课件）：命令行是助手最常用的入口，
    # 默认行为必须与 MCP / 看板一致——不显式指定就看不到 aux。
    hits = search_mod.search(conn, args.query, top_k=args.k,
                             max_per_doc=args.max_per_doc, mode=args.mode,
                             corpus=args.corpus)
    # [重建] 原函数体在调用中间被截断（缺 keyword 回退 + 两种输出 + 收尾）。
    # 依 SKILL.md 的输出约定重写：每条给 `cite`（出处）与 `why`（命中原因），
    # 并给出 chunk_id 供 `show <chunk_id>` 回看上下文。
    if not hits and args.mode == "hybrid":
        # 混合没命中时退回关键词：口语提问的向量路可能空手，
        # 而术语/公式靠 trigram 反而能精确命中（见 SKILL.md 的三层检索）。
        hits = search_mod.search(conn, args.query, top_k=args.k,
                                 max_per_doc=args.max_per_doc, mode="keyword",
                                 corpus=args.corpus)
    if args.json_flag:
        out({"query": args.query, "count": len(hits),
             "hits": [h.to_dict() for h in hits]}, True)
    elif not hits:
        print("没有检索到相关内容。")
        print("  可以试试：换说法 / --mode keyword / 先 `docs --outline <doc_id>` 看结构")
    else:
        print(f"命中 {len(hits)} 条（query={args.query!r} mode={args.mode} "
              f"corpus={args.corpus}）\n")
        for i, h in enumerate(hits, 1):
            why = ("  [" + " · ".join(h.why) + "]") if h.why else ""
            print(f"[{i}] {h.cite()}{why}")
            print(f"    chunk_id={h.chunk_id}  doc_id={h.doc_id}  ord={h.ord}")
            body = " ".join((h.text or "").split())
            print("    " + (body[:160] + " …" if len(body) > 160 else body))
            print()
        print("看上下文：study.py show <chunk_id> --before 1 --after 1")
    conn.close()
    return 0


def cmd_show(args) -> int:
    """看某个切块及其上下文（SKILL.md：检索结果只给切块，看上下文用 show）。"""
    conn = db.connect(args.db)
    db.init_db(conn)
    row = conn.execute("SELECT * FROM chunks WHERE id=?", (int(args.chunk_id),)).fetchone()
    if not row:
        print(f"没有这个切块: {args.chunk_id}", file=sys.stderr)
        conn.close()
        return 2
    did = int(row["doc_id"])
    doc = conn.execute("SELECT title, path, kind FROM documents WHERE id=?", (did,)).fetchone()
    title = (doc["title"] if doc else "") or f"doc#{did}"
    lo = int(row["ord"]) - max(0, int(args.before))
    hi = int(row["ord"]) + max(0, int(args.after))
    rows = conn.execute(
        "SELECT id, ord, heading_path, source_ref, text FROM chunks "
        "WHERE doc_id=? AND ord BETWEEN ? AND ? ORDER BY ord", (did, lo, hi)
    ).fetchall()
    # [重建] 原函数体整段丢失（只有注册）。以下按 SKILL.md 的 show 语义补：
    # 连相邻块一起给出，并回溯相邻章节（面包屑取自各块自身的 heading_path）。
    if args.json_flag:
        out({"doc_id": did, "title": title, "center": int(args.chunk_id),
             "blocks": [{"chunk_id": int(r["id"]), "ord": int(r["ord"]),
                         "heading": r["heading_path"] or "", "ref": r["source_ref"] or "",
                         "text": r["text"]} for r in rows]}, True)
    else:
        print(f"《{title}》 · doc_id={did} · 切块 {int(args.chunk_id)}"
              f"（含前后各 {args.before}/{args.after} 块）\n")
        for r in rows:
            mark = " ★" if int(r["id"]) == int(args.chunk_id) else "  "
            head = r["heading_path"] or ""
            if head and title and head.startswith(title):
                head = head[len(title):].lstrip(" >")
            print(f"{mark} [chunk {int(r['id'])} · ord {int(r['ord'])}] "
                  f"{r['source_ref'] or ''} {head}")
            print("     " + (r["text"] or "").strip().replace("\n", "\n     "))
            print()
    conn.close()
    return 0


def cmd_docs(args) -> int:
    """列出文档；给 --outline 则输出该文档大纲（最省 token 的"先看目录"）。"""
    conn = db.connect(args.db)
    db.init_db(conn)
    if args.outline:
        did = int(args.outline)
        row = conn.execute("SELECT title, path, kind FROM documents WHERE id=?",
                           (did,)).fetchone()
        if not row:
            print(f"没有这个文档: {did}", file=sys.stderr)
            conn.close()
            return 2
        title = row["title"] or row["path"]
        ol = search_mod.doc_outline(conn, did)
        if args.json_flag:
            out({"doc_id": did, "title": title, "kind": row["kind"],
                 "outline": ol}, True)
        else:
            print(f"《{title}》 · doc_id={did} · {row['kind']} · {len(ol)} 个标题段\n")
            for e in ol:
                print(f"  {e['heading']}")
                print(f"      {e['n_chunks']} 块 · {e['chars']} 字 · {e['ref_range']}"
                      f" · first_chunk={e['first_chunk']}")
                if e.get("preview"):
                    print(f"      {e['preview'][:100]}")
        conn.close()
        return 0
    rows = conn.execute(
        "SELECT id, title, path, kind, status, n_chunks, n_chars, role, corpus "
        "FROM documents ORDER BY id").fetchall()
    if args.json_flag:
        out({"count": len(rows),
             "docs": [{"doc_id": int(r["id"]), "title": r["title"] or r["path"],
                       "kind": r["kind"], "status": r["status"],
                       "n_chunks": int(r["n_chunks"] or 0), "n_chars": int(r["n_chars"] or 0),
                       "role": r["role"] or "", "corpus": r["corpus"] or ""} for r in rows]}, True)
    else:
        print(f"已入库 {len(rows)} 份资料\n")
        for r in rows:
            flag = "" if (r["status"] or "ok") == "ok" else f"  ⚠{r['status']}"
            print(f"  doc#{int(r['id']):<4} [{r['kind'] or '?':<5}] "
                  f"{int(r['n_chunks'] or 0):>4} 块  {int(r['n_chars'] or 0):>7} 字  "
                  f"{(r['role'] or ''):<10} {r['title'] or r['path']}{flag}")
        print("\n看大纲：study.py docs --outline <doc_id>")
    conn.close()
    return 0


def cmd_stats(args) -> int:
    """学习看板数据（文档/知识点/复习/错题/薄弱项）。"""
    conn = db.connect(args.db)
    db.init_db(conn)
    data = study.dashboard(conn)
    if args.json_flag:
        out(data, True)
    else:
        d, k = data["documents"], data["knowledge"]
        print(f"资料：{d['total']} 份 · {d['chunks']} 块 · {d['chars']} 字")
        if d.get("by_kind"):
            print("       " + " · ".join(f"{x['kind']}×{x['n']}" for x in d["by_kind"]))
        print(f"知识点：{k['total']} 个 · 待复习 {k['due']} · 未学过 {k['new']}"
              f" · 今日已复习 {k['reviews_today']}")
        m = data.get("mistakes") or {}
        print(f"错题：{m.get('total', 0)} 道（未解决 {m.get('open', 0)}）")
        print(f"笔记：{data.get('notes', 0)} 篇 · FTS5={'开' if data.get('fts5') else '关'}")
        weak = data.get("weakest") or []
        if weak:
            print("\n最薄弱的知识点：")
            for w in weak[:8]:
                print(f"  {w['mastery']:.2f}  {w['name']}"
                      f"（间隔 {w['interval_days']:.0f} 天 · 遗忘 {w['lapses']} 次）")
    conn.close()
    return 0


def cmd_review(args) -> int:
    """复习队列 / 记录一次复习（SKILL.md：`review` 取队列，`review <kp_id> --grade`）。"""
    conn = db.connect(args.db)
    db.init_db(conn)
    if args.kp_id:
        if args.grade is None:
            print("需要 --grade 0..5（>=3 视为通过）", file=sys.stderr)
            conn.close()
            return 2
        st = study.record_review(conn, int(args.kp_id), int(args.grade),
                                 question=args.question or "", answer=args.answer or "",
                                 note=args.note or "")
        if args.json_flag:
            out(st, True)
        else:
            print(f"已记录：kp#{st['kp_id']} 评分 {st['grade']}"
                  f"（{'通过' if st['grade'] >= 3 else '未通过'}）")
            print(f"  下次间隔 {st['interval_days']:.0f} 天 · ease {st['ease']}"
                  f" · 累计复习 {st['reps']} 次 · 遗忘 {st['lapses']} 次")
        conn.close()
        return 0
    queue = study.review_queue(conn, limit=int(args.limit), subject=args.subject or "")
    if args.json_flag:
        out({"count": len(queue), "queue": queue}, True)
    elif not queue:
        print("今天没有到期的复习 🎉")
    else:
        print(f"今日到期 {len(queue)} 个知识点\n")
        for q in queue:
            print(f"  kp#{q['id']:<4} {q['name']}  （掌握度 {q['mastery']:.2f}"
                  f" · 间隔 {q['interval_days']:.0f} 天 · 复习 {q['reps']} 次）")
        print("\n记录：study.py review <kp_id> --grade 0..5")
    conn.close()
    return 0


def cmd_kp(args) -> int:
    """知识点管理：add / link / list。"""
    conn = db.connect(args.db)
    db.init_db(conn)
    action = getattr(args, "kp_action", "")
    if action == "add":
        chunks: list[int] = []
        for x in (args.chunks or "").split(","):
            x = x.strip()
            if x.isdigit():
                chunks.append(int(x))
        kp_id = study.upsert_kp(conn, args.name, summary=args.summary or "",
                               subject=args.subject or "", chunk_ids=chunks)
        if args.json_flag:
            out({"kp_id": kp_id, "name": args.name, "chunks": chunks}, True)
        else:
            print(f"已写入知识点 kp#{kp_id}：{args.name}")
            if chunks:
                print(f"  关联出处 {len(chunks)} 个切块：{chunks}")
    elif action == "link":
        dst = study.link_kp(conn, args.name, args.target, kind=args.kind)
        if args.json_flag:
            out({"src": args.name, "dst": args.target, "kind": args.kind,
                 "dst_id": dst}, True)
        else:
            print(f"已建立依赖：{args.name} --{args.kind}--> {args.target}")
    elif action == "list":
        rows = conn.execute(
            "SELECT id, name, subject, mastery, reps, lapses, interval_days, due_at "
            "FROM knowledge_points ORDER BY COALESCE(mastery,0) DESC, id").fetchall()
        if args.json_flag:
            out({"count": len(rows),
                 "kps": [{"id": int(r["id"]), "name": r["name"],
                          "subject": r["subject"] or "",
                          "mastery": float(r["mastery"] or 0),
                          "reps": int(r["reps"] or 0), "lapses": int(r["lapses"] or 0),
                          "interval_days": float(r["interval_days"] or 0),
                          "due_at": r["due_at"]} for r in rows]}, True)
        else:
            print(f"知识点 {len(rows)} 个\n")
            for r in rows:
                print(f"  kp#{int(r['id']):<4} {float(r['mastery'] or 0):.2f}  "
                      f"{r['name']}  （{r['subject'] or '未分类'} · "
                      f"复习 {int(r['reps'] or 0)} 次 · 间隔 {float(r['interval_days'] or 0):.0f} 天）")
    else:
        print("用法：kp add|link|list", file=sys.stderr)
        conn.close()
        return 2
    conn.close()
    return 0


def cmd_mistake(args) -> int:
    """错题本：add / stats。"""
    conn = db.connect(args.db)
    db.init_db(conn)
    action = getattr(args, "mistake_action", "")
    if action == "add":
        mid = study.add_mistake(
            conn, question=args.question, my_answer=args.my or "",
            right_answer=args.right or "", reason=args.reason or "",
            kp_id=args.kp_id, chunk_id=args.chunk_id,
            missing_prereq=args.missing_prereq or "")
        if args.json_flag:
            out({"mistake_id": mid, "kp_id": args.kp_id, "reason": args.reason}, True)
        else:
            print(f"已记录错题 #{mid}")
            if args.kp_id:
                print(f"  已自动降级知识点 kp#{args.kp_id} 并提前复习（下次到期：现在）")
            if args.reason:
                print(f"  错因：{args.reason}")
    elif action == "stats":
        st = study.mistake_stats(conn)
        if args.json_flag:
            out(st, True)
        else:
            print(f"错题 {st['total']} 道（未解决 {st['open']}）")
            for x in st.get("by_reason") or []:
                print(f"  {x['reason']}：{x['n']}")
            rep = st.get("repeated_kps") or []
            if rep:
                print("\n反复出错的知识点：")
                for x in rep:
                    print(f"  {x['n']} 次  {x['name']}")
    else:
        print("用法：mistake add|stats", file=sys.stderr)
        conn.close()
        return 2
    conn.close()
    return 0


def cmd_note(args) -> int:
    """笔记回写：save / list（抗上下文刷新的关键机制）。"""
    conn = db.connect(args.db)
    db.init_db(conn)
    action = getattr(args, "note_action", "")
    if action == "save":
        body = args.body or ""
        if args.body_file:
            try:
                body = Path(args.body_file).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                print(f"读不到 --body-file：{e}", file=sys.stderr)
                conn.close()
                return 2
        if not body.strip():
            print("笔记正文为空（用 --body 或 --body-file）", file=sys.stderr)
            conn.close()
            return 2
        tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        path = study.save_note(conn, args.title, body, tags=tags,
                               source_refs=list(args.refs or []), kind=args.kind)
        if args.json_flag:
            out({"path": path, "title": args.title, "kind": args.kind,
                 "tags": tags}, True)
        else:
            print(f"已保存笔记：{path}")
    elif action == "list":
        notes = study.list_notes()
        if args.json_flag:
            out({"count": len(notes), "notes": notes}, True)
        else:
            print(f"笔记 {len(notes)} 篇\n")
            for n in notes:
                tag = ("  " + ",".join(n["tags"])) if n.get("tags") else ""
                print(f"  {n['title']}{tag}  ·  {n['file']}  ({n['chars']} 字)")
            print("\n读原文：study.py note read \"<名字>\"")
    else:
        print("用法：note save|list|read", file=sys.stderr)
        conn.close()
        return 2
    conn.close()
    return 0


def cmd_read(args) -> int:
    """读一篇笔记原文（模糊匹配；只给"人自己写的内容"）。"""
    r = study.read_note(args.name)
    if r.get("error"):
        print(r["error"], file=sys.stderr)
        return 2
    if args.json_flag:
        out(r, True)
    else:
        print(f"# {r['title']}   （{r['file']}）")
        if r.get("matches", 1) > 1:
            print(f"（匹配到 {r['matches']} 篇，显示第一篇）")
        print()
        print(r.get("body") or r.get("text") or "")
    return 0


# ---------------- 参数解析 ----------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="study.py",
        description="study_agent 学习助手：本地知识库摄入、带出处检索、复习调度",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""示例:
  {PY} tools\\study.py init
  {PY} tools\\study.py ingest                      # 摄入 raw\\ 下所有资料
  {PY} tools\\study.py ingest "raw\\教材.pdf"        # 摄入单个文件
  {PY} tools\\study.py search "什么是梯度消失" -k 5
  {PY} tools\\study.py search "梯度消失" --json
  {PY} tools\\study.py show 42                     # 看切块 42 及上下文
  {PY} tools\\study.py docs --outline 3            # 看文档 3 的大纲
  {PY} tools\\study.py stats
  {PY} tools\\study.py kp add "梯度消失" --chunks 42,43 --subject 深度学习
  {PY} tools\\study.py review                      # 今日到期复习队列
  {PY} tools\\study.py review 1 --grade 4          # 给 kp_id=1 打评分
  {PY} tools\\study.py mistake add --question "..." --right "..." --reason 概念混淆
  {PY} tools\\study.py note save --title "第三章要点" --body "..." --refs "教材.pdf p.12"
  {PY} tools\\study.py note read "第三章要点"
""",
    )
    p.add_argument("--db", default=None, help="覆盖数据库路径")
    p.add_argument("--json", dest="json_flag", action="store_true",
                   help="输出 JSON（供程序消费）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="初始化数据库与目录")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("check", help="自检环境、依赖与模型")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("ingest", help="摄入文件或目录（默认 raw\\）")
    sp.add_argument("paths", nargs="*", help="文件或目录路径")
    sp.add_argument("--force", action="store_true", help="忽略内容哈希，强制重建")
    sp.add_argument("--no-ocr", action="store_true", help="跳过 OCR")
    # 卡 C-0122：教材专线的 CLI 入口。默认 courseware = 改造前的原路
    # （逐页/按长度切块），textbook = `ingest_file(mode="textbook")`。
    sp.add_argument("--mode", choices=("courseware", "textbook"),
                    default="courseware",
                    help="入库口径：courseware（默认，按课件切块）| "
                         "textbook（教材专线：按章切块 + role='textbook'，"
                         "走本地免费 OCR）")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("search", help="混合检索（向量 + 关键词）")
    sp.add_argument("query", help="查询语句")
    sp.add_argument("-k", type=int, default=8, help="返回条数，默认 8")
    sp.add_argument("--mode", choices=["hybrid", "vector", "keyword"], default="hybrid")
    sp.add_argument("--corpus", choices=["main", "aux", "all"], default="main",
                    help="检索哪个库：main=教材+课件（默认），"
                         "aux=试题+资料笔记，all=两者都查")
    sp.add_argument("--max-per-doc", type=int, default=4, help="单文档最多返回块数")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("show", help="查看切块及其上下文")
    sp.add_argument("chunk_id", type=int)
    sp.add_argument("--before", type=int, default=1)
    sp.add_argument("--after", type=int, default=1)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("docs", help="列出已入库文档，或看某文档大纲")
    sp.add_argument("--outline", type=int, metavar="DOC_ID", help="输出该文档大纲")
    sp.set_defaults(func=cmd_docs)

    sp = sub.add_parser("stats", help="学习看板数据")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("review", help="复习队列 / 记录一次复习")
    sp.add_argument("kp_id", nargs="?", type=int, help="知识点 id（配合 --grade）")
    sp.add_argument("--grade", type=int, choices=range(0, 6), help="0..5，>=3 视为通过")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--subject", default="")
    sp.add_argument("--question", default="")
    sp.add_argument("--answer", default="")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("kp", help="知识点管理")
    ksub = sp.add_subparsers(dest="kp_action", required=True)
    k = ksub.add_parser("add", help="新增/更新知识点")
    k.add_argument("name")
    k.add_argument("--summary", default="")
    k.add_argument("--subject", default="")
    k.add_argument("--chunks", default="", help="逗号分隔的 chunk_id，作为出处")
    k.set_defaults(func=cmd_kp)
    k = ksub.add_parser("link", help="建立前置依赖")
    k.add_argument("name", help="前置知识点")
    k.add_argument("target", help="依赖它的知识点")
    k.add_argument("--kind", default="prereq", choices=["prereq", "related"])
    k.set_defaults(func=cmd_kp)
    k = ksub.add_parser("list", help="列出知识点")
    k.set_defaults(func=cmd_kp)

    sp = sub.add_parser("mistake", help="错题本")
    msub = sp.add_subparsers(dest="mistake_action", required=True)
    m = msub.add_parser("add", help="记录一道错题")
    m.add_argument("--question", required=True)
    m.add_argument("--my", default="")
    m.add_argument("--right", default="")
    m.add_argument("--reason", default="", help=" / ".join(study.REASON_TAGS))
    m.add_argument("--kp-id", type=int, default=None)
    m.add_argument("--chunk-id", type=int, default=None)
    m.add_argument("--missing-prereq", default="")
    m.set_defaults(func=cmd_mistake)
    m = msub.add_parser("stats", help="错题统计")
    m.set_defaults(func=cmd_mistake)

    sp = sub.add_parser("note", help="笔记回写（抗上下文刷新的关键机制）")
    nsub = sp.add_subparsers(dest="note_action", required=True)
    n = nsub.add_parser("save", help="保存笔记到 notes\\")
    n.add_argument("--title", required=True)
    n.add_argument("--body", default="")
    n.add_argument("--body-file", default="")
    n.add_argument("--tags", default="")
    n.add_argument("--refs", nargs="*", default=[], help="出处列表")
    n.add_argument("--kind", default="note", choices=["note", "summary", "mistake", "cheatsheet"])
    n.set_defaults(func=cmd_note)
    n = nsub.add_parser("list", help="列出笔记")
    n.set_defaults(func=cmd_note)
    n = nsub.add_parser("read", help="读笔记原文")
    n.add_argument("name")
    n.set_defaults(func=cmd_read)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except Exception as e:
        if getattr(args, "json_flag", False):
            print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        else:
            print(f"错误: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())