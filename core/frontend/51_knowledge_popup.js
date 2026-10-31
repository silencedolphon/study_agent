// ---------------- 导图节点 → 末级知识点总结小窗 ----------------
// 复用上面 evPop 那一套（同一个 .evpop 小窗、同一套 Esc/点外/滚动关闭），
// **不另造弹窗组件**：同一时刻只有一个小窗，连点不同节点就是就地换内容。
//
// 内容分两段取：树接口只内联标记（lkp_id / lkp_ready），点开时才去
// /api/summary-kp 拿总结正文——68 条总结正文不进树接口，免得拖慢首屏。
// 弹窗展示的是**提炼后的总结**（要点 + 正文 + 出处），不展示原文块：
// 人已拍板"知识点给总结，不贴原文"；要看原文就点出处里的 chunk_id 跳上下文。
//
// 小窗当前挂着哪个知识点，标记写在小窗自己身上（EVPOP.dataset.kp）：
// 这样"用户关掉小窗""改点无知识点的节点""出处弹窗顶掉它"三种情况下，
// 迟到的 /api/summary-kp 响应都能就地作废——否则点开节点后立刻 Esc，
// 慢一点的响应回来会把小窗重新弹出来；先点 A 再点 B，A 的响应也会盖掉 B。
//
// 卡 C-0036 的交互（人点名要的四件事）：
//   1. 正文区自己带滚动条：靠 CSS 的 .evpop .pbody{flex:1;min-height:0}，
//      而 evPopScroll 早判过"target 在弹窗里就不关窗"（见那里的注释）；
//   2. 默认**只显示要点**；总结正文 / 出处明细 / 生成链条收进 <details>，
//      点「▸ 展开：…」才出来，可再收起。**换知识点就重置回折叠**——
//      那屏是"上一条的详情"，留着展开会让人以为下一条也长这样；
//      而课件展开是"对照模式"，反而要跨知识点延续（见下）；
//   3. 左下「下一知识点」：顺序 = 导图树的先序（与 mmNodes 渲染的上下顺序一致），
//      **由后端给**（/api/summary-kp 的 next_id / order_index / order_total），
//      前端只负责跳，绝不自己排。首/末条策略：prev/next 是空串就**置灰**，
//      不做循环——从末条绕回第一条会让人以为"还有下一条"，比点不动更坏；
//   4. 右下「展开当页课件」：取 logs/figure_desc/<deck>/slide_<页号>.png
//      （走看板自己的 /img/slide 路由，只放行这一个目录、这一种文件名）。
//      已展开时点「下一知识点」，课件**跟着切到新知识点那一页**：
//      状态记在 EVPOP.dataset.deck 上，新内容渲染完自动重新展开。
//      多页知识点：给一排页号（p.15 / p.16 …），默认落在第一个**有图**的页，
//      点页号换页；没有图的页号标灰并写明"没有课件图缓存"（不静默给错页）。
//
// 就地读写、不抽成小函数：`tools/check_mindmap_kp.py` 是按函数名抠源码去
// node 里跑的，多一个函数就多一处要在那份清单里登记。
// （C-0036 新增的 mmNextKp / mmShowSlide / mmToggleDeck 是弹窗交互自己的
//  控制流，相关弹窗验证脚本会一起抽取运行。）
const MMPOP = {kp: null, anchor: null, page: 0, docId: 0};

// 有总结的节点：先占位，再去取总结。
// `keepDeck`：这次打开要不要**延续课件展开状态**——只有「下一知识点」
// 切过来才为 true（见 mmNextKp）。用户从导图点节点永远是 false：
// 换一条知识点就从"只看要点"重新开始。
// `docId`（卡 C-0055）：这次要读**哪份课件**的产物。缺省（不传/0）照旧用页签
// 当前选中的那份（`MINDMAP.docId`）——原有调用方一个字节都不用改；从问答页的
// **出处小窗**跳过来时必须显式给，因为出处那份资料未必是页签里选中的那份。
async function mmOpenKp(kpId, name, el, keepDeck, docId){
  if (!kpId) return;
  const did = docId || MINDMAP.docId;
  const pop = evPopEl();
  pop.dataset.kp = kpId;
  if (!keepDeck) pop.dataset.deck = '';
  // 特性1/2：先摘掉上一屏的侧栏状态；真正渲染时 mmRenderKp 会按本课件重新加回
  pop.classList.remove('mmkp-has-side', 'mm-tree-open',
    'mmkp-has-teach', 'mm-teach-open');
  MMPOP.anchor = el;
  MMPOP.docId = did;
  // task-33：记住这个知识点弹窗的参数；切页签回来自动恢复（主动关闭即清空）。
  uiRememberPopup({type: 'mm', kpId: String(kpId), name: name || '', docId: did});
  evPopMount(el, `<div class="ph"><div class="src">
      <div class="loc">${esc(name || kpId)}</div>
      <div class="hdg">正在读取知识点总结…</div>
    </div><button class="pclose" onclick="evPopClose()" title="关闭（Esc）">✕</button></div>
    <div class="pbody"><div class="small muted">加载中…</div></div>`);
  // 卡 C-0056：这一屏（"正在读取知识点总结…"）也是弹窗，人等接口时照样会滚它——
  // 防线先挂上，别让"加载中"那一段成为唯一还能把滚动漏给外层的窗口期。
  mmKpScrollGuard(pop);
  let d;
  try {
    d = await api('/api/summary-kp?doc_id=' + encodeURIComponent(did) +
                  '&kp_id=' + encodeURIComponent(kpId));
  } catch(e){
    // 期间用户又点了别的节点（或关掉了小窗）：这次结果作废，别盖掉新内容
    if ((evPopEl().dataset.kp || '') !== kpId) return;
    evPopMount(el, `<div class="ph"><div class="src">
        <div class="loc">知识点 ${esc(kpId)} 读取失败</div>
        <div class="hdg">${esc(e.message)}</div>
      </div><button class="pclose" onclick="evPopClose()" title="关闭（Esc）">✕</button></div>`);
    return;
  }
  if ((evPopEl().dataset.kp || '') !== kpId) return;
  mmRenderKp(d, el, !!keepDeck);
}

// 出处：页 + chunk_id（可点，跳"看上下文"）。跨页块的页码是区间字符串，
// 照原样显示——写死成"当前页"会指错页（见 提示词-知识点总结.md §3#17）。
function mmLkpSrcHtml(srcs){
  const list = (srcs || []).filter(s => s && (s.chunk_ids || []).length);
  if (!list.length){
    return `<div class="small muted">这条没有给出处（模型没写出 sources）。</div>`;
  }
  return `<div class="lkpsrcs">` + list.map(s =>
    `<span class="lkpsrc">p.${esc(s.page)}${s.figure_desc ? ' · 含图片理解' : ''} · ${
      (s.chunk_ids || []).map(c => `<span class="cid" data-chunk="${esc(c)}"
        title="点开看这个块的上下文">chunk ${esc(c)}</span>`).join('、')}</span>`
  ).join('') + `</div>`;
}

// 弹窗正文（卡 C-0036 重排）：头部 = 名字 + kp_id + 层级 + 导图路径；
// **默认只有"出处一行 + 要点列表"**；总结正文 / 出处明细 / 生成链条收进
// <details>（默认折叠，点标题行展开、可再收起）；底部固定一行两个按钮。
// keepDeck=true 时渲染完自动把课件区展开（「下一知识点」带着课件切过来）。
function mmRenderKp(d, el, keepDeck){
  MMPOP.kp = d;
  MMPOP.anchor = el;
  MMPOP.page = 0;          // 换了知识点：课件回到"第一个有图的页"
  const pts = (d.points || []);
  const nMat = (d.material_block_ids || []).length;
  const slides = (d.slides || []);
  const nFig = slides.filter(s => s.ok).length;
  const seqTip = d.order_total
    ? `导图上第 ${d.order_index} / ${d.order_total} 条知识点` : '这条不在导图顺序里';
  // 卡 C-0056：上一 / 下一对称。邻居照旧**由后端给**（`prev_id`/`next_id`，
  // 见 /api/summary-kp 的 _lkp_seq），前端一个字都不重排：首条没有 prev_id、
  // 末条没有 next_id，两处都只是把按钮置灰（点不动 > 跳到打不开的知识点）。
  const prevBtn = d.prev_id
    ? `<button class="ghost" id="mmkp-prev" data-prev="${esc(d.prev_id)}"
         title="上一个：${esc(d.prev_name || d.prev_id)}（${esc(seqTip)}）">◀ 上一知识点</button>`
    : `<button class="ghost" id="mmkp-prev" disabled
         title="已经是第一个知识点（${esc(seqTip)}）">◀ 上一知识点</button>`;
  const nextBtn = d.next_id
    ? `<button class="ghost" id="mmkp-next" data-next="${esc(d.next_id)}"
         title="下一个：${esc(d.next_name || d.next_id)}（${esc(seqTip)}）">下一知识点 ▶</button>`
    : `<button class="ghost" id="mmkp-next" disabled
         title="已经是最后一个知识点（${esc(seqTip)}）">下一知识点 ▶</button>`;
  // 卡 C-0056：这行小字的数字来自 `/api/summary-kp` 的 `note_count`
  // （后端口径 = 这个弹窗的「查看笔记」列表），所以**不会为它多发一次请求**。
  // 0 条也照常显示：不然"没有这行字"会被读成"这窗不支持笔记"，
  // 恒显示顺带把「还没有笔记 → 点【记入笔记】写第一条」这条路指出来。
  const nNotes = Math.max(0, parseInt(d.note_count || 0, 10) || 0);
  const noteTip = `已绑定 ${nNotes} 条笔记` + (nNotes
    ? ' · 点【查看笔记】展开' : ' · 点【记入笔记】写第一条');
  const deckTitle = nFig
    ? '展开这一页的课件大图（图可点开原尺寸）'
    : '这个知识点对应的页没有课件图缓存';
  const html = `
    <div class="mmkp-shell">
      <aside class="mmkp-side" id="mmkp-tree">
        <div class="mms-empty">左侧导图加载中…</div>
      </aside>
      <div class="mmkp-main">
    <div class="ph">
      <div class="src">
        <div class="loc">${esc(d.name || d.kp_id || '')}
          <span class="badge">${esc(d.kp_id || '')}</span>
          <span class="small muted">末级知识点 · 第 ${esc(d.level || '?')} 层</span></div>
        <div class="hdg">${esc((d.path || []).join(' › ') || d.node_name || '')}</div>
      </div>
      <button class="mmkp-toggle" id="mmkp-treebtn" title="展开 / 收起左侧导图">☰ 导图</button>
      <button class="mmkp-teachbtn" id="mmkp-teachbtn" title="展开 / 收起右侧教材补充">📖 教材</button>
      <button class="pclose" onclick="evPopClose()" title="关闭（Esc）">✕</button>
    </div>
    <div class="pbody" id="mmkp-body">
      <div class="small muted" style="margin-bottom:9px">
        出处：${esc(d.pages_label || '')} · doc#${esc(d.doc_id)}${
          nMat ? ` · 材料 ${nMat} 块（正文 ${d.n_body_blocks || 0} / 识图 ${
            d.n_fig_blocks || 0}）${d.material_chars ? ' / ' + d.material_chars + ' 字' : ''}` : ''}${
          d.insufficient ? ' · <span class="badge warn">材料不足</span>' : ''}
      </div>
      ${d.merged ? `<div class="small" style="margin-bottom:9px">
        <span class="badge">合并 ${(d.pages || []).length} 页</span>
        这一条由 ${esc(d.pages_label || '')} 合并而成：${
          esc(d.merge_reason || '（模型未给出合并理由）')}${
        d.merge_rejected_reason
          ? `<br><span style="color:var(--warn)">管线校验：${esc(d.merge_rejected_reason)}</span>`
          : ''}
      </div>` : ''}
      <div class="lkpsub" style="margin-top:0;border-top:none;padding-top:0">要点${
        pts.length ? ' ' + pts.length + ' 条' : ''}</div>
      ${pts.length
        ? `<ul class="lkppts">${pts.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`
        : `<div class="small muted">${d.insufficient
             ? '模型判定本页材料不足以概括（' + esc(d.insufficient_reason || '') +
               '），按提示词口径不给要点。'
             : '这条没有要点。'}</div>`}
      <details class="kpmore" id="mmkp-more">
        <summary>总结正文 · 出处明细 · 生成链条</summary>
        <div class="lkpsum${d.summary ? '' : ' none'}">${
          esc(d.summary || '（这条没有总结正文）')}</div>
        <div class="lkpsub">出处（页 / chunk_id，点 chunk 看上下文）</div>
        ${mmLkpSrcHtml(d.sources)}
        <div class="lkpsub">这条总结是怎么来的</div>
        <div class="lkpchain">材料 = 该节点覆盖页的正文块 + 识图块（图片理解）${
          (d.figure_pages || []).length
            ? '，其中含图片理解的页：' + d.figure_pages.map(p => 'p.' + p).join('、') : ''}；
          由 ${esc(d.model || 'deepseek-flash')} 提炼${
          d.source_card ? `（产出卡 ${esc(d.source_card)}` +
            (d.prompt_source ? ` · 提示词 ${esc(d.prompt_source)}` : '') + '）' : ''}，
          本条成本 CNY${(d.cost_cny || 0).toFixed(6)}${
          d.generated_at ? ' · ' + esc(d.generated_at) : ''}。</div>
      </details>
      <div class="kpslide" id="mmkp-slide" hidden>
        <div class="small muted">课件页图 · ${
          esc(d.slide_deck || '该课件在 figure_desc 下没有目录')}${
          slides.length ? ` · 本知识点覆盖 ${slides.length} 页，其中 ${nFig} 页有图` : ''}</div>
        <div class="kppages" id="mmkp-pages">${slides.map(s =>
          `<span class="kppage${s.ok ? '' : ' off'}" data-page="${esc(s.page)}"
            title="${s.ok ? '看 p.' + esc(s.page) + ' 的课件图'
                         : 'p.' + esc(s.page) + '：没有课件图缓存'}">p.${esc(s.page)}</span>`
        ).join('')}</div>
        <div id="mmkp-figbox"></div>
      </div>
      <!-- 卡 C-0052：笔记区（默认收起）。渲染阶段不取数——点按钮才请求，
           省掉一次"打开弹窗先等网络"；两个容器互斥展开（见 mmNoteEdit/mmNoteList）。 -->
      <div id="mmkp-noteedit" hidden style="margin-top:10px"></div>
      <div id="mmkp-notelist" hidden style="margin-top:10px"></div>
    </div>
    <div class="pf">
      ${prevBtn}
      ${nextBtn}
      <span class="pmeta">${esc(seqTip)}</span>
      <span class="pmeta" id="mmkp-notecount"
        title="这条知识点绑定的笔记条数（与「查看笔记」列出来的一致）">${
        esc(noteTip)}</span>
      <span style="flex:1"></span>
      <button class="ghost" id="mmkp-note"
        title="给这条知识点写一条笔记（绑定 ${esc(d.kp_id || '')}）">记入笔记</button>
      <button class="ghost" id="mmkp-notes"
        title="看与这条知识点绑定的笔记">查看笔记</button>
      <button class="ghost" id="mmkp-search">在资料库中检索此主题</button>
      <button class="ghost" id="mmkp-deck" title="${esc(deckTitle)}">展开当页课件</button>
    </div>
      </div>
      <aside class="mmkp-teach" id="mmkp-teach">
        <div class="mteach-loading">教材补充加载中…</div>
      </aside>
    </div>`;
  // 特性1：左侧栏占位（宽屏常驻）——失败会收起；窄屏抽屉若正开着，换节点后保持展开。
  const pop0 = evPopEl();
  pop0.classList.add('mmkp-has-side');
  if (MMSIDE.open) pop0.classList.add('mm-tree-open');
  pop0.classList.add('mmkp-has-teach');
  if (MMTEACH.open) pop0.classList.add('mm-teach-open');
  evPopMount(el, html);
  // ★ 卡 C-0042：点「▸ 展开：总结正文 · 出处明细 · 生成链条」也会让内容变高
  //   （同一类"尺寸变了位置没跟着变"的越界），所以折叠体一开一合也重新兜一次。
  const more = $('#mmkp-more');
  if (more) more.addEventListener('toggle', () => evPopPlace(MMPOP.anchor || evPopEl()));
  // 「上一知识点」/「下一知识点」：跳后端给的 prev_id / next_id
  // （首/末条在 HTML 里就是 disabled，这里也不给它挂 onclick）
  const pb = $('#mmkp-prev');
  if (pb && d.prev_id) pb.onclick = () => mmPrevKp();
  const nb = $('#mmkp-next');
  if (nb && d.next_id) nb.onclick = () => mmNextKp();
  // 右下「展开当页课件」：展开/收起课件图区
  const db = $('#mmkp-deck');
  if (db) db.onclick = () => mmToggleDeck();
  // 页号：有图的才可点（没图的页号灰着，点了也不会换错页）
  document.querySelectorAll('#mmkp-pages .kppage').forEach(chip => {
    if (chip.className.indexOf('off') >= 0) return;
    chip.onclick = () => mmShowSlide(MMPOP.kp, parseInt(chip.dataset.page));
  });
  // 原检索能力不丢：只是不再"默认跳走"，要点这个按钮才去检索
  $('#mmkp-search').onclick = () => { const nm = d.name; evPopClose(); doSearch(nm); };
  document.querySelectorAll('.lkpsrc .cid').forEach(b => {
    b.onclick = () => { const c = parseInt(b.dataset.chunk); evPopClose(); openChunk(c); };
  });
  // 卡 C-0052：知识点窗口的笔记。两个按钮都在这一屏里就地展开（不新开浮层：
  // 全页只有一个小窗，见 evPop 的说明），写入的是 notes/ 下的 markdown。
  const nAdd = $('#mmkp-note');
  if (nAdd) nAdd.onclick = () => mmNoteEdit(d);
  const nSee = $('#mmkp-notes');
  if (nSee) nSee.onclick = () => mmNoteList(d);
  // 联动：上一条开着课件，这一条接着开（人对着 PPT 连看是最常见的用法）
  if (keepDeck) mmToggleDeck();
  // 卡 C-0056：挂上"弹窗内怎么滚都不出窗"的两道防线（幂等，见 mmKpScrollGuard）
  mmKpScrollGuard(evPopEl());
  // 特性1：左侧「本课件完整导图」栏（异步；失败/无产物时收起，不影响右侧正文）。
  mmSidebarPaint(d, el);
  const treeBtn = $('#mmkp-treebtn');
  if (treeBtn) treeBtn.onclick = () => {
    MMSIDE.open = !MMSIDE.open;
    evPopEl().classList.toggle('mm-tree-open', MMSIDE.open);
    evPopPlace(MMPOP.anchor || evPopEl());
  };
  // 特性2：右侧「教材补充」栏（异步；无教材/需补做/无匹配都给人话）
  mmTeachPaint(d, el);
  const teachBtn = $('#mmkp-teachbtn');
  if (teachBtn) teachBtn.onclick = () => mmTeachToggle();
}

// ---------------- 特性1：知识点小窗左侧的「本课件完整导图」 ----------------
// 只做加法：左侧数据来自既有 `/api/mindmap?doc_id=`（同一 doc 一次会话只请求一次），
// 点左侧叶子复用 `mmOpenKp` **就地换右侧内容**（不关窗、不新开浮层）；
// 当前知识点按 `d.kp_id` 高亮。取不到 / 没整理产物时左栏给一句人话并自动收起，
// 右侧正文照常显示（绝不影响原有功能）。
const MMSIDE = {cache: {}, open: false};   // {docId: Promise<{ok, tree, title, err}>}

function mmSideData(docId){
  if (!MMSIDE.cache[docId]){
    MMSIDE.cache[docId] = api('/api/mindmap?doc_id=' + encodeURIComponent(docId))
      .then(r => ({ok: !!(r && r.has_structure && (r.tree || []).length),
                   tree: (r && r.tree) || [], title: (r && r.title) || ''}))
      .catch(e => ({ok: false, tree: [], err: e.message}));
  }
  return MMSIDE.cache[docId];
}

function mmSideTree(nodes, curId, docId){
  if (!nodes || !nodes.length) return '';
  return nodes.map(n => {
    const name = esc(n.name || '');
    const pg = n.lkp_pages_label
      ? `<span class="mms-pg">${esc(n.lkp_pages_label)}</span>`
      : (n.page ? `<span class="mms-pg">p.${esc(n.page)}</span>` : '');
    const kids = (n.children && n.children.length)
      ? `<ul class="mms-kids">${mmSideTree(n.children, curId, docId)}</ul>` : '';
    if (n.lkp_ready){
      const ids = (n.lkp_ids && n.lkp_ids.length)
        ? n.lkp_ids : (n.lkp_id ? [n.lkp_id] : []);
      if (ids.length > 1){
        // 一个结构叶节点挂了多条知识点（产物按连续 ≤3 页切开）：就地展开子项，
        // 点哪条看哪条——与右侧列表弹窗同口径，但不用先跳一层。
        const names = n.lkp_names || [];
        const sub = ids.map((id, i) => `<li><a href="#" class="mms-node mms-kp${
          String(id) === String(curId) ? ' on' : ''}" data-kp="${esc(id)}"
          data-name="${esc(names[i] || id)}"
          title="这条知识点的理解与要点">${esc(names[i] || id)}</a></li>`).join('');
        return `<li><div class="mms-node">${name}${pg}</div>
          <ul class="mms-kids">${sub}</ul></li>`;
      }
      const id = ids[0] || '';
      return `<li><a href="#" class="mms-node mms-kp${
        String(id) === String(curId) ? ' on' : ''}" data-kp="${esc(id)}"
        data-name="${esc(n.lkp_name || n.name || id)}"
        title="这条知识点的理解与要点">${name}${pg}</a></li>`;
    }
    if (n.lkp_role === 'leaf'){
      return `<li><div class="mms-node" title="末级节点：该建知识点，但总结待生成">${
        name}${pg}</div></li>`;
    }
    return `<li><div class="mms-node">${name}${pg}</div>${kids}</li>`;
  }).join('');
}

// 点左侧叶子 → 与 mmOpenNode（右侧导图点节点）同一个入口：先标"居中"再 mmOpenKp。
// 不关窗、不换浮层，只换右侧内容；高亮在重渲染后由 mmSidebarPaint 重新 mark。
function mmSidebarBind(aside, docId, curId){
  aside.querySelectorAll('.mms-kp').forEach(a => {
    a.onclick = e => {
      e.preventDefault();
      const kpId = a.dataset.kp;
      if (!kpId || String(kpId) === String(curId)) return;
      evPopEl().dataset.kpPop = '1';
      mmOpenKp(kpId, a.dataset.name || kpId, MMPOP.anchor, false, docId);
    };
  });
}

async function mmSidebarPaint(d, el){
  const aside = $('#mmkp-tree');
  const pop = evPopEl();
  if (!aside) return;
  const docId = parseInt((d && d.doc_id) || MMPOP.docId, 10) || 0;
  if (!docId){
    aside.innerHTML = '<div class="mms-empty">这条知识点没有关联课件，左侧导图不可用。</div>';
    pop.classList.remove('mmkp-has-side', 'mm-tree-open');
    evPopPlace(el || MMPOP.anchor || pop);
    return;
  }
  const curId = (d && d.kp_id) || '';
  const c = await mmSideData(docId);
  // 期间用户又切了别的知识点 / 关掉了小窗 → 这趟结果作废，别盖掉新内容
  if (evPopEl() !== pop || !document.body.contains(aside) || aside !== $('#mmkp-tree')) return;
  if ((pop.dataset.kp || '') !== String(curId)) return;
  if (!c || !c.ok){
    aside.innerHTML = `<div class="mms-empty">这份课件还没有可显示的整理导图${
      c && c.err ? '（' + esc(c.err) + '）' : ''}。右侧内容不受影响。</div>`;
    pop.classList.remove('mmkp-has-side', 'mm-tree-open');
    evPopPlace(el || MMPOP.anchor || pop);
    return;
  }
  pop.classList.add('mmkp-has-side');
  aside.innerHTML = `<div class="mms-ttl" title="${
    esc(c.title || '')}">本课件导图 · 点知识点直达</div>
    <ul class="mms-kids mms-root">${mmSideTree(c.tree, curId, docId)}</ul>`;
  mmSidebarBind(aside, docId, curId);
  evPopPlace(el || MMPOP.anchor || pop);
}

// ---------------- 特性2：知识点小窗右侧「教材补充」栏（task-27） ----------------
// 只做加法：小窗 = 左侧导图树 + 中间原有正文 + 右侧本学科教材的补充页图/段落。
// 数据来自只读接口（零模型）：/api/textbook-supplement/kp（这条 KP 的补充）+
// /api/textbook-supplement/status（有没有教材/要不要补做）。不改原知识点产物。
// ≥1280px 常驻；<1280px 头部「📖 教材」按钮打开覆盖式抽屉。
const MMTEACH = {cache: {}, open: false, req: 0, lastStatus: null};

async function mmTeachPaint(d, el){
  const aside = $('#mmkp-teach');
  if (!aside) return;
  const pop = evPopEl();
  const docId = parseInt((d && d.doc_id) || MMPOP.docId, 10) || 0;
  const kpId = (d && d.kp_id) || '';
  const myReq = ++MMTEACH.req;
  aside.innerHTML = '<div class="mteach-loading">教材补充加载中…</div>';
  if (!docId){
    aside.innerHTML = '<div class="mteach-empty">这条知识点没有关联课件，教材补充不可用。</div>';
    return;
  }
  let kpData = null, st = null;
  try {
    kpData = await api('/api/textbook-supplement/kp?doc_id=' +
                       encodeURIComponent(docId) + '&kp_id=' + encodeURIComponent(kpId));
  } catch(e){
    kpData = {ok: false, available: false,
              note: '读教材补充失败：' + e.message};
  }
  try {
    st = await api('/api/textbook-supplement/status?doc_id=' + encodeURIComponent(docId));
  } catch(e){ st = null; }
  // 期间用户又切了别的知识点/关了窗：这次结果作废
  if (myReq !== MMTEACH.req) return;
  if (evPopEl() !== pop || !document.body.contains(aside) || aside !== $('#mmkp-teach')) return;
  if ((pop.dataset.kp || '') !== String(kpId)) return;
  mmTeachRender(aside, kpData || {}, st || {}, d);
}

function mteachNum(v, dflt){ const n = Number(v); return isFinite(n) ? n : (dflt || 0); }
function mteachCostTxt(v, fallback){
  const n = mteachNum(v, 0);
  return n > 0 ? ('≤¥' + n.toFixed(4)) : (fallback || '按实际计费');
}
function mteachEst(est){
  const e = est || {};
  return {calls: mteachNum(e.calls, 1) || 1,
          cost: mteachNum(e.cost_cny_max, 0),
          model: e.model || '默认模型',
          thinking: !!e.thinking, effort: e.effort || ''};
}
function mteachSourceTxt(src){
  if (src === 'ai') return {cls: 'ai', text: 'AI 定位'};
  if (src === 'local_fallback') return {cls: 'local', text: '本地兜底'};
  return {cls: '', text: src || '本地匹配'};
}
function mteachChapterLine(cm, entry){
  const c = (cm && typeof cm === 'object') ? cm : {};
  const ch = (entry && entry.chapter && typeof entry.chapter === 'object')
    ? entry.chapter : {};
  const no = mteachNum(c.chapter_no || ch.no, 0);
  const nameRaw = String(c.chapter_name || c.name || ch.name || '');
  const nameHasNo = /第\s*\d+\s*章/.test(nameRaw);
  const name = nameHasNo ? nameRaw : nameRaw;
  const page = c.page_label || (entry && entry.page_label) || '';
  if (!no && !name && !page) return '';
  const src = mteachSourceTxt(c.source || (entry && entry.source));
  const cost = mteachNum(c.cost_cny, 0);
  return `<div class="mteach-cm">
    <span>整份课件定位：<b>${no && !nameHasNo ? ('第' + no + '章 ') : ''}${esc(name || '（未定位到章）')}</b>${page ? ' · ' + esc(page) : ''}</span>
    <span class="mteach-cm-src ${src.cls}">${esc(src.text)}</span>
    ${cost > 0 ? `<span class="mteach-cm-src">映射成本 ¥${cost.toFixed(4)}</span>` : ''}
  </div>`;
}
function mteachPagesHtml(entry, tb, st){
  const pages = (entry.pages || []).filter(p => parseInt(p, 10) > 0).slice(0, 3);
  if (!pages.length) return '';
  const tbId = mteachNum(tb.doc_id || ((st && st.supplement) || {}).textbook_doc_id, 0);
  if (!tbId) return '';
  return `<div class="mteach-pages">${pages.map(p => `<a class="mteach-pg"
      href="/api/teach-page?doc_id=${tbId}&page=${p}&dpi=150"
      target="_blank" rel="noopener" title="打开教材 p.${p} 原尺寸">
      <img loading="lazy" src="/api/teach-page?doc_id=${tbId}&page=${p}&dpi=110"
        alt="教材 p.${p}" onerror="this.classList.add('bad')">
      <span>p.${p}</span></a>`).join('')}</div>`;
}
function mteachLocalHtml(entry){
  const passages = (entry.passages || []).filter(Boolean);
  const terms = (entry.added_terms || []).map(t =>
    (typeof t === 'string') ? t : (t.term || t.word || t.name || '')).filter(Boolean);
  if (!passages.length && !terms.length) return '';
  const psHtml = passages.map(p => {
    const txt = String(p.text || '');
    const cut = txt.length > 350 ? '…' : '';
    const hits = (p.hit_terms || []).slice(0, 5);
    const pg = p.page_label || (p.page_start ? ('p.' + p.page_start) : '');
    return `<div class="mteach-ps">
      <div class="mteach-ps-h"><span class="mteach-pgno">${esc(pg)}</span>
        ${p.score ? `<span class="mteach-score">匹配 ${Number(p.score).toFixed(1)}</span>` : ''}</div>
      <div class="mteach-tx">${esc(txt.slice(0, 350))}${cut}</div>
      ${hits.length ? `<div class="mteach-hits">${hits.map(t =>
        `<span>${esc(t)}</span>`).join('')}</div>` : ''}</div>`;
  }).join('');
  const termsHtml = terms.length ? `<div class="mteach-sec">教材补充点</div>
    <div class="mteach-terms">${terms.slice(0, 12).map(t =>
      `<span class="mteach-term">${esc(t)}</span>`).join('')}</div>` : '';
  return `<details class="mteach-local">
    <summary>${passages.length} 段${terms.length ? ' · ' + terms.length + ' 个补充点' : ''}（本地匹配，点开看）</summary>
    ${psHtml}${termsHtml}
    <div class="mteach-foot">按教材章节切块匹配，仅供参考；不改动原知识点</div>
  </details>`;
}
function mteachAiPoints(points){
  return (points || []).filter(Boolean).map(p => {
    if (typeof p === 'string') return `<div class="mteach-point">${esc(p)}</div>`;
    const kind = p.kind || p.type || '';
    const pages = Array.isArray(p.pages) ? p.pages : (p.page ? [p.page] : []);
    return `<div class="mteach-point">${esc(String(p.text || p.content || ''))}
      ${kind ? `<span class="mteach-kind">${esc(kind)}</span>` : ''}
      ${pages.length ? `<span class="mteach-pages-tag">${pages.map(x => 'p.' + x).join('、')}</span>` : ''}
    </div>`;
  }).join('');
}
function mteachAiCard(entry, kpData, st, busy){
  const ai = (entry && entry.ai && typeof entry.ai === 'object') ? entry.ai : null;
  const est = mteachEst((kpData && kpData.estimate) || (st && st.estimate) || {});
  const aiCfg = (st && st.ai && typeof st.ai === 'object') ? st.ai : {};
  const enabled = aiCfg.enabled !== false;
  if (busy){
    return `<div class="mteach-ai"><div class="mteach-ai-h">AI 教材补充
      <span class="mteach-ai-badge warn"><span class="mteach-spin"></span>AI 分析中…</span></div>
      <div class="mteach-ai-est">正在让外接大模型对比课件与教材（约 1 次调用，可能 5–20 秒），请稍候。</div></div>`;
  }
  if (!ai || !ai.status){
    return `<div class="mteach-ai"><div class="mteach-ai-h">AI 教材补充
      <span class="mteach-ai-badge">未生成</span></div>
      <div class="mteach-ai-est">让外接大模型读「教材对应小节」与「本知识点」：
        补课件没讲到的内容、指出课件没讲清的缺口，不改动原知识点。<br>
        预计 <b>${est.calls} 次调用</b> · 成本上界 <b>${mteachCostTxt(est.cost)}</b> · 模型 <b>${esc(est.model)}</b>
        ${enabled ? '' : ' · <span style="color:var(--warn)">AI 未启用（检查设置中心密钥）</span>'}</div>
      <div class="mteach-ai-act"><button class="act" id="mteach-gen"
        ${enabled ? '' : 'disabled'}>生成 AI 补充</button></div></div>`;
  }
  const status = String(ai.status || '');
  if (status === 'failed'){
    return `<div class="mteach-ai"><div class="mteach-ai-h">AI 教材补充
      <span class="mteach-ai-badge bad">生成失败</span></div>
      <div class="mteach-ai-est">${esc(ai.error || ai.summary || 'AI 生成失败（可重试）')}
        <br>本地「教材原文（参考）」仍可在下方查看，不受影响。</div>
      <div class="mteach-ai-act"><button class="act" id="mteach-gen" data-force="1">重试生成</button></div></div>`;
  }
  const badge = status === 'no_supplement'
    ? '<span class="mteach-ai-badge warn">AI：无需补充</span>'
    : '<span class="mteach-ai-badge ok">AI 已生成</span>';
  const noSupLine = status === 'no_supplement'
    ? '<div class="mteach-ai-est">AI 判断教材对应小节没有需要补充的内容。</div>'
    : '';
  const points = mteachAiPoints(ai.points);
  const gaps = (ai.gaps || []).filter(Boolean);
  const cites = (ai.citations || []).filter(Boolean);
  const citesHtml = cites.length ? `<details class="mteach-cites">
    <summary>教材出处（${cites.length} 条，点开看原文）</summary>
    ${cites.map(c => {
      if (typeof c === 'string') return `<div class="mteach-cite">${esc(c)}</div>`;
      const pg = c.page_label || (c.page ? ('p.' + c.page) : '');
      return `<div class="mteach-cite">${pg ? `<b>${esc(pg)}</b> · ` : ''}${esc(String(c.text || c.quote || c.chunk_text || ''))}</div>`;
    }).join('')}</details>` : '';
  const foot = [ai.model ? ('模型 ' + ai.model) : '',
                ai.cost_cny != null ? ('费用 ¥' + mteachNum(ai.cost_cny).toFixed(4)) : '',
                ai.elapsed_s != null ? ('耗时 ' + mteachNum(ai.elapsed_s).toFixed(1) + 's') : ''
               ].filter(Boolean).join(' · ');
  return `<div class="mteach-ai"><div class="mteach-ai-h">AI 教材补充 ${badge}</div>
    ${noSupLine}
    ${ai.verdict ? `<div class="mteach-verdict">${pvText(ai.verdict)}</div>` : ''}
    ${ai.summary ? `<div class="mteach-sum">${pvText(ai.summary)}</div>` : ''}
    ${points ? `<div class="mteach-points">${points}</div>` : ''}
    ${gaps.length ? `<div class="mteach-gaps"><b>课件没讲 / 没讲清</b>${gaps.map(g => '· ' + esc(String(g))).join('<br>')}</div>` : ''}
    ${citesHtml}
    ${foot ? `<div class="mteach-ai-foot">${esc(foot)}</div>` : ''}
    <div class="mteach-ai-act">
      <button class="ghost" id="mteach-gen" data-force="1">重新生成</button>
    </div></div>`;
}

function mmTeachRender(aside, kpData, st, d){
  MMTEACH.lastStatus = st || {};
  MMTEACH.ctx = {aside, kpData, st, d};
  const tb = (kpData && kpData.textbook) || {};
  const entry = (kpData && kpData.entry) || {};
  const books = (st && st.textbooks) || [];
  const hasTb = !!(st && st.has_textbook) || books.length > 0;
  const legacy = !!(st && st.legacy);
  const sup = (st && st.supplement) || {};
  const available = !!((kpData && kpData.available) && ((entry.passages || []).length || entry.ai));
  const head = `<div class="mteach-h"><b>📖 教材补充</b>
    <button class="mteach-x" title="收起教材补充" onclick="mmTeachClose()">✕</button></div>`;
  let body = '';
  if (available || (legacy && kpData && kpData.available)){
    const bookTitle = tb.title || ((books[0] || {}).title) || '教材';
    const cmLine = mteachChapterLine((st && st.chapter_map) || entry.chapter || {},
                                     entry);
    body = head
      + `<div class="mteach-sub">📚 ${esc(bookTitle)}</div>`
      + cmLine
      + mteachPagesHtml(entry, tb, st);
    if (legacy){
      body += `<div class="mteach-legacy">⚠ 旧版教材补充（纯本地匹配，无 AI 章节映射）
        <span class="small">建议先重新 AI 定位整份课件的教材章节与小节，再对单条知识点生成 AI 补充；不改动原知识点。</span>
        <div class="mteach-ai-act"><button class="act" id="mteach-rebuild">重新 AI 定位并补做</button></div>
      </div>`;
    } else {
      body += mteachAiCard(entry, kpData, st, !!MMTEACH.busy);
    }
    body += mteachLocalHtml(entry);
  } else if (!hasTb){
    body = head + `<div class="mteach-empty">本学科尚未入库教材，教材补充暂不可用。
      <br><span class="small">把教材 PDF 放进 raw 并入库后，整理知识点时会自动补做。</span></div>`;
  } else if (st && st.needs_backfill){
    body = head + `<div class="mteach-empty">教材已入库，可到导图栏补做教材补充。
      <br><span class="small">教材《${esc(((books[0] || {}).title) || '')}》已入库；
      到「课件思维导图」栏点「补做教材补充」即可（不改动原知识点）。</span></div>
      <div class="mteach-act"><button class="act"
        onclick="mmTeachGotoBackfill(${parseInt(d.doc_id || 0, 10) || 0})">去导图栏补做</button></div>`;
  } else {
    const note = (kpData && kpData.note)
      || (st && st.reason)
      || '这条知识点在教材里没有匹配到可补充的段落。';
    body = head + `<div class="mteach-empty">${em(humanize(note))}</div>`;
  }
  aside.innerHTML = body;
  const gen = $('#mteach-gen');
  if (gen) gen.onclick = () => mmTeachGenerate(gen.getAttribute('data-force') === '1');
  const rebuild = $('#mteach-rebuild');
  if (rebuild) rebuild.onclick = () => mmTeachLegacyRebuild();
}

async function mmTeachGenerate(force){
  if (MMTEACH.busy) return;
  const ctx = MMTEACH.ctx || {};
  const d = ctx.d || {};
  const docId = parseInt(d.doc_id || MMPOP.docId, 10) || 0;
  const kpId = d.kp_id || '';
  if (!docId || !kpId) return;
  const est = mteachEst((ctx.kpData && ctx.kpData.estimate) || (ctx.st && ctx.st.estimate) || {});
  const ok = await confirmBox(force ? '重新生成 AI 教材补充？' : '生成 AI 教材补充？',
    `预计 ${est.calls} 次调用｜成本上界 ${mteachCostTxt(est.cost)}｜模型 ${est.model}\n\n`
    + 'AI 只依据教材原文补课件没讲到的内容、指出没讲清的缺口；不改动原知识点。\n'
    + '确认后才调用外接大模型。');
  if (!ok) return;
  MMTEACH.busy = true;
  ctx.aside && mmTeachRender(ctx.aside, ctx.kpData, ctx.st, d);
  try {
    const r = await api('/api/textbook-supplement/kp/build', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({doc_id: docId, kp_id: kpId, force: !!force})});
    if (!r || r.ok === false) throw new Error((r && r.error) || '生成失败');
    toast('AI 教材补充已生成');
    await mmTeachRefresh();
  } catch(e){
    toast('AI 教材补充失败：' + e.message);
  } finally {
    MMTEACH.busy = false;
    if (MMTEACH.ctx) mmTeachRender(MMTEACH.ctx.aside, MMTEACH.ctx.kpData,
                                   MMTEACH.ctx.st, MMTEACH.ctx.d);
  }
}

async function mmTeachLegacyRebuild(){
  if (MMTEACH.busy) return;
  const ctx = MMTEACH.ctx || {};
  const d = ctx.d || {};
  const docId = parseInt(d.doc_id || MMPOP.docId, 10) || 0;
  if (!docId) return;
  const est = mteachEst((ctx.st && ctx.st.estimate) || (ctx.kpData && ctx.kpData.estimate) || {});
  const ok = await confirmBox('重新 AI 定位教材补充？',
    `旧版产物只有本地按章匹配，没有 AI 章节映射。\n`
    + `这一步会先让 AI 为整份课件定位教材章节（约 ${est.calls} 次调用｜成本上界 ${mteachCostTxt(est.cost)}｜模型 ${est.model}），`
    + '再由本地定位小节/页；不改动原知识点。\n确认后才调用外接大模型。');
  if (!ok) return;
  MMTEACH.busy = true;
  ctx.aside && mmTeachRender(ctx.aside, ctx.kpData, ctx.st, d);
  try {
    const r = await api('/api/textbook-supplement/build', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({doc_id: docId, force: true})});
    if (!r || r.ok === false) throw new Error((r && r.error) || '补做失败');
    toast('AI 定位完成，已刷新教材补充');
    await mmTeachRefresh();
  } catch(e){
    toast('AI 定位失败：' + e.message);
  } finally {
    MMTEACH.busy = false;
    if (MMTEACH.ctx) mmTeachRender(MMTEACH.ctx.aside, MMTEACH.ctx.kpData,
                                   MMTEACH.ctx.st, MMTEACH.ctx.d);
  }
}

async function mmTeachRefresh(){
  const ctx = MMTEACH.ctx;
  if (!ctx) return;
  await mmTeachPaint(ctx.d, MMPOP.anchor);
}

function mmTeachToggle(){
  MMTEACH.open = !MMTEACH.open;
  evPopEl().classList.toggle('mm-teach-open', MMTEACH.open);
  evPopPlace(MMPOP.anchor || evPopEl());
}

function mmTeachClose(){
  MMTEACH.open = false;
  evPopEl().classList.remove('mm-teach-open');
  evPopPlace(MMPOP.anchor || evPopEl());
}

function mmTeachGotoBackfill(docId){
  evPopClose();
  go('kps');
  setTimeout(() => {
    const el = $('#mm-tree');
    if (el) el.scrollIntoView({ block: 'center' });
  }, 450);
  toast('在「课件思维导图」栏点「补做教材补充」（不改动原知识点）');
}

// ---------------- 特性3：课件思维导图栏「AI 定位章节 + 补做」提醒（task-30） ----------------
// 触发条件由后端 supplement_status 判：有 KP 产物 + 有教材 + 没补做/旧版/教材后入库。
// 只在 #mm-tree 顶部出现；不改其它页面、不改原知识点。
const MMBACK = {busy: false};

function mteachBackfillEst(st){
  return mteachEst((st && st.estimate)
    || ((st && st.ai) && st.ai.estimate) || {});
}
function mteachReadyLine(st){
  const cm = (st && st.chapter_map) || {};
  const sup = (st && st.supplement) || {};
  const secs = (cm.n_sections != null) ? mteachNum(cm.n_sections, 0) : 0;
  const no = mteachNum(cm.chapter_no, 0);
  const name = cm.chapter_name || '';
  const nameHasNo = /第\s*\d+\s*章/.test(name);
  const src = mteachSourceTxt(cm.source);
  const cost = mteachNum(cm.cost_cny || sup.cost_cny, 0);
  const parts = [];
  if (no || name) parts.push(`章节：${no && !nameHasNo ? ('第' + no + '章 ') : ''}${name}`);
  if (cm.page_label) parts.push(cm.page_label);
  parts.push(`本地小节：${secs} 个`);
  parts.push('可逐条生成 AI 补充');
  return `教材补充已就绪 · ${parts.join(' · ')}` +
    (cm.source ? `（${src.text}${cost > 0 ? ' · 映射成本 ¥' + cost.toFixed(4) : ''}）` : '');
}

async function mmTeachBackfill(docId){
  const box = $('#mm-tree');
  if (!box) return;
  const did = parseInt(docId || MINDMAP.docId || 0, 10) || 0;
  if (!did) return;
  const old = box.querySelector('.mmteach-banner, .mmteach-ready');
  if (old) old.remove();
  let st;
  try { st = await api('/api/textbook-supplement/status?doc_id=' + encodeURIComponent(did)); }
  catch(e){ return; }
  if (parseInt(MINDMAP.docId || 0, 10) !== did) return;   // 期间换了课件：作废
  if (!st || st.ok === false || !st.has_artifact || !st.has_textbook) return;
  const books = st.textbooks || [];
  const first = books[0] || {};
  const line = document.createElement('div');
  const legacy = !!st.legacy;
  if (st.needs_backfill || legacy){
    const est = mteachBackfillEst(st);
    const costTxt = mteachCostTxt(est.cost, '约 1 次小调用');
    const reason = humanize(st.reason || '');
    line.className = 'mmteach-banner';
    line.innerHTML = `<div class="mmteach-banner-t">${legacy
      ? `检测到<b>旧版</b>教材补充（纯本地匹配，无 AI 章节映射）：建议重新 AI 定位整份课件的章节与小节，再逐条生成 AI 补充（${esc(costTxt)}，<b>不改动原知识点</b>）。`
      : `检测到教材《${esc(first.title || '')}》已入库，可先 AI 定位本章并补做教材补充（${esc(costTxt)}，<b>不改动原知识点</b>）。`}
      ${reason ? `<span class="small">${esc(reason)}</span>` : ''}</div>
      <button class="act" id="mmteach-build">${legacy ? '重新 AI 定位并补做' : 'AI 定位章节并补做'}</button>`;
    box.insertBefore(line, box.firstChild);
    const btn = $('#mmteach-build');
    if (btn) btn.onclick = () => mmTeachBuild(did, btn);
  } else if ((st.supplement || {}).exists){
    line.className = 'mmteach-ready';
    line.textContent = mteachReadyLine(st);
    box.insertBefore(line, box.firstChild);
  }
}

async function mmTeachBuild(docId, btn){
  if (MMBACK.busy) return;
  let st = null;
  try { st = await api('/api/textbook-supplement/status?doc_id=' + encodeURIComponent(docId)); }
  catch(e){ st = null; }
  const est = mteachBackfillEst(st || {});
  const legacy = !!(st && st.legacy);
  const ok = await confirmBox(legacy ? '重新 AI 定位教材补充？' : 'AI 定位章节并补做教材补充？',
    `会先让 AI 为整份课件定位教材章节（约 ${est.calls} 次调用｜成本上界 ${mteachCostTxt(est.cost)}｜模型 ${est.model}），`
    + '再由本地定位小节/页；不改动原知识点。\n同步执行约 5–15 秒，确认后才调用外接大模型。');
  if (!ok) return;
  MMBACK.busy = true;
  const oldText = btn ? btn.textContent : '';
  if (btn){ btn.disabled = true; btn.textContent = 'AI 定位中…'; }
  try {
    const r = await api('/api/textbook-supplement/build', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ doc_id: docId, force: legacy }),
    });
    if (!r || r.ok === false) throw new Error((r && r.error) || '补做失败');
    toast('AI 定位完成：教材补充已更新');
    MMTEACH.cache = {};
    await mmTeachBackfill(docId);          // 重新取状态：横幅变"已就绪"
  } catch(e){
    toast('补做教材补充失败：' + e.message);
    if (btn){ btn.disabled = false; btn.textContent = oldText || '补做教材补充'; }
  } finally {
    MMBACK.busy = false;
  }
}
// ---------------- 知识点窗口的笔记（卡 C-0052） ----------------
// 人拍板：「在点击思维导图的知识点跳出的知识点窗口增加一个『记入笔记』的按钮……
// 该笔记与知识点窗口绑定。同时增加一个『查看笔记』的按钮……笔记页签也会出现
// 相应的笔记条目。」
//
// 存储**复用既有的笔记机制**（notes/ 下的 markdown，见 core/study.py::save_note）：
// 绑定写在 front-matter 的 kp_id/doc_id 里，不新建库表——正文与来源分开存必然
// 不同步，而「笔记」页签与 MCP 的 kb_note_* 读的正是这批文件。
// 这里只做三件事：展开输入框、写一条、按 kp_id 反查列出。
//
// 尺寸变化必须重新定位：弹窗是 position:fixed，展开/收起笔记区会改变高度
// （同「展开当页课件」的道理，见 evPopPlace）。
function mmNotePlace(){
  evPopPlace(MMPOP.anchor || evPopEl());
}

// 「记入笔记」：再点一次＝收起（按钮是开关，不是"又开一个框"）
function mmNoteEdit(d){
  const box = $('#mmkp-noteedit'); if (!box) return;
  const list = $('#mmkp-notelist');
  if (list){ list.hidden = true; list.innerHTML = ''; }
  if (!box.hidden){ box.hidden = true; box.innerHTML = ''; mmNotePlace(); return; }
  box.hidden = false;
  box.innerHTML = `
    <div class="lkpsub" style="margin-top:0">记入笔记 · 绑定知识点 ${
      esc(d.kp_id || '')}${d.name ? '（' + esc(d.name) + '）' : ''}</div>
    <textarea id="mmkp-notebody" style="width:100%;min-height:84px;resize:vertical"
      placeholder="写这条知识点的笔记…保存后落在 notes/ 下，并出现在「笔记」页签（条目里会写明它绑定的是这条知识点）"></textarea>
    <div class="row" style="margin-top:8px">
      <button class="act" id="mmkp-notesave">保存笔记</button>
      <button class="ghost" id="mmkp-notecancel">取消</button>
      <span class="small muted" id="mmkp-notemsg"></span>
    </div>`;
  const ta = $('#mmkp-notebody');
  if (ta) ta.focus();
  const cancel = $('#mmkp-notecancel');
  if (cancel) cancel.onclick = () => { box.hidden = true; box.innerHTML = ''; mmNotePlace(); };
  const save = $('#mmkp-notesave');
  if (save) save.onclick = () => mmNoteSave(d);
  mmNotePlace();
}

async function mmNoteSave(d){
  const ta = $('#mmkp-notebody');
  const msg = $('#mmkp-notemsg');
  const btn = $('#mmkp-notesave');
  const body = ta ? ta.value.trim() : '';
  if (!body){ if (msg) msg.textContent = '先写点什么再保存。'; return; }
  if (btn){ btn.disabled = true; btn.textContent = '保存中…'; }
  try {
    const r = await api('/api/note/kp', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({kp_id: d.kp_id, kp_name: d.name || d.kp_id,
        doc_id: d.doc_id, body,
        refs: [((d.pages_label || '') + ' · doc#' + d.doc_id).replace(/^ · /, '')]})});
    if (r.error) throw new Error(r.error);
    if (msg) msg.textContent = '已记入：' + r.file;
    if (ta) ta.value = '';
    toast('笔记已绑定知识点 ' + (d.kp_id || '') + '：' + r.file);
    // 卡 C-0056：刚写下的这条一定绑定当前知识点 → 底部「已绑定 N 条笔记」就地 +1，
    // 不用为一个数字再跑一趟接口（重新打开弹窗时以后端计数为准，两处同源，
    // 见 api_summary_kp 的 note_count）。
    const nc = $('#mmkp-notecount');
    const mn = nc ? /已绑定 (\d+) 条笔记/.exec(nc.textContent || '') : null;
    if (nc && mn) {
      nc.textContent = `已绑定 ${parseInt(mn[1], 10) + 1} 条笔记 · 点【查看笔记】展开`;
    }
  } catch(e){
    if (msg) msg.textContent = '保存失败：' + e.message;
  } finally {
    if (btn){ btn.disabled = false; btn.textContent = '保存笔记'; }
  }
}

// 「查看笔记」：列出**绑定这条知识点**的笔记（正文一并带回——小窗里点开一条
// 笔记不该再跳一次网络；超长的按 MAX_NOTE_INLINE 截断，全文去「笔记」页签看）
async function mmNoteList(d){
  const box = $('#mmkp-notelist'); if (!box) return;
  const edit = $('#mmkp-noteedit');
  if (edit){ edit.hidden = true; edit.innerHTML = ''; }
  if (!box.hidden){ box.hidden = true; box.innerHTML = ''; mmNotePlace(); return; }
  box.hidden = false;
  box.innerHTML = '<div class="small muted">读取笔记…</div>';
  mmNotePlace();
  let r;
  try {
    r = await api('/api/notes/by_kp?kp_id=' + encodeURIComponent(d.kp_id || '') +
                  '&doc_id=' + (d.doc_id || 0));
  } catch(e){
    box.innerHTML = `<div class="small muted">读取失败：${esc(e.message)}</div>`;
    mmNotePlace(); return;
  }
  const notes = (r && r.notes) || [];
  if (!notes.length){
    box.innerHTML = `<div class="lkpsub" style="margin-top:0">这条知识点的笔记</div>
      <div class="small muted">还没有笔记。点【记入笔记】写第一条——它会绑定到 ${
        esc(d.kp_id || '')}，并出现在「笔记」页签。</div>`;
    mmNotePlace(); return;
  }
  box.innerHTML = `<div class="lkpsub" style="margin-top:0">这条知识点的笔记 · ${
      notes.length} 条</div>` + notes.map((n, i) => `
    <div class="hit" style="margin-bottom:8px">
      <div class="cite">${esc(n.title || n.file)}</div>
      <div class="why">${fmtTime(n.created || n.mtime)} · ${esc(n.file)}${
        (n.body_truncated ?? n.truncated)
          ? ' · 正文较长，这里只显示开头，全文去「笔记」页签看' : ''}</div>
      <pre style="max-height:30vh;overflow:auto">${esc(n.body ?? n.content ?? '')}</pre>
      <div class="row" style="margin-top:6px">
        <button class="ghost" data-mmkpedit="${i}"
          title="改这篇笔记的正文（front-matter 与「## 出处」段原样保留）">编辑正文</button>
      </div>
    </div>`).join('');
  // 卡 C-0082：「编辑正文」进编辑态。绑定用**下标**而不是文件名——
  // 文件名里可能有引号/中文，塞进 HTML 属性里反而容易出错。
  box.querySelectorAll('button[data-mmkpedit]').forEach(b => {
    b.onclick = () => mmNoteEditOpen(d, notes[parseInt(b.dataset.mmkpedit, 10)]);
  });
  mmNotePlace();
}

// 卡 C-0082：在知识点窗口里**直接改**一条笔记的正文。
// ⚠ 进编辑态前必须再取一次全文（`GET /api/note`）：上面列表里的 `body` 是按
// `MAX_NOTE_INLINE` 截断过的，拿截断的文本回写会把正文尾部**永久砍掉**。
async function mmNoteEditOpen(d, n){
  const box = $('#mmkp-noteedit'); if (!box || !n) return;
  const list = $('#mmkp-notelist');
  // 两个容器互斥（同 mmNoteEdit / mmNoteList）：开编辑就收起列表，
  // 否则两块内容叠在一起、弹窗高度也算不准。
  if (list){ list.hidden = true; list.innerHTML = ''; }
  box.hidden = false;
  box.innerHTML = '<div class="small muted">读取笔记全文…</div>';
  mmNotePlace();
  let r;
  try {
    r = await api('/api/note?name=' + encodeURIComponent(n.file || ''));
  } catch(e){
    box.innerHTML = `<div class="small muted">读取失败：${esc(e.message)}</div>`;
    mmNotePlace(); return;
  }
  if (r.error){
    box.innerHTML = `<div class="small muted">读取失败：${esc(r.error)}</div>`;
    mmNotePlace(); return;
  }
  // 优先用 body（剥过壳）；后端万一没给就回落 content（宁可多显示几行机器壳，
  // 也不能让人在"看不到内容"的框里把正文覆盖成空）
  const full = (r.body ?? r.content ?? '');
  const mt = ((r.note || {}).mtime ?? n.mtime ?? 0);
  box.innerHTML = `
    <div class="lkpsub" style="margin-top:0">修改笔记正文 · ${esc(n.file || '')}</div>
    <div class="small muted" style="margin-bottom:6px">只改正文：front-matter
      （绑定知识点 / 出处 / 记录时间）与末尾的「## 出处」段原样保留，改完仍能从
      这里跳回知识点。</div>
    <textarea id="mmkp-notebody" style="width:100%;min-height:160px;resize:vertical"
      placeholder="改这篇笔记的正文…">${esc(full)}</textarea>
    <div class="row" style="margin-top:8px">
      <button class="act" id="mmkp-notesave">保存修改</button>
      <button class="ghost" id="mmkp-notecancel">取消</button>
      <span class="small muted" id="mmkp-notemsg"></span>
    </div>`;
  const ta = $('#mmkp-notebody');
  if (ta) ta.focus();
  const cancel = $('#mmkp-notecancel');
  if (cancel) cancel.onclick = () => {
    box.hidden = true; box.innerHTML = ''; mmNotePlace();
  };
  const save = $('#mmkp-notesave');
  if (save) save.onclick = () => mmNoteUpdate(d, n, mt);
  mmNotePlace();
}

async function mmNoteUpdate(d, n, mtime){
  const box = $('#mmkp-noteedit');
  const ta = $('#mmkp-notebody');
  const msg = $('#mmkp-notemsg');
  const btn = $('#mmkp-notesave');
  const body = ta ? ta.value : '';
  if (!body.trim()){ if (msg) msg.textContent = '正文不能是空的。'; return; }
  if (btn){ btn.disabled = true; btn.textContent = '保存中…'; }
  try {
    const r = await api('/api/note/update', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({file: n.file, body, mtime})});
    if (r.error) throw new Error(r.error);
    if (box){ box.hidden = true; box.innerHTML = ''; }
    toast('笔记已更新：' + (r.file || n.file));
    // 卡 C-0056 的写法：底部「已绑定 N 条笔记」就地同上限同步（编辑不改条数，
    // 但文案要跟着重算一遍，别让两处口径分家）
    const nc = $('#mmkp-notecount');
    const mn = nc ? /已绑定 (\d+) 条笔记/.exec(nc.textContent || '') : null;
    if (nc && mn) {
      nc.textContent = `已绑定 ${parseInt(mn[1], 10)} 条笔记 · 点【查看笔记】展开`;
    }
    await mmNoteReload(d);
  } catch(e){
    // 409（文件在别处被改过）也走到这里：原话就是后端写的那句"刚在别处被改过"，
    // 不吞掉、不换个说法
    if (msg) msg.textContent = '保存失败：' + e.message;
  } finally {
    if (btn){ btn.disabled = false; btn.textContent = '保存修改'; }
  }
}

// 保存后回到列表态并把刚改的内容读回来。`mmNoteList` 是"再点一次收起"的开关，
// 直接再调一次会把列表关掉，所以先归位成"收起"再让它展开（顺带清掉编辑态）。
async function mmNoteReload(d){
  const box = $('#mmkp-notelist');
  if (box){ box.hidden = true; box.innerHTML = ''; }
  await mmNoteList(d);
}

// 「下一知识点」（卡 C-0036）：跳导图先序里的下一个。
// next_id 是**后端算好**的（/api/summary-kp），前端一个字都不重排。
// 已展开课件时把展开状态带过去：换个知识点就让人再点一次「展开当页课件」，
// 等于把"连看几张"这件事打断。
async function mmNextKp(){
  const d = MMPOP.kp;
  if (!d || !d.next_id) return;
  const keep = (evPopEl().dataset.deck || '') === '1';
  // 卡 C-0055：这一链是**同一份课件**内的顺序，docId 跟着当前弹窗走
  // （从问答页的出处小窗跳过来的那一条也一样，见 mmOpenKp 的 docId）。
  await mmOpenKp(d.next_id, d.next_name || '', MMPOP.anchor, keep, MMPOP.docId);
}

// 「上一知识点」（卡 C-0056）：与 mmNextKp **完全对称**——同一条先序链上的
// prev，顺序同样由后端算好（`/api/summary-kp` 的 `prev_id`/`prev_name`，
// 见 `_lkp_seq`），前端一个字都不重排。已展开课件时照样把展开状态带过去：
// 人是在"对着 PPT 连着看几张"，往返一次就把这个动作打断了。
async function mmPrevKp(){
  const d = MMPOP.kp;
  if (!d || !d.prev_id) return;
  const keep = (evPopEl().dataset.deck || '') === '1';
  await mmOpenKp(d.prev_id, d.prev_name || '', MMPOP.anchor, keep, MMPOP.docId);
}

// ---------------- 弹窗内怎么滚都不出窗（卡 C-0056） ----------------
// 人报（2026-09-29 原话）：「知识点窗口的最右侧滑动条滑动到最顶部后，如果继续
// 滑动，知识点窗口会消失，并且整个网页最右边的滑动条会滑动。这个问题需要解决。
// 我希望打开知识点窗口后在知识点窗口内不论怎么滑动，知识点窗口都不会消失。」
//
// 病根是**滚动链**（scroll chaining），两个症状是同一个病根的先后两步：
//   ① `.pbody` 滚到边界后，浏览器把这次滚动剩下的量交给祖先 → 外层文档跟着滚
//      （人看到的"整个网页最右边的滑动条会滑动"）；
//   ② 外层这一滚，window 上那个**捕获阶段**的 scroll 监听（evPopScroll）就收到一个
//      target 不在弹窗里的事件，于是按"滚动即关窗"的老规矩把小窗关掉
//      （人看到的"知识点窗口会消失"）。
// 所以治法是"到边界就别再往上滚"，不是"别关窗"——关窗机制本身是各处共用的。
//
// 两道防线，都只挂在知识点弹窗这一侧（不碰 EVPOP 的关闭/定位机制）：
//   ① `overscroll-behavior:contain` 加在正文滚动区上：从根上断掉滚动链，
//      连触摸惯性一并管住，而且**不动原生滚动的手感**（惯性、平滑都在）。
//      它管的是 JS 判断不到的那一类：一次滚轮量跨过边界的"半程事件"
//      （离底还有 30px 时滚 100px，剩下 70px 原本会链给外层）。
//   ② 显式拦 wheel / touchmove：**只在当前方向上已经滚到头**时 preventDefault，
//      这是"到顶/到底后继续滚"的正对治法。还有余量时一个字都不拦——
//      弹窗里的正文、课件图、笔记照常滚。
//      ⚠ 监听器必须显式 `{passive:false}`：passive 的监听器里 preventDefault
//      会被浏览器直接忽略，那样这道防线看着在、其实一点用没有。
//
// 只对**知识点弹窗**生效（`dataset.kp` 非空，见 mmOpenKp）：问答页的出处小窗
// （卡 C-0055）保持原样"滚到外面就关"，行为一个字节都没改。
function mmKpScrollHost(node, pop, dy){
  // 从事件目标往上找最近的、**在这个方向上还有余量**的滚动容器；没有就返回 null
  for (let el = node; el && el !== pop; el = el.parentElement){
    if (el.nodeType !== 1) break;
    const oy = String(window.getComputedStyle(el).overflowY || '');
    if ((oy === 'auto' || oy === 'scroll' || oy === 'overlay') &&
        el.scrollHeight > el.clientHeight + 1){
      const max = el.scrollHeight - el.clientHeight;
      // 1px 容差：缩放 / 高分屏下 scrollTop 是小数，"到底没到底"要判得稳
      if (dy < 0 ? el.scrollTop > 1 : el.scrollTop < max - 1) return el;
    }
  }
  return null;
}

function mmKpWheel(e){
  const pop = evPopEl();
  if (!pop.dataset.kp || !pop.contains(e.target)) return;  // 不是知识点弹窗：不插手
  const dy = e.deltaY || 0;
  if (!dy) return;                       // 横向或无位移：原样放行
  if (mmKpScrollHost(e.target, pop, dy)) return;   // 还没到头：交给浏览器原生滚
  if (e.cancelable) e.preventDefault();            // 已经到头：拦住，别往祖先滚
}

function mmKpTouchStart(e){
  // 手指位置要自己记：touchmove 只给当前位置，方向得跟起点比
  MMPOP.touchY = (e.touches && e.touches[0]) ? e.touches[0].clientY : 0;
}

function mmKpTouchMove(e){
  const pop = evPopEl();
  if (!pop.dataset.kp || !pop.contains(e.target)) return;
  const t = e.touches && e.touches[0];
  if (!t) return;
  const dy = (MMPOP.touchY || 0) - t.clientY;   // 手指上滑 = 内容往上走
  MMPOP.touchY = t.clientY;
  if (!dy) return;
  if (mmKpScrollHost(e.target, pop, dy)) return;   // 还有余量：让它原生滚
  if (e.cancelable) e.preventDefault();            // 到头了：拦住
}

// 幂等：弹窗元素是全页复用的一个单例（见 evPopEl），挂一次就够。
// 正文区每次开窗都会重建，所以 overscroll-behavior 每次都要重设。
function mmKpScrollGuard(pop){
  const body = pop.querySelector('.pbody');
  if (body) body.style.overscrollBehavior = 'contain';
  if (pop.dataset.mmKpGuard === '1') return;
  pop.dataset.mmKpGuard = '1';
  pop.addEventListener('wheel', mmKpWheel, {passive: false});
  pop.addEventListener('touchstart', mmKpTouchStart, {passive: true});
  pop.addEventListener('touchmove', mmKpTouchMove, {passive: false});
}

// 课件图区：选页 + 放图。找不到图就**明说**（哪一页、哪个目录没有缓存），
// 绝不给别的页的图充数——张冠李戴比没图更坏。
function mmShowSlide(d, page){
  const box = $('#mmkp-figbox'); if (!box || !d) return;
  const has = (d.slides || []).filter(s => s.ok);
  if (!has.length){
    box.innerHTML = `<div class="small muted">该页没有课件图缓存${
      d.slide_deck
        ? '（这张页图还没有缓存）'
        : '（这份课件还没有缓存页图）'}。其他内容照常可看。</div>`;
    return;
  }
  const pick = has.some(s => s.page === page) ? page : has[0].page;
  MMPOP.page = pick;
  const url = '/img/slide?doc_id=' + encodeURIComponent(d.doc_id) + '&page=' + pick;
  box.innerHTML = `<img class="kpslideimg" src="${url}" alt="p.${esc(pick)} 课件页图"
      title="点图在新窗口打开原尺寸"><div class="small muted" style="margin-top:6px">
      p.${esc(pick)} · 点图可看原尺寸</div>`;
  const img = box.querySelector('.kpslideimg');
  if (img){
    img.onclick = () => window.open(url, '_blank');
    // ★ 卡 C-0042：图是异步加载的——写进 HTML 的这一刻它还没有内在尺寸，
    //   弹窗高度要等图解码完才涨到 `.kpslideimg{width:100%;max-height:52vh}`
    //   那么大。涨高之后必须**重新定位**（居中 + 兜边界），否则底部那一行
    //   会被顶出视口，「下一知识点」就点不到了——人报的"展开原图时有几率
    //   整个窗口有部分跑到屏幕外"就发生在这一步之间。
    const reflow = () => evPopPlace(MMPOP.anchor || evPopEl());
    img.onload = reflow;
    img.onerror = reflow;
    // 已经在缓存里时 load 不会再触发，补量一次（complete 对有 src 的 img 可靠）
    if (img.complete) reflow();
  }
  document.querySelectorAll('#mmkp-pages .kppage').forEach(b => {
    b.classList.toggle('on', parseInt(b.dataset.page) === pick);
  });
}

// 右下「展开当页课件」：展开/收起课件图区。
// 展开时给弹窗加 .deck-open（更宽更高）并**重新算一次位置**——弹窗是
// position:fixed，尺寸一变就得重新兜回视口内（见 evPopPlace）。
function mmToggleDeck(){
  const box = $('#mmkp-slide'); if (!box) return;
  const pop = evPopEl();
  const open = !!box.hidden;            // 现在藏着 → 这次是"展开"
  box.hidden = !open;
  pop.dataset.deck = open ? '1' : '';
  pop.classList.toggle('deck-open', open);
  const btn = $('#mmkp-deck');
  if (btn) btn.textContent = open ? '收起课件图' : '展开当页课件';
  if (open) mmShowSlide(MMPOP.kp, MMPOP.page || 0);
  evPopPlace(MMPOP.anchor || pop);
}

// 末级节点但总结还没到位（数据文件缺失、正在生成、或接口降级）。
// 这一屏必须与"本页按当前口径不建知识点"区分开：这个节点**是该有知识点的**，
// 缺的只是那份总结——所以说"待生成"，并告诉他数据文件在哪。
function mmOpenPending(n, el){
  evPopMount(el, `
    <div class="ph">
      <div class="src">
        <div class="loc">末级节点「${esc(n.name || '')}」
          <span class="badge warn">待生成</span></div>
        <div class="hdg">这是末级节点（该建知识点），但总结数据还没读到。</div>
      </div>
      <button class="pclose" onclick="evPopClose()" title="关闭（Esc）">✕</button>
    </div>
    <div class="pbody">
      <div class="small muted">导图照常出图，只是这条知识点还没有总结：
        数据文件 <code>logs/knowledge/&lt;doc_id&gt;-末级知识点总结.json</code>
        不存在或尚未生成。生成后刷新本页即可点开查看。</div>
      ${n.page ? `<div class="small muted" style="margin-top:6px">该节点对应 p.${
        n.page}</div>` : ''}
    </div>
    <div class="pf">
      <button class="ghost" id="mmkp-search">在资料库中检索此主题</button>
    </div>`);
  $('#mmkp-search').onclick = () => { const nm = n.name; evPopClose(); doSearch(nm); };
}

// 非末级节点：全覆盖，按类型给不同说法（纯本地渲染，不用请求）。
// 这里的每一档都**不含知识点**，但原因不同，不能一句话打发：
// 父节点页是"内容下放给后继末级页了"（卡 C-0045 起；旧产物是"人拍板暂不建"），
// 分类/根节点是"内容在子节点里"。
function mmNoKpHtml(n){
  const kind = n.lkp_role === 'parent_page' ? 'parent_page'
    : (n.kp_node_kind === 'root' ? 'root'
      : (n.children && n.children.length ? 'category'
        : (n.kp_node_kind === 'e1' && n.children && n.children.length ? 'e1' : 'none')));
  const nm = esc(n.name || '');
  let loc = `节点「${nm}」`, hdg = '', note = '';
  if (kind === 'parent_page'){
    loc = `父节点页「${nm}」`;
    hdg = `该节点对应 p.${n.page || '?'}，但它在导图里还有下级节点，本身不是末级节点。`;
    note = MINDMAP.downshift
      ? `按卡 C-0045 的口径，这一页是<b>下放</b>的：自己不单独成条，内容并入紧随其后的末级页那条知识点，并计入它的覆盖页——要看内容请展开子节点。`
      : `本页按当前口径不建知识点（人已拍板：这一页连同同类的其余 9 页暂不建，也不并入子节点）——要看内容请展开子节点。`;
  } else if (kind === 'e1'){
    loc = `非教学页「${nm}」`;
    hdg = '这一页是封皮/学习目标/目录/致谢一类的非教学页，按当前口径不建知识点。';
    note = '它不含可考的药理学内容，所以没有对应的知识点。';
  } else if (kind === 'category'){
    loc = `分类节点「${nm}」`;
    hdg = '这是分类节点，本页按当前口径不建知识点——展开子节点看它下面的知识点。';
    note = '末级节点才建知识点：内容是那一页正文与图片理解提炼出的总结。';
  } else if (kind === 'root'){
    loc = `根节点「${nm}」`;
    hdg = '这是整份课件的根节点，本页按当前口径不建知识点——展开子节点看下面的知识点。';
  } else {
    hdg = '该节点不是末级节点，按当前口径不建知识点。';
    note = '它可能只是层级里的过渡节点，展开看下面的末级节点。';
  }
  const expandBtn = (kind === 'category' || kind === 'root')
    ? '<button class="ghost" id="mmkp-expand">展开子节点</button>' : '';
  return `
    <div class="ph">
      <div class="src">
        <div class="loc">${loc} <span class="badge">${esc(mmKindLabel(kind, MINDMAP.downshift))}</span></div>
        <div class="hdg">${hdg}</div>
      </div>
      <button class="pclose" onclick="evPopClose()" title="关闭（Esc）">✕</button>
    </div>
    <div class="pbody">${note ? `<div class="small muted">${note}</div>` : ''}${
      n.page ? `<div class="small muted" style="margin-top:6px">该节点对应 p.${
        n.page}</div>` : ''}</div>
    <div class="pf">
      ${expandBtn}
      <button class="ghost" id="mmkp-search">在资料库中检索此主题</button>
    </div>`;
}

function mmOpenNode(key, el){
  const n = (MINDMAP.nodes || {})[key];
  if (!n) return;
  // 卡 C-0042：知识点页签里点任何节点弹出的小窗都**居中显示**（人点名要的）。
  // 标记挂在弹窗自己身上，evPopPlace 每次定位（含展开课件/图加载完的重定位）
  // 都读它；evPopClose / evPopShow 负责清掉（见那两处），不会串到出处小窗。
  evPopEl().dataset.kpPop = '1';
  if (n.lkp_ready){
    // 卡 C-0050：一个结构叶节点覆盖超过 3 页 → 产物把它切成多条知识点
    // （卡 C-0049 §5）。这种节点点开先**列出这几条**让人选，不替人挑一条。
    if ((n.lkp_ids || []).length > 1) return mmOpenKpList(n, el);
    return mmOpenKp(n.lkp_id, n.lkp_name || n.name, el);
  }
  evPopEl().dataset.kp = '';   // 这一屏不是总结：作废在途的总结请求
  if (n.lkp_role === 'leaf'){
    // 末级节点但还没读到总结：说"待生成"，别说成"这页不建知识点"
    return mmOpenPending(n, el);
  }
  evPopMount(el, mmNoKpHtml(n));
  const ex = $('#mmkp-expand');
  if (ex) ex.onclick = () => {
    const det = el.closest('details');
    if (det) det.open = true;
    evPopClose();
  };
  $('#mmkp-search').onclick = () => { const nm = n.name; evPopClose(); doSearch(nm); };
}

// 一个结构节点挂**多条**知识点时的选择列表（卡 C-0050）。
// 什么时候会出现：结构叶节点覆盖的页超过一条知识点的页数上限（连续 3 页，
// 见卡 C-0049 §5），管线按"连续 ≤3 页"把它切成多条——它们同属一个结构节点。
// 这里只做一件事：把这几条列出来，点哪条看哪条；**不替人挑**、也不把几条合并
// 成一段（那会让人以为产物里就是这么写的）。
function mmOpenKpList(n, el){
  const ids = n.lkp_ids || [];
  const names = n.lkp_names || [];
  evPopEl().dataset.kp = '';    // 这一屏不是某条总结：作废在途的总结请求
  evPopMount(el, `
    <div class="ph">
      <div class="src">
        <div class="loc">节点「${esc(n.name || '')}」
          <span class="badge">${ids.length} 条知识点</span></div>
        <div class="hdg">这个结构节点覆盖的页超过了一条知识点 3 页的上限，
          产物把它拆成了 ${ids.length} 条。</div>
      </div>
      <button class="pclose" onclick="evPopClose()" title="关闭（Esc）">✕</button>
    </div>
    <div class="pbody">
      <div class="small muted" style="margin-bottom:8px">${
        (n.pages || []).length
          ? '覆盖 ' + n.pages.map(p => 'p.' + p).join('、') + ' · ' : ''}点一条看它的理解与要点。</div>
      <ul class="lkppts">${ids.map((id, i) => `<li>
        <a href="#" class="mmkp-one" data-kp="${esc(id)}"
           data-name="${esc(names[i] || id)}">${esc(names[i] || id)}</a>
        <span class="small muted">${esc(id)}</span></li>`).join('')}</ul>
    </div>`);
  document.querySelectorAll('.mmkp-one').forEach(a => {
    a.onclick = e => {
      e.preventDefault();
      mmOpenKp(a.dataset.kp, a.dataset.name, el, false);
    };
  });
}

function kpForm(){
  return `<div class="row">
    <input id="kp-name" placeholder="名称（必填）" style="flex:1;min-width:180px">
    <input id="kp-subject" placeholder="科目" style="width:120px">
    <input id="kp-chunks" placeholder="出处 chunk_id，如 12,13" style="width:170px">
    <button class="act" id="kp-add">添加</button>
  </div>
  <div class="row" style="margin-top:8px">
    <input id="kp-summary" placeholder="一句话摘要" style="flex:1">
  </div>`;
}
function bindKpForm(){
  const btn = $('#kp-add'); if(!btn) return;
  btn.onclick = async () => {
    const name = $('#kp-name').value.trim();
    if (!name) return alert('名称不能为空');
    const cids = $('#kp-chunks').value.split(',').map(s=>parseInt(s.trim()))
      .filter(n=>!isNaN(n));
    await api('/api/kp', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name, summary:$('#kp-summary').value.trim(),
        subject:$('#kp-subject').value.trim(), chunk_ids:cids})});
    go('kps');
  };
}

async function openKp(id){
  // task-33：数据库知识点弹窗同样可跨页签恢复；评分/关闭时由 closeModal 清空。
  uiRememberPopup({type: 'db', id: id});
  const {kp} = await api('/api/kp?id='+id);
  const src = (kp.sources||[]).map(s => `
    <div class="hit">
      <div class="cite">${esc(s.title)} › ${esc(s.heading_path||'')}
        ${s.source_ref?`› ${esc(s.source_ref)}`:''}</div>
      <pre>${esc(s.text.slice(0,700))}${s.text.length>700?' …':''}</pre>
      <div style="margin-top:8px"><button class="ghost"
        onclick="openChunk(${s.id})">看上下文</button></div>
    </div>`).join('') || '<div class="empty">这个知识点还没有关联原文出处</div>';

  const hist = (kp.history||[]).map(h => `<tr>
      <td>${fmtTime(h.ts)}</td>
      <td><span class="badge ${h.grade>=4?'ok':(h.grade>=3?'warn':'bad')}">
        评分 ${h.grade}</span></td>
      <td class="small">${esc(h.question||'—')}</td></tr>`).join('');

  modal(`
    <h2>${esc(kp.name)}</h2>
    <div class="small muted">${esc(kp.subject||'')} ${kp.summary?'· '+esc(kp.summary):''}</div>
    <div class="grid" style="margin-top:14px">
      <div class="card"><h2>掌握度</h2><div class="big">${pct(kp.mastery)}%</div>
        <div class="bar"><i style="width:${pct(kp.mastery)}%"></i></div></div>
      <div class="card"><h2>复习间隔</h2><div class="big">${kp.interval_days||0}</div>
        <div class="sub">天</div></div>
      <div class="card"><h2>已复习</h2><div class="big">${kp.reps||0}</div>
        <div class="sub">遗忘 ${kp.lapses||0} 次</div></div>
      <div class="card"><h2>下次复习</h2>
        <div class="big" style="font-size:16px">${kp.due_at?fmtTime(kp.due_at):'未排期'}</div></div>
    </div>
    ${(kp.prerequisites&&kp.prerequisites.length)
      ? `<div class="sub" style="margin-top:12px">前置知识点：${
          kp.prerequisites.map(p=>esc(p.name)).join('、')}</div>` : ''}
    <h2 style="margin:18px 0 8px">原文出处（复习依据）</h2>${src}
    <h2 style="margin:18px 0 8px">评分</h2>
    <div class="chips">
      ${[0,1,2,3,4,5].map(g=>`<span class="chip g${g}" data-g="${g}">
        ${g} · ${['完全不会','几乎不会','答错','勉强','正确','轻松'][g]}</span>`).join('')}
    </div>
    <h2 style="margin:18px 0 8px">复习历史</h2>
    ${hist ? `<table><thead><tr><th>时间</th><th>结果</th><th>题目</th></tr>
      </thead><tbody>${hist}</tbody></table>` : '<div class="small muted">暂无记录</div>'}
    <div class="row" style="margin-top:18px">
      <button class="ghost" onclick="closeModal()">关闭</button>
    </div>`);

  $('#modalbox').querySelectorAll('.chip').forEach(c => c.onclick = async () => {
    const r = await api('/api/review', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({kp_id:id, grade:parseInt(c.dataset.g)})});
    closeModal();
    toast(`${r.name}：${r.due_in_days} 天后复习（掌握度 ${pct(r.mastery)}%）`);
    go(CURRENT);
  });
}

