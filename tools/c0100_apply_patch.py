# -*- coding: utf-8 -*-
"""C-0100 施工补丁：预习面板（前端）+ 一条**只读**预估接口。

人 2026-10-14：「增加一个预习模块，选择章节后将那一章节拆成更加细的知识点，之后再开一个
新的大模型对该章节做预习总结……该功能默认关闭，有选项可以打开。」后端卡 C-0094 已验收，
本卡补它缺的那层皮（前端面板）。**后端管线一行没重做**；只有一处**新增**的只读路由：

  P1 `api_preview_estimate()`      —— 新函数，插在 `api_preview_start()` 之后
  P2 `GET /api/preview/estimate`   —— 新路由，插在 `/api/preview/progress` 之后
  P3 知识点页工具栏的「📖 预习」入口 —— `viewKps` 里**只加一行按钮**
  P4 预习面板函数块（`openKpsPreview` / `pv*`）—— 插在卡④的 `openKpsHelp` 之后

⚠ P1/P2 为什么必须补：既有三个接口里 `est_cost_cny` 只出现在 `POST /api/preview`
   的响应里——**那时候已经开跑**。人要的是"点开始之前看得见钱数"，所以加一条只读
   dry-run（直接调已验收的 `preview_mod.estimate_preview()`，0 次模型调用、不写文件）。
   除这 2 处外，`core/` 与看板后端**一个字都不动**。

★ 不碰的区段：卡①的设计 token、卡②的 `viewDocs`/`bindUpload`/`docsDlgImport`/
  `docsMoreMenu`、卡③的 `rows`/`fileRows`/`docsDocRowMenu`/`docsFileRowMenu`/
  `batchCard`、卡④的 `openKpsHelp`/`mm-legend`/图标化 ⊞⊟；`$('#view').innerHTML`
  的拼接顺序不变（只加一个入口按钮）。

行尾符：读进来是 CRLF，写回去仍是 CRLF（脚本末尾自检 bare LF == 0）。

用法：
    python tools\\c0100_apply_patch.py            # dry-run：只打印将改哪几段
    python tools\\c0100_apply_patch.py --apply    # 落盘
"""
import argparse
import io
import sys

sys.stdout.reconfigure(encoding="utf-8")

TARGET = r"G:\study_agent\tools\dashboard.py"


def L(*lines):
    """把多行文本按 \\n 拼好，落盘时再统一转 CRLF。"""
    return "\n".join(lines)


# ---------- P1：只读预估接口（新函数，插在 api_preview_start 之后） ----------
P1_NEW = L(
    '',
    '# ---------------- 预习的只读预估（卡 C-0100：前端面板"选完章先看钱数"） ----------------',
    '#',
    '# 为什么补这一条：卡 C-0094 的三个接口里，成本上界 `est_cost_cny` **只出现在',
    '# `POST /api/preview` 的响应里**——那时候任务已经起跑了。人要的是"点开始**之前**',
    '# 花多少"，所以这里加一条**只读**入口：直接调卡 C-0094 已验收的',
    '# `preview_mod.estimate_preview()`（dry-run），**不调模型、不写任何文件**。',
    '#',
    '# ⚠ 它同样**不进 `_lock`**（路由挂在 `/api/preview/progress` 那条锁前区）：',
    '#   它只读库与产物 JSON，入库占着锁跑几十分钟时，弹窗里点一章也得算得出来。',
    'def api_preview_estimate(doc_id, chapter) -> dict:',
    '    """选完章就能看到"1 次调用 + 成本上界"；字段口径与 `POST /api/preview` 的 est 一致。',
    '',
    '    只读、零模型调用、零写盘（`estimate_preview` 自己就是 dry-run）。',
    '    """',
    '    try:',
    '        did = int(doc_id or 0)',
    '    except (TypeError, ValueError):',
    '        return {"ok": False, "error": "doc_id 必须是整数"}',
    '    if did <= 0:',
    '        return {"ok": False, "error": "缺少 doc_id（要预习哪一份教材）"}',
    '    if chapter is None or str(chapter).strip() == "":',
    '        return {"ok": False, "error": "缺少 chapter（要预习哪一章：章号或章名）"}',
    '    try:',
    '        est = preview_mod.estimate_preview(did, chapter)',
    '    except Exception as e:                       # noqa: BLE001',
    '        # 提示词缺失 / 产物读不动之类**如实回报**，不让它炸成 500（卡 C-0050 的规矩）',
    '        return {"ok": False, "doc_id": did, "chapter": str(chapter),',
    '                "error": "%s: %s" % (type(e).__name__, e)}',
    '    if not est.get("ok"):',
    '        return {"ok": False, "doc_id": did, "chapter": str(chapter),',
    '                "error": est.get("error") or "这一章不可预习"}',
    '    keep = ("doc_id", "preview_version", "out_path", "exists", "title", "chapter",',
    '            "chapter_no", "chapter_name", "heading_path", "chapter_source", "page_label",',
    '            "n_pages", "n_blocks", "material_chars", "material_mode", "n_sections",',
    '            "sections_fallback", "sections_read", "sections_source", "model", "thinking",',
    '            "reasoning_effort", "max_tokens", "enabled", "est_calls",',
    '            "est_calls_breakdown", "est_tokens_in", "est_tokens_out", "est_cost_cny",',
    '            "est_rule", "skip_would_happen", "skip_rule", "prev")',
    '    out = {k: est.get(k) for k in keep}',
    '    out["ok"] = True',
    '    return out',
)

# ---------- P2：路由（插在 /api/preview/progress 之后） ----------
P2_ANCHOR = L(
    '            if path == "/api/preview/progress":',
    '                return self._json(api_preview_progress())',
)
P2_NEW = L(
    '            if path == "/api/preview/progress":',
    '                return self._json(api_preview_progress())',
    '            # 预习的成本预估（卡 C-0100）：前端"选完章先看钱数"用的**只读** dry-run。',
    '            # 同样排在 `_lock` **之前**——它只读库与产物 JSON，**不调模型、不写文件**，',
    '            # 入库占锁时也要算得出来（否则弹窗里点一章得等到入库跑完）。',
    '            if path == "/api/preview/estimate":',
    '                return self._json(api_preview_estimate(_int_arg(qs.get("doc_id")),',
    '                                                      qs.get("chapter")))',
)

# ---------- P3：知识点页工具栏的入口按钮（只加一行 + 一个 title） ----------
P3_ANCHOR = L(
    '        <span style="flex:1"></span>',
    '        <button class="ghost" onclick="openKpsHelp()"',
    '          title="本页怎么用（整理 / 导图 / 名词解释）">?</button>',
)
P3_NEW = L(
    '        <span style="flex:1"></span>',
    '        <button class="act" onclick="openKpsPreview()"',
    '          title="预习总结：选一份资料 → 选一章 → 看预估 → 开始（整章 1 次调用；'
    '默认关闭，可在设置中心打开）">📖 预习</button>',
    '        <button class="ghost" onclick="openKpsHelp()"',
    '          title="本页怎么用（整理 / 导图 / 名词解释）">?</button>',
)

# ---------- P4：预习面板函数块（插在 bindKpBuild 之前，即 openKpsHelp 之后） ----------
P4_NEW = r'''// ---------------- ★ 卡 C-0100：预习面板（入口 + 弹窗 + 进度 + 产物） ----------------
//
// 人的原话（2026-10-14）：「增加一个预习模块，选择章节后将那一章节拆成更加细的知识点，
// 之后再开一个新的大模型对该章节做预习总结……该功能默认关闭，有选项可以打开。」
// 后端由卡 C-0094 做完并验收（`core/preview.py` + `/api/preview/{options,progress}` 与
// `POST /api/preview`）——**这一块只是它缺的那层皮**：选资料 → 选章 → 看预估 → 开始
// → 进度 → 产物去哪；后端管线一行没重做。
//
// 三条约定（改这段前先读）：
//   · 进度**复用** `renderProgress()`（入库 / 单份整理同一套），不新造进度条；预习的
//     快照与 `/api/kp-build/progress` 同构，差别只有"花了多少"那行——它另起一个小格子写，
//     因为 `renderProgress` 只对 `kind === 'kp_build'` 显示花费，改它会碰到别的卡。
//   · 轮询照 `watchKpProgress()` 那套（1.5 秒一次、跑完即停），不新增轮询机制。
//   · 「未开启」**不是失败**：开关默认关着是人拍板的默认值，弹窗里原样显示后端给的
//     `hint`（可操作文案）+ 一个「去设置中心」按钮，不写成红字 error、不 toast 成失败。
//
// ⚠ 预估（调用次数 + 成本上界）走只读的 `GET /api/preview/estimate`（dry-run：0 次模型
//   调用、不写文件）——`POST /api/preview` 回的 est 是"**已经开跑**"之后才有的，
//   而人要的是点开始**之前**就看得见钱数。
const PVP = { opts: null, docId: 0, chNo: 0, est: null, force: false, timer: null };

function openKpsPreview(){
  modal(`<h2>📖 预习总结</h2>
    <div class="small muted" style="margin:6px 0 10px;line-height:1.7">
      选一份资料 → 选一章 → 看预估 → 开始。整章<b>一次</b>调用；
      跑完的产物留在这台机器上（不写数据库）。</div>
    <div id="pv-body"><div class="small muted">正在读可预习的资料…</div></div>
    <div class="row" style="margin-top:16px;justify-content:flex-end">
      <button class="ghost" onclick="closeModal()">关闭</button></div>`);
  pvLoad(PVP.docId || 0);
}

async function pvLoad(docId){
  const body = $('#pv-body'); if (!body) return;
  const changed = (docId || 0) !== PVP.docId;
  PVP.est = null;
  if (changed) PVP.chNo = 0;          // 换资料就重选章（章号是跟着资料走的）
  body.innerHTML = '<div class="small muted">正在读清单…</div>';
  let o = null;
  try {
    o = await api('/api/preview/options' +
                  (docId ? ('?doc_id=' + encodeURIComponent(docId)) : ''));
  } catch(e){
    body.innerHTML = `<div class="small" style="color:var(--bad)">读不到预习清单：${
      esc(e.message)}</div>`;
    return;
  }
  PVP.opts = o; PVP.docId = (o && o.doc_id) || docId || 0;
  pvRender();
  if (o && o.running) pvWatch();   // 打开时若已有任务在跑，把进度接上（别让人以为没反应）
}

// 「去设置中心」：设置中心是右上角 ⚙（卡 C-0059 的独立容器），预习开关在它的
// 「📖 AI 预习总结」分区里。`SET_TAB` 是设置中心的当前分区变量，指过去就能直达。
function pvOpenSettings(){
  closeModal();
  try { SET_TAB = 'preview'; } catch(e){ /* 设置中心读不到配置时会自己退回首屏 */ }
  openSettings();
}

function pvRender(){
  const body = $('#pv-body'); if (!body || !PVP.opts) return;
  const o = PVP.opts;
  const docs = o.docs || [], chs = o.chapters || [];
  const running = !!(o.running || (o.progress && o.progress.active));
  const sel = docs.map(d => `<option value="${d.doc_id}"${
    d.doc_id === PVP.docId ? ' selected' : ''}>${esc(d.title)} · ${d.n_chunks} 块${
    d.kind ? ' · ' + esc(d.kind) : ''}</option>`).join('');
  // ① 开关关着：**不是失败**，把后端那句可操作提示原样摆出来 + 去设置中心的路
  const offBox = o.enabled ? '' : `<div style="margin:2px 0 12px;padding:10px 12px;
      border:1px solid var(--warn);border-radius:var(--radius)">
    <div><span class="badge warn">未开启</span>
      <span class="small">预习功能现在关着 —— 这是<b>默认设置</b>，不是出错。</span></div>
    <div class="small muted" style="margin:6px 0 8px;line-height:1.75">${em(o.hint || '')}</div>
    <button class="ghost" onclick="pvOpenSettings()">去设置中心打开</button>
    <span class="small muted" style="margin-left:8px">
      右上角「⚙ 设置」→ 左栏「📖 AI 预习总结」→ 开启预习功能</span>
  </div>`;
  // ② 章列表（章号 / 章名 / 页码范围 / 节数 / 产物标记）
  let chTable = '';
  if (!PVP.docId){
    chTable = `<div class="small muted" style="padding:10px 12px">
      先在上面选一份资料——预习是<b>按章</b>做的。</div>`;
  } else if (o.error){
    chTable = `<div class="small muted" style="padding:10px 12px">
      这份资料列不出章：${esc(o.error)}</div>`;
  } else if (!chs.length){
    chTable = `<div class="small muted" style="padding:10px 12px">
      这份资料在库里没有「第 N 章」这样的章节结构，预习按章做，所以这里没得选；
      换一份带章节的教材/课件试试。</div>`;
  } else {
    chTable = `<table><thead><tr><th style="width:74px">选择</th><th>章</th>
      <th style="width:96px">页范围</th><th style="width:110px">节数</th>
      <th style="width:96px">产物</th></tr></thead><tbody>${chs.map(c => {
        const on = (c.no === PVP.chNo);
        const pages = (c.page_start && c.page_end)
          ? `p.${c.page_start}${c.page_end > c.page_start ? '–' + c.page_end : ''}` : '—';
        const sec = c.n_sections ? `${c.n_sections} 节`
          : '<span class="muted">无细目</span>';
        const hasPrev = c.has_preview ||
          !!(o.prev && o.prev.exists && o.prev.chapter_no === c.no);
        return `<tr${on ? ' style="background:rgba(90,160,255,.10)"' : ''}>
          <td>${on ? '<span class="badge ok">已选</span>'
            : `<button class="ghost" onclick="pvPickCh(${c.no})"
                title="选第 ${c.no} 章，看它的预估">选它</button>`}</td>
          <td>第 ${c.no} 章 ${esc(c.name || '')}
            <div class="small muted">${esc(c.heading_path || '')}</div></td>
          <td class="small">${pages}</td>
          <td class="small">${sec}</td>
          <td class="small">${hasPrev ? '<span class="badge ok">已有预习</span>' : '—'}</td>
        </tr>`; }).join('')}</tbody></table>`;
  }
  body.innerHTML = `
    ${offBox}
    <div class="row" style="gap:8px;margin-bottom:8px">
      <select id="pv-doc" style="flex:1;min-width:260px"
        title="只列库里的资料；列不出章的资料说明入库时没有章节结构">
        <option value="">— 选一份资料 —</option>${sel}</select>
      <span class="small muted">${PVP.docId && o.title
        ? esc(o.title) + (o.n_chapters ? ` · 共 ${o.n_chapters} 章` : '')
        : '预习按章做：一份资料 → 一章'}</span>
    </div>
    <div id="pv-chwrap" style="max-height:34vh;overflow:auto;
      border:1px solid #2a2f3a;border-radius:8px">${chTable}</div>
    <div id="pv-est" style="margin-top:10px;padding:8px 10px;border:1px solid var(--line);
      border-radius:var(--radius)"></div>
    <div class="row" style="margin-top:10px;gap:12px;flex-wrap:wrap">
      <button class="act" id="pv-go" onclick="pvStart()"
        title="启动后台线程跑预习；同一时间只允许一个任务，与「批量整理」互斥">${
        running ? '预习进行中…' : (PVP.force ? '重新预习（重做）' : '开始预习')}</button>
      <label class="row" style="gap:6px"><input type="checkbox" id="pv-force"${
        PVP.force ? ' checked' : ''}> 强制重做（旧产物先自动备份）</label>
      <span class="small muted" id="pv-state"></span>
    </div>
    <div id="pv-msg"></div>
    <div id="pv-progress"></div>
    <div class="small muted" id="pv-cost" style="margin-top:4px"></div>
    <div id="pv-out"></div>
    ${o.doc_id ? `<div class="small muted" style="margin-top:12px;line-height:1.7">📄 ${
      em(o.artifact_note || '')}</div>` : ''}`;
  const dsel = $('#pv-doc');
  if (dsel) dsel.onchange = () => pvLoad(parseInt(dsel.value || '0', 10) || 0);
  const fb = $('#pv-force');
  if (fb) fb.onchange = () => {
    PVP.force = fb.checked;
    const b = $('#pv-go');
    if (b && !b.disabled) b.textContent = PVP.force ? '重新预习（重做）' : '开始预习';
  };
  pvEstRender(PVP.est);
  const btn = $('#pv-go');
  if (btn && running){ btn.disabled = true; pvPaintProgress(o.progress || {}); }
}

// ③ 预估区：调用次数恒为 1 + 成本上界（都来自只读的 dry-run，不调模型）
function pvEstRender(e){
  const box = $('#pv-est'); if (!box) return;
  if (!PVP.chNo){
    box.innerHTML = '<span class="small muted">选一章，这里显示预估（调用次数 + 成本上界）。</span>';
    return;
  }
  if (!e || e.ok === false || e.error){
    box.innerHTML = `<span class="small" style="color:var(--warn)">
      这一章暂时算不出预估：${esc((e && e.error) || '未知原因')}</span>`;
    return;
  }
  const cost = Number(e.est_cost_cny || 0);
  box.innerHTML = `
    <div><b>第 ${e.chapter_no} 章 ${esc(e.chapter_name || '')}</b>
      <span class="small muted">${esc(e.page_label || '')} · ${e.n_pages} 页 · ${
        e.n_blocks} 块 · ${e.n_sections} 节${
        e.sections_fallback ? '（无《章节细目》，节级降级）' : ''}</span></div>
    <div class="small" style="margin-top:5px"><b>调用次数：${e.est_calls || 1} 次</b>
      <span class="muted">（整章一次总结；节级细拆走正则产物，0 次调用）</span></div>
    <div class="small" style="margin-top:2px"><b>成本上界：CNY${cost.toFixed(4)}</b>
      <span class="muted">≈ 输入 ${e.est_tokens_in || 0} + 输出 ${e.est_tokens_out || 0} token
        · 模型 ${esc(e.model || '')}${
        e.thinking === false ? ' · 不推理' : (e.reasoning_effort
          ? ' · 推理 ' + esc(e.reasoning_effort) : '')}</span></div>
    <div class="small muted" style="margin-top:5px;line-height:1.75">${em(e.est_rule || '')}</div>
    ${e.skip_would_happen ? `<div class="small" style="margin-top:6px;color:var(--warn)">
      这一章的产物<b>已存在且材料没变</b>：直接开始会<b>跳过</b>（0 次调用、0 花费）；
      要重做就勾上「强制重做」。</div>` : ''}`;
}

async function pvPickCh(no){
  PVP.chNo = no; PVP.est = null;
  pvRender();
  const box = $('#pv-est');
  if (box) box.innerHTML = '<span class="small muted">正在算预估…（只读 dry-run，不调模型）</span>';
  let e = null;
  try {
    e = await api(`/api/preview/estimate?doc_id=${PVP.docId}&chapter=${encodeURIComponent(no)}`);
  } catch(err){
    if (PVP.chNo !== no) return;
    PVP.est = null; pvEstRender({error: err.message}); return;
  }
  if (PVP.chNo !== no) return;
  PVP.est = (e && e.ok !== false) ? e : null;
  pvEstRender(e);
}

// ④ 开始：后端会把拒绝原因连**可操作提示**一起回，这里如实展示（未开启不当失败）
async function pvStart(){
  if (!PVP.docId){ toast('先选一份资料'); return; }
  if (!PVP.chNo){ toast('先选一章'); return; }
  const o = PVP.opts || {};
  if (o.running || (o.progress && o.progress.active)){
    toast('已有一个预习任务在跑，等它结束（同一时间只允许一个）'); return;
  }
  const est = PVP.est || {};
  const cost = Number(est.est_cost_cny || 0);
  const thr = Number(KPB.thr || 2) || 2;
  if (cost > thr && !window.confirm(
      `这一章的成本上界 CNY${cost.toFixed(4)}（超过 CNY${thr.toFixed(2)} 阈值）。\n` +
      `整章 ${est.est_calls || 1} 次调用；实际花费通常低于上界。\n\n确定开始预习？`)) return;
  const btn = $('#pv-go');
  if (btn){ btn.disabled = true; btn.textContent = '启动中…'; }
  const mb = $('#pv-msg'); if (mb) mb.innerHTML = '';
  // ⚠ 这里**不走 `api()`**：它见到 `error` 就抛异常，会把后端一起回的 `enabled` /
  //   `hint` 丢掉——而"未开启"这条分支要的正是那句可操作提示（不是一条报错）。
  let r = null, netErr = '';
  try {
    const resp = await fetch('/api/preview', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({doc_id: PVP.docId, chapter: PVP.chNo, force: !!PVP.force})});
    r = await resp.json().catch(() => null);
    if (!r) netErr = 'HTTP ' + resp.status + '（响应不是 JSON）';
  } catch(e){ netErr = e.message; }
  if (netErr || !r || r.error){ pvShowFail(r || {}, netErr); return; }
  toast(`已开始预习：${r.chapter || ('第' + PVP.chNo + '章')}｜整章 ${r.est_calls || 1} 次调用、`
    + `上界 CNY${Number(r.est_cost_cny || 0).toFixed(4)}${
      r.skip_would_happen ? '（产物没变，会跳过：0 花费）' : ''}`);
  pvPaintProgress(r.progress || {});
  pvWatch();
}

// 后端拒绝的三条路都如实摆出来：未开启（给 hint + 去设置中心）/ 已有任务在跑 /
// 与「整理知识点」「批量整理」互斥。
function pvShowFail(r, netErr){
  const btn = $('#pv-go');
  if (btn){ btn.disabled = false;
    btn.textContent = PVP.force ? '重新预习（重做）' : '开始预习'; }
  const off = (r && r.enabled === false) || /未开启/.test((r && r.error) || '');
  const msg = netErr || r.error || '启动失败';
  const html = off
    ? `<div style="margin-top:10px;padding:9px 11px;border:1px solid var(--warn);
         border-radius:var(--radius)">
         <div><span class="badge warn">未开启</span>
           <span class="small">预习功能关着 —— 这是<b>默认设置</b>，不是启动失败。</span></div>
         <div class="small muted" style="margin:6px 0 8px;line-height:1.75">${em(r.hint || '')}</div>
         <button class="ghost" onclick="pvOpenSettings()">去设置中心打开</button></div>`
    : `<div class="small" style="color:var(--bad);margin-top:10px">
         不能开始预习：${esc(msg)}</div>`;
  const mb = $('#pv-msg');
  if (mb) mb.innerHTML = html; else toast(msg);
  if (!off) toast('不能开始预习：' + msg);
}

// ⑤ 进度：容器与渲染**复用**入库/整理那套（`#pv-progress`），花费另起一行
function pvPaintProgress(p){
  renderProgress(p, '#pv-progress');
  const btn = $('#pv-go');
  if (btn){
    const on = !!(p && p.active);
    btn.disabled = on;
    btn.textContent = on ? '预习进行中…' : (PVP.force ? '重新预习（重做）' : '开始预习');
  }
  const box = $('#pv-cost');
  if (!box) return;
  if (p && (p.active || p.status)){
    box.innerHTML = `已调用 <b>${p.calls || 0}</b> 次 · 已花 <b>CNY${
      Number(p.cost_cny || 0).toFixed(4)}</b>${p.active ? '' : ' · 用时 ' + _fmtDur(p.elapsed)}${
      p.model ? ' · 模型 ' + esc(p.model) : ''}`;
  } else { box.innerHTML = ''; }
}

function pvWatch(){
  if (PVP.timer) return;
  const tick = async () => {
    try {
      const p = await api('/api/preview/progress');
      pvPaintProgress(p);
      const st = $('#pv-state');
      if (st && p.active){
        st.textContent = `${p.stage || ''} · 第 ${(p.stage_index || 0) + 1}/${
          (p.stages || []).length} 步`;
      }
      if (!p.active){
        clearInterval(PVP.timer); PVP.timer = null;
        pvFinish(p);
      }
    } catch(e){ clearInterval(PVP.timer); PVP.timer = null; }
  };
  tick();
  PVP.timer = setInterval(tick, 1500);
}

function pvOut(html){ const b = $('#pv-out'); if (b) b.innerHTML = html; }

async function pvFinish(p){
  const st = $('#pv-state'); if (st) st.textContent = '';
  if (p.status === 'done'){
    toast(`预习完成：${p.chapter || ''} · 花费 CNY${Number(p.cost_cny || 0).toFixed(4)}`);
    await pvLoad(PVP.docId);        // 重取章列表：产物标记与「最近一章」跟着更新
    pvOut(`<div style="margin-top:12px;padding:9px 11px;border:1px solid var(--ok);
        border-radius:var(--radius)">
      <div><span class="badge ok">预习完成</span>
        <span class="small">${esc(p.chapter || '')} · 用时 ${_fmtDur(p.elapsed)} · 调用 ${
          p.calls || 0} 次 · 花费 CNY${Number(p.cost_cny || 0).toFixed(4)}</span></div>
      <div class="small muted" style="margin-top:6px;line-height:1.75">
        产物：<code>${esc(p.out_path || '')}</code><br>
        同一份资料<b>只保留最近一章</b>：换章预习时上一章会自动备份成
        <code>.bak-&lt;时间戳&gt;</code>，旧产物不会丢。</div></div>`);
  } else {
    toast('预习失败：' + (p.error || '未知原因'));
    pvOut(`<div class="small" style="color:var(--bad);margin-top:10px">
      预习失败：${esc(p.error || '未知原因')}</div>`);
  }
}

'''

P4_ANCHOR = 'function bindKpBuild(){'


def find_block(lines, start, end, first, last, what):
    """在 [start, end) 行范围内找以 first 开头、以 last 结尾的一段（两端都含）。"""
    s = None
    for i in range(max(0, start), min(len(lines), end)):
        if lines[i].rstrip("\r").startswith(first):
            s = i
            break
    if s is None:
        raise SystemExit("定位失败[%s]：找不到首行 %r" % (what, first))
    for i in range(s, min(len(lines), s + 200)):
        if lines[i].rstrip("\r").strip() == last:
            return s, i
    raise SystemExit("定位失败[%s]：找不到收尾行 %r" % (what, last))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="落盘（默认 dry-run）")
    a = ap.parse_args()

    path = TARGET
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    if raw.count("\n") - raw.count("\r\n") != 0:
        raise SystemExit("停手：目标文件里本来就有裸 LF（%d 个）——先查清楚再说"
                         % (raw.count("\n") - raw.count("\r\n")))
    lines = raw.split("\r\n")
    if lines and lines[-1] == "":
        lines.pop()
    n0 = len(lines)

    if "openKpsPreview" in raw or "api_preview_estimate" in raw:
        raise SystemExit("停手：文件里已经出现 openKpsPreview / api_preview_estimate —— "
                         "本补丁已经打过了（或有人先改过），不再重复落盘")

    # ---- P3：viewKps 工具栏（唯一的「知识点 · 共 N 个」标题那一行之后） ----
    h2 = [i for i, l in enumerate(lines)
          if l.strip() == '<h2 style="margin:0">知识点 · 共 ${kps.length} 个（按掌握度升序）</h2>']
    if len(h2) != 1:
        raise SystemExit("定位失败[P3]：viewKps 的标题行命中 %d 次" % len(h2))
    p3_at = None
    for i in range(h2[0], h2[0] + 12):
        if lines[i].strip() == '<span style="flex:1"></span>':
            p3_at = i
            break
    if p3_at is None:
        raise SystemExit("定位失败[P3]：标题行之后找不到工具栏的弹性占位")
    body = "\n".join(lines[p3_at:p3_at + 4])
    if 'onclick="openKpsHelp()"' not in body:
        raise SystemExit("定位失败[P3]：占位之后不是（卡④的）「?」按钮 —— 停手")
    print("[P3] viewKps 工具栏：第 %d 行之后插「📖 预习」按钮" % (p3_at + 1))

    # ---- P4：函数块插入点（bindKpBuild 之前） ----
    p4_hits = [i for i, l in enumerate(lines) if l.strip() == P4_ANCHOR]
    if len(p4_hits) != 1:
        raise SystemExit("定位失败[P4]：%r 命中 %d 次" % (P4_ANCHOR, len(p4_hits)))
    p4_at = p4_hits[0]
    if lines[p4_at - 1].strip() != "":
        raise SystemExit("定位失败[P4]：第 %d 行前面不是空行" % (p4_at + 1))
    if "function openKpsHelp(){" not in "\n".join(lines[max(0, p4_at - 60):p4_at]):
        raise SystemExit("定位失败[P4]：bindKpBuild 之前 60 行里没有 openKpsHelp —— 停手")
    print("[P4] 预习面板函数块：插在第 %d 行之前（bindKpBuild 之前）" % (p4_at + 1))

    # ---- P1：api_preview_estimate 插在 api_preview_start 之后 ----
    p1_hits = [i for i, l in enumerate(lines)
               if l.startswith("# ---------------- 批量整理知识点（卡 C-0038")]
    if len(p1_hits) != 1:
        raise SystemExit("定位失败[P1]：批量整理注释头命中 %d 次" % len(p1_hits))
    p1_at = p1_hits[0]
    if "def api_preview_start(payload: dict) -> dict:" not in "\n".join(
            lines[max(0, p1_at - 60):p1_at]):
        raise SystemExit("定位失败[P1]：批量整理之前没有 api_preview_start —— 停手")
    print("[P1] api_preview_estimate：插在第 %d 行之前（api_preview_start 之后）" % (p1_at + 1))

    # ---- P2：路由 ----
    p2_at = None
    for i, l in enumerate(lines):
        if l.rstrip("\r") == P2_ANCHOR.split("\n")[0]:
            nxt = lines[i + 1].rstrip("\r")
            if nxt == P2_ANCHOR.split("\n")[1]:
                if p2_at is not None:
                    raise SystemExit("定位失败[P2]：/api/preview/progress 命中两次")
                p2_at = i
    if p2_at is None:
        raise SystemExit("定位失败[P2]：找不到 /api/preview/progress 路由")
    print("[P2] /api/preview/estimate 路由：插在第 %d 行之后" % (p2_at + 2))

    if not a.apply:
        print("\n（dry-run，未落盘。加 --apply 才写。）")
        return 0

    def block(txt):
        return txt.split("\n")

    # 从后往前替换，避免行号漂移
    edits = [(p4_at, p4_at - 1, block(P4_NEW), "P4 预习面板函数块"),
             (p3_at + 1, p3_at, block(P3_NEW), "P3 入口按钮"),
             (p2_at, p2_at + 1, block(P2_NEW), "P2 estimate 路由"),
             (p1_at, p1_at - 1, block(P1_NEW), "P1 api_preview_estimate")]
    for s, t, new, what in sorted(edits, key=lambda x: -x[0]):
        lines[s:t + 1] = new
        print("  已替换 %s（第 %d–%d 行 → %d 行）" % (what, s + 1, t + 1, len(new)))

    out = "\r\n".join(lines) + "\r\n"
    if out.count("\n") - out.count("\r\n") != 0:
        raise SystemExit("自检失败：写回文本里出现了裸 LF，拒绝落盘")
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    print("\n已落盘：%s（%d 行 → %d 行，CRLF 保持，裸 LF = %d）"
          % (path, n0, len(lines), out.count("\n") - out.count("\r\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
