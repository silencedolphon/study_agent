// 卡 C-0083 探针：**真浏览器拖放**（headless Edge + CDP，不是 DOM 桩）。
//
// 拖放这种东西最容易"自说自话"：DOM 里 dispatch 一个假事件谁都会写，
// 但真浏览器的 drop 给的是 File 对象、dragover 不拦会直接把页面顶掉。
// 所以这里走 CDP 的 `Input.dispatchDragEvent`——由 Chromium 自己构造
// 带**真实文件**的 DataTransfer，跟人从资源管理器拖进来是同一条路径。
//
// 验的事：
//   1. 拖放区在「资料」页渲染出来，且「上传后立即入库」默认**不勾**；
//   2. dragover 已被 preventDefault（在 #dz 上后注册的监听器读
//      `e.defaultPrevented`），且拖放后页面**没被 file:// 顶掉**；
//   3. 一次拖两个文件 → 两个都真落盘到 raw\（磁盘字节与源文件逐字节一致）；
//   4. 落盘后页面出现「待入库」提示，而且文件在 /api/raw 里仍是未入库状态；
//   5. 同名文件按现有规则自动 _1，已有文件字节不变；
//   6. 子文件夹输入框有值时，文件落进 raw\<子文件夹>；
//   7. 非课件类型（.xls）与超大文件（>256MB）各有**可操作**的中文提示；
//   8. 拖文件夹走的是"请改用方式一"的专门提示（注入式补测这条分支）；
//   9. 整个过程 console 零 error。
//
// 一条命令跑完（自己起临时看板、跑完自己收）：
//   node tools\probe_c0083_dragdrop.mjs --outdir <截图目录> --json <结果json>
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync, openSync, closeSync, ftruncateSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const OUTDIR = arg('outdir', '');
const JSONOUT = arg('json', '');
const TMP = resolve(arg('tmp', join(ROOT, '_tmp_c0083', 'probe_data')));
const SRC = resolve(arg('src', join(ROOT, '_tmp_c0083', 'probe_src')));
const PORT = parseInt(arg('port', String(8800 + Math.floor(Math.random() * 90))), 10);
const CDP_PORT = parseInt(arg('cdp', String(9400 + Math.floor(Math.random() * 90))), 10);
const BASE = `http://127.0.0.1:${PORT}`;
const BIG_MB = parseInt(arg('bigmb', '300'), 10);   // 稀疏文件，只用来触发前端预检

const PY = join(ROOT, 'venv', 'Scripts', 'python.exe');
const EDGE = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
].find((p) => existsSync(p));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (!existsSync(PY)) throw new Error('找不到 venv 解释器：' + PY);
  if (!EDGE) throw new Error('找不到 msedge.exe');
  if (OUTDIR) mkdirSync(OUTDIR, { recursive: true });

  // ---------- 造测试素材 ----------
  for (const d of [TMP, SRC]) { rmSync(d, { recursive: true, force: true }); mkdirSync(d, { recursive: true }); }
  const rawDir = join(TMP, 'raw');
  mkdirSync(rawDir, { recursive: true });

  const srcDeck = join(SRC, '探针讲义.pdf');
  const srcDeckBytes = Buffer.from(('卡C-0083 探针：真拖放上传 ' + 'x'.repeat(200) + '\n').repeat(300), 'utf8');
  writeFileSync(srcDeck, srcDeckBytes);

  const srcSheet = join(SRC, '探针表格.xls');          // 非课件类型（legacy）
  const srcSheetBytes = Buffer.from('a,b,c\n1,2,3\n', 'utf8');
  writeFileSync(srcSheet, srcSheetBytes);

  const srcOther = join(SRC, '探针第二份.pdf');        // 用于"子文件夹"那一轮
  const srcOtherBytes = Buffer.from('第二份内容，放到 raw\\教材\\\n'.repeat(20), 'utf8');
  writeFileSync(srcOther, srcOtherBytes);

  const srcBig = join(SRC, '探针大文件.bin');          // 稀疏 300MB：前端预检就挡住，不会真传
  const fd = openSync(srcBig, 'w');
  ftruncateSync(fd, BIG_MB * 1024 * 1024);
  closeSync(fd);

  // 先在 raw\ 放一份**同名**文件：拖进去应当自动 _1，且这份字节不变
  const preexisting = join(rawDir, '探针讲义.pdf');
  const preexistingBytes = Buffer.from('这是 raw\\ 里原有的同名文件，不许被覆盖\n', 'utf8');
  writeFileSync(preexisting, preexistingBytes);

  // ---------- 起临时看板（临时数据根，不碰真实学科库） ----------
  const srv = spawn(PY, [join(ROOT, 'tools', 'c0083_tmp_launch.py'), TMP, String(PORT)],
    { cwd: ROOT, stdio: 'ignore' });
  let edge = null;
  const profile = join(TMP, '..', 'probe_edge_profile');
  rmSync(profile, { recursive: true, force: true });
  const checks = [];
  const check = (name, ok, detail) => {
    checks.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
    if (!ok) console.log('[FAIL] ' + name + (detail ? ' | ' + detail : ''));
    return !!ok;
  };

  try {
    // 等看板起来
    let ready = false;
    for (let i = 0; i < 80; i++) {
      try {
        const r = await fetch(BASE + '/');
        if (r.ok) { ready = true; break; }
      } catch { /* 还没起来 */ }
      await sleep(300);
    }
    check('临时看板实例就绪（' + BASE + '）', ready);
    if (!ready) throw new Error('看板没起来');

    // ---------- 起 headless Edge + CDP ----------
    edge = spawn(EDGE, ['--headless=new', `--remote-debugging-port=${CDP_PORT}`,
      `--user-data-dir=${profile}`, '--no-first-run', '--disable-gpu',
      '--window-size=1500,1200', 'about:blank'], { stdio: 'ignore' });
    let list = null;
    for (let i = 0; i < 80; i++) {
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
      return r.result?.result?.value;
    };
    const shot = async (name) => {
      if (!OUTDIR) return null;
      const s = await send('Page.captureScreenshot', { format: 'png' });
      if (s.result?.data) {
        const p = join(OUTDIR, name);
        writeFileSync(p, Buffer.from(s.result.data, 'base64'));
        return p;
      }
      return null;
    };
    const waitFor = async (expr, ms = 30000) => {
      const t0 = Date.now();
      while (Date.now() - t0 < ms) {
        if (await evalJs(expr)) return true;
        await sleep(250);
      }
      return false;
    };
    // 落盘是**磁盘事实**，只能轮询文件系统，不能拿页面文案当证据
    //（"开始上传…到 raw\\教材\\" 这段文案在上传发起**之前**就写好了）。
    const waitFile = async (p, ms = 40000) => {
      const t0 = Date.now();
      while (Date.now() - t0 < ms) {
        if (existsSync(p)) return true;
        await sleep(200);
      }
      return false;
    };

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Log.enable').catch(() => {});

    // ---------- 打开资料页 ----------
    await send('Page.navigate', { url: BASE + '/' });
    await sleep(2500);
    await waitFor(`!!document.querySelector('#nav button')`, 20000);
    await evalJs(`(async () => { await go('docs'); return 'ok'; })()`);
    await sleep(1500);

    const zone = await evalJs(`(() => {
      const z = document.querySelector('#dz');
      if (!z) return { ok:false };
      const auto = document.querySelector('#dz-auto');
      const sub = document.querySelector('#im-sub');
      // 后注册的监听器读 defaultPrevented：能直接看到 bindDropzone 有没有拦
      window.__dzSeen = { dragover: null, drop: null };
      z.addEventListener('dragover', e => { window.__dzSeen.dragover = e.defaultPrevented; });
      z.addEventListener('drop', e => { window.__dzSeen.drop = e.defaultPrevented; });
      z.scrollIntoView({block:'center'});
      const r = z.getBoundingClientRect();
      return { ok:true, autoChecked: auto ? auto.checked : null,
               hasSub: !!sub, subValue: sub ? sub.value : null,
               x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2),
               text: (z.innerText||'').slice(0,200) };
    })()`);
    check('资料页渲染出拖放区 #dz（含立即入库勾选项与子文件夹输入框）',
      zone?.ok && zone.hasSub, JSON.stringify(zone).slice(0, 200));
    check('「上传后立即入库」默认**不勾**（拖完不会自动花钱识图）',
      zone?.autoChecked === false, 'checked=' + zone?.autoChecked);
    const shots = {};
    shots.zone = await shot('01-dropzone.png');

    // ---------- 真拖放 ----------
    // 每一轮拖放前都要 sleep 掉"上传完成后 2.6 秒自动刷新列表"那个窗口：
    // 刷新会重建 #dz 与 #im-sub（输入框的值也会被清空），踩在窗口里拖
    // 会把文件送到错误的目录，挂在这些元素上的探针监听器也会一起失效。
    const arm = async () => evalJs(`(() => {
      const z = document.querySelector('#dz');
      if (!z) return null;
      window.__dzSeen = { dragover: null, drop: null };
      z.addEventListener('dragover', e => { window.__dzSeen.dragover = e.defaultPrevented; });
      z.addEventListener('drop', e => { window.__dzSeen.drop = e.defaultPrevented; });
      z.scrollIntoView({block:'center'});
      const b = z.getBoundingClientRect();
      return { x: Math.round(b.left + b.width/2), y: Math.round(b.top + b.height/2) };
    })()`);
    // 上传完成后 2.6 秒会自动 go('docs') 重建整页（拖放区与 #im-sub 的值
    // 都会被换掉），期间 #view 是"加载中…"。这里必须等页面**稳定**：
    // 打标记 → 睡过一个刷新窗口 → 标记还在，才算稳。
    const awaitStableZone = async () => {
      for (let i = 0; i < 10; i++) {
        if (!await waitFor(`!!document.querySelector('#dz')`, 20000)) continue;
        await evalJs(`(() => { const z=document.querySelector('#dz');
          if (z) z.dataset.probeKeep='1'; return 'ok'; })()`);
        await sleep(3000);
        const still = await evalJs(`(() => { const z=document.querySelector('#dz');
          return !!(z && z.dataset.probeKeep === '1'); })()`);
        if (still) return true;
      }
      return false;
    };
    const setSub = async (v) => evalJs(
      `(() => { const s=document.querySelector('#im-sub'); if(!s) return null; s.value=${JSON.stringify(v)}; return s.value; })()`);
    const readLog = async () =>
      (await evalJs(`(document.querySelector('#dz-log')||{}).innerText||''`)) || '';
    const clearLog = async () =>
      evalJs(`(() => { const l=document.querySelector('#dz-log'); if(l) l.innerHTML=''; return 'ok'; })()`);
    const drag = async (files, pos) => {
      const data = { items: [], files: files, dragOperationsMask: 1 };
      await send('Input.dispatchDragEvent', { type: 'dragEnter', x: pos.x, y: pos.y, data });
      await sleep(150);
      await send('Input.dispatchDragEvent', { type: 'dragOver', x: pos.x, y: pos.y, data });
      await sleep(250);
      const seen = await evalJs(`(() => { const s = window.__dzSeen||{}; return {dragover:s.dragover, cls: (document.querySelector('#dz')||{}).className}; })()`);
      await send('Input.dispatchDragEvent', { type: 'drop', x: pos.x, y: pos.y, data });
      return seen;
    };

    await send('Input.setInterceptDrags', { enabled: true }).catch(() => {});
    check('拖放前页面已稳定（自动刷新窗口已经过去）', await awaitStableZone());
    const pos1 = await arm();
    check('拿到拖放区中心坐标', !!pos1, JSON.stringify(pos1));
    const seen = await drag([srcDeck, srcSheet], pos1);
    check('dragover 已被 preventDefault（浏览器不会自己打开这个文件）',
      seen?.dragover === true, JSON.stringify(seen));
    check('拖到区域上时整框高亮（.dz.on）', /(^|\s)on(\s|$)/.test(seen?.cls || ''),
      'class=' + seen?.cls);
    check('页面没被 file:// 顶掉（还是看板自己）',
      (await evalJs(`location.href`))?.startsWith(BASE) === true,
      await evalJs(`location.href`));

    // 上传是异步的：等落盘 + 提示
    const pDeck1 = join(rawDir, '探针讲义_1.pdf');
    const pSheet = join(rawDir, '探针表格.xls');
    const ok = await waitFor(
      `(() => { const t=(document.querySelector('#dz-log')||{}).innerText||''; return t.includes('已放进') && t.includes('待入库'); })()`,
      40000);
    check('落盘后页面出现「已放进 raw\\…」+「N 个待入库」提示', ok,
      (await evalJs(`(document.querySelector('#dz-log')||{}).innerText||''`))?.slice(0, 400));
    shots.afterDrop = await shot('02-after-drop.png');

    const gotDeck = await waitFile(pDeck1), gotSheet = await waitFile(pSheet);
    check('多文件一次拖入：两个都真落盘到 raw\\', gotDeck && gotSheet,
      `${gotDeck} / ${gotSheet}`);
    check('落盘字节与源文件逐字节一致',
      existsSync(pDeck1) && Buffer.compare(readFileSync(pDeck1), srcDeckBytes) === 0
      && existsSync(pSheet) && Buffer.compare(readFileSync(pSheet), srcSheetBytes) === 0,
      existsSync(pDeck1) ? `${statSync(pDeck1).size} / ${srcDeckBytes.length} 字节` : '缺文件');
    check('同名文件自动 _1，不覆盖 raw\\ 里已有的那份',
      existsSync(preexisting) && Buffer.compare(readFileSync(preexisting), preexistingBytes) === 0
      && existsSync(pDeck1));
    check('非课件类型（.xls）给出可操作提示（说明入库会跳过）',
      ((await evalJs(`(document.querySelector('#dz-log')||{}).innerText||''`)) || '').includes('不入库管线'));

    const apiRaw = await (await fetch(BASE + '/api/raw')).json();
    const f1 = (apiRaw.files || []).find((f) => f.name === '探针讲义_1.pdf');
    check('落盘后**没有自动入库**：文件在 /api/raw 里仍是未入库状态',
      f1 && f1.state !== 'indexed', f1 ? `state=${f1.state} supported=${f1.supported}` : '找不到文件');
    const autoBox = await evalJs(`(() => { const a=document.querySelector('#dz-auto'); return a? a.checked : null; })()`);
    check('拖完勾选项仍然没被自动勾上', autoBox === false, 'checked=' + autoBox);

    // ---------- 子文件夹：填 #im-sub 后再拖 ----------
    await awaitStableZone();
    const subSet = await setSub('教材');
    const pos2 = await arm();
    await drag([srcOther], pos2);
    const pOther = join(rawDir, '教材', '探针第二份.pdf');
    const gotOther = await waitFile(pOther);
    check('子文件夹输入框有值时，文件落进 raw\\教材\\',
      gotOther && Buffer.compare(readFileSync(pOther), srcOtherBytes) === 0,
      `sub=${subSet} 落盘=${gotOther} 误落 raw 根=${existsSync(join(rawDir, '探针第二份.pdf'))}`);

    // ---------- 超大文件：前端预检就该拦住，且一个字节都不传 ----------
    // 这条路用**注入式**：真 File 对象 + 覆盖 size，事件仍然从 #dz 的 drop
    // 监听器进，走的是同一段前端代码。不用 CDP 真拖 300MB 文件，是因为
    // DragData 只收文件路径，Chromium 未必肯为这么大的文件构造 DataTransfer
    //（试过：事件根本没送到页面）。后端那条路——Content-Length 超限——
    // 由 tools\verify_c0083_dragdrop.py 用真实 HTTP 请求单独验。
    await awaitStableZone();
    await setSub('');
    await clearLog();
    const bigRes = await evalJs(`(() => {
      const z = document.querySelector('#dz');
      if (!z) return 'no-zone';
      const f = new File([new Uint8Array([1,2,3,4])], '探针大文件.bin');
      Object.defineProperty(f, 'size', { get: () => 300 * 1024 * 1024 });
      const dt = new DataTransfer();
      dt.items.add(f);
      z.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
      return 'sent:' + dt.files.length + ':' + (dt.files[0] ? dt.files[0].size : -1);
    })()`);
    const bigMsg = await waitFor(`(() => { const t=(document.querySelector('#dz-log')||{}).innerText||''; return t.includes('超过单次上传上限'); })()`, 20000);
    const bigText = await readLog();
    check('超大文件给出可操作提示（点名文件 + 指路「方式一」）',
      bigMsg && bigText.includes('方式一') && bigText.includes('探针大文件.bin'),
      `${bigRes} | ${(bigText || '').slice(0, 160)}`);
    await sleep(600);
    check('超大文件一个字节都没传进 raw\\（前端预检就挡住了）',
      !existsSync(join(rawDir, '探针大文件.bin')));

    // ---------- 拖文件夹：走"请改用方式一"这条分支（注入式补测） ----------
    // 真拖拽没法把"目录"交给 CDP（DragData 只收文件路径），所以这条分支
    // 用一个**真的 DataTransfer** + 覆盖 files/items 来构造，事件仍然从
    // #dz 的 drop 监听器进，走的是同一段代码。
    await awaitStableZone();
    await clearLog();
    const folderMsg = await evalJs(`(() => {
      const z = document.querySelector('#dz');
      if (!z) return 'no-zone';
      const dt = new DataTransfer();
      Object.defineProperty(dt, 'files', { get: () => [] });
      Object.defineProperty(dt, 'items', { get: () => [
        { kind: 'file', webkitGetAsEntry: () => ({ isDirectory: true }) }] });
      z.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
      return 'sent';
    })()`);
    await sleep(700);
    const folderText = await readLog();
    check('拖文件夹给专门提示（说明浏览器不给目录树、改用方式一）',
      folderMsg === 'sent' && folderText.includes('文件夹') && folderText.includes('方式一'),
      (folderText || '').slice(0, 200));
    shots.final = await shot('03-final.png');

    // ---------- 回归：其它入口都还在 ----------
    const reg = await evalJs(`(() => {
      const btns = [...document.querySelectorAll('#view button')].map(b => (b.textContent||'').trim());
      return { imGo: !!document.querySelector('#im-go'), imSrc: !!document.querySelector('#im-src'),
               shOpen: !!document.querySelector('#sh-open'), batch: !!document.querySelector('#batch-list'),
               ingestAll: btns.some(x => x.includes('入库全部待处理文件')) };
    })()`);
    check('回归：方式一（#im-src/#im-go）、分享包入口、批量入库都还在',
      reg.imGo && reg.imSrc && reg.shOpen && reg.ingestAll, JSON.stringify(reg));

    check('整个拖放过程 console 零 error', errors.length === 0, errors.slice(0, 3).join(' || '));

    const passed = checks.filter((c) => c.ok).length;
    const failed = checks.filter((c) => !c.ok).map((c) => c.name);
    const conclusion = `C0083-PROBE ${passed === checks.length ? 'PASS' : 'FAIL'} ${passed}/${checks.length}`;
    const out = {
      card: 'C-0083', kind: 'browser-probe', ranAt: new Date().toISOString(),
      base: BASE, port: PORT, tmp: TMP,
      passed: passed === checks.length, conclusion,
      checks, failed, shots, errors,
    };
    if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(out, null, 2), 'utf8');
    console.log(conclusion);
    console.log(`证据：raw\\ = ${rawDir}`);
    if (JSONOUT) console.log('结果 JSON：' + JSONOUT);
    return passed === checks.length ? 0 : 1;
  } finally {
    try { edge?.kill(); } catch { /* ignore */ }
    try { srv.kill(); } catch { /* ignore */ }
    await sleep(400);
    // profile 可能还被没退干净的 Edge 占着：删不掉就算了，别把结论搞砸
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ }
  }
}

main().then((c) => process.exit(c)).catch((e) => {
  console.log('[ERROR] ' + (e && e.message ? e.message : e));
  process.exit(2);
});
