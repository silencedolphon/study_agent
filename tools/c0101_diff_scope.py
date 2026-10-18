# -*- coding: utf-8 -*-
"""卡 C-0101：把「我这次改了什么」量出来——只比 `dashboard.py` 与它自己的改前快照。

用途：卡面红线要求「不扩大范围、不碰卡②③④的区段」。git 的 diff 是相对 HEAD 的，
里面混着前序卡（C-0093/C-0094/C-0100…）的未提交改动，看不出**本卡**动了哪几段。
所以本卡在打补丁前留了快照 `tools/dashboard.py.c0101-bak`，这里做三方对照：

    diff(bak → 现在)   = 本卡改的（应当只在预习面板那两处）
    并把改动区间聚成 [起, 止] 行段打印，人工一眼能核。

用法：python tools\\c0101_diff_scope.py
"""
from __future__ import annotations

import difflib
import io
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
NEW = ROOT / "tools" / "dashboard.py"
OLD = ROOT / "tools" / "dashboard.py.c0101-bak"
# 允许改动的区段（行号以**改后**文件为准，卡面划的圈）
#   ①后端：`api_preview_options` 本体 + 它自己的两个 helper（C-0101 注释块也在这一段）
#   ②路由：`GET /api/preview/options` 那一行把 scope 透传进去
#   ③前端：**预习面板函数族**整段——kpPreviewBtn / kpPreviewRow / PVP /
#     openKpsPreview / pvLoad / pvOpenSettings / pvSetScope / pvGoDocs / pvDocLabel /
#     pvRender / pvEstRender / pvPickCh / pvStart / pvShowFail / pvPaintProgress
#     （其中 `openKpsPreview` 的骨架本体**没有**出现在 diff 里，只动了它内部的列表渲染）
ALLOWED = [
    ("后端：预习 options 及其 helper", 2200, 2400),
    ("路由：/api/preview/options 透传 scope", 6290, 6320),
    ("前端：预习面板函数族", 9820, 10160),
]


def main() -> int:
    if not OLD.exists():
        print("C0101-DIFF-FAIL 找不到改前快照 %s" % OLD)
        return 1
    a = io.open(str(OLD), "r", encoding="utf-8", newline="").read().split("\r\n")
    b = io.open(str(NEW), "r", encoding="utf-8", newline="").read().split("\r\n")
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
        print("C0101-DIFF-FAIL 有 %d 个改动块落在允许区段之外" % len(bad))
        return 1
    print("C0101-DIFF-PASS 全部改动都落在：%s"
          % " / ".join(n for n, _s, _e in ALLOWED))
    return 0


if __name__ == "__main__":
    sys.exit(main())
