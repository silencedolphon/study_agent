// 卡 C-0056 临时诊断（非交付物）：为什么 preventDefault 没拦住滚动链？
// 实验 A：在 document 上（capture + bubble，passive:false）记录 wheel 事件的
//         target / cancelable / defaultPrevented，看事件到底有没有到弹窗、
//         以及"拦了没有"。
// 实验 B：直接给 .pbody 挂一个**无条件** preventDefault 的 wheel 监听，
//         看外层还会不会被滚（判定"preventDefault 在这台机器/这个 headless 里
//         到底管不管用"）。
// 用法：node tools/_c0056_diag.mjs --port 8899
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const arg = (n, d) => { const i = process.argv.indexOf('--' + n); return i > 0 ? process.argv[i + 1] : d; };
const PORT = arg('port', '8899');
const CDP = parseInt(arg('cdp', '9451'), 10);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const profile = mkdtempSync(join(tmpdir(), 'c0056diag-'));
const edge = spawn(EDGE, ['--headless=new', `--remote-debugging-port=${CDP}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--disable-gpu',
  '--window-size=1200,800', 'about:blank'], { stdio: 'ignore' });

const OUT = { steps: {} };
async function main() {
  let list = null;
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(`http://127.0.0.1:${CDP}/json/list`); list = await r.json();
            if (list?.length) break; } catch { /* wait */ }
    await sleep(250);
  }
  const ws = new WebSocket((list.find(t => t.type === 'page') || list[0]).webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (method, params = {}) => new Promise((res) => {
    const myId = ++id; pending.set(myId, res);
    ws.send(JSON.stringify({ id: myId, method, params })); });
  const js = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.text,
      __d: (r.result.exceptionDetails.exception || {}).description };
    return r.result?.result?.value; };
  const wheel = async (x, y, dy) => { await send('Input.dispatchMouseEvent',
    { type: 'mouseWheel', x: Math.round(x), y: Math.round(y), deltaX: 0, deltaY: dy, pointerType: 'mouse' });
    await sleep(200); };
  const snap = () => js(`(() => { const p = document.querySelector('.evpop');
    const b = p ? p.querySelector('.pbody') : null; const se = document.scrollingElement;
    const r = b ? b.getBoundingClientRect() : null;
    return { kp: p ? (p.dataset.kp||'') : null, display: p ? getComputedStyle(p).display : null,
      bodyTop: b ? Math.round(b.scrollTop) : null, bodyMax: b ? Math.round(b.scrollHeight-b.clientHeight) : null,
      outer: Math.round(se.scrollTop),
      aim: r ? { x: Math.round(r.left + r.width/2), y: Math.round(r.top + Math.min(r.height-10, r.height/2)) } : null,
      ob: b ? getComputedStyle(b).overscrollBehaviorY : null }; })()`);

  await send('Page.enable'); await send('Runtime.enable');
  await send('Page.navigate', { url: `http://127.0.0.1:${PORT}/` });
  await sleep(2500);

  OUT.steps.boot = await js(`(async () => { const s = ms => new Promise(r => setTimeout(r, ms));
    await go('kps');
    for (let i=0;i<80;i++){ await s(150); const x=document.querySelector('#mm-doc'); if (x && x.options.length) return {ok:true}; }
    return {ok:false}; })()`);

  // ---------- 实验 A：记录 wheel 事件属性 ----------
  await js(`(() => {
    window.__diag = [];
    document.addEventListener('wheel', e => {
      const p = document.querySelector('.evpop');
      window.__diag.push({ phase:'capture', target: (e.target.id || e.target.className || e.target.tagName),
        cancelable: e.cancelable, dy: e.deltaY, passiveUnknown: true,
        kp: p ? (p.dataset.kp||'') : null, inPop: !!(p && p.contains(e.target)),
        pbodyTop: (()=>{const b=p&&p.querySelector('.pbody'); return b?Math.round(b.scrollTop):null;})(),
        preventedAtCapture: e.defaultPrevented });
    }, {capture:true, passive:false});
    document.addEventListener('wheel', e => {
      window.__diag.push({ phase:'bubble', cancelable: e.cancelable,
        preventedAtBubble: e.defaultPrevented, dy: e.deltaY });
    }, {passive:false});
    return true; })()`);

  const open = await js(`(async () => { const s = ms => new Promise(r => setTimeout(r, ms));
    await mmJumpToKp(1, 'KP-N02-1', 'KP-N02-1');
    for (let i=0;i<120;i++){ await s(120); const p=document.querySelector('.evpop');
      const b=p&&p.querySelector('#mmkp-body');
      if (p && p.dataset.kp==='KP-N02-1' && b && !/加载中|正在读取/.test(b.innerText||'')) return {ok:true}; }
    return {ok:false}; })()`);
  OUT.steps.open = open;
  await sleep(300);
  await js(`window.scrollTo(0, Math.round((document.scrollingElement.scrollHeight - document.scrollingElement.clientHeight)/2))`);
  await js(`(() => { const b=document.querySelector('.evpop .pbody'); if(b) b.scrollTop = 0; return true; })()`);
  await sleep(200);
  OUT.steps.a_before = await snap();
  const aim = (await snap()).aim;
  await wheel(aim.x, aim.y, -120);
  OUT.steps.a_after = await snap();
  OUT.steps.a_diag = await js('window.__diag');

  // ---------- 实验 B：.pbody 上无条件 preventDefault ----------
  await js(`(() => { if (typeof evPopClose === 'function') evPopClose(); return true; })()`);
  await sleep(200);
  OUT.steps.b_open = await js(`window.__g56b = (async () => { const s = ms => new Promise(r => setTimeout(r, ms));
    await mmJumpToKp(1, 'KP-N02-1', 'KP-N02-1');
    for (let i=0;i<120;i++){ await s(120); const p=document.querySelector('.evpop');
      const b=p&&p.querySelector('#mmkp-body');
      if (p && p.dataset.kp==='KP-N02-1' && b && !/加载中|正在读取/.test(b.innerText||'')) return {ok:true}; }
    return {ok:false}; })()`);
  await js(`(() => { window.__diag = [];
    const b = document.querySelector('.evpop .pbody');
    if (b) b.addEventListener('wheel', e => { window.__diag.push({hard:true, cancelable:e.cancelable});
      e.preventDefault(); }, {passive:false});
    return !!b; })()`);
  await js(`window.scrollTo(0, 1000)`);
  await js(`(() => { const b=document.querySelector('.evpop .pbody'); if(b) b.scrollTop = 0; return true; })()`);
  await sleep(200);
  OUT.steps.b_before = await snap();
  const aim2 = (await snap()).aim;
  await wheel(aim2.x, aim2.y, -120);
  OUT.steps.b_after = await snap();
  OUT.steps.b_diag = await js('window.__diag');

  // ---------- 实验 C：wheel 落在弹窗**外**（对照：应该照旧滚外层） ----------
  OUT.steps.c_before = await snap();
  await wheel(50, 400, -120);
  OUT.steps.c_after = await snap();

  writeFileSync('logs/_c0056_diag.json', JSON.stringify(OUT, null, 2), 'utf-8');
  console.log(JSON.stringify(OUT, null, 1));
  ws.close(); edge.kill();
}
main().catch((e) => { console.error(e); try { edge.kill(); } catch { /* */ } process.exit(1); });
