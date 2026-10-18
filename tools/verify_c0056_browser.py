# -*- coding: utf-8 -*-
"""卡 C-0056 ★真浏览器验证（headless Edge + CDP）：知识点弹窗三项修复。

验什么（对照卡面验收标准逐条）：
  1. **滚动不出窗**：弹窗正文滚到最顶后**继续向上**滚 ≥10 次、滚到最底后
     **继续向下**滚 ≥10 次，弹窗**每一拍都还在**（DOM 存在 + display 不是 none +
     dataset.kp 没被清），且 `document.scrollingElement.scrollTop` **一个字没变**
     ——外层没被滚动链穿透（测之前先把外层滚到中间，否则"没变"没有意义）；
  2. **弹窗内还能正常滚**：正文能滚到首、能滚到尾（别把滚动一起截死），
     并记下正文区 overscroll-behavior 的实测值；
  3. **笔记数小字**：`#mmkp-notecount` 的「已绑定 N 条笔记」里 N 与
     `/api/notes/by_kp` 的真值、与 `/api/summary-kp` 的 `note_count` 三方一致，
     0 条 / 1 条 / ≥2 条三种情形各来一次；
  4. **上一知识点**：链首置灰且点不动；非链首点击后打开的知识点 == 后端 `prev_id`；
  5. **下一知识点未被破坏**：点击后 == 后端 `next_id`，且已展开的课件跟着切过去；
  6. **零模型调用**（费用 0）：只走 /api/mindmap /api/summary-kp /api/notes/by_kp；
     `check_dashboard_js.py` 通过；真浏览器全程无 JS 报错；
  7. （`--full`）**红绿对照**：用未修复的基线跑同一套滚动动作，必须能测出
     "弹窗消失 / 外层被滚"——证明这个测试真能测出病，而不是永远绿。

用法（自起临时端口实例，跑完杀掉；**不碰 8756**）：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0056_browser.py
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0056_browser.py --full --keep
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config, study, subjects  # noqa: E402

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0056")
SHOTS = CARD / "shots"
DASH = ROOT / "tools" / "dashboard.py"
PROBE = ROOT / "tools" / "probe_c0056_guard.mjs"
BASELINE = ROOT / "logs" / "_c0056_baseline_dashboard.py"   # 本卡改动前的整份文件
DOC = 1

OK = 0
BAD = 0
LINES: list[str] = []


def emit(s: str = "") -> None:
    """**只缓存，不直接打印**：结论要打在输出的**第一行**（见 `report()`）。"""
    LINES.append(s)


def report(head: str) -> None:
    print(head)
    body = "\n".join(LINES)
    if body:
        print(body)
    try:
        (CARD / "browser_report.txt").write_text(head + "\n" + body + "\n", encoding="utf-8")
    except OSError:
        pass


def check(cond, label: str, detail: str = "") -> bool:
    global OK, BAD
    if cond:
        OK += 1
        emit("  [OK] %s%s" % (label, ("  —— " + detail) if detail else ""))
    else:
        BAD += 1
        emit("  [★FAIL] %s%s" % (label, ("  —— " + detail) if detail else ""))
    return bool(cond)


def api(port: int, path: str, timeout: int = 30):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


def post(port: int, path: str, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


def note_counts() -> dict[str, int]:
    """notes/ 目录里每条知识点各绑定几条笔记（文件系统真值，与看板同源）。"""
    out: dict[str, int] = {}
    for n in study.list_notes():
        k = str(n.get("kp_id") or "").strip()
        if k and int(n.get("doc_id") or 0) == DOC:
            out[k] = out.get(k, 0) + 1
    return out


def chain(doc_id: int) -> list[str]:
    """导图先序链（后端算的那条；这里只用来挑测试参数，判定全在浏览器侧）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_dash_for_c0056", DASH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [x["id"] for x in mod._lkp_seq(doc_id)]


def spawn_dash(exe: str, script: Path, port: int, log: Path):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as fh:
        return subprocess.Popen([exe, "-X", "utf8", "-u", str(script),
                                 "--port", str(port), "--no-open"],
                                cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)


def wait_up(port: int, tries: int = 60) -> bool:
    for _ in range(tries):
        time.sleep(0.35)
        if not api(port, "/api/stats", timeout=10).get("error"):
            return True
    return False


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run_probe(port: int, mode: str, params: dict, out_json: Path,
              lean: bool, cdp: int, timeout: int = 300) -> tuple[dict | None, str]:
    cmd = ["node", str(PROBE), "--port", str(port), "--mode", mode,
           "--doc", str(DOC), "--cdp", str(cdp),
           "--json", str(out_json)]
    for k in ("kp", "kp2", "n2", "n1", "n0"):
        if params.get(k):
            cmd += ["--" + k, str(params[k])]
    if lean:
        cmd.append("--lean")
    else:
        cmd += ["--outdir", str(SHOTS)]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    tail = (r.stdout or "").strip().splitlines()
    hint = tail[-1] if tail else (r.stderr or "")[-400:]
    if not out_json.exists():
        return None, "node 退出码 %s / %s" % (r.returncode, hint)
    try:
        return json.loads(out_json.read_text(encoding="utf-8")), hint
    except ValueError as e:
        return None, "结果 JSON 读不动：%s" % e


def main() -> int:
    try:
        code = _run()
    except BaseException as e:  # noqa: BLE001
        emit("★验证脚本异常中止：%s: %s" % (type(e).__name__, e))
        report("★验证脚本未能跑完（通过 %d 项 / 失败 %d 项）：%s: %s"
               % (OK, BAD, type(e).__name__, e))
        return 3
    if code == 2:
        why = LINES[-1] if LINES else "前置条件不满足"
        report("★验证脚本未能跑完（通过 %d 项 / 失败 %d 项）：%s" % (OK, BAD, why))
        return 2
    report("结果：%d 项通过，%d 项失败" % (OK, BAD))
    return 0 if BAD == 0 else 1


def _run() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--keep", action="store_true", help="跑完不杀临时实例（排查用）")
    ap.add_argument("--full", action="store_true",
                    help="完整模式：红绿对照 + 截图 + 交叉跑 tools/probe_c0056_browser.mjs")
    ap.add_argument("--skip-js-check", action="store_true")
    args = ap.parse_args()

    PY = ROOT / "venv" / "Scripts" / "python.exe"
    exe = str(PY) if PY.exists() else sys.executable
    subj = subjects.restore()
    emit("卡 C-0056 ★真浏览器验证（headless Edge + CDP，%s 模式）"
         % ("完整" if args.full else "快速"))
    emit("  学科 %s · notes %s" % (subj["slug"], config.NOTES_DIR))

    if not args.skip_js_check:
        r = subprocess.run([exe, "-X", "utf8", str(ROOT / "tools" / "check_dashboard_js.py")],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        check(r.returncode == 0 and "语法通过" in (r.stdout or ""),
              "tools/check_dashboard_js.py 通过（内联 JS 语法）",
              ((r.stdout or "").strip().splitlines() or [""])[-1])

    # ---------------- 挑测试参数：链首 / 次条 / 笔记 0·1·≥2 ----------------
    seq = chain(DOC)
    if not seq:
        emit("★doc#%d 没有知识点链（产物缺失？）" % DOC)
        return 2
    cnt = note_counts()
    kp_head = seq[0]
    kp_second = seq[1] if len(seq) > 1 else ""
    kp_n2 = next((k for k in seq if cnt.get(k, 0) >= 2), "")
    kp_n1 = next((k for k in seq if cnt.get(k, 0) == 1), "")
    kp_n0 = next((k for k in seq if cnt.get(k, 0) == 0), "")
    emit("  链长 %d · 链首 %s · 次条 %s" % (len(seq), kp_head, kp_second))
    emit("  笔记样本：≥2 条 %s（%d）· 1 条 %s（%d）· 0 条 %s（%d）"
         % (kp_n2, cnt.get(kp_n2, 0), kp_n1, cnt.get(kp_n1, 0), kp_n0, cnt.get(kp_n0, 0)))
    if not (kp_n2 and kp_n1 and kp_n0):
        emit("★三情形样本不齐（≥2 条 / 1 条 / 0 条缺一不可），"
             "请先在药物化学学科里给某条知识点记 1~2 条笔记再跑")
        return 2

    # ---------------- 起临时端口实例（当前代码） ----------------
    port = args.port or free_port()
    proc = spawn_dash(exe, DASH, port, ROOT / "logs" / "_c0056_browser.out.log")
    if not args.keep:
        atexit.register(lambda: proc.terminate())
    if not wait_up(port):
        emit("★临时实例打不通（端口 %d）" % port)
        return 2
    emit("  临时实例 http://127.0.0.1:%d（PID %s）——8756 全程未碰" % (port, proc.pid))
    # 实例必须含本卡改动：新字段 note_count 就是探针（旧代码没这个键）
    probe_api = api(port, "/api/summary-kp?doc_id=%d&kp_id=%s" % (DOC, kp_head))
    if probe_api.get("note_count") is None:
        emit("★临时实例上没有 note_count（%s）——跑的不是本卡改过的代码？"
             % probe_api.get("error"))
        return 2
    html = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=30).read().decode("utf-8")
    for needle in ("mmkp-notecount", "mmkp-prev", "mmPrevKp", "mmKpScrollGuard"):
        if needle not in html:
            emit("★临时实例给的前端里没有 %s——跑的不是本卡改过的代码？" % needle)
            return 2
    emit("  后端 note_count=%s · 前端含 mmkp-notecount / mmkp-prev / mmKpScrollGuard"
         % probe_api["note_count"])

    params = {"kp": kp_head, "kp2": kp_second, "n2": kp_n2, "n1": kp_n1, "n0": kp_n0}
    out_json = CARD / "probe_guard_green.json"
    SHOTS.mkdir(parents=True, exist_ok=True)

    # ---------------- [1] 真跑浏览器（当前代码） ----------------
    emit("\n[1] 真浏览器：连续超界滚动 / 小字 / 上一下一（%s）" % PROBE.name)
    D, hint = run_probe(port, "green", params, out_json, lean=not args.full,
                        cdp=9441 if not args.full else 9442)
    check(D is not None, "探针跑完并写出结果 JSON", hint)
    if D is None:
        return 2
    S = D.get("steps") or {}
    boot, proof = S.get("boot") or {}, S.get("wheelSelfProof") or {}
    roll, cs = S.get("roll") or {}, S.get("canScroll") or {}
    notes = (S.get("notes") or {}).get("cases") or []
    pn = S.get("prevNext") or {}
    check(boot.get("ok"), "进了知识点页签并挂上课件下拉", str(boot))
    check(proof.get("moved"),
          "自证：无弹窗时同一套真滚轮**确实**能滚外层（否则后面的'没被滚'不算数）",
          "outer %s → %s（max %s）" % (proof.get("afterSet"), proof.get("afterWheel"),
                                       proof.get("outerMax")))

    # ---------------- [2] 滚动不出窗（核心） ----------------
    emit("\n[2] 弹窗内滚到顶 / 底后继续滚 ≥10 次：弹窗必须还在、外层不许动")
    up, down = roll.get("up") or [], roll.get("down") or []
    check(len(up) >= 10 and all(x.get("exists") and x.get("visible") for x in up),
          "到顶后继续向上滚 %d 次：每一拍弹窗都在（DOM 存在 + display 不是 none）"
          % len(up),
          "最后一拍 %s" % (up[-1] if up else None))
    check(len(down) >= 10 and all(x.get("exists") and x.get("visible") for x in down),
          "到底后继续向下滚 %d 次：每一拍弹窗都在" % len(down),
          "最后一拍 %s" % (down[-1] if down else None))
    fin = roll.get("final") or {}
    check(roll.get("survived") and fin.get("kp") == kp_head,
          "滚完弹窗仍在，且挂的还是同一条知识点（dataset.kp 未被清）",
          "final=%s（期望 kp=%s）" % (fin, kp_head))
    check(roll.get("outerUnchanged") and not roll.get("outerTouched"),
          "外层 `document.scrollingElement.scrollTop` **逐拍**都没变（不是只看首尾："
          "上滚 -120 与下滚 +120 会互相抵消，只看首尾会漏判）",
          "起点 %s · 逐拍取值 %s（测前先把外层滚到中间，max %s）"
          % (roll.get("outerBefore"), roll.get("outerSamples"), roll.get("outerMax")))
    emit("      正文区 overscroll-behavior-y 实测 = %s（防线①的现场值）"
         % roll.get("overscroll"))
    emit("      逐拍外层 scrollTop = %s"
         % sorted({x.get("outer") for x in (up + down) if x.get("outer") is not None}))

    # ---------------- [3] 弹窗内还能正常滚 ----------------
    emit("\n[3] 弹窗内正文仍能滚到首尾（别把滚动一起截死）")
    check(cs.get("ok") and cs.get("canScroll"), "正文区是**真的**可滚（max > 20px）",
          "client=%s scrollH=%s max=%s" % (cs.get("client"), cs.get("scrollH"), cs.get("max")))
    check(cs.get("reachedTop") and cs.get("reachedBottom"),
          "正文能滚到最顶、也能滚到最底",
          "top=%s bottom=%s max=%s" % (cs.get("top"), cs.get("bottom"), cs.get("max")))

    # ---------------- [4] 笔记数小字（0 / 1 / ≥2） ----------------
    emit("\n[4] 弹窗里的「已绑定 N 条笔记」：N 与真值逐一对齐（0 条 / 1 条 / ≥2 条）")
    import re as _re
    seen_tags = set()
    for c in notes:
        tag = c.get("tag")
        seen_tags.add(tag)
        if c.get("truthError"):
            check(False, "笔记情形 %s：知识点 %s 打不开" % (tag, c.get("kp")),
                  str(c.get("truthError")))
            continue
        m = _re.search(r"已绑定\s*(\d+)\s*条笔记", str(c.get("uiNote") or ""))
        n_ui = int(m.group(1)) if m else None
        check(m is not None and n_ui == c.get("apiNotes") == c.get("truthNotes"),
              "情形 %s：%s 上小字「%s」与 /api/notes/by_kp=%s、note_count=%s 三方一致"
              % (tag, c.get("kp"), c.get("uiNote"), c.get("apiNotes"), c.get("truthNotes")),
              "opened=%s uiKp=%s" % (c.get("opened"), c.get("uiKp")))
    check({"n0", "n1", "n2"} <= seen_tags,
          "0 条 / 1 条 / ≥2 条三种情形都真跑到了", "实到 %s" % sorted(seen_tags))
    real = [c for c in notes if not c.get("truthError")]
    check(real and all(c.get("askedSummary") for c in real)
          and not any(c.get("askedNotesApi") for c in real),
          "这行小字**没有**额外发一次重查询：打开弹窗那一刻的请求里有 "
          "/api/summary-kp（弹窗本来就要调它）、没有 /api/notes/by_kp",
          "实测请求 = %s" % [(c.get("kp"), c.get("reqs")) for c in real])

    # ---------------- [5] 上一知识点 / 下一知识点 ----------------
    emit("\n[5] 「◀ 上一知识点」与「下一知识点」")
    head = pn.get("head") or {}
    hs, ht = head.get("prev") or {}, head.get("truth") or {}
    check(hs.get("has") and hs.get("disabled"),
          "链首 %s：按钮存在且**置灰不可点**" % kp_head,
          "disabled=%s text=%r title=%r" % (hs.get("disabled"), hs.get("text"), hs.get("title")))
    check(ht.get("prev_id") == "", "后端也认为它是链首（prev_id 为空串）",
          "order %s/%s" % (ht.get("order_index"), ht.get("order_total")))
    check(head.get("stillSameKp") and head.get("stillVisible"),
          "对置灰按钮点了一次：知识点没变、弹窗也没关",
          "deadClick=%s" % (head.get("deadClick"),))
    pc = pn.get("prevClick") or {}
    check(pc.get("opened") and pc.get("want") and pc.get("got") == pc.get("want"),
          "次条 %s 点「上一知识点」→ 打开的就是后端给的 prev_id" % kp_second,
          "want=%s got=%s（按钮 data-prev=%s）"
          % (pc.get("want"), pc.get("got"), (pc.get("btnBefore") or {}).get("dataPrev")))
    nx = pn.get("next") or {}
    na = nx.get("after") or {}
    check((nx.get("click") or {}).get("ok") and na.get("kp") == (nx.get("before") or {}).get("next_id"),
          "「下一知识点」仍能跳（== 后端 next_id）",
          "want=%s got=%s" % ((nx.get("before") or {}).get("next_id"), na.get("kp")))
    check(na.get("deckStillOpen") and na.get("deckFlag") == "1",
          "已展开的课件跟着切到新知识点那一页（原有行为没被破坏）",
          "deckOpen=%s flag=%r img=%s" % (na.get("deckStillOpen"), na.get("deckFlag"),
                                          (na.get("imgSrc") or "")[:60]))

    # ---------------- [6] 无 JS 报错 ----------------
    emit("\n[6] 真浏览器 JS 报错")
    check(not D.get("errors"), "全程没有 JS 报错（Log.entryAdded + Runtime.exceptionThrown）",
          "、".join(D.get("errors") or []) or "0 条")

    # ---------------- [7] 红绿对照（--full） ----------------
    if args.full:
        emit("\n[7] 红绿对照：未修复基线跑同一套滚动动作，必须能测出病")
        if not BASELINE.exists():
            check(False, "找不到未修复基线 %s" % BASELINE, "无法做对照")
        else:
            bport = free_port()
            bproc = spawn_dash(exe, BASELINE, bport, ROOT / "logs" / "_c0056_baseline.out.log")
            if not args.keep:
                atexit.register(lambda: bproc.terminate())
            if not wait_up(bport):
                check(False, "基线临时实例打不通", "端口 %d" % bport)
            else:
                bapi = api(bport, "/api/summary-kp?doc_id=%d&kp_id=%s" % (DOC, kp_head))
                check(bapi.get("note_count") is None,
                      "基线实例上确实**没有** note_count（说明跑的是改动前的代码）",
                      "note_count=%r" % bapi.get("note_count"))
                rb_json = CARD / "probe_guard_red.json"
                RB, bhint = run_probe(bport, "red", params, rb_json, lean=False, cdp=9443)
                check(RB is not None, "基线探针跑完", bhint)
                if RB:
                    r2 = (RB.get("steps") or {}).get("roll") or {}
                    bpn = (RB.get("steps") or {}).get("prevNext") or {}
                    bcases = ((RB.get("steps") or {}).get("notes") or {}).get("cases") or []
                    broke = (not r2.get("survived")) or bool(r2.get("outerTouched"))
                    check(broke,
                          "基线（未修复）上**复现**了病：弹窗消失 / 外层被滚",
                          "survived=%s outerTouched=%s 逐拍外层 %s（起点 %s）"
                          % (r2.get("survived"), r2.get("outerTouched"),
                             r2.get("outerSamples"), r2.get("outerBefore")))
                    check(((bpn.get("head") or {}).get("prev") or {}).get("has") is False
                          and all(c.get("uiNote") is None for c in bcases)
                          and r2.get("overscroll") == "auto",
                          "基线弹窗里本卡新增的三样东西都不在（上一按钮 / 小字 / "
                          "overscroll 防线）——反证这些东西确实是本卡加的",
                          "prev.has=%s uiNote=%s overscroll=%s"
                          % (((bpn.get("head") or {}).get("prev") or {}).get("has"),
                             [c.get("uiNote") for c in bcases], r2.get("overscroll")))
        # 交叉：同目录他人预置的探针（若有）也跑一遍，参数用本次挑出来的
        other = ROOT / "tools" / "probe_c0056_browser.mjs"
        if other.exists():
            emit("\n[7b] 交叉验证：tools/probe_c0056_browser.mjs（他人预置的那份）")
            oj = CARD / "probe_external.json"
            cmd = ["node", str(other), "--port", str(port), "--doc", str(DOC),
                   "--first", kp_head, "--second", kp_second,
                   "--n2", kp_n2, "--n1", kp_n1, "--n0", kp_n0,
                   "--last", seq[-1], "--cdp", "9444",
                   "--outdir", str(SHOTS), "--json", str(oj)]
            rr = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=300)
            check(oj.exists(), "外部探针跑完并写出结果", (rr.stdout or "").strip()[-200:])
            if oj.exists():
                EX = json.loads(oj.read_text(encoding="utf-8"))
                exr = ((EX.get("steps") or {}).get("roll")) or {}
                check(bool(exr.get("survived")) and bool(exr.get("outerUnchanged")),
                      "外部探针同口径结论：弹窗活着 + 外层没被穿透",
                      "survived=%s outerUnchanged=%s" % (exr.get("survived"),
                                                         exr.get("outerUnchanged")))

    emit("\n[8] 未碰 8756：临时实例端口 %d、基线端口另取，8756 进程未被本脚本启停"
         % port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
