# -*- coding: utf-8 -*-
"""临时：逐条核对 result.json 的 file 类条目（跑完即删）。"""
import json
from pathlib import Path

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0052")
d = json.loads((CARD / "result.json").read_text(encoding="utf-8"))
print("字段：", sorted(d.keys()), "| selfCheck =", d["selfCheck"],
      "| verify 条数 =", len(d["verify"]))
bad = 0
for v in d["verify"]:
    if v["kind"] != "file":
        continue
    p = Path(v["path"])
    exists = p.exists() if p.is_file() else p.is_dir()
    txt = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    okk = exists and (len(txt) > 0 if v["expect"] == "nonempty" else True)
    if v.get("contains"):
        okk = okk and (v["contains"] in txt)
    bad += 0 if okk else 1
    print("  %-4s %-40s contains=%s" % ("OK" if okk else "FAIL", p.name[:40],
                                        repr(v.get("contains", ""))[:38]))
for a in d["artifacts"]:
    p = Path(a)
    ex = p.exists()
    if not ex:
        bad += 1
    print("  %-4s artifact %s" % ("OK" if ex else "FAIL", a))
print("失败 %d 条" % bad)
