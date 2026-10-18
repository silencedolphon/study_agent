# -*- coding: utf-8 -*-
"""C-0098③：把 dashboard.py 里的 <script> 抽出来，交给真实的 JS 引擎做语法校验。

为什么值得单独做：看板的前端全在一个 `<script>` 里，Python 侧 `py_compile` 只看得到
"字符串常量"，看不到里面的 JS。模板字符串里的无效转义（`\\8`、`\\x` 之类）会让
**整个脚本**不执行，页面看着像白屏——那种事故静态、无浏览器就能挡住。

做法：抽出 `<script>…</script>` → 写临时 .js → `node --check`（Node 24 自带，项目无第三方依赖）。

用法：python c0098_js_syntax_check.py [--file <dashboard.py>]
输出结尾固定打印 `C0098-JSYNTAX-PASS` 或 `C0098-JSYNTAX-FAIL`。
"""
import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=r"G:\study_agent\tools\dashboard.py")
    a = ap.parse_args()

    src = io.open(a.file, "r", encoding="utf-8", newline="").read()
    head = src.find("<script>")
    tail = src.rfind("</script>")
    if head < 0 or tail < 0 or tail < head:
        print("C0098-JSYNTAX-FAIL")
        print("找不到 <script>…</script>")
        return 1
    js = src[head + len("<script>"):tail]
    line0 = src[:head].count("\n") + 1
    print("<script> 起始行：%d | 抽出 %d 字符 / %d 行" % (line0, len(js), js.count("\n") + 1))

    node = shutil.which("node")
    if not node:
        print("C0098-JSYNTAX-FAIL")
        print("找不到 node，无法做 JS 语法校验")
        return 1

    fd, path = tempfile.mkstemp(suffix=".js", prefix="c0098_script_")
    os.close(fd)
    try:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(js)
        r = subprocess.run([node, "--check", path], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if out:
            print(out[:1500])
        print("node=%s | exit=%d" % (node, r.returncode))
        print("C0098-JSYNTAX-%s" % ("PASS" if r.returncode == 0 else "FAIL"))
        return 0 if r.returncode == 0 else 1
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
