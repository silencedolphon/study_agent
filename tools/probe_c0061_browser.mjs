// 卡 C-0061 真浏览器探针（headless Edge + CDP）：遗忘曲线月历 + 「录入遗忘曲线」按钮。
//
// 验的事（一步一步都留证据）：
//   ① 知识点页 → 课件思维导图栏：`#mm-enroll` 存在，文案 `录入遗忘曲线`，
//      且在 `#mm-ai`（C-0052 的 AI答疑）**左边**（比 DOM 位置，不是靠眼看截图）；
//   ② 复习页签：`#dc-grid` 月历渲染出来（7 列网格 + 当月天数个格子）；
//      队列为空时**队列卡片的原文案仍在**（`今天没有到期的复习 🎉`）——证明
//      "去掉早退"没有把既有文案改掉；
//   ③ 真鼠标点击 `#mm-enroll` → 按钮变「已录入 · 下次 MM-DD」→ 再点一次（幂等）
//      → `/api/mindmap` 里那份课件的 `deck_plan_id` 不变；
//   ④ 回复习页：月历上有 **≥3 个不同日期**标注了课件（逐格抄下 data-day + 事件文本）；
//   ⑤ 真鼠标点某一天 → 明细卡列出"当天该复习的课件"（课件名 / 第 N 轮 / 状态 / 按钮）；
//   ⑥ 真鼠标点「打开课件」→ 跳到知识点页且 `MINDMAP.docId` == 那一份课件；
//   ⑦ 跨学科：切到别的学科 → `/api/deck-calendar` 空 → 切回来 → 计划还在；
//   ⑧ 逾期（由 verify 脚本先把某一轮改成过去时间）：月历格子带 `.overdue`、
//      格内有 `逾期N` 徽标、明细里有 `逾期 N 天`。
//
// 零模型调用：全程只打看板自己的 HTTP 接口，不触发任何 LLM 管线。
//
// 用法：
//   node tools/probe_c0061_browser.mjs --port 8899 --outdir <截图目录> --json <结果 JSON>
//        [--steps enroll,calendar,day,jump,subjects,overdue] [--cdp 9352]
//        [--other 有机化学] [--back 药物化学]
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
const CDP_PORT = parseInt(arg('cdp', '9352'), 10);
const OTHER = arg('other', '有机化学');       // 跨学科验证：切到哪个学科
const BACK = arg('back', '');                 // 切回来（空 = 用当前学科）
const STEPS = arg('steps', 'enroll,calendar,day,jump,mark,subjects,overdue').split(',').map(s => s.trim());
const BASE = `http://127.0.0.1:${PORT}`;
const want = (s) => STEPS.includes(s);

const profile = mkdtempSync(join(tmpdir(), 'c0061-edge-'));
const edge = spawn(EDGE, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-gpu', '--window-size=1500,1000', 'about:blank',
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
    const s = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    if (s.result?.data) { writeFileSync(p, Buffer.from(s.result.data, 'base64')); return p; }
    return null;
  };
  const waitFor = async (expr, ms = 15000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (await evalJs(expr)) return true;
      await sleep(220);
    }
    return false;
  };
  // 真鼠标点击（CDP Input，不是 el.click()）：先滚动到视野中央再按坐标点。
  const clickSel = async (sel) => {
    const box = await evalJs(`(() => {
      const el = document.querySelector(${JSON.stringify(sel)});
      if (!el) return null;
      el.scrollIntoView({block: 'center'});
      const b = el.getBoundingClientRect();
      return {x: b.x + b.width / 2, y: b.y + b.height / 2, w: b.width, h: b.height,
              text: (el.textContent || '').trim().slice(0, 60)};
    })()`);
    if (!box || box.__error) return { ok: false, reason: '元素不存在: ' + sel };
    await send('Input.dispatchMouseEvent',
      { type: 'mouseMoved', x: box.x, y: box.y, button: 'none' });
    await send('Input.dispatchMouseEvent',
      { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent',
      { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    return { ok: true, x: Math.round(box.x), y: Math.round(box.y), text: box.text };
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable').catch(() => {});

  const out = { base: BASE, port: PORT, steps: {}, shots: {}, errors: [] };
  const gotoHome = async () => {
    await send('Page.navigate', { url: `${BASE}/` });
    await sleep(2200);
    await waitFor(`!!document.querySelector('#nav button')`, 12000);
  };
  await gotoHome();

  // ---------- ① 知识点页：按钮落位 ----------
  if (want('enroll')) {
    await evalJs(`(async () => { await go('kps'); return 1; })()`);
    await waitFor(`!!document.querySelector('#mm-enroll')`, 15000);
    out.steps.enroll = await evalJs(`(() => {
      const en = document.querySelector('#mm-enroll');
      const ai = document.querySelector('#mm-ai');
      const hint = document.querySelector('#mm-enroll-hint');
      if (!en || !ai) return { found: false };
      // 位置关系：同一次 querySelectorAll 的文档顺序里，enroll 必须排在 ai **前面**
      const btns = Array.from(document.querySelectorAll('button'));
      return {
        found: true,
        enrollText: (en.textContent || '').trim(),
        enrollClass: en.className,
        enrollHint: hint ? (hint.textContent || '').trim() : '',
        aiText: (ai.textContent || '').trim(),
        aiClass: ai.className,
        sameRow: en.parentElement === ai.parentElement,
        enrollBeforeAi: btns.indexOf(en) < btns.indexOf(ai),
        docId: (window.MINDMAP || {}).docId || 0,
      };
    })()`);
    out.shots.kpsBefore = await shot('01-kps-enroll-button.png');

    // ---------- ③ 真鼠标点「录入遗忘曲线」→ 已录入 ----------
    out.steps.clickEnroll = await clickSel('#mm-enroll');
    await sleep(1600);
    out.steps.afterEnroll = await evalJs(`(() => {
      const en = document.querySelector('#mm-enroll');
      const hint = document.querySelector('#mm-enroll-hint');
      return { text: (en.textContent || '').trim(), cls: en.className,
               hint: hint ? (hint.textContent || '').trim() : '' };
    })()`);
    out.shots.kpsAfter = await shot('02-kps-enrolled.png');

    // 后端真落库的证据：直接从接口读那份课件的 deck_* 字段
    out.steps.afterEnrollApi = await evalJs(`(async () => {
      const r = await fetch('/api/mindmap');
      const j = await r.json();
      const d = (j.documents || []).find(x => x.doc_id === (window.MINDMAP || {}).docId);
      return d ? { doc_id: d.doc_id, title: d.title, plan_id: d.deck_plan_id,
                   next_due: d.deck_next_due, done: d.deck_rounds_done,
                   total: d.deck_rounds_total } : null;
    })()`);

    // 幂等：再点一次（真鼠标），plan_id 必须**不变**、行数不增
    out.steps.clickEnrollAgain = await clickSel('#mm-enroll');
    await sleep(1600);
    out.steps.afterEnrollAgainApi = await evalJs(`(async () => {
      const r = await fetch('/api/mindmap');
      const j = await r.json();
      const d = (j.documents || []).find(x => x.doc_id === (window.MINDMAP || {}).docId);
      return d ? { doc_id: d.doc_id, plan_id: d.deck_plan_id, next_due: d.deck_next_due,
                   done: d.deck_rounds_done, total: d.deck_rounds_total } : null;
    })()`);
    out.shots.kpsAgain = await shot('03-kps-enroll-again.png');
  }

  // ---------- ②④ 复习页：队列空态文案 + 月历标注 ----------
  if (want('calendar')) {
    await evalJs(`(async () => { await go('review'); return 1; })()`);
    await waitFor(`!!document.querySelector('#dc-grid .day')`, 15000);
    out.steps.calendar = await evalJs(`(() => {
      const grid = document.querySelector('#dc-grid');
      const days = Array.from(document.querySelectorAll('#dc-grid .day[data-day]'));
      const marked = days.filter(d => d.querySelector('.ev'));
      return {
        title: (document.querySelector('#dc-title') || {}).textContent || '',
        cells: days.length,
        blanks: document.querySelectorAll('#dc-grid .day.blank').length,
        cols: getComputedStyle(grid).gridTemplateColumns.split(' ').length,
        note: (document.querySelector('#dc-note') || {}).textContent || '',
        queueEmptyText: (document.querySelector('#view .empty') || {}).textContent || '',
        stats: window.DECK ? window.DECK.stats : null,
        markedDays: marked.map(d => ({
          day: d.dataset.day,
          cls: d.className,
          text: (d.textContent || '').replace(/\\s+/g, ' ').trim(),
          events: Array.from(d.querySelectorAll('.ev')).map(e => ({
            text: e.textContent, cls: e.className, title: e.getAttribute('title'),
          })),
          badges: Array.from(d.querySelectorAll('.badge')).map(b => b.textContent),
        })),
      };
    })()`);
    out.shots.reviewCalendar = await shot('04-review-calendar.png');
  }

  // ---------- ⑤⑥ 点某天 → 明细 → 打开课件 ----------
  if (want('day')) {
    const firstDay = (out.steps.calendar?.markedDays || [])[0]?.day || '';
    out.steps.dayClick = await clickSel('#dc-grid .day[data-day="' + firstDay + '"]');
    await sleep(700);
    out.steps.day = await evalJs(`(() => {
      const box = document.querySelector('#dc-daylist');
      const rows = Array.from(box.querySelectorAll('tbody tr')).map(tr => {
        const tds = Array.from(tr.querySelectorAll('td'));
        return {
          title: (tds[0]?.textContent || '').replace(/\\s+/g, ' ').trim(),
          round: (tds[1]?.textContent || '').trim(),
          state: (tds[2]?.textContent || '').trim(),
          buttons: Array.from(tr.querySelectorAll('button')).map(b => b.textContent.trim()),
        };
      });
      return {
        title: (document.querySelector('#dc-daytitle') || {}).textContent || '',
        selDay: window.DECK ? window.DECK.sel : '',
        selInDom: !!document.querySelector('#dc-grid .day.sel'),
        rows: rows,
      };
    })()`);
    // 截图前把明细卡滚进视口：不然截到的是月历，清单本身被裁在屏幕外
    await evalJs(`(() => { const el = document.querySelector('#dc-daylist');
      if (el) el.scrollIntoView({block: 'center'}); return 1; })()`);
    await sleep(400);
    out.shots.dayDetail = await shot('05-day-detail.png');

    if (want('jump')) {
      const before = await evalJs(`(window.MINDMAP || {}).docId || 0`);
      out.steps.jumpClick = await clickSel('#dc-daylist button[data-act="open"]');
      await waitFor(`!!document.querySelector('#mm-doc')`, 15000);
      await sleep(1200);
      out.steps.jump = await evalJs(`(() => ({
        current: window.CURRENT,
        docId: (window.MINDMAP || {}).docId || 0,
        pendingDoc: (window.MINDMAP || {}).pendingDoc || 0,
        selectValue: parseInt((document.querySelector('#mm-doc') || {}).value || '0', 10),
        enrollText: (document.querySelector('#mm-enroll') || {}).textContent || '',
      }))()`);
      out.steps.jump.beforeDocId = before;
      out.shots.jump = await shot('06-jump-to-deck.png');
    }

    // ---------- 写路径：明细里的「标记完成」→ POST /api/deck-mark ----------
    // 只做「标记完成」这一件真事（verify 脚本跑完会把它还原成 pending，保证可重复跑）。
    if (want('mark')) {
      await evalJs(`(async () => { await go('review'); return 1; })()`);
      out.steps.markReady = await waitFor(
        `!!document.querySelector('#dc-daylist button[data-act="done"]')`, 15000);
      if (out.steps.markReady) {
        out.steps.markClick = await clickSel('#dc-daylist button[data-act="done"]');
        await sleep(1800);
        out.steps.mark = await evalJs(`(() => {
          const day = (typeof DECK !== 'undefined' ? DECK.sel : '');
          const cell = day
            ? document.querySelector('#dc-grid .day[data-day="' + day + '"]') : null;
          const box = document.querySelector('#dc-daylist');
          return {
            day: day,
            cellEvCls: cell
              ? Array.from(cell.querySelectorAll('.ev')).map(e => e.className) : [],
            title: (document.querySelector('#dc-daytitle') || {}).textContent || '',
            states: Array.from(box.querySelectorAll('tbody tr')).map(tr =>
              ((tr.querySelectorAll('td')[2] || {}).textContent || '').trim()),
            buttons: Array.from(box.querySelectorAll('button')).map(b => b.textContent.trim()),
            stats: (typeof DECK !== 'undefined' ? DECK.stats : null),
          };
        })()`);
        out.shots.markDone = await shot('11-mark-done.png');
      }
    }
  }

  // ---------- ⑦ 跨学科隔离 ----------
  if (want('subjects')) {
    out.steps.subjects = await evalJs(`(async () => {
      const cur = await (await fetch('/api/subjects')).json();
      const planBefore = await (await fetch('/api/deck-calendar')).json();
      const other = ${JSON.stringify(OTHER)};
      const sw = await (await fetch('/api/subjects/switch', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({slug: other})})).json();
      const calOther = await (await fetch('/api/deck-calendar')).json();
      const back = ${JSON.stringify(BACK)};
      let swBack = null, calBack = null;
      if (back) {
        swBack = await (await fetch('/api/subjects/switch', {method:'POST',
          headers:{'Content-Type':'application/json'}, body: JSON.stringify({slug: back})})).json();
        calBack = await (await fetch('/api/deck-calendar')).json();
      }
      return {
        currentBefore: cur.current,
        plansBefore: planBefore.stats ? planBefore.stats.plans : null,
        daysBefore: planBefore.days ? Object.keys(planBefore.days).length : null,
        switchedTo: other, switchError: sw.error || '',
        otherPlans: calOther.stats ? calOther.stats.plans : null,
        otherDays: calOther.days ? Object.keys(calOther.days).length : null,
        otherTotal: calOther.stats ? calOther.stats.total : null,
        switchedBack: back || '', backError: (swBack && swBack.error) || '',
        backPlans: calBack && calBack.stats ? calBack.stats.plans : null,
        backDays: calBack && calBack.days ? Object.keys(calBack.days).length : null,
      };
    })()`);
    // 切库后重渲染复习页，看月历是不是真的空了/回来了（走页面自己的渲染路径）
    out.steps.subjectsUi = await evalJs(`(async () => {
      const out = {};
      const other = ${JSON.stringify(OTHER)};
      await fetch('/api/subjects/switch', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({slug: other})});
      await go('review');
      for (let i = 0; i < 40 && !document.querySelector('#dc-grid .day'); i++)
        await new Promise(r => setTimeout(r, 250));
      out.otherMarked = Array.from(document.querySelectorAll('#dc-grid .day[data-day] .ev')).length;
      out.otherNote = (document.querySelector('#dc-note') || {}).textContent || '';
      const back = ${JSON.stringify(BACK)};
      if (back) {
        await fetch('/api/subjects/switch', {method:'POST',
          headers:{'Content-Type':'application/json'}, body: JSON.stringify({slug: back})});
        await go('review');
        for (let i = 0; i < 40 && !document.querySelector('#dc-grid .day'); i++)
          await new Promise(r => setTimeout(r, 250));
      }
      out.backMarked = Array.from(document.querySelectorAll('#dc-grid .day[data-day] .ev')).length;
      out.backNote = (document.querySelector('#dc-note') || {}).textContent || '';
      return out;
    })()`);
    out.shots.subjects = await shot('07-subjects-isolation.png');
  }

  // ---------- ⑧ 逾期显示（库已被 verify 脚本改成过去时间） ----------
  if (want('overdue')) {
    await evalJs(`(async () => { await go('review'); return 1; })()`);
    await waitFor(`!!document.querySelector('#dc-grid .day')`, 15000);
    await sleep(800);
    out.steps.overdue = await evalJs(`(() => {
      const over = Array.from(document.querySelectorAll('#dc-grid .day.overdue'));
      const first = over[0];
      const detail = first ? first.dataset.day : '';
      return {
        overdueCells: over.length,
        cells: over.map(d => ({ day: d.dataset.day, cls: d.className,
          text: (d.textContent || '').replace(/\\s+/g, ' ').trim(),
          evCls: Array.from(d.querySelectorAll('.ev')).map(e => e.className),
          badges: Array.from(d.querySelectorAll('.badge')).map(b => b.textContent) })),
        stats: window.DECK ? window.DECK.stats : null,
        note: (document.querySelector('#dc-note') || {}).textContent || '',
        firstOverdueDay: detail,
      };
    })()`);
    out.shots.overdue = await shot('08-overdue-calendar.png');
    const od = out.steps.overdue?.firstOverdueDay;
    if (od) {
      out.steps.overdueClick = await clickSel('#dc-grid .day[data-day="' + od + '"]');
      await sleep(700);
      out.steps.overdueDay = await evalJs(`(() => {
        const box = document.querySelector('#dc-daylist');
        return {
          title: (document.querySelector('#dc-daytitle') || {}).textContent || '',
          states: Array.from(box.querySelectorAll('tbody tr')).map(tr =>
            (tr.querySelectorAll('td')[2] || {}).textContent?.trim() || ''),
          badges: Array.from(box.querySelectorAll('.badge')).map(b => b.textContent.trim()),
        };
      })()`);
      out.shots.overdueDay = await shot('09-overdue-day.png');
    }
  }

  out.errors = errors;
  if (JSONOUT) writeFileSync(JSONOUT, JSON.stringify(out, null, 2), 'utf-8');
  console.log(JSON.stringify({ ok: true, steps: out.steps, errors }, null, 2));
  try { ws.close(); } catch { /* ignore */ }
  edge.kill();
}

main().catch(async (e) => {
  console.error('探针失败:', e);
  try { edge.kill(); } catch { /* ignore */ }
  process.exit(1);
});
