// 卡 C-0080 探针：headless Edge + CDP，**真点击**看板「资料」页上的
// 「导出」按钮与「导入分享包」入口（不是 DOM 桩——本项目为此刻过苦头）。
//
// 验的事：
//   1. 导出：点按钮 → 弹窗出现 → 两个选项可勾（并且勾了预估体积会变）→
//      导出后能看到包路径与体积，且那个 zip 在磁盘上**真的存在**；
//   2. 导入：填包路径 → 点「看包信息并导入」→ 包信息（标题/来源/导出时间/
//      包含什么/体积/需要多久）、学科库下拉、冲突三选一都在 → 走完导入拿到结果；
//   3. 失败路径：模型不一致 / 包被改坏 / 根本不是 zip，三种都要给出**可操作**原因；
//   4. 回归：资料页原有的入库/重建/移除/批量入库按钮与两个子页签仍在。
//
// 用法：
//   node tools\probe_c0080_share_ui.mjs --port 8793 --seed <包> --badmodel <包> ^
//        --broken <包> --notzip <文件> --fake "演示第二库" --outdir <截图目录> --json <结果>
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync, existsSync, statSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const PORT = arg('port', '8793');
const OUTDIR = arg('outdir', '');
const JSONOUT = arg('json', '');
const CDP_PORT = parseInt(arg('cdp', '9367'), 10);
const SEED = arg('seed', '');
const BAD = arg('badmodel', '');
const BROKEN = arg('broken', '');
const NOTZIP = arg('notzip', '');
const FAKE = arg('fake', '演示第二库');
const TMP = arg('tmp', '');
const BASE = `http://127.0.0.1:${PORT}`;

const EDGE_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];
const EDGE = EDGE_CANDIDATES.find((p) => existsSync(p));
if (!EDGE) { console.error('找不到 msedge.exe'); process.exit(3); }

const profile = mkdtempSync(join(tmpdir(), 'c0080-edge-'));
const edge = spawn(EDGE, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-gpu', '--window-size=1500,1100', 'about:blank',
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
      // 「覆盖」会先弹一次 confirm；探针一律按"确定"，否则页面会卡住
      send('Page.handleJavaScriptDialog', { accept: true });
    }
  };
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
  const waitFor = async (expr, ms = 30000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (await evalJs(expr)) return true;
      await sleep(250);
    }
    return false;
  };
  const checks = [];
  const check = (name, ok, detail) => {
    checks.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
    return !!ok;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable').catch(() => {});

  const out = { base: BASE, port: PORT, checks, shots: {}, errors: [], evidence: {} };

  // ---------- 0. 打开页面，进「资料」页 ----------
  await send('Page.navigate', { url: `${BASE}/` });
  await sleep(2600);
  await waitFor(`!!document.querySelector('#nav button')`, 20000);
  await evalJs(`(async () => { await go('docs'); return 'ok'; })()`);
  await sleep(1800);

  // ---------- 1. 回归：资料页原有功能一个都不少 ----------
  const reg = await evalJs(`(() => {
    const view = document.querySelector('#view');
    const t = view ? view.innerText : '';
    const btns = [...view.querySelectorAll('button')].map(b => (b.textContent||'').trim());
    return {
      btns,
      hasIngestAll: btns.some(x => x.includes('入库全部待处理文件')),
      hasForceAll: btns.some(x => x.includes('全部强制重建')),
      hasCleanup: btns.some(x => x.includes('清理无出处的记录')),
      hasImportPath: !!document.querySelector('#im-go'),
      hasSubTabs: !!document.querySelector('#docs-tabs'),
      hasBatch: t.includes('批量入库'),
      hasExportBtn: btns.filter(x => x === '导出').length,
      hasRebuild: btns.filter(x => x === '重建').length,
      hasRemove: btns.filter(x => x === '移除').length,
      hasImportShare: !!document.querySelector('#sh-open'),
      hasPickShare: !!document.querySelector('#sh-pick'),
      docRows: document.querySelectorAll('#view table tbody tr').length,
      text: t.slice(0, 500),
    };
  })()`);
  out.evidence.regression = reg;
  check('回归：入库全部待处理文件 按钮在', reg.hasIngestAll);
  check('回归：全部强制重建 按钮在', reg.hasForceAll);
  check('回归：清理无出处的记录 按钮在', reg.hasCleanup);
  check('回归：按路径导入入口（#im-go）在', reg.hasImportPath);
  check('回归：两个子页签（#docs-tabs）在', reg.hasSubTabs);
  check('回归：批量入库 区域在', reg.hasBatch);
  check('回归：已入库行的 重建/移除 仍在', reg.hasRebuild > 0 && reg.hasRemove > 0,
        `重建x${reg.hasRebuild} 移除x${reg.hasRemove}`);
  check('新增：已入库行有「导出」按钮', reg.hasExportBtn > 0, `导出x${reg.hasExportBtn}`);
  check('新增：添加资料区有「导入分享包」入口', reg.hasImportShare && reg.hasPickShare);
  out.shots.docs = await shot('01-docs.png');

  // ---------- 2. 导出：真点按钮 → 弹窗 → 勾选 → 导出 → 结果 ----------
  const clicked = await evalJs(`(() => {
    const b = [...document.querySelectorAll('#view button')]
      .find(x => (x.textContent||'').trim() === '导出');
    if (!b) return 'no-button';
    b.click(); return 'clicked';
  })()`);
  check('导出：按钮可点击', clicked === 'clicked', clicked);
  check('导出：弹窗出现', await waitFor(`!!document.querySelector('#she-go')`, 30000));
  await sleep(700);
  const modal = await evalJs(`(() => {
    const est = document.querySelector('#she-est');
    const o = document.querySelector('#she-orig'), p = document.querySelector('#she-pages');
    return { est: est ? est.textContent : null,
      hasOrig: !!o, hasPages: !!p,
      origDisabled: o ? o.disabled : null, pagesDisabled: p ? p.disabled : null,
      body: (document.querySelector('#she-body')||{}).innerText || '' };
  })()`);
  out.evidence.exportModal = modal;
  check('导出：弹窗有「包含原课件文件」选项', modal.hasOrig);
  check('导出：弹窗有「包含识图页图」选项', modal.hasPages);
  check('导出：两个选项都可勾（未被禁用）',
        modal.origDisabled === false && modal.pagesDisabled === false,
        `orig disabled=${modal.origDisabled} pages disabled=${modal.pagesDisabled}`);
  check('导出：显示了预估包体积', !!modal.est && /(KB|MB|B)/.test(modal.est), modal.est);
  check('导出：弹窗里没有 undefined / NaN 这类渲染漏洞',
        !/undefined|NaN/.test(modal.body), modal.body.slice(0, 200));
  out.shots.exportModal = await shot('02-export-modal.png');

  const toggled = await evalJs(`(() => {
    const o = document.querySelector('#she-orig'), p = document.querySelector('#she-pages');
    const before = (document.querySelector('#she-est')||{}).textContent;
    if (o) { o.checked = true; o.dispatchEvent(new Event('change')); }
    if (p) { p.checked = true; p.dispatchEvent(new Event('change')); }
    return { before, after: (document.querySelector('#she-est')||{}).textContent,
             o: o && o.checked, p: p && p.checked };
  })()`);
  out.evidence.exportToggled = toggled;
  check('导出：勾选原文件+页图后预估值随之变化',
        toggled.o && toggled.p && toggled.before !== toggled.after,
        `${toggled.before} -> ${toggled.after}`);

  await evalJs(`document.querySelector('#she-go').click(); 'ok'`);
  const gotZip = await waitFor(`!!document.querySelector('#shr-path')`, 180000);
  check('导出：跑完并给出结果弹窗', gotZip);
  const exp = await evalJs(`(() => {
    const p = document.querySelector('#shr-path');
    const box = document.querySelector('#modalbox');
    return { path: p ? p.innerText.trim() : null,
             badges: [...document.querySelectorAll('#modalbox .badge')].map(b => (b.innerText||'').trim()),
             hasReveal: !!document.querySelector('#shr-open-dir'),
             hasCopy: !!document.querySelector('#shr-copy'),
             text: box ? box.innerText.slice(0, 700) : '' };
  })()`);
  out.evidence.exportResult = exp;
  const zipOk = exp.path && existsSync(exp.path);
  const zipBytes = zipOk ? statSync(exp.path).size : 0;
  check('导出：结果里给了包文件路径', !!exp.path && /\.zip$/i.test(exp.path), exp.path);
  check('导出：那个 zip 在磁盘上真的存在（真落盘，不是界面自说）', zipOk,
        zipOk ? `${zipBytes} 字节` : '不存在');
  check('导出：界面显示的体积与磁盘实际一致',
        exp.badges.some(b => /(KB|MB|B)/.test(b)), exp.badges.join(' | '));
  check('导出：结果里有「打开所在文件夹 / 复制路径」',
        exp.hasReveal && exp.hasCopy);
  out.shots.exportResult = await shot('03-export-result.png');
  await evalJs(`closeModal(); 'ok'`);
  await sleep(400);

  // ---------- 3. 导入：填路径 → 看包信息 → 库选择 / 冲突提示 ----------
  await evalJs(`(() => {
    document.querySelector('#sh-zip').value = ${JSON.stringify(SEED)};
    document.querySelector('#sh-open').click();
    return 'ok';
  })()`);
  check('导入：包信息弹窗出现', await waitFor(`!!document.querySelector('#shi-go')`, 90000));
  await sleep(500);
  const info = await evalJs(`(() => {
    const body = document.querySelector('#shi-body');
    const sel = document.querySelector('#shi-sub');
    return {
      text: body ? body.innerText : '',
      opts: sel ? [...sel.options].map(o => ({ v: o.value, t: o.textContent.trim() })) : [],
      radios: [...document.querySelectorAll('input[name="shi-cf"]')].map(r => r.value),
      goDisabled: (document.querySelector('#shi-go')||{}).disabled,
    };
  })()`);
  out.evidence.importInfo = info;
  check('导入：展示了课件标题', /Medicinal Chemistry/.test(info.text));
  check('导入：展示了来源与导出时间', /来源/.test(info.text) && /导出于/.test(info.text));
  check('导入：展示了包含什么（原文件/页图/知识点产物）',
        /含原课件文件|不含原课件文件/.test(info.text) && /知识点产物/.test(info.text));
  check('导入：展示了体积与预计耗时',
        /(KB|MB)/.test(info.text) && /导入预计/.test(info.text));
  check('导入：有「导入到哪个学科库」下拉且不止一个选项',
        info.opts.length >= 2, JSON.stringify(info.opts));
  check('导入：检出冲突并给出三选一（跳过/另存一份/覆盖）',
        info.radios.join(',') === 'skip,duplicate,overwrite' && /本库已有同一份课件/.test(info.text),
        info.radios.join(','));
  check('导入：模型一致性校验通过', /校验通过/.test(info.text));
  check('导入：包信息里没有 undefined / NaN 这类渲染漏洞',
        !/undefined|NaN/.test(info.text), info.text.slice(0, 220));
  check('导入：可导入时「开始导入」按钮可用', info.goDisabled === false);
  out.shots.importInfo = await shot('04-import-info.png');

  // 换库：切到第二个学科，冲突应当消失（那个库是空的）
  await evalJs(`(() => {
    const s = document.querySelector('#shi-sub');
    s.value = ${JSON.stringify(FAKE)};
    s.dispatchEvent(new Event('change'));
    return 'ok';
  })()`);
  const switched = await waitFor(
    `(() => { const t = (document.querySelector('#shi-body')||{}).innerText || '';
      return t.includes(${JSON.stringify(FAKE)}) && !t.includes('本库已有同一份课件'); })()`,
    60000);
  check('导入：切到另一个学科库后按那个库重新校验（冲突消失、提示库还没建）',
        switched, String(await evalJs(
          `(document.querySelector('#shi-body')||{}).innerText.slice(0,200)`)));
  out.shots.importOtherSubject = await shot('05-import-other-subject.png');

  // 切回当前库，选「另存一份」并真的导入
  await evalJs(`(() => {
    const s = document.querySelector('#shi-sub');
    s.value = 'default';
    s.dispatchEvent(new Event('change'));
    return 'ok';
  })()`);
  await waitFor(`!!document.querySelector('input[name="shi-cf"][value="duplicate"]')`, 60000);
  await sleep(400);
  await evalJs(`(() => {
    const r = document.querySelector('input[name="shi-cf"][value="duplicate"]');
    if (r) r.checked = true;
    document.querySelector('#shi-go').click();
    return 'ok';
  })()`);
  const done = await waitFor(
    `/导入完成|导入失败|已跳过/.test((document.querySelector('#modalbox')||{}).innerText || '')`,
    180000);
  check('导入：走完流程并给出结果', done);
  const ires = await evalJs(`(() => {
    const box = document.querySelector('#modalbox');
    const t = box ? box.innerText : '';
    return { text: t.slice(0, 900), badges: [...box.querySelectorAll('.badge')].map(b => (b.innerText||'').trim()) };
  })()`);
  out.evidence.importResult = ires;
  check('导入：结果说明写入了多少行（切块/向量）',
        /切块 \d+/.test(ires.text) && /向量 \d+/.test(ires.text));
  check('导入：结果给出检索可用性', /全文检索/.test(ires.text));
  check('导入：结果里给出包来源（源学科 / doc# / 标题）与包指纹 sha256',
        /来源：/.test(ires.text) && /包指纹 sha256/.test(ires.text));
  check('导入：结果说明零模型调用与零费用',
        /模型调用：\s*0 次/.test(ires.text) && /CNY 0\.00/.test(ires.text));
  out.shots.importResult = await shot('06-import-result.png');
  await evalJs(`closeModal(); 'ok'`);
  await sleep(400);

  // ---------- 4. 失败路径：三种都必须给出可操作原因 ----------
  const failCases = [
    ['模型不一致', BAD, /不能导入：模型不一致/, /模型不一致：包里的向量用的是/],
    ['包被改坏', BROKEN, /这个包不能用/, /sha256|校验失败/],
    ['根本不是 zip', NOTZIP, /这个包不能用/, /不是有效的 zip/],
  ];
  for (const [label, path, headRe, bodyRe] of failCases) {
    await evalJs(`(() => {
      document.querySelector('#sh-zip').value = ${JSON.stringify(path)};
      document.querySelector('#sh-open').click();
      return 'ok';
    })()`);
    await waitFor(`(() => { const t = (document.querySelector('#shi-body')||{}).innerText || '';
      return t.length > 20; })()`, 90000);
    await sleep(900);
    const f = await evalJs(`(() => {
      const b = document.querySelector('#shi-body');
      return { text: b ? b.innerText : '',
               goDisabled: (document.querySelector('#shi-go')||{}).disabled };
    })()`);
    out.evidence['fail_' + label] = f;
    check(`失败路径「${label}」：头部结论正确`, headRe.test(f.text), f.text.slice(0, 120));
    check(`失败路径「${label}」：给了可操作原因`, bodyRe.test(f.text), f.text.slice(0, 300));
    check(`失败路径「${label}」：文案里的 **强调** 已渲染（不裸露星号）`,
          !/\*\*/.test(f.text), f.text.slice(0, 200));
    if (label === '模型不一致') {
      check('失败路径「模型不一致」：禁止导入（按钮禁用）', f.goDisabled === true);
    }
    out.shots['fail_' + label] = await shot(`07-fail-${label}.png`);
    await evalJs(`closeModal(); 'ok'`);
    await sleep(400);
  }

  // ---------- 5. 冲突三选一在接口层的真实行为（skip / overwrite / 导入到别的库） ----------
  const http = await evalJs(`(async () => {
    const post = async (b) => {
      const r = await fetch('/api/share/import', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)});
      return await r.json();
    };
    const seed = ${JSON.stringify(SEED)};
    const o = {};
    o.skip = await post({zip: seed, subject: 'default', on_conflict: 'skip'});
    o.overwrite = await post({zip: seed, subject: 'default', on_conflict: 'overwrite'});
    o.afterOverwrite = await post({zip: seed, subject: 'default', on_conflict: 'skip'});
    o.toOther = await post({zip: seed, subject: ${JSON.stringify(FAKE)}, on_conflict: 'skip'});
    return {
      skip: {status:o.skip.status, reason:o.skip.reason, doc_id:o.skip.doc_id},
      overwrite: {status:o.overwrite.status, doc_id:o.overwrite.doc_id,
                  overwritten:o.overwrite.overwritten ? {
                    deleted_ids:o.overwrite.overwritten.deleted_doc_ids,
                    chunks:o.overwrite.overwritten.chunks_removed} : null,
                  model_calls:o.overwrite.model_calls, cost:o.overwrite.cost_cny},
      afterOverwrite: {status:o.afterOverwrite.status, doc_id:o.afterOverwrite.doc_id},
      toOther: {status:o.toOther.status, doc_id:o.toOther.doc_id,
                slug:(o.toOther.target||{}).slug, db:(o.toOther.target||{}).db,
                model_calls:o.toOther.model_calls,
                fts:(o.toOther.fts||{}).ok},
    };
  })()`);
  out.evidence.httpConflicts = http;
  check('冲突「跳过」：返回 skipped 且说明原因',
        http.skip && http.skip.status === 'skipped', JSON.stringify(http.skip));
  check('冲突「覆盖」：删掉本库已有的同一份再导入，并报告删了哪几条',
        http.overwrite && http.overwrite.status === 'imported'
        && http.overwrite.overwritten
        && (http.overwrite.overwritten.deleted_ids || []).length > 0,
        JSON.stringify(http.overwrite));
  check('冲突「覆盖」之后本库恰好留下新导入的那一份（再导一次会被跳过）',
        http.afterOverwrite && http.afterOverwrite.status === 'skipped',
        JSON.stringify(http.afterOverwrite));
  check('导入到别的学科库：写进那个库（不是当前库）',
        http.toOther && http.toOther.status === 'imported' && http.toOther.slug === FAKE,
        JSON.stringify(http.toOther));
  check('导入全程零模型调用、零费用',
        http.overwrite.model_calls === 0 && http.toOther.model_calls === 0);

  // 第二个库真的落盘了吗（在 node 侧核对文件系统，不靠界面自说）
  if (TMP) {
    const otherDb = join(TMP, 'subjects', FAKE, 'index', 'study.db');
    const otherKnow = join(TMP, 'subjects', FAKE, 'logs', 'knowledge');
    const dbOk = existsSync(otherDb) && statSync(otherDb).size > 0;
    let knowFiles = [];
    try {
      knowFiles = readdirSync(otherKnow);
    } catch { /* 目录还不存在 */ }
    out.evidence.otherSubjectFiles = { otherDb, dbOk, knowFiles };
    check('导入到别的库：那个学科的库文件真的写出来了', dbOk, otherDb);
  } else {
    check('导入到别的库：那个学科的库文件真的写出来了', false, '未提供 --tmp');
  }

  // ---------- 6. 收尾：页面零 JS 错误 ----------
  out.errors = errors;
  check('浏览器控制台没有 JS 报错', errors.length === 0, errors.slice(0, 3).join(' | '));

  if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(out, null, 2), 'utf-8');
  const pass = checks.filter(c => c.ok).length;
  const failed = checks.filter(c => !c.ok);
  console.log(`结论：${failed.length === 0 ? 'PASS' : 'FAIL'}  ${pass}/${checks.length} 项通过`);
  if (failed.length) for (const f of failed) console.log(`  ✗ ${f.name} —— ${f.detail}`);
  console.log(`截图目录：${OUTDIR || '(未开)'}`);
  console.log(`证据 JSON：${JSONOUT || '(未开)'}`);
  console.log(`页面错误：${errors.length}`);
  ws.close();
  edge.kill();
  process.exit(failed.length === 0 ? 0 : 1);
}

main().catch((e) => { console.error('探针失败：' + (e && e.message)); try { edge.kill(); } catch {} process.exit(2); });
