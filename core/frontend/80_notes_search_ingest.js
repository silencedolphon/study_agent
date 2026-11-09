// ---------------- 笔记 ----------------

// 人拍板（卡 C-0052）：「笔记页签用来收集在知识点窗口和对整个课件提问和总结时

// 产出的笔记。笔记条目要带有记录的时间和来源。点进条目后不光可以看到笔记原文，

// 还可以按按钮跳转出绑定了该笔记的知识点窗口。」

//

// 时间与来源都在笔记文件的 front-matter 里（created / origin / kp_id / doc_id /

// source_label，格式见 core/study.py::save_note 的头注释），这里只负责显示与跳转。

// **不猜来源**：老笔记没有这些键就写"—"，绝不替它安一个"来自某个知识点"。

const NOTE_ORIGIN_LABEL = {knowledge_point: '知识点窗口', deck_chat: '课件问答'};



function noteSourceText(n){

  return (n && (n.source_label || NOTE_ORIGIN_LABEL[n.origin])) || '—';

}



// task-33：viewNotes 已改版（按课件→绑定 KP 分组，见复习流程区块）。

async function openNote(name){

  const d = await api('/api/note?name='+encodeURIComponent(name));

  if (d.error){ toast('读不到这篇笔记：' + d.error); return; }

  const n = d.note || {};

  // 「跳到知识点窗口」只在**真的绑定了知识点**时给：课件问答笔记本来就不属于

  // 任何一条知识点（没有 kp_id），给它一个跳不过去的按钮比不给更坏。

  const canJump = !!(n.kp_id && n.doc_id);

  // 卡 C-0082：正文显示的是 `body`——**只有人自己写进去的那部分**

  // （剥掉 front-matter、自动生成的一级标题、「## 出处」段，见

  // `core/study.py::note_body`）。后端没给 body 时回落 `content`（原文），

  // 宁可多显示几行机器壳，也不能显示成空白。

  // Markdown 渲染器禁用原始 HTML，并拒绝危险链接；编辑仍使用原始文本。

  modal(`<h2>${esc(n.title || name)}</h2>

    <div class="small muted">${esc(n.file || '')} ·

      记录时间 ${esc(fmtTime(n.created || n.mtime))} ·

      来源 ${esc(noteSourceText(n))}${n.question ? ' · 问：' + esc(n.question) : ''}${

      (n.tags || []).length ? ' · 标签 ' + esc(n.tags.join(' / ')) : ''}</div>

    <div class="note-body">${mdToHtml(d.body ?? d.content ?? '')}</div>

    <div class="row" style="margin-top:14px">

      ${canJump ? `<button class="act" id="note-jump">跳到知识点窗口（${

        esc(n.kp_id)}）</button>` : ''}

      <button class="ghost" onclick="closeModal()">关闭</button></div>`);

  const jb = $('#note-jump');

  if (jb) jb.onclick = () => mmJumpToKp(parseInt(n.doc_id), n.kp_id, n.title || '');

}



// ---------------- 检索 ----------------
function noteSelectVisible(){
  document.querySelectorAll('#view .nt-note').forEach(row => {
    if (row.getClientRects().length) row.querySelector('.nt-delete-cb').checked = true;
  });
}
async function noteDeleteSelected(){
  const expected_subject = NOTES_PAGE_SUBJECT;
  if (!expected_subject) return toast('学科信息尚未加载，请刷新页面');
  const files = [...document.querySelectorAll('#view .nt-delete-cb:checked')].map(x => x.value);
  if (!files.length) return toast('请先选择笔记');
  if (!await confirmBox('删除笔记？', `将永久删除选中的 ${files.length} 篇笔记，无法撤销。`)) return;
  try {
    const r = await api('/api/notes/delete', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({files, confirm:true, expected_subject})});
    if (r.error) return toast(r.error);
    const errs = r.errors || [];
    toast(`已删除 ${(r.deleted||[]).length} 篇；失败 ${errs.length} 篇` +
      (errs.length ? `：${errs[0].file} ${errs[0].error}` : ''));
    await viewNotes();
  } catch(e){ toast('删除失败：' + e.message); }
}
async function batchDeleteTextbookArtifacts(kind, docId, chapters){
  const expected_subject = kind === 'preview' ? PV.subject : BOOKKP.subject;
  if (!expected_subject) return toast('学科信息尚未加载，请刷新页面');
  if (!chapters || !chapters.length) return toast('请先勾选章节');
  const what = kind === 'preview' ? '预习结果' : '教材知识点总结、未完成内容及复习记录';
  if (!await confirmBox('删除章节产物？', `将删除选中 ${chapters.length} 章的${what}，不可撤销；教材原文和入库块保留，历史备份保留。`)) return;
  try {
    const r = await api('/api/textbook-artifacts/delete', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({doc_id:docId, chapters, kind, confirm:true, expected_subject})});
    if (r.error) return toast(r.error);
    const errs = r.errors || [];
    toast(`已删除 ${(r.deleted||[]).length} 个文件；失败 ${errs.length} 个` +
      (errs.length ? `：${errs[0].file} ${errs[0].error}` : ''));
    closeModal();
    if (kind === 'preview') { PV.arts = {}; await pvReload(); }
    else { await bookLoadChapters(); }
  } catch(e){ toast('删除失败：' + e.message); }
}

async function doSearch(q){
  const d = await api('/api/search?q='+encodeURIComponent(q)+'&k=8');
  const hits = d.hits.map(h => `
    <div class="hit">
      <div class="cite">${esc(h.cite)}</div>
      <div class="why">${esc((h.why||[]).join(' · '))}
        ${h.confidence==='low'?'<span class="badge warn">低置信</span>':''}</div>
      ${mdToHtml(h.text)}
      <div style="margin-top:8px"><button class="ghost"
        onclick="openChunk(${h.chunk_id})">看上下文</button></div>
    </div>`).join('');
  $('#view').innerHTML = `
    <div class="card"><h2>检索「${esc(q)}」· ${d.n} 条命中</h2>
      <div class="row" style="margin-top:10px">
        <button class="ghost" onclick="go('${CURRENT}')">← 返回</button>
      </div></div>
    <div style="margin-top:14px">${hits || '<div class="empty">没有命中。换个说法或关键词试试。</div>'}</div>`;
}

async function openChunk(id, before=1, after=1){
  const d = await api(`/api/chunk?id=${id}&before=${before}&after=${after}`);
  const blocks = d.context.map(c => `
    <div class="hit" style="${c.chunk_id===id?'border-color:var(--accent)':''}">
      <div class="cite">${esc(c.cite)}${c.chunk_id===id?' <span class="badge">命中</span>':''}</div>
      ${mdToHtml(c.text)}
    </div>`).join('');
  modal(`<h2>${esc(d.target.title||d.target.path)}</h2>
    <div class="small muted">${esc(d.target.heading_path||'')}</div>
    <div style="margin-top:14px">${blocks}</div>
    <div class="row" style="margin-top:14px">
      <button class="ghost" onclick="closeModal()">关闭</button></div>`);
}

// ---------------- 批量入库（卡 C-0037：多选 + 多管线并发） ----------------
//
// 人要求：「在"资料"页签，课件导入知识库那一功能栏中添加批量导入的功能，
// 若原本已经有了这个功能，可以尝试多个管线同时跑，以提高入库效率」。
//
// 这一屏管两件事：**发起**（勾多份待入库文件 + 并发数 + 待处理数与预估开销 +
// 确认）与**观察**（总体 N/M + 每份状态 + 失败原因 + 一键重试失败项）。
// 真正的并发、每任务独立写库连接、失败隔离、进度分离都在
// `core/pipeline_pool.py`，前端只负责显示——入库管线一行都不在这里重写。
//
// ⚠ 页面上现在有两条入库进度：`#ingest-progress`（单份 / 全部待处理，走
//   `core/progress.py` 的全局单例）与 `#batch-progress`（批量，走执行器的
//   per-task 状态表）。两条**刻意不合成一条**：合成就会互相盖掉，
//   那正是"进度分离"要解决的问题。
let BATCH = { id: null, snap: null, files: [], sel: [], timer: null,
              busy: false, toasted: false };

const BATCH_ST = {
  pending:     {label:'等待',   cls:''},
  running:     {label:'进行中', cls:'warn'},
  done:        {label:'完成',   cls:'ok'},
  skipped:     {label:'已跳过', cls:''},
  unsupported: {label:'不支持', cls:'bad'},
  failed:      {label:'失败',   cls:'bad'},
};

function batchCard(files){
  files = files || [];
  const n = files.length;
  const rows = files.map(f => {
    const st = STATE[f.state] || {label:f.state, cls:''};
    return `<tr>
      <td><input type="checkbox" class="batch-cb" value="${esc(f.rel)}"
        ${BATCH.sel.includes(f.rel) ? 'checked' : ''}></td>
      <td>${esc(f.name)}<div class="small muted">${esc(f.rel)}</div></td>
      <td class="small">${fmtSize(f.size)}</td>
      <td><span class="badge ${st.cls}">${st.label}</span></td>
    </tr>`;
  }).join('');
  const wopts = [1,2,3,4].map(k =>
    `<option value="${k}"${k === BATCH.workers ? ' selected' : ''}>${k}</option>`).join('');
  return `<div class="card" style="margin-top:14px">
    <h2>选择要入库的文件</h2>
    <div class="small muted" style="margin-bottom:10px">可以一次选择多份；开始前会显示预计费用。</div>
    <div id="batch-progress"></div>
    <div class="row" style="margin-bottom:10px">
      <button class="text-btn" onclick="batchSelectAll()">全选</button>
      <button class="text-btn" onclick="batchSelectNone()">取消选择</button>
      <label class="small muted">同时处理
        <select id="batch-workers">${wopts}</select></label>
      <button class="act" onclick="batchConfirm()">开始批量入库</button>
      <button class="ghost" onclick="batchRetry()" id="batch-retry"
        style="display:none">重试失败项</button>
      <button class="ghost" onclick="batchDismiss()" id="batch-dismiss"
        style="display:none">清除结果</button>
      <span class="small muted" id="batch-selinfo"></span>
    </div>
    ${n ? `<table><thead><tr><th style="width:34px"></th><th>文件</th><th>大小</th>
      <th>状态</th></tr></thead><tbody id="batch-list">${rows}</tbody></table>`
      : `<div class="empty">本库没有待入库的文件——都已入库，或格式不支持。</div>`}
    <div id="batch-tasks"></div>
  </div>`;
}

function bindBatch(files){
  BATCH.files = files || [];
  const list = $('#batch-list');
  if (list) list.onchange = e => {
    if (e.target.classList && e.target.classList.contains('batch-cb')) batchSelUpdate();
  };
  batchSelUpdate();
  // 切回这一页时把**已有的批次**进度接上：后端跑在后台线程池里，
  // 人离开这一页（甚至刷新）它都照跑，回来必须还能看见。
  if (BATCH.id){ batchRender(BATCH.snap); batchWatch(); }
  else { const box = $('#batch-progress'); if (box) box.innerHTML = ''; }
}

function batchSelUpdate(){
  const cbs = document.querySelectorAll('.batch-cb');
  BATCH.sel = Array.from(cbs).filter(c => c.checked).map(c => c.value);
  const el = $('#batch-selinfo');
  if (el) el.textContent = BATCH.sel.length ? `已选 ${BATCH.sel.length} 份` : '未选择';
}

function batchSelectAll(){
  document.querySelectorAll('.batch-cb').forEach(c => { c.checked = true; });
  batchSelUpdate();
}

function batchSelectNone(){
  document.querySelectorAll('.batch-cb').forEach(c => { c.checked = false; });
  batchSelUpdate();
}

async function batchConfirm(){
  const rels = BATCH.sel.slice();
  if (!rels.length) return toast('先勾选要入库的文件');
  const workers = parseInt(($('#batch-workers') || {}).value || '2', 10) || 2;
  let p = null;
  try {
    p = await api('/api/ingest/batch/plan', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({paths: rels, workers})});
  } catch(e){ return toast('预估失败：' + e.message); }
  if (!p || p.error) return toast('预估失败：' + ((p && p.error) || '未知错误'));
  BATCH.autoKp = !!p.auto_kp;
  const money = p.est_cost_cny || 0;
  modal(`
    <h2>确认批量入库</h2>
    <div class="sub" style="margin-bottom:10px">
      这一步会真的解析文件并写索引，<b>识图还会调用视觉模型产生费用</b>。
      先看清下面的数字再点开始。
    </div>
    <table><tbody>
      <tr><td>已选文件</td><td><b>${p.n_files}</b> 份</td></tr>
      <tr><td>其中待处理</td><td><b>${p.n_todo}</b> 份${
        p.n_skip ? `（${p.n_skip} 份内容未变，会跳过入库${p.auto_kp ? '；如缺知识点仍会整理' : ''}）` : ''}${
        p.n_unsupported ? `（${p.n_unsupported} 份格式不支持）` : ''}</td></tr>
      <tr><td>合计页数</td><td>${p.pages_total} 页</td></tr>
      <tr><td>识图调用</td><td><b>${p.est_calls}</b> 次${
        p.est_calls ? '' : '（识图已关，或选的不是 pdf/pptx）'}</td></tr>
      <tr><td>入库预估开销</td><td><b>CNY${money.toFixed(2)}</b> 以内${p.auto_kp ? '（不含后续知识点整理）' : ''}</td></tr>
      ${p.auto_kp ? '<tr><td>后续整理</td><td>自动整理课件知识点，将产生额外模型调用及费用；次数和费用需入库后按实际材料确定，已有完整结果会跳过。</td></tr>' : ''}
      <tr><td>并发数</td><td><b>${p.workers}</b>${
        p.workers > 2 ? '（偏高：识图更容易触发限流）' : ''}</td></tr>
    </tbody></table>
    <div class="small muted" style="margin-top:10px">${esc(p.est_rule || '')}</div>
    <div class="row" style="margin-top:14px">
      <button class="act" onclick="batchStart()">开始批量入库</button>
      <button class="ghost" onclick="closeModal()">取消</button>
    </div>`);
}

async function batchStart(){
  closeModal();
  if (BATCH.busy) return;
  const rels = BATCH.sel.slice();
  if (!rels.length) return toast('先勾选要入库的文件');
  const workers = parseInt(($('#batch-workers') || {}).value || '2', 10) || 2;
  BATCH.busy = true;
  try {
    const r = await api('/api/ingest/batch', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({paths: rels, workers, auto_kp: !!BATCH.autoKp})});
    if (r.error){
      toast(r.error);
      // 后端说"已有一个批次在跑"时会带上它的 batch_id：接上那条，别让人干等
      if (r.batch_id){ BATCH.id = r.batch_id; go('docs'); }
      return;
    }
    BATCH.id = r.batch_id; BATCH.toasted = false;
    toast(`已提交 ${r.total} 份，并发 ${r.workers}；可以离开这一页，后台照跑`);
    go('docs');
  } catch(e){ toast('提交失败：' + e.message); }
  finally { BATCH.busy = false; }
}

function batchWatch(){
  if (!BATCH.id || BATCH.timer) return;
  const tick = async () => {
    let s = null;
    try { s = await api('/api/ingest/batch/progress?batch=' + encodeURIComponent(BATCH.id)); }
    catch(e){ return; }        // 网络抖一下就等下一秒，不打断面板
    BATCH.snap = s;
    batchRender(s);
    if (!s.running){ clearInterval(BATCH.timer); BATCH.timer = null; }
  };
  tick();
  BATCH.timer = setInterval(tick, 1000);
}

function batchTaskRows(s){
  const rows = (s.tasks || []).map(t => {
    const st = BATCH_ST[t.status] || {label:t.status, cls:''};
    let mid;
    if (t.status === 'running'){
      const step = t.total ? `${t.done}/${t.total}` : '';
      mid = `<div class="pbar active" style="margin:0 0 3px"><i style="width:${
        t.percent || 0}%"></i></div>
        <span class="small muted">${esc(t.stage || '')} ${step} · ${t.percent || 0}%${
          t.note ? ' · ' + esc(t.note) : ''}</span>`;
    } else if (t.status === 'failed'){
      mid = `<span class="small" style="color:var(--bad)">${esc(t.error || t.reason || '失败')}</span>`;
    } else if (t.status === 'pending'){
      mid = `<span class="small muted">排队中（等前面的份跑完）</span>`;
    } else {
      mid = `<span class="small muted">${t.chunks ? t.chunks + ' 块' : ''}${
        t.kp_reason ? ' · ' + esc(t.kp_reason) : ''}${
        t.reason ? ' · ' + esc(t.reason) : ''}${
        (t.warnings && t.warnings.length) ? ' · ' + esc(t.warnings[0]) : ''}</span>`;
    }
    return `<tr>
      <td>${esc(t.name)}<div class="small muted">${esc(t.rel)}</div></td>
      <td><span class="badge ${st.cls}">${st.label}</span></td>
      <td style="min-width:170px">${mid}</td>
      <td class="small muted" style="white-space:nowrap">${
        t.seconds ? _fmtDur(t.seconds) : ''}${
        t.cost_cny ? `<br>CNY${t.cost_cny.toFixed(3)}` : ''}</td>
    </tr>`;
  }).join('');
  return rows ? `<table style="margin-top:10px"><thead><tr><th>文件</th><th>状态</th>
    <th>阶段</th><th>用时</th></tr></thead><tbody>${rows}</tbody></table>` : '';
}

function batchRender(s){
  const box = $('#batch-progress');
  if (box){
    if (!s || !s.batch_id){ box.innerHTML = ''; }
    else {
      const pct = s.percent || 0;
      const run = s.running_files || [];
      const tail = s.running
        ? `<span class="muted">已用 ${_fmtDur(s.elapsed)}</span>`
        : `<span class="badge ${s.failed ? 'bad' : 'ok'}">${
             s.failed ? '有失败项' : '完成'}</span>
           <span class="muted">用时 ${_fmtDur(s.elapsed)}</span>`;
      // 取消按钮（卡 C-0041）：**只在批次 running 时出现**——批次一停，
      // 下一次渲染（batchWatch 每秒一次）就不给这个按钮了，不需要手动隐藏。
      // 后端语义：未开始的份作废，正在跑的份跑完当前文件再收尾（不硬杀）。
      const cancelBtn = s.running
        ? `<button class="ghost" id="batch-cancel" style="margin-left:8px"
             title="未开始的份立即作废；正在跑的份跑完当前文件后收尾">取消未开始的份</button>`
        : '';
      box.innerHTML = `<div class="pwrap">
        <div class="phead">
          <b>批量入库 ${s.finished}/${s.total}</b>
          <span class="muted">并发 ${s.workers}${
            run.length ? ' · 正在跑：' + run.map(esc).join('、') : ''}</span>
          <span style="flex:1"></span>${tail}${cancelBtn}
        </div>
        <div class="pbar${s.running ? ' active' : ''}"><i style="width:${pct}%"></i></div>
        <div class="pfoot">
          <span class="muted">成功 ${s.succeeded} · 跳过 ${s.skipped}${
            s.unsupported ? ' · 不支持 ' + s.unsupported : ''} · 失败 ${s.failed}${
            s.chunks ? ' · 共 ' + s.chunks + ' 块' : ''}${
            s.cost_cny ? ' · CNY' + s.cost_cny.toFixed(3) : ''}</span>
          <span style="flex:1"></span><b>${pct}%</b>
        </div>
      </div>`;
      if (s.warmup_error){
        box.innerHTML += `<div class="small" style="color:var(--bad)">预热告警：${
          esc(s.warmup_error)}</div>`;
      }
    }
  }
  const tb = $('#batch-tasks');
  if (tb) tb.innerHTML = batchTaskRows(s || {});
  const rt = $('#batch-retry');
  if (rt) rt.style.display = (s && s.failed && !s.running) ? '' : 'none';
  const dis = $('#batch-dismiss');
  if (dis) dis.style.display = (s && s.batch_id && !s.running) ? '' : 'none';
  if (s && s.batch_id && !s.running && s.total && s.finished === s.total && !BATCH.toasted){
    BATCH.toasted = true;
    toast(`批量入库结束：成功 ${s.succeeded} · 跳过 ${s.skipped} · 失败 ${s.failed}`);
  }
  // innerHTML 每次渲染都重建，所以事件也要每次重新绑（与知识点页 kpb-cancel 同款）
  const cb = $('#batch-cancel');
  if (cb) cb.onclick = batchCancel;
}

async function batchCancel(){
  if (!BATCH.id) return toast('没有正在跑的批次');
  try {
    const r = await api('/api/ingest/batch/cancel', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({batch_id: BATCH.id})});
    if (r.error) return toast(r.error);
    if (!r.found) return toast(r.note || '批次已不在（看板重启后内存里的批次会丢）');
    // 后端回来的是"这次作废了几份 + 还有几份在跑"，直接念给用户听
    toast(r.cancelled
      ? `已取消：未开始的 ${r.cancelled} 份作废，正在跑的 ${r.running} 份跑完当前文件`
      : (r.note || '已请求取消'));
  } catch(e){ toast('取消失败：' + e.message); }
}

async function batchRetry(){
  if (!BATCH.id) return toast('没有可重试的批次');
  try {
    const r = await api('/api/ingest/batch/retry', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({batch_id: BATCH.id})});
    if (r.error) return toast(r.error);
    BATCH.id = r.batch_id; BATCH.toasted = false;
    toast(`已重新提交 ${r.total} 份失败项，并发 ${r.workers}`);
    go('docs');
  } catch(e){ toast('重试失败：' + e.message); }
}

function batchDismiss(){
  BATCH.id = null; BATCH.snap = null; BATCH.toasted = false;
  if (BATCH.timer){ clearInterval(BATCH.timer); BATCH.timer = null; }
  ['#batch-progress', '#batch-tasks'].forEach(sel => {
    const el = $(sel); if (el) el.innerHTML = '';
  });
  ['#batch-retry', '#batch-dismiss'].forEach(sel => {
    const el = $(sel); if (el) el.style.display = 'none';
  });
}


