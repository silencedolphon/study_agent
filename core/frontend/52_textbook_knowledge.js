// 教材知识点：按章整理、出处回查和本地自评复习。
const BOOKKP = {docs: [], chapters: [], docId: 0, chapter: 0, artifact: null,
                progress: null, timer: null, open: {}, loading: false, awaiting: false};
// task-42：教材知识点的「窗口阅读」状态（复用全页唯一 evPop 小窗，不另造浮层）。
const BOOKPOP = {open: false, docId: 0, chapter: 0, points: [], idx: 0, page: 0,
                 pages: {}, resolving: {}, chunkPages: {}, sideOpen: false};

function bookToday(){
  const d = new Date(), pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
}
function bookText(value){ return esc(value || '').replace(/\n/g, '<br>'); }

// task-40：进度百分比 / 已用时间（后端 progress 新增 percent/elapsed/label；
// 老后端没有 percent 时按 done/total 现算，两种都能画进度条）。
function bookPercent(p){
  p = p || {};
  const x = Number(p.percent);
  if (p.percent !== undefined && p.percent !== null && isFinite(x) && x > 0)
    return Math.min(100, Math.round(x));
  const d = Number(p.done) || 0, t = Number(p.total) || 0;
  return t > 0 ? Math.min(100, Math.round(d * 100 / t)) : 0;
}
function bookFmtElapsed(v){
  if (v === undefined || v === null || v === '') return '—';
  if (typeof v === 'string' && /[^\d.\s]/.test(v)) return v;   // 后端若已给 "3m 12s"
  const s = Number(v);
  if (!isFinite(s) || s < 0) return '—';
  if (s < 60) return (s < 10 ? s.toFixed(1) : String(Math.round(s))) + 's';
  const m = Math.floor(s / 60);
  return m + 'm ' + String(Math.round(s % 60)).padStart(2, '0') + 's';
}
function bookProgressMatch(p){
  return !!p && Number(p.doc_id) === Number(BOOKKP.docId)
    && Number(p.chapter) === Number(BOOKKP.chapter);
}
function bookProgressHtml(p){
  if (!bookProgressMatch(p)) return '';
  const busy = !!p.active;
  const done = Number(p.done) || 0, total = Number(p.total) || 0;
  const percent = busy ? bookPercent(p) : 100;
  const elapsed = bookFmtElapsed(p.elapsed);
  const label = p.label ? `<div class="small muted">${esc(p.label)}</div>` : '';
  if (busy){
    return `<div class="book-kp-progress" data-book-progress="active">
      <div class="pbar active"><i style="width:${percent}%"></i></div>
      <div class="book-kp-progress-text"><b>${percent}%</b> · ${done}/${total || 0} 段 · 已用 ${esc(elapsed)}</div>
      ${label}
      <div class="small muted">完成的部分会保存；可以离开本页，回来继续查看进度。</div>
    </div>`;
  }
  if (p.status === 'done' && total > 0){
    return `<div class="book-kp-progress done" data-book-progress="done">
      <div class="pbar"><i style="width:100%"></i></div>
      <div class="book-kp-progress-text">已完成 · 100% · ${done}/${total} 段 · 已用 ${esc(elapsed)}</div>
      ${label}
    </div>`;
  }
  if (p.status === 'partial' || p.status === 'remote_unknown'){
    const title = p.status === 'partial' ? '部分完成' : '结果未知';
    const hint = p.status === 'partial'
      ? '有效点已保留，未完成部分可续跑；旧完成产物不会被覆盖。'
      : '请求可能已发出，本次不会自动重发；请人工核对后再操作。';
    return `<div class="book-kp-progress" data-book-progress="${esc(p.status)}">
      <div class="book-kp-progress-text"><b>${title}</b>${done ? ` · ${done}/${total || 0} 段` : ''}${p.error ? ' · ' + esc(p.error) : ''}</div>
      ${label}
      <div class="small muted">${hint}</div>
    </div>`;
  }
  if (p.status === 'failed'){
    return `<div class="book-kp-progress failed" data-book-progress="failed">
      <div class="book-kp-error">上次未完成：${esc(p.error || '请重试')}${
        done ? `（已完成 ${done}/${total || 0} 段）` : ''}</div>
    </div>`;
  }
  return '';
}

function openTextbookReview(docId, chapter){
  BOOKKP.docId = docId;
  BOOKKP.chapter = chapter;
  MINDMAP.mode = 'textbook';
  go('kps');
}

async function mountTextbookKnowledge(){
  const box = $('#book-kp-panel');
  if (!box) return;
  box.innerHTML = '<div class="small muted">正在读取教材…</div>';
  try {
    const options = await api('/api/preview/options');
    BOOKKP.docs = options.textbooks || [];
    if (!BOOKKP.docs.length){
      box.innerHTML = `<div class="empty">还没有入库的教材。<br>
        <button class="ghost" onclick="go('docs')">去导入教材</button></div>`;
      return;
    }
    if (!BOOKKP.docs.some(d => Number(d.doc_id) === Number(BOOKKP.docId)))
      BOOKKP.docId = Number(BOOKKP.docs[0].doc_id);
    await bookLoadChapters();
    await bookPoll();
  } catch(e){ box.innerHTML = `<div class="empty">读取教材失败：${esc(e.message)}</div>`; }
}

async function bookLoadChapters(){
  const d = await api('/api/preview/chapters?doc_id=' + encodeURIComponent(BOOKKP.docId));
  BOOKKP.chapters = d.chapters || [];
  if (!BOOKKP.chapters.some(ch => Number(ch.no) === Number(BOOKKP.chapter)))
    BOOKKP.chapter = BOOKKP.chapters.length ? Number(BOOKKP.chapters[0].no) : 0;
  BOOKKP.open = {};
  await bookLoadSummary();
}

async function bookLoadSummary(){
  BOOKKP.artifact = null;
  if (BOOKKP.docId && BOOKKP.chapter){
    BOOKKP.artifact = await api('/api/textbook-kp/doc?doc_id=' + encodeURIComponent(BOOKKP.docId)
      + '&chapter=' + encodeURIComponent(BOOKKP.chapter));
  }
  bookRender();
}

function bookRender(){
  const box = $('#book-kp-panel'); if (!box) return;
  const art = BOOKKP.artifact || {}, points = art.points || [], reviews = art.reviews || {};
  const p = BOOKKP.progress || {};
  const busy = !!p.active;
  const due = points.filter(x => !reviews[x.id] || reviews[x.id].due <= bookToday()).length;
  const action = art.pending ? '继续整理' : (art.exists ? '重新整理' : '整理本章');
  const docOptions = BOOKKP.docs.map(d => `<option value="${Number(d.doc_id)}"${
    Number(d.doc_id)===Number(BOOKKP.docId)?' selected':''}>${esc(d.title)}</option>`).join('');
  const chapterOptions = BOOKKP.chapters.map(ch => `<option value="${Number(ch.no)}"${
    Number(ch.no)===Number(BOOKKP.chapter)?' selected':''}>第 ${Number(ch.no)} 章 ${esc(ch.name || '')}</option>`).join('');
  let content = '<div class="empty">选择章节后，可以整理知识点并进行回忆练习。</div>';
  if (!BOOKKP.chapters.length) content = '<div class="empty">这本教材尚未识别出章节，请检查教材入库结果。</div>';
  else if (points.length){
    content = `<div class="book-kp-count">${points.length} 个知识点 · ${due} 个待回顾</div>` + points.map(point => {
      const review = reviews[point.id], status = !review ? '未练习'
        : (review.due <= bookToday() ? '待回顾' : '下次 ' + review.due);
      const keys = (point.key_points || []).map(x => `<li>${bookText(x)}</li>`).join('');
      const questions = (point.questions || []).map(q => `<div class="book-question">
        <b>${bookText(q.question)}</b><details><summary>查看答案</summary><div>${bookText(q.answer)}</div></details>
      </div>`).join('');
      const sources = (point.source_ids || []).map(id => `<button class="text-btn" data-book-source="${Number(id)}">查看原文</button>`).join('');
      return `<details class="book-point" data-point="${esc(point.id)}" ${BOOKKP.open[point.id]?'open':''}>
        <summary><span>${esc(point.title)}</span><span class="small muted">${esc(status)}</span></summary>
        <div class="book-point-body"><div class="book-point-actions"><button class="act"
          data-book-window="${esc(point.id)}" title="像课件知识点窗口一样，左侧目录 + 教材页图/页码阅读">📖 窗口阅读</button></div>
          <p>${bookText(point.summary)}</p>${keys?`<ul>${keys}</ul>`:''}
          ${point.memory_tip?`<p><b>记忆提示：</b>${bookText(point.memory_tip)}</p>`:''}
          ${point.pitfall?`<p><b>易错点：</b>${bookText(point.pitfall)}</p>`:''}
          <div class="book-practice"><b>先回忆，再看答案</b>${questions}
            <div class="row"><button class="ghost" data-book-review="again" data-point-id="${esc(point.id)}">还没记住</button>
              <button class="ghost" data-book-review="remembered" data-point-id="${esc(point.id)}">记住了</button></div>
          </div>
          <div class="small muted">${point.source_scope==='segment'?'参考本段资料：':'教材出处：'}${sources}</div>
        </div></details>`;
    }).join('');
  }
  // task-40：进度条（percent/elapsed/label）+ 结束后的成功/失败状态。
  const progress = bookProgressHtml(p);
  box.innerHTML = `<div class="small muted" style="margin-bottom:10px">按章整理要点，用回忆题和间隔复习巩固记忆。</div>
    <div class="book-kp-controls"><select id="book-kp-doc" aria-label="选择教材">${docOptions}</select>
      <select id="book-kp-chapter" aria-label="选择章节" ${chapterOptions?'':'disabled'}>${chapterOptions}</select>
      <button class="act" id="book-kp-build" ${busy || !BOOKKP.chapter?'disabled':''}>${action}</button></div>
    ${art.stale?'<div class="book-kp-error">教材内容已变化，当前显示的是旧总结；可重新整理。</div>':''}
    ${progress}${art.pending && !busy?'<div class="small muted">上次已完成的部分会在继续整理时复用。</div>':''}
    ${content}`;
  $('#book-kp-doc').onchange = async e => {
    bookWindowClose();
    BOOKKP.docId = Number(e.target.value); BOOKKP.chapter = 0;
    try { await bookLoadChapters(); } catch(err){ toast('读取章节失败：' + err.message); }
  };
  $('#book-kp-chapter').onchange = async e => {
    bookWindowClose();
    BOOKKP.chapter = Number(e.target.value); BOOKKP.open = {};
    try { await bookLoadSummary(); } catch(err){ toast('读取知识点失败：' + err.message); }
  };
  $('#book-kp-build').onclick = bookStart;
  box.querySelectorAll('.book-point').forEach(el => {
    el.ontoggle = () => { BOOKKP.open[el.dataset.point] = el.open; };
  });
  box.querySelectorAll('[data-book-window]').forEach(b => {
    b.onclick = (ev) => { ev.preventDefault(); bookOpenWindow(b.dataset.bookWindow); };
  });
  box.querySelectorAll('[data-book-source]').forEach(b => {
    b.onclick = () => openChunk(Number(b.dataset.bookSource));
  });
  box.querySelectorAll('[data-book-review]').forEach(b => {
    b.onclick = () => bookReview(b.dataset.pointId, b.dataset.bookReview === 'remembered');
  });
}

async function bookStart(){
  if (BOOKKP.loading || !BOOKKP.chapter) return;
  BOOKKP.loading = true;
  try {
    const docId = BOOKKP.docId, chapter = BOOKKP.chapter;
    const force = !!(BOOKKP.artifact && BOOKKP.artifact.exists && !BOOKKP.artifact.pending);
    const query = '?doc_id=' + encodeURIComponent(docId) + '&chapter=' + encodeURIComponent(chapter)
      + '&force=' + (force ? '1' : '0');
    const est = await api('/api/textbook-kp/estimate' + query);
    // task-40：模型/推理强度/输出上限都来自设置；0 = 无上限要在确认框说清楚。
    const modelTxt = est.model || '当前设置的模型';
    const effortTxt = est.reasoning_effort
      ? ('推理强度 ' + est.reasoning_effort) : '按设置的推理强度';
    const cap = Number(est.estimated_output_cap);
    const capTxt = (!isFinite(cap) || cap <= 0)
      ? '不设输出上限（0 = 无上限）'
      : ('输出上限 ' + cap + ' tokens（含思考）');
    const ok = await confirmBox(actionTitle(force, est.completed_segments),
      `本章分为 ${est.segments} 段，本次预计调用 ${est.calls} 次 ${modelTxt}（${effortTxt}）。\n` +
      `输入估计约 ${est.estimated_input_tokens} tokens，${capTxt}；费用以服务商计费为准。\n` +
      '整理过程可中断，已完成的部分会保留；失败后不会自动重试。确认开始吗？');
    if (!ok) return;
    if (docId !== BOOKKP.docId || chapter !== BOOKKP.chapter) throw new Error('选择的章节已变化，请重新确认');
    await api('/api/textbook-kp/start', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({doc_id:docId, chapter, force,
        signature:est.signature, max_calls:est.calls,
        max_output_tokens:est.estimated_output_cap, confirmed:true})});
    BOOKKP.awaiting = true;
    await bookPoll();
  } catch(e){ toast('无法开始整理：' + e.message); }
  finally { BOOKKP.loading = false; }
}

function actionTitle(force, done){
  return done ? '继续整理教材知识点？' : (force ? '重新整理教材知识点？' : '整理教材知识点？');
}

async function bookPoll(){
  if (BOOKKP.timer){ clearTimeout(BOOKKP.timer); BOOKKP.timer = null; }
  if (!$('#book-kp-panel') || MINDMAP.mode !== 'textbook') return;
  try {
    const wasActive = !!(BOOKKP.progress && BOOKKP.progress.active);
    BOOKKP.progress = await api('/api/textbook-kp/progress');
    if ((wasActive || BOOKKP.awaiting) && !BOOKKP.progress.active){
      BOOKKP.awaiting = false;
      await bookLoadSummary();
    }
    else bookRender();
    if (BOOKKP.progress.active) BOOKKP.timer = setTimeout(bookPoll, 1500);
  } catch(e){ toast('读取整理进度失败：' + e.message); }
}

async function bookReview(pointId, remembered){
  const buttons = [...document.querySelectorAll('[data-book-review]')]
    .filter(b => b.dataset.pointId === pointId);
  buttons.forEach(b => { b.disabled = true; });
  try {
    const docId = BOOKKP.docId, chapter = BOOKKP.chapter;
    const r = await api('/api/textbook-kp/review', {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({doc_id:docId,
        chapter, point_id:pointId, remembered})});
    if (docId !== BOOKKP.docId || chapter !== BOOKKP.chapter) return;
    BOOKKP.artifact.reviews = BOOKKP.artifact.reviews || {};
    BOOKKP.artifact.reviews[pointId] = r.review;
    bookRender();
    toast('已记录，下次回顾：' + r.review.due);
  } catch(e){ toast('保存复习记录失败：' + e.message); }
  finally { buttons.forEach(b => { b.disabled = false; }); }
}

// ================= task-42：教材知识点「窗口阅读」 =================
// 用户要求：教材知识点总结要和课件知识点窗口一样 —— 左侧目录、中间教材原文页图 + 页码。
// 复用全页唯一 evPop 小窗（不是另造浮层）与现有 .mmkp-shell/.mmkp-side/.mmkp-main/
// .ph/.pbody/.pf/.kpslide/.kppages CSS；窗口里就地切换知识点，不重新请求、不关窗。
function bookWindowClose(){
  BOOKPOP.open = false;
  evPopClose();
}

function bookParsePages(text){
  const out = [];
  const s = String(text || '');
  const re = /p\.?\s*(\d{1,4})(?:\s*[-–—~至]\s*(\d{1,4}))?/gi;
  let m;
  while ((m = re.exec(s))){
    const a = parseInt(m[1], 10) || 0;
    const b = m[2] ? (parseInt(m[2], 10) || a) : a;
    if (!a || b < a) continue;
    for (let n = a; n <= b && n - a < 20; n++) if (out.indexOf(n) < 0) out.push(n);
  }
  return out.sort((a, b) => a - b);
}

function bookAddPages(out, val){
  if (val === undefined || val === null) return;
  if (Array.isArray(val)){
    val.forEach(v => {
      const n = parseInt(v, 10);
      if (n > 0 && out.indexOf(n) < 0) out.push(n);
      else bookParsePages(v).forEach(p => { if (out.indexOf(p) < 0) out.push(p); });
    });
    return;
  }
  bookParsePages(val).forEach(p => { if (out.indexOf(p) < 0) out.push(p); });
}

// 同步能拿到的页码：point.pages / page_start~page_end / sources[].pages·page·ref /
// point.source_ref·ref·refs·pages_label（task-41 新字段没有时也能至少解析出 p.N）。
function bookPointPagesSync(point){
  const out = [];
  if (!point) return out;
  bookAddPages(out, point.pages);
  if (point.page_start) {
    const a = parseInt(point.page_start, 10) || 0;
    const b = parseInt(point.page_end, 10) || a;
    if (a > 0) for (let n = a; n <= b && n - a < 20; n++) if (out.indexOf(n) < 0) out.push(n);
  }
  (point.sources || []).forEach(s => {
    if (!s || typeof s !== 'object') return;
    bookAddPages(out, s.pages);
    bookAddPages(out, s.page);
    bookAddPages(out, s.page_start);
    bookAddPages(out, s.ref);
    bookAddPages(out, s.source_ref);
    bookAddPages(out, s.cite);
    bookAddPages(out, s.page_label);
  });
  ['source_ref', 'ref', 'refs', 'pages_label', 'page_label', 'page'].forEach(k => {
    bookAddPages(out, point[k]);
  });
  return out.sort((a, b) => a - b);
}

// 后端老产物只有 source_ids（chunk id）：通过 /api/chunk 取 target.ref（p.N / p.N-M）兜底。
async function bookResolvePointPages(point){
  if (!point || !point.id) return;
  if (BOOKPOP.pages[point.id] !== undefined) return;
  if (BOOKPOP.resolving[point.id]) return;
  BOOKPOP.resolving[point.id] = true;
  const ids = (point.source_ids || []).slice(0, 3);
  const pages = [];
  for (const id of ids){
    let ps = BOOKPOP.chunkPages[id];
    if (!ps){
      try {
        const d = await api('/api/chunk?id=' + encodeURIComponent(id) + '&before=0&after=0');
        const ref = (d && d.target && (d.target.ref || d.target.source_ref)) ||
                    (((d && d.context) || [])[0] || {}).ref || '';
        ps = bookParsePages(ref);
      } catch(e){ ps = []; }
      BOOKPOP.chunkPages[id] = ps;
    }
    ps.forEach(p => { if (pages.indexOf(p) < 0) pages.push(p); });
  }
  pages.sort((a, b) => a - b);
  BOOKPOP.pages[point.id] = pages;
  BOOKPOP.resolving[point.id] = false;
  if (BOOKPOP.open && BOOKPOP.points[BOOKPOP.idx] &&
      String(BOOKPOP.points[BOOKPOP.idx].id) === String(point.id)) bookWindowPaint();
}

function bookEnsurePages(point){
  if (!point) return [];
  if (BOOKPOP.pages[point.id] !== undefined) return BOOKPOP.pages[point.id];
  const sync = bookPointPagesSync(point);
  if (sync.length){ BOOKPOP.pages[point.id] = sync; return sync; }
  BOOKPOP.pages[point.id] = [];
  if ((point.source_ids || []).length) bookResolvePointPages(point);
  return [];
}

function bookPageUrl(page, dpi){
  return '/api/teach-page?doc_id=' + encodeURIComponent(BOOKPOP.docId) +
         '&page=' + encodeURIComponent(page) + '&dpi=' + dpi;
}
function bookPageLabel(pages){
  if (!pages || !pages.length) return '';
  return pages.length > 1
    ? 'p.' + pages[0] + '–p.' + pages[pages.length - 1]
    : 'p.' + pages[0];
}

function bookWindowShellHtml(){
  return `<div class="mmkp-shell">
    <aside class="mmkp-side" id="bookkp-side"><div class="mms-empty">左栏目录加载中…</div></aside>
    <div class="mmkp-main">
      <div class="ph">
        <div class="src"><div class="loc" id="bookkp-loc"></div>
          <div class="hdg" id="bookkp-hdg"></div></div>
        <button class="mmkp-toggle" id="bookkp-treebtn" title="展开 / 收起左侧目录">☰ 目录</button>
        <button class="pclose" onclick="bookWindowClose()" title="关闭（Esc）">✕</button>
      </div>
      <div class="pbody" id="bookkp-body"></div>
      <div class="pf" id="bookkp-foot">
        <button class="ghost" id="bookkp-prev">‹ 上一知识点</button>
        <button class="ghost" id="bookkp-next">下一知识点 ›</button>
        <span class="pmeta" id="bookkp-status"></span>
        <span class="pmeta" id="bookkp-pagestat"></span>
        <span style="flex:1"></span>
        <button class="ghost" id="bookkp-close2">关闭</button>
      </div>
    </div></div>`;
}

function bookPointExcerpts(point){
  const srcs = Array.isArray(point.sources) ? point.sources.filter(s =>
    s && (s.text || s.excerpt || s.quote || s.content)) : [];
  if (srcs.length) return {kind: 'quotes', list: srcs};
  const ids = (point.source_ids || []).slice(0, 6);
  return {kind: 'chunks', list: ids};
}

function bookSlideHtml(point){
  const pages = BOOKPOP.pages[point.id] || [];
  if (!pages.length){
    const resolving = !!BOOKPOP.resolving[point.id];
    const ids = (point.source_ids || []).length;
    return `<div class="kpslide" id="bookkp-slide"><div class="small muted">教材原文</div>
      <div class="bookkp-nopages" id="bookkp-nopages">${resolving
        ? '正在从原文出处解析页码…'
        : (ids ? '这条知识点暂时解析不到教材页码（产物未记录 pages，chunk 出处也没有页码）。'
                 : '这条知识点没有可定位的教材页码（产物未记录 pages/sources）。')}
        <br><span class="small">页码与页图只用于对照教材；不影响上面的总结与复习。</span></div>
      ${bookExcerptsHtml(point)}</div>`;
  }
  const page = pages.indexOf(BOOKPOP.page) >= 0 ? BOOKPOP.page : pages[0];
  BOOKPOP.page = page;
  const url110 = bookPageUrl(page, 110);
  const url150 = bookPageUrl(page, 150);
  return `<div class="kpslide" id="bookkp-slide">
    <div class="small muted">教材原文 · doc#${esc(BOOKPOP.docId || '')} ·
      <span class="bookkp-pagelabel" id="bookkp-pagelabel">当前 p.${page}${
        pages.length > 1 ? '（范围 ' + esc(bookPageLabel(pages)) + '）' : ''}</span></div>
    <div class="kppages" id="bookkp-pages">${pages.map(p =>
      `<span class="kppage${p === page ? ' on' : ''}" data-page="${p}"
        title="看 p.${p} 的教材页图">p.${p}</span>`).join('')}</div>
    <div id="bookkp-figbox">
      <a class="bookkp-openfull" id="bookkp-openfull" href="${url150}" target="_blank"
         rel="noopener" title="点开原尺寸（dpi=150）">
        <img class="kpslideimg" id="bookkp-pageimg" src="${url110}"
             alt="教材 p.${page} 页图（点开原尺寸）"></a>
      <div class="bookkp-pageerr" id="bookkp-pageerr" hidden>这一页的教材图暂时读不到
        （可能教材 PDF 未入库、页号超出范围或渲染失败）。页码与下方原文摘录仍可参考。</div>
    </div>
    ${bookExcerptsHtml(point)}</div>`;
}

function bookExcerptsHtml(point){
  const ex = bookPointExcerpts(point);
  if (!ex.list.length) return '';
  if (ex.kind === 'quotes'){
    const items = ex.list.map((s, i) => {
      const txt = s.text || s.excerpt || s.quote || s.content || '';
      const pg = bookPageLabel(bookParsePages(s.ref || s.source_ref || s.pages || s.page || ''));
      const cid = s.chunk_id || s.source_id || s.id || (s.chunk_ids && s.chunk_ids[0]);
      return `<div class="bookkp-quote"><div class="small muted">摘录 ${i + 1}${pg ? ' · ' + esc(pg) : ''}</div>
        <div>${bookText(txt)}</div>${cid ? `<button class="ghost bookkp-chunk" data-chunk="${esc(cid)}">看上下文（chunk ${esc(cid)}）</button>` : ''}</div>`;
    }).join('');
    return `<details class="bookkp-excerpts"><summary>原文摘录（${ex.list.length} 条）</summary>${items}</details>`;
  }
  return `<details class="bookkp-excerpts"><summary>原文出处（${ex.list.length} 个原文块）</summary>
    <div class="small muted" style="line-height:1.8">老产物只记录了原文块 id：点一个块可看它附近的原文。</div>
    <div class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px">${ex.list.map(id =>
      `<button class="ghost bookkp-chunk" data-chunk="${esc(id)}">chunk ${esc(id)}</button>`).join('')}</div>
  </details>`;
}

function bookWindowBodyHtml(point){
  const art = BOOKKP.artifact || {};
  const pts = (point.key_points || []).map(x => `<li>${bookText(x)}</li>`).join('');
  return `<div class="small muted" style="margin-bottom:8px">
      ${esc(art.chapter_name || ('第 ' + BOOKPOP.chapter + ' 章'))}
      ${art.title ? ' · ' + esc(art.title) : ''}
      ${point.source_scope === 'segment' ? ' · 参考本段资料' : ''}</div>
    <p>${bookText(point.summary)}</p>
    ${pts ? `<ul class="lkppts">${pts}</ul>` : ''}
    ${point.memory_tip ? `<p><b>记忆提示：</b>${bookText(point.memory_tip)}</p>` : ''}
    ${point.pitfall ? `<p><b>易错点：</b>${bookText(point.pitfall)}</p>` : ''}
    <h3 class="bookkp-sec">教材原文与页码</h3>
    ${bookSlideHtml(point)}`;
}

function bookWindowPaint(){
  if (!BOOKPOP.open) return;
  const pop = evPopEl();
  const point = BOOKPOP.points[BOOKPOP.idx];
  if (!point) return;
  const art = BOOKKP.artifact || {};
  bookEnsurePages(point);
  const pages = BOOKPOP.pages[point.id] || [];
  if (pages.length && pages.indexOf(BOOKPOP.page) < 0) BOOKPOP.page = pages[0];
  const n = BOOKPOP.points.length;
  const body = document.getElementById('bookkp-body');
  const oldScroll = body ? body.scrollTop : 0;

  const loc = document.getElementById('bookkp-loc');
  if (loc) loc.innerHTML = `${esc(point.title)} <span class="badge">教材知识点</span>`;
  const hdg = document.getElementById('bookkp-hdg');
  if (hdg) hdg.textContent = `${art.chapter_name || ('第 ' + BOOKPOP.chapter + ' 章')}${
    art.title ? ' · ' + art.title : ''} · doc#${BOOKPOP.docId}`;

  const side = document.getElementById('bookkp-side');
  if (side){
    const oldSide = side.scrollTop;
    side.innerHTML = `<div class="mms-ttl">本章知识点 · ${n} 条</div>
      <ul class="mms-kids mms-root">${BOOKPOP.points.map((p, i) => {
        const ps = BOOKPOP.pages[p.id];
        const lab = ps === undefined ? '' : bookPageLabel(ps);
        return `<li><a href="#" class="mms-node mms-kp bookkp-dir${i === BOOKPOP.idx ? ' on' : ''}"
          data-bookkp-idx="${i}" title="${esc(p.title || '')}">${i + 1}. ${esc(p.title || p.id || '')}${
          lab ? `<span class="mms-pg">${esc(lab)}</span>` : ''}</a></li>`;
      }).join('')}</ul>`;
    side.scrollTop = oldSide;
  }

  if (body){
    body.innerHTML = bookWindowBodyHtml(point);
    body.scrollTop = oldScroll;
  }

  const status = document.getElementById('bookkp-status');
  if (status) status.textContent = `第 ${BOOKPOP.idx + 1} / ${n} 条`;
  const pstat = document.getElementById('bookkp-pagestat');
  if (pstat) pstat.textContent = pages.length ? bookPageLabel(pages) : '暂无页码';
  const prev = document.getElementById('bookkp-prev');
  if (prev){ prev.disabled = BOOKPOP.idx <= 0; prev.onclick = () => bookWindowStep(-1); }
  const next = document.getElementById('bookkp-next');
  if (next){ next.disabled = BOOKPOP.idx >= n - 1; next.onclick = () => bookWindowStep(1); }
  const close2 = document.getElementById('bookkp-close2');
  if (close2) close2.onclick = () => bookWindowClose();
  const treeBtn = document.getElementById('bookkp-treebtn');
  if (treeBtn) treeBtn.onclick = () => {
    BOOKPOP.sideOpen = !BOOKPOP.sideOpen;
    pop.classList.toggle('mm-tree-open', BOOKPOP.sideOpen);
    evPopPlace(null);
  };
  document.querySelectorAll('#bookkp-side .bookkp-dir').forEach(a => {
    a.onclick = (e) => {
      e.preventDefault();
      bookmarkClickDir(parseInt(a.dataset.bookkpIdx, 10));
    };
  });
  bindBookWindowSlide();
  evPopPlace(null);
}

function bookmarkClickDir(idx){
  if (!BOOKPOP.open || !(idx >= 0)) return;
  BOOKPOP.idx = idx; BOOKPOP.page = 0;
  bookWindowPaint();
}

function bookWindowStep(delta){
  if (!BOOKPOP.open) return;
  const i = BOOKPOP.idx + delta;
  if (i < 0 || i >= BOOKPOP.points.length) return;
  BOOKPOP.idx = i; BOOKPOP.page = 0;
  bookWindowPaint();
}

function bindBookWindowSlide(){
  const pop = evPopEl();
  pop.querySelectorAll('#bookkp-pages .kppage').forEach(chip => {
    chip.onclick = () => {
      BOOKPOP.page = parseInt(chip.dataset.page, 10) || 0;
      bookWindowPaint();
    };
  });
  pop.querySelectorAll('.bookkp-chunk').forEach(b => {
    b.onclick = () => openChunk(Number(b.dataset.chunk));
  });
  const img = document.getElementById('bookkp-pageimg');
  const err = document.getElementById('bookkp-pageerr');
  if (img){
    const fail = () => {
      if (err) err.hidden = false;
      img.style.display = 'none';
      evPopPlace(null);
    };
    img.onerror = fail;
    img.onload = () => evPopPlace(null);
    if (img.complete && img.naturalWidth === 0) fail();
  }
}

async function bookOpenWindow(pointId){
  const art = BOOKKP.artifact || {};
  const pts = art.points || [];
  if (!pts.length){ toast('这一章还没有知识点，先整理本章'); return; }
  let idx = pts.findIndex(p => String(p.id) === String(pointId));
  if (idx < 0) idx = 0;
  BOOKPOP.open = true;
  BOOKPOP.docId = Number(art.doc_id || BOOKKP.docId) || 0;
  BOOKPOP.chapter = Number(BOOKKP.chapter) || 0;
  BOOKPOP.points = pts;
  BOOKPOP.idx = idx;
  BOOKPOP.page = 0;
  BOOKPOP.pages = {};
  BOOKPOP.chunkPages = {};
  BOOKPOP.resolving = {};
  const pop = evPopEl();
  pop.dataset.kpPop = '1';
  pop.dataset.kp = '';
  pop.classList.add('mmkp-has-side');
  pop.classList.remove('mm-tree-open', 'mmkp-has-teach', 'mm-teach-open');
  BOOKPOP.sideOpen = false;
  MMSIDE.open = false; MMTEACH.open = false;
  evPopMount(null, bookWindowShellHtml());
  bookWindowPaint();
}
