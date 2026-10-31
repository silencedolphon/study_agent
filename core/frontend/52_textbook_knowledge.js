// 教材知识点：按章整理、出处回查和本地自评复习。
const BOOKKP = {docs: [], chapters: [], docId: 0, chapter: 0, artifact: null,
                progress: null, timer: null, open: {}, loading: false, awaiting: false};

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
        <div class="book-point-body"><p>${bookText(point.summary)}</p>${keys?`<ul>${keys}</ul>`:''}
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
    BOOKKP.docId = Number(e.target.value); BOOKKP.chapter = 0;
    try { await bookLoadChapters(); } catch(err){ toast('读取章节失败：' + err.message); }
  };
  $('#book-kp-chapter').onchange = async e => {
    BOOKKP.chapter = Number(e.target.value); BOOKKP.open = {};
    try { await bookLoadSummary(); } catch(err){ toast('读取知识点失败：' + err.message); }
  };
  $('#book-kp-build').onclick = bookStart;
  box.querySelectorAll('.book-point').forEach(el => {
    el.ontoggle = () => { BOOKKP.open[el.dataset.point] = el.open; };
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
