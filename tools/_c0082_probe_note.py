# -*- coding: utf-8 -*-
"""卡 C-0082 的正文剥离/回写**快速探针**（施工期自查用，不是交付的验证脚本）。

只跑纯函数：`note_body` 的五种边界 + `_note_rebuild` 的回写形状。
交付验证见 `tools/verify_c0082_offline.py`。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import study  # noqa: E402

OK = 0
BAD = 0


def ck(cond, label, detail=""):
    global OK, BAD
    if cond:
        OK += 1
        print("  [OK] %s%s" % (label, ("  —— " + str(detail)) if detail else ""))
    else:
        BAD += 1
        print("  [FAIL] %s%s" % (label, ("  —— " + str(detail)) if detail else ""))
    return bool(cond)


BODY = "第一行正文。\n第二行也要在。"
FULL = ("---\ntitle: 自检笔记\nkind: note\ncreated: 1700000000\n"
        "updated: 1700000000\ntags: [\"知识点笔记\"]\n---\n\n"
        "# 自检笔记\n\n" + BODY + "\n\n## 出处\n\n- 教材.pdf › 3.1 › p.12\n")

print("[1] note_body 五种边界")
ck(study.note_body(FULL) == BODY, "有 fm：剥掉 fm/自动标题/出处段", repr(study.note_body(FULL)))
old = "# 旧式笔记\n\n没有 front-matter 的老写法。\n"
ck(study.note_body(old) == old, "无 fm：整篇原文照旧（逐字）", repr(study.note_body(old)))
t2 = FULL.replace("# 自检笔记\n", "# 我自己改的标题\n", 1)
ck("我自己改的标题" in study.note_body(t2) and "第一行正文。" in study.note_body(t2),
   "标题≠fm.title：标题保留，正文照剥", repr(study.note_body(t2))[:80])
t3 = ("---\ntitle: 带出处\n---\n\n# 带出处\n\n正文里有出处。\n\n## 出处\n\n"
      "- 这是我正文里自己写的\n\n后面还有别的正文。\n")
b3 = study.note_body(t3)
ck("## 出处" in b3 and "后面还有别的正文。" in b3,
   "正文含「## 出处」但后面还有内容：不切", repr(b3))
t4 = ("---\ntitle: 尾巴\n---\n\n# 尾巴\n\n正文。\n\n## 出处\n\n- 教材 p.1\n\ntail\n")
ck("tail" in study.note_body(t4), "出处段后有多余内容：不切", repr(study.note_body(t4)))
t5 = ("---\ntitle: 正常\n---\n\n# 正常\n\n人写的。\n\n## 出处\n\n- a\n- b\n\n")
ck(study.note_body(t5) == "人写的。", "出处段后只有空行/列表行：切掉", repr(study.note_body(t5)))

print("\n[2] _note_rebuild：只换正文，fm 与出处段逐字保留")
rt = FULL.replace("\n", "\r\n")
new = study._note_rebuild(rt, "改过的正文。")
fm_old = rt[:rt.index("\r\n---", 3) + 5]
fm_new = new[:new.index("\r\n---", 3) + 5]
ck("第一行正文。" not in fm_old, "样本的 fm 里本来没有正文")
_norm = lambda s: re.sub(r"updated: \d+", "updated: X", s)  # noqa: E731
ck(_norm(fm_new) == _norm(fm_old),
   "front-matter 逐字未变（updated 的值除外）", repr(fm_new))
ck("## 出处\r\n\r\n- 教材.pdf › 3.1 › p.12\r\n" in new
   and new.endswith("- 教材.pdf › 3.1 › p.12\r\n"),
   "「## 出处」段逐字未变（含末尾换行）", repr(new[-28:]))
ck("# 自检笔记\r\n\r\n改过的正文。" in new, "标题与正文之间保留空行（不贴一起）",
   repr(new[new.index("# 自检"):][:30]))
ck(new.count("updated: ") == 1 and "updated: 1700000000" not in new,
   "updated: 就地改成一个新值",
   [l for l in new.split("\r\n") if l.startswith("updated:")])
ck([l.split(":")[0] for l in rt.split("\r\n")[:7]]
   == [l.split(":")[0] for l in new.split("\r\n")[:7]],
   "fm 键的集合与顺序未变", [l.split(":")[0] for l in new.split("\r\n")[:7]])
ck("第一行正文。" not in new, "旧正文已被换掉")

print("\n[3] 无 updated 键时不新增")
t6 = "---\ntitle: 无时间\n---\n\n# 无时间\n\n正文。\n"
n6 = study._note_rebuild(t6, "新正文。")
ck("updated" not in n6, "fm 里没有 updated：不回添", repr(n6))

print("\n结论：OK=%d BAD=%d" % (OK, BAD))
sys.exit(1 if BAD else 0)
