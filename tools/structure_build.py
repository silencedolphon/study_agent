#!/usr/bin/env python
# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
"""化学结构式补全 · 命令行壳（task-37 / 结构页扫描 + 选择性识图）。

逻辑内核在 `core/structures.py`；本文件只负责：解析命令行 → 调内核 → 打印摘要 /
交互确认 → 决定退出码。**默认不花钱**：不带 `--run` 就是"扫描 + 预算"，0 次模型调用。

## 用法

```powershell
# 默认：扫描结构页（零 AI），落 logs/knowledge/<doc>-结构页.json
venv\\Scripts\\python.exe -X utf8 tools\\structure_build.py --doc 47

# 只看扫描/预算，不落盘（等价于 --scan --dry-run）
venv\\Scripts\\python.exe -X utf8 tools\\structure_build.py --doc 47 --dry-run

# 真实识图（**花钱**，必须显式 --run；会先打印预算并要求确认）
venv\\Scripts\\python.exe -X utf8 tools\\structure_build.py --doc 47 --run
venv\\Scripts\\python.exe -X utf8 tools\\structure_build.py --doc 47 --run --yes --limit 3

# 指定页 / 强制重跑 / 只处理有图的页预算
venv\\Scripts\\python.exe -X utf8 tools\\structure_build.py --doc 46 --run --pages 3,10 --yes
venv\\Scripts\\python.exe -X utf8 tools\\structure_build.py --doc 47 --run --force --yes

# 看产物状态 / 机器可读输出 / 指定产物目录（测试与隔离实例用）
venv\\Scripts\\python.exe -X utf8 tools\\structure_build.py --doc 47 --status --json
venv\\Scripts\\python.exe -X utf8 tools\\structure_build.py --doc 47 --dry-run --json --out-dir _work\\structure\\knowledge
```

## 退出码

- 0 = 成功（扫描成功 / 预算成功 / 构建跑完——单页失败只记 `errors` 不失败）；
- 2 = 前置不满足（参数非法 / 文档不存在 / 源文件不在 / 没有密钥）；
- 3 = 用户取消（真实调用确认时回答不是 y/yes）。

**红线**：真实调用只有 `--run` 才可能发生；任何情况下都不会"自动花钱"。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# tools/ 下脚本统一从项目根 import core（与 tools/study.py 同一条路）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, structures  # noqa: E402

EXAMPLES = """\
示例：
  %(prog)s --doc 47                              # 扫描结构页（零 AI，落盘）
  %(prog)s --doc 47 --dry-run                    # 只看扫描结果/预算，不落盘
  %(prog)s --doc 47 --run                        # 真实识图（花钱，先确认）
  %(prog)s --doc 47 --run --yes --limit 3        # 真实识图前 3 页（调试）
  %(prog)s --doc 46 --run --pages 3,10 --yes     # 只跑指定页
  %(prog)s --doc 47 --status --json              # 看产物状态
"""


def _parse_pages(spec: str) -> list[int]:
    """`--pages 3,10` / `3，10` / `3 10` -> [3, 10]。"""
    out: list[int] = []
    for part in str(spec or "").replace("，", ",").replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            raise ValueError("--pages 里有非数字：%r" % part) from None
    return sorted(set(out))


def _fmt_money(v: float) -> str:
    return "¥%.4f" % float(v or 0.0)


def _human_scan(r: dict) -> str:
    lines = [
        "结构页扫描（零 AI）· doc#%s%s" % (r["doc_id"],
                                          (" · " + r["meta"].get("doc_title", ""))
                                          if r.get("meta") else ""),
        "  源文件  : %s" % r.get("source_pptx"),
        "  页数    : %d；候选 %d 页（%.1f%%）"
        % (r["n_pages"], r["n_candidates"],
           (r["n_candidates"] / r["n_pages"] * 100) if r["n_pages"] else 0.0),
        "  产物    : %s" % (r.get("written") or "（dry-run：未落盘）"),
    ]
    if r.get("warnings"):
        for w in r["warnings"]:
            lines.append("  ⚠ " + w)
    for c in r["candidates"][:15]:
        lines.append("  p%-3d %s" % (c["page"], "；".join(c["reasons"])[:140]))
    if r["n_candidates"] > 15:
        lines.append("  … 其余 %d 页见 JSON 产物" % (r["n_candidates"] - 15))
    lines.append("  下一步：真实识图（花钱）——venv\\Scripts\\python.exe "
                 "tools\\structure_build.py --doc %s --run" % r["doc_id"])
    return "\n".join(lines)


def _human_build(r: dict) -> str:
    lines = ["化学结构式识图 · doc#%s" % r["doc_id"]]
    if not r.get("ok"):
        lines.append("  失败：%s" % r.get("error"))
        return "\n".join(lines)
    if r.get("dry_run"):
        est = r["estimate"]
        lines += [
            "  模式    : dry-run（0 次调用、0 写盘）",
            "  源文件  : %s" % r["source_pptx"],
            "  扫描候选: %d 页；有整页图 %d 页；缓存命中 %d 页"
            % (len(r["candidates"]), len(r["pages"]), len(r["pages_cached"])),
            "  本轮计划: %d 页%s"
            % (est["calls"],
               ("（--limit 截断 %d 页）" % len(r["pages_skipped_by_limit"]))
               if r.get("pages_skipped_by_limit") else ""),
            "  费用上界: %s（闲时价）/ %s（峰值价）"
            % (_fmt_money(est["cost_cny_off_peak"]),
               _fmt_money(est["cost_cny_peak"])),
            "  假设    : %s" % est["assumptions"][0],
        ]
        if r.get("pages_missing_image"):
            lines.append("  ⚠ 缺整页图（不渲染、跳过）：%s" % r["pages_missing_image"])
        lines.append("  （本命令没有调用任何模型；真实调用请显式加 --run）")
        return "\n".join(lines)
    lines += [
        "  模式    : 真实识图（dry_run=False）",
        "  本次调用: %d 页；缓存命中 %d 页；失败 %d 页"
        % (r["n_calls"], r["n_cached"], len(r.get("errors") or [])),
        "  本次费用: %s（累计 %s）"
        % (_fmt_money(r["cost_cny"]),
           _fmt_money(float((r.get("meta") or {}).get("cost_cny") or 0.0))),
        "  产物    : %s" % (r.get("written") or r.get("out_path")),
    ]
    for e in (r.get("errors") or [])[:10]:
        lines.append("  ✗ p%-3d %s" % (e.get("page"), str(e.get("error"))[:140]))
    return "\n".join(lines)


def _human_status(r: dict) -> str:
    lines = ["结构式产物状态 · doc#%s" % r["doc_id"]]
    sc, st = r["scan"], r["structures"]
    lines.append("  结构页  : %s%s" % (
        "已生成（%d 页 / 候选 %d 页，%s）"
        % (sc.get("n_pages", 0), sc.get("n_candidates", 0), sc.get("generated_at", ""))
        if sc["exists"] else "未生成（先跑 --scan）",
        "" if sc["exists"] else ""))
    lines.append("  结构式  : %s" % (
        "已生成（%d 页，调用 %d 次，%s，%s）"
        % (st.get("n_pages", 0), st.get("calls", 0),
           _fmt_money(st.get("cost_cny", 0.0)),
           ("%d 页有结构" % len(st.get("with_structure") or [])))
        if st["exists"] else "未生成（真实识图需 --run）"))
    if st["exists"]:
        lines.append("  产物    : %s" % st["path"])
    else:
        lines.append("  产物    : %s" % st["path"])
    return "\n".join(lines)


def _confirm(estimate: dict, yes: bool) -> bool:
    """真实调用前的确认：非交互环境必须 --yes。"""
    calls = int(estimate.get("calls") or 0)
    print("即将真实调用视觉模型：%d 页；费用上界 %s（闲时）/ %s（峰值）。"
          % (calls, _fmt_money(estimate.get("cost_cny_off_peak")),
             _fmt_money(estimate.get("cost_cny_peak"))))
    if yes:
        print("  --yes 已给出，跳过交互确认。")
        return True
    if not sys.stdin or not sys.stdin.isatty():
        print("  非交互环境：真实调用必须显式加 --yes 确认。", file=sys.stderr)
        return False
    try:
        ans = input("确认开始真实识图？[y/N] ").strip().lower()
    except (EOFError, OSError):
        return False
    return ans in ("y", "yes", "是")


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="structure_build.py",
        description="化学结构式补全：PPTX 结构页扫描（零 AI）+ 选择性识图（默认 dry-run，--run 才花钱）",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--doc", required=True, type=int, help="课件 doc_id（必填，如 47）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--scan", action="store_true",
                      help="只扫描结构页并落盘（默认行为，零 AI）")
    mode.add_argument("--status", action="store_true",
                      help="只读产物状态摘要（不扫描、不调用）")
    mode.add_argument("--run", action="store_true",
                      help="真实识图（**会花钱**，必须显式给出；会先打印预算并要求确认）")
    ap.add_argument("--dry-run", action="store_true",
                    help="构建 dry-run（默认；与 --run 互斥，0 调用 0 落盘）")
    ap.add_argument("--pages", default="",
                    help="只处理这些页，逗号分隔（如 3,10）；留空=扫描候选页")
    ap.add_argument("--limit", type=int, default=0,
                    help="本轮最多真实调用 N 页（0=不限；缓存命中不计）")
    ap.add_argument("--force", action="store_true",
                    help="忽略缓存，重跑选中页")
    ap.add_argument("--yes", action="store_true",
                    help="真实调用前不再交互确认（非交互环境必须给）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--db", default=str(config.DB_PATH),
                    help=f"文档库（默认 {config.DB_PATH}；测试/隔离实例用副本）")
    ap.add_argument("--out-dir", default="",
                    help="产物目录（默认 logs/knowledge；测试/隔离实例请指向 _work）")
    ap.add_argument("--prompt", default="",
                    help="提示词真源路径（默认 logs/knowledge/提示词-结构式识图.md）")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir) if args.out_dir else None
    try:
        pages = _parse_pages(args.pages)
    except ValueError as e:
        print("错误：%s" % e, file=sys.stderr)
        return 2
    if args.run and args.dry_run:
        print("错误：--run 与 --dry-run 互斥（不给出 --run 就是 dry-run）", file=sys.stderr)
        return 2

    # ---- 状态：只读产物摘要 ----
    if args.status:
        r = structures.structure_status(args.doc, out_dir=out_dir)
        print(json.dumps(r, ensure_ascii=False, indent=2) if args.json
              else _human_status(r))
        return 0

    # ---- 默认模式：扫描（--scan / 不带 --run） ----
    if not args.run:
        persist = not args.dry_run
        r = structures.scan_pptx(args.doc, db_path=args.db, dry_run=not persist,
                                 out_dir=out_dir)
        if not r.get("ok"):
            print("错误：%s" % r.get("error"), file=sys.stderr)
            return 2
        print(json.dumps(r, ensure_ascii=False, indent=2) if args.json
              else _human_scan(r))
        return 0

    # ---- 真实识图：先 dry-run 预算 → 确认 → 再跑 ----
    plan = structures.build_structures(
        args.doc, pages=pages or None, dry_run=True, force=args.force,
        limit=args.limit, db_path=args.db, out_dir=out_dir,
        prompt_path=args.prompt or None)
    if not plan.get("ok"):
        print("错误：%s" % plan.get("error"), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(_human_build(plan))
    if plan["estimate"]["calls"] <= 0:
        print("没有需要真实调用的页（全部缓存命中/无图/超 limit）；无需花钱。")
        return 0
    if not _confirm(plan["estimate"], args.yes):
        print("已取消：未发生任何模型调用。", file=sys.stderr)
        return 3
    r = structures.build_structures(
        args.doc, pages=pages or None, dry_run=False, force=args.force,
        limit=args.limit, db_path=args.db, out_dir=out_dir,
        prompt_path=args.prompt or None)
    if not r.get("ok"):
        print("错误：%s" % r.get("error"), file=sys.stderr)
        return 2
    print(json.dumps(r, ensure_ascii=False, indent=2) if args.json
          else _human_build(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
