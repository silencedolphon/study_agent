# -*- coding: utf-8 -*-
"""唤醒看门狗：每 N 秒把 `steward_wake.py` 拉起一次（每次秒退）。

## 为什么需要"外部拉起"这一层

人要的是「**每 300 秒把大管家唤醒一次**」。而唤醒靠的是宿主事件：
**DSH 后台 job 结束 → 宿主发"job 完成"通知 → 大管家被唤醒**。

所以要形成稳定节奏，必须让"**启动 → 秒退**"这个动作每 300 秒重复一次：
  · `steward_wake.py`：只做一次检查、打印、**立即 exit 0**（秒退 = 一次唤醒事件）；
  · 本脚本（看门狗）：每 300 秒把它拉起一次，从而每 300 秒产生一次唤醒事件。

分工的理由：唤醒脚本**必须短命**（长命就没有"完成"事件），
而"每 300 秒重复"这件事必须由**另一个进程**来做 —— 就是本脚本。

## 用法

    python -X utf8 tools\\steward_wake_watch.py [间隔秒=300]

以**独立进程**启动（不受本会话回收影响）：
    Start-Process -FilePath 'G:\\study_agent\\venv\\Scripts\\python.exe' `
      -ArgumentList '-X','utf8','-u','G:\\study_agent\\tools\\steward_wake_watch.py','300' `
      -WindowStyle Hidden -WorkingDirectory 'G:\\study_agent'

停止：
    Stop-Process -Id (Get-Content G:\\study_agent\\logs\\steward_wake_watch.pid)

## 设计约束
* 看门狗本身**只读**（不碰账本、不写项目代码），只写自己的日志与 pid 文件。
* 单实例：pid 文件里是活进程就拒绝启动第二个。
* 日志自轮转（>4 MB 改名归档），防过夜写爆。
* 每轮打印一行"已拉起"，并附带唤醒脚本自己的输出，便于人核对节奏。
"""
import datetime
import os
import subprocess
import sys
import time

ROOT = r"G:\study_agent"
PYTHON = os.path.join(ROOT, "venv", "Scripts", "python.exe")
WAKE = os.path.join(ROOT, "tools", "steward_wake.py")
LOG_DIR = os.path.join(ROOT, "logs")
LOG = os.path.join(LOG_DIR, "steward_wake_watch.log")
PIDFILE = os.path.join(LOG_DIR, "steward_wake_watch.pid")
MAX_LOG_BYTES = 4 * 1024 * 1024


def now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(text: str) -> None:
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        f.write("[%s] %s\n" % (now(), text))


def rotate() -> None:
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > MAX_LOG_BYTES:
            os.replace(LOG, "%s.%s" % (LOG, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    except OSError:
        pass


def alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                         capture_output=True, text=True, errors="replace")
    return str(pid) in (out.stdout or "")


def main() -> None:
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    for p in (PYTHON, WAKE):
        if not os.path.exists(p):
            print("缺少必需文件: %s" % p)
            sys.exit(2)
    os.makedirs(LOG_DIR, exist_ok=True)

    if os.path.exists(PIDFILE):
        try:
            old = int(open(PIDFILE, encoding="utf-8").read().strip() or 0)
        except (OSError, ValueError):
            old = 0
        if old and old != os.getpid() and alive(old):
            print("看门狗已在运行 (pid %d)，本次退出。" % old)
            sys.exit(0)
    with open(PIDFILE, "w", encoding="utf-8", newline="") as f:
        f.write(str(os.getpid()))

    rotate()
    log("=" * 18 + " 看门狗启动 pid=%d 间隔=%ds " % (os.getpid(), interval) + "=" * 18)
    log("唤醒脚本 = %s" % WAKE)

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    n = 0
    while True:
        n += 1
        rotate()
        t0 = time.time()
        log("--- 第 %d 次拉起唤醒脚本 ---" % n)
        try:
            # 二进制句柄：中文按字节原样落盘，不做任何重编码
            with open(LOG, "ab") as fh:
                rc = subprocess.call([PYTHON, "-X", "utf8", "-u", WAKE],
                                     stdout=fh, stderr=subprocess.STDOUT, env=env)
        except Exception as e:  # noqa: BLE001
            log("拉起失败: %s: %s" % (type(e).__name__, e))
            rc = -1
        log("--- 唤醒脚本已退出 code=%s（用时 %.2fs）→ 该次退出即一次'唤醒事件'，宿主会通知大管家 ---"
            % (rc, time.time() - t0))
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("看门狗收到中断，退出。")
