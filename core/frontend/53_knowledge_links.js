// 本地知识联系试行版：只展示检索线索，查看原文后再判断具体关系。
let MMLINKS_REQUEST = 0;

function mmLinkEvidence(items){
  if (!items || !items.length)
    return '<div class="mm-link-no-source">暂无可读取的原始出处，请查看原课件。</div>';
  return items.map(e => '<div class="mm-link-quote">' +
    '<div class="mm-link-quote-label">' + esc(e.page || '页码未知') +
    (e.image_description ? ' · 机器识图描述' : ' · 课件文字') +
    (e.sparse ? ' · 原文较少' : '') + '</div>' +
    '<div>' + mdToHtml(e.excerpt || '') + '</div>' +
    '<button class="text-btn" data-mm-link-source="' + Number(e.chunk_id) + '">查看出处</button></div>').join('');
}

function mmLinkPointButton(point, label){
  return '<button class="ghost mm-link-open" data-mm-link-doc="' + Number(point.doc_id) +
    '" data-mm-link-kp="' + esc(point.kp_id || '') + '" data-mm-link-name="' + esc(point.name || '') +
    '">' + esc(label || '打开知识点') + '</button>';
}

function mmLinkCard(point, isBridge, key){
  const bridge = point.bridge;
  const term = (point.shared_terms || []).filter(Boolean).slice(0, 3);
  return '<article class="mm-link-card">' +
    '<div class="mm-link-card-head"><div><div class="mm-link-doc">' + esc(point.doc_title || '课件') +
    '</div><b>' + esc(point.name || '') + '</b></div>' + mmLinkPointButton(point) + '</div>' +
    (isBridge && bridge ? '<div class="mm-link-bridge">经过本课件的 ' +
      '<b>' + esc(bridge.name || '') + '</b> 发现这条线索 ' + mmLinkPointButton(bridge, '查看中间点') +
      '<div class="small muted">中间点与当前点属于同一目录，具体关系需核对原文。</div></div>' : '') +
    (term.length ? '<div class="mm-link-shared">共同提及：' + term.map(esc).join('、') + '</div>' : '') +
    '<div class="mm-link-summary">' + mdToHtml(point.summary || '') + '</div>' +
    (point.insufficient || point.doc_partial ? '<div class="mm-link-warning">该资料或知识点的内容不完整，请结合原课件确认。</div>' : '') +
    '<details class="mm-link-evidence"><summary>查看来源材料</summary>' +
      mmLinkEvidence(point.evidence) + '</details>' +
    '<div class="mm-link-ai"><button class="text-btn" data-mm-link-analyze="' + esc(key) +
      '">AI 分析这条联系</button><div class="mm-link-ai-content"></div></div></article>';
}

function mmLinkRender(data){
  const direct = data.direct || [], bridges = data.bridges || [];
  let html = '<div class="mm-link-head"><div><b>知识联系</b><span class="mm-link-tag">试行</span></div>' +
    '<div class="small muted">本地寻找其他课件中的线索，排序不代表结论已核实。</div></div>' +
    '<details class="mm-link-source"><summary>当前知识点的来源</summary>' +
      mmLinkEvidence((data.source || {}).evidence) + '</details>';
  if (direct.length) html += '<div class="mm-link-group"><h4>内容相近</h4>' +
    direct.map((p, i) => mmLinkCard(p, false, 'd' + i)).join('') + '</div>';
  if (bridges.length) html += '<div class="mm-link-group"><h4>通过中间知识点探索</h4>' +
    '<div class="small muted">先看本课件中的中间点，再查看其他课件。</div>' +
    bridges.map((p, i) => mmLinkCard(p, true, 'b' + i)).join('') + '</div>';
  if (!direct.length && !bridges.length)
    html += '<div class="mm-link-empty">当前学科中暂未找到足够接近的其他课件知识点。</div>';
  return html;
}

async function mmLinksToggle(d){
  const box = document.getElementById('mmkp-links');
  const btn = document.getElementById('mmkp-links-btn');
  if (!box || !btn) return;
  if (!box.hidden){
    box.hidden = true;
    btn.textContent = '知识联系';
    ++MMLINKS_REQUEST;
    evPopPlace(MMPOP.anchor || evPopEl());
    return;
  }
  box.hidden = false;
  btn.textContent = '收起联系';
  box.innerHTML = '<div class="mm-link-loading">正在本机寻找联系；首次使用需要准备索引，请稍候…</div>';
  box.scrollIntoView({block:'nearest'});
  evPopPlace(MMPOP.anchor || evPopEl());
  const request = ++MMLINKS_REQUEST;
  const did = Number(d.doc_id || MMPOP.docId) || 0;
  const kid = String(d.kp_id || '');
  try {
    const data = await api('/api/knowledge-links?doc_id=' + encodeURIComponent(did) +
      '&kp_id=' + encodeURIComponent(kid));
    if (request !== MMLINKS_REQUEST || box !== document.getElementById('mmkp-links') ||
        Number(MMPOP.docId) !== did || String(evPopEl().dataset.kp || '') !== kid) return;
    box.innerHTML = mmLinkRender(data);
    box.querySelectorAll('[data-mm-link-doc]').forEach(button => {
      button.onclick = () => mmOpenKp(button.dataset.mmLinkKp, button.dataset.mmLinkName,
        MMPOP.anchor, false, Number(button.dataset.mmLinkDoc));
    });
    box.querySelectorAll('[data-mm-link-source]').forEach(button => {
      button.onclick = () => { const id = Number(button.dataset.mmLinkSource); evPopClose(); openChunk(id); };
    });
    box.querySelectorAll('[data-mm-link-analyze]').forEach(button => {
      button.onclick = () => mmLinkOfferAnalysis(data, button, box, did, kid);
    });
    box.querySelectorAll('details').forEach(detail => detail.addEventListener('toggle',
      () => evPopPlace(MMPOP.anchor || evPopEl())));
    evPopPlace(MMPOP.anchor || evPopEl());
    // 结果加载后只滚动知识点正文，让用户立即看到联系列表。
    const body = document.getElementById('mmkp-body');
    if (body && body.contains(box))
      body.scrollTop += box.getBoundingClientRect().top - body.getBoundingClientRect().top;
  } catch(e){
    if (request === MMLINKS_REQUEST && box === document.getElementById('mmkp-links')) {
      box.innerHTML = '<div class="mm-link-empty">' + esc(e.message || '暂时无法读取联系') + '</div>';
    }
  }
}

function mmLinkOfferAnalysis(data, button, box, did, kid){
  const key = String(button.dataset.mmLinkAnalyze || '');
  const isBridge = key.startsWith('b');
  const index = Number(key.slice(1));
  const point = (isBridge ? data.bridges : data.direct)[index];
  const source = isBridge && point ? point.bridge : data.source;
  const area = button.parentElement.querySelector('.mm-link-ai-content');
  if (!point || !source || !area) return;
  if (!area.hidden && area.innerHTML){ area.innerHTML = ''; return; }
  const adequate = p => (p.evidence || []).some(e => !e.sparse && String(e.excerpt || '').length >= 30);
  if (!adequate(source) || !adequate(point)){
    area.innerHTML = '<p class="mm-link-warning">至少一侧原始出处不足，无法可靠分析；请先查看原课件。</p>';
    return;
  }
  const settings = data.analysis || {};
  const cap = Number(settings.max_tokens || 0);
  area.innerHTML = '<div class="mm-link-ai-confirm"><b>确认使用 AI 分析？</b>' +
    '<p>将两侧少量出处文字发送给 ' + esc(settings.model || '知识点设置中的模型') +
    '，调用 1 次，可能产生费用。' +
    (cap ? '输出上限 ' + cap + ' tokens。' : '当前未设置输出上限。') +
    '离开页面不会取消已发送的请求。</p>' +
    '<div class="row"><button class="act mm-link-ai-go">确认分析</button>' +
    '<button class="ghost mm-link-ai-cancel">取消</button></div></div>';
  area.querySelector('.mm-link-ai-cancel').onclick = () => { area.innerHTML = ''; };
  area.querySelector('.mm-link-ai-go').onclick = async () => {
    box.querySelectorAll('[data-mm-link-analyze]').forEach(b => { b.disabled = true; });
    area.innerHTML = '<p class="mm-link-loading">AI 正在分析材料，可能需要一些时间…</p>';
    try {
      const result = await api('/api/knowledge-links/analyze', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({doc_id:did, kp_id:kid, target_doc_id:point.doc_id,
          target_kp_id:point.kp_id, via_kp_id:isBridge ? source.kp_id : '',
          source_version:source.version, target_version:point.version,
          settings_signature:settings.signature, confirmed:true})
      });
      if (!document.body.contains(area)) return;
      if (!result.related){
        area.innerHTML = '<div class="mm-link-ai-result"><b>暂未确认明确联系</b>' +
          mdToHtml(result.reason || '现有材料不足。') + '</div>';
      } else {
        area.innerHTML = '<div class="mm-link-ai-result"><b>' + esc(result.relation || '关联分析') +
          '</b>' + mdToHtml(result.explanation || '') + '<details><summary>查看两侧引文</summary>' +
          '<p>当前材料：' + esc(result.source_quote || '') + '</p><p>关联材料：' +
          esc(result.target_quote || '') + '</p></details>' +
          (result.caution ? '<p class="small muted">' + esc(result.caution) + '</p>' : '') +
          '<div class="small muted">' + esc(result.note || '请结合上下文判断。') + '</div></div>';
      }
      evPopPlace(MMPOP.anchor || evPopEl());
    } catch(e){
      if (document.body.contains(area))
        area.innerHTML = '<p class="mm-link-warning">分析未完成：' + esc(e.message || '未知错误') + '</p>';
    } finally {
      if (document.body.contains(box))
        box.querySelectorAll('[data-mm-link-analyze]').forEach(b => { b.disabled = false; });
    }
  };
  evPopPlace(MMPOP.anchor || evPopEl());
}

