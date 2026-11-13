
const $ = (s, r=document) => r.querySelector(s);
const esc = s => String(s??'').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// 卡 C-0059：设置窗口里的说明文案允许写 **强调**。先 esc 再替换成 <b>，
// 顺序不能反——反了就等于把文案当 HTML 解析（同 mdToHtml 的规矩）。
const em = s => esc(s).replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');

async function api(path, opts){
  const r = await fetch(path, opts);
  let j = null;
  try { j = await r.json(); } catch(e) { /* 非 JSON 响应 */ }
  if (!r.ok) {
    throw new Error((j && j.error) ? j.error : `HTTP ${r.status}`);
  }
  if (j && j.error) throw new Error(j.error);
  return j;
}
const fmtTime = ts => ts ? new Date(ts*1000).toLocaleString('zh-CN',
  {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
const pct = v => Math.round((v||0)*100);

// M0-8：窄屏自动聚焦输入框会把页面滚下去（问答页首屏 scrollY=401 的根因）。
// `preventScroll` 让焦点进去但页面不动；老浏览器不认参数时退回普通 focus。
function safeFocus(el){
  if (!el || typeof el.focus !== 'function') return;
  try { el.focus({ preventScroll: true }); }
  catch(e){ try { el.focus(); } catch(e2){ /* 聚焦失败不影响页面 */ } }
}

// ---------------- 视图 ----------------
const VIEWS = {
  assistant:{label:'问答',  render: viewAssistant},
  overview: {label:'总览',  render: viewOverview},
  review:   {label:'复习',  render: viewReview},
  preview:  {label:'教材预习',  render: viewPreview},
  docs:     {label:'资料',  render: viewDocs},
  kps:      {label:'知识点', render: viewKps},
  mistakes: {label:'错题',  render: viewMistakes},
  notes:    {label:'笔记',  render: viewNotes},
};

const PRIMARY_VIEWS = ['kps', 'review', 'preview', 'assistant', 'docs', 'notes', 'mistakes', 'overview'];
function renderNav(active){
  const icons = {kps:'▤', review:'◷', preview:'▧', assistant:'✎', docs:'▱', notes:'▥', mistakes:'◯', overview:'▦'};
  const link = k => `<button data-v="${k}" class="${k===active?'on':''}" ${k===active?'aria-current="page"':''}><span class="study-nav-icon" aria-hidden="true">${icons[k]}</span>${VIEWS[k].label}</button>`;
  $('#nav').innerHTML = PRIMARY_VIEWS.map(link).join('');
  $('#nav').querySelectorAll('button[data-v]').forEach(b =>
    b.onclick = () => go(b.dataset.v));
}
// ---------------- 首次运行引导（卡 C-0071） ----------------
// 「库是空的」与「没配密钥」这两件事必须在**第一屏**说清楚：新用户双击看板，
// 看到满屏 0 加一条红字，第一反应是"程序坏了"。这里给的是**可点的下一步**，
// 而不是一句"请参考文档"。
//
// 三条硬规矩：
//   · 只呈报后端给的事实（文档数 / 凭据链是否可用），前端不猜、不自己拼路径；
//   · 密钥缺失**只说明哪几条管线不可用**，离线功能（浏览资料、检索本地库、
//     看知识点与笔记）一个字都不许写成"不可用"；
//   · 没有密钥时**不禁用**任何离线入口——这里只加一条提示，不动别的控件。
let FIRST = null;
async function loadFirstrun(force){
  if (FIRST && !force) return FIRST;
  try { FIRST = await api('/api/firstrun'); } catch(e){ return FIRST; }
  renderFirstrun();
  return FIRST;
}
function renderFirstrun(){
  const box = $('#firstrun');
  if (!box || !FIRST) return;
  const d = FIRST, out = [];
  // ① 没配密钥：说清"哪几条管线不可用 + 去哪填"，并明说离线功能不受影响
  if (!(d.credential && d.credential.ok)) {
    out.push(`<div class="fr warn" id="fr-nokey">
      <div><h3>想用 AI 问答？</h3><div class="frbody">添加模型密钥后即可使用；资料浏览和本地检索已经可以使用。</div></div>
      <button class="ghost" onclick="openSettings()">去设置</button>
    </div>`);
  }
  // ② 库是空的：给"先做这三步"，并直接给一个能点的入口
  if (d.empty) {
    out.push(`<div class="fr" id="fr-empty">
      <div><h3>从一份课件开始</h3><div class="frbody">添加资料并入库，就能搜索内容、查看出处。</div></div>
      <button class="act" onclick="go('docs')">添加资料</button>
    </div>`);
  }
  box.innerHTML = out.join('');
}

let CURRENT = 'overview';
async function go(name){
  // task-36：页签保持修复。
  // 离开：保存滚动位置；若弹窗还开着，先抓 DOM+参数快照再隐藏（不丢状态）。
  if (CURRENT && CURRENT !== name){
    uiSaveScroll();
    if (UI_PERSIST.popup && EVPOP && EVPOP.style.display !== 'none'){
      UI_PERSIST.keepNavClose = true;
      uiSnapshotPopup();
      evPopClose();
    }
    const _m = document.getElementById('modal');
    if (_m && _m.classList.contains('on') && UI_PERSIST.popup &&
        UI_PERSIST.popup.type === 'db'){
      uiSnapshotModal();
      _m.classList.remove('on');
    }
  }
  CURRENT = name;
  renderNav(name);
  // 切回：**同步**先摆回快照（0ms 可见，不闪），随后再渲染页面/静默刷新弹窗内容。
  uiShowPopupSnapshot();
  $('#view').innerHTML = '<div class="empty">加载中…</div>';
  // 卡 C-0071：与页面渲染**并行**拉一次首次运行体检（本地毫秒级），
  // 入库完成 / 配好密钥后切页，横幅会自动收掉，不用手动刷新。
  loadFirstrun(true);
  try { await VIEWS[name].render(); }
  catch(e){ $('#view').innerHTML = `<div class="empty">出错：${esc(e.message)}</div>`; }
  if (CURRENT !== name) return;
  // 恢复期压住"滚动即关"（滚动位置重试会触发 window.scroll）。
  UI_PERSIST.holdUntil = Math.max(UI_PERSIST.holdUntil || 0, Date.now() + 2000);
  uiRestoreScroll();
  await uiRestorePopup();
  // 切页后恢复进度条：入库可能跑十几分钟，用户中途刷新或切走再回来，
  // 不该失去"它还在跑"的反馈（后端状态还在，问一句就知道）。
  if (!_progTimer) {
    try {
      const p = await api('/api/ingest/progress');
      if (p && p.active) { renderProgress(p); watchProgress(); }
    } catch(e){}
  }
}

