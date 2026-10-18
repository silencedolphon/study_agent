# -*- coding: utf-8 -*-
"""C-0098③ 取证截图：资料页改后全貌 + 行内「⋯」菜单 + 批量入库弹窗（真浏览器）。

血统同 `tools/c0098_audit_ui.py`（最小 WebSocket + CDP，无第三方依赖）。
只连自己起的临时端口实例；只点不改数据（不点「开始批量入库」，不发任何写请求）。

用法：
  python c0098_shots.py --port 8798 --cdp-port 9336 --outdir <卡目录绝对路径>\\screenshots
输出结尾固定打印 `C0098-SHOTS-PASS` 或 `C0098-SHOTS-FAIL`。
"""
import argparse
import base64
import importlib.util
import io
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))

SHOT_JS = r"""
(async () => {
  const wait = ms => new Promise(z => setTimeout(z, ms));
  const out = { steps: [] };
  const log = (k, ok, note) => out.steps.push({step: k, ok: !!ok, note: String(note || '').slice(0, 120)});
  go('docs');
  for (let i = 0; i < 60 && document.querySelectorAll('#view button').length < 5; i++) await wait(250);
  await wait(600);
  const view = document.querySelector('#view');
  const txt = el => ((el && el.innerText) || '').replace(/\s+/g, ' ').trim();
  const dots = [...view.querySelectorAll('tbody tr button')].filter(b => txt(b).indexOf('⋯') >= 0);
  out.n_dots = dots.length;
  log('行内 ⋯ 数量', dots.length > 0, dots.length);
  const docDot = dots.find(b => (b.getAttribute('onclick') || '').indexOf('docsDocRowMenu') >= 0);
  out.has_docdot = !!docDot;
  return out;
})()
"""


def load_audit():
    path = os.path.join(HERE, "c0098_audit_ui.py")
    spec = importlib.util.spec_from_file_location("c0098_audit_ui", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--cdp-port", type=int, required=True)
    ap.add_argument("--outdir", required=True)
    a = ap.parse_args()

    au = load_audit()
    site = "http://127.0.0.1:%d/" % a.port
    outdir = os.path.abspath(a.outdir)
    os.makedirs(outdir, exist_ok=True)
    prof = os.path.join(os.environ.get("TEMP", "."), "c0098_shots_profile")
    steps = []

    def shot(ws, name, full=False):
        r = ws.cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": bool(full)})
        p = os.path.join(outdir, name)
        with io.open(p, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        steps.append(("截图 " + name, True, "%d KB" % (os.path.getsize(p) // 1024)))

    proc, ver = au.start_edge(prof, a.cdp_port)
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
        res = ws.eval_js(SHOT_JS, await_promise=True) or {}
        steps.extend((s["step"], s["ok"], s.get("note", "")) for s in res.get("steps", []))
        shot(ws, "docs-after-full.png", full=True)

        # 行内「⋯」菜单
        ws.eval_js("""(() => {
          const view = document.querySelector('#view');
          const txt = el => ((el && el.innerText) || '').trim();
          const d = [...view.querySelectorAll('tbody tr button')]
            .find(b => txt(b).indexOf('⋯') >= 0 &&
                       (b.getAttribute('onclick')||'').indexOf('docsDocRowMenu') >= 0);
          if (d) d.click();
          return !!d;
        })()""")
        time.sleep(0.6)
        shot(ws, "row-menu-after.png")

        # 批量入库弹窗
        ws.eval_js("""(() => {
          document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
          const view = document.querySelector('#view');
          const b = [...view.querySelectorAll('button')]
            .find(x => (x.innerText||'').indexOf('批量入库') >= 0);
          if (b) b.click();
          return !!b;
        })()""")
        time.sleep(0.8)
        steps.append(("批量入库弹窗已打开", bool(ws.eval_js(
            "(document.querySelector('#modal')||{}).className") == "modal on"),
            str(ws.eval_js("(document.querySelector('#modal')||{}).className"))))
        shot(ws, "batch-modal-after.png")
        steps.append(("JS 错误", not ws.js_errors(), str(len(ws.js_errors())) + " 条(含 favicon 404 噪声)"))
    finally:
        proc.kill()

    print("站点 %s | %s | 输出 %s" % (site, ver.get("Browser"), outdir))
    for k, ok, note in steps:
        print(("ok " if ok else "NG ") + k + ((" | " + str(note)) if note else ""))
    bad = [s for s in steps if not s[1]]
    print("C0098-SHOTS-%s" % ("PASS" if not bad else "FAIL"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
