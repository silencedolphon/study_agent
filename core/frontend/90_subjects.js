// ---------------- 学科切换（卡 C-0039） ----------------
// 学科 = 一套**完全独立**的知识库（课件 / 知识点 / 笔记 / 错题各自独立）。
// 切换**不需要重启看板**：后端重绑 config 路径并清进程内缓存，
// 前端把下面 `subjResetFrontend()` 里那份清单清干净后按当前页重渲染。
const SUBJ = { list: [], cur: 'default', paths: {}, busy: '', loaded: false };

async function subjLoad(){
  const d = await api('/api/subjects');
  SUBJ.list = d.subjects || [];
  SUBJ.cur = d.current || 'default';
  SUBJ.paths = d.paths || {};
  SUBJ.busy = d.busy || '';
  SUBJ.loaded = true;
  subjPaint();
  return d;
}

function subjCurName(){
  const s = SUBJ.list.find(x => x.slug === SUBJ.cur);
  return s ? s.name : SUBJ.cur;
}

function subjPaint(){
  const nm = $('#subj-name');
  if (nm) nm.textContent = SUBJ.loaded ? subjCurName() : '…';
  const m = $('#subj-menu');
  if (!m) return;
  const items = SUBJ.list.map(s => `
    <button class="sitem${s.slug===SUBJ.cur?' on':''}" data-slug="${esc(s.slug)}"
      title="${esc(s.dir||'')}">
      <span>${s.slug===SUBJ.cur?'●':'○'}</span>
      <span class="snm">${esc(s.name)}</span>
      <span class="smeta">${s.slug===SUBJ.cur ? '当前' : (s.db_exists ? '' : '空')}</span>
    </button>`).join('');
  m.innerHTML = `<div class="shead">切换学科知识库</div>
    ${items || '<div class="shead">（还没有学科）</div>'}
    <div class="ssep"></div>
    <button class="sitem snew" id="subj-new">＋ 新建学科</button>
    <button class="sitem snew" id="subj-ren">✎ 重命名学科…</button>
    <div class="sfoot" title="当前知识库的真实路径（用它核对隔离）">
      库：${esc(SUBJ.paths.DB_PATH || '')}${
        SUBJ.busy ? `<br><span style="color:var(--warn)">⚠ 有任务在跑（${esc(SUBJ.busy)}），跑完才能切换</span>` : ''}
    </div>`;
  m.querySelectorAll('.sitem[data-slug]').forEach(b => b.onclick = () => subjSwitch(b.dataset.slug));
  const nb = $('#subj-new');
  if (nb) nb.onclick = subjNewDialog;
  const rb = $('#subj-ren');
  if (rb) rb.onclick = () => subjRenameDialog();
}

function subjToggle(e){
  if (e) e.stopPropagation();
  const m = $('#subj-menu');
  if (!m) return;
  const open = !m.classList.contains('on');
  m.classList.toggle('on', open);
  if (open) subjLoad().catch(err => toast('读取学科列表失败：' + err.message));
}

// 切库后前端必须清掉的缓存。逐项理由见下（**全部是"上一个库的内容"**）：
//   CHAT       问答消息；正文里的出处角标绑的是旧库的 chunk id，留着必然串库
//   EVPOP      出处/知识点浮层（DOM 里还挂着上一条知识点）
//   DOCS_TAB   资料页分栏（新库只有默认目录，停在"题库与笔记"只会看到空）
//   _progTimer 入库进度轮询 + 进度条 DOM（旧库的进度不该出现在新库页面上）
//   BATCH      批量入库面板（卡 C-0037）：文件清单 / 选中集 / 计时器
//   KPB        一键整理面板（卡 C-0035 / C-0038）：清单 / 选中集 / 两个计时器
//   MINDMAP    导图（卡 C-0034）：当前文档 id / 展开状态 / 节点下标索引
//   MMPOP      导图知识点浮层的锚点与页号
// 刻意**不动**的：
//   STATE      经核实是静态标签映射（new/changed/indexed… → {label,cls}），
//              与哪个库无关，清了反而会渲染出错
//   MSG_EV     WeakMap，旧 DOM 被替换后自行回收
function subjResetFrontend(){
  try { evPopClose(); } catch(e){}
  CHAT = { msgs: [], ev: [], turnEv: [], busy: false };

  if (_progTimer){ clearInterval(_progTimer); _progTimer = null; }
  const pb = $('#ingest-progress'); if (pb) pb.innerHTML = '';

  DOCS_TAB = 'main';

  try { batchDismiss(); } catch(e){}

  if (KPB.timer){ clearInterval(KPB.timer); KPB.timer = null; }
  if (KPB.batchTimer){ clearInterval(KPB.batchTimer); KPB.batchTimer = null; }
  KPB.docs = []; KPB.sel = null;

  MINDMAP.docId = 0; MINDMAP.open = {}; MINDMAP.nodes = {};
  MMPOP.kp = null; MMPOP.anchor = null; MMPOP.page = 0;

  closeModal();
}

// 后端切完库后的统一收尾：更新状态 → 清前端缓存 → 重画顶栏 → 重渲染当前页
function subjApply(d, msg){
  if (d.subjects) SUBJ.list = d.subjects;
  if (d.current) SUBJ.cur = d.current;
  SUBJ.paths = d.paths || {};
  SUBJ.busy = d.busy || '';
  SUBJ.loaded = true;
  subjResetFrontend();
  subjPaint();
  if (msg) toast(msg);
  // 用 go(CURRENT) 重渲染当前页：所有数据重新从**新库**拉，不做局部替换
  go(CURRENT);
}

async function subjSwitch(slug){
  $('#subj-menu').classList.remove('on');
  if (!slug || slug === SUBJ.cur) return;
  if (SUBJ.busy){ toast(`有任务正在跑（${SUBJ.busy}），跑完再切学科`); return; }
  try {
    const d = await api('/api/subjects/switch', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({slug})});
    subjApply(d, `已切到学科「${d.switched ? d.switched.name : slug}」`);
  } catch(e){ toast('切换失败：' + e.message); }
}

function subjNewDialog(){
  $('#subj-menu').classList.remove('on');
  modal(`<h2>新建学科知识库</h2>
    <div class="sub" style="margin-bottom:12px">
      新学科与现有学科<b>功能完全相同</b>（入库、导图、一键整理知识点、问答都能用），
      但课件与知识点<b>完全独立</b>：它有自己的 raw\\ index\\ notes\\ logs\\，
      <b>不会复制</b>现有资料。
    </div>
    <input id="subj-name-in" placeholder="学科名，如：药理学 / 高等数学" style="width:100%"
      autocomplete="off">
    <div class="small" id="subj-err" style="color:var(--bad);min-height:18px;margin-top:6px"></div>
    <div class="row" style="justify-content:flex-end;margin-top:8px">
      <button class="ghost" onclick="closeModal()">取消</button>
      <button class="act" id="subj-ok">确定</button>
    </div>`);
  const inp = $('#subj-name-in');
  if (inp) inp.focus();
  if (inp) inp.onkeydown = e => { if (e.key === 'Enter') subjCreate(); };
  const ok = $('#subj-ok');
  if (ok) ok.onclick = subjCreate;
}

async function subjCreate(){
  const inp = $('#subj-name-in');
  const name = ((inp && inp.value) || '').trim();
  const err = $('#subj-err');
  if (!name){
    if (err) err.textContent = '请输入学科名';
    if (inp) inp.focus();
    return;
  }
  const btn = $('#subj-ok');
  if (btn){ btn.disabled = true; btn.textContent = '创建中…'; }
  try {
    const d = await api('/api/subjects/create', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
    closeModal();
    // 建完**直接切过去**：点「新建」的意图就是马上用它；而且新库必然是空的，
    // 不存在"以为还在旧库"的风险——新学科名就写在顶栏按钮上。
    subjApply(d, `已新建并切到学科「${d.created ? d.created.name : name}」`);
  } catch(e){
    // 空名 / 非法名 / 重名 / 有任务在跑：后端原话直接显示在弹窗里
    if (err) err.textContent = e.message;
    if (btn){ btn.disabled = false; btn.textContent = '确定'; }
  }
}

// ---------------- ★ 卡 C-0085：重命名学科知识库 ----------------
// 只改**显示名**：学科的目录名（subjects/&lt;目录&gt;/）与它的 raw / index / notes /
// logs 五条路径**一个字都不动**（后端 `core/subjects.py::rename`）——目录名与显示名
// 本来就解耦，所以这是零风险的那条路；搬目录才是要连带改一堆记录的那条。
// **默认学科也能改名**：它没有 subject.json，名字落在 logs/subjects.json 的
// default_name 一格（人的默认库往往资料最多，最需要改名）。
function subjRenameDialog(slug){
  $('#subj-menu').classList.remove('on');
  const cur = SUBJ.list.find(x => x.slug === (slug || SUBJ.cur)) || SUBJ.list[0] || {};
  const opts = SUBJ.list.map(s =>
    `<option value="${esc(s.slug)}" ${s.slug===cur.slug?'selected':''}>${esc(s.name)}${
      s.slug==='default' ? '（默认库）' : ''}</option>`).join('');
  modal(`<h2>重命名学科知识库</h2>
    <div class="sub" style="margin-bottom:12px;line-height:1.9">
      改的只是<b>显示名</b>（顶栏、学科菜单、资料页头部显示的那个）。
      学科的<b>目录名</b>与它的 raw / index / notes / logs 路径一个字都不动，
      资料、切块、向量、知识点、笔记<b>全部不受影响</b>，也不会搬任何文件。
    </div>
    <div class="row" style="margin-bottom:8px">
      <select id="sr-slug" style="flex:1">${opts || '<option value="default">默认</option>'}</select>
    </div>
    <input id="sr-name" placeholder="新的学科名" style="width:100%" autocomplete="off">
    <div class="small" id="sr-err" style="color:var(--bad);min-height:18px;margin-top:6px"></div>
    <div class="small muted" id="sr-hint"></div>
    <div class="row" style="justify-content:flex-end;margin-top:8px">
      <button class="ghost" onclick="closeModal()">取消</button>
      <button class="act" id="sr-ok">确定</button>
    </div>`);
  const sel = $('#sr-slug'), inp = $('#sr-name');
  const fill = () => {
    const s = SUBJ.list.find(x => x.slug === (sel && sel.value)) || {};
    if (inp) inp.value = s.name || '';
    const h = $('#sr-hint');
    // 目录原样摆出来：让人亲眼确认"改名不搬目录"
    if (h) h.textContent = '目录：' + (s.dir || '') + (s.slug === 'default'
      ? '（名字记在本机学科注册表里）' : '');
  };
  if (sel){ sel.onchange = fill; fill(); }
  if (inp){
    inp.focus();
    inp.onkeydown = e => { if (e.key === 'Enter') subjRenameGo(); };
  }
  const ok = $('#sr-ok');
  if (ok) ok.onclick = subjRenameGo;
}

async function subjRenameGo(){
  const sel = $('#sr-slug'), inp = $('#sr-name'), err = $('#sr-err');
  const slug = (sel && sel.value) || SUBJ.cur;
  const name = ((inp && inp.value) || '').trim();
  if (!name){ if (err) err.textContent = '请输入学科名'; if (inp) inp.focus(); return; }
  const btn = $('#sr-ok');
  if (btn){ btn.disabled = true; btn.textContent = '保存中…'; }
  try {
    const d = await api('/api/subjects/rename', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({slug, name})});
    closeModal();
    // 改名**不换库**：不需要 subjApply（那会清问答消息）。只更新学科快照 + 重画顶栏，
    // 再按当前页重渲染一次（资料页头部与文件名里也带学科信息）。
    if (d.subjects) SUBJ.list = d.subjects;
    if (d.current) SUBJ.cur = d.current;
    SUBJ.paths = d.paths || SUBJ.paths;
    SUBJ.busy = d.busy || '';
    SUBJ.loaded = true;
    subjPaint();
    const r = d.renamed || {};
    toast(`学科已重命名：${r.old || ''} → ${r.new || name}｜目录未动`);
    go(CURRENT);
  } catch(e){
    if (err) err.textContent = e.message;
    if (btn){ btn.disabled = false; btn.textContent = '确定'; }
  }
}

// ---------------- 通用 ----------------
function modal(html){ $('#modalbox').innerHTML = html; $('#modal').classList.add('on'); }
function closeModal(){ $('#modal').classList.remove('on'); uiForgetPopup(); uiClearSnapshot('modal'); }
function toast(msg){
  const el = document.createElement('div');
  el.textContent = msg;
  el.style.cssText = `position:fixed;left:50%;bottom:26px;transform:translateX(-50%);
    background:var(--panel);border:1px solid var(--accent);color:var(--fg);
    padding:11px 20px;border-radius:9px;z-index:99;font-size:13px`;
  document.body.appendChild(el);
  setTimeout(()=>el.remove(), 3200);
}
$('#modal').onclick = e => { if (e.target.id==='modal') closeModal(); };
document.addEventListener('keydown', e => {
  if (e.key==='Escape') closeModal();
  if (e.key==='/' && document.activeElement!==$('#q')){ e.preventDefault(); $('#q').focus(); }
});
$('#q').addEventListener('keydown', e => {
  if (e.key==='Enter' && $('#q').value.trim()) doSearch($('#q').value.trim());
});
// 供全局 onclick 使用
window.go=go; window.openKp=openKp; window.openDoc=openDoc;
window.openChunk=openChunk; window.openNote=openNote; window.closeModal=closeModal;
window.reindex=reindex; window.reindexOne=reindexOne;
window.batchConfirm=batchConfirm; window.batchStart=batchStart;
window.batchSelUpdate=batchSelUpdate; window.batchSelectAll=batchSelectAll;
window.batchSelectNone=batchSelectNone; window.batchRetry=batchRetry;
window.batchCancel=batchCancel;
window.batchDismiss=batchDismiss;
window.deleteDoc=deleteDoc; window.deleteRaw=deleteRaw; window.openCleanup=openCleanup;
// 卡 C-0085：三个改名入口写在 innerHTML 的 onclick 字符串里，必须显式挂到 window 上
// （理由与上面一批相同）。学科那条走 `subjPaint` 里的直接引用，不在此列。
window.renameRawFile=renameRawFile; window.renameDocTitle=renameDocTitle;
window.renameArtifactTitle=renameArtifactTitle;
// 分享包（卡 C-0080）：这些按钮都写在 innerHTML 的 onclick 字符串里，
// 必须显式挂到 window 上（与上面一批同理）。
window.openShareExport=openShareExport; window.openShareImport=openShareImport;
window.shareExportStart=shareExportStart; window.shareImportStart=shareImportStart;
window.sharePickZip=sharePickZip; window.sharePickFromDir=sharePickFromDir;
window.shareInspect=shareInspect;
window.delMistakes=delMistakes; window.delSelectedMistakes=delSelectedMistakes;
window.askSample=askSample; window.clearChat=clearChat; window.sendChat=sendChat;
window.evPopClose=evPopClose;

// 顶栏学科切换器（卡 C-0039）：按钮开合 + 点面板外面收起 + 启动时读一次当前学科。
// 读失败不阻塞页面：顶栏退化成显示「默认」，其余功能照常。
$('#subj-btn').onclick = subjToggle;
document.addEventListener('click', e => {
  const m = $('#subj-menu'), box = $('#subj');
  if (m && box && m.classList.contains('on') && !box.contains(e.target)){
    m.classList.remove('on');
  }
});
subjLoad().catch(() => {
  SUBJ.loaded = true;
  const n = $('#subj-name'); if (n) n.textContent = '默认';
});

