# -*- coding: utf-8 -*-
"""卡 C-0056 快速复核（目标 < 30 秒）：给大管家二次验收用的接口级 + 证据级验证。

验什么（对照卡面验收标准）：
  1. **源码特征**：`tools/dashboard.py` 里三项修复都在 —— 后端 `note_count`、
     前端「◀ 上一知识点」（首/末条置灰）、滚动守卫（wheel/touchmove +
     显式 `{passive:false}` + `overscroll-behavior:contain`）；
  2. **后端真值**：现取 `api_summary_kp()` 的 `note_count`，与
     `core/study.py::notes_for(kp_id, doc_id)`（= 弹窗「查看笔记」的同一口径）
     **逐一对齐**，并覆盖 0 条 / 1 条 / ≥2 条三种情形（本学科数据里凑不齐就如实标 SKIP）；
  3. **邻居链由后端给**：首条 `prev_id` 为空、末条 `next_id` 为空、相邻两条互为
     prev/next（前端不许自己排，见 `_lkp_seq`）；
  4. **两份各自独立写的真浏览器证据**（本会话 `probe_c0056_browser.mjs` 的
     `browser_probe_after.json` + 并行会话 `probe_c0056_guard.mjs` 的
     `probe_guard_green.json`）关键字段都成立：超界滚动 ≥10+10 拍弹窗**始终在**、
     外层 `scrollingElement.scrollTop` 全程未变、正文能滚到首尾、笔记数三种情形、
     上一知识点首条置灰 / 次条跳转、下一知识点未被破坏、0 条 JS 报错；
  5. `tools/check_dashboard_js.py` 通过。

**为什么快**：不重跑浏览器（重跑一次约 90 秒：`tools/verify_c0056_browser.py`，
以及并行会话的 `tools/verify_c0056_browser_guard.py` 同名脚本）。
本脚本只做"读源码 + 现算接口 + 校验证据 JSON"，全程**零模型调用**、不写任何产物。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0056_quick.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0056")
DASH = ROOT / "tools" / "dashboard.py"
HERE = Path(__file__).resolve().parent

OK = 0
BAD = 0
SKIP = 0
LINES: list[str] = []


def emit(s: str = "") -> None:
    LINES.append(s)


def check(cond, label: str, detail: str = "") -> bool:
    global OK, BAD
    if cond:
        OK += 1
        emit("  [OK] %s%s" % (label, ("  —— " + detail) if detail else ""))
    else:
        BAD += 1
        emit("  [★FAIL] %s%s" % (label, ("  —— " + detail) if detail else ""))
    return bool(cond)


def skip(label: str, why: str) -> None:
    global SKIP
    SKIP += 1
    emit("  [SKIP] %s  —— %s" % (label, why))


def report(head: str) -> None:
    """结论打在**第一行**：验收方捕获输出有长度上限，结论在最后会被截断。"""
    print(head)
    body = "\n".join(LINES)
    if body:
        print(body)


def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"__error": "%s: %s" % (type(e).__name__, e)}


def main() -> int:
    try:
        code = _run()
    except BaseException as e:  # noqa: BLE001
        emit("★复核脚本异常中止：%s: %s" % (type(e).__name__, e))
        report("★复核未能跑完（通过 %d / 失败 %d）：%s: %s"
               % (OK, BAD, type(e).__name__, e))
        return 3
    report("结果：%d 项通过，%d 项失败，%d 项跳过" % (OK, BAD, SKIP))
    return 0 if BAD == 0 else 1


def _run() -> int:
    emit("卡 C-0056 快速复核（接口级 + 证据级；不重跑浏览器）")
    if not DASH.is_file():
        emit("★找不到 %s" % DASH)
        return 2
    src = DASH.read_text(encoding="utf-8", errors="replace")

    # ---------------- [1] 源码特征：三项修复都在 ----------------
    emit("\n[1] 源码特征（%s，%d 行）" % (DASH, len(src.splitlines())))
    check("study.notes_for(kp_id=str(kp.get(\"id\") or \"\"), doc_id=doc_id)"
          in src,
          "后端在 api_summary_kp 里现算绑定笔记数（口径 = notes_for）")
    check('"note_count": n_notes' in src,
          "后端把 note_count 一起带回（前端不必为这行字多发一次请求）")
    check('id="mmkp-notecount"' in src,
          "弹窗里有「已绑定 N 条笔记」小字的容器 #mmkp-notecount")
    check(src.count('id="mmkp-prev"') >= 1 and "mmPrevKp" in src,
          "弹窗里有「◀ 上一知识点」按钮并绑到 mmPrevKp")
    check('id="mmkp-next"' in src and "function mmNextKp" in src,
          "原有「下一知识点」仍在（未被替换掉）")
    check("d.prev_id" in src and "d.next_id" in src and "d.prev_name" in src,
          "上一/下一都读后端的 prev_id / next_id，前端不自己排序")
    check("function mmKpWheel" in src and "function mmKpTouchMove" in src,
          "滚动守卫同时管 wheel 与 touchmove")
    check("{passive: false}" in src or "{passive:false}" in src,
          "监听器显式 passive:false（否则 preventDefault 会被浏览器忽略）")
    check("overscrollBehavior = 'contain'" in src,
          "正文区另加 overscroll-behavior:contain（从根上断滚动链）")
    check("function mmKpScrollGuard" in src and "mmKpScrollGuard(evPopEl())" in src,
          "守卫在知识点弹窗渲染完时挂上（幂等）")

    # ---------------- [2][3] 后端真值：note_count 与邻居链 ----------------
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(HERE))
    import dashboard as D  # noqa: E402
    from core import config, study, subjects  # noqa: E402
    subjects.restore()
    kdir = Path(config.LOG_DIR) / "knowledge"
    emit("\n[2] 后端真值（学科 %s · 产物目录 %s）" % (subjects.current(), kdir))

    doc_id, seq = 0, []
    for p in sorted(kdir.glob("*末级知识点总结.json")):
        head = p.name.split("-")[0]
        if not head.isdigit():
            continue
        s = D._lkp_seq(int(head))
        if len(s) >= 3:
            doc_id, seq = int(head), s
            break
    if not doc_id:
        emit("★找不到含 ≥3 条知识点的产物，后面没法判")
        return 2
    ids = [x["id"] for x in seq]
    emit("  doc#%d 链长 %d · 链首 %s · 链次 %s · 链末 %s"
         % (doc_id, len(seq), ids[0], ids[1], ids[-1]))

    first = D.api_summary_kp(doc_id, ids[0], None)
    second = D.api_summary_kp(doc_id, ids[1], None)
    last = D.api_summary_kp(doc_id, ids[-1], None)
    check(first.get("prev_id", None) == "" and first.get("order_index") == 1,
          "链首：prev_id 为空串（前端据此把「上一知识点」置灰）",
          "prev_id=%r order_index=%s" % (first.get("prev_id"), first.get("order_index")))
    check(last.get("next_id", None) == "" and last.get("order_index") == len(seq),
          "链末：next_id 为空串（前端据此把「下一知识点」置灰）",
          "next_id=%r order_index=%s" % (last.get("next_id"), last.get("order_index")))
    check(second.get("prev_id") == ids[0] and first.get("next_id") == ids[1],
          "相邻两条互为 prev/next（顺序由后端 _lkp_seq 给，前端不排）",
          "%s ←→ %s" % (ids[0], ids[1]))

    # note_count 三种情形：与 notes_for 逐一对齐
    cases = {"n0": [], "n1": [], "n2": []}
    for kid in ids:
        d = D.api_summary_kp(doc_id, kid, None)
        truth = len(study.notes_for(kp_id=kid, doc_id=doc_id))
        got = d.get("note_count")
        tag = "n0" if truth == 0 else ("n1" if truth == 1 else "n2")
        cases[tag].append((kid, truth, got))
        if got != truth:
            check(False, "note_count 与 notes_for 不一致：%s" % kid,
                  "%r != %r" % (got, truth))
    for tag, name in (("n0", "0 条"), ("n1", "1 条"), ("n2", "≥2 条")):
        if not cases[tag]:
            skip("笔记数情形 %s" % name, "本学科链上没有这种情形（如实标跳过，不算失败）")
            continue
        kid, truth, got = cases[tag][0]
        check(got == truth, "笔记数情形 %s：%s 的 note_count=%s == notes_for=%s"
              % (name, kid, got, truth))

    # ---------------- [4] 两份独立的真浏览器证据 ----------------
    emit("\n[3] 真浏览器证据（两份各自独立写的探针）")
    mine = load_json(CARD / "browser_probe_after.json")
    theirs = load_json(CARD / "probe_guard_green.json")

    if mine.get("__error"):
        check(False, "本会话探针证据可读", str(mine["__error"]))
    else:
        r = (mine.get("steps") or {}).get("roll") or {}
        up, dn = r.get("up") or [], r.get("down") or []
        first_kp = mine.get("steps", {}).get("open", {}).get("kp") or ""
        check(not mine.get("errors"), "本会话探针：真浏览器 0 条 JS 报错",
              "；".join(mine.get("errors") or [])[:200] or "0 条")
        check((mine.get("steps", {}).get("wheelSelfProof") or {}).get("moved") is True,
              "自证：无弹窗时同一套滚轮**真的**能滚外层（否则'没被穿透'不算数）",
              json.dumps(mine.get("steps", {}).get("wheelSelfProof"), ensure_ascii=False))
        check(len(up) >= 10 and all(x.get("visible") and x.get("exists")
                                    and x.get("kp") == first_kp for x in up),
              "到顶后继续上滚 ≥10 次：每一拍弹窗都在（存在 + 可见 + 还挂着同一条）",
              "上滚 %d 拍；最后一拍 %s" % (len(up), json.dumps(up[-1], ensure_ascii=False) if up else "—"))
        check(len(dn) >= 10 and all(x.get("visible") and x.get("exists")
                                    and x.get("kp") == first_kp for x in dn),
              "到底后继续下滚 ≥10 次：每一拍弹窗都在",
              "下滚 %d 拍；最后一拍 %s" % (len(dn), json.dumps(dn[-1], ensure_ascii=False) if dn else "—"))
        check(r.get("outerHeld") is True and r.get("outerBefore") == r.get("outerAfter"),
              "document.scrollingElement.scrollTop 全程未变（外层没被穿透）",
              "outer %s → %s" % (r.get("outerBefore"), r.get("outerAfter")))
        check(r.get("survived") is True, "滚完弹窗仍在（dataset.kp 未被清空）",
              json.dumps(r.get("final"), ensure_ascii=False))
        bs = (mine.get("steps") or {}).get("bodyScroll") or {}
        check(bs.get("reachedTop") and bs.get("reachedBottom") and bs.get("canScroll"),
              "弹窗正文仍能正常滚到内容首尾（没把滚动一起截死）",
              "top=%s bottom=%s max=%s" % (bs.get("top"), bs.get("bottom"), bs.get("max")))
        nb = (mine.get("steps") or {}).get("notes") or {}
        ui, truth = nb.get("ui") or {}, nb.get("truth") or {}
        bad_notes = []
        for tag in ("n0", "n1", "n2"):
            t = (truth.get(tag) or {}).get("n")
            u = ui.get(tag) or {}
            if t is None or not u:
                continue
            if not u.get("wholeHas") or ("绑定 %d 条笔记" % t) not in (u.get("text") or ""):
                bad_notes.append("%s: 期望“绑定 %d 条笔记”，实得 %r" % (tag, t, u.get("text")))
        check(not bad_notes and len(ui) >= 2,
              "弹窗里出现「绑定 N 条笔记」，N 与实际笔记数一致（0/1/≥2 三情形都跑了）",
              "；".join(bad_notes) if bad_notes else "；".join(
                  "%s=%r" % (k, (v or {}).get("text")) for k, v in sorted(ui.items())))
        pv = (mine.get("steps") or {}).get("prev") or {}
        f = pv.get("first") or {}
        check(f.get("has") and f.get("disabled") is True,
              "「◀ 上一知识点」存在；已是第一条时置灰不可点",
              "text=%r disabled=%s" % (f.get("text"), f.get("disabled")))
        check((pv.get("firstClick") or {}).get("after") == (pv.get("firstClick") or {}).get("before"),
              "首条上点它不会跳走（按钮真的点不动）",
              json.dumps(pv.get("firstClick"), ensure_ascii=False))
        sc = pv.get("secondClick") or {}
        before, after = sc.get("before"), sc.get("after")
        # 真值判据（不靠探针自报）：跳到的这条（after）**它的 next 正是原来的那条**
        # ⇒ after 就是 before 在先序链上的前一条。后端算，前端只是跳。
        pdoc = int(mine.get("doc") or doc_id)
        t_after = D.api_summary_kp(pdoc, after, None) if after else {}
        check(bool(before and after and after != before)
              and t_after.get("next_id") == before
              and (pv.get("second") or {}).get("disabled") is False,
              "次条上点它能打开先序链的前一条（与后端 prev_id/next_id 对照一致）",
              "%s → %s（后者的 next_id=%r，前者=%r）"
              % (before, after, t_after.get("next_id"), before))
        nx = (mine.get("steps") or {}).get("next") or {}
        nc = nx.get("click") or {}
        check(nc.get("ok") and nc.get("after") and nc.get("visible"),
              "「下一知识点」原有行为未被破坏（点它仍能正常跳转）",
              "%s → %s" % (nc.get("before"), nc.get("after")))
        check(nc.get("deckStillOpen") is True,
              "跳转时已展开的课件跟着切到新知识点那一页（C-0036 的联动没坏）",
              "deckFlag=%s imgSrc=%s" % (nc.get("deckFlag"), nc.get("imgSrc")))

    if theirs.get("__error"):
        check(False, "并行会话探针证据可读", str(theirs["__error"]))
    else:
        r2 = (theirs.get("steps") or {}).get("roll") or {}
        up2, dn2 = r2.get("up") or [], r2.get("down") or []
        outs = {x.get("outer") for x in (up2 + dn2)}
        check(not theirs.get("errors"), "并行会话探针：真浏览器 0 条 JS 报错",
              "；".join(theirs.get("errors") or [])[:200] or "0 条")
        check(len(up2) >= 10 and len(dn2) >= 10
              and all(x.get("visible") and x.get("exists") for x in (up2 + dn2)),
              "并行会话探针（独立实现、独立样例）：超界上滚/下滚各 ≥10 拍弹窗始终在",
              "上 %d 拍 / 下 %d 拍" % (len(up2), len(dn2)))
        check(len(outs) == 1 and None not in outs,
              "并行会话探针：外层 scrollTop 全程只有一个值（未穿透）", str(sorted(outs)))
        cs = (theirs.get("steps") or {}).get("canScroll") or {}
        check(cs.get("reachedTop") and cs.get("reachedBottom") and cs.get("canScroll"),
              "并行会话探针：弹窗正文仍能滚到首尾",
              "top=%s bottom=%s max=%s" % (cs.get("top"), cs.get("bottom"), cs.get("max")))
        cases2 = ((theirs.get("steps") or {}).get("notes") or {}).get("cases") or []
        bad2 = [c for c in cases2
                if c.get("truthNotes") != c.get("apiNotes")
                or ("绑定 %s 条笔记" % c.get("truthNotes")) not in (c.get("uiNote") or "")]
        check(len(cases2) >= 3 and not bad2,
              "并行会话探针：0 条 / 1 条 / ≥2 条三种笔记数在界面上与真值三方一致",
              "；".join("%s:%r" % (c.get("tag"), c.get("uiNote")) for c in cases2)
              if cases2 else "没有 cases")

    # ---------------- [4] 内联 JS 语法 ----------------
    emit("\n[4] 内联 JS 语法（tools/check_dashboard_js.py）")
    js = subprocess.run([sys.executable, "-X", "utf8",
                         str(HERE / "check_dashboard_js.py")],
                        cwd=str(ROOT), capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    check(js.returncode == 0 and "语法通过" in (js.stdout or ""),
          "check_dashboard_js.py 通过（内联 JS 语法 + 关键函数各 1 次）",
          (js.stdout or "").strip().splitlines()[-1] if js.stdout else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
