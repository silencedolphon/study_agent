# -*- coding: utf-8 -*-
"""C-0100 验收脚本：预习面板（前端）+ 一条只读预估接口。

    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\verify_c0100.py

**绝对路径、不依赖 cwd、不调模型（0 花费）**：全部判据要么读源码/读文件，要么在
**隔离临时库**（`_tmp_c0100\\data`，真库的副本）上跑一次 dry-run 预估。
结论行（`C0100-PASS` / `C0100-FAIL`）在**第一行**。

判据四组：
  A 源码：预习面板函数族齐备、入口按钮唯一真源、空态入口只增不删、卡④的「?」没被复制、
    进度条复用 `renderProgress`、后端只读预估接口与路由（锁前）；
  B 与基线（C-0099 完工版）逐行对照：**只有纯插入、0 删除、4 个 hunk**——
    一行都没删，就等于"已验收四张卡的区段/token 一个字节没碰"；
  C 换行符：CRLF 保持、裸 LF = 0；
  D 接口真跑（子进程 + 隔离数据根）：`api_preview_estimate(91, 2)` 回 1 次调用、
    成本上界 > 0、产物名带 `-预习总结.json`（**0 次模型调用**）。
"""
from __future__ import annotations

import difflib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"G:\study_agent")
DASH = ROOT / "tools" / "dashboard.py"
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0100")
BASE = CARD / "_work" / "dashboard.py.base"
TMPDATA = ROOT / "_tmp_c0100" / "data"
PY = Path(r"G:\study_agent\venv\Scripts\python.exe")

RES: list[tuple[bool, str, str]] = []


def ok(cond, name: str, note: str = "") -> bool:
    RES.append((bool(cond), name, str(note)[:220]))
    return bool(cond)


def count(text: str, needle: str) -> int:
    return text.count(needle)


# ---------------- D：子进程里跑隔离的 dry-run ----------------
D_SCRIPT = r'''
import json, os, sys, importlib.util
from pathlib import Path
ROOT = Path(r"G:\study_agent"); TMP = Path(r"G:\study_agent\_tmp_c0100\data")
os.environ["KB_RAW"] = str(TMP / "raw")
os.environ["KB_NOTES"] = str(TMP / "notes")
os.environ["KB_INDEX"] = str(TMP / "index")
os.environ["KB_DB"] = str(TMP / "index" / "study.db")
os.environ["KB_LOG"] = str(TMP / "logs")
os.environ["KB_SETTINGS_FILE"] = str(TMP / "config.local.json")
sys.path.insert(0, str(ROOT))
import core.config as cfg
import core.subjects as subjects_mod
cfg.ROOT = TMP
subjects_mod.registry_path = lambda: TMP / "logs" / "subjects.json"
subjects_mod.subjects_root = lambda: TMP / "subjects"
spec = importlib.util.spec_from_file_location("dash_c0100", str(ROOT / "tools" / "dashboard.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
out = {"db": str(cfg.DB_PATH), "log_dir": str(cfg.LOG_DIR), "isolated": str(cfg.DB_PATH).startswith(str(TMP))}
try:
    est = m.api_preview_estimate(91, 2)
    out["est"] = {k: est.get(k) for k in ("ok", "doc_id", "chapter_no", "chapter_name",
                                          "est_calls", "est_cost_cny", "out_path",
                                          "n_pages", "n_sections", "skip_would_happen")}
except Exception as e:
    out["error"] = "%s: %s" % (type(e).__name__, e)
print("C0100JSON" + json.dumps(out, ensure_ascii=False))
'''


def run_dry_run() -> dict:
    r = subprocess.run([str(PY), "-X", "utf8", "-c", D_SCRIPT],
                       capture_output=True, text=True, encoding="utf-8", timeout=90)
    for line in (r.stdout or "").splitlines():
        if line.startswith("C0100JSON"):
            return json.loads(line[len("C0100JSON"):])
    return {"error": "子进程没打出结果", "stdout": (r.stdout or "")[-400:],
            "stderr": (r.stderr or "")[-400:]}


def main() -> int:
    t0 = time.perf_counter()
    ok(DASH.exists(), "A0 tools/dashboard.py 存在", str(DASH))
    raw = DASH.read_bytes().decode("utf-8")
    src = raw

    # ---------------- A 源码 ----------------
    fam = ("openKpsPreview", "pvLoad", "pvRender", "pvEstRender", "pvPickCh", "pvStart",
           "pvShowFail", "pvPaintProgress", "pvWatch", "pvFinish", "pvOut", "pvOpenSettings")
    miss = [f for f in fam if count(src, "function %s(" % f) != 1]
    ok(not miss, "A1 预习面板函数族各自只定义一次（12 个）", "缺/重复：%s" % miss)
    ok(count(src, 'onclick="openKpsPreview()"') == 1
       and count(src, "function kpPreviewBtn()") == 1,
       "A2 入口按钮唯一真源（kpPreviewBtn 里挂一次 openKpsPreview）",
       "onclick=%d def=%d" % (count(src, 'onclick="openKpsPreview()"'),
                              count(src, "function kpPreviewBtn()")))
    ok(count(src, "${kpPreviewRow()}${kpBuildCard()}") == 1,
       "A3 空态也只增一个入口行（拼接顺序不变）")
    ok(count(src, 'onclick="openKpsHelp()"') == 2,
       "A4 卡④的「?」按钮仍只有两处（viewKps + kpBuildCard），没被复制",
       "count=%d" % count(src, 'onclick="openKpsHelp()"'))
    ok(count(src, "renderProgress(p, '#pv-progress')") == 1
       and count(src, "function pvBar(") == 0,
       "A5 进度条复用现成的 renderProgress（没新造进度条）")
    ok(count(src, 'if path == "/api/preview/estimate":') == 1
       and count(src, "def api_preview_estimate(") == 1,
       "A6 后端新增一条只读预估接口")
    i_route = src.find('if path == "/api/preview/estimate":')
    i_lock = src.find("            with _lock:")
    ok(i_route > 0 and i_lock > 0 and i_route < i_lock,
       "A7 该路由排在 `_lock` 之前（入库占锁时也算得出来）", "route@%d lock@%d" % (i_route, i_lock))
    ok("def api_preview_options(" in src and "def api_preview_start(" in src
       and "def api_preview_progress(" in src,
       "A8 卡 C-0094 的三个后端入口一字未动")

    # ---------------- B 与基线逐行对照 ----------------
    # 基线是**卡 C-0099 完工版**（`_work\\dashboard.py.base`）。判据：
    #   ① 基线里 13552 行，除了**唯一一处"只增"改动**外一行都没被删；
    #   ② 那一处就是空态拼接的第一行——只在 `${kpBuildCard()}` 前面加了
    #      `${kpPreviewRow()}`，三个卡片的先后顺序一个字没变（卡面允许"只增入口按钮"）。
    ALLOW_OLD = "    $('#view').innerHTML = `${kpBuildCard()}"
    ALLOW_NEW = "    $('#view').innerHTML = `${kpPreviewRow()}${kpBuildCard()}"
    if BASE.exists() and BASE.read_bytes().decode("utf-8").count("\n") > 0:
        base = BASE.read_bytes().decode("utf-8")
        old = base.splitlines()
        new = raw.splitlines()
        sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
        dels: list[str] = []
        adds = blocks = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("delete", "replace"):
                dels.extend(old[i1:i2])
            if tag in ("insert", "replace"):
                adds += (j2 - j1)
                blocks += 1
        bad_del = [ln for ln in dels if ln != ALLOW_OLD]
        ok(not bad_del and len(dels) <= 1 and ALLOW_NEW in src
           and adds == len(new) - len(old) + len(dels) and 3 <= blocks <= 6,
           "B1 与 C-0099 基线对照：除「空态拼接行只增一个入口」外，基线一行未删",
           "被改/删行=%d（允许的那行命中=%s）新增=%d 行/新增块=%d 行数 %d→%d"
           % (len(dels), not bad_del, adds, blocks, len(old), len(new)))
    else:
        ok(False, "B1 与 C-0099 基线对照", "找不到基线副本 %s" % BASE)

    # ---------------- C 换行符 ----------------
    crlf = raw.count("\r\n")
    bare = raw.count("\n") - crlf
    ok(crlf > 0 and bare == 0, "C1 CRLF 保持、裸 LF = 0", "CRLF=%d bareLF=%d" % (crlf, bare))

    # ---------------- D 隔离库上真跑一次预估 ----------------
    d = run_dry_run()
    est = d.get("est") or {}
    ok(bool(d.get("isolated")), "D0 子进程用的是隔离临时库（真库不碰）",
       "db=%s" % d.get("db"))
    ok(est.get("ok") is True and est.get("est_calls") == 1
       and float(est.get("est_cost_cny") or 0) > 0
       and str(est.get("out_path") or "").endswith("-预习总结.json"),
       "D1 只读预估接口可用：1 次调用 + 成本上界 > 0 + 产物名正确",
       "calls=%s cost=%s path=%s" % (est.get("est_calls"), est.get("est_cost_cny"),
                                     str(est.get("out_path") or "")[-28:]))
    ok(est.get("chapter_no") == 2 and (est.get("n_pages") or 0) > 0,
       "D2 章号/页数按所选章返回", "第%s章 %s 页 %s 节"
       % (est.get("chapter_no"), est.get("n_pages"), est.get("n_sections")))

    bad = [r for r in RES if not r[0]]
    head = "C0100-PASS" if not bad else "C0100-FAIL"
    print("%s 预习面板（前端）+ 只读预估接口：%d/%d 项通过（%.1fs）"
          % (head, len(RES) - len(bad), len(RES), time.perf_counter() - t0))
    for good, name, note in RES:
        print(("  ok  " if good else "  NG  ") + name + ((" | " + note) if note else ""))
    if bad:
        print("\n★ 未过的项：")
        for _, name, note in bad:
            print("   - %s | %s" % (name, note))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
