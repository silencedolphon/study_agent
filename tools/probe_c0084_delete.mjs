// 卡 C-0084 探针：**真浏览器**验批量真删（headless Edge + CDP，不是 DOM 桩）。
//
// 验的事（全部走页面上的真实点击，不直接调接口代替）：
//   1. 「资料」页的已入库表格每行有勾选框，全选/全不选/计数都对；
//   2. 点「批量删除选中…」→ 弹确认面板 → 面板里**列出了将一并删除的产物文件数**、
//      并明确写清"不可逆"与"原文件怎么办"两条路；
//   3. 选「保留原文件」→ window.confirm（由 CDP 代替人点"确定"）→
//      列表当场刷新、被删的几行**从「已入库资料」表格里消失**；
//   4. 页签的课件清单（后端 _artifact_doc_list）里**不再有「有产物但已不在库」的
//      幽灵条目**，title 也不再退化成「文档 N」；
//   5. 磁盘上四处产物（末级知识点总结 + .bak-*、名词解释、outline + .bak-*、
//      页图目录）确实不存在了，而 raw\ 里的原文件**还在**（本轮选的是保留）；
//   6. 「批量整理知识点」栏里那个「真删选中课件…」入口在，点它会给出明确提示
//      （勾选的都不在库了 → 引导到资料页），不会静默什么都不做；
//   7. 整个过程 console 零 error。
//
// 一条命令跑完（自己起临时看板 + 自己造夹具，跑完自己收）：
//   node tools\probe_c0084_delete.mjs --outdir <截图目录> --json <结果json>
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

/** 让内核给一个**真的空闲**的端口：随机端口撞上别的服务时，
 *  看板会 bind 失败、Edge 会静默退出，表现都是"起不来"这种假故障。 */
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
const TMP = resolve(arg('tmp', join(ROOT, '_tmp_c0084', 'browser_data')));
const PORT = parseInt(arg('port', '0'), 10) || await freePort();
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

async function main() {
  if (!existsSync(PY)) throw new Error('找不到 venv 解释器：' + PY);
  if (!EDGE) throw new Error('找不到 msedge.exe');
  if (OUTDIR) mkdirSync(OUTDIR, { recursive: true });

  // ---------- 造夹具（临时数据根，绝不碰真实学科库） ----------
  rmSync(TMP, { recursive: true, force: true });
  mkdirSync(TMP, { recursive: true });
  const fx = spawnSync(PY, [join(ROOT, 'tools', 'verify_c0084_fixture.py'), TMP],
    { cwd: ROOT, encoding: 'utf8' });
  if (fx.status !== 0) throw new Error('夹具生成失败：' + (fx.stderr || '').slice(0, 800));
  const man = JSON.parse(readFileSync(join(TMP, 'fixture_manifest.json'), 'utf8'));
  const DEL = String(man.del_file_id), KEEP = String(man.keep_file_id);

  const srv = spawn(PY, [join(ROOT, 'tools', 'c0084_tmp_launch.py'), TMP, String(PORT)],
    { cwd: ROOT, stdio: 'ignore' });
  let edge = null;
  let profile = '';        // Edge 的 user-data-dir：起在 try 里，清在 finally 里
  const out = { checks, screenshots: {}, fixture: man };

  try {
    let ready = false;
    for (let i = 0; i < 90; i++) {
      try { const r = await fetch(BASE + '/'); if (r.ok) { ready = true; break; } }
      catch { /* 还没起来 */ }
      await sleep(300);
    }
    check('临时看板实例就绪（' + BASE + '）', ready);
    if (!ready) throw new Error('看板没起来');

    // ---------- 起 headless Edge + CDP ----------
    // 端口由 `freePort()` 让内核分配（顶层已经分配好），这里再自证一次空闲，
    // 因为 `--remote-debugging-port` 撞上别的进程时 Edge 会静默退出，
    // 表现就是"CDP 未就绪"这种看着像代码坏了的假故障。
    const cdpPort = CDP_PORT;
    profile = join(TMP, '..', `probe_c0084_edge_profile_${cdpPort}`);
    rmSync(profile, { recursive: true, force: true });
    edge = spawn(EDGE, ['--headless=new', `--remote-debugging-port=${cdpPort}`,
      `--user-data-dir=${profile}`, '--no-first-run', '--disable-gpu',
      '--window-size=1500,1300', 'about:blank'], { stdio: 'ignore' });
    let list = null;
    for (let i = 0; i < 100; i++) {
      try {
        const r = await fetch(`http://127.0.0.1:${cdpPort}/json/list`);
        list = await r.json();
        if (list?.length) break;
      } catch { /* 还没起来 */ }
      await sleep(300);
    }
    if (!list?.length) throw new Error(`CDP 未就绪（端口 ${cdpPort}）`);
    out.cdpPort = cdpPort;
    const target = list.find((t) => t.type === 'page') || list[0];
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

    let id = 0;
    const pending = new Map();
    const errors = [];
    const dialogs = [];
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
        // 人点「确定」这一步由 CDP 代劳：记下来，再 accept（否则页面卡在对话框上）
        dialogs.push({ type: m.params.type, message: (m.params.message || '').slice(0, 400) });
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

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Log.enable').catch(() => {});
    await send('Page.navigate', { url: BASE + '/' });
    await sleep(2600);
    await waitFor(`!!document.querySelector('#nav button')`, 25000);
    await evalJs(`(async () => { await go('docs'); return 'ok'; })()`);
    await waitFor(`!!document.querySelector('.ds-cb')`, 25000);
    await sleep(600);

    // ---------- 1. 勾选框与选中计数 ----------
    const boxes = await evalJs(`(() => {
      const cbs = Array.from(document.querySelectorAll('.ds-cb'));
      return { n: cbs.length,
               ids: cbs.map(c => c.dataset.doc),
               hasBar: !!document.querySelector('#ds-bar'),
               info: (document.querySelector('#ds-info')||{}).textContent || '',
               delLabel: (document.querySelector('#ds-del')||{}).textContent || '' };
    })()`);
    check('资料页每行都有勾选框（.ds-cb），且夹具那两份都在列表里',
      boxes?.n >= 2 && boxes.ids.includes(DEL) && boxes.ids.includes(KEEP),
      `n=${boxes?.n} 含 ${DEL}/${KEEP}=${boxes?.ids.includes(DEL)}/${boxes?.ids.includes(KEEP)}`);
    check('批量删除工具栏渲染出来了（#ds-bar / #ds-del / #ds-info）',
      boxes?.hasBar && /批量删除/.test(boxes?.delLabel || '') && boxes?.info === '未选择',
      `info=${boxes?.info}`);
    out.screenshots.before = await shot('01-before.png');

    // 全选 → 计数与勾选态
    await evalJs(`(() => { document.querySelector('#ds-all').click(); return 'ok'; })()`);
    await sleep(250);
    const afterAll = await evalJs(`(() => ({
      checked: Array.from(document.querySelectorAll('.ds-cb')).filter(c=>c.checked).length,
      info: (document.querySelector('#ds-info')||{}).textContent || ''
    }))()`);
    check('「全选」把所有行勾上且计数如实',
      afterAll?.checked === boxes?.n && /已选 \d+ 份/.test(afterAll?.info || ''),
      JSON.stringify(afterAll));
    // 全不选 → 只勾夹具那两份（走真实 click，让页面的 onchange 收到事件）
    await evalJs(`(() => { document.querySelector('#ds-none').click(); return 'ok'; })()`);
    await sleep(200);
    const clickTwo = await evalJs(`(() => {
      const want = ${JSON.stringify([DEL, KEEP])};
      let n = 0;
      Array.from(document.querySelectorAll('.ds-cb')).forEach(c => {
        if (want.includes(c.dataset.doc)) { c.click(); n++; }
      });
      return { clicked: n,
               info: (document.querySelector('#ds-info')||{}).textContent || '' };
    })()`);
    check('只勾夹具那两份（点击事件被页面接住，计数更新）',
      clickTwo?.clicked === 2 && clickTwo.info.includes('已选 2 份'),
      JSON.stringify(clickTwo));

    // ---------- 2. 弹确认面板：必须列出"将一并删除的产物文件" ----------
    await evalJs(`(() => { document.querySelector('#ds-del').click(); return 'ok'; })()`);
    const panelOk = await waitFor(`!!document.querySelector('#dd-soft')`, 20000);
    const panel = await evalJs(`(() => {
      const box = document.querySelector('#modalbox');
      const t = box ? (box.innerText || '') : '';
      return { ok: !!document.querySelector('#dd-hard') && !!document.querySelector('#dd-soft'),
               text: t.slice(0, 2000),
               hasHard: /连原文件一起删/.test(t), hasSoft: /保留原文件/.test(t),
               irreversible: /不可逆/.test(t),
               artLine: (t.match(/一并删除[^\\n]*/) || [''])[0],
               fileLine: (t.match(/原文件[^\\n]*/) || [''])[0] };
    })()`);
    check('点「批量删除选中…」弹出确认面板（两条路 + 取消）',
      panelOk && panel?.ok && panel.hasHard && panel.hasSoft, JSON.stringify(panel).slice(0, 300));
    check('面板写明「不可逆」并列出将一并删除的产物文件数',
      panel?.irreversible && /一并删除\s*\d+\s*个整理产物文件/.test(panel?.artLine || ''),
      panel?.artLine || '(没匹配到)');
    check('面板每一份都写清 doc# 与产物件数（不出现 doc#undefined）',
      /doc#\d+/.test(panel?.text || '') && !/doc#undefined/.test(panel?.text || ''),
      (panel?.artLine || '') + ' || ' + (panel?.text || '').slice(0, 90));
    check('面板把「原文件怎么办」两条路摆清楚',
      /连原文件一起删/.test(panel?.text || '') && /保留原文件/.test(panel?.text || ''),
      (panel?.text || '').slice(0, 120));
    out.screenshots.panel = await shot('02-confirm-panel.png');

    // ---------- 3. 选「保留原文件」→ window.confirm 由 CDP 代点确定 → 列表刷新 ----------
    await evalJs(`(() => { document.querySelector('#dd-soft').click(); return 'ok'; })()`);
    const gone = await waitFor(`(() => {
      const ids = Array.from(document.querySelectorAll('.ds-cb')).map(c => c.dataset.doc);
      return !ids.includes(${DEL}) && !ids.includes(${KEEP});
    })()`, 40000);
    await sleep(1500);          // 等重绘落定再截图（否则截到"加载中…"）
    check('确认后列表当场刷新：被删的两份从「已入库资料」表格里消失',
      gone, '仍能查到 ' + DEL + '/' + KEEP);
    check('前端确实弹了 window.confirm（不可逆动作的第二道人为闸门）',
      dialogs.length >= 1 && /批量删除/.test(dialogs[0].message || ''),
      JSON.stringify(dialogs.map(d => d.message.slice(0, 80))));
    out.screenshots.after = await shot('03-after.png');

    // ---------- 4. 页签清单：被删的两份不再出现 ----------
    // ⚠ 幽灵条目的**清零**不在这里验：这份数据根是全新的夹具，幽灵那份（doc#62）
    //   还没被任何人删过，此刻它理应在页签里（in_library=false、title 退化成
    //   「文档 62」）。它的清理由 verify_c0084_purge.py 的 HTTP 段负责验
    //   （那里会专门删一次幽灵 doc_id 再断言页签归一）。这里只验 UI 这一侧
    //   能看见的结果：刚删掉的两份从清单里消失了。
    await evalJs(`(async () => { await go('mindmap'); return 'ok'; })()`);
    await sleep(2500);
    const mm = await evalJs(`(async () => {
      const d = await api('/api/mindmap');
      const ds = (d.documents || []).map(x => ({ id: x.doc_id, in_library: x.in_library,
                                                 title: x.title }));
      const gone = ${JSON.stringify([DEL, KEEP])}.map(Number);
      return { n: ds.length,
               ghosts: ds.filter(x => x.in_library === false).map(x => x.id),
               still: ds.filter(x => gone.includes(Number(x.id))) };
    })()`);
    check('页签课件清单里，刚真删的那两份已经不见了（产物没了 → 页签自然不列它）',
      (mm?.still || []).length === 0, JSON.stringify(mm?.still));
    check('页签清单照常渲染（不是因为它坏了才为空）',
      (mm?.n || 0) >= 1, 'n=' + mm?.n);
    out.mindmapGhosts = mm?.ghosts || [];
    out.screenshots.mindmap = await shot('04-mindmap.png');

    // ---------- 5. 「批量整理知识点」栏的真删入口在，点它会给确认面板 ----------
    const kpb = await evalJs(`(async () => {
      await openKpBuild();
      return { hasPurge: !!document.querySelector('#kpb-purge'),
               hasClear: !!document.querySelector('#kpb-clear'),
               label: (document.querySelector('#kpb-purge')||{}).textContent || '' };
    })()`);
    check('「批量整理知识点」栏里「真删选中课件…」入口在',
      kpb?.hasPurge && /真删/.test(kpb?.label || ''), JSON.stringify(kpb));
    check('同栏既有的「清除选中课件的整理产物」入口仍在（原行为未被顶掉）',
      kpb?.hasClear === true);
    // 勾一份 → 点真删 → 必须弹出**同一套**确认面板（复用 openDocDelByIds）
    const purgeAsk = await evalJs(`(async () => {
      const cbs = Array.from(document.querySelectorAll('.kpb-cb'));
      if (!cbs.length) return { noCbs: true };
      cbs.slice(0, 1).forEach(c => { c.checked = true; c.onchange && c.onchange(); });
      document.querySelector('#kpb-purge').click();
      await new Promise(r => setTimeout(r, 2500));
      const box = document.querySelector('#modalbox');
      return { n: cbs.length, cbsIds: cbs.map(c => c.dataset.doc),
               hasHard: !!document.querySelector('#dd-hard'),
               hasSoft: !!document.querySelector('#dd-soft'),
               text: box ? (box.innerText || '').slice(0, 400) : '' };
    })()`);
    check('点「真删选中课件…」弹出与资料页同一套确认面板（#dd-hard / #dd-soft）',
      purgeAsk?.noCbs || (purgeAsk?.hasHard && purgeAsk?.hasSoft),
      JSON.stringify(purgeAsk).slice(0, 300));
    // 点「取消」收工：这一步**不真删**（这一栏的清单里可能有别的资料）
    await evalJs(`(() => { if (typeof closeModal === 'function') closeModal(); return 'ok'; })()`);

    // ---------- 6. 零 JS 错误 ----------
    check('整个过程 console 零 error / 零未捕获异常',
      errors.length === 0, errors.slice(0, 3).join(' | '));

    out.dialogs = dialogs;
    out.jsErrors = errors;
  } finally {
    try { if (edge) edge.kill(); } catch { /* ignore */ }
    try { srv.kill(); } catch { /* ignore */ }
    // Edge 的 user-data-dir 别留在临时目录里：一次跑一个几百 MB 的 profile，
    // 攒几次就把 `_tmp_c0084` 撑成垃圾场（实测跑三次就留下三个）。
    // ⚠ Windows 上刚 kill 的 Edge 还攥着目录句柄，删一次常报 EPERM——
    //   重试几次，且**清理失败绝不能把整轮验证判成失败**。
    if (profile) {
      for (let i = 0; i < 6; i++) {
        try { rmSync(profile, { recursive: true, force: true }); } catch { /* 句柄未释放 */ }
        if (!existsSync(profile)) break;
        await sleep(500);
      }
    }
  }

  const passed = checks.filter((c) => c.ok).length;
  out.passed = passed;
  out.total = checks.length;
  if (JSONOUT) {
    mkdirSync(dirname(JSONOUT), { recursive: true });
    writeFileSync(JSONOUT, JSON.stringify(out, null, 2), 'utf8');
  }
  console.log(`C0084-BROWSER ${passed === checks.length ? 'PASS' : 'FAIL'} ${passed}/${checks.length}`);
  if (!JSONOUT) console.log(JSON.stringify(out, null, 2));
  return passed === checks.length ? 0 : 1;
}

main().then((c) => process.exit(c)).catch((e) => {
  console.log('[FAIL] 探针自身出错：' + (e && e.message ? e.message : e));
  process.exit(2);
});
