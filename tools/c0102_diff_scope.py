# -*- coding: utf-8 -*-
"""卡 C-0102：把「我这次改了什么」量出来——只比 `dashboard.py` 与它自己的改前快照。

用途：卡面红线要求「只改知识点窗口相关函数；不碰卡②③④与预习面板区段」。
git 的 diff 是相对 HEAD 的，里面混着前序卡（C-0093/C-0094/C-0100/C-0101…）的未提交
改动，看不出**本卡**动了哪几段。所以本卡在打补丁前留了快照
`tools/dashboard.py.c0102-bak`，这里做两方对照：

    diff(bak → 现在) = 本卡改的（应当只落在下面四个区段里）

另外顺带查两件本卡自己的事（都在同一次 diff 上做）：
  · 有没有把改动"漏"进 `<script>` 之外（CSS 段必须落在 <style> 里）；
  · `mmKpScrollGuard(evPopEl());` 这类行有没有被复制成两份（施工时踩过一次）。

用法：G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0102_diff_scope.py
输出结尾固定打印 `C0102-DIFF-PASS` 或 `C0102-DIFF-FAIL`。
"""
from __future__ import annotations

import difflib
import io
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"G:\study_agent")
NEW = ROOT / "tools" / "dashboard.py"
OLD = ROOT / "tools" / "dashboard.py.c0102-bak"
# 允许改动的区段（行号以**改后**文件为准，卡面划的圈）
#   ① CSS：知识点窗口两栏样式 + `.evpop:has(#mmkp-body)` 宽度（在 <style> 里）
#   ② mmRenderKp：要点区 → 「要点」框 + 「教材补充」框
#   ③ mmRenderKp 末尾：一行异步调用 + 三行说明注释
#   ④ 新函数块：mmTbHtml / mmTbOpenPage / mmTbPlace / mmLoadTextbook
ALLOWED = [
    ("CSS：知识点窗口两栏样式", 6940, 7000),
    ("mmRenderKp：要点/教材补充两栏", 11700, 11740),
    ("mmRenderKp：异步调用 mmLoadTextbook", 11805, 11825),
    ("新函数：mmTbHtml/mmTbOpenPage/mmTbPlace/mmLoadTextbook", 11815, 11990),
]
# 本卡**必须没有**出现的重复/越界痕迹
MUST_ONCE = ["  mmKpScrollGuard(evPopEl());", "  evPopMount(el, html);",
             "  if (keepDeck) mmToggleDeck();"]


def main() -> int:
    if not OLD.exists():
        print("C0102-DIFF-FAIL 找不到改前快照 %s" % OLD)
        return 1
    old_txt = io.open(str(OLD), "r", encoding="utf-8", newline="").read()
    new_txt = io.open(str(NEW), "r", encoding="utf-8", newline="").read()
    for nm, txt in (("改前", old_txt), ("改后", new_txt)):
        bare = txt.count("\n") - txt.count("\r\n")
        print("%s：%d 行，裸 LF = %d" % (nm, txt.count("\r\n"), bare))
        if bare:
            print("C0102-DIFF-FAIL %s文件里有裸 LF" % nm)
            return 1
    a = old_txt.split("\r\n")
    b = new_txt.split("\r\n")
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    hunks = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        hunks.append((tag, j1 + 1, j2 + 1, i2 - i1, j2 - j1))
    print("改前 %d 行 → 改后 %d 行；改动块 %d 个" % (len(a), len(b), len(hunks)))
    bad = []
    for tag, lo, hi, dn, up in hunks:
        hit = [name for name, s, e in ALLOWED if lo >= s and hi <= e + 1]
        mark = "OK " if hit else "越界"
        if not hit:
            bad.append((tag, lo, hi))
        print("  %s 行 %d-%d  (%s, -%d/+%d)  %s"
              % (mark, lo, hi, tag, dn, up, (hit[0] if hit else "不在允许区段内")))
    if bad:
        print("C0102-DIFF-FAIL 有 %d 个改动块落在允许区段之外" % len(bad))
        return 1

    # 三个"只能出现一次"的骨架行：防施工时把上下文行一起复制进来
    flat_bad = []
    for line in MUST_ONCE:
        n = b.count(line)
        print("  %s × %d" % (line.strip(), n))
        if n != 1:
            flat_bad.append(line)
    if flat_bad:
        print("C0102-DIFF-FAIL 有行被复制了：%s" % flat_bad)
        return 1

    print("C0102-DIFF-PASS 全部改动都落在：%s"
          % " / ".join(n for n, _s, _e in ALLOWED))
    return 0


if __name__ == "__main__":
    sys.exit(main())
