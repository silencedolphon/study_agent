# -*- coding: utf-8 -*-
"""steward_review_watch 的 **blocked 监控**功能测试（临时账本，不污染真实状态）。

三个场景：
  1. 启动时账本里**已经有** blocked 卡 → 只记为基线，**不该**误叫；
  2. 运行中**新出现** blocked 卡 → 立即唤醒并退出，摘要含卡号与原因；
  3. review 唤醒（原有能力）**回归**仍工作。

用法： venv\\Scripts\\python.exe -X utf8 _tmp_watch_test.py
结论行在最前面（本项目规范）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

PY = r"G:\study_agent\venv\Scripts\python.exe"
WATCH = r"G:\study_agent\tools\steward_review_watch.py"
T = os.path.join(tempfile.gettempdir(), "c0084-watch-test")
LEDGER = os.path.join(T, "ledger.json")
LOGDIR = os.path.join(T, "logs")

results = []


def write_ledger(cards, rev):
    os.makedirs(T, exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump({"revision": rev, "cards": cards}, f, ensure_ascii=False)


def start(tag):
    env = dict(os.environ)
    env["STEWARD_CONSOLE_DIR"] = T
    env["STEWARD_WATCH_LOGDIR"] = LOGDIR
    fout = open(os.path.join(T, tag + ".out"), "w", encoding="utf-8")
    p = subprocess.Popen([PY, "-X", "utf8", "-u", WATCH, "3"],
                         stdout=fout, stderr=subprocess.STDOUT, env=env)
    return p, fout, os.path.join(T, tag + ".out")


def wait_out(p, fout, outpath, timeout=40):
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
    try:
        fout.close()
    except Exception:  # noqa: BLE001
        pass
    with open(outpath, encoding="utf-8", errors="replace") as f:
        return f.read()


def main():
    shutil.rmtree(T, ignore_errors=True)
    os.makedirs(T)

    # ---------- 场景 1 + 2：共用一个进程 ----------
    write_ledger([{"id": "C-9001", "title": "旧 blocked 卡", "status": "blocked"}], 1)
    p, fout, outp = start("s1")
    time.sleep(9)
    alive = p.poll() is None
    results.append(("场景1：启动时已有的 blocked 卡不误叫（脚本仍在轮询）", alive))

    write_ledger([{"id": "C-9001", "title": "旧 blocked 卡", "status": "blocked"},
                  {"id": "C-9002", "title": "新卡死的卡", "status": "blocked",
                   "precheck": {"summary": "验收预检未通过，转人工判断"},
                   "sessionId": "session-test", "attempts": 1, "rescueCount": 0}], 2)
    out = wait_out(p, fout, outp)
    results.append(("场景2：新 blocked 立即唤醒", "NEW-BLOCKED" in out))
    results.append(("场景2：摘要含新卡号", "C-9002" in out))
    results.append(("场景2：摘要含卡住原因", "转人工判断" in out))
    results.append(("场景2：不误报旧卡 C-9001 为本次新卡", "新进 C-9002" in out))
    results.append(("场景2：退出码 0", p.returncode == 0))
    print("=== 场景 2 输出 ===")
    print(out)

    # ---------- 场景 3：review 回归 ----------
    write_ledger([{"id": "C-9003", "title": "交付卡", "status": "review"}], 3)
    p2, fout2, outp2 = start("s3")
    out2 = wait_out(p2, fout2, outp2)
    results.append(("场景3：review 唤醒仍工作（回归）",
                    "NEW-REVIEW" in out2 and "C-9003" in out2))
    print("=== 场景 3 输出 ===")
    print(out2)

    # ---------- 场景 4：二次卡死要重新叫（脱离 blocked 后记录被清） ----------
    write_ledger([{"id": "C-9002", "title": "新卡死的卡", "status": "running"}], 4)
    p3, fout3, outp3 = start("s4a")
    time.sleep(9)
    alive2 = p3.poll() is None
    write_ledger([{"id": "C-9002", "title": "新卡死的卡", "status": "blocked"}], 5)
    out3 = wait_out(p3, fout3, outp3)
    results.append(("场景4：离开 blocked 时不叫，二次卡死重新叫",
                    alive2 and "NEW-BLOCKED" in out3 and "C-9002" in out3))

    ok = all(r[1] for r in results)
    print()
    print("WATCH-TEST " + ("PASS" if ok else "FAIL"))
    for name, good in results:
        print(("  PASS  " if good else "  FAIL  ") + name)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
