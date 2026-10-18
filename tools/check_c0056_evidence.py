# -*- coding: utf-8 -*-
"""卡 C-0056 证据文件断言（可执行、无嵌套引号，供验收方直接跑）。

为什么单独写成一个脚本：把"证据 JSON 里到底有没有这些字段"写成
`verify` 里的 `contains` 子串（例如 `"survived": true`）会**依赖验收方
怎么读这个文件**——按文本读能匹配，按 JSON 解析成对象再看就匹配不上
（Python 侧布尔是 `True`、引号是单引号）。这里改成**真解析 JSON + 逐字段
断言**：判据比子串更严（`is True`、计数下限、跨字段一致性），且与读取方式无关。

断言的是两份**各自独立写的**真浏览器证据：
  A) cards\\C-0056\\browser_probe_after.json  —— 本会话探针（probe_c0056_browser.mjs）
  B) cards\\C-0056\\probe_guard_green.json    —— 并行会话探针（probe_c0056_guard.mjs）

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\check_c0056_evidence.py
成功时打印一行 `EVIDENCE-OK ...` 并以 0 退出；否则打印 [FAIL] 明细并以 1 退出。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0056")
MINE = CARD / "browser_probe_after.json"
THEIRS = CARD / "probe_guard_green.json"

FAILS: list[str] = []


def req(cond, label: str, detail: str = "") -> None:
    if not cond:
        FAILS.append("%s%s" % (label, ("  —— " + detail) if detail else ""))


def load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:  # noqa: BLE001
        FAILS.append("读不出证据文件 %s：%s: %s" % (p, type(e).__name__, e))
        return {}


def check_mine() -> str:
    d = load(MINE)
    if not d:
        return "（本会话证据缺失）"
    req(not d.get("errors"), "本会话探针应有 0 条 JS 报错", str(d.get("errors"))[:200])
    roll = ((d.get("steps") or {}).get("roll")) or {}
    up, dn = roll.get("up") or [], roll.get("down") or []
    kp = "".join([s for s in [((d.get("steps") or {}).get("open") or {}).get("kp")] if s])
    req(roll.get("survived") is True, "roll.survived 必须为 true（滚完弹窗仍在）")
    req(roll.get("outerHeld") is True, "roll.outerHeld 必须为 true（外层 scrollTop 全程未变）")
    req(roll.get("upFull") is True, "roll.upFull 必须为 true（到顶后每拍弹窗都在）")
    req(roll.get("downFull") is True, "roll.downFull 必须为 true（到底后每拍弹窗都在）")
    req(len(up) >= 10, "向上超界滚动拍数须 ≥10", "实为 %d" % len(up))
    req(len(dn) >= 10, "向下超界滚动拍数须 ≥10", "实为 %d" % len(dn))
    req(all(x.get("exists") and x.get("visible") and x.get("kp") for x in (up + dn)),
        "每一拍弹窗都必须存在、可见、且还挂着同一条知识点")
    req(len({x.get("outer") for x in (up + dn)}) == 1,
        "外层 scrollTop 在 24 拍里只能有一个值",
        str(sorted({x.get("outer") for x in (up + dn)})))
    req(roll.get("outerBefore") == roll.get("outerAfter"),
        "outerBefore 必须等于 outerAfter",
        "%r vs %r" % (roll.get("outerBefore"), roll.get("outerAfter")))
    req((roll.get("final") or {}).get("visible") is True, "最终快照里弹窗仍可见")
    bs = ((d.get("steps") or {}).get("bodyScroll")) or {}
    req(bs.get("canScroll") and bs.get("reachedTop") and bs.get("reachedBottom"),
        "弹窗正文须仍能滚到内容首尾",
        json.dumps(bs, ensure_ascii=False))
    # 自证：同一套滚轮在无弹窗时真的能滚外层
    proof = ((d.get("steps") or {}).get("wheelSelfProof")) or {}
    req(proof.get("moved") is True, "自证项：同一套滚轮须真的能滚外层",
        json.dumps(proof, ensure_ascii=False))
    # 笔记数三情形
    notes = ((d.get("steps") or {}).get("notes")) or {}
    ui, truth = notes.get("ui") or {}, notes.get("truth") or {}
    got_tags = sorted(ui.keys())
    req(len(got_tags) >= 3, "笔记数须跑满 0/1/≥2 三种情形", "实到 %s" % got_tags)
    for tag in ("n0", "n1", "n2"):
        t = (truth.get(tag) or {}).get("n")
        u = ui.get(tag) or {}
        req(t is not None and u, "情形 %s 缺数据" % tag)
        if t is None or not u:
            continue
        req(u.get("wholeHas") is True and ("绑定 %d 条笔记" % t) in (u.get("text") or ""),
            "情形 %s：界面须写「绑定 %d 条笔记」" % (tag, t), repr(u.get("text")))
    # 上一知识点
    pv = ((d.get("steps") or {}).get("prev")) or {}
    f, sc = pv.get("first") or {}, pv.get("secondClick") or {}
    req((f.get("has") is True) and (f.get("disabled") is True),
        "首条上「◀ 上一知识点」须存在且置灰",
        json.dumps(f, ensure_ascii=False))
    fc = pv.get("firstClick") or {}
    req(fc.get("after") == fc.get("before") and fc.get("stillVisible") is True,
        "首条上点它不得跳走", json.dumps(fc, ensure_ascii=False))
    req(sc.get("after") and sc.get("after") != sc.get("before"),
        "次条上点它须打开另一条知识点", json.dumps(sc, ensure_ascii=False))
    # 下一知识点与课件联动
    nc = (((d.get("steps") or {}).get("next")) or {}).get("click") or {}
    req(nc.get("ok") and nc.get("after") and nc.get("visible"),
        "「下一知识点」须仍能正常跳转", json.dumps(nc, ensure_ascii=False))
    req(nc.get("deckStillOpen") is True, "跳转时已展开的课件须跟着切页")
    return ("本会话：kp=%s 上滚 %d 拍 / 下滚 %d 拍 outer %s→%s，笔记 %s"
            % (kp or "?", len(up), len(dn), roll.get("outerBefore"),
               roll.get("outerAfter"),
               {k: (truth.get(k) or {}).get("n") for k in ("n0", "n1", "n2")}))


def check_theirs() -> str:
    d = load(THEIRS)
    if not d:
        return "（并行会话证据缺失）"
    req(not d.get("errors"), "并行会话探针应有 0 条 JS 报错", str(d.get("errors"))[:200])
    roll = ((d.get("steps") or {}).get("roll")) or {}
    up, dn = roll.get("up") or [], roll.get("down") or []
    req(len(up) >= 10 and len(dn) >= 10, "并行会话：超界上/下滚各须 ≥10 拍",
        "上 %d / 下 %d" % (len(up), len(dn)))
    req(all(x.get("exists") and x.get("visible") for x in (up + dn)),
        "并行会话：每一拍弹窗都须存在且可见")
    outs = {x.get("outer") for x in (up + dn)}
    req(len(outs) == 1 and None not in outs, "并行会话：外层 scrollTop 只能有一个值",
        str(sorted(outs)))
    cs = ((d.get("steps") or {}).get("canScroll")) or {}
    req(cs.get("canScroll") and cs.get("reachedTop") and cs.get("reachedBottom"),
        "并行会话：弹窗正文须仍能滚到首尾", json.dumps(cs, ensure_ascii=False))
    cases = ((d.get("steps") or {}).get("notes") or {}).get("cases") or []
    req(len(cases) >= 3, "并行会话：笔记数须跑满三种情形", "%d 例" % len(cases))
    for c in cases:
        req(c.get("truthNotes") == c.get("apiNotes")
            and ("绑定 %s 条笔记" % c.get("truthNotes")) in (c.get("uiNote") or ""),
            "并行会话：情形 %s 的三方数字须一致" % c.get("tag"),
            repr(c.get("uiNote")))
    return ("并行会话：上滚 %d 拍 / 下滚 %d 拍 outer=%s，笔记数三方一致 %s"
            % (len(up), len(dn), sorted(outs),
               [(c.get("tag"), c.get("truthNotes")) for c in cases]))


def main() -> int:
    a = check_mine()
    b = check_theirs()
    if FAILS:
        print("[FAIL] 证据断言未通过 %d 条：" % len(FAILS))
        for i, f in enumerate(FAILS, 1):
            print("  %d) %s" % (i, f))
        return 1
    print("EVIDENCE-OK %s ｜ %s" % (a, b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
