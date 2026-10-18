# -*- coding: utf-8 -*-
"""C-0100 施工补丁 3（修复 + 空态入口）。

## 修什么（★ 如实记账）

补丁 1 的 P3 本该是**纯插入**（在 `<span style="flex:1"></span>` 与卡④的「?」按钮之间
插一行预习按钮），但我把锚点那两行也抄进了新块里 → 落盘后知识点页工具栏变成了：

    <span style="flex:1"></span>
    <span style="flex:1"></span>          ← 多出来的
    <button class="act" ...>📖 预习</button>
    <button class="ghost" ...>?</button>  ← 多出来的（卡④那个「?」被复制成两个）
    <button class="ghost" ...>?</button>

这是**本卡自己引入的缺陷**（不是别人的改动痕迹），在这里删掉那两处重复行。

## 为什么加空态入口

真库 `knowledge_points` **一条都没有**（实测 0 条），`viewKps` 于是走空态分支——那个
分支**没有工具栏**。只按卡面把入口加在工具栏里，人在真实看板上仍然看不到预习入口，
正好又落回本卡要补的那个"入口真空"。所以：按钮本体抽成**唯一真源** `kpPreviewBtn()`，
空态用 `kpPreviewRow()` 单独占一行，两处渲染的是同一个定义。

## 只动这几处

  A 非空态工具栏：去掉两行重复 + 按钮改成 `${kpPreviewBtn()}`
  B 空态拼接：`${kpBuildCard()}` 前面**只增** `${kpPreviewRow()}`（顺序不变）
  C 在预习面板函数块开头新增 `kpPreviewBtn()` / `kpPreviewRow()` 两个小函数

行尾符：读进来是 CRLF，写回去仍是 CRLF（末尾自检 bare LF == 0）。

用法：
    python tools\\c0100_apply_patch3.py            # dry-run
    python tools\\c0100_apply_patch3.py --apply    # 落盘
"""
import argparse
import io
import sys

sys.stdout.reconfigure(encoding="utf-8")

TARGET = r"G:\study_agent\tools\dashboard.py"

# ---- A：非空态工具栏（8 行 → 4 行） ----
A_OLD = "\r\n".join([
    '        <span style="flex:1"></span>',
    '        <span style="flex:1"></span>',
    '        <button class="act" onclick="openKpsPreview()"',
    '          title="预习总结：选一份资料 → 选一章 → 看预估 → 开始（整章 1 次调用；'
    '默认关闭，可在设置中心打开）">📖 预习</button>',
    '        <button class="ghost" onclick="openKpsHelp()"',
    '          title="本页怎么用（整理 / 导图 / 名词解释）">?</button>',
    '        <button class="ghost" onclick="openKpsHelp()"',
    '          title="本页怎么用（整理 / 导图 / 名词解释）">?</button>',
])
A_NEW = "\r\n".join([
    '        <span style="flex:1"></span>',
    '        ${kpPreviewBtn()}',
    '        <button class="ghost" onclick="openKpsHelp()"',
    '          title="本页怎么用（整理 / 导图 / 名词解释）">?</button>',
])

# ---- B：空态拼接（只增一个片段，顺序不变） ----
B_OLD = "    $('#view').innerHTML = `${kpBuildCard()}"
B_NEW = "    $('#view').innerHTML = `${kpPreviewRow()}${kpBuildCard()}"

# ---- C：新增两个小函数（插在 `const PVP = ...` 之前） ----
C_ANCHOR = "const PVP = { opts: null, docId: 0, chNo: 0, est: null, force: false, timer: null };"
C_NEW = "\r\n".join([
    '// 入口按钮的**唯一真源**：知识点非空时它在工具栏那一行里（`viewKps` 的 row），',
    '// 知识点为空时整页走空态分支、**没有工具栏**，由 `kpPreviewRow()` 单独占一行——',
    '// 两处渲染的是同一个定义，不抄第二份。',
    '// ⚠ 空态也必须有入口：真库 `knowledge_points` 实测 **0 条**，空态就是多数人看到的',
    '//   那一屏；只在工具栏放入口，等于人还是找不到预习（本卡要补的就是这个真空）。',
    'function kpPreviewBtn(){',
    '  return `<button class="act" onclick="openKpsPreview()"',
    '    title="预习总结：选一份资料 → 选一章 → 看预估 → 开始（整章 1 次调用；'
    '默认关闭，可在设置中心打开）">📖 预习</button>`;',
    '}',
    '',
    'function kpPreviewRow(){',
    '  return `<div class="row" style="margin-bottom:12px">',
    '    <span style="flex:1"></span>${kpPreviewBtn()}</div>`;',
    '}',
    '',
    C_ANCHOR,
])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    with io.open(TARGET, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    if raw.count("\n") - raw.count("\r\n") != 0:
        raise SystemExit("停手：目标文件里本来就有裸 LF")
    for name, needle in (("A_OLD", A_OLD), ("B_OLD", B_OLD), ("C_ANCHOR", C_ANCHOR)):
        n = raw.count(needle)
        if n != 1:
            raise SystemExit("定位失败[%s]：命中 %d 次（应为 1）" % (name, n))
    if "function kpPreviewBtn(){" in raw:
        raise SystemExit("停手：kpPreviewBtn 已在文件里（补丁打过）")
    print("[A] 非空态工具栏：8 行 → 4 行（删掉重复的 span 与重复的「?」按钮）")
    print("[B] 空态拼接：`${kpBuildCard()}` 前只增 `${kpPreviewRow()}`（顺序不变）")
    print("[C] 新增 kpPreviewBtn() / kpPreviewRow()（插在 const PVP 之前）")

    if not a.apply:
        print("\n（dry-run，未落盘。加 --apply 才写。）")
        return 0

    out = raw.replace(A_OLD, A_NEW).replace(B_OLD, B_NEW).replace(C_ANCHOR, C_NEW)
    if out.count("\n") - out.count("\r\n") != 0:
        raise SystemExit("自检失败：写回文本里出现了裸 LF，拒绝落盘")
    with io.open(TARGET, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    print("\n已落盘：%s（行数 %d → %d，CRLF 保持，裸 LF = %d）"
          % (TARGET, raw.count("\r\n"), out.count("\r\n"), out.count("\n") - out.count("\r\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
