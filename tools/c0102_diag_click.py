# -*- coding: utf-8 -*-
"""卡 C-0102 排查用：导图节点 →「知识点窗口」的正确点击路径。

第一版探针卡在"点节点没出现 #mmkp-body"，本脚本把三种走法分开测，定位到底卡在哪：
  A 直接调 `mmOpenNode(key, el)`            —— 前端函数本身通不通
  B 真实鼠标事件（CDP Input.dispatchMouseEvent）—— 程序化 .click() 之外的走法
  C 如果开出来的是「N 条知识点」选择列表，再点第一条 → 才进总结窗口

只读、只对着**自己的临时实例**跑。

用法：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0102_diag_click.py --port 8795 --cdp-port 9341
"""
import argparse
import importlib.util
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))


def load_audit():
    path = os.path.join(HERE, "c0098_audit_ui.py")
    spec = importlib.util.spec_from_file_location("c0098_audit_ui", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PREP = r"""(async () => {
  const wait = ms => new Promise(z => setTimeout(z, ms));
  await go('kps');
  for (let i = 0; i < 90 && !document.querySelector('#mm-doc'); i++) await wait(200);
  const sel = document.querySelector('#mm-doc');
  sel.value = '103'; sel.dispatchEvent(new Event('change'));
  for (let i = 0; i < 90; i++){
    if (document.querySelector('#mm-tree .mmname')) break;
    await wait(200);
  }
  const k = [...document.querySelectorAll('#mm-tree .mmname')]
    .filter(n => (n.className||'').indexOf('has-kp') >= 0)[0];
  const r = k.getBoundingClientRect();
  const mm = (typeof MINDMAP !== 'undefined') ? MINDMAP : null;
  const nd = (mm.nodes || {})[k.dataset.k] || {};
  return {key: k.dataset.k, nIds: (nd.lkp_ids || []).length, name: nd.name,
          rect: {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)}};
})()"""

POP_STATE = r"""(() => {
  const p = document.querySelector('#evpop');
  return {
    exists: !!p,
    display: p ? getComputedStyle(p).display : null,
    kp: p ? (p.dataset.kp || '') : '',
    hasBody: !!document.querySelector('#mmkp-body'),
    hasTb: !!document.querySelector('#mmkp-tbbox'),
    nOne: document.querySelectorAll('.mmkp-one').length,
    text: p ? (p.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200) : ''
  };
})()"""

CALL_DIRECT = r"""(() => {
  const k = [...document.querySelectorAll('#mm-tree .mmname')]
    .filter(n => (n.className||'').indexOf('has-kp') >= 0)[0];
  mmOpenNode(k.dataset.k, k);
  return {called: true};
})()"""

CALL_CLICK = r"""(() => {
  const k = [...document.querySelectorAll('#mm-tree .mmname')]
    .filter(n => (n.className||'').indexOf('has-kp') >= 0)[0];
  k.click();
  return {clicked: true, hasOnclick: typeof k.onclick};
})()"""

CLICK_ONE = r"""(() => {
  const a = document.querySelector('.mmkp-one');
  if (!a) return {err: 'no .mmkp-one'};
  a.click();
  return {clicked: true, kp: a.dataset.kp, name: a.dataset.name};
})()"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--cdp-port", type=int, required=True)
    a = ap.parse_args()
    au = load_audit()
    prof = os.path.join(os.environ.get("TEMP", "."), "c0102_diag_click")
    proc, _v = au.start_edge(prof, a.cdp_port)
    try:
        ws = au.WS(au.page_target(a.cdp_port))
        ws.cmd("Page.enable"); ws.cmd("Runtime.enable")
        ws.cmd("Emulation.setDeviceMetricsOverride",
               {"width": 1600, "height": 1200, "deviceScaleFactor": 1, "mobile": False})
        ws.cmd("Page.navigate", {"url": "http://127.0.0.1:%d/" % a.port})
        for _ in range(60):
            time.sleep(0.5)
            if ws.eval_js("document.readyState") == "complete":
                break
        time.sleep(2.5)

        def js(e, wait=False):
            return ws.eval_js(e, await_promise=wait)

        info = js(PREP, wait=True)
        print("节点：%s" % json.dumps(info, ensure_ascii=False))

        print("\n[A] 直接调 mmOpenNode：%s" % json.dumps(js(CALL_DIRECT), ensure_ascii=False))
        time.sleep(1.2)
        print("    → %s" % json.dumps(js(POP_STATE), ensure_ascii=False))

        js("evPopClose()")
        time.sleep(0.4)

        print("\n[B] 真实鼠标事件（CDP Input）：")
        x, y = info["rect"]["x"], info["rect"]["y"]
        for t in ("mousePressed", "mouseReleased"):
            ws.cmd("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y,
                                                "button": "left", "clickCount": 1})
        time.sleep(1.2)
        print("    → %s" % json.dumps(js(POP_STATE), ensure_ascii=False))

        print("\n[C] 再点「N 条知识点」里的第一条：%s"
              % json.dumps(js(CLICK_ONE), ensure_ascii=False))
        for i in range(10):
            time.sleep(0.7)
            st = js(POP_STATE)
            print("    +%.1fs %s" % ((i + 1) * 0.7, json.dumps(st, ensure_ascii=False)))
            if st.get("hasBody") and st.get("hasTb"):
                break

        print("\nJS 错误：%s" % json.dumps(ws.js_errors()[:6], ensure_ascii=False)[:1200])
    finally:
        try:
            proc.kill()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
