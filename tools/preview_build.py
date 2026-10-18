# -*- coding: utf-8 -*-
"""预习总结的独立命令行入口（卡 C-0094：预习模块 ②）。

**独立管线分区**：模型 / 端点 / 密钥 / 推理档位都自己一份（设置中心「AI 预习总结」），
与「AI 知识点总结」互不影响。整章**只做 1 次**总结调用；节级细拆直接读卡 C-0092 的
《章节细目》（纯正则，**不额外调模型**）。

与看板走**同一份实现**（`core/preview.py::build_preview`）：
命令行只做参数解析与打印，管线一行都不重复写。

    set PY=G:\\study_agent\\venv\\Scripts\\python.exe
    cd /d G:\\study_agent
    %PY% -X utf8 tools\\preview_build.py --doc 91 --list-chapters        :: 列章（零模型成本）
    %PY% -X utf8 tools\\preview_build.py --doc 91 --chapter 1 --dry-run  :: 预估（不调模型）
    %PY% -X utf8 tools\\preview_build.py --doc 91 --chapter 1            :: 真跑（调模型）
    %PY% -X utf8 tools\\preview_build.py --doc 91 --chapter 39 --json    :: 末尾多打一行 JSON

产物：`<该学科的 logs/knowledge>/<doc_id>-预习总结.json`（只写这一个文件，
**不写库、不碰知识点产物**）。学科用 `--subject` 指定时**不落注册表**
（`subjects.switch(..., save=False)`）：跑完不会改掉看板下次启动的默认学科。

⚠ 本功能**默认关闭**（`PREVIEW_ENABLED=0`）。CLI 默认与看板同一口径：关着就拒绝。
`--ignore-switch` 是**开发/验证用**的显式绕过，正式使用请在设置中心打开开关。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout.reconfigure(encoding="utf-8")

from core import preview, subjects  # noqa: E402


def _print_chapters(rep: dict, as_json: bool) -> None:
    print("教材：%s（doc#%d）· %d 章 · 章来源 %s"
          % (rep["title"], rep["doc_id"], rep["n_chapters"], rep["chapter_source"]))
    art = rep.get("sections_artifact") or {}
    print("节级细目（卡 C-0092 纯正则产物）：%s%s"
          % ("已读到" if art.get("read") else "**没有**", " · %s" % art.get("path") or ""))
    if not art.get("read"):
        print("  %s" % art.get("note") or "")
    print("%-6s %-32s %6s %9s %10s" % ("章号", "章名", "节数", "页范围", "块数"))
    for c in rep["chapters"]:
        print("%-6d %-32s %6d %9s %10d%s"
              % (c["no"], c["name"][:30], c["n_sections"],
                 "%d-%d" % (c["page_start"], c["page_end"]), c["n_blocks"],
                 "  ← 抽不出节（降级）" if c["sections_fallback"] else ""))
    if as_json:
        print(json.dumps(rep, ensure_ascii=False))


def _print_est(est: dict, as_json: bool) -> None:
    print("管线：预习总结（**独立管线分区**，%s）· 产物 %s"
          % (est["preview_version"], est["out_path"]))
    print("提示词真源：%s" % est["prompt_path"])
    print("  sha256 %s…（会记进产物 meta.prompt_sha256）" % est["prompt_sha256"][:16])
    print("教材：%s（doc#%d）· %s · p.%s（%d 页 / %d 块）· 章来源 %s"
          % (est["title"], est["doc_id"], est["chapter"], est["page_label"],
             est["n_pages"], est["n_blocks"], est["chapter_source"]))
    print("推理模式：thinking=%s · reasoning_effort=%s · max_tokens=%d"
          % (est["thinking"], est["reasoning_effort"], est["max_tokens"]))
    print("材料：%d 字符（模式 %s，来源块 %d 个）"
          % (est["material_chars"], est["material_mode"], est["n_sources"]))
    print("节级细拆：%d 节（%s，**0 次模型调用**）"
          % (est["n_sections"], est["sections_source"]))
    print("总调用：**%d 次**（整章总结 1 + 节级正则 0）· 入 ~%d / 出 ~%d token · 上界 ¥%.6f"
          % (est["est_calls"], est["est_tokens_in"], est["est_tokens_out"],
             est["est_cost_cny"]))
    print("  %s" % est["est_rule"])
    if est.get("skip_would_happen"):
        print("✓ 产物已是最新（材料 sha256 未变）→ 真跑时会**跳过：0 次调用 0 花费**")
    prev = est.get("prev") or {}
    if prev.get("exists"):
        print("已有预习产物：%s（%s 条要点，%s，累计 ¥%.6f）"
              % (est["out_path"], prev.get("n_points"), prev.get("generated_at") or "无时间",
                 float(prev.get("cost_cny") or 0.0)))
    print("[dry-run] 未调用模型，未写任何文件")
    if as_json:
        print(json.dumps(est, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="给一份教材的某一章做预习总结（独立管线；核心实现 core/preview.py::build_preview）")
    ap.add_argument("--doc", type=int, default=0, help="文档 id（教材）")
    ap.add_argument("--chapter", default="", help="章号（1）或章名（第1章 绪论 / 绪论）")
    ap.add_argument("--subject", default="",
                    help="学科名或 slug（不写 = 当前学科）。**不落注册表**，只影响本次运行")
    ap.add_argument("--subjects", action="store_true", help="列出可用学科后退出")
    ap.add_argument("--list-chapters", action="store_true", help="列出该教材的章（零模型成本）")
    ap.add_argument("--dry-run", action="store_true", help="只预估调用次数与成本，不调用模型")
    ap.add_argument("--out", default="", help="产物路径（默认 <学科 logs/knowledge>/<doc>-预习总结.json）")
    ap.add_argument("--prompt", default="", help="提示词真源（默认 logs/knowledge/提示词-预习总结.md）")
    ap.add_argument("--force", action="store_true",
                    help="忽略「产物已是最新」的跳过，重跑")
    ap.add_argument("--ignore-switch", action="store_true",
                    help="★ 开发/验证用：绕过 PREVIEW_ENABLED 开关（正式使用请去设置中心打开）")
    ap.add_argument("--json", action="store_true", help="末尾多打一行 JSON 摘要")
    args = ap.parse_args()

    if args.subjects:
        for s in subjects.list_subjects():
            print("%-24s %s" % (s.get("slug"), s.get("name")))
        return 0

    if not args.doc:
        ap.error("要么给 --doc <id>，要么用 --subjects 列学科")

    if args.subject:
        entry = subjects.switch(args.subject, save=False)
        print("学科：%s（%s）——只影响本次进程，未落注册表"
              % (entry.get("name"), entry.get("slug")))

    if args.list_chapters:
        rep = preview.list_chapters(args.doc)
        if not rep.get("ok"):
            print("★%s" % rep.get("error"))
            return 1
        _print_chapters(rep, args.json)
        return 0

    if not str(args.chapter).strip():
        ap.error("要么给 --chapter <章号|章名>，要么用 --list-chapters 列章")

    kw = {"prompt_path": args.prompt or None, "out_path": args.out or None}
    if args.dry_run:
        est = preview.estimate_preview(args.doc, args.chapter, **kw)
        if not est.get("ok"):
            print("★%s" % est.get("error"))
            return 1
        _print_est(est, args.json)
        return 0

    rep = preview.build_preview(args.doc, args.chapter, dry_run=False,
                                force=args.force, ignore_switch=args.ignore_switch, **kw)
    if not rep.get("ok"):
        print("★%s" % rep.get("error"))
        if rep.get("hint"):
            print("  → %s" % rep["hint"])
            return 3
        return 1
    if args.json:
        print(json.dumps({"ok": rep["ok"], "status": rep["status"],
                          "out_path": rep["out_path"], "meta": rep.get("meta")},
                         ensure_ascii=False))
    return 0 if rep.get("status") in ("done", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
