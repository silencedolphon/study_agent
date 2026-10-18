// 卡 C-0053：用 headless Edge + CDP 真跑「知识点页签顶部区段」——
//   ① 顶部那两块**手工**栏（空态块 + 手工添加/新增知识点表单）在真 DOM 里**没有**：
//      抠 `#kp-add` / `#kp-name` / `#kp-chunks` / `#kp-summary` 是否存在、
//      `#view` 的可见文本里有没有那两句原话；
//   ② 「名词解释」栏真的在「课件思维导图」栏**上面**：用文档序
//      （`compareDocumentPosition`）+ 屏幕坐标（`getBoundingClientRect().top`）双证据；
//   ③ 选一份**有 terms** 的课件 → 展开 → 真的看到 名词 / 英文 / 中文解释
//      （逐条抠 `.termname` / `.termen` / `.termdef` 的文本）；
//   ④ **点名词 → 跳出该名词第一次出现的知识点窗口**：抠弹窗（`.evpop`）的
//      `dataset.kp` 与窗内显示的 kp_id 徽标，与名词自己的 `data-kp` 对照
//      （是否等于产物里的 `first_kp_id` 由 Python 侧对着产物真值判）；
//   ⑤ 旧产物（C-0051 之前的 `C-0049-v2`，没有 terms）→ 显示"暂无名词解释"空态，
//      **不是报错、不是空白**；真浏览器全程**无 JS 报错**。
//
// 为什么必须真浏览器：DOM 源码里"删没删干净"可以 grep，但"页面上到底还有没有
// 那个输入框""点名词到底弹不弹窗""两栏谁在上面"只有真跑才算数。
// 骨架照 tools/probe_c0055_browser.mjs / probe_c0052_browser.mjs。
//
// ⚠ **零模型调用**：只走"进页签 → 选课件 → 点名词"这条真实前端链路，
//    数据全部来自 `/api/terms`（只读产物），不碰 /api/chat/*。
//
// 用法：
//   node tools/probe_c0053_browser.mjs --port 8891 --v3 5 --v2 1 \
//        --outdir <截图目录> --json <结果 JSON>
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
const OUTDIR = arg('outdir', '');
const JSONOUT = arg('json', '');
const DOC_V3 = arg('v3', '0');
const DOC_V2 = arg('v2', '0');
const CDP_PORT = parseInt(arg('cdp', '9356'), 10);
const BASE = `http://127.0.0.1:${PORT}`;

const profile = mkdtempSync(join(tmpdir(), 'c0053-edge-'));
const edge = spawn(EDGE, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-gpu', '--window-size=1500,1000', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (OUTDIR) mkdirSync(OUTDIR, { recursive: true });
  const OUT = { base: BASE, port: PORT, v3doc: DOC_V3, v2doc: DOC_V2,
                errors: [], shots: {}, steps: {} };

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
    if (s.result?.data) { writeFileSync(p, Buffer.from(s.result.data, 'base64')); OUT.shots[name] = p; return p; }
    return null;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable').catch(() => {});
  await send('Page.navigate', { url: `${BASE}/` });
  await sleep(2500);

  // ---------------- ① 进知识点页签，抠顶部区段 ----------------
  const head = await evalJs(`(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const q = s => document.querySelector(s);
    if (typeof go !== 'function') return { ok:false, why:'no go()' };
    await go('kps');
    for (let i = 0; i < 80; i++) {
      await sleep(200);
      const s = q('#term-doc');
      if (s && s.options.length) break;
    }
    const view = q('#view');
    if (!view) return { ok:false, why:'no #view' };
    const h2s = [...view.querySelectorAll('h2')].map(h => (h.innerText||'').trim());
    const cardOf = sel => {
      let n = q(sel);
      while (n && !(n.classList && n.classList.contains('card'))) n = n.parentElement;
      return n;
    };
    const tCard = cardOf('#term-doc'), mCard = cardOf('#mm-box');
    const rect = e => e ? e.getBoundingClientRect() : null;
    const p = e => { const r = rect(e); return r ? { top: Math.round(r.top), h: Math.round(r.height) } : null; };
    return {
      ok: true,
      hasKpAdd: !!q('#kp-add'), hasKpName: !!q('#kp-name'),
      hasKpChunks: !!q('#kp-chunks'), hasKpSummary: !!q('#kp-summary'),
      manualH2: h2s.filter(t => /手工添加|新增知识点/.test(t)),
      emptyBlock: /还没有知识点/.test(view.innerText)
        || /让助手从资料中提取/.test(view.innerText)
        || /或在这里手工添加/.test(view.innerText),
      viewText: (view.innerText||'').replace(/\\n+/g, ' | ').slice(0, 500),
      h2s: h2s.slice(0, 8),
      order: {
        termH2: tCard ? (tCard.querySelector('h2')||{}).innerText : '',
        mmH2: mCard ? (mCard.querySelector('h2')||{}).innerText : '',
        termBeforeMm: !!(tCard && mCard && (tCard.compareDocumentPosition(mCard)
          & Node.DOCUMENT_POSITION_FOLLOWING)),
        termRect: p(tCard), mmRect: p(mCard),
      },
      termOptions: [...(q('#term-doc').options||[])].map(o => ({v:o.value, t:o.textContent})),
      mmOptions: q('#mm-doc') ? [...q('#mm-doc').options].map(o => o.value) : null,
      hasUnfoldBtn: !!q('#term-unfold'), unfoldText: (q('#term-unfold')||{}).textContent,
      hasMmAi: !!q('#mm-ai'),
    };
  })()`);
  OUT.steps.head = head;
  await shot('c0053-1-kps-top.png');

  // ---------------- ② 选一份有 terms 的课件 → 展开看名词 ----------------
  const pickJs = (docId) => `(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const q = s => document.querySelector(s);
    const s = q('#term-doc');
    if (!s) return { ok:false, why:'no #term-doc' };
    s.value = ${JSON.stringify(String(docId))};
    if (s.value !== ${JSON.stringify(String(docId))}) return { ok:false, why:'下拉里没有这份课件' };
    s.dispatchEvent(new Event('change', { bubbles: true }));
    for (let i = 0; i < 100; i++) {
      await sleep(200);
      const box = q('#term-box');
      if (box && !/加载中/.test(box.innerText)) break;
    }
    await sleep(400);
    const box = q('#term-box');
    const names = [...box.querySelectorAll('.termname')];
    const rows = names.slice(0, 6).map(a => {
      const li = a.closest('li') || box;
      return { term: (a.innerText||'').trim(), kp: a.dataset.kp || '',
               kpname: a.dataset.kpname || '',
               en: ((li.querySelector('.termen')||{}).innerText||'').trim(),
               def: ((li.querySelector('.termdef')||{}).innerText||'').trim(),
               meta: (li.innerText||'').replace(/\\n+/g,' | ').slice(0, 160) };
    });
    return { ok:true, sel: s.value, n: names.length,
             boxText: (box.innerText||'').replace(/\\n+/g,' | ').slice(0, 400),
             rows, btn: (q('#term-unfold')||{}).textContent || '',
             btnDisabled: !!(q('#term-unfold')||{}).disabled,
             boxH: Math.round(box.getBoundingClientRect().height) };
  })()`;
  const v3 = await evalJs(pickJs(DOC_V3));
  OUT.steps.v3 = v3;
  await shot('c0053-2-terms-expanded.png');

  // ---------------- ③ 点第一个名词 → 弹出它第一次出现的知识点窗口 ----------------
  const clickJs = `(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const q = s => document.querySelector(s);
    const a = q('#term-box .termname');
    if (!a) return { ok:false, why:'#term-box 里没有 .termname' };
    const want = { kp: a.dataset.kp || '', name: a.dataset.kpname || '',
                   term: (a.innerText||'').trim() };
    a.click();
    for (let i = 0; i < 100; i++) {
      await sleep(200);
      const p = q('.evpop');
      if (p && p.dataset.kp) break;
    }
    await sleep(900);
    const pop = q('.evpop');
    if (!pop) return { ok:false, why:'点名词后没有 .evpop', want };
    const r = pop.getBoundingClientRect();
    return { ok:true, want,
      kp: pop.dataset.kp || '',
      visible: getComputedStyle(pop).display !== 'none',
      loc: ((pop.querySelector('.loc')||{}).innerText||'').replace(/\\n+/g,' | ').slice(0,200),
      hdg: ((pop.querySelector('.hdg')||{}).innerText||'').replace(/\\n+/g,' | ').slice(0,200),
      body: ((pop.querySelector('.pbody')||{}).innerText||'').replace(/\\n+/g,' | ').slice(0,400),
      stillLoading: /正在读取知识点总结|加载中/.test(pop.innerText||''),
      badge: ((pop.querySelector('.badge')||{}).innerText||'').trim(),
      rect: { w: Math.round(r.width), h: Math.round(r.height) } };
  })()`;
  const pop = await evalJs(clickJs);
  OUT.steps.popup = pop;
  await shot('c0053-3-term-to-kp-popup.png');

  // ---------------- ④ 收起 / 再展开 ----------------
  const toggleJs = `(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const q = s => document.querySelector(s);
    if (typeof evPopClose === 'function') evPopClose();
    const btn = q('#term-unfold');
    if (!btn) return { ok:false, why:'no #term-unfold' };
    btn.click(); await sleep(400);
    const box = q('#term-box');
    const folded = { n: box.querySelectorAll('.termname').length,
                     text: (box.innerText||'').replace(/\\n+/g,' | ').slice(0,120),
                     btn: btn.textContent };
    btn.click(); await sleep(400);
    const shown = { n: box.querySelectorAll('.termname').length };
    return { ok:true, folded, shown };
  })()`;
  OUT.steps.toggle = await evalJs(toggleJs);

  // ---------------- ⑤ 旧产物（v2，没有 terms）→ 空态 ----------------
  const v2 = await evalJs(pickJs(DOC_V2));
  OUT.steps.v2 = v2;
  await shot('c0053-4-legacy-empty.png');

  // ---------------- ⑥ 收尾：真浏览器无 JS 报错 ----------------
  await sleep(400);
  OUT.done = true;
  ws.close();
  edge.kill();
  if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(OUT, null, 2), 'utf8');
  console.log('probe done: errors=%d shots=%d', OUT.errors.length,
              Object.keys(OUT.shots).length);
}

main().catch((e) => {
  console.error('probe 失败:', e && e.stack || e);
  try { edge.kill(); } catch { /* 忽略 */ }
  process.exit(1);
});
