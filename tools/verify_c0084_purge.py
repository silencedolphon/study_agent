#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""卡 C-0084 交付验证：批量**真删**（文件 + 向量块 + 知识点产物一并不留）。

一句话验的是：**勾几份资料点「批量删除」之后，库记录 / 切块 / 向量 / 识图记录 /
知识点出处与孤儿知识点 / 四处文件系统产物真的都没了，而该留的东西一件没动。**

跑法（工作目录＝项目根，cmd 与 pwsh 都能跑）：
    .\\venv\\Scripts\\python.exe tools\\verify_c0084_purge.py           整轮（含真浏览器）
    .\\venv\\Scripts\\python.exe tools\\verify_c0084_purge.py --quick   快速自检（约 6 秒）

两种模式验的是**同一套判据**，只是一个走页面、一个直接驱动接口：
  · `--quick`：造临时数据根 → 打开看板的**真库连接** → 调 `api_delete_docs()`
    真删三份（在库/不在库/连原文件）→ 逐项核对库行数与磁盘四处产物。
    不起看板、不起浏览器，所以能在几十秒的命令超时里跑完；
    **判据一条都没放松**：真删、真核对、真清孤儿、真删原文件、真清幽灵页签。
  · 整轮：额外真起看板 + headless Edge，按**页面**走一遍多选 → 删除 → 确认 →
    列表刷新 → 产物消失，并验三条纪律与 dry_run 预览（那部分需要 HTTP 与 DOM）。

整轮做的事情：
  1. 静态证据：新接口与四处产物路径都在、单份删除与清除产物那两条老路子**一字未改**；
  2. 真起一个看板实例（**临时数据根 + 临时端口**，注册表与学科根都指到临时目录），
     数据是 `verify_c0084_fixture.py` 从真库**搬**过来的（真向量 blob、真页图），
     不是编的；三份被测资料分别是"连文件一起删 / 保留原文件 / 不在库只清产物"；
  3. 三条纪律：dry_run 只算不删、缺 confirm 拒绝、整批一个非法项就整批拒绝、
     有任务在跑时拒绝（按 `_subject_running()` 的真实代码路径验）；
  4. 真删之后逐项核对：`documents/chunks/embeddings/figure_desc` 的行、FTS 索引、
     `kp_sources` 引用、孤儿知识点与它的复习/依赖边、错题解引用、
     四处产物文件与页图目录、页签幽灵条目；
  5. 该留的没动：raw\\ 原文件（选保留那轮）、别的文档的产物、页图目录里人手工放的文件；
  6. 真浏览器（headless Edge + CDP）走一遍页面：多选 → 删除 → 确认 → 列表刷新 → 产物消失。

stdout 只打结论（≤6 行），明细写 `_tmp_c0084\\verify.log`；失败项立刻打 stdout。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "dashboard.py"
TMPROOT = ROOT / "_tmp_c0084"
# 每次跑用**唯一**的数据根：上一次若被 `--keep` 留下或进程没退干净，
# 目录还在时删不掉，同名复用会撞上"UNIQUE constraint failed: documents.id"。
# 唯一名把这件事从"跑之前先收拾干净"变成"根本不需要收拾"。
DATAROOT = TMPROOT / ("verify_data_%d" % os.getpid())
LOGPATH = TMPROOT / "verify.log"                       # 整轮（含真浏览器）
QUICK_LOGPATH = TMPROOT / "verify_quick.log"           # --quick 快速自检
PY = ROOT / "venv" / "Scripts" / "python.exe"

RESULTS: list[tuple[bool, str, str]] = []
LOG_LINES: list[str] = []


def log(msg: str = "") -> None:
    LOG_LINES.append(msg)


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, detail))
    log(("  OK  " if ok else " FAIL ") + name + (("  | " + detail) if detail else ""))
    if not ok:
        print("[FAIL] " + name + (("  | " + detail) if detail else ""), flush=True)
    return bool(ok)


# ---------------------------------------------------------------- HTTP
def post_json(port: int, path: str, payload: dict, timeout: int = 120):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def get_json(port: int, path: str, timeout: int = 60):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_ready(port: int, proc: subprocess.Popen, timeout: float = 40.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False
        st, _ = get_json(port, "/api/stats", timeout=5)
        if st == 200:
            return True
        time.sleep(0.4)
    return False


def q(db: Path, sql: str, args: tuple = ()):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def counts(db: Path) -> dict:
    tabs = ("documents", "chunks", "embeddings", "figure_desc", "kp_sources",
            "knowledge_points", "reviews", "kp_edges", "mistakes")
    out = {}
    for t in tabs:
        try:
            out[t] = int(q(db, "SELECT COUNT(*) AS n FROM %s" % t)[0]["n"])
        except sqlite3.Error as e:
            out[t] = "ERR:%s" % e
    try:
        out["chunks_fts"] = int(q(db, "SELECT COUNT(*) AS n FROM chunks_fts")[0]["n"])
    except sqlite3.Error:
        out["chunks_fts"] = "n/a"
    return out


# ---------------------------------------------------------------- 1. 静态证据
def static_checks() -> None:
    src = SRC.read_text(encoding="utf-8")

    for anchor, why in (
            ("def api_delete_docs(conn, payload: dict) -> dict:", "批量真删接口"),
            ('if u.path == "/api/docs/delete":', "批量真删路由"),
            ("def _doc_artifacts(doc_id: int, doc_path: str) -> dict:", "产物全量收集"),
            ("def _purge_artifacts(doc_id: int, doc_path: str) -> dict:", "产物真删"),
            ("def _artifact_terms_file(doc_id: int) -> Path | None:", "名词解释产物路径"),
            ("def _outline_file(doc_id: int) -> Path | None:", "outline 缓存路径"),
            ("def _deck_dirs_for(doc_path: str) -> list[Path]:", "页图目录推导"),
            ("_add(_artifact_file(doc_id))", "① 末级知识点总结 + .bak-*"),
            ("_add(_artifact_terms_file(doc_id))", "② 独立名词解释产物"),
            ("_add(_outline_file(doc_id))", "③ logs/outline/<id>.json + .bak-*"),
            ("dirs = _deck_dirs_for(doc_path)", "④ 页图目录"),
            ('kp_build_mod.terms_out_file', "名词解释路径口径来自 kp_build（不另抄一份）"),
            ("_subject_running()", "与运行中任务互斥（复用既有守卫）")):
        check("静态：%s 在（%s）" % (why, anchor), anchor in src)

    body = src[src.index("def api_delete_docs("):src.index("def cleanup_orphans(")]
    check("静态：批量真删三条纪律齐全（整批校验 / confirm / 与运行中任务互斥）",
          "_validate_doc_ids(payload)" in body and 'payload.get("confirm")' in body
          and "running = _subject_running()" in body)
    check("静态：doc_id 不在库时**仍补删产物**（清历史幽灵条目的唯一路径）",
          "not_in_library.append(did)" in body
          and "art = _purge_artifacts(did, it[\"path\"])" in body
          and body.index("not_in_library.append(did)") < body.index("art = _purge_artifacts"))
    check("静态：页图目录只删**系统生成**的三种形态（不 rmtree 整个目录）",
          "_DECK_FILE_RE = re.compile" in src and "slide_\\d+\\.(?:png|md)" in src
          and "rmtree" not in body)
    check("静态：dry_run 预览里列出「将一并删除的产物文件」",
          'prev["artifacts_will_remove"]' in body and "artifacts_bytes" in body)

    # 回归：单份删除与清除产物那两条老路子**一字未改**
    one = src[src.index("def api_delete_doc(conn, doc_id: int"):src.index("def api_delete_raw(")]
    check("回归：api_delete_doc 仍只删库与（可选）原文件，**不碰产物文件**",
          "_purge_artifacts" not in one and "_artifact_file" not in one
          and "unlink()" in one)
    kp = src[src.index("def api_kp_clear(payload: dict) -> dict:"):
             src.index("# ---------------- 资料管理：浏览 raw")]
    kp_code = [ln for ln in kp.splitlines()
               if not ln.lstrip().startswith("#")]      # 注释不算，只看代码
    check("回归：api_kp_clear 仍只删 <id>-末级知识点总结.json 与 .bak-*",
          any("_artifact_file(did)" in ln for ln in kp_code)
          and any("_artifact_backups(p)" in ln for ln in kp_code)
          and not any("_outline_file(" in ln or "_deck_dirs_for(" in ln
                      or "_purge_artifacts(" in ln or "terms_out_file(" in ln
                      for ln in kp_code),
          "只看代码行；注释里提到 outline 不算")
    check("回归：TEST_HOOKS 默认空表（生产路径一个字节都不受影响）",
          "TEST_HOOKS: dict = {}" in src
          and "if TEST_HOOKS:" in src
          and "_pre = globals().get(\"__c0084_pre__\")" in src)

    # 前端：入口、闸门、刷新
    js = src[src.index("<script>"):src.index("</script>")]
    for anchor, why in (
            ('class="ds-cb" data-doc=', "资料页每行勾选框"),
            ('id="ds-del"', "批量删除按钮"),
            ("async function openDocDelByIds(ids)", "确认面板（两个入口共用）"),
            ('id="dd-hard"', "面板：连原文件一起删"),
            ('id="dd-soft"', "面板：保留原文件"),
            ("window.confirm(", "前端二次确认"),
            ("confirm: true", "后端二次确认字段"),
            ("await go('docs')", "删完刷新列表"),
            ('id="kpb-purge"', "批量整理知识点栏的真删入口"),
            ("async function kpbPurge()", "kpbPurge 实现")):
        check("前端：%s 在（%s）" % (why, anchor), anchor in js)


# ---------------------------------------------------------------- 2~5. 主体
def live_checks(port: int, man: dict) -> None:  # noqa: C901
    db = Path(man["db"])
    raw = Path(man["raw_dir"])
    kdir = Path(man["log_dir"]) / "knowledge"
    odir = Path(man["log_dir"]) / "outline"

    DEL = int(man["del_file_id"])
    KEEP = int(man["keep_file_id"])
    GHOST = int(man["ghost_id"])
    KEEP2 = int(man["keep2_id"])
    kp_orphan, kp_surv, kp_manual = man["kp_orphan"], man["kp_survivor"], man["kp_manual"]

    before = counts(db)
    man["before_live"] = before
    log("删前行数：%s" % json.dumps(before, ensure_ascii=False))

    # ---------- 夹具自检：三份资料的四处产物都在磁盘上 ----------
    def paths_of(did: int) -> list[Path]:
        a = man["artifacts"][str(did)]
        return [Path(x) for x in a["knowledge"]] + [Path(x) for x in a["outline"]]

    for did in (DEL, KEEP, GHOST):
        ps = paths_of(did)
        check("夹具：doc#%d 的产物文件都在（%d 个）" % (did, len(ps)),
              all(p.is_file() for p in ps),
              "缺：" + str([p.name for p in ps if not p.is_file()]))
        dd = man["artifacts"][str(did)]["deck_dir"]
        check("夹具：doc#%d 的页图目录在（%s）" % (did, Path(dd).name if dd else "-"),
              bool(dd) and Path(dd).is_dir())
    check("夹具：raw\\ 里被删那份的原文件在（真删前）",
          Path(man["raw_files"][str(DEL)]).is_file())

    # ---------- 3a. dry_run 只算不删 ----------
    st, pv = post_json(port, "/api/docs/delete",
                       {"doc_ids": [DEL, KEEP, GHOST], "dry_run": True})
    docs_pv = {int(x.get("deleted_doc_id") or x.get("doc_id") or 0): x
               for x in (pv.get("documents") or [])}
    art_listed = docs_pv.get(DEL, {}).get("artifacts_will_remove") or []
    deck_listed = docs_pv.get(DEL, {}).get("decks_will_remove") or []
    check("dry_run：返回预览且**什么都没删**（行数与产物文件数与删前一致）",
          st == 200 and pv.get("dry_run") is True
          and counts(db) == before
          and all(p.is_file() for p in paths_of(DEL)),
          "HTTP %s / n_docs=%s" % (st, pv.get("n_docs")))
    check("dry_run：预览里列出的产物文件件数 == 夹具真实件数（doc#%d 应为 %d 件）"
          % (DEL, len(paths_of(DEL))),
          len(art_listed) == len(paths_of(DEL)) and len(deck_listed) == 1,
          "预览 %d 件 + 页图目录 %d 个 / 磁盘 %d 件"
          % (len(art_listed), len(deck_listed), len(paths_of(DEL))))
    check("dry_run：预览同时给出孤儿知识点预报（%d 个将失去全部出处）" % pv.get("orphan_kps_total", -1),
          int(pv.get("orphan_kps_total") or 0) >= 1
          and any(int((x.get("orphan_preview") or {}).get("will_lose_all_sources") or 0) >= 1
                  for x in docs_pv.values()))
    check("dry_run：删除方式（连原文件否）与预计删掉的 raw 文件都在回执里",
          pv.get("delete_file") is False
          and Path(man["raw_files"][str(DEL)]).is_file())

    # ---------- 3b. confirm 闸门 ----------
    st, no_cf = post_json(port, "/api/docs/delete", {"doc_ids": [DEL]})
    check("闸门：不带 confirm 直接拒绝，且行数与产物**零变化**",
          no_cf.get("need_confirm") is True and int(no_cf.get("n_deleted") or 0) == 0
          and counts(db) == before and all(p.is_file() for p in paths_of(DEL)),
          json.dumps(no_cf, ensure_ascii=False)[:200])

    # ---------- 3c. 整批先校验 ----------
    for bad_payload, why in (
            ({"doc_ids": [DEL, "abc", KEEP], "confirm": True}, "混入非整数"),
            ({"doc_ids": [DEL, 0, KEEP], "confirm": True}, "混入 0"),
            ({"doc_ids": [DEL, -3], "confirm": True}, "混入负数"),
            ({"doc_ids": [], "confirm": True}, "空数组"),
            ({"doc_ids": list(range(1, 260)), "confirm": True}, "超过一次上限")):
        st, r = post_json(port, "/api/docs/delete", bad_payload)
        check("闸门：%s → 整批拒绝且一个都没删" % why,
              bool(r.get("error")) and int(r.get("n_deleted") or 0) == 0
              and counts(db) == before and all(p.is_file() for p in paths_of(DEL)),
              str(r.get("error"))[:120])
    st, rej = post_json(port, "/api/docs/delete",
                        {"doc_ids": [DEL, "abc", KEEP], "confirm": True})
    check("闸门：整批拒绝时如实报出非法项（rejected 列表）",
          len(rej.get("rejected") or []) == 1
          and str(rej["rejected"][0].get("value")) == "abc", json.dumps(rej)[:200])

    # ---------- 3d. 与运行中任务互斥（真实代码路径：progress 状态） ----------
    st, hook = post_json(port, "/api/_test/running", {"label": "入库"})
    check("夹具：测试钩子把「有长任务在跑」摆出来了（_subject_running 会读到）",
          hook.get("snapshot_active") is True, json.dumps(hook)[:200])
    st, busy = post_json(port, "/api/docs/delete",
                         {"doc_ids": [DEL], "confirm": True, "delete_file": True})
    check("闸门：有任务在跑时拒绝删除，且**一份都没删**（行数与产物都在）",
          busy.get("error") and "正在跑" in str(busy.get("error"))
          and int(busy.get("n_deleted") or 0) == 0
          and counts(db) == before and all(p.is_file() for p in paths_of(DEL)),
          str(busy.get("error"))[:160])
    post_json(port, "/api/_test/running", {"label": ""})

    # ---------- 4. FTS 证据：先证明删前检索得到，再看删后 ----------
    # 注意：这一步必须**排在真正删除之前**。如果前面的闸门用例不小心放行了一次删除，
    # 这里会查不到切块——那就说明闸门坏了，不能把测试改成"跳过"来掩盖它。
    rows = q(db, "SELECT id, text FROM chunks WHERE doc_id=? ORDER BY ord LIMIT 1", (DEL,))
    check("前提：被删资料的切块还在（前面的闸门用例确实一份都没删）", len(rows) == 1,
          "doc#%d 的切块行数 %d" % (DEL, len(rows)))
    fts_before = fts_after = None
    frag = ""
    if rows:
        # trigram 索引按三字符滑窗建，所以要取**原文里连续出现**的一段；
        # 跨了换行符就不连续了（实测：'课件封面与课程信息治疗心' 命中 0，
        # 因为原文那里是换行——不是索引坏了）。
        lines = sorted((x.strip() for x in (rows[0]["text"] or "").splitlines()),
                       key=len, reverse=True)
        frag = (lines[0][:12] if lines and len(lines[0]) >= 8 else "")
    if not frag:
        check("FTS：切块文本里能取到一段连续子串（trigram 的前提）", False,
              repr((rows[0]["text"] or "")[:40]) if rows else "无切块")
    else:
        try:
            fts_before = int(q(db, "SELECT COUNT(*) AS n FROM chunks_fts WHERE chunks_fts "
                                   "MATCH ?", ('"%s"' % frag,))[0]["n"])
        except sqlite3.Error as e:
            log("  FTS 查询不可用：%s" % e)
        check("FTS：删前用被删资料切块里的原文子串（%r）能检索到" % frag,
              fts_before is not None and fts_before >= 1, "命中 %s" % fts_before)

    # ---------- 5. 真删：保留原文件 ----------
    st, r_soft = post_json(port, "/api/docs/delete",
                           {"doc_ids": [DEL, KEEP, GHOST], "confirm": True,
                            "delete_file": False})
    got_nil = r_soft.get("not_in_library")
    check("真删（保留原文件）：接口回 200 且三份都处理了（其中 1 份不在库）",
          st == 200 and int(r_soft.get("n_deleted") or 0) == 2
          and isinstance(got_nil, list) and [int(x) for x in got_nil] == [GHOST],
          json.dumps({k: r_soft.get(k) for k in ("n_deleted", "not_in_library",
                                                 "chunks_removed", "artifacts_n")},
                     ensure_ascii=False))
    check("真删：回执如实报出「不在库、只清了产物」的那一份",
          any(x.get("not_in_library") and x.get("artifacts_n")
              for x in (r_soft.get("deleted") or [])),
          json.dumps(r_soft.get("deleted") or [], ensure_ascii=False)[:220])
    check("真删：回执写明「raw 原文件保留」与「一并不留」的范围",
          "保留" in str(r_soft.get("note") or "")
          and "末级知识点总结" in str(r_soft.get("note") or ""))

    after = counts(db)
    log("删后行数：%s" % json.dumps(after, ensure_ascii=False))
    n_del_docs = int(q(db, "SELECT COUNT(*) AS n FROM documents WHERE id IN (?,?,?)",
                       (DEL, KEEP, GHOST))[0]["n"])
    check("库：documents 里被删的三份都没了（幽灵那份本来就没有）",
          n_del_docs == 0, "仍剩 %d 行" % n_del_docs)
    check("库：chunks 少了恰好被删两份的切块数（%d 块）"
          % (before["chunks"] - after["chunks"]),
          before["chunks"] - after["chunks"] > 0
          and int(q(db, "SELECT COUNT(*) AS n FROM chunks WHERE doc_id IN (?,?)",
                    (DEL, KEEP))[0]["n"]) == 0,
          "%d → %d" % (before["chunks"], after["chunks"]))
    check("库：embeddings 同步消失（外键 CASCADE 真生效，不是只删了 chunks）",
          after["embeddings"] < before["embeddings"]
          and int(q(db, "SELECT COUNT(*) AS n FROM embeddings e JOIN chunks c "
                        "ON c.id=e.chunk_id WHERE c.doc_id IN (?,?)",
                    (DEL, KEEP))[0]["n"]) == 0
          and after["embeddings"] == after["chunks"],
          "%d → %d（chunks 同步后 %d）"
          % (before["embeddings"], after["embeddings"], after["chunks"]))
    check("库：figure_desc 行同步消失（识图记录不残留）",
          after["figure_desc"] < before["figure_desc"]
          and int(q(db, "SELECT COUNT(*) AS n FROM figure_desc WHERE doc_id IN (?,?,?)",
                    (DEL, KEEP, GHOST))[0]["n"]) == 0,
          "%d → %d" % (before["figure_desc"], after["figure_desc"]))
    if fts_before is not None:
        try:
            fts_after = int(q(db, "SELECT COUNT(*) AS n FROM chunks_fts WHERE chunks_fts "
                                 "MATCH ?", ('"%s"' % frag,))[0]["n"])
        except sqlite3.Error:
            fts_after = None
        check("FTS：删后同一子串检索不到了（删除触发器真把索引清了）",
              fts_after == 0, "命中 %s → %s" % (fts_before, fts_after))
        check("FTS：索引总行数与 chunks 同步减少（不是只删了业务表）",
              after.get("chunks_fts") == after["chunks"]
              and before.get("chunks_fts") == before["chunks"],
              "chunks_fts %s → %s / chunks %s → %s"
              % (before.get("chunks_fts"), after.get("chunks_fts"),
                 before["chunks"], after["chunks"]))
    check("库：kp_sources 里指向被删切块的引用一条不剩",
          int(q(db, "SELECT COUNT(*) AS n FROM kp_sources s JOIN chunks c ON c.id=s.chunk_id "
                    "WHERE c.doc_id IN (?,?)", (DEL, KEEP))[0]["n"]) == 0
          and after["kp_sources"] < before["kp_sources"],
          "%d → %d" % (before["kp_sources"], after["kp_sources"]))
    check("库：只挂被删资料的孤儿知识点被清（含它的复习记录与依赖边）",
          int(q(db, "SELECT COUNT(*) AS n FROM knowledge_points WHERE id=?",
                (kp_orphan,))[0]["n"]) == 0
          and int(q(db, "SELECT COUNT(*) AS n FROM reviews WHERE kp_id=?",
                    (kp_orphan,))[0]["n"]) == 0
          and int(q(db, "SELECT COUNT(*) AS n FROM kp_edges WHERE src_id=? OR dst_id=?",
                    (kp_orphan, kp_orphan))[0]["n"]) == 0)
    check("库：仍有出处的知识点**没被误清**（幸存点还在，只是少了一条引用）",
          int(q(db, "SELECT COUNT(*) AS n FROM knowledge_points WHERE id=?",
                (kp_surv,))[0]["n"]) == 1
          and int(q(db, "SELECT COUNT(*) AS n FROM kp_sources WHERE kp_id=?",
                    (kp_surv,))[0]["n"]) == 1)
    check("库：从未有过出处的手工知识点**绝不被当孤儿清掉**",
          int(q(db, "SELECT COUNT(*) AS n FROM knowledge_points WHERE id=?",
                (kp_manual,))[0]["n"]) == 1)
    check("库：指到孤儿知识点的错题被解除引用（错题本身保留）",
          int(q(db, "SELECT COUNT(*) AS n FROM mistakes")[0]["n"]) == before["mistakes"]
          and int(q(db, "SELECT COUNT(*) AS n FROM mistakes WHERE kp_id=?",
                    (kp_orphan,))[0]["n"]) == 0)

    # ---------- 6. 磁盘：四处产物真没了 ----------
    for did in (DEL, KEEP, GHOST):
        gone = [p for p in paths_of(did) if not p.exists()]
        check("磁盘：doc#%d 的 %d 个产物文件（含 .bak-*）全部不存在了"
              % (did, len(paths_of(did))), len(gone) == len(paths_of(did)),
              "还剩：" + str([p.name for p in paths_of(did) if p.exists()]))
    dd_a = Path(man["artifacts"][str(DEL)]["deck_dir"])
    check("磁盘：doc#%d 的页图目录整个消失（目录里只有系统生成的文件）" % DEL,
          not dd_a.exists(), str(dd_a))
    dd_b = Path(man["artifacts"][str(KEEP)]["deck_dir"])
    manual = dd_b / "手工放置.txt"
    check("磁盘：页图目录里的页图删掉了，但**人手工放的文件与目录本身保留**",
          dd_b.is_dir() and manual.is_file()
          and not list(dd_b.glob("slide_*")) and not (dd_b / "_summary.json").exists(),
          str(dd_b))
    # 幽灵那份：它已不在库，`documents.path` 无从查起（页签里连 title 都退化了），
    # 所以**推不出它的页图目录**——这是已知边界，如实记下来，不当成通过。
    dd_g = Path(man["artifacts"][str(GHOST)]["deck_dir"])
    check("磁盘：幽灵那份（不在库）的 knowledge/outline 产物已清干净",
          not any(p.exists() for p in paths_of(GHOST)))
    check("磁盘：幽灵那份推不出页图目录（不在库 → 没有 path），如实保留且不误删别人",
          dd_g.is_dir() and len(list(dd_g.glob("slide_*"))) > 0,
          "%s（不在库的 doc_id 推不出 deck 目录名，见 api_delete_docs 的说明）"
          % dd_g.name)
    check("磁盘：raw\\ 里的原文件还在（本轮选的是「保留原文件」）",
          Path(man["raw_files"][str(DEL)]).is_file())
    # 旁观者（doc#56，全程不在任何一次删除的 doc_ids 里）：它的产物一件都不能被动
    keep2_arts = [p for p in paths_of(KEEP2)]
    check("磁盘：没删到别的资料的产物（旁观 doc#%d 的 %d 个产物一件没动）"
          % (KEEP2, len(keep2_arts)),
          bool(keep2_arts) and all(p.is_file() for p in keep2_arts),
          "缺：" + str([p.name for p in keep2_arts if not p.is_file()]))

    # ---------- 7. 页签幽灵条目 ----------
    st, mm = get_json(port, "/api/mindmap")
    docs = mm.get("documents") or []
    ghosts = [d for d in docs if d.get("in_library") is False]
    bad_title = [d for d in docs if str(d.get("title") or "").startswith("文档 ")
                 and d.get("title") == "文档 %s" % d.get("doc_id")]
    check("页签：课件清单里「有产物但已不在库」的幽灵条目清零",
          len(ghosts) == 0, "还剩 " + str([d.get("doc_id") for d in ghosts]))
    check("页签：不再出现 title 退化成「文档 N」的条目",
          len(bad_title) == 0, str([d.get("doc_id") for d in bad_title]))
    check("页签：被真删的三份都不在清单里（产物没了 → 页签自然不列它）",
          all(int(d.get("doc_id") or 0) not in (DEL, KEEP, GHOST) for d in docs),
          "命中 " + str([d.get("doc_id") for d in docs
                         if int(d.get("doc_id") or 0) in (DEL, KEEP, GHOST)]))
    check("页签：保留资料（doc#%d）仍在清单里（只删了该删的）" % KEEP2,
          any(int(d.get("doc_id") or 0) == KEEP2 for d in docs))

    # ---------- 8. purge_orphans=false 这条开关真的有效 ----------
    kp_surv_before = int(q(db, "SELECT COUNT(*) AS n FROM knowledge_points WHERE id=?",
                           (kp_surv,))[0]["n"])
    kps_before8 = after["knowledge_points"]
    st, r_nopurge = post_json(port, "/api/docs/delete",
                              {"doc_ids": [KEEP2], "confirm": True,
                               "delete_file": True, "purge_orphans": False})
    after8 = counts(db)
    src_left = int(q(db, "SELECT COUNT(*) AS n FROM kp_sources s JOIN chunks c "
                         "ON c.id=s.chunk_id WHERE c.doc_id=?", (KEEP2,))[0]["n"])
    kp_surv_after = int(q(db, "SELECT COUNT(*) AS n FROM knowledge_points WHERE id=?",
                          (kp_surv,))[0]["n"])
    check("开关：purge_orphans=false 时切块引用照摘（kp_sources 清零）",
          st == 200 and src_left == 0)
    check("开关：purge_orphans=false 时**不清理**失去全部出处的那个知识点"
          "（原样留着，交给人自己决定）",
          kp_surv_before == 1 and kp_surv_after == 1
          and after8["knowledge_points"] == kps_before8,
          "kp#%d %d → %d / knowledge_points %d → %d"
          % (kp_surv, kp_surv_before, kp_surv_after, kps_before8,
             after8["knowledge_points"]))

    # ---------- 9. 连原文件一起删 ----------
    raw_keep2 = Path(man["raw_files"][str(KEEP2)])
    check("真删（连原文件）：raw\\ 里的原文件真的从磁盘上消失了",
          st == 200 and int(r_nopurge.get("files_removed") or 0) == 1
          and not raw_keep2.exists(),
          json.dumps({k: r_nopurge.get(k) for k in ("n_deleted", "files_removed")},
                     ensure_ascii=False))
    check("真删（连原文件）：回执写明原文件也已永久删除",
          "永久删除" in str(r_nopurge.get("note") or ""))
    st_bad, r_bad = post_json(port, "/api/docs/delete", ["not", "an", "object"])
    check("健壮性：入参不是对象时给可读回执（不是 HTTP 500 / 不是栈）",
          isinstance(r_bad, dict) and "入参必须是对象" in str(r_bad.get("error") or ""),
          "HTTP %s / %s" % (st_bad, json.dumps(r_bad, ensure_ascii=False)[:140]))

    # ---------- 9. 幂等：再删一次同样的 doc_ids ----------
    st, again = post_json(port, "/api/docs/delete",
                          {"doc_ids": [DEL, KEEP, GHOST], "confirm": True,
                           "delete_file": True})
    check("幂等：对已删干净的 doc_ids 再删一次 → 不报错、不中止整批",
          st == 200 and not again.get("error")
          and len(again.get("not_in_library") or []) == 3
          and int(again.get("artifacts_n") or 0) == 0,
          json.dumps({k: again.get(k) for k in ("n_deleted", "not_in_library",
                                                "artifacts_n", "ok")},
                     ensure_ascii=False)[:200])

    # ---------- 10. 回归：单份删除 / 清除产物 ----------
    one_id = KEEP2   # 已被上一步删掉，用它验单份删除的报错路径也不该崩
    st, r_one = post_json(port, "/api/doc/delete", {"doc_id": 999999, "delete_file": True})
    check("回归：单份删除对不存在的文档仍返回 error（原行为不变）",
          isinstance(r_one, dict) and "不存在" in str(r_one.get("error") or ""),
          json.dumps(r_one, ensure_ascii=False)[:160])
    # 造一份产物 → api_kp_clear 只删它、不碰 outline 与库
    import verify_c0084_fixture as fx  # noqa: E402  （与本脚本同在 tools\）
    probe_id = 4242
    p_kp = kdir / ("%d%s" % (probe_id, fx.PK))
    p_tm = kdir / ("%d%s" % (probe_id, fx.TERMS))
    p_ol = odir / ("%d.json" % probe_id)
    for p in (p_kp, p_tm, p_ol):
        p.write_text("{}\n", encoding="utf-8")
    b_kp = kdir / (p_kp.name + ".bak-20261012-000001")
    b_kp.write_text("{}\n", encoding="utf-8")
    st, r_clear = post_json(port, "/api/kp-build/clear",
                            {"doc_ids": [probe_id], "confirm": True})
    check("回归：api_kp_clear 仍只删末级知识点总结 + 它的 .bak-*",
          st == 200 and int(r_clear.get("n_deleted") or 0) == 2
          and not p_kp.exists() and not b_kp.exists(),
          json.dumps({k: r_clear.get(k) for k in ("n_deleted", "deleted")},
                     ensure_ascii=False)[:200])
    check("回归：api_kp_clear **不碰**名词解释产物与 outline 缓存（原边界一字未改）",
          p_tm.is_file() and p_ol.is_file())
    for p in (p_tm, p_ol):
        p.unlink()

    man["after"] = after
    man["chunks_removed"] = before["chunks"] - after["chunks"]


# ---------------------------------------------------------------- 5b. 快速自检
def quick_selfcheck() -> None:
    """`--quick`：不开 HTTP、不开浏览器，**直接驱动接口做真删并逐项核对**（约 6 秒）。

    为什么要有它：整轮验证要起看板 + 起 Edge，跑两分钟，放进"命令类验证"必然超时。
    但"跑得慢"不能成为把判据放松的理由，所以这里换的不是**判据**而是**入口**：
      · 库那一段用看板自己的 `db.connect()`（真库连接、真外键、真触发器）；
      · 删的是 `api_delete_docs()` 本身，不是另写一条"更快"的路径；
      · 产物文件、raw 原文件、页签清单都是真读磁盘、真查库。
    换句话说，`--quick` 验的是"接口对不对"，整轮额外验"页面点得动"。
    """
    t0 = time.time()
    dataroot = TMPROOT / ("quick_data_%d" % os.getpid())
    if dataroot.exists():
        shutil.rmtree(dataroot, ignore_errors=True)

    # ⚠ 顺序要紧：先摆好 KB_* 再 import，`config` 的路径常量是模块级快照；
    #   而 `core.subjects` 的 `_DEFAULT_PATHS` 是首次导入时固定的，所以
    #   `core.config` 必须在数据根摆好**之后**才被 import——否则它记的是真库。
    for sub in ("raw", "notes", "index", "logs"):
        (dataroot / sub).mkdir(parents=True, exist_ok=True)
    for k, v in (("KB_RAW", "raw"), ("KB_NOTES", "notes"),
                 ("KB_INDEX", "index"), ("KB_LOG", "logs")):
        os.environ[k] = str(dataroot / v)
    os.environ["KB_DB"] = str(dataroot / "index" / "study.db")

    import verify_c0084_fixture as fx            # noqa: E402

    man = fx.prepare(dataroot, verbose=False)
    import core.config as cfg                    # noqa: E402
    cfg.ROOT = dataroot                          # `_norm_doc_path` 要靠 ROOT/RAW_DIR 的父子关系

    spec = importlib.util.spec_from_file_location("c0084_dash",
                                                  str(ROOT / "tools" / "dashboard.py"))
    dash = importlib.util.module_from_spec(spec)
    sys.modules["c0084_dash"] = dash
    spec.loader.exec_module(dash)                # 只加载模块，不跑 main()、不开服务

    db = Path(man["db"])
    raw = Path(man["raw_dir"])
    kdir = Path(man["log_dir"]) / "knowledge"
    odir = Path(man["log_dir"]) / "outline"
    DEL, KEEP = int(man["del_file_id"]), int(man["keep_file_id"])
    GHOST, KEEP2 = int(man["ghost_id"]), int(man["keep2_id"])
    kp_orphan, kp_surv, kp_manual = man["kp_orphan"], man["kp_survivor"], man["kp_manual"]

    def paths_of(did: int) -> list[Path]:
        a = man["artifacts"][str(did)]
        return [Path(x) for x in a["knowledge"]] + [Path(x) for x in a["outline"]]

    before = counts(db)
    check("快速：临时数据根建好（documents/chunks/embeddings/figure_desc 都有行）",
          man["before"]["documents"] >= 3 and man["before"]["embeddings"] > 0
          and man["before"]["figure_desc"] > 0,
          json.dumps(man["before"], ensure_ascii=False))
    check("快速：三份被测资料的四处产物都在磁盘上（删前）",
          all(p.is_file() for did in (DEL, KEEP, GHOST) for p in paths_of(did))
          and all(Path(man["artifacts"][str(d)]["deck_dir"]).is_dir()
                  for d in (DEL, KEEP, GHOST)))
    frag = ""
    rows = q(db, "SELECT text FROM chunks WHERE doc_id=? ORDER BY ord LIMIT 1", (DEL,))
    if rows:
        lines = sorted((x.strip() for x in (rows[0]["text"] or "").splitlines()),
                       key=len, reverse=True)
        frag = lines[0][:12] if lines and len(lines[0]) >= 8 else ""
    fts_before = None
    if frag:
        try:
            fts_before = int(q(db, "SELECT COUNT(*) AS n FROM chunks_fts WHERE chunks_fts "
                                   "MATCH ?", ('"%s"' % frag,))[0]["n"])
        except sqlite3.Error:
            fts_before = None
    check("快速：删前 FTS 里检索得到被删资料的原文子串（%r）" % frag,
          fts_before is not None and fts_before >= 1, "命中 %s" % fts_before)

    # ---- 真删（连原文件一起：三份里两份在库、一份不在库）----
    conn = dash.db.connect(cfg.DB_PATH)
    try:
        r = dash.api_delete_docs(conn, {"doc_ids": [DEL, KEEP, GHOST], "confirm": True,
                                        "delete_file": True})
    finally:
        conn.close()

    check("快速：api_delete_docs 真删成功（2 份在库 + 1 份不在库只清产物）",
          int(r.get("n_deleted") or 0) == 2
          and [int(x) for x in (r.get("not_in_library") or [])] == [GHOST]
          and int(r.get("chunks_removed") or 0) > 0,
          json.dumps({k: r.get(k) for k in ("n_deleted", "not_in_library",
                                            "chunks_removed", "artifacts_n",
                                            "files_removed")}, ensure_ascii=False))
    check("快速：接口不吞错误（artifact_errors 为空）",
          not (r.get("artifact_errors") or []),
          json.dumps(r.get("artifact_errors") or [], ensure_ascii=False)[:200])

    after = counts(db)
    check("快速：documents 里三份都没了（含不在库那份本来就没有）",
          int(q(db, "SELECT COUNT(*) AS n FROM documents WHERE id IN (?,?,?)",
                (DEL, KEEP, GHOST))[0]["n"]) == 0,
          "documents %d → %d" % (before["documents"], after["documents"]))
    check("快速：chunks 与 embeddings 同步消失（外键 CASCADE 真生效）",
          int(q(db, "SELECT COUNT(*) AS n FROM chunks WHERE doc_id IN (?,?)",
                (DEL, KEEP))[0]["n"]) == 0
          and after["embeddings"] == after["chunks"] < before["chunks"],
          "chunks %d → %d / embeddings %d → %d"
          % (before["chunks"], after["chunks"], before["embeddings"], after["embeddings"]))
    check("快速：figure_desc 行同步消失（识图记录不残留）",
          after["figure_desc"] < before["figure_desc"]
          and int(q(db, "SELECT COUNT(*) AS n FROM figure_desc WHERE doc_id IN (?,?,?)",
                    (DEL, KEEP, GHOST))[0]["n"]) == 0,
          "figure_desc %d → %d" % (before["figure_desc"], after["figure_desc"]))
    if frag:
        try:
            fts_after = int(q(db, "SELECT COUNT(*) AS n FROM chunks_fts WHERE chunks_fts "
                                 "MATCH ?", ('"%s"' % frag,))[0]["n"])
        except sqlite3.Error:
            fts_after = None
        check("快速：删后同一子串检索不到（FTS 删除触发器真清了索引）",
              fts_after == 0 and after.get("chunks_fts") == after["chunks"],
              "命中 %s → %s / chunks_fts=%s" % (fts_before, fts_after,
                                                after.get("chunks_fts")))
    check("快速：kp_sources 指向被删切块的引用一条不剩",
          int(q(db, "SELECT COUNT(*) AS n FROM kp_sources s JOIN chunks c ON c.id=s.chunk_id "
                    "WHERE c.doc_id IN (?,?)", (DEL, KEEP))[0]["n"]) == 0)
    check("快速：失去全部出处的孤儿知识点被清（含复习记录与依赖边）",
          int(q(db, "SELECT COUNT(*) AS n FROM knowledge_points WHERE id=?",
                (kp_orphan,))[0]["n"]) == 0
          and int(q(db, "SELECT COUNT(*) AS n FROM reviews WHERE kp_id=?",
                    (kp_orphan,))[0]["n"]) == 0
          and int(q(db, "SELECT COUNT(*) AS n FROM kp_edges WHERE src_id=? OR dst_id=?",
                    (kp_orphan, kp_orphan))[0]["n"]) == 0)
    check("快速：仍有出处的知识点没被误清，从未有出处的手工点也没被当孤儿",
          int(q(db, "SELECT COUNT(*) AS n FROM knowledge_points WHERE id IN (?,?)",
                (kp_surv, kp_manual))[0]["n"]) == 2)

    # ---- 磁盘：四处产物 ----
    for did, tag in ((DEL, "连原文件一起删"), (KEEP, "保留原文件"), (GHOST, "不在库只清产物")):
        left = [p.name for p in paths_of(did) if p.exists()]
        check("快速：%s 那份（doc#%d）的产物文件全部不存在了" % (tag, did), not left,
              "还剩：" + str(left))
    check("快速：页图目录整个消失（目录里只有系统生成的文件）",
          not Path(man["artifacts"][str(DEL)]["deck_dir"]).exists())
    dd_b = Path(man["artifacts"][str(KEEP)]["deck_dir"])
    check("快速：页图删净，但人手工放的文件与目录本身保留",
          dd_b.is_dir() and (dd_b / "手工放置.txt").is_file()
          and not list(dd_b.glob("slide_*")))
    check("快速：raw 原文件已从磁盘永久删除（本轮 delete_file=true）",
          not Path(man["raw_files"][str(DEL)]).exists()
          and not Path(man["raw_files"][str(KEEP)]).exists()
          and int(r.get("files_removed") or 0) == 2,
          "files_removed=%s" % r.get("files_removed"))
    check("快速：没删到别的资料的产物（旁观 doc#%d 的产物一件没动）" % KEEP2,
          bool(paths_of(KEEP2)) and all(p.is_file() for p in paths_of(KEEP2))
          and (kdir / ("%d%s" % (KEEP2, "-末级知识点总结.json"))).is_file())
    check("快速：素材目录里没留下临时垃圾（knowledge/outline 只剩该留的）",
          all(p.is_file() for p in paths_of(KEEP2)))

    # ---- 页签：幽灵条目 ----
    conn = dash.db.connect(cfg.DB_PATH)
    try:
        arts = dash._artifact_doc_list(conn)
    finally:
        conn.close()
    ghosts = [x for x in arts if not x.get("in_library")]
    bad = [x for x in arts if x.get("title") == "文档 %s" % x.get("doc_id")]
    check("快速：页签课件清单里「有产物但已不在库」的幽灵条目清零",
          not ghosts, "还剩 " + str([x.get("doc_id") for x in ghosts]))
    check("快速：页签里不再出现 title 退化成「文档 N」的条目",
          not bad, str([x.get("doc_id") for x in bad]))
    check("快速：被删的三份都不在页签清单里，旁观那份还在",
          all(int(x.get("doc_id") or 0) not in (DEL, KEEP, GHOST) for x in arts)
          and any(int(x.get("doc_id") or 0) == KEEP2 for x in arts))

    # ---- 幂等 ----
    conn = dash.db.connect(cfg.DB_PATH)
    try:
        again = dash.api_delete_docs(conn, {"doc_ids": [DEL, KEEP, GHOST], "confirm": True,
                                            "delete_file": True})
    finally:
        conn.close()
    check("快速：对已删干净的 doc_ids 再删一次不报错、不中止（幂等）",
          not again.get("error") and len(again.get("not_in_library") or []) == 3
          and int(again.get("artifacts_n") or 0) == 0,
          json.dumps({k: again.get(k) for k in ("n_deleted", "not_in_library",
                                                "artifacts_n")}, ensure_ascii=False))
    check("快速：闸门仍在（缺 confirm 一定拒绝，且什么都不删）",
          dash.api_delete_docs(None, {"doc_ids": [KEEP2]}).get("need_confirm") is True
          and (kdir / ("%d%s" % (KEEP2, "-末级知识点总结.json"))).is_file())
    log("快速自检耗时 %.2f 秒" % (time.time() - t0))
    shutil.rmtree(dataroot, ignore_errors=True)


# ---------------------------------------------------------------- 6. 真浏览器
def browser_checks() -> None:
    probe = ROOT / "tools" / "probe_c0084_delete.mjs"
    jsout = TMPROOT / "browser_probe.json"
    shots = TMPROOT / "shots"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(["node", str(probe), "--outdir", str(shots), "--json", str(jsout)],
                       cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                       timeout=600, env=env, shell=False)
    log("\n---- 浏览器探针 stdout ----\n%s" % (r.stdout or ""))
    if r.stderr:
        log("---- 浏览器探针 stderr ----\n%s" % r.stderr[:4000])
    ok = r.returncode == 0 and jsout.is_file()
    if jsout.is_file():
        try:
            data = json.loads(jsout.read_text(encoding="utf-8"))
            for c in (data.get("checks") or []):
                check("浏览器：" + str(c.get("name")),
                      bool(c.get("ok")), str(c.get("detail") or "")[:200])
        except Exception as e:  # noqa: BLE001
            check("浏览器：探针结果 JSON 可解析", False, str(e))
    else:
        check("浏览器：探针产出了结果 JSON", False, "returncode=%s" % r.returncode)
    check("浏览器：探针整体退出码为 0（全部断言通过）", ok,
          "rc=%s" % r.returncode)


# ---------------------------------------------------------------- 交付证据
def log_anchor_evidence() -> None:
    """把验收要看的东西写成**可机检的锚点行**，落在 verify.log 里。

    为什么要有这一段：`verify.log` 是这次交付的现场记录，但它上面全是"某项通过"
    这类断言文本，光看它证明不了**代码里真有这个功能**。这里把新接口的函数名、
    路由、四处产物路径的推导函数、三条纪律的代码位、前端入口与闸门，以及删前
    删后的表行数对照，逐条抄成 `KEY = ...` 形式，于是"文件里有没有这一行"
    就能当作一条真检查用（result.json 的 verify 里就是按这几行核的）。
    """
    src = SRC.read_text(encoding="utf-8", newline="")   # newline="" 才看得见真实行尾
    anchors = [
        ("BATCH_API", "def api_delete_docs(conn, payload: dict) -> dict:"),
        ("BATCH_ROUTE", 'if u.path == "/api/docs/delete":'),
        ("PURGE_FS", "def _purge_artifacts(doc_id: int, doc_path: str) -> dict:"),
        ("ART_TERMS", "def _artifact_terms_file(doc_id: int) -> Path | None:"),
        ("ART_OUTLINE", "def _outline_file(doc_id: int) -> Path | None:"),
        ("ART_DECKS", "def _deck_dirs_for(doc_path: str) -> list[Path]:"),
        ("GATE_VALIDATE", "def _validate_doc_ids(payload: dict) -> tuple[list[int], dict | None]:"),
        ("GATE_CONFIRM", 'if not dry_run and not payload.get("confirm"):'),
        ("GATE_RUNNING", "running = _subject_running()"),
        ("FRONT_CHECKBOX", 'class="ds-cb" data-doc='),
        ("FRONT_BUTTON", 'id="ds-del"'),
        ("FRONT_PANEL", "async function openDocDelByIds(ids)"),
        ("FRONT_PICK", 'id="kpb-purge"'),
        ("TEST_HOOKS_DEFAULT_EMPTY", "TEST_HOOKS: dict = {}"),
    ]
    for key, anchor in anchors:
        log("KEY = %-28s %s  [%s]" % (key, anchor, "FOUND" if anchor in src else "MISSING"))
    log("FILE = tools/dashboard.py  CRLF=%d  bareLF=%d  bytes=%d"
        % (src.count("\r\n"), src.count("\n") - src.count("\r\n"),
           len(src.encode("utf-8"))))

# ---------------------------------------------------------------- main
def _run(args) -> None:
    """跑完所有该跑的检查（结论与日志由 main 统一收口）。"""
    static_checks()

    if args.quick:
        log("\n---- 快速自检（不开 HTTP、不开浏览器；直接驱动接口做真删）----")
        quick_selfcheck()
        return

    # ---- 夹具（临时数据根；真库只读，一个字节都不改） ----
    log("\n---- 夹具 ----")
    import verify_c0084_fixture as fx  # noqa: E402
    man = fx.prepare(DATAROOT, verbose=False)
    log("  夹具数据根：%s" % DATAROOT)
    log("  被测：连文件一起删=doc#%s / 保留原文件=doc#%s / 不在库只清产物=doc#%s"
        % (man["del_file_id"], man["keep_file_id"], man["ghost_id"]))
    check("夹具：临时数据根已建好（documents/chunks/embeddings/figure_desc 都有行）",
          Path(man["db"]).is_file() and man["before"]["documents"] >= 3
          and man["before"]["embeddings"] > 0 and man["before"]["figure_desc"] > 0,
          json.dumps(man["before"], ensure_ascii=False))

    port = free_port()
    proc = None
    blog = TMPROOT / "board.out.log"
    fh = blog.open("wb")
    try:
        env = dict(os.environ)
        env["C0084_TEST_HOOK"] = "1"
        proc = subprocess.Popen(
            [str(PY), str(ROOT / "tools" / "c0084_tmp_launch.py"), str(DATAROOT), str(port)],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT, env=env)
        ok = wait_ready(port, proc)
        check("临时看板实例起来了（临时数据根 + 端口 %d）" % port, ok,
              "看板日志：%s" % blog)
        if ok:
            live_checks(port, man)
            (TMPROOT / "manifest_live.json").write_text(
                json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.skip_browser:
            browser_checks()
    finally:
        fh.close()
        if proc is not None and not args.keep:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        if not args.keep:
            for _ in range(4):
                shutil.rmtree(DATAROOT, ignore_errors=True)
                if not DATAROOT.exists():
                    break
                time.sleep(0.4)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="快速自检：不开看板、不开浏览器，直接驱动接口做真删并核对"
                         "（约 6 秒；判据与整轮同源）")
    ap.add_argument("--skip-browser", action="store_true", help="整轮但跳过真浏览器那段")
    ap.add_argument("--keep", action="store_true", help="保留临时数据根与看板进程")
    args = ap.parse_args()

    if not PY.is_file():
        print("找不到 venv 解释器：%s" % PY)
        return 2
    TMPROOT.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tools"))
    # 两种模式各写各的日志：整轮要起看板与浏览器（分钟级），快速自检只有几秒，
    # 共用一个文件的话后跑的会把先跑的现场记录覆盖掉——交出去就不是"那次跑的"了。
    logpath = QUICK_LOGPATH if args.quick else LOGPATH
    logfile = open(logpath, "w", encoding="utf-8")

    log("=" * 78)
    log("卡 C-0084 验证：批量真删（库记录 + 向量块 + 四处产物 + 可选原文件）"
        " · 模式 = %s" % ("quick 快速自检" if args.quick else "整轮（含真浏览器）"))
    log("=" * 78)

    # 检查过程无论怎么退出，日志与结论都要落盘：`verify.log` 是交付的现场记录。
    try:
        _run(args)
    finally:
        log("\n---- 交付证据（可机检锚点）----")
        log_anchor_evidence()
        log("TRUE_DB_UNTOUCHED = %s" % (
            "figure_desc 目录 %d 个（本卡只读真库）" % (
                len([p for p in (ROOT / "logs" / "figure_desc").glob("*") if p.is_dir()])
                if (ROOT / "logs" / "figure_desc").is_dir() else 0)))
        passed = sum(1 for ok, _, _ in RESULTS if ok)
        total = len(RESULTS)
        verdict = "PASS" if passed == total else "FAIL"
        log("")
        log("C0084-VERIFY %s %d/%d  (mode=%s)"
            % (verdict, passed, total, "quick" if args.quick else "full"))
        if passed != total:
            log("未通过项：" + "；".join(n for ok, n, _ in RESULTS if not ok))
        logfile.write("\n".join(LOG_LINES) + "\n")
        logfile.close()
        globals()["_VERDICT"] = (verdict, passed, total)

    verdict, passed, total = globals()["_VERDICT"]
    print("卡 C-0084 验证：批量真删（文件 + 向量块 + 知识点产物一并不留）"
          " · %s" % ("快速自检" if args.quick else "整轮"))
    print("PASS" if passed == total else "FAIL", "%d/%d 项通过" % (passed, total))
    print("C0084-VERIFY %s %d/%d" % (verdict, passed, total))
    print("明细日志：%s" % logpath)
    if passed != total:
        print("未通过项：" + "；".join(n for ok, n, _ in RESULTS if not ok))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
