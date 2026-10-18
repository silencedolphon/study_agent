# -*- coding: utf-8 -*-
"""C-0094 交付辅助：把一份教材**全部章**的预习预估汇总起来（**零模型调用、不写文件**）。

用途：给出"整本教材跑一遍预习"的调用次数、成本与时间估算——卡 C-0094 的验收要求
「给出实测单章耗时与花费（CNY），并给出 doc#91 全 42 章的估算」。

两个口径都给，不混：
  · **上界**（`estimate_preview()` 的公式，与 dry-run 同源）：按 1.6 字符/token 与
    思考链 ≈ 输入 1.5 倍估，偏保守；
  · **回归**（按 C-0094 两次真实调用的实测点拟合）：小章（材料 5829 字符，¥0.058708/59.4s）
    与大章（材料 37235 字符，¥0.089205/65.2s）。拟合式与点数写在 `--json` 输出里，
    改公式请连着改这里的注释，别让报告和数据两张皮。

    set PY=G:\\study_agent\\venv\\Scripts\\python.exe
    %PY% -X utf8 tools\\c0094_estimate_doc.py --doc 91
    %PY% -X utf8 tools\\c0094_estimate_doc.py --doc 91 --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout.reconfigure(encoding="utf-8")

from core import preview  # noqa: E402

# 实测点（卡 C-0094，2026-10-14，deepseek-flash + thinking + high）：
#   doc#91 第 1 章：材料 5829 字符 → ¥0.058708 / 59.4 s（token 4741 入 / 12596 出）
#   doc#91 第 39 章：材料 37235 字符 → ¥0.089205 / 65.2 s（token 25969 入 / 14448 出）
MEASURED = {"ch1_material_chars": 5829, "ch1_cost": 0.058708, "ch1_sec": 59.4,
            "ch39_material_chars": 37235, "ch39_cost": 0.089205, "ch39_sec": 65.2,
            "sec_per_chapter": 62.0}
# 成本回归（元）：fixed 部分 ≈ 提示词 + 思考链/正文的固定开销；per_char 部分 ≈ 章材料的增量
COST_FIXED = 0.0545
COST_PER_CHAR = 9.3e-7


def main() -> int:
    ap = argparse.ArgumentParser(description="汇总一份教材全部章的预习预估（零模型调用）")
    ap.add_argument("--doc", type=int, required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = preview.list_chapters(args.doc)
    if not rep.get("ok"):
        print("★%s" % rep.get("error"))
        return 1
    t0 = time.perf_counter()
    rows, failed = [], []
    for c in rep["chapters"]:
        est = preview.estimate_preview(args.doc, c["no"])
        if not est.get("ok"):
            failed.append({"no": c["no"], "error": est.get("error")})
            continue
        rows.append({"no": c["no"], "name": c["name"], "n_pages": est["n_pages"],
                     "n_blocks": est["n_blocks"], "n_sections": est["n_sections"],
                     "material_chars": est["material_chars"],
                     "est_calls": est["est_calls"],
                     "est_cost_upper": est["est_cost_cny"],
                     "est_cost_fit": round(COST_FIXED + est["material_chars"] * COST_PER_CHAR, 6)})
    elapsed = time.perf_counter() - t0
    total_chars = sum(r["material_chars"] for r in rows)
    out = {
        "doc_id": args.doc, "title": rep.get("title") or "",
        "n_chapters": len(rows), "n_chapters_total": rep.get("n_chapters") or 0,
        "failed_chapters": failed,
        "total_material_chars": total_chars,
        "total_calls": sum(r["est_calls"] for r in rows),
        "est_cost_upper_cny": round(sum(r["est_cost_upper"] for r in rows), 4),
        "est_cost_fit_cny": round(sum(r["est_cost_fit"] for r in rows), 4),
        "est_seconds_serial": round(len(rows) * MEASURED["sec_per_chapter"], 1),
        "est_minutes_serial": round(len(rows) * MEASURED["sec_per_chapter"] / 60.0, 1),
        "measured": MEASURED,
        "fit_rule": ("成本回归：¥%.4f + 材料字符 × %.1e（由 C-0094 的两次真实调用拟合："
                     "材料 5829→¥%.6f、37235→¥%.6f）；时间按实测 %.1f 秒/章串行估"
                     % (COST_FIXED, COST_PER_CHAR, MEASURED["ch1_cost"],
                        MEASURED["ch39_cost"], MEASURED["sec_per_chapter"])),
        "note": ("**零模型调用**：这里只读库里的文本渲染材料、算 token 估算，"
                 "不调模型、不写任何文件；节级细拆（《章节细目》）也一并统计在内、"
                 "成本恒为 0"),
        "dry_run_seconds": round(elapsed, 1),
        "chapters": rows,
    }
    print("教材：%s（doc#%d）· %d 章（本次统计 %d 章，失败 %d）"
          % (out["title"], args.doc, out["n_chapters_total"], out["n_chapters"],
             len(failed)))
    print("材料合计：%d 字符 · 调用合计：**%d 次**（整章 1 次）"
          % (out["total_material_chars"], out["total_calls"]))
    print("成本估算：上界 ¥%.4f · 回归 ¥%.4f（%s）"
          % (out["est_cost_upper_cny"], out["est_cost_fit_cny"], out["fit_rule"]))
    print("时间估算：串行约 %.1f 分钟（%.1f 秒/章 × %d 章）"
          % (out["est_minutes_serial"], MEASURED["sec_per_chapter"], out["n_chapters"]))
    print("本次 dry-run 用时 %.1f 秒（%s）" % (elapsed, out["note"]))
    for r in rows:
        print("  第%-3d章 %-28s %3d 页 %6d 字符 %3d 节  回归 ¥%.4f"
              % (r["no"], r["name"][:26], r["n_pages"], r["material_chars"],
                 r["n_sections"], r["est_cost_fit"]))
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
