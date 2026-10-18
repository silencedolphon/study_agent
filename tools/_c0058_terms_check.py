# -*- coding: utf-8 -*-
"""卡 C-0058：独立名词解释产物的字段自查（只读）。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(r"G:\study_agent")
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0058")
P = ROOT / "subjects" / "有机化学" / "logs" / "knowledge" / "5-名词解释.json"
NEED = ("term", "en", "en_source", "def", "first_page", "first_kp_id",
        "first_kp_name", "kp_ids", "n_occurrences", "evidence",
        "seg_id", "seg_name", "page_from_model")


def main() -> int:
    d = json.loads(P.read_text(encoding="utf-8"))
    m = d.get("meta") or {}
    ts = [t for t in (d.get("terms") or []) if isinstance(t, dict)]
    lines = ["产物：%s（%d 字节）" % (P, P.stat().st_size),
             "顶层键 = %s" % list(d.keys()),
             "meta.pipeline_version = %s ｜ n_candidates=%s n_terms=%s ｜ status=%s"
             % (m.get("pipeline_version"), m.get("n_candidates"), m.get("n_terms"),
                m.get("status")),
             "meta.kp_link = %s" % json.dumps(m.get("kp_link") or {}, ensure_ascii=False),
             "meta.segments = %d 段；调用 %d 次；¥%.6f；用时 %ss"
             % (m.get("n_segments") or 0, (m.get("usage_total") or {}).get("calls") or 0,
                float(m.get("cost_cny") or 0), m.get("elapsed_s")),
             "筛选口径 = %s" % json.dumps(m.get("terms_filter") or {}, ensure_ascii=False)[:220],
             "terms 条数 = %d" % len(ts),
             ""]
    names = []
    for t in ts:
        miss = [k for k in NEED if k not in t]
        if miss:
            names.append("★缺字段 %s：%s" % (miss, t.get("term")))
    lines.append("字段缺失检查：%s" % (names or "全部 %d 个名词都带 %d 个字段" % (len(ts), len(NEED))))
    lines.append("")
    lines.append("== 逐条（前 6 条全字段） ==")
    for t in ts[:6]:
        lines.append(json.dumps({k: t.get(k) for k in NEED}, ensure_ascii=False))
    lines.append("")
    lines.append("== 字段覆盖率 ==")
    for k in NEED:
        have = sum(1 for t in ts if t.get(k) not in ("", 0, [], None))
        lines.append("  %-16s 有值 %d/%d" % (k, have, len(ts)))
    lines.append("")
    lines.append("kp_linked = %s / %s（按 first_page 关联到知识点产物的条数）"
                 % (m.get("kp_linked"), len(ts)))
    out = CARD / "独立名词产物-字段自查.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print()
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
