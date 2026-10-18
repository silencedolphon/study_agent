# -*- coding: utf-8 -*-
"""C-0057 改前快照 + 改后核对（施工用，交付前删）。

用法：
  python tools\\_c0057_snap.py before   # 改前存一份
  python tools\\_c0057_snap.py after    # 改后核对
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0057")
FILES = ["core/chat.py", "tools/dashboard.py", "core/config.py"]
TABLES = ["knowledge_points", "kp_edges", "kp_sources", "reviews", "mistakes"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def collect() -> dict:
    out = {"files": {}, "json_artifacts": {}, "five": {}, "counts": {}}
    for f in FILES:
        out["files"][f] = sha(ROOT / f)
    for d in ("logs/knowledge", "logs/outline"):
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
        for f in FILES:
            dst = CARD / "before" / Path(f).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((ROOT / f).read_bytes())
        print("SNAPSHOT_BEFORE_OK", CARD / "snapshot_before.json")
        return 0
    b = json.loads((CARD / "snapshot_before.json").read_text(encoding="utf-8"))
    a = collect()
    same = {k: (b["files"][k] == a["files"][k]) for k in FILES}
    print("文件是否与改前一致：", json.dumps(same, ensure_ascii=False))
    for d in b["json_artifacts"]:
        bs, as_ = b["json_artifacts"][d], a["json_artifacts"][d]
        ch = sorted(k for k in (set(bs) & set(as_)) if bs[k] != as_[k])
        print("%s：改前 %d 份 / 改后 %d 份 / 变动 %s"
              % (d, len(bs), len(as_), ch or "无"))
    print("五表：", json.dumps(a["five"], ensure_ascii=False),
          "改前：", json.dumps(b["five"], ensure_ascii=False))
    print("counts：", json.dumps(a["counts"], ensure_ascii=False))
    (CARD / "snapshot_after.json").write_text(
        json.dumps(a, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
