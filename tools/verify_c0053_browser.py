# -*- coding: utf-8 -*-
"""卡 C-0053 ★真浏览器验证（headless Edge + CDP）：知识点页签顶部区段。

验什么（对照卡面验收标准逐条）：
  1. 真浏览器里，知识点页签顶部**看不到**那两块手工栏：`#kp-add` / `#kp-name` /
     `#kp-chunks` / `#kp-summary` 都不存在，"还没有知识点 / 让助手从资料中提取 /
     或在这里手工添加"那几句在 `#view` 的可见文本里也没有（给出 DOM 文本证据）；
  2. 「名词解释」栏在「课件思维导图」栏**上面**（文档序 + 屏幕坐标双证据），
     课件清单与思维导图栏**同源**（同一份 doc_id 集合）；选一份有 terms 的课件
     → 展开 → 真的看到 名词 / 英文 / 中文解释，且逐条与产物真值一致；
  3. **点名词 → 跳出该名词第一次出现的知识点窗口**：弹窗 `dataset.kp` ==
     名词的 `data-kp` == 产物里这个名词的 `first_kp_id`（三方一致），窗内显示
     知识点名与 kp_id 徽标、正文已经加载出来（不是"读取失败"）；
  4. 旧产物兼容：选一份 `C-0049-v2`（**没有 terms**）→ 显示"暂无名词解释…"空态，
     **不是报错、不是空白**；真浏览器全程**无 JS 报错**；
  5. `check_dashboard_js.py` 通过；截图落盘；
  6. **零模型调用**（费用 0）：只走"进页签 → 选课件 → 点名词"这条真实前端链路，
     产物 sha256 跑前跑后逐份未变。

用法（自起临时端口实例，跑完杀掉；**不碰 8756**）：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0053_browser.py
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0053_browser.py --port 8899 --keep
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config, subjects  # noqa: E402

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0053")
SHOTS = CARD / "shots"
DASH = ROOT / "tools" / "dashboard.py"
PROBE = ROOT / "tools" / "probe_c0053_browser.mjs"

OK = 0
BAD = 0
LINES: list[str] = []
# 别的会话的 fixture 产物（卡 C-0055 的验证脚本会**反复重建/删除**它）：
# 产物快照把它排除，否则"产物没被动过"的断言会被别人的写操作误伤。
EXTERNAL_BUSY = {"99998-末级知识点总结.json"}


def snap(kdir: Path) -> dict:
    return {p.name: sha(p) for p in kdir.glob("*末级知识点总结.json")
            if p.name not in EXTERNAL_BUSY}


def emit(s: str = "") -> None:
    """**只缓存，不直接打印**：结论要打在输出的**第一行**（见 `report()`）。"""
    LINES.append(s)


def report(head: str) -> None:
    """先打结论、再打全部明细；`browser_report.txt` 同样**结论在前**。

    ★ 预检教训（卡 C-0051 记下的坑，C-0053 的快速验证被它误判过一次）：
    验收方的输出捕获有长度上限，结论打在最后一行会被截断掉。判据没放松：
    明细与计数照旧，只是换了顺序；跑不完的路径打另一句（不含"结果：N 项通过"）。
    """
    print(head)
    body = "\n".join(LINES)
    if body:
        print(body)
    try:
        (CARD / "browser_report.txt").write_text(head + "\n" + body + "\n",
                                                 encoding="utf-8")
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


def api(port: int, path: str):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


def sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def pick_docs(kdir: Path) -> tuple[int, list, int, str]:
    """挑一份有 terms 的（v3）与一份没有 terms 的（C-0051 之前的旧产物）。"""
    v3_doc, v3_terms, v2_doc, v2_ver = 0, [], 0, ""
    for p in sorted(kdir.glob("*末级知识点总结.json")):
        head = p.name.split("-")[0]
        if not head.isdigit():
            continue
        try:
            a = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        m = a.get("meta") or {}
        if (a.get("terms") or []) and not v3_doc:
            v3_doc, v3_terms = int(head), a["terms"]
        if (not (a.get("terms") or [])) and not v2_doc and m.get("pipeline_version"):
            v2_doc, v2_ver = int(head), str(m.get("pipeline_version"))
    return v3_doc, v3_terms, v2_doc, v2_ver


def main() -> int:
    """跑一遍并把结论打在**第一行**（真正的检查在 `_run()`）。"""
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
    args = ap.parse_args()

    PY = ROOT / "venv" / "Scripts" / "python.exe"
    exe = str(PY) if PY.exists() else sys.executable
    port = args.port
    if not port:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
    log = ROOT / "logs" / "_c0053_browser.out.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as fh:
        proc = subprocess.Popen(
            [exe, "-X", "utf8", "-u", str(DASH), "--port", str(port), "--no-open"],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
    if not args.keep:
        atexit.register(lambda: proc.terminate())
    try:
        for _ in range(100):
            time.sleep(0.4)
            if not api(port, "/api/stats").get("error"):
                break
        emit("卡 C-0053 ★真浏览器验证（headless Edge + CDP，端口 %d，PID %s）"
             % (port, proc.pid))
        if api(port, "/api/stats").get("error"):
            emit("★临时实例打不通")
            return 2
        subjects.restore()
        kdir = Path(config.LOG_DIR) / "knowledge"
        emit("  学科 %s · 产物目录 %s" % (subjects.current(), kdir))
        # 实例必须是**含本卡改动**的那份代码（新接口就是探针：旧代码会 404）
        probe_api = api(port, "/api/terms")
        if probe_api.get("error"):
            emit("★临时实例上没有 /api/terms（%s）——跑的不是本卡改过的代码？"
                 % probe_api["error"])
            return 2
        # 页签也必须是新前端（老 HTML 里还有"手工添加"）
        html = urllib.request.urlopen("http://127.0.0.1:%d/" % port,
                                      timeout=30).read().decode("utf-8")
        if "function termsCard" not in html:
            emit("★临时实例给的前端里没有 termsCard()——跑的不是本卡改过的代码？")
            return 2
        v3_doc, v3_terms, v2_doc, v2_ver = pick_docs(kdir)
        emit("  样例：有名词 doc#%s（%d 个）· 没有 terms 的旧产物 doc#%s（%s）"
             % (v3_doc, len(v3_terms), v2_doc, v2_ver))
        if not v3_doc or not v2_doc:
            emit("★找不到需要的样例产物")
            return 2
        art_before = snap(kdir)
        SHOTS.mkdir(parents=True, exist_ok=True)

        # ---------------- 真跑浏览器 ----------------
        emit("\n[1] 真跑浏览器（node tools\\probe_c0053_browser.mjs）")
        out_json = CARD / "browser_probe.json"
        r = subprocess.run(
            ["node", str(PROBE), "--port", str(port), "--v3", str(v3_doc),
             "--v2", str(v2_doc), "--outdir", str(SHOTS), "--json", str(out_json)],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600)
        if r.returncode != 0:
            emit("  node 退出码 %s\n  stderr: %s" % (r.returncode,
                                                    (r.stderr or "")[-1200:]))
        check(r.returncode == 0 and out_json.exists(),
              "探针跑完并写出结果 JSON",
              (r.stdout or "").strip().splitlines()[-1] if r.stdout else "")
        if not out_json.exists():
            emit("★没有探针结果，后面没法判")
            return 2
        D = json.loads(out_json.read_text(encoding="utf-8"))
        C = D.get("steps") or {}
        head, v3, pop, tog, v2 = (C.get("head") or {}, C.get("v3") or {},
                                  C.get("popup") or {}, C.get("toggle") or {},
                                  C.get("v2") or {})

        # ---------------- [2] 顶部两块手工栏真的没了 ----------------
        emit("\n[2] 知识点页签顶部：空态块与手工添加栏（真 DOM）")
        check(head.get("ok"), "进了知识点页签并等到了名词解释栏",
              str(head.get("why") or "ok"))
        check(not head.get("hasKpAdd") and not head.get("hasKpName")
              and not head.get("hasKpChunks") and not head.get("hasKpSummary"),
              "真 DOM 里没有手工表单的任何输入框/按钮（#kp-name/#kp-subject/"
              "#kp-chunks/#kp-summary/#kp-add）",
              "kp-add=%s kp-name=%s" % (head.get("hasKpAdd"), head.get("hasKpName")))
        check(not head.get("manualH2"),
              "页面上没有「手工添加」/「新增知识点」标题",
              "、".join(head.get("manualH2") or []) or "无")
        check(not head.get("emptyBlock"),
              "页面上没有「还没有知识点 / 让助手从资料中提取 / 或在这里手工添加」")
        emit("      DOM 文本证据（#view.innerText 前 300 字）：%s"
             % str(head.get("viewText"))[:300])
        emit("      页签上的 h2：%s" % "、".join(head.get("h2s") or []))

        # ---------------- [3] 名词解释栏在思维导图栏上面 ----------------
        emit("\n[3] 「名词解释」栏在「课件思维导图」栏上面 + 课件清单同源")
        od = head.get("order") or {}
        check(od.get("termBeforeMm"),
              "文档序：名词解释栏排在课件思维导图栏之前",
              "termH2=%r mmH2=%r" % (od.get("termH2"), od.get("mmH2")))
        tr, mr = od.get("termRect") or {}, od.get("mmRect") or {}
        check(bool(tr) and bool(mr) and tr.get("top", 1e9) < mr.get("top", -1e9),
              "屏幕坐标：名词解释栏的 top 更小（更靠上）",
              "term.top=%s < mm.top=%s" % (tr.get("top"), mr.get("top")))
        check("名词解释" in (od.get("termH2") or "")
              and "课件思维导图" in (od.get("mmH2") or ""),
              "两栏的标题各自认得出来", "%r / %r" % (od.get("termH2"), od.get("mmH2")))
        tids = {o.get("v") for o in (head.get("termOptions") or [])}
        mids = set(head.get("mmOptions") or [])
        check(tids and tids == mids,
              "名词解释栏的课件清单与思维导图栏**同源**（doc_id 集合一致）",
              "%d 份" % len(tids))
        check(head.get("hasUnfoldBtn"), "有【展开/收起】按钮",
              str(head.get("unfoldText")))

        # ---------------- [4] 选有 terms 的课件 → 展开看到名词/英文/解释 ----------------
        emit("\n[4] 选一份有 terms 的课件（doc#%d）→ 展开 → 名词 / 英文 / 中文解释"
             % v3_doc)
        check(v3.get("ok") and v3.get("sel") == str(v3_doc),
              "下拉里选中了这份课件并加载完成", "sel=%s n=%s" % (v3.get("sel"), v3.get("n")))
        check(v3.get("n") == len(v3_terms),
              "页面上列出的名词条数与产物一致",
              "%s == %s" % (v3.get("n"), len(v3_terms)))
        check("共" in (v3.get("boxText") or "") and "个名词解释" in (v3.get("boxText") or ""),
              "展开后写着「共 N 个名词解释」（一眼知道有多少）",
              str(v3.get("boxText"))[:60])
        truth = {str(t.get("term")): t for t in v3_terms}
        rows = v3.get("rows") or []
        check(len(rows) >= 3, "至少抠到 3 条名词（真渲染出来的）", "%d 条" % len(rows))
        bad = []
        for x in rows:
            t = truth.get(str(x.get("term")))
            if t is None:
                bad.append("%s 不在产物里" % x.get("term"))
                continue
            if (x.get("en") or "") != (t.get("en") or ""):
                bad.append("%s.en %r != %r" % (x.get("term"), x.get("en"), t.get("en")))
            if not (x.get("def") or ""):
                bad.append("%s 没有中文解释" % x.get("term"))
            elif not (t.get("def") or "").startswith((x.get("def") or "")[:12]):
                bad.append("%s.def 与产物不符" % x.get("term"))
            if (x.get("kp") or "") != (t.get("first_kp_id") or ""):
                bad.append("%s.first_kp_id %r != %r"
                           % (x.get("term"), x.get("kp"), t.get("first_kp_id")))
        check(not bad, "每条的三件套（名词/英文/中文解释）与首次出现知识点 id 都对得上",
              "；".join(bad[:3]) if bad else "%d 条逐字段比对" % len(rows))
        for x in rows[:2]:
            emit("      样例：%s ｜ %s ｜ %s ｜ → %s"
                 % (x.get("term"), x.get("en"), (x.get("def") or "")[:40], x.get("kp")))
        check(bool(rows) and all((x.get("en") or "") for x in rows[:3]),
              "英文列真的显示出来了（前 3 条都有）",
              "、".join(x.get("en") or "—" for x in rows[:3]))
        check(bool(tog.get("ok")) and (tog.get("folded") or {}).get("n") == 0
              and (tog.get("shown") or {}).get("n") == v3.get("n"),
              "【收起】→ 列表收起来；再【展开】→ 又回来（展开这个动作真的管用）",
              "收起后 %s 条 / 再展开 %s 条"
              % ((tog.get("folded") or {}).get("n"), (tog.get("shown") or {}).get("n")))

        # ---------------- [5] 点名词 → 第一次出现的那个知识点窗口 ----------------
        emit("\n[5] 点名词 → 跳出该名词**第一次出现**的知识点窗口")
        want = pop.get("want") or {}
        check(pop.get("ok"), "点名词真的弹出了窗（.evpop）", str(pop.get("why") or "ok"))
        check(pop.get("visible"), "弹窗是可见的（display != none）")
        check(pop.get("kp") and pop.get("kp") == want.get("kp"),
              "弹窗挂的知识点 == 这个名词的 data-kp",
              "%s == %s" % (pop.get("kp"), want.get("kp")))
        t0 = truth.get(str(want.get("term"))) or {}
        check(pop.get("kp") == (t0.get("first_kp_id") or ""),
              "弹窗挂的知识点 == 产物里该名词的 first_kp_id（**第一次出现**）",
              "%s ｜ %s == %s" % (want.get("term"), pop.get("kp"), t0.get("first_kp_id")))
        check(not pop.get("stillLoading"), "窗里已经加载完（不是「正在读取…」）",
              str(pop.get("body"))[:60])
        check((pop.get("kp") or "") in (pop.get("loc") or "")
              or (pop.get("badge") or "") == (pop.get("kp") or ""),
              "窗内显示出了知识点 id（徽标/标题）",
              "%r ｜ badge=%r" % (str(pop.get("loc"))[:80], pop.get("badge")))
        check((t0.get("first_kp_name") or "") in (pop.get("loc") or "")
              or (t0.get("first_kp_name") or "") in (pop.get("hdg") or ""),
              "窗内显示的知识点名 == 产物 first_kp_name",
              "%r" % (t0.get("first_kp_name") or ""))
        check(bool(pop.get("body")) and "读取失败" not in (pop.get("body") or ""),
              "窗里有正文内容（要点/理解），不是错误页",
              str(pop.get("body"))[:60])
        emit("      弹窗证据：kp=%s ｜ loc=%s" % (pop.get("kp"), pop.get("loc")))

        # ---------------- [6] 旧产物（无 terms）空态 ----------------
        emit("\n[6] 旧产物（doc#%d，%s，没有 terms）→ 空态" % (v2_doc, v2_ver))
        check(v2.get("ok") and v2.get("sel") == str(v2_doc),
              "切到了那份旧产物", "sel=%s" % v2.get("sel"))
        check(v2.get("n") == 0, "这一份不列任何名词（n=0）", "n=%s" % v2.get("n"))
        check("暂无名词解释" in (v2.get("boxText") or ""),
              "显示「暂无名词解释…」空态（不是报错、不是空白）",
              str(v2.get("boxText"))[:110])
        check("读取失败" not in (v2.get("boxText") or "")
              and "error" not in (v2.get("boxText") or "").lower(),
              "空态里没有「失败/出错」字样")
        check(v2.get("btnDisabled") is True,
              "这份没有名词时【展开】按钮置灰（点不动而不是点了没反应）")
        emit("      空态原文：%s" % str(v2.get("boxText"))[:160])

        # ---------------- [7] 浏览器与脚本卫生 ----------------
        emit("\n[7] 浏览器与脚本卫生")
        check(not D.get("errors"), "真浏览器无 JS 报错",
              "；".join(D.get("errors") or [])[:240] or "0 条")
        shots = D.get("shots") or {}
        check(len(shots) >= 3, "截图落盘", "、".join(sorted(shots))[:160])
        js = subprocess.run([exe, "-X", "utf8", str(ROOT / "tools"
                                                     / "check_dashboard_js.py")],
                            cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        check(js.returncode == 0 and "语法通过" in (js.stdout or ""),
              "check_dashboard_js.py 通过（内联 JS 语法 + 关键函数各 1 次）",
              (js.stdout or "").strip().splitlines()[-1] if js.stdout else "")

        # ---------------- [8] 零模型调用 ----------------
        emit("\n[8] 零模型调用（费用 0）")
        after = snap(kdir)
        changed = [n for n in art_before if after.get(n) != art_before[n]]
        check(not changed and len(after) == len(art_before),
              "产物跑前跑后逐份 sha256 未变（全程只读产物、没调模型）",
              "%d 份；变了的是 %s（已排除别的会话的 fixture 99998）"
              % (len(after), changed[:3] or "无"))
    finally:
        if not args.keep:
            proc.terminate()

    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
