// ---------------- 设置中心（卡 C-0059） ----------------
// 需求 6 原话：「在页面右上角添加一个设置栏，点击后跳出窗口显示的是 AI 问答管线、
// AI 知识点总结管线，入库管线和每份课件的 AI 答疑管线的大模型 api 及密钥的输入、
// 是否开启识图、是否开启推理和调节推理强度、提示词显示和修改。」
//
// 三条约定（改这里之前先读一遍）：
//   · 密钥明文**永不落到前端**：后端只回「是否已配 + 后 4 位」；输入框留空＝不改。
//     所以"点开设置再保存"绝不会把已有密钥覆盖成空串（后端还会再挡一道）。
//   · 保存是**部分更新**：只提交用户真正动过的字段（与打开时的值逐个比对），
//     免得拿界面上的旧值去覆盖别处刚改好的东西。
//   · 提示词是**文件真源**（logs/knowledge/*.md）：保存时后端自动 .bak，
//     并回报新旧 sha256 —— 产物 meta 里的 prompt_sha256 用的就是这个文件。
let SET = null;          // 最近一次 /api/settings 的返回
let SET_TAB = 'chat';    // 当前分区（管线 id）
let SET_MSG = { cls: '', text: '' };
let SET_BUSY = false;

// ---------------- 设置中心（改版）：按「现有功能」重排（只改前端展示/交互） ----------------
// 契约不变：字段仍来自 /api/settings 的 pipelines[].fields（key/type/advanced/hint 原样），
// 保存仍走 POST /api/settings {values: patch}；这里只负责分组、卡片、搜索与未保存计数。
// 任何字段都不丢、key 不改：未知字段会落进「其它参数」卡片兜底。
let SET_Q = '';   // 左栏搜索词（只影响显示，不影响 setCollect/保存）
const SET_GROUPS = [
  {title: 'AI 能力', ids: ['chat', 'kp', 'preview', 'textbook_kp', 'deck']},
  {title: '资料', ids: ['ingest']},
];
const SET_INTRO = {
  chat: '设置问答使用的模型和密钥。',
  kp: '设置课件知识点整理。',
  deck: '设置单份课件的答疑。',
  preview: '设置教材预习生成。',
  textbook_kp: '设置教材按章整理知识点（模型、密钥、推理强度与输出上限）。',
  ingest: '设置资料入库时的图片理解与整理方式。',
};

async function openSettings(){
  const box = $('#setmodal');
  if (box) box.classList.add('on');
  if (SET){ setRender(); return; }
  SET_MSG = { cls: '', text: '读取当前配置…' };
  setRender();
  try {
    SET = await api('/api/settings');
    if (SET && SET.pipelines && SET.pipelines.length){
      if (!SET.pipelines.some(p => p.id === SET_TAB)) SET_TAB = SET.pipelines[0].id;
    }
    SET_MSG = { cls: '', text: '' };
  } catch(e){
    SET = null;
    SET_MSG = { cls: 'bad', text: '读不到设置：' + e.message };
  }
  setRender();
}

function closeSettings(){
  const box = $('#setmodal');
  if (box) box.classList.remove('on');
}

function setPipe(){
  if (!SET || !SET.pipelines || !SET.pipelines.length) return null;
  return SET.pipelines.find(p => p.id === SET_TAB) || SET.pipelines[0];
}

function setOr(v, dflt){ return (v === undefined || v === null) ? dflt : v; }

// 「所有管线的输出 token 上限」统一收进一张卡片（task-40）。
// 关键点：**不管后端有没有把它标成 advanced**，只要 key 以 `_MAX_TOKENS` 结尾就
// 从高级折叠里拎出来，默认可见；0 在后端语义是「无上限」，输入框旁给人话提示。
const SET_TOKEN_RE = /_MAX_TOKENS$/;
function setIsTokenField(f){ return !!f && SET_TOKEN_RE.test(String(f.key || '')); }
function setTokenFields(p){ return ((p && p.fields) || []).filter(setIsTokenField); }
function setTokenNoteHtml(f){
  return setIsTokenField(f)
    ? '<span class="small muted set-tok-note" title="0 表示不限制输出 token">0 = 无上限</span>'
    : '';
}
const SET_TOKEN_CARD_NOTE = '0 = 无上限（不向服务端发送 max_tokens）；大于 0 时覆盖该管线的单次输出上限。';

// M0-6a：设置项的说明文字来自后端元数据，里面混着模块名 / 文件路径 / 接口名这类
// 开发者术语。**不改后端契约**，只在渲染前翻成人话；只过滤"实现细节"，
// 功能说明与警告一个字都不删（过滤后为空就整段不显示）。
const HUMAN_RULES = [
  [/本机 config\.local\.json/g, '本机'],
  [/（[^（）]*?(?:\.gitignore|config\.local\.json|\.env\b|core\/|payload\.|\.py\b|\.json\b|\.dsh)[^（）]*?）/g, ''],
  [/[，、；]?[^，。；：（）]*?(?:core\/[A-Za-z0-9_\/.:]+|payload\.[A-Za-z0-9_.]+|\/api\/[A-Za-z0-9_\/-]+)/g, ''],
  [/，不带 \/chat\/completions/g, ''],
  [/config\.local\.json/g, '本机设置'],
  [/\.gitignore/g, '版本管理'],
  [/\.dsh 凭据文件里的那把/g, '本机已保存的密钥'],
  [/\.dsh 凭据文件/g, '本机已保存的密钥'],
];
function humanize(s){
  let t = String(s == null ? '' : s);
  for (const [re, rep] of HUMAN_RULES) t = t.replace(re, rep);
  return t.replace(/（\s*）|\(\s*\)/g, '').replace(/[，、；]\s*。/g, '。')
    .replace(/\s{2,}/g, ' ').trim();
}

// 单条字段（「标签 / 控件 / 说明」三行式）。data-search 给左栏搜索用；
// data-k 仍是保存时 setCollect 的唯一依据（值/语义一个字不改）。
function setFieldHtml(f){
  const k = 'data-k="' + f.key + '"';
  const label = f.label === 'API Base' ? '接口地址' : f.label;
  const search = esc([label, f.label, f.key, f.hint || '', humanize(f.hint || ''),
    setIsTokenField(f) ? 'token 上限 无上限 0 = 无上限' : '']
    .join(' ').toLowerCase());
  let ctl = '', orig = '';
  if (f.type === 'bool' || f.type === 'fixed'){
    const on = String(f.value) === '1' || f.value === 1 || f.value === true ||
               f.type === 'fixed';
    orig = on ? '1' : '0';
    ctl = '<div class="switch ' + (on ? '' : 'off') + '">'
      + '<input type="checkbox" ' + k + ' data-orig="' + orig + '" '
      + (on ? 'checked ' : '') + (f.type === 'fixed' ? 'disabled ' : '') + '>'
      + '<span>' + (on ? '已开启' : '已关闭')
      + (f.type === 'fixed' ? '（固定）' : '') + '</span></div>';
  } else if (f.type === 'enum'){
    orig = String(setOr(f.value, ''));
    ctl = '<select ' + k + ' data-orig="' + esc(orig) + '">'
      + (f.choices || []).map(c => {
          const name = String(f.key).endsWith('_REASONING_EFFORT')
            ? ({none: '无', low: '低', high: '高', max: '最高'}[c] || c) : c;
          return '<option value="' + esc(c) + '"' + (orig === c ? ' selected' : '') + '>'
            + esc(name) + '</option>';
        }).join('') + '</select>';
  } else if (f.type === 'secret'){
    orig = '';
    const cred = (SET && SET.credential) || {};
    const fb = cred.ok
      ? '留空会保留已保存的密钥。'
      : '未找到可用密钥；需要 AI 功能时请填写。';
    ctl = '<input type="password" ' + k + ' data-orig="" value="" '
      + 'autocomplete="new-password" placeholder="'
      + (f.set ? '已配置，留空保持不变'
               : '输入密钥') + '">'
      + '<span class="sbadge ' + (f.set ? 'on' : '') + '">'
      + (f.set ? '已配置' : '未配置') + '</span>'
      + '<div class="shint">' + em(humanize(fb)) + '</div>';
  } else if (f.type === 'int' || f.type === 'float'){
    orig = String(setOr(f.value, ''));
    ctl = '<input type="number" ' + k + ' data-orig="' + esc(orig) + '" value="'
      + esc(orig) + '"' + (f.type === 'float' ? ' step="0.01"' : '') + '>'
      + setTokenNoteHtml(f);
  } else {
    orig = String(setOr(f.value, ''));
    ctl = '<input type="text" ' + k + ' data-orig="' + esc(orig) + '" value="'
      + esc(orig) + '">';
  }
  return '<div class="srow" data-key="' + esc(f.key) + '" data-search="' + search + '">'
    + '<label>' + esc(label) + '<span class="skey">' + esc(f.key) + '</span></label>'
    + '<div class="sctl">' + ctl
    + (f.type === 'secret' ? '' : '<div class="shint">' + em(humanize(f.hint || '')) + '</div>')
    + '</div></div>';
}

function setRows(fields){
  return (fields || []).map(setFieldHtml).join('');
}

function setCard(title, body, opts){
  const o = opts || {};
  return '<div class="settings-card' + (o.muted ? ' muted' : '') + '"'
    + (o.id ? ' id="' + o.id + '"' : '') + '>'
    + '<h4>' + esc(title)
    + (o.badge ? '<span class="sc-badge ' + (o.badgeCls || '') + '">'
        + esc(o.badge) + '</span>' : '')
    + '</h4>'
    + (o.note ? '<div class="set-card-note">' + o.note + '</div>' : '')
    + body + '</div>';
}

function setPromptsHtml(p){
  if (p.id === 'kp'){
    const cards = (SET.prompts || []).map(s =>
      '<div class="spcard" id="spc-' + s.id + '"><div class="spname">'
      + esc(s.label) + '</div>'
      + '<div class="spmeta">' + esc(humanize(s.role || '')) + '</div>'
      + '<div class="spact"><button class="ghost" onclick="promptOpen(\'' + s.id
      + '\')">查看 / 编辑</button></div><div id="sp-' + s.id + '"></div></div>').join('');
    return cards + '<div class="set-card-note">修改后会自动保留旧版本。</div>';
  }
  if (p.id === 'deck'){
    return '<div class="spcard readonly"><div class="spname">这条管线不用提示词</div>'
      + '<div class="spmeta">直接根据所选课件回答。</div></div>';
  }
  if (p.id === 'textbook_kp'){
    // task-40：教材知识点走内置提示词，**不能**落到 ingest_outline（入库结构分析）。
    return '<div class="spcard readonly"><div class="spname">这条管线使用内置提示词</div>'
      + '<div class="spmeta">按教材章节分段整理知识点，暂不支持在设置窗口里编辑。</div></div>';
  }
  const ids = (p.id === 'chat')
    ? ['chat_system', 'chat_expand'] : ['ingest_outline'];
  const cards = (SET.builtin_prompts || []).filter(b => ids.indexOf(b.id) >= 0).map(b => {
    return '<div class="spcard readonly" id="spc-' + b.id + '"><div class="spname">'
    + esc(b.label) + '</div>'
    + '<div class="spmeta">内置内容，仅供查看</div>'
    + '<div class="spact"><button class="ghost" onclick="promptOpenBuiltin(\''
    + b.id + '\')">查看</button></div><div id="sp-' + b.id + '"></div></div>';
  }).join('');
  return cards;
}

// 入库方式横幅文案：开=整页切块；关=旧模式（结构分析 + 跨页语义切块）。
function setModeBannerText(on){
  return on
    ? '按页整理，适合逐页查找课件内容。'
    : '按内容整理，适合跨页讲解的资料。';
}

// 入库页重排：入库方式（主开关）→ 识图 → AI 结构分析（旧模式专用，默认折叠）
// → 连接与密钥 → 提示词（旧模式）。字段一个不少，未知字段落入「其它参数」兜底。
function setIngestPaneHtml(p){
  const by = {};
  (p.fields || []).forEach(f => { by[f.key] = f; });
  const used = new Set();
  const take = keys => keys.map(k => by[k]).filter(Boolean).map(f => {
    used.add(f.key); return setFieldHtml(f);
  }).join('');
  const pageChunk = by['INGEST_PAGE_CHUNK'];
  const onChunk = !!pageChunk && String(pageChunk.value) === '1';

  let html = '';
  if (pageChunk){
    used.add('INGEST_PAGE_CHUNK');
    html += '<div class="settings-card" id="set-ingest-mode">'
      + '<h4>入库方式 <span class="sc-badge ' + (onChunk ? 'ok' : 'warn') + '">'
      + (onChunk ? '逐页' : '按内容') + '</span></h4>'
      + setFieldHtml(pageChunk)
      + '<div class="mode-banner ' + (onChunk ? '' : 'off') + '" id="ingest-mode-banner">'
      + esc(setModeBannerText(onChunk)) + '</div>'
      + '</div>';
  }
  html += setCard('入库后自动整理', take(['INGEST_AUTO_KP']),
    {note: '开启后，课件入库结束会继续整理知识点，并产生额外模型调用和费用。'});
  html += setCard('识图（图片理解）',
    take(['DEEPSEEK_VISION_MODEL', 'FIGURE_DESC', 'FIGURE_THINKING',
          'FIGURE_REASONING_EFFORT', 'FIGURE_RENDER_DPI', 'FIGURE_MAX_CALLS_PER_DOC']),
    {note: '开启后可识别课件里的图片，可能产生 AI 费用。'});
  html += '<div class="settings-card' + (onChunk ? ' muted' : '') + '" id="set-structure-card">'
    + '<h4>结构分析（按内容整理时使用）'
    + '<span class="sc-badge ' + (onChunk ? 'warn' : 'ok') + '">'
    + (onChunk ? '当前不使用' : '已启用')
    + '</span></h4>'
    + '<details class="set-adv set-adv-in"><summary>设置结构分析</summary>'
    + '<div class="setadvbody">'
    + take(['INGEST_MODEL', 'OUTLINE_ANALYSIS', 'OUTLINE_THINKING'])
    + '</div></details>'
    + '</div>';
  html += setCard('连接与密钥', take(['DEEPSEEK_BASE_URL', 'INGEST_API_KEY']));
  // task-40：token 上限统一成独立卡片，默认可见（0 = 无上限），不塞进高级折叠。
  const tokenFs = setTokenFields(p);
  const tokenKeys = new Set(tokenFs.map(f => f.key));
  if (tokenFs.length){
    html += setCard('Token 上限（0 = 无上限）', setRows(tokenFs),
      {id: 'set-token-card', note: SET_TOKEN_CARD_NOTE});
  }
  const rest = (p.fields || []).filter(f => !used.has(f.key) && !tokenKeys.has(f.key));
  if (rest.length){
    html += setCard('其它参数', setRows(rest));
  }
  html += '<details class="settings-extra"><summary>提示词（高级）</summary>'
    + setCard('提示词', setPromptsHtml(p)) + '</details>';
  return html;
}

function setPaneHtml(p){
  if (!p) return '<div class="empty">没有可配置的管线</div>';
  const head = '<h3><span>' + (p.icon || '') + '</span>' + esc(p.label) + '</h3>'
    + '<div class="pdesc">' + esc(SET_INTRO[p.id] || humanize(p.sub || '')) + '</div>';
  if (p.id === 'ingest') return head + setIngestPaneHtml(p);
  // task-40：token 上限字段单独抽卡（不受 advanced 标记影响，默认可见）。
  const tokenFs = setTokenFields(p);
  const tokenKeys = new Set(tokenFs.map(f => f.key));
  const others = (p.fields || []).filter(f => !tokenKeys.has(f.key));
  const conn = others.filter(f =>
    !f.advanced && (f.type === 'str' || f.type === 'secret'));
  const sw = others.filter(f =>
    !f.advanced && f.type !== 'str' && f.type !== 'secret' && f.type !== 'note');
  const adv = others.filter(f => f.advanced);
  let html = head;
  html += setCard('连接与密钥', setRows(conn));
  html += setCard('参数与开关', setRows(sw));
  if (tokenFs.length){
    html += setCard('Token 上限（0 = 无上限）', setRows(tokenFs),
      {id: 'set-token-card', note: SET_TOKEN_CARD_NOTE});
  }
  if (adv.length){
    html += '<div class="settings-card">'
      + '<details class="set-adv set-adv-in"><summary>高级参数（'
      + adv.length + ' 项）</summary><div class="setadvbody">'
      + setRows(adv) + '</div></details></div>';
  }
  html += '<details class="settings-extra"><summary>提示词（高级）</summary>'
    + setCard('提示词', setPromptsHtml(p)) + '</details>';
  return html;
}

// 左栏：搜索框 + 两组导航（AI 能力 / 资料）+ 每项状态点 + 底部说明。
function setNavHtml(pipes){
  const byId = {};
  pipes.forEach(p => { byId[p.id] = p; });
  const btn = p => {
    const st = setPipeKeyState(p);
    const dot = st
      ? '<span class="sn-dot ' + st + '" title="'
        + (st === 'on' ? '密钥已配置' : '密钥未配置（留空＝用本机已保存的密钥）')
        + '"></span>' : '';
    return '<button data-p="' + p.id + '" class="' + (p.id === SET_TAB ? 'on' : '')
      + '" onclick="setTab(\'' + p.id + '\')">'
      + '<span class="sn-ic">' + (p.icon || '') + '</span>'
      + '<span class="sn-name">' + esc(p.label) + '</span>' + dot + '</button>';
  };
  let h = '<input class="set-search" id="set-q" type="search" value="' + esc(SET_Q)
    + '" placeholder="搜索设置" autocomplete="off">'
    + '<div class="set-qinfo" id="set-qinfo"></div>';
  const grouped = new Set();
  SET_GROUPS.forEach(g => {
    const items = g.ids.map(id => byId[id]).filter(Boolean);
    items.forEach(p => grouped.add(p.id));
    if (items.length) h += '<div class="sn-group">' + esc(g.title) + '</div>'
      + items.map(btn).join('');
  });
  const others = pipes.filter(p => !grouped.has(p.id));
  if (others.length){
    h += '<div class="sn-group">其它</div>' + others.map(btn).join('');
  }
  h += '<div class="sn-group">界面</div><button data-p="display" class="' +
    (SET_TAB === 'display' ? 'on' : '') + '" onclick="setTab(\'display\')">' +
    '<span class="sn-ic">◫</span><span class="sn-name">显示设置</span></button>';
  return h;
}

// 每项管线的小状态点：看这条管线的密钥字段（后端给的 set 状态）；取不到就不显示。
function setPipeKeyState(p){
  const sec = (p.fields || []).find(f => f.type === 'secret');
  if (!sec) return '';
  return sec.set ? 'on' : 'off';
}

function setRender(){
  const box = $('#setbox');
  if (!box) return;
  if (!SET){
    box.innerHTML = '<div class="set-head"><div><h2>⚙ 设置中心</h2></div>'
      + '<div class="set-head-actions"><button class="ghost" onclick="startUserGuide()" title="查看网站主要功能和操作说明">新手指引</button>'
      + '<button class="set-x" onclick="closeSettings()" title="关闭">✕</button></div></div>'
      + '<div class="set-pane"><div class="empty">'
      + esc(SET_MSG.text || '读取中…') + '</div></div>';
    return;
  }
  const pipes = SET.pipelines || [];
  const cur = setPipe();
  box.innerHTML =
    '<div class="set-head"><div><h2>设置</h2>'
    + '<div class="ssub">更改后点击保存；密钥留空会保留原值。</div></div>'
    + '<div class="set-head-actions"><button class="ghost" onclick="startUserGuide()" title="查看网站主要功能和操作说明">新手指引</button>'
    + '<button class="set-x" onclick="closeSettings()" title="关闭（Esc）">✕</button></div></div>'
    + '<div class="set-body">'
    + '<div class="set-nav">' + setNavHtml(pipes) + '</div>'
    + '<div class="set-pane" id="setpane">' + (SET_TAB === 'display' ? kpDisplayPaneHtml() : setPaneHtml(cur))
    + '<div class="set-nomatch" id="set-nomatch" style="display:none">没有匹配项</div></div>'
    + '</div>'
    + (SET_TAB === 'display' ? '<div class="set-foot"><span class="small muted">窗口大小会自动保存在当前浏览器。</span></div>' : '<div class="set-foot">'
    + '<button class="act" id="set-save" onclick="setSave()">保存</button>'
    + '<details class="settings-foot-more"><summary>其他操作</summary><div>'
    + '<button class="ghost" onclick="setResetPane()">恢复本页默认</button>'
    + '<button class="ghost" onclick="SET=null;openSettings()">重新读取</button>'
    + '</div></details>'
    + '<span class="save-n zero" id="set-unsaved">无未保存改动</span>'
    + '<div class="sstat ' + SET_MSG.cls + '" id="setstat">'
    + esc(SET_MSG.text || '') + '</div>'
    + '</div>');
  setBind();
}

function setBind(){
  const q = $('#set-q');
  if (q){
    q.oninput = () => setSearchApply(q.value);
    setSearchApply(SET_Q);
  }
  const pane = $('#setpane');
  if (pane){
    pane.addEventListener('input', setCounter);
    pane.addEventListener('change', setCounter);
  }
  setIngestBind();
  if (SET_TAB === 'display') kpDisplayBind();
  setCounter();
}

// 搜索：只切显示/高亮，**不删 DOM**（setCollect 仍能读到被隐藏字段的改动，保存不受影响）。
function setSearchApply(q){
  SET_Q = String(q || '').trim();
  const pane = $('#setpane');
  if (!pane) return;
  const ql = SET_Q.toLowerCase();
  let hit = 0;
  const rows = [...pane.querySelectorAll('.srow')];
  rows.forEach(row => {
    const text = (row.dataset.search || row.textContent || '').toLowerCase();
    const ok = !ql || text.indexOf(ql) >= 0;
    row.style.display = ok ? '' : 'none';
    row.classList.toggle('hit', !!ql && ok);
    if (ok) hit++;
  });
  pane.querySelectorAll('.settings-card').forEach(card => {
    const rs = [...card.querySelectorAll('.srow')];
    if (rs.length && ql && !rs.some(r => r.style.display !== 'none')){
      card.style.display = 'none';
    } else {
      card.style.display = '';
    }
  });
  // 命中的字段在折叠区里 → 自动展开，方便定位
  if (ql){
    pane.querySelectorAll('details').forEach(det => {
      if (det.querySelector('.srow.hit')) det.open = true;
    });
  }
  const no = $('#set-nomatch');
  if (no) no.style.display = (ql && hit === 0) ? '' : 'none';
  const info = $('#set-qinfo');
  if (info) info.textContent = ql ? ('命中 ' + hit + ' 项') : '';
  setCounter();
}

// 入库方式开关：**不重渲染**（否则会丢掉其它未保存改动），只就地更新横幅/徽标/卡片灰化。
function setIngestBind(){
  const cb = document.querySelector('#set-ingest-mode [data-k="INGEST_PAGE_CHUNK"]');
  if (!cb) return;
  cb.addEventListener('change', setIngestModePaint);
}
function setIngestModePaint(){
  const cb = document.querySelector('#set-ingest-mode [data-k="INGEST_PAGE_CHUNK"]');
  if (!cb) return;
  const on = !!cb.checked;
  const banner = $('#ingest-mode-banner');
  if (banner){
    banner.className = 'mode-banner' + (on ? '' : ' off');
    banner.textContent = setModeBannerText(on);
  }
  const modeCard = $('#set-ingest-mode');
  const modeBadge = modeCard ? modeCard.querySelector('h4 .sc-badge') : null;
  if (modeBadge){
    modeBadge.className = 'sc-badge ' + (on ? 'ok' : 'warn');
    modeBadge.textContent = on ? '逐页' : '按内容';
  }
  const card = $('#set-structure-card');
  if (card){
    card.classList.toggle('muted', on);
    const badge = card.querySelector('h4 .sc-badge');
    if (badge){
      badge.className = 'sc-badge ' + (on ? 'warn' : 'ok');
      badge.textContent = on ? '当前不使用' : '已启用';
    }
  }
  const sw = cb.closest('.switch');
  if (sw){
    sw.classList.toggle('off', !on);
    const label = sw.querySelector('span');
    if (label && label.textContent.indexOf('固定') < 0){
      label.textContent = on ? '已开启' : '已关闭';
    }
  }
  setCounter();
}

function setCollect(){
  const out = {};
  document.querySelectorAll('#setpane [data-k]').forEach(el => {
    if (el.disabled) return;
    const k = el.getAttribute('data-k');
    const o = el.getAttribute('data-orig');
    const orig = (o === null) ? '' : o;
    const v = (el.type === 'checkbox') ? (el.checked ? '1' : '0') : el.value;
    if (String(v) !== String(orig)) out[k] = v;
  });
  return out;
}

// footer：实时「N 项未保存」；无改动/保存中时禁用「保存」。
function setCounter(){
  const n = Object.keys(setCollect()).length;
  const el = $('#set-unsaved');
  if (el){
    el.textContent = n ? (n + ' 项未保存') : '无未保存改动';
    el.classList.toggle('zero', n === 0);
  }
  const btn = $('#set-save');
  if (btn) btn.disabled = (n === 0) || !!SET_BUSY;
}

async function setTab(id){
  if (id === SET_TAB) return;
  const patch = setCollect();
  if (Object.keys(patch).length){
    const go2 = await confirmBox('有未保存的改动',
      '当前分区还有 ' + Object.keys(patch).length + ' 项改动没保存，切走就会丢掉。要切吗？');
    if (!go2) return;
  }
  SET_TAB = id;
  SET_Q = '';
  SET_MSG = { cls: '', text: '' };
  setRender();
  const pane = $('#setpane');
  if (pane) pane.scrollTop = 0;
}

async function setSave(){
  if (SET_BUSY) return;
  const patch = setCollect();
  const n = Object.keys(patch).length;
  if (!n){
    SET_MSG = { cls: 'warn', text: '没有改动可保存（密钥留空＝不改动）' };
    setRender();
    return;
  }
  SET_BUSY = true;
  SET_MSG = { cls: '', text: '保存中…' };
  setRender();
  try {
    const r = await api('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: patch }) });
    if (!r || r.ok === false){
      throw new Error(((r && r.errors) || []).join('；') || '保存失败');
    }
    if (r.settings) SET = r.settings;
    // 刚在窗口里配好密钥 → 首屏那条"还没配置密钥"横幅立刻收掉
    loadFirstrun(true);
    SET_MSG = { cls: 'ok', text: '设置已保存并生效。' };
  } catch(e){
    SET_MSG = { cls: 'bad', text: '保存失败：' + e.message };
  }
  SET_BUSY = false;
  setRender();
}

async function setResetPane(){
  const cur = setPipe();
  if (!cur) return;
  const keys = (cur.fields || []).filter(f => f.type !== 'fixed' && f.type !== 'note')
    .map(f => f.key);
  SET_MSG = { cls: '', text: '恢复中…' };
  setRender();
  try {
    const r = await api('/api/settings/reset', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keys: keys }) });
    if (r.settings) SET = r.settings;
    SET_MSG = { cls: 'ok', text: '本区已恢复默认设置' };
  } catch(e){
    SET_MSG = { cls: 'bad', text: '恢复失败：' + e.message };
  }
  setRender();
}

async function promptOpen(slotId){
  const box = $('#sp-' + slotId);
  if (!box) return;
  if (box.getAttribute('data-open') === '1'){
    box.innerHTML = '';
    box.setAttribute('data-open', '0');
    return;
  }
  box.innerHTML = '<div class="spmsg">读取中…</div>';
  try {
    const d = await api('/api/settings/prompt?name=' + encodeURIComponent(slotId));
    if (!d || !d.ok) throw new Error((d && d.error) || '读不到这份提示词');
    box.setAttribute('data-open', '1');
    box.innerHTML = '<div class="spedit"><textarea id="sptext-' + slotId
      + '"></textarea><div class="row" style="margin-top:9px">'
      + '<button class="act" onclick="promptSave(\'' + slotId + '\')">保存提示词</button>'
      + '<button class="ghost" onclick="promptOpen(\'' + slotId + '\')">取消</button>'
      + '<span class="small muted">保存后立即用于下一次整理</span></div>'
      + '<div class="spmsg" id="spmsg-' + slotId + '"></div></div>';
    $('#sptext-' + slotId).value = d.text || '';
  } catch(e){
    box.innerHTML = '<div class="spmsg bad">' + esc(e.message) + '</div>';
  }
}

async function promptSave(slotId){
  const ta = $('#sptext-' + slotId), msg = $('#spmsg-' + slotId);
  if (!ta || !msg) return;
  msg.className = 'spmsg';
  msg.textContent = '保存中…';
  try {
    const r = await api('/api/settings/prompt', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: slotId, text: ta.value }) });
    if (!r || !r.ok) throw new Error((r && r.error) || '保存失败');
    msg.className = 'spmsg ok';
    msg.innerHTML = '已保存（旧版本已自动备份）· 下一次整理就用新提示词';
    if (r.prompts) SET.prompts = r.prompts;
  } catch(e){
    msg.className = 'spmsg bad';
    msg.textContent = '保存失败：' + e.message;
  }
}

async function promptOpenBuiltin(bid){
  const box = $('#sp-' + bid);
  if (!box) return;
  if (box.getAttribute('data-open') === '1'){
    box.innerHTML = '';
    box.setAttribute('data-open', '0');
    return;
  }
  try {
    const d = await api('/api/settings/prompt?builtin=' + encodeURIComponent(bid));
    box.setAttribute('data-open', '1');
    box.innerHTML = '<div class="spedit"><textarea readonly '
      + 'style="min-height:32vh"></textarea><div class="spmsg muted">'
      + esc(humanize(d.source || '')) + ' —— 内建提示词，只能查看（本版不支持在窗口里改）</div></div>';
    box.querySelector('textarea').value = d.text || '';
  } catch(e){
    box.innerHTML = '<div class="spmsg bad">' + esc(e.message) + '</div>';
  }
}

window.openSettings = openSettings; window.closeSettings = closeSettings;
window.setTab = setTab; window.setSave = setSave; window.setResetPane = setResetPane;
window.promptOpen = promptOpen; window.promptSave = promptSave;
window.promptOpenBuiltin = promptOpenBuiltin;

$('#set-btn').onclick = openSettings;
$('#setmodal').addEventListener('click', e => {
  if (e.target && e.target.id === 'setmodal') closeSettings();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && $('#setmodal').classList.contains('on')) closeSettings();
});

