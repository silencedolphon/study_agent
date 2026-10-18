# -*- coding: utf-8 -*-
"""卡 C-0101 定点补丁：预习面板只列**入库的教材**（role=textbook）+ 空态引导。

手法：**定点文本替换**（绝不整文件重写），二进制读写以保住 CRLF——
本项目踩过 LF→CRLF 让 diff 爆到 13000+ 行的坑，所以每一处替换都断言
"出现且仅出现一次"，并且写完复查 `\\r\\n` 计数与裸 LF 为 0。

改动的四处（都在卡面允许的范围内）：
  A. `tools/dashboard.py` 后端：新增范围筛选与**章数批量统计** helper
     + `api_preview_options()` 加 `scope` 参数（默认 textbook）
  B. 路由 `GET /api/preview/options` 把 `scope` 透传进后端
  C. 前端 `PVP` 状态 + `pvLoad()` 带上 scope
  D. 前端 `pvRender()` 的列表渲染：范围切换、role/章数标签、空态引导
     （外加两个新的小函数 `pvDocLabel` / `pvSetScope` / `pvGoDocs`）

用法：
    .\\venv\\Scripts\\python.exe -X utf8 tools\\c0101_apply_patch.py            # 应用
    .\\venv\\Scripts\\python.exe -X utf8 tools\\c0101_apply_patch.py --check    # 只检查是否已应用
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "tools" / "dashboard.py"

PATCH_ID = "C-0101"

# ---------------------------------------------------------------- A 后端 helper
A_OLD = (
    "# ⚠ 写库禁止：只写 `logs/knowledge/<doc_id>-预习总结.json`，五张表仍然一行都不碰。\r\n"
    "def api_preview_options(conn, doc_id: int = 0) -> dict:\r\n"
    '    """预习面板的清单：可预习的资料 + 选中资料的**章**（每章带节数与产物状态）。\r\n'
    "\r\n"
    "    只读、**零模型调用**。章节来自 `chunks.heading_path`（入库结构），\r\n"
    "    节数来自卡 C-0092 的《章节细目》正则产物。\r\n"
    '    """\r\n'
    '    rows = conn.execute("SELECT id, title, kind, corpus, n_chunks FROM documents "\r\n'
    '                        "ORDER BY id").fetchall()\r\n'
    '    docs = [{"doc_id": int(r["id"]), "title": r["title"] or ("文档 %d" % int(r["id"])),\r\n'
    '             "kind": r["kind"] or "", "corpus": r["corpus"] or "",\r\n'
    '             "n_chunks": int(r["n_chunks"] or 0)} for r in rows]\r\n'
)

A_NEW = (
    "# ⚠ 写库禁止：只写 `logs/knowledge/<doc_id>-预习总结.json`，五张表仍然一行都不碰。\r\n"
    "#\r\n"
    "# ---------------- 资料范围筛选 + 章数批量统计（卡 C-0101） ----------------\r\n"
    "#\r\n"
    "# 人的原话（2026-10-14）：「**我的要求是使用入库的教材进行预习，不是用入库的\r\n"
    "# 所有课件来预习**。」改前这里是 `SELECT id, title, kind, corpus, n_chunks FROM\r\n"
    "# documents ORDER BY id`——**全表返回、零筛选**：真库实测 38 份资料全被列出来，\r\n"
    "# 其中 36 份是本学科课件（role=courseware：pdf 21 / pptx 12 / docx 3），而课件\r\n"
    "# **根本没有「章」**——章结构来自 `chunks.heading_path`，那是「教材入库（专线）」\r\n"
    "# 按章切块的产物（实测课件 doc#55/56/73 都是 0 章）。人从列表里挑一份课件，\r\n"
    "# 只会得到一张空章表。\r\n"
    "#\r\n"
    "# 口径与 `/api/kp-textbook` 的安全闸（`core/teach.py::resolve_textbook_pdf`，卡\r\n"
    "# C-0093）**同一份**：那边放行 `role='textbook'` 且 `kind='pdf'`；这里默认只看\r\n"
    "# `role='textbook'`（`role` 缺失的老库按 `COALESCE(role,'courseware')` 兜底，\r\n"
    "# 与 `teach.textbook_docs()` 是同一条兜底）。要看全库（排查用）必须**显式**传\r\n"
    "# `scope=all`——默认值永远是「仅教材」。\r\n"
    'PREVIEW_SCOPE_TEXTBOOK = "textbook"\r\n'
    'PREVIEW_SCOPE_ALL = "all"\r\n'
    "# 一条 SQL 里 `IN (...)` 的参数个数上限保护（真库 38 份，留足余量）\r\n"
    "_PREVIEW_ID_BATCH = 400\r\n"
    "\r\n"
    "\r\n"
    "def _preview_doc_scope(scope) -> str:\r\n"
    '    """资料范围归一：只认 textbook / all，其余（空值、乱填）一律**回默认的仅教材**。\r\n'
    "\r\n"
    "    这是故意的：范围筛选往「少列资料」的方向收是安全的，任何解析不出来的\r\n"
    "    输入都不许把它放开成全库。\r\n"
    '    """\r\n'
    "    if isinstance(scope, (list, tuple)):\r\n"
    "        scope = (scope[0] if scope else \"\")\r\n"
    '    s = str(scope or "").strip().lower()\r\n'
    '    if s in (PREVIEW_SCOPE_ALL, "全部", "全部资料", "所有", "1", "true"):\r\n'
    "        return PREVIEW_SCOPE_ALL\r\n"
    "    return PREVIEW_SCOPE_TEXTBOOK\r\n"
    "\r\n"
    "\r\n"
    "def _chapter_count_map(conn, doc_ids) -> dict:\r\n"
    '    """批量算每份资料的**章数**（口径与 `core.preview.list_chapters` **完全一致**）。\r\n'
    "\r\n"
    "    为什么批量、而不是对每份资料当场调 `preview_mod.list_chapters()`：实测\r\n"
    "    （真库，`tools/c0101_probe_cost.py`）**逐份 38 份 = 12.2 秒**——那个函数每份\r\n"
    "    都要新开一条连接、把自己那份 chunks 全读出来，还要读《章节细目》产物 JSON；\r\n"
    "    而这里「一条 SQL 取 `(doc_id, source_ref, heading_path)` + 同一个正则归章」\r\n"
    "    实测 **0.14 秒**（同机同数据）。\r\n"
    "\r\n"
    "    归类判据**复用** `preview_mod._CH_RE`（`> 第N章 …`）与 `kp_build_mod._parse_ref`\r\n"
    "    ——不抄第二份规则，所以这里给出的章数，永远等于「点进这一份看到的章列表\r\n"
    "    长度」。没块 / 认不出章的文档如实回 **0**（前端据此显示「无章可预习」，\r\n"
    "    不假装有章）。\r\n"
    '    """\r\n'
    "    ids: list = []\r\n"
    "    for x in (doc_ids or []):\r\n"
    "        try:\r\n"
    "            v = int(x)\r\n"
    "        except (TypeError, ValueError):\r\n"
    "            continue\r\n"
    "        if v > 0 and v not in ids:\r\n"
    "            ids.append(v)\r\n"
    "    out: dict = {i: 0 for i in ids}\r\n"
    "    if not ids:\r\n"
    "        return out\r\n"
    "    seen: dict = {}\r\n"
    "    for i in range(0, len(ids), _PREVIEW_ID_BATCH):\r\n"
    "        batch = ids[i:i + _PREVIEW_ID_BATCH]\r\n"
    "        try:\r\n"
    "            rows = conn.execute(\r\n"
    '                "SELECT doc_id, source_ref, heading_path FROM chunks "\r\n'
    '                "WHERE doc_id IN (%s)" % ",".join("?" * len(batch)),\r\n'
    "                batch).fetchall()\r\n"
    "        except sqlite3.OperationalError:\r\n"
    "            # 老库没有 heading_path 这一列（或库还没建全）：如实回 0，不把面板打崩\r\n"
    "            return out\r\n"
    "        for r in rows:\r\n"
    "            # 用**下标**取列：`sqlite3.Row` 与默认 tuple 都能吃，不依赖 row_factory\r\n"
    '            hp = str(r[2] or "")\r\n'
    "            m = preview_mod._CH_RE.search(hp)\r\n"
    "            if not m:\r\n"
    "                continue\r\n"
    '            if kp_build_mod._parse_ref(str(r[1] or "")) is None:\r\n'
    "                continue\r\n"
    "            try:\r\n"
    "                did = int(r[0] or 0)\r\n"
    "                no = int(m.group(1))\r\n"
    "            except (TypeError, ValueError):\r\n"
    "                continue\r\n"
    "            seen.setdefault(did, set()).add(no)\r\n"
    "    for did, nos in seen.items():\r\n"
    "        if did in out:\r\n"
    "            out[did] = len(nos)\r\n"
    "    return out\r\n"
    "\r\n"
    "\r\n"
    "def _preview_doc_entry(row, nch_map) -> dict:\r\n"
    '    """一行 `documents` → 面板要的资料条目（`role` 与 `n_chapters` 都摆在明面上）。\r\n'
    "\r\n"
    "    卡面要求「返回的每份资料里带上 role 字段，别把筛选藏在别处」——所以\r\n"
    "    `role` / `is_textbook` / `n_chapters` / `previewable` 全在这里显式给出，\r\n"
    "    前端不必再去猜哪一份能预习。\r\n"
    '    """\r\n'
    "    did = int(row[0] or 0)\r\n"
    '    role = str(row[3] or "")\r\n'
    "    n_ch = int((nch_map or {}).get(did, 0) or 0)\r\n"
    "    return {\r\n"
    '        "doc_id": did,\r\n'
    '        "title": row[1] or ("文档 %d" % did),\r\n'
    '        "kind": row[2] or "",\r\n'
    '        "role": role,\r\n'
    '        "corpus": str(row[4] or ""),\r\n'
    '        "n_chunks": int(row[5] or 0),\r\n'
    "        # ★ 真实章数：0 就是 0（前端据此显示「无章可预习」），不猜、不藏\r\n"
    '        "n_chapters": n_ch,\r\n'
    '        "is_textbook": role == "textbook",\r\n'
    '        "previewable": n_ch > 0,\r\n'
    "    }\r\n"
    "\r\n"
    "\r\n"
    "# 资料条目要查的六列（主查询与「补一份范围外的」共用，避免两处抄出漂移）\r\n"
    "_PREVIEW_DOC_COLS = (\r\n"
    '    "SELECT id, title, kind, COALESCE(role,\'courseware\') AS role, "\r\n'
    '    "COALESCE(corpus,\'main\') AS corpus, COALESCE(n_chunks,0) AS n_chunks "\r\n'
    '    "FROM documents ")\r\n'
    "\r\n"
    "\r\n"
    "def api_preview_options(conn, doc_id: int = 0, scope: str = \"\") -> dict:\r\n"
    '    """预习面板的清单：可预习的资料 + 选中资料的**章**（每章带节数与产物状态）。\r\n'
    "\r\n"
    "    只读、**零模型调用**。章节来自 `chunks.heading_path`（入库结构），\r\n"
    "    节数来自卡 C-0092 的《章节细目》正则产物。\r\n"
    "\r\n"
    "    ★ 卡 C-0101：`scope` 默认 `textbook` —— 清单**只列入库的教材**\r\n"
    "      （`role='textbook'`），因为预习按章做、而章只有教材有（实测 doc#91=42 章，\r\n"
    "      课件 doc#55/56/73=0 章）。要看全库排查就**显式**传 `scope=all`。\r\n"
    "      每份资料都带 `role` 与 `n_chapters`，筛选**不藏在别处**。\r\n"
    '    """\r\n'
    "    sc = _preview_doc_scope(scope)\r\n"
    "    where = (\"WHERE COALESCE(role,'courseware')='textbook' \"\r\n"
    "             if sc == PREVIEW_SCOPE_TEXTBOOK else \"\")\r\n"
    "    rows = conn.execute(_PREVIEW_DOC_COLS + where + \"ORDER BY id\").fetchall()\r\n"
    '    nch = _chapter_count_map(conn, [int(r[0]) for r in rows])\r\n'
    "    docs = [_preview_doc_entry(r, nch) for r in rows]\r\n"
    "    # 显式点了某一份、但它不在当前范围里（例如从「全部资料」切回「仅教材」时\r\n"
    "    # 手上还停在一份课件上）：**补进清单并如实标注**。否则下拉框会丢掉选中项，\r\n"
    "    # 而下面的章表还停在那一份上，两边说的不是同一件事。\r\n"
    "    want = int(doc_id or 0)\r\n"
    "    if want > 0 and not any(d[\"doc_id\"] == want for d in docs):\r\n"
    "        one = conn.execute(_PREVIEW_DOC_COLS + \"WHERE id=?\", (want,)).fetchone()\r\n"
    "        if one is not None:\r\n"
    "            d = _preview_doc_entry(one, _chapter_count_map(conn, [want]))\r\n"
    '            d["out_of_scope"] = True\r\n'
    "            docs.append(d)\r\n"
    "    try:\r\n"
    "        n_total = int(conn.execute(\"SELECT count(*) FROM documents\").fetchone()[0] or 0)\r\n"
    "    except (sqlite3.OperationalError, TypeError, IndexError):\r\n"
    "        n_total = len(docs)\r\n"
    "    # 范围内的教材份数（不含被补进来的范围外那一份）：空态文案要靠它说准话\r\n"
    "    n_tb = len([d for d in docs if d[\"is_textbook\"] and not d.get(\"out_of_scope\")])\r\n"
)

# ---------------------------------------------------------------- A2 out 字段
A2_OLD = (
    '        "preview_version": preview_mod.PREVIEW_VERSION,\r\n'
    '        "docs": docs, "doc_id": int(doc_id or 0), "chapters": [],\r\n'
)

A2_NEW = (
    '        "preview_version": preview_mod.PREVIEW_VERSION,\r\n'
    "        # ★ 卡 C-0101：范围与计数都摆在响应里，前端不必自己猜\r\n"
    '        "scope": sc, "scope_default": PREVIEW_SCOPE_TEXTBOOK,\r\n'
    '        "n_textbooks": n_tb, "n_docs_total": n_total,\r\n'
    '        "docs": docs, "doc_id": int(doc_id or 0), "chapters": [],\r\n'
)

# ---------------------------------------------------------------- B 路由
B_OLD = (
    "                    # 预习面板的清单（卡 C-0094）：列可预习的资料 + 选中资料的章\r\n"
    "                    # （每章带节数与产物状态）。**零模型调用**，与整理面板同一份文档清单。\r\n"
    '                    if path == "/api/preview/options":\r\n'
    "                        return self._json(api_preview_options(\r\n"
    '                            conn, _int_arg(qs.get("doc_id"))))\r\n'
)

B_NEW = (
    "                    # 预习面板的清单（卡 C-0094）：列可预习的资料 + 选中资料的章\r\n"
    "                    # （每章带节数与产物状态）。**零模型调用**。\r\n"
    "                    # ★ 卡 C-0101：默认**只列教材**（`role='textbook'`）——预习按章\r\n"
    "                    #   做，而章只有教材有（课件实测 0 章）。要看全库排查就显式带\r\n"
    "                    #   `scope=all`；乱填/不传一律回默认的「仅教材」。\r\n"
    '                    if path == "/api/preview/options":\r\n'
    "                        return self._json(api_preview_options(\r\n"
    '                            conn, _int_arg(qs.get("doc_id")),\r\n'
    '                            qs.get("scope", "")))\r\n'
)

# ---------------------------------------------------------------- C PVP + pvLoad
C_OLD = (
    "const PVP = { opts: null, docId: 0, chNo: 0, est: null, force: false, timer: null };\r\n"
)

C_NEW = (
    "// ★ 卡 C-0101：`scope` 是**资料范围**，默认 `'textbook'`（仅教材）。\r\n"
    "// 预习按章做，而章只有教材有（`chunks.heading_path` 是「教材入库（专线）」\r\n"
    "// 按章切块的产物）；`'all'` 只给排查用，且必须**人自己切**过去。\r\n"
    "const PVP = { opts: null, docId: 0, chNo: 0, est: null, force: false, timer: null,\r\n"
    "              scope: 'textbook' };\r\n"
)

C2_OLD = (
    "  let o = null;\r\n"
    "  try {\r\n"
    "    o = await api('/api/preview/options' +\r\n"
    "                  (docId ? ('?doc_id=' + encodeURIComponent(docId)) : ''));\r\n"
    "  } catch(e){\r\n"
)

C2_NEW = (
    "  const scope = (PVP.scope === 'all') ? 'all' : 'textbook';\r\n"
    "  let o = null;\r\n"
    "  try {\r\n"
    "    o = await api('/api/preview/options?scope=' + encodeURIComponent(scope) +\r\n"
    "                  (docId ? ('&doc_id=' + encodeURIComponent(docId)) : ''));\r\n"
    "  } catch(e){\r\n"
)

# ---------------------------------------------------------------- D 新函数
D_OLD = (
    "// 「去设置中心」：设置中心是右上角 ⚙（卡 C-0059 的独立容器），预习开关在它的\r\n"
    "// 「📖 AI 预习总结」分区里。`SET_TAB` 是设置中心的当前分区变量，指过去就能直达。\r\n"
    "function pvOpenSettings(){\r\n"
    "  closeModal();\r\n"
    "  try { SET_TAB = 'preview'; } catch(e){ /* 设置中心读不到配置时会自己退回首屏 */ }\r\n"
    "  openSettings();\r\n"
    "}\r\n"
)

D_NEW = (
    "// 「去设置中心」：设置中心是右上角 ⚙（卡 C-0059 的独立容器），预习开关在它的\r\n"
    "// 「📖 AI 预习总结」分区里。`SET_TAB` 是设置中心的当前分区变量，指过去就能直达。\r\n"
    "function pvOpenSettings(){\r\n"
    "  closeModal();\r\n"
    "  try { SET_TAB = 'preview'; } catch(e){ /* 设置中心读不到配置时会自己退回首屏 */ }\r\n"
    "  openSettings();\r\n"
    "}\r\n"
    "\r\n"
    "// ★ 卡 C-0101：资料范围切换（默认「仅教材」）。切了就重拉清单——\r\n"
    "// 当前选中的那一份若不在新范围里，后端会把它补进清单并标「不在当前范围」，\r\n"
    "// 所以下拉框不会丢选中项，章表也不会和下拉框说的不是同一件事。\r\n"
    "function pvSetScope(s){\r\n"
    "  PVP.scope = (s === 'all') ? 'all' : 'textbook';\r\n"
    "  pvLoad(PVP.docId || 0);\r\n"
    "}\r\n"
    "\r\n"
    "// ★ 卡 C-0101 空态的路：当前学科库一份教材都没有时，把人送到**能教材入库**\r\n"
    "// 的地方（「资料」页里的「📖 教材入库（专线）」，`docsDlgTextbook()`）。\r\n"
    "function pvGoDocs(){ closeModal(); go('docs'); }\r\n"
    "\r\n"
    "// ★ 卡 C-0101：每份资料把 **role 与真实章数** 摆在明面上——\r\n"
    "// 「教材 · 42 章」能预习，「courseware · 无章可预习」一眼就知道别选它。\r\n"
    "function pvDocLabel(d){\r\n"
    "  const role = (d.role === 'textbook') ? '教材'\r\n"
    "    : (d.role ? esc(d.role) : '资料');\r\n"
    "  const ch = (d.n_chapters > 0) ? (d.n_chapters + ' 章') : '无章可预习';\r\n"
    "  return `${esc(d.title)} · ${role} · ${ch} · ${d.n_chunks} 块${\r\n"
    "    d.kind ? ' · ' + esc(d.kind) : ''}${d.out_of_scope ? ' · 不在当前范围' : ''}`;\r\n"
    "}\r\n"
)

# ---------------------------------------------------------------- E pvRender 列表
E_OLD = (
    "  const docs = o.docs || [], chs = o.chapters || [];\r\n"
    "  const running = !!(o.running || (o.progress && o.progress.active));\r\n"
    "  const sel = docs.map(d => `<option value=\"${d.doc_id}\"${\r\n"
    "    d.doc_id === PVP.docId ? ' selected' : ''}>${esc(d.title)} · ${d.n_chunks} 块${\r\n"
    "    d.kind ? ' · ' + esc(d.kind) : ''}</option>`).join('');\r\n"
)

E_NEW = (
    "  const docs = o.docs || [], chs = o.chapters || [];\r\n"
    "  const running = !!(o.running || (o.progress && o.progress.active));\r\n"
    "  const scope = (PVP.scope === 'all') ? 'all' : 'textbook';\r\n"
    "  const sel = docs.map(d => `<option value=\"${d.doc_id}\"${\r\n"
    "    d.doc_id === PVP.docId ? ' selected' : ''}>${pvDocLabel(d)}</option>`).join('');\r\n"
    "  // ★ 卡 C-0101 空态：**默认「仅教材」下一份教材都没有**——这时绝不能只留一个空\r\n"
    "  //   白下拉框。把「为什么空」（预习按章做、而章只有教材有）和「两条路」\r\n"
    "  //   （教材专线入库 / 换学科库）直接摆出来，并给两个能点的去处。\r\n"
    "  const noTb = (scope === 'textbook') && !docs.length;\r\n"
    "  const popEmpty = !Number(o.n_docs_total || 0) && !docs.length;\r\n"
    "  const emptyBox = noTb ? `<div style=\"margin:2px 0 12px;padding:12px 14px;\r\n"
    "      border:1px solid var(--warn);border-radius:var(--radius)\">\r\n"
    "    <div><span class=\"badge warn\">没有教材</span>\r\n"
    "      <span class=\"small\">当前学科库里<b>一份入库的教材都没有</b>\r\n"
    "      （role=textbook）——预习是<b>按章</b>做的，而<b>章只有教材有</b>\r\n"
    "      （章结构是「教材入库（专线）」按章切块时写进库的），课件没有章可预习。</span></div>\r\n"
    "    <div class=\"small muted\" style=\"margin:8px 0;line-height:1.8\">${\r\n"
    "      popEmpty ? '这个学科库里现在<b>一份资料都没有</b>。'\r\n"
    "      : `库里现有 <b>${Number(o.n_docs_total || 0)}</b> 份资料，但<b>没有一份是教材</b>。`}</div>\r\n"
    "    <div class=\"small muted\" style=\"margin:8px 0;line-height:1.8\">两条路：<br>\r\n"
    "      ① 去「资料」页，用 <b>「📖 教材入库（专线）」</b>把教材 PDF 入库\r\n"
    "        （按章切块，跑完就有章可预习）；<br>\r\n"
    "      ② 教材在<b>别的学科库</b>里？先用右上角的学科库切换过去。</div>\r\n"
    "    <div class=\"row\" style=\"gap:8px;flex-wrap:wrap\">\r\n"
    "      <button class=\"act\" onclick=\"pvGoDocs()\">去「资料」页 → 教材入库（专线）</button>\r\n"
    "      <button class=\"ghost\" onclick=\"pvSetScope('all')\"\r\n"
    "        title=\"看看库里到底有哪些资料（课件会如实标成「无章可预习」）\">\r\n"
    "        看看全部资料（${Number(o.n_docs_total || 0)} 份）</button>\r\n"
    "    </div>\r\n"
    "  </div>` : '';\r\n"
)

# ---------------------------------------------------------------- F 章表空分支
F_OLD = (
    "  let chTable = '';\r\n"
    "  if (!PVP.docId){\r\n"
    "    chTable = `<div class=\"small muted\" style=\"padding:10px 12px\">\r\n"
    "      先在上面选一份资料——预习是<b>按章</b>做的。</div>`;\r\n"
    "  } else if (o.error){\r\n"
    "    chTable = `<div class=\"small muted\" style=\"padding:10px 12px\">\r\n"
    "      这份资料列不出章：${esc(o.error)}</div>`;\r\n"
    "  } else if (!chs.length){\r\n"
    "    chTable = `<div class=\"small muted\" style=\"padding:10px 12px\">\r\n"
    "      这份资料在库里没有「第 N 章」这样的章节结构，预习按章做，所以这里没得选；\r\n"
    "      换一份带章节的教材/课件试试。</div>`;\r\n"
    "  } else {\r\n"
)

F_NEW = (
    "  let chTable = '';\r\n"
    "  if (noTb){\r\n"
    "    // 空态已经由上面的 emptyBox 说清楚了：这里**什么都不画**，\r\n"
    "    // 免得人看到的是一个带边框的空框（卡面明确不要空白框）。\r\n"
    "    chTable = '';\r\n"
    "  } else if (!PVP.docId){\r\n"
    "    chTable = `<div class=\"small muted\" style=\"padding:10px 12px\">\r\n"
    "      先在上面选一份教材——预习是<b>按章</b>做的。</div>`;\r\n"
    "  } else if (o.error){\r\n"
    "    chTable = `<div class=\"small muted\" style=\"padding:10px 12px\">\r\n"
    "      这份资料列不出章：${esc(o.error)}</div>`;\r\n"
    "  } else if (!chs.length){\r\n"
    "    chTable = `<div class=\"small muted\" style=\"padding:10px 12px\">\r\n"
    "      这份资料在库里没有「第 N 章」这样的章节结构，预习按章做，所以这里没得选。${\r\n"
    "      scope === 'all'\r\n"
    "        ? '课件本来就没有章（章是「教材入库（专线）」按章切块时才写进库的）；'\r\n"
    "          + '换一份教材，或把「资料范围」切回「仅教材」。'\r\n"
    "        : '若这份教材是用普通「入库」进来的，请用「教材入库（专线）」重入一次。'}</div>`;\r\n"
    "  } else {\r\n"
)

# ---------------------------------------------------------------- G 下拉行 + 范围切换
G_OLD = (
    "  body.innerHTML = `\r\n"
    "    ${offBox}\r\n"
    "    <div class=\"row\" style=\"gap:8px;margin-bottom:8px\">\r\n"
    "      <select id=\"pv-doc\" style=\"flex:1;min-width:260px\"\r\n"
    "        title=\"只列库里的资料；列不出章的资料说明入库时没有章节结构\">\r\n"
    "        <option value=\"\">— 选一份资料 —</option>${sel}</select>\r\n"
    "      <span class=\"small muted\">${PVP.docId && o.title\r\n"
    "        ? esc(o.title) + (o.n_chapters ? ` · 共 ${o.n_chapters} 章` : '')\r\n"
    "        : '预习按章做：一份资料 → 一章'}</span>\r\n"
    "    </div>\r\n"
    "    <div id=\"pv-chwrap\" style=\"max-height:34vh;overflow:auto;\r\n"
    "      border:1px solid #2a2f3a;border-radius:8px\">${chTable}</div>\r\n"
)

G_NEW = (
    "  // ★ 卡 C-0101：资料**范围**切换。默认「仅教材」——预习题板只列入库的教材；\r\n"
    "  //   「全部资料」是排查用的，切过去才能看到那 36 份课件（并如实标成「无章可预习」）。\r\n"
    "  const scopeBox = `<div class=\"row\" style=\"gap:8px;margin-bottom:8px;flex-wrap:wrap\">\r\n"
    "      <span class=\"small muted\">资料范围</span>\r\n"
    "      <select id=\"pv-scope\" style=\"min-width:170px\"\r\n"
    "        title=\"预习按章做，而章只有教材有：默认只看入库的教材（role=textbook）\">\r\n"
    "        <option value=\"textbook\"${scope === 'textbook' ? ' selected' : ''}>仅教材（推荐）</option>\r\n"
    "        <option value=\"all\"${scope === 'all' ? ' selected' : ''}>全部资料（排查用）</option>\r\n"
    "      </select>\r\n"
    "      <span class=\"small muted\">${scope === 'textbook'\r\n"
    "        ? `教材 ${docs.filter(d => d.is_textbook && !d.out_of_scope).length} 份${\r\n"
    "            Number(o.n_docs_total || 0)\r\n"
    "              ? `（库里共 ${Number(o.n_docs_total || 0)} 份资料）` : ''}`\r\n"
    "        : `全部 ${docs.length} 份资料 —— 课件没有章结构，会标成「无章可预习」`}</span>\r\n"
    "    </div>`;\r\n"
    "  // 空态下不画章表容器：留一个带边框的空框等于什么都没说\r\n"
    "  const chWrap = noTb ? '' : `<div id=\"pv-chwrap\" style=\"max-height:34vh;overflow:auto;\r\n"
    "      border:1px solid #2a2f3a;border-radius:8px\">${chTable}</div>`;\r\n"
    "  body.innerHTML = `\r\n"
    "    ${offBox}\r\n"
    "    ${emptyBox}\r\n"
    "    ${scopeBox}\r\n"
    "    <div class=\"row\" style=\"gap:8px;margin-bottom:8px\">\r\n"
    "      <select id=\"pv-doc\" style=\"flex:1;min-width:260px\"\r\n"
    "        title=\"只列当前范围内的资料；每项都标了 role 与真实章数，写着「无章可预习」的选了也没有章\">\r\n"
    "        <option value=\"\">— 选一份教材 —</option>${sel}</select>\r\n"
    "      <span class=\"small muted\">${PVP.docId && o.title\r\n"
    "        ? esc(o.title) + (o.n_chapters ? ` · 共 ${o.n_chapters} 章` : ' · 无章可预习')\r\n"
    "        : '预习按章做：一份教材 → 一章'}</span>\r\n"
    "    </div>\r\n"
    "    ${chWrap}\r\n"
)

# ---------------------------------------------------------------- H 绑定 scope
H_OLD = (
    "  const dsel = $('#pv-doc');\r\n"
    "  if (dsel) dsel.onchange = () => pvLoad(parseInt(dsel.value || '0', 10) || 0);\r\n"
)

H_NEW = (
    "  const ssel = $('#pv-scope');\r\n"
    "  if (ssel) ssel.onchange = () => pvSetScope(ssel.value);\r\n"
    "  const dsel = $('#pv-doc');\r\n"
    "  if (dsel) dsel.onchange = () => pvLoad(parseInt(dsel.value || '0', 10) || 0);\r\n"
)

# ---------------------------------------------------------------- I pvStart toast
I_OLD = "  if (!PVP.docId){ toast('先选一份资料'); return; }\r\n"
I_NEW = "  if (!PVP.docId){ toast('先选一份教材'); return; }\r\n"

PATCHES = [
    ("A  api_preview_options + 范围/章数 helper", A_OLD, A_NEW),
    ("A2 响应里的 scope / n_textbooks / n_docs_total", A2_OLD, A2_NEW),
    ("B  路由透传 scope", B_OLD, B_NEW),
    ("C  PVP.scope 状态", C_OLD, C_NEW),
    ("C2 pvLoad 带上 scope", C2_OLD, C2_NEW),
    ("D  pvSetScope / pvGoDocs / pvDocLabel", D_OLD, D_NEW),
    ("E  pvRender 列表渲染 + 空态", E_OLD, E_NEW),
    ("F  章表空分支", F_OLD, F_NEW),
    ("G  下拉行 + 范围切换行", G_OLD, G_NEW),
    ("H  绑定范围切换", H_OLD, H_NEW),
    ("I  pvStart 提示语", I_OLD, I_NEW),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报告是否已应用，不改文件")
    a = ap.parse_args()

    raw = TARGET.read_bytes()
    src = raw.decode("utf-8")
    crlf_before = src.count("\r\n")
    bare_before = src.count("\n") - crlf_before
    print("目标：%s" % TARGET)
    print("改前：%d 字节 | CRLF %d | 裸 LF %d" % (len(raw), crlf_before, bare_before))

    already = [name for name, _o, new in PATCHES if new in src]
    if a.check:
        print("已应用 %d/%d 处" % (len(already), len(PATCHES)))
        for name in already:
            print("  [已应用] %s" % name)
        return 0

    if already:
        print("！这些替换已经存在，说明本补丁已应用过（或部分应用）：")
        for name in already:
            print("  - %s" % name)
        if len(already) == len(PATCHES):
            print("C0101-PATCH-SKIP 全部已应用，不重复改")
            return 0
        print("C0101-PATCH-FAIL 部分已应用，请人工核对后再决定")
        return 1

    out = src
    for name, old, new in PATCHES:
        n = out.count(old)
        if n != 1:
            print("C0101-PATCH-FAIL %s：锚点出现 %d 次（要求恰好 1 次）" % (name, n))
            return 1
        out = out.replace(old, new, 1)
        print("  [改] %s" % name)

    # 换行符复核：补丁文本全部用 \r\n 写，替换不该改变总数以外的任何东西
    crlf_after = out.count("\r\n")
    bare_after = out.count("\n") - crlf_after
    if bare_after != 0:
        print("C0101-PATCH-FAIL 出现裸 LF %d 个（必须为 0）" % bare_after)
        return 1
    if out.count("\r") != crlf_after:
        print("C0101-PATCH-FAIL 出现孤立 CR")
        return 1

    # 备份（临时实例之外不留任何东西；这里只是本卡自己的回滚点）
    bak = TARGET.with_suffix(".py.c0101-bak")
    if not bak.exists():
        shutil.copyfile(TARGET, bak)
        print("回滚点：%s" % bak)

    TARGET.write_bytes(out.encode("utf-8"))
    print("改后：%d 字节 | CRLF %d | 裸 LF %d" % (len(out.encode("utf-8")),
                                                crlf_after, bare_after))
    print("C0101-PATCH-OK 共 %d 处" % len(PATCHES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
