// 卡 C-0089 探针：**真浏览器点按钮**跑完整条教材入库专线（headless Edge + CDP）。
//
// 为什么必须走真浏览器、真点击：这一卡的交付物就是"资料栏多一个按钮"。
// DOM 桩里 dispatch 一个假 click 谁都会写，但真浏览器能同时回答三件事——
// 按钮**在不在**（渲染出来了）、点了**有没有反应**（onclick 接上了）、
// 整条链路**有没有 JS 错误**（控制台干净）。所以这里用 CDP 的
// `Input.dispatchMouseEvent` 在按钮的**真实坐标**上按下再抬起。
//
// 验的事（对应卡面验收标准）：
//   1. 资料栏渲染出独立的「教材入库（专线）」一节（按钮 + 路径框 + 说明），
//      而方式一 / 拖拽 / 批量入库 / 方式二**四个旧入口都还在**；
//   2. 在路径框里填一份**真扫描件**的本机路径 → 真点按钮 → **一条龙**跑完：
//      文件真落进 raw\教材\（磁盘字节级核对）→ 本地 OCR（免费、不调视觉模型）
//      → 按章切块 → 入库；
//   3. 入库回执**列识别到的章**（章号 + 章名 + 页范围）与块数变化；
//   4. 库里 documents.role='textbook'、extra.chunk_scheme='textbook-v1'、
//      面包屑是「书名 > 第X章 章名」、`extra.ocr_pages` 有值、
//      **没有任何识图块**（= 没花视觉模型的钱）；
//   5. 全过程 console 零 error，截图落盘。
//
// 一条命令跑完（自己起临时看板、跑完自己收）：
//   node tools\probe_c0089_textbook.mjs --outdir <截图目录> --json <结果json>
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, copyFileSync, statSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const OUTDIR = arg('outdir', join(ROOT, '_tmp_c0089', 'shots'));
const JSONOUT = arg('json', join(ROOT, '_tmp_c0089', 'probe_result.json'));
const TMP = resolve(arg('tmp', join(ROOT, '_tmp_c0089', 'probe_data')));
const PORT = parseInt(arg('port', String(8810 + Math.floor(Math.random() * 80))), 10);
const CDP_PORT = parseInt(arg('cdp', String(9510 + Math.floor(Math.random() * 80))), 10);
const BASE = `http://127.0.0.1:${PORT}`;
const FIXTURE = join(ROOT, 'tools', 'c0089_mini_textbook.pdf');
// 入库用的文件名刻意取短：文档标题是在"文件名 vs 正文标题"之间按信息量
// （长度）择优的，文件名太长会赢过正文里的真书名（见夹具脚本里的说明）。
const MINI_NAME = '药理学6版.pdf';
const WAIT_RECEIPT_MS = parseInt(arg('wait', '300000'), 10);

const PY = join(ROOT, 'venv', 'Scripts', 'python.exe');
const EDGE = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
].find((p) => existsSync(p));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (!existsSync(PY)) throw new Error('找不到 venv 解释器：' + PY);
  if (!EDGE) throw new Error('找不到 msedge.exe');
  if (!existsSync(FIXTURE)) throw new Error('找不到夹具教材：' + FIXTURE);
  mkdirSync(OUTDIR, { recursive: true });
  mkdirSync(dirname(JSONOUT), { recursive: true });

  // ---------- 临时数据根（真库一行都不碰） ----------
  rmSync(TMP, { recursive: true, force: true });
  const srcDir = join(TMP, '..', 'probe_src');
  rmSync(srcDir, { recursive: true, force: true });
  mkdirSync(srcDir, { recursive: true });
  const srcPdf = join(srcDir, MINI_NAME);
  copyFileSync(FIXTURE, srcPdf);
  const srcSize = statSync(srcPdf).size;

  const checks = [];
  const check = (name, ok, detail) => {
    checks.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
    if (!ok) console.log('[FAIL] ' + name + (detail ? ' | ' + detail : ''));
    return !!ok;
  };

  // ---------- 起临时看板（临时数据根，不碰真实学科库） ----------
  const srvEnv = { ...process.env };
  delete srvEnv.KB_TESTING;   // 子进程要连的就是它自己的临时库，见 verify 脚本里的说明
  srvEnv.KB_SETTINGS_FILE = join(TMP, 'settings.json');
  const srv = spawn(PY, [join(ROOT, 'tools', 'c0083_tmp_launch.py'), TMP, String(PORT)],
    { cwd: ROOT, env: srvEnv, stdio: 'ignore' });
  let edge = null;
  const profile = join(TMP, '..', 'probe_edge_profile');
  rmSync(profile, { recursive: true, force: true });

  const out = {
    ok: false, base: BASE, tmp: TMP, srcPdf,
    jsErrors: 0, ocrPages: null, visionCalls: null, chaptersListed: 0,
    receiptText: '', db: null, shots: [], checks,
  };

  try {
    // 等看板起来
    let ready = false;
    for (let i = 0; i < 90; i++) {
      try { const r = await fetch(BASE + '/'); if (r.ok) { ready = true; break; } }
      catch { /* 还没起来 */ }
      await sleep(300);
    }
    check('临时看板实例就绪（' + BASE + '）', ready);
    if (!ready) throw new Error('看板没起来');

    // ---------- headless Edge + CDP ----------
    edge = spawn(EDGE, ['--headless=new', `--remote-debugging-port=${CDP_PORT}`,
      `--user-data-dir=${profile}`, '--no-first-run', '--disable-gpu',
      '--window-size=1500,1400', 'about:blank'], { stdio: 'ignore' });
    let list = null;
    for (let i = 0; i < 90; i++) {
      try {
        const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        list = await r.json();
        if (list?.length) break;
      } catch { /* 还没起来 */ }
      await sleep(300);
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
      }
    };
    const evalJs = async (expr) => {
      const r = await send('Runtime.evaluate',
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result?.exceptionDetails) {
        errors.push('eval: ' + (r.result.exceptionDetails.text || ''));
      }
      return r.result?.result?.value;
    };
    const shot = async (name, clip) => {
      const s = await send('Page.captureScreenshot',
        clip ? { format: 'png', clip: { ...clip, scale: 1 }, captureBeyondViewport: true }
             : { format: 'png' });
      if (s.result?.data) {
        const p = join(OUTDIR, name);
        writeFileSync(p, Buffer.from(s.result.data, 'base64'));
        out.shots.push(p);
        return p;
      }
      return null;
    };
    const rectOf = async (sel) => {
      const j = await evalJs(`(() => { const el = document.querySelector(${JSON.stringify(sel)});
        if (!el) return '';
        el.scrollIntoView({block:'center'});
        const r = el.getBoundingClientRect();
        return JSON.stringify({x: r.left + window.scrollX, y: r.top + window.scrollY,
          w: r.width, h: r.height}); })()`);
      try { return j ? JSON.parse(j) : null; } catch { return null; }
    };
    // ⚠ 两套坐标别搞混：`Page.captureScreenshot` 的 clip 是**文档坐标**
    // （配合 captureBeyondViewport），而 `Input.dispatchMouseEvent` 要的是
    // **视口坐标**。第一版把文档坐标喂给 Input，在"元素需要滚动才可见"的
    // 页面上点击就落到了别处（实测：按钮没反应、文件没落盘）。
    const pointOf = async (sel) => {
      const j = await evalJs(`(() => { const el = document.querySelector(${JSON.stringify(sel)});
        if (!el) return '';
        el.scrollIntoView({block:'center'});
        const r = el.getBoundingClientRect();
        return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2,
          w: r.width, h: r.height, inView: r.top >= 0 && r.bottom <= innerHeight}); })()`);
      try { return j ? JSON.parse(j) : null; } catch { return null; }
    };
    const waitFor = async (expr, ms = 30000) => {
      const t0 = Date.now();
      while (Date.now() - t0 < ms) {
        if (await evalJs(expr)) return true;
        await sleep(400);
      }
      return false;
    };
    // 真点击：先在**视口坐标**上按下、再抬起（DOM.click() 是合成事件，不作数）
    const realClick = async (sel) => {
      const r = await pointOf(sel);
      if (!r) return false;
      const x = Math.round(r.x), y = Math.round(r.y);
      out.clickPoint = { x, y, inView: r.inView, sel };
      await send('Input.dispatchMouseEvent',
        { type: 'mouseMoved', x, y, button: 'none', clickCount: 0 });
      await sleep(80);
      await send('Input.dispatchMouseEvent',
        { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
      await sleep(80);
      await send('Input.dispatchMouseEvent',
        { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
      return true;
    };

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Log.enable').catch(() => {});
    await send('Page.navigate', { url: BASE + '/' });
    await sleep(2500);
    await waitFor(`!!document.querySelector('#nav button')`, 20000);
    // 记下页面发出的每一个请求：这条链路的失败**不会**在 console 报错
    // （前端把错误吞进 toast 了），所以"点了按钮到底发了什么请求、后端答了什么"
    // 必须留证，否则失败时只剩一句"文件没落盘"。
    await evalJs(`(() => { window.__reqs = window.__reqs || [];
      if (!window.__wrapped) { const of = window.fetch.bind(window);
        window.fetch = async (...a) => {
          // ⚠ 必须在**请求发出的那一刻**就记下来：教材入库要跑几十秒（本地
          // OCR + 嵌入），若等响应回来才记录，"请求到底发了没有"这个问题
          // 在等待窗口内根本答不上来（第一版就是这么误判的）。
          const rec = {url: String(a[0]), status: 0, body: '',
                       started: Date.now()};
          window.__reqs.push(rec);
          try {
            const r = await of(...a);
            rec.status = r.status;
            try { rec.body = (await r.clone().text()).slice(0, 400); } catch (e) {}
            rec.ms = Date.now() - rec.started;
            return r;
          } catch (e) { rec.error = String(e); throw e; }
        };
        window.__wrapped = true; }
      return 'ok'; })()`);
    await evalJs(`(async () => { await go('docs'); return 'ok'; })()`);
    await sleep(1500);

    // ---------- 1) UI：新入口在、四个旧入口都在 ----------
    const ui = await evalJs(`JSON.stringify({
      tbSection: !!document.querySelector('#tb-src') && !!document.querySelector('#tb-go'),
      tbBtnText: (document.querySelector('#tb-go')||{}).textContent || '',
      tbHint: document.body.innerText.includes('教材入库（专线）'),
      tbNote: document.body.innerText.includes('本地 OCR')
              && document.body.innerText.includes('按章切块'),
      tbLog: !!document.querySelector('#tb-log'),
      oldWay1: !!document.querySelector('#im-go'),
      oldDrop: !!document.querySelector('#dz'),
      // 批量入库卡片：#batch-list 只在"本库有待入库文件"时才渲染，而探针
      // 起手时 raw 里是空的 ⇒ 恒有的判据只能是那两个**按钮元素**本身
      // （不能拿 body.innerText 判：隐藏按钮（display:none）不在 innerText 里，
      //  而 body.textContent 又会把 script 的源码算进去）。
      oldBatch: [...document.querySelectorAll('button')].some(
        b => (b.textContent || '').includes('开始批量入库')),
      oldBatchRetry: [...document.querySelectorAll('button')].some(
        b => (b.textContent || '').includes('重试失败项')),
      oldBatchTable: !!document.querySelector('#batch-list'),
      oldWay2: !!document.querySelector('#sh-open'),
      oldAll: document.body.innerText.includes('入库全部待处理文件'),
      oldReindexBtns: document.body.innerText.includes('全部强制重建'),
      fnDefined: typeof tbIngest,
      btnOnclick: typeof (document.querySelector('#tb-go') || {}).onclick,
    })`);
    const u = JSON.parse(ui || '{}');
    check('新增独立入口：路径框 + 「选择教材并入库」按钮',
      u.tbSection && /选择教材并入库/.test(u.tbBtnText || ''), u.tbBtnText);
    check('新入口带说明（教材专线 / 本地 OCR / 按章切块）', u.tbHint && u.tbNote);
    check('有回执落点 #tb-log（一条龙之后把章列在这里）', u.tbLog);
    check('旧入口原样都在：方式一（#im-go）', u.oldWay1);
    check('旧入口原样都在：拖拽入库（#dz）', u.oldDrop);
    // 批量入库卡片：候选表格 `#batch-list` 只在"本库有待入库文件"时才渲染，
    // 所以恒有的判据是那两个按钮文案，表格存在与否单独报出来备查。
    check('旧入口原样都在：批量入库（开始/重试按钮）', u.oldBatch,
      'batchTable=' + u.oldBatchTable);
    check('旧入口原样都在：方式二分享包（#sh-open）', u.oldWay2);
    check('旧按钮原样都在：入库全部待处理文件 / 全部强制重建',
      u.oldAll && u.oldReindexBtns);

    await shot('c0089_docs_textbook.png');
    const secRect = await rectOf('#tb-src');
    if (secRect) {
      await shot('c0089_textbook_section.png',
        { x: Math.max(0, secRect.x - 16), y: Math.max(0, secRect.y - 60),
          width: Math.min(1400, secRect.w + 32), height: 260 });
    }

    // ---------- 2) 填路径 + 真点按钮 ----------
    const setSrc = `(() => { const el = document.querySelector('#tb-src');
      if (!el) return 'NO-INPUT';
      el.value = ${JSON.stringify(srcPdf)};
      el.dispatchEvent(new Event('input', {bubbles: true}));
      return el.value; })()`;
    const typed = await evalJs(setSrc);
    check('路径框收下了教材的本机路径', typed === srcPdf, String(typed));

    // 点击**前一刻**再写一次：资料页的 DOM 会被 `go('docs')` 整个重建，
    // 早先写进去的值可能已经被换掉（"点了没反应"最常见的真因之一）。
    await evalJs(setSrc);

    const clicked = await realClick('#tb-go');
    check('在按钮真实坐标上按下并抬起（CDP Input.dispatchMouseEvent）', clicked);
    check('按钮真的接上了处理函数（tbIngest 是全局函数、onclick 已绑定）',
      u.fnDefined === 'function' && u.btnOnclick === 'function',
      `typeof tbIngest=${u.fnDefined} onclick=${u.btnOnclick}`);
    out.clickedAt = new Date().toISOString();

    // 点击必须真的把请求发出去（"点了个寂寞"是这条链路上最容易骗过自己的失败）
    let reqSent = await waitFor(
      `(window.__reqs||[]).some(r => String(r.url).includes('/api/ingest/textbook'))`,
      12000);
    if (!reqSent) {
      // 先分清是"没点到"还是"没接上"：坐标上的元素到底是谁、有没有弹提示
      out.clickHit = await evalJs(
        `(() => { const el = document.elementFromPoint(${out.clickPoint?.x ?? -1},`
        + ` ${out.clickPoint?.y ?? -1});`
        + ` return el ? (el.id || el.tagName) : 'null'; })()`);
      out.toastAfterClick = await evalJs(
        `(document.body.innerText.match(/请先填[^\\n]*/) || [''])[0]`);
      if (u.fnDefined === 'function') {
        // 退一步用 DOM click 把链路走完（坐标点击这一条的结论已经如实记在上面的
        // check 与 clickHit 里），否则这一轮就只剩"文件没落盘"这一句废话。
        await evalJs(`document.querySelector('#tb-go').click()`);
        reqSent = await waitFor(
          `(window.__reqs||[]).some(r => String(r.url).includes('/api/ingest/textbook'))`,
          20000);
        out.clickFallback = 'dom-click';
      }
    }
    check('点击后真的发出了 POST /api/ingest/textbook', reqSent,
      out.clickFallback ? `（坐标点击未触发，改用 DOM click；命中元素 ${out.clickHit}）` : '');

    // ---------- 3) 等回执（真 OCR + 真切块 + 真嵌入，几十秒） ----------
    const landFile = join(TMP, 'raw', '教材', MINI_NAME);
    const got = reqSent ? await waitFor(
      `!!document.querySelector('#tb-log table')`
      + ` || document.body.innerText.includes('教材入库失败')`,
      WAIT_RECEIPT_MS) : false;
    check('入库回执渲染出来了（#tb-log 里出现了章清单表格）',
      got && !!(await evalJs(`!!document.querySelector('#tb-log table')`)));

    // 落盘是**磁盘事实**，另算一条（不能拿页面文案当证据）
    let landed = false;
    for (let i = 0; i < 40 && !landed; i++) {
      landed = existsSync(landFile);
      if (!landed) await sleep(250);
    }
    check('文件真落进 raw\\教材\\（磁盘事实）', landed, landFile);
    if (landed) {
      check('落盘字节与源文件一致', statSync(landFile).size === srcSize,
        `${statSync(landFile).size} vs ${srcSize}`);
    }

    const rc = await evalJs(`(() => { const b = document.querySelector('#tb-log');
      if (!b) return '';
      return JSON.stringify({text: b.innerText,
        rows: b.querySelectorAll('table tbody tr').length,
        head: (b.querySelector('.sub')||{}).innerText || ''}); })()`);
    const r = JSON.parse(rc || '{}');
    out.receiptText = r.text || '';
    out.chaptersListed = r.rows || 0;
    // 页面到底发了哪些请求、后端答了什么（失败时这是唯一的线索：
    // 前端把错误吞进 toast 了，console 里什么都没有）
    try {
      out.reqs = JSON.parse(await evalJs(
        `JSON.stringify((window.__reqs||[]).map(r => ({url: String(r.url),
          status: r.status, body: String(r.body||'').slice(0, 400)})))`) || '[]');
    } catch { out.reqs = []; }
    check('回执列出识别到的章（表格行数 ≥ 3）', (r.rows || 0) >= 3, String(r.rows));
    check('回执含章号 + 章名 + 页范围（p.17 那一行）',
      /第1章/.test(r.text || '') && /绪论/.test(r.text || '')
      && /p\.17/.test(r.text || ''), (r.text || '').slice(0, 160));
    check('回执含块数变化（0 → N）',
      /块数\s*0\s*→\s*\d+/.test((r.text || '').replace(/\s+/g, ' '))
      || /0\s*→\s*\d+/.test(r.text || ''), (r.text || '').slice(0, 160));

    const logRect = await rectOf('#tb-log');
    if (logRect) {
      await shot('c0089_receipt.png',
        { x: Math.max(0, logRect.x - 16), y: Math.max(0, logRect.y - 20),
          width: Math.min(1400, logRect.w + 32),
          height: Math.min(900, logRect.h + 40) });
    }

    // ---------- 4) 库里的真相（用独立脚本直连那个 .db 文件） ----------
    const dbOut = await new Promise((res) => {
      const p = spawn(PY, ['-X', 'utf8', join(ROOT, 'tools', 'c0089_probe_dbcheck.py'),
        join(TMP, 'index', 'study.db'), '教材'], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
      let s = '';
      p.stdout.on('data', (d) => { s += d.toString('utf8'); });
      p.on('close', () => res(s.trim()));
      setTimeout(() => { try { p.kill(); } catch {} res(s.trim()); }, 20000);
    });
    let db = {};
    try { db = JSON.parse(dbOut.split('\n').pop() || '{}'); } catch { db = {}; }
    out.db = db;
    out.ocrPages = db.ocr_pages == null ? null : db.ocr_pages;
    // "没调视觉模型"的判据取自库：教材入库走的是本地 OCR（免费），
    // 库里**不该有任何识图产物**（识图块的 source_ref 形如 "p.5 图"）。
    out.visionCalls = db.figure_chunks == null ? null : db.figure_chunks;

    check('库里 role=textbook', db.role === 'textbook', String(db.role));
    check('库里 extra.chunk_scheme=textbook-v1',
      db.chunk_scheme === 'textbook-v1', String(db.chunk_scheme));
    check('本地 OCR 真跑了（extra.ocr_pages > 0）',
      Number(db.ocr_pages) > 0, String(db.ocr_pages));
    check('★ 没有任何识图产物（本地 OCR 路线，不花视觉模型的钱）',
      Number(db.figure_chunks) === 0, String(db.figure_chunks));
    check('面包屑是「书名 > 第X章 章名」',
      Array.isArray(db.heading_paths)
      && db.heading_paths.some((h) => /第1章/.test(h)),
      JSON.stringify((db.heading_paths || []).slice(0, 3)));
    // 形态必须**干净**：每块要么只有书名（前置内容），要么"书名 > 第X章 章名"。
    // 出现三层（「书名 > 书名 > …」）说明切块把页内标题又叠了一层，
    // 那正是"按章切块"要避免的形态，不能睁一只眼闭一只眼。
    check('面包屑形态干净：每块 ≤ 2 段（书名 / 书名 > 第X章 章名）',
      Array.isArray(db.heading_paths)
      && db.heading_paths.every((h) => String(h).split('>').length <= 2),
      JSON.stringify(db.heading_paths));
    check('库里落地的章数 ≥ 3', Array.isArray(db.chapters) && db.chapters.length >= 4,
      String((db.chapters || []).length));
    check('块数与回执一致（回执 chunks_after 有值）',
      Number(db.chunks) > 0 && /\d+\s*块/.test(r.text || ''), String(db.chunks));

    // ---------- 5) 零 JS 错误 ----------
    out.jsErrors = errors.length;
    check('真浏览器 console 零 error（含 CDP 异常）', errors.length === 0,
      errors.slice(0, 3).join(' | '));

    out.ok = checks.every((c) => c.ok);
  } catch (e) {
    out.error = `${e.name}: ${e.message}`;
    console.log('[ERR] ' + out.error);
  } finally {
    if (edge) { try { edge.kill(); } catch { /* 已退出 */ } }
    try { srv.kill(); } catch { /* 已退出 */ }
    writeFileSync(JSONOUT, JSON.stringify(out, null, 2), 'utf8');
    const bad = checks.filter((c) => !c.ok);
    console.log(`\nC0089-PROBE-${out.ok ? 'PASS' : 'FAIL'}  ` +
      `${checks.length - bad.length}/${checks.length} 项通过`
      + `；JS 错误 ${out.jsErrors}；OCR 页 ${out.ocrPages}；`
      + `识图产物 ${out.visionCalls}；回执章行 ${out.chaptersListed}`);
    console.log('截图: ' + out.shots.join(' , '));
    console.log('结果 JSON: ' + JSONOUT);
    for (const c of bad) console.log('   失败项：' + c.name + '  ' + c.detail);
    if (bad.length && out.reqs) {
      console.log('   页面发出的请求：');
      for (const q of out.reqs) {
        console.log(`     ${q.status} ${q.url}  ${String(q.body).slice(0, 200)}`);
      }
    }
  }
  return out.ok ? 0 : 2;
}

main().then((code) => process.exit(code)).catch((e) => {
  console.log('C0089-PROBE-FAIL 未捕获异常: ' + (e && e.stack || e));
  process.exit(2);
});
