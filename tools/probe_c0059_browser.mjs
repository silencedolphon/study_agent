// 卡 C-0059 真浏览器探针（headless Edge + CDP）：页面右上角「设置」入口 +
// 四分区设置窗口（模型 / API Base / 密钥 / 识图 / 推理 / 推理强度 / 提示词）。
//
// 验的事：
//   ① 顶栏最右侧有「⚙ 设置」按钮（给出坐标，证明它真在右上角）；
//   ② 点开是**模态窗**（不是新页签）：#setmodal 带 on，页面 URL 不变；
//   ③ 窗口按四条管线分区：AI 问答 / AI 知识点总结 / 入库 / 课件答疑，
//      每区都有 模型 / API Base / 密钥（脱敏） / 识图 / 推理 / 强度档位 / 提示词；
//   ④ 改一个值（知识点总结的推理强度 → high）→ 保存 → 页面真的把新值提交上去，
//      且重新读取时后端回的就是 high（证明"保存后管线读新配置"的服务端链路）；
//   ⑤ 密钥输入框**是空的**且提示写"留空＝不改动"（脱敏：明文不落到前端）；
//   ⑥ 全程无 JS 报错。
//
// 用法：
//   node probe_c0059_browser.mjs --port 8899 --outdir <截图目录> --json <结果 JSON>
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const PORT = arg('port', '8899');
const OUTDIR = arg('outdir', '');
const JSONOUT = arg('json', '');
const CDP_PORT = parseInt(arg('cdp', '9339'), 10);
const BASE = `http://127.0.0.1:${PORT}`;

const profile = mkdtempSync(join(tmpdir(), 'c0059-edge-'));
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
  const consoleErrors = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    else if (m.method === 'Log.entryAdded' && m.params?.entry?.level === 'error') {
      const t = m.params.entry.text || '';
      const u = m.params.entry.url || '';
      if (!/favicon/i.test(t) && !/favicon/i.test(u)) errors.push(u ? `${t} @ ${u}` : t);
    } else if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params?.exceptionDetails?.text || 'exception');
    } else if (m.method === 'Runtime.consoleAPICalled' && m.params?.type === 'error') {
      consoleErrors.push((m.params.args || []).map((a) => a.value ?? a.description).join(' '));
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
  const waitFor = async (expr, ms = 12000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      const v = await evalJs(expr);
      if (v) return v;
      await sleep(200);
    }
    return null;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable').catch(() => {});

  // ★ 抓设置接口的原始请求/响应：这样"保存到底提交了什么、后端回了什么"
  //   是**页面真收到的东西**，不是我从界面上抄的。必须在 navigate 之前注入。
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `
    (() => {
      const orig = window.fetch;
      window.__c0059 = [];
      window.fetch = async function(...a) {
        const resp = await orig.apply(this, a);
        try {
          const url = (a[0] && (a[0].url || String(a[0]))) || '';
          if (/\\/api\\/settings/.test(url)) {
            const cl = resp.clone();
            const body = await cl.text();
            window.__c0059.push({
              url, method: ((a[1] && a[1].method) || 'GET'),
              body: (a[1] && a[1].body) || '', status: resp.status,
              resp: body.slice(0, 40000),
            });
          }
        } catch (e) { /* 记录失败不影响页面 */ }
        return resp;
      };
    })();
  ` });

  const steps = {};
  await send('Page.navigate', { url: BASE + '/' });
  await sleep(1200);
  await waitFor("!!document.querySelector('#set-btn')", 15000);
  await waitFor("!!document.querySelector('#nav button')", 15000);

  // ① 右上角入口
  steps.entry = await evalJs(`(() => {
    const b = document.querySelector('#set-btn');
    if (!b) return { ok: false };
    const r = b.getBoundingClientRect();
    return { ok: true, text: b.textContent.trim(), x: Math.round(r.x), y: Math.round(r.y),
             w: Math.round(r.width), h: Math.round(r.height),
             vw: window.innerWidth, url: location.href };
  })()`);
  steps.entryHtml = await evalJs(
    "document.querySelector('#set-btn') ? document.querySelector('#set-btn').outerHTML : ''");
  if (OUTDIR) await shot('01_topbar_setting_entry.png');

  // ② 点开设置窗
  await evalJs("document.querySelector('#set-btn').click()");
  const opened = await waitFor(
    "document.querySelector('#setmodal') && document.querySelector('#setmodal').classList.contains('on')"
    + " && document.querySelectorAll('#setpane .srow').length > 0");
  steps.opened = { ok: !!opened, url: await evalJs('location.href') };
  await sleep(400);
  steps.windowHtml = await evalJs(
    "(() => { const b = document.querySelector('#setbox'); if (!b) return '';"
    + " const h = b.innerHTML; return h.length > 24000 ? h.slice(0, 24000) : h; })()");
  steps.headHtml = await evalJs(
    "(() => { const h = document.querySelector('.set-head'); return h ? h.outerHTML : ''; })()");
  steps.secretbar = await evalJs(
    "(() => { const h = document.querySelector('.set-secretbar'); return h ? h.textContent.trim() : ''; })()");
  if (OUTDIR) await shot('02_settings_opened.png');

  // ③ 四个分区逐一看
  const paneInfo = async (i) => evalJs(`(() => {
    const nav = document.querySelectorAll('.set-nav button');
    if (nav[${i}]) nav[${i}].click();
    return true;
  })()`);
  const readPane = () => evalJs(`(() => {
    const pane = document.querySelector('#setpane');
    if (!pane) return { ok: false };
    const rows = [...pane.querySelectorAll('.srow')].map(r => {
      const el = r.querySelector('input,select');
      return {
        label: (r.querySelector('label') || {}).textContent?.trim() || '',
        tag: el ? el.tagName : '',
        type: el ? (el.type || '') : '',
        value: el && el.type === 'checkbox' ? (el.checked ? '1' : '0') : (el ? el.value : ''),
        disabled: el ? !!el.disabled : false,
        options: el && el.tagName === 'SELECT'
          ? [...el.options].map(o => o.value) : [],
        hint: ((r.querySelector('.shint') || {}).textContent || '').slice(0, 60),
      };
    });
    return {
      ok: true,
      title: (pane.querySelector('h3') || {}).textContent?.trim() || '',
      groups: [...pane.querySelectorAll('.set-group > h4')].map(h => h.textContent.trim()),
      rows,
      promptCards: [...pane.querySelectorAll('.spcard .spname')].map(x => x.textContent.trim()),
      promptBtns: [...pane.querySelectorAll('.spcard button')].map(x => x.textContent.trim()),
      hasAdvanced: !!pane.querySelector('.set-adv'),
    };
  })()`);

  const tabs = [];
  const names = ['chat', 'kp', 'ingest', 'deck'];
  for (let i = 0; i < 4; i++) {
    await paneInfo(i);
    await sleep(350);
    const info = await readPane();
    info.tab = names[i];
    info.navLabel = await evalJs(
      `(() => { const n = document.querySelectorAll('.set-nav button')[${i}];`
      + " return n ? n.textContent.trim() : ''; })()");
    tabs.push(info);
    if (OUTDIR) await shot(`0${3 + i}_settings_${names[i]}.png`);
  }
  steps.tabs = tabs;

  // ④ 改一个值 → 保存 → 看真请求与后端回值
  await paneInfo(1);                    // 回到「AI 知识点总结」
  await sleep(300);
  steps.beforeSave = await evalJs(`(() => {
    const s = document.querySelector('#setpane select[data-k="KP_BUILD_REASONING_EFFORT"]');
    return s ? { value: s.value, options: [...s.options].map(o => o.value) } : { value: null };
  })()`);
  await evalJs(`(() => {
    const s = document.querySelector('#setpane select[data-k="KP_BUILD_REASONING_EFFORT"]');
    if (s) s.value = 'high';
    // 同时把模型名改成带标记的值，验证"字符串字段"也提交得上去
    const m = document.querySelector('#setpane input[data-k="KP_BUILD_MODEL"]');
    if (m) m.value = 'deepseek-flash';
    return true;
  })()`);
  await evalJs("document.querySelector('.set-foot .act').click()");
  const saved = await waitFor(
    "(() => { const s = document.querySelector('#setstat');"
    + " return s && /已保存/.test(s.textContent) ? s.textContent.trim() : null; })()", 15000);
  steps.saveMsg = saved;
  steps.saveReq = await evalJs(
    "(() => { const a = (window.__c0059 || []).filter(x => x.method === 'POST');"
    + " const x = a[a.length - 1]; return x ? { url: x.url, body: x.body,"
    + " status: x.status, resp: x.resp.slice(0, 3000) } : null; })()");
  if (OUTDIR) await shot('07_settings_saved.png');

  // ⑤ 重新读取：后端回的当前值应该就是 high（说明配置真的落盘并被认可）
  await evalJs("(async () => { const r = await fetch('/api/settings');"
    + " window.__c0059_reload = await r.json(); })()");
  await waitFor('!!window.__c0059_reload', 10000);
  steps.reload = await evalJs(`(() => {
    const d = window.__c0059_reload || {};
    const kp = (d.pipelines || []).find(p => p.id === 'kp') || {};
    const f = (kp.fields || []).find(x => x.key === 'KP_BUILD_REASONING_EFFORT') || {};
    const sec = (kp.fields || []).find(x => x.type === 'secret') || {};
    return { effortValue: f.value, secretValue: sec.value, secretSet: sec.set,
             secretTail: sec.tail, file: d.settings_file, restart: d.restart_required };
  })()`);

  // ⑥ 密钥框是空的（明文不落到前端）
  steps.secretInputs = await evalJs(`(() => {
    const out = [];
    document.querySelectorAll('#setpane input[type=password]').forEach(el => {
      out.push({ key: el.getAttribute('data-k'), value: el.value,
                 placeholder: el.placeholder });
    });
    return out;
  })()`);

  // ⑦ 提示词真源：点开「查看 / 编辑」，看正文是否就是真源内容（只读，不改）
  await paneInfo(1);
  await sleep(300);
  await evalJs("(() => { const b = document.querySelector('#spc-kp .spact button');"
    + " if (b) b.click(); return !!b; })()");
  const loaded = await waitFor("!!document.querySelector('#sptext-kp')", 10000);
  steps.promptEditor = await evalJs(`(() => {
    const t = document.querySelector('#sptext-kp');
    if (!t) return { ok: false };
    return { ok: true, chars: t.value.length, head: t.value.slice(0, 120),
             tail: t.value.slice(-120),
             hasSysMarker: t.value.indexOf('<!-- PROMPT-SYS-BEGIN -->') >= 0,
             hasUserMarker: t.value.indexOf('<!-- PROMPT-USER-BEGIN -->') >= 0,
             readonly: !!t.readOnly,
             meta: (document.querySelector('.spedit .muted') || {}).textContent || '' };
  })()`);
  steps.promptLoaded = !!loaded;
  if (OUTDIR) await shot('08_settings_prompt_editor.png');

  // ⑧ 关闭
  await evalJs("document.querySelector('.set-x').click()");
  await sleep(300);
  steps.closed = await evalJs(
    "!document.querySelector('#setmodal').classList.contains('on')");

  const out = {
    base: BASE, steps,
    jsErrors: errors, consoleErrors,
    fetchLog: await evalJs("(window.__c0059 || []).map(x => ({url: x.url, method: x.method, status: x.status}))"),
  };
  if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(out, null, 2), 'utf-8');
  console.log('PROBE OK tabs=' + tabs.length + ' jsErrors=' + errors.length);
  ws.close();
  edge.kill();
  return 0;
}

main().then((c) => process.exit(c)).catch((e) => {
  console.error('PROBE FAILED:', e.message);
  try { edge.kill(); } catch { /* ignore */ }
  process.exit(1);
});
