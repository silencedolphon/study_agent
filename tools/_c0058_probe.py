# -*- coding: utf-8 -*-
"""卡 C-0058 只读调查（零费用，不调模型、不写库）。

产出：doc#5（v3 产物）里 terms 链路的 token/字符占比、各 kind 调用分布。
输出一律写 UTF-8 文件（避开 Windows 控制台编码）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs" / "_c0058_probe.txt"


def main() -> None:
    lines: list[str] = []
    p = ROOT / "subjects" / "有机化学" / "logs" / "knowledge" / "5-末级知识点总结.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    meta = d.get("meta") or {}
    calls = meta.get("calls") or []

    lines.append("产物：%s" % p)
    lines.append("pipeline_version=%s  kp=%d  terms=%d  顶层键=%s"
                 % (meta.get("pipeline_version"), len(d.get("knowledge_points") or []),
                    len(d.get("terms") or []), list(d.keys())))

    # ---- 1. 逐 kind 统计调用 ----
    lines.append("")
    lines.append("== 各 kind 调用分布（meta.calls 全量） ==")
    kinds: dict[str, dict] = {}
    for c in calls:
        k = str(c.get("kind") or "?")
        a = kinds.setdefault(k, {"n": 0, "fail": 0, "tin": 0, "tout": 0, "reason": 0,
                                 "cost": 0.0, "chars_in": 0})
        a["n"] += 1
        a["fail"] += 0 if c.get("ok") else 1
        a["tin"] += int(c.get("tokens_in") or 0)
        a["tout"] += int(c.get("tokens_out") or 0)
        a["reason"] += int(c.get("reasoning_tokens") or 0)
        a["chars_in"] += int(c.get("chars_in") or 0)
        try:
            a["cost"] += float(c.get("cost_cny") or 0.0)
        except (TypeError, ValueError):
            pass
    tot = {"n": 0, "tin": 0, "tout": 0, "reason": 0, "cost": 0.0}
    for k in sorted(kinds):
        a = kinds[k]
        tot["n"] += a["n"]
        tot["tin"] += a["tin"]
        tot["tout"] += a["tout"]
        tot["reason"] += a["reason"]
        tot["cost"] += a["cost"]
    for k in sorted(kinds):
        a = kinds[k]
        lines.append("  %-14s n=%-3d fail=%-2d in=%-7d out=%-7d reasoning=%-7d "
                     "cost=%.4f  out占比=%.1f%%"
                     % (k, a["n"], a["fail"], a["tin"], a["tout"], a["reason"], a["cost"],
                        100.0 * a["tout"] / max(1, tot["tout"])))
    lines.append("  %-14s n=%-3d       in=%-7d out=%-7d reasoning=%-7d cost=%.4f"
                 % ("合计", tot["n"], tot["tin"], tot["tout"], tot["reason"], tot["cost"]))

    # ---- 2. 逐 run 统计（run5 = C-0052 试点重跑）----
    lines.append("")
    lines.append("== meta.runs ==")
    for r in meta.get("runs") or []:
        lines.append("  run%s calls=%s tin=%s tout=%s reasoning=%s cost=%s first=%s"
                     % (r.get("run"), r.get("calls"), r.get("tokens_in"),
                        r.get("tokens_out"), r.get("reasoning_tokens"), r.get("cost_cny"),
                        r.get("first_at")))

    # ---- 3. terms 字段的字符占比（逐条提炼那一步真实占走的输出额度）----
    lines.append("")
    lines.append("== 逐条 rec 里 terms 字段的字符占比（第②步的输出负担） ==")
    recs = [r for r in (d.get("knowledge_points") or []) if isinstance(r, dict)]
    tot_chars = 0
    body_chars = 0
    term_chars = 0
    n_kp_with_terms = 0
    n_raw_terms = 0
    for r in recs:
        ts = r.get("terms") or []
        if ts:
            n_kp_with_terms += 1
        n_raw_terms += len(ts)
        strip = {k: v for k, v in r.items() if k not in ("terms", "terms_rejected",
                                                         "quality_terms")}
        a = len(json.dumps(strip, ensure_ascii=False))
        b = len(json.dumps(ts, ensure_ascii=False))
        tot_chars += a + b
        body_chars += a
        term_chars += b
    lines.append("  知识点条数=%d，其中带 terms 的=%d；提炼出的候选名词（未筛选）=%d"
                 % (len(recs), n_kp_with_terms, n_raw_terms))
    lines.append("  recs 总字符=%d，其中 terms 字段=%d → 占 %.2f%%"
                 % (tot_chars, term_chars, 100.0 * term_chars / max(1, tot_chars)))
    lines.append("  换算 token（÷1.6）= terms 约 %d token，其余约 %d token"
                 % (term_chars / 1.6, body_chars / 1.6))

    # ---- 4. 产物顶层 terms 的规模 ----
    lines.append("")
    lines.append("== 产物顶层 terms（第③④步的结果） ==")
    ts = [t for t in (d.get("terms") or []) if isinstance(t, dict)]
    tchars = len(json.dumps(ts, ensure_ascii=False))
    lines.append("  条数=%d 字符=%d 字段=%s"
                 % (len(ts), tchars, sorted(ts[0].keys()) if ts else []))
    lines.append("  terms_filter.mode=%s  note=%s"
                 % ((meta.get("terms_filter") or {}).get("mode"),
                    (meta.get("terms_filter") or {}).get("note")))
    lines.append("  顶层 terms 占整个产物字符的 %.2f%%（产物总字符 %d）"
                 % (100.0 * tchars / max(1, len(json.dumps(d, ensure_ascii=False))),
                    len(json.dumps(d, ensure_ascii=False))))

    # ---- 5. 结构叶节点数（决定 kp 条数的那一层）----
    lines.append("")
    lines.append("== 结构树 ==")
    st = d.get("structure") or {}
    lines.append("  structure 顶层键=%s" % list(st.keys()))

    def walk(n, depth=0, acc=None):
        if acc is None:
            acc = []
        if not isinstance(n, dict):
            return acc
        kids = [c for c in (n.get("children") or []) if isinstance(c, dict)]
        if not kids:
            acc.append((depth, n.get("name") or n.get("title") or ""))
        for c in kids:
            walk(c, depth + 1, acc)
        return acc

    root = st.get("tree") or st.get("root") or st
    leaves = []
    if isinstance(root, dict):
        leaves = walk(root)
    lines.append("  叶节点数=%d" % len(leaves))
    lines.append("  前 10 个叶节点：%s" % [x[1] for x in leaves[:10]])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
