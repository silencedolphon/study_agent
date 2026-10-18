// 卡 C-0085 探针：**真浏览器**验四类重命名（headless Edge + CDP，不是 DOM 桩）。
//
// 验的事（全部走页面上的真实点击，不直接调接口代替）：
//   1. 顶栏学科按钮 → 学科菜单里的「✎ 重命名学科…」→ 弹窗里写明"只改显示名、目录不动"
//      → 选学科 + 输新名 → 确定 → **顶栏与菜单当场显示新名**，且刷新页面后仍是新名；
//   2. 「资料」页已入库那一行的「改名」→ 弹窗输入新文件名 → **先出现 dry_run 预览**
//      （库记录与页图目录的改前/改后）→ 确定 → 列表里显示新文件名，
//      且 `/img/slide` 仍能取到页图（页图目录跟着改名了，不是"图全丢"）；
//   3. 同一行的「改标题」→ 弹窗写明"产物里的名字不会跟着变" → 确定 → 列表标题变新值；
//   4. 「知识点」页签选中课件 → 「改产物标题」→ 弹窗列出产物里那**四处**当前值
//      → 确定 → 思维导图根节点当场变成新名（页面真的重渲染，不是缓存里的旧值）；
//   5. 整个过程 console 零 error。
//
// 一条命令跑完（不给 --port 时自己起临时看板 + 自造夹具，跑完自己收）：
//   node tools\probe_c0085_rename.mjs --json <结果json> --outdir <截图目录>
// 也可以挂到已经起好的临时看板上：
//   node tools\probe_c0085_rename.mjs --port 8123 --data <临时数据根> --json <结果json>
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function freePort() {
  return new Promise((res, rej) => {
    const s = createServer();
    s.on('error', rej);
    s.listen(0, '127.0.0.1', () => {
      const p = s.address().port;
      s.close(() => res(p));
    });
  });
}

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const OUTDIR = arg('outdir', '');
const JSONOUT = arg('json', '');
const GIVEN_PORT = parseInt(arg('port', '0'), 10) || 0;
const DATA = resolve(arg('data', join(ROOT, '_tmp_c0085', 'probe_data')));
const PORT = GIVEN_PORT || await freePort();
const CDP_PORT = parseInt(arg('cdp', '0'), 10) || await freePort();
const BASE = `http://127.0.0.1:${PORT}`;

const PY = join(ROOT, 'venv', 'Scripts', 'python.exe');
const EDGE = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
].find((p) => existsSync(p));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const checks = [];
const check = (name, ok, detail) => {
  checks.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
  if (!ok) console.log('[FAIL] ' + name + (detail ? ' | ' + detail : ''));
  return !!ok;
};

async function api(path, opts) {
  const r = await fetch(BASE + path, opts);
  return { status: r.status, body: await r.json().catch(() => null) };
}

async function main() {
  if (!existsSync(PY)) throw new Error('找不到 venv 解释器：' + PY);
  if (!EDGE) throw new Error('找不到 msedge.exe');
  if (OUTDIR) mkdirSync(OUTDIR, { recursive: true });

  let srv = null;
  let edge = null;
  let profile = '';
  const out = { checks, screenshots: {}, errors: [], base: BASE, data: DATA };
  try {
    // ---------- 没给端口就自己起一套（夹具 + 临时看板） ----------
    if (!GIVEN_PORT) {
      rmSync(DATA, { recursive: true, force: true });
      mkdirSync(DATA, { recursive: true });
      const fx = spawnSync(PY, [join(ROOT, 'tools', 'verify_c0085_fixture.py'), DATA],
        { cwd: ROOT, encoding: 'utf8' });
      if (fx.status !== 0) throw new Error('夹具生成失败：' + (fx.stderr || '').slice(0, 800));
      out.fixture = JSON.parse(readFileSync(join(DATA, 'fixture_manifest.json'), 'utf8'));
      srv = spawn(PY, [join(ROOT, 'tools', 'c0085_tmp_launch.py'), DATA, String(PORT)],
        { cwd: ROOT, stdio: 'ignore' });
      let ready = false;
      for (let i = 0; i < 120; i++) {
        try { const r = await fetch(BASE + '/'); if (r.ok) { ready = true; break; } }
        catch { /* 还没起来 */ }
        await sleep(300);
      }
      if (!ready) throw new Error('临时看板没起来：' + BASE);
    }

    // ---------- 起 headless Edge + CDP ----------
    profile = join(DATA, '..', `probe_c0085_edge_${CDP_PORT}`);
    rmSync(profile, { recursive: true, force: true });
    edge = spawn(EDGE, ['--headless=new', `--remote-debugging-port=${CDP_PORT}`,
      `--user-data-dir=${profile}`, '--no-first-run', '--disable-gpu',
      '--window-size=1500,1300', 'about:blank'], { stdio: 'ignore' });
    let list = null;
    for (let i = 0; i < 100; i++) {
      try {
        const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        list = await r.json();
        if (list?.length) break;
      } catch { /* 还没起来 */ }
      await sleep(300);
    }
    if (!list?.length) throw new Error(`CDP 未就绪（端口 ${CDP_PORT}）`);
    const target = list.find((t) => t.type === 'page') || list[0];
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

    let id = 0;
    const pending = new Map();
    const errors = out.errors;
    const send = (method, params = {}) => new Promise((res) => {
      const myId = ++id;
      pending.set(myId, res);
      ws.send(JSON.stringify({ id: myId, method, params }));
    });
    ws.onmessage = async (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
      if (m.method === 'Log.entryAdded' && m.params?.entry?.level === 'error') {
        const t = m.params.entry.text || '';
        const u = m.params.entry.url || '';
        if (!/favicon/i.test(t) && !/favicon/i.test(u)) errors.push(u ? `${t} @ ${u}` : t);
      } else if (m.method === 'Runtime.exceptionThrown') {
        errors.push(m.params?.exceptionDetails?.text || 'exception');
      } else if (m.method === 'Page.javascriptDialogOpening') {
        await send('Page.handleJavaScriptDialog', { accept: true });
      }
    };
    const evalJs = async (expr) => {
      const r = await send('Runtime.evaluate',
        { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result?.exceptionDetails) {
        return { __err: r.result.exceptionDetails.text || 'eval error' };
      }
      return r.result?.result?.value;
    };
    const shot = async (name) => {
      if (!OUTDIR) return null;
      const s = await send('Page.captureScreenshot', { format: 'png' });
      if (s.result?.data) {
        const p = join(OUTDIR, name);
        writeFileSync(p, Buffer.from(s.result.data, 'base64'));
        out.screenshots[name] = p;
        return p;
      }
      return null;
    };
    const waitFor = async (expr, ms = 25000) => {
      const t0 = Date.now();
      while (Date.now() - t0 < ms) {
        if (await evalJs(expr)) return true;
        await sleep(250);
      }
      return false;
    };
    const click = (sel) => evalJs(
      `(() => { const el = document.querySelector(${JSON.stringify(sel)});
                if (!el) return false; el.click(); return true; })()`);
    const setVal = (sel, v) => evalJs(
      `(() => { const el = document.querySelector(${JSON.stringify(sel)});
                if (!el) return false; el.value = ${JSON.stringify(v)};
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true})); return true; })()`);
    const modalText = () => evalJs(
      `(() => { const b = document.querySelector('#modalbox');
                return b ? (b.innerText || '') : ''; })()`);

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Log.enable').catch(() => {});
    await send('Page.navigate', { url: BASE + '/' });
    await sleep(2600);
    await waitFor(`!!document.querySelector('#nav button')`, 25000);

    // ---------- 0. 基线（与前面跑过的后端流程无关，全从当前状态读） ----------
    const docs0 = await api('/api/docs');
    const med0 = (docs0.body?.documents || []).find((d) => d.rel === '05. Med Chem.pptx'
      || /05\. Med Chem/.test(d.rel || ''));
    check('资料页基线：夹具那份课件在库里（doc#）', !!med0,
      JSON.stringify((docs0.body?.documents || []).map((d) => d.rel)));
    const MED = med0 ? med0.id : 0;
    const relBase = med0 ? med0.rel : '';
    const titleBase = med0 ? (med0.title || '') : '';
    const subj0 = await api('/api/subjects');
    const curSlug = subj0.body?.current || 'default';
    const curEntry = (subj0.body?.subjects || []).find((s) => s.slug === curSlug) || {};
    const nameBase = curEntry.name || '';
    const stamp = String(Date.now()).slice(-6);

    // ================================================================ ① 学科库改名
    await click('#subj-btn');
    const menuOk = await waitFor(`!!document.querySelector('#subj-ren')`, 15000);
    check('学科菜单里有「✎ 重命名学科…」入口', menuOk);
    out.screenshots.menu = await shot('01-subj-menu.png');
    await click('#subj-ren');
    const dlgOk = await waitFor(`!!document.querySelector('#sr-name')`, 15000);
    const dlgText = await modalText();
    check('点它弹出「重命名学科知识库」弹窗，且写明**只改显示名、目录不动**',
      dlgOk && /重命名学科知识库/.test(dlgText) && /目录名/.test(dlgText)
      && /不受影响|不会搬/.test(dlgText), dlgText.slice(0, 200).replace(/\n/g, ' '));
    const newSubjName = `${nameBase}-页面改名${stamp}`;
    await setVal('#sr-name', newSubjName);
    await click('#sr-ok');
    const subjDone = await waitFor(
      `(document.querySelector('#subj-name')||{}).textContent === ${JSON.stringify(newSubjName)}`,
      20000);
    check('确定后**顶栏当场显示新学科名**（不需要重启看板）', subjDone,
      await evalJs(`(document.querySelector('#subj-name')||{}).textContent`));
    const subjApi = await api('/api/subjects');
    const curNew = (subjApi.body?.subjects || []).find((s) => s.slug === curSlug) || {};
    check('接口侧也是新名，且**目录没变**（改名不搬库）',
      curNew.name === newSubjName && curNew.dir === curEntry.dir,
      `name=${curNew.name} dir 同=${curNew.dir === curEntry.dir}`);
    await click('#subj-btn');
    await waitFor(`!!document.querySelector('#subj-menu.on')`, 10000);
    const menuName = await evalJs(
      `(() => { const b = document.querySelector('#subj-menu .sitem.on .snm');
                return b ? b.textContent : ''; })()`);
    check('学科菜单里那一项也显示新名', menuName === newSubjName, menuName);
    await evalJs(`(() => { document.querySelector('#subj-menu').classList.remove('on'); return 1; })()`);
    // 刷新：改名必须**落盘**，不是只改了内存
    await send('Page.navigate', { url: BASE + '/' });
    await sleep(2400);
    await waitFor(`!!document.querySelector('#subj-name')`, 20000);
    await sleep(800);
    const afterReload = await evalJs(`(document.querySelector('#subj-name')||{}).textContent`);
    check('刷新页面后顶栏仍是新名（改名真的落盘了）', afterReload === newSubjName, afterReload);

    // ================================================================ ② 原文件改名
    await evalJs(`(async () => { await go('docs'); return 'ok'; })()`);
    await waitFor(`!!document.querySelector('.ds-cb')`, 25000);
    await sleep(600);
    const rowSel = `tr:has(.ds-cb[data-doc="${MED}"])`;
    const hasRow = await evalJs(`!!document.querySelector(${JSON.stringify(rowSel)})`);
    check('资料页里能找到夹具那一行（doc#' + MED + '）', hasRow);
    const hasRename = await evalJs(
      `!!document.querySelector(${JSON.stringify(rowSel + ' button[onclick*="renameRawFile"]')})`);
    check('该行有「改名」按钮（改 raw 目录里的文件名）', hasRename);
    out.screenshots.docs = await shot('02-docs-row.png');
    await evalJs(`document.querySelector(${JSON.stringify(rowSel + ' button[onclick*="renameRawFile"]')}).click()`);
    const rnOk = await waitFor(`!!document.querySelector('#rn-name')`, 15000);
    const rnText = await modalText();
    check('弹出「重命名原文件」弹窗，且写明四处连带（文件 / 库路径 / 页图目录 / 扩展名不能改）',
      rnOk && /重命名原文件/.test(rnText) && /页图目录/.test(rnText)
      && /扩展名/.test(rnText), rnText.slice(0, 240).replace(/\n/g, ' '));
    const ext = (relBase.match(/\.[^.]+$/) || ['.pptx'])[0];
    const newFileName = `05. Med Chem-页面改名${stamp}${ext}`;
    await setVal('#rn-name', newFileName);
    const prevOk = await waitFor(
      `/预览/.test((document.querySelector('#rn-prev')||{}).textContent||'')`, 20000);
    const prevText = await evalJs(`(document.querySelector('#rn-prev')||{}).textContent`);
    check('输入新名后**先出 dry_run 预览**（库记录 + 页图目录的改前/改后）',
      prevOk && /→/.test(prevText || ''), (prevText || '').slice(0, 200));
    out.screenshots.renameDialog = await shot('03-rename-dialog.png');
    await click('#rn-ok');
    // 弹窗"关闭"的判据是 `#modal` 掉了 `.on` 类——`closeModal()` **不清空** #modalbox
    // 的 innerHTML，所以 #rn-name 这种元素会留在 DOM 里（第一版探针就是这么误判的）。
    const rnDone = await waitFor(`!document.querySelector('#modal.on')`, 25000);
    check('确定后弹窗关闭、列表当场刷新', rnDone);
    await sleep(900);
    const docs1 = await api('/api/docs');
    const med1 = (docs1.body?.documents || []).find((d) => d.id === MED) || {};
    check('接口侧 rel 已变成新文件名（库里路径同步改了）',
      med1.rel === newFileName, `rel=${med1.rel}`);
    const shown = await evalJs(
      `(() => { const t = document.querySelector(${JSON.stringify(rowSel)});
                return t ? (t.innerText || '') : ''; })()`);
    check('页面上那一行显示的是新文件名', (shown || '').includes(newFileName),
      (shown || '').slice(0, 160).replace(/\n/g, ' '));
    // 页图是**页面自己**加载出来的（不是 Node 侧代查）：真 <img> 能 onload 才算数
    const imgOk = await evalJs(`(async () => await new Promise(res => {
      const im = new Image();
      im.onload = () => res({ ok: true, w: im.naturalWidth, h: im.naturalHeight });
      im.onerror = () => res({ ok: false, w: 0, h: 0 });
      im.src = '/img/slide?doc_id=${MED}&page=1&t=' + Date.now();
      setTimeout(() => res({ ok: false, w: 0, h: 0, timeout: true }), 15000);
    }))()`);
    check('改名后页面里仍能**真的加载出页图**（页图目录跟着改名了，不是"图全丢"）',
      imgOk?.ok && imgOk.w > 0, JSON.stringify(imgOk));

    // ================================================================ ③ 课件标题改名
    const hasTitleBtn = await evalJs(
      `!!document.querySelector(${JSON.stringify(rowSel + ' button[onclick*="renameDocTitle"]')})`);
    check('该行还有「改标题」按钮（改 documents.title）', hasTitleBtn);
    await evalJs(`document.querySelector(${JSON.stringify(rowSel + ' button[onclick*="renameDocTitle"]')}).click()`);
    const dtOk = await waitFor(`!!document.querySelector('#dt-name')`, 15000);
    const dtText = await modalText();
    check('弹出「改课件标题」弹窗，且写明产物里的名字**不会跟着变**',
      dtOk && /改课件标题/.test(dtText) && /不会跟着变/.test(dtText),
      dtText.slice(0, 220).replace(/\n/g, ' '));
    const newTitle = `${titleBase}-页面改标题${stamp}`;
    await setVal('#dt-name', newTitle);
    await sleep(400);          // 让那一句"产物里现在叫什么"的补充提示先回来
    await click('#dt-ok');
    await waitFor(`!document.querySelector('#modal.on')`, 20000);
    await sleep(900);
    const docs2 = await api('/api/docs');
    const med2 = (docs2.body?.documents || []).find((d) => d.id === MED) || {};
    check('确定后库里标题变成新值', med2.title === newTitle,
      `title=${med2.title} err=${await evalJs(`(document.querySelector('#dt-err')||{}).textContent`)}`);
    const shown2 = await evalJs(
      `(() => { const t = document.querySelector(${JSON.stringify(rowSel)});
                return t ? (t.innerText || '') : ''; })()`);
    check('页面上那一行也显示新标题', (shown2 || '').includes(newTitle),
      (shown2 || '').slice(0, 160).replace(/\n/g, ' '));

    // ================================================================ ④ 产物标题改名
    // ⚠ 知识点页签在 `VIEWS` 里的名字是 `kps`（不是 mindmap）；写错了 `go()` 会把
    //   `#view` 换成一句"出错：…"，后面所有元素都找不到（探针第一版就踩了这个）。
    await evalJs(`(async () => { await go('kps'); return 'ok'; })()`);
    const mmOk = await waitFor(`!!document.querySelector('#mm-ren-art')`, 30000);
    check('知识点页签里有「改产物标题」按钮', mmOk);
    const picked = await evalJs(`(() => { const s = document.querySelector('#mm-doc');
      if (!s) return null; s.value = ${JSON.stringify(String(MED))};
      s.dispatchEvent(new Event('change', {bubbles:true})); return s.value; })()`);
    check('课件下拉切到夹具那一份（触发重画）', String(picked) === String(MED), 'value=' + picked);
    await sleep(1500);
    await click('#mm-ren-art');
    const atOk = await waitFor(`!!document.querySelector('#at-name')`, 20000);
    const atText = await modalText();
    check('弹出「改产物标题」弹窗，且把产物里那**四处**当前值都摆出来',
      atOk && /structure\.root\.name/.test(atText) && /structure\.raw_root\.name/.test(atText)
      && /structure\.nodes\[0\]\.name/.test(atText) && /meta\.deck/.test(atText),
      atText.slice(0, 160).replace(/\n/g, ' '));
    check('弹窗写明"知识点关联一个字都不动"与"自动备份"',
      /知识点关联/.test(atText) && /备份/.test(atText));
    out.screenshots.artifactDialog = await shot('04-artifact-dialog.png');
    const newArt = `Medicinal Chemistry-页面改名${stamp}`;
    await setVal('#at-name', newArt);
    await click('#at-ok');
    const atDone = await waitFor(`!document.querySelector('#modal.on')`, 25000);
    check('确定后弹窗关闭', atDone);
    await sleep(1500);
    const mm = await api(`/api/mindmap?doc_id=${MED}`);
    const rootName = (mm.body?.tree || [])[0]?.name;
    check('接口侧思维导图根名已变（四处同步的结果）', rootName === newArt, 'root=' + rootName);
    const treeText = await evalJs(
      `(() => { const t = document.querySelector('#mm-tree');
                return t ? (t.innerText || '') : ''; })()`);
    check('页面上思维导图**当场显示新根名**（缓存没把旧值顶回来）',
      (treeText || '').includes(newArt), (treeText || '').slice(0, 160).replace(/\n/g, ' '));
    out.screenshots.mindmap = await shot('05-mindmap-after.png');
    const docsJson = JSON.parse(readFileSync(join(DATA, 'fixture_manifest.json'), 'utf8'));
    const artFile = join(docsJson.log_dir, 'knowledge', `${MED}-末级知识点总结.json`);
    const artObj = JSON.parse(readFileSync(artFile, 'utf8'));
    const spots = {
      root: artObj?.structure?.root?.name,
      raw_root: artObj?.structure?.raw_root?.name,
      node0: artObj?.structure?.nodes?.[0]?.name,
      deck: artObj?.meta?.deck,
    };
    check('磁盘产物里四处**都是**新名（不是只改了页面看到的那一处）',
      Object.values(spots).every((v) => v === newArt), JSON.stringify(spots));

    // ================================================================ ⑤ 刷新后全局复核
    // 卡片验收标准点名的最后一段：**输入新名 → 提交 → 刷新 → 全局显示新名**。
    // 刷新会丢掉所有前端内存态，能活下来的只有"真的落盘了"的那些。
    await send('Page.navigate', { url: BASE + '/' });
    await sleep(2600);
    await waitFor(`!!document.querySelector('#nav button')`, 25000);
    await sleep(900);
    const sName2 = await evalJs(`(document.querySelector('#subj-name')||{}).textContent`);
    check('刷新后：顶栏学科名仍是新名（学科改名已落盘）', sName2 === newSubjName, sName2);
    await evalJs(`(async () => { await go('docs'); return 'ok'; })()`);
    await waitFor(`!!document.querySelector('.ds-cb')`, 25000);
    await sleep(800);
    const rowAfter = await evalJs(`(() => { const t = document.querySelector(${JSON.stringify(rowSel)});
      return t ? (t.innerText || '') : ''; })()`);
    check('刷新后：资料页那一行仍是**新文件名**',
      (rowAfter || '').includes(newFileName), (rowAfter || '').slice(0, 150).replace(/\n/g, ' '));
    check('刷新后：资料页那一行仍是**新标题**（documents.title 已落库）',
      (rowAfter || '').includes(newTitle), (rowAfter || '').slice(0, 150).replace(/\n/g, ' '));
    await evalJs(`(async () => { await go('kps'); return 'ok'; })()`);
    await waitFor(`!!document.querySelector('#mm-ren-art')`, 30000);
    await evalJs(`(() => { const s = document.querySelector('#mm-doc');
      if (s) { s.value = ${JSON.stringify(String(MED))};
               s.dispatchEvent(new Event('change', {bubbles:true})); } return 1; })()`);
    await sleep(1800);
    const treeAfter = await evalJs(`(document.querySelector('#mm-tree')||{}).innerText || ''`);
    check('刷新后：思维导图根名仍是新名（产物四处同步已落盘）',
      (treeAfter || '').includes(newArt), (treeAfter || '').slice(0, 150).replace(/\n/g, ' '));
    out.screenshots.afterReload = await shot('06-after-reload.png');
  } finally {
    try { edge?.kill(); } catch { /* ignore */ }
    try { srv?.kill(); } catch { /* ignore */ }
    // Edge 的文件句柄常常晚一拍才释放：清不掉 profile 不算失败（它只是临时目录），
    // 这里绝不能让它把整轮结论带成"探针异常"。
    await sleep(400);
    try { if (profile) rmSync(profile, { recursive: true, force: true }); }
    catch { /* 留着就留着，系统会清 %TEMP% 之外的临时目录由人处理 */ }
  }

  const ok = checks.filter((c) => c.ok).length;
  const head = `卡 C-0085 浏览器探针：${ok}/${checks.length} 通过，JS 错误 ${out.errors.length} 条`;
  console.log(head);
  console.log('看板：' + BASE);
  if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(out, null, 2));
  return checks.every((c) => c.ok) && out.errors.length === 0 ? 0 : 1;
}

process.exit(await main().catch((e) => { console.log('[FAIL] 探针异常：' + e.message); return 1; }));
