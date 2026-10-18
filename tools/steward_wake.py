# -*- coding: utf-8 -*-
"""大管家定时唤醒器（每 N 秒唤醒一次大管家去检查与验收）。

## 它和旧轮询器的区别（**这是重写的原因**）

旧的 `steward_poll.py` 是"**事件驱动**"：只有发现「未通知过的 review 卡」才 `sys.exit(0)`，
一切正常时**静默循环**。结果是：大管家平时不会被叫，只能在卡恰好进 review 的那一次被唤醒。

人的要求是"**定时唤醒**"：「每 300 秒把你唤醒去检查与验收」——
即**每次到点都叫一次**，让大管家按固定节奏去查账本、做预检、推进流程。

所以本脚本的语义是：
  · 启动 → 打印当前现场摘要 → **立即退出（exit 0）**；
  · 退出即"job 完成"事件 → 宿主通知大管家 → 大管家被唤醒去做检查与验收；
  · 由后台再次启动本脚本（间隔 N 秒），形成"每 N 秒唤醒一次"的节奏。

## 用法

    # 单次唤醒（宿主后台 job）
    python -X utf8 -u tools\\steward_wake.py

    # 自己带间隔循环（本进程不退出，仅在日志里打点；不适合做唤醒）
    python -X utf8 -u tools\\steward_wake.py --loop 300

大管家重挂方式（**每 300 秒唤醒一次**）：
    # 1) 由后台每隔 300 秒启动一次本脚本；脚本秒退 → 宿主发"job 完成"通知 = 唤醒
    # 2) 若上一轮还没处理完，无需担心重复：重复唤醒只是多一次检查提醒

## 设计约束
* **只读**：不写账本、不写任何状态文件、不碰项目代码。
* **秒退**：单次模式必须立刻结束，宿主才会发出"job 完成"通知。
* 输出克制：只列待验收卡（要干活的）+ 在跑/就绪卡号，不打印长交付说明。
"""
import datetime
import json
import os
import sys
import time

LEDGER = os.path.join(
    r"C:\Users\Lenovo\.dsh\steward-lite\consoles",
    "session-23a82a5f-fd66-4ae6-94c1-18f3e276c043", "ledger.json")


def now() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def load() -> tuple:
    """返回 (review 卡列表, 各状态计数, 在跑/就绪卡号)。读不到就抛，由调用方兜。"""
    with open(LEDGER, encoding="utf-8") as f:
        d = json.load(f)
    cards = d.get("cards") or []
    if isinstance(cards, dict):
        cards = list(cards.values())
    counts: dict = {}
    review, running, ready = [], [], []
    for c in cards:
        st = c.get("status") or "?"
        counts[st] = counts.get(st, 0) + 1
        cid = c.get("id")
        if st == "review":
            review.append((cid, (c.get("title") or "")[:46],
                           (c.get("result") or {}).get("summary") or ""))
        elif st == "running":
            running.append(cid)
        elif st == "ready":
            ready.append(cid)
    return review, counts, running, ready


def one_shot() -> None:
    print("[%s] 🔔 定时唤醒（每 300 秒一次）—— 大管家请去做检查与验收" % now(), flush=True)
    try:
        review, counts, running, ready = load()
    except Exception as e:  # noqa: BLE001
        print("    ⚠ 账本读取失败: %s: %s" % (type(e).__name__, e), flush=True)
        return
    stat = " ".join("%s=%d" % (k, counts[k]) for k in sorted(counts))
    print("    卡[%s]  在跑=%s  就绪=%s" % (stat, ",".join(running) or "无", ",".join(ready) or "无"), flush=True)
    if not review:
        print("    ⏳ 当前待验收：0 张（无事可做，本轮唤醒可忽略）", flush=True)
        return
    print("    ⏳ 当前待验收：%d 张 —— 需要大管家亲自预检" % len(review), flush=True)
    for cid, title, summary in review:
        print("       · %s  %s" % (cid, title), flush=True)
        if summary:
            print("         交付说明: %s" % summary.replace("\n", " ")[:150], flush=True)


def main() -> int:
    loop = 0
    if "--loop" in sys.argv:
        i = sys.argv.index("--loop")
        if i + 1 < len(sys.argv):
            loop = int(sys.argv[i + 1])
    one_shot()
    if loop > 0:
        while True:
            time.sleep(loop)
            one_shot()
    return 0


if __name__ == "__main__":
    sys.exit(main())
