# -*- coding: utf-8 -*-
"""名词解释的独立命令行入口（卡 C-0058）。

名词解释曾经是知识点总结管线的一步（卡 C-0052）；人拍板 2026-09-29
「取消知识点总结管线与名词解释功能的偶联（因为总结出的知识点太多了）」之后，
它变成**按课件单独触发**的独立功能，入口就是这个脚本。

与看板/批量走**同一份实现**（`core/kp_build.py::build_terms`）：
命令行只做参数解析与打印，管线一行都不重复写。

    set PY=G:\\study_agent\\venv\\Scripts\\python.exe
    cd /d G:\\study_agent
    %PY% -X utf8 tools\\terms_build.py --doc 5 --dry-run        :: 只看段数/调用次数/成本上界
    %PY% -X utf8 tools\\terms_build.py --doc 5                  :: 真跑（调模型）
    %PY% -X utf8 tools\\terms_build.py --doc 5 --json           :: 末尾多打一行 JSON 摘要
    %PY% -X utf8 tools\\terms_build.py --doc 5 --limit 1        :: 只跑前 1 段（调试）
    %PY% -X utf8 tools\\terms_build.py --doc 5 --force          :: 忽略已有产物重跑
    %PY% -X utf8 tools\\terms_build.py --subjects               :: 列出可用的学科
    %PY% -X utf8 tools\\terms_build.py --doc 5 --subject 有机化学 :: 用某个学科跑

产物：`<该学科的 logs/knowledge>/<doc_id>-名词解释.json`（只写这一个文件，
**不写库、不碰知识点产物**）。学科用 `--subject` 指定时**不落注册表**
（`subjects.switch(..., save=False)`）：跑完不会改掉看板下次启动的默认学科。
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

from core import kp_build, subjects  # noqa: E402


def _print_est(est: dict, as_json: bool) -> None:
    print("管线：名词解释（**独立功能**，%s）· 产物 %s"
          % (est["pipeline_version"], est["out_path"]))
    print("提示词真源：%s" % est["prompt_path"])
    print("  sha256 %s…（会记进产物 meta.prompt_sha256）" % est["prompt_sha256"][:16])
    print("课件：%s（doc#%d）· %d 页 · %d 块 · 每页喂模型 ≤%d 字符 · 材料合计 %d 字"
          % (est["title"], est["doc_id"], est["n_pages"], est["n_blocks"],
             est["page_chars"], est["material_chars_total"]))
    print("推理模式：thinking=%s · reasoning_effort=%s · max_tokens=%d"
          % (est["thinking"], est["reasoning_effort"], est["max_tokens"]))
    print("分段：%d 段（每段 ≤%d 页 / ≤%d 字符；提炼**每段一次调用**）"
          % (est["n_segments"], est["seg_pages"], est["seg_chars"]))
    for s in est["segments"]:
        print("  段 %d：p.%d~%d（%d 页 / %d 字符）"
              % (s["index"], s["a"], s["b"], s["n_pages"], s["chars"]))
    print("总调用：**%d 次**（提炼 %d + 全局筛选 1）· 入 %d / 出 %d token · ¥%.6f"
          % (est["est_calls"], est["est_calls_breakdown"]["extract"],
             est["est_tokens_in"], est["est_tokens_out"], est["est_cost_cny"]))
    print("  %s" % est["est_rule"])
    prev = est.get("prev") or {}
    if est.get("exists"):
        print("已有名词产物：%s（%s 条，%s，累计 ¥%.6f）——重跑会覆盖，先备份"
              % (est["out_path"], prev.get("n_terms"), prev.get("generated_at") or "无时间",
                 float(prev.get("cost_cny") or 0.0)))
    print("[dry-run] 未调用模型，未写任何文件")
    if as_json:
        print(json.dumps(est, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="给一份课件生成名词解释（独立功能；核心实现 core/kp_build.py::build_terms）")
    ap.add_argument("--doc", type=int, default=0, help="文档 id")
    ap.add_argument("--subject", default="",
                    help="学科名或 slug（不写 = 当前学科）。**不落注册表**，只影响本次运行")
    ap.add_argument("--subjects", action="store_true", help="列出可用学科后退出")
    ap.add_argument("--dry-run", action="store_true", help="只列段数与预估成本，不调用模型")
    ap.add_argument("--out", default="", help="产物路径（默认 <学科 logs/knowledge>/<doc>-名词解释.json）")
    ap.add_argument("--prompt", default="", help="提示词真源（默认 logs/knowledge/提示词-名词解释.md）")
    ap.add_argument("--limit", type=int, default=0, help="最多提炼前 N 段（调试用）")
    ap.add_argument("--force", action="store_true", help="忽略已有产物重跑（旧产物请先备份）")
    ap.add_argument("--json", action="store_true", help="末尾多打一行 JSON 摘要")
    args = ap.parse_args()

    if args.subjects:
        for s in subjects.list_subjects():
            print("%-16s %s" % (s.get("slug"), s.get("name")))
        return 0

    if not args.doc:
        ap.error("要么给 --doc <id>，要么用 --subjects 列学科")

    if args.subject:
        entry = subjects.switch(args.subject, save=False)
        print("学科：%s（%s）——只影响本次进程，未落注册表"
              % (entry.get("name"), entry.get("slug")))

    kw = {"prompt_path": args.prompt or None, "out_path": args.out or None}
    if args.dry_run:
        est = kp_build.estimate_terms(args.doc, **kw)
        if not est.get("ok"):
            print("★%s" % est.get("error"))
            return 1
        _print_est(est, args.json)
        return 0

    rep = kp_build.build_terms(args.doc, dry_run=False, limit=args.limit,
                               force=args.force, **kw)
    if not rep.get("ok"):
        print("★%s" % rep.get("error"))
        return 1
    if args.json:
        print(json.dumps({"ok": rep["ok"], "status": rep["status"],
                          "out_path": rep["out_path"], "meta": rep.get("meta")},
                         ensure_ascii=False))
    return 0 if rep.get("status") in ("done", "stopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
