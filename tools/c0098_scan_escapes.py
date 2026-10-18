# -*- coding: utf-8 -*-
"""C-0098③ 静态自检：JS 模板字符串里的转义地雷 + 行尾符。

为什么需要它：`docsDocRowMenu`、`docsFileRowMenu`、`batchCard` 都是把 HTML 交给
**未标记模板字符串**（`evPopMount(el, `…`)` / `return `…`` ）。模板字符串里的转义
规则和普通字符串**不完全一样**，两类问题只在这里才看得出来：

  · **FAIL**：`\\` 后跟数字、或 `\\x`/`\\u` 后不是十六进制 —— 这是 NotEscapeSequence，
    未标记模板里直接**语法错误**，整个 <script> 不执行（页面像白屏）。
  · **WARN**：`\\` 后跟空白或中文（如 `raw\\ 里的原文件`）—— 合法（NonEscapeCharacter
    转义），但**反斜杠会被吃掉**，界面显示成 `raw 里`，与作者原意（显示 `raw\\`）不符。
    这类在别的卡里已经存在；本卡新增的 3 个 title 已按 `raw\\\\ ` 写对，扫描应为 0。

启发式：逐字符状态机，只跟踪 `//`、`/* */`、单/双引号、反引号四种状态
（不解析模板 `${…}` 里再嵌的字符串，够用；误报会打印出来供人判断）。

用法：python c0098_scan_escapes.py [--file <dashboard.py>]
输出结尾固定打印 `C0098-SCAN-PASS` 或 `C0098-SCAN-FAIL`。
"""
import argparse
import io
import sys

sys.stdout.reconfigure(encoding="utf-8")


def scan_template_escapes(src):
    """返回 (fails, warns)，每项是 (行号, 片段, 说明)。"""
    fails, warns = [], []
    i, n = 0, len(src)
    line = 1
    state = "code"          # code | line | block | sq | dq | tpl
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "\n":
            line += 1
        if state == "code":
            if c == "/" and nxt == "/":
                state = "line"; i += 2; continue
            if c == "/" and nxt == "*":
                state = "block"; i += 2; continue
            if c == "'":
                state = "sq"
            elif c == '"':
                state = "dq"
            elif c == "`":
                state = "tpl"
        elif state == "line":
            if c == "\n":
                state = "code"
        elif state == "block":
            if c == "*" and nxt == "/":
                state = "code"; i += 2; continue
        elif state in ("sq", "dq"):
            if c == "\\":
                i += 2; continue
            if (state == "sq" and c == "'") or (state == "dq" and c == '"'):
                state = "code"
        elif state == "tpl":
            if c == "\\":
                e = nxt
                frag = src[i:i + 12].split("\n")[0]
                if e.isdigit() or (e in ("x", "u") and not (
                        src[i + 2:i + 3] and (src[i + 2:i + 3].isalnum() or src[i + 2:i + 3] == "{"))):
                    fails.append((line, frag, "NotEscapeSequence：未标记模板里是语法错误"))
                elif e == "" or e.isspace() or ord(e) > 127:
                    warns.append((line, frag, "合法但会吃掉反斜杠（显示成 raw 里）"))
                i += 2; continue
            if c == "`":
                state = "code"
        i += 1
    return fails, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=r"G:\study_agent\tools\dashboard.py")
    a = ap.parse_args()
    src = io.open(a.file, "r", encoding="utf-8", newline="").read()
    bare_lf = src.count("\n") - src.count("\r\n")

    fails, warns = scan_template_escapes(src)
    print("文件：%s" % a.file)
    print("行数：%d | CRLF 行数：%d | 裸 LF：%d" % (src.count("\r\n") + 1, src.count("\r\n"), bare_lf))
    for ln, frag, why in fails[:20]:
        print("  ✗ FAIL 第 %d 行 %r —— %s" % (ln, frag, why))
    for ln, frag, why in warns[:20]:
        print("  · WARN 第 %d 行 %r —— %s" % (ln, frag, why))
    print("模板字符串转义：FAIL %d 处 / WARN %d 处" % (len(fails), len(warns)))
    ok = (not fails) and bare_lf == 0
    print("C0098-SCAN-%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
