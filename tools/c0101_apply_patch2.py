# -*- coding: utf-8 -*-
"""卡 C-0101 补丁②：把空态判据从「没有教材」放宽到「当前范围下一份资料都没有」。

为什么补这一刀：补丁① 的空态只在「仅教材」下触发。若整个学科库是空的
（人刚建库/还没入库），切到「全部资料」也会是 0 份，那时面板只剩一个带边框的
空框加一句「先在上面选一份教材」——正是卡面不要的"空白框"。这里按范围给两种
说法（「没有教材」/「没有资料」），两条路（教材专线入库 / 换学科库）都保留。

仍然只改 `pvRender()` 的列表渲染。用法同补丁①：
    .\\venv\\Scripts\\python.exe -X utf8 tools\\c0101_apply_patch2.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "tools" / "dashboard.py"

OLD_HEAD = (
    "  const noTb = (scope === 'textbook') && !docs.length;\r\n"
    "  const popEmpty = !Number(o.n_docs_total || 0) && !docs.length;\r\n"
    "  const emptyBox = noTb ? `<div style=\"margin:2px 0 12px;padding:12px 14px;\r\n"
    "      border:1px solid var(--warn);border-radius:var(--radius)\">\r\n"
    "    <div><span class=\"badge warn\">没有教材</span>\r\n"
    "      <span class=\"small\">当前学科库里<b>一份入库的教材都没有</b>\r\n"
    "      （role=textbook）——预习是<b>按章</b>做的，而<b>章只有教材有</b>\r\n"
    "      （章结构是「教材入库（专线）」按章切块时写进库的），课件没有章可预习。</span></div>\r\n"
    "    <div class=\"small muted\" style=\"margin:8px 0;line-height:1.8\">${\r\n"
    "      popEmpty ? '这个学科库里现在<b>一份资料都没有</b>。'\r\n"
    "      : `库里现有 <b>${Number(o.n_docs_total || 0)}</b> 份资料，但<b>没有一份是教材</b>。`}</div>\r\n"
)

NEW_HEAD = (
    "  const noDocs = !docs.length;   // 当前范围下一份资料都没有\r\n"
    "  const noTb = noDocs && (scope === 'textbook');   // 其中最常见的一种：库里没有教材\r\n"
    "  const emptyBox = noDocs ? `<div style=\"margin:2px 0 12px;padding:12px 14px;\r\n"
    "      border:1px solid var(--warn);border-radius:var(--radius)\">\r\n"
    "    <div><span class=\"badge warn\">${noTb ? '没有教材' : '没有资料'}</span>\r\n"
    "      <span class=\"small\">${noTb\r\n"
    "        ? '当前学科库里<b>一份入库的教材都没有</b>（role=textbook）——'\r\n"
    "          + '预习是<b>按章</b>做的，而<b>章只有教材有</b>（章结构是'\r\n"
    "          + '「教材入库（专线）」按章切块时写进库的），课件没有章可预习。'\r\n"
    "        : '这个学科库里现在<b>一份资料都没有</b>，所以也没得选。'}</span></div>\r\n"
    "    <div class=\"small muted\" style=\"margin:8px 0;line-height:1.8\">${noTb\r\n"
    "      ? `库里现有 <b>${Number(o.n_docs_total || 0)}</b> 份资料，但<b>没有一份是教材</b>。`\r\n"
    "      : '先入库一份教材，预习才有章可选。'}</div>\r\n"
)

OLD_TAIL = (
    "  let chTable = '';\r\n"
    "  if (noTb){\r\n"
    "    // 空态已经由上面的 emptyBox 说清楚了：这里**什么都不画**，\r\n"
    "    // 免得人看到的是一个带边框的空框（卡面明确不要空白框）。\r\n"
    "    chTable = '';\r\n"
)

NEW_TAIL = (
    "  let chTable = '';\r\n"
    "  if (noDocs){\r\n"
    "    // 空态已经由上面的 emptyBox 说清楚了：这里**什么都不画**，\r\n"
    "    // 免得人看到的是一个带边框的空框（卡面明确不要空白框）。\r\n"
    "    chTable = '';\r\n"
)

OLD_WRAP = "  const chWrap = noTb ? '' : `<div id=\"pv-chwrap\" style=\"max-height:34vh;overflow:auto;\r\n"
NEW_WRAP = "  const chWrap = noDocs ? '' : `<div id=\"pv-chwrap\" style=\"max-height:34vh;overflow:auto;\r\n"

PATCHES = [
    ("空态判据 noDocs + 两种文案", OLD_HEAD, NEW_HEAD),
    ("章表分支用 noDocs", OLD_TAIL, NEW_TAIL),
    ("章表容器用 noDocs", OLD_WRAP, NEW_WRAP),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    src = TARGET.read_bytes().decode("utf-8")
    done = [n for n, o, _w in PATCHES if o not in src]
    if a.check:
        print("已应用 %d/%d 处" % (len(done), len(PATCHES)))
        return 0
    if done:
        print("C0101-PATCH2-SKIP 已应用过：%s" % ", ".join(done))
        return 0
    out = src
    for name, old, new in PATCHES:
        n = out.count(old)
        if n != 1:
            print("C0101-PATCH2-FAIL %s：锚点 %d 次" % (name, n))
            return 1
        out = out.replace(old, new, 1)
        print("  [改] %s" % name)
    crlf = out.count("\r\n")
    bare = out.count("\n") - crlf
    if bare != 0 or out.count("\r") != crlf:
        print("C0101-PATCH2-FAIL 换行符被破坏：裸 LF=%d" % bare)
        return 1
    TARGET.write_bytes(out.encode("utf-8"))
    print("CRLF=%d 裸LF=%d" % (crlf, bare))
    print("C0101-PATCH2-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
