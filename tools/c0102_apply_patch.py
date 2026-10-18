# -*- coding: utf-8 -*-
"""卡 C-0102 施工补丁：知识点窗口的「教材补充」框 + 「打开教材页」按钮。

人 2026-10-14：「总结了知识点后，能够根据知识点联系到教材上的内容并可以挂载到知识
点窗口上。知识点窗口有按钮能够打开新的页面显示教材对应页。知识点窗口里**要点分一个
框**，在要点框的旁边再有一个框是教材对总结的知识点内容的补充。」

后端（`GET /api/kp-textbook` / `GET /api/teach-page`）由卡 C-0093 做完并验收，本卡
**一行后端都不动**，只补它缺的那层皮。四处改动：

  P1 CSS：`.kpcols`/`.kpcol`/`.kptb*` 的样式 + 知识点窗口的宽度（`:has(#mmkp-body)`）
  P2 `mmRenderKp` 的要点区 → 「要点」框 + 「教材补充」框两栏并排
  P3 `mmRenderKp` 末尾加一行 `mmLoadTextbook(d)`（**异步**，不阻塞首屏）
  P4 新增三个函数：`mmTbHtml` / `mmTbOpenPage` / `mmTbPlace` / `mmLoadTextbook`
     —— 插在 `mmRenderKp` 之后、笔记区（卡 C-0052）之前

★ 不碰的区段：卡②的 `viewDocs`/`bindUpload`、卡③的 `rows`/`fileRows`/`batchCard`、
  卡④的 `openKpsHelp`/`mm-legend`、C-0100/C-0101 的 `openKpsPreview`/预习面板；
  弹窗公共机制（`evPopMount`/`evPopPlace`/`evPopClose`）**一个字都不改**——宽度用
  CSS `:has()` 认内容，关窗即自动复原，不需要在任何公共函数里加/减 class。

行尾符：读进来是 CRLF，写回去仍是 CRLF（脚本末尾自检 bare LF == 0）。

用法：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0102_apply_patch.py           # dry-run
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0102_apply_patch.py --apply
"""
from __future__ import annotations

import argparse
import io
import sys

sys.stdout.reconfigure(encoding="utf-8")

TARGET = r"G:\study_agent\tools\dashboard.py"


def L(*lines):
    return "\n".join(lines)


# ---------------- P1：CSS ----------------
P1_ANCHOR = ".evpop.deck-open{width:min(1000px,calc(100vw - 24px));max-height:min(88vh,860px)}"
P1_NEW = L(
    "",
    "/* ---- 卡 C-0102：知识点窗口的「要点」框 + 「教材补充」框（人 2026-10-14） ----",
    "   原话：「知识点窗口里**要点分一个框**，在要点框的旁边再有一个框是教材对总结的",
    "   知识点内容的补充。」布局就是两个带边框的框并排（.kpcols 是 flex 行，",
    "   每个 .kpcol 是一栏）；窄屏放不下时自动堆叠，不是横向挤压。",
    "   弹窗宽度只在这里认一次：`:has(#mmkp-body)` —— 只有知识点窗口（正文里有",
    "   #mmkp-body）会变宽，别的弹窗（出处小窗等）宽度不受影响；关窗即内容被换掉，",
    "   宽度**自动复原**，所以 evPopMount/evPopClose 这些公共函数一个字都不用改。",
    "   `:not(.deck-open)`：展开课件时那条 1000px 的规则仍然说话。 */",
    ".evpop:has(#mmkp-body):not(.deck-open){width:min(880px,calc(100vw - 24px))}",
    ".kpcols{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-start;margin-top:10px}",
    ".kpcol{flex:1 1 320px;min-width:0;border:1px solid var(--line);border-radius:8px;",
    "  background:var(--panel2);padding:9px 11px}",
    ".kpcol .lkpsub{margin:0 0 7px;border-top:none;padding-top:0}",
    ".kpcol .lkppts{padding-left:18px}",
    ".kpcol-tb{background:transparent}",
    ".kptbname{color:var(--fg);margin:0 0 6px}",
    ".kptbnote{font-size:12.5px;line-height:1.7;color:var(--dim);background:var(--panel);",
    "  border-left:2px solid var(--accent);border-radius:0 6px 6px 0;padding:6px 9px;",
    "  margin:0 0 8px;word-break:break-word}",
    ".kptbwarn{font-size:12.5px;line-height:1.7;color:var(--warn);background:var(--panel);",
    "  border-left:2px solid var(--warn);border-radius:0 6px 6px 0;padding:6px 9px;",
    "  margin:0 0 8px;word-break:break-word}",
    ".kptbacts{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 8px}",
    ".kptbterms{margin:0 0 8px;line-height:1.7}",
    ".kptbpasses{display:flex;flex-direction:column;gap:8px;max-height:44vh;overflow:auto}",
    ".kptbpass{border:1px solid var(--line);border-radius:7px;background:var(--panel);",
    "  padding:7px 9px}",
    ".kptbpasshd{display:flex;align-items:center;gap:7px;flex-wrap:wrap;font-size:11.5px;",
    "  color:var(--dim);margin-bottom:4px}",
    ".kptbpasstext{font-size:12.5px;line-height:1.75;color:var(--fg);",
    "  white-space:pre-wrap;word-break:break-word;max-height:150px;overflow:auto}",
    ".kptbfoot{margin-top:8px;line-height:1.7}",
    "@media (max-width:700px){.kpcols>.kpcol{flex:1 1 100%}}",
)

# ---------------- P2：mmRenderKp 的要点区 → 两栏 ----------------
P2_OLD = L(
    '      <div class="lkpsub" style="margin-top:0;border-top:none;padding-top:0">要点${',
    "        pts.length ? ' ' + pts.length + ' 条' : ''}</div>",
    "      ${pts.length",
    '        ? `<ul class="lkppts">${pts.map(x => `<li>${esc(x)}</li>`).join(\'\')}</ul>`',
    '        : `<div class="small muted">${d.insufficient',
    "             ? '模型判定本页材料不足以概括（' + esc(d.insufficient_reason || '') +",
    "               '），按提示词口径不给要点。'",
    "             : '这条没有要点。'}</div>`}",
)
P2_NEW = L(
    "      <!-- ★ 卡 C-0102（人 2026-10-14）：「要点分一个框，在要点框的旁边再有一个框",
    "           是教材对总结的知识点内容的补充」。两栏并排、窄屏堆叠（见 .kpcols）。",
    "           右栏由 mmLoadTextbook() **异步**补进来——这一屏先出来，不等教材。 -->",
    '      <div class="kpcols">',
    '        <div class="kpcol" id="mmkp-ptsbox">',
    '          <div class="lkpsub">要点${pts.length ? \' \' + pts.length + \' 条\' : \'\'}</div>',
    "          ${pts.length",
    '            ? `<ul class="lkppts">${pts.map(x => `<li>${esc(x)}</li>`).join(\'\')}</ul>`',
    '            : `<div class="small muted">${d.insufficient',
    "                 ? '模型判定本页材料不足以概括（' + esc(d.insufficient_reason || '') +",
    "                   '），按提示词口径不给要点。'",
    "                 : '这条没有要点。'}</div>`}",
    "        </div>",
    '        <div class="kpcol kpcol-tb" id="mmkp-tbbox">',
    '          <div class="lkpsub">教材补充',
    '            <span class="small muted">· 自动匹配，以教材原文为准</span></div>',
    '          <div class="small muted" id="mmkp-tbstatus">正在查教材…（上面的要点先看，',
    "            这一栏随后补上）</div>",
    '          <div id="mmkp-tbbody"></div>',
    "        </div>",
    "      </div>",
)

# ---------------- P3：mmRenderKp 末尾的异步调用 ----------------
P3_ANCHOR = "  mmKpScrollGuard(evPopEl());"
P3_NEW = L(
    "  // ★ 卡 C-0102：教材补充**异步**加载——窗口先出既有内容，教材那一栏后到。",
    "  //   刻意不 await：它要按知识点词项在教材里找段落、还要现渲染页图，快慢不定；",
    "  //   让它挡住「点开节点先看到要点」就本末倒置了（见 mmLoadTextbook）。",
    "  mmLoadTextbook(d);",
)

# ---------------- P4：新增函数块 ----------------
P4_ANCHOR = "// ---------------- 知识点窗口的笔记（卡 C-0052） ----------------"
P4_NEW = L(
    "// ---------------- ★ 卡 C-0102：知识点窗口的「教材补充」框 ----------------",
    "// 人 2026-10-14：「总结了知识点后，能够根据知识点联系到教材上的内容并可以挂载到",
    "// 知识点窗口上。知识点窗口有按钮能够打开新的页面显示教材对应页。」",
    "//",
    "// 数据来自卡 C-0093 已验收的**只读**接口",
    "// `GET /api/kp-textbook?doc_id=<课件id>&kp_id=<知识点id>`（读产物 JSON + 库里的",
    "// chunks + 现渲染教材页图，**零模型调用**）。前端只做三件事：把结果画进右栏、",
    "// 给每段配一个「打开教材页」按钮、把\"取不到\"的原因**如实写出来**。",
    "//",
    "// ★ 三条不能违反的约定：",
    "//   1. **不阻塞首屏**：窗口先渲染既有内容（要点/正文/出处/生成链条/课件…），",
    "//      教材这一栏由 `mmLoadTextbook()` 异步补（后端注释：教材页首次渲染约 0.2 秒）；",
    "//   2. 后端的四个 status **都不是错误**（HTTP 200 + 明确说法）：",
    "//      `ok` / `no_kp`（没有这条知识点）/ `no_textbook`（本学科库没有教材）/",
    "//      `no_match`（教材里没匹配上），外加一条兜底的 `error`。**每一种都要有看得懂",
    "//      的中文说法**，不许白屏、不许把它当失败；",
    "//   3. 页图 URL **只用后端给的** `textbook.page_url` 模板（`{page}` 换页码），",
    "//      前端不自己拼 `/api/teach-page`——只放行教材类 PDF 的安全闸在后端。",
    "//",
    "// 竞态：换/关知识点后迟到的响应一律作废（判据与 mmOpenKp 一致：小窗的 dataset.kp，",
    "// 关窗时被 evPopClose 清空）。",
    "// 教材补充框的正文（按 status 分支）。**任何分支都有话说**——这是本卡的硬要求。",
    "function mmTbHtml(tb){",
    "  const st = String((tb && tb.status) || 'error');",
    "  const t = (tb && tb.textbook) || {};",
    "  const msg = esc((tb && tb.message) || '');",
    "  const title = esc(t.title || '');",
    "  const nPages = parseInt(t.pages || 0, 10) || 0;",
    "  const nChunks = parseInt(t.n_chunks || 0, 10) || 0;",
    "  const head = title",
    "    ? `<div class=\"small kptbname\">教材《${title}》${",
    "        nPages ? ' · 共 ' + nPages + ' 页' : ''}${",
    "        nChunks ? ' · ' + nChunks + ' 块' : ''}</div>`",
    "    : '';",
    "  // 「打开教材页」：有页码才可点；没有页码的四种情况一律**置灰 + 写明为什么**",
    "  // （点不动 > 点了没用；空按钮会让人以为功能坏了）。",
    "  const btn = (page, why) => page",
    "    ? `<button class=\"act\" data-tbpage=\"${page}\" title=\"在新标签打开教材 p.${page}",
    "         的页图（后端按页渲染，零模型调用）\">📖 打开教材页 p.${page}</button>`",
    "    : `<button class=\"ghost\" disabled title=\"${esc(why)}\">📖 打开教材页</button>`;",
    "  if (st === 'ok'){",
    "    const ps = (t.passages || []);",
    "    const terms = (t.terms || []);",
    "    const p0 = parseInt((ps[0] || {}).page || 0, 10) || 0;",
    "    const passHtml = ps.map(p => {",
    "      const pg = parseInt(p.page || 0, 10) || 0;",
    "      const why = (p.why || []).slice(0, 4).map(x => esc(x)).join('、');",
    "      return `<div class=\"kptbpass\">",
    "        <div class=\"kptbpasshd\">",
    "          <b>${esc(p.page_label || (pg ? 'p.' + pg : '页码未知'))}</b>${",
    "            p.page_exact ? '' : '<span class=\"small muted\">（块起始页）</span>'}${",
    "            p.chapter ? `<span class=\"small muted\">第 ${esc(p.chapter)} 章</span>` : ''}",
    "          <span style=\"flex:1\"></span>",
    "          ${pg ? `<button class=\"ghost\" data-tbpage=\"${pg}\"",
    "             title=\"在新标签打开教材 p.${pg} 的页图\">打开 p.${pg}</button>` : ''}",
    "        </div>",
    "        ${p.heading_path ? `<div class=\"small muted\">${esc(p.heading_path)}</div>` : ''}",
    "        <div class=\"kptbpasstext\">${esc(p.text || '')}${",
    "          p.text_truncated ? '<span class=\"muted\">（这一段太长，已截断）</span>' : ''}</div>",
    "        ${why ? `<div class=\"small muted\">命中：${why}${",
    "          (p.score === 0 || p.score) ? ' · 相关度 ' + esc(p.score) : ''}</div>` : ''}",
    "      </div>`;",
    "    }).join('');",
    "    return `${head}",
    "      <div class=\"kptbnote\">${msg}</div>",
    "      <div class=\"kptbacts\">${btn(p0, '')}",
    "        <span class=\"small muted\">在新标签打开该页的图</span></div>",
    "      ${terms.length ? `<div class=\"small muted kptbterms\"",
    "        title=\"按这些词项在教材里检索\">命中词项：${",
    "        terms.slice(0, 12).map(x => esc((x && x.term) || x)).join('、')}</div>` : ''}",
    "      <div class=\"kptbpasses\">${passHtml}</div>",
    "      <div class=\"small muted kptbfoot\">★ 这一栏是按词项与章名<b>自动匹配</b>的",
    "        教材补充，仅供参考，<b>请以教材原文为准</b>${",
    "        t.truncated ? '（还有更多命中段落未列出）' : ''}${",
    "        t.note ? '<br>' + esc(t.note) : ''}</div>`;",
    "  }",
    "  // 其余四种都没有可跳的页码 —— 按钮置灰 + 写明原因，正文给后端那句话",
    "  if (st === 'no_textbook'){",
    "    return `${head}<div class=\"kptbwarn\">${",
    "      msg || '当前学科库里没有教材类资料。'}</div>",
    "      ${btn(0, '本学科库没有 role=textbook 的 PDF：没有页图可打开')}",
    "      <div class=\"small muted kptbfoot\">教材补充依赖当前学科库里 role=textbook 的",
    "        PDF；把教材放进 raw 并入库后，重开这个窗口就会看到。</div>`;",
    "  }",
    "  if (st === 'no_match'){",
    "    return `${head}<div class=\"kptbwarn\">${",
    "      msg || '教材里没有找到与这条知识点匹配的段落。'}</div>",
    "      ${btn(0, '教材里没匹配上：不知道打开哪一页')}",
    "      <div class=\"small muted kptbfoot\">教材是在库里的，只是这次没匹配上——",
    "        上面「要点」框里的内容不受影响。</div>`;",
    "  }",
    "  if (st === 'no_kp'){",
    "    return `${head}<div class=\"kptbwarn\">${",
    "      msg || '没有这条知识点的总结产物。'}</div>",
    "      ${btn(0, '没有这条知识点的产物：无从匹配页码')}",
    "      <div class=\"small muted kptbfoot\">教材补充是拿知识点去教材里找段落，",
    "        没有产物就无从匹配；这一栏空着不影响上面的要点。</div>`;",
    "  }",
    "  return `${head}<div class=\"kptbwarn\">${",
    "    msg || '教材补充读取失败（后端没有给出原因）。'}</div>",
    "    ${btn(0, '接口没有给出可用的页码')}",
    "    <div class=\"small muted kptbfoot\">只影响这一栏，上面的要点照常。</div>`;",
    "}",
    "",
    "// 「打开教材页」：用后端给的模板拼 URL，**新标签**打开那一页的图。",
    "// 页码来自后端（`passages[].page`），前端不猜页；模板里的 `{page}` 是后端留的槽位。",
    "function mmTbOpenPage(page){",
    "  const t = ((MMPOP.tb || {}).textbook) || {};",
    "  const tpl = String(t.page_url || '');",
    "  const p = parseInt(page, 10) || 0;",
    "  if (p <= 0 || tpl.indexOf('{page}') < 0){",
    "    toast('这一栏没有可以打开的教材页（教材里没匹配上，或本学科库没有教材）');",
    "    return;",
    "  }",
    "  window.open(tpl.replace('{page}', encodeURIComponent(String(p))), '_blank', 'noopener');",
    "}",
    "",
    "// 尺寸变了要重新定位：弹窗是 position:fixed，右栏长出来会改变高度",
    "// （同「展开当页课件」/ 笔记区的道理，见 evPopPlace）。",
    "function mmTbPlace(){ evPopPlace(MMPOP.anchor || evPopEl()); }",
    "",
    "// 【教材补充】异步加载：窗口先出既有内容，这一栏后到。",
    "async function mmLoadTextbook(d){",
    "  const kpId = String((d && d.kp_id) || '');",
    "  const did = parseInt((d && d.doc_id) || 0, 10) || 0;",
    "  if (!$('#mmkp-tbbody')) return;",
    "  MMPOP.tb = null;                       // 换知识点：上一份教材结果作废",
    "  let tb;",
    "  try {",
    "    tb = await api('/api/kp-textbook?doc_id=' + encodeURIComponent(did) +",
    "                   '&kp_id=' + encodeURIComponent(kpId));",
    "  } catch(e){",
    "    // 网络/HTTP 失败也走同一套渲染（status=error）：**不白屏、不当成没救**",
    "    if ((evPopEl().dataset.kp || '') !== kpId) return;   // 换了/关了窗：作废",
    "    const b0 = $('#mmkp-tbbody');",
    "    if (!b0) return;",
    "    b0.innerHTML = mmTbHtml({status: 'error',",
    "                             message: '教材补充读取失败：' + ((e && e.message) || e)});",
    "    const s0 = $('#mmkp-tbstatus'); if (s0) s0.hidden = true;",
    "    mmTbPlace();",
    "    return;",
    "  }",
    "  if ((evPopEl().dataset.kp || '') !== kpId) return;      // 迟到的响应：作废",
    "  const box = $('#mmkp-tbbody');",
    "  if (!box) return;",
    "  MMPOP.tb = tb;",
    "  box.innerHTML = mmTbHtml(tb);",
    "  const st = $('#mmkp-tbstatus'); if (st) st.hidden = true;   // 收掉「正在查教材…」",
    "  // 绑定放在填充之后：按钮是这段 HTML 带出来的（异步到，绑不到渲染那一刻）",
    "  box.querySelectorAll('[data-tbpage]').forEach(x => {",
    "    x.onclick = () => mmTbOpenPage(parseInt(x.dataset.tbpage, 10));",
    "  });",
    "  mmTbPlace();",
    "}",
    "",
)


def find_line(lines, text, what):
    hits = [i for i, l in enumerate(lines) if l.rstrip("\r") == text]
    if len(hits) != 1:
        raise SystemExit("定位失败[%s]：%r 命中 %d 次" % (what, text, len(hits)))
    return hits[0]


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

    if "mmLoadTextbook" in raw or "kpcols" in raw:
        raise SystemExit("停手：文件里已经出现 mmLoadTextbook / kpcols —— 本补丁已经打过了")
    # 边界：本卡要动的只有 mmRenderKp 那一段。先确认"不认识的改动痕迹"不存在：
    for probe, why in (("openKpsPreview", "C-0100/C-0101 预习面板"),
                       ("mm-legend", "卡④ 图例"),
                       ("function mmRenderKp", "mmRenderKp 本体")):
        if probe not in raw:
            raise SystemExit("停手：找不到 %s（%s）——文件与人给的前提不符" % (probe, why))

    # ---- P1 ----
    p1_at = find_line(lines, P1_ANCHOR, "P1")
    # ---- P2 ----
    p2_s = None
    old_lines = P2_OLD.split("\n")
    for i in range(len(lines)):
        if lines[i] == old_lines[0] and lines[i:i + len(old_lines)] == old_lines:
            if p2_s is not None:
                raise SystemExit("定位失败[P2]：要点区命中两次")
            p2_s = i
    if p2_s is None:
        raise SystemExit("定位失败[P2]：找不到 mmRenderKp 的要点区原文（停手，别硬猜）")
    print("[P2] 要点区：第 %d-%d 行 → 两栏（%d 行）"
          % (p2_s + 1, p2_s + len(old_lines), len(P2_NEW.split("\n"))))
    # ---- P3 ----
    p3_at = find_line(lines, P3_ANCHOR, "P3")
    if "function mmRenderKp" not in "\n".join(lines[max(0, p3_at - 220):p3_at]):
        raise SystemExit("定位失败[P3]：mmKpScrollGuard(evPopEl()) 之前 220 行里没有 "
                         "mmRenderKp —— 停手")
    print("[P3] mmLoadTextbook(d) 调用：插在第 %d 行之前" % (p3_at + 1))
    # ---- P4 ----
    p4_at = find_line(lines, P4_ANCHOR, "P4")
    print("[P4] 教材补充函数块：插在第 %d 行之前（笔记区之前）" % (p4_at + 1))
    print("[P1] CSS：插在第 %d 行之后（.evpop.deck-open 之后）" % (p1_at + 1))

    if not a.apply:
        print("\n（dry-run，未落盘。加 --apply 才写。）")
        return 0

    edits = [
        (p4_at, p4_at - 1, P4_NEW.split("\n"), "P4 教材补充函数块"),
        (p3_at, p3_at - 1, P3_NEW.split("\n"), "P3 异步调用"),
        (p2_s, p2_s + len(old_lines) - 1, P2_NEW.split("\n"), "P2 两栏布局"),
        (p1_at + 1, p1_at, P1_NEW.split("\n"), "P1 CSS"),
    ]
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
