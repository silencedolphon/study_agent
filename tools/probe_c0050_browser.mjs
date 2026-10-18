// 卡 C-0050：用 headless Edge + CDP 真跑知识点页签，验证两件事——
//   ① 页签由**整理产物**驱动：结构树 + 点节点看到理解与要点；
//     旧格式课件给出"需重新整理"的状态（不是空白、不是报错）。
//   ② 「清除」的**前端链路**：批量面板勾选 → 点清除 → 真弹二次确认 → 确定 →
//      toast 报数 → 页签当场回到"未整理"。
//
// 为什么必须真浏览器（本档位血的教训）：DOM 桩验不出布局与交互——
// 这里要验的恰恰是"confirm 有没有真弹""点完页签有没有当场变""弹窗里到底
// 渲染出什么文本"，这些只有真跑才算数。骨架照 tools/check_c0046_lkp_bar.mjs。
//
// 用法：
//   node tools/probe_c0050_browser.mjs --port 8899 --doc 5 --legacy 1 \
//        --outdir <截图目录> --json <结果 JSON>
// ⚠ 本探针会**真的点清除**（真删产物）。调用方（verify_c0050_browser.py）
//   负责先备份、跑完恢复。
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const PORT = arg('port', '8899');
const DOC = arg('doc', '5');
const LEGACY = arg('legacy', '1');
const OUTDIR = arg('outdir', '');
const JSONOUT = arg('json', '');
const CDP_PORT = parseInt(arg('cdp', '9341'), 10);
// 可选：一个"某个结构节点挂多条知识点"的 fixture 课件（卡 C-0050 新增的选择列表）。
// 调用方（probe_c0050_multi_kp.py）负责生成与删除那份假产物。
const MULTI = arg('multidoc', '');
const BASE = `http://127.0.0.1:${PORT}`;

const profile = mkdtempSync(join(tmpdir(), 'c0050-edge-'));
const edge = spawn(EDGE, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-gpu', '--window-size=1500,1000', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (OUTDIR) mkdirSync(OUTDIR, { recursive: true });
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
  let errors = [];
  const dialogs = [];          // window.confirm 的原文（证明"二次确认真的弹了"）
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    else if (m.method === 'Page.javascriptDialogOpening') {
      dialogs.push({ type: m.params?.type, message: m.params?.message || '' });
    } else if (m.method === 'Log.entryAdded' && m.params?.entry?.level === 'error') {
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
    const s = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    if (s.result?.data) { writeFileSync(p, Buffer.from(s.result.data, 'base64')); return p; }
    return null;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable').catch(() => {});

  const out = { base: BASE, port: PORT, doc: DOC, legacy: LEGACY, shots: {}, steps: {} };

  await send('Page.navigate', { url: `${BASE}/` });
  await sleep(2200);

  // 1) 进「知识点」页，等导图卡挂上
  out.steps.mount = await evalJs(`(async () => {
    if (typeof go === 'function') go('kps');
    for (let i = 0; i < 80; i++) {
      await new Promise(r => setTimeout(r, 250));
      if (document.querySelector('#mm-doc') && document.querySelector('#mm-tree')) {
        for (let j = 0; j < 40; j++) {           // 等树真的渲染出来
          await new Promise(r => setTimeout(r, 250));
          if (document.querySelectorAll('#mm-tree .mmname').length) return { ok:true, wait:i };
        }
        return { ok:true, noNodes:true };
      }
    }
    return { ok:false, html: (document.querySelector('#view')?.innerHTML || '').slice(0, 300) };
  })()`);

  const pickDoc = (docId, waitMs) => evalJs(`(async () => {
    const sel = document.querySelector('#mm-doc');
    if (!sel) return { ok:false, reason:'没有 #mm-doc' };
    const opt = [...sel.options].find(o => o.value === '${docId}');
    if (!opt) return { ok:false, reason:'下拉里没有 doc#${docId}',
                       have: [...sel.options].map(o => o.value) };
    sel.value = opt.value;
    sel.dispatchEvent(new Event('change'));
    await new Promise(r => setTimeout(r, ${waitMs}));
    return { ok:true, label: opt.textContent.trim() };
  })()`);

  const readTree = () => evalJs(`(() => {
    const tree = document.querySelector('#mm-tree');
    const bar = tree ? tree.querySelector(':scope > div') : null;
    const names = [...document.querySelectorAll('#mm-tree .mmname')];
    const emptyBox = tree ? tree.querySelector('.empty') : null;
    const sel = document.querySelector('#mm-doc');
    const opt = (sel && sel.selectedOptions && sel.selectedOptions[0])
      ? sel.selectedOptions[0].textContent.trim() : '';
    return {
      hasTree: !!tree,
      infoBar: bar ? bar.innerText : '',
      emptyText: emptyBox ? emptyBox.innerText : '',
      nNodes: names.length,
      nHasKp: names.filter(n => n.classList.contains('has-kp')).length,
      nPending: names.filter(n => n.classList.contains('lkp-pending')).length,
      optionText: opt,
      treeText: tree ? tree.innerText.slice(0, 700) : '',
      bodyText: document.body.innerText.slice(0, 400),
    };
  })()`);

  // 2) 已整理课件（v2 产物）→ 结构树
  out.steps.pickV2 = await pickDoc(DOC, 2600);
  out.v2 = await readTree();
  out.shots.v2 = await shot(`v2-doc${DOC}-tree.png`);

  // 3) 展开全部 + 点一个**末级节点**（.has-kp）→ 弹窗里要有理解与要点
  await evalJs(`(() => { const b = document.querySelector('#mm-expand'); if (b) b.click(); })()`);
  await sleep(700);
  out.shots.v2expanded = await shot(`v2-doc${DOC}-expanded.png`);
  out.steps.popup = await evalJs(`(async () => {
    const el = [...document.querySelectorAll('#mm-tree .mmname.has-kp')][0];
    if (!el) return { ok:false, reason:'树里没有 .has-kp 节点（末级知识点）' };
    const name = el.textContent.trim();
    el.click();
    for (let i = 0; i < 40; i++) {
      await new Promise(r => setTimeout(r, 200));
      const pop = document.querySelector('.evpop');
      if (pop && pop.innerText.indexOf('加载中') < 0 && pop.innerText.length > 80) {
        const r = pop.getBoundingClientRect();
        // 展开「总结正文 · 出处明细 · 生成链条」：**理解**（summary）在折叠区里，
        // 展开后连正文一起抠出来，供断言"理解与要点都看得到"。
        const more = pop.querySelector('#mmkp-more');
        if (more) more.open = true;
        await new Promise(r2 => setTimeout(r2, 400));
        const sum = pop.querySelector('.lkpsum');
        return { ok:true, clicked:name, visible: r.height > 0 && r.width > 0,
                 text: pop.innerText,
                 summaryText: sum ? sum.innerText : '',
                 pointsCount: pop.querySelectorAll('.lkppts li').length,
                 html: pop.innerHTML.slice(0, 300) };
      }
    }
    const pop = document.querySelector('.evpop');
    return { ok:false, reason:'弹窗没出内容', text: pop ? pop.innerText : '' };
  })()`);
  out.shots.popup = await shot(`v2-doc${DOC}-popup.png`);
  // 弹窗里的「下一知识点」要能点（链是先序，后端给的 next_id）
  out.steps.nextKp = await evalJs(`(async () => {
    const nb = document.querySelector('#mmkp-next');
    if (!nb) return { ok:false, reason:'没有 #mmkp-next' };
    const disabled = !!nb.disabled;
    const title = nb.title || '';
    if (!disabled) {
      nb.click();
      await new Promise(r => setTimeout(r, 1800));
      const pop = document.querySelector('.evpop');
      return { ok:true, clicked:true, title:title, afterText:(pop?pop.innerText:'').slice(0,300) };
    }
    return { ok:true, clicked:false, title:title };
  })()`);
  await evalJs(`(() => { if (typeof evPopClose === 'function') evPopClose(); })()`);

  // 4) 旧格式课件（C-0045-v1 产物）→ 明确状态，不是空白、不是报错
  out.steps.pickLegacy = await pickDoc(LEGACY, 2200);
  out.legacyView = await readTree();
  out.shots.legacy = await shot(`legacy-doc${LEGACY}.png`);

  // 4b) 一个结构节点挂**多条**知识点 → 点开应当先列出这几条让人选
  if (MULTI) {
    out.steps.pickMulti = await pickDoc(MULTI, 2600);
    out.multiView = await readTree();
    out.shots.multiTree = await shot(`multi-doc${MULTI}-tree.png`);
    out.steps.multiPopup = await evalJs(`(async () => {
      const els = [...document.querySelectorAll('#mm-tree .mmname.has-kp')];
      // 优先挑 title 里写着"这个节点下有 N 条知识点"的那个
      const el = els.find(e => /条知识点/.test(e.title || '')) || els[0];
      if (!el) return { ok:false, reason:'树里没有 .has-kp 节点' };
      el.click();
      for (let i = 0; i < 40; i++) {
        await new Promise(r => setTimeout(r, 200));
        const pop = document.querySelector('.evpop');
        if (pop && pop.innerText.length > 40) {
          const r = pop.getBoundingClientRect();
          return { ok:true, clicked: el.textContent.trim(), title: el.title || '',
                   visible: r.height > 0 && r.width > 0,
                   nItems: pop.querySelectorAll('.mmkp-one').length,
                   text: pop.innerText };
        }
      }
      return { ok:false, reason:'弹窗没出内容' };
    })()`);
    out.shots.multiList = await shot(`multi-doc${MULTI}-list.png`);
    // 点列表里的第一条 → 应当切到那条知识点的总结（要点 + 理解）
    out.steps.multiPick = await evalJs(`(async () => {
      const a = document.querySelector('.mmkp-one');
      if (!a) return { ok:false, reason:'列表里没有可点的条目' };
      const want = a.dataset.kp, wantName = a.dataset.name;
      a.click();
      for (let i = 0; i < 40; i++) {
        await new Promise(r => setTimeout(r, 200));
        const pop = document.querySelector('.evpop');
        if (pop && pop.innerText.indexOf('加载中') < 0 && pop.innerText.length > 80
            && !pop.querySelector('.mmkp-one')) {
          const more = pop.querySelector('#mmkp-more');
          if (more) more.open = true;
          await new Promise(r2 => setTimeout(r2, 350));
          const sum = pop.querySelector('.lkpsum');
          return { ok:true, kp: want, name: wantName,
                   pointsCount: pop.querySelectorAll('.lkppts li').length,
                   summaryText: sum ? sum.innerText : '', text: pop.innerText };
        }
      }
      return { ok:false, reason:'点了列表条目但没出总结' };
    })()`);
    out.shots.multiKp = await shot(`multi-doc${MULTI}-kp.png`);
  }

  // 5) 回到 v2 课件，开批量面板 → 勾选 → 点清除（**真弹 confirm**）
  await pickDoc(DOC, 2200);
  out.steps.openPanel = await evalJs(`(async () => {
    const b = document.querySelector('#kp-build-open');
    if (!b) return { ok:false, reason:'没有 #kp-build-open' };
    b.click();
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 250));
      if (document.querySelector('#kpb-list')) {
        const cb = document.querySelector('.kpb-cb[data-doc="${DOC}"]');
        return { ok:true, hasCheckbox: !!cb,
                 nRows: document.querySelectorAll('.kpb-cb').length,
                 listText: (document.querySelector('#kpb-list').innerText || '').slice(0, 400),
                 clearBtn: !!document.querySelector('#kpb-clear') };
      }
    }
    return { ok:false, reason:'批量面板没出来' };
  })()`);
  out.shots.panel = await shot(`batch-panel.png`);

  out.steps.tick = await evalJs(`(() => {
    const cb = document.querySelector('.kpb-cb[data-doc="${DOC}"]');
    if (!cb) return { ok:false, reason:'勾选框不在' };
    if (!cb.checked) cb.click();
    const sum = document.querySelector('#kpb-sum');
    return { ok:true, checked: cb.checked, sum: sum ? sum.innerText.slice(0, 200) : '' };
  })()`);

  // ★ 点清除：先不 await（window.confirm 会把 JS 阻塞住），等 dialog 事件再 accept
  const clickP = evalJs(`(() => {
    const b = document.querySelector('#kpb-clear');
    if (!b) return 'no-button';
    b.click();
    return 'clicked';
  })()`);
  let dialogSeen = false;
  for (let i = 0; i < 40; i++) {
    await sleep(150);
    if (dialogs.length) { dialogSeen = true; break; }
  }
  if (dialogSeen) await send('Page.handleJavaScriptDialog', { accept: true });
  out.steps.clearClick = { dialogSeen, clickResult: await clickP };
  out.shots.confirm = await shot(`confirm-then-cleared.png`);

  // 6) toast 报数（3200ms 后自己消失，要抢时间抠）
  out.steps.toast = await evalJs(`(async () => {
    for (let i = 0; i < 30; i++) {
      const t = [...document.querySelectorAll('body > div')]
        .filter(d => (d.style.zIndex === '99') && /清除/.test(d.textContent));
      if (t.length) return { ok:true, text: t[0].textContent };
      await new Promise(r => setTimeout(r, 150));
    }
    return { ok:false, text:'' };
  })()`);

  // 7) 页签当场回到「未整理」（kpbClear 里会顺手刷一次 loadMindmapTree）
  out.steps.afterClear = await evalJs(`(async () => {
    if (typeof closeModal === 'function') closeModal();
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 250));
      const tree = document.querySelector('#mm-tree');
      if (tree && tree.querySelector('.empty')) return { ok:true, wait:i };
    }
    return { ok:false, text: (document.querySelector('#mm-tree')||{}).innerText || '' };
  })()`);
  out.afterClear = await readTree();
  out.shots.afterClear = await shot(`after-clear-doc${DOC}.png`);
  out.dialogs = dialogs;
  out.errors = errors.slice(0, 8);

  if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(out, null, 2), 'utf-8');
  console.log(JSON.stringify(out, null, 2));

  ws.close();
  edge.kill();
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* 忽略 */ }
}

main().catch((e) => {
  console.error('探针失败: ' + e.message);
  try { edge.kill(); } catch { /* 忽略 */ }
  process.exit(1);
});