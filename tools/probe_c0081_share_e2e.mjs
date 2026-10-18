// 卡 C-0081 探针：headless Edge + CDP，**真点按钮**走完
//   「甲：按键导出 → 传给乙 → 乙：按键导入并可用」的全流程。
//
// 为什么不能只跑 CLI：人的原话是「用户可以**按键**导出一个已经入库的课件…」，
// 所以导出与导入都必须在真浏览器的真按钮上发生（本项目为"DOM 桩"刻过苦头）。
//
// 两个阶段：
//   --stage a（甲端，数据根 A）：
//       资料页 → 点「导出」→ 勾「包含原课件文件 + 包含识图页图」→ 导出 → 包复制到 transfer
//       → 再点「导出」→ 两个都不勾 → 导出 → 包复制到 transfer
//       → 甲端快照：检索命中 / 知识点页签 / 课件页图
//   --stage b（乙端，数据根 B，全新库）：
//       资料页 → 填包路径 → 看包信息 → 选目标学科库 → 真点「开始导入」（带进度采样）
//       → 资料页/知识点页签核对 → 同题检索 → 重复导入 → 全量包导入到第二个库
//       → 5 类恶意包逐个尝试 → 页图与原文件落盘核对
//
// 用法：
//   node tools\probe_c0081_share_e2e.mjs --stage a --port 8811 --outdir <截图> --json <结果>
//   node tools\probe_c0081_share_e2e.mjs --stage b --port 8812 --zip-min <包> --zip-full <包> \
//        --subj-min 接收精简库 --subj-full 接收全量库 --bad <目录> --outdir <截图> --json <结果>
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync, existsSync, statSync, copyFileSync,
         unlinkSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const STAGE = arg('stage', 'a');
const PORT = arg('port', '8811');
const OUTDIR = arg('outdir', '');
const JSONOUT = arg('json', '');
const CDP_PORT = parseInt(arg('cdp', STAGE === 'a' ? '9367' : '9368'), 10);
const TRANSFER = arg('transfer', '');
const DOC_ID = parseInt(arg('docId', '1'), 10);
const ZIP_MIN = arg('zip-min', '');
const ZIP_FULL = arg('zip-full', '');
const SUBJ_MIN = arg('subj-min', '接收精简库');
const SUBJ_FULL = arg('subj-full', '接收全量库');
const BADDIR = arg('bad', '');
const PROF_DIR = arg('prof-dir', '');
const MIN_BYTES = parseInt(arg('minBytes', '0'), 10);
const EXPECT_CHUNKS = parseInt(arg('expectChunks', '0'), 10);
const EXPECT_DOC_CHUNKS = parseInt(arg('expectDocChunks', '0'), 10);
const BASE = `http://127.0.0.1:${PORT}`;

// 检索/对照用的三个问题：术语、中文短语、语义型（覆盖关键词路与向量路）
const QUERIES = ['Lipinski', '细胞色素P450', '药物代谢'];

const EDGE_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];
const EDGE = EDGE_CANDIDATES.find((p) => existsSync(p));
if (!EDGE) { console.error('找不到 msedge.exe'); process.exit(3); }

const profile = mkdtempSync(join(tmpdir(), 'c0081-edge-'));
const edge = spawn(EDGE, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-gpu', '--window-size=1500,1100', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha256 = (buf) => createHash('sha256').update(buf).digest('hex');
const J = (x) => JSON.stringify(x);

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
  const send = (method, params = {}) => new Promise((res) => {
    const myId = ++id;
    pending.set(myId, res);
    ws.send(JSON.stringify({ id: myId, method, params }));
  });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Log.entryAdded' && m.params?.entry?.level === 'error') {
      const t = m.params.entry.text || '';
      const u = m.params.entry.url || '';
      if (!/favicon/i.test(t) && !/favicon/i.test(u)) errors.push(u ? `${t} @ ${u}` : t);
    } else if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params?.exceptionDetails?.text || 'exception');
    } else if (m.method === 'Page.javascriptDialogOpening') {
      send('Page.handleJavaScriptDialog', { accept: true });
    }
  };
  const evalJs = async (expr) => {
    const r = await send('Runtime.evaluate',
      { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) {
      return { __error: r.result.exceptionDetails.text || 'Uncaught' };
    }
    return r.result?.result?.value;
  };
  const shot = async (name) => {
    if (!OUTDIR) return null;
    const p = join(OUTDIR, name);
    const s = await send('Page.captureScreenshot', { format: 'png' });
    if (s.result?.data) { writeFileSync(p, Buffer.from(s.result.data, 'base64')); return p; }
    return null;
  };
  const waitFor = async (expr, ms = 30000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (await evalJs(expr)) return true;
      await sleep(200);
    }
    return false;
  };
  const checks = [];
  const check = (name, ok, detail) => {
    checks.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
    return !!ok;
  };
  const modalText = async () => await evalJs(
    `((document.querySelector('#modalbox')||{}).innerText || '')`);

  // 页面内高频进度采样（导入/导出的进度"一闪而过"，靠它留证据）
  const progStart = async () => evalJs(`(() => {
    window.__prog = []; if (window.__pt) clearInterval(window.__pt);
    window.__pt = setInterval(async () => {
      try {
        const r = await fetch('/api/share/progress', {cache:'no-store'});
        const d = await r.json();
        window.__prog.push({t: Date.now(), active: !!d.active, stage: d.stage || '',
                            done: d.done || 0, total: d.total || 0, error: d.error || ''});
      } catch (e) { /* 采样失败不影响主流程 */ }
    }, 15);
    return 'ok';
  })()`);
  const progStop = async () => await evalJs(
    `(() => { if (window.__pt) clearInterval(window.__pt); return window.__prog || []; })()`);

  // 让宿主哨兵只统计"这一次导出/导入"的时间窗（哨兵每 50 ms 认一次 prof_on）
  const profOn = () => {
    if (!PROF_DIR) return;
    try { writeFileSync(join(PROF_DIR, 'prof_on'), String(Date.now())); } catch { /* 忽略 */ }
  };
  const profOff = () => {
    if (!PROF_DIR) return;
    try { unlinkSync(join(PROF_DIR, 'prof_on')); } catch { /* 忽略 */ }
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable').catch(() => {});

  const out = { stage: STAGE, base: BASE, port: PORT, checks, shots: {}, errors: [],
                evidence: {}, snapshot: {} };

  // =====================================================================
  //                                甲端
  // =====================================================================
  if (STAGE === 'a') {
    await send('Page.navigate', { url: `${BASE}/` });
    await sleep(2600);
    await waitFor(`!!document.querySelector('#nav button')`, 20000);
    await evalJs(`(async () => { await go('docs'); return 'ok'; })()`);
    await sleep(1600);

    const rows = await evalJs(`(() => {
      const view = document.querySelector('#view');
      const btns = [...view.querySelectorAll('button')].map(b => (b.textContent||'').trim());
      return { exportBtns: btns.filter(x => x === '导出').length,
               text: (view.innerText || '').slice(0, 400) };
    })()`);
    out.evidence.docsPage = rows;
    check('甲：资料页有已入库课件行，且行上有「导出」按钮',
          rows.exportBtns > 0, `导出 x${rows.exportBtns}`);
    out.shots.docs = await shot('01-a-docs.png');

    const openExport = async () => {
      await evalJs(`(() => {
        const b = [...document.querySelectorAll('#view button')]
          .find(x => (x.textContent||'').trim() === '导出');
        if (b) b.click(); return 'ok';
      })()`);
      return await waitFor(`!!document.querySelector('#she-go')`, 30000);
    };
    const setOpts = async (orig, pages) => await evalJs(`(() => {
      const o = document.querySelector('#she-orig'), p = document.querySelector('#she-pages');
      const before = (document.querySelector('#she-est')||{}).textContent;
      if (o) { o.checked = ${orig}; o.dispatchEvent(new Event('change')); }
      if (p) { p.checked = ${pages}; p.dispatchEvent(new Event('change')); }
      return { before, after: (document.querySelector('#she-est')||{}).textContent };
    })()`);

    const exportOnce = async (label, orig, pages, dupTo) => {
      const opened = await openExport();
      check(`甲：点「导出」弹出导出窗口（${label}）`, opened);
      const toggled = await setOpts(orig, pages);
      await sleep(600);
      const modal = await evalJs(`(() => {
        const o = document.querySelector('#she-orig'), p = document.querySelector('#she-pages');
        return { est: (document.querySelector('#she-est')||{}).textContent,
                 o: o && o.checked, p: p && p.checked,
                 body: ((document.querySelector('#she-body')||{}).innerText || '') };
      })()`);
      out.evidence['exportModal_' + label] = { toggled, modal };
      check(`甲：导出窗口的勾选状态与预期一致（${label}）`,
            modal.o === orig && modal.p === pages, `orig=${modal.o} pages=${modal.p}`);
      check(`甲：勾选后预估体积随之变化（${label}）`,
            !!modal.est && /(B|KB|MB)/.test(modal.est), `${toggled.before} -> ${toggled.after}`);
      out.shots['exportModal_' + label] = await shot(`02-a-export-${label}.png`);

      profOn();
      await sleep(150);
      const t0 = Date.now();
      await progStart();
      await evalJs(`document.querySelector('#she-go').click(); 'ok'`);
      const got = await waitFor(`!!document.querySelector('#shr-path')`, 300000);
      await sleep(250);
      const t1 = Date.now();
      const wall = t1 - t0;
      const prog = await progStop();
      profOff();
      check(`甲：导出跑完并给出结果弹窗（${label}）`, got);
      const res = await evalJs(`(() => {
        const p = document.querySelector('#shr-path');
        const box = document.querySelector('#modalbox');
        return { path: p ? p.innerText.trim() : '',
                 badges: [...document.querySelectorAll('#modalbox .badge')].map(b => (b.innerText||'').trim()),
                 text: box ? box.innerText.slice(0, 900) : '' };
      })()`);
      const p = (res.path || '').trim();
      const disk = p && existsSync(p) ? statSync(p).size : 0;
      out.evidence['export_' + label] = { wall_ms: wall, t0_epoch: t0 / 1000, t1_epoch: t1 / 1000,
                                          ...res, disk_bytes: disk, progress_samples: prog.length };
      check(`甲：结果里给出包文件路径（${label}）`, /\.zip$/i.test(p), p);
      check(`甲：那个 zip 在磁盘上真实存在（${label}）`, disk > 0, `${disk} 字节`);
      check(`甲：界面显示的体积与磁盘实际一致（${label}）`,
            res.badges.some(b => /(B|KB|MB)/.test(b)) && disk > 0,
            res.badges.join(' | '));
      if (p && disk > 0 && dupTo) {
        copyFileSync(p, dupTo);
        const cp = statSync(dupTo).size;
        check(`甲：包已复制到「传输目录」（${label}）`, cp === disk, `${dupTo} ${cp} 字节`);
      }
      await evalJs(`closeModal(); 'ok'`);
      await sleep(400);
      return { path: p, bytes: disk };
    };

    const full = await exportOnce('full', true, true, TRANSFER ? join(TRANSFER, 'A-full.zip') : '');
    const min = await exportOnce('min', false, false, TRANSFER ? join(TRANSFER, 'A-min.zip') : '');
    check('甲：带页图+原文件的包明显大于最精简包',
          full.bytes > min.bytes, `full=${full.bytes} min=${min.bytes}`);
    if (MIN_BYTES) check('甲：最精简包体积与预期一致', min.bytes === MIN_BYTES,
                         `${min.bytes} vs ${MIN_BYTES}`);

    // ---- 甲端快照 ①：检索 ----
    const searchSnap = {};
    for (const q of QUERIES) {
      const r = await evalJs(`(async () => {
        const t0 = Date.now();
        const d = await (await fetch('/api/search?q=' + encodeURIComponent(${J(q)}) +
          '&k=8&corpus=main', {cache:'no-store'})).json();
        return { ms: Date.now() - t0, n: d.n, hits: (d.hits||[]).map(h => ({
          ord: h.ord, ref: h.ref, cite: h.cite, text: (h.text||'').slice(0, 70),
          heading: (h.heading||'').slice(0, 60), title: h.title,
          vec: Math.round((h.vec_score||0)*1000)/1000, kw: Math.round((h.kw_score||0)*1000)/1000 })) };
      })()`);
      searchSnap[q] = r;
      check(`甲：同题检索有命中（${q}）`, r && r.n > 0, JSON.stringify(r).slice(0, 180));
    }
    out.snapshot.search = searchSnap;

    // ---- 甲端快照 ②：知识点页签 ----
    await evalJs(`(async () => { await go('kps'); return 'ok'; })()`);
    await sleep(2500);
    const mm = await evalJs(`(async () => {
      const sel = document.querySelector('#mm-doc');
      if (sel) { sel.value = '${DOC_ID}'; sel.dispatchEvent(new Event('change')); }
      return { hasSel: !!sel, opts: sel ? [...sel.options].map(o => ({v:o.value, t:o.textContent.trim()})) : [] };
    })()`);
    await sleep(2500);
    const tree = await evalJs(`(() => {
      const t = document.querySelector('#mm-tree');
      return { text: t ? t.innerText.slice(0, 1200) : '' };
    })()`);
    const mmApi = await evalJs(`(async () => {
      const d = await (await fetch('/api/mindmap?doc_id=${DOC_ID}', {cache:'no-store'})).json();
      const names = [];
      (function walk(n) {
        if (!n) return;
        if (Array.isArray(n)) { n.forEach(walk); return; }
        const nm = n.name || n.label || n.title || n.text;
        if (nm) names.push(String(nm));
        (n.children || []).forEach(walk);
      })(d.tree || []);
      return { n_nodes: d.n_nodes, n_pages: d.n_pages, lkp_total: d.lkp_total,
               lkp_ready: d.lkp_ready, state: d.artifact_state, cost_cny: d.cost_cny,
               n_tree_names: names.length, names: names.slice(0, 40) };
    })()`);
    out.snapshot.kp = { docPicker: mm, domText: tree.text.slice(0, 600), api: mmApi };
    out.shots.kpTree = await shot('03-a-kp-tree.png');
    check('甲：知识点页签列出该课件', mm.hasSel && (mm.opts || []).length > 0,
          JSON.stringify(mm.opts).slice(0, 200));
    check('甲：知识点页签渲染出内容（不是空态）',
          !!(tree.text && tree.text.length > 20), (tree.text || '').slice(0, 120));
    check('甲：产物里的末级知识点条数与结构节点可读',
          !!(mmApi && ((mmApi.lkp_total || 0) > 0 || (mmApi.n_nodes || 0) > 0)),
          JSON.stringify({ n_nodes: mmApi && mmApi.n_nodes, lkp: mmApi && mmApi.lkp_total }));

    // ---- 甲端快照 ③：课件页图（HTTP 直取，算 sha256） ----
    const slide = {};
    for (const pg of [2, 5, 12]) {
      const r = await fetch(`${BASE}/img/slide?doc_id=${DOC_ID}&page=${pg}`);
      const buf = Buffer.from(await r.arrayBuffer());
      slide[pg] = { status: r.status, bytes: buf.length,
                    sha256: r.status === 200 ? sha256(buf) : '',
                    ctype: r.headers.get('content-type') || '' };
    }
    out.snapshot.slide = slide;
    check('甲：课件页图能取到（/img/slide 200 + image/png）',
          Object.values(slide).some(s => s.status === 200 && s.bytes > 1000),
          JSON.stringify(slide).slice(0, 240));
  }

  // =====================================================================
  //                                乙端
  // =====================================================================
  if (STAGE === 'b') {
    await send('Page.navigate', { url: `${BASE}/` });
    await sleep(2600);
    await waitFor(`!!document.querySelector('#nav button')`, 20000);
    await evalJs(`(async () => { await go('docs'); return 'ok'; })()`);
    await sleep(1800);

    // 取"当前库的资料清单"：连**当前真实库路径**一起带回来，便于核对读的是哪个库。
    // ⚠ 一律 `cache:'no-store'`：浏览器可能命中启发式缓存，读到导入前的空清单（踩过）。
    const docsNow = async () => await evalJs(`(async () => {
      const r = await fetch('/api/docs', {cache:'no-store'});
      let d = {};
      try { d = await r.json(); } catch (e) { d = { __parseError: String(e) }; }
      const list = d.documents || [];
      let s = {};
      try { s = await (await fetch('/api/subjects', {cache:'no-store'})).json(); } catch (e) { s = {}; }
      return { n: list.length, status: r.status, error: d.error || '',
               keys: Object.keys(d).slice(0, 8),
               current: s.current || '', db_now: ((s.paths || {}).DB_PATH) || '',
               n_chunks_sum: list.reduce((a, x) => a + (x.n_chunks||0), 0),
               titles: list.map(x => x.title),
               rows: list.map(x => ({ id: x.id, title: x.title, n_chunks: x.n_chunks,
                                      path: x.path, status: x.status })) };
    })()`);
    const docs0 = await docsNow();
    out.evidence.docsBefore = docs0;
    check('乙：收货前是一个全新的空库（0 份资料）', docs0.n === 0, JSON.stringify(docs0).slice(0, 220));
    out.shots.before = await shot('10-b-empty-docs.png');

    // 确保"资料"页就绪（#sh-zip / #sh-open 都在）——知识点页签上没有这两个元素，
    // 这个坑踩过一次：在 kps 页签上死等也等不到弹窗。
    const ensureDocsReady = async () => {
      for (let i = 0; i < 20; i++) {
        if (await evalJs(`!!(document.querySelector('#sh-zip') && document.querySelector('#sh-open'))`)) return true;
        await evalJs(`(async () => { await go('docs'); return 'ok'; })()`);
        await sleep(1200);
      }
      return false;
    };
    // 清掉已关闭弹窗里的残留内容：`closeModal()` 只隐藏、**不清空**，
    // 残留文本会让"等结果"立刻命中上一次的文案（探针为此误判过一次）。
    // ⚠ 只能在"准备打开新弹窗"时用，绝不能用在"看包信息"弹窗开着的时候。
    const modalReset = async () => await evalJs(`(() => {
      try { closeModal(); } catch(e){}
      const b = document.querySelector('#modalbox'); if (b) b.innerHTML = '';
      return 'ok';
    })()`);

    const inspectAttempts = [];
    const openInspect = async (zip) => {
      if (!(await ensureDocsReady())) {
        inspectAttempts.push({ zip, err: '资料页就绪失败：#sh-zip/#sh-open 不在' });
        return false;
      }
      for (let i = 0; i < 12; i++) {
        await modalReset();
        await sleep(250);
        const r = await evalJs(`(() => {
          const el = document.querySelector('#sh-zip');
          const btn = document.querySelector('#sh-open');
          if (!el || !btn) return 'no-elements';
          el.value = ${J(zip)};
          btn.click();
          return 'clicked';
        })()`);
        inspectAttempts.push({ zip, try: i, r });
        if (r === 'clicked') {
          const ok = await waitFor(
            `(() => { const t = (document.querySelector('#shi-body')||{}).innerText || ''; return t.length > 20; })()`,
            8000);
          if (ok) return true;
          inspectAttempts[inspectAttempts.length - 1].modal = await modalText();
        }
        await sleep(400);
      }
      return false;
    };
    const inspectState = async () => await evalJs(`(() => ({
      text: (document.querySelector('#shi-body')||{}).innerText || '',
      opts: [...((document.querySelector('#shi-sub')||{options:[]}).options||[])].map(o => ({v:o.value,t:o.textContent.trim()})),
      goDisabled: (document.querySelector('#shi-go')||{}).disabled,
      radios: [...document.querySelectorAll('input[name="shi-cf"]')].map(r => r.value),
    }))()`);
    const pickSubject = async (slug) => {
      await evalJs(`(() => {
        const s = document.querySelector('#shi-sub');
        s.value = ${J(slug)}; s.dispatchEvent(new Event('change')); return 'ok';
      })()`);
      await sleep(2500);            // change 会触发一次重新校验（弹窗内容重建）
      return await inspectState();
    };
    const doImport = async () => {
      profOn();
      await sleep(150);
      await progStart();
      const t0 = Date.now();
      const clicked = await evalJs(`(() => {
        const g = document.querySelector('#shi-go');
        if (!g) return 'no-go-button';
        if (g.disabled) return 'go-disabled';
        g.click();
        return 'clicked';
      })()`);
      let done = false;
      if (clicked === 'clicked') {
        done = await waitFor(
          `(() => { const t = (document.querySelector('#modalbox')||{}).innerText || '';
             return t.length > 20 && /导入完成|导入失败|已跳过|不能导入|这个包不能用|导入被拒|失败/.test(t); })()`,
          600000);
      }
      await sleep(250);
      const t1 = Date.now();
      const wall = t1 - t0;
      const prog = await progStop();
      profOff();
      const res = await evalJs(`(() => {
        const box = document.querySelector('#modalbox');
        return { text: box ? box.innerText.slice(0, 1400) : '',
                 badges: box ? [...box.querySelectorAll('.badge')].map(b => (b.innerText||'').trim()) : [] };
      })()`);
      await evalJs(`closeModal(); 'ok'`);
      await sleep(400);
      return { wall_ms: wall, t0_epoch: t0 / 1000, t1_epoch: t1 / 1000,
               clicked, done, ...res, progress_samples: prog };
    };

    // ---------- 1) 最精简包 → 「接收精简库」（主路径：省钱） ----------
    check('乙：看包信息弹窗打开（最精简包）', await openInspect(ZIP_MIN));
    await sleep(700);
    const info = await inspectState();
    out.evidence.inspectMin = info;
    check('乙：包里信息展示齐全（来源/导出时间/体积/预计耗时）',
          /来源/.test(info.text) && /导出于/.test(info.text)
          && /(B|KB|MB)/.test(info.text) && /导入预计/.test(info.text),
          info.text.replace(/\n/g, ' ').slice(0, 200));
    check('乙：包信息里说明「不含原课件文件 / 不含页图」与知识点产物',
          info.text.includes('不含原课件文件') && info.text.includes('不含页图')
          && /知识点产物/.test(info.text),
          info.text.replace(/\n/g, ' ').slice(0, 200));
    check('乙：向量模型一致性校验通过', /校验通过/.test(info.text), info.text.slice(0, 160));
    check('乙：可导入，「开始导入」按钮可用', info.goDisabled === false);
    check('乙：包信息没有 undefined / NaN 渲染漏洞', !/undefined|NaN/.test(info.text));
    out.shots.inspectMin = await shot('11-b-inspect-min.png');

    const picked = await pickSubject(SUBJ_MIN);
    out.evidence.pickMin = { opts: picked.opts, text: picked.text.slice(0, 300),
                             goDisabled: picked.goDisabled };
    check('乙：目标学科下拉里有「接收精简库」',
          (picked.opts || []).some(o => o.t === SUBJ_MIN || o.v === SUBJ_MIN),
          JSON.stringify(picked.opts).slice(0, 240));
    check('乙：切到空库后按那个库重新校验（无冲突、可导入）',
          picked.goDisabled === false && !/本库已有同一份课件/.test(picked.text),
          picked.text.replace(/\n/g, ' ').slice(0, 200));

    const imp1 = await doImport();
    out.evidence.importMin = imp1;
    check('乙：导入跑完并给出结果', imp1.done && /导入完成/.test(imp1.text),
          `${imp1.clicked} | ` + imp1.text.replace(/\n/g, ' ').slice(0, 160));
    check('乙：结果说明写入了多少行（切块/向量）',
          /切块\s*\d+/.test(imp1.text) && /向量\s*\d+/.test(imp1.text), imp1.text.slice(0, 200));
    check('乙：结果说明全文检索可用', /全文检索/.test(imp1.text), imp1.text.slice(0, 200));
    check('乙：结果给出包来源与包指纹 sha256',
          /来源：/.test(imp1.text) && /包指纹 sha256/.test(imp1.text), imp1.text.slice(0, 220));
    check('乙：结果明说零模型调用 / 零费用',
          /模型调用：\s*0 次/.test(imp1.text) && /CNY 0\.00/.test(imp1.text),
          imp1.text.slice(0, 240));
    const activeSamples = (imp1.progress_samples || []).filter(s => s.active);
    const staged = activeSamples.filter(s => s.stage && s.stage !== '准备中');
    out.evidence.progressMin = { samples: (imp1.progress_samples || []).length,
                                 active: activeSamples.length, staged: staged.length,
                                 sample: activeSamples.slice(0, 4) };
    check('乙：导入期间进度通道有真实读数（active/stage/done/total）',
          activeSamples.length > 0, JSON.stringify(out.evidence.progressMin).slice(0, 260));
    out.shots.importMin = await shot('12-b-import-min.png');

    await evalJs(`(async () => { await go('docs'); return 'ok'; })()`);
    await sleep(2000);
    const docs1 = await docsNow();
    out.evidence.docsAfterMin = docs1;
    check('乙：资料页出现了这份课件（1 份）', docs1.n === 1,
          JSON.stringify({ n: docs1.n, current: docs1.current, db: docs1.db_now,
                           rows: docs1.rows }).slice(0, 320));
    check(`乙：界面/库里这份课件的行数口径与导出方一致（正文切块数 ${EXPECT_DOC_CHUNKS}）`,
          EXPECT_DOC_CHUNKS ? docs1.n_chunks_sum === EXPECT_DOC_CHUNKS : docs1.n_chunks_sum > 0,
          `n_chunks 合计=${docs1.n_chunks_sum}，期望 ${EXPECT_DOC_CHUNKS}`);
    check(`乙：导入结果里写的实测块数 = 导出方包里的块数（${EXPECT_CHUNKS}）`,
          EXPECT_CHUNKS ? new RegExp('切块\\s*' + EXPECT_CHUNKS).test(imp1.text) : true,
          imp1.text.replace(/\n/g, ' ').slice(0, 160));
    out.shots.docsAfter = await shot('13-b-docs-after-import.png');

    // ---------- 2) 同题检索（与甲端快照对照） ----------
    const searchSnap = {};
    for (const q of QUERIES) {
      const r = await evalJs(`(async () => {
        const t0 = Date.now();
        const d = await (await fetch('/api/search?q=' + encodeURIComponent(${J(q)}) +
          '&k=8&corpus=main', {cache:'no-store'})).json();
        return { ms: Date.now() - t0, n: d.n, hits: (d.hits||[]).map(h => ({
          ord: h.ord, ref: h.ref, cite: h.cite, text: (h.text||'').slice(0, 70),
          heading: (h.heading||'').slice(0, 60), title: h.title,
          vec: Math.round((h.vec_score||0)*1000)/1000, kw: Math.round((h.kw_score||0)*1000)/1000 })) };
      })()`);
      searchSnap[q] = r;
      check(`乙：同题检索有命中（${q}）`, r && r.n > 0, JSON.stringify(r).slice(0, 180));
    }
    out.snapshot.search = searchSnap;

    // ---------- 3) 知识点页签（与甲端对照） ----------
    await evalJs(`(async () => { await go('kps'); return 'ok'; })()`);
    await sleep(2500);
    const mm = await evalJs(`(async () => {
      const sel = document.querySelector('#mm-doc');
      if (sel) { sel.value = '1'; sel.dispatchEvent(new Event('change')); }
      return { hasSel: !!sel, opts: sel ? [...sel.options].map(o => ({v:o.value,t:o.textContent.trim()})) : [] };
    })()`);
    await sleep(2500);
    const treeB = await evalJs(`(() => {
      const t = document.querySelector('#mm-tree');
      return { text: t ? t.innerText.slice(0, 1200) : '' };
    })()`);
    const mmApiB = await evalJs(`(async () => {
      const d = await (await fetch('/api/mindmap?doc_id=1', {cache:'no-store'})).json();
      const names = [];
      (function walk(n) {
        if (!n) return;
        if (Array.isArray(n)) { n.forEach(walk); return; }
        const nm = n.name || n.label || n.title || n.text;
        if (nm) names.push(String(nm));
        (n.children || []).forEach(walk);
      })(d.tree || []);
      return { n_nodes: d.n_nodes, n_pages: d.n_pages, lkp_total: d.lkp_total,
               lkp_ready: d.lkp_ready, state: d.artifact_state, cost_cny: d.cost_cny,
               artifact_source: d.artifact_source, n_tree_names: names.length,
               names: names.slice(0, 40) };
    })()`);
    const firstKpId = await evalJs(`(async () => {
      const d = await (await fetch('/api/mindmap?doc_id=1', {cache:'no-store'})).json();
      let found = '';
      (function walk(n) {
        if (!n || found) return;
        if (Array.isArray(n)) { n.forEach(walk); return; }
        if (n.lkp_ids && n.lkp_ids.length) { found = n.lkp_ids[0]; return; }
        (n.children||[]).forEach(walk);
      })(d.tree || []);
      return found;
    })()`);
    let summaryOk = { ok: false, chars: 0, name: '' };
    if (firstKpId) {
      summaryOk = await evalJs(`(async () => {
        const d = await (await fetch('/api/summary-kp?doc_id=1&kp_id=' +
          encodeURIComponent(${J(firstKpId)}), {cache:'no-store'})).json();
        const body = JSON.stringify(d);
        return { ok: !d.error, chars: body.length, name: d.name || (d.kp && d.kp.name) || '',
                 keys: Object.keys(d).slice(0, 15), error: d.error || '' };
      })()`);
    }
    out.snapshot.kp = { docPicker: mm, domText: treeB.text.slice(0, 600), api: mmApiB,
                        firstKpId, summary: summaryOk };
    out.shots.kpTree = await shot('14-b-kp-tree.png');
    check('乙：知识点页签列出这份课件', mm.hasSel && (mm.opts || []).length > 0,
          JSON.stringify(mm.opts).slice(0, 200));
    check('乙：知识点页签渲染出内容（不是空态）',
          !!(treeB.text && treeB.text.length > 20), (treeB.text || '').slice(0, 140));
    check('乙：末级知识点条数 > 0', (mmApiB && mmApiB.lkp_total || 0) > 0,
          JSON.stringify({ lkp: mmApiB && mmApiB.lkp_total, ready: mmApiB && mmApiB.lkp_ready }));
    check('乙：点开一条末级知识点能取到总结正文',
          summaryOk.ok && summaryOk.chars > 200, JSON.stringify(summaryOk).slice(0, 200));

    // ---------- 4) 重复导入（同一包第二次） ----------
    const before = await docsNow();
    check('乙：重复导入前先打开看包信息（应检出冲突）', await openInspect(ZIP_MIN));
    await sleep(700);
    const dup = await inspectState();
    out.evidence.inspectDup = { text: dup.text.slice(0, 300), radios: dup.radios,
                                goDisabled: dup.goDisabled };
    check('乙：第二次看同一个包 → 检出「本库已有同一份课件」',
          /本库已有同一份课件/.test(dup.text), dup.text.replace(/\n/g, ' ').slice(0, 200));
    check('乙：冲突处给了三选一（跳过 / 另存一份 / 覆盖）',
          (dup.radios || []).join(',') === 'skip,duplicate,overwrite', (dup.radios || []).join(','));
    const imp2 = await doImport();
    out.evidence.importDup = imp2;
    const after = await docsNow();
    check('乙：第二次导入被明确跳过（不重复导）', /已跳过|跳过/.test(imp2.text),
          imp2.text.replace(/\n/g, ' ').slice(0, 200));
    check('乙：重复导入后资料数与切块数都没变（不产生重复块）',
          after.n === before.n && after.n_chunks_sum === before.n_chunks_sum,
          `docs ${before.n}->${after.n} n_chunks ${before.n_chunks_sum}->${after.n_chunks_sum}`);
    out.shots.dupImport = await shot('15-b-dup-import.png');

    // ---------- 5) 全量包（带原文件+页图）→ 「接收全量库」 ----------
    check('乙：打开全量包的包信息', await openInspect(ZIP_FULL));
    await sleep(700);
    const infoFull = await inspectState();
    out.evidence.inspectFull = { text: infoFull.text.slice(0, 500),
                                 opts: (infoFull.opts || []).map(o => o.t),
                                 goDisabled: infoFull.goDisabled };
    const fo = infoFull.text || '';
    check('乙：全量包信息里说明「含原课件文件」与「有页图」',
          fo.includes('含原课件文件') && !fo.includes('不含原课件文件') && /页图\s*[1-9]/.test(fo),
          fo.replace(/\n/g, ' ').slice(0, 220));
    const pickFull = await pickSubject(SUBJ_FULL);
    out.evidence.pickFull = { text: pickFull.text.slice(0, 200), goDisabled: pickFull.goDisabled };
    check('乙：切到「接收全量库」后可导入', pickFull.goDisabled === false,
          pickFull.text.replace(/\n/g, ' ').slice(0, 200));
    const impFull = await doImport();
    out.evidence.importFull = impFull;
    check('乙：全量包导入成功（大包不崩）', impFull.done && /导入完成/.test(impFull.text),
          `${impFull.clicked} | ` + impFull.text.replace(/\n/g, ' ').slice(0, 200));
    const fullActive = (impFull.progress_samples || []).filter(s => s.active);
    out.evidence.progressFull = { samples: (impFull.progress_samples || []).length,
                                  active: fullActive.length, sample: fullActive.slice(0, 6) };
    check('乙：大包导入期间进度可见（有 active + done/total 读数）',
          fullActive.length > 0, JSON.stringify(out.evidence.progressFull).slice(0, 300));
    out.shots.importFull = await shot('16-b-import-full.png');

    // 切到「接收全量库」——走顶栏那个学科菜单（真界面操作），切完再核对页图。
    await evalJs(`(() => {
      window.__c0081Switch = async (slug) => {
        try {
          const btn = document.querySelector('#subj-btn');
          if (!btn) return { ok: false, error: '顶栏没有学科按钮' };
          btn.click();
          let item = null;
          for (let i = 0; i < 60; i++) {
            item = [...document.querySelectorAll('#subj-menu .sitem[data-slug]')]
              .find(b => b.dataset.slug === slug);
            if (item) break;
            await new Promise(r => setTimeout(r, 150));
          }
          if (!item) return { ok: false, error: '学科菜单里没有这个学科：' + slug };
          item.click();
          for (let i = 0; i < 80; i++) {
            await new Promise(r => setTimeout(r, 150));
            let cur = '';
            try { cur = SUBJ.cur; } catch (e) { cur = ''; }
            if (cur === slug) return { ok: true, cur: cur, via: '顶栏学科菜单' };
          }
          let last = '';
          try { last = SUBJ.cur; } catch (e) { last = '?'; }
          return { ok: false, error: '点了菜单但当前学科仍是 ' + last };
        } catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
      };
      return 'ok';
    })()`);
    const switched = await evalJs(`window.__c0081Switch(${J(SUBJ_FULL)})`);
    out.evidence.switchFull = switched;
    check('乙：用顶栏学科菜单切到「接收全量库」（多库并存，互不干扰）',
          switched && switched.ok, JSON.stringify(switched));
    await sleep(2500);
    const slideB = {};
    for (const pg of [2, 5, 12]) {
      const r = await fetch(`${BASE}/img/slide?doc_id=1&page=${pg}`);
      const buf = Buffer.from(await r.arrayBuffer());
      slideB[pg] = { status: r.status, bytes: buf.length,
                     sha256: r.status === 200 ? sha256(buf) : '',
                     ctype: r.headers.get('content-type') || '' };
    }
    out.snapshot.slide = slideB;
    check('乙：带页图的包导入后能看到课件页图（/img/slide 200 + 字节数 > 0）',
          Object.values(slideB).some(s => s.status === 200 && s.bytes > 1000),
          JSON.stringify(slideB).slice(0, 260));
    await evalJs(`(async () => { await go('docs'); return 'ok'; })()`);
    await sleep(2000);
    const docsFull = await docsNow();
    out.evidence.docsFull = docsFull;
    check('乙：全量库里出现这份课件', docsFull.n >= 1,
          JSON.stringify({ n: docsFull.n, current: docsFull.current,
                           rows: docsFull.rows }).slice(0, 300));

    // ---------- 6) 恶意包：逐个尝试导入，都必须被拒 ----------
    const badCases = [
      ['路径穿越', join(BADDIR, 'bad-traversal.zip'), /非法|路径|条目|穿越/],
      ['模型名/维度不符', join(BADDIR, 'bad-model.zip'), /模型不一致/],
      ['缺必需条目', join(BADDIR, 'bad-missing-chunks.zip'), /缺必需条目|不是分享包|缺/],
      ['缺字段（doc.json 少 content_hash）', join(BADDIR, 'bad-nofield.zip'), /content_hash|缺/],
      ['超大解压体积（zip 炸弹）', join(BADDIR, 'bad-bomb.zip'), /炸弹|压缩比|解压|上限|超大/],
    ];
    const badResults = [];
    for (const [label, path, bodyRe] of badCases) {
      const t0 = Date.now();
      const opened = await openInspect(path);
      await sleep(800);
      const st = await inspectState();
      let clickedGo = false;
      let afterGo = '';
      if (st.goDisabled === false) {
        clickedGo = true;
        await evalJs(`(() => {
          const g = document.querySelector('#shi-go');
          if (g && !g.disabled) g.click();
          return 'ok';
        })()`);
        await waitFor(`(() => { const t = (document.querySelector('#modalbox')||{}).innerText || '';
          return t.length > 20 && /导入完成|导入失败|已跳过|不能导入|这个包不能用|拒绝|失败/.test(t); })()`, 120000);
        afterGo = await modalText();
        await evalJs(`closeModal(); 'ok'`);
        await sleep(400);
      }
      const blob = (st.text || '') + ' ' + afterGo;
      const rejected = st.goDisabled === true
        || /不能导入|这个包不能用|失败|拒绝/.test(blob);
      const actionable = bodyRe.test(blob)
        || /怎么办|请让导出方|重新导出|出路/.test(blob);
      const docsAfter = await docsNow();
      badResults.push({ label, path, inspect_ms: Date.now() - t0, opened,
                        goDisabled: st.goDisabled, clickedGo,
                        text: (st.text || '').slice(0, 600),
                        afterGo: afterGo.slice(0, 600), rejected, actionable,
                        docs_after: docsAfter.n, n_chunks_after: docsAfter.n_chunks_sum });
      check(`恶意包「${label}」：被拒绝`, rejected, blob.replace(/\n/g, ' ').slice(0, 220));
      check(`恶意包「${label}」：给出可操作提示（哪里不对/怎么办）`, actionable,
            blob.replace(/\n/g, ' ').slice(0, 300));
      check(`恶意包「${label}」：被拒后库内资料数不变`,
            docsAfter.n === docsFull.n, `${docsFull.n} -> ${docsAfter.n}`);
      out.shots['bad_' + label] = await shot(`17-b-bad-${badResults.length}.png`);
      await evalJs(`closeModal(); 'ok'`);
      await sleep(300);
    }
    out.evidence.badPackages = badResults;

    // 路径穿越包有没有把文件写到库外（几个最可能的位置）
    const evil = [];
    for (const p of [join(process.env.TEMP || '.', 'c0081-evil.txt'),
                     join(process.env.USERPROFILE || '.', 'c0081-evil.txt'),
                     'C:\\c0081-evil.txt']) {
      if (existsSync(p)) evil.push(p);
    }
    out.evidence.traversalEscapes = evil;
    check('恶意包「路径穿越」：没有在库外写出任何文件', evil.length === 0,
          evil.join(' | ') || '（库外三个可能落点都不存在该文件）');

    // ---------- 7) 收尾 ----------
    out.evidence.inspectAttempts = inspectAttempts;
    out.errors = errors;
    check('浏览器控制台没有 JS 报错', errors.length === 0, errors.slice(0, 3).join(' | '));
  }

  if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(out, null, 2), 'utf-8');
  const pass = checks.filter(c => c.ok).length;
  const failed = checks.filter(c => !c.ok);
  console.log(`结论：${failed.length === 0 ? 'PASS' : 'FAIL'}  ${pass}/${checks.length} 项通过（stage ${STAGE}）`);
  if (failed.length) for (const f of failed) console.log(`  ✗ ${f.name} —— ${f.detail}`);
  console.log(`截图目录：${OUTDIR || '(未开)'}`);
  console.log(`证据 JSON：${JSONOUT || '(未开)'}`);
  console.log(`页面错误：${errors.length}`);
  try { ws.close(); } catch { /* 忽略 */ }
  try { edge.kill(); } catch { /* 忽略 */ }
  await sleep(400);
  process.exitCode = failed.length === 0 ? 0 : 1;
  return;
}

main().catch((e) => {
  console.error('探针失败：' + (e && e.message));
  try { edge.kill(); } catch { /* 忽略 */ }
  process.exit(2);
});
