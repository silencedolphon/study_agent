# -*- coding: utf-8 -*-
"""大管家轮询器守护进程（Python 版）——保证轮询器在无人值守时永不掉线。

## 为什么需要它
`tools/steward_poll.py`（循环版）在**发现有卡新进 review** 时会主动 `sys.exit(0)`，
故意的：宿主据此把「job 已完成」通知给大管家，触发预检。
代价是**每唤醒一次就自尽一次**——若没人在场重新挂上，下一张卡交付时**没人会被唤醒**。
用户要求「项目要过夜完成，我不能一直盯着电脑」，所以必须有人替大管家守着这件事。

## 本守护进程做什么
无限循环：启动轮询器 → 等它退出 → 立刻重启。唤醒机制照旧（轮询器退出 = 信号），
只是**不再需要任何人在场重新挂**。

## 「已通知」状态（2026-09-29 第 7 任补的缺口修复）
轮询器只在「**新进** review」时退出，于是**启动前就已躺在 review 里的卡永远不会
触发唤醒**（新实例把当时已有的卡当成初始状态）。守护重启轮询器时正好会制造这种
局面。修法：
  * 本守护维护 `logs/steward_poll_notified.txt` = **已经通知过大管家的 review 卡号**；
  * 每轮启动轮询器**之前**，把当前处于 review 且**不在**该文件里的卡号写进去；
  * 轮询器（改后）启动时用 `STEWARD_POLL_NOTIFIED` 环境变量读它，做差集：
    有没通知过的卡 → 立刻退出唤醒大管家；全都通知过 → 安心转入循环。
  ⇒ 既补掉盲区（不漏卡），又不会"启动即自尽"死循环（不重复通知）。
  大管家处理掉卡后该卡离开 review，本文件会被自动剪枝，永不需要手工维护。

## 为什么用 Python 而不是 PowerShell
PowerShell 5.1 处理「原生命令输出重定向」会转成 UTF-16LE，与 UTF-8 混写后日志全乱码
（本守护的第一版就是 .ps1，实测乱码后换掉的）。
Python 用 `subprocess` + 二进制文件句柄，**字节原样透传**，中文日志可读、不依赖代码页。

## 用法
    python -X utf8 tools\\steward_poll_daemon.py [间隔秒=60] [最小重启间隔秒=5]

停止：`Stop-Process -Id (Get-Content logs\\steward_poll_daemon.pid)`
查看：`Get-Content logs\\steward_poll_daemon.log -Tail 40 -Wait`

## 设计约束
* 对账本**只读**：本脚本不写账本、不写卡目录，只读账本快照 + 写自己的两个状态文件。
* 单实例：pid 文件存在且进程存活时拒绝启动第二个。
* 防热循环：单轮用时不足 MinRestartSec 时先睡够，避免崩溃-重启风暴。
* 日志自轮转：超过 MaxLogBytes 就改名归档，防过夜写爆。
"""
import datetime
import json
import os
import subprocess
import sys
import time

ROOT = r"G:\study_agent"
PYTHON = os.path.join(ROOT, "venv", "Scripts", "python.exe")
POLLER = os.path.join(ROOT, "tools", "steward_poll.py")
LOG_DIR = os.path.join(ROOT, "logs")
LOG = os.path.join(LOG_DIR, "steward_poll_daemon.log")
PIDFILE = os.path.join(LOG_DIR, "steward_poll_daemon.pid")
NOTIFIED = os.path.join(LOG_DIR, "steward_poll_notified.txt")
LEDGER = os.path.join(
    r"C:\Users\Lenovo\.dsh\steward-lite\consoles",
    "session-23a82a5f-fd66-4ae6-94c1-18f3e276c043", "ledger.json")
MAX_LOG_BYTES = 20 * 1024 * 1024


def now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(text: str) -> None:
    line = "[%s] %s\n" % (now(), text)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        f.write(line)


def rotate_if_needed() -> None:
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > MAX_LOG_BYTES:
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            rolled = "%s.%s" % (LOG, stamp)
            os.replace(LOG, rolled)
            log("日志轮转 -> %s" % os.path.basename(rolled))
    except OSError as e:
        log("日志轮转失败（忽略）: %s" % e)


def alive(pid: int) -> bool:
    """Windows 上 os.kill(pid, 0) 语义不可靠，直接问任务列表。"""
    out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                         capture_output=True, text=True, errors="replace")
    return str(pid) in (out.stdout or "")


def review_ids() -> set:
    """当前账本里 status == review 的卡号集合。读不到就返回空集（绝不误删通知记录）。"""
    try:
        with open(LEDGER, encoding="utf-8") as f:
            cards = json.load(f).get("cards") or []
    except (OSError, ValueError):
        return set()
    if isinstance(cards, dict):
        cards = list(cards.values())
    return {c.get("id") for c in cards if c.get("status") == "review" and c.get("id")}


def read_notified() -> set:
    try:
        # utf-8-sig：容错 BOM。若外部工具（如 PowerShell 的 Set-Content -Encoding UTF8）
        # 给状态文件加了 BOM，按 utf-8 读会让首行带 '\ufeff' → 差集失配 → 每轮误判
        # "未通知" → 反复唤醒。详见 steward_poll.py 同处注释。
        with open(NOTIFIED, encoding="utf-8-sig") as f:
            return {x.strip() for x in f.read().splitlines() if x.strip()}
    except OSError:
        return set()


def mark_notified(ids: set, why: str) -> set:
    """把 ids 记入已通知集合，落盘（同时剪掉已离开 review 的卡）。返回落盘后的集合。"""
    keep = {i for i in (read_notified() | ids) if i}
    try:
        with open(NOTIFIED, "w", encoding="utf-8", newline="") as f:
            f.write("".join("%s\n" % x for x in sorted(keep)))
    except OSError as e:
        log("写入已通知记录失败（忽略，宁可重复唤醒）: %s" % e)
        return set()
    log("    已通知记录更新（%s）：%s" % (why, ",".join(sorted(keep)) or "(空)"))
    return keep


def sync_notified() -> None:
    """启动时同步一次：把当前 review 且未记录过的卡补记为「已通知」。

    这些卡在守护启动前就已进入 review —— 大管家此刻正在处理（或被宿主通知过），
    不该再由轮询器重复唤醒一次。真正需要唤醒的是**之后**新进入 review 的卡。
    """
    fresh = review_ids() - read_notified()
    if fresh:
        mark_notified(fresh, "启动时已在 review，视为已通知")
        log("启动检查：发现 %d 张已在 review 的卡 %s（已记为已通知，避免重复唤醒）"
            % (len(fresh), ",".join(sorted(fresh))))
    else:
        log("启动检查：无未通知的在 review 卡。当前已通知记录 = %s"
            % (",".join(sorted(read_notified())) or "(空)"))


def main() -> None:
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    min_restart = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    for p in (PYTHON, POLLER):
        if not os.path.exists(p):
            print("缺少必需文件: %s" % p)
            sys.exit(2)
    os.makedirs(LOG_DIR, exist_ok=True)

    # 单实例保护
    if os.path.exists(PIDFILE):
        try:
            old = int(open(PIDFILE, encoding="utf-8").read().strip() or 0)
        except (OSError, ValueError):
            old = 0
        if old and old != os.getpid() and alive(old):
            print("守护进程已在运行 (pid %d)，本次退出。" % old)
            sys.exit(0)

    with open(PIDFILE, "w", encoding="utf-8", newline="") as f:
        f.write(str(os.getpid()))

    # 轮询器的中文输出必须按 UTF-8 落盘：让子进程强制 UTF-8，父进程按字节透传。
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["STEWARD_POLL_NOTIFIED"] = NOTIFIED

    log("=" * 20 + " 守护进程启动 pid=%d 间隔=%ds 最小重启=%ds " % (os.getpid(), interval, min_restart) + "=" * 20)
    log("轮询器 = %s" % POLLER)
    log("日志   = %s" % LOG)
    log("已通知 = %s" % NOTIFIED)
    sync_notified()

    started = time.time()
    rnd = 0
    while True:
        rnd += 1
        rotate_if_needed()
        # 关键顺序：**先**把当前 review 卡记为已通知，再启动轮询器。
        # 否则轮询器会因为"有卡在 review 且未通知"而立刻退出 → 守护重启 → 再次退出 = 死循环。
        # 之后的轮询轮次里若有卡**新进** review，它不在通知记录里 → 轮询器正常退出唤醒大管家。
        cur = review_ids()
        if cur - read_notified():
            mark_notified(cur, "本轮启动前的在 review 卡")
        log("--- 第 %d 轮：启动轮询器（当前 review=%s） ---"
            % (rnd, ",".join(sorted(cur)) or "无"))
        t0 = time.time()
        try:
            # 二进制句柄，字节原样透传：不做任何解码/重编码
            with open(LOG, "ab") as fh:
                rc = subprocess.call([PYTHON, "-X", "utf8", "-u", POLLER, str(interval)],
                                     stdout=fh, stderr=subprocess.STDOUT, env=env)
        except Exception as e:  # noqa: BLE001
            log("启动轮询器失败: %s: %s" % (type(e).__name__, e))
            rc = -1
        secs = time.time() - t0
        if rc == 0:
            log("--- 轮询器退出 code=0（用时 %.1fs）：有卡新进 review，这是唤醒信号；立即重启以保持守护 ---" % secs)
        else:
            log("--- 轮询器退出 code=%s（用时 %.1fs，非预期）；立即重启 ---" % (rc, secs))

        if secs < min_restart:
            nap = min_restart - secs
            log("    防热循环：先睡 %.1fs 再重启" % nap)
            time.sleep(nap)
        log("    守护已运行 %.1f 分钟" % ((time.time() - started) / 60.0))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("守护进程收到中断，退出。")
