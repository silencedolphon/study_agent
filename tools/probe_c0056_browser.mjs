// 卡 C-0056：用 headless Edge + CDP 真跑「知识点弹窗」的三项修复——
//   ① **滚动不出窗**（人报的核心问题）：把弹窗正文滚到最顶 / 最底后**继续滚**
//      （真 wheel 输入事件，走 CDP Input.dispatchMouseEvent），弹窗必须**始终存在**，
//      且外层 `document.scrollingElement.scrollTop` **一个字都不变**（没被穿透）；
//   ② 「已绑定 N 条笔记」小字：N 要和后端计数、`/api/notes/by_kp` 的真值、
//      以及「查看笔记」列表列出来的条数**三方一致**（0 条 / 1 条 / ≥2 条各来一次）；
//   ③ 「◀ 上一知识点」：与「下一知识点」对称，顺序照后端 `prev_id` 走；
//      链首时置灰且点不动。
//
// 为什么必须真浏览器：滚动链（scroll chaining）是**布局 + 输入事件**共同作用的
// 结果，只读代码可以写出"我 preventDefault 了"，但"到底拦没拦住、拦完还能不能
// 正常滚"只有真发滚轮事件才算数。基线（未修复）与修复后跑同一个探针，
// 前者要能看到"弹窗消失 + 外层被滚"，后者要看到两者都不发生。
//
// ⚠ **零模型调用**：只走"进页签 → 点知识点节点 → 滚 / 点按钮"这条真实前端链路，
//    数据全部来自 `/api/summary-kp`、`/api/notes/by_kp`（只读产物与 notes/ 文件）。
//
// 用法：
//   node tools/probe_c0056_browser.mjs --port 8891 --mode green \
//        --outdir <截图目录> --json <结果 JSON>
//   node tools/probe_c0056_browser.mjs --port 8892 --mode red ...   # 基线复现
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
const MODE = arg('mode', 'green');          // green=修复后 / red=未修复基线
const CDP_PORT = parseInt(arg('cdp', MODE === 'red' ? '9401' : '9402'), 10);
const BASE = `http://127.0.0.1:${PORT}`;
const N_TOP = parseInt(arg('top', '12'), 10);     // 到顶后继续向上滚的次数
const N_BOTTOM = parseInt(arg('bottom', '12'), 10); // 到底后继续向下滚的次数

const profile = mkdtempSync(join(tmpdir(), 'c0056-edge-'));
const edge = spawn(EDGE, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-gpu', '--window-size=1500,1000', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (OUTDIR) mkdirSync(OUTDIR, { recursive: true });
  const OUT = { base: BASE, port: PORT, mode: MODE, errors: [], shots: {}, steps: {} };

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
  const wheel = async (x, y, dy) => {
    await send('Input.dispatchMouseEvent',
      { type: 'mouseWheel', x, y, deltaX: 0, deltaY: dy, pointerType: 'mouse' });
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable').catch(() => {});
  await send('Page.navigate', { url: `${BASE}/` });
  await sleep(2500);

  // ---------------- 页面内辅助（只读快照 + 真实链路动作） ----------------
  const mkHelpers = `(() => {
    const q = (s) => document.querySelector(s);
    const pop = () => document.querySelector('.evpop');
    const se = () => document.scrollingElement || document.documentElement;
    const body = () => document.querySelector('#mmkp-body');
    const vis = (el) => !!el && getComputedStyle(el).display !== 'none';
    window.__c0056 = {
      q, pop, se, body, vis,
      snap(){
        const p = pop(), b = body(), n = q('#mmkp-notecount');
        return {
          exists: !!p,
          display: p ? getComputedStyle(p).display : null,
          kp: p ? (p.dataset.kp || '') : null,
          seTop: Math.round(se().scrollTop),
          seMax: Math.round(se().scrollHeight - se().clientHeight),
          bodyTop: b ? Math.round(b.scrollTop) : -1,
          bodyMax: b ? Math.round(b.scrollHeight - b.clientHeight) : -1,
          noteText: n ? n.textContent.trim() : null,
        };
      },
      // 滚轮事件的落点：正文区中心（保证命中可滚容器 .pbody 本身或其后代）
      aim(){
        const b = body() || pop();
        const r = b.getBoundingClientRect();
        const cx = Math.round(r.left + r.width / 2);
        const cy = Math.round(r.top + Math.min(r.height / 2, r.height - 6));
        return { cx, cy, rect: { left: Math.round(r.left), top: Math.round(r.top),
                                 w: Math.round(r.width), h: Math.round(r.height) } };
      },
      popRect(){
        const p = pop(); if (!p) return null;
        const r = p.getBoundingClientRect();
        const de = document.documentElement;
        return { left: Math.round(r.left), top: Math.round(r.top),
                 right: Math.round(r.right), bottom: Math.round(r.bottom),
                 vw: de.clientWidth, vh: de.clientHeight,
                 inView: r.left >= -1 && r.top >= -1 &&
                         r.right <= de.clientWidth + 1 && r.bottom <= de.clientHeight + 1,
                 overscroll: (() => { const b = body();
                   return b ? getComputedStyle(b).overscrollBehaviorY : null; })() };
      },
      setBody(top){ const b = body(); if (b) b.scrollTop = top; return b ? Math.round(b.scrollTop) : -1; },
      setOuter(top){ se().scrollTop = top; return Math.round(se().scrollTop); },
      async openKp(key){
        const el = document.querySelector('.mmname[data-k="' + key + '"]');
        if (!el) return { ok: false, why: 'no .mmname for ' + key };
        el.click();
        for (let i = 0; i < 120; i++) {
          await new Promise(r => setTimeout(r, 150));
          const p = pop();
          if (p && p.dataset.kp && q('#mmkp-body') &&
              !/加载中/.test((q('#mmkp-body') || {}).textContent || '')) {
            return { ok: true, kp: p.dataset.kp };
          }
        }
        return { ok: false, why: '弹窗没等到正文' };
      },
      close(){ if (typeof evPopClose === 'function') evPopClose(); },
      click(id){ const el = document.getElementById(id); if (!el) return { ok:false, why:'no #'+id };
        el.click(); return { ok: true, disabled: !!el.disabled }; },
      async waitKp(kpId, ms){
        const t0 = Date.now();
        while (Date.now() - t0 < (ms || 8000)) {
          await new Promise(r => setTimeout(r, 120));
          const p = pop();
          if (p && p.dataset.kp === kpId) return true;
        }
        return false;
      },
      async waitText(sel, re, ms){
        const t0 = Date.now();
        while (Date.now() - t0 < (ms || 8000)) {
          const el = q(sel);
          if (el && re.test(el.textContent || '')) return (el.textContent || '').trim();
          await new Promise(r => setTimeout(r, 120));
        }
        const el = q(sel);
        return el ? (el.textContent || '').trim() : null;
      },
    };
    return true;
  })()`;
  await evalJs(mkHelpers);

  // ---------------- ① 进知识点页签，选一份有导图的课件，点开一条知识点 ----------------
  const setup = await evalJs(`(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    if (typeof go !== 'function') return { ok:false, why:'no go()' };
    await go('kps');
    let sel = null;
    for (let i = 0; i < 80; i++) {
      await sleep(200);
      sel = document.querySelector('#mm-doc');
      if (sel && sel.querySelector('option[value]:not([value=""])')) break;
    }
    if (!sel) return { ok:false, why:'课件下拉没出现' };
    const opts = [...sel.querySelectorAll('option')].filter(o => o.value);
    // 逐个试：要一份**真在页签树上点得开**的课件（有 lkp_ready 的节点）
    for (const o of opts) {
      sel.value = o.value;
      sel.dispatchEvent(new Event('change', {bubbles:true}));
      for (let i = 0; i < 60; i++) {
        await sleep(200);
        if (document.querySelector('#mm-tree .mmname.has-kp')) break;
      }
      const nodes = (typeof MINDMAP !== 'undefined' && MINDMAP.nodes) || {};
      const keys = Object.keys(nodes).filter(k => {
        const n = nodes[k];
        return n && n.lkp_ready && !(n.lkp_ids || []).length > 1 &&
               (n.lkp_ids || []).length <= 1 && document.querySelector('.mmname[data-k="' + k + '"]');
      });
      if (keys.length) {
        // 展开整棵树：节点可能是折叠的（点击仍有效，但截图与坐标更直观）
        if (typeof mmToggleAll === 'function') mmToggleAll(true);
        await sleep(200);
        const first = keys[0];
        const n = nodes[first];
        return { ok:true, docId: parseInt(sel.value, 10), key: first,
                 kpId: n.lkp_id || '', name: n.lkp_name || n.name || '',
                 nKeys: keys.length, orderTotal: (typeof MINDMAP !== 'undefined' && MINDMAP.total) || 0 };
      }
    }
    return { ok:false, why:'没有一份课件在树上带可点知识点' };
  })()`);
  OUT.steps.setup = setup;
  if (!setup?.ok) {
    OUT.fatal = 'setup: ' + (setup?.why || 'unknown');
    return finish(OUT);
  }

  const opened = await evalJs(`window.__c0056.openKp(${JSON.stringify(setup.key)})`);
  OUT.steps.open = opened;
  if (!opened?.ok) {
    OUT.fatal = 'openKp: ' + (opened?.why || 'unknown');
    return finish(OUT);
  }
  await sleep(300);
  const KP0 = opened.kp;

  // 后端真值（有出处：同一个接口，探针只读）
  const truth = async (kpId) => await evalJs(
    `fetch('/api/summary-kp?doc_id=${setup.docId}&kp_id=' + encodeURIComponent(${JSON.stringify(kpId)}))
       .then(r => r.json()).then(d => ({ kp_id: d.kp_id, name: d.name,
         prev_id: d.prev_id, prev_name: d.prev_name, next_id: d.next_id, next_name: d.next_name,
         order_index: d.order_index, order_total: d.order_total,
         note_count: d.note_count === undefined ? null : d.note_count,
         error: d.error || null }))`);
  OUT.steps.truth0 = await truth(KP0);
  OUT.steps.popRect = await evalJs('window.__c0056.popRect()');
  await shot('01-popup-opened.png');

  // ---------------- ② 滚动不出窗（核心） ----------------
  const scroll = { top: [], bottom: [], aim: null, samplesOk: true };
  // 外层先滚到中间：这样"外层有没有被穿透"才看得见（在外层本来就在顶部时判不出来）
  const outer0 = await evalJs('window.__c0056.setOuter(Math.round(window.__c0056.snap().seMax / 2))');
  scroll.outerBefore = outer0;
  scroll.outerMax = (await evalJs('window.__c0056.snap()')).seMax;
  const aim = await evalJs('window.__c0056.aim()');
  scroll.aim = aim;

  // (a) 先滚到最顶，然后**继续向上滚** N_TOP 次
  await evalJs('window.__c0056.setBody(0)');
  for (let i = 0; i < N_TOP; i++) {
    await wheel(aim.cx, aim.cy, -120);
    await sleep(70);
    scroll.top.push(await evalJs('window.__c0056.snap()'));
  }
  await shot('02-after-over-scroll-top.png');

  // (b) 再滚到最底，然后**继续向下滚** N_BOTTOM 次
  await evalJs('window.__c0056.setBody(1e9)');
  const atBottom = await evalJs('window.__c0056.snap()');
  scroll.bottomStart = atBottom;
  for (let i = 0; i < N_BOTTOM; i++) {
    await wheel(aim.cx, aim.cy, 120);
    await sleep(70);
    scroll.bottom.push(await evalJs('window.__c0056.snap()'));
  }
  await shot('03-after-over-scroll-bottom.png');
  scroll.outerAfter = (await evalJs('window.__c0056.snap()')).seTop;
  scroll.popRectAfter = await evalJs('window.__c0056.popRect()');
  OUT.steps.scroll = scroll;

  // ---------------- ③ 弹窗内还能正常滚（别把滚动一起截死） ----------------
  const canScroll = { steps: [] };
  await evalJs('window.__c0056.setBody(0)');
  canScroll.startTop = (await evalJs('window.__c0056.snap()')).bodyTop;
  for (let i = 0; i < 6; i++) {
    await wheel(aim.cx, aim.cy, 120);
    await sleep(70);
    canScroll.steps.push((await evalJs('window.__c0056.snap()')).bodyTop);
  }
  canScroll.afterDownTop = (await evalJs('window.__c0056.snap()')).bodyTop;
  for (let i = 0; i < 6; i++) {
    await wheel(aim.cx, aim.cy, -120);
    await sleep(70);
    canScroll.steps.push((await evalJs('window.__c0056.snap()')).bodyTop);
  }
  canScroll.afterUpTop = (await evalJs('window.__c0056.snap()')).bodyTop;
  canScroll.max = (await evalJs('window.__c0056.snap()')).bodyMax;
  OUT.steps.canScroll = canScroll;

  // ---------------- ④ 课件区：展开后弹窗仍在、内容不越界 ----------------
  const deck = await evalJs(`(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const btn = document.getElementById('mmkp-deck');
    if (!btn) return { ok:false, why:'没有 #mmkp-deck' };
    btn.click();
    await sleep(1200);
    const box = document.getElementById('mmkp-slide');
    const fig = document.getElementById('mmkp-figbox');
    const img = fig ? fig.querySelector('img') : null;
    return { ok:true, shown: !!(box && !box.hidden), figText: fig ? (fig.textContent || '').trim().slice(0, 160) : null,
             hasImg: !!img, imgComplete: img ? !!img.complete : null,
             rect: window.__c0056.popRect(), snap: window.__c0056.snap() };
  })()`);
  OUT.steps.deck = deck;
  await sleep(600);
  OUT.steps.deckAfter = { rect: await evalJs('window.__c0056.popRect()'),
                          snap: await evalJs('window.__c0056.snap()') };
  await shot('04-deck-open.png');
  // 收起课件，回到"只有正文"的形状（后面的按钮测试不受图影响）
  await evalJs(`(async () => { const b = document.getElementById('mmkp-deck');
    if (b) b.click(); await new Promise(r => setTimeout(r, 400)); return true; })()`);

  const notes = { opened0: null, kp: KP0, created: [], checks: [] };
  const countVia = async (kpId, docId) => await evalJs(
    `fetch('/api/notes/by_kp?kp_id=' + encodeURIComponent(${JSON.stringify(kpId)}) + '&doc_id=${docId}')
       .then(r => r.json()).then(d => (d.notes || []).length)`);
  const noteText = async () => await evalJs(
    `(() => { const el = document.getElementById('mmkp-notecount');
       return el ? el.textContent.trim() : null; })()`);
  const addNote = async (n) => await evalJs(
    `fetch('/api/note/kp', {method:'POST', headers:{'Content-Type':'application/json'},
       body: JSON.stringify({ kp_id: ${JSON.stringify(KP0)}, kp_name: ${JSON.stringify(setup.name)},
         doc_id: ${setup.docId}, title: 'C0056-临时验证笔记-' + ${n},
         body: 'C-0056 真浏览器验证用临时笔记 #' + ${n} + '（跑完即删）', refs: [] })})
     .then(r => r.json())`);

  // 情形 A：不写笔记（0 条）——这一屏就是上面打开的那个弹窗
  notes.count0 = await countVia(KP0, setup.docId);
  notes.text0 = await noteText();
  notes.checks.push({ label: '0 条', count: notes.count0, text: notes.text0 });
  // 情形 B：写 1 条 → 重开弹窗
  notes.created.push(await addNote(1));
  await evalJs(`(async () => { window.__c0056.close();
    await new Promise(r => setTimeout(r, 200));
    return await window.__c0056.openKp(${JSON.stringify(setup.key)}); })()`);
  await sleep(300);
  notes.count1 = await countVia(KP0, setup.docId);
  notes.text1 = await noteText();
  notes.checks.push({ label: '1 条', count: notes.count1, text: notes.text1 });
  // 情形 C：再写 1 条（≥2 条）→ 重开弹窗
  notes.created.push(await addNote(2));
  await evalJs(`(async () => { window.__c0056.close();
    await new Promise(r => setTimeout(r, 200));
    return await window.__c0056.openKp(${JSON.stringify(setup.key)}); })()`);
  await sleep(300);
  notes.count2 = await countVia(KP0, setup.docId);
  notes.text2 = await noteText();
  notes.checks.push({ label: '>=2 条', count: notes.count2, text: notes.text2 });
  notes.truth = await truth(KP0);
  await shot('05-note-count.png');
  // 小字与「查看笔记」列表条数对齐（同源口径的现场证据）
  const listed = await evalJs(`(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const b = document.getElementById('mmkp-notes');
    if (!b) return { ok:false, why:'没有 #mmkp-notes' };
    b.click();
    await sleep(1500);
    const box = document.getElementById('mmkp-notelist');
    return { ok:true, hits: box ? box.querySelectorAll('.hit').length : -1,
             head: box ? (box.textContent || '').trim().slice(0, 90) : null };
  })()`);
  notes.listed = listed;
  // 收起笔记列表（避免干扰后面的按钮测试）
  await evalJs(`(async () => { const b = document.getElementById('mmkp-notes');
    if (b) b.click(); await new Promise(r => setTimeout(r, 300)); return true; })()`);
  OUT.steps.notes = notes;

  // ---------------- ⑤ 上一知识点 / 下一知识点 ----------------
  const prevNext = { steps: [], disabled: {} };
  const prevBtnState = async () => await evalJs(
    `(() => { const b = document.getElementById('mmkp-prev');
       return b ? { exists:true, disabled: !!b.disabled, dataPrev: b.dataset.prev || '',
                    text: (b.textContent||'').trim(), title: b.title || '' } : { exists:false }; })()`);
  prevNext.initial = { snap: await evalJs('window.__c0056.snap()'),
                       btn: await prevBtnState(), truth: await truth(KP0) };
  // (a) 点一次「上一知识点」（若已是链首则跳过，改由 (b) 覆盖）
  if (prevNext.initial.truth?.prev_id) {
    await evalJs('window.__c0056.click("mmkp-prev")');
    const want = prevNext.initial.truth.prev_id;
    const got = await evalJs(`window.__c0056.waitKp(${JSON.stringify(want)}, 9000)`);
    await sleep(300);
    prevNext.steps.push({ dir: 'prev', want, got,
                          snap: await evalJs('window.__c0056.snap()'),
                          truth: await truth(want) });
    await shot('06-prev-kp.png');
  }
  // (b) 一路「上一知识点」到链首：按钮必须置灰，且再点不动
  let guard = 0;
  while (guard++ < 15) {
    const st = await prevBtnState();
    if (!st.exists || st.disabled) break;
    await evalJs('window.__c0056.click("mmkp-prev")');
    await sleep(900);
  }
  const atHead = { attempts: guard, btn: await prevBtnState(),
                   snap: await evalJs('window.__c0056.snap()') };
  atHead.truth = await truth(atHead.snap.kp);
  const kpBeforeClick = atHead.snap.kp;
  await evalJs('window.__c0056.click("mmkp-prev")');   // 置灰按钮：点了不该有任何反应
  await sleep(900);
  atHead.afterDeadClick = await evalJs('window.__c0056.snap()');
  atHead.stayed = atHead.afterDeadClick.kp === kpBeforeClick;
  prevNext.head = atHead;
  await shot('07-first-kp-disabled.png');
  // (c) 「下一知识点」原有行为：能跳 + 带课件切换
  const beforeNext = await truth(kpBeforeClick);
  if (beforeNext?.next_id) {
    // 先把课件展开，验证"下一知识点"把展开状态带过去（卡 C-0036 的原有行为）
    await evalJs(`(async () => { const b = document.getElementById('mmkp-deck');
      if (b) b.click(); await new Promise(r => setTimeout(r, 900)); return true; })()`);
    const deckOpenBefore = await evalJs(`(() => { const b = document.getElementById('mmkp-slide');
      return !!(b && !b.hidden); })()`);
    await evalJs('window.__c0056.click("mmkp-next")');
    const got = await evalJs(`window.__c0056.waitKp(${JSON.stringify(beforeNext.next_id)}, 9000)`);
    await sleep(1200);
    prevNext.next = { want: beforeNext.next_id, got, deckOpenBefore,
                      deckOpenAfter: await evalJs(`(() => { const b = document.getElementById('mmkp-slide');
                        return !!(b && !b.hidden); })()`),
                      snap: await evalJs('window.__c0056.snap()'),
                      truth: await truth(beforeNext.next_id) };
    await shot('08-next-kp-deck.png');
  }
  OUT.steps.prevNext = prevNext;

  OUT.errorsAfter = OUT.errors.slice();
  return finish(OUT);

  function finish(o) {
    o.ok = !o.fatal;
    if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(o, null, 2), 'utf-8');
    console.log(JSON.stringify({ ok: o.ok, fatal: o.fatal || null,
      mode: MODE, errors: o.errors.length,
      scrollKept: (o.steps.scroll?.top || []).every(s => s.exists && s.display !== 'none'),
      outerMoved: o.steps.scroll ? (o.steps.scroll.outerAfter !== o.steps.scroll.outerBefore) : null,
      noteTexts: (o.steps.notes?.checks || []).map(c => c.text),
      prevBtn: o.steps.prevNext?.initial?.btn?.exists ?? null }, null, 1));
    try { ws.close(); } catch { /* 忽略 */ }
    try { edge.kill(); } catch { /* 忽略 */ }
    return o;
  }
}

main().then(() => process.exit(0)).catch((e) => {
  console.error('探针异常：' + (e && e.stack || e));
  try { writeFileSync(JSONOUT || 'probe_c0056_error.json',
    JSON.stringify({ ok: false, fatal: String(e && e.stack || e) }, null, 2), 'utf-8'); } catch { /* 忽略 */ }
  process.exit(1);
});
