# -*- coding: utf-8 -*-
# Copyright (c) 2026 <2227663973@qq.com>. All rights reserved.
# Proprietary - authorized testing only. No copying, distribution
# or publication without written permission.
# File-ID: SA-75-1c9b47e5   (project study_agent; keep this line)
"""大管家侧工具：改**一张卡**的 status（带备份 + 原子写 + rev 自增 + 写完复核）。

## 什么时候用

只有**大管家手动**运行（施工会话调用 steward_* 会被家规拒绝）。两个真实场景：

1. 卡被自动预检判 `blocked`、但人工复核确认**已达标**（只是流程/预检问题）⇒ 推回 `review` 走正常验收。
   （实例：C-0084 因预检 30 秒超时被判 blocked，功能本身 55/55 达标。）
2. 要**拦下一张 ready 卡**（例：路线变更、先作废）⇒ 改 `blocked`。
   ⚠ `dispatch` 总开关开着时 tick 每 30 秒自动派活 ready 卡，**光不派活拦不住，必须改 status**。

## 用法

    venv\\Scripts\\python.exe -X utf8 tools\\steward_card_set_status.py C-0084 review --note "人工复核：功能达标，blocked 系预检超时"
    ... --dry-run                    只看会改成什么，不落盘
    ... --console <账本目录>          换档位（默认本档位；也可用 STEWARD_CONSOLE_DIR）

## 安全设计

* 写前**备份** `ledger.json` → `ledger.json.bak-status-<时间戳>`；
* **原子落盘**（tmp + `os.replace`），避免 tick 读到半截；
* `revision` 自增 1；
* 写完**立即复核**并打印结论（结论行在最前，本项目规范）；
* 不碰卡的任何其它字段（只改 `status`，`--note` 才写 `reviewNote`）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys

DEFAULT_ROOT = (r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
                r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043")
VALID = ("ready", "running", "review", "done", "blocked")


def _items(data: dict) -> list:
    cards = data.get("cards") or []
    return cards if isinstance(cards, list) else list(cards.values())


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("card", help="卡号，如 C-0084")
    ap.add_argument("status", help="目标状态：%s" % "/".join(VALID))
    ap.add_argument("--note", default="", help="同时写入 reviewNote（可选）")
    ap.add_argument("--console", default=os.environ.get("STEWARD_CONSOLE_DIR") or DEFAULT_ROOT,
                    help="账本目录（默认本档位）")
    ap.add_argument("--dry-run", action="store_true", help="只预演，不落盘")
    a = ap.parse_args()

    if a.status not in VALID:
        print("STATUS-SET FAIL 非法状态：%s（可选 %s）" % (a.status, "/".join(VALID)))
        return 2

    led = os.path.join(a.console, "ledger.json")
    with open(led, encoding="utf-8") as f:
        data = json.load(f)

    card = next((c for c in _items(data) if isinstance(c, dict) and c.get("id") == a.card), None)
    if card is None:
        print("STATUS-SET FAIL 账本里找不到卡 %s" % a.card)
        return 2

    old = card.get("status")
    if a.dry_run:
        print("STATUS-SET DRY-RUN %s：%s → %s（未落盘）" % (a.card, old, a.status))
        return 0

    bak = led + ".bak-status-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(led, bak)

    card["status"] = a.status
    if a.note:
        card["reviewNote"] = a.note
    data["revision"] = int(data.get("revision") or 0) + 1

    tmp = led + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, led)

    with open(led, encoding="utf-8") as f:
        chk = json.load(f)
    got = next((c.get("status") for c in _items(chk)
                if isinstance(c, dict) and c.get("id") == a.card), None)
    ok = got == a.status
    print("STATUS-SET %s：%s → %s（rev=%s）%s"
          % (a.card, old, got, chk.get("revision"), "OK" if ok else "FAIL 复核不一致"))
    print("备份：%s" % bak)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
