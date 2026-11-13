// ---------------- 总览：各页签的学习摘要与快捷入口 ----------------
async function viewOverview(){
  if (!SUBJ.loaded) await subjLoad();
  const subject = SUBJ.loaded ? SUBJ.cur : '';
  // 使用各页签的只读接口；单项失败不影响其它区块，且不触发模型或写入。
  const read = async path => {
    try { return await api(path); }
    catch(e){ return {_overviewError: e.message}; }
  };
  const [d, docsRes, queueRes, mistakeRes, noteRes, reviewRes, bookDueRes, previewRes] =
    await Promise.all([
      read('/api/stats'),
      read('/api/docs'),
      read('/api/queue?limit=6'),
      read('/api/mistakes'),
      read('/api/notes'),
      read('/api/review/coursewares'),
      read('/api/textbook-kp/due'),
      read('/api/preview/options'),
    ]);
  if (!subject || SUBJ.cur !== subject) return;
  if (!d || d._overviewError){
    $('#view').innerHTML = `<div class="empty">总览读取失败：${esc(d && d._overviewError || '数据暂不可用')}<br>
      <button class="ghost" onclick="go('overview')">重新读取</button></div>`;
    return;
  }

  const doc = d.documents || {}, k = d.knowledge || {}, m = d.mistakes || {};
  const docs = Array.isArray(docsRes.documents) ? docsRes.documents.slice() : [];
  const queue = Array.isArray(queueRes.queue) ? queueRes.queue : [];
  const mistakes = Array.isArray(mistakeRes.mistakes) ? mistakeRes.mistakes : [];
  const notes = Array.isArray(noteRes.notes) ? noteRes.notes.slice() : [];
  const coursewares = reviewRes.ok === false ? [] : (Array.isArray(reviewRes.items) ? reviewRes.items : []);
  const textbooks = Array.isArray(previewRes.textbooks) ? previewRes.textbooks : [];
  const bookDue = Array.isArray(bookDueRes.chapters) ? bookDueRes.chapters : [];
  const coursewareError = reviewRes._overviewError || reviewRes.error || '';
  const textbookError = previewRes._overviewError || previewRes.error || '';
  const kindTxt = (doc.by_kind||[]).map(x=>`${x.kind} ${x.n}`).join(' · ') || '—';
  const statusBad = (doc.by_status||[]).filter(x=>x.status!=='ok');
  const weak = (d.weakest||[]).map(w => `
    <tr><td><button class="ov-text-link" data-overview-kp="${Number(w.id)}">${esc(w.name)}</button></td>
      <td style="width:190px">
        <div class="bar"><i style="width:${pct(w.mastery)}%"></i></div>
        <span class="small muted">掌握度 ${pct(w.mastery)}%</span></td>
      <td class="small">间隔 ${w.interval_days} 天</td>
      <td class="small">${w.lapses?`<span class="badge bad">遗忘 ${w.lapses}</span>`:'—'}</td>
    </tr>`).join('');

  const summaryCard = (title, value, sub, view) => `
    <div class="card ov-stat">
      <h2><button class="ov-stat-link" data-overview-view="${view}">${title}</button></h2>
      <div class="big">${value}</div><div class="sub">${sub}</div>
    </div>`;
  const panel = (title, view, body, action='查看全部') => `
    <section class="card ov-panel">
      <div class="ov-panel-head"><h2>${title}</h2>
        <button class="ghost" data-overview-view="${view}">${action} →</button></div>
      ${body}
    </section>`;
  const item = (title, meta='', tail='') => `
    <div class="ov-item"><div class="ov-item-main"><div class="ov-item-title">${title}</div>
      ${meta ? `<div class="ov-item-meta">${meta}</div>` : ''}</div>${tail ? `<div class="ov-item-tail">${tail}</div>` : ''}</div>`;

  const queueHtml = queue.length ? queue.map(x => {
    const status = x.status === 'new' ? '<span class="badge">待学习</span>'
      : (Number(x.overdue_days) > 0 ? `<span class="badge warn">逾期 ${Number(x.overdue_days)} 天</span>`
        : '<span class="badge warn">待复习</span>');
    return item(`<button class="ov-text-link" data-overview-kp="${Number(x.id)}">${esc(x.name)}</button>`,
      `掌握度 ${pct(x.mastery)}% · ${esc(x.subject || '未分类')}`, status);
  }).join('') : `<div class="ov-empty">${queueRes._overviewError
    ? `读取失败：${esc(queueRes._overviewError)}` : '今天没有到期知识点。'}</div>`;

  const coursewareHtml = coursewares.length ? coursewares.slice(0,5).map(x => {
    const selftest = x.selftest || {};
    const tested = !!selftest.exists;
    const badge = tested
      ? `<span class="badge ok">熟悉度 ${Math.round((parseFloat(selftest.familiarity)||0)*100)}%</span>`
      : '<span class="badge">待自测</span>';
    const meta = `${Number(x.n_kps)||0} 个知识点${x.terms && x.terms.available ? ` · ${Number(x.terms.n)||0} 个重点名词` : ''}`;
    return item(esc(x.title || `课件 ${x.doc_id}`), meta, badge);
  }).join('') : `<div class="ov-empty">${coursewareError
    ? `读取失败：${esc(coursewareError)}` : '还没有可复习的课件知识点。'}</div>`;

  const previewHtml = textbooks.length ? textbooks.slice(0,5).map(x =>
    item(esc(x.title || `教材 ${x.doc_id}`), `${Number(x.n_chapters)||0} 章`,
      x.has_preview ? '<span class="badge ok">已有预习</span>' : '<span class="badge">待预习</span>')
  ).join('') : `<div class="ov-empty">${textbookError
    ? `读取失败：${esc(textbookError)}` : '还没有入库教材。'}</div>`;

  const bookDueHtml = bookDue.length ? bookDue.slice(0,5).map(x =>
    item(esc(x.title || `教材 ${x.doc_id}`),
      `第 ${Number(x.chapter)} 章 ${esc(x.chapter_name || '')} · ${Number(x.due)||0}/${Number(x.total)||0} 个待回顾`,
      `<button class="ghost ov-small-action" data-overview-book-doc="${Number(x.doc_id)}" data-overview-book-chapter="${Number(x.chapter)}">去回顾</button>`)
  ).join('') : `<div class="ov-empty">${bookDueRes._overviewError || bookDueRes.error
    ? `读取失败：${esc(bookDueRes._overviewError || bookDueRes.error)}` : '教材章节暂无待回顾内容。'}</div>`;

  const openMistakes = mistakes.filter(x => !Number(x.resolved)).slice(0,5);
  const mistakeHtml = openMistakes.length ? openMistakes.map(x => {
    const question = String(x.question || '（题目内容为空）').replace(/\s+/g,' ').slice(0,90);
    const suffix = String(x.question || '').length > 90 ? '…' : '';
    return item(esc(question + suffix),
      `${esc(x.reason || '未分类')}${x.kp_name ? ` · ${esc(x.kp_name)}` : ''}`,
      '<span class="badge bad">未解决</span>');
  }).join('') : `<div class="ov-empty">${mistakeRes._overviewError
    ? `读取失败：${esc(mistakeRes._overviewError)}` : '没有未解决错题。</div>'}`;

  notes.sort((a,b) => (Number(b.created)||Number(b.mtime)||0) - (Number(a.created)||Number(a.mtime)||0));
  const recentNotes = notes.slice(0,5);
  const noteHtml = recentNotes.length ? recentNotes.map(n => item(
    `<button class="ov-text-link" data-overview-note="${esc(n.file)}">${esc(n.title || n.file)}</button>`,
    `${esc(n.source_label || (n.origin === 'deck_chat' ? '课件问答' : '学习笔记'))} · ${esc(fmtTime(n.created || n.mtime))}`,
    `<span class="small muted">${Number(n.chars)||0} 字</span>`
  )).join('') : `<div class="ov-empty">${noteRes._overviewError
    ? `读取失败：${esc(noteRes._overviewError)}` : '还没有笔记。</div>'}`;

  docs.sort((a,b) => (Number(b.indexed_at)||0) - (Number(a.indexed_at)||0));
  const recentDocs = docs.slice(0,5);
  const docsHtml = recentDocs.length ? recentDocs.map(x => item(
    esc(x.title || `资料 ${x.id}`),
    `${esc(({textbook:'教材',courseware:'课件',questionbank:'题库'})[x.role] || x.kind || '资料')} · ${Number(x.n_chunks)||0} 个块${x.indexed_at ? ` · ${esc(fmtTime(x.indexed_at))}` : ''}`,
    x.status === 'ok' ? '<span class="badge ok">已入库</span>' : `<span class="badge warn">${esc(x.status || '待处理')}</span>`
  )).join('') : `<div class="ov-empty">${docsRes._overviewError
    ? `读取失败：${esc(docsRes._overviewError)}` : '还没有资料。</div>'}`;

  const kindBadges = statusBad.length ? `<div class="row" style="margin-top:8px">
    ${statusBad.map(x=>`<span class="badge warn">${esc(x.status)} ${x.n}</span>`).join(' ')}
    </div>` : '';
  const textbookDueTotal = bookDue.reduce((sum,x)=>sum+(Number(x.due)||0),0);
  $('#view').innerHTML = `
    <div class="grid ov-stats">
      ${summaryCard('资料', doc.total || 0, `约 ${((doc.chars||0)/10000).toFixed(1)} 万字`, 'docs')}
      ${summaryCard('知识点', k.total || 0, `未学 ${k.new||0} · 已复习 ${Math.max(0,(k.total||0)-(k.new||0))}`, 'kps')}
      ${summaryCard('今日待复习', k.due || 0, `今日已复习 ${k.reviews_today||0} 次`, 'review')}
      ${summaryCard('未解决错题', m.open || 0, `累计 ${m.total||0} 道`, 'mistakes')}
      ${summaryCard('笔记', d.notes || 0, '最近记录可在下方打开', 'notes')}
    </div>
    <div class="ov-panels">
      ${panel(`今日学习队列 · ${Number(k.due)||0} 项待复习`, 'review', queueHtml)}
      ${panel(coursewareError ? '课件复习与自测' : `课件复习与自测 · ${coursewares.length} 份`, 'review', coursewareHtml)}
      ${panel(textbookError ? '教材预习' : `教材预习 · ${textbooks.filter(x=>x.has_preview).length}/${textbooks.length} 份有记录`, 'preview', previewHtml, '前往预习')}
      ${panel(bookDueRes._overviewError || bookDueRes.error ? '教材待回顾' : `教材待回顾 · ${textbookDueTotal} 个`, 'review', bookDueHtml, '查看复习')}
      ${panel(`未解决错题 · ${Number(m.open)||0} 道`, 'mistakes', mistakeHtml)}
      ${panel(`最近笔记 · ${Number(d.notes)||0} 篇`, 'notes', noteHtml)}
      ${panel('最近入库资料', 'docs', docsHtml)}
      ${panel('资料问答', 'assistant', `<div class="ov-ask">
        <div class="ov-item-title">基于已入库教材和课件提问</div>
        <div class="ov-item-meta">回答会附上对应资料与页码，可从问答页继续追问。</div>
      </div>`, '开始提问')}
    </div>
    <div class="ov-panels ov-secondary">
      <section class="card ov-panel"><div class="ov-panel-head"><h2>资料构成</h2>
        <button class="ghost" data-overview-view="docs">管理资料 →</button></div>
        <div class="row"><span>${esc(kindTxt)}</span></div>${kindBadges}
      </section>
      <section class="card ov-panel"><div class="ov-panel-head"><h2>最薄弱的知识点</h2>
        <button class="ghost" data-overview-view="kps">查看知识点 →</button></div>
        ${weak ? `<table><thead><tr><th>知识点</th><th>掌握度</th>
          <th>复习间隔</th><th>遗忘</th></tr></thead><tbody>${weak}</tbody></table>`
          : `<div class="ov-empty">复习后，这里会显示需要重点回顾的知识点。</div>`}
      </section>
    </div>`;

  $('#view').querySelectorAll('[data-overview-view]').forEach(b =>
    b.onclick = () => go(b.dataset.overviewView));
  $('#view').querySelectorAll('[data-overview-kp]').forEach(b =>
    b.onclick = () => openKp(Number(b.dataset.overviewKp)));
  $('#view').querySelectorAll('[data-overview-note]').forEach(b =>
    b.onclick = () => openNote(b.dataset.overviewNote));
  $('#view').querySelectorAll('[data-overview-book-doc]').forEach(b =>
    b.onclick = () => openTextbookReview(Number(b.dataset.overviewBookDoc), Number(b.dataset.overviewBookChapter)));
}
