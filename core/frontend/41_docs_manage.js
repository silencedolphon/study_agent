// ---------------- ★ 卡 C-0085：重命名（原文件 / 课件标题 / 产物标题） ----------------
//
// 人原话（2026-10-12）：「重命名**入库后的 PPT 名字**，重命名**总结知识点后整个
// 课件的标题**」。三类对象的分工（都可逆，与删除不是一回事）：
//   · 原文件（本段 renameRawFile）：磁盘上的文件名 = 真相。库里的
//     `documents.path / abs_path` 与页图目录 `logs/figure_desc/<目录>/` 会**一起改**
//     ——少改一处，页面立刻出现"索引里有、raw 里没有"的孤儿或页图全丢。
//   · 课件标题（renameDocTitle）：只改库里那一列 `documents.title`，不动文件、
//     不动产物。⚠ 产物内容里那个课件名是整理时写进去的，**不会跟着变**。
//   · 产物标题（renameArtifactTitle）：改产物 JSON 里那个课件名（页签上思维导图的
//     根名），四处一起改。**不重跑整理、不花一分钱**，知识点关联不断。
//
// 三道闸门与批量删除同款：① 先 dry_run 预览（后端的 dry_run 分支）；② 真做要带
// `confirm=true`（接口能被直接 curl，前端这个确认框不算闸门）；③ 后端发现入库/整理
// 任务在跑 → 拒绝（409），一份都不动。

async function renameRawFile(docId, rel, oldName){
  if (!rel){ toast('这个文件没有可用的相对路径，无法改名'); return; }
  const dot = (oldName || '').lastIndexOf('.');
  const keep = dot > 0 ? oldName.slice(dot) : '';
  modal(`<h2>重命名原文件</h2>
    <div class="sub" style="margin:12px 0;line-height:1.95">
      ${esc(rel)}<br>
      · 改的是 <b>raw 目录里那个文件名</b>（磁盘上真实的那份）<br>
      · 库里的路径（<code>documents.path / abs_path</code>）与
        <code>logs/figure_desc/&lt;目录&gt;/</code> 下的<b>页图目录会一起改名</b>——
        少改一处，页图会全部显示不出来或资料变成"索引里有、raw 里没有"的孤儿<br>
      · <b>不能改扩展名</b>${keep?`（必须保留 <b>${esc(keep)}</b>，入库类型由它决定）`:''}<br>
      · 文件内容一个字节都不动，所以"内容未变"的判定不变，<b>重入不会重建索引</b>
    </div>
    <input id="rn-name" value="${esc(oldName || '')}" style="width:100%"
      autocomplete="off" placeholder="新的文件名（含扩展名）">
    <div class="small" id="rn-err" style="color:var(--bad);min-height:18px;margin-top:6px"></div>
    <div class="small muted" id="rn-prev" style="line-height:1.7"></div>
    <div class="row" style="justify-content:flex-end;margin-top:10px">
      <button class="ghost" onclick="closeModal()">取消</button>
      <button class="act" id="rn-ok">确定改名</button>
    </div>`);
  const inp = $('#rn-name');
  let timer = null;
  const preview = async () => {
    const el = $('#rn-prev');
    const nm = ((inp && inp.value) || '').trim();
    if (!nm || nm === oldName){ if (el) el.textContent = ''; return; }
    try {
      const d = await api('/api/rename/raw', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({rel, new_name: nm, dry_run: true})});
      if (!el) return;
      if (d && d.error){ el.textContent = '⚠ ' + d.error; return; }
      const db = (d.db && d.db.updated)
        ? `${d.db.before.path} → ${d.db.after.path}`
        : '（这个文件还没入库，只改磁盘上的名字）';
      const decks = ((d.figure_desc || []).map(x => `${x.from} → ${x.to}`).join('；'))
        || '（没有页图目录，无需连带）';
      el.textContent = `预览 · 库记录：${db}｜页图目录：${decks}`;
    } catch(e){ const el2 = $('#rn-prev'); if (el2) el2.textContent = '预览失败：' + e.message; }
  };
  if (inp){
    inp.focus();
    inp.oninput = () => { clearTimeout(timer); timer = setTimeout(preview, 350); };
    preview();
  }
  const ok = $('#rn-ok');
  if (ok) ok.onclick = async () => {
    const nm = ((inp && inp.value) || '').trim();
    const err = $('#rn-err');
    if (!nm){ if (err) err.textContent = '请输入新文件名'; return; }
    ok.disabled = true; ok.textContent = '改名中…';
    try {
      const d = await api('/api/rename/raw', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({rel, new_name: nm, confirm: true})});
      if (d && d.need_confirm) throw new Error(d.error || '后端要求二次确认');
      if (d && d.error) throw new Error(d.error);
      closeModal();
      const moved = (d.figure_desc || []).filter(x => x.moved).length;
      const kept = (d.figure_desc || []).filter(x => x.moved === false).length;
      toast(`已改名为「${(d.rel && d.rel.to) || nm}」` +
        (moved ? `，页图目录同步改名 ${moved} 个` : '') +
        (kept ? `；${kept} 个页图目录因目标同名未搬（见回执）` : ''));
      go('docs');
    } catch(e){
      if (err) err.textContent = '改名失败：' + e.message;
      ok.disabled = false; ok.textContent = '确定改名';
    }
  };
}

async function renameDocTitle(docId, oldTitle){
  if (!docId){ toast('这份文件还没有入库，没有课件标题可改'); return; }
  modal(`<h2>改课件标题</h2>
    <div class="sub" style="margin:12px 0;line-height:1.95">
      · 改的是<b>库里这一列</b>（<code>documents.title</code>）：资料列表、导图页的
        课件下拉、页签上都显示它<br>
      · <b>不动</b> raw 里的文件，也不动整理产物文件<br>
      · ⚠ 产物<b>内容</b>里那个课件名（思维导图根名）是整理时写进去的，
        <b>不会跟着变</b>；想让页签上那个根名立刻换，请用「改产物标题」
        （只改名字、不重跑、不花一分钱）
    </div>
    <input id="dt-name" value="${esc(oldTitle || '')}" style="width:100%" autocomplete="off">
    <div class="small" id="dt-err" style="color:var(--bad);min-height:18px;margin-top:6px"></div>
    <div class="small muted" id="dt-hint"></div>
    <div class="row" style="justify-content:flex-end;margin-top:8px">
      <button class="ghost" onclick="closeModal()">取消</button>
      <button class="act" id="dt-ok">确定</button>
    </div>`);
  const inp = $('#dt-name');
  if (inp) inp.focus();
  // ★ 先把「确定」接上：面板一出现它就得能用。下面那句 dry_run 只是**补充提示**，
  //   要等一次请求才回来——把绑定排在它后面，手快的人会点到还没接线的按钮
  //   （C-0085 的浏览器探针第一版就是这么挂的：点 #dt-ok 毫无反应）。
  const ok = $('#dt-ok');
  if (ok) ok.onclick = async () => {
    const nm = ((inp && inp.value) || '').trim();
    const err = $('#dt-err');
    if (!nm){ if (err) err.textContent = '请输入新标题'; return; }
    ok.disabled = true; ok.textContent = '保存中…';
    try {
      const d = await api('/api/doc/rename', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({doc_id: docId, title: nm, confirm: true})});
      if (d && d.need_confirm) throw new Error(d.error || '后端要求二次确认');
      if (d && d.error) throw new Error(d.error);
      closeModal();
      toast(`课件标题已改为「${nm}」` +
        (d.artifact_deck ? `｜产物里的根名仍是「${d.artifact_deck}」，要一起换请点「改产物标题」` : ''));
      // 知识点页签的页签名是 'kps'（VIEWS 表里那一项），不是 'mindmap'；
      // 在这个页签上改的就留在这一页（那是「改产物标题」按钮所在的地方）。
      go(CURRENT === 'kps' ? 'kps' : 'docs');
    } catch(e){
      if (err) err.textContent = e.message;
      ok.disabled = false; ok.textContent = '确定';
    }
  };
  // 再问后端"产物里现在叫什么"，把"改了标题但导图根名还是旧的"这件事说在明处
  (async () => {
    try {
      const pv = await api('/api/doc/rename', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({doc_id: docId, title: oldTitle || '', dry_run: true})});
      const h = $('#dt-hint');
      if (h && pv){
        h.textContent = pv.artifact_state === 'v2'
          ? `当前整理产物里的根名：${pv.artifact_deck || '（空）'}（它不会跟着变，除非改产物标题或重新整理）`
          : '这份课件还没有可用的整理产物，改标题不影响任何产物。';
      }
    } catch(e){ /* 提示拿不到就不显示，绝不挡改名 */ }
  })();
}

async function renameArtifactTitle(docId, oldName){
  if (!docId){ toast('先在上面选一份课件'); return; }
  let pv = null;
  try {
    pv = await api('/api/artifact/rename', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({doc_id: docId, name: oldName || '', dry_run: true})});
  } catch(e){ toast('读产物失败：' + e.message); return; }
  if (!pv || pv.error){ toast((pv && pv.error) || '这份课件没有可改名的整理产物'); return; }
  const spot = (k, v) => `<div class="small muted"><code>${k}</code> = ${esc(v == null ? '（空）' : String(v))}</div>`;
  modal(`<h2>改产物标题（思维导图根名）</h2>
    <div class="sub" style="margin:12px 0;line-height:1.95">
      · 改的是<b>整理产物 JSON 里的课件名</b>，页签上思维导图的根节点就是它<br>
      · 同一个名字在产物里有<b>四处</b>，会一起改（少改一处，页面上就会出现两个名字）：
      <div style="margin:4px 0 4px 10px">
        ${spot('structure.root.name', pv.spots_before && pv.spots_before['structure.root.name'])}
        ${spot('structure.raw_root.name', pv.spots_before && pv.spots_before['structure.raw_root.name'])}
        ${spot('structure.nodes[0].name', pv.spots_before && pv.spots_before['structure.nodes[0].name'])}
        ${spot('meta.deck', pv.spots_before && pv.spots_before['meta.deck'])}
      </div>
      · <b>知识点关联一个字都不动</b>（节点之间靠 id 引用，不靠名字），
        整理产物文件名也不变<br>
      · 改之前会自动备份一份旧产物<br>
      · 这是<b>不花钱</b>的那条路：不重跑整理、不调模型
    </div>
    <input id="at-name" value="${esc(pv.name ? (pv.name.from || '') : '')}" style="width:100%"
      autocomplete="off" placeholder="新的课件标题（就是思维导图根名）">
    <div class="small" id="at-err" style="color:var(--bad);min-height:18px;margin-top:6px"></div>
    <div class="row" style="justify-content:flex-end;margin-top:8px">
      <button class="ghost" onclick="closeModal()">取消</button>
      <button class="act" id="at-ok">确定改名</button>
    </div>`);
  const inp = $('#at-name');
  if (inp) inp.focus();
  const ok = $('#at-ok');
  if (ok) ok.onclick = async () => {
    const nm = ((inp && inp.value) || '').trim();
    const err = $('#at-err');
    if (!nm){ if (err) err.textContent = '请输入新标题'; return; }
    ok.disabled = true; ok.textContent = '保存中…';
    try {
      const d = await api('/api/artifact/rename', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({doc_id: docId, name: nm, confirm: true})});
      if (d && d.need_confirm) throw new Error(d.error || '后端要求二次确认');
      if (d && d.error) throw new Error(d.error);
      closeModal();
      toast(`产物标题已改为「${nm}」（四处同步：${(d.changed || []).length} 处）`);
      go('kps');
    } catch(e){
      if (err) err.textContent = e.message;
      ok.disabled = false; ok.textContent = '确定改名';
    }
  };
}

// ---------------- ★ 卡 C-0084：批量**真删**（文件 + 向量块 + 知识点产物一并不留） ----------------
//
// 人原话（2026-10-12）：「还要**批量删除**的功能。这种删除是**直接把文件和所有
// 向量块和知识点删除**，不是解除索引那样的删除。」
//
// 与上面两个入口的分工（三者互不替代，都留着）：
//   · 「移除」（deleteDoc）    ：只删索引，raw\ 原文件与产物都留着 —— 可逆；
//   · 「删文件」（deleteRaw）  ：单份，删 raw\ 原文件 + 索引（仍不动产物）；
//   · 「批量删除」（本段）     ：勾几份 → 弹窗里再选原文件留不留 → 整批真删
//                               （库记录 / 切块 / 向量 / 识图记录 / 知识点出处
//                                / 四处产物文件）；**不可逆**。
//
// 三道闸门（照 kpbClear 与后端 api_delete_docs 的既有规矩）：
//   ① 弹窗 + `window.confirm`（前端）；② 后端 `confirm=true`（真闸门，
//   接口能被直接 curl）；③ 后端发现入库/整理任务在跑 → 拒绝，一份都不删。
// 顺序上**先 dry_run 预览、再确认**：不可逆的操作必须先把影响范围摆给人看，
// 而不是让他点完才知道删了多少（卡面验收标准点名要求）。
const DS = { sel: new Set(), busy: false };

function docSelReset(){
  DS.sel = new Set();
  docSelBind();
  docSelPaint();
}

function docSelBind(){
  const list = document.querySelectorAll('.ds-cb');
  list.forEach(cb => {
    cb.onchange = () => {
      const id = parseInt(cb.dataset.doc);
      if (cb.checked) DS.sel.add(id); else DS.sel.delete(id);
      docSelPaint();
    };
  });
  const all = $('#ds-all');
  if (all) all.onclick = () => {
    document.querySelectorAll('.ds-cb').forEach(cb => { cb.checked = true; });
    DS.sel = new Set(docSelIds());
    docSelPaint();
  };
  const none = $('#ds-none');
  if (none) none.onclick = () => {
    document.querySelectorAll('.ds-cb').forEach(cb => { cb.checked = false; });
    DS.sel = new Set(docSelIds());
    docSelPaint();
  };
  const del = $('#ds-del');
  if (del) del.onclick = () => openDocDel();
}

function docSelIds(){
  return Array.from(document.querySelectorAll('.ds-cb'))
    .filter(cb => cb.checked)
    .map(cb => parseInt(cb.dataset.doc))
    .filter(n => n > 0)
    .filter((v, i, a) => a.indexOf(v) === i);
}

function docSelPaint(){
  const el = $('#ds-info');
  if (el){
    el.textContent = DS.sel.size ? `已选 ${DS.sel.size} 份（批量删除不可逆）` : '未选择';
  }
}

async function openDocDel(){
  const ids = docSelIds();
  if (!ids.length) return toast('请先勾选至少一份资料');
  return openDocDelByIds(ids);
}

// 弹「批量删除确认」面板。**入参是 doc_id 列表**——资料页的勾选集
// （`openDocDel`）与「批量整理知识点」栏的勾选集（`kpbPurge`）共用这一个面板，
// 于是同一个动作在页面里只有一套行为（预览、文案、两道闸门）。
async function openDocDelByIds(ids){
  ids = (ids || []).map(n => parseInt(n)).filter(n => n > 0)
    .filter((v, i, a) => a.indexOf(v) === i);
  if (!ids.length) return toast('请先勾选至少一份资料');
  if (DS.busy) return;
  DS.busy = true;
  let pv = null;
  try {
    pv = await api('/api/docs/delete', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({doc_ids: ids, dry_run: true})});
  } catch(e){
    DS.busy = false;
    return toast('无法获取删除预览：' + e.message);
  } finally { DS.busy = false; }
  if (pv && pv.error) return toast(pv.error);
  const docs = (pv && pv.documents) || [];
  const lines = docs.slice(0, 8).map(x =>
    '· ' + esc(x.title || '未命名资料')
    + (x.in_library === false ? '（仅清理剩余的整理结果）' : '')).join('<br>');
  const more = docs.length > 8 ? `<br>… 另有 ${docs.length - 8} 份` : '';
  const art = (pv && pv.artifacts_total) || 0;
  const artMb = ((pv && pv.artifacts_bytes_total) || 0) / 1048576;
  const purge = purgeSummary({orphan_kps: pv && pv.orphan_kps_total, names: []});
  modal(`
    <h2 style="color:var(--bad)">删除 ${ids.length} 份资料？</h2>
    <div class="sub" style="margin:12px 0;line-height:1.95">${lines}${more}</div>
    <div class="sub" style="line-height:1.95">
      这些资料将从搜索和问答中移除，相关的整理结果也会删除。
      ${purge ? '<br>' + esc(purge).replace(/\n/g,'<br>') : ''}
    </div>
    <details class="docs-extra"><summary>查看详细影响</summary><div class="docs-extra-body small muted">
      预计删除 ${art} 个整理文件（约 ${artMb.toFixed(1)} MB）和
      ${(pv && pv.decks_total) || 0} 组课件图片。相关知识点会解除与这些资料的关联；
      失去全部出处的知识点及其复习记录会一并清理。
    </div></details>
    <div class="sub" style="margin-top:14px;line-height:1.95;border-top:1px solid var(--line);padding-top:12px">
      <b style="color:var(--warn)">原文件怎么处理？</b><br>
      「连原文件一起删」会从电脑永久删除，<b>无法撤销</b>；<br>
      「保留原文件」只移除资料记录和整理结果，以后可以重新入库。
    </div>
    <div class="row" style="margin-top:16px">
      <button class="act" id="dd-hard" style="background:var(--bad);color:#fff">连原文件一起删</button>
      <button class="ghost" id="dd-soft" style="color:var(--bad);border-color:#4a1f1c">保留原文件</button>
      <button class="ghost" onclick="closeModal()">取消</button>
    </div>`);
  const hb = $('#dd-hard');
  if (hb) hb.onclick = () => docDelGo(ids, true);
  const sb = $('#dd-soft');
  if (sb) sb.onclick = () => docDelGo(ids, false);
}

async function docDelGo(ids, withFile){
  if (DS.busy) return;
  const what = withFile
    ? `这 ${ids.length} 份资料及电脑里的原文件会被永久删除，相关整理结果也会清理，无法撤销。`
    : `这 ${ids.length} 份资料及整理结果会从学习看板移除，原文件保留，可重新入库。`;
  const ok = window.confirm(
    `批量删除确认\n\n` + what + '\n\n确定继续？');
  if (!ok) return;
  closeModal();
  DS.busy = true;
  const btn = $('#ds-del');
  if (btn){ btn.disabled = true; btn.textContent = '删除中…'; }
  try {
    const r = await api('/api/docs/delete', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({doc_ids: ids, delete_file: withFile,
                            purge_orphans: true, confirm: true})});
    if (r.need_confirm) return toast('操作尚未确认，请重试');
    if (r.error) return toast('删除被拒绝：' + r.error);
    const g = r.purged || {};
    let msg = `已删除 ${r.n_deleted} 份资料`;
    if (r.files_removed) msg += `，含 ${r.files_removed} 个原文件`;
    if (g.deleted_kps) msg += `；清理 ${g.deleted_kps} 个失去出处的知识点`;
    if ((r.artifact_errors || []).length) msg += `；${r.artifact_errors.length} 个整理文件删除失败`;
    toast(msg);
    if ((r.artifact_errors || []).length) console.warn('批量删除产物失败明细', r.artifact_errors);
    go('docs');
  } catch(e){
    toast('删除失败：' + e.message);
  } finally {
    DS.busy = false;
    const b2 = $('#ds-del');
    if (b2){ b2.disabled = false; b2.textContent = '删除所选…'; }
  }
}

// 一键清理：删完资料后把失去出处的知识点、复习记录、依赖边一并收拾干净
async function openCleanup(){
  let pv;
  try { pv = await api('/api/cleanup', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({dry_run: true, include_notes: true})}); }
  catch(e){ return toast('无法获取清理预览：' + e.message); }
  const nothing = !pv.orphan_kps && !pv.notes;
  modal(`
    <h2>清理无出处的记录</h2>
    <div class="sub" style="margin-top:10px;line-height:1.9">
      删除资料后，有些知识点可能找不到原文出处。这里可以清理这些知识点
      及相关复习记录。当前资料：<b>${pv.documents ?? 0}</b> 份。
    </div>
    <div class="grid" style="margin-top:14px">
      <div class="card"><h2>失去出处的知识点</h2><div class="big">${pv.orphan_kps||0}</div></div>
      <div class="card"><h2>相关复习记录</h2><div class="big">${pv.reviews||0}</div></div>
      <div class="card"><h2>知识点关联</h2><div class="big">${pv.edges||0}</div></div>
      <div class="card"><h2>笔记</h2><div class="big">${pv.notes||0}</div></div>
    </div>
    ${pv.names && pv.names.length ? `<div class="sub" style="margin-top:12px">
      将被清理的知识点：${pv.names.map(esc).join('、')}${pv.orphan_kps>pv.names.length?' …':''}
    </div>` : ''}
    <div class="sub" style="margin-top:12px">
      错题和笔记默认保留。错题会解除与已删除知识点的关联；
      勾选下面的选项才会一并删除。
    </div>
    <div class="row" style="margin-top:14px;flex-direction:column;align-items:flex-start;gap:8px">
      <label class="row" style="gap:6px">
        <input type="checkbox" id="cl-notes"> 同时删除 ${pv.notes||0} 篇笔记
      </label>
      <label class="row" style="gap:6px">
        <input type="checkbox" id="cl-mis"> 同时删除 ${pv.mistakes||0} 道错题
      </label>
    </div>
    <div class="row" style="margin-top:18px">
      <button class="act" id="cl-go">执行清理</button>
      <button class="ghost" onclick="closeModal()">取消</button>
    </div>`);
  $('#cl-go').onclick = async () => {
    const withNotes = $('#cl-notes').checked;
    const withMis = $('#cl-mis').checked;
    closeModal();
    try {
      const r = await api('/api/cleanup', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({dry_run: false, include_notes: withNotes,
                              include_mistakes: withMis})});
      const parts = [`${r.deleted_kps} 个失去出处的知识点`, `${r.reviews} 条复习记录`];
      if (withNotes) parts.push(`${r.notes||0} 篇笔记`);
      if (withMis) parts.push(`${r.mistakes||0} 道错题`);
      const summary = parts.join('、').replace(/^0 个失去出处的知识点、0 条复习记录、?/, '');
      toast(summary ? '已清理 ' + summary : '没有需要清理的记录');
      go('docs');
    } catch(e){ toast('清理失败：' + e.message); }
  };
}

async function openDoc(id){
  const d = await api('/api/doc?id='+id);
  // 跨页跳转（方案 §7.3 里"必须做的例外"）：先把子页签对齐到这份文档所属的库，
  // **再**进资料页。否则从问答页证据角标点开一份辅库文档，资料页会停在主库页签，
  // 用户看到的是一张空列表。从资料页内部点标题时 CURRENT 已是 docs、库也一致，
  // 不会多一次重渲染。
  const tab = (d.document && d.document.corpus) || 'main';
  if (DOCS_TAB !== tab || CURRENT !== 'docs') {
    DOCS_TAB = tab;
    await go('docs');
  }
  const o = d.outline.map(e => `
    <tr><td><a href="#" onclick="openChunk(${e.first_chunk});return false">
      ${esc(e.heading)}</a></td>
      <td class="small">${esc(e.ref_range||'')}</td>
      <td class="small">${e.n_chunks}</td>
      <td class="small">${e.chars}</td></tr>`).join('');
  modal(`
    <h2>${esc(d.document.title||d.document.path)}</h2>
    <div class="small muted" style="margin-bottom:12px">
      ${esc(d.document.path)} · ${d.document.n_chunks} 块 · ${d.document.n_chars} 字
    </div>
    ${d.document.warnings && d.document.warnings!=='[]'
      ? `<div class="small" style="color:var(--warn);margin-bottom:10px">⚠ ${
          esc(JSON.parse(d.document.warnings).join('；'))}</div>` : ''}
    <table><thead><tr><th>章节</th><th>出处</th><th>块数</th><th>字数</th></tr>
    </thead><tbody>${o}</tbody></table>`);
}

