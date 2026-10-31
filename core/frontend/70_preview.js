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

function pvText(s){
  return esc(String(s == null ? '' : s)).replace(/\n/g, '<br>');
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
  const box = $('#view');
  if (!box) return;
  if (PV.timer){ clearInterval(PV.timer); PV.timer = null; }
  box.innerHTML = `<div class="pv-top"><div><h2>📖 预习</h2>
      <div class="pdesc">选择章节，查看预习重点与自测题。</div></div></div>
    <div class="empty" id="pv-loading">正在读取教材与章节…</div>`;
  PV.loading = true;
  await pvLoadOptions();
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
    if (p && p.active){ PV.prog = p; pvWatchProgress(); }
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
        if (art && art.ok && art.sections.length) PV.sel.sec = 0;  // 默认第一节
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
        <button class="ghost" onclick="pvReload()" title="重新读取章节与产物状态">刷新</button></div>
    </div>
    ${banner}
    <div id="pv-progress"></div>
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
      <span class="pv-ch-act">
        ${p.status === 'failed'
          ? `<button class="act" data-pvact="regen" data-ch="${ch.no}">重新预习（上次失败）</button>`
          : (p.exists
              ? `<button class="ghost" data-pvact="view" data-ch="${ch.no}">查看预习</button>
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
      lecture: Array.isArray(s.lecture) ? s.lecture
        : (s.lecture ? [str(s.lecture)] : []),
      pitfalls: list(s.pitfalls || s.common_mistakes || []),
      self_check: scOf(s.self_check || s.questions || []),
      sources,
    };
  });
  const terms = (Array.isArray(r.terms) ? r.terms : []).map(t => ({
    term: str(t.term || t.name || ''), en: str(t.en || ''),
    def: str(t.def || t.desc || t.explain || ''), why: str(t.why || ''),
  })).filter(t => t.term || t.def);
  const warnings = Array.isArray(raw && raw.warnings) ? raw.warnings
    : (Array.isArray(meta.warnings) ? meta.warnings : []);
  return {
    ok: true, sections: secs, terms,
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
  const termsHtml = (art.terms || []).length ? `
    <div class="pv-card"><h3>本章关键名词</h3>
      <div class="pv-core">${art.terms.map(t => `<div class="pv-core-row">
        <b>${esc(t.term)}${t.en ? '<span class="muted" style="font-weight:400"> ' + esc(t.en) + '</span>' : ''}</b>
        <span>${pvText(t.def)}${t.why ? '<br><span class="muted">为什么重要：' + pvText(t.why) + '</span>' : ''}</span>
      </div>`).join('')}</div></div>` : '';
  const ovHtml = `
    <div class="pv-card">
      <h3>第${ch.no}章 章概览</h3>
      ${ov.summary ? `<div class="pv-text">${pvText(ov.summary)}</div>` : '<div class="muted small">（这一章没有单独的章概览，直接看小节即可。）</div>'}
      ${ov.study_order.length ? `<div class="pv-sub"><b>学习顺序</b><ol>${ov.study_order.map(x => `<li>${pvText(x)}</li>`).join('')}</ol></div>` : ''}
      <div class="pv-cols">
        ${ov.difficulties.length ? `<div><b>难点</b><ul>${ov.difficulties.map(x => `<li>${pvText(x)}</li>`).join('')}</ul></div>` : ''}
        ${ov.prerequisites.length ? `<div><b>前置知识</b><ul>${ov.prerequisites.map(x => `<li>${pvText(x)}</li>`).join('')}</ul></div>` : ''}
        ${ov.exam_points.length ? `<div><b>考点</b><ul>${ov.exam_points.map(x => `<li>${pvText(x)}</li>`).join('')}</ul></div>` : ''}
      </div>
      <div class="row" style="margin-top:10px">
        <button class="act" data-pvact="sec" data-ch="${ch.no}" data-i="0">从第一节开始</button>
      </div>
    </div>`;
  if (secIdx == null) return crumb + warnHtml + ovHtml + termsHtml;
  const s = art.sections[secIdx];
  if (!s) return crumb + ovHtml;
  const coreHtml = s.core.length ? `
    <div class="pv-card"><h3>核心概念</h3>
      <div class="pv-core">${s.core.map(x => `<div class="pv-core-row">
        ${x.term ? `<b>${esc(x.term)}</b>` : ''}<span>${pvText(x.desc)}</span></div>`).join('')}</div>
    </div>` : '';
  const lectHtml = s.lecture.length ? `
    <div class="pv-card"><h3>老师会怎么讲</h3>
      <ol class="pv-lecture">${s.lecture.map(x => `<li>${pvText(x)}</li>`).join('')}</ol>
    </div>` : '';
  const pitHtml = s.pitfalls.length ? `
    <div class="pv-card"><h3>易错点</h3>
      <ul class="pv-pit">${s.pitfalls.map(x => `<li>${pvText(x)}</li>`).join('')}</ul>
    </div>` : '';
  const scHtml = s.self_check.length ? `
    <div class="pv-card"><h3>自测（答案默认收起）</h3>
      ${s.self_check.map(x => `<details class="pv-qa"><summary>${esc(x.q)}</summary>
        <div class="pv-ans">${pvText(x.a || '（本题没有给答案）')}</div></details>`).join('')}
    </div>` : '';
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
    </div>` + coreHtml + lectHtml + pitHtml + scHtml +
    (srcs.length ? `<div class="pv-card"><h3>出处</h3><div class="pv-srcs">${srcs.join('')}</div></div>` : '') +
    termsHtml;
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
        if (art && art.ok && art.sections.length && PV.sel.sec == null) PV.sel.sec = 0;
      }
      pvRender();
    } else if (act === 'view'){
      if (!ch) return;
      PV.open[chNo] = true;
      if (!PV.arts[pvKeyOf(chNo)]) await pvEnsureArt(chNo);
      const art = PV.arts[pvKeyOf(chNo)];
      PV.sel = {ch:chNo, sec: (art && art.ok && art.sections.length) ? 0 : null};
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
    } else if (act === 'regen'){
      await pvStart(chNo, true);
    }
  };
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
    const calls = Number(est.calls || est.est_calls || 1);
    const cost = Number(est.cost_cny_max != null ? est.cost_cny_max : (est.est_cost_cny || 0));
    const pages = Number(est.n_pages || est.pages || 0);
    const costTxt = cost > 0 ? ('≤¥' + cost.toFixed(4)) : '无法估算（按实际计）';
    const lines = [
      '预计 ' + calls + ' 次调用｜成本上界 ' + costTxt + '｜章 ' + (pages ? (pages + ' 页') : '若干页'),
      '模型：' + (est.model || '默认配置') + (est.page_label ? '｜' + est.page_label : ''),
      force ? '⚠ 这一章已有产物：重新预习会覆盖（旧产物会先备份）。' : '',
      est.skip_would_happen ? '产物与材料都没变，后端可能直接跳过（0 调用、0 花费）。' : '',
      '确认后才开始调用外接大模型。'
    ].filter(Boolean);
    const ok = await confirmBox(force ? '重新预习这一章？' : '现在预习这一章？', lines.join('\n'));
    if (!ok) return;
    const r = await api('/api/preview/start', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({doc_id: PV.docId, chapter: chNo, force: !!force})});
    if (!r.started){
      toast('没能开始：' + (r.error || '未知原因'));
      if (r.hint) toast(r.hint);
      return;
    }
    toast('已开始预习，生成中…');
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
        if (done) toast('预习完成：第' + (p.chapter || '') + '章已生成' + (p.cost_cny ? ' · CNY' + Number(p.cost_cny).toFixed(4) : ''));
        else if (p && p.status === 'failed') toast('预习失败：' + (p.error || '未知原因'));
        // 刷新章节状态，并自动选中刚生成的章
        await pvLoadChapters();
        const lastCh = parseInt(String(p && p.chapter || '').replace(/[^0-9]/g, ''), 10);
        if (done && lastCh && PV.chapters.some(c => c.no === lastCh)){
          PV.sel = {ch: lastCh, sec: null};
          PV.open[lastCh] = true;
          await pvEnsureArt(lastCh);
          const art = PV.arts[pvKeyOf(lastCh)];
          if (art && art.ok && art.sections.length) PV.sel.sec = 0;
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


