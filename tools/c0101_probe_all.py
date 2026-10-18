# -*- coding: utf-8 -*-
"""卡 C-0101 一键重跑真浏览器验证：起两个**临时实例** → 跑探针 → 关掉、清理。

它把"大管家亲跑"需要的三件事串起来，免得手工记端口与参数：
    ① data 模式：临时库 = **真库的副本**（只读真库、单向复制）→ 验默认只列 2 份教材、
       doc#91 的 42 章、切「全部资料」看到 38 份、再切回来；
    ② empty 模式：临时库是全新空库 → 验空态引导（不是空白框）；
    ③ 两个实例都用隔离 `KB_*` + `KB_SETTINGS_FILE`，跑完即杀、临时目录即清。
**绝不碰 8756，也绝不写真库。**

用法（绝对路径，不依赖 cwd）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0101_probe_all.py
输出结尾固定打印 `C0101-UI-ALL-PASS` 或 `C0101-UI-ALL-FAIL`。
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LAUNCH = ROOT / "tools" / "c0101_tmp_launch.py"
PROBE = ROOT / "tools" / "c0101_probe_ui.py"
REAL_DB = ROOT / "index" / "study.db"
# 端口刻意避开 8756（人正在用的那个实例）
CASES = (("data", 8793, 9337), ("empty", 8794, 9338))


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, seconds: float = 90.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if port_open(port):
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    if not REAL_DB.exists():
        print("C0101-UI-ALL-FAIL 找不到真库 %s" % REAL_DB)
        return 1
    outdir = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
                  r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0101\screenshots")
    outdir.mkdir(parents=True, exist_ok=True)
    results = []
    for mode, port, cdp in CASES:
        if port_open(port):
            print("C0101-UI-ALL-FAIL 端口 %d 已被占用（换个端口再跑）" % port)
            return 1
        tmp = Path(tempfile.gettempdir()) / ("c0101_tmp_" + mode)
        shutil.rmtree(tmp, ignore_errors=True)
        args = [PY, "-X", "utf8", str(LAUNCH), str(tmp), str(port)]
        if mode == "data":
            args += ["--copy-db", str(REAL_DB)]
        print("=== 起临时实例 mode=%s port=%d（数据根 %s）===" % (mode, port, tmp),
              flush=True)
        proc = subprocess.Popen(args, cwd=str(ROOT),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = 1
        try:
            if not wait_port(port):
                print("C0101-UI-ALL-FAIL 临时实例 %d 没起来" % port)
                return 1
            time.sleep(1.0)
            rc = subprocess.call([PY, "-X", "utf8", str(PROBE), "--mode", mode,
                                  "--port", str(port), "--cdp-port", str(cdp),
                                  "--outdir", str(outdir)], cwd=str(ROOT))
        finally:
            proc.kill()
            try:
                proc.wait(timeout=15)
            except Exception:  # noqa: BLE001
                pass
            shutil.rmtree(tmp, ignore_errors=True)
        results.append((mode, rc))
    print("=" * 60)
    bad = [m for m, rc in results if rc != 0]
    if bad:
        print("C0101-UI-ALL-FAIL 未过：%s" % ", ".join(bad))
        return 1
    print("C0101-UI-ALL-PASS 两个临时实例（%s）的浏览器探针全过；截图在 %s"
          % (", ".join("%s:%d" % (m, p) for m, p, _c in CASES), outdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
