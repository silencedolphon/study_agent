# -*- coding: utf-8 -*-
"""卡 C-0101 验收脚本：预习面板的资料范围**真的**只列教材（role=textbook）。

它在验什么（全部对着**真库只读**、零模型调用、零写盘）：
  1. 不传 scope（=面板打开时的默认请求）→ 返回的每一份资料 `role` 都是 `textbook`，
     且份数 == 库里 `role='textbook'` 的份数（真库实测 38 份里只有 2 份）；
  2. **不是空筛选**：库里确实存在 courseware，且默认返回与它们**零交集**
     （防"库里本来就没课件所以看起来通过"的假阳性）；
  3. `scope=all` → 份数回到 `documents` 全表（真库 38），且两类都在；
  4. 乱填的 scope（`scope=xyz`）**回落**到仅教材，不放开；
  5. 每份资料都带 `n_chapters`，且**逐个等于** `core.preview.list_chapters()` 的
     章数口径（即"列表里写的章数"与"点进去看到的章列表长度"永远不会不一致）；
     doc#91 实测 42 章、doc#102 实测 0 章会原样打印；
  6. 范围外但被显式点中的那一份会被补进清单并标 `out_of_scope`（下拉框不丢选中项）；
  7. 源码级断言：筛选写在后端查询里（不是前端藏）、路由透传 `scope`、
     前端有范围切换与空态引导、`dashboard.py` 裸 LF 为 0（CRLF 保持）。

用法（**绝对路径，不依赖 cwd**）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0101_verify.py
输出第 1 行固定是 `C0101-PASS` 或 `C0101-FAIL`；退出码 0 = 全部断言通过。
"""
from __future__ import annotations

import importlib.util
import io
import sqlite3
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"G:\study_agent")
DASH = ROOT / "tools" / "dashboard.py"
MAX_CONSISTENCY_DOCS = 5      # 逐份核对章数口径的份数上限（真库只有 2 份教材）


def load_dashboard():
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("c0101_dash", str(DASH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    t0 = time.time()
    fails: list = []
    notes: list = []

    def check(ok, label, detail=""):
        if not ok:
            fails.append(label + ((" | " + detail) if detail else ""))
        return bool(ok)

    dash = load_dashboard()
    from core import config, preview as preview_mod  # noqa: E402

    db = Path(config.DB_PATH)
    conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    conn.row_factory = sqlite3.Row

    n_all = int(conn.execute("SELECT count(*) FROM documents").fetchone()[0] or 0)
    tb_ids = [int(r[0]) for r in conn.execute(
        "SELECT id FROM documents WHERE COALESCE(role,'courseware')='textbook' "
        "ORDER BY id")]
    cw_ids = [int(r[0]) for r in conn.execute(
        "SELECT id FROM documents WHERE COALESCE(role,'courseware')<>'textbook' "
        "ORDER BY id")]

    # ---------- 1 默认：只列教材 ----------
    d = dash.api_preview_options(conn)
    d_docs = d.get("docs") or []
    d_ids = [int(x["doc_id"]) for x in d_docs]
    roles = sorted({str(x.get("role") or "") for x in d_docs})
    check(d.get("scope") == "textbook", "默认 scope 不是 textbook",
          repr(d.get("scope")))
    check(d.get("scope_default") == "textbook", "scope_default 不是 textbook")
    check(bool(d_docs), "默认范围下一份资料都没有（这个库没有教材）")
    check(roles == ["textbook"] if roles else False,
          "默认返回里出现了非教材资料", "roles=%s" % roles)
    check(sorted(d_ids) == sorted(tb_ids), "默认返回与库里 textbook 清单不一致",
          "api=%s db=%s" % (sorted(d_ids), sorted(tb_ids)))
    check(len(d_docs) < n_all, "默认返回没有比全表少（筛选没生效）",
          "docs=%d all=%d" % (len(d_docs), n_all))
    check(bool(cw_ids) and not (set(d_ids) & set(cw_ids)),
          "默认返回里混进了课件", "courseware=%d" % len(cw_ids))
    check(all(isinstance(x.get("n_chapters"), int) for x in d_docs),
          "有资料缺 n_chapters 字段")
    check(d.get("n_textbooks") == len(tb_ids), "n_textbooks 与实际不符",
          "%s vs %d" % (d.get("n_textbooks"), len(tb_ids)))
    check(d.get("n_docs_total") == n_all, "n_docs_total 与实际不符",
          "%s vs %d" % (d.get("n_docs_total"), n_all))

    # ---------- 2 scope=all：回到全表 ----------
    a = dash.api_preview_options(conn, 0, "all")
    a_ids = [int(x["doc_id"]) for x in (a.get("docs") or [])]
    a_roles = sorted({str(x.get("role") or "") for x in (a.get("docs") or [])})
    check(a.get("scope") == "all", "scope=all 没被认出来", repr(a.get("scope")))
    check(len(a_ids) == n_all, "scope=all 的份数没有回到全表",
          "docs=%d all=%d" % (len(a_ids), n_all))
    check(sorted(a_ids) == sorted(tb_ids + cw_ids), "scope=all 与全表 doc_id 不一致")
    check(len(a_roles) > 1, "scope=all 里只有一类资料（应含教材与课件）",
          "roles=%s" % a_roles)

    # ---------- 3 乱填的 scope 必须回落，不许放开 ----------
    for bad in ("xyz", "", None, "ALLS", "全部课件"):
        r = dash.api_preview_options(conn, 0, bad)
        check(r.get("scope") == "textbook",
              "非法 scope=%r 没有回落到仅教材" % (bad,), repr(r.get("scope")))

    # ---------- 4 章数口径：与 core.preview.list_chapters 逐个一致 ----------
    consist = []
    for x in d_docs[:MAX_CONSISTENCY_DOCS]:
        did = int(x["doc_id"])
        rep = preview_mod.list_chapters(did)
        real = int(rep.get("n_chapters") or 0) if rep.get("ok") else 0
        consist.append((did, int(x["n_chapters"]), real))
        check(int(x["n_chapters"]) == real,
              "doc#%d 的 n_chapters 与 list_chapters 口径不一致" % did,
              "api=%s pipeline=%s" % (x["n_chapters"], real))
    check(any(c[1] > 0 for c in consist), "默认列表里没有一份资料有章可预习",
          str(consist))
    by_id = {int(x["doc_id"]): x for x in d_docs}
    doc91 = by_id.get(91)
    doc102 = by_id.get(102)
    if doc91 is not None:
        check(int(doc91["n_chapters"]) > 0, "doc#91（教材）章数为 0")
    if doc102 is not None:
        notes.append("doc#102 n_chapters=%s（0 就如实显示「无章可预习」）"
                     % doc102["n_chapters"])

    # ---------- 5 范围外被显式点中的那一份：补进来并标注 ----------
    pinned_ok = True
    if cw_ids:
        p = dash.api_preview_options(conn, cw_ids[0])
        pd = {int(x["doc_id"]): x for x in (p.get("docs") or [])}
        pinned_ok = (cw_ids[0] in pd) and bool(pd[cw_ids[0]].get("out_of_scope"))
        check(pinned_ok, "显式点了一份课件，它没被补进清单/没标 out_of_scope",
              "doc=%d" % cw_ids[0])
        check(p.get("scope") == "textbook", "补 pinned 时把范围放开了")

    # ---------- 6 源码级断言 ----------
    src = io.open(str(DASH), "r", encoding="utf-8", newline="").read()
    crlf = src.count("\r\n")
    bare_lf = src.count("\n") - crlf
    check(bare_lf == 0, "dashboard.py 出现裸 LF（CRLF 被破坏）", str(bare_lf))
    must = [
        "PREVIEW_SCOPE_TEXTBOOK = \"textbook\"",
        "WHERE COALESCE(role,'courseware')='textbook' ",
        "def _chapter_count_map(conn, doc_ids)",
        "def _preview_doc_entry(row, nch_map)",
        "qs.get(\"scope\", \"\")",
        "function pvSetScope(s)",
        "function pvGoDocs()",
        "function pvDocLabel(d)",
        "教材入库（专线）",
        "仅教材（推荐）",
        "全部资料（排查用）",
        "没有教材",
        "const noDocs = !docs.length;",
        "const chWrap = noDocs ? '' :",
    ]
    for m in must:
        check(m in src, "源码里找不到：%s" % m)
    check("\"SELECT id, title, kind, corpus, n_chunks FROM documents \"" not in src,
          "旧的全表无筛选查询还在")
    check("pvSetScope(ssel.value)" in src, "范围切换没有绑定到 onchange")
    notes.append("courseware %d 份 / textbook %d 份 / 全表 %d 份"
                 % (len(cw_ids), len(tb_ids), n_all))
    notes.append("逐份核对章数：%s" % ", ".join(
        "#%d=%d" % (c[0], c[1]) for c in consist))
    notes.append("doc#91 章数=%s" % (doc91["n_chapters"] if doc91 else "不在库里"))
    notes.append("db=%s" % db)
    notes.append("耗时 %.2fs" % (time.time() - t0))
    conn.close()

    # ---------- 结论（前 6 行内给出判定与硬数据） ----------
    lines = []
    if fails:
        lines.append("C0101-FAIL preview-options textbook-scope assertions failed")
    else:
        lines.append("C0101-PASS preview-options only lists role=textbook textbooks")
    lines.append("default_scope=%s textbooks=%d/%d docs=%d roles=%s"
                 % (d.get("scope"), len(d_docs), n_all, len(d_docs), ",".join(roles)))
    lines.append("scope_all docs=%d (all=%d) roles=%s"
                 % (len(a_ids), n_all, ",".join(a_roles)))
    lines.append("doc91_chapters=%s doc102_chapters=%s"
                 % (doc91["n_chapters"] if doc91 else "-",
                    doc102["n_chapters"] if doc102 else "-"))
    lines.append("chapter_counts_match_pipeline=%s pinned_out_of_scope=%s"
                 % (all(c[1] == c[2] for c in consist), pinned_ok))
    lines.append("src_checks=%s bare_lf=%d"
                 % ("ok" if not fails else "see-below", bare_lf))
    for n in notes:
        lines.append("- note " + n)
    if fails:
        for f in fails:
            lines.append("- FAIL " + f)
    # 每一行都压掉连续空格：大管家要按整串匹配 `C0101-PASS`，任何双空格都可能坏事
    out = "\n".join(" ".join(ln.split()) for ln in lines)
    print(out)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
