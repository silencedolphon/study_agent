# -*- coding: utf-8 -*-
"""卡 C-0102 真浏览器探针：知识点窗口里的「要点」框 + 「教材补充」框 + 「打开教材页」。

血统同 `tools/c0101_probe_ui.py`（importlib 复用 `tools/c0098_audit_ui.py` 的最小
WebSocket + CDP，无第三方依赖，headless Edge）。**只连自己起的临时端口实例**
（`tools\\c0102_tmp_launch.py`，数据根/配置文件全隔离），真库与 8756 一个字节都不碰。

两种模式：
    --mode data    临时库 = 真库**副本** + 真产物（有教材 doc#91）→ 验 ok / no_match /
                   no_kp / error 四个分支的**界面文案**、两栏布局、打开教材页按钮
    --mode nothb   同一个副本但 role=textbook 被摘掉 → 验 no_textbook 分支

★ 0 次模型调用：全程只读 `/api/summary-kp`、`/api/kp-textbook`、`/api/teach-page`。

用法：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0102_probe_ui.py \\
        --mode data  --port 8795 --cdp-port 9339 --outdir <卡目录>\\screenshots
输出结尾固定打印 `C0102-UI-PASS` 或 `C0102-UI-FAIL`。
"""
import argparse
import base64
import importlib.util
import io
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))


def load_audit():
    path = os.path.join(HERE, "c0098_audit_ui.py")
    spec = importlib.util.spec_from_file_location("c0098_audit_ui", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 1) 打开知识点页签 → 选中 doc#103 → 点第一个带知识点的节点 ----------
OPEN_STEP1 = r"""(async () => {
  const wait = ms => new Promise(z => setTimeout(z, ms));
  await go('kps');
  for (let i = 0; i < 90 && !document.querySelector('#mm-doc'); i++) await wait(200);
  const sel = document.querySelector('#mm-doc');
  if (!sel) return {err: '没有 #mm-doc（知识点页签没渲染出来）'};
  const opts = [...sel.options].map(o => ({v: o.value, t: (o.textContent||'').trim().slice(0, 36)}));
  const hit = [...sel.options].find(o => o.value === '103');
  if (!hit) return {err: '课件下拉里没有 doc#103', opts: opts.slice(0, 6)};
  sel.value = '103'; sel.dispatchEvent(new Event('change'));
  for (let i = 0; i < 90; i++){
    if (document.querySelector('#mm-tree .mmname')) break;
    await wait(200);
  }
  const nodes = [...document.querySelectorAll('#mm-tree .mmname')];
  const kps = nodes.filter(n => (n.className||'').indexOf('has-kp') >= 0);
  return {nOpts: opts.length, nNodes: nodes.length, nKp: kps.length,
          firstKp: kps.length ? (kps[0].textContent||'').trim().slice(0, 40) : '',
          opts: opts.slice(0, 4)};
})()"""

# ---------- 2) 点节点开窗：挂 MutationObserver 记「要点框先出、教材补充后到」 ----------
# ⚠ 两个坑（都是实测踩出来的，写在这里免得下次再踩）：
#   ① 小窗元素**没有 id**，只有 `className='evpop'`（见 evPopEl）；观察器挂在
#      `document.body` 上最稳，挂在还不存在的 `#evpop` 上会直接抛异常；
#   ② 结构叶节点挂**多条**知识点时（卡 C-0050 的 3 页上限），先出来的是
#      「N 条知识点」选择列表（.mmkp-one），点第一条才进总结窗口。
INSTALL_OBS = r"""(() => {
  window.__tbOpened = [];
  window.open = function(u, t, f){ window.__tbOpened.push([String(u), String(t||'')]); return null; };
  window.__mmLog = [];
  const snap = () => {
    const p = document.querySelector('#mmkp-ptsbox');
    const b = document.querySelector('#mmkp-tbbody');
    const s = document.querySelector('#mmkp-tbstatus');
    window.__mmLog.push({t: Math.round(performance.now()), pts: !!p,
      tb: !!(b && (b.innerText||'').trim().length),
      wait: !!(s && !s.hidden)});
  };
  window.__mmObs = new MutationObserver(snap);
  window.__mmObs.observe(document.body, {childList: true, subtree: true, attributes: true});
  snap();
  return {ok: true};
})()"""

NODE_POS = r"""(() => {
  const k = [...document.querySelectorAll('#mm-tree .mmname')]
    .filter(n => (n.className||'').indexOf('has-kp') >= 0)[0];
  if (!k) return {err: '导图里没有带知识点的节点'};
  const r = k.getBoundingClientRect();
  const mm = (typeof MINDMAP !== 'undefined') ? MINDMAP : null;
  const nd = ((mm && mm.nodes) || {})[k.dataset.k] || {};
  return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
          key: k.dataset.k, nIds: (nd.lkp_ids || []).length, name: nd.name || ''};
})()"""

# 点节点名（`.mmname` 的 onclick 由 bindMindmapTree 用 `el.onclick` 挂上）。
# ⚠ 实测：headless 下用 CDP 合成的鼠标事件（Input.dispatchMouseEvent）**打不动**
#   这个 onclick（节点在折叠的 <details> 里，坐标命中的是 summary 那一层），
#   所以主路径用 DOM 的 `el.click()`；鼠标合成只作后备（见下面的调用处）。
CLICK_NODE = r"""(() => {
  const k = [...document.querySelectorAll('#mm-tree .mmname')]
    .filter(n => (n.className||'').indexOf('has-kp') >= 0)[0];
  if (!k) return {err: '导图里没有带知识点的节点'};
  if (typeof k.onclick !== 'function') return {err: '这个节点没挂 onclick（bindMindmapTree 没跑？）'};
  k.click();
  return {clicked: true};
})()"""

PEEK = r"""(() => ({
  one: document.querySelectorAll('.mmkp-one').length,
  body: !!document.querySelector('#mmkp-body'),
  pop: !!document.querySelector('.evpop')
}))()"""

WAIT_OPEN = r"""(async () => {
  const wait = ms => new Promise(z => setTimeout(z, ms));
  let listed = false;
  for (let i = 0; i < 80; i++){
    if (document.querySelector('.mmkp-one')){ listed = true; break; }
    if (document.querySelector('#mmkp-body')) break;
    await wait(150);
  }
  if (listed){
    const a = document.querySelector('.mmkp-one');
    if (a) a.click();
  }
  for (let i = 0; i < 110; i++){
    if (document.querySelector('#mmkp-body')) break;
    await wait(150);
  }
  for (let i = 0; i < 140; i++){
    const b = document.querySelector('#mmkp-tbbody');
    if (b && (b.innerText||'').trim().length) break;
    await wait(150);
  }
  return {listed: listed, ok: !!document.querySelector('#mmkp-body'),
          kp: (document.querySelector('#mmkp-tbbox') ? (evPopEl().dataset.kp || '') : '')};
})()"""

# ---------- 3) 量布局与内容 ----------
MEASURE = r"""(() => {
  const q = s => document.querySelector(s);
  const R = e => { if (!e) return null; const r = e.getBoundingClientRect();
    return {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width),
            h: Math.round(r.height), rig: Math.round(r.right), bot: Math.round(r.bottom)}; };
  const pts = q('#mmkp-ptsbox'), tb = q('#mmkp-tbbox'), tbb = q('#mmkp-tbbody');
  const txt = e => e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : '';
  return {
    vw: document.documentElement.clientWidth, vh: document.documentElement.clientHeight,
    popRect: R(q('.evpop')), ptsRect: R(pts), tbRect: R(tb),
    colsClass: pts && pts.parentElement ? (pts.parentElement.className || '') : '',
    sameRow: !!(pts && tb && pts.parentElement === tb.parentElement),
    ptsCount: document.querySelectorAll('#mmkp-ptsbox .lkppts li').length,
    ptsText: txt(pts).slice(0, 120),
    tbText: txt(tbb).slice(0, 600), tbHtmlLen: tbb ? tbb.innerHTML.length : 0,
    headText: txt(q('#mmkp-tbbox .lkpsub')),
    nameText: txt(q('#mmkp-tbbox .kptbname')),
    noteText: txt(q('#mmkp-tbbox .kptbnote')),
    warnText: txt(q('#mmkp-tbbox .kptbwarn')),
    footText: txt(q('#mmkp-tbbox .kptbfoot')),
    passCount: document.querySelectorAll('#mmkp-tbbox .kptbpass').length,
    passTextLen: [...document.querySelectorAll('#mmkp-tbbox .kptbpasstext')].map(x => (x.innerText||'').length),
    termsText: txt(q('#mmkp-tbbox .kptbterms')),
    btns: [...document.querySelectorAll('#mmkp-tbbox [data-tbpage]')].map(b => ({
      page: b.dataset.tbpage, disabled: !!b.disabled, text: txt(b), title: b.title })),
    disabled: [...document.querySelectorAll('#mmkp-tbbox button[disabled]')].map(b => ({
      text: txt(b), title: b.title })),
    // ★ 窗口里既有内容一个不能少
    has: {deck: !!q('#mmkp-deck'), prev: !!q('#mmkp-prev'), next: !!q('#mmkp-next'),
          search: !!q('#mmkp-search'), note: !!q('#mmkp-note'), notes: !!q('#mmkp-notes'),
          more: !!q('#mmkp-more'), slide: !!q('#mmkp-slide'), body: !!q('#mmkp-body'),
          src: !!q('.lkpsrc .cid')},
    opened: window.__tbOpened || [],
    log: (window.__mmLog || []).slice(0, 60)
  };
})()"""

CLICK_OPEN = r"""(() => {
  const b = document.querySelector('#mmkp-tbbox button[data-tbpage]:not([disabled])');
  if (!b) return {err: '教材补充框里没有可点的「打开教材页」按钮'};
  b.click();
  return {clicked: true, text: (b.innerText||'').trim(), opened: window.__tbOpened || []};
})()"""

# 直接驱动渲染（no_kp / error 分支）：前端正常不会对不存在的 kp 发这个请求，
# 所以这两个分支用"真接口 + 真渲染函数"驱动，而不是从点节点走到（报告里如实标注）。
DRIVE_NOKP = r"""(async () => {
  evPopEl().dataset.kp = 'KP-不存在-C0102';
  await mmLoadTextbook({doc_id: 103, kp_id: 'KP-不存在-C0102'});
  return {tb: (document.querySelector('#mmkp-tbbody')||{}).innerText || ''};
})()"""
DRIVE_ERROR = r"""(async () => {
  const orig = window.api;
  window.api = async () => { throw new Error('模拟网络失败（探针注入）'); };
  evPopEl().dataset.kp = 'KP-N03-1';
  await mmLoadTextbook({doc_id: 103, kp_id: 'KP-N03-1'});
  window.api = orig;
  return {tb: (document.querySelector('#mmkp-tbbody')||{}).innerText || '',
          html: (document.querySelector('#mmkp-tbbody')||{}).innerHTML || ''};
})()"""
OPEN_NOMATCH = r"""(async () => {
  const wait = ms => new Promise(z => setTimeout(z, ms));
  await mmOpenKp('KP-C0102-NOMATCH', 'Zariski etale cohomology', null, false, 103);
  for (let i = 0; i < 110; i++){
    const b = document.querySelector('#mmkp-tbbody');
    if (b && (b.innerText||'').trim().length) break;
    await wait(150);
  }
  return {tb: (document.querySelector('#mmkp-tbbody')||{}).innerText || '',
          body: !!document.querySelector('#mmkp-body'),
          pts: document.querySelectorAll('#mmkp-ptsbox .lkppts li').length};
})()"""
RESTORE_KP = r"""(async () => {
  await mmOpenKp('KP-N03-1', 'DNA一级结构与构建块', null, false, 103);
  for (let i = 0; i < 140; i++){
    const b = document.querySelector('#mmkp-tbbody');
    if (b && (b.innerText||'').trim().length) break;
    await new Promise(z => setTimeout(z, 150));
  }
  return {ok: !!document.querySelector('#mmkp-body')};
})()"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=("data", "nothb"))
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--cdp-port", type=int, required=True)
    ap.add_argument("--outdir", required=True)
    a = ap.parse_args()

    au = load_audit()
    site = "http://127.0.0.1:%d/" % a.port
    outdir = os.path.abspath(a.outdir)
    os.makedirs(outdir, exist_ok=True)
    prof = os.path.join(os.environ.get("TEMP", "."), "c0102_probe_%s" % a.mode)
    steps = []

    def step(name, ok, note=""):
        steps.append((name, bool(ok), str(note)[:320]))
        print(("ok  " if ok else "NG  ") + name + ((" | " + str(note)) if note else ""),
              flush=True)

    def http(port, path, timeout=90):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")

    proc, _ver = au.start_edge(prof, a.cdp_port)
    try:
        ws = au.WS(au.page_target(a.cdp_port))
        ws.cmd("Page.enable")
        ws.cmd("Runtime.enable")
        ws.cmd("Log.enable")
        ws.cmd("Emulation.setDeviceMetricsOverride",
               {"width": 1600, "height": 1200, "deviceScaleFactor": 1, "mobile": False})
        ws.cmd("Page.navigate", {"url": site})
        for _ in range(60):
            time.sleep(0.5)
            if ws.eval_js("document.readyState") == "complete":
                break
        time.sleep(2.5)

        def js(expr, wait=False):
            return ws.eval_js(expr, await_promise=wait)

        def shot(name, full=True):
            r = ws.cmd("Page.captureScreenshot",
                       {"format": "png", "captureBeyondViewport": bool(full)})
            p = os.path.join(outdir, name)
            with io.open(p, "wb") as f:
                f.write(base64.b64decode(r["data"]))
            print("    截图 %s（%d KB）" % (name, os.path.getsize(p) // 1024), flush=True)

        # ---------- 1 打开知识点窗口 ----------
        s1 = js(OPEN_STEP1, wait=True) or {}
        step("1 知识点页签 → 选中 doc#103 → 导图里有点得开的知识点节点",
             not s1.get("err") and (s1.get("nKp") or 0) > 0,
             "err=%s 节点=%s 带知识点=%s 首个=%s"
             % (s1.get("err"), s1.get("nNodes"), s1.get("nKp"), s1.get("firstKp")))
        pos = js(NODE_POS) or {}
        js(INSTALL_OBS)
        c = js(CLICK_NODE) or {}
        time.sleep(1.6)
        pk = js(PEEK) or {}
        if not pk.get("one") and not pk.get("body") and not c.get("err"):
            # 后备：CDP 合成的真实鼠标事件（主路径没生效时才走）
            ws.cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": pos.get("x", 0),
                                                "y": pos.get("y", 0), "button": "left",
                                                "clickCount": 1})
            ws.cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pos.get("x", 0),
                                                "y": pos.get("y", 0), "button": "left",
                                                "clickCount": 1})
            time.sleep(1.6)
            pk2 = js(PEEK) or {}
            print("    （DOM click 没开窗，鼠标合成后备：%s）" % pk2, flush=True)
        s2 = js(WAIT_OPEN, wait=True) or {}
        step("2 点节点 → 知识点窗口打开（必要时先过「N 条知识点」选择列表）",
             bool(s2.get("ok")) and not s2.get("err"),
             "err=%s ok=%s 经过列表=%s 窗口挂着=%s node=%s"
             % (s2.get("err"), s2.get("ok"), s2.get("listed"), s2.get("kp"), pos.get("key")))
        time.sleep(0.6)
        m = js(MEASURE) or {}
        shot("01-%s-kp-popup.png" % a.mode)

        pts_r, tb_r = m.get("ptsRect"), m.get("tbRect")
        pe = pts_r or {}
        te = tb_r or {}
        # ---------- 3 两栏并排（两种模式都验：布局与教材内容无关） ----------
        side_by_side = (abs(pe.get("y", 0) - te.get("y", 0)) <= 4
                        and te.get("x", 0) >= pe.get("rig", 0) - 2)
        step("3 「要点」框与「教材补充」框**并排**（同一 .kpcols、同一行）",
             bool(pts_r and tb_r) and m.get("sameRow")
             and "kpcols" in str(m.get("colsClass")) and side_by_side,
             "sameRow=%s cols=%s pts=%s tb=%s"
             % (m.get("sameRow"), m.get("colsClass"), pts_r, tb_r))
        pw = (m.get("popRect") or {})
        step("3b 弹窗整体在视口内（含加宽后的 880px 规则）",
             pw.get("x", 0) >= 0 and pw.get("y", 0) >= 0
             and pw.get("rig", 9999) <= m.get("vw", 0)
             and pw.get("bot", 9999) <= m.get("vh", 0) and pw.get("w", 0) > 700,
             "pop=%s 视口=%sx%s" % (pw, m.get("vw"), m.get("vh")))

        # ---------- 4 教材补充框内容 ----------
        if a.mode == "data":
            step("4 教材补充框显示教材标题 + 命中说明（带页码）",
                 bool(m.get("nameText")) and "药理学" in str(m.get("nameText"))
                 and bool(m.get("noteText")) and "命中" in str(m.get("noteText"))
                 and "p." in str(m.get("noteText")),
                 "教材=%s | 说明=%s" % (m.get("nameText"), m.get("noteText")[:110]))
            step("5 教材补充框列出 passages 原文段落（不是标题占位）",
                 (m.get("passCount") or 0) >= 1
                 and all(l > 50 for l in (m.get("passTextLen") or [0])),
                 "段数=%s 各段字数=%s" % (m.get("passCount"), m.get("passTextLen")))
            step("6 明确标注「自动匹配 / 以原文为准」",
                 "自动匹配" in str(m.get("headText")) + str(m.get("footText"))
                 and "以教材原文为准" in str(m.get("footText")),
                 "框头=%s | 脚注=%s" % (m.get("headText"), str(m.get("footText"))[:120]))
            step("7 命中词项可见（terms，且渲染的是词不是 [object Object]）",
                 bool(m.get("termsText")) and "[object Object]" not in str(m.get("termsText"))
                 and len(str(m.get("termsText"))) > 8,
                 str(m.get("termsText"))[:140])

        # ---------- 8 打开教材页按钮 ----------
        if a.mode == "data":
            en = [b for b in (m.get("btns") or []) if not b.get("disabled")]
            step("8 「打开教材页」按钮可用且带页码提示",
                 bool(en) and any("打开教材页 p." in str(b.get("text")) for b in en),
                 "按钮=%s" % (en[:3],))
            c = js(CLICK_OPEN) or {}
            time.sleep(0.5)
            opened = (js("window.__tbOpened || []") or [])
            url = str(opened[0][0]) if opened else ""
            step("9 点按钮 → window.open 收到后端的 page_url（新标签打开该页图）",
                 bool(opened) and url.startswith("/api/teach-page?doc_id=91&page="),
                 "open(%s)" % (opened[:2],))
            if url:
                st, body, ct = http(a.port, url)
                step("9b 该 URL 真的能取到图（HTTP 200 + PNG）",
                     st == 200 and body[:8] == b"\x89PNG\r\n\x1a\n" and len(body) > 20000,
                     "http=%s ct=%s bytes=%d" % (st, ct, len(body)))
            shot("02-%s-open-page-url.png" % a.mode)

        # ---------- 10 既有内容一个不少 ----------
        need = m.get("has") or {}
        step("10 知识点窗口既有内容一个不少（要点/正文/出处/生成链条/看课件/上一·下一/🔍/记笔记）",
             all(need.get(k) for k in ("body", "more", "deck", "prev", "next", "search",
                                       "note", "notes", "slide", "src"))
             and (m.get("ptsCount") or 0) >= 1,
             "has=%s 要点=%s 条" % (need, m.get("ptsCount")))

        # ---------- 11 首屏不阻塞：要点框先出、教材补充后到 ----------
        if a.mode == "data":
            log = m.get("log") or []
            first_pts = next((x["t"] for x in log if x.get("pts")), None)
            first_tb = next((x["t"] for x in log if x.get("tb")), None)
            wait_seen = any(x.get("wait") for x in log)
            step("11 教材补充异步：先出要点框（+「正在查教材…」），教材内容后到",
                 first_pts is not None and first_tb is not None
                 and first_tb >= first_pts and wait_seen,
                 "要点首现=%sms 教材首现=%sms 见到等待态=%s"
                 % (first_pts, first_tb, wait_seen))

        # ---------- 12 窄屏堆叠 ----------
        if a.mode == "data":
            ws.cmd("Emulation.setDeviceMetricsOverride",
                   {"width": 620, "height": 1100, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(1.5)
            m2 = js(MEASURE) or {}
            pe2, te2 = (m2.get("ptsRect") or {}), (m2.get("tbRect") or {})
            step("12 窄屏（620px）两栏**上下堆叠**（不是横向挤压/溢出）",
                 te2.get("y", 0) >= pe2.get("bot", 0) - 4
                 and (m2.get("popRect") or {}).get("rig", 9999) <= m2.get("vw", 0),
                 "pts=%s tb=%s pop=%s 视口=%s"
                 % (pe2, te2, m2.get("popRect"), m2.get("vw")))
            shot("03-%s-narrow-stack.png" % a.mode)
            ws.cmd("Emulation.setDeviceMetricsOverride",
                   {"width": 1600, "height": 1200, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(1.2)

        if a.mode == "data":
            # ---------- 13 no_match 分支（真接口：脱靶知识点） ----------
            nm = js(OPEN_NOMATCH, wait=True) or {}
            time.sleep(0.4)
            m3 = js(MEASURE) or {}
            step("13 no_match 分支：教材里没匹配上 → 有明确文案、按钮置灰并说明原因",
                 "没有出现这条知识点的任何关键词" in str(m3.get("warnText"))
                 and bool(m3.get("disabled"))
                 and all("没匹配上" in str(b.get("title")) for b in (m3.get("disabled") or [])),
                 "文案=%s | 置灰=%s" % (str(m3.get("warnText"))[:90], m3.get("disabled")))
            shot("04-data-no-match.png")

            js(RESTORE_KP, wait=True)
            time.sleep(0.5)

            # ---------- 14 no_kp 分支 ----------
            nk = js(DRIVE_NOKP, wait=True) or {}
            time.sleep(0.3)
            m4 = js(MEASURE) or {}
            step("14 no_kp 分支：没有这条知识点 → 有明确文案、按钮置灰",
                 "没有末级知识点" in str(m4.get("warnText")) and bool(m4.get("disabled")),
                 "文案=%s | 置灰=%s" % (str(m4.get("warnText"))[:90], m4.get("disabled")))
            shot("05-data-no-kp.png")

            # ---------- 15 error 兜底分支（模拟网络失败） ----------
            er = js(DRIVE_ERROR, wait=True) or {}
            time.sleep(0.3)
            m5 = js(MEASURE) or {}
            step("15 error 兜底：请求失败也出文案（不白屏、不当作没救）",
                 "教材补充读取失败" in str(m5.get("warnText")),
                 "文案=%s" % str(m5.get("warnText"))[:100])

            js(RESTORE_KP, wait=True)
            time.sleep(0.4)

        else:
            # ---------- nothb 模式：no_textbook ----------
            m3 = js(MEASURE) or {}
            step("13 no_textbook 分支：本学科库没有教材 → 明确文案 + 置灰 + 引导",
                 "没有教材类资料" in str(m3.get("warnText"))
                 and bool(m3.get("disabled"))
                 and "role=textbook" in str(m3.get("footText")),
                 "文案=%s | 脚注=%s" % (str(m3.get("warnText"))[:100],
                                       str(m3.get("footText"))[:80]))
            step("13b 没有教材时也**不白屏**：要点框照常有内容",
                 (m3.get("ptsCount") or 0) >= 1, "要点=%s 条" % m3.get("ptsCount"))
            shot("06-nothb-no-textbook.png")

        errs = [e for e in ws.js_errors() if "favicon" not in str(e).lower()]
        step("16 零 JS 错误（favicon 404 噪声不算）", not errs, str(errs)[:300])
    finally:
        try:
            proc.kill()
        except Exception:
            pass

    bad = [s for s in steps if not s[1]]
    print("-" * 62)
    if bad:
        print("C0102-UI-FAIL mode=%s 共 %d/%d 步未过" % (a.mode, len(bad), len(steps)))
        for n, _o, note in bad:
            print("  NG %s | %s" % (n, note))
        return 1
    print("C0102-UI-PASS mode=%s 共 %d 步全过（真浏览器 headless Edge，临时实例 %d，0 次模型调用）"
          % (a.mode, len(steps), a.port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
