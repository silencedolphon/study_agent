# -*- coding: utf-8 -*-
"""卡 C-0052 快速自检：接口 + 笔记文件 + MCP 兼容（不花钱、不起浏览器、十秒内）。

完整验证（真浏览器 + 真调一次 deepseek-flash）是 `tools/verify_c0052_browser.py`；
那一条要跑一分多钟、还会产生 ¥0.02 的模型费用。这一条把**不需要浏览器与模型**
的部分做成一条能随手复核的命令：

  1. 知识点笔记真的落成 notes/*.md，front-matter 带 origin/kp_id/doc_id/source_label；
  2. 按来源反查 `/api/notes/by_kp` 只返回绑定该知识点的那条；
  3. 课件答疑笔记：origin=deck_chat + doc_id，正文含问/答/出处；
  4. 兼容：MCP 的读取路径（`tools/serve_mcp.py` 的 `tools/call kb_note_list` /
     `kb_note_read`）读得到这两条——同一批文件、同一套 front-matter；
  5. 笔记页签用的 `/api/notes` 元数据里带 created 与 source_label；
  6. **检索限定**：`gather_evidence(doc_ids=[该课件])` 的命中全在这份课件里，
     且 `_scope_only()` 会把范围外的证据滤掉（含"全库回退"那一档）；
  7. 跑完把测试笔记全删掉，**不留痕**（notes 目录用临时目录，连真目录都不碰）。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0052_quick.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from core import config, db, study  # noqa: E402

FAILS: list[str] = []
N_OK = 0


def ok(cond, label: str, detail: str = "") -> None:
    global N_OK
    if cond:
        N_OK += 1
        print("  [OK] %s%s" % (label, ("  —— " + str(detail)) if detail else ""))
    else:
        FAILS.append(label + (("  —— " + str(detail)) if detail else ""))
        print("  [★FAIL] %s%s" % (label, ("  —— " + str(detail)) if detail else ""))


def main() -> int:
    # 笔记写在**临时目录**里：自检绝不在真 notes/ 下留东西（改 config 只影响本进程）
    tmp = Path(tempfile.mkdtemp(prefix="c0052_quick_"))
    config.NOTES_DIR = tmp
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "c0052_dash", str(ROOT / "tools" / "dashboard.py"))
    dash = importlib.util.module_from_spec(spec)
    sys.modules["c0052_dash"] = dash
    spec.loader.exec_module(dash)
    import serve_mcp  # noqa: E402

    print("C-0052 快速自检（笔记目录用临时目录：%s）" % tmp)
    conn = db.connect()
    db.init_db(conn)

    print("\n[1] 知识点窗口「记入笔记」")
    r1 = dash.api_note_save_kp(conn, {
        "kp_id": "KP-TEST-1", "kp_name": "自检知识点", "doc_id": 7,
        "body": "自检写入的正文（第一行）\n第二行也要在。",
        "refs": ["p.1–p.3 · doc#7"]})
    p1 = Path(r1.get("path") or "")
    ok(not r1.get("error") and p1.exists() and p1.stat().st_size > 0,
       "笔记真的落成文件", p1.name)
    t1 = p1.read_text(encoding="utf-8") if p1.exists() else ""
    for key, want in (("origin", "knowledge_point"), ("kp_id", "KP-TEST-1"),
                      ("doc_id", "7")):
        ok(("%s: %s" % (key, want)) in t1, "front-matter 有 `%s: %s`" % (key, want))
    ok("source_label:" in t1 and "第二行也要在。" in t1,
       "带人可读来源，且正文多行完整落盘")
    ok(str(r1.get("error") or "") == "", "接口没报错")

    print("\n[2] 空内容被拒绝（不写空文件）")
    bad = dash.api_note_save_kp(conn, {"kp_id": "KP-TEST-1", "body": "   "})
    ok(bool(bad.get("error")), "空正文被拒绝", str(bad.get("error"))[:40])
    bad2 = dash.api_note_save_kp(conn, {"kp_id": "", "body": "x"})
    ok(bool(bad2.get("error")), "缺 kp_id 被拒绝（笔记必须绑定知识点）",
       str(bad2.get("error"))[:40])

    print("\n[3] 「查看笔记」按知识点反查")
    r3 = dash.api_notes_by(kp_id="KP-TEST-1", doc_id=7)
    ok(len(r3.get("notes") or []) == 1 and r3["notes"][0]["file"] == p1.name,
       "只返回绑定这条知识点的笔记", "命中 %d 条" % len(r3.get("notes") or []))
    ok(str(r3["notes"][0].get("content") or "").find("第二行也要在。") >= 0,
       "一次带回正文（小窗里点开不用再跳一次网络）")
    ok(len((dash.api_notes_by(kp_id="KP-NOT-EXIST") or {}).get("notes") or []) == 0,
       "别的知识点查不到它")
    ok(dash.api_notes_by() == {"notes": [], "kp_id": "", "doc_id": 0},
       "不给条件时返回空（不是把全部笔记倒出来）")

    print("\n[4] 同名再记一条 → 不覆盖上一条")
    r4 = dash.api_note_save_kp(conn, {
        "kp_id": "KP-TEST-1", "kp_name": "自检知识点", "doc_id": 7,
        "body": "第二次记的正文。"})
    ok(Path(r4["path"]).name != p1.name and Path(r4["path"]).exists()
       and p1.exists(), "第二条是新文件，第一条还在",
       "%s + %s" % (p1.name, Path(r4["path"]).name))

    print("\n[5] 课件问答「记入笔记」")
    r5 = dash.api_note_save_deck(conn, {
        "doc_id": 7, "doc_title": "自检课件", "question": "这份课件讲了什么？",
        "answer": "分三部分：分类命名、结构构象、制备反应。[1]",
        "refs": ["[1] 自检课件 第1页（doc#7）"]})
    p5 = Path(r5.get("path") or "")
    t5 = p5.read_text(encoding="utf-8") if p5.exists() else ""
    ok(p5.exists() and "origin: deck_chat" in t5 and "doc_id: 7" in t5,
       "来源类型与课件 id 写进 front-matter", p5.name)
    ok("**问**" in t5 and "**答**" in t5 and "## 出处" in t5,
       "正文含问、答、出处三样")
    ok(bool(dash.api_note_save_deck(conn, {"doc_id": 7, "answer": ""}).get("error")),
       "空回答被拒绝")

    print("\n[6] 与 MCP 的 kb_note_* 读同一批文件")
    resp = serve_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "kb_note_list", "arguments": {}}})
    notes = json.loads(resp["result"]["content"][0]["text"])["notes"]
    files = {n["file"] for n in notes}
    ok(p1.name in files and p5.exists() and p5.name in files,
       "MCP 的 kb_note_list 列出了这几条", "MCP 看到 %d 篇" % len(files))
    got = next(n for n in notes if n["file"] == p1.name)
    ok(got.get("kp_id") == "KP-TEST-1" and got.get("origin") == "knowledge_point"
       and int(got.get("doc_id") or 0) == 7,
       "MCP 读到的元数据带着绑定", json.dumps(
           {k: got.get(k) for k in ("kp_id", "doc_id", "origin", "source_label")},
           ensure_ascii=False)[:120])
    resp2 = serve_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "kb_note_read",
                                         "arguments": {"name": p1.name}}})
    body = json.loads(resp2["result"]["content"][0]["text"])
    ok("第二行也要在。" in str(body.get("content") or ""),
       "MCP 的 kb_note_read 读得到正文", str(body.get("title"))[:40])

    print("\n[7] 笔记页签用的元数据（记录时间 + 来源）")
    listed = {n["file"]: n for n in dash.api_notes(conn)["notes"]}
    n1 = listed.get(p1.name) or {}
    ok(float(n1.get("created") or 0) > 1e9, "带记录时间（created，unix 秒）",
       n1.get("created"))
    ok("知识点" in str(n1.get("source_label") or ""), "带人可读来源",
       n1.get("source_label"))
    ok(str(n1.get("kp_id")) == "KP-TEST-1", "带绑定知识点 id")
    ok(n1.get("tags") == ["知识点笔记"], "带 tags（front-matter 的 JSON 数组解析回来）",
       n1.get("tags"))
    ok(n1.get("kind") == "note", "带 kind", n1.get("kind"))
    # 旧笔记（没有 front-matter）必须照常列出——放宽格式不能把老文件读崩
    old_path = tmp / "旧式笔记.md"
    old_path.write_text("# 旧式笔记\n\n没有 front-matter 的老写法。\n", encoding="utf-8")
    o = {n["file"]: n for n in study.list_notes()}.get(old_path.name) or {}
    ok(o.get("title") == "旧式笔记" and o.get("tags") == [] and o.get("origin") == ""
       and int(o.get("doc_id") or 0) == 0,
       "没有 front-matter 的旧笔记照常列出（title 回落文件名、来源为空、不抛）",
       json.dumps({k: o.get(k) for k in ("title", "tags", "origin", "doc_id")},
                  ensure_ascii=False))

    print("\n[8] 检索确实限定在这一份课件")
    from core import chat as chat_mod
    row = conn.execute(
        "SELECT id, title, COALESCE(n_chunks,0) AS n FROM documents "
        "WHERE COALESCE(n_chunks,0)>0 ORDER BY id LIMIT 1").fetchone()
    doc = int(row["id"])
    c0 = conn.execute("SELECT text FROM chunks WHERE doc_id=? ORDER BY id LIMIT 1",
                      (doc,)).fetchone()
    q = (str(c0["text"] or "").strip()[:12] or "内容")
    # expand=False：不调模型（查询改写那一步会花钱），只跑检索
    try:
        ev_all = chat_mod.gather_evidence(conn, q, per_kind=3, doc_ids=[doc],
                                          expand=False)
    except Exception as e:  # noqa: BLE001
        ok(False, "gather_evidence 可调用", "%s: %s" % (type(e).__name__, e))
        ev_all = []
    ev = dash._scope_only(ev_all, doc)
    outside = sorted({int(e.doc_id) for e in ev_all} - {doc})
    ok(bool(ev) and all(int(e.doc_id) == doc for e in ev),
       "doc#%d 的检索证据只留这一份课件" % doc,
       "原始 %d 条 → 范围过滤后 %d 条；doc_id 集合 %s"
       % (len(ev_all), len(ev), sorted({int(e.doc_id) for e in ev})))
    if outside:
        # 实测这一档真的会发生：core/chat.py 在"这份课件里一条都没召回"时
        # 会回退成全库检索（`if not picked:` 那一支）。如实报出来，不粉饰。
        print("  [注意] gather_evidence 这次**回退到了全库**：捞到范围外 %s；"
              "已被 _scope_only 挡掉——这正是它存在的理由。" % outside)
    fake = [SimpleNamespace(doc_id=doc), SimpleNamespace(doc_id=99999),
            SimpleNamespace(doc_id=99998)]
    kept = dash._scope_only(fake, doc)
    ok([e.doc_id for e in kept] == [doc],
       "★_scope_only 把范围外的证据滤掉（挡住 gather_evidence 的全库回退）",
       "%d → %d 条" % (len(fake), len(kept)))

    print("\n[9] 清理：临时目录整棵删掉（真 notes/ 一个字节都没碰）")
    import shutil
    ok(config.NOTES_DIR == tmp, "本次自检全程写在临时目录里", str(tmp))
    shutil.rmtree(tmp, ignore_errors=True)
    ok(not tmp.exists(), "临时目录已删除")
    conn.close()

    print("\n%s" % ("=" * 66))
    if FAILS:
        print("C-0052 快速自检：★失败 %d 项 / 通过 %d 项" % (len(FAILS), N_OK))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("C-0052 快速自检：全部通过（%d 项）" % N_OK)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
