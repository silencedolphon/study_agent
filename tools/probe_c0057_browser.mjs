// 卡 C-0057 真浏览器探针（headless Edge + CDP）：课件级 AI 答疑走的是**新管线**。
//
// 验两件事：
//   ① 课件页「AI答疑」：窗口说明里写明"不检索 / 不用提示词 / 思考强度最高"，
//      真提一个问题 → 收到回答 → **范围行与回答下方那行**如实报出这次实际参数
//      （模型 / 思考强度 / 送出 token / 预算 / 是否截断）；
//   ② 同一次运行里，问答页的知识库问答**照旧**（仍有检索、回答里仍带出处角标），
//      证明两条链路互不影响。
//
// 骨架照 tools/probe_c0052_browser.mjs（同一套 CDP 收发）。
// ⚠ 本探针会**真调模型两次**（课件答疑一次 + 知识库问答一次），调用方负责记账。
//
// 用法：
//   node probe_c0057_browser.mjs --port 8891 --doc 89 --q "…" \
//        --kbq "…" --outdir <截图目录> --json <结果 JSON>
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const PORT = arg('port', '8891');
const DOC = arg('doc', '89');
const Q = arg('q', '这份课件的重点是什么？分条列出。');
const KBQ = arg('kbq', '这份课件讲了哪些主要内容？');
const KBQ2 = arg('kbq2', '请分条说明这份课件的几个主要方面，并在每一条后面标注它的出处编号。');
const OUTDIR = arg('outdir', '');
const JSONOUT = arg('json', '');
const CDP_PORT = parseInt(arg('cdp', '9351'), 10);
const SKIP_AI = arg('skipai', '') === '1';
const BASE = `http://127.0.0.1:${PORT}`;

const profile = mkdtempSync(join(tmpdir(), 'c0057-edge-'));
const edge = spawn(EDGE, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-gpu', '--window-size=1500,1000', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (OUTDIR) mkdirSync(OUTDIR, { recursive: true });
  let list = null;
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
      list = await r.json();
      if (list?.length) break;
    } catch { /* 还没起来 */ }
    await sleep(250);
  }
  if (!list?.length) throw new Error('CDP 未就绪');
  const target = list.find((t) => t.type === 'page') || list[0];
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0;
  const pending = new Map();
  const errors = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    else if (m.method === 'Log.entryAdded' && m.params?.entry?.level === 'error') {
      const t = m.params.entry.text || '';
      const u = m.params.entry.url || '';
      if (!/favicon/i.test(t) && !/favicon/i.test(u)) errors.push(u ? `${t} @ ${u}` : t);
    } else if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params?.exceptionDetails?.text || 'exception');
    }
  };
  const send = (method, params = {}) => new Promise((res) => {
    const myId = ++id;
    pending.set(myId, res);
    ws.send(JSON.stringify({ id: myId, method, params }));
  });
  const evalJs = async (expr) => {
    const r = await send('Runtime.evaluate',
      { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) return { __error: r.result.exceptionDetails.text };
    return r.result?.result?.value;
  };
  const shot = async (name) => {
    if (!OUTDIR) return null;
    const p = join(OUTDIR, name);
    const s = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    if (s.result?.data) { writeFileSync(p, Buffer.from(s.result.data, 'base64')); return p; }
    return null;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable').catch(() => {});

  // ★ 抓原始 SSE：包装 window.fetch，把 /api/deck-chat/stream 与 /api/chat/stream
  //   的响应体各存一份。这样"这次到底发了什么事件、用量多少"是**页面真收到的东西**，
  //   不是我从界面上抄的估计值。必须在 navigate 之前注入。
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `
    (() => {
      const orig = window.fetch;
      window.__c0057 = [];
      window.fetch = async function(...a) {
        const resp = await orig.apply(this, a);
        try {
          const url = (a[0] && (a[0].url || String(a[0]))) || '';
          if (/\\/api\\/(deck-chat|chat)\\/stream/.test(url)) {
        bub.innerHTML = linkCites(bub.innerHTML + ' [1]');
        if (typeof bindCiteClicks === 'function') bindCiteClicks(host);
        span = host.querySelector('.cite');
        made = true;
      }
      if (!span) return { ok:false, reason:'造不出角标', synth:synth };
      const detail = { synth:synth, made:made, hasOnclick:!!span.onclick,
                       dataN:String(span.dataset.n), nEv:ev.length,
                       evIdx:ev.map(x => x.idx).slice(0,4) };
      span.click();
      await new Promise(r => setTimeout(r, 150));
      const pop0 = document.querySelector('.evpop');
      const dispEarly = pop0 ? (pop0.style.display || '') : '(无 .evpop)';
      await new Promise(r => setTimeout(r, 900));
      const pop = document.querySelector('.evpop');
      const disp = pop ? (pop.style.display || '') : '(无 .evpop)';
      const raw = pop ? String(pop.innerText || '') : '';
      const len = raw.length;
      // 判定以**内容**为准，不以"900ms 后还亮着"为准：这个浮窗的既定规矩是
      // "页面一滚就收"（evPopScroll），边上有任何滚动它就会自己关掉——
      // 那是对的机制，不是链路坏了。只要能证明"点角标之后，小窗里真的出现了
      // 这一条出处的原文"，这条链路就是通的。
      const want = String(ev[0].text || '').replace(/\s+/g, '').slice(0, 12);
      const got = raw.replace(/\s+/g, '');
      return Object.assign(detail, {
        ok: !!(pop && len > 20 && (!want || got.indexOf(want) >= 0)),
        displayEarly: dispEarly, display: disp, innerLen: len,
        wantSnippet: want,
        hasLabel: got.indexOf(String(ev[0].label || '').slice(0, 8).replace(/\s+/g,'')) >= 0,
        evLabel: String(ev[0].label || ev[0].location || '').slice(0, 80),
        text: raw.slice(0, 260) });
  })()`);
  out.shots.kb = await shot('4-kb-chat-answer.png');
  // 把两条链路**原始 SSE 文本**带回来（记账与事件类型都以此为准）。