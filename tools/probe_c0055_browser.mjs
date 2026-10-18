// 卡 C-0055：用 headless Edge + CDP 真跑「出处小窗里的这一页」——
//   ① 点问答页回答里的引用角标 → 小窗里**真的显示出那一页的课件图**
//      （抠出 <img> 的 src / naturalWidth / naturalHeight，并 fetch 该 URL 记 HTTP 状态）；
//   ② 小窗里列出**归属这一页的知识点**（可能多条）→ 点它真能打开**知识点弹窗**
//      （抠出弹窗里显示的 kp_id，与产物 page_owner 对照由 Python 侧做）；
//   ③ 页码解析边界：`p.N-M` 跨页、`p.N 图` 识图块、认不出页码的出处——
//      三种都要"不报错 + 给可理解提示"；
//   ④ 无图课件 / 越界页：明说"该页没有课件图缓存"，绝不给别的页的图充数。
//
// 为什么必须真浏览器（本档位血的教训）：DOM 桩验不出交互——图有没有真的加载出来
// （naturalWidth>0）、点知识点是不是真的换成了知识点弹窗，只有真跑才算数。
// 骨架照 tools/probe_c0052_browser.mjs / probe_c0050_browser.mjs。
//
// ⚠ **零模型调用**：证据由调用方（verify_c0055_browser.py）从真库的 chunks 表
//    组装成 `Evidence.to_dict()` 的形态，本探针只把它注入页面（MSG_EV），
//    走的是页面自己的渲染与绑定链路（viewAssistant → linkCites → bindCiteClicks
//    → evPopShow → evCiteExtras）。模型只负责写回答正文，那一步本卡禁跑。
//
// 用法：
//   node tools/probe_c0055_browser.mjs --port 8891 --cases <cases.json> \
//        --outdir <截图目录> --json <结果 JSON>
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const PORT = arg('port', '8891');
const OUTDIR = arg('outdir', '');
const CASES = arg('cases', '');
const JSONOUT = arg('json', '');
const CDP_PORT = parseInt(arg('cdp', '9355'), 10);
const BASE = `http://127.0.0.1:${PORT}`;

const profile = mkdtempSync(join(tmpdir(), 'c0055-edge-'));
const edge = spawn(EDGE, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-gpu', '--window-size=1500,1000', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (OUTDIR) mkdirSync(OUTDIR, { recursive: true });
  const spec = JSON.parse(readFileSync(CASES, 'utf8'));
  const order = spec.order || Object.keys(spec.cases);
  const OUT = { base: BASE, port: PORT, cases: {}, errors: [], shots: {} };

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
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    else if (m.method === 'Log.entryAdded' && m.params?.entry?.level === 'error') {
      const t = m.params.entry.text || '';
      const u = m.params.entry.url || '';
      if (!/favicon/i.test(t) && !/favicon/i.test(u)) OUT.errors.push(u ? `${t} @ ${u}` : t);
    } else if (m.method === 'Runtime.exceptionThrown') {
      OUT.errors.push(m.params?.exceptionDetails?.text || 'exception');
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
    if (r.result?.exceptionDetails) {
      return { __error: r.result.exceptionDetails.text || 'exception',
               __detail: (r.result.exceptionDetails.exception || {}).description || '' };
    }
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

  await send('Page.navigate', { url: `${BASE}/` });
  await sleep(2200);

  const mount = await evalJs(`(async () => {
    if (typeof go === 'function') go('assistant');
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 200));
      if (document.querySelector('#chatq')) return { ok: true,
        hasViewAssistant: typeof viewAssistant === 'function',
        hasEvPopShow: typeof evPopShow === 'function',
        hasEvCiteExtras: typeof evCiteExtras === 'function' };
    }
    return { ok: false };
  })()`);
  OUT.mount = mount;

  // 每个 case：注入一条真实证据的回答 → 点角标 → 抓小窗里的"这一页" → 点知识点 chip
  for (const name of order) {
    const c = spec.cases[name];
    if (!c) continue;
    const res = await evalJs(`(async () => {
      const sleep = (ms) => new Promise(r => setTimeout(r, ms));
      if (typeof evPopClose === 'function') evPopClose();
      const q = ${JSON.stringify(c.question || '探针问题')};
      const ev = ${JSON.stringify(c.evidence || [])};
      const text = ${JSON.stringify(c.answer || '回答正文[1]。')};
      CHAT.msgs = [{ role: 'user', content: q }, { role: 'ai', content: text }];
      CHAT.turnEv = []; setTurnEv(1, ev); CHAT.ev = ev;
      await viewAssistant();
      const cite = document.querySelector('#thread .cite');
      if (!cite) return { ok: false, reason: '回答里没有出现角标' };
      cite.click();
      await sleep(${c.waitMs || 1100});
      const pop = document.querySelector('.evpop');
      if (!pop) return { ok: false, reason: '点了角标没弹出小窗' };
      const extra = document.querySelector('#evpop-extra');
      const r = extra ? extra.getBoundingClientRect() : null;
      const pr = pop.getBoundingClientRect();
      const out = {
        ok: true,
        citeText: cite.textContent,
        popupLoc: (pop.querySelector('.ph .loc') || {}).textContent || '',
        extraHtml: extra ? extra.innerHTML : '',
        extraText: extra ? extra.innerText : '',
        hasExtra: !!extra,
        popupRect: [Math.round(pr.left), Math.round(pr.top), Math.round(pr.width), Math.round(pr.height)],
        extraRect: r ? [Math.round(r.top - pr.top), Math.round(r.height)] : null,
        pagesChips: [...document.querySelectorAll('#evpop-pages .kppage')].map(x => ({
          page: x.dataset.page, off: x.className.indexOf('off') >= 0,
          on: x.className.indexOf('on') >= 0, title: x.title })),
        kpChips: [...document.querySelectorAll('#evpop-extra .evkp')].map(x => ({
          kp: x.dataset.kp, name: x.dataset.name, title: x.title })),
        imgs: [...pop.querySelectorAll('img')].map(i => ({
          src: i.getAttribute('src'), cls: i.className,
          nw: i.naturalWidth, nh: i.naturalHeight, complete: i.complete,
          visible: i.getBoundingClientRect().height > 8 })),
        dataset: { ...pop.dataset },
      };
      // 图真的能取到吗：直接 fetch 一次，记 HTTP 状态与字节数
      const img = pop.querySelector('img.evslideimg');
      out.imgHttp = null;
      if (img) {
        try {
          const resp = await fetch(img.getAttribute('src'));
          const buf = await resp.arrayBuffer();
          out.imgHttp = { status: resp.status, type: resp.headers.get('content-type'),
                          bytes: buf.byteLength, url: img.getAttribute('src') };
        } catch (e) { out.imgHttp = { error: String(e) }; }
      }
      // 点第一个知识点 chip 的动作在**外面**做（中间要插一张截图，见下）
      return out;
    })()`);
    OUT.cases[name] = res;
    // 点知识点 chip **之前**先截一张：验收要能看清"小窗里真有图 + 真有知识点"
    OUT.shots[name + '-popup'] = await shot(`c0055-${name}-popup.png`);
    if (c.clickKp !== false && (res?.kpChips || []).length) {
      OUT.cases[name].kpClick = await evalJs(`(async () => {
        const sleep = (ms) => new Promise(r => setTimeout(r, ms));
        const kp = document.querySelector('#evpop-extra .evkp');
        if (!kp) return null;
        const want = kp.dataset.kp;
        kp.click();
        await sleep(1600);
        const pop2 = document.querySelector('.evpop');
        const info = {
          want,
          datasetKp: (pop2.dataset.kp || ''),
          loc: (pop2.querySelector('.ph .loc') || {}).textContent || '',
          hdg: (pop2.querySelector('.ph .hdg') || {}).textContent || '',
          badges: [...pop2.querySelectorAll('.badge')].map(b => b.textContent.trim()),
          nPoints: pop2.querySelectorAll('.lkppts li').length,
          hasDeckBtn: !!pop2.querySelector('#mmkp-deck'),
          hasNoteBtn: !!pop2.querySelector('#mmkp-note'),
          kpPop: pop2.dataset.kpPop || '',
          rect: (() => { const q = pop2.getBoundingClientRect();
                         return [Math.round(q.left), Math.round(q.top),
                                 Math.round(q.width), Math.round(q.height)]; })(),
          centerOffset: (() => { const q = pop2.getBoundingClientRect();
            const cx = (document.documentElement.clientWidth - q.width) / 2;
            const cy = (document.documentElement.clientHeight - q.height) / 2;
            return [Math.round(q.left - cx), Math.round(q.top - cy)]; })(),
        };
        // 网状可达的**另一半**：知识点弹窗里已有的「展开当页课件」要能回到图。
        // 这一条同时证明本卡的 docId 改动让整条链自洽（弹窗读的是出处那份课件）。
        const deck = pop2.querySelector('#mmkp-deck');
        info.deck = null;
        if (deck) {
          deck.click();
          await sleep(1400);
          const pop3 = document.querySelector('.evpop');
          const box = pop3.querySelector('#mmkp-slide');
          const im = pop3.querySelector('#mmkp-figbox img');
          info.deck = {
            opened: box ? !box.hidden : false,
            imgSrc: im ? im.getAttribute('src') : '',
            nw: im ? im.naturalWidth : 0,
            nh: im ? im.naturalHeight : 0,
            pages: [...pop3.querySelectorAll('#mmkp-pages .kppage')].map(x => x.dataset.page),
            emptyText: (pop3.querySelector('#mmkp-figbox') || {}).innerText || '',
          };
        }
        return info;
      })()`);
      OUT.shots[name + '-kp'] = await shot(`c0055-${name}-kp.png`);
    }
  }

  // 收尾：小窗之后仍能关掉（Esc）且页面没留下脏状态
  OUT.closeCheck = await evalJs(`(async () => {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    if (typeof evPopClose === 'function') evPopClose();
    await sleep(150);
    const pop = document.querySelector('.evpop');
    return { display: pop ? getComputedStyle(pop).display : 'none',
             citeOn: document.querySelectorAll('.cite.on').length };
  })()`);

  OUT.errors = OUT.errors;
  if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(OUT, null, 2), 'utf8');
  console.log(JSON.stringify({ mount: OUT.mount, cases: Object.keys(OUT.cases),
                               errors: OUT.errors.length, closeCheck: OUT.closeCheck }));
  ws.close();
  edge.kill();
}

main().catch((e) => {
  console.error('FAIL', e);
  try { edge.kill(); } catch { /* ignore */ }
  process.exit(1);
});
