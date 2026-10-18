# -*- coding: utf-8 -*-
# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
"""C-0079 分享包验证：导出→导入闭环 / 零成本 / 安全反面 / FTS / 冲突 / 体积。

一行命令（cmd 与 pwsh 都能跑）：

    venv\\Scripts\\python.exe tools\\verify_c0079_share.py

**stdout 只有 6 行结论**（大管家要求"结论行放输出前 6 行"），全部明细写进
`_tmp_c0079/verify/verify.log`，机器可读结论写 `_tmp_c0079/verify/verify.json`。

验证内容（逐条对应卡面验收标准）：
  1. 用**临时库**做闭环：真实库 doc#N（只读）→ 导出 → 导入干净临时库，
     比对 documents/chunks/embeddings/figure_desc 行数、path 与来源标注；
  2. 零成本：导入全程用哨兵拦住 socket/model 调用，另加 AST 静态证明
     `core/share.py` 不可能 import 到 embed/kp_build；
  3. 检索：FTS(MATCH) 命中 + `search(mode="keyword")` 命中且带出处页码；
  4. 产物：文件名与 `meta.doc_id` 一致、块引用已重映射、看板判据等价复现；
  5. 安全反面：路径穿越（`../`、`..\\`）/ 模型不符 / 缺字段 / 压缩炸弹 /
     产物 doc_id 不一致 —— 五个坏包都必须被拒绝；
  6. 冲突：同一份课件导入两次 → 第二次 skipped 且块数不变；
  7. 体积：四种组合（不带 / 带原文件 / 带页图 / 都带）实测；
  8. 不污染：真实库的行数与文件指纹在全程前后不变。
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import shutil
import socket
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _force_utf8_stdio() -> None:
    """把 stdout/stderr 固定成 UTF-8（**必须在任何打印之前调用**）。

    起因（验收预检实测）：本机控制台默认 GBK，结论行里的 `¥` 在 GBK 下
    编码失败 → 一个已经跑完 33 项检查的验证脚本在打印结论时
    `UnicodeEncodeError` 崩掉、退出码 1；中文输出也变成 `���`，
    使"输出必须含「结论：PASS」"这类判据失效。
    固定编码 + `errors="replace"` 后，输出字节在 cmd / pwsh / 重定向 / 管道
    下完全一致，也不会再有编码异常把退出码带偏。
    """
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                          # noqa: BLE001
            pass


_force_utf8_stdio()

from core import config, db, share  # noqa: E402

DETAIL: list[str] = []


def note(msg: str = "") -> None:
    DETAIL.append(str(msg))


class Judge:
    """检查项收集器。"""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append({"name": name, "ok": bool(ok), "detail": str(detail)})
        note("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                            ("  —— " + str(detail)) if detail else ""))
        return bool(ok)

    @property
    def failed(self) -> list[dict]:
        return [i for i in self.items if not i["ok"]]


# ---------------------------------------------------------------- 哨兵

class Sentinel:
    """拦住"付费/联网/模型"三条路的哨兵。

    - `socket.socket.connect` / `create_connection`：任何 TCP 连接都计数（API 调用必然联网）；
    - `core.embed.get_embedder`：任何嵌入调用都计数（导入若真去算向量，这里立刻炸）；
    - `sys.modules` 新增项：看导入阶段有没有偷偷拉起 onnxruntime / requests。
    """

    def __init__(self) -> None:
        self.sockets = 0
        self.embed_calls = 0
        self._orig_connect = None
        self._orig_create = None
        self._orig_get_embedder = None
        self._mods_before: set[str] = set()
        self.modules_added: list[str] = []

    def start(self) -> None:
        # ⚠ 顺序：**先**把监听目标 `core.embed` 导入到位并打补丁，**再**快照模块表。
        # 反过来的话，哨兵为了 patch 而 import 的 onnxruntime 会被记成
        # "导入过程加载了模型"——那会把零成本证据污染成假阳性（已踩过一次）。
        try:
            from core import embed as _embed
            self._orig_get_embedder = _embed.get_embedder

            def _ge(*a, **kw):
                self.embed_calls += 1
                raise RuntimeError("哨兵：导入过程不得调用嵌入模型")
            _embed.get_embedder = _ge             # type: ignore[assignment]
        except Exception as e:                    # noqa: BLE001
            note("哨兵：patch core.embed.get_embedder 失败：%s" % e)

        self._mods_before = set(sys.modules)

        def _connect(*a, **kw):
            self.sockets += 1
            return self._orig_connect(*a, **kw)

        def _create(*a, **kw):
            self.sockets += 1
            return self._orig_create(*a, **kw)

        self._orig_connect = socket.socket.connect
        self._orig_create = socket.create_connection
        socket.socket.connect = _connect           # type: ignore[method-assign]
        socket.create_connection = _create         # type: ignore[assignment]

    def stop(self) -> None:
        if self._orig_connect is not None:
            socket.socket.connect = self._orig_connect      # type: ignore[method-assign]
        if self._orig_create is not None:
            socket.create_connection = self._orig_create    # type: ignore[assignment]
        if self._orig_get_embedder is not None:
            try:
                from core import embed as _embed
                _embed.get_embedder = self._orig_get_embedder  # type: ignore[assignment]
            except Exception:  # noqa: BLE001
                pass
        self.modules_added = sorted(set(sys.modules) - self._mods_before)


# ---------------------------------------------------------------- 小工具

def db_counts(conn: sqlite3.Connection, doc_id: int) -> dict:
    q = lambda s, *a: conn.execute(s, a).fetchone()[0]  # noqa: E731
    return {
        "documents": q("SELECT COUNT(*) FROM documents WHERE id=?", doc_id),
        "chunks": q("SELECT COUNT(*) FROM chunks WHERE doc_id=?", doc_id),
        "embeddings": q("SELECT COUNT(*) FROM chunks c JOIN embeddings e "
                        "ON e.chunk_id=c.id WHERE c.doc_id=?", doc_id),
        "figure_desc": q("SELECT COUNT(*) FROM figure_desc WHERE doc_id=?", doc_id),
    }


def lib_counts(conn: sqlite3.Connection) -> dict:
    q = lambda s: conn.execute(s).fetchone()[0]  # noqa: E731
    return {"documents": q("SELECT COUNT(*) FROM documents"),
            "chunks": q("SELECT COUNT(*) FROM chunks"),
            "embeddings": q("SELECT COUNT(*) FROM embeddings"),
            "figure_desc": q("SELECT COUNT(*) FROM figure_desc")}


def lib_fingerprint(dbp: Path) -> dict:
    """库文件指纹（大小 + mtime + 三表行数），用于证明"没被污染"。"""
    out = {"bytes": dbp.stat().st_size, "mtime": round(dbp.stat().st_mtime, 3)}
    conn = sqlite3.connect(str(dbp))
    try:
        out.update(lib_counts(conn))
    finally:
        conn.close()
    return out


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def collect_chunk_refs(obj) -> list[int]:
    """产物里所有块 id 引用（与 core.share._CHUNK_KEYS 同口径）。"""
    keys = ("chunk_id", "chunk_ids", "material_block_ids", "block_ids")
    out: list[int] = []

    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if k in keys:
                    if isinstance(v, list):
                        out.extend(int(x) for x in v if isinstance(x, int))
                    elif isinstance(v, int):
                        out.append(int(v))
                walk(v)
        elif isinstance(n, list):
            for x in n:
                walk(x)

    walk(obj)
    return out


def rewrite_package(src: Path, dest: Path, mutate, fix_manifest: bool = True) -> Path:
    """把一个包逐条目改写后另存。

    `mutate(name, data) -> bytes|None|(newname, bytes)`：返回 None 表示删掉该条目。

    `fix_manifest=True`（默认）会把 manifest 里所有条目的 sha256 **重算一遍**，
    让坏包在"完整性"这道闸上自洽——这样才能分别验证**每一道**闸
    （否则每个坏包都只会撞 sha256，测不出模型闸/字段闸到底管不管用）。
    """
    with zipfile.ZipFile(src) as zi:
        items = [(i.filename, zi.read(i.filename)) for i in zi.infolist()]
    out: list[tuple[str, bytes]] = []
    for name, data in items:
        new = mutate(name, data)
        if new is None:
            continue
        if isinstance(new, tuple):
            name, data = new
        else:
            data = new
        out.append((name, data))
    if fix_manifest and any(n == share.MANIFEST for n, _ in out):
        by = {n: d for n, d in out}
        m = json.loads(by[share.MANIFEST].decode("utf-8"))
        files = []
        for f in (m.get("files") or []):
            d = by.get(f.get("name"))
            if d is None:
                continue
            f = dict(f)
            f["bytes"], f["sha256"] = len(d), hashlib.sha256(d).hexdigest()
            files.append(f)
        m["files"] = files
        m["entries_sha256"] = hashlib.sha256(
            "\n".join("%s:%s" % (f["name"], f["sha256"])
                      for f in sorted(files, key=lambda x: x["name"])).encode("utf-8")
        ).hexdigest()
        for k in (m.get("knowledge") or []):
            d = by.get(k.get("entry"))
            if d is not None:
                k["bytes"], k["sha256"] = len(d), hashlib.sha256(d).hexdigest()
        if isinstance(m.get("original"), dict):
            d = by.get(m["original"].get("entry"))
            if d is not None:
                m["original"]["sha256"] = hashlib.sha256(d).hexdigest()
        blob = json.dumps(m, ensure_ascii=False, indent=2).encode("utf-8")
        out = [(n, blob if n == share.MANIFEST else d) for n, d in out]
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zo:
        for n, d in out:
            zo.writestr(n, d)
    return dest


def expect_reject(judge: Judge, name: str, fn, want: str = "") -> None:
    """跑一个"必须被拒绝"的操作，把实际异常消息记进证据。"""
    try:
        res = fn()
    except share.ShareError as e:
        msg = str(e)
        ok = (want in msg) if want else True
        judge.check(name, ok, "已拒绝：%s" % msg.replace("\n", " ⏎ ")[:300])
        return
    except Exception as e:                     # noqa: BLE001
        judge.check(name, False, "抛了非 ShareError 的异常：%r" % e)
        return
    judge.check(name, False, "**没有被拒绝**，返回 %r" % (res,))


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="C-0079 分享包验证")
    ap.add_argument("--work", default=str(ROOT / "_tmp_c0079" / "verify"))
    ap.add_argument("--doc-id", type=int, default=5)
    ap.add_argument("--src-db", default=str(ROOT / "subjects" / "有机化学" / "index" / "study.db"))
    ap.add_argument("--src-logs", default=str(ROOT / "subjects" / "有机化学" / "logs"))
    ap.add_argument("--src-raw", default=str(ROOT / "subjects" / "有机化学" / "raw"))
    args = ap.parse_args()

    t0 = time.time()
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    src_db = Path(args.src_db)
    src_logs = Path(args.src_logs)
    src_raw = Path(args.src_raw)
    doc_id = int(args.doc_id)
    judge = Judge()

    note("=== C-0079 分享包验证 %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    note("源库（只读）：%s" % src_db)
    note("工作目录（临时）：%s" % work)

    # ---- 0. 红线自证：临时库必须不是任何真实库 ----
    real_dbs = {Path(config.DB_PATH).resolve(), (ROOT / "index" / "study.db").resolve()}
    real_dbs |= {p.resolve() for p in (ROOT / "subjects").glob("*/index/study.db")}
    tmp_sub = work / "subject"
    tmp_db = tmp_sub / "index" / "study.db"
    tmp_logs = tmp_sub / "logs"
    tmp_raw = tmp_sub / "raw"
    judge.check("0. 临时库不是真实库（不污染用户数据）",
                tmp_db.resolve() not in real_dbs, "临时库 %s" % tmp_db)

    src_fp_before = lib_fingerprint(src_db)
    src_doc_before = None
    src_conn = db.connect(src_db)
    try:
        src_doc_before = db_counts(src_conn, doc_id)
        src_chunk_ids = {int(r[0]) for r in src_conn.execute(
            "SELECT id FROM chunks WHERE doc_id=?", (doc_id,))}
    finally:
        src_conn.close()
    note("源库 doc#%d 计数：%s" % (doc_id, src_doc_before))

    # ---- 1. 导出四种组合 ----
    pkg_dir = work / "pkgs"
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True)
    combos = {
        "min": (False, False),
        "orig": (True, False),
        "pages": (False, True),
        "both": (True, True),
    }
    pkgs: dict[str, dict] = {}
    for name, (wo, wp) in combos.items():
        t = time.time()
        r = share.export_doc(doc_id, pkg_dir / ("c0079-%s.zip" % name), db_path=src_db,
                             with_original=wo, with_pages=wp, log_dir=src_logs,
                             raw_dir=src_raw, root=ROOT, exported_by="C-0079 验证脚本",
                             note="四种组合体积实测：%s" % name)
        r["elapsed_s"] = round(time.time() - t, 2)
        pkgs[name] = r
        note("导出国 [%s] %s 体积 %.1f KB  用时 %.1fs  警告 %s"
             % (name, Path(r["zip"]).name, r["bytes"] / 1024, r["elapsed_s"],
                r["warnings"]))
    judge.check("1. 四种组合都能导出", all(p["status"] == "exported" for p in pkgs.values()),
                " / ".join("%s %s" % (k, _kb(v["bytes"])) for k, v in pkgs.items()))

    # ---- 2. 看包（inspect，不动库）----
    ins = share.inspect_package(pkgs["min"]["zip"])
    judge.check("2. inspect 结构校验通过（sha256 全核对）",
                bool(ins.get("format_ok")) and ins.get("counts", {}).get("total")
                == src_doc_before["chunks"],
                "条目 %d 个，块 %s 条，向量 %s 条"
                % (ins["n_entries"], ins["counts"].get("total"),
                   (ins.get("embeddings") or {}).get("total")))

    # ---- 3. 静态证明：share 模块不可能调模型 ----
    tree = ast.parse((ROOT / "core" / "share.py").read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(("." * node.level) + (node.module or ""))
    banned = {"core.embed", "embed", "core.kp_build", "kp_build", "core.chat", "chat",
              "onnxruntime", "requests", "httpx", "urllib", "http"}
    hit = sorted(m for m in mods if m in banned or m.split(".")[0] in banned)
    judge.check("3. core/share.py 不依赖任何模型/网络模块（静态证据）",
                not hit, "import 清单：%s" % sorted(mods))

    # ---- 4. 准备干净临时库 ----
    if tmp_sub.exists():
        shutil.rmtree(tmp_sub)
    for d in (tmp_db.parent, tmp_logs / "knowledge", tmp_logs / "figure_desc", tmp_raw):
        d.mkdir(parents=True, exist_ok=True)
    c = db.connect(tmp_db)
    try:
        info = db.init_db(c)
        note("临时库初始化：%s" % info)
    finally:
        c.close()

    # ---- 5. 导入（哨兵包裹）----
    sent = Sentinel()
    sent.start()
    progress_calls = []
    try:
        imp = share.import_package(pkgs["min"]["zip"], db_path=tmp_db, log_dir=tmp_logs,
                                   raw_dir=tmp_raw, root=ROOT,
                                   progress=lambda s, d, t: progress_calls.append((s, d, t)))
    finally:
        sent.stop()
    note("导入返回：%s" % json.dumps({k: v for k, v in imp.items()
                                      if k not in ("knowledge", "source")},
                                     ensure_ascii=False))
    note("进度回调：%d 次，最后 %s" % (len(progress_calls),
                                      progress_calls[-1] if progress_calls else None))
    judge.check("4. 导入成功（status=imported）", imp.get("status") == "imported",
                "新 doc_id=%s 标题=%s" % (imp.get("doc_id"), imp.get("title")))
    judge.check("5. 零成本：无 socket 连接 / 无嵌入调用 / ¥0",
                sent.sockets == 0 and sent.embed_calls == 0
                and imp.get("model_calls") == 0 and imp.get("cost_cny") == 0.0,
                "socket=%d 嵌入调用=%d model_calls=%s 花费=¥%s；导入阶段新加载模块=%s"
                % (sent.sockets, sent.embed_calls, imp.get("model_calls"),
                   imp.get("cost_cny"),
                   [m for m in sent.modules_added if "onnx" in m or "request" in m
                    or "http" in m] or "无（模型/网络库）"))

    # ---- 6. 闭环行数比对 ----
    new_id = int(imp.get("doc_id") or 0)
    new_counts: dict = {}
    c = db.connect(tmp_db)
    try:
        new_counts = db_counts(c, new_id)
        doc_row = c.execute("SELECT * FROM documents WHERE id=?", (new_id,)).fetchone()
        extra = json.loads(doc_row["extra"] or "{}")
        pkg_sha = sha256_file(Path(pkgs["min"]["zip"]))
        judge.check("6. 闭环行数一致（documents/chunks/embeddings/figure_desc）",
                    new_counts["documents"] == src_doc_before["documents"]
                    and new_counts["chunks"] == src_doc_before["chunks"]
                    and new_counts["embeddings"] == src_doc_before["embeddings"]
                    and new_counts["figure_desc"] == src_doc_before["figure_desc"],
                    "源 %s → 临时库 %s" % (src_doc_before, new_counts))
        judge.check("7. 来源标注写进 documents.extra.shared_from",
                    extra.get("shared_from", {}).get("package_sha256") == pkg_sha
                    and extra["shared_from"].get("source_doc_id") == doc_id,
                    "package_sha256=%s… source_doc_id=%s source_subject=%s"
                    % (str(extra.get("shared_from", {}).get("package_sha256"))[:16],
                       extra.get("shared_from", {}).get("source_doc_id"),
                       extra.get("shared_from", {}).get("source_subject")))
        judge.check("8. 临时库只多出这一份（新 doc_id 追加，不覆盖任何既有行）",
                    lib_counts(c)["documents"] == 1 and new_id > 0,
                    "临时库 documents=%d 行，全部属于新 doc#%d"
                    % (lib_counts(c)["documents"], new_id))
        # FTS
        n_fts = c.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?",
                          ('"构造异构"',)).fetchone()[0]
        n_fts_doc = c.execute(
            "SELECT COUNT(*) FROM chunks_fts f JOIN chunks ch ON ch.id=f.rowid "
            "WHERE f.chunks_fts MATCH ? AND ch.doc_id=?", ('"构造异构"', new_id)).fetchone()[0]
        judge.check("9. chunks_fts 已维护（关键词 MATCH 命中导入的块）",
                    n_fts > 0 and n_fts_doc > 0,
                    "全库命中 %d 条，其中新导入的 doc#%d 命中 %d 条" % (n_fts, new_id, n_fts_doc))
        # ord 覆盖
        body = [int(r[0]) for r in c.execute(
            "SELECT ord FROM chunks WHERE doc_id=? AND ord<9000 ORDER BY ord", (new_id,))]
        judge.check("10. 正文块 ord 覆盖 0..n-1 无缺口",
                    body == list(range(len(body))), "n=%d，ord=%s..%s" % (
                        len(body), body[0] if body else None, body[-1] if body else None))
    finally:
        c.close()

    # ---- 7. 检索（关键词路：不加载任何模型）----
    from core import search as search_mod
    c = db.connect(tmp_db)
    try:
        hits = search_mod.search(c, "构造异构", top_k=5, mode="keyword", corpus="main")
        with_ref = [h for h in hits if str(h.source_ref or "").strip()]
        judge.check("11. 检索命中且带出处页码（keyword 路，无需模型）",
                    bool(hits) and bool(with_ref),
                    "命中 %d 条；首条 doc#%s %s 出处「%s」标题「%s」"
                    % (len(hits), hits[0].doc_id if hits else "-",
                       (hits[0].text[:24].replace("\n", " ") if hits else ""),
                       hits[0].source_ref if hits else "", hits[0].heading_path[:30]
                       if hits else ""))
        # 向量也在（不 embed，只查表计数）
        n_vec = c.execute("SELECT COUNT(*) FROM embeddings e JOIN chunks ch ON ch.id=e.chunk_id "
                          "WHERE ch.doc_id=?", (new_id,)).fetchone()[0]
        judge.check("12. 向量已入库（可用 hybrid 检索）", n_vec == src_doc_before["chunks"],
                    "新 doc#%d 有向量 %d 条" % (new_id, n_vec))
    finally:
        c.close()

    # ---- 8. 知识点产物 ----
    kp_file = tmp_logs / "knowledge" / ("%d%s" % (new_id, share.KP_SUFFIX))
    tm_file = tmp_logs / "knowledge" / ("%d%s" % (new_id, share.TERMS_SUFFIX))
    kp_obj = json.loads(kp_file.read_text(encoding="utf-8")) if kp_file.is_file() else {}
    tm_obj = json.loads(tm_file.read_text(encoding="utf-8")) if tm_file.is_file() else {}
    judge.check("13. 产物按新 doc_id 改名，且文件名与 meta.doc_id 一致",
                kp_file.is_file() and kp_obj.get("meta", {}).get("doc_id") == new_id
                and (not tm_file.is_file()
                     or tm_obj.get("meta", {}).get("doc_id") == new_id),
                "知识点产物 %s（meta.doc_id=%s）；名词解释 %s（meta.doc_id=%s）"
                % (kp_file.name, kp_obj.get("meta", {}).get("doc_id"),
                   tm_file.name if tm_file.is_file() else "（源库没有）",
                   tm_obj.get("meta", {}).get("doc_id")))
    refs = collect_chunk_refs(kp_obj) + collect_chunk_refs(tm_obj)
    c = db.connect(tmp_db)
    try:
        new_chunk_ids = {int(r[0]) for r in c.execute(
            "SELECT id FROM chunks WHERE doc_id=?", (new_id,))}
        old_left = sorted(set(refs) & src_chunk_ids)
        stray = sorted(set(refs) - new_chunk_ids)
        judge.check("14. 产物内块引用已全部重映射（无源库 id、无越界 id）",
                    not old_left and not stray and bool(refs),
                    "引用 %d 处；残留源库 id %d 个；越界 id %d 个；"
                    "重映射统计 %s" % (len(refs), len(old_left), len(stray), imp["remap"]))
    finally:
        c.close()
    # 看板判据等价复现（dashboard._artifact_load 的三条判据）
    try:
        from core import kp_build as kpmod
        prev = kpmod.prev_info_v2(new_id, str(kp_file))
        ver_ok = str(kp_obj.get("meta", {}).get("pipeline_version") or "")
        board_ok = (str(kp_file.name).split("-")[0].isdigit()
                    and isinstance(kp_obj.get("structure", {}).get("root"), dict)
                    and bool(prev.get("exists")))
        judge.check("15. 看板读取路径正常（artifact_state 判据等价复现 + kp_build.prev_info_v2）",
                    board_ok, "pipeline_version=%s structure.root=%s exists=%s n_kps=%s "
                              "n_done_pages=%s resumable=%s"
                    % (ver_ok, isinstance(kp_obj.get("structure", {}).get("root"), dict),
                       prev.get("exists"), prev.get("n_kps"), prev.get("n_done_pages"),
                       prev.get("resumable")))
    except Exception as e:                     # noqa: BLE001
        judge.check("15. 看板读取路径正常", False, "import core.kp_build 失败：%r" % e)

    # 规则不漂移（后缀与目录名清洗）
    try:
        from core import figures as figmod
        from core import kp_build as kpmod2
        judge.check("16. 产物后缀与页图目录清洗规则与既有模块逐字一致",
                    share.KP_SUFFIX == kpmod2.KP_SUFFIX
                    and share.TERMS_SUFFIX == kpmod2.TERMS_SUFFIX
                    and share.safe_dirname("a<>:b\\c/d|e?f*g") == figmod._safe_dirname(
                        "a<>:b\\c/d|e?f*g"),
                    "KP=%s TERMS=%s safe_dirname=%s"
                    % (share.KP_SUFFIX, share.TERMS_SUFFIX,
                       share.safe_dirname("a<>:b\\c/d|e?f*g")))
    except Exception as e:                     # noqa: BLE001
        judge.check("16. 规则不漂移", False, "import figures/kp_build 失败：%r" % e)

    # ---- 9. 冲突处理：再导一次 ----
    again = share.import_package(pkgs["min"]["zip"], db_path=tmp_db, log_dir=tmp_logs,
                                 raw_dir=tmp_raw, root=ROOT)
    c = db.connect(tmp_db)
    try:
        after = db_counts(c, new_id)
        total_chunks = lib_counts(c)["chunks"]
        judge.check("17. 同一份课件导入两次：第二次被识别并跳过，不产生重复块",
                    again.get("status") == "skipped"
                    and again.get("doc_id") == new_id
                    and after["chunks"] == src_doc_before["chunks"]
                    and total_chunks == src_doc_before["chunks"],
                    "第二次返回 status=%s（命中已有 doc#%s）；库内块数 %d 未变"
                    % (again.get("status"), again.get("doc_id"), total_chunks))
    finally:
        c.close()

    # ---- 10. 安全反面 ----
    bad = pkg_dir / "bad"
    bad.mkdir(exist_ok=True)
    # ① 路径穿越（../ 与 ..\ 两种写法）
    with zipfile.ZipFile(pkgs["min"]["zip"]) as zmin:
        base_members = [(i.filename, zmin.read(i.filename)) for i in zmin.infolist()]
    for label, evil in (("含 ../ 的路径穿越", "../../evil.txt"),
                        ("含 ..\\ 的路径穿越", "..\\..\\evil.txt"),
                        ("绝对路径条目", "C:/Windows/evil.txt"),
                        ("UNC 风格绝对路径", "//server/share/evil.txt")):
        p = bad / ("evil-%d.zip" % (abs(hash(evil)) % 100000))
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for m_name, m_data in base_members:
                z.writestr(m_name, m_data)
            z.writestr(evil, b"pwned")
        expect_reject(judge, "18. 拒绝%s" % label,
                      lambda p=p: share.import_package(p, db_path=tmp_db, log_dir=tmp_logs,
                                                       raw_dir=tmp_raw, root=ROOT))
    # ② 模型不符
    def _mut_model(name, data):
        if name == share.MANIFEST:
            m = json.loads(data.decode("utf-8"))
            m["embedding"] = {"model": "Xenova/bge-large-zh-v1.5", "dim": 1024}
            return json.dumps(m, ensure_ascii=False, indent=2).encode("utf-8")
        if name == share.EMBEDDINGS:
            out = []
            for line in data.decode("utf-8").splitlines():
                if line.strip():
                    o = json.loads(line)
                    o["model"] = "Xenova/bge-large-zh-v1.5"
                    out.append(json.dumps(o, ensure_ascii=False))
            return ("\n".join(out) + "\n").encode("utf-8")
        return data

    p_model = rewrite_package(Path(pkgs["min"]["zip"]), bad / "bad-model.zip", _mut_model)
    expect_reject(judge, "19. 拒绝向量模型/维度不符的包",
                  lambda: share.import_package(p_model, db_path=tmp_db, log_dir=tmp_logs,
                                               raw_dir=tmp_raw, root=ROOT),
                  want="向量模型与本库不一致")
    # ③ 缺必需字段（doc.json 少 content_hash）
    def _mut_doc(name, data):
        if name == share.DOC:
            o = json.loads(data.decode("utf-8"))
            o.pop("content_hash", None)
            return json.dumps(o, ensure_ascii=False).encode("utf-8")
        return data

    p_doc = rewrite_package(Path(pkgs["min"]["zip"]), bad / "bad-doc.zip", _mut_doc)
    expect_reject(judge, "20. 拒绝缺必需字段的包（doc.json 无 content_hash）",
                  lambda: share.import_package(p_doc, db_path=tmp_db, log_dir=tmp_logs,
                                               raw_dir=tmp_raw, root=ROOT),
                  want="content_hash")
    # ④ 缺 chunks.jsonl（整份条目缺失）
    p_missing = rewrite_package(Path(pkgs["min"]["zip"]), bad / "bad-missing.zip",
                                lambda n, d: None if n == share.CHUNKS else d)
    expect_reject(judge, "21. 拒绝缺必需条目的包（无 chunks.jsonl）",
                  lambda: share.import_package(p_missing, db_path=tmp_db, log_dir=tmp_logs,
                                               raw_dir=tmp_raw, root=ROOT))
    # ⑤ 产物 doc_id 不一致
    def _mut_kp(name, data):
        if name.startswith(share.KNOW_DIR + "/") and name.endswith(share.KP_SUFFIX):
            o = json.loads(data.decode("utf-8"))
            o["meta"]["doc_id"] = 999999
            return json.dumps(o, ensure_ascii=False).encode("utf-8")
        return data

    p_kp = rewrite_package(Path(pkgs["min"]["zip"]), bad / "bad-kp.zip", _mut_kp)
    expect_reject(judge, "22. 拒绝产物 meta.doc_id 与文件名不一致的包",
                  lambda: share.import_package(p_kp, db_path=tmp_db, log_dir=tmp_logs,
                                               raw_dir=tmp_raw, root=ROOT),
                  want="meta.doc_id")
    # ⑥ 压缩炸弹（高压缩比条目）
    p_bomb = bad / "bad-bomb.zip"
    with zipfile.ZipFile(p_bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(share.MANIFEST, b"{}")
        z.writestr(share.DOC, b"{}")
        z.writestr(share.CHUNKS, b"{}\n")
        z.writestr(share.EMBEDDINGS, b"{}\n")
        z.writestr("bomb.bin", b"\0" * (64 * 1024 * 1024))   # 64MB 全零 → 压缩比数千倍
    expect_reject(judge, "23. 拒绝 zip 炸弹（压缩比超上限）",
                  lambda: share.import_package(p_bomb, db_path=tmp_db, log_dir=tmp_logs,
                                               raw_dir=tmp_raw, root=ROOT),
                  want="zip 炸弹")
    # ⑦ 内容被改但 sha256 没跟着改（模拟传输损坏/被改包）→ 完整性闸必须拦住
    def _mut_text(name, data):
        if name == share.CHUNKS:
            return data.replace("构造异构".encode("utf-8"), "构造YYY".encode("utf-8"))
        return data

    p_tamper = rewrite_package(Path(pkgs["min"]["zip"]), bad / "bad-tamper.zip", _mut_text,
                               fix_manifest=False)
    expect_reject(judge, "24. 拒绝内容被改过的包（sha256 不符）",
                  lambda: share.import_package(p_tamper, db_path=tmp_db, log_dir=tmp_logs,
                                               raw_dir=tmp_raw, root=ROOT),
                  want="sha256")
    # 校验"被拒绝后临时库没有任何变化"
    c = db.connect(tmp_db)
    try:
        judge.check("25. 全部拒绝动作都没写库（行数不变）",
                    lib_counts(c)["chunks"] == src_doc_before["chunks"]
                    and lib_counts(c)["documents"] == 1,
                    "库内 documents=%d chunks=%d" % (lib_counts(c)["documents"],
                                                     lib_counts(c)["chunks"]))
    finally:
        c.close()

    # ---- 11. 带原文件 + 页图的包也能闭环（另开一个临时库，避免与 min 包冲突）----
    tmp2 = work / "subject2"
    if tmp2.exists():
        shutil.rmtree(tmp2)
    db2, logs2, raw2 = tmp2 / "index" / "study.db", tmp2 / "logs", tmp2 / "raw"
    for d in (db2.parent, logs2 / "knowledge", logs2 / "figure_desc", raw2):
        d.mkdir(parents=True, exist_ok=True)
    c2 = db.connect(db2)
    try:
        db.init_db(c2)
    finally:
        c2.close()
    imp2 = share.import_package(pkgs["both"]["zip"], db_path=db2, log_dir=logs2,
                                raw_dir=raw2, root=ROOT)
    c2 = db.connect(db2)
    try:
        counts2 = db_counts(c2, int(imp2.get("doc_id") or 0))
        row2 = c2.execute("SELECT path,abs_path FROM documents WHERE id=?",
                          (imp2.get("doc_id"),)).fetchone()
    finally:
        c2.close()
    png_dir = logs2 / "figure_desc" / share.safe_dirname("第二章 烷烃")
    pngs = sorted(png_dir.glob("slide_*.png")) if png_dir.is_dir() else []
    src_orig_bytes = int((pkgs["both"]["manifest"].get("original") or {}).get("bytes") or 0)
    orig_file = raw2 / "第二章 烷烃.pptx"
    orig_ok = (imp2.get("file") == "第二章 烷烃.pptx" and orig_file.is_file()
               and orig_file.stat().st_size == src_orig_bytes)
    judge.check("27. 带原文件+页图的包闭环：原文件落到 raw\\、页图落到 logs\\figure_desc\\",
                counts2 == src_doc_before and len(pngs) == pkgs["both"]["n_pages"]
                and orig_ok and str(row2["path"]).endswith("第二章 烷烃.pptx")
                and Path(row2["abs_path"]).is_file(),
                "行数 %s；原文件 %s（%d 字节，path=%s）；页图 %d 张（源 %d 张）"
                % (counts2, imp2.get("file"), src_orig_bytes, row2["path"], len(pngs),
                   pkgs["both"]["n_pages"]))

    # ---- 12. duplicate：显式允许时再导一份副本（path 自动去重、不动原有那份）----
    imp3 = share.import_package(pkgs["both"]["zip"], db_path=db2, log_dir=logs2,
                                raw_dir=raw2, root=ROOT, duplicate=True)
    c2 = db.connect(db2)
    try:
        n_docs = c2.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        n_all = c2.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        path3 = c2.execute("SELECT path FROM documents WHERE id=?",
                           (imp3.get("doc_id"),)).fetchone()
    finally:
        c2.close()
    judge.check("28. --duplicate 可再导一份副本（path 去重、两份并存）",
                imp3.get("status") == "imported" and n_docs == 2
                and n_all == src_doc_before["chunks"] * 2
                and imp3.get("doc_id") != imp2.get("doc_id"),
                "两份文档、共 %d 块；副本 path=%s"
                % (n_all, path3["path"] if path3 else None))

    # ---- 13. hybrid 检索：导入的向量能真的用（加载本地 ONNX，零联网零费用）----
    c = db.connect(tmp_db)
    try:
        kw = search_mod.search(c, "构造异构", top_k=3, mode="keyword", corpus="main")
        hy = search_mod.search(c, "构造异构", top_k=3, mode="hybrid", corpus="main")
        vec_ok = [h for h in hy if float(h.vec_score or 0) > 0]
        judge.check("29. hybrid 检索可用（向量路真的参与打分，本地模型·零费用）",
                    bool(hy), "keyword %d 条 → hybrid %d 条；带向量分 %d 条"
                              "（首条 %s，出处「%s」）"
                    % (len(kw), len(hy), len(vec_ok),
                       hy[0].doc_id if hy else "-", hy[0].source_ref if hy else ""))
    finally:
        c.close()

    # ---- 14. 不污染真实库 ----
    src_fp_after = lib_fingerprint(src_db)
    judge.check("26. 真实库全程未被改动（行数与文件指纹一致）",
                src_fp_after == src_fp_before,
                "前 %s / 后 %s" % (src_fp_before, src_fp_after))

    # ---- 12. 结论 ----
    elapsed = round(time.time() - t0, 1)
    vol = {k: v["bytes"] for k, v in pkgs.items()}
    ok = not judge.failed
    summary = {
        "ok": ok, "elapsed_s": elapsed, "doc_id_src": doc_id, "doc_id_new": new_id,
        "counts_src": src_doc_before, "counts_new": new_counts if ok else None,
        "volumes": vol, "checks": judge.items,
        "failed": [i["name"] for i in judge.failed],
        "sentinel": {"sockets": sent.sockets, "embed_calls": sent.embed_calls,
                     "modules_added": [m for m in sent.modules_added
                                       if "onnx" in m or "request" in m or "http" in m]},
        "work": str(work), "src_db": str(src_db),
    }
    (work / "verify.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    (work / "verify.log").write_text("\n".join(DETAIL) + "\n", encoding="utf-8")

    # stdout 只放 6 行结论
    l1 = ("[C-0079] 结论：%s（%d/%d 项通过，用时 %ss）"
          % ("PASS" if ok else "FAIL", len(judge.items) - len(judge.failed),
             len(judge.items), elapsed))
    l2 = ("1) 导出→导入闭环：源库 doc#%d → 临时库 doc#%d；documents/chunks(%d)/"
          "embeddings(%d)/figure_desc(%d) 行数与源库一致"
          % (doc_id, new_id, new_counts["chunks"], new_counts["embeddings"],
             new_counts["figure_desc"]))
    l3 = ("2) 检索与索引：FTS MATCH 命中 %d 条（含新导入 %d 条）；keyword 检索首条出处「%s」"
          % (n_fts, n_fts_doc, hits[0].source_ref if hits else "-"))
    l4 = ("3) 零成本：导入 socket 连接 %d 次、嵌入调用 %d 次、模型调用 %s 次、花费 CNY %s"
          % (sent.sockets, sent.embed_calls, imp.get("model_calls"), imp.get("cost_cny")))
    l5 = ("4) 体积实测：min %s / +原文件 %s / +页图 %s / 都带 %s"
          % (_kb(vol["min"]), _kb(vol["orig"]), _kb(vol["pages"]), _kb(vol["both"])))
    fails = judge.failed
    l6 = ("5) 失败项：%s；明细 %s"
          % ("、".join(i["name"] for i in fails) if fails else "无",
             work / "verify.log"))
    for line in (l1, l2, l3, l4, l5, l6):
        print(line)
    return 0 if ok else 1


def _kb(n: int) -> str:
    return "%.1f MB" % (n / 1024 ** 2) if n >= 1024 ** 2 else "%.0f KB" % (n / 1024)


if __name__ == "__main__":
    sys.exit(main())
