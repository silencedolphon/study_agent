# -*- coding: utf-8 -*-
"""卡 C-0059 ★真浏览器验证：右上角「设置」入口 + 四分区设置窗口。

流程：
  1. 用**临时端口**起一个看板实例（跑完杀掉；绝不碰 8756），
     并把 `KB_SETTINGS_FILE` 指到卡目录下的临时文件——
     也就是说"在设置窗口里保存"这一步写的是临时文件，**不碰用户真实配置**；
  2. 跑 `tools/probe_c0059_browser.mjs`（真 Edge + CDP）：
     · 断言右上角有「⚙ 设置」入口（给出坐标）；
     · 点开 → 模态窗（URL 不变）；四个分区逐个截图 + 抓字段 DOM；
     · 把「知识点总结」的推理强度改成 high → 保存 → 抓**原始请求体与响应**；
     · 重新读取 `/api/settings` → 断言后端回的当前值就是 high；
     · 密钥框必须是空的（明文不落到前端）；
     · 点开提示词「查看 / 编辑」→ 断言正文就是真源内容；
     · 全程零 JS 报错；
  3. 解析结果并逐条断言，DOM 证据落盘到卡目录。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0059_browser.py
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0059_browser.py --verbose

⚠ 输出约定（别改成"全部往 stdout 打"）：逐条明细**只进日志文件**
（`browser_verify.log`），stdout 固定只有几行——第一行 RUNNING、
最后一行 `RESULT: PASS/FAIL`。理由是自动验收会截断长输出（实测 1000
字符左右就被截掉尾部），明细一多结论行就跟着没了，预检会误判成没通过。
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(r"G:\study_agent")
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0059")
SHOTS = CARD / "shots"
DOMDIR = CARD / "dom"
PROBE_SETTINGS = CARD / "_probe" / "browser_config.local.json"
JS_JSON = CARD / "browser_probe.json"

OK = 0
BAD = 0
LINES: list[str] = []
VERBOSE = False
LOG_PATH = CARD / "browser_verify.log"


def emit(s: str = "", force: bool = False) -> None:
    """逐条明细默认只进日志文件；`force=True`（失败项、关键行）才上 stdout。

    ⚠ stdout 必须保持极短：自动验收会截断长输出，明细一多就把结尾的
    `RESULT: PASS/FAIL` 行一起截掉，预检会误判成没通过。
    """
    LINES.append(s)
    if VERBOSE or force:
        print(s, flush=True)


def check(cond, label: str, detail="") -> bool:
    global OK, BAD
    detail = "" if detail is None else str(detail)
    if cond:
        OK += 1
        emit("  [OK] %s%s" % (label, ("  —— " + detail) if detail else ""))
    else:
        BAD += 1
        emit("  [★FAIL] %s%s" % (label, ("  —— " + detail) if detail else ""),
             force=True)
    return bool(cond)


def get_json(port: int, path: str) -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s" % e.code}
    except Exception as e:                    # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def main() -> int:
    global VERBOSE
    VERBOSE = ("--verbose" in sys.argv[1:]) or ("-v" in sys.argv[1:])
    print("C0059 BROWSER CHECK: RUNNING", flush=True)     # ← stdout 第 1 行
    rc = _main()
    line = ("C0059 BROWSER CHECK RESULT: %s (%d ok / %d bad)"
            % ("PASS" if rc == 0 else "FAIL", OK, BAD))
    # ★ 这一行必须留在 stdout 且靠近开头：自动验收按它判通过与否，
    #   而长输出会被截断（明细已经全部进日志文件，这里只剩结论）。
    print(line, flush=True)
    LOG_PATH.write_text("\n".join(LINES) + "\n" + line + "\n", encoding="utf-8")
    if not VERBOSE:
        print("逐条明细见 %s（加 --verbose 可在终端看全量）" % LOG_PATH,
              flush=True)
    return rc


def _five(port: int) -> dict:
    return get_json(port, "/api/stats")


def _main() -> int:                            # noqa: C901
    SHOTS.mkdir(parents=True, exist_ok=True)
    DOMDIR.mkdir(parents=True, exist_ok=True)
    PROBE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if PROBE_SETTINGS.exists():
        PROBE_SETTINGS.unlink()
    real_settings = ROOT / "config.local.json"
    real_before = sha256_file(real_settings)

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    env = dict(os.environ)
    env["KB_SETTINGS_FILE"] = str(PROBE_SETTINGS)     # ★ 设置只写临时文件
    blog = CARD / "_probe" / "browser_dashboard.log"
    fh = blog.open("wb")
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-u", str(ROOT / "tools" / "dashboard.py"),
         "--port", str(port), "--no-open"],
        cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT, env=env)
    atexit.register(lambda: proc.terminate())

    ok8756 = not get_json(8756, "/api/stats").get("error")
    stats0 = get_json(port, "/api/stats") if False else {}
    for _ in range(80):
        time.sleep(0.4)
        stats0 = get_json(port, "/api/stats")
        if not stats0.get("error"):
            break
    if stats0.get("error"):
        emit("★临时实例 %d 打不通：%s" % (port, stats0["error"]), force=True)
        try:
            emit(fh and blog.read_text(encoding="utf-8", errors="replace")[-1200:],
                 force=True)
        except Exception:                      # noqa: BLE001
            pass
        return 2

    emit("=" * 78)
    emit("卡 C-0059 ★真浏览器验证（headless Edge + CDP，临时端口 %d）" % port)
    emit("  临时实例 PID %s · 常驻 8756 %s（只探活，不碰）"
         % (proc.pid, "可达" if ok8756 else "不可达"))
    emit("  设置文件（本实例）%s" % PROBE_SETTINGS)
    emit("  真实配置文件 %s（本脚本不碰，sha256 %s…）"
         % (real_settings, real_before[:12] or "(不存在)"))
    emit("=" * 78)

    r = subprocess.run(
        ["node", str(ROOT / "tools" / "probe_c0059_browser.mjs"),
         "--port", str(port), "--outdir", str(SHOTS), "--json", str(JS_JSON)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=420)
    if r.returncode != 0:
        emit("  node 退出码 %s\n  stdout: %s\n  stderr: %s"
             % (r.returncode, (r.stdout or "")[-800:], (r.stderr or "")[-1200:]),
             force=True)
    if not JS_JSON.exists():
        emit("★探针没有产出 JSON", force=True)
        proc.terminate()
        return 1
    D = json.loads(JS_JSON.read_text(encoding="utf-8"))
    st = D.get("steps") or {}

    # 0) 临时实例与真实配置
    emit("\n[0] 现场")
    cfg_probe = get_json(port, "/api/settings")
    check(str(cfg_probe.get("settings_file") or "") == str(PROBE_SETTINGS),
          "★ 临时实例把设置写到临时文件（用户真实配置零风险）",
          str(cfg_probe.get("settings_file")))

    # 1) 右上角入口
    emit("\n[1] 断言：右上角出现「设置」入口")
    e = st.get("entry") or {}
    check(bool(e.get("ok")), "顶栏有 #set-btn", str(e.get("text")))
    check("设置" in str(e.get("text")), "按钮文案含「设置」", str(e.get("text")))
    check(int(e.get("x") or 0) > int(e.get("vw") or 0) / 2 and int(e.get("y") or 999) < 120,
          "★ 它确实在**右上角**（视口右侧、顶栏内）",
          "x=%s y=%s 视口宽=%s" % (e.get("x"), e.get("y"), e.get("vw")))
    (DOMDIR / "topbar_entry.html").write_text(
        str(st.get("entryHtml") or ""), encoding="utf-8")

    # 2) 点开是模态窗
    emit("\n[2] 断言：点击弹出设置窗口（模态，不跳页）")
    o = st.get("opened") or {}
    check(bool(o.get("ok")), "点开后 #setmodal 带 on 且内容已渲染")
    check(str(o.get("url") or "").rstrip("/") == D.get("base", "").rstrip("/"),
          "★ 页面 URL 没变（是弹窗不是新页签）", str(o.get("url")))
    sb = str(st.get("secretbar") or "")
    check("只写在本机" in sb and "gitignore" in sb,
          "窗口里写明「密钥只存本机、不会被提交」", sb[:70] + "…")

    # 3) 四个分区
    emit("\n[3] 断言：按四条管线分区，每区字段齐全")
    tabs = st.get("tabs") or []
    check(len(tabs) == 4, "四个分区都在", str([t.get("tab") for t in tabs]))
    want = {"chat": "AI 问答", "kp": "知识点总结",
            "ingest": "入库", "deck": "课件答疑"}
    for t in tabs:
        tid = str(t.get("tab"))
        nav = str(t.get("navLabel") or "")
        check(want.get(tid, "??") in nav,
              "分区「%s」的标签正确" % want.get(tid, tid), nav)
        rows = t.get("rows") or []
        labels = [str(x.get("label") or "") for x in rows]
        joined = " | ".join(labels)
        has_base = any("API Base" in x for x in labels)
        has_key = any(x.get("type") == "password" for x in rows)
        has_shot = any("识图" in x for x in labels)
        has_think = any("推理" in x and "强度" not in x for x in labels)
        effs = [x for x in rows if x.get("options") == ["none", "low", "high", "max"]]
        check(any("模型" in x for x in labels) and has_base and has_key,
              "「%s」有 模型 / API Base / 密钥" % want.get(tid, tid), joined[:80])
        check(has_shot and has_think and bool(effs),
              "「%s」有 识图开关 / 推理开关 / 强度档位 none-low-high-max"
              % want.get(tid, tid),
              "强度档位字段=%s" % [x.get("label") for x in effs])
        check(bool(t.get("promptCards")),
              "「%s」有提示词区块" % want.get(tid, tid),
              str(t.get("promptCards"))[:90])
        if tid == "kp":
            check(len(t.get("promptCards") or []) >= 3,
                  "★ 知识点分区含 3 份可编辑真源（知识点 / 结构 / 名词解释）",
                  str(t.get("promptCards")))

    # DOM 证据
    (DOMDIR / "settings_window.html").write_text(
        str(st.get("headHtml") or "") + "\n<!-- ...省略正文，见 window_dom.txt ... -->\n"
        + str(st.get("windowHtml") or ""), encoding="utf-8")
    (DOMDIR / "settings_panes.json").write_text(
        json.dumps(tabs, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4) 改一个值 → 保存 → 后端认可
    emit("\n[4] 断言：改一个值 → 保存 → 后端真的用新值")
    bs = st.get("beforeSave") or {}
    check(bs.get("value") == "max", "改之前：知识点总结的推理强度是默认 max",
          str(bs.get("value")))
    req = st.get("saveReq") or {}
    body = str(req.get("body") or "")
    check("KP_BUILD_REASONING_EFFORT" in body and "high" in body,
          "★ 页面提交的原始请求体里带上了新值", body[:150])
    resp = str(req.get("resp") or "")
    check('"saved"' in resp and "KP_BUILD_REASONING_EFFORT" in resp,
          "★ 后端回执确认已保存该项")
    check("已保存" in str(st.get("saveMsg") or ""),
          "界面提示保存成功", str(st.get("saveMsg"))[:80])
    rl = st.get("reload") or {}
    check(rl.get("effortValue") == "high",
          "★★ 重新读取：后端回的当前值就是 high（配置真的落盘并被认可）",
          "effort=%r file=%s" % (rl.get("effortValue"), rl.get("file")))
    check(rl.get("restart") is False, "接口声明不需要重启")

    # 5) 密钥脱敏
    emit("\n[5] 断言：密钥脱敏")
    secs = st.get("secretInputs") or []
    check(bool(secs), "当前分区渲染了密钥输入框", "%d 个" % len(secs))
    check(all(not x.get("value") for x in secs),
          "★ 密钥框是空的（明文不落到前端）",
          str([x.get("value") for x in secs]))
    check(all("留空" in str(x.get("placeholder") or "") for x in secs),
          "★ 提示写明「留空＝不改动」",
          str([x.get("placeholder") for x in secs])[:120])
    check(rl.get("secretValue") in ("", None),
          "接口返回的密钥字段 value 为空串", repr(rl.get("secretValue")))

    # 6) 提示词编辑器
    emit("\n[6] 断言：提示词显示")
    pe = st.get("promptEditor") or {}
    check(bool(pe.get("ok")) and int(pe.get("chars") or 0) > 1000,
          "★ 点开「查看 / 编辑」后加载出真源正文",
          "%s 字" % pe.get("chars"))
    check(pe.get("readonly") is False, "可编辑（不是只读展示）")
    kp_slot = next((x for x in (cfg_probe.get("prompts") or []) if x["id"] == "kp"), {})
    check(int(pe.get("chars") or 0) == int(kp_slot.get("chars") or -1),
          "★ 编辑器里的字数 == 后端报的真源字数（就是那一份文件）",
          "%s vs %s" % (pe.get("chars"), kp_slot.get("chars")))
    check(bool(pe.get("hasSysMarker")) and bool(pe.get("hasUserMarker")),
          "★ 正文里含真源的 PROMPT-SYS / PROMPT-USER 代码块标记",
          "head=%s" % str(pe.get("head"))[:50].replace("\n", " "))

    # 7) 关闭 + JS 报错
    emit("\n[7] 断言：关闭与零报错")
    check(bool(st.get("closed")), "点 ✕ 能关掉窗口")
    js = D.get("jsErrors") or []
    ce = D.get("consoleErrors") or []
    check(not js, "★ 真浏览器里零 JS 报错", str(js)[:200])
    check(not ce, "控制台也没有 error 级输出", str(ce)[:200])

    # 8) 副作用
    emit("\n[8] 零副作用")
    check(sha256_file(real_settings) == real_before,
          "★ 用户真实的 config.local.json 未被触碰")
    ok2 = not get_json(8756, "/api/stats").get("error")
    check(ok2 == ok8756, "8756 状态未变（只探活）")
    shots = sorted(x.name for x in SHOTS.glob("*.png"))
    check(len(shots) >= 6, "截图已落盘", str(shots))
    proc.terminate()
    emit("\n  截图目录 %s" % SHOTS)
    emit("  DOM 证据 %s" % DOMDIR)
    emit("=" * 78)
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
