# -*- coding: utf-8 -*-
"""C-0098③ 施工补丁：资料页表格降噪 + 批量入库弹窗。

只改两处（以**当前文件**为准，行号会漂移，脚本用锚行断言先核对再改）：
  Z2 表格行生成区：`rows`（已入库资料表）与 `fileRows`（raw 文件表）两个函数；
  Z3 批量入库卡片：`batchCard(files)` 的返回值（卡片 HTML → 弹窗内容）。

平行卡 C-0097② 的区段（`viewDocs` 骨架、`bindUpload`、`docsBatchOpen`、`docsMoreMenu`）
一个字都不碰；`batchCard(files)` 签名不变、`$('#view').innerHTML` 的拼接顺序不变。

行尾符：读进来是 CRLF，写回去仍是 CRLF（脚本末尾自检 bare LF == 0）。

用法：
    python c0098_apply_patch.py            # dry-run：只打印将改哪几段
    python c0098_apply_patch.py --apply    # 落盘
"""
import argparse
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

TARGET = r"G:\study_agent\tools\dashboard.py"


def L(*lines):
    """把多行文本按 \n 拼好，落盘时再统一转 CRLF。"""
    return "\n".join(lines)


# ---------- Z2-A：已入库资料表行内操作（5 按钮 → 1 + ⋯） ----------
A_NEW = L(
    '      <td class="small" style="white-space:nowrap">',
    '        <button class="ghost" onclick="reindexOne(${d.id})"',
    '          title="重建：按文件当前内容重新解析并覆盖索引（源文件改过之后用它）">重建</button>',
    '        <button class="ghost" onclick="docsDocRowMenu(this)"',
    '          data-id="${d.id}" data-title="${esc(d.title||d.path)}"',
    '          data-title-cur="${esc(d.title||\'\')}" data-path="${esc(d.path)}"',
    '          data-rel="${esc(d.rel||\'\')}"',
    '          data-name="${esc((d.rel||\'\').split(\'/\').pop()||\'\')}"',
    '          title="这一行的其余操作：导出分享包 / 改标题 / 改名 / 移除">⋯</button>',
    '      </td>',
)

# ---------- Z2-B：raw 文件表行内操作（2~4 按钮 → 1 + ⋯） ----------
B_OLD_HEAD = "  const fileRows = rawFiles.map(f => {"
B_NEW = L(
    '  const fileRows = rawFiles.map(f => {',
    "    const st = STATE[f.state] || {label:f.state, cls:''};",
    '    // 卡 C-0098③（方案 §2.2 第 6 条）：raw 表与「已入库资料」表**同一套规则**——',
    '    // 行内只留该行最高频的一个主操作（待入库＝「入库」，已入库＝「重建」）+ 行内「⋯」；',
    '    // 改名 / 删文件 / 移除索引进菜单，两个不可逆项用危险色。同一页两套规则会让人',
    '    // 困惑，所以这里的形状与上面的 rows 逐条对齐。',
    '    const mainAct = f.state === \'indexed\'',
    '      ? `<button class="ghost" onclick="reindex(\'${esc(f.rel)}\', true)"',
    '          title="重建：按文件当前内容重新解析并覆盖索引（源文件改过之后用它）">重建</button>`',
    '      : (f.supported',
    '        ? `<button class="ghost" onclick="reindex(\'${esc(f.rel)}\')"',
    '            title="入库：把这份文件解析、识别图片、切块嵌入并写进索引">入库</button>`',
    "        : '');",
    '    return `<tr>',
    '      <td>${esc(f.name)}',
    "        ${f.supported ? '' : '<span class=\"badge bad\">格式不支持</span>'}",
    '        <div class="small muted">${esc(f.rel)}</div></td>',
    '      <td class="small">${fmtSize(f.size)}</td>',
    '      <td><span class="badge ${st.cls}">${st.label}</span></td>',
    '      <td class="small" style="white-space:nowrap">${mainAct}',
    '        <button class="ghost" onclick="docsFileRowMenu(this)"',
    '          data-rel="${esc(f.rel)}" data-name="${esc(f.name)}"',
    '          data-doc="${f.doc_id || \'\'}"',
    '          data-indexed="${f.state === \'indexed\' ? \'1\' : \'\'}"',
    '          title="这一行的其余操作：改名 / 删文件${',
    "            f.state === 'indexed' ? ' / 移除索引' : ''}\">⋯</button>",
    '      </td>',
    '    </tr>`;',
    "  }).join('');",
)

# ---------- Z2-C：行内「⋯」菜单的两个渲染函数（新增，插在批量入库小节之前） ----------
MENU_BLOCK = L(
    '// ---------------- 资料页表格行内「⋯」菜单（卡 C-0098③，方案 §2.2 第 5、6 条）----------------',
    '//',
    '// 已入库表原来每行 5 个按钮、raw 表每行 2~4 个：34 行 × 5 = **170 个按钮**，是资料页',
    '// 按钮总数的最大来源（方案 §8.4 点名）。现在两张表**同一套规则**——行内只留 1 个主操作',
    '// （该行最高频的那个）+ 1 个「⋯」，其余全部进菜单，**功能一个不少**；两个不可逆项',
    '// （移除索引 / 删文件）在菜单里用危险色（方案 P3：危险动作降权但不隐藏）。',
    '//',
    '// ★ 用现成的 `evPop` 浮层机制（`evPopMount`，与 mmOpenKp / docsMoreMenu 同款），',
    '//   **不新写定位逻辑**：手写 position:absolute 会和卡片、表格容器的 overflow 打架',
    '//   （方案 §8.3）。关闭沿用同一条路径：点菜单外 / Esc / 滚动',
    '//   （evPopOutside / evPopEsc / evPopScroll，都是现成的）。',
    '//',
    '// ★ D11 的处置（方案 §3.2：「重建」/「移除」行为说明**收纳进 ⋯ 菜单对应项的 tooltip**）：',
    '//   行内「重建」主按钮的 title 收「重建会按文件当前内容重新解析并覆盖索引」，',
    '//   菜单里「移除…」「移除索引…」的 title 收「默认只删索引、保留 raw\\ 里的原文件」。',
    '//   原文不再常驻页面——正文数字降下来靠的就是这一条。',
    '//',
    '// 两行的数据走 `data-*` 从按钮自己身上读（同 `docsMoreMenu(this)` 的形态），',
    '// 不把 JSON 拼进 onclick：文件名里的引号/斜杠不会破事件参数。',
    '//',
    '// ⚠ JsArg 是给**内联 onclick 属性**用的字符串字面量：先 JSON.stringify 拿定界双引号，',
    '//   再把 `&` 与 `"` 转成实体（顺序不能反）——浏览器解析属性时解码回原字符，',
    '//   JS 拿到的就是一份合法字面量。直接写 esc() 会把单引号转成 &#39;，解码后反而破引号。',
    'const JsArg = v => JSON.stringify(String(v == null ? \'\' : v))',
    "  .replace(/&/g, '&amp;').replace(/\"/g, '&quot;');",
    '',
    'function docsDocRowMenu(el){',
    '  const d = el.dataset || {};',
    "  const id = parseInt(d.id, 10);",
    '  evPopMount(el, `',
    '    <div class="ph">',
    '      <div class="src">',
    '        <div class="loc">${esc(d.title || \'\')}</div>',
    '        <div class="hdg">这一行的其余操作（重建在主按钮上）。</div>',
    '      </div>',
    '      <button class="pclose" onclick="evPopClose()" title="关闭（Esc）">✕</button>',
    '    </div>',
    '    <div class="pbody">',
    '      <div class="row" style="flex-direction:column;align-items:stretch;gap:8px">',
    '        <button class="ghost" onclick="evPopClose();openShareExport(${id}, ${JsArg(d.title || \'\')})"',
    '          title="把这份课件的切块、向量与知识点产物打成分享包，给对方导入（对方零模型调用、不花钱）">导出分享包</button>',
    '        <button class="ghost" onclick="evPopClose();renameDocTitle(${id}, ${JsArg(d.titleCur || \'\')})"',
    '          title="改这个课件在看板上的标题（只改库里一列，不动文件、不动产物）">改标题…</button>',
    '        <button class="ghost" onclick="evPopClose();renameRawFile(${id}, ${JsArg(d.rel || \'\')}, ${JsArg(d.name || \'\')})"',
    '          title="改 raw 目录里这个文件的文件名（库里路径与页图目录会一起改）">改名…</button>',
    '        <button class="ghost" onclick="evPopClose();deleteDoc(${id}, ${JsArg(d.path || \'\')}, ${JsArg(d.title || \'\')})"',
    '          style="color:var(--bad);border-color:#4a1f1c"',
    '          title="移除：默认只删索引（切块、向量、知识点出处），raw\\ 里的原文件保留，可随时重新入库">移除…</button>',
    '      </div>',
    '    </div>`);',
    '}',
    '',
    'function docsFileRowMenu(el){',
    '  const d = el.dataset || {};',
    "  const rel = d.rel || '', name = d.name || '';",
    '  const docId = d.doc ? parseInt(d.doc, 10) : null;',
    "  const argDoc = docId === null ? 'null' : String(docId);",
    "  const indexed = d.indexed === '1';",
    '  evPopMount(el, `',
    '    <div class="ph">',
    '      <div class="src">',
    '        <div class="loc">${esc(name)}</div>',
    '        <div class="hdg">${esc(rel)}</div>',
    '      </div>',
    '      <button class="pclose" onclick="evPopClose()" title="关闭（Esc）">✕</button>',
    '    </div>',
    '    <div class="pbody">',
    '      <div class="row" style="flex-direction:column;align-items:stretch;gap:8px">',
    '        ${indexed ? `<button class="ghost" onclick="evPopClose();deleteDoc(${argDoc}, ${JsArg(rel)}, ${JsArg(name)})"',
    '          style="color:var(--bad);border-color:#4a1f1c"',
    '          title="移除索引：默认只删索引（切块、向量、知识点出处），raw\\ 里的原文件保留，可随时重新入库">移除索引…</button>` : \'\'}',
    '        <button class="ghost" onclick="evPopClose();renameRawFile(${argDoc}, ${JsArg(rel)}, ${JsArg(name)})"',
    '          title="改 raw 目录里这个文件的文件名${indexed ? \'（库里路径与页图目录会一起改）\' : \'\'}">改名…</button>',
    '        <button class="ghost" onclick="evPopClose();deleteRaw(${JsArg(rel)}, ${argDoc})"',
    '          style="color:var(--bad);border-color:#4a1f1c"',
    '          title="删文件：从 raw\\ 目录里真删掉这个文件，无法撤销${indexed ? \'；它的索引与切块同时移除\' : \'\'}">删文件…</button>',
    '      </div>',
    '    </div>`);',
    '}',
    '',
    '// 供全局 onclick 使用（与文件末尾那批 window.xxx 同因：这些名字都写在 innerHTML 的',
    '// onclick 字符串里）。显式挂一遍，不依赖"顶层函数声明会挂 window"这条隐式规则。',
    'window.docsDocRowMenu = docsDocRowMenu;',
    'window.docsFileRowMenu = docsFileRowMenu;',
    '',
)

# ---------- Z3：批量入库卡片 → 弹窗内容 ----------
C_NEW = L(
    '  // 卡 C-0098③（方案 §2.2 第 3 条）：整张卡片从常驻页面收纳为**弹窗内容**。',
    '  // 签名 `batchCard(files)` 不变；调用方仍是卡 C-0097② 的 `docsBatchOpen()`：',
    '  //     modal(batchCard(DOCS_PEND)); bindBatch(DOCS_PEND);',
    '  // 返回值从"整张 .card"改成**弹窗正文**——弹窗容器与关闭路径（Esc / 点遮罩）',
    '  // 都沿用现成的 `modal()` / `closeModal()`，不新建容器（方案 §8.3）。',
    '  // 表格、并发选择、全选/全不选、开始、重试失败项、清除结果**一个不少**；',
    '  // S1「识图要花钱」按方案 §3.3 做成弹窗内第一条醒目提示（触发前必须看见，不许只进帮助）。',
    '  // D10（「入库全部待处理文件」仍是原来的串行入口…）按方案 §3.2 **删除**——',
    '  // 那是实现细节对比，用户不需要区分串行/并发。',
    '  return `',
    '    <div class="row">',
    '      <h2 style="margin:0">批量入库 <span class="small muted">勾多份 → 多管线并发跑</span></h2>',
    '      <span style="flex:1"></span>',
    '      <button class="ghost" onclick="closeModal()" title="关闭（Esc / 点遮罩也可以）">✕</button>',
    '    </div>',
    '    <div class="sub" style="margin:10px 0;padding:9px 11px;border-radius:9px;',
    '      border:1px solid #7a5a1e;background:#2a2113;color:var(--fg)">',
    '      <b>⚠ 识图要花钱</b>（实测约 25 秒/页，按页计费）。点开始前会先给出待处理数与预估开销。',
    '    </div>',
    '    <div id="batch-progress"></div>',
    '    <div class="row" style="margin-bottom:10px">',
    '      <button class="ghost" onclick="batchSelectAll()">全选待入库</button>',
    '      <button class="ghost" onclick="batchSelectNone()">全不选</button>',
    '      <label class="small muted">并发数',
    '        <select id="batch-workers">${wopts}</select></label>',
    '      <button class="act" onclick="batchConfirm()">开始批量入库</button>',
    '      <button class="ghost" onclick="batchRetry()" id="batch-retry"',
    '        style="display:none">重试失败项</button>',
    '      <button class="ghost" onclick="batchDismiss()" id="batch-dismiss"',
    '        style="display:none">清除结果</button>',
    '      <span class="small muted" id="batch-selinfo"></span>',
    '    </div>',
    '    ${n ? `<table><thead><tr><th style="width:34px"></th><th>文件</th><th>大小</th>',
    '      <th>状态</th></tr></thead><tbody id="batch-list">${rows}</tbody></table>`',
    '      : `<div class="empty">本库没有待入库的文件——都已入库，或格式不支持。</div>`}',
    '    <div id="batch-tasks"></div>',
    '    <div class="small muted" style="margin-top:10px">',
    '      并发只决定"几份一起跑"，不改每份的处理过程；某一份失败<b>不影响其他份</b>，',
    '      失败原因逐条列出并可单独重试。',
    '    </div>`;',
)


def read_lines(path):
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    if "\r\n" not in raw:
        raise SystemExit("拒绝继续：文件不是 CRLF 行尾")
    return raw


def find_span(lines, head_line, tail_line, head_expect, tail_expect, what):
    """按锚文本定位一段（含首尾行），返回 (start_idx, end_idx)，0-based 闭区间。"""
    hits = [i for i, l in enumerate(lines) if l.strip() == head_expect.strip()]
    if len(hits) != 1:
        raise SystemExit("定位失败[%s]：首行锚 %r 命中 %d 次" % (what, head_expect, len(hits)))
    s = hits[0]
    t = None
    for i in range(s, min(len(lines), s + 400)):
        if lines[i].strip() == tail_expect:
            t = i
            break
    if t is None:
        raise SystemExit("定位失败[%s]：从第 %d 行往后找不到尾行锚 %r" % (what, s + 1, tail_expect))
    # 首尾行必须落在卡面给的近似区间附近，防止锚文本漂到别处
    if not (head_line - 60 <= s + 1 <= head_line + 60):
        raise SystemExit("定位失败[%s]：首行锚在第 %d 行，偏离卡面行号 %d 过多" % (what, s + 1, head_line))
    return s, t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--file", default=TARGET)
    a = ap.parse_args()

    path = a.file
    raw = read_lines(path)
    lines = raw.split("\r\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]          # 末尾换行不当作一行
    n0 = len(lines)
    print("文件：%s\n行数：%d（CRLF 文件）\n" % (path, n0))

    # ---- A：rows 的行内操作 td ----
    # 首锚取「入库时间」那一格（已入库表独有，全文件唯一），下一行才是操作列；
    # 不能用 `<td class="small" style="white-space:nowrap">` 当首锚——raw 表里也有一个。
    a_hits = [i for i, l in enumerate(lines)
              if l.strip() == '<td class="small muted">${fmtTime(d.indexed_at)}</td>']
    if len(a_hits) != 1:
        raise SystemExit("定位失败[Z2-A]：入库时间格锚命中 %d 次" % len(a_hits))
    if lines[a_hits[0] + 1].strip() != '<td class="small" style="white-space:nowrap">':
        raise SystemExit("定位失败[Z2-A]：入库时间格的下一行不是操作列单元格")
    a_s = a_hits[0] + 1
    a_t = None
    for i in range(a_s, a_s + 80):
        if lines[i].strip() == '</td>':
            a_t = i
            break
    if a_t is None:
        raise SystemExit("定位失败[Z2-A]：操作列找不到收尾 </td>")
    # 同一段里必须见到旧的 5 个按钮，否则说明已被改过 / 不是这一处
    a_body = "\n".join(lines[a_s:a_t + 1])
    for must in ('reindexOne(${d.id})', 'openShareExport(${d.id},', 'renameDocTitle(${d.id},',
                 'renameRawFile(${d.id},', 'deleteDoc(${d.id},'):
        if must not in a_body:
            raise SystemExit("Z2-A 锚到了第 %d 行，但里面没有 %r —— 不是已入库表的操作列，停手"
                             % (a_s + 1, must))
    print("[A] rows 操作列：第 %d–%d 行（%d 行）→ 1 个主按钮 + ⋯" % (a_s + 1, a_t + 1, a_t - a_s + 1))

    # ---- B：fileRows 整个函数体 ----
    b_s, b_t = find_span(lines, 8128, 8150, B_OLD_HEAD, "}).join('');", 'Z2-B fileRows')
    b_body = "\n".join(lines[b_s:b_t + 1])
    for must in ("const acts = [];", "f.doc_id || 'null'", '删文件</button>'):
        if must not in b_body:
            raise SystemExit("Z2-B 锚到了第 %d 行，但里面没有 %r —— 不是 raw 表行生成区，停手"
                             % (b_s + 1, must))
    print("[B] fileRows：第 %d–%d 行（%d 行）→ 1 个主按钮 + ⋯" % (b_s + 1, b_t + 1, b_t - b_s + 1))

    # ---- C：batchCard 的返回块 ----
    # 首锚不能用 `return \`<div class="card" style="margin-top:14px">`（别的函数也这么开头，
    # 全文件命中 5 次）；改为先锚 `function batchCard(files){`，再在函数体内找 return。
    bc = [i for i, l in enumerate(lines) if l.strip() == 'function batchCard(files){']
    if len(bc) != 1:
        raise SystemExit("定位失败[Z3]：function batchCard(files){ 命中 %d 次" % len(bc))
    c_s = None
    for i in range(bc[0], bc[0] + 40):
        if lines[i].strip().startswith('return `<div class="card"'):
            c_s = i
            break
    if c_s is None:
        raise SystemExit("定位失败[Z3]：batchCard 函数体内找不到返回卡片的那一行")
    if not (11983 - 60 <= c_s + 1 <= 11983 + 60):
        raise SystemExit("定位失败[Z3]：返回块在第 %d 行，偏离卡面行号 11983 过多" % (c_s + 1))
    c_t = None
    for i in range(c_s, c_s + 80):
        if lines[i].strip() == '</div>`;':
            c_t = i
            break
    if c_t is None:
        raise SystemExit("定位失败[Z3]：返回块找不到收尾 </div>`;")
    c_body = "\n".join(lines[c_s:c_t + 1])
    for must in ('<h2>批量入库', 'id="batch-workers"', 'id="batch-list"', '串行'):
        if must not in c_body:
            raise SystemExit("Z3 锚到了第 %d 行，但里面没有 %r —— 不是批量入库卡片，停手"
                             % (c_s + 1, must))
    print("[C] batchCard 返回块：第 %d–%d 行（%d 行）→ 弹窗内容"
          % (c_s + 1, c_t + 1, c_t - c_s + 1))

    # ---- D：菜单小节插入点（批量入库注释头之前） ----
    d_hits = [i for i, l in enumerate(lines)
              if l.startswith('// ---------------- 批量入库（卡 C-0037')]
    if len(d_hits) != 1:
        raise SystemExit("定位失败[Z2-C]：批量入库小节注释头命中 %d 次" % len(d_hits))
    d_at = d_hits[0]
    if lines[d_at - 1].strip() != "":
        raise SystemExit("定位失败[Z2-C]：第 %d 行前面不是空行" % (d_at + 1))
    print("[D] 菜单小节插入点：第 %d 行之前（原第 %d 行是批量入库注释头）"
          % (d_at + 1, d_at + 1))

    if not a.apply:
        print("\n（dry-run，未落盘。加 --apply 才写。）")
        return 0

    # 从后往前替换，避免行号漂移
    def block(txt):
        return txt.split("\n")

    blocks = [(d_at, d_at - 1, block(MENU_BLOCK), "Z2-C 菜单函数"),
              (c_s, c_t, block(C_NEW), "Z3 batchCard"),
              (b_s, b_t, block(B_NEW), "Z2-B fileRows"),
              (a_s, a_t, block(A_NEW), "Z2-A rows")]
    for s, t, new, what in sorted(blocks, key=lambda x: -x[0]):
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
