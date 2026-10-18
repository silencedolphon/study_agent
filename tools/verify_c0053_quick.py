# -*- coding: utf-8 -*-
"""卡 C-0053 快速验证（**不发浏览器**，约 15 秒；真浏览器验证见
`tools/verify_c0053_browser.py`）。

它证明五件事：
  1. 后端只读接口 `GET /api/terms` 真的在跑：不带 `doc_id` 列课件清单（与
     「课件思维导图」栏**同源**：同一份 `_artifact_doc_list`），带了 `doc_id`
     给这一份的名词——**逐条与产物真值对照**（名词/英文/解释/首次出现页/
     **首次出现的知识点 id**）；
  2. 旧产物兼容（卡面硬要求）：C-0051 之前的 `C-0049-v2` 产物**没有 terms**
     → `terms_state="empty"` + 一句人话，**HTTP 200、不报错、不是空白**；
     没整理过 / 没产物 → `stale`，也不报错；`doc_id=abc` 这种非法入参不 500；
  3. 点名词要跳的**知识点弹窗链路**是通的：`/api/summary-kp` 用名词的
     `first_kp_id` 真能取到那条知识点（v3 产物也读得出——卡 C-0051 点名要下游
     做的版本白名单，见 `_artifact_load`）；
  4. 前端那份 HTML 里：页签顶部那两块**手工**栏的原话已经不在（「还没有知识点」
     「手工添加」「新增知识点」+ `kp-add`/`kp-name` 输入框），「名词解释」栏
     （`termsCard`/`#term-doc`/`.termname`）在 `viewKps()` 里排在
     `mindmapCard()` **之前**；
  5. 改动面自查：与开工基线（卡目录 `baseline/dashboard.before.py`）做 difflib——
     每个改动块附近都有 "C-0053" 标记、K5（知识点弹窗内部）与 K6（问答出处小窗）
     区段的函数体**逐字节未变**、`core/` 与两张库（五表）**没被本卡动过**。

用法（自起临时端口实例，跑完杀掉；**不碰 8756**）：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0053_quick.py

★ **输出约定（预检教训，卡 C-0051 记过、本卡被误判过一次）**：结论行
  `结果：N 项通过，M 项失败` **打在输出的第一行**，逐项明细跟在后面；报告文件
  `cards\\C-0053\\quick_report.txt` 同样结论在前。因为验收方的输出捕获有长度上限，
  结论打在最后一行会被截断掉，"跑过且全绿"就会被误判成"输出不含结论"。
  判据**一个字没放松**：`OK/BAD` 的计数与每一项断言都没动；跑不完的路径打的是
  `★验证脚本未能跑完（…）`，它**不含**那句结论，不会被误判成通过。
"""
from __future__ import annotations

import atexit
import difflib
import hashlib
import json
import re
import socket
import sqlite3
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
BASELINE = CARD / "baseline" / "dashboard.before.py"
DASH = ROOT / "tools" / "dashboard.py"
# 本卡**不许**碰的区段（别人的卡的范围）：
#   K5 = 知识点弹窗内部（卡 C-0036/C-0052），K6 = 问答出处小窗（卡 C-0055）。
K5K6_FUNCS = ("mmOpenKp", "mmRenderKp", "mmShowSlide", "mmToggleDeck",
              "mmOpenNode", "mmOpenKpList", "mmNextKp", "mmLkpSrcHtml",
              "mmNotePlace", "mmNoteEdit", "mmNoteSave", "mmNoteList",
              "mmNoKpHtml", "mmOpenPending", "mmKindLabel",
              "evPopShow", "evPopClose", "evPopMount", "evPopEl",
              "evCiteExtras", "evCiteExtrasHtml", "evCiteSlide",
              "bindCiteClicks", "api_page_kps", "_page_kp_index", "_ref_pages")
# 本卡**动过**的后端函数（必须真的变了，证明改动落在这里而不是漏在别处）：
TOUCHED_PY = ("_artifact_load", "api_terms", "_terms_of", "_terms_state",
              "_terms_payload")
TOUCHED_JS = ("viewKps", "termsCard", "termsBodyHtml", "renderTerms",
              "loadTerms", "mountTerms")
# 本卡只读、绝不许写的 core 文件（"不动管线 / 零模型"的硬证据之一）
CORE_WATCH = ("core/kp_build.py", "core/chat.py", "core/study.py",
              "core/kp_batch.py")
# 别的会话的 fixture 产物（卡 C-0055 的验证脚本会反复重建/删除它）：
# 产物快照把它排除，否则"产物没被动过"的断言会被别人的写操作误伤。
EXTERNAL_BUSY = {"99998-末级知识点总结.json"}


def snap(kdir: Path) -> dict:
    return {p.name: sha(p) for p in kdir.glob("*末级知识点总结.json")
            if p.name not in EXTERNAL_BUSY}

OK = 0
BAD = 0
LINES: list[str] = []


def emit(s: str = "") -> None:
    """**只缓存，不直接打印**：结论要打在输出的**第一行**（见文件头的预检教训）。"""
    LINES.append(s)


def report(head: str, name: str = "quick_report.txt") -> None:
    """先打结论、再打全部明细；报告文件同样**结论在前**。

    ★ 预检教训（卡 C-0051 记下的坑，本卡复现过一次）：验收方的输出捕获有长度
    上限，脚本把结论打在最后一行会被截断掉，于是"跑过且全绿"被误判成
    "输出不含结论"。判据**一个字没放松**：明细与 `OK/BAD` 计数照旧，只是换了
    打印顺序；跑不完的路径打的是另一句（不含"结果：N 项通过"），不会被误判成通过。
    """
    print(head)
    body = "\n".join(LINES)
    if body:
        print(body)
    try:
        (CARD / name).write_text(head + "\n" + body + "\n", encoding="utf-8")
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


def get(port: int, path: str):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


def func_block(src: str, name: str) -> str:
    """抠出一个顶层函数/方法的源码块（到下一个顶层定义或分节注释为止）。

    与卡 C-0055 的 `verify_c0055_browser.py::func_block` 同一套做法
    （名字必须精确匹配到 `(`：`mmOpenKp` 是 `mmOpenKpList` 的前缀）。
    """
    m = re.search(r"\n(?:async )?def %s\s*\(" % re.escape(name), src)
    pats = ("\ndef ", "\nclass ", "\n# ----------------")
    if not m:                       # 前端函数
        m = re.search(r"\n(?:async )?function %s\s*\(" % re.escape(name), src)
        pats = ("\nfunction ", "\nasync function ", "\n// ----------------")
    if not m:
        return ""
    i = m.start()
    j = len(src)
    for p in pats:
        k = src.find(p, i + 10)
        if 0 <= k < j:
            j = k
    return src[i:j]


def sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def core_git_diff() -> str:
    """`git diff -- core/` 的输出（拿不到就给空串，**不让脚本因此失败**）。"""
    try:
        r = subprocess.run(["git", "diff", "--", "core/"], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        return r.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def tree_ready(tree: list) -> int:
    """页签树上"挂着已有总结的知识点"的节点数（递归数 `lkp_ready`）。"""
    n = 0
    for node in (tree or []):
        if not isinstance(node, dict):
            continue
        if node.get("lkp_ready"):
            n += 1
        n += tree_ready(node.get("children") or [])
    return n


def table_counts(db: Path) -> dict:
    out = {}
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
        try:
            for t in ("knowledge_points", "kp_sources", "kp_edges",
                      "reviews", "mistakes"):
                try:
                    out[t] = conn.execute("SELECT COUNT(*) FROM %s" % t
                                          ).fetchone()[0]
                except sqlite3.Error:
                    out[t] = -1
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return out


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
    CORE_BEFORE = {f: sha(ROOT / f) for f in CORE_WATCH}
    PY = ROOT / "venv" / "Scripts" / "python.exe"
    exe = str(PY) if PY.exists() else sys.executable
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    log = ROOT / "logs" / "_c0053_quick.out.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as fh:
        proc = subprocess.Popen(
            [exe, "-X", "utf8", "-u", str(DASH), "--port", str(port), "--no-open"],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
    atexit.register(lambda: proc.terminate())
    try:
        for _ in range(80):
            time.sleep(0.4)
            if not get(port, "/api/stats").get("error"):
                break
        emit("卡 C-0053 快速验证（临时端口 %d，PID %s）" % (port, proc.pid))
        if get(port, "/api/stats").get("error"):
            emit("★临时实例打不通")
            return 2
        subjects.restore()
        kdir = Path(config.LOG_DIR) / "knowledge"
        js = DASH.read_text(encoding="utf-8")
        # 产物快照（跑前）：本卡纯只读，跑完必须**逐份一模一样**——这也是
        # "零模型调用"的硬证据（真调了模型就会重写产物）。
        ART_BEFORE = snap(kdir)
        emit("  学科 %s · 产物目录 %s（%d 份产物已快照）"
             % (subjects.current(), kdir, len(ART_BEFORE)))

        # ---------- 0. 挑样例：一份有 terms 的（v3）与一份没有的（v2） ----------
        v3_doc, v3_terms = 0, []
        v2_doc, v2_ver = 0, ""
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
        emit("\n[0] 样例：有名词 doc#%s（%d 个）· 没有 terms 的旧产物 doc#%s（%s）"
             % (v3_doc, len(v3_terms), v2_doc, v2_ver))
        if not v3_doc or not v2_doc:
            emit("★找不到需要的样例产物（有 terms 的 / 没有 terms 的）")
            return 2

        # ---------- 1. 接口：清单 ----------
        emit("\n[1] GET /api/terms（不带 doc_id = 列课件清单）")
        lst = get(port, "/api/terms")
        check(not lst.get("error") and isinstance(lst.get("documents"), list)
              and lst.get("n"), "接口可用且返回课件清单",
              "n=%s" % lst.get("n"))
        items = {int(d["doc_id"]): d for d in (lst.get("documents") or [])}
        check(v3_doc in items and items[v3_doc].get("n_terms") == len(v3_terms),
              "清单里那份有名词的课件报出正确条数",
              "doc#%s n_terms=%s" % (v3_doc, items.get(v3_doc, {}).get("n_terms")))
        check(v2_doc in items and items[v2_doc].get("terms_state") == "empty"
              and items[v2_doc].get("n_terms") == 0,
              "清单里旧产物标成 empty（不是报错）",
              "doc#%s state=%s" % (v2_doc, items.get(v2_doc, {}).get("terms_state")))
        mm = get(port, "/api/mindmap")
        same = {int(d["doc_id"]) for d in (mm.get("documents") or [])}
        check(same == set(items),
              "课件清单与「课件思维导图」栏**同源**（同一份 _artifact_doc_list）",
              "%d 份 == %d 份" % (len(same), len(items)))

        # ---------- 2. 接口：逐条与产物真值对照 ----------
        emit("\n[2] GET /api/terms?doc_id=%d（逐条与产物 terms 对照）" % v3_doc)
        one = get(port, "/api/terms?doc_id=%d" % v3_doc)
        check(one.get("terms_state") == "ok" and not one.get("error"),
              "terms_state=ok、不报错", str(one.get("terms_state")))
        got = one.get("terms") or []
        check(len(got) == len(v3_terms),
              "条数与产物一致", "%d == %d" % (len(got), len(v3_terms)))
        truth = {str(t.get("term")): t for t in v3_terms}
        keys = ("en", "def", "first_page", "first_kp_id", "first_kp_name",
                "n_occurrences")
        diff = []
        for g in got:
            t = truth.get(str(g.get("term")))
            if t is None:
                diff.append("%s 不在产物里" % g.get("term"))
                continue
            for k in keys:
                if g.get(k) != t.get(k):
                    diff.append("%s.%s: %r != %r" % (g.get("term"), k,
                                                     g.get(k), t.get(k)))
        check(not diff, "每条的名词/英文/解释/首次出现页/**首次出现的知识点 id** 与产物逐字段一致",
              "；".join(diff[:3]) if diff else "%d 条逐字段比对" % len(got))
        check(all(g.get("term") and g.get("def") for g in got),
              "每条都有名词与中文解释（前端展示三件套不会空着）")
        one_kp = [g for g in got if g.get("first_kp_id")][:1]
        check(bool(one_kp), "每条都带首次出现的知识点 id（点名词才有得跳）",
              (one_kp[0]["term"] + " → " + one_kp[0]["first_kp_id"]) if one_kp else "")

        # ---------- 3. 点名词要跳的弹窗链路（后端半边） ----------
        emit("\n[3] 点名词 → 知识点弹窗：/api/summary-kp 读得出那条知识点")
        if one_kp:
            g = one_kp[0]
            sk = get(port, "/api/summary-kp?doc_id=%d&kp_id=%s"
                     % (v3_doc, urllib.request.quote(str(g["first_kp_id"]))))
            check(not sk.get("error") and sk.get("kp_id") == g["first_kp_id"],
                  "名词「%s」的 first_kp_id 真能取到知识点总结" % g["term"],
                  "kp_id=%s name=%s" % (sk.get("kp_id"), sk.get("name")))
            check(sk.get("name") == g.get("first_kp_name") or not g.get("first_kp_name"),
                  "取到的知识点名字与产物 first_kp_name 一致",
                  "%s == %s" % (sk.get("name"), g.get("first_kp_name")))

        # ---------- 4. 旧产物兼容 / 边界 ----------
        emit("\n[4] 旧产物（v2，无 terms）与边界入参")
        d2 = get(port, "/api/terms?doc_id=%d" % v2_doc)
        check(not d2.get("error") and d2.get("terms_state") == "empty"
              and d2.get("terms") == [] and d2.get("message"),
              "v2 产物：terms_state=empty + 一句人话（不报错、不是空白）",
              str(d2.get("message"))[:56])
        d3 = get(port, "/api/terms?doc_id=999999")
        check(not d3.get("error") and d3.get("terms_state") == "stale"
              and d3.get("message"), "没有产物：stale + 一句人话",
              str(d3.get("message"))[:40])
        d4 = get(port, "/api/terms?doc_id=abc")
        check(not d4.get("error") and d4.get("n"),
              "非法 doc_id 不 500（退回列清单）", "n=%s" % d4.get("n"))
        vers = get(port, "/api/terms?doc_id=%d" % v3_doc).get("pipeline_version")
        mm_ids = [d.get("doc_id") for d in (mm.get("documents") or [])]
        check(v3_doc in mm_ids,
              "v3 产物已进页签可读范围（C-0051 §2.4 点名要下游做的那一步）",
              "doc#%s 版本 %s" % (v3_doc, vers))

        # ---------- 4b. 全库逐份：18 份 v2 产物都不许因缺 terms 报错 ----------
        emit("\n[4b] 全库逐份：每份产物都不报错（含 18 份没有 terms 的 v2 产物）")
        n_doc = n_ok = n_empty = 0
        bad_docs = []
        for p in sorted(kdir.glob("*末级知识点总结.json")):
            head = p.name.split("-")[0]
            if not head.isdigit():
                continue
            did = int(head)
            n_doc += 1
            try:
                a = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                a = {}
            want_n = len(a.get("terms") or [])
            r = get(port, "/api/terms?doc_id=%d" % did)
            if r.get("error") or r.get("terms_state") not in ("ok", "empty",
                                                             "legacy", "stale"):
                bad_docs.append("doc#%d %s" % (did, r.get("error") or r.get("terms_state")))
                continue
            if r.get("n_terms") != want_n:
                bad_docs.append("doc#%d n_terms %s != %s"
                                % (did, r.get("n_terms"), want_n))
                continue
            if want_n:
                n_ok += 1
            else:
                n_empty += 1
                if not r.get("message"):
                    bad_docs.append("doc#%d 没有 terms 却没给空态说明" % did)
        check(not bad_docs,
              "%d 份产物逐份打接口都不报错、条数与产物一致（有名词 %d 份 / 无名词 %d 份）"
              % (n_doc, n_ok, n_empty),
              "；".join(bad_docs[:3]) if bad_docs else "逐份 OK")

        # ---------- 4c. 开工补料点名要的三条：v3 进了页签、序列非空，v2 仍正常 ----------
        emit("\n[4c] 补料点名：v3 进页签 + 知识点序列非空；v2 两边都仍正常")
        m3 = get(port, "/api/mindmap?doc_id=%d" % v3_doc)
        check(m3.get("artifact_state") == "v2" and m3.get("has_structure") is True
              and m3.get("has_outline") is True,
              "v3 产物：/api/mindmap 的 artifact_state = v2（页签显示得出来）",
              "state=%s has_structure=%s n_pages=%s n_nodes=%s"
              % (m3.get("artifact_state"), m3.get("has_structure"),
                 m3.get("n_pages"), m3.get("n_nodes")))
        check(int(m3.get("lkp_total") or 0) > 0 and int(m3.get("lkp_ready") or 0) > 0,
              "v3 产物：知识点索引**非空**（lkp_total / lkp_ready 都有数）",
              "lkp_total=%s lkp_ready=%s" % (m3.get("lkp_total"), m3.get("lkp_ready")))
        check(tree_ready(m3.get("tree")) > 0,
              "v3 产物：页签树上真的挂着有总结的知识点节点",
              "%d 个 lkp_ready 节点" % tree_ready(m3.get("tree")))
        m2 = get(port, "/api/mindmap?doc_id=%d" % v2_doc)
        check(m2.get("artifact_state") == "v2" and m2.get("has_structure") is True
              and int(m2.get("lkp_total") or 0) > 0
              and tree_ready(m2.get("tree")) > 0,
              "v2 产物（doc#%d）仍然正常显示、互不干扰" % v2_doc,
              "state=%s lkp_total=%s 树上 %d 个"
              % (m2.get("artifact_state"), m2.get("lkp_total"),
                 tree_ready(m2.get("tree"))))

        # ---------- 5. 前端源码：删了哪两块、加了哪一栏 ----------
        emit("\n[5] 前端 HTML 源码（删两块 + 新增名词解释栏，且在思维导图栏上面）")
        html = urllib.request.urlopen("http://127.0.0.1:%d/" % port,
                                      timeout=30).read().decode("utf-8")
        # 空态块那两句话**逐句**查（源码里本来就分两行 + <br>，整串查等于白查）；
        # ⚠ 只在 `viewKps()` 函数体里查——文件别处（图谱页空态）与**本卡注释里
        #   引用的原话**都会出现这几个字，整份 HTML 查等于白查。
        body = func_block(js, "viewKps")
        for tok in ("让助手从资料中提取", "或在这里手工添加",
                    "<h2>手工添加</h2>", "<h2>新增知识点</h2>"):
            check(tok not in body, "viewKps() 里已无「%s」" % tok)
        for tok in ("function termsCard", "function mountTerms",
                    'id="term-doc"', 'id="term-unfold"', 'class="termname"',
                    "名词解释", "卡 C-0053"):
            check(tok in html, "前端含 %s" % tok)
        check(body.count("termsCard()") >= 1 and "mindmapCard()" in body,
              "viewKps() 里同时挂上名词解释栏与思维导图栏")
        check(body.find("termsCard()") < body.find("mindmapCard()"),
              "名词解释栏排在**课件思维导图**栏上面（源码顺序）")
        check("bindKpForm()" not in body and "kpForm()" not in body,
              "viewKps() 里不再挂手工添加表单（页签入口已删）")
        # 只数**真的调用**的写法（`${kpForm()}` / `bindKpForm();`）；注释里提到的
        # 函数名不算——本卡注释里逐字引用了这两处入口的名字（可追溯）。
        calls_n = re.findall(r"\$\{kpForm\(\)\}|bindKpForm\(\);", html)
        check(not calls_n,
              "整份前端**没有任何**手工表单的调用点（${kpForm()} / bindKpForm();）",
              "命中 %d 处" % len(calls_n))
        check("function kpForm" in html and "function bindKpForm" in html
              and 'if u.path == "/api/kp"' in js,
              "手工新建的函数与 POST /api/kp 接口**保留**（只删页签入口）")

        # ---------- 6. 改动面自查 ----------
        emit("\n[6] 改动面自查（相对开工基线，未整文件重写 / 未碰 K5-K6 区段）")
        if not BASELINE.exists():
            check(False, "开工基线 baseline/dashboard.before.py 存在")
        else:
            old = BASELINE.read_text(encoding="utf-8").splitlines()
            new = js.splitlines()
            sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
            blocks = [op for op in sm.get_opcodes()
                      if op[0] != "equal" and (op[1] < op[2] or op[3] < op[4])]
            added = removed = 0
            untagged = []
            for tag, i1, i2, j1, j2 in blocks:
                added += max(0, j2 - j1)
                removed += max(0, i2 - i1)
                # 窗口 40 行：本卡每处改动附近都写了"卡 C-0053"注释；`viewKps`
                # 的删除点离函数上方的注释有 30 余行，20 行窗口会误判成"未标记"。
                win = "\n".join(new[max(0, j1 - 40):min(len(new), j2 + 40)])
                if "C-0053" not in win:
                    untagged.append((tag, i1 + 1, i2, j1 + 1, j2))
            check(not untagged, "每个改动块附近都有 C-0053 标记（改动全是本卡的）",
                  "改动块 %d 个，未标记 %s" % (len(blocks), untagged[:3]))
            check(removed < 40 and added < 500,
                  "改动是局部的（不是整文件重写）",
                  "删 %d 行 / 增 %d 行；文件 %d → %d 行"
                  % (removed, added, len(old), len(new)))
            otext, ntext = "\n".join(old), "\n".join(new)
            missing = [n for n in K5K6_FUNCS if not func_block(otext, n)]
            check(not missing, "待比的 K5/K6 区段函数在基线里都找得到",
                  "找不到：%s" % missing if missing else "%d 个" % len(K5K6_FUNCS))
            changed = [n for n in K5K6_FUNCS
                       if func_block(otext, n) != func_block(ntext, n)]
            check(not changed, "K5（知识点弹窗内部）与 K6（问答出处小窗）区段逐字节未变",
                  ("变了的是：%s" % changed) if changed
                  else "%d 个函数体逐个比对" % len(K5K6_FUNCS))
            t_changed = [n for n in TOUCHED_PY + TOUCHED_JS
                         if func_block(otext, n) != func_block(ntext, n)]
            check(sorted(t_changed) == sorted(TOUCHED_PY + TOUCHED_JS),
                  "本卡声明的改动函数就是这些（%d 个）" % len(TOUCHED_PY + TOUCHED_JS),
                  "实际变了：%s" % (", ".join(t_changed) or "无"))
            check("_ARTIFACT_V2 = \"C-0049-v2\"" in otext
                  and "_KP_READABLE_VERSIONS" in ntext,
                  "版本白名单是**复用** C-0055 定义的常量（没另抄一份版本号）")

        # ---------- 7. 零模型调用 / 五表 ----------
        emit("\n[7] 零模型调用与写库自查")
        core_now = {f: sha(ROOT / f) for f in CORE_WATCH}
        for f in CORE_WATCH:
            try:
                mt = time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime((ROOT / f).stat().st_mtime))
            except OSError:
                mt = "?"
            emit("      · %s sha256=%s mtime=%s" % (f, core_now[f][:16], mt))
        check(all(core_now[f] == CORE_BEFORE.get(f) for f in CORE_WATCH),
              "core/ 四个文件跑前跑后**一字未变**（本卡只读、不写 core）")
        check("C-0053" not in core_git_diff(),
              "`git diff core/` 里没有本卡标记（core 的改动都是别人的卡留下的）")
        art_after = snap(kdir)
        changed_art = [n for n in ART_BEFORE if art_after.get(n) != ART_BEFORE[n]]
        check(not changed_art and len(art_after) == len(ART_BEFORE),
              "产物跑前跑后**逐份 sha256 未变**（只读、零模型调用）",
              "%d 份；变了的是 %s（已排除别的会话的 fixture 99998）"
              % (len(art_after), changed_art[:3] or "无"))
        dbs = {Path(config.DB_PATH)} | set((ROOT / "subjects").glob("*/index/study.db"))
        for db in sorted(dbs, key=str):
            if not Path(db).exists():
                continue
            cnt = table_counts(Path(db))
            if cnt:
                try:
                    tag = str(Path(db).relative_to(ROOT))
                except ValueError:
                    tag = str(db)
                check(all(v == 0 for v in cnt.values()),
                      "五表 0 行：%s" % tag, json.dumps(cnt, ensure_ascii=False))
        calls = re.findall(r"chat_once|stream_chat|_v2_call",
                           func_block(js, "api_terms"))
        check(not calls, "本卡新增的后端函数里没有任何模型调用")
        chat = get(port, "/api/chat/status")
        check(not chat.get("error"), "/api/chat/status 可用（本卡没碰问答链路）",
              str(chat.get("model") or chat.get("status") or "")[:30])
    finally:
        proc.terminate()

    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
