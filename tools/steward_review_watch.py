# -*- coding: utf-8 -*-
# Copyright (c) 2026 <2227663973@qq.com>. All rights reserved.
# Proprietary - authorized testing only. No copying, distribution
# or publication without written permission.
# File-ID: SA-74-3f7c1d92   (project study_agent; keep this line)
"""大管家验收提醒（**唤醒器**）：发现"有卡新进 review"就立刻退出 = 唤醒大管家。

## 它解决什么问题

大管家（管理会话）不能一直盯着账本，但"卡交付了"这件事必须立刻有人去预检。
本脚本就是那个哨兵：

    ┌─ 宿主以后台 job 方式挂上本脚本
    │
    ├─ 脚本每 N 秒读一次账本（**只读**，不写账本、不碰项目文件）
    │
    ├─ 发现「有新卡进入 review」─→ 打印摘要 + sys.exit(0)
    │                                  │
    │                          宿主收到"job 已完成"事件
    │                                  │
    │                          大管家被唤醒 → 亲自跑预检 → 摆给人拍板
    │                                  │
    └──────────────── 验收完毕后由大管家**重新挂上**本脚本，闭环 ◄──┘

⇒ **唯一退出条件 = 发现新卡进 review**（外加一个用于兜底的心跳超时，见 `--idle-exit`）。
   两个退出条件都会让宿主唤醒大管家，区别只是提示语不同。

## 用法

    # 后台 job 挂上（推荐间隔 60 秒；间隔只影响发现延迟，不影响正确性）
    python -X utf8 -u tools\\steward_review_watch.py 60

    # 只跑一轮就退出（调试用）
    python -X utf8 -u tools\\steward_review_watch.py --once

    # 长时间没有任何交付时也退出来叫一次（默认 3600 秒；0 = 关闭）
    python -X utf8 -u tools\\steward_review_watch.py 60 --idle-exit 3600

## 设计约束

* **只读**：不写账本、不写项目文件；唯一写入是 `logs\\steward_review_watch.state.json`
  与 `logs\\steward_review_watch.log`（记录"已通知过哪些卡"，避免同一张卡反复唤醒）。
* **退出即唤醒**：这是宿主 job 机制决定的——本脚本不主动"叫人"，它只是结束自己。
* **失败不静默**：账本读不到时**不退出**，而是打印错误继续轮询（避免哨兵悄悄死掉）。
* 输出保持短：唤醒时的摘要固定几行，明细写日志文件。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

# ---- 账本位置 --------------------------------------------------------------
# 优先取环境变量（便于换档位/换机器），否则用本档位默认目录。
DEFAULT_ROOT = (r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
                r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043")
ROOT = os.environ.get("STEWARD_CONSOLE_DIR") or DEFAULT_ROOT
LEDGER = os.path.join(ROOT, "ledger.json")

PROJECT = r"G:\study_agent"
LOG_DIR = os.environ.get("STEWARD_WATCH_LOGDIR") or os.path.join(PROJECT, "logs")
STATE = os.path.join(LOG_DIR, "steward_review_watch.state.json")
LOG = os.path.join(LOG_DIR, "steward_review_watch.log")


def now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    """写日志；日志失败不影响主流程。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (now(), msg))
    except Exception:  # noqa: BLE001
        pass


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:  # noqa: BLE001
        return {}


def save_state(d: dict) -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE)
    except Exception as e:  # noqa: BLE001
        log("save_state 失败：%s: %s" % (type(e).__name__, e))


def load_review() -> tuple[dict, dict, str]:
    """返回 ({review 卡号: 卡}, {blocked 卡号: 卡}, 错误串)。

    **读不到不抛异常**，由调用方决定继续轮询。
    blocked 也要盯：卡被判 blocked 时若没人叫醒大管家，会**静默卡死整条依赖链**
    （2026-10-12 C-0084 被判 blocked，下游 C-0085 就一直等在它后面，是人工查看才发现）。
    """
    try:
        with open(LEDGER, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        return {}, {}, "%s: %s" % (type(e).__name__, e)
    cards = data.get("cards") or []
    items = cards if isinstance(cards, list) else list(cards.values())
    reviews, blocked = {}, {}
    for c in items:
        if not isinstance(c, dict):
            continue
        cid = c.get("id") or "?"
        if c.get("status") == "review":
            reviews[cid] = c
        elif c.get("status") == "blocked":
            blocked[cid] = c
    return reviews, blocked, ""


def card_dir(cid: str) -> str:
    return os.path.join(ROOT, "cards", cid)


def brief_of(cid: str, c: dict) -> list[str]:
    """一张待验收卡的摘要行（保持短）。"""
    res = os.path.join(card_dir(cid), "result.json")
    size = os.path.getsize(res) if os.path.exists(res) else 0
    lines = ["  %s  %s" % (cid, (c.get("title") or "")[:56])]
    lines.append("      交付说明: %s" % ((c.get("reviewNote") or c.get("summary") or "")
                                       .replace("\n", " ")[:110]))
    lines.append("      result.json=%d B · 会话=%s" % (size, c.get("sessionId") or "-"))
    d = card_dir(cid)
    if os.path.isdir(d):
        newest, mt = "", 0.0
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isfile(p) and os.path.getmtime(p) > mt:
                newest, mt = name, os.path.getmtime(p)
        if newest:
            lines.append("      最新交付物: %s (%s)"
                         % (newest, datetime.datetime.fromtimestamp(mt).strftime("%H:%M:%S")))
    return lines


def brief_blocked(cid: str, c: dict) -> list[str]:
    """一张**卡死**卡的摘要行：卡号、标题、卡住原因（保持短）。"""
    lines = ["  %s  %s" % (cid, (c.get("title") or "")[:56])]
    why = ""
    pre = c.get("precheck")
    if isinstance(pre, dict):
        why = pre.get("summary") or ""
    if not why:
        alerts = c.get("alerts") or []
        if alerts:
            last = alerts[-1]
            why = ((last.get("detail") or last.get("title") or "")
                   if isinstance(last, dict) else str(last))
    if not why:
        why = c.get("reviewNote") or "（卡内未记原因，需人工判断）"
    lines.append("      卡住原因: %s" % str(why).replace("\n", " ")[:110])
    lines.append("      会话=%s · attempts=%s · 救援 %s 次"
                 % (c.get("sessionId") or "-", c.get("attempts", "-"),
                    c.get("rescueCount", 0)))
    return lines


def wake_reason(new_ids: list[str], reviews: dict, new_blocked: list[str],
                blocked: dict, idle: bool, idle_sec: int) -> list[str]:
    """唤醒摘要。**结论行放在最前面**（本项目历史上多次因结论落在输出末尾被截断误判）。"""
    if new_blocked:
        head = "STEWARD-WAKE: NEW-BLOCKED %d 张卡卡死，请立即救援（依赖链可能被堵）" % len(new_blocked)
    elif new_ids:
        head = "STEWARD-WAKE: NEW-REVIEW %d 张卡交付，请立即预检" % len(new_ids)
    else:
        head = ("STEWARD-WAKE: IDLE %d 分钟无新交付，请巡检在跑卡"
                % max(1, idle_sec // 60))
    out = [head, ""]
    out.append("待验收合计 %d 张（新进 %s）｜blocked 合计 %d 张（新进 %s）"
               % (len(reviews), ",".join(new_ids) or "-",
                  len(blocked), ",".join(new_blocked) or "-"))
    out.append("")
    for cid in new_blocked:
        out.extend(brief_blocked(cid, blocked[cid]))
    show = new_ids or ([] if new_blocked else list(reviews.keys()))
    for cid in show:
        out.extend(brief_of(cid, reviews[cid]))
    out.append("")
    out.append("下一步：大管家亲自跑预检（不听施工自述）→ 摆给人拍板 → 验收完重新挂上本提醒。")
    out.append("（卡被 blocked 时：先复核证据判断是「功能缺陷」还是「流程/预检问题」，"
               "再用 steward_recover 投递续干消息。）")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("interval", nargs="?", type=int, default=60,
                    help="轮询间隔秒数（默认 60）")
    ap.add_argument("--once", action="store_true", help="只检查一轮就退出")
    ap.add_argument("--idle-exit", type=int, default=3600,
                    help="无新交付时，跑满这么多秒也退出来叫一次（0=关闭该兜底；默认 3600）")
    ap.add_argument("--force", action="store_true",
                    help="忽略已通知记录：只要账本里有 review 卡就立即唤醒")
    args = ap.parse_args()

    state = load_state()
    notified = set(state.get("notified") or [])
    notified_blocked = set(state.get("notified_blocked") or [])
    interval = max(5, args.interval)

    # 启动基线：当前**已经** blocked 的卡记为"已知"（例：有意作废的 C-0070/C-0072），
    # 只对启动之后**新出现**的 blocked 唤醒，避免每次挂载都误叫一遍。
    _r0, _b0, _err0 = load_review()
    if not _err0:
        notified_blocked |= set(_b0.keys())
        state["notified_blocked"] = sorted(notified_blocked)
        save_state(state)

    log("启动：间隔=%ds idle_exit=%ds force=%s 已通知过=%d 张 review / %d 张 blocked"
        % (interval, args.idle_exit, args.force, len(notified), len(notified_blocked)))
    print("大管家验收提醒已挂上：每 %d 秒检查一次账本（只读）" % interval)
    print("退出条件：发现新卡进 review / 新卡被 blocked（或跑满 %d 秒无新交付）"
          % args.idle_exit)
    print("账本：%s" % LEDGER)
    sys.stdout.flush()

    started = time.time()
    first_round = True
    while True:
        reviews, blocked, err = load_review()
        if err:
            # 读不到账本：不退出，继续轮询（避免哨兵悄悄死掉）
            log("读账本失败（继续轮询）：%s" % err)
            print("[%s] 读账本失败，稍后重试：%s" % (time.strftime("%H:%M:%S"), err))
            sys.stdout.flush()
            time.sleep(interval)
            continue

        new_ids = [cid for cid in reviews if cid not in notified]
        # --force：把"只要在 review 就算新"（人工重启后想立刻确认一次时用）
        if args.force and reviews:
            new_ids = list(reviews.keys())

        # 已脱离 blocked 的卡清掉记录 ⇒ 同一张卡**二次卡死**会重新唤醒
        notified_blocked &= set(blocked.keys())
        # 「大管家暂扣」= 大管家**有意**把 ready 卡按住（防多卡并发改同一文件），
        # 卡面里已写明「非故障」，不是卡死 ⇒ 不唤醒（2026-10-14 加）。真卡死的卡
        # 不会带这个标记；同一张卡被暂扣后若真的卡死（换了 reviewNote）仍会叫。
        new_blocked, withheld = [], []
        for cid in blocked:
            if cid in notified_blocked:
                continue
            note = blocked[cid].get("reviewNote") or ""
            (withheld if "大管家暂扣" in note else new_blocked).append(cid)
        if withheld:
            log("有意暂扣（不唤醒）：%s" % ",".join(withheld))

        if new_ids or new_blocked:
            for line in wake_reason(new_ids, reviews, new_blocked, blocked,
                                    idle=False, idle_sec=0):
                print(line)
            print("STEWARD-WAKE-END")
            sys.stdout.flush()
            notified.update(new_ids)
            notified_blocked.update(new_blocked)
            state["notified"] = sorted(notified)
            state["notified_blocked"] = sorted(notified_blocked)
            state["last_wake_at"] = now()
            state["last_wake_ids"] = new_ids
            state["last_wake_blocked"] = new_blocked
            save_state(state)
            log("唤醒：新进 review = %s；新进 blocked = %s"
                % (",".join(new_ids) or "-", ",".join(new_blocked) or "-"))
            return 0

        if args.once:
            print("STEWARD-WATCH: 本轮无新交付（待验收 %d 张 / blocked %d 张）"
                  % (len(reviews), len(blocked)))
            return 0

        if args.idle_exit and (time.time() - started) >= args.idle_exit:
            for line in wake_reason([], reviews, [], blocked, idle=True,
                                    idle_sec=args.idle_exit):
                print(line)
            print("STEWARD-WAKE-END")
            sys.stdout.flush()
            state["last_idle_wake_at"] = now()
            save_state(state)
            log("唤醒：空闲兜底（%ds 无新交付）" % args.idle_exit)
            return 0

        if first_round:
            print("[%s] 已就绪：当前待验收 %d 张，等待新交付…"
                  % (time.strftime("%H:%M:%S"), len(reviews)))
            sys.stdout.flush()
            first_round = False

        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
