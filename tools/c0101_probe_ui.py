# -*- coding: utf-8 -*-
"""卡 C-0101 真浏览器探针：预习面板**默认只列教材**（切了范围才看得到全部）。

血统同 `tools/c0100_probe_ui.py`（importlib 复用 `tools/c0098_audit_ui.py` 的最小
WebSocket + CDP，无第三方依赖）。**只连自己起的临时端口实例**
（`tools\\c0101_tmp_launch.py`，数据根/配置文件全隔离），真库与 8756 一个字节都不碰。

两种模式：
    --mode data    临时库是**真库的副本** → 验默认 2 份教材、doc#91 的 42 章、
                   切「全部资料」看到 38 份、再切回来
    --mode empty   临时库是**空库** → 验空态引导（不是空白框）

★ 0 次模型调用：全程只读 `/api/preview/options`（只读接口），不点「开始预习」。

用法：
    python tools\\c0101_probe_ui.py --mode data  --port 8793 --cdp-port 9337 --outdir <卡目录>\\screenshots
    python tools\\c0101_probe_ui.py --mode empty --port 8794 --cdp-port 9338 --outdir <卡目录>\\screenshots
输出结尾固定打印 `C0101-UI-PASS` 或 `C0101-UI-FAIL`。
"""
import argparse
import base64
import importlib.util
import io
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))

# 打开面板 → 抓「范围 + 资料下拉 + 正文」三样（列表渲染的全部可见证据）
PANEL_JS = r"""(async () => {
  const wait = ms => new Promise(z => setTimeout(z, ms));
  for (let i = 0; i < 120; i++){
    const b = document.querySelector('#pv-body');
    const sc = document.querySelector('#pv-scope');
    if (b && sc) {
      const sel = document.querySelector('#pv-doc');
      const all = sel ? [...sel.options].map(o => ({
        v: o.value, t: (o.textContent||'').replace(/\s+/g,' ').trim()})) : [];
      // 占位项「— 选一份教材 —」（value=""）不算一份资料，单独报告
      const opts = all.filter(o => o.v !== '');
      const rows = [...document.querySelectorAll('#pv-chwrap tbody tr')];
      return {
        scope: sc.value,
        scopeOpts: [...sc.options].map(o => o.value).join(','),
        n: opts.length,
        hasPh: all.length !== opts.length,
        opts: opts.slice(0, 3),
        last: opts.length ? opts[opts.length-1] : null,
        chRows: rows.length,
        hasChWrap: !!document.querySelector('#pv-chwrap'),
        text: (b.innerText || '').replace(/\s+/g,' ').trim().slice(0, 700)
      };
    }
    await wait(200);
  }
  return {scope: null, n: -1, text: ''};
})()"""


def J(s):
    """把中文安全地塞进 JS 表达式（ensure_ascii 转义，避开所有编码坑）。"""
    return json.dumps(s, ensure_ascii=True)


def load_audit():
    path = os.path.join(HERE, "c0098_audit_ui.py")
    spec = importlib.util.spec_from_file_location("c0098_audit_ui", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=("data", "empty"))
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--cdp-port", type=int, required=True)
    ap.add_argument("--outdir", required=True)
    a = ap.parse_args()

    au = load_audit()
    site = "http://127.0.0.1:%d/" % a.port
    outdir = os.path.abspath(a.outdir)
    os.makedirs(outdir, exist_ok=True)
    prof = os.path.join(os.environ.get("TEMP", "."), "c0101_probe_%s" % a.mode)
    steps = []

    def step(name, ok, note=""):
        steps.append((name, bool(ok), str(note)[:260]))
        print(("ok  " if ok else "NG  ") + name + ((" | " + str(note)) if note else ""),
              flush=True)

    proc, _ver = au.start_edge(prof, a.cdp_port)
    try:
        ws = au.WS(au.page_target(a.cdp_port))
        ws.cmd("Page.enable")
        ws.cmd("Runtime.enable")
        ws.cmd("Emulation.setDeviceMetricsOverride",
               {"width": 1600, "height": 1200, "deviceScaleFactor": 1, "mobile": False})
        ws.cmd("Page.navigate", {"url": site})
        for _ in range(60):
            time.sleep(0.5)
            if ws.eval_js("document.readyState") == "complete":
                break
        time.sleep(3.0)

        def js(expr, wait=False):
            return ws.eval_js(expr, await_promise=wait)

        def shot(name, full=True):
            r = ws.cmd("Page.captureScreenshot",
                       {"format": "png", "captureBeyondViewport": bool(full)})
            p = os.path.join(outdir, name)
            with io.open(p, "wb") as f:
                f.write(base64.b64decode(r["data"]))
            print("    截图 %s（%d KB）" % (name, os.path.getsize(p) // 1024), flush=True)

        # ---------- 0 打开预习面板 ----------
        js("go('kps')", wait=True)
        opened = js("""(() => {
          const b = [...document.querySelectorAll('#view button')]
            .find(x => (x.getAttribute('onclick')||'').includes('openKpsPreview'));
          if (b) b.click();
          return !!b;
        })()""")
        time.sleep(1.0)
        step("0 知识点页点开「📖 预习」弹窗", bool(opened)
             and "on" in str(js("(document.querySelector('#modal')||{}).className || ''")),
             "入口=%s" % opened)

        p1 = js(PANEL_JS, wait=True) or {}
        step("1 面板默认范围 = textbook（仅教材）",
             p1.get("scope") == "textbook" and "textbook" in str(p1.get("scopeOpts")),
             "scope=%s options=%s" % (p1.get("scope"), p1.get("scopeOpts")))

        if a.mode == "data":
            step("1b 每份资料都标了 role 与真实章数",
                 bool(p1.get("opts")) and all(
                     ("教材" in (o.get("t") or "")) and
                     (("章" in (o.get("t") or "")) or ("无章可预习" in (o.get("t") or "")))
                     for o in (p1.get("opts") or [])),
                 "样例=%s" % (p1.get("opts") or [])[:2])
            step("2 默认只列教材：应为 2 份（真库有 38 份资料、36 份是课件）",
                 p1.get("n") == 2 and bool(p1.get("hasPh")) and not any(
                     o.get("v") in ("55", "56", "58") for o in (p1.get("opts") or [])),
                 "份数=%s 占位项=%s 首项=%s 末项=%s" % (p1.get("n"), p1.get("hasPh"),
                                                       (p1.get("opts") or [])[:1],
                                                       p1.get("last")))
            has42 = any("42 章" in (o.get("t") or "") for o in (p1.get("opts") or []))
            step("2b 教材行带章数：doc#91 显示「42 章」", has42,
                 "选项=%s" % [o.get("t") for o in (p1.get("opts") or [])])
            shot("01-default-textbook-only.png")

            # ---------- 3 选 doc#91 → 章列表 ----------
            js("""(() => {
              const s = document.querySelector('#pv-doc');
              s.value = '91'; s.dispatchEvent(new Event('change')); return s.value;
            })()""")
            time.sleep(2.0)
            p2 = js(PANEL_JS, wait=True) or {}
            step("3 选中 doc#91 → 章列表可进入（42 章）",
                 (p2.get("chRows") or 0) == 42 and "第 1 章" in (p2.get("text") or ""),
                 "章行数=%s | %s" % (p2.get("chRows"), (p2.get("text") or "")[:120]))
            shot("02-doc91-42-chapters.png")

            # ---------- 4 切「全部资料」 → 38 份 ----------
            js("""(() => {
              const sc = document.querySelector('#pv-scope');
              sc.value = 'all'; sc.dispatchEvent(new Event('change')); return sc.value;
            })()""")
            time.sleep(2.0)
            p3 = js(PANEL_JS, wait=True) or {}
            cw = [o for o in (p3.get("opts") or []) if "courseware" in (o.get("t") or "")]
            step("4 切「全部资料」→ 38 份（课件如实标成「无章可预习」）",
                 (p3.get("n") or 0) == 38 and p3.get("scope") == "all" and bool(cw),
                 "份数=%s scope=%s 课件样例=%s" % (p3.get("n"), p3.get("scope"),
                                                  (cw or [])[:1]))
            shot("03-scope-all-38.png")

            # ---------- 5 切回「仅教材」 → 2 份，且选中项不丢 ----------
            js("""(() => {
              const sc = document.querySelector('#pv-scope');
              sc.value = 'textbook'; sc.dispatchEvent(new Event('change')); return sc.value;
            })()""")
            time.sleep(2.0)
            p4 = js(PANEL_JS, wait=True) or {}
            sel_now = js("(document.querySelector('#pv-doc')||{}).value || ''")
            step("5 切回「仅教材」→ 回到 2 份，且被选中的那份仍在清单里（不丢选中项）",
                 (p4.get("n") or 0) == 2 and str(sel_now) == "91",
                 "份数=%s 当前选中=%s scope=%s" % (p4.get("n"), sel_now, p4.get("scope")))
            shot("04-back-to-textbook.png")

        else:  # ---------- 空库模式：空态引导 ----------
            t = p1.get("text") or ""
            step("1b 空库默认范围下没有任何可选项",
                 (p1.get("n") or 0) == 0, "份数=%s" % p1.get("n"))
            step("6 空态：说清「没有教材」，不是空白框",
                 ("没有教材" in t) and ("role=textbook" in t),
                 "正文=%s" % t[:200])
            step("6b 空态给出可操作引导：教材入库（专线）+ 去资料页的按钮",
                 ("教材入库（专线）" in t) and ("学科库" in t)
                 and bool(js("""!!document.querySelector('#pv-body button[onclick*="pvGoDocs"]')""")),
                 "按钮=%s" % js("""!!document.querySelector('#pv-body button[onclick*="pvGoDocs"]')"""))
            step("6c 空态下不渲染空白章表框（#pv-chwrap 不存在）",
                 not p1.get("hasChWrap") and (p1.get("n") or 0) == 0,
                 "hasChWrap=%s 份数=%s" % (p1.get("hasChWrap"), p1.get("n")))
            shot("05-empty-no-textbook.png")

            js("""(() => {
              const sc = document.querySelector('#pv-scope');
              sc.value = 'all'; sc.dispatchEvent(new Event('change')); return sc.value;
            })()""")
            time.sleep(2.0)
            p2 = js(PANEL_JS, wait=True) or {}
            t2 = p2.get("text") or ""
            step("7 空库切「全部资料」也有引导（没有资料 + 教材专线）",
                 ("没有资料" in t2) and ("教材入库（专线）" in t2) and not p2.get("hasChWrap"),
                 "正文=%s" % t2[:180])
            shot("06-empty-all-scope.png")

        errs = [e for e in ws.js_errors() if "favicon" not in str(e).lower()]
        step("8 零 JS 错误（favicon 404 噪声不算）", not errs, str(errs)[:200])
    finally:
        try:
            proc.kill()
        except Exception:
            pass

    bad = [s for s in steps if not s[1]]
    print("-" * 60)
    if bad:
        print("C0101-UI-FAIL mode=%s 共 %d/%d 步未过" % (a.mode, len(bad), len(steps)))
        for n, _o, note in bad:
            print("  NG %s | %s" % (n, note))
        return 1
    print("C0101-UI-PASS mode=%s 共 %d 步全过（真浏览器，临时实例 %d）"
          % (a.mode, len(steps), a.port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
