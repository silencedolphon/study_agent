// 卡 C-0082 真浏览器探针（headless Edge + CDP，零依赖）。
//
// 验的是人拍板的那两件事：
//   A. 「笔记」页签点开一条笔记，看到的是**只有我自己写进去的内容**
//      ——没有 front-matter、没有自动生成的一级标题、没有「## 出处」段；
//   B. 知识点窗口里能**直接改**这条笔记的正文，保存成功、列表就地刷新成新正文。
//
// 用法：
//   node tools/probe_c0082_browser.mjs --port 8896 --doc 55 --note <文件名>
//        --newbody-file <新正文的 UTF-8 文本文件> --json <结果 JSON> --outdir <截图目录>
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const PORT = arg('port', '8896');
const DOC = arg('doc', '');
const NOTE = arg('note', '');
const NEWBODY = arg('newbody-file', '')
  ? readFileSync(arg('newbody-file', ''), 'utf-8') : '';
const JSONOUT = arg('json', '');
const OUTDIR = arg('outdir', '');
const CDP_PORT = parseInt(arg('cdp', '9391'), 10);
const BASE = `http://127.0.0.1:${PORT}`;

const EDGE_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];
const EDGE = EDGE_CANDIDATES.find((p) => existsSync(p));
if (!EDGE) { console.error('找不到 msedge.exe'); process.exit(3); }

const profile = mkdtempSync(join(tmpdir(), 'c0082-edge-'));
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
  const waitFor = async (expr, ms = 20000) => {
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

  const out = { base: BASE, port: PORT, doc: DOC, note: NOTE, newBody: NEWBODY,
                steps: {}, shots: {}, errors: [] };

  await send('Page.navigate', { url: `${BASE}/` });
  await sleep(2400);
  await waitFor(`typeof go === 'function'`, 20000);
  await sleep(800);

  // ---------------- A. 笔记页签：只显示人写的内容 ----------------
  await evalJs(`(async () => { try { await go('notes'); return 'ok'; }
                               catch(e){ return 'ERR:'+e.message; } })()`);
  await sleep(1600);
  await waitFor(`document.querySelectorAll('#view a[data-note]').length > 0`, 15000);

  out.steps.list = await evalJs(`(() => {
    const as = [...document.querySelectorAll('#view a[data-note]')];
    const mine = as.find(a => (a.dataset.note || '') === ${JSON.stringify(NOTE)});
    return { total: as.length, found: !!mine,
             names: as.map(a => a.dataset.note).slice(0, 12) };
  })()`);

  out.steps.opened = await evalJs(`(async () => {
    const as = [...document.querySelectorAll('#view a[data-note]')];
    const a = as.find(x => (x.dataset.note || '') === ${JSON.stringify(NOTE)});
    if (!a) return { ok: false, reason: '笔记页签里没有这条笔记' };
    a.click();
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 200));
      const el = document.querySelector('.modal .note-body');
      if (el) {
        const t = el.innerText;
        const lines = t.split('\\n').map(s => s.trim());
        return { ok: true, text: t, html: el.innerHTML.slice(0, 400),
                 hasFmMarker: lines.includes('---'),
                 hasHeading: lines.some(s => s.startsWith('# ')),
                 hasRefHead: lines.includes('## 出处'),
                 rawText: el.textContent };
      }
    }
    return { ok: false, reason: '点开后没出现 .note-body' };
  })()`);
  out.shots.noteTab = await shot('1-note-tab.png');
  await evalJs(`(() => { if (typeof closeModal === 'function') closeModal(); return true; })()`);
  await sleep(400);

  // ---------------- B. 知识点窗口里改笔记 ----------------
  await evalJs(`(async () => { try { await go('kps'); return 'ok'; }
                               catch(e){ return 'ERR:'+e.message; } })()`);
  await waitFor(`document.querySelector('#mm-doc') && document.querySelector('#mm-tree')`,
                20000);
  await sleep(900);

  out.steps.pickDoc = await evalJs(`(async () => {
    const sel = document.querySelector('#mm-doc');
    if (!sel) return { ok: false, reason: '没有 #mm-doc' };
    const opt = [...sel.options].find(o => o.value === ${JSON.stringify(DOC)});
    if (!opt) return { ok: false, have: [...sel.options].map(o => o.value) };
    sel.value = opt.value;
    sel.dispatchEvent(new Event('change'));
    for (let i = 0; i < 80; i++) {
      await new Promise(r => setTimeout(r, 250));
      if (document.querySelectorAll('#mm-tree .mmname.has-kp').length)
        return { ok: true, label: opt.textContent.trim(),
                 kpNodes: document.querySelectorAll('#mm-tree .mmname.has-kp').length };
    }
    return { ok: false, reason: '树没渲染出 has-kp 节点' };
  })()`);

  out.steps.popup = await evalJs(`(async () => {
    const el = [...document.querySelectorAll('#mm-tree .mmname.has-kp')][0];
    if (!el) return { ok: false, reason: '没有 .has-kp 节点' };
    el.click();
    for (let i = 0; i < 70; i++) {
      await new Promise(r => setTimeout(r, 220));
      const pop = document.querySelector('.evpop');
      if (pop && pop.innerText.indexOf('加载中') < 0 && pop.innerText.length > 80
          && pop.querySelector('#mmkp-notes'))
        return { ok: true, clicked: el.textContent.trim(),
                 hasSeeNotes: !!pop.querySelector('#mmkp-notes'),
                 hasEditBox: !!pop.querySelector('#mmkp-noteedit'),
                 hasListBox: !!pop.querySelector('#mmkp-notelist') };
    }
    return { ok: false, reason: '知识点弹窗没出内容' };
  })()`);
  out.shots.popup = await shot('2-kp-popup.png');

  // 点「查看笔记」
  out.steps.openList = await evalJs(`(async () => {
    const b = document.querySelector('#mmkp-notes');
    if (!b) return { ok: false, reason: '没有 #mmkp-notes' };
    b.click();
    // 这条知识点上可能已经有好几条笔记（真人写的），所以按**文件名**认自己那条
    const mine = () => [...document.querySelectorAll('#mmkp-notelist .hit')]
      .find(h => (h.innerText || '').indexOf(${JSON.stringify(NOTE)}) >= 0);
    for (let i = 0; i < 80; i++) {
      await new Promise(r => setTimeout(r, 220));
      const box = document.querySelector('#mmkp-notelist');
      if (box && !box.hidden && box.querySelector('.hit')) {
        const m = mine();
        return { ok: true, hits: box.querySelectorAll('.hit').length,
                 mineFound: !!m,
                 pre: m ? (m.querySelector('pre') || {}).innerText || '' : '',
                 hasEditBtn: !!(m && m.querySelector('button[data-mmkpedit]')),
                 editBtnText: m ? ((m.querySelector('button[data-mmkpedit]') || {})
                                     .textContent || '') : '' };
      }
    }
    return { ok: false, reason: '笔记列表没出来',
             html: (document.querySelector('#mmkp-notelist') || {}).innerHTML || '' };
  })()`);
  out.shots.noteList = await shot('3-kp-note-list.png');

  // 点「编辑正文」→ 进编辑态（并断言列表已收起、拿到的是全文）
  out.steps.enterEdit = await evalJs(`(async () => {
    const m = [...document.querySelectorAll('#mmkp-notelist .hit')]
      .find(h => (h.innerText || '').indexOf(${JSON.stringify(NOTE)}) >= 0);
    const b = m ? m.querySelector('button[data-mmkpedit]') : null;
    if (!b) return { ok: false, reason: '没有找到自己那条的「编辑正文」按钮' };
    b.click();
    for (let i = 0; i < 80; i++) {
      await new Promise(r => setTimeout(r, 220));
      const ta = document.querySelector('#mmkp-noteedit #mmkp-notebody');
      const list = document.querySelector('#mmkp-notelist');
      if (ta) return { ok: true, tag: ta.tagName,
                       valueLen: ta.value.length,
                       value: ta.value,
                       listHidden: !!(list && list.hidden),
                       listEmptied: !!(list && list.innerHTML === ''),
                       saveText: (document.querySelector('#mmkp-notesave') || {})
                                   .textContent || '' };
    }
    return { ok: false, reason: '编辑框没出来',
             html: (document.querySelector('#mmkp-noteedit') || {}).innerHTML || '' };
  })()`);
  out.shots.editBox = await shot('4-kp-note-edit.png');

  // 改内容 → 保存
  out.steps.saved = await evalJs(`(async () => {
    const ta = document.querySelector('#mmkp-notebody');
    if (!ta) return { ok: false, reason: '没有编辑框' };
    ta.value = ${JSON.stringify(NEWBODY)};
    ta.dispatchEvent(new Event('input'));
    const b = document.querySelector('#mmkp-notesave');
    if (!b) return { ok: false, reason: '没有保存按钮' };
    b.click();
    const first = ${JSON.stringify((NEWBODY.split('\n')[0] || '').trim())};
    for (let i = 0; i < 100; i++) {
      await new Promise(r => setTimeout(r, 250));
      const box = document.querySelector('#mmkp-notelist');
      const edit = document.querySelector('#mmkp-noteedit');
      const m = box ? [...box.querySelectorAll('.hit')]
        .find(h => (h.innerText || '').indexOf(${JSON.stringify(NOTE)}) >= 0) : null;
      if (box && !box.hidden && m && (m.innerText || '').indexOf(first) >= 0)
        return { ok: true,
                 editClosed: !!(edit && edit.hidden),
                 preText: (m.querySelector('pre') || {}).innerText || '',
                 hitCount: box.querySelectorAll('.hit').length };
      if (edit && !edit.hidden) {
        const msg = document.querySelector('#mmkp-notemsg');
        if (msg && (msg.textContent || '').indexOf('保存失败') >= 0)
          return { ok: false, reason: '保存失败提示：' + msg.textContent };
      }
    }
    return { ok: false, reason: '保存后列表没刷出新正文',
             listHtml: (document.querySelector('#mmkp-notelist') || {}).innerHTML || '',
             editHtml: (document.querySelector('#mmkp-noteedit') || {}).innerHTML || '' };
  })()`);
  out.shots.afterSave = await shot('5-kp-after-save.png');

  out.errors = errors;
  if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(out, null, 2), 'utf-8');

  console.log('笔记页签条目数 = ' + (out.steps.list?.total ?? '?')
    + '，找到本次笔记 = ' + (out.steps.list?.found ? 'yes' : 'no'));
  console.log('笔记页签正文 = ' + JSON.stringify((out.steps.opened?.text || '').slice(0, 60)));
  console.log('知识点窗口改笔记 = ' + (out.steps.saved?.ok ? 'ok' : 'FAILED')
    + '，保存后列表正文 = '
    + JSON.stringify((out.steps.saved?.preText || '').slice(0, 60)));
  console.log('页面错误数 = ' + errors.length);
  if (OUTDIR) console.log('截图：' + OUTDIR);
  ws.close();
  edge.kill();
  process.exit(out.steps.opened?.ok && out.steps.saved?.ok ? 0 : 1);
}

main().catch((e) => {
  console.error('探针失败：' + e.message);
  try { edge.kill(); } catch { /* ignore */ }
  process.exit(1);
});
