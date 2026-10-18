# -*- coding: utf-8 -*-
"""C-0090 验收脚本：静态核对本卡的全部交付物，**不重跑、不写库、不起服务**。

用法（绝对路径，不依赖 cwd）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\verify_c0090_textbook.py

输出第一行是结论（含字面标记 C0090-PASS / C0090-FAIL），后面才是明细。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(r"G:\study_agent")
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0090")
DB = ROOT / "index" / "study.db"
DOC_ID = 91

checks: list[tuple[str, bool, str]] = []


def add(name: str, ok: bool, note: str = "") -> bool:
    checks.append((name, bool(ok), note))
    return bool(ok)


def jload(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                       # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def main() -> int:
    # ---------- 1. 备份 ----------
    latest = ROOT / "backup" / "c0090_latest.txt"
    bdir = Path(latest.read_text(encoding="utf-8").strip()) if latest.exists() else None
    add("备份目录存在", bool(bdir and bdir.is_dir()), str(bdir))
    man = jload(bdir / "MANIFEST.json") if bdir else {}
    add("MANIFEST.json 可读", "study_db" in man,
        f"{(bdir / 'MANIFEST.json') if bdir else '?'}")
    add("备份的 study.db 与源同尺寸",
        bool(man.get("study_db")) and
        int(man["study_db"].get("src_size", 0)) == int(man["study_db"].get("dst_size", -1)),
        f"{man.get('study_db', {}).get('src_size')} -> "
        f"{man.get('study_db', {}).get('dst_size')}")
    before_cnt = man.get("before", {})
    add("重跑前四个计数实测且全为 0",
        all(int(before_cnt.get(k, -1)) == 0
            for k in ("knowledge_points", "kp_sources", "mistakes", "reviews")),
        json.dumps({k: before_cnt.get(k) for k in
                    ("knowledge_points", "kp_sources", "mistakes", "reviews")},
                   ensure_ascii=False))
    add("回滚命令已给出", bool(man.get("rollback", {}).get("db")),
        str(man.get("rollback", {}).get("db"))[:110])
    add("logs\\knowledge 已备份",
        int(man.get("logs_knowledge", {}).get("files", 0)) > 0,
        f"{man.get('logs_knowledge', {}).get('files')} 个文件")

    # ---------- 2. 重跑回执 ----------
    rec = jload(CARD / "rerun_receipt.json")
    add("重跑回执存在且无 error",
        bool(rec) and not rec.get("error") and not rec.get("_error"),
        str(rec.get("error") or rec.get("_error") or rec.get("summary") or "")[:120])
    docs = rec.get("docs") or []
    d0 = docs[0] if docs else {}
    add("回执列出识别到的章（章数 > 0）",
        int(d0.get("chapters_n") or 0) > 0 and len(d0.get("chapters") or []) > 0,
        f"chapters_n={d0.get('chapters_n')} 列出的分区={len(d0.get('chapters') or [])}")
    add("回执未降级（fallback=false）", d0.get("fallback") is False,
        f"fallback={d0.get('fallback')}")
    add("回执自报跨章块 = 0", d0.get("cross_chapter_chunks") == 0,
        f"cross_chapter_chunks={d0.get('cross_chapter_chunks')}")
    add("回执模式为教材专线", "教材专线" in str(rec.get("summary") or "")
        or d0.get("book_title") not in (None, ""),
        f"book_title={d0.get('book_title')!r} 块数 "
        f"{d0.get('chunks_before')} -> {d0.get('chunks_after')}")

    # ---------- 3. 同尺对照 ----------
    cmp_ = jload(CARD / "compare.json")
    cb = (cmp_.get("before") or {}).get("cross_loose")
    ca_s = (cmp_.get("after") or {}).get("cross_strict")
    ca_l = (cmp_.get("after") or {}).get("cross_loose")
    ch_n = (cmp_.get("chapters") or {}).get("n")
    add("★ 跨章块：改造前 > 0", isinstance(cb, int) and cb > 0, f"before.cross_loose={cb}")
    add("★ 跨章块：改造后 = 0（strict_hp）", ca_s == 0, f"after.cross_strict={ca_s}")
    cb_lb = (cmp_.get("before") or {}).get("cross_loose_body")
    ca_lb = (cmp_.get("after") or {}).get("cross_loose_body")
    add("★ 跨章块：改造后正文块 = 0（宽松口径；无章级面包屑的目录/编委块不计）",
        ca_lb == 0,
        f"正文跨章 {cb_lb} -> {ca_lb}；宽松口径合计 前 "
        f"{(cmp_.get('before') or {}).get('cross_loose')} → 后 {ca_l}")
    hb = (cmp_.get("before") or {}).get("heading_path_kinds")
    ha = (cmp_.get("after") or {}).get("heading_path_kinds")
    add("heading_path 种类：改造前 = 1", hb == 1, f"before={hb}")
    add("heading_path 种类：改造后 ≥ 章数", isinstance(ha, int) and isinstance(ch_n, int)
        and ha >= ch_n, f"after={ha} 章数={ch_n}")
    samples = cmp_.get("samples") or []
    ok_split = [s for s in samples if s.get("split_ok")]
    add("★ 真实样本：≥2 处「前跨章、后分开」", len(ok_split) >= 2,
        f"配对成功 {len(ok_split)}/{len(samples)}")
    tab = cmp_.get("chapter_table") or []
    body_tab = [t for t in tab if int(t.get("chapter_no") or 0) > 0]
    add("每章块数一览（章号+章名+块数）≥ 30 章", len(body_tab) >= 30,
        f"正文章 {len(body_tab)} 行，合计块数 "
        f"{sum(int(t.get('chunks') or 0) for t in body_tab)}")
    add("一览表的章号连续无缺（1..N）",
        len(body_tab) >= 30 and
        [int(t["chapter_no"]) for t in body_tab] ==
        list(range(1, len(body_tab) + 1)),
        f"{len(body_tab)} 行：{[int(t['chapter_no']) for t in body_tab][:6]}...")
    front = cmp_.get("front_after") or []
    front_ok = bool(front) and all(
        "章" not in (f.get("heading_path") or "").split(">")[-1] for f in front)
    add("目录/版权/编者页已脱离正文（独立前置分区，面包屑不带章号）",
        front_ok,
        f"无章级面包屑的块 {len(front)} 个，例："
        f"{front[0].get('heading_path') if front else '?'}")

    # ---------- 4. 防覆盖实测 ----------
    g = jload(CARD / "guard_result.json")
    add("★ 防覆盖实测：通用单份入口被拒绝", bool(g.get("ingest_blocked")),
        f"error 提示含『教材入库』字样={g.get('ingest_hint_has_howto')}")
    add("★ 防覆盖实测：批量入口挡住教材", int(g.get("batch_blocked_n") or 0) >= 1
        and not g.get("batch_plan_contains_doc91"),
        f"挡住 {g.get('batch_blocked_n')} 份；计划里含 doc#91="
        f"{g.get('batch_plan_contains_doc91')}")
    add("★ 防覆盖实测：库指纹一字未变",
        bool(g.get("unchanged")) and all(g["unchanged"].values()),
        json.dumps(g.get("unchanged"), ensure_ascii=False))
    add("防覆盖实测总判 ok", g.get("ok") is True, f"ok={g.get('ok')}")

    # ---------- 5. 库现状 ----------
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        d = conn.execute("SELECT role, status, n_chunks, extra FROM documents"
                         " WHERE id=?", (DOC_ID,)).fetchone()
        ex = json.loads(d["extra"] or "{}") if d else {}
        add("doc#91 带教材专线标记 chunk_scheme=textbook-v1",
            ex.get("chunk_scheme") == "textbook-v1", f"{ex.get('chunk_scheme')!r}")
        add("doc#91 role=textbook", (d["role"] if d else "") == "textbook",
            f"{d['role'] if d else '?'}")
        n_hp = int(conn.execute(
            "SELECT COUNT(DISTINCT heading_path) FROM chunks WHERE doc_id=?",
            (DOC_ID,)).fetchone()[0])
        n_ck = int(conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id=?",
                                (DOC_ID,)).fetchone()[0])
        add("库里 doc#91 heading_path 种数 ≥ 章数", n_hp >= (ch_n or 999),
            f"{n_hp} 种 / {ch_n} 章")
        add("库里 doc#91 块数 = 回执的 chunks_after",
            n_ck == int(d0.get("chunks_after") or -1),
            f"库 {n_ck} vs 回执 {d0.get('chunks_after')}")
        conn.close()
    except sqlite3.Error as e:
        add("读默认库", False, f"{type(e).__name__}: {e}")

    # ---------- 6. 检索对照 ----------
    sb = jload(CARD / "search_before.json")
    sa = jload(CARD / "search_after.json")
    add("检索对照：改造前基线存在", len(sb.get("queries") or []) >= 2,
        f"{len(sb.get('queries') or [])} 个问题，跨章命中 "
        f"{sb.get('cross_chapter_hits_total')}")
    add("检索对照：改造后快照存在", len(sa.get("queries") or []) >= 2,
        f"{len(sa.get('queries') or [])} 个问题，跨章命中 "
        f"{sa.get('cross_chapter_hits_total')}")
    add("★ 检索串味：改造后 doc#91 跨章命中 = 0",
        sa.get("cross_chapter_hits_total") == 0,
        f"before={sb.get('cross_chapter_hits_total')} "
        f"after={sa.get('cross_chapter_hits_total')}")

    # ---------- 7. 其他学科库未被触碰 ----------
    bad = []
    for name, fp in (man.get("other_subjects") or {}).items():
        p = Path(fp.get("path", ""))
        if not p.exists():
            bad.append(f"{name}:缺失")
            continue
        st = p.stat()
        if int(st.st_size) != int(fp.get("size", -1)):
            bad.append(f"{name}:size {fp.get('size')}->{st.st_size}")
        if abs(float(st.st_mtime) - float(fp.get("mtime", 0))) > 0.5:
            bad.append(f"{name}:mtime 变了")
        try:
            c2 = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            n = int(c2.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            c2.close()
            if n != int(fp["chunks"]):
                bad.append(f"{name}:chunks {fp['chunks']}->{n}")
        except sqlite3.Error as e:
            bad.append(f"{name}:{type(e).__name__}")
    n_other = len(man.get("other_subjects") or {})
    add("其他学科库未被触碰（size/mtime/块数）", n_other >= 5 and not bad,
        f"{n_other} 个库" + ("；异常：" + ";".join(bad) if bad else "全部与备份指纹一致"))

    # ---------- 8. 报告 ----------
    rep = CARD / "教材重跑对照.md"
    add("对照报告存在且非空", rep.exists() and rep.stat().st_size > 2000,
        f"{rep} {rep.stat().st_size if rep.exists() else 0} 字节")

    # ---------- 输出（结论行在前，明细同时落日志）----------
    failed = [c for c in checks if not c[1]]
    mark = "C0090-PASS" if not failed else "C0090-FAIL"
    buf = [f"{mark} 教材专线重跑 doc#91 效果对照 —— "
           f"{len(checks) - len(failed)}/{len(checks)} 项通过"]
    for name, ok, note in checks:
        buf.append(f"  [{'PASS' if ok else 'FAIL'}] {name}｜{note}")
    if failed:
        buf.append("")
        buf.append("未通过：")
        for name, _, note in failed:
            buf.append(f"  - {name}｜{note}")
    buf.append("")
    buf.append(f"(at {time.strftime('%Y-%m-%d %H:%M:%S')}, "
               f"耗时 {time.perf_counter() - t_start:.1f}s)")
    report = "\n".join(buf)
    print(report)
    try:
        log = ROOT / "logs" / "c0090_verify.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(report + "\n", encoding="utf-8")
    except OSError:
        pass
    return 0 if not failed else 1


if __name__ == "__main__":
    t_start = time.perf_counter()
    raise SystemExit(main())
