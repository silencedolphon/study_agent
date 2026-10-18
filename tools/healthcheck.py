"""一键健康检查：开发者/验收者每次改动后先跑它。

它只做**快速、只读、可重复**的检查，不做隔离环境搭建（那是 tests/ 的事）：

1. 解释器与核心模块导入（core/*.py 逐个 import）
2. 产品脚本语法（core/、tools/ 下的运行必需脚本）
3. 配置与数据库（DB 存在、integrity_check、文档/切块计数）
4. ``study.py check``（依赖 / 模型 / 嵌入推理 / 数据库）
5. MCP：``serve_mcp.py --selftest`` + 真实 stdio 握手
6. 看板：端口探测 + ``GET /`` 与 ``GET /api/stats``
7. Git 状态（信息项，不计失败）

用法::

    venv\\Scripts\\python.exe -X utf8 tools\\healthcheck.py
    venv\\Scripts\\python.exe -X utf8 tools\\healthcheck.py --json
    venv\\Scripts\\python.exe -X utf8 tools\\healthcheck.py --port 8757 --no-git

退出码：0 = 全部通过；1 = 有 FAIL；2 = 有 SKIP（不影响退出码，仅提示）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 脚本位于 tools/ 下，直接把项目根加入 sys.path，才能 import core.*
sys.path.insert(0, str(ROOT))
PY = ROOT / "venv" / "Scripts" / "python.exe"
DASHBOARD = ROOT / "tools" / "dashboard.py"
STUDY = ROOT / "tools" / "study.py"
MCP = ROOT / "tools" / "serve_mcp.py"
MCP_HANDSHAKE = ROOT / "tools" / "verify_mcp_handshake.py"

# 运行必需的产品脚本（不含历史 verify_*/c0xxx 脚本）
REQUIRED_TOOLS = [
    "dashboard.py", "study.py", "serve_mcp.py", "kp_build.py",
    "terms_build.py", "reimport_all.py", "download_model.py",
    "verify_mcp_handshake.py",
]

results: list[dict] = []


def add(name: str, status: str, detail: str = "", elapsed: float = 0.0) -> None:
    results.append({"name": name, "status": status, "detail": detail,
                    "elapsed": round(elapsed, 2)})
    mark = {"PASS": "[OK  ]", "FAIL": "[FAIL]", "SKIP": "[SKIP]", "INFO": "[INFO]"}[status]
    print(f"{mark} {name}" + (f"  ({detail})" if detail else ""))


def run(cmd: list[str], timeout: float = 120.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"超时（{timeout}s）"
    except Exception as e:  # noqa: BLE001
        return -2, f"{type(e).__name__}: {e}"


def check_python() -> None:
    t = time.time()
    v = sys.version.split()[0]
    ok = v.startswith("3.")
    add("解释器", "PASS" if ok else "FAIL",
        f"Python {v} @ {sys.executable}", time.time() - t)


def check_core_imports() -> None:
    t = time.time()
    import importlib
    core = ROOT / "core"
    mods = sorted(p.stem for p in core.glob("*.py") if p.stem != "__init__")
    bad = []
    for m in mods:
        try:
            importlib.import_module("core." + m)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{m}: {type(e).__name__}: {e}")
    add("core 模块导入", "PASS" if not bad else "FAIL",
        f"{len(mods) - len(bad)}/{len(mods)} 成功"
        + ("" if not bad else "；失败: " + "; ".join(bad[:5])), time.time() - t)


def check_syntax() -> None:
    t = time.time()
    import py_compile
    files = list((ROOT / "core").glob("*.py")) + [ROOT / "tools" / n for n in REQUIRED_TOOLS]
    bad = []
    for f in files:
        if not f.exists():
            bad.append(f"{f.name}: 缺失")
            continue
        try:
            py_compile.compile(str(f), doraise=True)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{f.name}: {type(e).__name__}")
    add("产品脚本语法", "PASS" if not bad else "FAIL",
        f"{len(files) - len(bad)}/{len(files)} 通过"
        + ("" if not bad else "；失败: " + "; ".join(bad)), time.time() - t)


def check_db() -> None:
    t = time.time()
    sys.path.insert(0, str(ROOT))
    try:
        from core import config, db  # noqa: F401
        db_path = Path(config.DB_PATH)
        if not db_path.exists():
            add("数据库", "FAIL", f"{db_path} 不存在", time.time() - t)
            return
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()
        add("数据库", "PASS" if integ == "ok" else "FAIL",
            f"{db_path.name} integrity={integ} documents={docs} chunks={chunks}",
            time.time() - t)
    except Exception as e:  # noqa: BLE001
        add("数据库", "FAIL", f"{type(e).__name__}: {e}", time.time() - t)


def check_study_check() -> None:
    t = time.time()
    rc, out = run([sys.executable, "-X", "utf8", str(STUDY), "check"], timeout=180)
    ok = "总体: 全部就绪" in out
    tail = [ln.strip() for ln in out.splitlines() if ln.strip()][-1:] or [""]
    add("study.py check", "PASS" if ok and rc == 0 else "FAIL",
        tail[0][:120], time.time() - t)


def check_mcp() -> None:
    t = time.time()
    rc, out = run([sys.executable, "-X", "utf8", str(MCP), "--selftest"], timeout=180)
    add("MCP 协议自测", "PASS" if rc == 0 and "7/7" in out else "FAIL",
        "7/7" if "7/7" in out else out.strip().splitlines()[-1:][0][:120] if out.strip() else "无输出",
        time.time() - t)

    t = time.time()
    if not MCP_HANDSHAKE.exists():
        add("MCP stdio 握手", "SKIP", "verify_mcp_handshake.py 不存在", 0.0)
        return
    rc, out = run([sys.executable, "-X", "utf8", str(MCP_HANDSHAKE)], timeout=180)
    passed = "RESULT: PASS" in out
    detail = "10/10" if passed else (out.strip().splitlines()[-1] if out.strip() else "无输出")
    add("MCP stdio 握手", "PASS" if passed else "FAIL", detail[:120], time.time() - t)


def check_dashboard(port: int) -> None:
    t = time.time()
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as r:
            html = r.read(2000)
        ok = r.status == 200 and b"<!DOCTYPE html>" in html
        add("看板首页", "PASS" if ok else "FAIL",
            f"{base} HTTP {r.status}，{len(html)}+ 字节", time.time() - t)
    except Exception as e:  # noqa: BLE001
        add("看板首页", "SKIP",
            f"{base} 未运行或不可达（{type(e).__name__}）；启动: 看板.cmd", time.time() - t)
        return

    t = time.time()
    try:
        with urllib.request.urlopen(base + "/api/stats", timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        ok = r.status == 200 and "documents" in data
        docs = data.get("documents", {}).get("total")
        add("看板 /api/stats", "PASS" if ok else "FAIL",
            f"HTTP {r.status}，documents={docs}", time.time() - t)
    except Exception as e:  # noqa: BLE001
        add("看板 /api/stats", "FAIL", f"{type(e).__name__}: {e}", time.time() - t)


def check_git() -> None:
    t = time.time()
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        add("Git 仓库", "INFO", "未初始化", 0.0)
        return
    rc, out = run(["git", "-C", str(ROOT), "status", "--porcelain"], timeout=60)
    dirty = len([ln for ln in out.splitlines() if ln.strip()])
    rc2, head = run(["git", "-C", str(ROOT), "log", "--oneline", "-1"], timeout=60)
    add("Git 仓库", "INFO",
        f"{head.strip()[:80]}；未提交变更 {dirty} 项", time.time() - t)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="study_agent 一键健康检查")
    ap.add_argument("--port", type=int, default=8756, help="看板端口，默认 8756")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--no-git", action="store_true", help="跳过 Git 检查")
    args = ap.parse_args(argv)

    if not args.json:
        print("=== study_agent 健康检查 ===")
        print(f"项目根: {ROOT}")
        print(f"解释器: {sys.executable}")
        print()

    check_python()
    check_core_imports()
    check_syntax()
    check_db()
    check_study_check()
    check_mcp()
    check_dashboard(args.port)
    if not args.no_git:
        check_git()

    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    n_skip = sum(1 for r in results if r["status"] == "SKIP")

    if args.json:
        print(json.dumps({"results": results, "pass": n_pass,
                          "fail": n_fail, "skip": n_skip},
                         ensure_ascii=False, indent=2))
    else:
        print(f"\n结果: PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip}")
        if n_fail == 0:
            print("RESULT: PASS")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
