# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
"""课件分享包 CLI（卡 C-0079）：导出 / 导入 / 看包 / 列可导出的课件。

    python tools\\share.py list
    python tools\\share.py export --doc-id 5
    python tools\\share.py export --doc-id 5 --with-original --with-pages
    python tools\\share.py inspect --zip share\\xxx-share.zip
    python tools\\share.py import  --zip share\\xxx-share.zip
    python tools\\share.py import  --zip <包> --duplicate --json

默认值（大管家 2026 拍板）：**不带原课件、不带识图页图** ⇒ 包最小（实测 1~6 MB/份），
这正是"摊平 API 费用"的主路径。两个开关按需勾选：

    --with-original   带原课件文件（对方想重新入库/归档时用；接收方不必再要一次源文件）
    --with-pages      带识图页图（对方没装 Office、想看原页图时用；体积最大的一项）

导入侧**不调用任何模型**：`core.share` 不导入 embed/kp_build，也不联网。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _force_utf8_stdio() -> None:
    """把 stdout/stderr 固定成 UTF-8。

    为什么必须先做这一步：本机控制台默认编码是 GBK（cp936），而本脚本要打印
    `⚠`、`→`、`¥`、`「」` 这类字符——它们在 GBK 下**编码失败**，会让命令
    以 `UnicodeEncodeError` 崩掉（退出码 1）；中文也会变成 `���`，
    使"输出里必须出现的子串"这类自动验收失效。

    固定成 UTF-8 后，无论调用方是 cmd、pwsh 还是把输出重定向/管道捕获，
    字节都是同一份 UTF-8。`errors="replace"` 是兜底：极端情况下宁可打问号，
    也不让整条命令失败。
    """
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                          # noqa: BLE001
            pass                                   # 被替换成非文本流时只能作罢


_force_utf8_stdio()

from core import config, db, share  # noqa: E402


def _fmt_bytes(n: int) -> str:
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= div:
            return "%.1f %s" % (n / div, unit)
    return "%d B" % n


def _progress(stage: str, done: int, total: int) -> None:
    if total <= 0:
        return
    if stage in ("chunks", "embeddings", "pages"):
        pct = done * 100 // max(total, 1)
        sys.stdout.write("\r  %-10s %d/%d (%d%%)   " % (stage, done, total, pct))
        if done >= total:
            sys.stdout.write("\n")
        sys.stdout.flush()


def _load_db(args) -> None:
    """把 --db/--log-dir/--raw-dir 覆盖到 config 上（临时库验证就靠它）。"""
    if args.db:
        config.DB_PATH = Path(args.db)
    if getattr(args, "log_dir", None):
        config.LOG_DIR = Path(args.log_dir)
    if getattr(args, "raw_dir", None):
        config.RAW_DIR = Path(args.raw_dir)


def cmd_list(args) -> int:
    _load_db(args)
    conn = db.connect(config.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT d.id,d.title,d.kind,d.path,d.n_chunks,"
            "(SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS n_all,"
            "(SELECT COUNT(*) FROM figure_desc f WHERE f.doc_id=d.id) AS n_fig "
            "FROM documents d ORDER BY d.id").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        kp = config.LOG_DIR / "knowledge" / ("%d%s" % (r["id"], share.KP_SUFFIX))
        tm = config.LOG_DIR / "knowledge" / ("%d%s" % (r["id"], share.TERMS_SUFFIX))
        out.append({"id": int(r["id"]), "title": r["title"], "kind": r["kind"],
                    "path": r["path"], "n_chunks": int(r["n_chunks"]),
                    "n_blocks": int(r["n_all"]), "n_figures": int(r["n_fig"]),
                    "has_kp": kp.is_file(), "has_terms": tm.is_file()})
    if args.json:
        print(json.dumps({"db": str(config.DB_PATH), "docs": out},
                         ensure_ascii=False, indent=2))
        return 0
    print("库：%s（%d 份资料）" % (config.DB_PATH, len(out)))
    print("  %-4s %-30s %-6s %6s %6s %6s  %s"
          % ("id", "标题", "类型", "正文块", "全部块", "识图页", "产物"))
    for d in out:
        prod = ("知识点总结" if d["has_kp"] else "") + ("+名词解释" if d["has_terms"] else "")
        print("  %-4d %-30s %-6s %6d %6d %6d  %s"
              % (d["id"], (d["title"] or "")[:30], d["kind"], d["n_chunks"],
                 d["n_blocks"], d["n_figures"], prod or "（无）"))
    print("\n导出：python tools\\share.py export --doc-id <id> [--with-original] [--with-pages]")
    return 0


def cmd_export(args) -> int:
    _load_db(args)
    res = share.export_doc(
        args.doc_id, args.out, db_path=config.DB_PATH, with_original=args.with_original,
        with_pages=args.with_pages, exported_by=args.by, note=args.note,
        log_dir=config.LOG_DIR, raw_dir=config.RAW_DIR, root=config.ROOT,
        progress=None if args.quiet else _progress)
    if args.json:
        m = dict(res)
        m.pop("manifest", None)
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0
    print("导出完成：%s" % res["zip"])
    print("  体积      ：%s" % _fmt_bytes(res["bytes"]))
    print("  文本块    ：%d 块 / 向量 %d 条（%s dim=%d）"
          % (res["counts"]["chunks"], res["counts"]["embeddings"],
             res["embedding"]["model"], res["embedding"]["dim"]))
    print("  识图描述  ：%d 页" % res["counts"]["figure_desc"])
    print("  带原文件  ：%s%s" % ("是" if res["includes"]["original"] else "否",
                                 "（%s）" % _fmt_bytes(res["original_bytes"])
                                 if res["includes"]["original"] else ""))
    print("  带识图页图：%s%s" % ("是" if res["includes"]["pages"] else "否",
                                 "（%s）" % _fmt_bytes(res["pages_bytes"])
                                 if res["includes"]["pages"] else ""))
    for w in res["warnings"]:
        print("  ⚠ %s" % w)
    print("\n对方导入：python tools\\share.py import --zip \"%s\"" % res["zip"])
    return 0


def cmd_inspect(args) -> int:
    res = share.inspect_package(args.zip, deep=not args.fast)
    if args.json:
        r = dict(res)
        if isinstance(r.get("doc"), dict):
            r["doc"] = {k: v for k, v in r["doc"].items() if k != "extra"}
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    m = res["manifest"]
    src = m.get("source") or {}
    emb = m.get("embedding") or {}
    print("分享包：%s（%s）" % (res["zip"], _fmt_bytes(res["bytes"])))
    print("  格式    ：%s v%s，导出时间 %s，导出者 %s"
          % (m.get("format"), m.get("format_version"), m.get("created_at"),
             m.get("exported_by") or "未署名"))
    print("  来源    ：%s · doc#%s · %s（%s）"
          % (src.get("subject"), src.get("doc_id"), src.get("title"), src.get("kind")))
    print("  向量    ：%s dim=%s" % (emb.get("model"), emb.get("dim")))
    c = res.get("counts") or {}
    print("  内容    ：块 %s / 向量 %s / 识图页 %s / 页图 %s / 原文件 %s"
          % (c.get("total"), (res.get("embeddings") or {}).get("total"),
             (c.get("total") and m.get("counts", {}).get("figure_desc")),
             res["includes"]["pages"] and "有" or "无",
             res["includes"]["original"] and "有" or "无"))
    for k in res.get("knowledge") or []:
        print("  产物    ：%s（%d 条）" % (k["name"], k["n_points"]))
    print("  结构校验：通过（%d 个条目，sha256 全部核对）" % res["n_entries"])
    print("\n导入：python tools\\share.py import --zip \"%s\"" % res["zip"])
    return 0


def cmd_import(args) -> int:
    _load_db(args)
    res = share.import_package(
        args.zip, db_path=config.DB_PATH, log_dir=config.LOG_DIR,
        raw_dir=config.RAW_DIR, root=config.ROOT, duplicate=args.duplicate,
        dry_run=args.dry_run, progress=None if args.quiet else _progress)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    st = res["status"]
    if st == "skipped":
        print("已跳过：%s" % res["reason"])
        print("  本库已有 doc#%s（%s）" % (res["doc_id"], res["existing_title"]))
        print("  想再导一份副本：加 --duplicate")
        return 0
    if st == "dry_run":
        print("预演通过（未写任何数据）：%s" % res["title"])
        print("  将写入：块 %s / 向量 %s / 识图页 %s / 产物 %d 个 / 页图 %d 张"
              % (res["counts"].get("chunks"), res["counts"].get("embeddings"),
                 res["counts"].get("figure_desc"), len(res["knowledge"]), res["pages"]))
        if res.get("conflict"):
            print("  ⚠ 本库已有同一份课件 doc#%s（%s）：不加 --duplicate 时会被跳过"
                  % (res["conflict"]["doc_id"], res["conflict"]["existing_title"]))
        return 0
    print("导入完成：doc#%d %s" % (res["doc_id"], res["title"]))
    print("  写入      ：块 %d / 向量 %d / 识图页 %d / 产物 %d 个 / 页图 %d 张 / 原文件 %s"
          % (res["counts"]["chunks"], res["counts"]["embeddings"],
             res["counts"]["figure_desc"], res["counts"]["knowledge"],
             res["counts"]["pages"], res["file"] or "未包含（用占位路径）"))
    print("  来源      ：%s · doc#%s（%s）"
          % (res["source"].get("subject"), res["source"].get("doc_id"),
             res["source"].get("title")))
    for k in res["knowledge"]:
        print("  知识点产物：%s → %s（%d 条，重映射引用 %d 处）"
              % (k["file"], k["new_file"], k["n_points"], k["remapped_refs"]))
    f = res.get("fts") or {}
    print("  全文索引  ：%s" % ("已生效（探测命中 %d 条）" % f.get("hits", 0)
                               if f.get("ok") else "未启用或未命中：%s" % f.get("reason", "")))
    print("  模型调用  ：%d 次，花费 CNY %.2f" % (res["model_calls"], res["cost_cny"]))
    for w in res["warnings"]:
        print("  ⚠ %s" % w)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="share", description="课件分享包：导出（入库数据+向量+知识点）/导入（零调用重建）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _paths(p, with_out: bool = False):
        p.add_argument("--db", default="", help="库文件（默认当前学科）")
        p.add_argument("--log-dir", default="", help="学科 logs 目录（临时库验证用）")
        p.add_argument("--raw-dir", default="", help="学科 raw 目录（临时库验证用）")
        p.add_argument("--json", action="store_true", help="机器可读输出")
        if with_out:
            p.add_argument("--quiet", action="store_true", help="不打印进度")

    p = sub.add_parser("list", help="列出库里可导出的课件")
    _paths(p)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("export", help="导出分享包")
    _paths(p, with_out=True)
    p.add_argument("--doc-id", type=int, required=True, help="课件的 documents.id")
    p.add_argument("--out", default="", help="输出 zip 路径（默认 share\\<标题>-share.zip）")
    p.add_argument("--with-original", action="store_true", help="带上原课件文件")
    p.add_argument("--with-pages", action="store_true", help="带上识图页图")
    p.add_argument("--by", default="", help="导出者署名（写进包，便于追溯）")
    p.add_argument("--note", default="", help="备注（写进包）")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="导入分享包（零模型调用）")
    _paths(p, with_out=True)
    p.add_argument("--zip", required=True, help="分享包路径")
    p.add_argument("--duplicate", action="store_true",
                   help="本库已有同一份课件时也再导一份副本")
    p.add_argument("--dry-run", action="store_true", help="只校验不写库")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("inspect", help="看包里有什么（不动数据库）")
    p.add_argument("--zip", required=True, help="分享包路径")
    p.add_argument("--fast", action="store_true", help="只读 manifest，不逐行校验")
    p.add_argument("--json", action="store_true", help="机器可读输出")
    p.set_defaults(func=cmd_inspect)

    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except share.ShareError as e:
        print("✗ %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
