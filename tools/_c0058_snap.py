# -*- coding: utf-8 -*-
"""卡 C-0058 改前快照 + 改后核对（施工用；交付后保留，供大管家复跑）。

用法：
  python tools\\_c0058_snap.py before   # 改前存一份
  python tools\\_c0058_snap.py after    # 改后逐项核对（只读，不改任何东西）
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0058")
FILES = ["core/kp_build.py", "tools/dashboard.py", "tools/kp_build.py",
         "core/chat.py", "core/config.py", "core/ingest.py", "core/figures.py",
         "core/extract.py", "core/kp_batch.py"]
PROMPTS = ["logs/knowledge/提示词-知识点总结-v2.md",
           "logs/knowledge/提示词-思维导图结构.md",
           "logs/knowledge/提示词-知识点总结.md"]
TABLES = ["knowledge_points", "kp_edges", "kp_sources", "reviews", "mistakes"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def collect() -> dict:
    out: dict = {"files": {}, "prompts": {}, "json_artifacts": {}, "five": {},
                 "counts": {}}
    for f in FILES:
        q = ROOT / f
        out["files"][f] = sha(q) if q.is_file() else "(missing)"
    for f in PROMPTS:
        q = ROOT / f
        out["prompts"][f] = sha(q) if q.is_file() else "(missing)"
    for d in ("logs/knowledge", "logs/outline", "subjects/有机化学/logs/knowledge"):
        dd = ROOT / d
        out["json_artifacts"][d] = {
            str(p.relative_to(ROOT)): sha(p)
            for p in sorted(dd.rglob("*")) if p.is_file()}
    db = ROOT / "index" / "study.db"
    c = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    for t in TABLES:
        out["five"][t] = int(c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0])
    out["counts"]["documents"] = int(
        c.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    out["counts"]["chunks"] = int(
        c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    c.close()
    return out


def main() -> int:
    which = (sys.argv[1] if len(sys.argv) > 1 else "before").lower()
    CARD.mkdir(parents=True, exist_ok=True)
    if which == "before":
        (CARD / "snapshot_before.json").write_text(
            json.dumps(collect(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        print("SNAPSHOT_BEFORE_OK", CARD / "snapshot_before.json")
        return 0
    b = json.loads((CARD / "snapshot_before.json").read_text(encoding="utf-8"))
    a = collect()
    print("== 源文件 sha256（改前 → 改后）==")
    for k in FILES + PROMPTS:
        bk = b["files"].get(k) or b["prompts"].get(k)
        ak = a["files"].get(k) or a["prompts"].get(k)
        print("  %-42s %s  %s" % (Path(k).name, "变了" if bk != ak else "未变",
                                  "%s → %s" % ((bk or "")[:12], (ak or "")[:12])))
    print("== 产物 sha256 逐份对照 ==")
    for d in b["json_artifacts"]:
        bs, as_ = b["json_artifacts"][d], a["json_artifacts"][d]
        changed = sorted(k for k in (set(bs) & set(as_)) if bs[k] != as_[k])
        added = sorted(set(as_) - set(bs))
        gone = sorted(set(bs) - set(as_))
        print("  %-34s 改前 %d 份 / 改后 %d 份" % (d, len(bs), len(as_)))
        print("    变动：%s" % (changed or "无"))
        if added:
            print("    新增：%s" % added)
        if gone:
            print("    缺失：%s" % gone)
    print("== 五表（改前 → 改后）==")
    for t in TABLES:
        print("  %-18s %d → %d" % (t, b["five"][t], a["five"][t]))
    print("  counts 改后：%s" % json.dumps(a["counts"], ensure_ascii=False))
    print("  counts 改前：%s" % json.dumps(b["counts"], ensure_ascii=False))
    (CARD / "snapshot_after.json").write_text(
        json.dumps(a, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
