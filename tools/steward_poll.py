# -*- coding: utf-8 -*-
"""大管家验收提醒轮询器（**循环版**，每 5 分钟一轮）。

用途：把「待验收」当成一等信号，持续替大管家盯账本。

行为（与早期版本的关键区别）：
  · **持续循环，不因「没有待验收」而退出**——用户要求"轮询器一直开着"；
  · 每一轮都报告「当前待验收 N 张」；
  · ★ 唯一退出条件：**发现有卡新进 review** → 打印该卡验收要点后退出，
    由宿主把「job 已完成」通知给大管家，触发预检（保留原有唤醒机制）；
  · 同时兜住其它变化（卡状态、result.json、events、卡目录新产物）。

用法:
    python -X utf8 -u tools\\steward_poll.py [间隔秒数]     # 默认 300 秒

设计约束：**只读**。不写账本、不碰项目文件。

历史：本文件由 `%TEMP%\\steward_poll_verify.py`（前任版）改造而来——
那份放在临时目录、且"无待验收即退出"，被环境回收后就彻底没了；2026-09-22
大管家把它循环化并移入项目 `tools/`，避免脚本本身丢失。
"""
import datetime
import json
import os
import sys
import time
from collections import Counter

ROOT = r"C:\Users\Lenovo\.dsh\steward-lite\consoles\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043"
LEDGER = os.path.join(ROOT, "ledger.json")
EVENTS = os.path.join(ROOT, "events.jsonl")
CARDS = os.path.join(ROOT, "cards")
INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 300


def now() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def load_cards() -> dict:
    with open(LEDGER, encoding="utf-8") as f:
        d = json.load(f)
    cards = d.get("cards")
    items = cards if isinstance(cards, list) else list(cards.values())
    return {c.get("id"): c for c in items}


def newest_artifact(cid: str) -> str:
    d = os.path.join(CARDS, cid)
    if not os.path.isdir(d):
        return ""
    best, mt = "", 0.0
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            m = os.path.getmtime(p)
            if m > mt:
                best, mt = name, m
    return "%s@%s" % (best, datetime.datetime.fromtimestamp(mt).strftime("%H:%M:%S")) if best else ""


def snapshot() -> dict:
    snap = {"cards": {}, "reviews": {}, "results": {}, "artifacts": {}, "events": 0}
    try:
        for cid, c in load_cards().items():
            st = c.get("status")
            snap["cards"][cid] = st
            if st == "review":
                rp = os.path.join(CARDS, cid, "result.json")
                sz = os.path.getsize(rp) if os.path.exists(rp) else 0
                snap["reviews"][cid] = {
                    "title": (c.get("title") or "")[:52],
                    "note": (c.get("reviewNote") or "")[:300],
                    "result_bytes": sz,
                    "session": c.get("sessionId") or "",
                }
    except Exception as e:  # noqa: BLE001
        snap["error"] = "%s: %s" % (type(e).__name__, e)
    for cid in snap["cards"]:
        rp = os.path.join(CARDS, cid, "result.json")
        if os.path.exists(rp):
            st = os.stat(rp)
            snap["results"][cid] = "%dB@%s" % (
                st.st_size, datetime.datetime.fromtimestamp(st.st_mtime).strftime("%H:%M:%S"))
        snap["artifacts"][cid] = newest_artifact(cid)
    try:
        with open(EVENTS, encoding="utf-8") as f:
            snap["events"] = sum(1 for _ in f)
    except Exception:  # noqa: BLE001
        pass
    return snap


def counts(s: dict) -> str:
    c = Counter(v for v in s["cards"].values() if v)
    return " ".join("%s=%d" % (k, n) for k, n in sorted(c.items()))


def report_reviews(s: dict) -> None:
    """把当前待验收的卡逐张列清楚——这是本轮的「提醒」主体。"""
    if not s["reviews"]:
        print("      ⏳ 当前待验收：0 张", flush=True)
        return
    print("      ⏳ 当前待验收：%d 张 —— 需要大管家亲自预检" % len(s["reviews"]), flush=True)
    for cid, r in sorted(s["reviews"].items()):
        print("         · %s  %s" % (cid, r["title"]), flush=True)
        print("           result.json=%d B   session=%s" % (r["result_bytes"], r["session"][:20]), flush=True)
        if r["note"]:
            print("           交付说明: %s" % r["note"].replace("\n", " ")[:200], flush=True)


def diff(prev: dict, cur: dict) -> list:
    out = []
    for cid, st in cur["cards"].items():
        old = prev["cards"].get(cid)
        if old != st:
            out.append("卡 %s: %s -> %s" % (cid, old, st))
    for cid, v in cur["results"].items():
        if prev["results"].get(cid) != v:
            out.append("交付 %s: result.json %s" % (cid, v))
    if cur["events"] != prev["events"]:
        out.append("事件 +%d 条" % (cur["events"] - prev["events"]))
    for cid, v in cur["artifacts"].items():
        if v and prev["artifacts"].get(cid) != v:
            out.append("产物 %s: %s" % (cid, v))
    return out


def main() -> None:
    prev = snapshot()
    print("[%s] 验收提醒轮询器启动 | 间隔 %d 秒 | 账本 revision 读取中" % (now(), INTERVAL), flush=True)
    print("    初始 卡[%s]" % counts(prev), flush=True)
    report_reviews(prev)
    n = 0
    while True:
        time.sleep(INTERVAL)
        n += 1
        try:
            cur = snapshot()
        except Exception as e:  # noqa: BLE001
            print("[%s] 轮询 #%d 读取失败: %s: %s" % (now(), n, type(e).__name__, e), flush=True)
            continue

        new_reviews = [cid for cid in cur["reviews"] if prev["cards"].get(cid) != "review"]
        ch = diff(prev, cur)

        if new_reviews:
            print("", flush=True)
            print("=" * 66, flush=True)
            print("[%s] 轮询 #%d  🔔 有待验收的卡了 —— 请立即预检" % (now(), n), flush=True)
            print("=" * 66, flush=True)
            for x in ch:
                print("      - %s" % x, flush=True)
            print("      卡[%s]" % counts(cur), flush=True)
            report_reviews(cur)
            sys.exit(0)  # 退出 -> 宿主通知大管家接手

        if ch:
            print("[%s] 轮询 #%d  ★ 有变化（暂无新待验收）" % (now(), n), flush=True)
            for x in ch:
                print("      - %s" % x, flush=True)
            print("      卡[%s]" % counts(cur), flush=True)
            report_reviews(cur)
        else:
            print("[%s] 轮询 #%d  无变化  卡[%s]" % (now(), n, counts(cur)), flush=True)
            report_reviews(cur)
        prev = cur


if __name__ == "__main__":
    # ---- 大管家循环版（A 版，2026-09-22）----
    # 与前任版的区别：**不因「无新待验收」而退出**——只按 INTERVAL 持续循环，
    # 每轮照常打印状态（用户要求"轮询器一直开着"）。
    # 唯一退出条件：**发现有卡新进 review** → 打印要点后退出，
    # 由宿主把「job 已完成」通知给大管家，触发预检（保留原有唤醒机制）。
    print("[%s] 大管家轮询器（循环版）启动 | 间隔 %d 秒 | 只读、不写账本" % (now(), INTERVAL), flush=True)
    seen_reviews = set()
    try:
        seen_reviews = set(snapshot()["reviews"])
    except Exception:  # noqa: BLE001
        pass
    print("    启动时已有待验收：%d 张 %s" % (len(seen_reviews), sorted(seen_reviews)), flush=True)

    # ---- 启动即唤醒：补掉「本进程启动前就已躺在 review 里的卡」这个盲区 ----
    #
    # 为什么需要这一段（2026-09-29 大管家第 7 任实测发现的真实缺口）：
    #   下面的循环只在「**新进** review」时才 sys.exit(0) 唤醒大管家，而
    #   `seen_reviews` 把**启动时已存在**的 review 卡当成了已知状态。
    #   由守护进程（steward_poll_daemon.py）反复重启本脚本时，新实例又会把当时
    #   已有的卡当成"启动时就有"——于是 **启动前就在 review 里的卡永远不会触发
    #   唤醒**，若大管家没主动查账本，那张卡就会一直躺着没人预检。
    #
    # 修法：启动时与「已通知集合」做差集，只要存在**没通知过**的 review 卡，
    #   就立刻退出一次（沿用"退出=唤醒信号"的原机制）。已通知集合由**守护进程**
    #   写入（本脚本保持不写账本、不写状态文件），通过环境变量 STEWARD_POLL_NOTIFIED
    #   指定路径；读不到就视为"全部没通知过"，宁可多唤醒一次，绝不漏。
    notified_path = os.environ.get("STEWARD_POLL_NOTIFIED", "")
    notified = set()
    if notified_path and os.path.exists(notified_path):
        try:
            # encoding="utf-8-sig"：容错 **BOM**。实测（2026-09-29 第 7 任）
            # PowerShell 的 `Set-Content -Encoding UTF8` 会给文件加 BOM，若按 utf-8
            # 读，首行会变成 '\ufeffC-0054' → 差集永远不匹配 → 每轮都误判"未通知"
            # → 守护反复重启轮询器，唤醒信号退化成噪声。读取侧必须吃得住 BOM。
            with open(notified_path, encoding="utf-8-sig") as f:
                notified = {x.strip() for x in f.read().splitlines() if x.strip()}
        except OSError:
            notified = set()
    if seen_reviews:
        fresh = sorted(seen_reviews - notified)
        if fresh:
            print("", flush=True)
            print("=" * 66, flush=True)
            print("[%s] 🔔 启动即发现**未通知过**的待验收卡：%s" % (now(), fresh), flush=True)
            print("      （这些卡在本轮启动前就已在 review，靠循环差分永远等不到；不做这一步就会漏）", flush=True)
            print("      已通知记录: %s" % (notified_path or "(未配置)"))
            print("=" * 66, flush=True)
            try:
                report_reviews(snapshot())
            except Exception:  # noqa: BLE001
                pass
            sys.exit(0)
        print("    待验收 %d 张均已通知过（%s），不重复唤醒，转入循环" % (len(seen_reviews), ",".join(sorted(seen_reviews))), flush=True)

    while True:
        try:
            print("[%s] ── 开始一轮 ──" % now(), flush=True)
            main()
            cur_rv = set(snapshot()["reviews"])
            new = sorted(cur_rv - seen_reviews)
            if new:
                print("=" * 66, flush=True)
                print("[%s] 🔔 发现新进 review 的卡：%s —— 退出以唤醒大管家预检" % (now(), new), flush=True)
                print("=" * 66, flush=True)
                sys.exit(0)
            seen_reviews = cur_rv
            print("[%s] 本轮结束（无新待验收）→ %d 秒后继续" % (now(), INTERVAL), flush=True)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            print("[%s] ⚠ 本轮异常：%s: %s（%d 秒后重试）" % (now(), type(e).__name__, e, INTERVAL), flush=True)
        time.sleep(INTERVAL)