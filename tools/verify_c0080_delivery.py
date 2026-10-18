# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
"""卡 C-0080 交付自检：**一条命令**复核"分享包的看板接口真的能用"。

它**不依赖浏览器、不碰用户真实库、不联网**，全部在一个临时根
`.c0080verify/` 里跑：

  1. 造一个最小库（1 份课件 / 3 个切块 / 3 条向量 / 1 个知识点产物）；
  2. 起一个**临时端口**的看板实例（跑的是同一份 `tools/dashboard.py`，
     只是库路径指向临时根）——8756 那个常驻实例一根手指都不碰；
  3. 依次打首页与 5 个接口：体积预估 / 导出 / 看包 / 导入 / 冲突跳过，
     外加"模型不一致"和"包被改坏"两条失败路径；
  4. 逐条断言，任何一条不过就以退出码 1 结束。

真浏览器（headless Edge + CDP，真点按钮）那一路是本卡的**主验证**，在
`tools/probe_c0080_share_ui.mjs` + `tools/verify_c0080_share_ui.py`；
本脚本只是让"交付物可用"这件事能被一条命令、30 秒内复核。

    venv\\Scripts\\python.exe tools\\verify_c0080_delivery.py
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
TMP = PROJ / ".c0080verify"
SHARE_DIR = PROJ / "share"
MIN_DOC_ID = 1
# ★ 文本里**不要有空格**：`core/share.py::_ensure_fts` 的探测串会把空白字符
#   剔掉再拿去 MATCH，而 trigram 是按三字符滑窗建索引的——原文里的空格会让
#   两边对不上，探测就报"未命中"（那是探测串的取法问题，不是导入坏了）。
#   本脚本要验的是"导入后检索可用"，所以给不含空格的语料。
TEXTS = [
    "第一章绪论：本验证用的最小课件正文第一段。",
    "第二章方法：切块与向量成对写入，导入后应当能被检索命中。",
    "第三章结论：分享包不调用任何模型，零接口费用。",
]


def _force_utf8_stdio() -> None:
    """控制台默认是 GBK，中文断言名会编不出来——固定成 UTF-8。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                       # noqa: BLE001
            pass


def _env_setup() -> None:
    for key, val in (("KB_RAW", TMP / "raw"), ("KB_NOTES", TMP / "notes"),
                     ("KB_INDEX", TMP / "index"),
                     ("KB_DB", TMP / "index" / "study.db"),
                     ("KB_LOG", TMP / "logs")):
        os.environ[key] = str(val)


def _make_min_library() -> int:
    """造一份最小课件：3 个切块 + 3 条向量 + 1 个知识点产物。"""
    from core import config, db, share as share_mod
    conn = db.connect(TMP / "index" / "study.db")
    db.init_db(conn)
    now = time.time()
    conn.execute(
        "INSERT INTO documents(path,abs_path,kind,title,content_hash,size_bytes,mtime,"
        "n_chunks,n_chars,status,role,corpus,warnings,extra,indexed_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("raw/验证课件.md", str(TMP / "raw" / "验证课件.md"), "text", "验证用最小课件",
         hashlib.sha256("c0080-verify".encode()).hexdigest(), 4096, now, len(TEXTS),
         sum(len(t) for t in TEXTS), "ok", "courseware", "main", "[]", "{}", now, now))
    doc_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    model, dim = str(config.EMBED_MODEL_REPO), int(config.EMBED_DIM)
    chunk_ids = []
    for i, text in enumerate(TEXTS):
        conn.execute(
            "INSERT INTO chunks(doc_id,ord,text,text_norm,heading_path,topic,source_ref,"
            "char_len,token_est,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, i, text, db.normalize_for_match(text), "第%d章" % (i + 1), "",
             "p.%d" % (i + 1), len(text), len(text) // 2,
             hashlib.sha256(text.encode()).hexdigest(), now))
        cid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        chunk_ids.append(cid)
        conn.execute("INSERT INTO embeddings(chunk_id,model,dim,vec) VALUES(?,?,?,?)",
                     (cid, model, dim, os.urandom(dim * 4)))
    conn.commit()
    conn.close()
    kdir = TMP / "logs" / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    (kdir / ("%d%s" % (doc_id, share_mod.KP_SUFFIX))).write_text(json.dumps({
        "meta": {"doc_id": doc_id, "built_at": "2026-10-10 00:00:00"},
        "knowledge_points": [{"name": "验证知识点", "summary": "来自最小课件的结论",
                              "chunk_ids": chunk_ids}],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc_id


def _load_dashboard():
    """加载看板模块。**不调用 main()**，所以不会去 restore 用户的学科注册表。"""
    spec = importlib.util.spec_from_file_location(
        "dash_c0080v", PROJ / "tools" / "dashboard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dash_c0080v"] = mod
    spec.loader.exec_module(mod)
    return mod


def _repack(src: Path, dest: Path, *, swap_embedding=None, break_chunks=False) -> None:
    """造副本：整包自洽地换成另一个向量模型 / 故意改坏一个 jsonl。"""
    with zipfile.ZipFile(src) as zf:
        items = {i.filename: zf.read(i.filename) for i in zf.infolist()}
    man = json.loads(items["manifest.json"].decode("utf-8"))
    if swap_embedding is not None:
        model, dim = swap_embedding
        rows = [json.loads(x) for x in
                items["embeddings.jsonl"].decode("utf-8").splitlines() if x.strip()]
        for row in rows:
            row["model"], row["dim"] = model, dim
            # 随机字节：零向量会把压缩比顶到 400+ 倍，先被"zip 炸弹"闸拦下
            row["vec_b64"] = base64.b64encode(os.urandom(dim * 4)).decode("ascii")
        blob = ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode("utf-8")
        items["embeddings.jsonl"] = blob
        man["embedding"] = {"model": model, "dim": dim}
        for f in (man.get("files") or []):
            if f.get("name") == "embeddings.jsonl":
                f["bytes"] = len(blob)
                f["sha256"] = hashlib.sha256(blob).hexdigest()
    if break_chunks:
        lines = [x for x in items["chunks.jsonl"].decode("utf-8").splitlines() if x.strip()]
        if lines:
            lines[0] = lines[0].replace("绪论", "绪论（已改坏）")
        items["chunks.jsonl"] = ("\n".join(lines) + "\n").encode("utf-8")
    items["manifest.json"] = json.dumps(man, ensure_ascii=False, indent=2).encode("utf-8")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in items.items():
            zf.writestr(name, data)


def _http(port: int, path: str, payload=None, timeout: int = 90):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data,
                                 method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _share_snapshot() -> set:
    return {p.name for p in SHARE_DIR.glob("*.zip")} if SHARE_DIR.is_dir() else set()


def _share_cleanup(before: set) -> list:
    out = []
    if not SHARE_DIR.is_dir():
        return out
    for p in sorted(SHARE_DIR.glob("*.zip")):
        if p.name not in before:
            try:
                p.unlink()
                out.append(p.name)
            except OSError:
                pass
    try:
        if not any(SHARE_DIR.iterdir()):
            SHARE_DIR.rmdir()
    except OSError:
        pass
    return out


def main() -> int:
    _force_utf8_stdio()
    results: list[tuple] = []

    def ok(name: str, cond, detail: str = "") -> bool:
        results.append((name, bool(cond), str(detail)))
        return bool(cond)

    if TMP.exists():
        shutil.rmtree(TMP, ignore_errors=True)
    for sub in ("raw", "index", "notes", "logs", "seed", "subjects"):
        (TMP / sub).mkdir(parents=True, exist_ok=True)
    _env_setup()
    sys.path.insert(0, str(PROJ))

    doc_id = _make_min_library()
    dash = _load_dashboard()
    from core import subjects as subjects_mod     # noqa: E402
    # 只改"学科目录在哪"：真实注册表与真实学科目录一个都不碰
    subjects_mod.subjects_root = lambda: TMP / "subjects"
    dash.config.ensure_dirs()

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = dash.ThreadingHTTPServer(("127.0.0.1", port), dash.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    before = _share_snapshot()
    try:
        # ---- 1. 首页里必须真的挂着新入口（不是只有后端接口）----
        st, html = _http(port, "/")
        ok("首页 200", st == 200, str(st))
        ok("首页含「导出」按钮的处理函数 openShareExport",
           "openShareExport" in html)
        ok("首页含「方式二：导入分享包」入口", "导入分享包" in html)
        ok("首页含「从 share 目录选一个」入口", "sharePickFromDir" in html)
        ok("首页含分享包进度轮询接口 /api/share/progress",
           "/api/share/progress" in html)
        ok("回归：资料页既有入口（入库 / 重建 / 移除 / 批量入库 / 清理）仍在首页里",
           all(k in html for k in ("入库全部待处理文件", "全部强制重建", "清理无出处的记录",
                                   "reindexOne", "deleteDoc", "batchConfirm")))

        # ---- 1b. 进度通道：导出/导入期间前端靠它显示进度 ----
        st, body = _http(port, "/api/share/progress")
        pr = json.loads(body)
        ok("进度接口可用（有 active/stage/done/total 字段）",
           all(k in pr for k in ("active", "stage", "done", "total")),
           json.dumps(pr, ensure_ascii=False)[:140])

        # ---- 2. 体积预估 ----
        st, body = _http(port, "/api/share/estimate", {"doc_id": doc_id})
        e = json.loads(body)
        ok("体积预估接口可用", e.get("ok") and e.get("n_chunks") == len(TEXTS),
           json.dumps(e, ensure_ascii=False)[:160])
        ok("预估区分了「带原文件 / 带页图」三档组合",
           all(k in e for k in ("est_base", "est_with_original", "est_with_pages",
                                "est_with_both")))

        # ---- 3. 导出 ----
        st, body = _http(port, "/api/share/export", {"doc_id": doc_id})
        ex = json.loads(body)
        ok("导出接口可用", ex.get("ok"), json.dumps(ex, ensure_ascii=False)[:200])
        zpath = Path(str(ex.get("zip") or ""))
        ok("导出的 zip 真的落盘且非空",
           zpath.is_file() and zpath.stat().st_size > 0,
           "%s (%s 字节)" % (zpath, zpath.stat().st_size if zpath.is_file() else 0))

        # ---- 4. 看包：标题/来源/导出时间/包含什么/体积/需要多久 ----
        st, body = _http(port, "/api/share/inspect", {"zip": str(zpath)})
        ins = json.loads(body)
        ok("看包接口可用且判定为可导入",
           ins.get("ok") and ins.get("importable"), json.dumps(ins, ensure_ascii=False)[:200])
        ok("看包给了 标题/来源/导出时间",
           bool(ins.get("title")) and bool((ins.get("source") or {}).get("subject"))
           and bool(ins.get("created_at")))
        ok("看包给了 包含什么/体积/预计耗时",
           "includes" in ins and int(ins.get("bytes") or 0) > 0
           and int(ins.get("est_seconds") or 0) > 0)
        ok("看包给条目清单与知识点产物",
           int(ins.get("entry_total") or 0) > 0 and isinstance(ins.get("knowledge"), list))

        # ---- 5. 导入（另存一份，绕开与源库同 content_hash 的冲突）----
        st, body = _http(port, "/api/share/import",
                         {"zip": str(zpath), "subject": "", "on_conflict": "duplicate"})
        im = json.loads(body)
        ok("导入接口可用（另存一份）",
           im.get("ok") and im.get("status") == "imported",
           json.dumps(im, ensure_ascii=False)[:200])
        ok("导入写入的切块/向量数与包一致",
           (im.get("counts") or {}).get("chunks") == len(TEXTS)
           and (im.get("counts") or {}).get("embeddings") == len(TEXTS),
           json.dumps(im.get("counts"), ensure_ascii=False))
        ok("导入后全文检索探测可用", bool((im.get("fts") or {}).get("ok")),
           json.dumps(im.get("fts"), ensure_ascii=False)[:160])
        # ⚠ 别写成 `x or -1`：0 与 0.0 都是 falsy，那样写会把"零调用零费用"
        #   判成失败（这个坑本脚本自己踩过一次）。
        ok("导入零模型调用、零费用",
           int(im.get("model_calls", -1)) == 0
           and float(im.get("cost_cny", -1)) == 0.0,
           "model_calls=%r cost_cny=%r" % (im.get("model_calls"), im.get("cost_cny")))

        # ---- 6. 冲突：默认跳过 ----
        st, body = _http(port, "/api/share/import",
                         {"zip": str(zpath), "subject": "", "on_conflict": "skip"})
        sk = json.loads(body)
        ok("冲突（同 content_hash）默认跳过，并说明本库已有哪一条",
           sk.get("status") == "skipped" and sk.get("doc_id"),
           json.dumps(sk, ensure_ascii=False)[:200])

        # ---- 7. 失败路径：模型不一致 / 包被改坏 ----
        bad = TMP / "seed" / "badmodel.zip"
        _repack(zpath, bad, swap_embedding=("Xenova/bge-large-zh-v1.5", 1024))
        st, body = _http(port, "/api/share/inspect", {"zip": str(bad)})
        b = json.loads(body)
        reason = str(b.get("reason") or "")
        ok("模型不一致：拒绝导入", b.get("importable") is False and b.get("reason_kind") == "model")
        ok("模型不一致：原因里同时给出「包里的模型」与「本库的模型」",
           str((b.get("embedding") or {}).get("model")) in reason
           and str((b.get("local_embedding") or {}).get("model")) in reason,
           reason[:200])
        broke = TMP / "seed" / "broken.zip"
        _repack(zpath, broke, break_chunks=True)
        st, body = _http(port, "/api/share/inspect", {"zip": str(broke)})
        bb = json.loads(body)
        ok("包被改坏：报出 sha256 校验失败并让人重新导出",
           "sha256" in str(bb.get("error") or ""), str(bb.get("error"))[:160])
        notzip = TMP / "seed" / "notzip.zip"
        notzip.write_bytes("not a zip".encode("utf-8"))
        st, body = _http(port, "/api/share/inspect", {"zip": str(notzip)})
        nn = json.loads(body)
        ok("根本不是 zip：报出「不是有效的 zip 文件」",
           "不是有效的 zip" in str(nn.get("error") or ""), str(nn.get("error"))[:160])
    finally:
        srv.shutdown()
        removed = _share_cleanup(before)

    passed = sum(1 for _, good, _ in results if good)
    failed = [r for r in results if not r[1]]
    for name, good, detail in results:
        print("  [%s] %s%s" % ("OK" if good else "XX", name,
                               ("  <- " + detail) if (detail and not good) else ""))
    print("本次在 share\\ 里新增并已清理：%s" % (removed or "无"))
    print("临时根：%s（可随时删；下次运行会重建）" % TMP)
    print("CONCLUSION: %s  %d/%d" % ("PASS" if not failed else "FAIL", passed, len(results)))
    if failed:
        for name, _, detail in failed:
            print("FAILED: %s | %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
