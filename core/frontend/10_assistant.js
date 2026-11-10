// ---------------- 问答助手 ----------------
// 全站共用 Markdown：禁用原始 HTML，依赖 markdown-it 拒绝危险 URL。
// 外部图片只提供点击链接，避免阅读未可信资料时自动向外发起请求。
const STUDY_MD = window.markdownit({html:false, breaks:true, linkify:false});
// 数学公式作为 Markdown 语法解析，代码块和代码行不受影响。禁用 KaTeX 的
// trust 扩展，阻止公式中注入链接、HTML 或外部资源。
STUDY_MD.use(texmath, {engine: window.katex, delimiters: ['dollars', 'brackets'],
  katexOptions: {trust: false, throwOnError: false, strict: 'ignore', maxExpand: 1000}});
// 兼容旧版成果中少数明确的药动学记法；只处理 Markdown 文本 token，
// 不碰代码、链接地址或已标注的数学公式，也不猜测 C=O 等化学结构。
const LEGACY_FORMULAS = [
  [/Fsys\s*=\s*Fabs\s*[*×]\s*Fg\s*[*×]\s*FH/g, 'F_{\\mathrm{sys}} = F_{\\mathrm{abs}} \\times F_{g} \\times F_{H}'],
  [/\bF\s*=\s*fa\s*[×*]\s*fg\s*[×*]\s*fh\s*[×*]\s*fl\b/g, 'F = f_a \\times f_g \\times f_h \\times f_l'],
  [/pKa\s*[−-]\s*pH\s*=\s*log\(Cu\s*\/\s*Ci\)/g, '\\mathrm{p}K_a - \\mathrm{pH} = \\log(C_u/C_i)'],
  [/pKa\s*[−-]\s*pH\s*=\s*log\(Ci\s*\/\s*Cu\)/g, '\\mathrm{p}K_a - \\mathrm{pH} = \\log(C_i/C_u)'],
  [/dC\/dt\s*=\s*DS\/h\s*[·⋅]\s*\(Cs\s*[−-]\s*C\)\s*=\s*kS\(Cs\s*[−-]\s*C\)/g, '\\frac{dC}{dt} = \\frac{DS}{h}(C_s-C)=kS(C_s-C)'],
  [/\bt1\/2\b/g, 't_{1/2}'], [/\bCmax\b/g, 'C_{\\max}'],
];
// 兼容旧总结中的高置信度、无分隔符等式。只接受 ASCII 变量、数字和明确
// 运算符，不推断下标或单位；纯化学双键、金额、URL、普通文本均不转换。
const LEGACY_EQUATION = /(?<![A-Za-z0-9_/.\$])([A-Za-z][A-Za-z0-9]{0,7}(?:\/[A-Za-z][A-Za-z0-9]{0,7})?)\s*=\s*([A-Za-z0-9]+(?:[()+*/^−×·-][A-Za-z0-9()+*/^−×·-]*)*)/g;
STUDY_MD.renderer.rules.text = (tokens, idx) => {
  const raw = tokens[idx].content;
  const matches = [];
  for (const [pattern, latex] of LEGACY_FORMULAS) {
    pattern.lastIndex = 0;
    for (const m of raw.matchAll(pattern)) matches.push({start: m.index, end: m.index + m[0].length, latex});
  }
  let linkDepth = 0;
  for (let i = 0; i < idx; i++) {
    if (tokens[i].type === 'link_open') linkDepth++;
    if (tokens[i].type === 'link_close') linkDepth--;
  }
  if (!linkDepth) {
    LEGACY_EQUATION.lastIndex = 0;
    for (const m of raw.matchAll(LEGACY_EQUATION)) {
      if (/https?:\/\/\S*$/i.test(raw.slice(0, m.index))) continue;
      let rhs = m[2];
      // 不吞掉外层中文括注的右括号。
      while (rhs.endsWith(')') && (rhs.match(/\)/g) || []).length > (rhs.match(/\(/g) || []).length)
        rhs = rhs.slice(0, -1);
      if (!rhs || (!m[1].includes('/') && !/[()+*/^−×·-]/.test(rhs))) continue;
      if ((rhs.match(/\)/g) || []).length !== (rhs.match(/\(/g) || []).length) continue;
      const safe = s => s.replace(/−/g, '-').replace(/\^\(([^()]*)\)/g, '^{$1}')
        .replace(/×/g, '\\times ').replace(/·/g, '\\cdot ')
        .replace(/\*/g, '\\cdot ').replace(/\b(log|ln|exp)\b/g, '\\$1');
      matches.push({start: m.index, end: m.index + m[0].length - (m[2].length - rhs.length),
        latex: safe(m[1]) + '=' + safe(rhs)});
    }
  }
  matches.sort((a, b) => a.start - b.start || b.end - a.end);
  let html = '', last = 0;
  for (const m of matches) {
    if (m.start < last) continue;
    html += STUDY_MD.utils.escapeHtml(raw.slice(last, m.start));
    html += window.katex.renderToString(m.latex, {trust: false, throwOnError: false, maxExpand: 1000});
    last = m.end;
  }
  return html + STUDY_MD.utils.escapeHtml(raw.slice(last));
};
const STUDY_MD_LINK = STUDY_MD.renderer.rules.link_open ||
  ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options));
STUDY_MD.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const href = tokens[idx].attrGet('href') || '';
  if (/^(?:https?:)?\/\//i.test(href)){
    tokens[idx].attrSet('target', '_blank');
    tokens[idx].attrSet('rel', 'noopener noreferrer');
  }
  return STUDY_MD_LINK(tokens, idx, options, env, self);
};
const STUDY_MD_IMAGE = STUDY_MD.renderer.rules.image;
STUDY_MD.renderer.rules.image = (tokens, idx, options, env, self) => {
  const src = tokens[idx].attrGet('src') || '';
  if (/^\/(?!\/)/.test(src)) return STUDY_MD_IMAGE(tokens, idx, options, env, self);
  const label = esc(tokens[idx].content || '查看图片');
  return /^(?:https?:)?\/\//i.test(src)
    ? '<a href="' + esc(src) + '" target="_blank" rel="noopener noreferrer">图片：' + label + '</a>'
    : '<span>' + label + '</span>';
};
function mdToHtml(src){
  return '<div class="markdown-body">' + STUDY_MD.render(String(src ?? '')) + '</div>';
}
function mdInline(src){ return STUDY_MD.renderInline(String(src ?? '')); }

// 把 [1] [2][3] 变成可点击的引用角标
function linkCites(html){
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const walker = document.createTreeWalker(tpl.content, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(node => {
    if (node.parentElement && node.parentElement.closest('a,code,pre')) return;
    const text = node.nodeValue || '';
    const re = /\[(\d{1,2})\]/g;
    let match, last = 0, found = false;
    const frag = document.createDocumentFragment();
    while ((match = re.exec(text))){
      found = true;
      frag.appendChild(document.createTextNode(text.slice(last, match.index)));
      const cite = document.createElement('span');
      cite.className = 'cite'; cite.dataset.n = match[1];
      cite.title = '点开看这条出处'; cite.textContent = match[1];
      frag.appendChild(cite); last = re.lastIndex;
    }
    if (found){ frag.appendChild(document.createTextNode(text.slice(last))); node.replaceWith(frag); }
  });
  return tpl.innerHTML;
}

let CHAT = { msgs: [], ev: [], busy: false };

// 每一轮的出处要**按轮保存**，不能只留 CHAT.ev（那只是当前这一轮）。
// 否则切走再切回问答页时，历史消息的角标全都点不开——
// 因为重渲染只拿得到回答文本，拿不到它当初引用的那些片段。
function evOfTurn(i){ return (CHAT.turnEv && CHAT.turnEv[i]) || []; }
function setTurnEv(i, ev){
  if (!CHAT.turnEv) CHAT.turnEv = [];
  CHAT.turnEv[i] = ev || [];
}

async function viewAssistant(){
  let st = {ok:false, error:'', model:''};
  try { st = await api('/api/chat/status'); } catch(e){ st.error = e.message; }
  let docs = [];
  try { docs = (await api('/api/docs')).documents || []; } catch(e){}

  const hist = CHAT.msgs.map((m, i) => `
    <div class="msg ${m.role === 'user' ? 'user' : 'ai'}">
      <div class="who">${m.role === 'user' ? '我' : 'AI'}</div>
      <div class="body">
        <div class="bubble">${m.role === 'user'
          ? mdToHtml(m.content)
          : linkCites(mdToHtml(m.content))}</div>
      </div>
    </div>`).join('');

  const samples = ['阿司匹林的作用机制是什么','GABA 受体分布在哪里',
                   '抗高血压药分几类'];

  $('#view').innerHTML = `
    <div class="card">
      <h2>问答 <span class="small muted">基于你的资料，回答附出处</span></h2>
      ${st.ok ? '' : `<div class="sub" style="color:var(--bad);margin-top:8px">
        问答暂不可用：${esc(st.error || '请检查模型设置')}
        <button class="ghost" onclick="openSettings()">打开设置</button>
      </div>`}
      ${docs.length ? '' : `<div class="sub" style="margin-top:8px">
        还没有资料。<a href="#" onclick="go('docs');return false">添加资料</a>后即可提问。</div>`}
      <div class="row" style="margin-top:10px">
        <label class="small muted" for="chat-scope">资料范围</label>
        <select id="chat-scope" style="flex:1;max-width:420px">
          <option value="">全部资料（${docs.length} 份）</option>
          ${docs.map(d => `<option value="${d.id}">${esc((d.title||d.path).slice(0,40))} · ${d.kind}</option>`).join('')}
        </select>
        ${CHAT.msgs.length ? '<button class="ghost" onclick="clearChat()">清空对话</button>' : ''}
      </div>
      ${CHAT.msgs.length ? '' : `<div class="chips" style="margin-top:12px">
        ${samples.map(s => `<span class="qchip" onclick="askSample('${esc(s)}')">${esc(s)}</span>`).join('')}
      </div>`}
      <div id="chatwrap" class="${CHAT.msgs.length?'':'is-empty'}" style="margin-top:14px">
        <div id="thread">${hist}</div>
        <div id="chatbar">
          <textarea id="chatq" placeholder="输入你的问题…" ${st.ok?'':'disabled'}></textarea>
          <button class="act" id="chatgo" ${st.ok?'':'disabled'}>发送</button>
        </div>
        <div class="hint" style="margin-top:7px">回车发送 · Shift+回车换行</div>
      </div>
    </div>`;

  const ta = $('#chatq'), btn = $('#chatgo');
  if (btn) btn.onclick = sendChat;
  if (ta) {
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
    });
    if (CHAT.msgs.length === 0) safeFocus(ta);
  }
  const th = $('#thread');
  if (th) th.scrollTop = th.scrollHeight;
  // 历史消息里可能有多条带角标的回答，逐条在自己的容器内绑定；
  // 同时把那一轮当初的出处数据还原回去，否则切回来角标点不开。
  document.querySelectorAll('#thread .msg').forEach((el, i) => {
    const body = el.querySelector('.body');
    if (!body) return;
    if (el.classList.contains('ai')) setMsgEvidence(body, evOfTurn(i));
    bindCiteClicks(body);
  });
}

function askSample(q){ const ta = $('#chatq'); if (ta) ta.value = q; sendChat(); }
function clearChat(){
  evPopClose();                       // 否则浮窗留在屏幕上、指向已不存在的角标
  CHAT = { msgs: [], ev: [], turnEv: [], busy: false };
  go('assistant');
}

// 跳转必须**限定在本条消息的容器内**。
//
// 这里曾经用硬编码的 id="evd" 定位证据区，多轮对话时页面上会出现多个同 id 元素，
// 而 getElementById 只返回第一个 —— 于是第二轮点角标总跳到第一轮的出处。
// 全局 querySelectorAll('.cite') 也是同样的问题。
// 现在一律按本条消息的 .body 容器作用域查找（见下方同名函数）。
// 注意：这个作用域是"取哪一条证据"的依据，不能退化成全局查找。

// 每条消息的证据数据挂在它自己的 DOM 节点上（用 WeakMap，不污染 DOM 属性）。
// 不用 CHAT.ev 存：那是"当前这一轮"的，多轮历史会互相覆盖，
// 点旧消息的角标就会拿到新消息的出处——正是早先那类 bug。
const MSG_EV = new WeakMap();

function setMsgEvidence(bodyEl, ev){ if (bodyEl) MSG_EV.set(bodyEl, ev || []); }
function msgEvidence(bodyEl){ return MSG_EV.get(bodyEl) || []; }

// 点角标弹出的小窗。全局只维持一个：同一时刻只需要看一条出处，
// 切换角标就地换内容，比堆叠多个浮层更好收拾。
let EVPOP = null;

function evPopEl(){
  if (EVPOP && document.body.contains(EVPOP)) return EVPOP;
  const d = document.createElement('div');
  d.className = 'evpop';
  // 点小窗内部不该关掉它；点外部才关（见 evPopOutside）
  d.addEventListener('click', e => e.stopPropagation());
  d.addEventListener('pointerdown', e => {
    if (d.dataset.kpPop !== '1' || e.button !== 0) return;
    const head = e.target.closest && e.target.closest('.ph');
    if (!head || !d.contains(head) || e.target.closest('button,a,input,select,textarea,summary,[contenteditable]')) return;
    const rect = d.getBoundingClientRect();
    d._kpDrag = {id:e.pointerId, x:e.clientX, y:e.clientY, left:rect.left, top:rect.top};
    d.classList.add('kp-dragging');
    try { d.setPointerCapture(e.pointerId); } catch(err){}
    e.preventDefault();
  });
  d.addEventListener('pointermove', e => {
    const drag = d._kpDrag;
    if (!drag || drag.id !== e.pointerId) return;
    const de = document.documentElement;
    const vw = de.clientWidth || window.innerWidth, vh = de.clientHeight || window.innerHeight;
    const left = Math.max(8, Math.min(drag.left + e.clientX - drag.x, vw - d.offsetWidth - 8));
    const top = Math.max(8, Math.min(drag.top + e.clientY - drag.y, vh - d.offsetHeight - 8));
    d._kpPosition = {left, top};
    d.style.left = Math.round(left) + 'px'; d.style.top = Math.round(top) + 'px';
  });
  const endDrag = e => {
    if (!d._kpDrag || d._kpDrag.id !== e.pointerId) return;
    d._kpDrag = null; d.classList.remove('kp-dragging');
    try { if (d.hasPointerCapture(e.pointerId)) d.releasePointerCapture(e.pointerId); } catch(err){}
  };
  d.addEventListener('pointerup', endDrag);
  d.addEventListener('pointercancel', endDrag);
  document.body.appendChild(d);
  EVPOP = d;
  return d;
}

// 点击小窗外部关闭。用**捕获**阶段：否则消息里的 onclick/按钮会先处理，
// 而且冒泡阶段可能被 stopPropagation 拦掉，导致"点外面关不掉"。
function evPopOutside(e){
  if (!EVPOP || EVPOP.contains(e.target)) return;
  // task-33：点顶部页签是"切页"，不是"主动关窗"——先把弹窗收起来，
  // 但保留参数，回到该页签时自动放回来（主动关/评分的清空见 evPopClose/closeModal）。
  const nav = e.target && e.target.closest ? e.target.closest('#nav') : null;
  if (nav){ UI_PERSIST.keepNavClose = true; evPopClose(); return; }
  evPopClose();
}

function evPopClose(){
  // task-36：切页签收起前先抓 DOM+参数快照（切回时 0ms 摆回，不闪）；
  // 主动关闭（✕/Esc/点窗外）则连参数带快照一起清掉，不会自己弹回来。
  if (UI_PERSIST.keepNavClose) uiSnapshotPopup();
  else { uiForgetPopup(); uiClearSnapshot(); }
  UI_PERSIST.keepNavClose = false;
  // 顺手清掉"小窗当前挂着哪个知识点"的标记（见 mmOpenKp）：
  // 关掉即作废，否则在途的 /api/mindmap/kp 响应回来会把小窗又弹出来。
  // 卡 C-0036：课件展开状态（dataset.deck / .deck-open）也一起清——
  // 关掉小窗再点别的节点，不该莫名其妙带着上一条的课件图展开着。
  if (EVPOP) {
    EVPOP.style.display = 'none';
    EVPOP.dataset.kp = '';
    // 卡 C-0055：出处小窗"这一页"的在途请求也一并作废（关掉即作废），
    // 否则迟到的 /api/page-kps 响应会把内容写回一个已经关掉的窗。
    EVPOP.dataset.cite = '';
    // 卡 C-0042：居中标记与 resize 监听也一起作废，否则下一个出处小窗
    // 会继承"知识点弹窗"的居中身份，或者监听越挂越多。
    EVPOP.dataset.kpPop = '';
    EVPOP._kpPosition = null;
    EVPOP._kpDrag = null;
    EVPOP.classList.remove('kp-dragging');
    if (EVPOP._resizeH) {
      window.removeEventListener('resize', EVPOP._resizeH);
      EVPOP._resizeH = null;
    }
    EVPOP.dataset.deck = '';
    EVPOP.classList.remove('deck-open');
    // 特性1：左侧导图栏状态一并清掉，下一个出处小窗不会继承"加宽 + 抽屉展开"
    EVPOP.classList.remove('mmkp-has-side', 'mm-tree-open',
      'mmkp-has-teach', 'mm-teach-open');
    MMSIDE.open = false;
    MMTEACH.open = false;
  }
  document.removeEventListener('click', evPopOutside, true);
  document.removeEventListener('keydown', evPopEsc);
  window.removeEventListener('scroll', evPopScroll, true);
  document.querySelectorAll('.cite.on').forEach(c => c.classList.remove('on'));
}

function evPopEsc(e){ if (e.key === 'Escape') evPopClose(); }

// 浮窗是 position:fixed，滚动之后就会和角标脱节（看起来指向别处）。
// 与其跟着滚（要持续重算坐标、还会闪烁），不如按浮层惯例收掉。
// 用捕获阶段：内部滚动容器（#thread）的 scroll 事件不冒泡到 window。
//
// ⚠ 但**弹窗自己正文区的滚动必须排除**：知识点弹窗里是一整段块原文，
// 滚一下就被关掉的话根本读不完（scroll 不冒泡，却在捕获阶段经过 window，
// 所以这里只能靠判 target 是不是弹窗内部来解决）。
function evPopScroll(e){
  // task-36：切回后的恢复期（快照恢复 + 滚动位置重试）不能被
  // window.scroll 误关，否则表现为"闪一下然后消失"。
  if (Date.now() < (UI_PERSIST.holdUntil || 0)) return;
  if (EVPOP && e && e.target && EVPOP.contains(e.target)) return;
  evPopClose();
}

// 把浮窗放进视口：**知识点弹窗直接居中**，出处小窗仍贴角标；两者都过边界钳制。
//
// 必须先显示再测量：`display:none` 时 offsetWidth/offsetHeight 都是 0，
// 算出来的 left/top 全是 0——表现是"弹窗永远贴在左上角"，
// 而且看起来像定位算错，其实是测量时机错。
//
// ★ 卡 C-0042：人报"知识点弹窗展开课件原图时有几率跑到屏幕外，
//   「下一知识点」点不到"。根因有两条，都在这个函数的历史写法里：
//   1. `<img>` 是**异步加载**的：mmShowSlide 塞进 .kpslideimg 那一刻它还没有
//      内在尺寸（高度≈0），这次量到的 h 偏小；等图加载完弹窗长高（CSS 给的是
//      `.kpslideimg{width:100%;max-height:52vh}`），位置却还按旧尺寸算，
//      底部那一行 .pf（「下一知识点」/「展开当页课件」）就被顶出视口——
//      "有几率"正来自这里：取决于图片加载快慢、图的长宽比、当时的剩余空间。
//      → 现在图 load/error 之后重新调一次本函数（见 mmShowSlide）。
//   2. 老的贴底分支 `Math.max(8, innerHeight - h - 8)` 在 h 超过视口时会退化成
//      top=8，于是 bottom = 8 + h 照样越界——**没有"尺寸本身超限"的兜底**。
//      → 现在先做尺寸自限，再做位置钳制（下面 b/c 两步）。
//
// 于是这里有四件事：
//   a) 居中：加过 `dataset.kpPop='1'` 的弹窗（知识点页签点节点弹出来的那些）
//      left=(vw-w)/2、top=(vh-h)/2 —— 人点名要的"直接显示在屏幕正中央"；
//   b) 尺寸自限：w/h 超过可用空间就把内联 max-width/max-height 压到可用空间
//      再重量一次——比视口还大的元素，位置怎么算都会出界；
//   c) 位置钳制：left/top 夹进 [pad, vw-w-pad] / [pad, vh-h-pad]，
//      于是 **left/top/right/bottom 四条边全在视口内**；而 .pf 是 flex 尾部
//      不参与 .pbody 滚动，弹窗在视口内 ⇒「下一知识点」永远可点；
//   d) 测量一律在 .evpop 自己的 flex 布局下做：早先切成 block 量到的是
//      "不收缩的块高"，与真正渲染出来的高度不是一回事（见下面的 ★ 注释）。
//
// vw/vh 取 documentElement.clientWidth/Height（= 可视内容区，不含滚动条），
// 这样"右边缘被滚动条压住"也不算越界；取不到才回落到 innerWidth/innerHeight。
function evPopPlace(el){
  const d = evPopEl();
  const c = (el && el.getBoundingClientRect) ? el.getBoundingClientRect() : null;
  // 先按"下方"假定放一次拿到真实尺寸；不够放再翻上方
  d.style.visibility = 'hidden';
  d.style.display = 'flex';   // 用真实布局测量（不是 block，见函数头 d 条）
  d.style.left = '0px';
  d.style.top = '0px';
  d.style.maxWidth = '';      // 每次重算都从 CSS 的约束重新出发，免得越压越小
  d.style.maxHeight = '';
  const de = document.documentElement || {};
  const pad = 8;
  const vw = de.clientWidth || window.innerWidth;
  const vh = de.clientHeight || window.innerHeight;
  const availW = Math.max(120, vw - pad * 2);
  const availH = Math.max(120, vh - pad * 2);
  const isKp = d.dataset.kpPop === '1';
  d.style.width = isKp ? Math.min(availW, vw * KPW.width / 100) + 'px' : '';
  d.style.height = isKp ? Math.min(availH, vh * KPW.height / 100) + 'px' : '';
  if (isKp){ d.style.maxWidth = availW + 'px'; d.style.maxHeight = availH + 'px'; }
  let w = d.offsetWidth, h = d.offsetHeight;
  // (b) 尺寸自限：比视口还大就压到可用空间，然后重量（否则钳制区间是空的）
  if (w > availW || h > availH){
    if (w > availW) d.style.maxWidth = availW + 'px';
    if (h > availH) d.style.maxHeight = availH + 'px';
    w = d.offsetWidth; h = d.offsetHeight;
  }
  d.classList.toggle('kp-narrow', isKp && w < 850);
  d.classList.toggle('kp-compact', isKp && w < 1000);
  let left, top;
  if (isKp && d._kpPosition){
    left = d._kpPosition.left;
    top = d._kpPosition.top;
  } else if (!c || isKp){
    // (a) 知识点弹窗：水平垂直居中于视口（也是"没有锚点"时的默认落点）
    left = (vw - w) / 2;
    top = (vh - h) / 2;
  } else {
    left = c.left + c.width / 2 - w / 2;
    top = c.bottom + pad;
    if (top + h > vh - pad) {
      const above = c.top - h - pad;
      // 上方也放不下时，比较上下剩余空间，取大的一侧并贴边
      top = above >= pad ? above : Math.max(pad, vh - h - pad);
    }
  }
  // (c) 边界钳制：居中/贴角标都过这一道，四条边全部落在视口内
  left = Math.max(pad, Math.min(left, vw - w - pad));
  top = Math.max(pad, Math.min(top, vh - h - pad));
  d.style.left = Math.round(left) + 'px';
  d.style.top = Math.round(top) + 'px';
  if (isKp && d._kpPosition) d._kpPosition = {left, top};
  d.style.visibility = '';
  // ★ 大管家修复（2026-09-22）：测量完必须把 display 还原成 CSS 里的 flex。
  //   原写法停在 'block'（内联样式战胜 .evpop{display:flex}），导致
  //   `.evpop .pbody{flex:1 1 auto;min-height:0;overflow:auto}` 三件套全部失效：
  //   正文区被内容撑破、超出部分被 .evpop{overflow:hidden} 裁掉，
  //   于是**长内容既看不全也滚不动**（人反馈"没有垂直滚动条"就是这个原因）。
  //   实测（headless Edge + CDP）：改 flex 后 .pbody 被约束到可视高度，
  //   scrollHeight 1145 > clientHeight 412、滚动条宽 10px，内容不再溢出。
  d.style.display = 'flex';
}

// 把一份 HTML 装进小窗并显示，同时挂上"点外关闭 / Esc / 滚动关闭"。
//
// 抽出来是为了让**知识点弹窗复用同一套关闭机制**，而不是另造一个浮层：
// 全页只有一个小窗（#evpop 那个 div），同一时刻只显示一份内容，
// 连点两个节点就是就地换内容，不会堆叠、也不会串。
function evPopMount(el, html){
  const d = evPopEl();
  // html 可省：evPopShow 是先自己填好 innerHTML 再叫这里来定位的
  if (html !== undefined) d.innerHTML = html;
  if (d.dataset.kpPop === '1'){
    const head = d.querySelector('.ph');
    if (head && !head.querySelector('.kp-drag-hint')){
      const hint = document.createElement('span');
      hint.className = 'kp-drag-hint'; hint.title = '按住顶部拖动窗口';
      hint.textContent = '⋮⋮ 拖动'; head.insertBefore(hint, head.firstChild);
    }
  }
  evPopPlace(el);
  document.removeEventListener('click', evPopOutside, true);
  document.addEventListener('click', evPopOutside, true);
  document.removeEventListener('keydown', evPopEsc);
  document.addEventListener('keydown', evPopEsc);
  window.removeEventListener('scroll', evPopScroll, true);
  window.addEventListener('scroll', evPopScroll, true);
  // 卡 C-0042：视口尺寸一变（拖窗口 / 缩放 / 分屏）当初算的坐标就过期了，
  // 重新兜一次边界。句柄挂在弹窗元素自己身上，关闭时一并摘掉（见 evPopClose），
  // 不会越挂越多。
  if (d._resizeH) window.removeEventListener('resize', d._resizeH);
  d._resizeH = () => evPopPlace(el);
  window.addEventListener('resize', d._resizeH);
}

function evPopShow(el, n){
  n = String(n);
  const body = el.closest('.body');
  const list = msgEvidence(body);
  const e = list.find(x => String(x.idx) === n);
  document.querySelectorAll('.cite.on').forEach(c => c.classList.remove('on'));
  el.classList.add('on');

  const d = evPopEl();
  // 卡 C-0042：出处小窗贴角标（不居中），顺手摘掉"知识点弹窗"的居中身份，
  // 免得它顶掉知识点弹窗之后还按居中的规矩摆。
  d.dataset.kpPop = '';
  // 特性1：出处小窗不显示左侧导图栏（宽屏加宽 / 窄屏抽屉都不该带过来）
  d.classList.remove('mmkp-has-side', 'mm-tree-open',
    'mmkp-has-teach', 'mm-teach-open');
  MMSIDE.open = false;
  MMTEACH.open = false;
  d.dataset.cite = '';   // 卡 C-0055：上一条出处的"这一页"请求一并作废
  if (!e) {
    d.innerHTML = `<div class="ph"><div class="src">
        <div class="loc">未找到第 ${esc(n)} 条出处</div>
        <div class="hdg">本轮回答只带了 ${list.length} 条出处，
          可能该编号是模型自己写的、并不存在。</div>
      </div><button class="pclose" onclick="evPopClose()">✕</button></div>`;
  } else {
    d.innerHTML = `
      <div class="ph">
        <div class="src">
          <div class="loc">[${e.idx}] 《${esc(e.doc_title)}》${esc(e.location || '')}</div>
          <div class="hdg">${esc(e.role_label || '')}${
            e.is_figure ? ' · 视觉模型解读，非原文' : ''}${
            e.heading_path ? ' · ' + esc(e.heading_path) : ''}</div>
        </div>
        <button class="pclose" onclick="evPopClose()" title="关闭（Esc）">✕</button>
      </div>
      <div class="pbody"><div class="evx" id="evpop-extra"></div>${mdToHtml(e.text)}</div>
      <div class="pf">
        <button class="ghost" onclick="evPopClose();openChunk(${e.chunk_id})">看上下文</button>
        <button class="ghost" onclick="evPopClose();openDoc(${e.doc_id})">看该资料大纲</button>
        <span class="pmeta">语义 ${e.vec_score} · 关键词 ${e.kw_score}${
          e.confidence === 'low' ? ' · <span class="badge warn">低置信</span>' : ''}</span>
      </div>`;
  }
  evPopMount(el);   // 定位 + 挂关闭监听（与上面填 innerHTML 是两步，别合并）
  // ★ 卡 C-0055：正文先出来（上面这一段），"这一页的课件图 + 该页知识点"由
  //   这个异步请求补进去——点角标不该等一次网络往返才看到原文。
  if (e) evCiteExtras(e, el);
}

// ---------------- ★ 卡 C-0055：出处小窗里的"这一页" ----------------
//
// 人拍板（2026-09-27）：「AI问答版块点击右下标签时最好可以跳出那一页的课件图片。
// 并且可以看到那一页课件对应的知识点，形成一个网状知识体系。」
//
// 于是出处小窗里补两样：
//   ① 这一页的课件图（点图可开原尺寸，与知识点弹窗的交互一致）；
//   ② 归属这一页的知识点（点它开**知识点弹窗**）。
// 「图 ↔ 知识点」在小窗里这样就双向可达了：知识点弹窗那边本来就有页码 chip
// 能回到图（`mmShowSlide`），这一头补上"图 → 知识点"。
//
// **页码解析、图缓存、页归属全在后端算**（`/api/page-kps`，见 `api_page_kps`），
// 前端只渲染：一处请求拿全，免得页码规则在前后端各写一套、迟早在边界上分叉。
//
// 渲染分两步：小窗先出正文（点下去立刻读得到），这次请求回来再填占位块。
// 所以这里**绝不能把小窗卡在加载态**；拿不到就写一句人话（同 C-0036 容错风格）。
async function evCiteExtras(e, el){
  const box = $('#evpop-extra');
  if (!box) return;
  const n = String(e.idx);
  const pop = evPopEl();
  // 迟到的响应不许盖掉新内容：小窗全页只有一个，连点两个角标时先发的请求
  // 可能后回来（与 dataset.kp 是同一个道理）。
  pop.dataset.cite = n;
  box.innerHTML = '<div class="small muted">正在取这一页的课件图与知识点…</div>';
  let d;
  try {
    d = await api('/api/page-kps?doc_id=' + encodeURIComponent(e.doc_id) +
                  '&source_ref=' + encodeURIComponent(e.source_ref || ''));
  } catch(err){
    if (!evCiteAlive(pop, n)) return;
    box.innerHTML = `<div class="small muted">这一页的课件图与知识点没取到：${
      esc(err.message)}</div>`;
    evPopPlace(el);
    return;
  }
  if (!evCiteAlive(pop, n)) return;
  box.innerHTML = evCiteExtrasHtml(d);
  evCiteBind(d, el);
  evPopPlace(el);   // 内容变高了：位置要重算（同 evPopPlace 头部的说明）
}

// 这次响应还算不算数：① 没被更晚点的角标顶掉；② 小窗没被知识点弹窗接管
// （接管后 `#evpop-extra` 已经不在文档里，往它写等于写进孤儿节点，而
//  evPopPlace 会把**知识点弹窗**挪到角标位置——那才是真的错）。
function evCiteAlive(pop, n){
  return (pop.dataset.cite || '') === n && !(pop.dataset.kp || '')
      && !!document.getElementById('evpop-extra');
}

// 小窗里"这一页"那两块 HTML。空态一律**明说原因**（没有页码 / 没有图缓存 /
// 这一页没有归属的知识点 / 产物是旧格式…），绝不留白也绝不猜。
function evCiteExtrasHtml(d){
  const sl = d.slide || {};
  const pages = d.pages || [];
  const slides = sl.slides || [];
  let html = '';
  // ① 课件页图
  if (!pages.length){
    html += `<div class="evxttl">课件页图</div>
      <div class="small muted">${esc(d.ref_note || '这条出处没有页码，定位不到课件页。')}</div>`;
  } else {
    html += `<div class="evxttl">课件页图 · ${
      pages.length > 1 ? 'p.' + pages[0] + '–p.' + pages[pages.length - 1]
                       : 'p.' + pages[0]}${
      d.ref_kind === 'figure' ? '（识图块）' : ''}</div>`;
    if (pages.length > 1){
      // 一条出处跨多页：给一排页号，有图的可点（没图的灰着，点了也不会换错页）。
      // 这套 chip 的样式与知识点弹窗的页号是同一套（`.kppage`），观感一致。
      html += `<div class="kppages" id="evpop-pages">` + slides.map(s =>
        `<span class="kppage${s.ok ? '' : ' off'}" data-page="${esc(s.page)}"
          title="${s.ok ? '看 p.' + esc(s.page) + ' 的课件图'
                       : 'p.' + esc(s.page) + '：没有课件图缓存'}">p.${esc(s.page)}</span>`
      ).join('') + `</div>`;
    }
    html += `<div id="evpop-figbox"></div>`;
    if (d.ref_note){
      html += `<div class="small muted" style="margin-top:5px">${esc(d.ref_note)}</div>`;
    }
  }
  // ② 该页的知识点（可能多条：v3 起一条知识点可以覆盖多页，一页也可能被多条命中）
  html += `<div class="evxttl" style="margin-top:11px">这一页的知识点${
    d.n_kps ? ' · ' + d.n_kps + ' 条' : ''}</div>`;
  if (d.n_kps){
    html += `<div class="evkps">` + d.kps.map(k => `
      <span class="evkp" data-kp="${esc(k.kp_id)}" data-name="${esc(k.name || '')}"
        title="点开这条知识点（${esc(k.pages_label || '')}${
          k.insufficient ? ' · 材料不足' : ''}）"><b>${esc(k.kp_id)}</b>${
        k.name ? ' ' + esc(k.name) : ''}<span class="pg">${
        esc(k.pages_label || '')}</span></span>`).join('') + `</div>`;
  } else if (!pages.length){
    html += `<div class="small muted">没有页码也就找不到这一页的知识点。${
      esc(d.kp_message || '')}</div>`;
  } else {
    html += `<div class="small muted">${esc(d.kp_message
      || ('p.' + pages[0] + ' 这一页没有归属的知识点（按当前口径不建，'
          + '或者这一页还没被整理覆盖）。'))}</div>`;
  }
  return html;
}

function evCiteBind(d, el){
  const pages = d.pages || [];
  if (pages.length) evCiteSlide(d, pages[0], el);
  document.querySelectorAll('#evpop-pages .kppage').forEach(chip => {
    if (chip.className.indexOf('off') >= 0) return;
    chip.onclick = (ev) => {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      evCiteSlide(d, parseInt(chip.dataset.page), el);
    };
  });
  // 知识点 chip：点它开**知识点弹窗**（复用 mmKp* 那一套，见 mmOpenKp）。
  // ★ docId 必须显式传：出处这份资料**未必**是「知识点」页签当前选中的那份
  //   （人可以从问答页直接点角标，一次都没进过页签），不传就会去别份课件里找
  //   同一个 kp_id，结果必然是"读取失败"。
  document.querySelectorAll('#evpop-extra .evkp').forEach(chip => {
    chip.onclick = (ev) => {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      mmOpenKp(chip.dataset.kp, chip.dataset.name, null, false, d.doc_id);
    };
  });
}

// 放这一页的图。找不到图就**明说**（哪一页、哪个目录没有缓存），绝不给别的
// 页的图充数——张冠李戴比没图更坏（与知识点弹窗的 mmShowSlide 同一条底线；
// 那边容器是 #mmkp-figbox、图类是 .kpslideimg，这边是 #evpop-figbox/
// .evslideimg：全页只有一个小窗，两种内容的容器不能同名）。
function evCiteSlide(d, page, el){
  const box = $('#evpop-figbox'); if (!box) return;
  const sl = d.slide || {};
  const slides = sl.slides || [];
  const has = slides.filter(s => s.ok);
  if (!slides.some(s => s.page === page && s.ok)){
    box.innerHTML = `<div class="small muted">该页没有课件图缓存${
      sl.deck
        ? '（这张页图还没有缓存）'
        : '（这份课件还没有缓存页图）'}。其他内容照常可看。</div>`;
    evPopPlace(el);
    return;
  }
  const url = '/img/slide?doc_id=' + encodeURIComponent(d.doc_id) + '&page=' + page;
  box.innerHTML = `<img class="evslideimg" src="${url}" alt="p.${esc(page)} 课件页图"
      title="点图在新窗口打开原尺寸"><div class="small muted" style="margin-top:5px">
      p.${esc(page)} · 点图可看原尺寸${
      has.length > 1 ? ' · 这一条出处覆盖 ' + has.length + ' 页，可点上面的页号换页' : ''}</div>`;
  const img = box.querySelector('.evslideimg');
  if (img){
    img.onclick = () => window.open(url, '_blank');
    // 图是**异步加载**的：塞进 DOM 这一刻它还没有内在尺寸（高度≈0），这次量到的
    // 位置就是错的；加载完必须重算一次（与 mmShowSlide 的 reflow 同一个原因）。
    const reflow = () => evPopPlace(el);
    img.onload = reflow;
    img.onerror = reflow;
    if (img.complete) reflow();    // 已在缓存里时 load 不会再触发
  }
  document.querySelectorAll('#evpop-pages .kppage').forEach(b =>
    b.classList.toggle('on', parseInt(b.dataset.page) === page));
}

// 绑定必须**限定在本条消息的容器内**。
//
// 这里曾经用硬编码的 id="evd" 定位证据区，多轮对话时页面上会出现多个同 id 元素，
// 而 getElementById 只返回第一个 —— 于是第二轮点角标总跳到第一轮的出处。
// 全局 querySelectorAll('.cite') 也是同样的问题。
// 现在一律按 msgEl（本条消息的 .body 容器）作用域查找。
//
// 另注：出处小窗挂在 document.body 下，所以它**不在** 这个作用域里；
// 但取哪一条证据始终从 body 走 MSG_EV，因此多轮不会串。
function bindCiteClicks(root){
  const scope = root || document;
  scope.querySelectorAll('.cite').forEach(el => {
    el.onclick = (ev) => {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      evPopShow(el, el.dataset.n);
    };
  });
}

async function sendChat(){
  if (CHAT.busy) return;
  const ta = $('#chatq');
  const q = (ta ? ta.value : '').trim();
  if (!q) return;
  ta.value = '';
  CHAT.busy = true;

  // 先把这一轮的 DOM 骨架放进去，流式内容直接往里写
  const thread = $('#thread');
  const scope = $('#chat-scope') ? $('#chat-scope').value : '';
  const uEl = document.createElement('div');
  uEl.className = 'msg user';
  uEl.innerHTML = `<div class="who">我</div><div class="body">
    <div class="bubble">${mdToHtml(q)}</div></div>`;
  const aEl = document.createElement('div');
  aEl.className = 'msg ai';
  aEl.innerHTML = `<div class="who">AI</div><div class="body">
    <div class="bubble"><span class="typing"></span></div></div>`;
  thread.appendChild(uEl);
  thread.appendChild(aEl);
  const wrap = $('#chatwrap');
  if (wrap) wrap.classList.remove('is-empty');
  thread.scrollTop = thread.scrollHeight;
  const bubble = aEl.querySelector('.bubble');
  // 出处数据挂在本条消息的 .body 上（见 MSG_EV），流式增量只碰 .bubble。
  // 早先每来一个增量就把「答案+证据」整块重写 innerHTML，
  // 结果是证据卡片被反复销毁重建——展开状态丢失，用户点角标时
  // 拿到的可能是已被替换掉的旧节点。
  const btn = $('#chatgo'); if (btn) { btn.disabled = true; btn.textContent = '思考中…'; }

  let answer = '';
  // 只带最近若干轮，避免历史把上下文撑爆
  const history = CHAT.msgs.slice(-8).map(m => ({role:m.role, content:m.content}));

  try {
    const body = { query: q, history };
    if (scope) body.doc_ids = [parseInt(scope)];
    const resp = await fetch('/api/chat/stream', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const reader = resp.body.getReader();
    const dec = new TextDecoder('utf-8');
    let buf = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!chunk.startsWith('data: ')) continue;
        let obj; try { obj = JSON.parse(chunk.slice(6)); } catch(e){ continue; }
        if (obj.type === 'sources') {
          CHAT.ev = obj.evidence || [];
          // 证据挂在本条消息上（角标点击就地弹出），同时按轮存一份供重渲染还原
          setMsgEvidence(aEl.querySelector('.body'), CHAT.ev);
          setTurnEv(CHAT.msgs.length + 1, CHAT.ev);
          bindCiteClicks(aEl.querySelector('.body'));
        } else if (obj.type === 'delta') {
          answer += obj.text;
          bubble.innerHTML = linkCites(mdToHtml(answer));
          // 重写 bubble 会**换掉全部 .cite 节点**，所以两件事都要做：
          //   1. 重新绑定（新节点没有 onclick）
          //   2. 若浮窗正指向一个已被换掉的角标，就收掉它——
          //      否则会出现"浮窗还开着、但对应角标已经不存在"的悬空状态
          bindCiteClicks(aEl.querySelector('.body'));
          if (EVPOP && EVPOP.style.display !== 'none') {
            const on = aEl.querySelector('.cite.on');
            if (!on || !document.body.contains(on)) evPopClose();
          }
          thread.scrollTop = thread.scrollHeight;
        } else if (obj.type === 'done') {
          const u = obj.usage || {};
          if (u.completion_tokens) {
            const hint = document.createElement('div');
            hint.className = 'small muted';
            hint.style.marginTop = '6px';
            hint.textContent = `用时 ${(obj.elapsed||0).toFixed(1)}s · `
              + `输出 ${u.completion_tokens} tokens`;
            aEl.querySelector('.body').appendChild(hint);
          }
        } else if (obj.type === 'error') {
          answer += `\n\n**出错**：${obj.message}`;
          bubble.innerHTML = linkCites(mdToHtml(answer));
          bindCiteClicks(aEl.querySelector('.body'));
        }
      }
    }
  } catch(e) {
    answer += `\n\n**请求失败**：${e.message}`;
    bubble.innerHTML = linkCites(mdToHtml(answer));
    bindCiteClicks(aEl.querySelector('.body'));
  }

  CHAT.msgs.push({role:'user', content:q});
  CHAT.msgs.push({role:'assistant', content:answer});
  CHAT.busy = false;
  if (btn) { btn.disabled = false; btn.textContent = '发送'; }
  // 只绑定本轮这条消息；历史消息的绑定在 viewAssistant 里做。
  // 早先这里调的是全局 bindCiteClicks()，会把所有历史消息重新绑一遍，
  // 而绑定逻辑当时又是全局查找 id，于是多轮之后跳转全指向第一轮。
  bindCiteClicks(aEl.querySelector('.body'));
  thread.scrollTop = thread.scrollHeight;
  safeFocus(ta);
}

