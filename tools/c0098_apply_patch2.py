# -*- coding: utf-8 -*-
"""C-0098③ 追加补丁（第二个施工补丁，接在 c0098_apply_patch.py 之后跑）。

**为什么要追加**：截图取证（`screenshots/batch-modal-after.png`）暴露一个观感退化——
批量入库表格原来铺满整页，现在进了 `modal()` 的 `.box`（既有 CSS `max-width:800px`），
长文件名把「大小 / 状态」两列挤窄，`未入库` 这个 badge 被折成两行。
最小修法是在这两列上加 `white-space:nowrap`（**不碰任何 CSS**：`modal()`/`.box` 是
既有基础设施，不属本卡区段；设计 token 也不许动）。

用法：python c0098_apply_patch2.py [--apply]
"""
import argparse
import io
import sys

sys.stdout.reconfigure(encoding="utf-8")

TARGET = r"G:\study_agent\tools\dashboard.py"

PAIRS = [
    # ① 表头两列不折行
    ('<th>文件</th><th>大小</th>\n      <th>状态</th></tr></thead><tbody id="batch-list">',
     '<th>文件</th><th style="white-space:nowrap">大小</th>\n'
     '      <th style="white-space:nowrap">状态</th></tr></thead><tbody id="batch-list">'),
    # ② 行内两列不折行（锚用三行，batchCard 的文件格是"一行写完"的，与 fileRows 不同）
    ('      <td>${esc(f.name)}<div class="small muted">${esc(f.rel)}</div></td>\n'
     '      <td class="small">${fmtSize(f.size)}</td>\n'
     '      <td><span class="badge ${st.cls}">${st.label}</span></td>',
     '      <td>${esc(f.name)}<div class="small muted">${esc(f.rel)}</div></td>\n'
     '      <td class="small" style="white-space:nowrap">${fmtSize(f.size)}</td>\n'
     '      <td style="white-space:nowrap"><span class="badge ${st.cls}">${st.label}</span></td>'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--file", default=TARGET)
    a = ap.parse_args()

    src = io.open(a.file, "r", encoding="utf-8", newline="").read()
    if src.count("\n") - src.count("\r\n") != 0:
        raise SystemExit("拒绝继续：文件里有裸 LF")
    out = src
    for old, new in PAIRS:
        old_crlf = old.replace("\n", "\r\n")
        new_crlf = new.replace("\n", "\r\n")
        n = out.count(old_crlf)
        if n != 1:
            raise SystemExit("定位失败：锚出现 %d 次（应为 1 次）：%r" % (n, old_crlf[:80]))
        if new_crlf in out:
            print("  （已应用过，跳过：%r）" % old_crlf[:60])
            continue
        out = out.replace(old_crlf, new_crlf)
        print("  替换 1 处：%r → 加了 white-space:nowrap" % old_crlf[:60])
    if out == src:
        print("文件未变化（补丁此前已应用）")
        return 0
    if out.count("\n") - out.count("\r\n") != 0:
        raise SystemExit("自检失败：写回文本里出现裸 LF，拒绝落盘")
    if not a.apply:
        print("（dry-run，未落盘。加 --apply 才写。）")
        return 0
    with io.open(a.file, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    print("已落盘：%s（CRLF 保持，裸 LF = 0）" % a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
