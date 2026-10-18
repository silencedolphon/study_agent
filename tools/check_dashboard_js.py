"""把 dashboard.py 里的内联 JS 抽出来，交给 node --check 做语法检查。

存在的理由：dashboard.py 是一个装着 HTML/CSS/JS 的 Python 字符串，
Python 语法检查管不到里面的 JS。改了前端却只在浏览器里看结果，
写错一个括号就白跑一轮。这个脚本能先把语法错误挡住。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "tools" / "dashboard.py").read_text(encoding="utf-8")


def main() -> int:
    html = re.search(r'INDEX_HTML = r"""(.*?)"""', SRC, re.S).group(1)
    js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    # 产物放到系统临时目录：documents 目录里不该混进构建产物
    import tempfile
    out = Path(tempfile.gettempdir()) / "dsh_dashboard_check.js"
    out.write_text(js, encoding="utf-8")
    print(f"抽出 JS {len(js.splitlines())} 行 -> {out}")
    for name in ("bindCiteClicks", "evPopShow", "evPopClose", "evPopPlace",
                 "setMsgEvidence", "msgEvidence", "linkCites", "mdToHtml"):
        n = len(re.findall(rf"function {name}\b", js))
        flag = "" if n == 1 else "   ← 定义次数异常"
        print(f"  function {name}: {n} 次{flag}")
    print()
    r = subprocess.run(["node", "--check", str(out)],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print("node --check: 语法通过")
        return 0
    print("node --check: ★语法错误★")
    print(r.stderr[:3000])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())