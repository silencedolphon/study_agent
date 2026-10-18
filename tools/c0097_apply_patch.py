# -*- coding: utf-8 -*-
"""卡 C-0097② 定点补丁：资料页「工具栏 + 卡片骨架」信息架构重排。

为什么用脚本而不是编辑器手改：本项目踩过"LF → CRLF 让 diff 爆到 13000+ 行"的坑。
本脚本一律 `newline=''` 读、按行拆分、按 CRLF 重组写回，并在写盘前后各校验一次
「裸 LF 必须为 0」，改完仍然是 13224+N 行全 CRLF。

只做**定点替换**（锚点行定位，不整文件重写）：
  A  二级页签角标 `8 待入库` → `8` + tooltip（方案 §2.2-7 / D8）
  B  孤儿记录卡片默认折叠成一行提示条（方案 §2.2-9 / D13 保留）
  C  `$('#view').innerHTML` 卡片骨架：四入口一行 + 主 CTA + ⋯ 更多 + ↻ +
     raw 卡片标题行的「批量入库」入口 + 浮动选中条（方案 §2.2-1/2/3/4/10）
  D  浮动操作条显隐（docSelPaint）
  E  D12「删除不可逆」移进真正执行的确认弹窗（方案 §3.2-D12 / §3.3-S2）
  F  D7「模型不一致就拒绝导入」进分享包导入弹窗（方案 §3.2-D7 / §3.3-S3）
  G  新增四个入口弹窗 / 批量入库入口 / 「⋯ 更多」浮层（复用 modal() 与 evPop）

用法：
  python tools\\c0097_apply_patch.py            # dry-run：只报告锚点与将要替换的区间
  python tools\\c0097_apply_patch.py --apply    # 真正写盘
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")   # 报告里有中文与 ⋯，控制台默认 GBK 会崩

DASH = r"G:\study_agent\tools\dashboard.py"

PATCHES = []

# ---------------------------------------------------------------- A
PATCHES.append(dict(
    name="A 二级页签角标改纯数字 + tooltip（§2.2-7 / D8）",
    mode="replace",
    start="  // 二级页签栏：选中态复用既有的 .ghost.on 样式，不新增 CSS。",
    end="  }).join(' ');",
    new=r"""  // 二级页签栏：选中态复用既有的 .ghost.on 样式，不新增 CSS。
  //
  // 卡 C-0097②（方案 §2.2 第 7 条）：页签**原地保留**——它是主导航，收纳导航会让人
  // 迷路（方案 P4）；只把角标从「8 待入库」改成纯数字 `8` + tooltip，省掉三个字的重复。
  // 同一条里收下 D8：那段 75 字的「两个子页签各自只列本库的文件与已入库文档…」整段
  // 移出页面，只把"进哪个库由目录决定"这句规则挂到页签 tooltip 上（成段说明按方案
  // §3.1 口径归该页的「?」帮助弹窗，那是后续卡的事）。
  const tabHint = '进哪个库由文件的目录决定：raw\\药理题库\\、raw\\学习笔记\\ 进辅库，其余进主库';
  const tabBar = Object.entries(DOCS_TABS).map(([k, label]) => {
    const n = pendingOf(k);
    return `<button class="ghost ${k === DOCS_TAB ? 'on' : ''}"
      onclick="docsTab('${k}')" title="${tabHint}">${label}${
      n ? ` <span class="badge warn" title="${n} 个待入库">${n}</span>` : ''}</button>`;
  }).join(' ');""",
))

# ---------------------------------------------------------------- B
PATCHES.append(dict(
    name="B 孤儿记录卡片折叠成一行提示条（§2.2-9 / D13）",
    mode="replace",
    start="  const orphanNote = orphans.length ? `",
    end="    </div>` : '';",
    new=r"""  // 孤儿记录（方案 §2.2 第 9 条）：位置不变，但**默认折叠成一行提示条**。
  // 它是异常态（平时 0 个），一旦出现要显眼——所以用 warn 色 + ⚠ + 数字，
  // 但不该平时占一整张卡的版面。用原生 <details>：不新增 CSS、不写 JS。
  // D13 那 40 字（「这些是手工把源文件移走或改名后留下的记录…」）按方案**保留**，
  // 展开后才显示——它解释了异常态的成因，不是教学文案。
  const orphanNote = orphans.length ? `
    <details class="card" style="margin-top:14px">
      <summary style="cursor:pointer;color:var(--warn)">
        ⚠ 索引里有 ${orphans.length} 个文件在 raw\\ 里已找不到 ›</summary>
      <div class="sub" style="margin:10px 0">
        这些是手工把源文件移走或改名后留下的记录。移除后相关切块会一并删除。
      </div>
      <table><tbody>${orphans.map(o => `<tr>
        <td>${esc(o.path)}</td>
        <td class="small">${o.n_chunks} 块</td>
        <td><button class="ghost" onclick="deleteDoc(${o.id}, '${esc(o.path)}', '${esc(o.path)}')"
          style="color:var(--bad);border-color:#4a1f1c">移除索引</button></td>
      </tr>`).join('')}</tbody></table>
    </details>` : '';""",
))

# ---------------------------------------------------------------- C
PATCHES.append(dict(
    name="C #view 卡片骨架：四入口一行 + 主CTA + ⋯更多 + ↻ + 批量入库入口 + 浮动条",
    mode="replace",
    start="""  $('#view').innerHTML = `
    ${docsRes.stale ? `<div class="card" style="border-color:#7a5a1e;background:#2a2113">
      <b>${esc(docsRes.stale)}</b>
    </div>` : ''}""",
    end="    ${orphanNote}`;",
    new=r"""  // ---------------- 卡片骨架（卡 C-0097②，方案 §2.2）----------------
  //
  // ① 四个导入入口（路径 / 拖拽 / 教材专线 / 分享包）从"平铺 + 各自带输入框与成段说明"
  //    收成**一行 4 个按钮**，点谁开谁的弹窗，输入框与说明随行进弹窗（§2.2-1）。
  // ② 原来四个平级按钮只留「入库全部待处理文件」当**唯一主 CTA**（act 样式），
  //    其余三个进右侧「⋯ 更多」；卡片标题右侧另加纯图标刷新 ↻（§2.2-2、§2.2-10）。
  // ③ 批量入库整张卡片收纳为弹窗，入口在 raw\ 文件卡片标题行、带待入库数角标（§2.2-3）。
  //    弹窗内容本体属平行卡的 batchCard 区段，这里只做入口与调用（接口签名不变）。
  // ④ 「已入库资料」上方三个按钮改成**勾选任意一行后才出现的浮动条**（§2.2-4）。
  // ⑤ D1–D13 文案按方案 §3.2 逐条处置；§3.3 的 S1–S5 安全告知一律"收纳但可达"。
  //
  // ★ 与平行卡的约定：本卡只碰工具栏与卡片骨架——表格行的生成函数（rows / fileRows）
  //   一行不动，各张卡片在 `$('#view').innerHTML` 里的**相对顺序**也不动
  //   （唯一移出的是 `${batchCard(pendingFiles)}`：它整张进了弹窗，见 §2.2-3）。
  DOCS_PEND = pendingFiles;      // 「批量入库」入口按钮要用（弹窗打开时读它）

  const stats = pending
    ? `<span class="badge warn">${pending} 个待入库</span>`
    : `<span class="badge ok">${c.unsupported ? '可入库的都已入库' : '全部已入库'}</span>${
        c.unsupported ? `<span class="badge bad">${c.unsupported} 个格式不支持</span>` : ''}`;

  $('#view').innerHTML = `
    ${docsRes.stale ? `<div class="card" style="border-color:#7a5a1e;background:#2a2113">
      <b>${esc(docsRes.stale)}</b>
    </div>` : ''}
    <div class="card">
      <div class="row" id="docs-tabs">${tabBar}</div>
    </div>
    <div class="card" style="margin-top:14px">
      <div class="row">
        <h2 style="margin:0">添加资料</h2>
        <span style="flex:1"></span>
        <button class="ghost" onclick="go('docs')"
          title="刷新列表（重新读 raw\\ 目录与索引）">↻</button>
      </div>
      <div class="row" style="margin-top:12px">
        <button class="ghost" onclick="docsDlgImport()"
          title="从本机路径导入（推荐，尤其适合大文件或整个文件夹）">＋ 从路径导入</button>
        <button class="ghost" onclick="docsDlgUpload()"
          title="拖拽 / 上传：走浏览器上传，单个几百 MB 的文件可能被浏览器掐断，那种文件请走路径导入">⇪ 拖拽 / 上传</button>
        <button class="ghost" onclick="docsDlgTextbook()"
          title="教材专线：拷进 raw\\教材\\ → 本地免费 OCR → 按章切块 → 入库，一条龙不问第二遍">📖 教材专线</button>
        <button class="ghost" onclick="openShareImport()"
          title="导入别人的分享包（.zip）：包里已带切块与向量，不调模型、不花接口费">📦 导入分享包</button>
      </div>
      <datalist id="folder-list">
        ${folderOpts.map(f=>`<option value="${esc(f)}">`).join('')}
      </datalist>
      <div class="row" style="margin-top:14px">
        <button class="act" onclick="reindex(null)"
          title="两个库一起入：一份资料进哪个库由它所在的目录决定，不由此按钮决定">入库全部待处理文件</button>
        ${stats}
        <span style="flex:1"></span>
        <button class="ghost" onclick="docsMoreMenu(this)" title="更多维护动作">⋯ 更多</button>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="row">
        <h2 style="margin:0">raw\\ 中的文件 · ${rawFiles.length} 个
          <span class="small muted">（未入库 ${c.new} · 有变 ${c.changed} · 已入库 ${c.indexed}${
            c.unsupported ? ` · 不支持 ${c.unsupported}` : ''}）</span></h2>
        <span style="flex:1"></span>
        <button class="ghost" onclick="docsBatchOpen()"
          title="勾多份 → 多管线并发跑。识图要花钱（约 25 秒/页），点开始前会先给出待处理数与预估开销">批量入库${
          pending ? ` <span class="badge warn">${pending}</span>` : ''}</button>
      </div>
      ${rawFiles.length ? `<table><thead><tr><th>文件</th><th>大小</th><th>状态</th>
        <th>操作</th></tr></thead><tbody>${fileRows}</tbody></table>`
        : `<div class="empty">「${DOCS_TABS[DOCS_TAB]}」在 raw\\ 里还没有文件。
           用上面的导入，或直接把文件拷进对应目录。</div>`}
    </div>

    <div class="card" style="margin-top:14px">
      <h2 title="重建：按文件当前内容重新解析并覆盖索引（源文件改过之后用它）｜移除：默认只删索引、保留 raw\\ 里的原文件，可随时重新入库">已入库资料 · ${documents.length} 个</h2>
      ${documents.length ? `<div class="row" id="ds-bar" style="margin:10px 0 6px;display:none">
        <span class="small muted" id="ds-info">未选择</span>
        <button class="ghost" id="ds-all">全选</button>
        <button class="ghost" id="ds-none">全不选</button>
        <button class="ghost" id="ds-del" style="color:var(--bad);border-color:#4a1f1c">批量删除选中…</button>
      </div>` : ''}
      ${documents.length ? `<table><thead><tr><th style="width:30px"></th><th>标题</th><th>类型</th><th>库</th><th>切块</th>
        <th>字数</th><th>状态</th><th>入库时间</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody></table>`
        : `<div class="empty">「${DOCS_TABS[DOCS_TAB]}」还没有已入库的资料。</div>`}
    </div>
    ${orphanNote}`;""",
))

# ---------------------------------------------------------------- D
PATCHES.append(dict(
    name="D 浮动操作条：勾选后才出现（docSelPaint）",
    mode="replace",
    start="function docSelPaint(){",
    end="}",
    new=r"""function docSelPaint(){
  const el = $('#ds-info');
  if (el){
    el.textContent = DS.sel.size ? `已选 ${DS.sel.size} 份（批量删除不可逆）` : '未选择';
  }
  // 卡 C-0097②（方案 §2.2 第 4 条）：浮动操作条**勾选任意一行后才出现**，未勾选不占位。
  // 显隐收在这一个渲染点里：行内复选框与「全选 / 全不选」最终都会走到 docSelPaint，
  // 所以不必在 docSelBind 的三个绑定点上各写一遍。
  const bar = $('#ds-bar');
  if (bar) bar.style.display = DS.sel.size ? '' : 'none';
}""",
))

# ---------------------------------------------------------------- E
PATCHES.append(dict(
    name="E D12「删除不可逆」移进批量删除确认弹窗（§3.2-D12 / §3.3-S2）",
    mode="insert_before",
    start="""    <div class="row" style="margin-top:16px">
      <button class="act" id="dd-hard" style="background:var(--bad);color:#fff">连原文件一起删</button>""",
    new=r"""    <div class="sub" style="margin-top:14px;line-height:1.95">
      <b style="color:var(--bad)">删除不可逆</b>：勾几份就整批删掉它们的库记录、切块与向量、
      知识点出处，以及四处整理产物（末级知识点总结 + 备份、名词解释、outline 缓存、页图目录）。
      确认前先把影响范围摆出来（上面就是），弹窗里再选原文件留不留。
    </div>""",
))

# ---------------------------------------------------------------- F
PATCHES.append(dict(
    name="F D7「模型不一致就拒绝导入」进分享包导入弹窗（§3.2-D7 / §3.3-S3）",
    mode="insert_before",
    start='    <div id="shi-body"></div>`);',
    new=r"""    <div class="sub" style="margin-bottom:10px">
      导入前会先核对整包（逐条 sha256）与向量模型：模型与本库不一致就<b>拒绝导入</b>
      并告诉你该怎么办，绝不用"重算一遍"偷偷花掉接口费。
    </div>""",
))

# ---------------------------------------------------------------- G
PATCHES.append(dict(
    name="G 新增：四个入口弹窗 / 批量入库入口 / 「⋯ 更多」浮层",
    mode="insert_after",
    start="function bindUpload(){",
    end="}",
    new=r"""
// ---------------- 资料页「添加资料」四入口弹窗（卡 C-0097②，方案 §2.2 第 1 条）----------------
//
// 四个入口在语义上**完全并列**（本机大文件 / 随手拖 / 教材长任务 / 收别人的包），
// 没有一个是主路径——正因为并列，才不该全摊开：以前每次导入都要先读完 4 段说明
// 才知道该用哪个。现在页面上只留一行 4 个按钮，**选哪个才看哪个的说明**。
//
// 一律复用既有基础设施，不新造：
//   · 弹窗容器 = 现成的 `modal()` / `closeModal()`（Esc、点遮罩关闭沿用同一条关闭路径）；
//   · 路径导入的绑定 = 现成的 `bindUpload()`（内部对每个元素都判空，可重复调用）；
//   · 拖拽区 = 现成的 `bindDropzone()`（同上：拖放区只在弹窗打开时才存在，
//     所以在弹窗渲染后调用它；它内部"window 上只装一次监听"的守卫保持原样）。
//
// 「放到 raw\ 下的子文件夹」两个入口共用（原实现本来就共用：拖拽读的是「方式一」
// 那一栏的 `#im-sub`）。以前靠两个 DOM 挨在同一张卡片里，现在两个入口分处两个弹窗，
// 于是把值提到这条模块变量上，两边打开时都以它初始化。
// ⚠ 内联 oninput 走 `docsSubSet()`，不直接写 `DOCS_SUB=this.value`：顶层 `let`
//   只活在脚本作用域，不挂 window，内联处理器里读不到它（会 ReferenceError）。
let DOCS_SUB = '';
// 当前子页签的待入库文件（批量入库弹窗的入参）。viewDocs 每次渲染都刷新它。
let DOCS_PEND = [];

function docsSubSet(v){ DOCS_SUB = v || ''; }

function docsSubField(){
  return `<input id="im-sub" list="folder-list" value="${esc(DOCS_SUB)}"
    placeholder="放到 raw\\ 下的子文件夹（可留空）" style="flex:1;min-width:220px"
    oninput="docsSubSet(this.value)">`;
}

// 「＋ 从路径导入」。D1（65 字「看板在本机运行，可以直接读磁盘…」）按方案
// **收纳进 tooltip**（挂 #im-src）：它是"为什么推荐路径导入"的理由，只在犹豫用哪种
// 方式时才需要看。D4（100 字「这种方式只是把文件放进 raw\…」）按方案**删除**，
// 只留「不会自动入库」这一句——它正是点完「导入」之后最需要知道的那件事。
function docsDlgImport(){
  modal(`
    <h2>＋ 从本机路径导入</h2>
    <div class="row" style="margin-top:12px">
      <input id="im-src" placeholder="粘贴本机路径，如 D:\\我的课件\\第一讲.pdf"
        style="flex:1;min-width:260px"
        title="看板在本机运行，可以直接读磁盘——不走 HTTP，没有大小限制、不吃内存，几百 MB 的 PDF 或几十个文件一次导入都没问题">
    </div>
    <div class="row" style="margin-top:8px">${docsSubField()}</div>
    <div class="sub" style="margin-top:10px;color:var(--dim)">
      ⓘ 导入只是把文件放进 <kbd>raw\\</kbd>、<b>不会自动入库</b>；入库请回页面点「入库全部待处理文件」。
    </div>
    <div class="row" style="margin-top:16px">
      <button class="act" id="im-go">导入</button>
      <button class="ghost" onclick="closeModal()">关闭</button>
    </div>`);
  bindUpload();     // 现成的绑定：只认 #im-go / #sh-open / #sh-pick，判空后逐个挂
}

// 「⇪ 拖拽 / 上传」。S1（识图要花钱）与 S4（单个几百 MB 会被浏览器掐断）都必须
// 留在**触发它的这个弹窗里**（方案 §3.3）：花钱与丢文件的事要在动手前看见，
// 不能只写进帮助。`#dz-auto` 那个复选框连文案一起原样保留——它已经做对了。
function docsDlgUpload(){
  modal(`
    <h2>⇪ 拖拽 / 上传</h2>
    <div class="sub" style="margin-top:10px">把文件（可一次多个）直接拖进下面的框里松手</div>
    <div class="row" style="margin-top:8px">${docsSubField()}</div>
    <div class="dz" id="dz">
      <div class="dz-hint" id="dz-hint">
        把文件拖到这里松手 → 上传进 <kbd>raw\\</kbd><br>
        <span class="small">落到上面那一栏指定的子文件夹（留空＝raw\\ 根目录）</span>
      </div>
      <div class="dz-foot">
        <label><input type="checkbox" id="dz-auto">
          上传后立即入库（<b>要花识图钱</b>，默认关）</label>
      </div>
      <div class="dz-bar-wrap"><div class="dz-bar" id="dz-bar"></div></div>
      <div class="dz-foot">
        <span id="dz-state">等待文件…</span><span id="dz-pct"></span>
      </div>
      <div id="dz-log"></div>
    </div>
    <div class="sub" style="margin-top:10px;color:var(--warn)">
      ⚠ <b>单个几百 MB 的文件可能被浏览器自己掐断</b>（实测 645MB 会 fail to fetch，
      服务端根本没收到）——拖拽走的是浏览器上传，那种文件请改用「＋ 从路径导入」，
      那条路直接读磁盘、不看体积。
    </div>
    <div class="sub" style="margin-top:8px;color:var(--warn)">
      <b>识图要花钱</b>（约 25 秒/页且按页计费）。拖进来的文件<b>只放进 raw\\、
      不会自动入库</b>：回页面的「批量入库」勾选后才会跑。
    </div>
    <div class="row" style="margin-top:14px">
      <button class="ghost" onclick="closeModal()">关闭</button>
    </div>`);
  bindDropzone();
}

// 「📖 教材专线」。S5（内容未变也重跑会重建块的 id，知识点出处会失效）**紧跟复选框**，
// 原样搬走、不许分离（方案 §7 第 3 条）。D3（260 字「一条龙，不问第二遍…」）按方案
// 收进弹窗内的折叠：它是"一条龙、不问第二遍"的对价说明，不能在触发前完全不可见。
function docsDlgTextbook(){
  modal(`
    <h2>📖 教材入库（专线）</h2>
    <div class="sub" style="margin-top:10px">
      <b>教材只从这里进</b>，别用「入库全部待处理文件」。
    </div>
    <div class="row" style="margin-top:10px">
      <input id="tb-src"
        placeholder="教材文件或文件夹的本机路径，如 D:\\教材\\药理学 第6版.pdf"
        style="flex:1;min-width:260px">
    </div>
    <div class="row" style="margin-top:8px">
      <label class="small muted"><input type="checkbox" id="tb-force">
        内容未变也重跑（会重建块的 id，引用它的知识点出处会失效）</label>
    </div>
    <div class="sub" style="margin-top:10px">
      <b>一条龙</b>，不问第二遍：文件先拷进 <kbd>raw\\教材\\</kbd>，再用
      <b>本地 OCR 识图</b>（免费、不调视觉模型、约 2~16 秒/页），然后<b>按章切块</b>入库。
      这是<b>长任务、串行</b>的：几百页的教材要跑一两个小时，期间可以离开这一页。
    </div>
    <details style="margin-top:8px">
      <summary class="small" style="color:var(--dim);cursor:pointer">这次会做什么？跑完怎么核对？（点开）</summary>
      <div class="sub" style="margin-top:8px">
        入库后这份资料会被打上 <b>chunk_scheme=textbook-v1</b> 标记，
        「入库全部待处理文件」与批量入库遇到它会<b>跳过</b>——
        不会用旧切块把按章切块覆盖掉。<br>
        跑完这里会列出<b>识别到的章（章号 + 章名 + 页范围）与块数变化</b>，
        供你事后核对；章识别失败（少于 3 章）会如实标成"降级"，不假装成功。
      </div>
    </details>
    <div id="tb-log"></div>
    <div class="row" style="margin-top:14px">
      <button class="act" id="tb-go" onclick="tbIngest()">选择教材并入库</button>
      <button class="ghost" onclick="closeModal()">关闭</button>
    </div>`);
}

// 批量入库弹窗（方案 §2.2 第 3 条）：整张卡片从常驻页面收纳到这里，入口是
// raw\ 文件卡片标题行那个带角标的按钮。
// ★ 内容本体（batchCard / bindBatch）属平行卡区段，这里**只做入口与调用**，
//   签名 `batchCard(files)` 一个字不改。它将来把返回值改成「按钮 + 弹窗 HTML」时，
//   只需把标题行那个入口按钮的 onclick 指过去即可（本函数就是那一层薄封装）。
function docsBatchOpen(){
  modal(batchCard(DOCS_PEND));
  bindBatch(DOCS_PEND);
  docsBatchKeepWorkers();
}

// batchConfirm() 会**复用同一个 modal 容器**弹确认框，把 `#batch-workers` 连同卡片
// 一起冲掉；而 batchStart() 随后仍要读它 ⇒ 从弹窗入口进来时并发数会静默掉回默认值。
// 这不在本卡区段（batchCard / batchStart 属另一张卡），所以只在入口这里兜住：
// 记下用户选过的值，在 batchStart 读之前把它补回容器里，**不改那两张卡的一行**。
let DOCS_BW = '';
function docsBatchKeepWorkers(){
  const w = $('#batch-workers');
  if (w){
    DOCS_BW = w.value;
    w.addEventListener('change', () => { DOCS_BW = w.value; });
  }
  if (window.__docsBatchHooked) return;
  window.__docsBatchHooked = true;
  const _confirm = window.batchConfirm, _start = window.batchStart;
  if (typeof _confirm === 'function') window.batchConfirm = function(){
    const s = $('#batch-workers'); if (s) DOCS_BW = s.value;
    return _confirm.apply(this, arguments);
  };
  if (typeof _start === 'function') window.batchStart = function(){
    if (DOCS_BW && !$('#batch-workers')){
      const box = $('#modalbox');
      if (box) box.insertAdjacentHTML('beforeend',
        `<select id="batch-workers" style="display:none"><option value="${
          esc(DOCS_BW)}" selected></option></select>`);
    }
    return _start.apply(this, arguments);
  };
}

// 工具栏右侧「⋯ 更多」（方案 §2.2 第 2 条 + 第 10 条）：收三个维护动作——
// 「全部强制重建」（危险色，且挂上 S5 那条 tooltip）/「清理无出处的记录」/「刷新列表」。
// ★ 用现成的 `evPop` 浮层机制（与 mmOpenKp 同款），**不新写定位逻辑**：
//   手写 position:absolute 会和卡片、表格容器的 overflow 打架（方案 §8.3）。
function docsMoreMenu(el){
  evPopMount(el, `
    <div class="ph">
      <div class="src">
        <div class="loc">更多操作</div>
        <div class="hdg">维护类动作，日常用不到；「全部强制重建」会重跑每一份。</div>
      </div>
      <button class="pclose" onclick="evPopClose()" title="关闭（Esc）">✕</button>
    </div>
    <div class="pbody">
      <div class="row" style="flex-direction:column;align-items:stretch;gap:8px">
        <button class="ghost" onclick="evPopClose();reindex(null,true)"
          style="color:var(--bad);border-color:#4a1f1c"
          title="内容没变也重跑：会重建块的 id，引用它的知识点出处会失效">全部强制重建</button>
        <button class="ghost" onclick="evPopClose();openCleanup()"
          title="删掉资料后失去全部出处的知识点会变成孤儿记录，这里一并收拾">清理无出处的记录</button>
        <button class="ghost" onclick="evPopClose();go('docs')">刷新列表</button>
      </div>
    </div>`);
}""",
))


def load_lines():
    with open(DASH, "r", encoding="utf-8", newline="") as f:
        txt = f.read()
    if "\r\n" not in txt:
        raise SystemExit("读到的文件没有 CRLF —— 停手，先查清楚")
    bare = txt.count("\n") - txt.count("\r\n")
    if bare:
        raise SystemExit("改前就有 %d 个裸 LF —— 停手" % bare)
    return txt.replace("\r\n", "\n").split("\n")


def locate(lines, start, end=None):
    """按行序列找锚点。

    start 可以是多行（按行序列精确匹配），必须**唯一命中**——命中 0 次或多次
    都直接停手，绝不"猜一个最近的"改下去。
    end 是单行，取 start 之后**最近**的一次命中（近邻语义天然避免同名歧义）。
    """
    s = start.split("\n")
    idx = [i for i in range(len(lines) - len(s) + 1) if lines[i:i + len(s)] == s]
    if len(idx) != 1:
        raise SystemExit("锚点 start 命中 %d 次（要求 1 次）：%r" % (len(idx), s[0][:80]))
    i = idx[0]
    if end is None:
        return i, i + len(s) - 1
    for j in range(i + len(s), len(lines)):
        if lines[j] == end:
            return i, j
    raise SystemExit("锚点 end 未在 start 之后找到：%r" % end[:80])


def main():
    apply = "--apply" in sys.argv
    lines = load_lines()
    print("读入 %d 行（全 CRLF）" % len(lines))
    plan = []
    for p in PATCHES:
        end = p.get("end")
        i, j = locate(lines, p["start"], end)
        new = p["new"].split("\n")
        mode = p["mode"]
        if mode == "replace":
            span = lines[i:j + 1]
            plan.append((p["name"], i, j, span, new, "replace"))
        elif mode == "insert_before":
            plan.append((p["name"], i, i - 1, [], new, "insert_before"))
        elif mode == "insert_after":
            plan.append((p["name"], i, j, [], new, "insert_after"))
        else:
            raise SystemExit("未知 mode: %s" % mode)
        print("\n== %s" % p["name"])
        print("   mode=%s 行 %d..%d" % (mode, i + 1, j + 1))
        if mode == "replace":
            print("   old 首行: %s" % lines[i][:100])
            print("   old 尾行: %s" % lines[j][:100])
            print("   old %d 行 → new %d 行" % (j - i + 1, len(new)))
        else:
            print("   插入 %d 行（锚点行: %s）" % (len(new), lines[i][:80]))
    if not apply:
        print("\n[dry-run] 加 --apply 才写盘")
        return 0
    # 从后往前改，行号不失效
    out = list(lines)
    for name, i, j, span, new, mode in sorted(plan, key=lambda x: -x[1]):
        if mode == "replace":
            out[i:j + 1] = new
        elif mode == "insert_before":
            out[i:i] = new
        elif mode == "insert_after":
            out[j + 1:j + 1] = new
    data = "\r\n".join(out)
    with open(DASH, "w", encoding="utf-8", newline="") as f:
        f.write(data)
    chk = open(DASH, "rb").read()
    bare = chk.count(b"\n") - chk.count(b"\r\n")
    print("\n写盘完成：%d 行 / %d 字节 / 裸 LF = %d" % (len(out), len(chk), bare))
    if bare:
        raise SystemExit("裸 LF 不是 0！立刻回滚")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
