"""检查看板前端 JavaScript 的语法和关键函数定义次数。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "core" / "frontend"


def main() -> int:
    scripts = json.loads((FRONTEND_DIR / "manifest.json").read_text(encoding="utf-8"))["scripts"]
    js = "".join((FRONTEND_DIR / name).read_text(encoding="utf-8") for name in scripts)
    print(f"检查 JS {len(js.splitlines())} 行、{len(scripts)} 个文件")
    for name in ("bindCiteClicks", "evPopShow", "evPopClose", "evPopPlace",
                 "setMsgEvidence", "msgEvidence", "linkCites", "mdToHtml"):
        n = len(re.findall(rf"function {name}\b", js))
        flag = "" if n == 1 else "   ← 定义次数异常"
        print(f"  function {name}: {n} 次{flag}")
    print()
    r = subprocess.run(["node", "--check"], input=js,
                       capture_output=True, text=True)
    if r.returncode == 0:
        print("node --check: 语法通过")
        return 0
    print("node --check: ★语法错误★")
    print(r.stderr[:3000])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
