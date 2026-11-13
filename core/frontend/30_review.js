// ---------------- 复习 ----------------
async function viewReview(){
  // 复习流程的三个阶段（自测 / 三步 / 完成）优先；否则渲染复习首页（课件卡 + 队列 + 月历）。
  if (REV.stage === 'selftest' && REV.st){ renderReviewSelftest(); return; }
  if (REV.stage === 'flow' && REV.flowData){ renderReviewFlow(); return; }
  if (REV.stage === 'done'){ REV.stage = ''; }
  await renderReviewHome();
}

// ---------------- 遗忘曲线月历（卡 C-0061） ----------------
// 粒度是**课件**（documents.id），算法是固定间隔表 1/3/7/16/35 天，
// 与上面的「到期复习队列」（知识点级 SM-2）是两个粒度、两张卡片，互不写入。
// 月历**纯手写**：零 CDN、零第三方库，日期计算只有"当月首日星期偏移 + 当月天数"两行。
const DECK = {ym:'', days:{}, stats:{}, sel:'', today:''};

const dcPad = n => String(n).padStart(2, '0');
function dcToday(){
  const t = new Date();
  return `${t.getFullYear()}-${dcPad(t.getMonth()+1)}-${dcPad(t.getDate())}`;
}
function dcMonthOf(ym){
  const p = String(ym || '').split('-');
  return {y: parseInt(p[0], 10) || 0, m: parseInt(p[1], 10) || 0};
}
const dcShort = s => { s = String(s || ''); return s.length > 9 ? s.slice(0, 9) + '…' : s; };

function deckCalendarCard(){
  return `<div class="card" id="dc-card" style="margin-top:14px">
    <div class="row" style="justify-content:space-between">
      <h2 style="margin:0" id="dc-title">复习日历</h2>
      <div class="row" id="dc-controls">
        <button class="ghost" id="dc-prev">‹ 上月</button>
        <button class="ghost" id="dc-today">今天</button>
        <button class="ghost" id="dc-next">下月 ›</button>
      </div>
    </div>
    <div class="small muted" style="margin:6px 0 10px">查看课件的复习安排。</div>
    <div class="calhead"><span>一</span><span>二</span><span>三</span><span>四</span>
      <span>五</span><span>六</span><span>日</span></div>
    <div class="calgrid" id="dc-grid"></div>
    <div class="legend">
      <span><i style="background:#233047"></i>待复习</span>
      <span><i style="background:#16281b"></i>已完成</span>
      <span><i style="background:#33191a"></i>逾期</span>
      <span><i style="background:transparent;border-color:var(--accent)"></i>今天</span>
    </div>
    <div class="small muted" id="dc-note" style="margin-top:10px"></div>
  </div>
  <div class="card" id="dc-day-card" style="margin-top:14px">
    <h2 id="dc-daytitle">当天的课件</h2>
    <div id="dc-daylist"><div class="small muted">点月历上任意一天查看当天清单。</div></div>
  </div>`;
}

async function mountDeckCalendar(){
  const grid = $('#dc-grid'); if (!grid) return;
  const prev = $('#dc-prev'), next = $('#dc-next'), tdy = $('#dc-today');
  if (prev) prev.onclick = () => deckShiftMonth(-1);
  if (next) next.onclick = () => deckShiftMonth(1);
  if (tdy) tdy.onclick = () => {
    DECK.ym = DECK.today.slice(0, 7); DECK.sel = DECK.today; renderDeckCalendar();
  };
  DECK.today = dcToday();
  if (!DECK.ym) { DECK.ym = DECK.today.slice(0, 7); DECK.sel = DECK.today; }
  await renderDeckCalendar();
}

// 切月只改前端状态并重取**那个月**的数据；服务端按月返回，不吃全库。
function deckShiftMonth(delta){
  const cur = dcMonthOf(DECK.ym || DECK.today.slice(0, 7));
  let y = cur.y, m = cur.m + delta;
  if (m < 1) { m = 12; y -= 1; }
  if (m > 12) { m = 1; y += 1; }
  DECK.ym = `${y}-${dcPad(m)}`;
  DECK.sel = '';                 // 换月后旧选中日不在视图里
  renderDeckCalendar();
}

async function renderDeckCalendar(){
  const grid = $('#dc-grid'); if (!grid) return;
  let d;
  try { d = await api('/api/deck-calendar?ym=' + encodeURIComponent(DECK.ym)); }
  catch(e){
    grid.innerHTML = `<div class="empty">月历读取失败：${esc(e.message)}</div>`;
    return;
  }
  DECK.days = d.days || {};
  DECK.stats = d.stats || {};
  DECK.ym = d.ym || DECK.ym;
  DECK.today = d.today || DECK.today;
  if (!DECK.sel) DECK.sel = DECK.today.indexOf(DECK.ym) === 0 ? DECK.today : '';

  const {y, m} = dcMonthOf(DECK.ym);
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7;   // 周一为第一列
  const ndays = new Date(y, m, 0).getDate();               // 当月天数
  let html = '';
  for (let i = 0; i < lead; i++) html += '<div class="day blank"></div>';
  for (let dd = 1; dd <= ndays; dd++){
    const key = `${y}-${dcPad(m)}-${dcPad(dd)}`;
    html += dcCell(key, dd, DECK.days[key] || []);
  }
  grid.innerHTML = html;
  grid.querySelectorAll('.day[data-day]').forEach(el => {
    el.onclick = () => selectDeckDay(el.dataset.day);
  });
  const title = $('#dc-title');
  if (title) title.textContent = `${y} 年 ${m} 月 · 复习日历`;
  const note = $('#dc-note'), st = DECK.stats;
  const empty = !st.plans && !Object.values(DECK.days).some(items =>
    Array.isArray(items) && items.length);
  const card = $('#dc-card'), dayCard = $('#dc-day-card');
  if (card) card.classList.toggle('dc-empty', empty);
  if (dayCard) dayCard.style.display = empty ? 'none' : '';
  if (note){
    if (st.total){
      note.innerHTML = `本月 <b>${st.total}</b> 项：待复习 ${st.pending} · 已完成 ${st.done}` +
        (st.overdue ? ` · <span style="color:var(--bad)">逾期 ${st.overdue}</span>` : '') +
        `　·　总计待复习 ${st.pending_all} 项` +
        (st.overdue_all ? `（其中逾期 <span style="color:var(--bad)">${st.overdue_all}</span>）` : '');
    } else if (!st.plans){
      note.innerHTML = '还没有复习安排。在「知识点」页选择课件即可添加。';
    } else {
      note.innerHTML = `本月没有安排复习。其他日期还有 <b>${st.pending_all}</b> 项待复习` +
        (st.overdue_all ? `（逾期 <span style="color:var(--bad)">${st.overdue_all}</span>）` : '') + '。';
    }
  }
  renderDeckDay();
}

function dcCell(key, dd, list){
  const cls = ['day'];
  if (key === DECK.today) cls.push('today');
  if (key === DECK.sel) cls.push('sel');
  const over = list.filter(x => x.overdue > 0).length;
  if (over) cls.push('overdue');
  const evs = list.slice(0, 3).map(x => {
    const tail = x.overdue > 0 ? `（逾期 ${x.overdue} 天）` : '';
    return `<div class="ev ${x.state === 'done' ? 'done' : (x.overdue > 0 ? 'over' : '')}"` +
      ` title="${esc(x.title)} · 第 ${x.round}/${x.n_rounds} 轮${tail}">` +
      `${esc(dcShort(x.title))}·${x.round}</div>`;
  }).join('');
  return `<div class="${cls.join(' ')}" data-day="${key}">
    <div class="dnum"><span>${dd}</span>${
      over ? `<span class="badge bad">逾期${over}</span>` : ''}${
      list.length ? `<span class="badge">${list.length} 项</span>` : ''}</div>
    ${evs}${list.length > 3 ? `<div class="more">还有 ${list.length - 3} 项</div>` : ''}</div>`;
}

// 点某天：只切换选中样式 + 重画明细卡，不整月重渲染（更快，也不丢滚动位置）。
function selectDeckDay(day){
  DECK.sel = day;
  const grid = $('#dc-grid');
  if (grid) grid.querySelectorAll('.day[data-day]').forEach(el => {
    el.classList.toggle('sel', el.dataset.day === day);
  });
  renderDeckDay();
}

function renderDeckDay(){
  const t = $('#dc-daytitle'), box = $('#dc-daylist');
  if (!t || !box) return;
  const day = DECK.sel, list = (DECK.days || {})[day] || [];
  if (!day){
    t.textContent = '某天该复习哪些课件';
    box.innerHTML = '<div class="small muted">点月历上任意一天查看当天清单。</div>';
    return;
  }
  const pend = list.filter(x => x.state !== 'done').length;
  t.textContent = list.length
    ? `${day} · ${list.length} 项（待复习 ${pend}，已完成 ${list.length - pend}）`
    : `${day} · 无安排`;
  if (!list.length){
    box.innerHTML = '<div class="empty">这一天没有安排复习。</div>';
    return;
  }
  box.innerHTML = `<table class="dc-list"><thead><tr><th>课件</th><th>轮次</th>
    <th>状态</th><th style="width:320px">操作</th></tr></thead><tbody>${
    list.map(x => `<tr>
      <td><b>${esc(x.title)}</b></td>
      <td class="small">第 ${x.round}/${x.n_rounds} 轮</td>
      <td class="small">${x.state === 'done'
        ? '<span class="badge ok">已完成</span>'
        : (x.overdue > 0 ? `<span class="badge bad">逾期 ${x.overdue} 天</span>`
                         : '<span class="badge">待复习</span>')}</td>
      <td><button class="act" data-act="review" data-doc="${x.doc_id}">开始复习</button>${
        ' <button class="ghost" data-act="open" data-doc="' + x.doc_id + '">打开课件</button>'}${
        x.state === 'done' ? ''
        : ` <button class="ghost" data-act="done" data-plan="${x.plan_id}"` +
          ` data-round="${x.round}">标记完成</button>`}</td>
    </tr>`).join('')}</tbody></table>`;
  box.querySelectorAll('button[data-act="review"]').forEach(b => {
    b.onclick = () => revStartFromMonth(parseInt(b.dataset.doc, 10) || 0);
  });
  box.querySelectorAll('button[data-act="open"]').forEach(b => {
    b.onclick = () => openDeckMindmap(parseInt(b.dataset.doc, 10) || 0);
  });
  box.querySelectorAll('button[data-act="done"]').forEach(b => {
    b.onclick = () => markDeckDone(parseInt(b.dataset.plan, 10) || 0,
                                   parseInt(b.dataset.round, 10) || 0);
  });
}

// 跳到该课件的思维导图：切到知识点页签，并让 mountMindmap 优先打开这一份。
function openDeckMindmap(docId){
  if (!docId) return;
  MINDMAP.mode = 'courseware';
  MINDMAP.pendingDoc = docId;
  go('kps');
}

async function markDeckDone(planId, round){
  if (!planId || !round) return;
  try {
    const r = await api('/api/deck-mark', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({plan_id: planId, round: round, state: 'done'})});
    toast(r.next_due
      ? `第 ${round} 轮已完成 · 下次 ${r.next_due}`
      : `第 ${round} 轮已完成 · 这份课件的复习计划已全部完成 🎉`);
    await renderDeckCalendar();
  } catch(e){ toast('标记失败：' + e.message); }
}

// ================= 复习流程（task-33）：自测 / 三步 runner / 悬浮窗 / 页签保持 =================
//
// 用户需求：复习前自测（随机抽一半 KP，总结文字挖名词填空，不能全对=不熟悉）→ 熟悉度；
// 自测后三步复习（思维导图逐个 KP → 整份课件笔记 → 预习重点名词）；遗忘曲线月历当入口；
// 全局进度悬浮窗；切页签后知识点弹窗要回来；笔记页按课件+绑定 KP 整理。
// 后端契约见 /api/review/*（core/review.py 并行开发；未就绪时页面给一句人话，不报 500）。

// ---------------- 页签状态保持（滚动 + 知识点弹窗） ----------------
const UI_PERSIST = {scroll: {}, popup: null, popupSnap: null,
                     keepNavClose: false, restoring: false, holdUntil: 0};

function uiSaveScroll(){
  try { UI_PERSIST.scroll[CURRENT] = window.scrollY || document.documentElement.scrollTop || 0; }
  catch(e){ /* 滚动位置保存失败不影响切页 */ }
}
function uiRestoreScroll(){
  const y = UI_PERSIST.scroll[CURRENT] || 0;
  if (!y) return;
  // 页面内容是异步渲染的：刚渲染完时页面可能还不够高（滚动被钳到 0），
  // 所以分几次重试，等内容/图片把高度撑起来后再落到目标位置。
  let tries = 0;
  const apply = () => {
    try { window.scrollTo(0, y); } catch(e){}
    const cur = window.scrollY || document.documentElement.scrollTop || 0;
    if (Math.abs(cur - y) > 8 && tries < 5){
      tries++;
      setTimeout(apply, 160 + tries * 140);
    }
  };
  requestAnimationFrame(apply);
}
function uiRememberPopup(p){
  if (UI_PERSIST.restoring) return;
  UI_PERSIST.popup = Object.assign({view: CURRENT}, p || {});
}
function uiForgetPopup(){ UI_PERSIST.popup = null; }
async function uiRestorePopup(){
  const p = UI_PERSIST.popup;
  if (!p || (p.view || '') !== CURRENT) return;
  UI_PERSIST.restoring = true;
  UI_PERSIST.holdUntil = Date.now() + 2000;
  try {
    // 快照已由 uiShowPopupSnapshot 同步摆回；这里只负责静默刷新内容，
    // 成功后清掉快照（下次切页会从最新 DOM 重新抓）。
    if (p.type === 'mm') await uiRefreshMmPopup(p);
    else if (p.type === 'db') await uiRefreshDbPopup(p);
    if (UI_PERSIST.popupSnap) uiClearSnapshot();
  } catch(e){ /* 刷新失败就保留快照，绝不清空已显示内容 */ }
  finally {
    UI_PERSIST.restoring = false;
    UI_PERSIST.holdUntil = Date.now() + 1500;
  }
}

// ============ task-36：页签保持修复（快照恢复，不闪不消失） ============
// 用户 bug：打开知识点弹窗→切走→切回，内容闪一下然后消失。
// 根因：①切走时 popup 被 evPopClose 隐藏，切回后要走网络重新渲染，期间先出现
// "正在读取…"占位（闪）；②恢复后滚动位置重试的 window.scroll 事件被 evPopScroll
// 捕获，又把刚恢复的弹窗关掉（消失）。
// 修法：离开前抓 DOM+参数快照；切回时**同步**先把快照摆回来（0ms 即可见），
// 页面渲染完再静默刷新内容；恢复期用 holdUntil 压住"滚动即关"的自动关闭。
// 主动关闭/Esc/评分仍会清空参数+快照，不会自己弹回来。

function uiSnapshotPopup(){
  const p = UI_PERSIST.popup;
  if (!p || !EVPOP || EVPOP.style.display === 'none') return;
  UI_PERSIST.popupSnap = {
    kind: 'ev', view: p.view, params: Object.assign({}, p),
    html: EVPOP.innerHTML, cls: EVPOP.className,
    dataset: {kp: EVPOP.dataset.kp || '', deck: EVPOP.dataset.deck || '',
              kpPop: EVPOP.dataset.kpPop || ''},
    side: {side: !!MMSIDE.open, teach: !!MMTEACH.open},
    at: Date.now(),
  };
}
function uiClearSnapshot(kind){
  const s = UI_PERSIST.popupSnap;
  if (!s) return;
  if (!kind || s.kind === kind) UI_PERSIST.popupSnap = null;
}
function uiSnapshotModal(){
  const p = UI_PERSIST.popup;
  const m = document.getElementById('modal');
  if (!p || !m || !m.classList.contains('on')) return;
  UI_PERSIST.popupSnap = {
    kind: 'modal', view: p.view, params: Object.assign({}, p),
    html: (document.getElementById('modalbox') || {}).innerHTML || '',
    at: Date.now(),
  };
}
function uiShowPopupSnapshot(){
  const s = UI_PERSIST.popupSnap;
  if (!s || (s.view || '') !== CURRENT) return;
  UI_PERSIST.holdUntil = Date.now() + 2500;
  if (s.kind === 'ev'){
    if (!EVPOP) return;
    EVPOP.innerHTML = s.html;
    EVPOP.className = s.cls;
    EVPOP.dataset.kp = s.dataset.kp || '';
    EVPOP.dataset.deck = s.dataset.deck || '';
    EVPOP.dataset.kpPop = s.dataset.kpPop || '1';
    EVPOP.dataset.cite = '';
    EVPOP.style.display = 'flex';
    EVPOP.style.visibility = 'hidden';
    MMSIDE.open = !!s.side.side;
    MMTEACH.open = !!s.side.teach;
    try { mmKpScrollGuard(EVPOP); } catch(e){}
    document.addEventListener('click', evPopOutside, true);
    document.addEventListener('keydown', evPopEsc);
    window.addEventListener('scroll', evPopScroll, true);
    try { evPopPlace(null); } catch(e){ EVPOP.style.visibility = 'visible'; }
  } else if (s.kind === 'modal'){
    const m = document.getElementById('modal');
    const box = document.getElementById('modalbox');
    if (!m || !box) return;
    box.innerHTML = s.html;
    m.classList.add('on');
  }
}
async function uiRefreshMmPopup(p){
  const pop = evPopEl();
  // 快照已经在屏幕上，不再 mount "正在读取…" 占位；直接请求后整块替换。
  pop.dataset.kp = String(p.kpId || '');
  pop.dataset.deck = '';
  pop.dataset.kpPop = '1';
  pop.style.display = 'flex';
  pop.style.visibility = 'visible';
  MMPOP.anchor = null;
  MMPOP.docId = p.docId;
  pop.classList.remove('mmkp-has-side', 'mm-tree-open', 'mmkp-has-teach', 'mm-teach-open');
  let d;
  try {
    d = await api('/api/summary-kp?doc_id=' + encodeURIComponent(p.docId) +
                  '&kp_id=' + encodeURIComponent(p.kpId));
  } catch(e){ return; }   // 失败就保留快照，不把内容清掉
  if ((evPopEl().dataset.kp || '') !== String(p.kpId) || CURRENT !== p.view) return;
  UI_PERSIST.holdUntil = Date.now() + 1500;
  mmRenderKp(d, null, false);
}
async function uiRefreshDbPopup(p){
  try { await openKp(p.id); } catch(e){ /* 失败就保留快照 */ }
}
// ---------------- 复习状态（内存 + localStorage） ----------------
const REV = {stage: '', docId: 0, title: '', flowData: null, st: null, step: 1,
             kpIdx: 0, termIdx: 0, result: null, coursewares: [], stResults: {},
             stepOpen: '', active: false, minimized: false};
const REV_LS = 'study_agent_review_flow_v1';

function revSave(){
  try {
    localStorage.setItem(REV_LS, JSON.stringify({
      active: !!REV.active, docId: REV.docId, title: REV.title,
      step: REV.step, kpIdx: REV.kpIdx, termIdx: REV.termIdx,
      minimized: !!REV.minimized, updated: Date.now(),
    }));
  } catch(e){ /* 隐私模式写不进去：不影响本次会话使用 */ }
}
function revDrop(){
  try { localStorage.removeItem(REV_LS); } catch(e){}
}
function reviewBoot(){
  let s = null;
  try { s = JSON.parse(localStorage.getItem(REV_LS) || 'null'); } catch(e){ s = null; }
  if (s && s.active && s.docId){
    REV.active = true; REV.docId = parseInt(s.docId, 10) || 0;
    REV.title = String(s.title || ''); REV.step = Math.min(3, Math.max(1, parseInt(s.step, 10) || 1));
    REV.kpIdx = parseInt(s.kpIdx, 10) || 0; REV.termIdx = parseInt(s.termIdx, 10) || 0;
    REV.minimized = !!s.minimized;
  }
  revFloatUpdate();
}

// ---------------- 悬浮进度窗 ----------------
// ---------------- 悬浮进度窗 v2（继续=重开当前步窗口；下一步/跳过本步/结束） ----------------
function revFloatActions(){
  if (REV.stage === 'selftest'){
    return [['continue', '继续'], ['end', '结束']];
  }
  if (REV.stage !== 'flow') return [['continue', '继续'], ['end', '结束']];
  const acts = [['continue', '继续']];
  if (REV.step === 1) acts.push(['next', '下一个']);
  else if (REV.step === 2) acts.push(['next', '下一步']);
  else acts.push(['next', '下一个词']);
  acts.push(['skip', '跳过' + revStepCn(REV.step)], ['end', '结束']);
  return acts;
}

function revFloatUpdate(){
  let el = document.getElementById('rev-float');
  if (!el){
    el = document.createElement('div');
    el.id = 'rev-float';
    document.body.appendChild(el);
  }
  if (!REV.active){ el.classList.remove('on', 'rvf-min'); return; }
  const f = REV.flowData || {};
  const kps = f.kps || [];
  const items = (f.terms || {}).items || [];
  const stageTxt = REV.stage === 'selftest' ? '自测'
    : (REV.stage === 'flow' ? ('Step ' + REV.step + '/3') : '复习');
  let pos = '';
  if (REV.stage === 'selftest' && REV.st){
    const picked = REV.stResults || {};
    const n = (REV.st.items || REV.st.kps || []).length;
    const k = (REV.st.items || REV.st.kps || []).filter(x =>
      Object.prototype.hasOwnProperty.call(picked, String(x.kp_id))).length;
    pos = ` · 已标记 ${k}/${n}`;
  } else if (REV.stage === 'flow'){
    if (REV.step === 1) pos = ` · ${Math.min(REV.kpIdx + 1, kps.length)}/${kps.length}${revCurrentKp().name ? ' ' + revCurrentKp().name : ''}`;
    else if (REV.step === 2) pos = ' · 笔记';
    else pos = ` · 名词 ${Math.min(REV.termIdx + 1, Math.max(1, items.length))}/${items.length}`;
  }
  const acts = revFloatActions();
  const btns = acts.map(([k, label]) => `<button class="${k === 'continue' ? 'act' : (k === 'end' ? '' : 'ghost')}"
    data-f="${k}"${k === 'end' ? ' style="color:var(--bad);border-color:#4a1f1c"' : ''}>${label}</button>`).join('');
  el.className = 'on' + (REV.minimized ? ' rvf-min' : '');
  el.innerHTML = REV.minimized
    ? `<div class="rvf-t">📖 复习中 · ${esc(stageTxt)}${esc(pos)} · 点开继续</div>`
    : `<div class="rvf-t">📖 复习中 · ${esc(REV.title || '课件')}</div>
       <div class="rvf-m">${esc(stageTxt)}${esc(pos)}</div>
       <div class="rvf-act">${btns}</div>`;
  const bind = (key) => {
    const b = el.querySelector(`[data-f="${key}"]`);
    if (!b) return;
    b.onclick = (e) => {
      e.stopPropagation();
      if (key === 'continue') revContinue();
      else if (key === 'next') revFloatNext();
      else if (key === 'skip') revSkipStep();
      else if (key === 'end') revEnd();
    };
  };
  if (REV.minimized){
    el.onclick = () => { REV.minimized = false; revSave(); revFloatUpdate(); };
  } else {
    el.onclick = null;
    acts.forEach(([k]) => bind(k));
  }
}

async function revFloatNext(){
  if (REV.stage !== 'flow') return;
  await revFlowNext();
}

async function revEnd(){
  if (REV.active){
    const ok = await confirmBox('结束本次复习？',
      '进度会从「复习」页的课件卡重新开始；已得熟悉度会保留。');
    if (!ok) return;
  }
  revCloseReviewWindows();
  REV.active = false; REV.stage = ''; REV.step = 1; REV.kpIdx = 0;
  REV.flowData = null; REV.st = null;
  revDrop(); revFloatUpdate();
  toast('已结束本次复习');
  if (CURRENT === 'review') go('review');
}

// ---------------- 复习页（课件复习流程 + 原队列 + 月历） ----------------
async function renderReviewHome(){
  REV.stage = ''; REV.st = null; REV.result = null;
  let queue = [];
  try { queue = (await api('/api/queue?limit=30')).queue || []; } catch(e){ queue = []; }
  let cws = null, cwErr = '';
  try {
    const d = await api('/api/review/coursewares');
    if (d && d.ok === false) cwErr = d.error || '复习后端还没就绪';
    else cws = d.items || d.documents || d.coursewares || [];
    if (!Array.isArray(cws)) cws = [];
  } catch(e){ cwErr = e.message; }
  let bookDue = null, bookErr = '';
  try { bookDue = await api('/api/textbook-kp/due'); }
  catch(e){ bookErr = e.message; }
  REV.coursewares = cws || [];
  const queueHtml = reviewQueueHtml(queue);
  const cwHtml = await reviewCoursewaresHtml(cws, cwErr);
  const bookHtml = bookErr ? `<div class="small muted">教材复习读取失败：${esc(bookErr)}</div>`
    : (bookDue && bookDue.due ? `<details class="card docs-orphans book-due" style="margin-bottom:14px">
        <summary>教材知识点 · ${Number(bookDue.due)} 个待回顾</summary>
        ${(bookDue.chapters || []).map(ch => `<div class="row book-due-row">
          <span>${esc(ch.title)} · 第 ${Number(ch.chapter)} 章 ${esc(ch.chapter_name || '')}
            <span class="small muted">${Number(ch.due)} 个待回顾</span></span>
          <button class="ghost" data-book-due-doc="${Number(ch.doc_id)}" data-book-due-chapter="${Number(ch.chapter)}">去回顾</button>
        </div>`).join('')}</details>` : '');
  $('#view').innerHTML = bookHtml + cwHtml + queueHtml + deckCalendarCard();
  $('#view').querySelectorAll('[data-book-due-doc]').forEach(b => {
    b.onclick = () => openTextbookReview(Number(b.dataset.bookDueDoc), Number(b.dataset.bookDueChapter));
  });
  bindReviewCoursewares();
  await mountDeckCalendar();
}

function reviewQueueHtml(queue){
  return !queue.length
    ? `<div class="empty">今天没有到期的复习</div>`
    : `<div class="card">
      <h2>到期复习队列 · 共 ${queue.length} 项</h2>
      <table><thead><tr><th>知识点</th><th>科目</th><th>掌握度</th><th>已复习</th>
      </tr></thead><tbody>${queue.map(x => `
    <tr>
      <td><a href="#" onclick="openKp(${x.id});return false">${esc(x.name)}</a>
        ${x.status==='new' ? '<span class="badge">新</span>'
          : (x.overdue_days>0?`<span class="badge warn">逾期${x.overdue_days}天</span>`:'')}
        <div class="small muted">${mdToHtml(x.summary||'')}</div></td>
      <td class="small">${x.subject?esc(x.subject):'—'}</td>
      <td style="width:150px">
        <div class="bar"><i style="width:${pct(x.mastery)}%"></i></div>
        <span class="small muted">${pct(x.mastery)}%</span></td>
      <td class="small">${x.reps||0} 次</td>
    </tr>`).join('')}</tbody></table>
      <div class="sub" style="margin-top:12px">点击知识点查看出处并评分。</div>
    </div>`;
}

async function reviewCoursewaresHtml(items, err){
  if (!items.length){
    return `<div class="card rv-card">
      <h2>课件复习</h2>
      <div class="rv-empty">${err ? '读取失败：' + esc(err) : '还没有可复习的课件。'}
        <br><span class="small">先到「知识点」页整理一份课件。</span>
        <div class="row" style="margin-top:10px"><button class="ghost" onclick="go('kps')">前往知识点</button>
        ${err ? `<button class="ghost" id="rv-cw-retry">重试</button>` : ''}</div></div></div>`;
  }
  const cards = items.map(it => {
    const stt = it.selftest || {};
    const fam = Math.round((parseFloat(stt.familiarity) || 0) * 100);
    // 自测 v3：熟悉度 = 已掌握 / 抽样（n_mastered/n_sample）；卡片按这个口径显示
    const nSample = parseInt(stt.n_sample, 10) || 0;
    const nMastered = (stt.n_mastered != null)
      ? parseInt(stt.n_mastered, 10) || 0
      : Math.round((parseFloat(stt.familiarity) || 0) * nSample);
    const badge = stt.exists
      ? `<span class="rv-badge ok" title="已掌握 ${nMastered}/${nSample}">熟悉度 ${fam}%</span>`
      : '<span class="rv-badge warn">未自测</span>';
    const terms = it.terms || {};
    const ch = it.chapter || {};
    const chName = String(ch.name || '');
    const chTxt = (ch.no || chName)
      ? (/第\s*\d+\s*章/.test(chName) ? chName : `第${ch.no || '?'}章 ${chName}`) : '';
    const meta = [`${it.n_kps || 0} 条知识点`,
                  terms.available ? `预习名词 ${terms.n || 0} 个` : '暂无预习名词',
                  chTxt].filter(Boolean).join(' · ');
    const nextAct = stt.exists ? 'flow' : 'selftest';
    const mainTxt = stt.exists ? '继续复习' : '开始自测';
    return `<div class="rv-cw">
      <div class="rv-cw-h"><b>${esc(it.title || ('doc#' + it.doc_id))}</b>${badge}</div>
      <div class="rv-cw-meta">${meta}</div>
      <div class="rv-cw-act">
        <button class="act" data-doc="${it.doc_id}" data-act="${nextAct}">${mainTxt}</button>
        ${stt.exists ? `<details class="row-actions"><summary aria-label="更多复习操作" title="更多操作">⋯</summary>
          <div class="row-actions-menu"><button class="ghost" data-doc="${it.doc_id}" data-act="selftest">重新自测</button></div></details>` : ''}
      </div></div>`;
  }).join('');
  return `<div class="card rv-card">
    <h2>选择课件复习</h2>
    <div class="small muted" style="margin-top:4px">先自测，再按知识点、笔记和重点名词复习。${err ? '（读取失败：' + esc(err) + '）' : ''}</div>
    <div class="rv-cws">${cards}</div></div>`;
}

function bindReviewCoursewares(){
  const retry = $('#rv-cw-retry');
  if (retry) retry.onclick = () => go('review');
  $('#view').querySelectorAll('.rv-cw-act button[data-doc]').forEach(b => {
    b.onclick = () => {
      const doc = parseInt(b.dataset.doc, 10) || 0;
      if (!doc) return;
      if (b.dataset.act === 'flow') revStartFlow(doc, {step: 1});
      else revStartSelftest(doc);
    };
  });
}

// 月历某天的课件 → 「开始复习」：已有自测直接三步，没有先自测。
async function revStartFromMonth(docId){
  if (!docId) return;
  try {
    const st = await api('/api/review/selftest/status?doc_id=' + encodeURIComponent(docId));
    if (st && st.ok !== false && st.exists) return revStartFlow(docId, {step: 1});
  } catch(e){ /* 状态读不到就按未自测处理 */ }
  return revStartSelftest(docId);
}

// ---------------- 自测 v2 ----------------
async function revStartSelftest(docId){
  try {
    const d = await api('/api/review/selftest?doc_id=' + encodeURIComponent(docId));
    if (!d || d.ok === false) throw new Error((d && d.error) || '自测数据读不到');
    REV.st = d; REV.stage = 'selftest'; REV.result = null; REV.stResults = {};
    REV.docId = parseInt(d.doc_id || docId, 10) || docId;
    REV.title = d.title || '';
    REV.active = true; REV.minimized = false;
    revSave(); revFloatUpdate();
    await go('review');
  } catch(e){ toast('开始自测失败：' + e.message); }
}

function reviewBranchOf(kp){
  if (Array.isArray(kp.branch) && kp.branch.length) return kp.branch;
  // 兼容旧后端：只有 node_path 时按层级展开（打头的是课件标题，不画成树节点）
  const path = (kp.node_path || []).filter(Boolean);
  const start = path.length > 1 ? 1 : 0;
  return path.slice(start).map((name, i) => ({name: String(name), level: i + 1}));
}

function reviewPathHtml(kp, qi){
  const br = reviewBranchOf(kp);
  if (!br.length){
    return `<div class="rv-path"><div class="rv-pnode lv1 self">${esc(kp.name || kp.kp_id || '')}</div></div>`;
  }
  const rows = br.map((b, i) => {
    const self = i === br.length - 1;
    const last = i === br.length - 1;
    const pad = Math.max(0, (parseInt(b.level, 10) || i + 1) - 1) * 16;
    return `<div class="rv-pnode${self ? ' self' : ''}" style="margin-left:${pad}px">
      <span class="rv-ptree">${last ? '└─' : '├─'}</span>
      <span>${esc(b.name || '')}</span></div>`;
  }).join('');
  return `<div class="rv-path" aria-label="第 ${qi + 1} 条知识点的结构路径">${rows}</div>`;
}

function reviewSummaryHtml(kp){
  const sum = String(kp.summary || '').trim();
  const pts = kp.points || [];
  if (!sum && !pts.length) return '';
  return `<details class="rv-sum"><summary>展开总结</summary>
    <div class="rv-sum-body">${mdToHtml(sum || '（这条没有总结正文，下面是要点）')}${
      pts.length ? `<ul>${pts.map(p => `<li>${mdToHtml(p)}</li>`).join('')}</ul>` : ''}</div>
  </details>`;
}

// 自测与知识点页共用同一棵导图和弹窗，但独立保存展开状态。
const REV_MAP = {docId: 0, subject: '', session: '', tree: null, nodes: {}, open: {}, downshift: false};
async function revMountSelftestMap(){
  const box = $('#rv-selftest-map');
  if (!box || !REV.docId) return;
  const docId = Number(REV.docId), subject = SUBJ.cur, session = String((REV.st || {}).session_id || '');
  if (REV_MAP.docId !== docId || REV_MAP.subject !== subject || REV_MAP.session !== session){
    Object.assign(REV_MAP, {docId, subject, session, tree: null, nodes: {}, open: {}, downshift: false});
  }
  if (!REV_MAP.tree){
    box.innerHTML = '<div class="small muted">正在读取课件导图…</div>';
    try {
      const d = await api('/api/mindmap?doc_id=' + encodeURIComponent(docId));
      if (REV.stage !== 'selftest' || Number(REV.docId) !== docId || String((REV.st || {}).session_id || '') !== session || SUBJ.cur !== subject || $('#rv-selftest-map') !== box) return;
      if (!d.has_structure) { box.innerHTML = '<div class="small muted">这份课件暂无可用导图。</div>'; return; }
      REV_MAP.tree = d.tree || [];
      REV_MAP.downshift = !!d.lkp_downshift;
    } catch(e){ if ($('#rv-selftest-map') === box) box.innerHTML = `<div class="small">导图读取失败：${esc(e.message)}</div>`; return; }
  }
  REV_MAP.nodes = {};
  box.innerHTML = `<div class="mmtree">${mmNodes(REV_MAP.tree, '', REV_MAP)}</div>`;
  box.querySelectorAll('details.mmd').forEach(el => {
    el.addEventListener('toggle', () => { REV_MAP.open[el.dataset.k] = el.open; });
  });
  box.querySelectorAll('.mmname').forEach(el => {
    el.onclick = e => {
      e.preventDefault(); e.stopPropagation();
      const node = REV_MAP.nodes[el.dataset.k];
      if (!node) return;
      if (node.lkp_ready){
        evPopEl().dataset.kpPop = '1';
        if ((node.lkp_ids || []).length > 1) mmOpenKpList(node, el, docId);
        else mmOpenKp(node.lkp_id, node.lkp_name || node.name, el, false, docId);
      } else if (node.children && node.children.length){
        const details = el.closest('details'); if (details) details.open = !details.open;
      } else { mmOpenPending(node, el); }
    };
  });
}

function renderReviewSelftest(){
  const d = REV.st || {};
  const res = REV.result;
  const kps = d.items || d.kps || [];
  const picked = REV.stResults || {};
  const nPicked = kps.filter(k => Object.prototype.hasOwnProperty.call(picked, String(k.kp_id))).length;
  const cards = kps.map((kp, qi) => {
    let ansHtml = '';
    if (res){
      const r = (res.results || []).find(x => String(x.kp_id) === String(kp.kp_id)) || {};
      const ok = !!r.mastered;
      ansHtml = `<div class="rv-q-ans"><span class="${ok ? 'ok' : 'bad'}">${
        ok ? '✓ 已掌握' : '✗ 未掌握'}</span></div>`;
    }
    const mark = picked[String(kp.kp_id)];
    const clsOk = mark === true ? ' on ok' : '';
    const clsBad = mark === false ? ' on bad' : '';
    return `<div class="rv-q" data-kp="${esc(kp.kp_id)}">
      ${reviewPathHtml(kp, qi)}
      <div class="rv-q-name">${esc(kp.name || kp.kp_id)}</div>
      ${res ? '' : `<button type="button" class="ghost rv-exclude" data-kp="${esc(kp.kp_id)}">从自测排除</button>`}
      ${reviewSummaryHtml(kp)}
      ${res ? '' : `<div class="rv-master" role="group" aria-label="这条知识点是否已掌握">
        <button type="button" class="rv-mbtn ok${clsOk}" data-kp="${esc(kp.kp_id)}" data-val="1">已掌握</button>
        <button type="button" class="rv-mbtn bad${clsBad}" data-kp="${esc(kp.kp_id)}" data-val="0">未掌握</button>
      </div>`}
    </div>${ansHtml}`;
  }).join('');
  const resultHtml = res ? (() => {
    const nM = parseInt(res.n_mastered, 10) || 0;
    const nS = parseInt(res.n_sample, 10) || kps.length;
    const fam = Math.round((parseFloat(res.familiarity) || 0) * 100);
    const badIds = res.unfamiliar_ids || [];
    const badNames = badIds.map(id => {
      const kp = kps.find(x => String(x.kp_id) === String(id)) || {};
      return esc(kp.name || id);
    });
    return `<div class="rv-res">
      <div>本次熟悉度 <span class="big">${fam}%</span>
        <span class="small muted">（已掌握 ${nM} / 抽样 ${nS}）</span></div>
      ${badNames.length ? `<div class="small" style="margin-top:6px;color:var(--warn)">
        未掌握：${badNames.join('、')}</div>`
        : '<div class="small" style="margin-top:6px;color:var(--ok)">全部标记为已掌握，这份课件算熟悉 🎉</div>'}
    </div>`;
  })() : '';
  const actions = res
    ? `<button class="act" id="rv-st-flow">进入三步复习</button>
       <button class="ghost" id="rv-st-again">再测一次</button>
       <button class="ghost" id="rv-st-home">返回复习页</button>`
    : `<button class="act" id="rv-st-submit" ${!kps.length || nPicked < kps.length ? 'disabled' : ''}>${
        nPicked < kps.length ? `提交自测（${nPicked}/${kps.length} 已标记）` : '提交自测'}
       </button>
       ${d.n_excluded ? `<button class="ghost" id="rv-st-restore">恢复已排除知识点（${d.n_excluded}）</button>` : ''}
       <button class="ghost" id="rv-st-home">返回复习页</button>`;
  $('#view').innerHTML = `
    <div class="card rv-card">
      <div class="rv-st-head"><h2>📝 课件自测 · ${esc(d.title || '')}</h2>
        <span class="rv-badge">随机抽 ${d.n_sample || kps.length} / ${d.n_total || kps.length} 条知识点</span></div>
      <div class="rv-st-tip">看结构路径回忆这条知识点，点「展开总结」核对；然后<b>标记自己能否复述/掌握</b>。
        不要求写字；熟悉度 = <b>已掌握条数 / 抽样条数</b>，全部标记完才能提交。</div>
      <div class="rv-selftest-layout">
        <aside class="rv-selftest-map"><div class="rv-selftest-map-head"><b>课件导图</b><span class="small muted">点击知识点查看</span></div><div id="rv-selftest-map"></div></aside>
        <div class="rv-q-list">${cards || '<div class="empty">本课件没有可抽取的知识点，请使用下方按钮恢复已排除的知识点。</div>'}</div>
      </div>
      ${resultHtml}
      <div class="rv-nav">${actions}</div>
    </div>`;
  bindReviewSelftest();
  revMountSelftestMap();
  revFloatUpdate();
}

function reviewSyncSelftestButtons(){
  const kps = (REV.st && (REV.st.items || REV.st.kps)) || [];
  const picked = REV.stResults || {};
  const nPicked = kps.filter(k => Object.prototype.hasOwnProperty.call(picked, String(k.kp_id))).length;
  const btn = $('#rv-st-submit');
  if (btn){
    btn.disabled = !kps.length || nPicked < kps.length;
    btn.textContent = nPicked < kps.length
      ? `提交自测（${nPicked}/${kps.length} 已标记）` : '提交自测';
  }
  revFloatUpdate();
}

function bindReviewSelftest(){
  const home = $('#rv-st-home');
  if (home) home.onclick = () => { REV.stage = ''; REV.st = null; go('review'); };
  const again = $('#rv-st-again');
  if (again) again.onclick = () => revStartSelftest(REV.docId);
  const flow = $('#rv-st-flow');
  if (flow) flow.onclick = () => revStartFlow(REV.docId, {step: 1});
  const submit = $('#rv-st-submit');
  if (submit) submit.onclick = () => submitReviewSelftest();
  const restore = $('#rv-st-restore');
  if (restore) restore.onclick = () => reviewUpdateExclusion('', true);
  document.querySelectorAll('#view .rv-exclude').forEach(b => {
    b.onclick = () => reviewUpdateExclusion(b.dataset.kp, false);
  });
  document.querySelectorAll('#view .rv-master .rv-mbtn').forEach(b => {
    b.onclick = () => {
      const kp = b.dataset.kp;
      const val = b.dataset.val === '1';
      if (!REV.stResults) REV.stResults = {};
      REV.stResults[kp] = val;
      const box = b.closest('.rv-master');
      box.querySelectorAll('.rv-mbtn').forEach(x => x.classList.remove('on', 'ok', 'bad'));
      b.classList.add('on');
      b.classList.add(val ? 'ok' : 'bad');
      reviewSyncSelftestButtons();
    };
  });
}

async function reviewUpdateExclusion(kpId, restore){
  const d = REV.st;
  if (!d || REV.result) return;
  const msg = restore ? '恢复这份课件已排除的知识点，之后自测可以再次抽到。'
    : '此知识点今后不会出现在这份课件的自测中；导图和原始总结仍保留。可随时恢复。';
  if (!await confirmBox(restore ? '恢复知识点？' : '从自测排除？', msg)) return;
  try {
    const previousCount = (d.items || []).length;
    const r = await api('/api/review/selftest/exclusion', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({doc_id:REV.docId, session_id:d.session_id, kp_id:kpId, restore})});
    if (!r.ok) throw new Error(r.error || '操作失败');
    if (!restore) delete REV.stResults[String(kpId)];
    Object.assign(d, {items:r.items, n_total:r.n_total, n_sample:r.n_sample, n_excluded:r.n_excluded});
    renderReviewSelftest();
    toast(restore ? '已恢复排除的知识点' : (r.items.length >= previousCount ? '已排除并从剩余知识点补充' : '已排除；没有足够的其他知识点补充，本次题数减少'));
  } catch(e) { toast('操作失败：' + e.message); }
}

async function submitReviewSelftest(){
  const d = REV.st;
  if (!d) return;
  const kps = d.items || d.kps || [];
  const results = {};
  let missing = 0;
  kps.forEach(kp => {
    const key = String(kp.kp_id);
    if (!Object.prototype.hasOwnProperty.call(REV.stResults || {}, key)) missing++;
    else results[kp.kp_id] = !!REV.stResults[key];
  });
  if (missing){ toast(`还有 ${missing} 条没标记（已掌握/未掌握）；全部标记后才能提交`); return; }
  const btn = $('#rv-st-submit');
  if (btn){ btn.disabled = true; btn.textContent = '提交中…'; }
  try {
    const r = await api('/api/review/selftest/submit', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({doc_id: REV.docId, session_id: d.session_id, results: results})});
    if (!r || r.ok === false) throw new Error((r && r.error) || '提交失败');
    REV.result = r;
    renderReviewSelftest();
    toast(`自测完成：已掌握 ${r.n_mastered || 0}/${r.n_sample || 0}`);
  } catch(e){
    toast('自测提交失败：' + e.message);
    reviewSyncSelftestButtons();
  }
}

// ---------------- 三步复习 v2（窗口化 + 可跳过 + 悬浮窗回当前步） ----------------
async function revStartFlow(docId, opts){
  opts = opts || {};
  try {
    const d = await api('/api/review/flow?doc_id=' + encodeURIComponent(docId));
    if (!d || d.ok === false) throw new Error((d && d.error) || '复习数据读不到');
    REV.flowData = d; REV.stage = 'flow'; REV.active = true;
    REV.docId = parseInt((d.doc || {}).id || docId, 10) || docId;
    REV.title = (d.doc || {}).title || '';
    REV.step = Math.min(3, Math.max(1, parseInt(opts.step, 10) || 1));
    REV.kpIdx = Math.max(0, parseInt(opts.idx, 10) || 0);
    REV.termIdx = 0; REV.minimized = false; REV.revealed = {}; REV.stepOpen = '';
    revSave(); revFloatUpdate();
    await go('review');
    await revOpenStepWindow();
  } catch(e){ toast('复习数据读取失败：' + e.message); }
}

function revCurrentKp(){
  const f = REV.flowData || {};
  const kps = f.kps || [];
  return kps[Math.min(REV.kpIdx, Math.max(0, kps.length - 1))] || {};
}
function revCurrentTerm(){
  const items = ((REV.flowData || {}).terms || {}).items || [];
  return items[Math.min(REV.termIdx, Math.max(0, items.length - 1))] || {};
}
function revStepCn(step){ return ['', '第一步（知识点）', '第二步（笔记）', '第三步（重点名词）'][step] || ''; }

function revCloseReviewWindows(){
  try { if (EVPOP && EVPOP.style.display !== 'none') evPopClose(); } catch(e){}
  try {
    const m = document.getElementById('modal');
    if (m && m.classList.contains('on')) closeModal();
  } catch(e){}
  REV.stepOpen = '';
}

async function revOpenStepWindow(){
  if (!REV.active || REV.stage !== 'flow') return;
  if (CURRENT !== 'review'){ await go('review'); if (CURRENT !== 'review') return; }
  if (REV.step === 1){
    const kp = revCurrentKp();
    if (!kp.kp_id){ toast('这份课件没有可复习的知识点'); return; }
    REV.stepOpen = 'kp';
    evPopEl().dataset.kpPop = '1';
    await mmOpenKp(kp.kp_id, kp.name, null, false, REV.docId);
    revSave();
  } else if (REV.step === 2){
    REV.stepOpen = 'notes';
    revOpenNotesWindow();
  } else {
    REV.stepOpen = 'term';
    revOpenTermWindow();
  }
}

function revNotesRowsHtml(){
  const f = REV.flowData || {};
  const notes = f.notes || {};
  const groups = (notes.groups || []).filter(g => (g.notes || []).length);
  const un = notes.unbound || [];
  const groupHtml = groups.map(g => `<details class="rv-note-grp" open>
    <summary>${esc(g.kp_name || g.kp_id || '未命名知识点')} · ${(g.notes || []).length} 篇</summary>
    ${g.notes.map(n => `<div class="rv-note-row"><a href="#" class="rv-note-open"
        data-file="${esc(n.file)}">${esc(n.title || n.file || '')}</a>
      <span class="rv-note-meta">${esc(n.source_label || '')}${n.chars ? ' · ' + n.chars + ' 字' : ''}${
        n.created || n.mtime ? ' · ' + esc(fmtTime(n.created || n.mtime)) : ''}</span></div>`).join('')}
  </details>`).join('');
  const unHtml = un.length ? `<details class="rv-note-grp"><summary>未绑定具体知识点 · ${un.length} 篇</summary>
    ${un.map(n => `<div class="rv-note-row"><a href="#" class="rv-note-open" data-file="${esc(n.file)}">${
      esc(n.title || n.file || '')}</a><span class="rv-note-meta">${esc(n.source_label || '')}</span></div>`).join('')}
  </details>` : '';
  return groupHtml + unHtml || '<div class="rv-empty">这份课件还没有笔记；点「跳过第二步」继续。</div>';
}

function revOpenNotesWindow(){
  modal(`<h2>📒 第二步 · 整份课件的笔记</h2>
    <div class="small muted" style="margin-bottom:10px">按绑定知识点分组；点标题看原文（可叠加）。
      关闭原文后点右下悬浮窗「继续」可回到本窗口。</div>
    ${revNotesRowsHtml()}
    <div class="row" style="margin-top:14px">
      <button class="act" id="rv-note-next">下一步：重点名词</button>
      <button class="ghost" id="rv-note-skip">跳过第二步</button>
      <button class="ghost" onclick="closeModal()">收起窗口</button>
    </div>`);
  document.querySelectorAll('#modalbox .rv-note-open').forEach(a => {
    a.onclick = (e) => { e.preventDefault(); openNote(a.dataset.file); };
  });
  const next = document.getElementById('rv-note-next');
  if (next) next.onclick = () => { revCloseReviewWindows(); revFlowGo(3); revOpenStepWindow(); };
  const skip = document.getElementById('rv-note-skip');
  if (skip) skip.onclick = () => { revCloseReviewWindows(); revFlowGo(3); revOpenStepWindow(); };
}

function revTermWindowHtml(){
  const f = REV.flowData || {};
  const items = (f.terms || {}).items || [];
  if (!items.length){
    return `<h2>🔤 第三步 · 预习重点名词</h2>
      <div class="rv-empty">这份课件没有可用的预习重点名词（教材预习产物里还没有 terms）。
        这一步可以直接跳过。</div>
      <div class="row" style="margin-top:14px">
        <button class="act" id="rv-term-finish">完成第三步</button>
        <button class="ghost" id="rv-term-skip">跳过第三步</button>
      </div>`;
  }
  const i = Math.min(REV.termIdx, items.length - 1);
  const t = items[i];
  const key = i + ':' + (t.term || '');
  const open = !!(REV.revealed && REV.revealed[key]);
  return `<h2>🔤 第三步 · 预习重点名词</h2>
    <div class="small muted" style="margin-bottom:6px">${esc((f.terms || {}).chapter && ((f.terms).chapter.name) || '')}
      · 来源 ${esc((f.terms || {}).source === 'preview_chapter' ? '预习名词' : ((f.terms || {}).source || '—'))}</div>
    <div class="rv-term" id="rv-term">
      <div class="rv-term-en">${esc(t.en || '')}</div>
      <div class="rv-term-t">${esc(t.term || '')}</div>
      ${open ? `<div class="rv-term-def">${esc(t.def || '')}</div>
        ${t.why ? `<div class="rv-term-why">为什么重要：${esc(t.why)}</div>` : ''}`
        : '<div class="small muted" style="margin-top:10px">点「显示解释」看定义与重要性</div>'}
    </div>
    <div class="rv-nav" style="justify-content:center">
      <button class="ghost" id="rv-term-prev" ${i <= 0 ? 'disabled' : ''}>‹ 上一个</button>
      <button class="act" id="rv-term-show">${open ? '收起解释' : '显示解释'}</button>
      <button class="ghost" id="rv-term-next" ${i >= items.length - 1 ? 'disabled' : ''}>下一个 ›</button>
      <span class="rv-prog">${i + 1} / ${items.length}</span>
    </div>
    <div class="row" style="margin-top:14px">
      <button class="act" id="rv-term-finish">完成第三步</button>
      <button class="ghost" id="rv-term-skip">跳过第三步</button>
      <button class="ghost" onclick="closeModal()">收起窗口</button>
    </div>`;
}

function revBindTermWindow(){
  const show = document.getElementById('rv-term-show');
  if (show) show.onclick = () => {
    const items = ((REV.flowData || {}).terms || {}).items || [];
    const i = Math.min(REV.termIdx, items.length - 1);
    const key = i + ':' + ((items[i] || {}).term || '');
    if (!REV.revealed) REV.revealed = {};
    REV.revealed[key] = !REV.revealed[key];
    revOpenTermWindow();
  };
  const prev = document.getElementById('rv-term-prev');
  if (prev && REV.termIdx > 0) prev.onclick = () => { REV.termIdx--; revSave(); revFloatUpdate(); revOpenTermWindow(); };
  const next = document.getElementById('rv-term-next');
  if (next) next.onclick = () => {
    const items = ((REV.flowData || {}).terms || {}).items || [];
    if (REV.termIdx < items.length - 1){ REV.termIdx++; revSave(); revFloatUpdate(); revOpenTermWindow(); }
  };
  const finish = document.getElementById('rv-term-finish');
  if (finish) finish.onclick = () => { revCloseReviewWindows(); renderReviewDone(); };
  const skip = document.getElementById('rv-term-skip');
  if (skip) skip.onclick = () => { revCloseReviewWindows(); renderReviewDone(); };
}

function revOpenTermWindow(){
  modal(revTermWindowHtml());
  revBindTermWindow();
}

function renderReviewFlow(){
  const f = REV.flowData;
  if (!f){ $('#view').innerHTML = '<div class="empty">正在读取复习数据…</div>'; revResumeFlow(); return; }
  const kps = f.kps || [];
  const items = (f.terms || {}).items || [];
  const notes = f.notes || {};
  const nNotes = (notes.groups || []).reduce((a, g) => a + (g.notes || []).length, 0) +
                 (notes.unbound || []).length;
  const stepChips = [1, 2, 3].map(i => `<span class="rv-step${REV.step === i ? ' on' : (REV.step > i ? ' done' : '')}">
     ${i === 1 ? '① 知识点' : i === 2 ? '② 笔记' : '③ 重点名词'}</span>`).join('');
  let body = '';
  if (REV.step === 1){
    const kp = revCurrentKp();
    body = `<div class="rv-kp">
      <h3>${esc(kp.name || '（没有知识点）')}</h3>
      <div class="rv-kp-meta">导图位置 ${esc((kp.node_path || []).join(' › ') || '—')}
        ${(kp.pages || []).length ? ' · 页码 ' + (kp.pages || []).map(p => 'p.' + p).join('、') : ''}
        ${kp.note_count ? ' · 笔记 ' + kp.note_count + ' 条' : ''}
        ${kp.mastered === false || kp.unfamiliar ? ' · <span style="color:var(--warn)">自测未掌握</span>' : ''}</div>
      <div class="rv-kp-hint">已直接打开这条知识点的原文窗口；关掉窗口不影响复习进度，
        用右下悬浮窗「继续」可重新打开。</div>
    </div>`;
  } else if (REV.step === 2){
    body = `<div class="rv-kp"><h3>整份课件的笔记</h3>
      <div class="rv-kp-meta">${(notes.groups || []).filter(g => (g.notes || []).length).length} 个知识点分组 · 共 ${nNotes} 篇笔记</div>
      <div class="rv-kp-hint">笔记窗口已打开；点标题看原文。也可用下面的按钮重新打开窗口。</div></div>`;
  } else {
    const t = revCurrentTerm();
    body = `<div class="rv-kp"><h3>预习重点名词 · ${items.length} 个</h3>
      <div class="rv-kp-meta">${items.length ? `当前 ${Math.min(REV.termIdx + 1, items.length)} / ${items.length}：${esc(t.term || '')}` : '该章没有预习重点名词，可直接跳过这一步'}</div>
      <div class="rv-kp-hint">名词窗口已打开（翻面看定义）；也可用下面的按钮重新打开窗口。</div></div>`;
  }
  const winLabel = REV.step === 1 ? '知识点窗口' : (REV.step === 2 ? '笔记窗口' : '名词窗口');
  $('#view').innerHTML = `
    <div class="card rv-card">
      <div class="rv-flow-head"><h2>📖 ${esc(REV.title || '课件复习')}</h2>
        <button class="ghost" id="rv-flow-home">退出复习</button></div>
      <div class="rv-steps">${stepChips}</div>
      ${body}
      <div class="rv-nav">
        <button class="act" id="rv-reopen">重新打开${winLabel}</button>
        ${REV.step < 3 ? `<button class="ghost" id="rv-step-next">下一步：${
          REV.step === 1 ? '整份课件笔记' : '重点名词'}</button>` : ''}
        <button class="ghost" id="rv-step-skip">跳过${revStepCn(REV.step)}</button>
        ${REV.step === 3 ? '<button class="act" id="rv-flow-done">完成复习</button>' : ''}
      </div>
    </div>`;
  bindReviewFlow();
}

function revFlowGo(step){
  REV.step = Math.min(3, Math.max(1, step));
  if (REV.step === 1) REV.kpIdx = Math.min(REV.kpIdx, Math.max(0, ((REV.flowData || {}).kps || []).length - 1));
  revSave(); revFloatUpdate(); renderReviewFlow();
}

function bindReviewFlow(){
  const home = $('#rv-flow-home');
  if (home) home.onclick = () => { revCloseReviewWindows(); REV.stage = ''; REV.flowData = null; revFloatUpdate(); go('review'); };
  const reop = $('#rv-reopen');
  if (reop) reop.onclick = () => revOpenStepWindow();
  const next = $('#rv-step-next');
  if (next) next.onclick = () => revFlowNext();
  const skip = $('#rv-step-skip');
  if (skip) skip.onclick = () => revSkipStep();
  const done = $('#rv-flow-done');
  if (done) done.onclick = () => { revCloseReviewWindows(); renderReviewDone(); };
}

async function revFlowNext(){
  if (REV.step === 1){
    const n = ((REV.flowData || {}).kps || []).length;
    if (REV.kpIdx < n - 1){
      REV.kpIdx++; revSave(); revFloatUpdate();
      await revOpenStepWindow();
    } else {
      revCloseReviewWindows(); revFlowGo(2); await revOpenStepWindow();
    }
  } else if (REV.step === 2){
    revCloseReviewWindows(); revFlowGo(3); await revOpenStepWindow();
  } else {
    const items = ((REV.flowData || {}).terms || {}).items || [];
    if (REV.termIdx < items.length - 1){
      REV.termIdx++; revSave(); revFloatUpdate(); await revOpenStepWindow();
    } else {
      revCloseReviewWindows(); renderReviewDone();
    }
  }
}

async function revSkipStep(){
  if (REV.step >= 3){ revCloseReviewWindows(); renderReviewDone(); return; }
  const went = REV.step + 1;
  revCloseReviewWindows();
  revFlowGo(went);
  await revOpenStepWindow();
  toast(`已跳过${revStepCn(went - 1)}`);
}

async function revContinue(){
  if (!REV.active) return;
  REV.minimized = false; revSave(); revFloatUpdate();
  if (CURRENT !== 'review') await go('review');
  if (REV.stage === 'selftest'){ if (REV.st) renderReviewSelftest(); return; }
  if (REV.stage === 'flow'){
    if (!REV.flowData){
      await revResumeFlow();
      return;
    }
    await revOpenStepWindow();
  }
}

async function revResumeFlow(){
  if (!REV.docId) return;
  try {
    const d = await api('/api/review/flow?doc_id=' + encodeURIComponent(REV.docId));
    if (!d || d.ok === false) throw new Error((d && d.error) || '读不到复习数据');
    REV.flowData = d; REV.title = (d.doc || {}).title || REV.title;
    if (CURRENT === 'review') renderReviewFlow();
    await revOpenStepWindow();
  } catch(e){ toast('复习数据读取失败：' + e.message); }
}

function renderReviewDone(){
  const f = REV.flowData || {};
  const nKp = (f.kps || []).length;
  const notes = f.notes || {};
  const nNotes = (notes.groups || []).reduce((a, g) => a + (g.notes || []).length, 0) +
                 (notes.unbound || []).length;
  const nTerms = ((f.terms || {}).items || []).length;
  const nM = REV.result ? (parseInt(REV.result.n_mastered, 10) || 0) : null;
  const nS = REV.result ? (parseInt(REV.result.n_sample, 10) || 0) : null;
  const fam = REV.result ? Math.round((parseFloat(REV.result.familiarity) || 0) * 100) : null;
  const unl = (REV.result && REV.result.unfamiliar_ids) || [];
  REV.stage = 'done';
  revSave();
  const sug = [];
  if (unl.length) sug.push(`明天再刷 ${unl.length} 条「未掌握」的知识点（自测里标红的）。`);
  if (nNotes) sug.push('把这份课件的笔记再翻一遍，尤其是自己写过的易错点。');
  if (nTerms) sug.push('重点名词按「能一句话解释」的标准过第二遍。');
  if (!sug.length) sug.push('保持这个节奏：先自测标掌握度，再按知识点/笔记/名词三步过。');
  $('#view').innerHTML = `
    <div class="rv-done">
      <h2>✅ 本次复习完成</h2>
      <div class="rv-done-line">课件：<b>${esc(REV.title || '')}</b></div>
      ${fam != null ? `<div class="rv-done-line">本次自测熟悉度：<b>${fam}%</b>
        （已掌握 ${nM}/${nS}）${unl.length ? ` · 未掌握 ${unl.length} 条` : ''}</div>`
        : '<div class="rv-done-line">本次未做自测，只完成了三步复习。</div>'}
      <div class="rv-done-line">复习条目：知识点 ${nKp} 条 · 笔记 ${nNotes} 篇 · 重点名词 ${nTerms} 个</div>
      <div class="rv-done-sug">建议：${sug.map(s => esc(s)).join(' ')}</div>
      <div class="row" style="margin-top:14px">
        <button class="act" id="rv-done-home">回到复习页</button>
        <button class="ghost" id="rv-done-end">结束本次复习</button>
      </div>
    </div>`;
  REV.active = false; REV.stepOpen = ''; revDrop(); revFloatUpdate();
  const h = $('#rv-done-home');
  if (h) h.onclick = () => { REV.stage = ''; go('review'); };
  const e = $('#rv-done-end');
  if (e) e.onclick = () => { REV.stage = ''; go('review'); };
}


// ---------------- 名词整理入口（task-36）：预习重点名词 → 课件名词解释（0 AI） ----------------
// termsCard 底部加一行状态 +「按预习重点名词重新整理（0 AI）」；数据来自
// /api/terms/preview/options（有预习产物才显示按钮，无预习给人话），
// 点击 → confirmBox（说明零 AI/只留相关词）→ POST /api/terms/build-preview → 刷新 /api/terms。
async function refreshTermsPreview(){
  const box = $('#term-preview');
  if (!box) return;
  const did = TERMS.docId;
  if (!did){ box.innerHTML = ''; return; }
  box.innerHTML = '<span class="small muted">检查预习重点名词…</span>';
  let d = null;
  try { d = await api('/api/terms/preview/options?doc_id=' + encodeURIComponent(did)); }
  catch(e){
    box.innerHTML = `<span class="small muted">名词整理选项读取失败：${esc(e.message)}</span>`;
    return;
  }
  if (!d || d.ok === false){
    box.innerHTML = `<span class="small muted">${esc((d && d.error) || '名词整理后端未就绪')}</span>`;
    return;
  }
  TERMS.preview = d;
  const pv = d.preview || {}, cur = d.current || {}, ch = d.chapter || {};
  const cand = parseInt(pv.n_candidates, 10) || 0;
  const available = !!pv.exists && cand > 0;
  const curSrc = cur.exists ? String(cur.source || '') : '';
  const srcLabel = curSrc === 'preview_key_terms' ? '预习重点名词'
    : (curSrc ? '已有名词解释' : '暂无');
  if (!available){
    box.innerHTML = `<div class="small muted">教材${
      ch.no ? '第' + esc(ch.no) + '章' : ''}还没有预习重点名词。</div>`;
    return;
  }
  box.innerHTML = `<div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
      <span class="small muted">教材第${esc(ch.no || '?')}章有 ${cand} 个候选名词 · ${esc(srcLabel)}</span>
      <button class="ghost" id="term-preview-build">更新名词解释</button>
    </div>`;
  const b = $('#term-preview-build');
  if (b) b.onclick = () => buildTermsPreview(did);
}

async function buildTermsPreview(docId){
  const ok = await confirmBox('更新名词解释？',
    '将从教材预习重点中挑选课件出现过的名词，替换当前列表。旧结果会自动备份；这一步不产生 AI 费用。');
  if (!ok) return;
  const b = $('#term-preview-build');
  if (b){ b.disabled = true; b.textContent = '整理中…'; }
  try {
    const r = await api('/api/terms/build-preview', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({doc_id: docId, force: true})});
    if (!r || r.ok === false) throw new Error((r && r.error) || '整理失败');
    const meta = r.meta || {};
    const nSel = r.n_selected != null ? r.n_selected : (meta.n_selected != null ? meta.n_selected : '?');
    toast(`名词解释已更新，保留 ${nSel} 个。`);
    await loadTerms();
  } catch(e){
    toast('名词整理失败：' + e.message);
    if (b){ b.disabled = false; b.textContent = '更新名词解释'; }
  }
}
// ---------------- 笔记页签：按课件 → 绑定 KP 分组 ----------------
function noteKpNamesFromTree(tree){
  const out = {};
  const walk = (ns) => {
    (ns || []).forEach(n => {
      if (!n) return;
      if (n.lkp_id) out[String(n.lkp_id)] = n.lkp_name || n.name || '';
      if (Array.isArray(n.lkp_ids)) n.lkp_ids.forEach((id, i) => {
        out[String(id)] = (n.lkp_names || [])[i] || n.name || '';
      });
      if (n.children) walk(n.children);
    });
  };
  walk(tree);
  return out;
}

let NOTES_PAGE_SUBJECT = '';
async function viewNotes(){
  if (!SUBJ.loaded) await subjLoad();
  const pageSubject = SUBJ.loaded ? SUBJ.cur : '';
  const [{notes}, docsRes] = await Promise.all([
    api('/api/notes'),
    api('/api/docs').catch(() => ({documents: []})),
  ]);
  if (!pageSubject || SUBJ.cur !== pageSubject) return;
  const docs = (docsRes && docsRes.documents) || [];
  const docTitle = {};
  docs.forEach(d => { docTitle[String(d.id)] = d.title || ('doc#' + d.id); });
  if (!notes || !notes.length){
    $('#view').innerHTML = `<div class="empty">还没有笔记。<br>
      <span class="small">笔记会一直留在这里，下次打开还能看。<br>
      在「知识点」页打开任一知识点，点【记入笔记】就能写第一条。</span><br>
      <button class="act" id="nt-go-kps">去「知识点」页</button></div>`;
    const b = $('#nt-go-kps');
    if (b) b.onclick = () => go('kps');
    return;
  }
  const byDoc = new Map();
  notes.forEach(n => {
    const did = parseInt(n.doc_id || 0, 10) || 0;
    if (!byDoc.has(did)) byDoc.set(did, []);
    byDoc.get(did).push(n);
  });
  // 拉每份课件的导图树，把 kp_id → 知识点名（失败就用 kp_id，绝不阻塞渲染）
  const kpNames = {};
  await Promise.all([...byDoc.keys()].filter(Boolean).map(async did => {
    try {
      const mm = await api('/api/mindmap?doc_id=' + encodeURIComponent(did));
      Object.assign(kpNames, noteKpNamesFromTree(mm.tree || []));
    } catch(e){ /* 名字拿不到不影响按 kp_id 分组 */ }
  }));
  if (SUBJ.cur !== pageSubject) return;
  NOTES_PAGE_SUBJECT = pageSubject;
  const docIds = [...byDoc.keys()].sort((a, b) => (a ? (b ? a - b : -1) : 1));
  const groupsHtml = docIds.map(did => {
    const list = byDoc.get(did).slice();
    list.sort((a, b) => (b.created || b.mtime || 0) - (a.created || a.mtime || 0));
    const nChars = list.reduce((a, n) => a + (n.chars || 0), 0);
    const latest = list[0] ? (list[0].created || list[0].mtime || 0) : 0;
    const byKp = new Map();
    list.forEach(n => {
      const kid = String(n.kp_id || '');
      if (!byKp.has(kid)) byKp.set(kid, []);
      byKp.get(kid).push(n);
    });
    const kpIds = [...byKp.keys()].sort((a, b) => {
      if (!a) return 1; if (!b) return -1; return a.localeCompare(b, 'zh');
    });
    const kpHtml = kpIds.map(kid => {
      const ns = byKp.get(kid);
      const kpName = kid ? (kpNames[kid] || ('知识点 ' + kid)) : '未绑定知识点';
      const rows = ns.map(n => noteRowHtml(n)).join('');
      return `<details class="nt-kp" data-search="${esc(((kid ? kpName : '未绑定') + ' ' + ns.map(x => (x.title || '') + ' ' + (x.source_label || '')).join(' ')).toLowerCase())}">
        <summary>${kid ? '🔗 ' : ''}${esc(kpName)} · ${ns.length} 篇</summary>
        ${rows}</details>`;
    }).join('');
    const title = did ? (docTitle[String(did)] || ('doc#' + did)) : '未绑定课件';
    return `<details class="nt-grp" data-doc="${did}" open data-search="${esc(((did ? title : '未绑定课件') + ' ' + list.map(x => (x.title || '') + ' ' + (x.source_label || '')).join(' ')).toLowerCase())}">
      <summary>📚 ${esc(title)} <span class="nt-g-meta">${list.length} 篇 · ${nChars} 字${
        latest ? ' · 最近 ' + esc(fmtTime(latest)) : ''}</span>
        ${did ? `<button class="ghost" data-review-notes="${did}" style="margin-left:8px;vertical-align:-2px">复习这份课件的笔记</button>` : ''}
      </summary>
      ${kpHtml}</details>`;
  }).join('');
  $('#view').innerHTML = `<div class="card rv-card">
    <div class="nt-head"><h2>笔记 · ${notes.length} 篇</h2>
      <input class="nt-search" id="nt-q" placeholder="搜索笔记标题 / 来源 / 知识点…" autocomplete="off">
      <button class="ghost" onclick="noteSelectVisible()">全选可见</button>
      <button class="ghost" onclick="noteDeleteSelected()">删除选中</button>
      <button class="ghost" id="nt-go-kps">去「知识点」页</button></div>
    <div class="small muted" style="margin-bottom:10px">
      按<b>课件 → 绑定知识点</b>分组；没绑定的笔记归到「未绑定」。点标题看全文，
      点「跳到知识点」打开对应知识点窗口。</div>
    <div id="nt-groups">${groupsHtml}</div>
    <div class="nt-empty" id="nt-nomatch" style="display:none">没有匹配的笔记。</div>
  </div>`;
  bindReviewNotes();
}

function noteRowHtml(n){
  const canJump = !!(n.kp_id && n.doc_id);
  const meta = [noteSourceText(n), n.chars ? n.chars + ' 字' : '',
                fmtTime(n.created || n.mtime)].filter(Boolean).join(' · ');
  return `<div class="nt-note" data-search="${esc(((n.title || '') + ' ' + (noteSourceText(n) || '') + ' ' + (n.kp_id || '')).toLowerCase())}">
    <input type="checkbox" class="nt-delete-cb" aria-label="选择笔记" value="${esc(n.file)}">
    <a href="#" data-note="${esc(n.file)}">${esc(n.title)}</a>
    <span class="nt-meta">${esc(meta)}</span>
    ${canJump ? `<span class="nt-act"><button class="ghost" data-jump="${esc(n.doc_id)}"
      data-kp="${esc(n.kp_id)}">跳到知识点</button></span>` : ''}
  </div>`;
}

function bindReviewNotes(){
  const q = $('#nt-q');
  if (q) q.oninput = () => {
    const v = (q.value || '').trim().toLowerCase();
    let hit = 0;
    document.querySelectorAll('#view .nt-note').forEach(row => {
      const ok = !v || (row.dataset.search || '').indexOf(v) >= 0;
      row.style.display = ok ? '' : 'none';
      if (ok) hit++;
    });
    document.querySelectorAll('#view .nt-kp').forEach(kp => {
      const rows = [...kp.querySelectorAll('.nt-note')];
      const vis = rows.filter(r => r.style.display !== 'none').length;
      kp.style.display = (v && !vis) ? 'none' : '';
      if (v && vis) kp.open = true;
    });
    document.querySelectorAll('#view .nt-grp').forEach(g => {
      const rows = [...g.querySelectorAll('.nt-note')];
      const vis = rows.filter(r => r.style.display !== 'none').length;
      g.style.display = (v && !vis) ? 'none' : '';
      if (v && vis) g.open = true;
    });
    const no = $('#nt-nomatch');
    if (no) no.style.display = (v && !hit) ? '' : 'none';
  };
  $('#view').querySelectorAll('a[data-note]').forEach(a => {
    a.onclick = e => { e.preventDefault(); openNote(a.dataset.note); };
  });
  $('#view').querySelectorAll('button[data-jump]').forEach(b => {
    b.onclick = () => mmJumpToKp(parseInt(b.dataset.jump, 10), b.dataset.kp, '');
  });
  $('#view').querySelectorAll('button[data-review-notes]').forEach(b => {
    b.onclick = (e) => {
      e.preventDefault(); e.stopPropagation();
      revStartFlow(parseInt(b.dataset.reviewNotes, 10), {step: 2});
    };
  });
  const g = $('#nt-go-kps');
  if (g) g.onclick = () => go('kps');
}

// boot：刷新页面后如果上次有进行中的复习，右下角先把悬浮窗挂出来（数据点「继续」再取）
try { reviewBoot(); } catch(e){ /* 启动恢复失败不影响其它页面 */ }
