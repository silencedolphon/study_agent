const Q_BANK_VIEW = {subject:'', chapter:null, offset:0};
const Q_BANK_STATUS = {queued:'排队', running:'摘取中', done:'完成', partial:'部分完成', failed:'失败', waiting:'等待续跑'};
const Q_BANK_STAGES = {questions:'摘取题目', answers:'摘取答案', matching:'匹配并保存', saved:'已保存', paused:'本章待续跑'};

function questionBankApi(subject, path='', values={}){
  return api('/api/question-bank' + path + '?' + new URLSearchParams({subject, ...values}));
}

function questionBankPost(subject, path, values){
  return api('/api/question-bank/' + path, {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({subject, ...values})});
}

async function viewQuestionBank(subject, tabs){
  if (Q_BANK_VIEW.subject !== subject) Object.assign(Q_BANK_VIEW, {subject, chapter:null, offset:0});
  $('#view').innerHTML = tabs + `<div class="card" id="question-bank">
    <h2>题库</h2><p class="small muted">从已入库的资料摘取原题。批次按教材、考试资料、课件的顺序运行。</p>
    <div id="qb-sources"></div><div id="qb-progress" role="status"></div>
    <hr><label>章节 <select id="qb-chapter"></select></label><div id="qb-questions"></div></div>`;
  const host = $('#question-bank');
  const sourceHost = $('#qb-sources', host), chapterSelect = $('#qb-chapter', host);
  const progressHost = $('#qb-progress', host), questionsHost = $('#qb-questions', host);
  const current = () => host.isConnected && SUBJ.cur === subject && CURRENT === 'docs' && DOCS_TAB === 'bank';
  const key = value => value || 'unassigned';

  function chapterOptions(chapters){
    return chapters.map(ch => `<option value="${esc(ch.key)}">${esc(ch.label)} · ${esc(ch.source)}</option>`).join('');
  }

  async function displayQuestions(reset=false){
    if (reset) Q_BANK_VIEW.offset = 0;
    const requested = Q_BANK_VIEW.chapter, offset = Q_BANK_VIEW.offset;
    if (requested === null){
      questionsHost.textContent = '请选择章节，查看已经摘取的题目。';
      return;
    }
    const data = await questionBankApi(subject, '', {chapter:key(requested), offset});
    if (!current() || requested !== Q_BANK_VIEW.chapter || offset !== Q_BANK_VIEW.offset) return;
    const rows = data.items.map(item => `<details class="qbank-item">
      <summary>${mdInline(item.stem.split('\n')[0].slice(0, 110))}${item.incomplete ? ' <span class="badge warn">原题待核对</span>' : ''}</summary>
      <div>${mdToHtml(item.stem)}</div>
      ${item.options.length ? '<ul>' + item.options.map(o => '<li>' + mdInline(o) + '</li>').join('') + '</ul>' : ''}
      ${item.answer ? '<details><summary>查看原文答案</summary>' + mdToHtml(item.answer) +
        (item.answer_refs || []).map(ref => `<button class="text-btn" data-qb-chunk="${Number(ref.chunk_id)}">答案出处 ${esc(ref.ref || '')}</button>`).join(' ') + '</details>' : ''}
      <p class="small muted"><button class="text-btn" data-qb-source="${Number(item.doc_id)}">来源 · ${esc(item.title)}</button> ${item.refs.map(ref => `<button class="text-btn" data-qb-chunk="${Number(ref.chunk_id)}">${esc(ref.ref || '查看出处')}</button>`).join(' ')}</p>
      <div class="row"><label>移到章节 <select aria-label="移到章节"><option value="">待归章</option>${chapterOptions(data.chapters)}</select></label>
      <button class="ghost" data-qb-assign="${esc(item.id)}">保存章节</button><button class="ghost" data-qb-share="${esc(item.share_item_id)}">分享此题</button></div></details>`).join('');
    const count = data.groups.find(g => g.key === requested)?.count || 0;
    questionsHost.innerHTML = rows || '<p class="small muted">本章暂时没有题目。</p>';
    if (count > 40) questionsHost.insertAdjacentHTML('beforeend', `<div class="row">
      <button class="ghost" id="qb-prev" ${offset ? '' : 'disabled'}>上一页</button>
      <span class="small muted">${Math.floor(offset/40)+1} / ${Math.ceil(count/40)} 页</span>
      <button class="ghost" id="qb-next" ${offset+40 < count ? '' : 'disabled'}>下一页</button></div>`);
    for (const [id, delta] of [['qb-prev',-40],['qb-next',40]]){
      const button = $('#' + id, questionsHost);
      if (button) button.onclick = () => { Q_BANK_VIEW.offset += delta; displayQuestions().catch(e => toast(e.message)); };
    }
    questionsHost.querySelectorAll('[data-qb-chunk]').forEach(button => {
      button.onclick = () => openChunk(Number(button.dataset.qbChunk)).catch(e => toast(e.message));
    });
    questionsHost.querySelectorAll('[data-qb-source]').forEach(button => {
      button.onclick = () => openDoc(Number(button.dataset.qbSource));
    });
    questionsHost.querySelectorAll('[data-qb-share]').forEach((button, i) => {
      button.onclick = () => openShareExport(data.items[i].doc_id, data.items[i].title, button.dataset.qbShare);
    });
    questionsHost.querySelectorAll('[data-qb-assign]').forEach((button, i) => {
      const select = $('select', button.closest('.qbank-item'));
      select.value = data.items[i].chapter;
      button.onclick = async () => {
        button.disabled = true;
        try {
          await questionBankPost(subject, 'assign', {id:button.dataset.qbAssign, chapter:select.value});
          await load(false, true);
        } catch(e){ toast(e.message); }
        finally { button.disabled = false; }
      };
    });
  }

  async function load(init=false, reset=false){
    const data = await questionBankApi(subject);
    if (!current()) return;
    const groups = data.groups;
    if (!groups.some(ch => ch.key === Q_BANK_VIEW.chapter)) Q_BANK_VIEW.chapter = groups[0]?.key ?? null;
    chapterSelect.innerHTML = groups.length ? groups.map(ch => `<option value="${esc(key(ch.key))}">
      ${esc(ch.label)}${ch.source ? ' · ' + esc(ch.source) : ''}（${ch.count}）</option>`).join('') : '<option>暂无题目</option>';
    chapterSelect.disabled = !groups.length;
    if (groups.length) chapterSelect.value = key(Q_BANK_VIEW.chapter);
    chapterSelect.onchange = () => {
      Q_BANK_VIEW.chapter = chapterSelect.value === 'unassigned' ? '' : chapterSelect.value;
      displayQuestions(true).catch(e => toast(e.message));
    };
    if (init){
      sourceHost.innerHTML = data.sources.length ? `<details open><summary>选择资料 · ${data.sources.length} 份</summary>
        <div class="row"><label><input type="checkbox" id="qb-all"> 全选</label><button class="act" id="qb-start">批量摘取题目</button></div>
        ${data.sources.map(doc => `<div><label><input type="checkbox" data-qb-doc="${doc.id}">
          ${esc(doc.title)} <span class="small muted">${esc(doc.category)}</span></label></div>`).join('')}</details>` :
        '<p class="small muted">先在「资料」页签入库教材、考试资料或课件。</p>';
      const all = $('#qb-all', sourceHost);
      const button = $('#qb-start', sourceHost);
      if (all) {
        const checks = [...sourceHost.querySelectorAll('[data-qb-doc]')];
        all.onchange = () => checks.forEach(el => { el.checked = all.checked; });
        button.onclick = async () => {
          const doc_ids = checks.filter(el => el.checked).map(el => Number(el.dataset.qbDoc));
          if (!doc_ids.length) return toast('请先勾选要摘题的资料');
          if (!await confirmBox('批量摘取题目？', `将处理 ${doc_ids.length} 份资料，复用已经完成的片段。模型按实际用量计费。`)) return;
          if (!current()) return;
          button.disabled = true;
          try { await watch(await questionBankPost(subject, 'start', {doc_ids, confirmed:true})); }
          catch(e){ toast(e.message); }
          finally { button.disabled = false; }
        };
      }
    }
    await displayQuestions(reset);
  }

  async function watch(state){
    let savedChapters = 0;
    while (current()){
      const button = $('#qb-start', sourceHost);
      if (button) button.disabled = state.active;
      progressHost.innerHTML = state.files.map(file => `<details class="qbank-item">
        <summary>${esc(file.category + ' · ' + file.title)} · ${Q_BANK_STATUS[file.status]}
        ${file.total_parts ? ' · ' + Number(file.parts || 0) + '/' + Number(file.total_parts) + ' 段' : ''}
        · ${Number(file.questions || 0)} 题${file.answers ? ' · ' + Number(file.answers) + ' 题有原文答案' : ''}</summary>
        ${file.chapter ? `<p class="small muted">${esc(file.chapter)} · ${esc(Q_BANK_STAGES[file.stage] || '')}
        · ${Number(file.chapters_done || 0)}/${Number(file.chapters_total)} 个章节已完成</p>` : ''}
        ${file.warnings?.length ? '<ul>' + file.warnings.map(w => '<li>' + esc(w) + '</li>').join('') + '</ul>' : ''}</details>`).join('');
      const completed = state.files.reduce((n, file) => n + (file.chapters_done || 0), 0);
      if (!state.active){
        if (state.files.length) await load(false, true);
        return;
      }
      if (completed > savedChapters){
        savedChapters = completed;
        await load();
      }
      await new Promise(resolve => setTimeout(resolve, 1500));
      if (!current()) return;
      state = await questionBankApi(subject, '/progress');
    }
  }

  await load(true, true);
  if (current()) watch(await questionBankApi(subject, '/progress')).catch(e => toast(e.message));
}
