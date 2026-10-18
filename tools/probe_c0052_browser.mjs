// 卡 C-0052：用 headless Edge + CDP 真跑三块新功能——
//   ① 知识点弹窗：「记入笔记」写入一条 → 文件系统真出现 → 「查看笔记」看得到
//      （关掉弹窗再打开，同一条还在）；
//   ② 课件思维导图栏右下角「AI答疑」：按钮位置、对话窗口、真实提问、
//      检索范围只在本课件（范围行 + 证据 doc_id 集合都抠出来）、问答记入笔记；
//   ③ 笔记页签：条目带记录时间与来源、点开看原文、「跳到知识点窗口」按钮
//      真能把导图上那一屏弹出来（课件问答类笔记没有这个按钮，且不报错）。
//
// 为什么必须真浏览器（本档位血的教训）：DOM 桩验不出交互——按钮在不在、
// 点了会不会真写文件、跳转后弹窗里到底是什么，只有真跑才算数。
// 骨架照 tools/probe_c0050_browser.mjs。
//
// 用法：
//   node tools/probe_c0052_browser.mjs --port 8891 --doc 7 --text "…" \
//        --outdir <截图目录> --json <结果 JSON>
// ⚠ 本探针会**真写笔记文件**（在 notes/ 下）并**真调一次模型**（AI答疑）。
//   调用方（verify_c0052_browser.py）负责记录现场、跑完清理测试笔记。
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const PORT = arg('port', '8891');
const DOC = arg('doc', '7');
const TEXT = arg('text', 'C0052 探针写入的知识点笔记');
const OUTDIR = arg('outdir', '');
const JSONOUT = arg('json', '');
const CDP_PORT = parseInt(arg('cdp', '9345'), 10);
const SKIP_AI = arg('skipai', '') === '1';   // 省钱模式：跳过真实模型调用
const BASE = `http://127.0.0.1:${PORT}`;

const profile = mkdtempSync(join(tmpdir(), 'c0052-edge-'));
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
    const s = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    if (s.result?.data) { writeFileSync(p, Buffer.from(s.result.data, 'base64')); return p; }
    return null;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable').catch(() => {});

  const out = { base: BASE, port: PORT, doc: DOC, text: TEXT, shots: {}, steps: {} };

  await send('Page.navigate', { url: `${BASE}/` });
  await sleep(2200);

  // 1) 进「知识点」页 → 选课件 → 展开全部 → 点一个末级知识点节点
  out.steps.mount = await evalJs(`(async () => {
    if (typeof go === 'function') go('kps');
    for (let i = 0; i < 80; i++) {
      await new Promise(r => setTimeout(r, 250));
      if (document.querySelector('#mm-doc') && document.querySelector('#mm-tree')) return { ok:true };
    }
    return { ok:false, html:(document.querySelector('#view')||{}).innerHTML };
  })()`);

  out.steps.pickDoc = await evalJs(`(async () => {
    const sel = document.querySelector('#mm-doc');
    const opt = [...sel.options].find(o => o.value === '${DOC}');
    if (!opt) return { ok:false, have:[...sel.options].map(o=>o.value) };
    sel.value = opt.value;
    sel.dispatchEvent(new Event('change'));
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 250));
      if (document.querySelectorAll('#mm-tree .mmname').length) return { ok:true, label:opt.textContent.trim() };
    }
    return { ok:false, reason:'树没渲染出来' };
  })()`);

  // ★「AI答疑」按钮：位置断言（在卡片右下角：横向靠右、纵向在树下面）
  await evalJs(`(() => { const b=document.querySelector('#mm-expand'); if (b) b.click(); })()`);
  await sleep(600);
  out.steps.aiButton = await evalJs(`(() => {
    const b = document.querySelector('#mm-ai');
    if (!b) return { ok:false, reason:'没有 #mm-ai' };
    const card = b.closest('.card');
    const tree = document.querySelector('#mm-tree');
    if (!card) return { ok:false, reason:'按钮不在卡片里' };
    const rb = b.getBoundingClientRect(), rc = card.getBoundingClientRect();
    const rt = tree ? tree.getBoundingClientRect() : null;
    return { ok:true, text:b.textContent.trim(),
             inCard: rb.left >= rc.left - 2 && rb.right <= rc.right + 2,
             belowTree: rt ? (rb.top >= rt.bottom - 4) : null,
             rightAligned: (rc.right - rb.right) < 60,   // 距卡片右边界 < 60px
             hint: (document.querySelector('#mm-ai-hint')||{}).textContent || '',
             rect:{x:Math.round(rb.left),y:Math.round(rb.top)},
             cardRect:{x:Math.round(rc.left),y:Math.round(rc.top),
                       w:Math.round(rc.width),h:Math.round(rc.height)} };
  })()`);

  // 2) 点末级节点 → 弹窗 → 断言两个新按钮在
  out.steps.popup = await evalJs(`(async () => {
    const el = [...document.querySelectorAll('#mm-tree .mmname.has-kp')][0];
    if (!el) return { ok:false, reason:'没有 .has-kp 节点' };
    el.click();
    for (let i = 0; i < 50; i++) {
      await new Promise(r => setTimeout(r, 200));
      const pop = document.querySelector('.evpop');
      if (pop && pop.innerText.indexOf('加载中') < 0 && pop.innerText.length > 80) {
        const add = pop.querySelector('#mmkp-note'), see = pop.querySelector('#mmkp-notes');
        return { ok:true, clicked:el.textContent.trim(),
                 kpId:(pop.querySelector('.badge')||{}).textContent || '',
                 hasAdd:!!add, addText:add?add.textContent.trim():'',
                 addTitle:add?add.title:'',
                 hasSee:!!see, seeText:see?see.textContent.trim():'',
                 footerText:(pop.querySelector('.pf')||{}).innerText || '',
                 text:pop.innerText };
      }
    }
    return { ok:false, reason:'弹窗没出内容' };
  })()`);
  out.shots.popup = await shot(`1-kp-popup-doc${DOC}.png`);

  // 3) 「记入笔记」→ 输入 → 保存
  out.steps.noteEdit = await evalJs(`(async () => {
    const b = document.querySelector('#mmkp-note');
    if (!b) return { ok:false, reason:'没有 #mmkp-note' };
    b.click();
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 150));
      const ta = document.querySelector('#mmkp-notebody');
      if (ta) return { ok:true, hasTextarea:true, placeholder:ta.placeholder,
                       hasSave:!!document.querySelector('#mmkp-notesave') };
    }
    return { ok:false, reason:'输入框没出现' };
  })()`);
  out.shots.edit = await shot(`2-kp-note-edit.png`);

  out.steps.noteSave = await evalJs(`(async () => {
    const ta = document.querySelector('#mmkp-notebody');
    const b = document.querySelector('#mmkp-notesave');
    if (!ta || !b) return { ok:false, reason:'输入框或保存按钮不在' };
    ta.value = ${JSON.stringify(TEXT)};
    b.click();
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 200));
      const m = document.querySelector('#mmkp-notemsg');
      const t = m ? m.textContent : '';
      if (/已记入/.test(t)) return { ok:true, msg:t, file:t.replace(/^已记入：/, '') };
      if (/失败/.test(t)) return { ok:false, reason:t };
    }
    return { ok:false, reason:'保存没有回音' };
  })()`);
  out.shots.saved = await shot(`3-kp-note-saved.png`);

  // 4) 「查看笔记」列出这条（含正文）
  const readList = `(async () => {
    const b = document.querySelector('#mmkp-notes');
    if (!b) return { ok:false, reason:'没有 #mmkp-notes' };
    const box = document.querySelector('#mmkp-notelist');
    if (box && box.hidden) b.click();          // 只在收起时才点开
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 200));
      const nb = document.querySelector('#mmkp-notelist');
      if (nb && !nb.hidden && nb.querySelectorAll('.hit').length)
        return { ok:true, n:nb.querySelectorAll('.hit').length,
                 text:nb.innerText, hasText:nb.innerText.indexOf(${JSON.stringify(TEXT)}) >= 0 };
    }
    const nb = document.querySelector('#mmkp-notelist');
    return { ok:false, text:nb ? nb.innerText : '' };
  })()`;
  out.steps.noteList = await evalJs(readList);
  out.shots.list = await shot(`4-kp-note-list.png`);

  // 5) 关掉弹窗 → 重新点同一个节点 → 再「查看笔记」：同一条还在
  out.steps.reopen = await evalJs(`(async () => {
    const name = (document.querySelector('.evpop .src .loc')||{}).innerText || '';
    if (typeof evPopClose === 'function') evPopClose();
    await new Promise(r => setTimeout(r, 600));
    const el = [...document.querySelectorAll('#mm-tree .mmname.has-kp')][0];
    if (!el) return { ok:false, reason:'树里没有节点了' };
    el.click();
    for (let i = 0; i < 50; i++) {
      await new Promise(r => setTimeout(r, 200));
      const pop = document.querySelector('.evpop');
      if (pop && pop.innerText.indexOf('加载中') < 0 && pop.innerText.length > 80) break;
    }
    const b = document.querySelector('#mmkp-notes');
    if (!b) return { ok:false, reason:'重开后没有 #mmkp-notes' };
    b.click();
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 200));
      const nb = document.querySelector('#mmkp-notelist');
      if (nb && !nb.hidden && nb.querySelectorAll('.hit').length)
        return { ok:true, saved:true, name:name,
                 n:nb.querySelectorAll('.hit').length,
                 hasText:nb.innerText.indexOf(${JSON.stringify(TEXT)}) >= 0,
                 text:nb.innerText.slice(0, 300) };
    }
    return { ok:false, reason:'重开弹窗后看不到笔记' };
  })()`);
  out.shots.reopen = await shot(`5-kp-note-reopen.png`);

  // 6) AI 答疑：点按钮 → modal → 真提问 → 范围行/证据 → 记入笔记
  out.steps.aiOpen = await evalJs(`(async () => {
    if (typeof evPopClose === 'function') evPopClose();
    await new Promise(r => setTimeout(r, 300));
    const b = document.querySelector('#mm-ai');
    if (!b) return { ok:false, reason:'没有 #mm-ai' };
    b.click();
    for (let i = 0; i < 40; i++) {
      await new Promise(r => setTimeout(r, 150));
      const m = document.querySelector('#modal');
      const q = document.querySelector('#dk-q');
      if (m && m.classList.contains('on') && q) {
        return { ok:true, title:(document.querySelector('#modalbox h2')||{}).innerText || '',
                 head:(document.querySelector('#modalbox .small.muted')||{}).innerText || '',
                 hasSum:!!document.querySelector('#dk-sum'),
                 hasSend:!!document.querySelector('#dk-send'),
                 hasThread:!!document.querySelector('#dk-thread') };
      }
    }
    return { ok:false, reason:'对话窗口没打开' };
  })()`);
  out.shots.aiOpen = await shot(`6-deck-chat-open.png`);

  if (SKIP_AI) {
    out.steps.aiAsk = { ok:false, skipped:true };
  } else {
    out.steps.aiAsk = await evalJs(`(async () => {
      const b = document.querySelector('#dk-sum');
      if (!b) return { ok:false, reason:'没有 #dk-sum' };
      b.click();
      for (let i = 0; i < 200; i++) {          // 真实模型调用：最长等 100 秒
        await new Promise(r => setTimeout(r, 500));
        const bubbles = [...document.querySelectorAll('#dk-thread .msg.ai .bubble')];
        const last = bubbles[bubbles.length - 1];
        const t = last ? last.innerText : '';
        const err = /出错|请求失败/.test(t);
        if (t.length > 60 && !last.querySelector('.typing') && !err){
          const body = last.closest('.body');
          const usage = body ? [...body.querySelectorAll('.small.muted')]
            .map(x => x.innerText).find(x => /tokens/.test(x)) || '' : '';
          return { ok:true, answer:t,
                   scope:(document.querySelector('#dk-scope')||{}).textContent || '',
                   scopeTitle:(document.querySelector('#dk-scope')||{}).title || '',
                   usageText:usage,
                   refs:(document.querySelector('#dk-thread .msg.ai .small.muted')||{}).innerText || '' };
        }
        if (err) return { ok:false, reason:t.slice(0, 300) };
      }
      return { ok:false, reason:'回答超时' };
    })()`);
  }
  out.shots.aiAnswer = await shot(`7-deck-chat-answer.png`);

  out.steps.aiNote = await evalJs(`(async () => {
    const b = document.querySelector('#dk-thread button[data-dknote]');
    if (!b) return { ok:false, reason:'AI 回答下面没有「记入笔记」按钮' };
    b.click();
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 200));
      const tip = document.querySelector('[data-dkmsg]');
      const t = tip ? tip.textContent : '';
      if (/已记入/.test(t)) return { ok:true, msg:t, file:t.replace(/^已记入 /, '') };
      if (/失败/.test(t)) return { ok:false, reason:t };
    }
    return { ok:false, reason:'记入没有回音' };
  })()`);
  out.shots.aiNote = await shot(`8-deck-chat-saved.png`);
  await evalJs(`(() => { if (typeof closeModal === 'function') closeModal(); })()`);

  // 7) 笔记页签：条目 + 时间 + 来源 + 跳回知识点窗口
  const notesRow = (kw) => `(async () => {
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 250));
      const rows = [...document.querySelectorAll('#view table tbody tr')];
      if (!rows.length) continue;
      const row = rows.find(r => r.innerText.indexOf(${JSON.stringify(kw)}) >= 0);
      if (row) {
        const cells = [...row.querySelectorAll('td')].map(td => td.innerText);
        const link = row.querySelector('a[data-note]');
        const jump = row.querySelector('button[data-jump]');
        return { ok:true, cells:cells, file:link?link.dataset.note:'',
                 hasJump:!!jump, jumpText:jump?jump.innerText.trim():'',
                 headerText:(document.querySelector('#view .card .small.muted')||{}).innerText || '' };
      }
    }
    return { ok:false, reason:'表格里找不到这一行', text:(document.querySelector('#view')||{}).innerText };
  })()`;

  out.steps.notesTab = await evalJs(`(async () => {
    if (typeof go === 'function') go('notes');
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 250));
      if (document.querySelectorAll('#view table tbody tr').length) return { ok:true,
        n:document.querySelectorAll('#view table tbody tr').length };
    }
    return { ok:false, text:(document.querySelector('#view')||{}).innerText };
  })()`);
  out.shots.notesTab = await shot(`9-notes-tab.png`);

  // 用**探针刚写进去的那个文件名**定位条目（同一条知识点可能有多篇笔记，
  // 按标题找会挑到别人的）；文件名的来源是上一步 noteSave 的返回。
  const kpFile = (out.steps.noteSave && out.steps.noteSave.file) || '知识点笔记';
  out.steps.kpFile = kpFile;
  out.steps.kpRow = await evalJs(notesRow(kpFile));
  out.steps.deckRow = await evalJs(notesRow('AI答疑'));

  // 8) 点开知识点笔记 → 原文 + 跳按钮 → 真跳回知识点窗口
  out.steps.openKpNote = await evalJs(`(async () => {
    const rows = [...document.querySelectorAll('#view table tbody tr')];
    const row = rows.find(r => r.innerText.indexOf(${JSON.stringify(kpFile)}) >= 0);
    if (!row) return { ok:false, reason:'找不到知识点笔记那一行' };
    row.querySelector('a[data-note]').click();
    for (let i = 0; i < 40; i++) {
      await new Promise(r => setTimeout(r, 200));
      const box = document.querySelector('#modalbox');
      if (box && document.querySelector('#modal').classList.contains('on') && box.innerText.length > 40)
        return { ok:true, text:box.innerText,
                 hasBody:box.innerText.indexOf(${JSON.stringify(TEXT)}) >= 0,
                 hasJump:!!document.querySelector('#note-jump'),
                 jumpText:(document.querySelector('#note-jump')||{}).innerText || '' };
    }
    return { ok:false, reason:'笔记详情没打开' };
  })()`);
  out.shots.openNote = await shot(`10-note-detail.png`);

  out.steps.jump = await evalJs(`(async () => {
    const b = document.querySelector('#note-jump');
    if (!b) return { ok:false, reason:'没有 #note-jump' };
    const wantKp = (b.innerText.match(/[（(]([^）)]+)[）)]/) || [])[1] || '';
    b.click();
    for (let i = 0; i < 120; i++) {          // 切页签 + 等树 + 打开弹窗
      await new Promise(r => setTimeout(r, 300));
      const pop = document.querySelector('.evpop');
      const cur = document.querySelector('#nav button.on');
      if (pop && pop.innerText.length > 80 && pop.innerText.indexOf('加载中') < 0
          && cur && cur.textContent.trim() === '知识点')
        return { ok:true, wantKp:wantKp, tab:cur.textContent.trim(),
                 popText:pop.innerText.slice(0, 400),
                 popKpId:((pop.querySelector('.badge')||{}).textContent || '').trim(),
                 modalClosed:!document.querySelector('#modal').classList.contains('on'),
                 loc:(pop.querySelector('.src .loc')||{}).innerText || '' };
    }
    const pop = document.querySelector('.evpop');
    return { ok:false, reason:'点了跳转但没弹出知识点窗口', text:pop?pop.innerText.slice(0,200):'' };
  })()`);
  out.shots.jump = await shot(`11-jump-back-kp.png`);

  // 9) 课件问答笔记：没有绑定知识点 → 不出现跳转按钮（且不报错）
  out.steps.openDeckNote = await evalJs(`(async () => {
    if (typeof evPopClose === 'function') evPopClose();
    if (typeof closeModal === 'function') closeModal();
    if (typeof go === 'function') go('notes');
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 250));
      const rows = [...document.querySelectorAll('#view table tbody tr')];
      const row = rows.find(r => r.innerText.indexOf('AI答疑') >= 0);
      if (row) {
        row.querySelector('a[data-note]').click();
        for (let j = 0; j < 40; j++) {
          await new Promise(r => setTimeout(r, 200));
          const box = document.querySelector('#modalbox');
          if (box && box.innerText.length > 40)
            return { ok:true, hasJump:!!document.querySelector('#note-jump'),
                     text:box.innerText.slice(0, 500) };
        }
        return { ok:false, reason:'课件笔记详情没打开' };
      }
    }
    return { ok:false, reason:'笔记页签里找不到课件问答那一条' };
  })()`);
  out.shots.deckNote = await shot(`12-deck-note-detail.png`);

  out.errors = errors.slice(0, 10);
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
