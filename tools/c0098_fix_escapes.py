# -*- coding: utf-8 -*-
"""C-0098③：修正补丁脚本里的模板字符串反斜杠，然后从备份重放一次补丁。

背景（自查发现的真问题，不是猜测）：
    `docsDocRowMenu` / `docsFileRowMenu` 把菜单 HTML 交给 `evPopMount(el, `...`)` 这个
    **未标记模板字符串**。JS 规范里，未标记模板里的 `\\` 后跟非转义字符（如空格）属于
    NotEscapeSequence ⇒ **语法错误**，整个 <script> 会整体不执行。
    我第一版把 title 写成了 `raw\\ 里的原文件保留`（文件里只有一个反斜杠），必须写成
    `raw\\\\ 里的原文件保留`（文件里两个反斜杠，模板字符串求值后显示成一个）。
    普通字符串与注释里单个反斜杠无害（本文件里本来就有这种写法），所以只修这 3 个 title。

脚本做三件事：
    1. 改 `tools/c0098_apply_patch.py` 里那 3 行（用 chr(92) 拼反斜杠，避开转义地狱）；
    2. 用 `_work/dashboard.py.before-c0098` 覆盖 `tools/dashboard.py`（回到未打补丁状态）；
    3. 重放 `c0098_apply_patch.py --apply`，并逐条断言落盘结果。
"""
import io
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

BS = chr(92)
PY = r"G:\study_agent\venv\Scripts\python.exe"
PATCH = r"G:\study_agent\tools\c0098_apply_patch.py"
TARGET = r"G:\study_agent\tools\dashboard.py"
BACKUP = (r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
          r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0098"
          r"\_work\dashboard.py.before-c0098")

MARKS = ['title="移除：', 'title="移除索引：', 'title="删文件：']


def read(p):
    with io.open(p, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


def main():
    src = read(PATCH)
    # 补丁脚本自身用 write 工具创建，行尾是 LF —— 按实际行尾切，别硬编码 CRLF
    nl = "\r\n" if "\r\n" in src else "\n"
    lines = src.split(nl)
    old = "raw" + BS * 2 + " "      # 脚本里现在是 raw\  （Python 转义后只输出一个）
    new = "raw" + BS * 4 + " "      # 目标：脚本里 raw\\ ⇒ 输出 raw\\
    hits = [i for i, l in enumerate(lines) if old in l]
    titles = [i for i in hits if any(m in lines[i] for m in MARKS)]
    others = [i for i in hits if i not in titles]
    for i in others:
        if "//" not in lines[i]:
            raise SystemExit("补丁脚本第 %d 行含 %r 且不在注释里，停手" % (i + 1, old))
    if len(titles) != 3:
        raise SystemExit("补丁脚本里 title 行命中 %d 处（应为 3 处），停手" % len(titles))
    for i in titles:
        lines[i] = lines[i].replace(old, new)
        print("  改脚本第 %d 行（title）：%s" % (i + 1, lines[i].strip()[:100]))
    for i in others:
        # 注释行不需要反斜杠（少一处转义地雷），直接写成「raw 里的原文件」
        lines[i] = lines[i].replace(old, "raw ")
        print("  改脚本第 %d 行（注释，去掉反斜杠）：%s" % (i + 1, lines[i].strip()[:100]))
    out = nl.join(lines)
    if nl == "\r\n" and out.count("\n") - out.count("\r\n") != 0:
        raise SystemExit("补丁脚本自检失败：出现裸 LF")
    write(PATCH, out)
    print("① 补丁脚本已修正（3 处模板字符串反斜杠）")

    shutil.copyfile(BACKUP, TARGET)
    print("② 已从备份恢复 tools/dashboard.py（%d 字节）" % os.path.getsize(TARGET))

    r = subprocess.run([PY, PATCH, "--apply"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print("③ 重放补丁：exit=%d" % r.returncode)
    print((r.stdout or "").strip())
    if r.returncode != 0:
        print((r.stderr or "").strip())
        raise SystemExit("重放失败")

    # ---- 断言落盘结果 ----
    src2 = read(TARGET)
    if src2.count("\n") - src2.count("\r\n") != 0:
        raise SystemExit("断言失败：dashboard.py 出现裸 LF")
    bad = []
    for i, l in enumerate(src2.split("\r\n"), 1):
        if any(m in l for m in MARKS) and ("raw" + BS + " ") in l:
            bad.append(i)
    if bad:
        raise SystemExit("断言失败：第 %s 行仍是单反斜杠（模板字符串会语法错误）" % bad)
    ok = sum(1 for l in src2.split("\r\n")
             if any(m in l for m in MARKS) and ("raw" + BS * 2 + " ") in l)
    print("④ 断言：3 个 title 行都已是双反斜杠（命中 %d 行）；裸 LF = 0" % ok)
    if ok != 3:
        raise SystemExit("断言失败：只有 %d 行符合预期" % ok)
    for must in ("function docsDocRowMenu(el){", "function docsFileRowMenu(el){",
                 "window.docsDocRowMenu = docsDocRowMenu;",
                 "onclick=\"docsDocRowMenu(this)\"", "onclick=\"docsFileRowMenu(this)\""):
        if must not in src2:
            raise SystemExit("断言失败：落盘结果里缺少 %r" % must)
    print("⑤ 断言：两个菜单函数与两处行内调用都在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
