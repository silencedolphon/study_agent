# -*- coding: utf-8 -*-
"""C-0096 界面 token 卡的改前/改后量化抓取工具。

血统：复制自 C-0091 的 `_work/audit_cdp.py`（同一套最小 WebSocket + CDP 客户端），
按本卡需要加了三样东西：
  1) 全参数化（站点端口 / CDP 端口 / 输出目录），改前改后各跑一次同一份代码；
  2) 收集 JS 错误（`Runtime.exceptionThrown` + console error）——"零 JS 错误"要有据；
  3) 采样真实计算样式的**对比度**（token 落地后要给出实测比值）。

只读：不写产品代码、不碰 8756，只连自己起的临时端口实例。
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
PAGES = ["overview", "assistant", "docs", "kps", "notes", "mistakes", "review", "graph"]

PROBE_JS = r"""
(() => {
  const vis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' &&
           s.display !== 'none' && r.bottom > -2000;
  };
  const q = sel => [...document.querySelectorAll(sel)].filter(vis);
  const tx = (e, n) => (e.innerText || e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, n);
  return {
    page: %PAGE%,
    buttons: q('button').map(b => ({
      t: tx(b, 50), title: (b.title || '').slice(0, 120), id: b.id || '',
      cls: (b.className || '').toString().slice(0, 40),
      oc: (b.getAttribute('onclick') || '').slice(0, 90)
    })),
    inputs: q('input,select,textarea').map(i => ({
      tag: i.tagName, type: i.type || '', ph: i.placeholder || '',
      id: i.id || '', title: (i.title || '').slice(0, 80)
    })),
    hints: q('.muted,.small,.sub,.why,.empty,.pfoot,.frbody,.fr li,.setnote').map(e => tx(e, 260)).filter(Boolean),
    heads: q('h2,h3').map(e => tx(e, 60)),
    drops: q('[id^=dz],[class*=drop]').map(e => e.id || e.className),
    tabs: q('nav#nav button').map(b => ({ t: tx(b, 20), on: b.className.includes('on') })),
    bodyLen: document.body.innerText.length,
    viewLen: (document.querySelector('#view') || {}).innerText?.length || 0
  };
})()
"""

# token 实际取值 + 对比度实测（WCAG 2.x 相对亮度公式）
STYLE_JS = r"""
(() => {
  const rgb = s => {
    if (!s) return null;
    const m = s.match(/[\d.]+/g);
    if (!m) return null;
    const a = m.length > 3 ? Number(m[3]) : 1;
    return [Number(m[0]), Number(m[1]), Number(m[2]), a];
  };
  const lum = c => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b), hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  };
  // 自底向上把半透明背景合成出一个实色底
  const bgOf = el => {
    const stack = [];
    let n = el;
    while (n && n.nodeType === 1) {
      const c = rgb(getComputedStyle(n).backgroundColor);
      if (c && c[3] > 0) stack.push(c);
      n = n.parentElement;
    }
    let base = [0, 0, 0];
    for (let i = stack.length - 1; i >= 0; i--) {
      const c = stack[i], a = c[3];
      base = [c[0] * a + base[0] * (1 - a), c[1] * a + base[1] * (1 - a), c[2] * a + base[2] * (1 - a)];
    }
    return base.map(Math.round);
  };
  const pick = sel => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const cs = getComputedStyle(el), fg = rgb(cs.color), bg = bgOf(el);
    if (!fg) return null;
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    return { sel, color: cs.color, bg: 'rgb(' + bg.join(',') + ')',
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
        self.events = []          # ★ 收下所有非应答事件（JS 错误就藏在这里）

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

    def eval_js(self, expr, await_promise=False):
        res = self.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": await_promise})
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
                            "url": (d.get("url") or "")[:120],
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
                                "text": str(p.get("text"))[:200], "url": str(p.get("url"))[:120]})
        return out


def clean(o):
    """截断 emoji 会留下孤立代理项（\\ud83d），json 写盘会炸——递归洗掉。"""
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
    ap.add_argument("--port", type=int, required=True, help="看板站点端口（自起临时实例）")
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

    # 页面加载阶段的错误先清一次（只统计"操作期间"的错误更公平，但这里全留，注释区分）
    load_errors = ws.js_errors()

    report = {"site": site, "browser": ver.get("Browser"), "tag": a.tag,
              "pages": {}, "tokens": {}, "contrast": {}, "interactions": {},
              "load_errors": load_errors}
    for p in PAGES:
        ws.eval_js("go(%s)" % json.dumps(p), await_promise=True)
        time.sleep(2.2)
        data = ws.eval_js(PROBE_JS.replace("%PAGE%", json.dumps(p))) or {"error": "probe 空"}
        st = ws.eval_js(STYLE_JS.replace("%PAGE%", json.dumps(p))) or {}
        data["scrollHeight"] = ws.eval_js(