r"""按**最终入库管线**重导入资料（**默认教材除外**，见 `--textbooks`）。

## 这条管线就是"以后入库就用它"

对每份资料依次做四件事，全部由 `core.ingest.ingest_file` 编排：

```
1. 提取文字（pdf 走版面感知；扫描/截图页补 OCR，**表格按二维还原**）
2. 切块（语义边界 + 标题面包屑 + 出处区间 + 分组边界）
3. 嵌入（本地 ONNX bge-small-zh-v1.5，int8）
4. 整页识图（图片理解）→ 作为"图示说明"块入库
```

第 4 步以前要手工跑三个脚本（渲染 → 逐页描述 → 写库），
现在并进了 `figures.ingest_document_figures`，由 `ingest_file` 调用。
`.env` 的 `FIGURE_DESC=0` 可整体关掉识图。

## 为什么每份资料单独提交

整批跑要好几个小时（35 份 / 1836 页，其中约 800 页要调视觉模型）。
按资料提交 + 逐份打印结果，好处是**中断可续**：
重跑时已完成的资料会因内容哈希未变而被跳过
（除非加 `--force`），识图也会按图片哈希复用已有描述、不重复计费。

用法:
    python tools/reimport_all.py --dry-run
    python tools/reimport_all.py                 # 非教材全部
    python tools/reimport_all.py --kinds pptx    # 只做 pptx
    python tools/reimport_all.py --only 66 71    # 只做指定 doc id
    python tools/reimport_all.py --textbooks     # 只做教材（走教材专线）

## 教材为什么要单独一条（卡 C-0089）

教材走的是另一条切块路线：`ingest_file(mode="textbook")` 先按书眉 + 章号
单调认章（`core/textbook.py`，零模型调用），再**逐章**切块，章之间不串。
这条路线由资料栏的「教材入库」按钮负责，本脚本用 `--textbooks` 接上同一
条路线（`mode="textbook"`）。

⚠ 两边**不能互相覆盖**：通用管线（不带 mode）会把教材按章做好的切块用旧
切块重跑掉。所以本脚本默认把教材**整批排除**，而且排除的判据不只是
`role`，还包括教材专线打在 `documents.extra.chunk_scheme='textbook-v1'`
上的产物标记——判据必须是"这份资料的切块是哪条管线做的"。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, db, ingest  # noqa: E402


def _is_textbook_row(r) -> bool:
    """这份资料的产物是不是"教材"（该走教材专线）。

    两条判据取**或**：
      · `role == 'textbook'`——教学角色是教材；
      · `extra.chunk_scheme == 'textbook-v1'`——切块**确实是教材专线做的**。

    为什么要第二条：防覆盖不能建立在"role 一定会被写对"这个假设上。
    role 是启发式算出来的（改标题、改目录都可能变），而 `chunk_scheme`
    是教材专线显式打上的产物标记——"这份的块是按章切的"这件事，只有它说了算。
    老教材（专线之前用通用管线入的）没有标记，靠 role 也能被 `--textbooks` 收进来。
    """
    if (r["role"] or "") == "textbook":
        return True
    try:
        extra = json.loads(r["extra"] or "{}")
    except (TypeError, ValueError):
        return False
    return (isinstance(extra, dict)
            and extra.get("chunk_scheme") == ingest.TEXTBOOK_CHUNK_SCHEME)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="内容未变也重建（提取/切块逻辑改动后用）")
    ap.add_argument("--kinds", default="", help="只做这些格式，逗号分隔")
    ap.add_argument("--only", default="", help="只做这些 doc id，逗号分隔")
    ap.add_argument("--no-figures", action="store_true", help="本次不做识图")
    ap.add_argument("--textbooks", action="store_true",
                    help="只做教材（走教材专线 mode=textbook；默认整批跳过教材）")
    ap.add_argument("--figures", action="store_true",
                    help="教材专线也做视觉识图（按页计费；默认关）")
    args = ap.parse_args()

    conn = db.connect()
    rows = conn.execute(
        "SELECT id, path, kind, role, title, extra FROM documents "
        "ORDER BY kind, id").fetchall()

    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}
    only = {int(x) for x in args.only.split(",") if x.strip()}
    targets = []
    n_tb_skipped = 0
    for r in rows:
        is_tb = _is_textbook_row(r)
        if is_tb != bool(args.textbooks):
            if is_tb:
                n_tb_skipped += 1
            continue
        if kinds and r["kind"] not in kinds:
            continue
        if only and int(r["id"]) not in only:
            continue
        p = Path(r["path"])
        if not p.is_absolute():
            p = config.ROOT / p
        if not p.exists():
            print(f"  跳过（文件不存在）: {r['path']}")
            continue
        targets.append((int(r["id"]), p, r["kind"], r["title"]))

    n_before = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    print(f"目标 {len(targets)} 份"
          f"（{'教材专线 mode=textbook' if args.textbooks else '教材除外'}）")
    if n_tb_skipped:
        print(f"  另有 {n_tb_skipped} 份教材产物被跳过——要重跑请加 --textbooks；"
              f"通用管线会用旧切块覆盖按章切块")
    print(f"当前库: {n_before} 块")
    # 识图的实际口径：教材专线默认**关**（按页计费，要叠得显式 --figures），
    # 非教材路径仍然只看 --no-figures（一行没改）。打印必须与实际一致——
    # 否则人会以为"教材这次也识图了"。
    fig_on = bool(args.figures) if args.textbooks else not args.no_figures
    print(f"识图: {'开' if fig_on else '关'}；"
          f"force={'是' if args.force else '否'}")
    for did, p, kind, title in targets:
        print(f"  doc#{did:<3} {kind:<5} {title[:44]}")
    if args.dry_run:
        print("（dry-run，未执行）")
        conn.close()
        return

    bak = config.INDEX_DIR / f"study.db.bak-before-reimport-{int(time.time())}"
    conn.close()
    shutil.copy2(config.DB_PATH, bak)
    print(f"\n已备份 -> {bak}\n")

    conn = db.connect()
    t_all = time.perf_counter()
    tot_cost = tot_fig = tot_chunks = 0
    rows_out: list[tuple] = []
    for i, (did, p, kind, title) in enumerate(targets, 1):
        t0 = time.perf_counter()
        print(f"[{i}/{len(targets)}] doc#{did} {title[:44]}", flush=True)
        r = ingest.ingest_file(
            conn, p, force=args.force,
            # 教材专线默认**不做视觉识图**：那是按页计费的（¥0.0105/页，
            # 452 页 ≈ ¥4.7），与资料栏「教材入库」的口径一致。要叠识图
            # 得显式 `--textbooks --figures`。非教材路径一个字没改。
            with_figures=(bool(args.figures) if args.textbooks
                          else not args.no_figures),
            verbose=True,
            mode=(ingest.TEXTBOOK_MODE if args.textbooks else ""))
        conn.commit()
        el = time.perf_counter() - t0
        fs = r.get("figures") or {}
        cost = float(fs.get("cost_cny") or 0.0)
        tot_cost += cost
        tot_fig += int(fs.get("chunks") or 0)
        tot_chunks += int(r.get("chunks") or 0)
        rows_out.append((did, title, r.get("status"), r.get("chunks"),
                         fs))
        print(f"    -> {r.get('status')}: 正文 {r.get('chunks')} 块，"
              f"识图 {fs.get('described', 0)} 新 / {fs.get('reused', 0)} 复用"
              f" / {fs.get('no_info', 0)} 无信息 → {fs.get('chunks', 0)} 块，"
              f"¥{cost:.4f}，{el:.0f}s", flush=True)
        tb = r.get("textbook") or {}
        if tb:
            # 教材专线的自述（卡 C-0088 起的返回字段）：章数、跨章块、是否降级。
            # 重跑教材时这几个数就是"这次到底切得怎么样"的判据。
            print(f"    教材专线：{tb.get('chapters')} 章 / 跨章块 "
                  f"{tb.get('cross_chapter_chunks')} / "
                  f"fallback={tb.get('fallback')} / "
                  f"{tb.get('chunk_scheme')}", flush=True)
        if fs.get("empty_retried"):
            print(f"    ↻ 空输出重试 {fs['empty_retried']} 页"
                  + (f"，仍为空 {fs['still_empty']}"
                     if fs.get("still_empty") else "，全部修好"), flush=True)
        for w in (r.get("warnings") or [])[:3]:
            print(f"    !! {w}", flush=True)

    el_all = time.perf_counter() - t_all
    n_after = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    n_fig = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE source_ref LIKE 'p.% 图'"
    ).fetchone()["n"]
    no_vec = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks c WHERE NOT EXISTS "
        "(SELECT 1 FROM embeddings e WHERE e.chunk_id=c.id)").fetchone()["n"]

    print("\n" + "=" * 88)
    print(f"完成 {len(targets)} 份，耗时 {el_all/60:.1f} 分钟")
    print(f"正文块合计 {tot_chunks}；识图块合计 {tot_fig}")
    print(f"识图成本合计 ¥{tot_cost:.4f}")
    print(f"库: {n_before} → {n_after} 块（其中识图块 {n_fig}）")
    print(f"缺向量的块: {no_vec}" + ("  ✅" if no_vec == 0 else "  ⚠"))

    # 失败与告警汇总
    bad = [x for x in rows_out if x[2] in ("failed", "partial")]
    if bad:
        print(f"\n⚠ 有 {len(bad)} 份状态异常:")
        for did, title, st, n, fs in bad:
            print(f"  doc#{did} {title[:40]}: {st} "
                  f"{fs.get('errors', [''])[:1]}")
    conn.close()


if __name__ == "__main__":
    main()