// ---------------- 错题 ----------------
async function viewMistakes(){
  const {mistakes, stats} = await api('/api/mistakes');
  const reasonTxt = (stats.by_reason||[]).map(r =>
    `<span class="badge warn">${esc(r.reason)} ${r.n}</span>`).join(' ') || '—';
  const rows = mistakes.map(m => `
    <tr>
      <td><input type="checkbox" class="mk-ck" value="${m.id}"></td>
      <td>${m.resolved?'<span class="badge ok">已解决</span>'
                      :'<span class="badge bad">未解决</span>'}</td>
      <td>${esc(m.question||'')}
        ${m.kp_name?`<div class="small muted">知识点：${esc(m.kp_name)}</div>`:''}</td>
      <td class="small">${esc(m.reason||'未分类')}</td>
      <td class="small">${esc(m.my_answer||'—')}</td>
      <td class="small">${esc(m.right_answer||'—')}</td>
      <td class="small muted">${fmtTime(m.created_at)}</td>
      <td><button class="text-btn" onclick="delMistakes([${m.id}])"
        style="color:var(--bad)">删除</button></td>
    </tr>`).join('');
  const nResolved = mistakes.filter(m => m.resolved).length;
  $('#view').innerHTML = `
    ${mistakes.length ? `<div class="card">
      <h2>错因分布</h2><div class="row">${reasonTxt}</div>
      ${(stats.repeated_kps&&stats.repeated_kps.length)
        ? `<div class="sub" style="margin-top:10px">反复出错：
            ${stats.repeated_kps.map(k=>`${esc(k.name)}（${k.n}次）`).join('、')}</div>`
        : ''}
    </div>` : ''}
    <div class="card" style="margin-top:14px">
      <h2>错题本 · ${mistakes.length} 道</h2>
      ${mistakes.length ? `
        <div class="row" style="margin:10px 0 14px">
          <label class="row" style="gap:6px">
            <input type="checkbox" id="mk-all"> 全选
          </label>
          <button class="ghost" onclick="delSelectedMistakes()">删除选中</button>
          <details class="row-actions"><summary aria-label="更多错题操作" title="更多操作">⋯</summary>
            <div class="row-actions-menu">
              ${nResolved ? `<button class="ghost" onclick="delMistakes(null, true)">删除已解决的 ${nResolved} 道</button>` : ''}
              <button class="ghost" onclick="delMistakes(null, false, true)" style="color:var(--bad)">清空全部</button>
            </div></details>
        </div>
        <table><thead><tr><th></th><th>状态</th><th>题目</th><th>错因</th>
        <th>我的答案</th><th>正确答案</th><th>时间</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>`
        : `<div class="empty">还没有错题记录。</div>`}
    </div>`;
  const all = $('#mk-all');
  if (all) all.onclick = () => {
    document.querySelectorAll('.mk-ck').forEach(c => c.checked = all.checked);
  };
}

function delSelectedMistakes(){
  const ids = [...document.querySelectorAll('.mk-ck')]
    .filter(c => c.checked).map(c => parseInt(c.value));
  if (!ids.length) return toast('请先勾选要删除的错题');
  delMistakes(ids);
}

async function delMistakes(ids, onlyResolved, all){
  const what = all ? '全部错题'
    : onlyResolved ? '所有已解决的错题'
    : ids && ids.length === 1 ? `错题 #${ids[0]}`
    : `选中的 ${ids ? ids.length : 0} 道错题`;
  const ok = await confirmBox(
    '删除错题确认',
    `将删除${what}。\n\n此操作不可撤销。错题本身是独立记录，` +
    `删除不影响资料与知识点。`);
  if (!ok) return;
  try {
    // 清空全部必须显式带 all:true，不能靠"没选中任何项"来推断，
    // 否则空选择会被误解成清空。
    const body = {};
    if (all) body.all = true;
    else if (onlyResolved) body.only_resolved = true;
    else if (ids && ids.length) body.ids = ids;
    else return toast('未指定要删除的错题');
    const r = await api('/api/mistake/delete', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    if (r.error) return toast(r.error);
    toast(`已删除 ${r.deleted} 道错题，剩余 ${r.remaining} 道`);
    go('mistakes');
  } catch(e){ toast('删除失败：' + e.message); }
}

// 统一的确认框：用模态而不是 confirm()，这样能排版、能显示多行影响说明
function confirmBox(title, text){
  return new Promise(resolve => {
    modal(`
      <h2>${esc(title)}</h2>
      <div class="sub" style="margin:12px 0;line-height:1.9;white-space:pre-wrap">${esc(text)}</div>
      <div class="row" style="margin-top:18px">
        <button class="act" id="cb-ok" style="background:var(--bad);color:#fff">确认</button>
        <button class="ghost" id="cb-no">取消</button>
      </div>`);
    $('#cb-ok').onclick = () => { closeModal(); resolve(true); };
    $('#cb-no').onclick = () => { closeModal(); resolve(false); };
  });
}

