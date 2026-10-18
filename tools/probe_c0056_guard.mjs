// 卡 C-0056：知识点弹窗三项修复的**真浏览器**探针（headless Edge + CDP）。
//
// 与同目录 `probe_c0056_browser.mjs`（他人预置的那份）**各测各的、互不依赖**：
// 这份的特点是把"滚动穿透"当成一次**红/绿对照实验**来做——
//   · 先把外层页面滚到中间（外层本来在顶部时，"外层没被滚"是判不出来的）；
//   · 把正文滚到最顶后**继续向上**滚 N 次、滚到最底后**继续向下**滚 N 次，
//     每一拍都记下：弹窗在不在（exists/display/dataset.kp）、正文 scrollTop、
//     外层 scrollTop；任何一拍弹窗没了就停手并记 lost。
//   · `--mode red` 用未修复的基线跑同一套动作，用来证明"这个测试真能测出病"。
//
// 为什么必须真浏览器 + 真滚轮：`new WheelEvent()` 是合成事件（isTrusted=false），
// 浏览器**不执行默认滚动**，拿它测穿透必然假绿；所以一律走
// `Input.dispatchMouseEvent{type:'mouseWheel'}`（可信输入，走完整滚动链）。
//
// 另外三项：`#mmkp-notecount`（与该知识点真实笔记数逐一对齐）、
// `#mmkp-prev`（链首置灰点不动 / 非链首点击 == 后端 prev_id）、
// `#mmkp-next`（回归：仍能跳转且已展开的课件跟着切）。
//
// ⚠ 零模型调用：只走 /api/mindmap /api/summary-kp /api/notes/by_kp 这些只读接口。
//
// 用法：
//   node tools/probe_c0056_guard.mjs --port 8899 --doc 1 --kp KP-N02-1 --kp2 KP-N05-1 \
//        --n2 KP-N24-1 --n1 KP-N29-1 --n0 KP-N54-1 --mode green \
//        --outdir <截图目录> --json <结果 JSON> [--lean]
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const PORT = arg('port', '8899');
const OUTDIR = arg('outdir', '');
const JSONOUT = arg('json', '');
const DOC = parseInt(arg('doc', '1'), 10);
const KP = arg('kp', '');            // 主测：链首知识点（prev 应为空）
const KP2 = arg('kp2', '');          // 次测：prev 不为空的那条
const KP_N2 = arg('n2', '');         // 笔记 >= 2 条
const KP_N1 = arg('n1', '');         // 笔记 1 条
const KP_N0 = arg('n0', '');         // 笔记 0 条
const MODE = arg('mode', 'green');   // green=当前代码 / red=未修复基线
const LEAN = process.argv.includes('--lean');
const CDP_PORT = parseInt(arg('cdp', MODE === 'red' ? '9431' : '9432'), 10);
const BASE = `http://127.0.0.1:${PORT}`;
const UPS = parseInt(arg('ups', '10'), 10);
const DOWNS = parseInt(arg('downs', '10'), 10);

const profile = mkdtempSync(join(tmpdir(), 'c0056g-edge-'));
const edge = spawn(EDGE, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-gpu', '--window-size=1200,800', 'about:blank',
], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (OUTDIR) mkdirSync(OUTDIR, { recursive: true });
  const OUT = { base: BASE, port: PORT, mode: MODE, doc: DOC, lean: LEAN,
                errors: [], shots: {}, steps: {} };

  let list = null;
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
            list = await r.json(); if (list?.length) break; } catch { /* 还没起来 */ }
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
      const t = m.params.entry.text || '', u = m.params.entry.url || '';
      if (!/favicon/i.test(t) && !/favicon/i.test(u)) OUT.errors.push(u ? `${t} @ ${u}` : t);
    } else if (m.method === 'Runtime.exceptionThrown') {
      OUT.errors.push(m.params?.exceptionDetails?.text || 'exception');
    }
  };
  const send = (method, params = {}) => new Promise((res) => {
    const myId = ++id; pending.set(myId, res);
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
    if (!OUTDIR || LEAN) return null;
    const p = join(OUTDIR, name);
    const s = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    if (s.result?.data) { writeFileSync(p, Buffer.from(s.result.data, 'base64')); OUT.shots[name] = p; return p; }
    return null;
  };
  // ★ 真滚轮：可信输入，浏览器会走完整的滚动链（合成事件不会）
  const wheel = async (x, y, dy) => {
    await send('Input.dispatchMouseEvent',
      { type: 'mouseWheel', x: Math.round(x), y: Math.round(y), deltaX: 0,
        deltaY: Math.round(dy), pointerType: 'mouse' });
    await sleep(LEAN ? 70 : 120);
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable').catch(() => {});
  await send('Page.navigate', { url: `${BASE}/` });
  await sleep(LEAN ? 2200 : 2600);

  const inj = await evalJs(`(() => {
    const q = s => document.querySelector(s);
    const pop = () => q('.evpop');
    const se = () => document.scrollingElement || document.documentElement;
    const bodyEl = () => { const p = pop(); return p ? p.querySelector('.pbody') : null; };
    window.__g56 = {
      q, pop, se, bodyEl,
      snap(){
        const p = pop(), b = bodyEl(), el = q('#mmkp-notecount');
        const r = b ? b.getBoundingClientRect() : null;
        return {
          exists: !!p, display: p ? getComputedStyle(p).display : null,
          visible: !!(p && getComputedStyle(p).display !== 'none'),
          kp: p ? (p.dataset.kp || '') : '',
          bodyTop: b ? Math.round(b.scrollTop) : null,
          bodyMax: b ? Math.round(b.scrollHeight - b.clientHeight) : null,
          bodyClient: b ? b.clientHeight : null,
          outer: Math.round(se().scrollTop),
          outerMax: Math.round(se().scrollHeight - se().clientHeight),
          // 滚轮落点：正文区中心（保证命中 .pbody 或其后代，而不是弹窗外）
          aim: r ? { x: Math.round(r.left + Math.min(r.width - 20, r.width / 2)),
                     y: Math.round(r.top + Math.min(r.height - 10, r.height / 2)) } : null,
          note: el ? (el.textContent || '').trim() : null,
          noteHidden: el ? !!el.hidden : null,
          overscroll: b ? getComputedStyle(b).overscrollBehaviorY : null,
          prev: (() => { const x = q('#mmkp-prev');
            return x ? { has:true, disabled: !!x.disabled, dataPrev: x.dataset.prev || '',
                         text: (x.textContent||'').trim(), title: x.title || '' } : { has:false }; })(),
          next: (() => { const x = q('#mmkp-next');
            return x ? { has:true, disabled: !!x.disabled, dataNext: x.dataset.next || '',
                         text: (x.textContent||'').trim() } : { has:false }; })(),
        };
      },
      setBody(top){ const b = bodyEl(); if (b) b.scrollTop = top; return b ? Math.round(b.scrollTop) : -1; },
      setOuter(top){ se().scrollTop = top; return Math.round(se().scrollTop); },
      async open(kpId){
        if (typeof evPopClose === 'function') evPopClose();
        await new Promise(r => setTimeout(r, 120));
        await mmJumpToKp(${DOC}, kpId, kpId);
        for (let i = 0; i < 120; i++){
          const p = pop(), b = p ? p.querySelector('#mmkp-body') : null;
          if (p && p.dataset.kp === kpId && b &&
              !/加载中|正在读取知识点总结/.test(b.innerText || '')) return { ok:true, kp:p.dataset.kp };
          await new Promise(r => setTimeout(r, 120));
        }
        const p = pop(), b = p ? p.querySelector('#mmkp-body') : null;
        return { ok:false, kp: p ? (p.dataset.kp || '') : '',
                 seen: b ? (b.innerText || '').replace(/\\s+/g,' ').slice(0, 100) : null };
      },
      async clickWait(idName, kpId, ms){
        const el = document.getElementById(idName);
        if (!el) return { ok:false, why:'no #' + idName };
        if (el.disabled) return { ok:false, why:'disabled', before: (pop()||{}).dataset?.kp || '' };
        const before = (pop() || { dataset: {} }).dataset.kp || '';
        el.click();
        const t0 = Date.now();
        while (Date.now() - t0 < (ms || 9000)){
          await new Promise(r => setTimeout(r, 120));
          const p = pop(), b = p ? p.querySelector('#mmkp-body') : null;
          if (p && p.dataset.kp && p.dataset.kp !== before && (!kpId || p.dataset.kp === kpId) &&
              b && !/加载中|正在读取知识点总结/.test(b.innerText || '')) {
            return { ok:true, before, after: p.dataset.kp };
          }
        }
        const p = pop();
        return { ok:false, why:'没跳过去', before, after: p ? (p.dataset.kp || '') : '' };
      },
      deckOpen(){ const b = document.getElementById('mmkp-slide'); return !!(b && !b.hidden); },
      async deckToggle(on){
        const b = document.getElementById('mmkp-deck');
        if (!b) return { ok:false };
        if (this.deckOpen() !== !!on) { b.click(); await new Promise(r => setTimeout(r, 1200)); }
        return { ok:true, open: this.deckOpen(), imgSrc: (() => { const i = q('#mmkp-figbox img'); return i ? i.getAttribute('src') : ''; })() };
      },
    };
    return { ok:true, hasJump: typeof mmJumpToKp === 'function' };
  })()`);
  OUT.steps.inject = inj;
  if (!inj?.ok || !inj.hasJump) { OUT.fatal = '页面函数没挂上（mmJumpToKp 不在）'; return finish(); }

  const truth = async (kpId) => await evalJs(
    `fetch('/api/summary-kp?doc_id=${DOC}&kp_id=' + encodeURIComponent(${JSON.stringify(kpId)}))
      .then(r => r.json()).then(d => ({ kp_id: d.kp_id, name: d.name, error: d.error || null,
        prev_id: d.prev_id, prev_name: d.prev_name, next_id: d.next_id, next_name: d.next_name,
        order_index: d.order_index, order_total: d.order_total,
        note_count: d.note_count === undefined ? null : d.note_count }))`);
  const notesApi = async (kpId) => await evalJs(
    `fetch('/api/notes/by_kp?kp_id=' + encodeURIComponent(${JSON.stringify(kpId)}) + '&doc_id=${DOC}')
      .then(r => r.json()).then(d => (d.notes || []).length)`);

  // ---------- [0] 挂页签 + 无弹窗时自证滚轮有效 ----------
  const boot = await evalJs(`(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    if (typeof go !== 'function') return { ok:false, why:'no go()' };
    await go('kps');
    for (let i = 0; i < 80; i++){ await sleep(150);
      const s = document.querySelector('#mm-doc');
      if (s && s.options.length) return { ok:true, nOpts: s.options.length, sel: s.value }; }
    return { ok:false, why:'#mm-doc 没出来' };
  })()`);
  OUT.steps.boot = boot;
  if (!boot?.ok) { OUT.fatal = 'boot: ' + boot?.why; return finish(); }
  const p0 = await evalJs('window.__g56.snap()');
  await evalJs(`window.__g56.setOuter(Math.round(window.__g56.snap().outerMax / 2))`);
  const p1 = await evalJs('window.__g56.snap()');
  // 三拍真滚轮，看外层动不动（不动就说明这机器上滚轮没生效，后面的"没被穿透"不作数）
  for (let i = 0; i < 3; i++) await wheel(200, 300, 120);
  const p2 = await evalJs('window.__g56.snap()');
  OUT.steps.wheelSelfProof = { outerMax: p0.outerMax, afterSet: p1.outer, afterWheel: p2.outer,
                               moved: p2.outer !== p1.outer };

  // ---------- [1] 打开主测知识点 ----------
  // ★ 顺序是有讲究的：**先把外层滚到中间、再开弹窗**。
  //   反过来（开了弹窗再去滚外层）那一下就会被 evPopScroll 判成"窗外的滚动"
  //   把弹窗关掉——那正是本卡要修的病；于是后面的滚动测试全在"弹窗已经没了"
  //   的状态下做，测了个寂寞（本探针第一版就踩了这个坑，第一拍就 display:none）。
  const open1 = await evalJs(`window.__g56.open(${JSON.stringify(KP)})`);
  await sleep(250);
  const openSnap = await evalJs('window.__g56.snap()');
  OUT.steps.open = { res: open1, snap: openSnap, truth: await truth(KP) };
  await shot('c0056g-1-open.png');
  if (!open1?.ok) { OUT.fatal = '打不开主测知识点 ' + KP; return finish(); }
  if (!openSnap.visible || !openSnap.aim) {
    OUT.fatal = '弹窗打开后不可见（display=' + openSnap.display + '）——没法测滚动';
    return finish();
  }

  // ---------- [2] 连续滚动超界（核心） ----------
  // 先把折叠区展开：正文只有几十像素时，"到顶后继续滚"说明不了什么；
  // 展开「总结正文 · 出处明细 · 生成链条」后正文才有几百像素可滚。
  OUT.steps.growForRoll = await evalJs(`(async () => {
    const m = document.getElementById('mmkp-more');
    if (m && !m.open) { m.open = true; await new Promise(r => setTimeout(r, 450)); }
    const b = window.__g56.bodyEl();
    return { more: !!(m && m.open), max: b ? Math.round(b.scrollHeight - b.clientHeight) : null };
  })()`);
  const roll = { up: [], down: [], outerBefore: null, outerAfter: null };
  const start = await evalJs('window.__g56.snap()');
  roll.outerBefore = start.outer;
  roll.outerMax = start.outerMax;
  roll.bodyMax = start.bodyMax;
  roll.noteText = start.note;
  roll.overscroll = start.overscroll;
  // (a) 到顶后继续向上滚
  await evalJs('window.__g56.setBody(0)');
  for (let i = 0; i < UPS; i++) {
    const s = await evalJs('window.__g56.snap()');
    if (!s.aim) { roll.up.push({ i, lost: true, snap: s }); break; }
    await wheel(s.aim.x, s.aim.y, -120);
    const s2 = await evalJs('window.__g56.snap()');
    roll.up.push({ i, exists: s2.exists, visible: s2.visible, display: s2.display,
                   kp: s2.kp, bodyTop: s2.bodyTop, outer: s2.outer });
    if (!(s2.exists && s2.visible)) break;
  }
  const midSnap = await evalJs('window.__g56.snap()');
  roll.kpAfterUp = midSnap.kp;
  await shot('c0056g-2-after-up-overscroll.png');
  // (b) 到底后继续向下滚
  await evalJs('window.__g56.setBody(1e9)');
  const atBottom = await evalJs('window.__g56.snap()');
  roll.reachedBottom = atBottom.bodyTop !== null && atBottom.bodyMax !== null &&
                       atBottom.bodyTop >= atBottom.bodyMax - 2;
  for (let i = 0; i < DOWNS; i++) {
    const s = await evalJs('window.__g56.snap()');
    if (!s.aim) { roll.down.push({ i, lost: true, snap: s }); break; }
    await wheel(s.aim.x, s.aim.y, 120);
    const s2 = await evalJs('window.__g56.snap()');
    roll.down.push({ i, exists: s2.exists, visible: s2.visible, display: s2.display,
                     kp: s2.kp, bodyTop: s2.bodyTop, outer: s2.outer });
    if (!(s2.exists && s2.visible)) break;
  }
  const final = await evalJs('window.__g56.snap()');
  roll.outerAfter = final.outer;
  roll.final = { exists: final.exists, visible: final.visible, display: final.display, kp: final.kp };
  roll.survived = !!(final.exists && final.visible && final.kp === KP);
  roll.outerUnchanged = final.outer === roll.outerBefore;
  // ★ 只看首尾是不够的：向上滚 -120 与向下滚 +120 会互相抵消（基线实测就是
  //   3811→(3691)→3811，首尾相同但外层**确实被滚过**）。所以逐拍比对，
  //   任何一拍的外层位置偏离起点就算"被穿透"。
  roll.outerTouched = [...roll.up, ...roll.down]
    .some(x => x.outer !== undefined && x.outer !== roll.outerBefore);
  roll.outerSamples = [...new Set([...roll.up, ...roll.down]
    .map(x => x.outer).filter(v => v !== undefined))];
  OUT.steps.roll = roll;
  await shot('c0056g-3-after-down-overscroll.png');

  // ---------- [3] 正文还能正常滚到首尾（别把滚动一起截死） ----------
  // 先把内容撑长（展开「总结正文·出处明细」+ 展开当页课件图）再测：
  // 内容只有几十像素时"能滚到首尾"说服力不够，人真实读的正是这种长内容。
  const grew = await evalJs(`(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const m = document.getElementById('mmkp-more');
    if (m && !m.open) { m.open = true; await sleep(400); }
    const d = await window.__g56.deckToggle(true);
    await sleep(1200);
    const b = window.__g56.bodyEl();
    return { more: !!(m && m.open), deck: d, max: b ? Math.round(b.scrollHeight - b.clientHeight) : null };
  })()`);
  OUT.steps.grow = grew;
  const canScroll = await evalJs(`(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const b = window.__g56.bodyEl();
    if (!b) return { ok:false, why:'.pbody 不在（弹窗被关了？）' };
    b.scrollTop = 0; await sleep(100);
    const top = b.scrollTop;
    b.scrollTop = b.scrollHeight; await sleep(100);
    const bottom = b.scrollTop;
    const max = b.scrollHeight - b.clientHeight;
    return { ok:true, top: Math.round(top), bottom: Math.round(bottom), max: Math.round(max),
             client: b.clientHeight, scrollH: b.scrollHeight, canScroll: max > 20,
             reachedTop: Math.round(top) === 0, reachedBottom: Math.round(bottom) >= Math.round(max) - 2 };
  })()`);
  OUT.steps.canScroll = canScroll;
  // 收起课件图，回到"只有正文"的形状（后面还要用它测按钮，别让宽版布局干扰）
  await evalJs(`(async () => { await window.__g56.deckToggle(false);
    const m = document.getElementById('mmkp-more'); if (m) m.open = false;
    await new Promise(r => setTimeout(r, 300)); return true; })()`);

  // ---------- [4] 笔记数小字：0 / 1 / ≥2 三情形 ----------
  // 顺手拿一个硬证据：这行小字的数字**没有**额外发一次请求（hook 住 fetch 数请求）。
  await evalJs(`(() => { window.__reqs = []; const of = window.fetch;
    window.fetch = function(){ try { window.__reqs.push(String(arguments[0])); } catch(e) {}
      return of.apply(this, arguments); }; return true; })()`);
  const notes = { cases: [] };
  for (const [tag, kp] of [['n0', KP_N0], ['n1', KP_N1], ['n2', KP_N2]]) {
    if (!kp) continue;
    const t = await truth(kp);
    if (t?.error) { notes.cases.push({ tag, kp, truthError: t.error }); continue; }
    const apiN = await notesApi(kp);          // 先算，别混进"打开弹窗"的请求统计
    await evalJs('window.__reqs = []');
    const op = await evalJs(`window.__g56.open(${JSON.stringify(kp)})`);
    await sleep(200);
    const s = await evalJs('window.__g56.snap()');
    const reqs = (await evalJs('window.__reqs')) || [];
    notes.cases.push({ tag, kp, truthNotes: t.note_count, apiNotes: apiN,
                       opened: !!op?.ok, uiNote: s.note, uiNoteHidden: s.noteHidden,
                       uiKp: s.kp, snap: s, reqs,
                       askedNotesApi: reqs.some(u => String(u).includes('/api/notes/by_kp')),
                       askedSummary: reqs.some(u => String(u).includes('/api/summary-kp')) });
    if (tag === 'n2') await shot('c0056g-4-note-2.png');
    if (tag === 'n0') await shot('c0056g-5-note-0.png');
  }
  OUT.steps.notes = notes;

  // ---------- [5] 上一知识点 / 下一知识点 ----------
  const pn = {};
  // (a) 链首：prev 置灰 + 点了不动（用主测知识点：它的 prev 应为空）
  const head = await evalJs(`window.__g56.open(${JSON.stringify(KP)})`);
  await sleep(200);
  const headSnap = await evalJs('window.__g56.snap()');
  const deadClick = await evalJs(`window.__g56.clickWait('mmkp-prev', null, 1200)`);
  await sleep(300);
  const headSnap2 = await evalJs('window.__g56.snap()');
  pn.head = { opened: !!head?.ok, prev: headSnap.prev, next: headSnap.next,
              truth: await truth(KP), deadClick,
              stillSameKp: headSnap2.kp === headSnap.kp, stillVisible: headSnap2.visible };
  await shot('c0056g-6-head-disabled.png');
  // (b) 非链首：点 prev 应跳到后端给的 prev_id
  if (KP2) {
    const t2 = await truth(KP2);
    const o2 = await evalJs(`window.__g56.open(${JSON.stringify(KP2)})`);
    await sleep(200);
    const s2 = await evalJs('window.__g56.snap()');
    const click = await evalJs(`window.__g56.clickWait('mmkp-prev', ${JSON.stringify(t2?.prev_id || '')})`);
    await sleep(250);
    const s3 = await evalJs('window.__g56.snap()');
    pn.prevClick = { opened: !!o2?.ok, truth2: t2, btnBefore: s2.prev, btnText: s2.prev.text,
                     want: t2?.prev_id || '', got: s3.kp, click, visible: s3.visible,
                     truthAfter: await truth(s3.kp) };
    await shot('c0056g-7-prev-clicked.png');
  }
  // (c) 「下一知识点」回归：能跳 + 已展开的课件跟着切
  const nextInfo = { before: null, deckOn: null, click: null, after: null };
  const o3 = await evalJs(`window.__g56.open(${JSON.stringify(KP)})`);
  await sleep(200);
  const t3 = await truth(KP);
  nextInfo.before = t3;
  nextInfo.deckOn = await evalJs(`window.__g56.deckToggle(true)`);
  const nc = await evalJs(`window.__g56.clickWait('mmkp-next', ${JSON.stringify(t3?.next_id || '')})`);
  await sleep(900);
  const s4 = await evalJs('window.__g56.snap()');
  nextInfo.click = nc;
  nextInfo.after = { kp: s4.kp, visible: s4.visible,
                     deckStillOpen: await evalJs('window.__g56.deckOpen()'),
                     deckFlag: await evalJs(`(window.__g56.pop() || {dataset:{}}).dataset.deck`),
                     imgSrc: await evalJs(`(() => { const i = window.__g56.q('#mmkp-figbox img'); return i ? i.getAttribute('src') : ''; })()`),
                     truth: await truth(s4.kp) };
  pn.next = nextInfo;
  OUT.steps.prevNext = pn;
  await shot('c0056g-8-next-clicked.png');

  OUT.done = true;
  return finish();

  function finish() {
    OUT.ok = !OUT.fatal;
    if (JSONOUT) { try { writeFileSync(JSONOUT, JSON.stringify(OUT, null, 2), 'utf-8'); } catch { /* 忽略 */ } }
    const roll2 = OUT.steps.roll || {};
    console.log(JSON.stringify({ ok: OUT.ok, fatal: OUT.fatal || null, mode: MODE,
      errors: OUT.errors.length,
      survived: roll2.survived ?? null, outerUnchanged: roll2.outerUnchanged ?? null,
      upKept: (roll2.up || []).every(x => x.exists && x.visible),
      downKept: (roll2.down || []).every(x => x.exists && x.visible),
      notes: (OUT.steps.notes?.cases || []).map(c => `${c.tag}:${c.uiNote}`),
      prevHead: OUT.steps.prevNext?.head?.prev?.disabled ?? null,
      prevClick: OUT.steps.prevNext?.prevClick?.got ?? null,
      nextClick: OUT.steps.prevNext?.next?.after?.kp ?? null }, null, 1));
    try { ws.close(); } catch { /* 忽略 */ }
    try { edge.kill(); } catch { /* 忽略 */ }
  }
}

main().then(() => process.exit(0)).catch((e) => {
  console.error('探针异常：' + ((e && e.stack) || e));
  try { writeFileSync(JSONOUT || 'probe_c0056_guard_error.json',
    JSON.stringify({ ok: false, fatal: String((e && e.stack) || e) }, null, 2), 'utf-8'); } catch { /* 忽略 */ }
  process.exit(1);
});
