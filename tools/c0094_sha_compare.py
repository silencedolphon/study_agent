# -*- coding: utf-8 -*-
"""C-0094 收尾：把 `logs/knowledge/*.json` 的 sha256 与开工前基线逐份对照。

    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0094_sha_compare.py

基线：`<卡目录>\\sha_before.json`（本卡开工前快照）。
结论：既有知识点产物（`-末级知识点总结.json`）与其它既有 json 产物**逐份一致**；
      本卡新增的产物（`-预习总结.json` 及其 `.bak`）是**新增**，不在基线里，单独列出。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0094")
KD = ROOT / "logs" / "knowledge"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    before = json.loads((CARD / "sha_before.json").read_text(encoding="utf-8"))
    now = {p.name: {"size": p.stat().st_size, "sha256": _sha(p)}
           for p in sorted(KD.glob("*.json"))}
    (CARD / "sha_after.json").write_text(
        json.dumps(now, ensure_ascii=False, indent=1), encoding="utf-8")

    changed = [k for k, v in before.items()
               if k not in now or now[k]["sha256"] != v["sha256"]]
    missing = [k for k in before if k not in now]
    added = [k for k in now if k not in before]
    kp_n = len([k for k in before if k.endswith("末级知识点总结.json")])

    print("开工前 json 产物 %d 份（其中 %d 份末级知识点总结）；现在 %d 份"
          % (len(before), kp_n, len(now)))
    print("内容变化：%d 份 %s" % (len(changed), changed or "（无）"))
    print("被删除：%d 份 %s" % (len(missing), missing or "（无）"))
    print("本卡新增：%s" % (added or "（无）"))
    for k in sorted(before):
        if k.endswith("末级知识点总结.json"):
            print("  %-30s %s %s" % (k, before[k]["sha256"][:16], now.get(k, {}).get("sha256", "-")[:16]))
    if changed or missing:
        print("C0094-SHA-FAIL 既有产物被动过：%s" % (changed + missing))
        return 1
    print("C0094-SHA-PASS 既有 json 产物 %d 份 sha256 前后**逐份一致**（含全部 %d 份知识点产物）"
          % (len(before), kp_n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
