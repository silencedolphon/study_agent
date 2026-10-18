// 卡 C-0074 首页探针（headless Edge + CDP，零依赖）。
//
// 验的事：**一个下载了 exe、什么环境都没有的人，双击后看到的首页长什么样**。
//   · 首页是否给出"下一步该干什么"的引导（而不是一片空白）；
//   · 未配置大模型密钥时，是否有明确、可操作的提示；
//   · 不依赖模型的功能（浏览资料 / 检索本地库 / 复习 / 错题）是否仍然可用。
//
// 用法：
//   node tools/probe_c0074_browser.mjs --port 8791 --outdir <截图目录> --json <结果 JSON>
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const PORT = arg('port', '8791');
const OUTDIR = arg('outdir', '');
const JSONOUT = arg('json', '');
const CDP_PORT = parseInt(arg('cdp', '9361'), 10);
const BASE = `http://127.0.0.1:${PORT}`;

const EDGE_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];
const EDGE = EDGE_CANDIDATES.find((p) => existsSync(p));
if (!EDGE) { console.error('找不到 msedge.exe'); process.exit(3); }

const profile = mkdtempSync(join(tmpdir(), 'c0074-edge-'));
const edge = spawn(EDGE, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-gpu', '--window-size=1400,1000', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (OUTDIR) mkdirSync(OUTDIR, { recursive: true });
  let list = null;
  for (let i = 0; i < 80; i++) {
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
    const s = await send('Page.captureScreenshot', { format: 'png' });
    if (s.result?.data) { writeFileSync(p, Buffer.from(s.result.data, 'base64')); return p; }
    return null;
  };
  const waitFor = async (expr, ms = 15000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (await evalJs(expr)) return true;
      await sleep(220);
    }
    return false;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable').catch(() => {});

  const out = { base: BASE, port: PORT, steps: {}, shots: {}, errors: [] };

  // ---------- 首页 ----------
  await send('Page.navigate', { url: `${BASE}/` });
  await sleep(2600);
  await waitFor(`!!document.querySelector('#nav button')`, 15000);
  await sleep(1200);

  out.steps.home = await evalJs(`(() => {
    const nav = [...document.querySelectorAll('#nav button')].map(b => (b.textContent||'').trim());
    const vis = (el) => {
      if (!el) return false;
      const s = getComputedStyle(el);
      if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    // 主视图文本（排掉导航）
    const view = document.querySelector('#view');
    const viewText = view ? (view.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim() : '';
    // 全页可点元素（找"去哪里配密钥"的入口）
    const clickable = [...document.querySelectorAll('a,button,[onclick]')]
      .filter(vis).map(e => ((e.innerText || e.textContent || '').trim().slice(0, 40)))
      .filter(t => t).slice(0, 40);
    // 关键小部件
    const gear = [...document.querySelectorAll('button,a,span,div')]
      .filter(vis).map(e => ((e.textContent||'').trim()))
      .filter(t => t && /⚙|设置/.test(t) && t.length < 20);
    // 收藏：任何元素文本里出现"密钥/配置/上手/开始/第一步/入门"的，连选择器一起抄下来
    const hints = [];
    for (const el of document.querySelectorAll('body *')) {
      if (el.children.length > 3) continue;
      const t = (el.innerText || '').trim();
      if (!t || t.length > 300) continue;
      if (/密钥|未配置|配置|上手|先做|第一步|入门|开始用/.test(t) && vis(el)) {
        hints.push({ tag: el.tagName.toLowerCase(),
                     cls: (el.className || '').toString().slice(0, 60),
                     id: el.id || '', text: t.slice(0, 200) });
      }
    }
    return { nav, viewText: viewText.slice(0, 2000), clickable, gear: [...new Set(gear)],
             hints: hints.slice(0, 25), title: document.title };
  })()`);
  out.shots.home = await shot('home.png');

  // ---------- 非模型功能：资料页 / 检索页 是否仍可用 ----------
  const goStep = async (key, label) => {
    const r = await evalJs(`(async () => {
      try { await go(${JSON.stringify(key)}); return 'ok'; }
      catch (e) { return 'ERR:' + e.message; }
    })()`);
    await sleep(1800);
    const t = await evalJs(`(() => {
      const v = document.querySelector('#view');
      return v ? (v.innerText || '').replace(/\\n{3,}/g,'\\n\\n').trim().slice(0, 900) : '(无 #view)';
    })()`);
    out.steps[label] = { called: r, text: t };
    out.shots[label] = await shot(label + '.png');
  };
  // ⚠ 视图名必须取自 dashboard.py 的 `VIEWS`（assistant/overview/review/graph/
  //   docs/kps/mistakes/notes）。写错名字会让 `VIEWS[name].render()` 抛
  //   TypeError，界面上显示成"出错：…"——那是探针的错，不是产品的错。
  for (const [key, label] of [['overview', 'overview'], ['docs', 'docs'],
                              ['kps', 'kps'], ['graph', 'graph'],
                              ['notes', 'notes'], ['review', 'review'],
                              ['mistakes', 'mistakes']]) {
    await goStep(key, label);
  }

  await send('Page.navigate', { url: `${BASE}/` });
  await sleep(2500);
  out.shots.home2 = await shot('home2.png');
  out.errors = errors;
  if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(out, null, 2), 'utf-8');

  console.log('结论：首页导航 [' + (out.steps.home?.nav || []).join(' | ') + ']');
  console.log('首页提示元素数：' + (out.steps.home?.hints || []).length);
  console.log('页面错误数：' + errors.length);
  if (OUTDIR) console.log('截图：' + OUTDIR);
  ws.close();
  edge.kill();
  process.exit(0);
}

main().catch((e) => { console.error('探针失败：' + e.message); try { edge.kill(); } catch {} process.exit(1); });
