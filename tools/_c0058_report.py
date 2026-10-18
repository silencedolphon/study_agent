# -*- coding: utf-8 -*-
"""卡 C-0058 交付数据：v3 与 v4 同一份课件（doc#5）的逐轮对照（只读）。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(r"G:\study_agent")
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0058")
P = ROOT / "subjects" / "有机化学" / "logs" / "knowledge" / "5-末级知识点总结.json"


def main() -> int:
    d = json.loads(P.read_text(encoding="utf-8"))
    m = d["meta"]
    calls = m.get("calls") or []
    lines: list[str] = []
    lines.append("产物：%s" % P)
    lines.append("pipeline_version = %s" % m.get("pipeline_version"))
    lines.append("顶层键 = %s" % list(d.keys()))
    lines.append("kp_total=%s kp_actual=%s 覆盖页=%s/%s"
                 % (m.get("kp_total"), m.get("kp_actual"),
                    (m.get("coverage") or {}).get("n_covered"), m.get("pages_total")))
    st = d.get("structure") or {}
    lines.append("结构：%s 节点 / %s 叶节点 / 最深 %s 级"
                 % (st.get("n_nodes"), st.get("n_leaves"), st.get("max_level")))
    lines.append("meta 里有 n_terms 吗：%s ｜ terms_filter 吗：%s ｜ terms 顶层键吗：%s"
                 % ("n_terms" in m, "terms_filter" in m, "terms" in d))
    rec = (d.get("knowledge_points") or [{}])[0]
    lines.append("单条知识点字段（第一个 rec）：%s" % sorted(rec.keys()))
    lines.append("单条里有 terms 吗：%s ｜ terms_rejected 吗：%s ｜ quality_terms 吗：%s"
                 % ("terms" in rec, "terms_rejected" in rec, "quality_terms" in rec))
    lines.append("")
    lines.append("== 逐轮（run）调用构成 ==")
    runs = {}
    for c in calls:
        r = int(c.get("run") or 1)
        a = runs.setdefault(r, {})
        k = str(c.get("kind") or "?")
        b = a.setdefault(k, {"n": 0, "tin": 0, "tout": 0, "reason": 0, "cost": 0.0,
                             "fail": 0})
        b["n"] += 1
        b["fail"] += 0 if c.get("ok") else 1
        b["tin"] += int(c.get("tokens_in") or 0)
        b["tout"] += int(c.get("tokens_out") or 0)
        b["reason"] += int(c.get("reasoning_tokens") or 0)
        b["cost"] += float(c.get("cost_cny") or 0.0)
    for r in sorted(runs):
        tot = sum(x["cost"] for x in runs[r].values())
        lines.append("  run%d 合计 ¥%.6f" % (r, tot))
        for k in sorted(runs[r]):
            x = runs[r][k]
            lines.append("     %-14s n=%-3d(incl %d 失败) in=%-7d out=%-7d 思考=%-7d ¥%.6f"
                         % (k, x["n"], x["fail"], x["tin"], x["tout"], x["reason"],
                            x["cost"]))
    lines.append("")
    lines.append("== v3（run4，2026-09-29）vs v4（run6，本卡）同口径对照 ==")
    r4 = runs.get(4, {})
    r6 = runs.get(6, {})
    k4 = r4.get("knowledge", {})
    k6 = r6.get("knowledge", {})
    t4 = r4.get("terms.filter", {})
    lines.append("  v3 run4：结构 %d 次 + knowledge %d 条 + terms.filter %d 次 → ¥%.6f"
                 % (sum(x["n"] for k, x in r4.items() if k.startswith("structure")),
                    k4.get("n", 0), t4.get("n", 0), sum(x["cost"] for x in r4.values())))
    lines.append("  v4 run6：结构 %d 次 + knowledge %d 条 + terms.filter %d 次 → ¥%.6f"
                 % (sum(x["n"] for k, x in r6.items() if k.startswith("structure")),
                    k6.get("n", 0), r6.get("terms.filter", {}).get("n", 0),
                    sum(x["cost"] for x in r6.values())))
    lines.append("  knowledge 单条均价：v3 ¥%.6f → v4 ¥%.6f（%.1f%%）"
                 % (k4.get("cost", 0) / max(1, k4.get("n", 1)),
                    k6.get("cost", 0) / max(1, k6.get("n", 1)),
                    100.0 * (k6.get("cost", 0) / max(1, k6.get("n", 1)))
                    / max(1e-9, k4.get("cost", 0) / max(1, k4.get("n", 1)))))
    lines.append("  knowledge 单条输出 token 均价：v3 %d → v4 %d"
                 % (k4.get("tout", 0) / max(1, k4.get("n", 1)),
                    k6.get("tout", 0) / max(1, k6.get("n", 1))))
    lines.append("  知识点**条数**：v3 %s 条 → v4 %s 条（叶节点决定，terms 摘出不影响条数）"
                 % (24, m.get("kp_actual")))
    out = CARD / "对照数据-v3-vs-v4.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", out)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
