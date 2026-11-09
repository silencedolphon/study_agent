// ---------------- 资料 ----------------
const fmtSize = b => b < 1024 ? b + ' B'
  : (b < 1048576 ? (b/1024).toFixed(1) + ' KB' : (b/1048576).toFixed(1) + ' MB');

// 资料页的两个子页签（卡 C-0021，方案 §7.2）：教材+课件 = 主库（main），
// 试题+资料笔记 = 辅库（aux）。
//
// 它是**资料页内部的二级状态**，不是主导航——VIEWS 仍是 8 项，不新增条目。
// 状态只存在内存里（方案 §7.3 选 S1：现状本就没有任何 URL 状态，加 hash 路由
// 要同步 20+ 处 go() 调用，属独立需求）。刷新回默认，与现状"刷新回问答页"一致。
let DOCS_TAB = 'main';
const DOCS_TABS = { main:'教材与课件', aux:'题库与笔记' };
// 已入库列表**始终**显示库徽章：在 main 页签里要能一眼看出有没有混进试题，
// 所以即使在 aux 页签（那里每行都是辅库）也不省略。
const CORPUS_BADGE = { main:{label:'主库', cls:'ok'}, aux:{label:'辅库', cls:'warn'} };

function docsTab(k){
  if (!DOCS_TABS[k] || k === DOCS_TAB) return;
  DOCS_TAB = k;
  go('docs');          // 复用主导航的渲染：切页签后进度条恢复等逻辑一并保留
}

const STATE = {
  new:       {label:'未入库', cls:'warn'},
  changed:   {label:'内容有变', cls:'warn'},
  indexed:   {label:'已入库', cls:'ok'},
};
const DOC_STATUS = {ok:'正常', partial:'部分完成', failed:'处理失败', error:'处理失败'};

let DOCS_PAGE_SUBJECT = '';
async function viewDocs(){
  if (!SUBJ.loaded) await subjLoad();
  const pageSubject = SUBJ.loaded ? SUBJ.cur : '';
  const [docsRes, rawRes] = await Promise.all([api('/api/docs'), api('/api/raw')]);
  if (!pageSubject || SUBJ.cur !== pageSubject) return;
  DOCS_PAGE_SUBJECT = pageSubject;
  const raw = rawRes;

  // 按当前子页签过滤：两个库的文件与已入库文档互不混显。
  // corpus 由**文件路径**决定（core/db.py 的 classify_corpus），不由文件名决定。
  const filesOf = k => raw.files.filter(f => (f.corpus||'main') === k);
  // 「待入库」= 本库里还没入库、且格式支持的文件。
  // 页签按钮的角标与下面卡片的角标**共用这一个函数**，两处数字必然相等，
  // 也等于列表里「未入库且支持」的行数。
  const pendingOf = k => filesOf(k).filter(f => f.state !== 'indexed' && f.supported).length;

  const documents = docsRes.documents.filter(d => (d.corpus||'main') === DOCS_TAB);
  const rawFiles  = filesOf(DOCS_TAB);
  const orphans   = (raw.orphans||[]).filter(o => (o.corpus||'main') === DOCS_TAB);
  // 计数取**本库**那一份（后端的 counts_by_corpus）。若这里仍用全量的 raw.counts，
  // 角标就会写"两个库加起来"的数，而列表只有当前库的行——正是方案 §7.4 末尾
  // 点名的那个可见 bug（"4 个待入库"但列表只有 1 个）。
  // C-0109：末尾 `|| {}` 是防御性兜底——后端若再返回 null 计数（这次的根因就是
  // `api_raw_list._counts()` 空实现返回 None，counts/counts_by_corpus 全是 null），
  // 这一行会退化成空对象，页面只少几个数字，不再整块抛 TypeError 报红。
  const c = (raw.counts_by_corpus || {})[DOCS_TAB] || raw.counts || {};

  const pending = pendingOf(DOCS_TAB);
  // 批量入库（卡 C-0037）的候选：本库里**未入库且格式支持**的文件。
  // 与上面 pendingOf 用的是同一个判据，所以角标数字与可勾选的行数必然相等。
  const pendingFiles = rawFiles.filter(f => f.state !== 'indexed' && f.supported);

  // 二级页签栏：选中态复用既有的 .ghost.on 样式，不新增 CSS。
  const tabBar = Object.entries(DOCS_TABS).map(([k, label]) => {
    const n = pendingOf(k);
    return `<button class="ghost ${k === DOCS_TAB ? 'on' : ''}"
      onclick="docsTab('${k}')">${label}${
      n ? ` <span class="badge warn">${n} 待入库</span>` : ''}</button>`;
  }).join(' ');

  // 「放到 raw\ 下的哪个子目录」的候选：本库真实存在的目录 + 本库的推荐目录。
  // 按当前子页签给候选，是为了让"路径决定库归属"这条规则在 UI 上可见；
  // 去重是因为推荐目录往往已经存在（如 raw\药理题库\）。
  const folderOpts = [...new Set([
    ...((raw.folders_by_corpus || {})[DOCS_TAB] || raw.folders || []).filter(Boolean),
    ...(DOCS_TAB === 'aux' ? ['药理题库', '学习笔记'] : ['教材', '课件']),
  ])];

  const rows = documents.map(d => {
    return `
    <tr>
      <td class="ds-td"><input type="checkbox" class="ds-cb" data-doc="${d.id}"
        title="选择这份资料"></td>
      <td><a href="#" onclick="openDoc(${d.id});return false">${esc(d.title||d.path)}</a>
        <div class="small muted">${esc(String(d.kind || '文件').toUpperCase())} · ${d.role === 'textbook' ? '教材' : '课件'}</div></td>
      <td>${d.status==='ok'
        ? '<span class="badge ok">正常</span>'
        : `<span class="badge warn">${esc(DOC_STATUS[d.status] || d.status || '待处理')}</span>`}</td>
      <td class="small muted">${fmtTime(d.indexed_at)}</td>
      <td class="ds-actions"><details class="row-actions"><summary aria-label="更多资料操作" title="更多操作">⋯</summary><div class="row-actions-menu">
        <button class="ghost" onclick="reindexOne(${d.id})"
          title="按文件内容重新整理">重新入库</button>
        <button class="ghost" onclick="openShareExport(${d.id},
          ${JSON.stringify(d.title||d.path).replace(/"/g,'&quot;')})"
          title="把这份资料打成分享包">导出分享包</button>
        <button class="ghost" onclick="renameDocTitle(${d.id},
          ${JSON.stringify(d.title||'').replace(/"/g,'&quot;')})"
          title="更改看板上显示的标题">修改标题</button>
        <button class="ghost" onclick="renameRawFile(${d.id},
          ${JSON.stringify(d.rel||'').replace(/"/g,'&quot;')},
          ${JSON.stringify((d.rel||'').split('/').pop()||'').replace(/"/g,'&quot;')})"
          title="更改原文件名称">重命名文件</button>
        <button class="ghost" onclick="deleteDoc(${d.id}, ${JSON.stringify(d.path).replace(/"/g,'&quot;')},
          ${JSON.stringify(d.title||d.path).replace(/"/g,'&quot;')})"
          style="color:var(--bad)">移除资料</button>
      </div></details></td>
    </tr>`;
  }).join('');

  const fileRows = rawFiles.map(f => {
    const st = STATE[f.state] || {label:f.state, cls:''};
    const acts = [];
    let primary = '';
    if (f.state === 'indexed') {
      acts.push(`<button class="ghost" onclick="reindex('${esc(f.rel)}', true)">重新入库</button>`);
      acts.push(`<button class="ghost" onclick="deleteDoc(${f.doc_id}, '${esc(f.rel)}', '${esc(f.name)}')"
        style="color:var(--bad)">移除资料</button>`);
    } else if (f.supported) {
      primary = `<button class="act" onclick="reindex('${esc(f.rel)}')">入库</button>`;
    }
    acts.push(`<button class="ghost" onclick="renameRawFile(${f.doc_id || 'null'}, ${JSON.stringify(f.rel).replace(/"/g,'&quot;')}, ${JSON.stringify(f.name).replace(/"/g,'&quot;')})"
      title="更改原文件名称">重命名文件</button>`);
    acts.push(`<button class="ghost" onclick="deleteRaw('${esc(f.rel)}', ${f.doc_id || 'null'})"
      style="color:var(--bad)">删除文件</button>`);
    return `<tr>
      <td><input type="checkbox" class="raw-delete-cb" data-rel="${esc(f.rel)}" aria-label="选择文件 ${esc(f.name)}"></td>
      <td>${f.doc_id ? `<a href="#" onclick="openDoc(${f.doc_id});return false">${esc(f.name)}</a>` : esc(f.name)}
        ${f.supported ? '' : '<span class="badge bad">格式不支持</span>'}
        <div class="small muted" title="${esc(f.rel)}">${esc(f.rel)}</div></td>
      <td class="small">${fmtSize(f.size)}</td>
      <td><span class="badge ${st.cls}">${st.label}</span></td>
      <td class="ds-actions"><div class="row-actions-wrap">${primary}
        <details class="row-actions"><summary aria-label="更多文件操作" title="更多操作">⋯</summary>
        <div class="row-actions-menu">${acts.join('')}</div></details></div></td>
    </tr>`;
  }).join('');

  const orphanNote = orphans.length ? `
    <details class="docs-orphans card" style="margin-top:14px">
      <summary>找不到原文件的资料 · ${orphans.length} 个</summary>
      <div class="sub" style="margin-bottom:10px">原文件可能已被移动或改名，可从列表移除这些记录。</div>
      <table><tbody>${orphans.map(o => `<tr>
        <td>${esc(o.path)}</td>
        <td><button class="ghost" onclick="deleteDoc(${o.id}, '${esc(o.path)}', '${esc(o.path)}')"
          style="color:var(--bad)">移除记录</button></td>
      </tr>`).join('')}</tbody></table>
    </details>` : '';

  $('#view').innerHTML = `
    ${docsRes.stale ? `<div class="card" style="border-color:#7a5a1e;background:#2a2113">
      <b>${esc(docsRes.stale)}</b>
    </div>` : ''}
    <div class="card">
      <div class="row" id="docs-tabs">${tabBar}</div>
    </div>
    <div class="card" style="margin-top:14px">
      <h2>添加资料</h2>

      <div class="sub" style="margin:10px 0 6px">
        粘贴文件或文件夹路径，也可以把文件拖到下方。
      </div>
      <div class="row">
        <!-- 卡 C-0074：占位符原来写的是作者本机的绝对路径（含开发者用户名）。
             这一栏是给**拿到 exe 的外部用户**看的，写别人的用户名既没用、
             又是把开发者痕迹印在了界面上。改成中性示例。 -->
        <input id="im-src" placeholder="粘贴本机路径，如 D:\\我的课件\\第一讲.pdf"
          style="flex:1;min-width:260px">
        <input id="im-sub" list="folder-list" placeholder="分类文件夹（可选）"
          style="width:210px">
        <button class="act" id="im-go">导入</button>
        <datalist id="folder-list">
          ${folderOpts.map(f=>`<option value="${esc(f)}">`).join('')}
        </datalist>
      </div>
      <div class="sub" style="margin-top:6px">大文件或整个文件夹建议使用本机路径。</div>

      <!-- 卡 C-0083：拖拽入库。刻意紧贴「方式一」——两者都是"把文件放进
           raw\\"，只是一个粘路径、一个拖文件。也刻意**不占**「方式N」编号：
           下面 C-0080 的分享包仍是「方式二」，那一节一个字都不动。 -->
      <div class="dz" id="dz">
        <div class="dz-hint" id="dz-hint">
          拖放文件到这里<br><span class="small">上传后可在下方选择入库</span>
        </div>
        <div class="dz-foot">
          <label><input type="checkbox" id="dz-auto">
            上传后立即入库（可能产生 AI 费用）</label>
        </div>
        <div class="dz-bar-wrap"><div class="dz-bar" id="dz-bar"></div></div>
        <div class="dz-foot">
          <span id="dz-state">等待文件…</span><span id="dz-pct"></span>
        </div>
        <div id="dz-log"></div>
      </div>
      <div class="sub" style="margin-top:6px">上传大文件失败时，请改用上方的本机路径。</div>

      <!-- 教材入库专线（卡 C-0089 的入口；C-0122 从重建里恢复）。
           落点刻意留在「拖拽说明」之后、「raw\\ 文案」之前，与 C-0089 当
           时一致：它和上面几节是并列的"把资料弄进来"的方式，不是入库动作。 -->
      <details class="docs-extra"><summary>导入教材</summary><div class="docs-extra-body">
      <div class="sub">教材会按章节整理，较大的文件可能需要较长时间。</div>
      <div class="row" style="margin-top:6px">
        <input id="tb-src"
          placeholder="教材文件或文件夹的本机路径，如 D:\\教材\\药理学 第6版.pdf"
          style="flex:1;min-width:260px">
        <button class="act" id="tb-go" onclick="tbIngest()">导入教材</button>
      </div>
      <div class="row" style="margin-top:8px">
        <label class="small muted"><input type="checkbox" id="tb-force">
          强制重新整理（已有知识点出处可能失效）</label>
      </div>
      <div id="tb-log"></div>
      </div></details>

      <div class="row docs-main-actions" style="margin-top:16px">
        <button class="act" onclick="reindex(null)">入库待处理文件</button>
        ${pending ? `<span class="badge warn">${pending} 个待入库</span>`
          : `<span class="badge ok">${c.unsupported ? '可入库的都已入库' : '全部已入库'}</span>${
              c.unsupported ? `<span class="badge bad">${c.unsupported} 个格式不支持</span>` : ''}`}
        <details class="row-actions"><summary aria-label="更多资料操作" title="更多操作">⋯</summary>
          <div class="row-actions-menu">
            <button class="ghost" onclick="go('docs')">刷新列表</button>
            <button class="ghost" onclick="reindex(null, true)">全部重新入库</button>
            <button class="ghost" onclick="openCleanup()">清理失效记录</button>
          </div></details>
      </div>

      <!-- 方式二：导入分享包（卡 C-0080）。刻意放在卡片末尾：不动上面任何一段
           既有布局与文案，只往下接一节。 -->
      <details class="docs-extra"><summary>导入分享包</summary><div class="docs-extra-body">
      <div class="sub">一键导入课件或教材分享包，包含的切块、预习与知识点会一起导入。</div>
      <div class="row" style="margin-top:8px">
        <input id="sh-zip" placeholder="分享包完整路径，如 D:\\下载\\第一讲-share.zip"
          style="flex:1;min-width:260px">
        <button class="act" id="sh-open">查看并导入</button>
        <button class="act" id="sh-direct">一键导入当前学科</button>
        <button class="ghost" id="sh-pick">选择本机分享包</button>
      </div>
      </div></details>
    </div>
    <details class="docs-batch" ${BATCH.id?'open':''}><summary>批量选择文件入库</summary>${batchCard(pendingFiles)}</details>

    <div class="card" style="margin-top:14px">
      <h2>本机文件 · ${rawFiles.length} 个</h2>
      ${rawFiles.length ? `<div class="row" style="margin:10px 0"><label><input type="checkbox" id="raw-delete-all"> 全选文件</label><button class="ghost" id="raw-delete-selected" style="color:var(--bad)">批量删除文件…</button><span class="small muted">已入库和未入库文件均可选择</span></div><table><thead><tr><th>选择</th><th>文件</th><th>大小</th><th>状态</th>
        <th>操作</th></tr></thead><tbody>${fileRows}</tbody></table>`
        : `<div class="empty">这里还没有文件。可以从上方添加资料。</div>`}
    </div>

    <div class="card" id="docs-indexed" style="margin-top:14px">
      <h2>已入库资料 · ${documents.length} 个</h2>
      ${documents.length ? `<details class="docs-bulk" id="ds-bar" open><summary>批量删除已入库资料 <span class="small muted" id="ds-info">未选择</span></summary><div class="row">
        <button class="ghost" id="ds-all">全选</button>
        <button class="ghost" id="ds-none">取消选择</button>
        <button class="ghost" id="ds-del" style="color:var(--bad)">删除所选…</button>
      </div></details>` : ''}
      ${documents.length ? `<table><thead><tr><th class="ds-select-head" style="width:30px"></th><th>资料</th>
        <th>状态</th><th>添加时间</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody></table>`
        : `<div class="empty">「${DOCS_TABS[DOCS_TAB]}」还没有已入库的资料。</div>`}
    </div>
    ${orphanNote}`;
  bindUpload();
  bindDropzone();   // 卡 C-0083：拖拽入库。bindUpload() 内部一行都没动
  bindBatch(pendingFiles);
  docSelReset();    // 卡 C-0084：批量真删的选中集。上面三行一行都没动
  const rawAll = $('#raw-delete-all');
  if (rawAll) rawAll.onchange = () => document.querySelectorAll('.raw-delete-cb').forEach(cb => { cb.checked = rawAll.checked; });
  const rawDelete = $('#raw-delete-selected');
  if (rawDelete) rawDelete.onclick = deleteSelectedRawFiles;
}

async function deleteSelectedRawFiles(){
  const paths = Array.from(document.querySelectorAll('.raw-delete-cb:checked')).map(cb => cb.dataset.rel);
  if (!paths.length) return toast('请先勾选要删除的文件');
  const expected_subject = DOCS_PAGE_SUBJECT;
  if (!expected_subject) return toast('学科信息尚未加载，请刷新页面');
  try {
    const plan = await api('/api/raw/delete-batch', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({paths, expected_subject, dry_run:true})});
    const ok = await confirmBox('批量删除文件？', `将永久删除 ${paths.length} 个文件，其中 ${plan.indexed} 个已入库文件的索引和部分整理结果也会删除。教材的按章预习和总结文件可能保留。此操作不可撤销。`);
    if (!ok) return;
    const result = await api('/api/raw/delete-batch', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({paths, expected_subject, signatures:plan.signatures, confirm:true})});
    const errors = result.errors || [];
    if (errors.length) {
      modal(`<h2>批量删除结果</h2><div class="sub">已删除 ${result.deleted} 个文件；以下文件删除未完全成功：</div><div class="small" style="margin:12px 0;white-space:pre-wrap">${errors.map(x=>esc(x.path + '：' + x.error)).join('<br>')}</div><button class="ghost" onclick="closeModal();go('docs')">关闭</button>`);
      return;
    }
    toast(`已删除 ${result.deleted} 个文件`);
    await go('docs');
  } catch(e){ toast('批量删除失败：' + e.message); }
}

function bindUpload(){
  // 方式一：从本机路径导入
  const imp = $('#im-go');
  if (imp) imp.onclick = async () => {
    const src = $('#im-src').value.trim();
    if (!src) return toast('请填写本机路径');
    const sub = $('#im-sub').value.trim();
    imp.disabled = true; imp.textContent = '导入中…';
    try {
      const r = await api('/api/import', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({src, sub})});
      if (r.error) { toast(r.error); return; }
      let msg = `已导入 ${r.copied} 个文件（${fmtSize(r.total_bytes)}）到 raw\\${sub}`;
      if (r.renamed) msg += `，${r.renamed} 个重命名`;
      if (r.skipped) msg += `，${r.skipped} 个跳过`;
      if (r.errors && r.errors.length) msg += `，${r.errors.length} 个失败`;
      toast(msg);
      go('docs');
    } catch(e){
      toast('导入失败：' + e.message);
    } finally {
      imp.disabled = false; imp.textContent = '导入';
    }
  };

  // 方式二：导入分享包（卡 C-0080）。按钮行为与「方式一」并列，**不改动上面
  // 任何一行**——方式一的路径导入逻辑逐字保留。
  const shOpen = $('#sh-open');
  if (shOpen) shOpen.onclick = () => {
    const z = $('#sh-zip').value.trim();
    if (!z) return toast('请先填写分享包路径，或点「从 share\\ 目录选一个」');
    openShareImport(z);
  };
  const shPick = $('#sh-pick');
  if (shPick) shPick.onclick = () => sharePickFromDir();
  const shDirect = $('#sh-direct');
  if (shDirect) shDirect.onclick = () => shareImportDirect($('#sh-zip').value.trim());

}

// ---------------- 拖拽入库（卡 C-0083）----------------
//
// 需求：入库方式除了「粘贴本机路径」，还要能**把文件拖进入库区**就入库。
//
// 为什么非走上传不可：浏览器出于安全只把 File 对象交给页面、**不给
// file.path**（Chrome/Edge 都是如此）⇒ 拖拽拿不到本机路径，只能把字节
// 真的传一遍。于是它天生受浏览器/连接层限制：单文件几百 MB 会 fail to
// fetch（用户实测 645MB），那种文件要走「方式一」——那条路不经过 HTTP。
//
// 落盘位置与「方式一」完全一致（raw\\ + #im-sub 里的子文件夹值），
// 落盘后同样**不自动入库**：入库要识图（约 25 秒/页且按页计费），
// 默认交给人去「批量入库」勾选。只有勾了复选框才接着跑入库。
//
// 上传用的是既有的 `/api/upload` → `api_upload()` → `_parse_multipart_stream()`
// ——**没有为拖拽另写一套上传通道**（流式落盘、重名 _1、文件名清洗都是现成的）。
const DZ_MAX = 256 * 1024 * 1024;    // ⚠ 必须等于 dashboard.py 的 MAX_UPLOAD_BYTES
const DZ_NOTIFY = 32 * 1024 * 1024;  // 超过这个体积先提醒一句（仍然照传）
const DZ_KIND = {pdf:'PDF', docx:'Word(.docx)', pptx:'PPT(.pptx)', image:'图片',
  text:'文本', legacy:'旧版 Office（.doc/.ppt/.xls）', unknown:'看板不认识的格式'};
let DZ_LAST = '';      // 上一次上传的结论：刷新列表会重建 DOM，这里把它接回来
let DZ_FLASH = false;  // 刷新后要把「批量入库」卡片标一下
let DZ_BUSY = false;   // 一次只跑一批

// 与后端 api_import_path 的 supported 判据逐字一致（只有 unknown 不支持；
// 旧版 Office 会由入库管线自动转换）。
function dzKindOk(kind){ return !!kind && kind !== 'unknown'; }

function dzFlashBatch(){
  const el = $('#batch-list') || $('#batch-progress');
  const card = el ? el.closest('.card') : null;
  if (!card) return;
  card.classList.add('flash');
  setTimeout(() => card.classList.remove('flash'), 3600);
  card.scrollIntoView({behavior:'smooth', block:'center'});
}

function bindDropzone(){
  const zone = $('#dz');
  if (!zone) return;
  const log = $('#dz-log');
  const state = $('#dz-state');
  const pctEl = $('#dz-pct');
  const bar = $('#dz-bar');
  let depth = 0;       // dragenter/dragleave 会因子元素来回触发，用计数消抖
  let lastHtml = '';

  const say = (html, cls, keep) => {
    const h = `<div class="dz-line ${cls || ''}">${html}</div>`;
    if (log) log.insertAdjacentHTML('beforeend', h);
    if (keep) lastHtml += h;
  };
  const setPct = (p, text) => {
    if (bar) bar.style.width = p + '%';
    if (pctEl) pctEl.textContent = text === undefined ? (p ? p + '%' : '') : text;
  };

  if (DZ_LAST && log) log.insertAdjacentHTML('beforeend', DZ_LAST);  // 上次的结论
  if (DZ_FLASH){ DZ_FLASH = false; setTimeout(dzFlashBatch, 80); }

  // ★ dragover / dragenter **必须** preventDefault：不拦的话浏览器把拖进来的
  //   文件当成"导航目标"直接打开，当前页面（连同填了一半的输入框）被顶掉。
  ['dragenter','dragover','dragleave','drop'].forEach(t =>
    zone.addEventListener(t, e => { e.preventDefault(); e.stopPropagation(); }));
  zone.addEventListener('dragenter', () => { depth++; zone.classList.add('on'); });
  zone.addEventListener('dragover',  () => zone.classList.add('on'));
  zone.addEventListener('dragleave', () => {
    if (--depth <= 0){ depth = 0; zone.classList.remove('on'); }
  });
  zone.addEventListener('drop', e => {
    depth = 0; zone.classList.remove('on');
    dzHandle(e.dataTransfer);
  });

  // 拖到区域**外**松手也不许浏览器把文件打开。**只装一次**：
  // go('docs') 每次刷新都会重建拖放区，若在这里无脑 addEventListener，
  // 切几次页就会攒下一串监听器、一次拖放弹好几个提示。
  if (!window.__dzWinBound){
    window.__dzWinBound = true;
    window.addEventListener('dragover', e => { if ($('#dz')) e.preventDefault(); });
    window.addEventListener('drop', e => {
      const z = $('#dz');
      if (!z || z.contains(e.target)) return;
      e.preventDefault();
      toast('请把文件拖到「拖拽入库」那个虚线框里，页面其它位置不接收文件');
    });
  }

  async function dzHandle(dt){
    if (!dt) return;
    if (DZ_BUSY) return toast('上一批还在上传，等它结束再拖');
    const files = Array.from(dt.files || []);

    if (!files.length){
      // 拖文件夹时 dataTransfer.files 是空的，但 items 里的 entry.isDirectory
      // 看得出来——两种"空"要给不同的话，不能只说一句"没有文件"。
      let dir = false;
      try {
        dir = Array.from(dt.items || []).some(it => it.kind === 'file' &&
          it.webkitGetAsEntry && (it.webkitGetAsEntry() || {}).isDirectory);
      } catch(_){}
      return say(dir
        ? '拖进来的是<b>文件夹</b>。看板这一版不收文件夹（浏览器不把目录树交给网页）。'
          + '请把文件夹路径粘到上面「方式一」的输入框——那条路整目录导入，没有大小限制。'
        : '没有拿到文件。若拖的是网页里的图片或链接，请先「另存为」到本机再拖；'
          + '若拖的是文件夹，请改用上面「方式一」粘路径。', 'bad', true);
    }

    // 前端先分流：后端也会拦（Content-Length 超限直接 400），但在这里拦能
    // **指名道姓**地说出是哪个文件、有多大、下一步该走哪条路。
    const over = files.filter(f => f.size > DZ_MAX);
    const ok = files.filter(f => f.size <= DZ_MAX);
    if (over.length) say(`<b>${over.length} 个文件超过单次上传上限 `
      + `${fmtSize(DZ_MAX)}</b>，没有上传：`
      + over.map(f => `${esc(f.name)}（${fmtSize(f.size)}）`).join('、')
      + '。请改用上面的「方式一」把它的路径粘进去——那条路直接读磁盘，不看体积。',
      'bad', true);
    if (!ok.length){ DZ_LAST = lastHtml; return; }

    const sub = ($('#im-sub') && $('#im-sub').value.trim()) || '';
    const where = 'raw\\' + (sub ? sub + '\\' : '');
    const total = ok.reduce((a, f) => a + f.size, 0);
    const big = ok.filter(f => f.size > DZ_NOTIFY);
    if (big.length) say(`${big.length} 个文件偏大（${big.map(f => fmtSize(f.size)).join('、')}），`
      + '上传可能很慢、也可能被浏览器掐断；真失败了就改用「方式一」。', 'warn', true);

    const fd = new FormData();
    // ★ folder 必须**排在文件之前**：后端是边收边落盘的流式解析，遇到第一个
    //   文件时才去解析目标目录（见 dashboard.py 的 _parse_multipart_stream）。
    //   顺序反了，文件就落到 raw 根目录而不是子文件夹里。
    fd.append('folder', sub);
    ok.forEach(f => fd.append('files', f, f.name));

    DZ_BUSY = true;
    setPct(0, '0%');
    if (state) state.textContent = `正在上传 ${ok.length} 个文件（${fmtSize(total)}）…`;
    say(`开始上传 ${ok.length} 个文件（共 ${fmtSize(total)}）到 <kbd>${esc(where)}</kbd>…`,
        'run', true);

    let r = null, err = '';
    const t0 = Date.now();
    try {
      r = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/upload');
        // ⚠ 刻意不设 Content-Type：boundary 由浏览器生成，手写会把它弄丢，
        //   后端解析必然失败。也刻意不设 timeout：几百 MB 可能传很久，
        //   超时反而误杀；连接层真失败会走 onerror。
        xhr.upload.onprogress = e => {
          if (e.lengthComputable) setPct(Math.round(e.loaded * 100 / e.total));
        };
        xhr.onload = () => {
          let j = null;
          try { j = JSON.parse(xhr.responseText || 'null'); } catch(_){}
          if (xhr.status >= 200 && xhr.status < 300 && j && !j.error) resolve(j);
          else reject(new Error((j && j.error) || ('HTTP ' + xhr.status)));
        };
        // 几百 MB 撞上浏览器/连接层限制时拿到的是**没有响应**的失败（用户实测
        // 645MB 就是这样，本机服务端根本没收到请求）。这里不能只回一句"失败"，
        // 要告诉人下一步走哪条路。
        xhr.onerror = () => reject(new Error(
          '浏览器没能把文件传出去（连接层失败，看板服务端通常根本没收到）。'
          + '单个文件太大时会这样——请改用上面的「方式一」粘贴路径导入。'));
        xhr.send(fd);
      });
    } catch(e){ err = e.message || String(e); }
    DZ_BUSY = false;

    if (err){
      setPct(0, '');
      if (state) state.textContent = '上传失败';
      say(`<b>上传失败</b>：${esc(err)}`, 'bad', true);
      DZ_LAST = lastHtml;
      toast('上传失败：' + err);
      return;
    }

    const rows = r.saved || [];
    const bad = rows.filter(s => !dzKindOk(s.kind));
    const renamed = rows.filter(s => s.renamed);
    const pend = rows.length - bad.length;
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    setPct(100, '');
    if (state) state.textContent = '上传完成';
    say(`<b>已放进 ${esc(where)}</b>：${rows.length} 个文件，共 ${fmtSize(total)}，`
      + `用时 ${secs} 秒。`, 'ok', true);
    if (renamed.length) say(`${renamed.length} 个文件在 raw\\ 里已有同名，按现有规则`
      + `自动改名（加 _1 等），<b>没有覆盖</b>已有文件：`
      + renamed.map(s => esc(s.name)).join('、') + '。', '', true);
    if (bad.length) say(`${bad.length} 个文件格式不入库管线：`
      + bad.map(s => `${esc(s.name)}（${esc(DZ_KIND[s.kind] || s.kind)}）`).join('、')
      + '。可入库的是 PDF / Word(.docx) / PPT(.pptx) / 图片 / 文本；'
      + '旧版 `.doc/.ppt/.xls` 也可以直接拖，入库时会用本机 Office 自动转换'
      + '（转换需要几秒到几十秒，源文件保留）。'
      + '这些文件仍然留在 raw\\ 里，只是不会进入入库队列。', 'bad', true);

    const auto = $('#dz-auto') && $('#dz-auto').checked;
    if (pend){
      say(`本次有 <b>${pend} 个待入库</b>：文件已经放进 raw\\，<b>没有自动入库</b>`
        + `——入库要识图（约 25 秒/页且按页计费）。请到下面的「批量入库」勾选后开始。`,
        'warn', true);
      toast(`已上传 ${rows.length} 个文件，${pend} 个待入库（未自动入库）`);
      DZ_FLASH = true;
      setTimeout(dzFlashBatch, 2500);   // 等下面刷新完再标，位置才对得上
      if (auto){
        say('已勾选「上传后立即入库」：接着跑入库。它会把 raw\\ 里'
          + '<b>全部待入库文件</b>一起入（含之前遗留的），识图按页计费。', 'warn', true);
        setTimeout(() => reindex(null), 1200);
      }
    } else {
      toast(`已上传 ${rows.length} 个文件，其中没有可入库的格式`);
    }
    DZ_LAST = lastHtml;
    // 刷新一次：顶部的「N 个待入库」角标、raw\\ 文件列表、批量入库候选都要跟上。
    // 延后 2.6 秒是为了让人先看清上面这几行结论。
    setTimeout(() => { if ($('#dz')) go('docs'); }, 2600);
  }
}

// ---- 入库进度条 ----
//
// 入库可能跑十几分钟到几小时（识图约 25 秒/页），只弹一句 toast 的话
// 用户完全不知道是在跑、卡住了、还是已经死了。
// 进度状态在后端是进程内的一个字典，这里每秒轮询一次。
// 轮询接口**不加库锁**（否则会和正在入库的请求抢锁，进度永远不动）。
let _progTimer = null;

function _fmtDur(sec){
  sec = Math.max(0, Math.round(sec||0));
  if (sec < 60) return sec + ' 秒';
  const m = Math.floor(sec/60), s = sec%60;
  if (m < 60) return m + ' 分' + (s ? s + ' 秒' : '');
  return Math.floor(m/60) + ' 小时' + (m%60 ? (m%60) + ' 分' : '');
}

// 卡 C-0124：知识点整理进度条的**秒级心跳**（在两次 1.5 秒轮询之间本地走秒）。
// 后端 `since_last_call_s` 只在调用返回时更新，若只显示它，界面每 15 分钟才动一次，
// 看着就是卡死；这里用后端给的基准 + 本地时间差把它续成连续走秒。
// ⚠ 必须声明在 `renderProgress()` **之前** —— 它是 `let`，落在后面会踩 TDZ
// （`Cannot access 'KP_HEART' before initialization`），整块进度渲染直接不执行。
let KP_HEART = { at: 0, done: -1, since: 0 };

function renderProgress(p, sel){
  // sel 默认入库进度条；知识点整理（卡 C-0035）复用同一个渲染，只是换容器。
  const box = $(sel || '#ingest-progress');
  if (!box) return;
  if (!p || (!p.active && !p.status)) { box.innerHTML = ''; return; }
  const pct = p.percent || 0;
  const stepTxt = p.total ? `${p.done}/${p.total}` : '';
  const files = (p.files_total > 1)
    ? `<span class="muted">文件 ${Math.min(p.files_done+1, p.files_total)}/${p.files_total}</span>` : '';
  const stageRow = (p.stages||[]).map((s, i) => {
    const cls = !p.active ? 'done'
      : (i < p.stage_index ? 'done' : (i === p.stage_index ? 'now' : ''));
    return `<span class="pstep ${cls}">${esc(s)}</span>`;
  }).join('<span class="muted"> › </span>');
  const tail = p.active
    ? `${files}<span class="muted">已用 ${_fmtDur(p.elapsed)}</span>`
    : (p.status === 'done'
        ? `<span class="badge ok">完成</span><span class="muted">用时 ${_fmtDur(p.elapsed)}</span>`
        : `<span class="badge bad">失败</span><span class="muted">${esc(p.error||'')}</span>`);
  // 预习失败后给一个明确的"重新预习"入口（不用回章节列表里找）。
  const retryBtn = (!p.active && p.status === 'failed' && p.kind === 'preview'
    && typeof PV !== 'undefined' && PV.docId)
    ? `<button class="ghost" style="margin-left:8px" onclick="pvRetryFromProgress()">重新预习</button>`
    : '';
  // 卡 C-0124：知识点整理**单次调用实测最慢 914 秒**（15 分钟），期间阶段名与条数
  // 都不变 ⇒ 界面看着就是卡死。这里补一个**心跳**：把"这一条已经等了多久"和
  // "同类调用的中位耗时"直接写出来，并在两次轮询之间**本地走秒**（轮询 1.5 秒一次，
  // 但秒数要连续动，用户才看得出进程还活着）。
  let kpExtra = '';
  if (p.kind === 'kp_build' && p.active) {
    let waited = Number(p.since_last_call_s || 0);
    if (KP_HEART.at && KP_HEART.done === (p.kp_done||0)) {
      waited = KP_HEART.since + (Date.now() - KP_HEART.at)/1000;
    }
    KP_HEART = {at: Date.now(), done: (p.kp_done||0), since: waited};
    const eta = Number(p.eta_call_s || 0);
    const beat = waited >= 5
      ? `<span class="muted" title="模型网关高峰期单次调用可能十几分钟；只要这个秒数在走，进程就是活的">
           ⏳ 当前一条等待 <b>${_fmtDur(waited)}</b>${eta ? `（同类中位 ${_fmtDur(eta)}）` : ''}</span>`
      : '';
    kpExtra = `<span class="muted">已出 ${p.kp_done||0}/${p.kp_total||0} 条 ·
                 CNY${(p.cost_cny||0).toFixed(4)}</span>${beat}`;
  }
  box.innerHTML = `
    <div class="pwrap">
      <div class="phead">
        <b>${esc(p.label||'')}</b>
        <span class="muted">${esc(p.stage||'')} ${esc(stepTxt)} ${esc(p.note||'')}</span>
        <span style="flex:1"></span>${kpExtra}${tail}${retryBtn}
      </div>
      <div class="pbar${p.active ? ' active' : ''}"><i style="width:${pct}%"></i></div>
      <div class="pfoot">${stageRow}<span style="flex:1"></span><b>${pct}%</b></div>
    </div>`;
}

let _ingestRequestActive = false;
async function pollProgress(){
  try {
    const p = await api('/api/ingest/progress');
    renderProgress(p);
    if (p.active || _ingestRequestActive) return true;
    // 跑完了：停在结果上 5 秒再收起来，否则用户什么都没看清就没了
    if (p.status) { setTimeout(() => { const b=$('#ingest-progress'); if(b) b.innerHTML=''; }, 5000); }
    return false;
  } catch(e){ return false; }
}

function watchProgress(){
  if (_progTimer) return;
  const tick = async () => {
    const still = await pollProgress();
    if (!still) { clearInterval(_progTimer); _progTimer = null; }
  };
  tick();
  _progTimer = setInterval(tick, 1000);
}

// ---------------- 教材入库专线（卡 C-0089；C-0122 从重建中恢复）----------------
//
// 与「方式一」的分工：方式一只把文件放进 raw 目录，入不入库、怎么入库由下面的
// 「入库」按钮决定；这个按钮**一条龙**跑完整条教材专线（拷进 raw 的教材目录
// → 本地免费 OCR 识图 → 按章切块 → 入库）。
//
// 人已拍板"一条龙、不用问我"，所以这里**没有**确认步骤。省下的那点交互，
// 用"把识别到的章列出来"（tbRender）补回去——事后能核对，才敢不问。
async function tbIngest(){
  const el = $('#tb-src');
  const src = (el && el.value || '').trim();
  if (!src) return toast('请先填教材文件或文件夹的本机路径');
  const cb = $('#tb-force');
  const force = !!(cb && cb.checked);
  const btn = $('#tb-go');
  if (btn){ btn.disabled = true; btn.textContent = '教材入库中…'; }
  watchProgress();   // 请求会一直挂着（几百页教材要一两个小时），先开轮询
  toast('教材入库中：拷进 raw 的教材目录 → 本地 OCR → 按章切块。'
        + '长任务，请别关看板…');
  try {
    const r = await api('/api/ingest/textbook', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({src, force})});
    toast(r.error || r.summary || '教材入库完成');
    await go('docs');      // 先刷页面（顺带刷新列表），再把回执渲染回去
    tbRender(r);
  } catch(e){
    toast('教材入库失败：' + e.message);
  } finally {
    if (btn){ btn.disabled = false; btn.textContent = '选择教材并入库'; }
    pollProgress();
  }
}

// 入库回执：**逐份**列出识别到的章（章号 + 章名 + 页范围）与块数变化。
// 这是"一条龙、不做逐步确认"这个选择的对价——没有确认步骤，就必须让人
// 事后看得见到底发生了什么（章识别降级也照实标出来）。
function tbRender(r){
  const box = $('#tb-log');
  if (!box) return;
  const docs = (r && r.docs) || [];
  if (!docs.length){
    box.innerHTML = r && r.error
      ? `<div class="sub" style="margin-top:10px;color:var(--bad)">教材入库未执行：${esc(r.error)}</div>`
      : '';
    return;
  }
  const rows = docs.map(d => {
    const chs = (d.chapters || []).map(c => {
      const pg = c.page_start
        ? 'p.' + c.page_start + (c.page_end && c.page_end !== c.page_start
            ? '-' + c.page_end : '')
        : '—';
      return `<tr><td>${esc(c.label)}</td><td class="small">${esc(pg)}</td></tr>`;
    }).join('');
    const statusText = {added:'已导入', updated:'已更新', failed:'处理失败', skipped:'已跳过'}[d.status]
      || DOC_STATUS[d.status] || d.status || '已处理';
    const head = `<div class="small" style="margin-top:10px">
      <b>${esc(d.title || d.path)}</b>
      <span class="badge ${d.status === 'failed' ? 'bad' : d.status === 'skipped' ? 'warn' : 'ok'}">${esc(statusText)}</span>
      ${d.chapters_n != null ? `<span class="muted">识别到 ${d.chapters_n} 章</span>` : ''}
      ${d.fallback ? ' <span class="badge warn">章节识别不完整，已按整本书导入</span>' : ''}
      ${d.reason ? `<span class="muted">${esc(d.reason)}</span>` : ''}
    </div>`;
    const table = chs
      ? `<table><thead><tr><th>章</th><th>页范围</th></tr></thead>
          <tbody>${chs}</tbody></table>`
      : `<div class="small muted">没有识别到章节</div>`;
    const warn = (d.warnings || []).length
      ? `<div class="sub small" style="color:var(--warn)">${d.warnings.map(esc).join('<br>')}</div>`
      : '';
    return head + table + warn;
  }).join('');
  box.innerHTML = `<div class="sub" style="margin-top:12px"><b>教材入库结果</b></div>` + rows;
}

async function reindex(rel, force, noFigures){
  let autoKp = false;
  try {
    const options = await api('/api/ingest/options');
    autoKp = !!options.auto_kp;
    if (autoKp && !await confirmBox('入库并整理知识点？',
      '课件入库后将自动整理知识点，会额外调用模型并产生费用；调用次数和费用需入库后才能按实际材料确定。已有完整总结会复用。' +
      (rel ? '' : '\n\n已入库但尚无完整总结的课件也会检查并整理。') +
      '\n\n可在入库设置中关闭。')) return;
  } catch(e){ toast('无法读取入库设置：' + e.message); return; }
  // 识图会让入库慢很多（实测约 25 秒/页），所以提示里要说清楚。
  // 批量入库仍建议走命令行 tools/reimport_all.py —— 按资料提交、中断可续。
  _ingestRequestActive = true;
  watchProgress();   // 入库与知识点阶段之间也保持轮询
  if (rel) go('docs');
  toast(rel
    ? (noFigures ? '正在入库（跳过识图）…' : '正在入库（含图片理解，可能需要很久）…')
    : '正在入库全部待处理文件，可能要几分钟到几小时…');
  try {
    const body = {force: !!force, auto_kp: autoKp};
    if (rel) body.paths = [rel];
    if (noFigures) body.no_figures = true;
    const r = await api('/api/ingest', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    if (r.error) { toast(r.error); return; }
    const bad = (r.details||[]).filter(d => d.status !== 'added' && d.status !== 'updated');
    let msg = r.summary;
    if (bad.length) msg += '｜' + bad.slice(0,2).map(d =>
      `${d.path}: ${d.reason||d.status}`).join('；');
    toast(msg);
    go('docs');
  } catch(e){ toast('入库失败：' + e.message); }
  finally { _ingestRequestActive = false; pollProgress(); }   // 收尾时立刻刷新
}

async function reindexOne(docId){
  // 从已入库文档反查它在 raw 里的相对路径，再触发重建。
  // 必须用后端给的 d.rel（相对 raw/，卡 C-0023）：d.path 是相对**项目根**的
  // （raw\a\b.docx），直接递给 /api/ingest 会被拼成 raw\raw\... 报「路径不存在」。
  const {documents} = await api('/api/docs');
  const d = documents.find(x => x.id === docId);
  if (!d) return toast('找不到该文档');
  if (!d.rel) return toast('该文档没有对应的 raw/ 相对路径，无法重建');
  reindex(d.rel, true);
}

function purgeSummary(p){
  if (!p || !p.orphan_kps) return '';
  const names = (p.names||[]).slice(0,6).join('、');
  return `\n· 另有 ${p.orphan_kps} 个失去出处的知识点会被清理` +
         (names ? `（${names}${p.names.length>6?'…':''}）` : '') +
         (p.reviews ? `\n  含 ${p.reviews} 条复习记录` : '') +
         (p.mistakes_unlinked ? `\n· ${p.mistakes_unlinked} 道相关错题会保留` : '');
}

async function deleteDoc(docId, rel, label){
  if (docId === null || docId === undefined) return toast('该文件尚未入库');
  // 先问后端"删掉它会影响什么"，把影响范围摆给用户看，而不是让他猜
  let pv = null;
  try { pv = await api('/api/doc/delete', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({doc_id: docId, dry_run: true})}); } catch(e){}
  const purge = purgeSummary(pv && pv.orphan_preview);
  modal(`
    <h2>移除资料</h2>
    <div style="margin:12px 0">${esc(label)}</div>
    <div class="sub" style="line-height:1.9">
      · 问答和搜索将不再引用这份资料<br>
      · 相关知识点会解除与它的关联<br>
      · <b>电脑里的原文件会保留</b>，以后可以重新入库
      ${purge ? '<br>' + esc(purge).replace(/\n/g,'<br>') : ''}
    </div>
    <div class="row" style="margin-top:18px">
      <button class="act" id="cd-ok">确认移除</button>
      <button class="ghost" onclick="closeModal()">取消</button>
    </div>`);
  $('#cd-ok').onclick = async () => {
    closeModal();
    try {
      const r = await api('/api/doc/delete', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({doc_id: docId, delete_file: false,
                              purge_orphans: true})});
      let msg = `已移除资料：${r.title}`;
      const g = r.purged || {};
      if (g.deleted_kps) msg += `；已清理 ${g.deleted_kps} 个失去出处的知识点`;
      toast(msg);
      go('docs');
    } catch(e){ toast('移除失败：' + e.message); }
  };
}

async function deleteRaw(rel, docId){
  let pv = null;
  if (docId) {
    try { pv = await api('/api/doc/delete', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({doc_id: docId, dry_run: true})}); } catch(e){}
  }
  const purge = purgeSummary(pv && pv.orphan_preview);
  modal(`
    <h2 style="color:var(--bad)">永久删除文件</h2>
    <div style="margin:12px 0">${esc(rel)}</div>
    <div class="sub" style="line-height:1.9">
      · 文件会<b>从电脑中永久删除</b>，无法撤销${docId ? '<br>· 资料库里的内容也会移除' : ''}
      ${purge ? '<br>' + esc(purge).replace(/\n/g,'<br>') : ''}
    </div>
    <div class="sub" style="margin-top:12px">
      如果只想从资料库移除，请使用「移除资料」，原文件会保留。
    </div>
    <div class="row" style="margin-top:18px">
      <button class="act" id="dr-del" style="background:var(--bad);color:#fff">确认永久删除</button>
      <button class="ghost" onclick="closeModal()">取消</button>
    </div>`);
  $('#dr-del').onclick = async () => {
    closeModal();
    try {
      if (docId) {
        const r = await api('/api/doc/delete', {method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({doc_id: docId, delete_file: true,
                                purge_orphans: true})});
        const g = r.purged || {};
        toast(`文件与资料记录已删除${g.deleted_kps ? `，清理 ${g.deleted_kps} 个失去出处的知识点` : ''}`);
      } else {
        await api('/api/raw/delete', {method:'POST',
          headers:{'Content-Type':'application/json'}, body: JSON.stringify({rel})});
        toast('文件已删除');
      }
      go('docs');
    } catch(e){ toast('删除失败：' + e.message); }
  };
}

