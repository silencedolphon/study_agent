# -*- coding: utf-8 -*-
"""C-0099「界面改造·卡④：知识点页与知识点窗口清理」的改前/改后量化抓取工具。

血统：C-0096 的 `_work/audit_c0096.py`（同一套最小 WebSocket + CDP 客户端 +
真 headless Edge + 只跑自己起的临时端口实例），C-0091 的 `_work/audit_cdp.py` 是原始版。
本卡按需要加了三样：

  1) **只抓知识点页**，并把 `#view` 按卡片分块统计字数 —— 验收要求「正文 3283 → ≤1200」，
     必须能看清这 3283 字到底由哪几张卡片构成（否则降不下来时不知道原因）。
  2) **知识点窗口探针**：底部 `.pf` 的直接子元素清单（元素一个不少要有据）、
     「在资料库中检索此主题」按钮的实际形状（文字 or 图标 + tooltip）。
  3) **弹窗/菜单探针**：批量整理弹窗、本页「? 本页怎么用」帮助弹窗、导图卡片「⋯ 更多」菜单。
     外加"内部信息扫描"：界面可见文本里还剩不剩「卡 C-xxxx」「产出卡 C-xxxx」「CLI 命令行」。

只读：不写产品代码、不碰 8756，只连自己起的临时端口实例（数据根用副本）。
不依赖任何第三方库。
"""
import argparse
import base64
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# 知识点页主探针：字数（body/view/逐卡片）、按钮、提示块、图例、内部信息扫描
KPS_JS = r"""
(() => {
  const vis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' &&
           s.display !== 'none' && r.bottom > -3000;
  };
  const q = sel => [...document.querySelectorAll(sel)].filter(vis);
  const tx = (e, n) => (e.innerText || e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, n);
  const viewEl = document.querySelector('#view');
  const full = (viewEl ? viewEl.innerText : '') || '';
  const cards = [...document.querySelectorAll('#view .card')].map(c => ({
    head: tx(c.querySelector('h2') || c, 60),
    len: ((c.innerText || '') + '').length
  }));
  const one = sel => { const e = document.querySelector(sel); return e ? e : null; };
  const btnInfo = sel => { const b = one(sel); return b
      ? { t: tx(b, 30), title: (b.title || ''), inView: !!b.closest('#view'),
          inMmBox: !!(b.closest('#mm-box')) } : null; };
  const legendEl = one('#mm-legend');
  const searchRe = /(?:产出)?卡\s*C-\d{4}(?:-v\d+)?|C-\d{4}/g;
  const cliRe = /[\w./\\-]*\b[a-z_]+\.py\b|python\s+[^\s，。；]+/g;
  // ★ 本卡新增的口径：`<select>` 里那几十条 <option> 的文本也计入 innerText，
  //   但浏览器只显示**选中项一行**。验收要的"正文字数"必须两口径都给，
  //   否则 45 份课件的下拉列表会把"提示文字清理"的成果整个盖住。
  const sels = [...document.querySelectorAll('#view select')];
  const selLen = sels.reduce((a, s) => a + ((s.innerText || '').length), 0);
  return {
    page: 'kps',
    bodyLen: document.body.innerText.length,
    viewLen: full.length,
    selectLen: selLen,
    noSelectLen: full.length - selLen,
    bodyNoSelectLen: document.body.innerText.length - selLen,
    selectOpts: sels.map(s => s.options.length),
    cards: cards,
    cardsLen: cards.reduce((a, c) => a + c.len, 0),
    buttons: q('button').map(b => ({
      t: tx(b, 40), title: (b.title || '').slice(0, 160), id: b.id || '',
      cls: (b.className || '').toString().slice(0, 30)
    })),
    inputs: q('input,select,textarea').map(i => ({ tag: i.tagName, id: i.id || '' })),
    hints: q('.muted,.small,.sub,.why,.empty,.pfoot').map(e => tx(e, 320)).filter(Boolean),
    heads: q('h2,h3').map(e => tx(e, 70)),
    legend: legendEl ? (legendEl.innerText || '').trim().replace(/\s+/g, ' ') : '',
    legendShown: !!(legendEl && vis(legendEl)),
    enroll: btnInfo('#mm-enroll'),
    expandIco: btnInfo('#mm-expand'),
    collapseIco: btnInfo('#mm-collapse'),
    moreIco: btnInfo('#mm-more'),
    helpBtn: (() => { const b = q('button').filter(x => tx(x, 4) === '?');
      return { n: b.length, ids: b.map(x => x.id || x.getAttribute('onclick') || '') }; })(),
    pickOpenBtn: btnInfo('#kp-build-open'),
    pickBadge: (() => { const e = one('#kp-build-n');
      return e ? (e.innerText || '').trim() : null; })(),
    cardNos: Array.from(new Set(full.match(searchRe) || [])),
    cli: Array.from(new Set(full.match(cliRe) || [])),
    text: full.slice(0, 9000),
    scrollHeight: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
  };
})()
"""

# 知识点窗口探针：走产品自己的点击路径（展开树 → 点知识点名），不许直接调内部函数
KP_JS = r"""
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const tx = (e, n) => (e.innerText || e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, n);
  const r = { steps: [] };
  const log = (k, v) => r.steps.push({ step: k, ok: !!v, note: String(v).slice(0, 110) });
  try {
    // 先在下拉里挑一份**有 v2 产物**的课件（选项里带"页 / 节点"），否则树是空态、
    // 没有节点可点，知识点窗口就抓不到。
    const sel = document.querySelector('#mm-doc');
    if (sel) {
      const opt = [...sel.options].find(o => /节点/.test(o.text));
      if (opt && sel.value !== opt.value) {
        sel.value = opt.value;
        if (typeof sel.onchange === 'function') sel.onchange({ target: sel });
        log('switch-doc', opt.text.slice(0, 40));
        await sleep(2600);
      }
    }
    document.querySelectorAll('#mm-tree details.mmd').forEach(d => { d.open = true; });
    await sleep(400);
    const names = [...document.querySelectorAll('.mmname')]
      .filter(e => e.getBoundingClientRect().height > 0);
    // ★ 优先点**有总结的末级节点**（mmNodes 给它们加 `has-kp`）：
    //   点到"课件封面/分类节点"那种会走 mmNoKpHtml 另一屏，抓不到知识点的 6 元素底栏。
    const ready = names.filter(e => /has-kp/.test(e.className || ''));
    log('mmname-count', `${names.length}(ready ${ready.length})`);
    if (!names.length) { r.window = null; return r; }
    const pick = ready[0] || names[Math.min(2, names.length - 1)];
    log('picked', pick.textContent.slice(0, 30) + '|' + pick.className);
    pick.click();
    await sleep(2200);
    const pop = document.querySelector('.evpop');
    if (!pop) { log('evpop', 'missing'); r.window = null; return r; }
    log('evpop', 'mounted');
    const pf = [...pop.querySelectorAll('.pf > *')].map(e => ({
      tag: e.tagName, id: e.id || '', cls: (e.className || '').toString().slice(0, 24),
      t: tx(e, 44), title: (e.title || '').slice(0, 120)
    }));
    const sb = pop.querySelector('#mmkp-search');
    r.window = {
      visible: pop.style.display !== 'none',
      textLen: (pop.innerText || '').length,
      pf: pf,
      pfButtons: pf.filter(x => x.tag === 'BUTTON').length,
      searchBtn: sb ? { t: tx(sb, 40), title: sb.title || '' } : null,
      hasPrev: !!pop.querySelector('#mmkp-prev'),
      hasNext: !!pop.querySelector('#mmkp-next'),
      prevDisabled: !!(pop.querySelector('#mmkp-prev') || {}).disabled,
      nextDisabled: !!(pop.querySelector('#mmkp-next') || {}).disabled,
      hasNoteBtn: !!pop.querySelector('#mmkp-note'),
      hasNotesBtn: !!pop.querySelector('#mmkp-notes'),
      hasDeckBtn: !!pop.querySelector('#mmkp-deck'),
      hasDetails: !!pop.querySelector('details.kpmore'),
      noteCount: !!pop.querySelector('#mmkp-notecount'),
      seqMeta: !!pop.querySelector('.pf .pmeta'),
      text: (pop.innerText || '').slice(0, 4000)
    };
    // 关掉小窗（沿用产品自己的关闭路径）
    if (typeof evPopClose === 'function') evPopClose();
    await sleep(200);
    log('closed', (document.querySelector('.evpop') || {}).style ?
        (document.querySelector('.evpop').style.display === 'none') : 'n/a');
  } catch (e) { log('error', e.message); }
  return r;
})()
"""

# 批量整理弹窗（entry: 卡片按钮） + Esc 关闭路径 + 「?」折叠
KPB_JS = r"""
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const tx = (e, n) => (e.innerText || e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, n);
  const r = { steps: [] };
  const log = (k, v) => r.steps.push({ step: k, ok: !!v, note: String(v).slice(0, 110) });
  try {
    const btn = document.querySelector('#kp-build-open');
    log('entry-btn', btn ? tx(btn, 30) : 'missing');
    if (btn) btn.click(); else if (typeof openKpBuild === 'function') openKpBuild();
    await sleep(2000);
    const box = document.querySelector('#modalbox');
    const modalEl = document.querySelector('#modal');
    const open = !!(modalEl && modalEl.classList.contains('on'));
    log('modal-open', open);
    if (box) box.querySelectorAll('details').forEach(d => { d.open = true; });
    await sleep(250);
    r.kpb = {
      open: open,
      textLen: box ? (box.innerText || '').length : 0,
      hasQ: !!(box && box.querySelector('details')),
      qSummary: box && box.querySelector('details summary')
        ? tx(box.querySelector('details summary'), 40) : '',
      qLen: box && box.querySelector('details')
        ? ((box.querySelector('details').innerText || '').length) : 0,
      text: box ? tx(box, 3000) : '',
      cli: box ? Array.from(new Set((box.innerText || '').match(
        /[\w./\\-]*\b[a-z_]+\.py\b|python\s+[^\s，。；]+/g) || [])) : [],
      cardNos: box ? Array.from(new Set((box.innerText || '').match(/C-\d{4}/g) || [])) : []
    };
    // Esc 关闭路径（沿用 #modal 现成的 keydown 监听）
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await sleep(300);
    r.kpbEscClosed = !(document.querySelector('#modal') || {}).classList.contains('on');
    // 遮罩关闭路径：先重开，再点遮罩
    if (typeof openKpBuild === 'function' && r.kpb.open) {
      openKpBuild(); await sleep(1200);
      const m = document.querySelector('#modal');
      if (m) { m.click(); await sleep(200); r.kpbMaskClosed = !m.classList.contains('on'); }
    }
  } catch (e) { log('error', e.message); }
  return r;
})()
"""

# 本页「? 本页怎么用」帮助弹窗（卡④新增）
HELP_JS = r"""
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const tx = (e, n) => (e.innerText || e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, n);
  const r = {};
  try {
    const btn = [...document.querySelectorAll('button')]
      .filter(b => tx(b, 4) === '?')[0] || null;
    r.entryFound = !!btn;
    if (!btn) { r.missing = '页面上的「?」入口按钮没找到'; return r; }
    btn.click();
    await sleep(600);
    const box = document.querySelector('#modalbox');
    const m = document.querySelector('#modal');
    r.open = !!(m && m.classList.contains('on'));
    if (box) box.querySelectorAll('details').forEach(d => { d.open = true; });
    await sleep(250);
    r.textLen = box ? (box.innerText || '').length : 0;
    r.sections = box ? [...box.querySelectorAll('details summary')].map(e => tx(e, 50)) : [];
    r.hasLegend = box ? /◆/.test(box.innerText) && /◇/.test(box.innerText) : false;
    r.text = box ? tx(box, 4000) : '';
    r.cardNos = box ? Array.from(new Set((box.innerText || '').match(/C-\d{4}/g) || [])) : [];
    r.cli = box ? Array.from(new Set((box.innerText || '').match(
      /[\w./\\-]*\b[a-z_]+\.py\b|python\s+[^\s，。；]+/g) || [])) : [];
    if (m) { m.click(); await sleep(200); r.maskClosed = !m.classList.contains('on'); }
  } catch (e) { r.error = e.message; }
  return r;
})()
"""

# 导图卡片「⋯ 更多」菜单（evPop 浮层）
MORE_JS = r"""
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const tx = (e, n) => (e.innerText || e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, n);
  const r = {};
  try {
    const b = document.querySelector('#mm-more');
    r.entryFound = !!b;
    if (!b) { r.missing = '导图卡片上没有「⋯ 更多」入口'; return r; }
    r.entryTitle = b.title || '';
    r.entryText = tx(b, 10);
    b.click();
    await sleep(400);
    const pop = document.querySelector('.evpop');
    r.menuOpen = !!(pop && pop.style.display !== 'none' && (pop.innerText || '').trim());
    r.items = pop ? [...pop.querySelectorAll('.pbody button')].map(x =>
      ({ t: tx(x, 30), title: (x.title || '').slice(0, 130) })) : [];
    r.text = pop ? tx(pop, 900) : '';
    if (typeof evPopClose === 'function') evPopClose();
    await sleep(200);
    r.closed = !(document.querySelector('.evpop') || {}).style ||
      document.querySelector('.evpop').style.display === 'none';
  } catch (e) { r.error = e.message; }
  return r;
})()
"""

# 知识点表格卡片 / 空态（K5、K6、K7 的落点）——只读页面文本，不点任何东西
EMPTY_JS = r"""
(() => {
  const tx = (e, n) => (e.innerText || e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, n);
  const t = document.querySelector('#mm-tree');
  const term = document.querySelector('#term-box');
  return {
    mmTreeHead: t ? tx(t, 400) : null,
    mmTreeLen: t ? (t.innerText || '').length : 0,
    termBox: term ? tx(term, 400) : null,
    termBoxLen: term ? (term.innerText || '').length : 0,
    termCardSub: tx(document.querySelector('#view .card:nth-of-type(4) h2') || document.body, 90)
  };
})()
"""


class WS:
    """最小 WebSocket 客户端（RFC6455 客户端侧，仅够连 CDP 用）。"""

    def __init__(self, url):
        assert url.startswith("ws://"), url
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=60)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        req = ("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key))
        self.sock.sendall(req.encode("latin1"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("握手被关闭")
            buf += chunk
        head, _, self.buf = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError("握手失败")
        self.mid = 0
        self.events = []          # 非应答事件（JS 错误就藏在这里）

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf) + 1024))
            if not chunk:
                raise RuntimeError("连接被关闭")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode("utf-8")
        n = len(data)
        hdr = bytearray([0x81])
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = secrets.token_bytes(4)
        hdr += mask
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        payload = b""
        while True:
            b0, b1 = self._read(2)
            fin, opcode, ln = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            data = self._read(ln) if ln else b""
            if opcode == 0x8:
                raise RuntimeError("对端关闭了 WebSocket")
            if opcode == 0x9:
                continue
            if opcode in (0x0, 0x1, 0x2):
                payload += data
                if fin:
                    return payload

    def cmd(self, method, params=None, timeout=90):
        self.mid += 1
        mid = self.mid
        msg = {"id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(json.dumps(msg))
        deadline = time.time() + timeout
        while time.time() < deadline:
            m = json.loads(self.recv().decode("utf-8"))
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError("%s -> %s" % (method, m["error"]))
                return m.get("result", {})
            if "method" in m:
                self.events.append(m)
        raise TimeoutError("CDP 超时: %s" % method)

    def eval_js(self, expr, await_promise=False, timeout=90):
        res = self.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                            "awaitPromise": await_promise}, timeout=timeout)
        if "exceptionDetails" in res:
            return {"__jsException": str(res["exceptionDetails"])[:400]}
        return res.get("result", {}).get("value")

    def js_errors(self):
        out = []
        for e in self.events:
            m = e.get("method")
            if m == "Runtime.exceptionThrown":
                d = e.get("params", {}).get("exceptionDetails", {})
                out.append({"kind": "exceptionThrown",
                            "text": (d.get("text") or "")[:200],
                            "desc": str((d.get("exception") or {}).get("description") or "")[:300],
                            "line": d.get("lineNumber")})
            elif m == "Runtime.consoleAPICalled" and e.get("params", {}).get("type") == "error":
                args = e["params"].get("args", [])
                out.append({"kind": "console.error",
                            "text": " ".join(str(a.get("value") or a.get("description") or "")[:160]
                                             for a in args)[:300]})
            elif m == "Log.entryAdded":
                p = e.get("params", {}).get("entry", {})
                if p.get("level") == "error":
                    out.append({"kind": "log." + str(p.get("source")),
                                "text": str(p.get("text"))[:200]})
        return out


def clean(o):
    """截断 emoji 会留下孤立代理项，json 写盘会炸——递归洗掉。"""
    if isinstance(o, str):
        return o.encode("utf-8", "ignore").decode("utf-8")
    if isinstance(o, dict):
        return {clean(k): clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def start_edge(profile, cdp_port):
    os.makedirs(profile, exist_ok=True)
    args = [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-background-networking", "--mute-audio",
            "--remote-debugging-port=%d" % cdp_port, "--user-data-dir=" + profile,
            "--window-size=1600,1200", "about:blank"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % cdp_port, timeout=2) as r:
                return proc, json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
    proc.kill()
    raise RuntimeError("Edge 调试端口未起来")


def page_target(cdp_port):
    for _ in range(30):
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % cdp_port, timeout=3) as r:
            for t in json.loads(r.read().decode("utf-8")):
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        time.sleep(0.5)
    raise RuntimeError("找不到 page target")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True, help="看板站点端口（自起的临时实例）")
    ap.add_argument("--cdp-port", type=int, required=True)
    ap.add_argument("--outdir", required=True, help="输出目录（绝对路径）")
    ap.add_argument("--tag", required=True, help="before / after")
    a = ap.parse_args()

    site = "http://127.0.0.1:%d/" % a.port
    outdir = os.path.abspath(a.outdir)
    shots = os.path.join(outdir, "screenshots")
    profile = os.path.join(outdir, "edge-profile")
    os.makedirs(shots, exist_ok=True)

    proc, ver = start_edge(profile, a.cdp_port)
    print("Edge:", ver.get("Browser"), "| site:", site, "| tag:", a.tag)
    try:
        ws = WS(page_target(a.cdp_port))
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
        time.sleep(3.5)

        load_errors = ws.js_errors()
        report = {"site": site, "browser": ver.get("Browser"), "tag": a.tag,
                  "load_errors": load_errors}

        # 知识点页：切页走产品自己的 go()，与真人点标签路径完全一致
        ws.eval_js("go('kps')", await_promise=True)
        time.sleep(3.0)
        kps = ws.eval_js(KPS_JS) or {}
        report["kps"] = kps
        shot = ws.cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        with open(os.path.join(shots, "kps.png"), "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("[kps] 正文 %s 字 | #view %s 字 | 按钮 %d | 提示块 %d | 页高 %s" % (
            kps.get("bodyLen"), kps.get("viewLen"), len(kps.get("buttons", [])),
            len(kps.get("hints", [])), kps.get("scrollHeight")))
        for c in kps.get("cards", []):
            print("     卡片 %4d 字 · %s" % (c.get("len", 0), c.get("head", "")))

        report["empty_state"] = ws.eval_js(EMPTY_JS) or {}

        for name, js in (("kp_window", KP_JS), ("kpb_modal", KPB_JS),
                         ("help_modal", HELP_JS), ("more_menu", MORE_JS)):
            try:
                report[name] = ws.eval_js(js, await_promise=True, timeout=120)
            except Exception as e:
                report[name] = {"error": str(e)[:300]}
            time.sleep(1.0)
        # 弹窗/菜单探针会改页面状态，收尾把页面切回来并抓一张"操作后"的截图
        ws.eval_js("if (typeof closeModal === 'function') closeModal();")
        ws.eval_js("if (typeof evPopClose === 'function') evPopClose();")
        time.sleep(0.6)
        shot = ws.cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        with open(os.path.join(shots, "kps-end.png"), "wb") as f:
            f.write(base64.b64decode(shot["data"]))

        report["js_errors"] = ws.js_errors()

        # ---- 结论行（验收要看的就是这几行）----
        win = report.get("kp_window", {}).get("window") or {}
        kpb = report.get("kpb_modal", {}).get("kpb") or {}
        helpd = report.get("help_modal", {}) or {}
        more = report.get("more_menu", {}) or {}
        lines = [
            "== C0099 %s ==" % a.tag,
            "KPS bodyLen=%s viewLen=%s buttons=%d hints=%d scrollHeight=%s" % (
                kps.get("bodyLen"), kps.get("viewLen"), len(kps.get("buttons", [])),
                len(kps.get("hints", [])), kps.get("scrollHeight")),
            "KPS_NOSELECT bodyLen=%s viewLen=%s (selectOpts=%s selectLen=%s)" % (
                kps.get("bodyNoSelectLen"), kps.get("noSelectLen"),
                kps.get("selectOpts"), kps.get("selectLen")),
            "LEGEND=%r shown=%s ENROLL=%r HELP_BTNS=%s BADGE=%r" % (
                kps.get("legend"), kps.get("legendShown"),
                (kps.get("enroll") or {}).get("t"), (kps.get("helpBtn") or {}).get("n"),
                kps.get("pickBadge")),
            "KPWIN pf_children=%d pf_buttons=%d search=%r searchTitle=%r details=%s prev/next=%s/%s" % (
                len(win.get("pf", [])), win.get("pfButtons", 0),
                (win.get("searchBtn") or {}).get("t"), (win.get("searchBtn") or {}).get("title"),
                win.get("hasDetails"), win.get("hasPrev"), win.get("hasNext")),
            "KPB modal_open=%s has_q=%s q_len=%s esc_closed=%s cli=%s" % (
                kpb.get("open"), kpb.get("hasQ"), kpb.get("qLen"),
                report.get("kpb_modal", {}).get("kpbEscClosed"), kpb.get("cli")),
            "HELP open=%s sections=%s legend=%s" % (
                helpd.get("open"), helpd.get("sections"), helpd.get("hasLegend")),
            "MORE entry=%s items=%s" % (more.get("entryFound"),
                                        [x.get("t") for x in (more.get("items") or [])]),
            "CARDNOS=%s CLI=%s JSERR=%d" % (
                kps.get("cardNos"), kps.get("cli"), len(report.get("js_errors", []))),
        ]
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "audit-report.json"), "w", encoding="utf-8") as f:
            json.dump(clean(report), f, ensure_ascii=False, indent=1)
        with open(os.path.join(outdir, "summary.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("\n".join(lines))
        for e in report["js_errors"][:10]:
            print("   ! %s %s" % (e.get("kind"), e.get("text", "")[:140]))
        print("报告 ->", os.path.join(outdir, "audit-report.json"))
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
