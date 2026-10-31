// ---------------- 知识点 ----------------
// 卡 C-0053（人拍板 2026-09-27）：页签顶部那两块**手工**入口整栏删掉——
//   ① 空态块「还没有知识点。让助手从资料中提取，或在这里手工添加。」
//   ② 手工添加 / 新增知识点表单（名称 / 科目 / 出处 chunk_id / 一句话摘要 + 添加）
// 删的只是**页签上的入口**：后端 `POST /api/kp`（库内知识点 CRUD）与 `kpForm()` /
// `bindKpForm()` 两个函数**保留**（全仓库没有别的前端调用方，但接口是公开契约，
// 删了会砸到脚本/将来的入口。）
// 同时在「课件思维导图」栏**上面**加一栏「名词解释」（`termsCard()`）。
async function viewKps(){
  const {knowledge_points: kps} = await api('/api/kps');
  if (!kps.length){
    $('#view').innerHTML = `${kpBuildCard()}
      ${termsCard()}
      ${mindmapCard()}`;
    bindKpBuild(); mountTerms(); mountMindmap(); return;
  }
  const rows = kps.map(k => {
    const cls = k.mastery>=.75?'ok':(k.mastery>=.4?'warn':'bad');
    return `<tr>
      <td><a href="#" onclick="openKp(${k.id});return false">${esc(k.name)}</a>
        <div class="small muted kpsum">${esc(k.summary||'')}</div>
        <div class="small muted">${esc(k.subject||'')}</div></td>
      <td style="width:140px"><div class="bar"><i style="width:${pct(k.mastery)}%"></i></div>
        <span class="small muted">${pct(k.mastery)}%</span></td>
      <td class="small muted">${k.due_at?fmtTime(k.due_at):'未排期'}</td>
    </tr>`;}).join('');
  $('#view').innerHTML = `
    <div class="card">
      <h2>知识点 · ${kps.length} 个</h2>
      <table><thead><tr><th>知识点</th><th>掌握度</th><th>下次复习</th></tr></thead><tbody>${rows}</tbody></table>
    </div>
    ${kpBuildCard()}
    ${termsCard()}
    ${mindmapCard()}`;
  bindKpBuild();
  mountTerms();
  mountMindmap();
}

// ---------------- 批量整理知识点（卡 C-0038：多选课件 + 多管线并发） ----------------
//
// 人要求：「在"知识点"页签添加批量整理知识点的功能：可选择知识库中的多个课件整理知识点。
// 同样尝试多个管线同时跑以提高整理效率」。
//
// 这一屏管两件事：**发起**（弹窗里勾多份 + 并发数 + 合计预估 + 花钱前确认）与
// **观察**（总体 N/M + 每份状态 + 失败原因 + 单独重试）。
// 真正的并发、失败隔离、进度分离全在 `core/kp_batch.py`（线程池 + per-task 状态），
// 前端只负责显示——管线一行都不在这里重写（命令行 `tools/kp_build.py` 同源）。
//
// ⚠ 卡片上并排两条进度：`#kp-batch-progress`（批量，本卡）与 `#kp-build-progress`
//   （单份后台线程，卡 C-0035）。两者互斥，谁 active 显示谁；合成一条会互相盖掉
//   ——这正是"进度分离"要解决的问题（见 core/kp_batch.py 头部）。
const KPB = { timer: null, batchTimer: null, docs: [], sel: null, thr: 2.0 };

function kpBuildCard(){
  return `<div class="card" style="margin-top:14px">
    <h2>整理知识点</h2>
    <div class="small muted" style="margin-bottom:10px">选择课件，生成可复习的知识点。开始前会显示预计费用。</div>
    <div id="kp-batch-progress"></div>
    <div id="kp-build-progress"></div>
    <div class="row">
      <button class="act" id="kp-build-open">选择课件</button>
      <span class="small muted" id="kp-build-state"></span>
    </div>
  </div>`;
}

function bindKpBuild(){
  const b = $('#kp-build-open'); if (!b) return;
  b.onclick = openKpBuild;
  // 进这一页时若已有任务在跑（或刚跑完），把进度接上，别让人以为没反应
  kpProbe();
}

async function kpProbe(){
  const st = $('#kp-build-state');
  // 批量（本卡）
  try {
    const b = await api('/api/kp-build/batch/progress');
    if (b && (b.active || b.status)) {
      renderKpBatch(b);
      if (b.active) watchKpBatch();
      if (st && b.active) {
        st.textContent = `批量整理进行中：完成 ${b.n_done}/${b.n_total}…`;
      }
    }
  } catch(e){}
  // 单份（卡 C-0035 的后台线程）：与批量互斥，但页面刷新后要把状态接回来
  try {
    const p = await api('/api/kp-build/progress');
    if (p && (p.active || p.status)) {
      renderKpProgress(p);
      if (p.active) watchKpProgress();
      if (st && p.active) st.textContent = '有一个单份整理任务正在跑…';
    }
  } catch(e){}
}

// 状态 → 徽章文案/颜色（与后端 kp_batch 的状态机一一对应）
const KPB_ST = {
  waiting:   ['等待',   ''],
  running:   ['进行中', 'warn'],
  done:      ['完成',   'ok'],
  partial:   ['部分完成', 'warn'],
  remote_unknown: ['结果未知', 'warn'],
  failed:    ['失败',   'bad'],
  cancelled: ['已取消', 'warn'],
  skipped:   ['跳过',   ''],
};

function kpbTaskRow(t){
  const st = KPB_ST[t.status] || [t.status, ''];
  const badge = `<span class="badge ${st[1]}">${st[0]}</span>`;
  const prog = t.status === 'running'
    ? `<span class="muted">${esc(t.stage || '')}${
        t.kp_total ? ` · 第 ${Math.min((t.kp_done||0)+1, t.kp_total)}/${t.kp_total} 条` : ''}</span>`
    : (t.status === 'done'
        ? `<span class="muted">${t.kp_actual || t.kp_done || 0} 条 · 覆盖 ${t.pages_covered || 0} 页 · ${
            t.status_note ? esc(t.status_note) + ' · ' : ''}CNY${(t.cost_cny||0).toFixed(4)} · ${
            _fmtDur(t.elapsed)}</span>`
        : (t.status === 'waiting'
            ? '<span class="muted">排队等待中…</span>'
            : `<span class="muted">${esc(t.status_note || '')}</span>`));
  const err = (t.status === 'failed')
    ? `<div class="small" style="color:var(--bad);margin-top:2px">原因：${esc(t.error||'未知')}</div>` : '';
  const cur = (t.status === 'running' || t.status === 'done')
    ? `<div class="pbar${t.status === 'running' ? ' active' : ''}" style="height:6px;margin-top:5px">
         <i style="width:${t.percent||0}%"></i></div>` : '';
  return `<div style="padding:7px 2px;border-bottom:1px solid #232833">
    <div class="row" style="gap:8px;align-items:baseline;flex-wrap:wrap">
      ${badge}
      <b style="flex:1;min-width:0">${esc(t.title || ('doc#' + t.doc_id))}</b>
      <span class="small muted">${t.n_pages} 页 / 末级 ${t.n_leaf_pages}</span>
      ${prog}
    </div>${err}${cur}
    ${(t.log_tail && t.log_tail.length && t.status === 'running')
      ? `<div class="small muted" style="margin-top:3px;white-space:pre-wrap">${
          esc(t.log_tail[t.log_tail.length-1])}</div>` : ''}
  </div>`;
}

function kpbRetryHtml(p){
  const fails = (p.tasks||[]).filter(t => t.status === 'failed');
  if (!fails.length || p.active) return '';
  return `<div class="row" style="margin-top:10px">
    <button class="act" id="kpb-retry">重试失败的 ${fails.length} 份</button>
    <span class="small muted">重试只跑这几份，已完成的份不受影响</span>
  </div>`;
}

function renderKpBatch(p){
  const box = $('#kp-batch-progress');
  if (!box) return;
  if (!p || (!p.active && !p.status)) { box.innerHTML = ''; return; }
  const pv = p.percent || 0;
  const tail = p.active
    ? `<span class="muted">已用 ${_fmtDur(p.elapsed)}</span>`
    : (p.status === 'done'
        ? `<span class="badge ok">完成</span><span class="muted">用时 ${_fmtDur(p.elapsed)}</span>`
        : (p.status === 'cancelled'
            ? `<span class="badge warn">已取消</span>`
            : (p.status === 'partial'
                ? `<span class="badge warn">部分完成</span><span class="muted">有效结果已保留，未完成部分可续跑${p.error ? '：' + esc(p.error) : ''}</span>`
                : (p.status === 'remote_unknown'
                    ? `<span class="badge warn">结果未知</span><span class="muted">请求可能已发出，本次不会自动重发；请人工核对后再操作${p.error ? '：' + esc(p.error) : ''}</span>`
                    : `<span class="badge bad">有失败</span><span class="muted">${esc(p.error||'')}</span>`))));
  const rows = (p.tasks||[]).map(kpbTaskRow).join('');
  const stBtn = p.active
    ? `<button class="ghost" id="kpb-cancel" style="margin-left:8px">取消未开始的份</button>` : '';
  box.innerHTML = `
    <div class="pwrap">
      <div class="phead">
        <b>${esc(p.label || '批量整理知识点')}</b>
        <span class="muted">完成 ${p.n_done||0}/${p.n_total||0}${
          p.n_failed ? ` · 失败 ${p.n_failed}` : ''}${
          p.n_running ? ` · 进行中 ${p.n_running}` : ''}${
          p.n_waiting ? ` · 等待 ${p.n_waiting}` : ''}</span>
        <span style="flex:1"></span>
        <span class="muted">并发 ${p.max_workers} · 已花 CNY${(p.cost_cny||0).toFixed(4)}${
          p.est_cost_cny ? ` / 预估上限 CNY${p.est_cost_cny.toFixed(4)}` : ''}</span>${tail}${stBtn}
      </div>
      <div class="pbar${p.active ? ' active' : ''}"><i style="width:${pv}%"></i></div>
      <div class="pfoot">
        <span class="muted">总体进度</span><span style="flex:1"></span><b>${pv}%</b>
      </div>
      <div style="margin-top:8px">${rows}</div>
      ${kpbRetryHtml(p)}
    </div>`;
  const rb = $('#kpb-retry'); if (rb) rb.onclick = () => kpbRetryFailed(p);
  const cb = $('#kpb-cancel');
  if (cb) cb.onclick = async () => {
    try { const r = await api('/api/kp-build/batch/cancel', {method:'POST',
      headers:{'Content-Type':'application/json'}, body:'{}'});
      toast(r.cancelled ? `已请求取消（未开始的 ${r.pending} 份作废）` : (r.error||'取消失败'));
    } catch(e){ toast('取消失败：' + e.message); }
  };
}

function kpbRetryFailed(p){
  const ids = (p.tasks||[]).filter(t => t.status === 'failed').map(t => t.doc_id);
  if (!ids.length) return;
  kpbSubmit(ids, {force:false});
}

function watchKpBatch(){
  if (KPB.batchTimer) return;
  const tick = async () => {
    try {
      const p = await api('/api/kp-build/batch/progress');
      renderKpBatch(p);
      if (!p.active) {
        clearInterval(KPB.batchTimer); KPB.batchTimer = null;
        const st = $('#kp-build-state'); if (st) st.textContent = '';
        if (p.status === 'done') {
          toast(`批量整理完成：${p.n_done}/${p.n_total} 份 · 共 ${p.tasks.reduce(
            (a,t)=>a+(t.kp_actual||0),0)} 条 · 花费 CNY${(p.cost_cny||0).toFixed(4)}`);
        } else if (p.n_failed) {
          toast(`批量整理结束：${p.n_failed} 份失败，可在进度条里点「重试失败的 N 份」`);
        }
        // 跑完刷新清单缓存（已有总结/可续跑的状态变了）
        try { await api('/api/kp-build/batch/options'); } catch(e){}
        const box = $('#kp-batch-progress');
        if (box) setTimeout(() => { box.innerHTML = ''; }, 12000);
      }
    } catch(e) {
      clearInterval(KPB.batchTimer); KPB.batchTimer = null;
    }
  };
  tick();
  KPB.batchTimer = setInterval(tick, 1500);
}

// ---------------- 批量整理：选择弹窗 ----------------
function kpbRow(x){
  const checked = KPB.sel.has(x.doc_id) ? 'checked' : '';
  // 卡 C-0050：产物状态由后端按 `meta.pipeline_version` 判（none / legacy /
  // incomplete / v2）——旧格式在页签上**不显示**了，这里必须说得出原因。
  const ast = x.artifact_state || 'none';
  const tag = x.already_done
    ? '<span class="badge ok">已完成</span>'
    : ((ast === 'legacy' || ast === 'incomplete')
        ? '<span class="badge warn">旧格式，需重新整理</span>'
        : (ast === 'v2' ? '<span class="badge warn">未完成（可续跑）</span>'
                        : '<span class="badge">未整理</span>'));
  const note = x.already_done
    ? `<span style="color:var(--ok)">${esc(x.done_note||'')}</span>`
    : (x.has_summary
        ? `<span style="color:var(--warn)">${esc(x.done_note||'')}</span>`
        : '<span class="muted">还没有总结</span>');
  return `<label class="row" style="gap:10px;align-items:flex-start;padding:7px 10px;
      border-bottom:1px solid #232833;cursor:pointer">
    <input type="checkbox" class="kpb-cb" data-doc="${x.doc_id}" ${checked} style="margin-top:4px">
    <span style="flex:1;min-width:0">
      <b>${esc(x.title)}</b>
      <span class="small muted">doc#${x.doc_id} · ${x.n_pages} 页 / 末级 ${x.n_leaf_pages}${
        x.in_library ? '' : ' · ⚠ 已不在库'}</span> ${tag}
      <div class="small muted">预计 ${x.kp_min}~${x.kp_max} 条 · 预估 CNY${
        (x.est_cost_cny||0).toFixed(4)}（上界，按每页一条算 ${x.est_calls} 次调用）· ${note}</div>
    </span>
  </label>`;
}

function kpbPaintList(){
  const list = $('#kpb-list'); if (!list) return;
  const onlyUndone = !!($('#kpb-undone') && $('#kpb-undone').checked);
  const rows = KPB.docs.filter(x => !onlyUndone || !x.has_summary);
  list.innerHTML = rows.length ? rows.map(kpbRow).join('')
    : '<div class="small muted" style="padding:12px">没有符合条件的课件。</div>';
  list.querySelectorAll('.kpb-cb').forEach(cb => {
    cb.onchange = () => {
      const id = parseInt(cb.dataset.doc);
      if (cb.checked) KPB.sel.add(id); else KPB.sel.delete(id);
      kpbPaintSum();
    };
  });
}

function kpbSelDocs(){
  return KPB.docs.filter(x => KPB.sel.has(x.doc_id));
}

function kpbPaintSum(){
  const el = $('#kpb-sum'); if (!el) return;
  const sel = kpbSelDocs();
  const skipDone = !!($('#kpb-skip') && $('#kpb-skip').checked);
  const force = !!($('#kpb-force') && $('#kpb-force').checked);
  const willRun = sel.filter(x => !(skipDone && x.already_done && !force));
  const skipped = sel.length - willRun.length;
  const est = willRun.reduce((a,x)=>a + (x.est_cost_cny||0), 0);
  const kmin = willRun.reduce((a,x)=>a + (x.kp_min||0), 0);
  const kmax = willRun.reduce((a,x)=>a + (x.kp_max||0), 0);
  const thr = KPB.thr;
  const over = est > thr;
  el.innerHTML = `<div class="card" style="background:var(--panel2)"><div class="small muted"
      style="line-height:1.95">
    已选 <b>${sel.length}</b> 份${
      skipped ? `，其中 <b>${skipped}</b> 份已完成会被跳过（不花钱）` : ''} ·
    实际要跑 <b>${willRun.length}</b> 份<br>
    预计条数：<b>${kmin} ~ ${kmax} 条</b>（合并得越多条数越少）·
    预估花费合计：<b>CNY${est.toFixed(4)}</b>
    <span class="muted">（上界：按每页一条算；合并掉的页不单独调用，真实只会更少）</span>
    ${over ? `<br><span style="color:var(--warn)">⚠ 合计超过 CNY${thr.toFixed(2)} 阈值，
      点「开始批量整理」会再弹一次确认</span>` : ''}
    <br><span class="muted">整理结果会保存在本机，点节点即可查看</span>
  </div></div>`;
}

async function openKpBuild(){
  modal(`<h2>批量整理知识点</h2>
    <div id="kpb-body"><div class="small muted">正在读可整理的课件…</div></div>
    <div class="row" style="margin-top:16px">
      <button class="ghost" onclick="closeModal()">关闭</button></div>`);
  let d;
  try { d = await api('/api/kp-build/batch/options'); }
  catch(e){
    $('#kpb-body').innerHTML =
      `<div class="small" style="color:var(--bad)">读取失败：${esc(e.message)}</div>`;
    return;
  }
  if (d.batch_running || d.single_running) {
    $('#kpb-body').innerHTML = `<div class="small" style="color:var(--warn)">
      ${d.batch_running ? '已有一个<b>批量</b>整理任务在跑' : '已有一个<b>单份</b>整理任务在跑'}
      （同一时间只允许一个）。关掉本窗口就能看到进度条；等它结束后再来。</div>`;
    return;
  }
  KPB.docs = (d.documents || []).filter(x => x.n_leaf_pages);
  KPB.thr = parseFloat(d.confirm_threshold_cny || 2) || 2;
  KPB.sel = new Set();
  if (!KPB.docs.length){
    $('#kpb-body').innerHTML = `<div class="small muted">
      没有可整理的课件：先入库课件，再回来整理。整理只读库里的文本，
      不需要入库导图。</div>`;
    return;
  }
  const wmax = d.max_workers_max || 4;
  const wdef = d.max_workers_default || 2;
  let wopts = '';
  for (let n = 1; n <= wmax; n++) {
    wopts += `<option value="${n}"${n === wdef ? ' selected' : ''}>${n}</option>`;
  }
  $('#kpb-body').innerHTML = `
    <div class="row" style="margin-top:10px;gap:14px;flex-wrap:wrap">
      <label class="row" style="gap:6px"><input type="checkbox" id="kpb-undone">
        只看未整理过的</label>
      <label class="row" style="gap:6px"><input type="checkbox" id="kpb-skip" checked>
        跳过已完成（不重复花钱）</label>
      <label class="row" style="gap:6px"><input type="checkbox" id="kpb-force">
        强制重跑（旧产物先自动备份）</label>
      <label class="row" style="gap:6px">并发数
        <select id="kpb-workers">${wopts}</select></label>
      <label class="row" style="gap:6px">每份花费上限 CNY
        <input id="kpb-cost" type="number" step="0.1" min="0.1" value="0.8" style="width:84px"></label>
    </div>
    <div class="row" style="margin-top:8px">
      <button class="ghost" id="kpb-all">全选（当前列表）</button>
      <button class="ghost" id="kpb-none">全不选</button>
      <span class="small muted">并发越高越快，但更容易撞上模型限流；建议 2。</span>
    </div>
    <div id="kpb-list" style="margin-top:10px;max-height:44vh;overflow:auto;
      border:1px solid #2a2f3a;border-radius:8px"></div>
    <div id="kpb-sum" style="margin-top:10px"></div>
    <div class="row" style="margin-top:12px">
      <button class="act" id="kpb-go">开始批量整理</button>
      <button class="ghost" id="kpb-clear"
        title="删除选中课件的整理产物与备份（不可逆，会二次确认）">
        清除选中课件的整理产物</button>
      <button class="ghost" id="kpb-purge"
        title="真删选中课件：库记录 + 切块 + 向量 + 知识点出处 + 四处产物，可选连 raw\ 原文件一起删（不可逆，会二次确认）"
        style="color:var(--bad);border-color:#4a1f1c">
        真删选中课件…</button>
      <span class="small muted" id="kpb-tip">${esc(d.est_rule || '')}</span>
    </div>`;
  $('#kpb-undone').onchange = kpbPaintList;
  $('#kpb-skip').onchange = kpbPaintSum;
  $('#kpb-force').onchange = kpbPaintSum;
  $('#kpb-all').onclick = () => {
    KPB.docs.forEach(x => {
      const onlyUndone = !!($('#kpb-undone') && $('#kpb-undone').checked);
      if (!onlyUndone || !x.has_summary) KPB.sel.add(x.doc_id);
    });
    kpbPaintList(); kpbPaintSum();
  };
  $('#kpb-none').onclick = () => { KPB.sel.clear(); kpbPaintList(); kpbPaintSum(); };
  // 卡 C-0050：清除选中课件的整理产物（不可逆；二次确认与确认框在 kpbClear 里）
  const kc = $('#kpb-clear');
  if (kc) kc.onclick = () => kpbClear();
  // 卡 C-0084：真删选中课件（库 + 产物 + 可选原文件）。复用资料页那套预览/确认。
  const kp = $('#kpb-purge');
  if (kp) kp.onclick = () => kpbPurge();
  $('#kpb-go').onclick = () => kpbSubmit(Array.from(KPB.sel), {
    workers: parseInt(($('#kpb-workers')||{}).value || '2') || 2,
    maxCost: parseFloat(($('#kpb-cost')||{}).value || '0.8') || 0.8,
    skipDone: !!($('#kpb-skip') && $('#kpb-skip').checked),
    force: !!($('#kpb-force') && $('#kpb-force').checked),
  });
  kpbPaintList();
  kpbPaintSum();
}

// 「清除」（卡 C-0050，人拍板）：把选中课件的整理产物（思维导图结构 + 理解 +
// 要点）与备份删掉，让它们回到"未整理"——**必须重新整理才能重建**。
// 两道闸门：这里的 window.confirm + 后端的 confirm=true。不可逆的操作只靠前端
// 一个对话框把关是不够的（接口能被直接调用）。
// 只删 logs/knowledge/<doc_id>-末级知识点总结.json 与它的 .bak-*：
// logs/outline/ 与数据库任何一张表都不动（后端 `api_kp_clear` 里逐条校验）。
async function kpbClear(){
  const sel = kpbSelDocs();
  if (!sel.length){ toast('请先勾选至少一份课件'); return; }
  const ids = sel.map(x => x.doc_id);
  const lines = sel.slice(0, 8).map(x => '· ' + x.title).join('\n');
  const more = sel.length > 8 ? `\n… 另有 ${sel.length - 8} 份` : '';
  const ok = window.confirm(
    `将清除这 ${sel.length} 份课件的整理产物（思维导图结构 + 理解 + 要点）及其备份：\n` +
    lines + more + '\n\n' +
    '⚠ 此操作不可逆：清除后页签回到"未整理"，必须重新整理才能重建。\n' +
    '不会动入库结构，也不会写数据库任何一张表。\n\n确定清除？');
  if (!ok) return;                       // 二次确认：点"取消"就什么都不做
  const btn = $('#kpb-clear');
  if (btn){ btn.disabled = true; btn.textContent = '清除中…'; }
  try {
    const r = await api('/api/kp-build/clear', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({doc_ids: ids, confirm: true})});
    const nf = (r.not_found || []).length;
    const errs = (r.errors || []).length;
    toast(`已清除 ${r.n_deleted} 个文件（${(r.deleted||[]).length} 份课件）` +
          `${nf ? ` · ${nf} 份本来就没有产物` : ''}${errs ? ` · ${errs} 项失败` : ''}`);
    // 清单里的"已完成/旧格式"状态变了：重取一次，并清空选中集合
    KPB.sel = new Set();
    try {
      const d = await api('/api/kp-build/batch/options');
      KPB.docs = (d.documents || []).filter(x => x.n_leaf_pages);
    } catch(e){}
    kpbPaintList(); kpbPaintSum();
    // 页签那边当场刷新：人要看的就是"清完这几份变成未整理了"。
    // 用 `mountMindmap()`（重取清单 + 重绘下拉 + 重画树）而不是只刷树——
    // 清除会改掉"页数 / 节点数"，下拉项里那份摘要也得跟着变。
    if (typeof mountMindmap === 'function' && $('#mm-tree')) mountMindmap();
  } catch(e){
    toast('清除失败：' + e.message);
  } finally {
    if (btn){ btn.disabled = false; btn.textContent = '清除选中课件的整理产物'; }
  }
}

// 「真删选中课件」（卡 C-0084）：这一栏的清单来自**整理产物**，里面可能有
// 「已不在库」的幽灵条目，所以这里先如实报一句"其中几份只剩产物"，再**复用
// 资料页那一套确认面板**（openDocDelByIds → /api/docs/delete）——同一个动作
// 在页面里只有一套预览、一套文案、两道闸门，不会长出两个行为不一致的实现。
async function kpbPurge(){
  const sel = kpbSelDocs();
  if (!sel.length){ toast('请先勾选至少一份课件'); return; }
  const ids = sel.map(x => x.doc_id);
  const ghosts = sel.filter(x => x.in_library === false);
  if (ghosts.length){
    toast(`注意：选中的 ${sel.length} 份里有 ${ghosts.length} 份已不在库（只剩产物），`
        + '它们只会被清掉产物文件');
  }
  if (typeof closeModal === 'function') closeModal();
  // 与资料页同一套：先 dry_run 预览 → 弹窗选原文件留不留 → 再 confirm=true 真删
  return openDocDelByIds(ids);
}

async function kpbSubmit(docIds, opt){
  opt = opt || {};
  if (!docIds || !docIds.length){ toast('请先勾选至少一份课件'); return; }
  const skipDone = opt.skipDone !== false;      // 重试失败项时默认沿用"跳过已完成"
  const force = !!opt.force;
  const workers = opt.workers || 2;
  const maxCost = opt.maxCost || 0.8;
  // 花钱前的**二次确认**：口径与后端一致（只算真要跑的那几份，跳过的不计）
  const willRun = KPB.docs.filter(x => docIds.indexOf(x.doc_id) >= 0
    && !(skipDone && x.already_done && !force));
  const est = willRun.reduce((a,x) => a + (x.est_cost_cny||0), 0);
  if (est > KPB.thr){
    const ok = window.confirm(
      `预估花费合计 CNY${est.toFixed(4)}（上界），超过 CNY${KPB.thr.toFixed(2)} 阈值。\n` +
      `将整理 ${willRun.length} 份课件，实际花费通常低于上界（合并后调用更少）。\n\n确定开始？`);
    if (!ok) return;
  }
  const btn = $('#kpb-go');
  if (btn){ btn.disabled = true; btn.textContent = '启动中…'; }
  try {
    const r = await api('/api/kp-build/batch', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({doc_ids: docIds, max_workers: workers, max_cost: maxCost,
                            skip_done: skipDone, force: force, confirm: est > KPB.thr})});
    closeModal();
    go('kps');
    toast(`已开始批量整理：${r.n_run} 份 · 并发 ${r.max_workers}` +
          `${r.n_skip ? `（跳过已完成 ${r.n_skip} 份）` : ''}`);
    watchKpBatch();
  } catch(e){
    toast('启动失败：' + e.message);
    if (btn){ btn.disabled = false; btn.textContent = '开始批量整理'; }
  }
}

// ---------------- 单份整理：进度（卡 C-0035，保留兼容） ----------------
function renderKpProgress(p){
  renderProgress(p, '#kp-build-progress');
}

async function pollKpProgress(){
  try {
    const p = await api('/api/kp-build/progress');
    renderKpProgress(p);
    if (p.active) return true;
    const box = $('#kp-build-progress');
    if (p.status === 'done') {
      toast(`整理完成：${p.kp_done || 0} 条 · 花费 CNY${(p.cost_cny || 0).toFixed(4)}｜` +
            '去「资料」页 → 课件思维导图 → 点节点看总结');
      if (box) setTimeout(() => { box.innerHTML = ''; }, 6000);
    } else if (p.status === 'failed') {
      toast('整理失败：' + (p.error || '未知原因'));
    }
    return false;
  } catch(e){ return false; }
}

function watchKpProgress(){
  if (KPB.timer) return;
  const tick = async () => {
    const still = await pollKpProgress();
    if (!still) { clearInterval(KPB.timer); KPB.timer = null; }
  };
  tick();
  KPB.timer = setInterval(tick, 1500);
}

// ---------------- ★ 卡 C-0053：名词解释栏（在「课件思维导图」栏**上面**） ----------------
// 人拍板（2026-09-27）：「新增一个"名词解释"栏，专门收集知识点整理的 AI 提炼出的
// 名词解释。这一栏应该在思维导图栏上面，有一个选择课件的部分，选好之后展开就能
// 展示该课件的名词解释。点击名词还能跳出该名词解释第一次出现的知识点。」
//
// 数据全部来自只读接口 `/api/terms`（卡 C-0058 起取值顺序：**独立名词解释产物优先**，
// 读不到再回落读知识点产物的顶层 `terms`；**不调模型**、不写库）：
// 名词 `term` / 英文 `en` / 中文解释 `def` /
// **首次出现的知识点** `first_kp_id`+`first_kp_name` / 首次出现的页 `first_page`。
// 点名词 → `mmOpenKp(first_kp_id, …, this, false, TERMS.docId)`：**复用现有知识点
// 弹窗**（K5 那套，一行没改）。`docId` **必须显式传**——这一栏的下拉与思维导图栏
// 的下拉各自独立，用缺省值会去思维导图栏选的那份课件里找知识点。
//
// ⚠ 卡 C-0058：名词解释**已从知识点产物摘出**（人拍板 2026-09-29「取消知识点总结管线与
//   名词解释功能的偶联」）——它现在是独立功能（`tools/terms_build.py --doc <id>`），
//   产物是 `logs/knowledge/<doc_id>-名词解释.json`。后端 `/api/terms` 的取值顺序是
//   **独立产物优先 → 旧产物顶层 `terms` 回落**，两种情况都归一成同一个 `terms` 数组，
//   所以这一栏的渲染逻辑**一行没改**；新增的 `terms_source` / `terms_pipeline_version`
//   只用来把"这份名词是哪来的"说清楚。
// ⚠ 旧产物兼容（硬要求）：C-0051 之前的产物（`C-0049-v2`）**没有 terms**，
//   后端给 `terms_state` + 一句人话（`message`），这里原样显示成空态——
//   **不是报错、不是空白**（18 份 v2 产物逐份试过，见验证脚本）。
// ⚠ 课件清单与思维导图栏**同源**（后端两个接口都走 `_artifact_doc_list`）。
const TERMS = {docId: 0, open: true, n: 0, data: null};

function termsCard(){
  return `<div class="card" style="margin-top:14px">
    <h2>名词解释</h2>
    <div class="small muted" style="margin-bottom:10px">选择课件，查看其中的重点名词。</div>
    <div class="row" style="margin-bottom:10px">
      <select id="term-doc" style="flex:1;min-width:260px"></select>
      <button class="text-btn" id="term-unfold">展开</button>
    </div>
    <div id="term-preview" class="small muted" style="margin:8px 0"></div>
    <div id="term-box"><div class="small muted">加载中…</div></div>
  </div>`;
}

// 列表正文。三种状态各自说得清：有名词（展开/收起）、空态（后端给的 `message`）、
// 加载中/读取失败（由调用方直接写 #term-box）。
function termsBodyHtml(d){
  const ts = (d && d.terms) || [];
  if (!d || d.terms_state !== 'ok' || !ts.length){
    return `<div class="empty">这份课件还没有名词解释。</div>`;
  }
  if (!TERMS.open){
    return `<div class="small muted">共 <b>${ts.length}</b> 个名词解释（已收起）——
      点上面的【展开】查看。</div>`;
  }
  const rows = ts.map(t => {
    const kp = t.first_kp_id || '';
    const name = t.first_kp_name || t.term || kp;
    return `<li style="margin:0 0 10px;padding:10px 12px;
        border:1px solid var(--line);border-radius:var(--radius)">
      <div>
        <a href="#" class="termname" data-kp="${esc(kp)}" data-kpname="${esc(name)}"
          title="点开「${esc(name)}」（${esc(kp || '—')}）——这个名词第一次出现的知识点"
          style="font-weight:600;text-decoration:none">${esc(t.term)}</a>${
        t.en ? `<span class="termen" style="margin-left:10px;color:var(--accent);
            opacity:.9">${esc(t.en)}</span>`
          : `<span class="small muted" style="margin-left:10px">（这份材料没给英文）</span>`}
      </div>
      <div class="termdef" style="margin-top:6px;line-height:1.65">${
        esc(t.def || '（这条没有解释）')}</div>
      <div class="small muted" style="margin-top:6px">
        首次出现 p.${t.first_page || '?'}${
        t.first_kp_name ? ' · 知识点 ' + esc(t.first_kp_name) : ''}${
        (t.kp_ids || []).length > 1
          ? ` · 共出现在 ${t.kp_ids.length} 条知识点里` : ''}
      </div>
    </li>`;
  }).join('');
  return `<div class="small muted" style="margin-bottom:8px">
      共 <b>${ts.length}</b> 个名词解释${
      d.title ? ' · ' + esc(d.title) : ''}（点名词跳到它第一次出现的知识点）</div>
    <ul style="list-style:none;padding:0;margin:0">${rows}</ul>`;
}

function renderTerms(){
  const box = $('#term-box'); if (!box) return;
  const d = TERMS.data || {};
  box.innerHTML = termsBodyHtml(d);
  const n = (d.terms_state === 'ok') ? ((d.terms || []).length) : 0;
  const btn = $('#term-unfold');
  if (btn){
    btn.textContent = n ? (TERMS.open ? '收起' : `展开（${n} 个名词）`) : '展开';
    btn.disabled = !n;
    btn.title = n ? '' : (d.message || '该课件暂无名词解释');
  }
  // 点名词 → 现有知识点弹窗（mmKp*）：把这条名词的**首次出现知识点 id** 传进去。
  // 用 dataset + 逐个绑定（节点名里可能有引号，不塞内联 onclick——与导图同一思路）。
  box.querySelectorAll('.termname').forEach(el => {
    el.onclick = (ev) => {
      ev.preventDefault();
      mmOpenKp(el.dataset.kp, el.dataset.kpname, el, false, TERMS.docId);
    };
  });
}

async function loadTerms(){
  const box = $('#term-box'); if (!box) return;
  box.innerHTML = '<div class="small muted">加载中…</div>';
  let d;
  try { d = await api('/api/terms?doc_id=' + TERMS.docId); }
  catch(e){
    box.innerHTML = `<div class="empty">名词解释读取失败：${esc(e.message)}</div>`;
    return;
  }
  TERMS.data = d;
  TERMS.n = (d.terms || []).length;
  renderTerms();
  try { await refreshTermsPreview(); } catch(e){ /* 预览状态读不到不影响名词列表 */ }
}

async function mountTerms(){
  const box = $('#term-box'); if (!box) return;
  const btn = $('#term-unfold');
  if (btn) btn.onclick = () => { TERMS.open = !TERMS.open; renderTerms(); };
  let docs = [];
  try { docs = (await api('/api/terms')).documents || []; }
  catch(e){
    box.innerHTML = `<div class="empty">名词解释清单读取失败：${esc(e.message)}</div>`;
    return;
  }
  if (!docs.length){
    box.innerHTML = `<div class="empty">知识库里还没有课件。<br>
      <span class="small">先去「资料」页入库课件并整理知识点，再回来这里看名词解释。</span></div>`;
    if (btn) btn.disabled = true;
    return;
  }
  // 默认选中：**优先挑一份真有名词的**（人进这一栏就是想看名词解释）；
  // 已经选过且还在清单里就保持不变（换了页签回来不该跳走）。
  if (!docs.some(d => d.doc_id === TERMS.docId)){
    TERMS.docId = (docs.find(d => (d.n_terms || 0) > 0) || docs[0]).doc_id;
  }
  const sel = $('#term-doc');
  if (sel){
    sel.innerHTML = docs.map(d => {
      const n = d.n_terms || 0;
      const tail = n > 0 ? ` · ${n} 个名词`
        : (d.terms_state === 'stale' ? ' · 未整理'
          : (d.terms_state === 'legacy' ? ' · 旧格式产物' : ' · 暂无名词解释'));
      return `<option value="${d.doc_id}" ${
        d.doc_id === TERMS.docId ? 'selected' : ''}>${esc(d.title)}${tail}${
        d.in_library ? '' : '（已不在库）'}</option>`;
    }).join('');
    sel.onchange = e => {
      TERMS.docId = parseInt(e.target.value) || 0;
      loadTerms();          // 换课件**不重置展开状态**：人刚点开，别给他收回去
    };
  }
  await loadTerms();
}

// ---------------- 课件思维导图 ----------------
// 数据来自入库时 AI 全局结构分析的留存（`pages[页码] = 该页的完整路径`），
// 后端 /api/mindmap 把各页路径按前缀合并成层级树，并给节点内联**末级知识点
// 标记**（lkp_id / lkp_ready / lkp_role）——新方案（卡 C-0032）：每个末级节点
// 一个知识点，内容是"该节点覆盖页的正文 + 图片理解"经模型提炼的**总结**。
// 折叠用原生 <details>，点节点名**就地弹小窗看总结**（复用 evPop 那套），
// 不引入任何外部 CDN / 第三方图表库（本项目离线优先，见 README.md）。
//
// 展开状态与选中的文档存在 MINDMAP 里：点节点看知识点后关掉小窗，
// 树不该被重置成"全折叠 + 回到第一份文档"。
// nodes 是"下标路径 → 节点对象"的映射，点名字时按 data-k 回查：
// 节点名里可能有引号/尖括号，不适合塞进 DOM 属性或内联 onclick。
// downshift：本份产物是不是卡 C-0045 的"父节点页下放"口径（由 /api/mindmap 给），
// 只影响父节点页的提示话术——旧产物为 false，说法与改动前一致。
// docs（卡 C-0061）：最近一次 `/api/mindmap` 拿到的课件清单，按钮状态按它画，
// 录入成功后也就地更新这一份，不必重新拉整张清单。
// pendingDoc（卡 C-0061）：从复习页签的月历点「打开课件」跳进来时，要优先打开
// **那一份**课件的思维导图；mountMindmap 取完就置 0（不然下次进页签又被抢一次）。
const MINDMAP = {docId: 0, open: {}, nodes: {}, downshift: false,
                 docs: [], pendingDoc: 0, mode: 'courseware'};

function mindmapCard(){
  return `<div class="card" style="margin-top:14px">
    <div class="row kb-map-head"><h2>学习导图</h2>
      <div class="kb-map-tabs" role="group" aria-label="选择学习内容">
        <button class="ghost ${MINDMAP.mode==='courseware'?'on':''}" data-map-mode="courseware">课件</button>
        <button class="ghost ${MINDMAP.mode==='textbook'?'on':''}" data-map-mode="textbook">教材</button>
      </div></div>
    <div id="mm-course-panel" ${MINDMAP.mode==='textbook'?'hidden':''}>
    <div class="small muted" style="margin-bottom:10px">点击节点查看要点和原文出处。</div>
    <div id="mm-box"><div class="small muted">加载中…</div></div>
    <!-- 卡 C-0052：右下角「AI答疑」按钮。刻意放在 #mm-box **外面**——
         mountMindmap 每次进页签都会重写 #mm-box 的内容，放里面会被连根换掉。
         卡 C-0061：「录入遗忘曲线」同理，放在「AI答疑」**左边**（同一行、同款 .act）。 -->
    <div class="row" style="margin-top:12px;justify-content:flex-end">
      <span class="small muted" id="mm-enroll-hint"></span>
      <button class="ghost" id="mm-enroll">加入复习日历</button>
      <button class="act" id="mm-ai">课件问答</button>
    </div>
    </div>
    <div id="book-kp-panel" ${MINDMAP.mode==='textbook'?'':'hidden'}></div>
  </div>`;
}

async function mountMindmap(){
  document.querySelectorAll('[data-map-mode]').forEach(b => {
    b.onclick = () => setMindmapMode(b.dataset.mapMode);
  });
  if (MINDMAP.mode === 'textbook'){
    await mountTextbookKnowledge();
    return;
  }
  const box = $('#mm-box'); if (!box) return;
  // 卡 C-0052：「AI答疑」按钮的接线放在**早退之前**——没课件时点它也要给一句
  // 明白话（"先选一份课件"），而不是点了没反应。onclick 是赋值，重复进页签不会叠加。
  const aiBtn = $('#mm-ai');
  if (aiBtn) aiBtn.onclick = openDeckChat;
  // 卡 C-0061：「录入遗忘曲线」的接线同样放在早退之前，理由与上面一句相同。
  const enBtn = $('#mm-enroll');
  if (enBtn) enBtn.onclick = deckEnrollClick;
  let docs = [];
  try { docs = (await api('/api/mindmap')).documents || []; }
  catch(e){ box.innerHTML = `<div class="empty">课件清单读取失败：${esc(e.message)}</div>`; return; }
  if (!docs.length){
    box.innerHTML = `<div class="empty">知识库里还没有课件。<br>
      <span class="small">先去「资料」页添加并入库课件。</span></div>`;
    return;
  }
  MINDMAP.docs = docs;          // 卡 C-0061：按钮状态的数据源（就地更新，不重拉）
  // 卡 C-0061：从复习页签的月历点「打开课件」跳进来 → 优先打开**那一份**（取完清 0）；
  // 其次才是老逻辑"当前选中不在清单里就取第一份"。
  const pend = MINDMAP.pendingDoc;
  MINDMAP.pendingDoc = 0;
  if (pend && docs.some(d => d.doc_id === pend)) MINDMAP.docId = pend;
  else if (!docs.some(d => d.doc_id === MINDMAP.docId)) MINDMAP.docId = docs[0].doc_id;
  mmEnrollPaint(docs.find(d => d.doc_id === MINDMAP.docId));
  // 下拉项里那份摘要按**产物状态**说：有 v2 产物才谈页数/节点数，
  // 否则写"未整理 / 旧格式，需重新整理"——显示"0 页 / 0 节点"会让人以为课件空了。
  const mmOpt = d => {
    const tail = d.artifact_state === 'v2'
      ? ` · 已整理`
      : (d.artifact_state === 'legacy' ? ' · 旧格式，需重新整理' : ' · 未整理');
    return `<option value="${d.doc_id}" ${
      d.doc_id===MINDMAP.docId?'selected':''}>${esc(d.title)}${tail}${
      d.in_library?'':'（已不在库）'}</option>`;
  };
  box.innerHTML = `
    <div class="row" style="margin-bottom:10px">
      <select id="mm-doc" style="flex:1;min-width:260px">
        ${docs.map(mmOpt).join('')}
      </select>
      <details class="row-actions"><summary aria-label="更多思维导图操作" title="更多操作">⋯</summary>
        <div class="row-actions-menu">
          <button class="ghost" id="mm-ren-deck" title="更改资料标题">修改资料标题</button>
          <button class="ghost" id="mm-ren-art" title="更改思维导图标题">修改导图标题</button>
          <button class="ghost" id="mm-expand">展开全部</button>
          <button class="ghost" id="mm-collapse">折叠全部</button>
        </div></details>
    </div>
    <div id="mm-tree"><div class="small muted">加载中…</div></div>`;
  // 卡 C-0085：两个改名按钮都作用于**当前选中的这份课件**（MINDMAP.docId）——
  // 与下拉框、展开/折叠同一屏，不需要先跳到资料页。
  const rd = $('#mm-ren-deck');
  if (rd) rd.onclick = () => {
    const it = (MINDMAP.docs || []).find(x => x.doc_id === MINDMAP.docId);
    if (!MINDMAP.docId) return toast('先在上面选一份课件');
    renameDocTitle(MINDMAP.docId, (it && it.title) || '');
  };
  const ra = $('#mm-ren-art');
  if (ra) ra.onclick = () => {
    if (!MINDMAP.docId) return toast('先在上面选一份课件');
    renameArtifactTitle(MINDMAP.docId, '');
  };
  $('#mm-doc').onchange = e => {
    MINDMAP.docId = parseInt(e.target.value) || 0;
    MINDMAP.open = {};   // 换了文档，旧的展开状态没有意义
    mmEnrollPaint(docs.find(d => d.doc_id === MINDMAP.docId));   // 卡 C-0061
    loadMindmapTree();
  };
  $('#mm-expand').onclick = () => mmToggleAll(true);
  $('#mm-collapse').onclick = () => mmToggleAll(false);
  await loadMindmapTree();
}

function setMindmapMode(mode){
  if (mode !== 'courseware' && mode !== 'textbook') return;
  MINDMAP.mode = mode;
  const course = $('#mm-course-panel'), book = $('#book-kp-panel');
  if (!course || !book) return;
  course.hidden = mode !== 'courseware';
  book.hidden = mode !== 'textbook';
  document.querySelectorAll('[data-map-mode]').forEach(b =>
    b.classList.toggle('on', b.dataset.mapMode === mode));
  if (mode === 'textbook') mountTextbookKnowledge();
  else mountMindmap();
}

// 卡 C-0061：把「录入遗忘曲线」按钮画成**当前选中课件**的状态。
// 数据来自 `/api/mindmap` 清单里每份课件的 4 个 `deck_*` 字段（后端一次批量查完）。
// `item` 为空（清单里找不到）时按"未录入"画——宁可让人再点一次（幂等），
// 也不能因为拿不到状态就把按钮画成禁用。
function mmEnrollPaint(item){
  const btn = $('#mm-enroll'), hint = $('#mm-enroll-hint');
  if (!btn || !hint) return;
  const planId = item ? (item.deck_plan_id || 0) : 0;
  const done = item ? (item.deck_rounds_done || 0) : 0;
  const total = item ? (item.deck_rounds_total || 0) : 0;
  if (!planId){
    btn.className = 'ghost';
    btn.textContent = '加入复习日历';
    hint.textContent = '';
  } else if (total && done >= total){
    btn.className = 'ghost';
    btn.textContent = '计划已完成';
    hint.textContent = '';
  } else {
    btn.className = 'ghost';
    btn.textContent = '已加入日历';
    hint.textContent = `下次 ${(item.deck_next_due || '').slice(5) || '—'}`;
  }
}

// 点击「录入遗忘曲线」：POST /api/deck-enroll（**幂等**，见 core/study.py::enroll_deck）。
// 已录入的课件再点一次不会重建计划、不会清掉已完成的轮次，只会回一句"已录入 + 下次日期"。
async function deckEnrollClick(){
  const docId = MINDMAP.docId;
  if (!docId){ toast('先在上面选一份课件'); return; }
  try {
    const r = await api('/api/deck-enroll', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({doc_id: docId})});
    if (r.ok === false) throw new Error(r.error || '录入失败');
    if (r.already){
      toast(`${r.title}：已录入（重复点击不会重复排课）· ` +
        `已完成 ${r.rounds_done}/${r.n_rounds} 轮 · 下次 ${r.next_due || '—'}`);
    } else {
      const days = (r.days || []).map(d => d.due_day.slice(5)).join(' / ');
      toast(`${r.title}：已录入 5 轮复习 → ${days}（去「复习」页签看月历）`);
    }
    // 就地更新清单缓存 + 按钮状态：不重拉 `/api/mindmap`（那会顺带重扫产物目录）
    const item = (MINDMAP.docs || []).find(d => d.doc_id === docId);
    if (item){
      item.deck_plan_id = r.plan_id;
      item.deck_next_due = r.next_due || '';
      item.deck_rounds_done = r.rounds_done || 0;
      item.deck_rounds_total = r.n_rounds || 5;
      mmEnrollPaint(item);
    }
  } catch(e){ toast('录入失败：' + e.message); }
}

async function loadMindmapTree(){
  const t = $('#mm-tree'); if (!t) return;
  t.innerHTML = '<div class="small muted">加载中…</div>';
  let d;
  try { d = await api('/api/mindmap?doc_id=' + MINDMAP.docId); }
  catch(e){ t.innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`; return; }
  if (!d.has_structure){
    // 卡 C-0050：页签只有**整理产物**一个数据源。"没有"分三种，后端
    // `artifact_message` 已经把每种"下一步做什么"写好了，这里只补一个徽标与
    // 入口位置——照旧**不是报错**，人看到的是状态说明，不是"出错"。
    const ast = d.artifact_state || 'none';
    const astTag = ast === 'legacy' ? '旧格式，需重新整理'
      : (ast === 'incomplete' ? '产物不完整，需重新整理' : '未整理');
    t.innerHTML = `<div class="empty">
      <span class="badge warn">${esc(astTag)}</span>
      <div style="margin-top:8px">该课件还没有整理过知识点。<br>
        <span class="small">点击本页上方「选择课件」开始整理。</span></div>
      ${d.lkp_total ? `<div class="small muted" style="margin-top:8px">
        部分内容需要重新整理。</div>` : ''}</div>`;
    return;
  }
  MINDMAP.nodes = {};   // 重新渲染前清空：下标路径只在本次渲染里有效
  // 卡 C-0045 起的口径：父节点页**下放**（不单独成条，内容并入后继末级页）。
  // 旧产物没有这个键 → false → 节点提示走"暂不建"的老说法，页面显示不变。
  MINDMAP.downshift = !!d.lkp_downshift;
  // ⚠ 卡 C-0046：`lkp_total` 是**知识点条数**，`lkp_ready` 是**挂上总结的末级节点数**。
  // C-0045「一条覆盖多页」之后两者必然不同（doc#5：12 条 vs 23 个节点，
  // LN-06 一条覆盖 p.4–p.8 就挂 5 个节点），所以旧文案
  // `已就绪 ${lkp_ready} / ${lkp_total} 个` 会写出"23 / 12"——分子大于分母。
  // 修法与 `_lkp_seq()` 同思路：两个数各说各的口径，不并成一个分数。
  const lkpLine = d.lkp_available
    ? `知识点 <b>${d.lkp_total || 0} 条</b> · 已挂到 ${d.lkp_ready || 0} 个末级节点${
        d.lkp_pending ? `（<span style="color:var(--warn)">待生成 ${
          d.lkp_pending} 个</span>）` : ''}`
    : '末级知识点总结：<span class="badge warn">待生成</span>（还没读到总结数据文件）';
  // 卡 C-0045 的核心成果得看得见：覆盖了全篇多少页 · 有没有一页被两条重复计数 ·
  // 多少页是下放过来的。**向后兼容**：17 份旧产物没有 `meta.coverage`，
  // 后端就不给 `lkp_coverage`，这一行整行不渲染——绝不显示 0/0。
  const cov = d.lkp_coverage;
  const covLine = cov ? `覆盖 ${cov.n_covered || 0}/${cov.pages_all || 0} 页${
      (cov.overlap_pages && cov.overlap_pages.length)
        ? ` · <span style="color:var(--warn)">⚠ ${cov.overlap_pages.length} 页被多条知识点重复计数</span>`
        : ' · 无跨条重复计数'}${
      d.lkp_parent_nodes ? ` · 下放 ${d.lkp_parent_nodes} 页（内容并入后继知识点）` : ''}`
    : '';
  // 父节点页那句：**新口径优先用产物自带的 `meta.excluded_reason`**
  // （它把哪几页、并到哪儿都写清了）；旧产物没有这句话，保持改动前的说法。
  let exclLine = '';
  if (d.lkp_excluded_pages && d.lkp_excluded_pages.length){
    exclLine = (d.lkp_downshift && d.lkp_excluded_reason)
      ? esc(String(d.lkp_excluded_reason).replace(/\*\*/g, ''))
      : `其中 ${d.lkp_excluded_pages.map(p => 'p.' + p).join('、')}
         这些页的节点在导图里还有下级，<b>按当前口径不建知识点</b>`;
  }
  t.innerHTML = `
    <div class="small muted" style="margin-bottom:8px">
      ${d.n_pages} 页 → ${d.n_nodes} 个节点 · 结构来自<b>整理产物</b>（模型生成）·
      模型 ${esc(d.model||'—')}${d.created_at ? ' · ' + fmtTime(d.created_at) : ''} ·
      整理花费 CNY${(d.cost_cny||0).toFixed(4)}${
      d.in_library?'':' · ⚠ 该文档已不在库'}<br>
      ${lkpLine}${covLine ? '<br>' + covLine : ''}${
        exclLine ? '<br>' + exclLine : ''}
    </div>
    <div class="mmtree">${mmNodes(d.tree, '')}</div>`;
  bindMindmapTree();
  // 特性2：教材后入库时，在导图栏顶部提醒「补做教材补充」（不打扰其它状态）
  mmTeachBackfill(MINDMAP.docId);
}

function mmNodes(list, pkey){
  if (!list || !list.length) return '';
  return '<ul>' + list.map((n, i) => {
    // key 用"下标路径"：同一份数据每次渲染都一样，才能记住展开状态；
    // 点名字时也靠它回查节点对象（见 MINDMAP.nodes）
    const key = pkey ? pkey + '.' + i : String(i);
    MINDMAP.nodes[key] = n;
    // 视觉上区分：有总结（◆ 实线）/ 末级待生成（◇ 黄虚线）/
    // 父节点页与其余节点（灰）。都用纯 CSS，不引图标库。
    const cls = n.lkp_ready ? ' has-kp'
      : (n.lkp_role === 'leaf' ? ' lkp-pending'
        : (n.lkp_role === 'parent_page' ? ' parent-page' : ' nonkp'));
    // 卡 C-0050：v2 的结构节点自带 kp_ids，一条知识点覆盖相邻 ≤3 页；一个叶节点
    // 覆盖超过 3 页时会挂**多条**知识点（lkp_multi），点开先列出这几条让人选。
    const tip = n.lkp_ready
      ? `末级知识点 ${n.lkp_id}${n.lkp_pages_label ? ' · ' + n.lkp_pages_label : ''}${
          n.lkp_multi ? ` · 这个节点下有 ${(n.lkp_ids||[]).length} 条知识点，点开选一条`
            : (n.lkp_merged ? ' · 这条覆盖相邻多页' : '')} · 点开看总结`
      : (n.lkp_role === 'leaf'
          ? '末级节点：该建知识点，但总结待生成'
          : (n.lkp_role === 'parent_page'
              ? (MINDMAP.downshift
                  ? '父节点页：本页不单独成条，内容已下放给紧随其后的末级页知识点'
                  : '该页路径还有下级节点，按当前口径不建知识点')
              : '非末级节点：本身不含知识点，展开看下面的末级节点'));
    const name = `<span class="mmname${cls}" data-k="${key}"
      data-name="${esc(n.name)}" title="${esc(tip)}">${esc(n.name)}</span>`;
    const meta = `<span class="small muted"> ${n.n_pages} 页${
      n.page ? ' · p.' + n.page : ''}</span>`;
    if (n.children && n.children.length){
      return `<li><details class="mmd" data-k="${key}"${
        MINDMAP.open[key] ? ' open' : ''}><summary>${name}${meta}</summary>${
        mmNodes(n.children, key)}</details></li>`;
    }
    return `<li><div class="mmleaf">${name}${meta}</div></li>`;
  }).join('') + '</ul>';
}

function bindMindmapTree(){
  const t = $('#mm-tree'); if (!t) return;
  // 点名字 = **就地看该节点的知识点总结**（不再跳检索页）；点前面的箭头 = 折叠/展开
  // （<summary> 自带行为）。名字走 data-k 而不是内联 onclick：
  // 节点名里可能有引号/尖括号；preventDefault 是为了别把 <details> 一起折叠了。
  t.querySelectorAll('.mmname').forEach(el => {
    el.onclick = e => {
      e.preventDefault(); e.stopPropagation();
      mmOpenNode(el.dataset.k, el);
    };
  });
  t.querySelectorAll('details.mmd').forEach(d => {
    d.addEventListener('toggle', () => { MINDMAP.open[d.dataset.k] = d.open; });
  });
}

function mmToggleAll(open){
  document.querySelectorAll('#mm-tree details.mmd').forEach(d => { d.open = open; });
}

// ---------------- 从笔记跳回知识点窗口（卡 C-0052） ----------------
// 人拍板：「点进条目后不光可以看到笔记原文，还可以按按钮跳转出绑定了该笔记的
// 知识点窗口。」笔记里存的是 kp_id + doc_id，这里负责把页签切过去、把课件选对、
// 把树上那一支展开、再调 mmOpenKp 打开**导图上那个真窗口**——不新造一个
// "只有笔记的弹窗"，否则同一条知识点会出现两种长相的窗口。
async function mmWaitFor(fn, ms){
  const t0 = Date.now();
  while (Date.now() - t0 < (ms || 8000)){
    let v = null;
    try { v = fn(); } catch(e){ v = null; }
    if (v) return v;
    await new Promise(r => setTimeout(r, 120));
  }
  return null;
}

async function mmJumpToKp(docId, kpId, label){
  closeModal();
  MINDMAP.mode = 'courseware';
  if (CURRENT !== 'kps') await go('kps');
  else setMindmapMode('courseware');
  if (!await mmWaitFor(() => $('#mm-doc'))){
    toast('知识点页签还没挂上，稍后再试');
    return;
  }
  const sel = $('#mm-doc');
  if (!sel.querySelector('option[value="' + docId + '"]')){
    // 该课件已不在库 / 不在当前学科：如实说，不假装跳过去了
    toast('这份课件（doc#' + docId + '）不在当前学科的课件清单里，跳不过去');
    return;
  }
  if (MINDMAP.docId !== parseInt(docId)){
    MINDMAP.docId = parseInt(docId) || 0;
    MINDMAP.open = {};
    sel.value = String(docId);
    await loadMindmapTree();
  }
  // mountMindmap 是**不 await** 的（见 viewKps），所以"树已经有内容"得等出来：
  // 非空态等到节点，空态等到 .empty（两种都算"加载完了"）
  await mmWaitFor(() => {
    const t = $('#mm-tree');
    return t && (t.querySelector('.mmname') || t.querySelector('.empty'));
  });
  let key = '', node = null;
  Object.keys(MINDMAP.nodes || {}).forEach(k => {
    if (key) return;
    const n = MINDMAP.nodes[k];
    const ids = (n.lkp_ids || []).map(String);
    if (n.lkp_id) ids.push(String(n.lkp_id));
    if (ids.indexOf(String(kpId)) >= 0){ key = k; node = n; }
  });
  evPopEl().dataset.kpPop = '1';    // 与点节点一致：居中显示（见 evPopPlace）
  if (key){
    mmToggleAll(true);              // 祖先 <details> 全展开，否则它的 rect 全是 0
    const el = await mmWaitFor(() =>
      document.querySelector('.mmname[data-k="' + key + '"]'));
    if (el){
      try { el.scrollIntoView({block:'center'}); } catch(e){}
      // ★ 滚动会**异步**派发 window scroll，而 evPop 的既有规矩是"一滚就收窗"
      //   （见 evPopScroll）。若立刻开窗，那次迟到的 scroll 会把刚打开的知识点
      //   弹窗就地收掉（dataset.kp 被清空 → 总结响应回来直接作废），
      //   表现是弹窗永远停在"加载中"。所以先让这一拍过去，再开窗。
      await new Promise(r => setTimeout(r, 350));
      return mmOpenKp(kpId, (node && node.name) || label || kpId, el, false);
    }
  }
  // 树上找不到这条知识点（课件被重新整理过、或这条已不在产物里）：
  // 不报错——直接把这条知识点的窗口居中打开，能读到什么就显示什么
  toast('导图上没找到这条知识点（课件可能已重新整理），直接打开它的总结');
  return mmOpenKp(kpId, label || kpId, null, false);
}

// ---------------- 课件级 AI 答疑（卡 C-0052 起 · C-0057 改独立管线） ----------------
// 人最初拍板（C-0052）：「在课件思维导图那一栏右下角新增一个『AI答疑』按钮，
// 点击后可以跳出一个小对话窗口……这个窗口是用来对整个课件提问和总结的，
// 提问和总结得到的产出也要记入笔记页签。」
//
// 人后一次拍板（C-0057，2026-09-29 原话）：「每份课件的AI答疑不要和整个知识库的
// AI问答混用，开一条新的管线用于每份课件的答疑。这条管线不用提示词，思考强度
// 开到最高。」——所以这个窗口现在走的**不是**知识库问答那条路：
//
//   1. 后端是**独立管线**（/api/deck-chat/stream → core/chat.py 的 deck_* 区段）：
//      **不检索**（不查库里的相似片段）、**不用提示词**（messages 里没有 system，
//      也不读 logs/knowledge/ 下的提示词文件）、**思考强度最高**
//      （thinking=True + reasoning_effort=max）。整份课件按页序直接进上下文，
//      装不下才保序等距抽样，并把实况报在范围行上（见下）；
//   2. 「跟这份课件有关」不再是"检索限定在它"——这条路上压根不检索，
//      上下文里只有这一份课件的内容，**不会有别的资料的片段混进来**；
//   3. 产出记入笔记＝POST /api/note/deck（origin=deck_chat + doc_id），
//      笔记页签据此显示来源与记录时间。
//
// 窗口用 modal 而**不是** evpop 小窗：对话要能选中/复制文本，而 evpop 是
// "点外面就关"，且全页只有一个小窗（点节点看知识点会把对话顶掉）。
// 对话按课件保存：换一份课件就重开一段——两份课件的内容不该混进一个上下文。
const DKCHAT = {docId: 0, title: '', msgs: [], busy: false};

function deckChatDoc(){
  const sel = $('#mm-doc');
  const docId = MINDMAP.docId || 0;
  let title = '';
  if (sel && sel.selectedOptions && sel.selectedOptions[0])
    title = String(sel.selectedOptions[0].textContent || '').trim();
  // 下拉项的文案尾巴带着**产物状态摘要**（" · 34 页 / 36 节点"、" · 未整理"…），
  // 那是给下拉看的状态，不是课件名：它会写进对话窗口标题、笔记标题与文件名里，
  // 变成一串噪声（实测："… · 34 页 / 36 节点 · AI答疑：…"）。这里剥掉。
  title = title.replace(
    /\s*·\s*(?:\d+\s*页\s*\/\s*\d+\s*节点|旧格式，需重新整理|产物不完整，需重新整理|未整理)\s*(（已不在库）)?$/,
    '').trim();
  return {docId, title: title || ('doc#' + docId)};
}

// 千分位：范围行里要写 60,000 这种数，人眼扫得快（只服务本区段）
const dkNum = n => String(Number(n) || 0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');

function deckChatHtml(){
  return `<h2>AI 答疑 · ${esc(DKCHAT.title)}</h2>
    <div class="small muted">独立管线（与「问答」页的知识库问答不是一条路）：
      <b>不检索</b> · <b>不用提示词</b> · 思考强度 <b>最高</b> ·
      整份课件（doc#${DKCHAT.docId}）直接进上下文 ·
      提问与总结的产出可一键记入「笔记」页签</div>
    <div class="row" style="margin-top:10px">
      <button class="ghost" id="dk-sum">总结这份课件</button>
      <button class="ghost" id="dk-clear">清空对话</button>
      <span class="small muted" id="dk-scope">首次提问后这里显示本次实际送出的参数</span>
    </div>
    <div id="dk-thread" style="margin-top:12px;display:flex;flex-direction:column;
      gap:14px;max-height:46vh;overflow:auto;padding-right:6px"></div>
    <div class="row" style="margin-top:12px;align-items:flex-end">
      <textarea id="dk-q" style="flex:1;min-width:220px;min-height:44px;
        max-height:140px;resize:vertical"
        placeholder="就这份课件提问，例如：这份课件的重点是什么（Enter 发送，Shift+Enter 换行）"></textarea>
      <button class="act" id="dk-send">发送</button>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="ghost" onclick="closeModal()">关闭</button>
    </div>`;
}

// 这次回答**到底依据了多少内容、用了什么参数**：写进回答下方一小行（不打扰），
// 完整字段挂在 title 上（hover 可见，机器也抠得到）。C-0052 那版这里显示的是
// "检索到的片段出处"——C-0057 起不检索了，硬列几个出处就是骗人，所以改成
// 如实报上下文：送出/共几块、约多少 token、有没有截断。
function dkCtxLine(m){
  const c = m.ctx; if (!c) return '';
  return `依据：本课件全文 ${c.chunks_used}/${c.chunks_total} 块` +
    (c.figures_total ? ` + 页图识别 ${c.figures_used}/${c.figures_total} 页` : '') +
    ` · 约 ${dkNum(c.tokens_est)} token` +
    (c.truncated ? ' · 已截断（保序等距抽样）' : ' · 未截断') +
    ` · ${c.model} · 思考 ${c.reasoning_effort}`;
}

function renderDeckChat(){
  const th = $('#dk-thread'); if (!th) return;
  th.innerHTML = DKCHAT.msgs.length ? DKCHAT.msgs.map((m, i) => {
    if (m.role === 'user')
      return `<div class="msg user"><div class="who">我</div><div class="body">
        <div class="bubble">${esc(m.content).replace(/\n/g,'<br>')}</div></div></div>`;
    return `<div class="msg ai"><div class="who">AI</div><div class="body">
      <div class="bubble">${mdToHtml(m.content)}</div>
      ${m.ctx ? `<div class="small muted" style="margin-top:6px"
        data-dkctx="${i}" title="${esc(JSON.stringify(m.ctx))}">${
        esc(dkCtxLine(m))}</div>` : ''}
      <div class="row" style="margin-top:6px">
        <button class="ghost" data-dknote="${i}">记入笔记</button>
        <span class="small muted" data-dkmsg="${i}">${
          m.saved ? '已记入 ' + esc(m.saved) : ''}</span>
      </div>
      ${m.elapsed ? `<div class="small muted" style="margin-top:4px">用时 ${
        Number(m.elapsed).toFixed(1)}s · 输入 ${
        (m.usage && m.usage.prompt_tokens) || 0} / 输出 ${
        (m.usage && m.usage.completion_tokens) || 0} tokens</div>` : ''}
    </div></div>`;
  }).join('') : `<div class="small muted">还没有对话。点「总结这份课件」，
    或直接在下面提问——回答只依据这份课件里的内容（整份直接给模型，不检索）。</div>`;
  th.querySelectorAll('button[data-dknote]').forEach(b => {
    b.onclick = () => deckChatSaveNote(parseInt(b.dataset.dknote));
  });
  th.scrollTop = th.scrollHeight;
}

function openDeckChat(){
  const d = deckChatDoc();
  if (!d.docId){ toast('先在「课件思维导图」里选一份课件'); return; }
  if (DKCHAT.docId !== d.docId){ DKCHAT.docId = d.docId; DKCHAT.msgs = []; }
  DKCHAT.title = d.title;
  modal(deckChatHtml());
  const sum = $('#dk-sum');
  if (sum) sum.onclick = () => deckChatSend(
    '请总结这份课件的整体内容与重点（分条列出，每条后面标注出处编号）。');
  const clr = $('#dk-clear');
  if (clr) clr.onclick = () => { DKCHAT.msgs = []; renderDeckChat(); };
  const send = $('#dk-send');
  const ta = $('#dk-q');
  const fire = () => { const q = ta ? ta.value.trim() : ''; if (q) deckChatSend(q); };
  if (send) send.onclick = fire;
  if (ta){
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); fire(); }
    });
    ta.focus();
  }
  renderDeckChat();
}

async function deckChatSend(q){
  if (DKCHAT.busy || !q) return;
  const ta = $('#dk-q'); if (ta) ta.value = '';
  DKCHAT.busy = true;
  const ai = {role:'assistant', content:'', ev:[], ctx:null};
  DKCHAT.msgs.push({role:'user', content:q});
  DKCHAT.msgs.push(ai);
  renderDeckChat();
  const send = $('#dk-send');
  if (send){ send.disabled = true; send.textContent = '思考中…'; }
  // 历史只带本轮之前的最近 8 条（与问答页同口径），别把上下文撑爆
  const history = DKCHAT.msgs.slice(0, -2).slice(-8)
    .map(m => ({role:m.role, content:m.content}));
  const lastBubble = () => {
    const t = $('#dk-thread'); if (!t) return null;
    const els = t.querySelectorAll('.msg.ai .bubble');
    return els.length ? els[els.length - 1] : null;
  };
  let err = '';
  try {
    const resp = await fetch('/api/deck-chat/stream', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({doc_id: DKCHAT.docId, query: q, history}),
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const reader = resp.body.getReader();
    const dec = new TextDecoder('utf-8');
    let buf = '';
    while (true){
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0){
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!chunk.startsWith('data: ')) continue;
        let obj; try { obj = JSON.parse(chunk.slice(6)); } catch(e){ continue; }
        if (obj.type === 'scope'){
          // 后端在开答**之前**把这次实际送出去的参数报过来（模型 / 思考强度 /
          // 送出多少 token / 预算 / 有没有截断）。写进范围行供人核对，
          // 完整字段挂 title（hover 可见、验证脚本也抠得到）。
          // ⚠ C-0057 起这条路上**不检索**，所以这里不再有"命中几条证据"。
          ai.ctx = obj;
          const sc = $('#dk-scope');
          if (sc){
            sc.textContent = `${obj.model} · 思考 ${obj.reasoning_effort}`
              + ` · 上下文 ${dkNum(obj.tokens_est)}/${dkNum(obj.tokens_budget)} token`
              + (obj.truncated ? ' · 已截断' : ' · 未截断');
            sc.title = '独立管线：不检索 / 不用提示词 / 思考强度最高 · '
              + `课件《${obj.doc_title}》doc#${obj.doc_id} · `
              + `正文 ${obj.chunks_used}/${obj.chunks_total} 块 · `
              + `页图识别 ${obj.figures_used}/${obj.figures_total} 页 · `
              + obj.strategy;
          }
        } else if (obj.type === 'sources'){
          // 老事件：C-0057 的独立管线不发它。留着是为了万一跑的是旧后端
          // （人没重启看板）时前端不炸——那种情况下也照常显示回答。
          ai.ev = obj.evidence || [];
        } else if (obj.type === 'delta'){
          ai.content += obj.text;
          const b = lastBubble();
          if (b) b.innerHTML = mdToHtml(ai.content) + '<span class="typing"></span>';
          const t = $('#dk-thread'); if (t) t.scrollTop = t.scrollHeight;
        } else if (obj.type === 'done'){
          ai.usage = obj.usage || {}; ai.elapsed = obj.elapsed || 0;
        } else if (obj.type === 'error'){
          err = obj.message || '未知错误';
        }
      }
    }
  } catch(e){ err = e.message; }
  if (err) ai.content += (ai.content ? '\n\n' : '') + '**出错**：' + err;
  DKCHAT.busy = false;
  if (send){ send.disabled = false; send.textContent = '发送'; }
  renderDeckChat();
  if (ta) ta.focus();
}

// 把这一轮问答记入「笔记」页签：正文含问与答，来源类型 origin=deck_chat +
// doc_id=该课件（无 kp_id——这是对整份课件的问答）。
// `## 出处` 段（后端按 payload.refs 渲染）在 C-0057 起写的是**这次回答的依据与
// 参数**：独立管线不检索，没有"第几页的哪个片段"可写，硬编几个页码就是骗人；
// 而"依据的是这份课件的全文、送了多少、用的什么模型与思考强度"才是下次读的
// 人真正需要核对的（他看这条笔记时，上下文早就不在了）。
async function deckChatSaveNote(i){
  const m = DKCHAT.msgs[i];
  if (!m || m.role !== 'assistant' || !m.content) return;
  const prev = DKCHAT.msgs[i - 1];
  const q = (prev && prev.role === 'user') ? prev.content : '';
  const refs = (m.ev && m.ev.length)
    ? m.ev.map(e => `[${e.idx}] ${e.label || e.doc_title || ''}（doc#${e.doc_id}）`)
    : (m.ctx ? [
        `依据：本课件《${m.ctx.doc_title}》（doc#${m.ctx.doc_id}）全文 — `
        + `送出 ${m.ctx.chunks_used}/${m.ctx.chunks_total} 块正文`
        + `（页图识别 ${m.ctx.figures_used}/${m.ctx.figures_total} 页），`
        + `约 ${m.ctx.tokens_est} token，`
        + (m.ctx.truncated ? '已截断（保序等距抽样）' : '未截断'),
        `管线：独立课件答疑（不检索 / 不用提示词）· 模型 ${m.ctx.model}`
        + ` · 思考强度 ${m.ctx.reasoning_effort}`
        + ` · 上下文预算 ${m.ctx.tokens_budget} token`] : []);
  const tip = document.querySelector('[data-dkmsg="' + i + '"]');
  try {
    const r = await api('/api/note/deck', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({doc_id: DKCHAT.docId, doc_title: DKCHAT.title,
        question: q, answer: m.content, refs})});
    if (r.error) throw new Error(r.error);
    m.saved = r.file;
    if (tip) tip.textContent = '已记入 ' + r.file;
    toast('已记入「笔记」页签：' + r.file);
  } catch(e){
    if (tip) tip.textContent = '记入失败：' + e.message;
  }
}

// 节点类型的中文名：徽标文案与"没有知识点"的说法都靠它，别散在各处写死。
// `downshift` 只改父节点页那一档的说法（卡 C-0045 起是"下放"）；
// **默认取旧说法**——这个函数被 `tools/check_mindmap_kp.py` 单独抠去 node 里跑，
// 那时没有 MINDMAP 可读，不能让它依赖全局状态。
function mmKindLabel(kind, downshift){
  return {leaf: '末级节点 · 知识点总结', pending: '末级节点 · 待生成',
          parent_page: downshift ? '父节点页 · 内容已下放' : '父节点页 · 本页不建知识点',
          e1: '非教学页',
          category: '分类节点', root: '根节点', none: '节点',
          r1: '三级节点 · R1 知识点', r2: '浅层节点 · R2 兜底知识点',
          lv4: '四级节点'}[kind] || '节点';
}

