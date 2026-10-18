# -*- coding: utf-8 -*-
"""卡 C-0055 快速验证（**不发浏览器**，约 10 秒；完整真浏览器验证见
`tools/verify_c0055_browser.py`，那个要跑 5 分钟左右）。

它证明三件事：
  1. 后端新接口 `GET /api/page-kps` 真的在跑，且"该页的知识点"与产物
     `meta.coverage.page_owner` **逐页一致**；四种页码形态（`p.N` / `p.N 图` /
     `p.N-M` 跨页 / 认不出）都不报错并给可理解提示；无图时 `ok=false`；
  2. `/img/slide` 对同一页返回真的 PNG（HTTP 200 + image/png + 有字节）；
  3. 前端那几样东西真的在给浏览器的那份 HTML 里（`evCiteExtras` / `#evpop-extra` /
     `.evslideimg`）——否则就是"后端改了、前端没上"。

用法（自起临时端口实例，跑完杀掉；**不碰 8756**）：
    venv\\Scripts\\python.exe tools\\verify_c0055_quick.py
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config, subjects  # noqa: E402

# ★ 输出一律 UTF-8（**不依赖调用方加 `-X utf8`**）。
#
# 为什么必须写死：Windows 上 Python 默认按 locale（cp936）往管道写字节，而预检
# / CI 捕获 stdout 后是按 **UTF-8** 解码的——于是中文断言行全变成 `���`，
# 判据里的中文字串永远匹配不上（卡 C-0055 预检第一次就是这么"未通过"的，
# 退出码 0、功能全对，只是行没了）。这里 reconfigure 一次就与 `-X utf8` 等效。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001  （老解释器 / 被重定向成非 TextIOWrapper）
        pass

DASH = ROOT / "tools" / "dashboard.py"
DOC = 9           # 有机化学库 doc#9：74 页、74 张页图、v2 产物齐全
NOGRAPH_DOC = 4   # 只有 19 张图 → p.99 一定没有图缓存
OK = 0
BAD = 0


def emit(s: str = "") -> None:
    print(s)


def check(cond, label: str, detail: str = "") -> bool:
    global OK, BAD
    if cond:
        OK += 1
        emit("  [OK] %s%s" % (label, ("  —— " + detail) if detail else ""))
    else:
        BAD += 1
        emit("  [★FAIL] %s%s" % (label, ("  —— " + detail) if detail else ""))
    return bool(cond)


def get(port: int, path: str, raw: bool = False):
    url = "http://127.0.0.1:%d%s" % (port, path)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            body = r.read()
            return (r.status, body, r.headers.get("content-type", "")) if raw \
                else json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s" % e.code} if not raw else (e.code, b"", "")
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)} if not raw else (0, b"", "")


def main() -> int:
    PY = ROOT / "venv" / "Scripts" / "python.exe"
    exe = str(PY) if PY.exists() else sys.executable
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    blog = ROOT / "logs" / "_c0055_quick.out.log"
    blog.parent.mkdir(parents=True, exist_ok=True)
    with blog.open("wb") as fh:
        proc = subprocess.Popen(
            [exe, "-X", "utf8", "-u", str(DASH), "--port", str(port), "--no-open"],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            time.sleep(0.4)
            if not get(port, "/api/stats").get("error"):
                break
        emit("卡 C-0055 快速验证（临时端口 %d，PID %s）" % (port, proc.pid))
        h = get(port, "/api/stats")
        if h.get("error"):
            emit("★临时实例打不通：%s" % h["error"])
            return 2
        subjects.restore()

        # ---- 1. 后端接口 ----
        d = get(port, "/api/page-kps?doc_id=%d&source_ref=p.11" % DOC)
        check(not d.get("error") and d.get("pages") == [11] and d.get("ref_kind") == "page",
              "GET /api/page-kps 可用（p.11 解析成 [11]）", json.dumps(
                  {k: d.get(k) for k in ("pages", "ref_kind", "kp_state")},
                  ensure_ascii=False))
        kdir = Path(config.LOG_DIR) / "knowledge"
        art = json.loads((kdir / ("%d-末级知识点总结.json" % DOC)).read_text(encoding="utf-8"))
        owner = ((art.get("meta") or {}).get("coverage") or {}).get("page_owner") or {}
        want = str(owner.get("11") or "")
        got = (d.get("kps") or [{}])[0].get("kp_id", "")
        check(bool(want) and got == want,
              "该页知识点 == 产物 meta.coverage.page_owner['11']", "%s == %s" % (got, want))
        check(d.get("slide", {}).get("ok") is True and d.get("slide", {}).get("deck"),
              "该页有课件图缓存", str(d.get("slide", {}).get("deck"))[:24])

        st, body, ctype = get(port, "/img/slide?doc_id=%d&page=11" % DOC, raw=True)
        check(st == 200 and ctype.startswith("image/png") and len(body) > 10000,
              "GET /img/slide 同一页返回真 PNG", "%s %s %d bytes" % (st, ctype, len(body)))

        f = get(port, "/api/page-kps?doc_id=%d&source_ref=%s"
                % (DOC, urllib.parse.quote("p.2 图")))
        check(f.get("ref_kind") == "figure" and f.get("pages") == [2],
              "`p.N 图`（识图块）解析出页号", json.dumps(
                  {k: f.get(k) for k in ("ref_kind", "pages")}, ensure_ascii=False))
        sp = get(port, "/api/page-kps?doc_id=%d&source_ref=p.1-2" % DOC)
        check(sp.get("ref_kind") == "span" and sp.get("pages") == [1, 2],
              "`p.N-M` 跨页展开成整排页", str(sp.get("pages")))
        bad = get(port, "/api/page-kps?doc_id=%d&source_ref=%s"
                  % (DOC, urllib.parse.quote("封面")))
        check(not bad.get("error") and not bad.get("pages") and "没有认得出的页码"
              in (bad.get("ref_note") or ""),
              "认不出页码：不报错、给一句人话", (bad.get("ref_note") or "")[:40])
        ng = get(port, "/api/page-kps?doc_id=%d&source_ref=p.99" % NOGRAPH_DOC)
        check(ng.get("slide", {}).get("ok") is False
              and (ng.get("slide", {}).get("slides") or [{}])[0].get("ok") is False,
              "无图页：ok=false（前端据此给空态）", "doc#%d p.99" % NOGRAPH_DOC)

        # ---- 2. 前端那份 HTML 里真的有本卡的东西 ----
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=30) as r:
            html = r.read().decode("utf-8")
        for token in ("evCiteExtras", "evCiteExtrasHtml", "evCiteSlide",
                      'id="evpop-extra"', ".evslideimg", "evpop-pages",
                      "卡 C-0055"):
            check(token in html, "前端含 %s" % token)
        q = chr(34); lt = chr(60); gt = chr(62)
        frag = 'evpop-extra' + q + gt + lt + '/div' + gt + lt + 'pre' + gt
        check(frag in html, "『这一页』区块排在块原文**之前**（第一眼就看到图与知识点）")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
    emit("结果：%d 项通过，%d 项失败" % (OK, BAD))
    # ★ 纯 ASCII 判定行：给预检/CI 做 expectOutput 匹配用。
    #   数字是**实际跑出来的**（不是写死的）——环境再换编码也匹配得上，
    #   判据强度不变：仍然要求 16 项全过、0 项失败。
    emit("C0055-QUICK RESULT: ok=%d fail=%d" % (OK, BAD))
    emit("（完整真浏览器验证：venv\\Scripts\\python.exe tools\\verify_c0055_browser.py，"
         "约 5 分钟；报告见卡目录 verify_report.txt 与 shots/*.png）")
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
