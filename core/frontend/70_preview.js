// ---------------- 预习（task-24：替换原「知识图谱」页签） ----------------
// 用户要求：教材入库后按章显示；每章可让外接大模型生成预习；预习先细分小节、
// 再按小节给内容；查看方式沿用"知识点小窗"的左树 + 右文。
//
// 后端契约（core/preview.py，另一位智能体并行重写；这里防御式调用）：
//   GET  /api/preview/options            教材列表 + 开关状态
//   GET  /api/preview/chapters?doc_id=   章列表（含每章产物状态）
//   GET  /api/preview/estimate?...       dry-run 估价（0 调用 0 写盘）
//   POST /api/preview/start              起后台任务
//   GET  /api/preview/progress           后台进度（必须不进写锁）
//   GET  /api/preview/doc?...            读某章产物（查看器数据）
// 生成前必须先看估价再确认——不替用户花钱。
const PV = {docs:[], docId:0, title:'', enabled:null, chapters:[], err:'',
            open:{}, sel:{ch:0, sec:null}, arts:{}, prog:null, timer:null,
            mobile:false, busy:{}, loading:false};

let textbookBatchTimer = null;
async function chapterQuestionsManage(mode){
  const docId = mode === 'preview' ? PV.docId : BOOKKP.docId;
  const chapter = mode === 'preview' ? PV.sel.ch : BOOKKP.chapter;
  if (!docId || !chapter) return toast('请先选择教材章节');
  try {
    const body = {mode, doc_id:docId, chapter, expected_subject: mode === 'preview' ? PV.subject : BOOKKP.subject};
    const result = await api('/api/chapter-questions', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    if (!result.items.length) return toast('本章没有可删除的题目');
    modal(`<h2>批量删除本章题目</h2><p class="small muted">教材知识点须至少保留一道回忆题。删除前会备份章节结果。</p>
      <div style="max-height:50vh;overflow:auto">${result.items.map(x => `<label style="display:block;padding:6px"><input type="checkbox" class="chapter-question-ck" value="${esc(x.id)}"> <span class="muted">${esc(x.group)}：</span>${esc(x.question)}</label>`).join('')}</div>
      <div class="row"><button class="ghost" id="cq-all">全选</button><button class="ghost" id="cq-delete">删除所选</button><button class="ghost" onclick="closeModal()">取消</button></div>`);
    $('#cq-all').onclick = () => document.querySelectorAll('.chapter-question-ck').forEach(x => x.checked = true);
    $('#cq-delete').onclick = async () => {
      const ids = Array.from(document.querySelectorAll('.chapter-question-ck:checked')).map(x => x.value);
      if (!ids.length) return toast('请先选择题目');
      if (!await confirmBox('删除选中题目？', `将删除 ${ids.length} 道题，保留章节正文和删除前备份。重新生成章节可能重新产生题目。`)) return;
      try {
        const r = await api('/api/chapter-questions', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...body,delete:true,confirm:true,revision:result.revision,scope:result.scope,ids})});
        toast(`已删除 ${r.deleted} 道题，原结果已备份`);
        if (mode === 'preview') { PV.arts = {}; await viewPreview(); }
        else { BOOKKP.artifact = null; await viewKps(); }
      } catch(e) { toast('删除失败：' + e.message); }
    };
  } catch(e) { toast('无法读取题目：' + e.message); }
}
function textbookBatchChoose(mode){
  const state = mode === 'preview' ? PV : BOOKKP;
  const chapters = state.chapters || [];
  if (!chapters.length) return toast('请先选择有章节的教材');
  modal(`<h2>${mode === 'preview' ? '批量预习' : '批量整理教材知识点'}</h2>
    <p>选择章节。已有完整产物会复用，未完成部分继续整理。</p>
    <div style="max-height:45vh;overflow:auto">${chapters.map(c =>
      `<label style="display:block;padding:4px"><input type="checkbox" class="tb-batch-ch" value="${Number(c.no)}" checked> 第${Number(c.no)}章 ${esc(c.name || '')}</label>`).join('')}</div>
    <p><button class="ghost" onclick="document.querySelectorAll('.tb-batch-ch').forEach(x=>x.checked=true)">全选</button>
    <button class="ghost" onclick="document.querySelectorAll('.tb-batch-ch').forEach(x=>x.checked=false)">全不选</button></p>
    <button class="act" onclick="textbookBatchPlan('${mode}')">估算并继续</button>
    <button class="ghost" onclick="textbookBatchDelete('${mode}')">删除所选产物</button>
    <button class="ghost" onclick="closeModal()">取消</button>`);
}
function textbookBatchSelection(){
  return Array.from(document.querySelectorAll('.tb-batch-ch:checked')).map(x => Number(x.value));
}
function textbookBatchDelete(mode){
  const chapters = textbookBatchSelection(), docId = mode === 'preview' ? PV.docId : BOOKKP.docId;
  if (!chapters.length) return toast('请先选择章节');
  if (typeof batchDeleteTextbookArtifacts !== 'function') return toast('删除功能暂不可用');
  closeModal();
  batchDeleteTextbookArtifacts(mode, docId, chapters);
}
async function textbookBatchPlan(mode){
  const chapters = textbookBatchSelection(), docId = mode === 'preview' ? PV.docId : BOOKKP.docId;
  if (!chapters.length) return toast('请先选择章节');
  try {
    const plan = await api('/api/textbook-batch/plan', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({mode, doc_id:docId, chapters})});
    if (!plan || !plan.ok || !plan.token) throw new Error((plan && plan.error) || '估算未完成');
    let previewCalls = 0, bookCalls = 0, cost = 0, input = 0;
    for (const item of plan.items){
      const e = item.estimate;
      if (mode === 'preview'){
        previewCalls += e.skip_would_happen ? 0 : Number(e.calls || 0);
        cost += e.skip_would_happen ? 0 : Number(e.cost_cny_max || 0);
        bookCalls += Number((e.textbook_kp || {}).calls || 0);
        input += Number((e.textbook_kp || {}).estimated_input_tokens || 0);
      } else { bookCalls += Number(e.calls || 0); input += Number(e.estimated_input_tokens || 0); }
    }
    const msg = `共 ${chapters.length} 章，按顺序处理。预计预习 ${previewCalls} 次，教材知识点 ${bookCalls} 次；教材总结输入约 ${input} tokens。预习估算约 ¥${cost.toFixed(4)}，教材总结费用另计，最终按实际调用及服务商价格结算。已有完整结果会复用；内容或设置变化时该章会停止并提示重新估算。`;
    closeModal();
    if (!await confirmBox('确认批量处理？', msg)) return;
    const started = await api('/api/textbook-batch/start', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:plan.token, confirmed:true})});
    if (!started || !started.started) throw new Error((started && started.error) || '任务未启动');
    toast('批量任务已开始'); textbookBatchWatch();
  } catch(e){ toast('批量任务未启动：' + e.message); }
}
async function textbookBatchWatch(){
  if (textbookBatchTimer) return;
  try {
    const s = await api('/api/textbook-batch/progress');
    const el = document.querySelector('#textbook-batch-progress-' + s.mode);
    if (el && s.total){
      const failures = (s.items || []).filter(x => x.status === 'failed');
      el.innerHTML = `<div class="small" style="padding:10px">${s.active ? '批量处理中' : '批量处理结束'}：${s.done}/${s.total} 章${s.active ? ' · 当前第' + s.chapter + '章' : ''}${failures.length ? ' · 失败 ' + failures.length + ' 章：' + failures.map(x => '第' + x.chapter + '章 ' + esc(x.error)).join('；') : ''}</div>`;
    }
    if (s.active) textbookBatchTimer = setTimeout(() => { textbookBatchTimer = null; textbookBatchWatch(); }, 1500);
  } catch(e){ /* 页面关闭时无需提示 */ }
}

function pvText(s){
  return mdToHtml(String(s == null ? '' : s));
}
function pvList(v, fmt){
  if (v == null || v === '') return '';
  const arr = Array.isArray(v) ? v : [v];
  return arr.map(x => (typeof x === 'string') ? pvText(x) : (fmt ? fmt(x) : pvText(x)))
    .filter(x => x !== '').join('');
}
function pvPageLabel(ch){
  if (!ch) return '';
  if (ch.page_label) return ch.page_label;
  const a = ch.page_start, b = ch.page_end;
  if (a && b && a !== b) return 'p.' + a + '–p.' + b;
  if (a) return 'p.' + a;
  return '';
}
function pvChapKey(ch){ return 'ch' + (ch && ch.no || 0); }

async function viewPreview(){
  PV.subject = SUBJ.cur;
  const box = $('#view');
  if (!box) return;
  if (PV.timer){ clearInterval(PV.timer); PV.timer = null; }
  box.innerHTML = `<div class="pv-top"><div><h2>📖 预习</h2>
      <div class="pdesc">选择章节，查看预习重点与自测题。</div></div></div>
    <div class="empty" id="pv-loading">正在读取教材与章节…</div>`;
  PV.loading = true;
  await pvLoadOptions();
  textbookBatchWatch();
  if (!PV.bookRetry){
    try {
      const b = await api('/api/textbook-kp/progress');
      if (b.active && Number(b.doc_id) === Number(PV.docId))
        PV.bookRetry = {docId:Number(b.doc_id), chapter:Number(b.chapter)};
    } catch(e) { /* 进度不可用不影响阅读 */ }
  }
  if (PV.bookRetry) pvBookWatch();
  PV.loading = false;
  // 窗口尺寸变化时重渲染一次，保证窄屏单栏/宽屏两栏切换正确（用 matchMedia，
  // 它比 resize 事件更可靠：headless 截图工具可能在页面加载后才改视口）
  if (!PV._mq){
    PV._mq = window.matchMedia('(max-width: 900px)');
    PV._mqH = () => { if (document.querySelector('.pv-wrap')) pvRender(); };
    if (PV._mq.addEventListener) PV._mq.addEventListener('change', PV._mqH);
    else if (PV._mq.addListener) PV._mq.addListener(PV._mqH);
  }
  // 若后台已经有预习任务在跑（例如刚刷新页面），把进度条接回来
  try {
    const p = await api('/api/preview/progress');
    if (p && (p.active || p.linked)){
      PV.prog = p;
      if (p.active) pvWatchProgress();
      else pvRender();
    }
  } catch(e){ /* 进度接口不可用不影响页面 */ }
}

async function pvLoadOptions(){
  try {
    const d = await api('/api/preview/options');
    PV.enabled = d.enabled || {enabled:false, hint:'', switch:'PREVIEW_ENABLED'};
    PV.docs = d.textbooks || [];
    if (!PV.docs.length){ pvRenderNoTextbook(); return; }
    if (!PV.docs.some(t => t.doc_id === PV.docId)){
      const withPv = PV.docs.find(t => t.has_preview);
      PV.docId = (withPv || PV.docs[0]).doc_id;
    }
    await pvLoadChapters();
  } catch(e){
    $('#view').innerHTML = `<div class="pv-top"><div><h2>📖 预习</h2></div></div>
      <div class="empty">读不到预习数据：${esc(e.message)}<br>
      <button class="ghost" onclick="viewPreview()">刷新</button></div>`;
  }
}

function pvRenderNoTextbook(){
  $('#view').innerHTML = `<div class="pv-top"><div><h2>📖 预习</h2>
      <div class="pdesc">选择章节，查看预习重点与自测题。</div></div></div>
    <div class="empty">还没有教材入库，所以没有可预习的章节。<br>
      <button class="act" onclick="go('docs')">导入教材</button></div>`;
}

async function pvLoadChapters(){
  try {
    const d = await api('/api/preview/chapters?doc_id=' + encodeURIComponent(PV.docId));
    if (d.ok === false){
      PV.chapters = [];
      PV.err = d.error || '这份教材未见章节结构（可能是扫描版或还没做完章级切块）。';
      pvRender();
      return;
    }
    PV.err = '';
    PV.title = d.title || (PV.docs.find(t => t.doc_id === PV.docId) || {}).title || '';
    PV.chapters = d.chapters || [];
    PV.autoTextbook = !!d.auto_textbook_kp;
    if (d.enabled) PV.enabled = d.enabled;
    // 默认选中：第一章有产物的章 + 它的第一节；没有产物就停在第一章的章级空态
    let ch = PV.chapters[0];
    if (PV.sel.ch && PV.chapters.some(c => c.no === PV.sel.ch)) ch = PV.chapters.find(c => c.no === PV.sel.ch);
    const firstDone = PV.chapters.find(c => (c.preview || {}).exists) || ch;
    if (!PV.sel.ch || !PV.chapters.some(c => c.no === PV.sel.ch)){
      PV.sel = {ch: firstDone ? firstDone.no : 0, sec: null};
    }
    if (firstDone && (firstDone.preview || {}).exists){
      PV.open[firstDone.no] = PV.open[firstDone.no] !== false;
      if (PV.sel.sec == null && PV.sel.ch === firstDone.no && !PV.arts[pvKeyOf(PV.sel.ch)]){
        await pvEnsureArt(PV.sel.ch);
        const art = PV.arts[pvKeyOf(PV.sel.ch)];
        // 先展示章概览，再由学生选择小节。
      }
    }
    pvRender();
  } catch(e){
    PV.chapters = []; PV.err = '读不到章节：' + e.message;
    pvRender();
  }
}

function pvKeyOf(chNo){ return String(PV.docId) + ':' + String(chNo); }

// 章级状态徽标
function pvBadge(ch){
  const p = ch.preview || {};
  const running = PV.prog && PV.prog.active &&
    Number(PV.prog.doc_id) === Number(PV.docId) &&
    String(PV.prog.chapter || '').indexOf(String(ch.no)) >= 0;
  if (running) return {cls:'run', text:'⏳ 生成中'};
  if (p.status === 'failed') return {cls:'bad', text:'✗ 失败'};
  if (p.exists) return {cls:'ok', text:'✓ 已预习' + (p.n_subsections ? ' · ' + p.n_subsections + ' 小节' : '')};
  return {cls:'', text:'未预习'};
}

function pvRender(){
  const box = $('#view');
  if (!box) return;
  const chs = PV.chapters || [];
  const cur = chs.find(c => c.no === PV.sel.ch) || null;
  const art = PV.arts[pvKeyOf(PV.sel.ch)];
  const banner = (PV.enabled && PV.enabled.enabled === false) ? `
    <div class="pv-banner">
      <div><b>🔒 预习功能未开启</b>
        <div class="pv-banner-hint">${em(humanize(PV.enabled.hint || '可在「设置中心 → AI 预习总结」里打开。'))}</div></div>
      <button class="act" onclick="PVTOSET()">去设置开启</button>
    </div>` : '';
  const sel = PV.docs.length > 1
    ? `<select id="pv-doc">${PV.docs.map(t => `<option value="${t.doc_id}"${t.doc_id === PV.docId ? ' selected' : ''}>${esc(t.title)}（${t.n_chapters || 0} 章）</option>`).join('')}</select>`
    : `<span class="pv-docname">📚 ${esc(PV.title || (PV.docs[0] || {}).title || '')}</span>`;
  const narrow = PV._mq ? PV._mq.matches : (window.innerWidth <= 900);
  const wrapCls = 'pv-wrap' + (narrow ? (PV.mobile ? ' pv-mob-hide-side' : ' pv-mob-hide-main') : '');
  box.innerHTML = `
    <div class="pv-top">
      <div><h2>📖 预习</h2>
        <div class="pdesc">选择章节，查看预习重点与自测题。</div></div>
      <div class="pv-tools">${sel}
        <button class="ghost" onclick="textbookBatchChoose('preview')">批量预习</button>
        <button class="ghost" onclick="chapterQuestionsManage('preview')">管理本章题目</button>
        <button class="ghost" onclick="pvReload()" title="重新读取章节与产物状态">刷新</button></div>
    </div>
    ${banner}
    <div id="pv-progress"></div>
    ${PV.bookRetryMessage ? `<div class="small muted">${esc(PV.bookRetryMessage)}</div>` : ''}
    <div id="textbook-batch-progress-preview"></div>
    ${PV.err ? `<div class="empty">${esc(PV.err)}<br>
      <span class="small">请先在「资料」页重新导入教材。</span></div>` : ''}
    ${(!PV.err && !chs.length) ? '<div class="empty">这份教材没有列出任何章。</div>' : ''}
    ${chs.length ? `<div class="${wrapCls}">
      <aside class="pv-side">
        <div class="pv-side-h"><b>章节目录</b><span class="muted">${chs.length} 章</span></div>
        <div class="pv-chaps">${chs.map(c => pvChapHtml(c)).join('')}</div>
      </aside>
      <div class="pv-main">${pvViewerHtml(cur, art)}</div>
    </div>` : ''}`;
  pvBind();
  if (PV.prog) renderProgress(PV.prog, '#pv-progress');
}

function pvChapHtml(ch){
  const open = !!PV.open[ch.no];
  const b = pvBadge(ch);
  const p = ch.preview || {};
  const book = ch.textbook_summary || {};
  const progress = PV.prog || {};
  const matchingRun = Number(progress.doc_id) === Number(PV.docId) &&
    parseInt(String(progress.chapter || '').replace(/[^0-9]/g, ''), 10) === Number(ch.no);
  const bookFailed = matchingRun && progress.linked && !progress.active &&
    !['done', 'skipped'].includes(progress.book_status || '');
  const bookError = bookFailed ? (progress.book_error || progress.error || '教材总结未完成') : '';
  const selCls = (ch.no === PV.sel.ch) ? ' on' : '';
  let body = '';
  if (open){
    if (p.status === 'failed'){
      // 2026-10-27：失败章不再显示成"可查看的预习"，直接给错误与重试入口，
      // 避免用户点「查看预习」只看到一句读不到产物、以为页面卡住。
      body = `<div class="pv-hint" style="color:var(--bad)">上一轮生成失败：${esc(p.error || '模型调用或输出解析失败')}
        <br><span class="small muted">已自动提高 token 上限、缩短等模型槽位的时间；点下面「重新预习」重试（旧产物会先备份）。</span></div>`;
    } else if (p.exists){
      const art = PV.arts[pvKeyOf(ch.no)];
      if (!art) body = '<div class="pv-hint">正在读取小节…</div>';
      else if (art.ok === false) body = `<div class="pv-hint">读不到这一章的预习产物：${esc(art.error || '')}</div>`;
      else {
        body = `<div class="pv-secs">
          <button class="pv-sec${PV.sel.ch === ch.no && PV.sel.sec === null ? ' on' : ''}"
            data-pvact="overview" data-ch="${ch.no}">章概览 · ${art.sections.length} 小节</button>
          ${art.sections.map((s, i) => `<button class="pv-sec${PV.sel.ch === ch.no && PV.sel.sec === i ? ' on' : ''}"
            data-pvact="sec" data-ch="${ch.no}" data-i="${i}">
            <span class="pv-sec-no">${esc(s.no || (i + 1))}</span>
            <span class="pv-sec-name">${esc(s.title || ('第' + (i + 1) + '小节'))}</span>
            <span class="pv-sec-pg">${esc(s.page_label || '')}</span></button>`).join('')}
        </div>`;
      }
    } else {
      body = `<div class="pv-hint">还没预习。点右侧「预习本章」生成（先看费用预估再确认）。</div>`;
    }
  }
  return `<div class="pv-chap${selCls}" data-ch="${ch.no}">
    <div class="pv-chap-head" data-pvact="toggle" data-ch="${ch.no}">
      <span class="pv-caret">${open ? '▾' : '▸'}</span>
      <span class="pv-ch-title">第${ch.no}章 ${esc(ch.name || '')}</span>
      <span class="pv-ch-meta">${esc(pvPageLabel(ch))}${ch.n_sections ? ' · ' + ch.n_sections + ' 小节' : ''}</span>
      <span class="pv-badge ${b.cls}">${b.text}</span>
      <span class="pv-badge">${book.pending ? '教材总结待续' : (book.exists ? '教材总结已整理' : '教材总结未整理')}</span>
      ${bookError ? `<span class="small" style="color:var(--bad)">教材总结未完成：${esc(bookError)}</span>` : ''}
      <span class="pv-ch-act">
        ${p.status === 'failed'
          ? `<button class="act" data-pvact="regen" data-ch="${ch.no}">重新预习（上次失败）</button>`
          : (p.exists
              ? `<button class="ghost" data-pvact="view" data-ch="${ch.no}">查看预习</button>
                 ${book.exists ? `<button class="ghost" data-pvact="book" data-ch="${ch.no}">查看教材总结</button>` : ''}
                 ${(!book.exists || book.pending || bookFailed) ? `<button class="ghost" data-pvact="book-retry" data-ch="${ch.no}" ${PV.busy[ch.no] ? 'disabled' : ''}>补齐教材总结</button>` : ''}
                 <button class="ghost" data-pvact="regen" data-ch="${ch.no}">重新预习</button>`
              : `<button class="act" data-pvact="gen" data-ch="${ch.no}"
                   ${PV.busy[ch.no] || (PV.prog && PV.prog.active) ? 'disabled' : ''}>
                   ${PV.busy[ch.no] ? '估算中…' : '预习本章'}</button>`)}
      </span>
    </div>
    ${open ? `<div class="pv-ch-body">${body}</div>` : ''}
  </div>`;
}

// 产物归一：兼容后端 v2（artifact:{meta,chapter,summary,subsections,terms,sources}）
// 与旧版/其它字段名；前端只看归一后的 {overview, sections, terms, legacy, warnings}。
function pvNormArtifact(raw){
  const env = (raw && (raw.artifact || raw.preview)) ? (raw.artifact || raw.preview) : (raw || {});
  const r = (env && typeof env === 'object') ? env : {};
  const meta = (r.meta && typeof r.meta === 'object') ? r.meta : {};
  const sm = (r.summary && typeof r.summary === 'object') ? r.summary : {};
  const chInfo = (r.chapter && typeof r.chapter === 'object') ? r.chapter : {};
  const str = v => (v == null) ? '' : String(v);
  const list = v => {
    if (v == null || v === '') return [];
    return (Array.isArray(v) ? v : [v]).map(x => {
      if (typeof x === 'string') return x;
      if (x && typeof x === 'object') return str(x.text || x.name || x.title || x.point || x.desc || '');
      return str(x);
    }).filter(Boolean);
  };
  const coreOf = v => (Array.isArray(v) ? v : []).map(x => {
    if (typeof x === 'string') return {term: '', desc: x};
    return {term: str(x.name || x.term || ''),
            desc: str(x.explain || x.desc || x.text || x.detail || '')};
  }).filter(x => x.term || x.desc);
  const scOf = v => (Array.isArray(v) ? v : []).map(x => {
    if (typeof x === 'string') return {q: x, a: ''};
    return {q: str(x.q || x.question || x.text || ''),
            a: str(x.a || x.answer || x.explain || '')};
  }).filter(x => x.q || x.a);
  const secs = (Array.isArray(r.subsections) ? r.subsections
    : (Array.isArray(r.sections) ? r.sections
    : (Array.isArray(sm.sections) ? sm.sections : []))).map((s, i) => {
    const pages = Array.isArray(s.pages) ? s.pages : [];
    const pgLabel = str(s.page_label) || (pages.length
      ? (pages.length > 1 ? 'p.' + pages[0] + '–p.' + pages[pages.length - 1]
                          : 'p.' + pages[0]) : '');
    const ids = Array.isArray(s.chunk_ids) ? s.chunk_ids
      : (Array.isArray(s.material_block_ids) ? s.material_block_ids : []);
    const sources = (Array.isArray(s.sources) && s.sources.length) ? s.sources
      : ids.map(id => ({page: pages[0] || '', chunk_ids: [id]}));
    return {
      no: s.no || (i + 1),
      title: str(s.title || s.name || ('第' + (i + 1) + '小节')),
      page_label: pgLabel,
      why: str(s.why || s.why_text || s.position || ''),
      core: coreOf(s.core || s.concepts || s.key_terms || []),
      pitfalls: list(s.pitfalls || s.common_mistakes || []),
      self_check: scOf(s.self_check || s.questions || []),
      sources,
    };
  });
  const warnings = Array.isArray(raw && raw.warnings) ? raw.warnings
    : (Array.isArray(meta.warnings) ? meta.warnings : []);
  return {
    ok: true, sections: secs,
    legacy: !!(raw && raw.legacy),
    warnings,
    meta, chapter: chInfo,
    overview: {
      summary: str(sm.overview || sm.summary
        || (typeof r.overview === 'string' ? r.overview : '')),
      study_order: list(sm.study_order || sm.order),
      difficulties: list(sm.difficulties),
      prerequisites: list(sm.prerequisites),
      exam_points: list(sm.exam_focus || sm.exam_points),
    },
  };
}

async function pvEnsureArt(chNo){
  const key = pvKeyOf(chNo);
  if (PV.arts[key]) return PV.arts[key];
  try {
    const d = await api('/api/preview/doc?doc_id=' + encodeURIComponent(PV.docId) +
                        '&chapter=' + encodeURIComponent(chNo));
    PV.arts[key] = (d && d.ok === false) ? {ok:false, error:d.error || '读不到产物'} : pvNormArtifact(d, chNo);
  } catch(e){
    PV.arts[key] = {ok:false, error:e.message};
  }
  return PV.arts[key];
}

function pvViewerHtml(ch, art){
  if (!ch) return '<div class="empty">从左侧选一章开始。</div>';
  const p = ch.preview || {};
  const secIdx = (PV.sel.ch === ch.no) ? PV.sel.sec : null;
  const crumb = `<div class="pv-crumb">
    <button class="ghost pv-back" data-pvact="back">← 返回章节</button>
    <span class="muted">${esc(PV.title || '')}</span> ›
    <b>第${ch.no}章 ${esc(ch.name || '')}</b>
    ${secIdx != null && art && art.sections[secIdx] ? ` › ${esc(art.sections[secIdx].title)}` : ''}
    <span class="pv-crumb-pg">${esc(pvPageLabel(ch))}</span></div>`;
  if (!p.exists){
    return crumb + `<div class="empty pv-empty">这一章还没预习。<br>
      <span class="small">点左侧「预习本章」：先看费用预估，确认后由外接大模型生成；生成完这里按小节显示。</span></div>`;
  }
  if (!art) return crumb + '<div class="empty">正在读取这一章的预习产物…</div>';
  if (art.ok === false) return crumb + `<div class="empty">读不到这一章产物：${esc(art.error || '')}</div>`;
  const ov = art.overview || {};
  const warnHtml = (art.legacy || (art.warnings || []).length)
    ? `<div class="pv-banner"><div><b>⚠ ${art.legacy ? '旧版产物（只读显示）' : '产物提示'}</b>
        <div class="pv-banner-hint">${(art.warnings || []).slice(0, 3).map(w => esc(w)).join('<br>')
          || '这一章是旧版产物；重新预习后会升级为 v2 按小节产物。'}</div></div></div>`
    : '';
  const ovHtml = `
    <div class="pv-card">
      <h3>第${ch.no}章 章概览</h3>
      ${ov.summary ? `<div class="pv-text">${pvText(ov.summary)}</div>` : '<div class="muted small">（这一章没有单独的章概览，直接看小节即可。）</div>'}
      ${ov.study_order.length ? `<div class="pv-sub"><b>学习顺序</b><ol>${ov.study_order.map(x => `<li>${pvText(x)}</li>`).join('')}</ol></div>` : ''}
      <details class="pv-sub"><summary>查看难点、考点与前置知识</summary><div class="pv-cols">
        ${ov.difficulties.length ? `<div><b>难点</b><ul>${ov.difficulties.map(x => `<li>${pvText(x)}</li>`).join('')}</ul></div>` : ''}
        ${ov.prerequisites.length ? `<div><b>前置知识</b><ul>${ov.prerequisites.map(x => `<li>${pvText(x)}</li>`).join('')}</ul></div>` : ''}
        ${ov.exam_points.length ? `<div><b>考点</b><ul>${ov.exam_points.map(x => `<li>${pvText(x)}</li>`).join('')}</ul></div>` : ''}
      </div></details>
      <div class="row" style="margin-top:10px">
        <button class="act" data-pvact="sec" data-ch="${ch.no}" data-i="0">从第一节开始</button>
      </div>
    </div>`;
  if (secIdx == null) return crumb + warnHtml + ovHtml;
  const s = art.sections[secIdx];
  if (!s) return crumb + ovHtml;
  const coreHtml = s.core.length ? `
    <div class="pv-card"><h3>核心概念</h3>
      <div class="pv-core">${s.core.map(x => `<div class="pv-core-row">
        ${x.term ? `<b>${mdInline(x.term)}</b>` : ''}<div>${pvText(x.desc)}</div></div>`).join('')}</div>
    </div>` : '';
  const pitHtml = s.pitfalls.length ? `
    <details class="pv-card"><summary>容易混淆的地方</summary>
      <ul class="pv-pit">${s.pitfalls.map(x => `<li>${pvText(x)}</li>`).join('')}</ul>
    </details>` : '';
  const scHtml = s.self_check.length ? `
    <details class="pv-card"><summary>试着回忆</summary>
      ${s.self_check.map(x => `<details class="pv-qa"><summary>${mdInline(x.q)}</summary>
        <div class="pv-ans">${pvText(x.a || '（本题没有给答案）')}</div></details>`).join('')}
    </details>` : '';
  const srcs = [];
  (s.sources || []).forEach(x => {
    const page = (typeof x === 'object') ? (x.page || x.page_no || '') : x;
    const ids = (typeof x === 'object') ? (x.chunk_ids || x.blocks || []) : [];
    if (ids && ids.length){
      ids.forEach(id => srcs.push(`<a class="pv-chip" href="#" onclick="openChunk(${Number(id)});return false">p.${esc(page || '?')} · 块 ${Number(id)}</a>`));
    } else if (page){
      srcs.push(`<span class="pv-chip">p.${esc(page)}</span>`);
    }
  });
  return crumb + warnHtml + `
    <div class="pv-card pv-sec-head">
      <h3>${esc(s.title)}</h3>
      <div class="pv-sec-meta">${esc(s.page_label || '')}
        <span class="muted">· 小节 ${secIdx + 1} / ${art.sections.length}</span></div>
      ${s.why ? `<div class="pv-why"><b>这一小节要解决什么</b><div>${pvText(s.why)}</div></div>` : ''}
    </div>` + coreHtml + pitHtml + scHtml +
    (srcs.length ? `<details class="pv-card"><summary>查看出处</summary><div class="pv-srcs">${srcs.join('')}</div></details>` : '');
}

function pvBind(){
  const root = $('#view');
  if (!root) return;
  const sel = $('#pv-doc');
  if (sel) sel.onchange = async () => {
    PV.docId = parseInt(sel.value, 10) || 0;
    PV.chapters = []; PV.sel = {ch:0, sec:null}; PV.arts = {}; PV.open = {};
    await pvLoadChapters();
  };
  root.onclick = async (e) => {
    const el = e.target.closest('[data-pvact]');
    if (!el) return;
    const act = el.getAttribute('data-pvact');
    const chNo = parseInt(el.getAttribute('data-ch') || '0', 10);
    const ch = PV.chapters.find(c => c.no === chNo);
    if (act === 'toggle'){
      if (!ch) return;
      PV.open[chNo] = !PV.open[chNo];
      if (ch.no !== PV.sel.ch){ PV.sel = {ch:ch.no, sec:null}; }
      if (PV.open[chNo] && (ch.preview || {}).exists && !PV.arts[pvKeyOf(chNo)]){
        await pvEnsureArt(chNo);
        const art = PV.arts[pvKeyOf(chNo)];
      }
      pvRender();
    } else if (act === 'view'){
      if (!ch) return;
      PV.open[chNo] = true;
      if (!PV.arts[pvKeyOf(chNo)]) await pvEnsureArt(chNo);
      const art = PV.arts[pvKeyOf(chNo)];
      PV.sel = {ch:chNo, sec:null};
      PV.mobile = true;
      pvRender();
    } else if (act === 'overview'){
      PV.sel = {ch:chNo, sec:null};
      PV.open[chNo] = true;
      if (!PV.arts[pvKeyOf(chNo)]) await pvEnsureArt(chNo);
      PV.mobile = true;
      pvRender();
    } else if (act === 'sec'){
      const i = parseInt(el.getAttribute('data-i') || '0', 10);
      PV.sel = {ch:chNo, sec:i};
      PV.open[chNo] = true;
      PV.mobile = true;
      if (!PV.arts[pvKeyOf(chNo)]) await pvEnsureArt(chNo);
      pvRender();
    } else if (act === 'back'){
      PV.mobile = false;
      pvRender();
    } else if (act === 'gen'){
      await pvStart(chNo, false);
    } else if (act === 'book'){
      openTextbookReview(PV.docId, chNo);
    } else if (act === 'book-retry'){
      await pvBookRetry(chNo);
    } else if (act === 'regen'){
      await pvStart(chNo, true);
    }
  };
}

// 只补教材总结：绝不经过预习管线，旧预习提示词变化也不会触发重算。
async function pvBookRetry(chNo){
  if (PV.busy[chNo]) return;
  const docId = PV.docId, subject = PV.subject;
  PV.busy[chNo] = true;
  pvRender();
  try {
    const query = '?doc_id=' + encodeURIComponent(docId) + '&chapter=' + encodeURIComponent(chNo) + '&force=0';
    const est = await api('/api/textbook-kp/estimate' + query);
    if (!est || est.ok === false || !est.signature) throw new Error((est && est.error) || '估算未完成');
    const cap = Number(est.estimated_output_cap || 0);
    const ok = await confirmBox('补齐教材知识点？',
      `只整理本章教材知识点，不重新预习。预计 ${Number(est.calls || 0)} 次模型调用，输入约 ${Number(est.estimated_input_tokens || 0)} tokens；` +
      (cap > 0 ? `输出上限 ${cap} tokens。` : '输出不限额。') + '费用按实际调用和服务商价格结算。');
    if (!ok) return;
    if (docId !== PV.docId || subject !== PV.subject || subject !== SUBJ.cur) throw new Error('教材或学科已变化，请重新选择');
    const r = await api('/api/textbook-kp/start', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({doc_id:docId, chapter:chNo, force:false, signature:est.signature,
        max_calls:Number(est.calls || 0), max_output_tokens:cap, confirmed:true, expected_subject:subject})});
    if (!r || !r.started) throw new Error((r && r.error) || '教材总结未启动');
    PV.prog = null;
    PV.bookRetry = {docId, chapter:chNo};
    toast('已开始补齐教材知识点');
    pvBookWatch();
  } catch(e){ toast('无法补齐教材总结：' + e.message); }
  finally { PV.busy[chNo] = false; pvRender(); }
}

async function pvBookWatch(){
  if (!PV.bookRetry || PV.bookRetryTimer) return;
  try {
    const p = await api('/api/textbook-kp/progress');
    const run = PV.bookRetry;
    if (Number(p.doc_id) !== Number(run.docId) || Number(p.chapter) !== Number(run.chapter)) {
      PV.bookRetry = null; toast('教材总结进度已改变，请查看教材知识点页'); return;
    }
    const label = p.active ? `教材总结进行中 ${p.done || 0}/${p.total || 0} 段` :
      (p.status === 'done' ? '教材总结已完成' : '教材总结未完成：' + (p.error || p.status || '未知原因'));
    PV.bookRetryMessage = `第${run.chapter}章 · ${label}`;
    pvRender();
    if (p.active && document.querySelector('.pv-wrap')) {
      PV.bookRetryTimer = setTimeout(() => { PV.bookRetryTimer = null; pvBookWatch(); }, 1500);
    } else if (!p.active) {
      PV.bookRetry = null;
      toast(label);
      if (PV.docId === run.docId && document.querySelector('.pv-wrap')) await pvLoadChapters();
    }
  } catch(e){ PV.bookRetry = null; toast('读取教材总结进度失败：' + e.message); }
}

// 生成：先 estimate（dry-run，0 调用 0 写盘）→ 确认框（先看钱）→ 启动 → 轮询进度
async function pvStart(chNo, force){
  if (PV.busy[chNo]) return;
  PV.busy[chNo] = true;
  pvRender();
  try {
    const est = await api('/api/preview/estimate?doc_id=' + encodeURIComponent(PV.docId) +
                          '&chapter=' + encodeURIComponent(chNo));
    if (est.ok === false){
      toast('估算失败：' + (est.error || '未知原因'));
      return;
    }
    const reusePreview = !force && !!est.skip_would_happen;
    const calls = reusePreview ? 0 : Number(est.calls ?? est.est_calls ?? 1);
    const cost = reusePreview ? 0 : Number(est.cost_cny_max != null ? est.cost_cny_max : (est.est_cost_cny || 0));
    const pages = Number(est.n_pages || est.pages || 0);
    const costTxt = reusePreview ? '¥0（复用已有预习）' : (cost > 0 ? ('约 ¥' + cost.toFixed(4)) : '无法估算（按实际计）');
    const book = est.textbook_kp || {};
    const lines = [
      '预计 ' + calls + ' 次调用｜成本上界 ' + costTxt + '｜章 ' + (pages ? (pages + ' 页') : '若干页'),
      '模型：' + (est.model || '默认配置') + (est.page_label ? '｜' + est.page_label : ''),
      est.auto_textbook_kp ? ('还将并行整理教材知识点：预计 ' + Number(book.calls || 0) +
        ' 次调用，输入约 ' + Number(book.estimated_input_tokens || 0) +
        ' tokens，费用另计；最终按实际输入、输出和模型价格结算（现有完整总结会复用）。') : '',
      force ? '⚠ 这一章已有产物：重新预习会覆盖（旧产物会先备份）。' : '',
      est.skip_would_happen ? '产物与材料都没变，后端可能直接跳过（0 调用、0 花费）。' : '',
      '确认后才开始调用外接大模型。'
    ].filter(Boolean);
    const ok = await confirmBox(force ? '重新预习这一章？' : '现在预习这一章？', lines.join('\n'));
    if (!ok) return;
    const r = await api('/api/preview/start', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({doc_id: PV.docId, chapter: chNo, force: !!force,
        auto_textbook_kp: !!est.auto_textbook_kp, book_signature: book.signature || '',
        book_max_calls: Number(book.calls || 0),
        book_max_output_tokens: Number(book.estimated_output_cap || 0)})});
    if (!r.started){
      toast('没能开始：' + (r.error || '未知原因'));
      if (r.hint) toast(r.hint);
      return;
    }
    toast(r.companion_error ? '预习已开始；教材总结未启动：' + r.companion_error
      : (est.auto_textbook_kp ? '已开始预习和教材知识点整理…' : '已开始预习…'));
    pvWatchProgress();
  } catch(e){
    toast('启动失败：' + e.message);
  } finally {
    PV.busy[chNo] = false;
    pvRender();
  }
}

function pvRetryFromProgress(){
  const p = (typeof PV !== 'undefined' && PV.prog) ? PV.prog : {};
  const m = String(p.chapter || '').match(/(\d+)/);
  if (!m || !PV.docId){ toast('没有可重试的章节'); return; }
  const ch = parseInt(m[1], 10);
  if (ch) pvStart(ch, true);
}

function pvWatchProgress(){
  if (PV.timer) return;
  const tick = async () => {
    if (!$('#view') || !document.querySelector('.pv-wrap')){
      clearInterval(PV.timer); PV.timer = null; return;   // 离开页面就停
    }
    try {
      const p = await api('/api/preview/progress');
      PV.prog = p;
      renderProgress(p, '#pv-progress');
      if (!p || !p.active){
        clearInterval(PV.timer); PV.timer = null;
        const done = p && p.status === 'done';
        if (done) toast(p.linked ? '预习和教材知识点整理均已完成' : '预习完成：' + (p.chapter || '') + '已生成');
        else if (p && p.status === 'failed'){
          const prefix = p.linked && ['done','skipped'].includes(p.preview_status)
            ? '预习已完成，教材知识点整理未完成：' : '预习或教材知识点整理未完成：';
          toast(prefix + (p.error || '未知原因'));
        }
        // 刷新章节状态，并自动选中刚生成的章
        await pvLoadChapters();
        const lastCh = parseInt(String(p && p.chapter || '').replace(/[^0-9]/g, ''), 10);
        if (done && lastCh && PV.chapters.some(c => c.no === lastCh)){
          PV.sel = {ch: lastCh, sec: null};
          PV.open[lastCh] = true;
          delete PV.arts[pvKeyOf(lastCh)];
          await pvEnsureArt(lastCh);
          const art = PV.arts[pvKeyOf(lastCh)];
          // 保持章概览，避免一打开就进入密集内容。
          pvRender();
        }
      } else {
        pvRender();
      }
    } catch(e){
      clearInterval(PV.timer); PV.timer = null;
    }
  };
  tick();
  PV.timer = setInterval(tick, 800);
}

async function pvReload(){
  PV.arts = {};
  await pvLoadOptions();
  toast('已刷新章节与产物状态');
}
function PVTOSET(){
  SET_TAB = 'preview';
  openSettings();
}


