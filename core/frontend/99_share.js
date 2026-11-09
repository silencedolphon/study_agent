// ---------------- 课件分享包（卡 C-0080）----------------
// 人 2026-10-08：「用户可以**按键**导出一个已经入库的课件的入库文件和知识点
// 总结文件，方便分享给别的用户，这样就能摊平调用 api 的费用」。
//
// 前端这一层只做三件事：收集选项、显示进度、把后端的**原话**失败原因端出来。
// 所有校验都在后端（`core/share.py`，卡 C-0079）——前端绝不重复实现任何一条判据：
// 两边各写一套迟早会不一致，而"能不能导入"这种判断错一次就是白花钱重算一遍。
let SHARE_TIMER = null;
let SHARE_TARGET = '';        // 导入的目标学科 slug（空 = 当前学科）

function fmtRange(a, b){ return (b && b !== a) ? `${fmtSize(a)} ~ ${fmtSize(b)}` : fmtSize(a); }

// 分享包进度轮询。导出/导入的 HTTP 请求是**同步**的（跑完才返回），所以必须先开
// 轮询再发请求，否则用户在整段时间里看到的是"点了没反应"。接口只读一个进程内
// 字典，与入库进度同款。
function shareWatchProgress(sel){
  if (SHARE_TIMER){ clearInterval(SHARE_TIMER); SHARE_TIMER = null; }
  const tick = async () => {
    let p = null;
    try { p = await api('/api/share/progress'); } catch(e){ return; }
    const el = $(sel);
    if (el){
      const frac = p.total ? ` ${p.done}/${p.total}` : '';
      el.textContent = p.active ? `正在${p.stage || '处理'}${frac}…（${p.elapsed||0}s）` : '';
    }
    if (!p.active && SHARE_TIMER){ clearInterval(SHARE_TIMER); SHARE_TIMER = null; }
  };
  tick();
  SHARE_TIMER = setInterval(tick, 700);
}

// ---------- 导出 ----------
async function openShareExport(docId, title){
  modal(`<h2>导出分享包</h2>
    <div class="sub" style="margin-bottom:10px">${esc(title||'')}</div>
    <div id="she-body"><div class="empty">正在估算体积…</div></div>`);
  let e = null;
  try {
    e = await api('/api/share/estimate', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({doc_id: docId})});
  } catch(err){
    const b0 = $('#she-body');
    if (b0) b0.innerHTML = `<div class="empty">估算失败：${esc(err.message)}</div>`;
    return;
  }
  const b = $('#she-body');
  if (!b) return;                       // 用户已经关掉了窗口
  const knowN = (e.knowledge_files||[]).length;
  b.innerHTML = `
    <div class="sub">切块 <b>${e.n_chunks}</b> · 向量 <b>${e.n_embeddings}</b> ·
      识图描述 <b>${e.n_figures}</b> 页 · 知识点产物 <b>${knowN}</b> 个${
      e.n_pages ? ` · 识图页图 <b>${e.n_pages}</b> 张` : ''}</div>
    <label class="row" style="margin-top:12px;cursor:pointer">
      <input type="checkbox" id="she-orig" ${e.has_original ? '' : 'disabled'}>
      <span>包含原资料文件${e.has_original
        ? `（${fmtSize(e.parts.original)}${e.original_name ? '：' + esc(e.original_name) : ''}）`
        : '（本机找不到源文件，不可选）'}</span>
    </label>
    <label class="row" style="margin-top:6px;cursor:pointer">
      <input type="checkbox" id="she-pages" ${e.n_pages ? '' : 'disabled'}>
      <span>包含识图页图${e.n_pages
        ? `（${e.n_pages} 张 / ${fmtSize(e.parts.pages)}，体积最大的一项）`
        : '（本机没有渲染好的页图，不可选）'}</span>
    </label>
    <div class="card" style="margin-top:12px;padding:10px">
      <div class="row"><span class="muted">预估包体积</span><b id="she-est"></b></div>
      <div class="sub" style="margin-top:4px">${esc(e.note)}</div>
    </div>
    <div class="row" style="margin-top:14px">
      <button class="act" id="she-go">开始导出</button>
      <button class="ghost" onclick="closeModal()">取消</button>
      <span class="sub" id="she-prog"></span>
    </div>`;
  const upd = () => {
    const o = $('#she-orig') && $('#she-orig').checked;
    const pg = $('#she-pages') && $('#she-pages').checked;
    const r = (o && pg) ? e.est_with_both
      : (o ? e.est_with_original : (pg ? e.est_with_pages : e.est_base));
    const t = $('#she-est');
    if (t) t.textContent = fmtRange(r[0], r[1]);
  };
  if ($('#she-orig')) $('#she-orig').onchange = upd;
  if ($('#she-pages')) $('#she-pages').onchange = upd;
  upd();
  $('#she-go').onclick = () => shareExportStart(docId);
}

async function shareExportStart(docId){
  const go = $('#she-go');
  const body = {doc_id: docId,
    with_original: !!($('#she-orig') && $('#she-orig').checked),
    with_pages: !!($('#she-pages') && $('#she-pages').checked)};
  if (go){ go.disabled = true; go.textContent = '导出中…'; }
  shareWatchProgress('#she-prog');
  let r = null;
  try {
    r = await api('/api/share/export', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  } catch(e){
    if (SHARE_TIMER){ clearInterval(SHARE_TIMER); SHARE_TIMER = null; }
    shareExportFail(e.message);
    return;
  }
  if (SHARE_TIMER){ clearInterval(SHARE_TIMER); SHARE_TIMER = null; }
  shareExportResult(r);
}

function shareExportFail(msg){
  modal(`<h2>导出失败</h2>
    <div class="sub" style="white-space:pre-wrap">${em(msg)}</div>
    <div class="sub" style="margin-top:8px">库里那份资料没有被动过，可以直接重试。</div>
    <div class="row" style="margin-top:14px">
      <button class="act" onclick="closeModal()">知道了</button>
    </div>`);
}

function shareExportResult(r){
  const c = r.counts || {};
  modal(`<h2>导出完成</h2>
    <div class="sub" style="margin-bottom:10px">
      包已经写在本机磁盘上（看板是本机服务，不走浏览器下载）。</div>
    <div class="card" style="padding:10px">
      <div class="sub">包文件（可选中复制）</div>
      <div id="shr-path" style="word-break:break-all;user-select:all">${esc(r.zip)}</div>
      <div class="row" style="margin-top:8px">
        <span class="badge ok">${fmtSize(r.bytes)}</span>
        <span class="badge">切块 ${c.chunks}</span>
        <span class="badge">向量 ${c.embeddings}</span>
        <span class="badge">识图描述 ${c.figure_desc}</span>
        <span class="badge">页图 ${r.n_pages}</span>
        <span class="badge">${r.includes && r.includes.original ? '含原文件' : '不含原文件'}</span>
        <span class="badge">用时 ${r.elapsed}s</span>
      </div>
      ${(r.warnings||[]).length ? `<div class="sub" style="margin-top:8px">${
        r.warnings.map(w => '⚠ ' + esc(w)).join('<br>')}</div>` : ''}
    </div>
    <div class="sub" style="margin-top:10px">
      把这个 zip 发给对方，对方在「资料 → 添加资料 → 导入分享包」里选它即可：
      <b>对方不调用模型、不花钱</b>。</div>
    <div class="row" style="margin-top:14px">
      <button class="act" id="shr-open-dir">打开所在文件夹</button>
      <button class="ghost" id="shr-copy">复制路径</button>
      <button class="ghost" onclick="closeModal()">关闭</button>
    </div>`);
  $('#shr-open-dir').onclick = async () => {
    try {
      const d = await api('/api/share/reveal', {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({path: r.zip})});
      toast(d.error ? d.error : ('已打开：' + d.dir));
    } catch(e){ toast('打不开：' + e.message); }
  };
  $('#shr-copy').onclick = async () => {
    try { await navigator.clipboard.writeText(r.zip); toast('路径已复制'); }
    catch(e){ toast('复制失败——请手动选中上面那行路径'); }
  };
}

// ---------- 导入 ----------
async function shareImportDirect(zip){
  if (!zip) return toast('请先填写分享包完整路径');
  modal(`<h2>正在导入分享包</h2><div class="sub">自动校验并导入当前学科，已有相同资料将跳过。</div><div id="shi-prog"></div>`);
  shareWatchProgress('#shi-prog');
  try {
    const r = await api('/api/share/import', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({zip, subject:'', on_conflict:'skip'})});
    if (r.error) throw new Error(r.error);
    shareImportResult(r, zip);
  } catch(e){ shareImportFail(e.message); }
  finally { if (SHARE_TIMER){ clearInterval(SHARE_TIMER); SHARE_TIMER = null; } }
}

async function openShareImport(zipPath){
  const zip = zipPath || '';
  modal(`<h2>导入分享包</h2>
    <div class="sub" style="margin-bottom:10px">包里的切块与向量直接落地，
      <b>不调用任何模型、不产生接口费用</b>。</div>
    <div class="row">
      <input id="shi-zip" placeholder="分享包完整路径（.zip）" style="flex:1;min-width:280px"
        value="${esc(zip)}">
      <button class="act" id="shi-look">看包信息</button>
      <button class="ghost" id="shi-pick">从 share\\ 目录选</button>
    </div>
    <div id="shi-body"></div>`);
  $('#shi-look').onclick = () => shareInspect($('#shi-zip').value.trim());
  $('#shi-pick').onclick = () => sharePickFromDir();
  $('#shi-zip').onkeydown = e => {
    if (e.key === 'Enter') shareInspect($('#shi-zip').value.trim());
  };
  if (zip) shareInspect(zip);
}

async function sharePickFromDir(){
  let d = null;
  try { d = await api('/api/share/packages'); }
  catch(e){ toast('读不到 share 目录：' + e.message); return; }
  const ps = d.packages || [];
  if (!ps.length){
    toast(`${d.dir} 里还没有分享包——把别人发来的 zip 放进去，或直接粘贴它的完整路径`);
    return;
  }
  modal(`<h2>选择分享包</h2>
    <div class="sub" style="margin-bottom:8px">${esc(d.dir)}（${ps.length} 个）</div>
    ${ps.map(p => `<div class="row" style="margin-top:6px;cursor:pointer"
      onclick="sharePickZip(${JSON.stringify(p.path).replace(/"/g,'&quot;')})">
      <b>${esc(p.name)}</b>
      <span class="muted small">${fmtSize(p.bytes)} · ${fmtTime(p.mtime)}</span></div>`).join('')}
    <div class="row" style="margin-top:14px">
      <button class="ghost" onclick="closeModal()">取消</button>
    </div>`);
}

function sharePickZip(p){ openShareImport(p); }

async function shareInspect(zip){
  if (!$('#shi-body')) return;
  if (!zip){
    $('#shi-body').innerHTML =
      '<div class="empty" style="margin-top:12px">请先填写分享包路径。</div>';
    return;
  }
  $('#shi-body').innerHTML =
    '<div class="empty" style="margin-top:12px">正在读包并逐条核对 sha256…</div>';
  let subs = null;
  try { subs = await api('/api/subjects'); } catch(e){ /* 拿不到就只显示当前库 */ }
  let m = null;
  try {
    m = await api('/api/share/inspect', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({zip: zip, subject: SHARE_TARGET})});
  } catch(e){
    const box0 = $('#shi-body');
    if (box0) box0.innerHTML = `<div class="card" style="margin-top:12px;border-color:#4a1f1c">
      <h2>这个包不能用</h2>
      <div class="sub" style="white-space:pre-wrap;margin-top:6px">${em(e.message)}</div>
      <div class="sub" style="margin-top:8px">换一个包，或让导出方重新导出一次。</div>
    </div>`;
    return;
  }
  const box = $('#shi-body');
  if (!box) return;                      // 弹窗已被关掉
  if (!SHARE_TARGET) SHARE_TARGET = (m.target && m.target.slug) || '';
  const emb = m.embedding || {};
  const inc = m.includes || {};
  const src = m.source || {};
  const know = (m.knowledge||[]).length
    ? (m.knowledge||[]).map(k => `${esc(k.name)}（${k.n_points} 条）`).join('、') : '无';
  const subsList = (subs && subs.subjects) ? subs.subjects : [];
  const opts = subsList.map(s => `<option value="${esc(s.slug)}" ${
    s.slug === m.target.slug ? 'selected' : ''}>${esc(s.name)}（${esc(s.slug)}）${
    s.db_exists ? '' : ' · 库还未建'}</option>`).join('')
    || `<option value="${esc(m.target.slug)}" selected>${esc(m.target.slug)}</option>`;
  box.innerHTML = `
    <div class="card" style="margin-top:12px">
      <h2>${esc(m.title || '(无标题)')}</h2>
      <div class="sub">来源：${esc(src.subject || '未知学科')} · doc#${esc(src.doc_id)} ·
        导出于 ${esc(m.created_at || '未知时间')}${
        m.exported_by ? ' · 导出者 ' + esc(m.exported_by) : ''}</div>
      <div class="row" style="margin-top:8px">
        <span class="badge">${fmtSize(m.bytes)}</span>
        <span class="badge">切块 ${m.chunks_n}</span>
        <span class="badge">向量 ${m.embeddings_n}</span>
        <span class="badge">${esc(emb.model || '?')} dim=${esc(emb.dim)}</span>
        <span class="badge">${inc.original ? '含原资料文件' : '不含原资料文件'}</span>
        <span class="badge">${inc.pages ? ('识图页图 ' + inc.pages + ' 张') : '不含页图'}</span>
      </div>
      <div class="sub" style="margin-top:8px">知识点产物：${know}</div>
      <div class="sub">包内 ${m.n_entries} 个条目；导入预计 <b>${m.est_seconds} 秒</b>
        （纯本机写库，0 次模型调用、CNY 0）</div>
      ${(m.entry_total > (m.entries||[]).length) ? `<div class="sub">占体积最大的是：${
        (m.entries||[]).slice(0,5).map(x => `${esc(x.name)} ${fmtSize(x.bytes)}`).join(' · ')}</div>` : ''}
    </div>
    <div class="card" style="margin-top:12px">
      <h2>导入到哪个学科库</h2>
      <div class="row">
        <select id="shi-sub">${opts}</select>
        <span class="sub">只是把包写进那个库，<b>不会</b>把你当前所在的库切走。</span>
      </div>
      ${m.importable ? `<div class="card" style="margin-top:10px;padding:10px;border-color:#1f4a24">
        <b style="color:var(--ok)">校验通过</b>
        <div class="sub" style="margin-top:4px">整包内容逐条核对通过；向量模型与本库一致
          （${esc(emb.model)} dim=${esc(emb.dim)}）。</div>
      </div>` : `<div class="card" style="margin-top:10px;padding:10px;border-color:#4a1f1c">
        <b style="color:var(--bad)">不能导入：模型不一致</b>
        <div class="sub" style="white-space:pre-wrap;margin-top:6px">${em(m.reason)}</div>
      </div>`}
      ${m.conflict ? `<div class="card" style="margin-top:10px;padding:10px;
        border-color:#7a5a1e;background:#2a2113">
        <b>本库已有同一份课件</b>：doc#${m.conflict.doc_id} ${esc(m.conflict.title || '')}
        <div class="sub" style="margin-top:4px">${esc(m.conflict.path || '')}</div>
        <div class="row" style="margin-top:8px">
          <label class="row" style="cursor:pointer">
            <input type="radio" name="shi-cf" value="skip" checked> 跳过（不动已有的）</label>
          <label class="row" style="cursor:pointer">
            <input type="radio" name="shi-cf" value="duplicate"> 另存一份（两份并存）</label>
        </div>
        <div class="sub" style="margin-top:6px">为保护已有资料，导入不会覆盖已有内容。</div>
      </div>` : ''}
      ${m.db_note ? `<div class="sub" style="margin-top:8px">${esc(m.db_note)}</div>` : ''}
      <div class="row" style="margin-top:12px">
        <button class="act" id="shi-go" ${m.importable ? '' : 'disabled'}>开始导入</button>
        <button class="ghost" onclick="closeModal()">取消</button>
        <span class="sub" id="shi-prog"></span>
      </div>
    </div>`;
  const sel = $('#shi-sub');
  if (sel) sel.onchange = () => {
    SHARE_TARGET = sel.value;
    shareInspect($('#shi-zip').value.trim());
  };
  const go = $('#shi-go');
  if (go) go.onclick = () => shareImportStart($('#shi-zip').value.trim());
}

async function shareImportStart(zip){
  const sel = $('#shi-sub');
  const cf = document.querySelector('input[name="shi-cf"]:checked');
  const onConflict = cf ? cf.value : 'skip';
  const go = $('#shi-go');
  if (onConflict === 'overwrite' && !confirm(
      '「覆盖」会先删掉本库已有的那一份（索引，不动 raw\\ 原文件），再导入这个包。\\n\\n确定继续？')){
    return;
  }
  if (go){ go.disabled = true; go.textContent = '导入中…'; }
  shareWatchProgress('#shi-prog');
  let r = null;
  try {
    r = await api('/api/share/import', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({zip: zip, subject: sel ? sel.value : '',
                            on_conflict: onConflict})});
  } catch(e){
    if (SHARE_TIMER){ clearInterval(SHARE_TIMER); SHARE_TIMER = null; }
    shareImportFail(e.message);
    return;
  }
  if (SHARE_TIMER){ clearInterval(SHARE_TIMER); SHARE_TIMER = null; }
  shareImportResult(r, zip);
}

function shareImportFail(msg){
  modal(`<h2>导入失败</h2>
    <div class="sub" style="white-space:pre-wrap">${em(msg)}</div>
    <div class="sub" style="margin-top:8px">
      库里原有的数据没有被这次失败改坏（导入是整事务的，失败会整体回滚）。</div>
    <div class="row" style="margin-top:14px">
      <button class="act" onclick="closeModal()">知道了</button>
    </div>`);
}

function shareImportResult(r, zip){
  if (r.status === 'skipped'){
    modal(`<h2>已跳过</h2>
      <div class="sub">${esc(r.reason || '本库已有同一份课件')}</div>
      <div class="sub" style="margin-top:6px">
        本库已有 doc#${r.doc_id} ${esc(r.title || '')}。</div>
      <div class="sub" style="margin-top:6px">
        此次没有导入包内的章节、预习或知识点。若需导入这些成果，请查看包信息并选「另存一份」。</div>
      <div class="row" style="margin-top:14px">
        <button class="act" id="shi-again">查看并另存一份</button>
        <button class="act" onclick="closeModal()">知道了</button>
      </div>`);
    $('#shi-again').onclick = () => openShareImport(zip || '');
    return;
  }
  const c = r.counts || {};
  const f = r.fts || {};
  const ftsTxt = (f.fts === false)
    ? `未启用（${esc(f.reason || '')}）`
    : (f.ok ? `可用（探测命中 ${f.hits||0} 条）`
            : `探测未命中：${esc(f.reason || f.probe || '')}`);
  modal(`<h2>导入完成</h2>
    <div class="sub" style="margin-bottom:10px">
      doc#${r.doc_id} ${esc(r.title || '')} → 学科「${esc((r.target||{}).slug || '')}」</div>
    <div class="row">
      <span class="badge ok">切块 ${c.chunks}</span>
      <span class="badge ok">向量 ${c.embeddings}</span>
      <span class="badge">识图描述 ${c.figure_desc}</span>
      <span class="badge">知识点产物 ${c.knowledge} 个</span>
      <span class="badge">页图 ${c.pages}</span>
      <span class="badge">用时 ${r.elapsed}s</span>
    </div>
    <div class="card" style="margin-top:12px;padding:10px">
      <div class="sub">全文检索：<b>${ftsTxt}</b></div>
      <div class="sub">模型调用：<b>${r.model_calls} 次</b>，花费 <b>CNY ${(r.cost_cny||0).toFixed(2)}</b></div>
      ${r.source ? `<div class="sub">来源：${esc(r.source.subject || '未知学科')} ·
        doc#${esc(r.source.doc_id)} · ${esc(r.source.title || '')}</div>` : ''}
      ${r.package_sha256 ? `<div class="sub">已记录来源
        <span style="user-select:all">${esc(String(r.package_sha256).slice(0, 16))}…</span>
        （日后可追溯是从哪个包来的）</div>` : ''}
      ${r.file ? `<div class="sub">原资料已落到 raw\\：${esc(r.file)}</div>`
               : `<div class="sub">包内不含原文件：已有文字块可检索，查看原文件和按页渲染需要源文件。</div>`}
      ${r.overwritten ? `<div class="sub">已按你的选择覆盖：先删掉本库已有的同一份
        （doc#${(r.overwritten.deleted_doc_ids||[]).join('、doc#')}，共
        ${r.overwritten.chunks_removed} 个切块），再导入这个包。raw\\ 里的原文件没动。</div>` : ''}
      ${(r.knowledge||[]).map(k => `<div class="sub">知识点产物：${esc(k.new_file)}
        （${k.n_points} 条，重映射引用 ${k.remapped_refs} 处）</div>`).join('')}
      ${(r.warnings||[]).length ? `<div class="sub" style="margin-top:6px">${
        r.warnings.map(w => '⚠ ' + esc(w)).join('<br>')}</div>` : ''}
    </div>
    <div class="row" style="margin-top:14px">
      <button class="act" id="shi-see">去看这份资料</button>
      <button class="ghost" onclick="closeModal()">关闭</button>
    </div>`);
  $('#shi-see').onclick = () => { closeModal(); go('docs'); };
}

go('assistant');
