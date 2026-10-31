# -*- coding: utf-8 -*-
"""知识点整理管线的命令行入口（卡 C-0035；卡 C-0049 起默认走 v2，C-0052 起 v3，
C-0058 起 v4：**只出知识点**，名词解释已摘成独立功能 `tools/terms_build.py`）。

与看板「知识点 → 整理知识点」按钮**走同一份实现**（`core/kp_build.py`）：
命令行只是参数解析与打印，管线一行都不重复写——否则两条路的结果会漂。

    set PY=G:\\study_agent\\venv\\Scripts\\python.exe
    cd /d G:\\study_agent
    %PY% -X utf8 tools\\kp_build.py --doc 103 --dry-run      :: 只看清单与预估成本
    %PY% -X utf8 tools\\kp_build.py --doc 103                :: 真跑（调模型，断点续跑）
    %PY% -X utf8 tools\\kp_build.py --doc 103 --json         :: 末尾多打一行 JSON 摘要
    %PY% -X utf8 tools\\kp_build.py --doc 5 --pipeline v1    :: 回退到读 outline 的旧管线

⚠ **预算默认无上限**（卡 C-0052，人拍板 2026-09-27）：`--max-cost` 默认 0，
而且只有再加 `--enforce-budget` 才会真的到线即停；单独写 `--max-cost 0.5` 只会把
0.5 记进产物 `meta.budget_cny`（`meta.budget_enforced=false`），**不拦停**。

⚠ **推理模式默认开启且强度拉满**（`thinking=True` + `reasoning_effort="max"`）：
思考 token 计入 completion、按输出计价，所以同样几页材料会比旧版贵一个量级
（实测一条 8 页材料 ≈ ¥0.057）。这是人拍板接受的代价。

⚠ 学科：不加 `--subject` 时跑的是**当前学科**（默认库）。卡 C-0058 起可以加
`--subject <学科名或 slug>` 指定别的学科——它**不落注册表**（`switch(..., save=False)`），
不会改掉看板下次启动的默认学科；不加时行为与从前逐字相同（先自己切库，
见 `core/subjects.py::restore()` 与 `cards/C-0049/run_v2.py`）。

产物：`logs/knowledge/<doc_id>-末级知识点总结.json`（只写这一个文件，**不写库**）。
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

from core import kp_build  # noqa: E402


def _print_est(est: dict, pipeline: str, as_json: bool) -> None:
    if pipeline == "v1":
        print("提示词真源：%s" % est["prompt_path"])
        print("  sha256 %s…（会记进产物 meta.prompt_sha256）" % est["prompt_sha256"][:16])
        print("管线：v1（%s，读 logs/outline/<doc>.json）" % kp_build.PIPELINE_VERSION_V1)
        print("课件：%s（doc#%d）· %d 页 · 末级节点 %d 个 · 父节点页 %d 个"
              % (est["title"], est["doc_id"], est["n_pages"], est["n_leaf_pages"],
                 len(est["excluded_parent_pages"])))
        print("材料：%d 页合计 %d 字；按「每页一条」的窗口合计 %d 字"
              % (len(est["pages"]), est["material_chars_total"],
                 est["window_chars_total"]))
        print("预估条数：最少 %d 条（全部 3 页合并）～ 最多 %d 条（完全不合并）"
              % (est["kp_min"], est["kp_max"]))
        print("预估成本（上界，不合并时）：%d 次调用 · 入 %d / 出 %d token · ¥%.6f"
              % (est["est_calls"], est["est_tokens_in"], est["est_tokens_out"],
                 est["est_cost_cny"]))
        print("  %s" % est["est_rule"])
    else:
        print("管线：v4（%s，**只读库里的文本**：chunks 正文 + 识图文本，按 source_ref 组织；"
              "**只出知识点**）"
              % est["pipeline_version"])
        print("  不读 outline：%s" % est["outline_note"])
        print("推理模式：thinking=%s · reasoning_effort=%s · 知识点 max_tokens=%d · "
              "结构 max_tokens=%d"
              % (est.get("thinking"), est.get("reasoning_effort"),
                 est.get("kp_max_tokens"), est.get("struct_max_tokens")))
        print("预算：%s（人拍板 2026-09-27 解除花费上限；要看硬上限加 --enforce-budget + --max-cost）"
              % ("不限（默认）" if est.get("budget_unlimited") else "硬上限"))
        print("提示词真源（结构生成）：%s" % est["structure_prompt_path"])
        print("  sha256 %s…" % est["structure_prompt_sha256"][:16])
        print("提示词真源（写知识点）：%s" % est["prompt_path"])
        print("  sha256 %s…" % est["prompt_sha256"][:16])
        print("课件：%s（doc#%d）· %d 页（1~%d）· %d 块 · 材料合计 %d 字"
              % (est["title"], est["doc_id"], est["n_pages"], est["max_page"],
                 est["n_blocks"], est["material_chars_total"]))
        segs = est["segments"]
        print("结构生成：%d 段 map + %s = **%d 次调用**（每段 ≤%d 页 / ≤%d 字符；"
              "每页喂给结构模型 ≤%d 字符；层级不设上限、由模型判断）"
              % (len(segs), ("1 次归并" if len(segs) > 1 else "0 次归并（单段本地合成根）"),
                 est["structure_calls"], est["seg_pages"], est["seg_chars"],
                 est["struct_page_chars"]))
        for s in segs:
            print("  段 %d：p.%d~%d（%d 页）"
                  % (s["index"], s["a"], s["b"], len(s["pages"])))
        print("知识点：预估 **%d ~ %d 条**（v4 一条 = 一个结构叶节点、**页数不限**；"
              "只有材料超 %d 字才按连续页再切）"
              % (est["kp_min"], est["kp_max"], est.get("kp_material_chars_max") or 0))
        print("预估成本（上界，含思考 token）：总调用 %d 次（结构 %d + 知识点 ≤%d）· "
              "入 %d / 出 %d token · ¥%.6f"
              % (est["est_calls"], est["est_calls_breakdown"]["structure"],
                 est["est_calls_breakdown"]["knowledge_max"],
                 est["est_tokens_in"], est["est_tokens_out"], est["est_cost_cny"]))
        print("  %s" % est["est_rule"])
    if est.get("already_done_pages"):
        print("已是本版产物、可续跑：%d 条已完成" % len(est["already_done_pages"]))
    print("产物路径：%s" % est["out_path"])
    print("[dry-run] 未调用模型，未写任何文件")
    if as_json:
        print(json.dumps(est, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="整理一份课件的知识点（核心实现：core/kp_build.py；默认 v2）")
    ap.add_argument("--doc", type=int, required=True, help="文档 id")
    ap.add_argument("--subject", default="",
                    help="学科名或 slug（不写 = 当前学科）。**不落注册表**，只影响本次运行"
                         "（卡 C-0058 加；与 tools/terms_build.py 同一套语义）")
    ap.add_argument("--pipeline", choices=("v2", "v1"), default="v2",
                    help="v2（默认，卡 C-0049：只读库里的文本，模型自生成结构）/ "
                         "v1（卡 C-0045：读 logs/outline/<doc>.json，仅供对照回退）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列清单与预估成本，不调用模型")
    ap.add_argument("--out", default="", help="产物路径（默认 logs/knowledge/<doc>-末级知识点总结.json）")
    ap.add_argument("--outline", default="", help="[v1 专用] outline 缓存路径（v2 不读 outline）")
    ap.add_argument("--prompt", default="", help="知识点提示词真源（v2 默认 logs/knowledge/提示词-知识点总结-v2.md）")
    ap.add_argument("--struct-prompt", default="",
                    help="[v2 专用] 结构生成提示词真源（默认 logs/knowledge/提示词-思维导图结构.md）")
    ap.add_argument("--limit", type=int, default=0, help="最多整理前 N 条（调试用）")
    ap.add_argument("--max-cost", type=float, default=0.0,
                    help="花费上限（人民币，0 = 不限）。**默认 0 = 不限**（卡 C-0052："
                         "人拍板解除整理知识点的花费限制）；真要用它拦停必须同时加 "
                         "--enforce-budget，否则它只记进产物 meta.budget_cny、不拦停")
    ap.add_argument("--enforce-budget", action="store_true",
                    help="让 --max-cost 恢复成硬上限（到线即停）。不加则预算无上限")
    ap.add_argument("--force", action="store_true",
                    help="忽略已有产物重跑（旧产物请先备份）")
    ap.add_argument("--json", action="store_true", help="末尾多打一行 JSON 摘要")
    args = ap.parse_args()

    if args.subject:
        from core import subjects  # 延迟 import：不给 --subject 时不碰切库这一套
        entry = subjects.switch(args.subject, save=False)
        print("学科：%s（%s）——只影响本次进程，未落注册表"
              % (entry.get("name"), entry.get("slug")))

    kw = {"prompt_path": args.prompt or None, "pipeline": args.pipeline}
    if args.pipeline == "v1":
        kw["outline_path"] = args.outline or None
    else:
        kw["struct_prompt_path"] = args.struct_prompt or None

    if args.dry_run:
        est = kp_build.estimate(args.doc, **kw)
        if not est.get("ok"):
            print("★%s" % est.get("error"))
            return 1
        _print_est(est, args.pipeline, args.json)
        return 0

    # GPT 真实验收发现：--out 只被 argparse 接收、没有传给 build，
    # 导致“用 --out 跑隔离样本”实际覆盖默认 logs/knowledge 产物。
    # dry-run 不写文件，estimate 也没有 out_path 参数；只给真实 build 传。
    if args.out:
        kw["out_path"] = args.out
    rep = kp_build.build(args.doc, dry_run=False, limit=args.limit,
                         max_cost=args.max_cost, force=args.force, **kw,
                         budget_unlimited=(not args.enforce_budget))
    if not rep.get("ok"):
        print("★%s" % rep.get("error"))
        return 1
    if args.json:
        print(json.dumps({"ok": rep["ok"], "status": rep["status"],
                          "out_path": rep["out_path"],
                          "meta": rep.get("meta")}, ensure_ascii=False))
    return 0 if rep.get("status") in ("done", "stopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())