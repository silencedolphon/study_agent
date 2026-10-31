// ---------------- 总览 ----------------
async function viewOverview(){
  const d = await api('/api/stats');
  const doc = d.documents, k = d.knowledge, m = d.mistakes;
  const kindTxt = (doc.by_kind||[]).map(x=>`${x.kind} ${x.n}`).join(' · ') || '—';
  const statusBad = (doc.by_status||[]).filter(x=>x.status!=='ok');
  let weak = (d.weakest||[]).map(w => `
    <tr><td><a href="#" onclick="openKp(${w.id});return false">${esc(w.name)}</a></td>
      <td style="width:190px">
        <div class="bar"><i style="width:${pct(w.mastery)}%"></i></div>
        <span class="small muted">掌握度 ${pct(w.mastery)}%</span></td>
      <td class="small">间隔 ${w.interval_days} 天</td>
      <td class="small">${w.lapses?`<span class="badge bad">遗忘 ${w.lapses}</span>`:'—'}</td>
    </tr>`).join('');
  $('#view').innerHTML = `
    <div class="grid">
      <div class="card"><h2>资料</h2><div class="big">${doc.total}</div>
        <div class="sub">约 ${(doc.chars/10000).toFixed(1)} 万字</div></div>
      <div class="card"><h2>知识点</h2><div class="big">${k.total}</div>
        <div class="sub">未学 ${k.new} · 已复习 ${k.total-k.new}</div></div>
      <div class="card"><h2>今日待复习</h2>
        <div class="big" style="color:${k.due?'var(--warn)':'var(--ok)'}">${k.due}</div>
        <div class="sub">今日已复习 ${k.reviews_today} 次</div></div>
      <div class="card"><h2>未解决错题</h2>
        <div class="big" style="color:${m.open?'var(--bad)':'var(--ok)'}">${m.open}</div>
        <div class="sub">累计 ${m.total} 道</div></div>
      <div class="card"><h2>笔记</h2><div class="big">${d.notes}</div></div>
    </div>
    <section class="on" style="margin-top:20px">
      <div class="card">
        <h2>资料构成</h2>
        <div class="row"><span>${esc(kindTxt)}</span></div>
        ${statusBad.length ? `<div class="row" style="margin-top:8px">
          ${statusBad.map(x=>`<span class="badge warn">${esc(x.status)} ${x.n}</span>`).join('')}
        </div>` : ''}
      </div>
    </section>
    <section class="on" style="margin-top:14px">
      <div class="card">
        <h2>最薄弱的知识点</h2>
        ${weak ? `<table><thead><tr><th>知识点</th><th>掌握度</th>
          <th>复习间隔</th><th>遗忘</th></tr></thead><tbody>${weak}</tbody></table>`
          : `<div class="empty">复习后，这里会显示需要重点回顾的知识点。</div>`}
      </div>
    </section>`;
}

