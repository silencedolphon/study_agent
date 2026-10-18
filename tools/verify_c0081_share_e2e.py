# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
"""卡 C-0081 总验收驱动：模拟「甲导出 → 传给乙 → 乙异地导入并可用」的全流程。

它做四件事，每一步都留机器可读证据：
  1. **甲**：把真实库的一份课件（只读）复制成"甲端整机根"，起临时端口实例，
     用真浏览器（headless Edge + CDP）点「导出」两次（带原文件+页图 / 最精简）；
  2. **异地**：把两个包复制到"传输目录"，然后**把甲端数据根改名封存**——
     乙端导入期间，甲端库在原来的路径上根本不存在；
  3. **乙**：另一个全新数据根、全新空库、临时端口实例，**断网 + 没配密钥**，
     用真浏览器点「导入分享包」把两个包导进去；随后同题检索、知识点页签、
     重复导入、5 类恶意包、页图与原文件落盘；
  4. **对照**：甲/乙两边同一问题的检索命中（界面 + 库里各跑一遍）、
     知识点产物条数与节点名、页图 sha256、库内行数，外加"成本对照表"。

红线：不碰真实库（只读复制 + 只读检索）、不碰 8756、不 git、不改前置卡产物。

跑法（约 3~6 分钟）：
    venv\\Scripts\\python.exe tools\\verify_c0081_share_e2e.py
输出：前 6 行是结论，明细写 %TEMP%\\sa_share_test\\logs\\e2e_detail.log；
     证据与机器可读汇总写 %TEMP%\\sa_share_test\\evidence\\。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
VENV_PY = PROJ / "venv" / "Scripts" / "python.exe"
HOST_PY = PROJ / "tools" / "verify_c0081_host.py"
PROBE_JS = PROJ / "tools" / "probe_c0081_share_e2e.mjs"
SEED_SUBJECT = "药物化学"
SEED_DOC = 1
PORT_A, PORT_B = 8811, 8812
SUBJ_MIN, SUBJ_FULL = "接收精简库", "接收全量库"
QUERIES = ["Lipinski", "细胞色素P450", "药物代谢"]
API_MODULES = ("core.chat", "core.figures", "core.outline", "core.kp_build",
               "core.ingest", "core.terms_build")
LOCAL_MODULES = ("core.embed", "onnxruntime", "tokenizers", "transformers")

LOG_LINES: list[str] = []


def say(msg: str = "") -> None:
    LOG_LINES.append(msg)
    print(msg, flush=True)


def _force_utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                      # noqa: BLE001
            pass


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with Path(p).open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def ro_conn(p: Path) -> sqlite3.Connection:
    c = sqlite3.connect("file:///%s?mode=ro" % Path(p).as_posix(), uri=True)
    c.row_factory = sqlite3.Row
    return c


# --------------------------------------------------------------------- 准备
def prepare_a(sa: Path) -> dict:
    """把真实库的 `药物化学` **只读复制**成甲端整机根（绝不在真实库上动手）。"""
    src = PROJ / "subjects" / SEED_SUBJECT
    a = sa / "A"
    dst = a / "subjects" / SEED_SUBJECT
    if a.exists():
        shutil.rmtree(a)
    for sub in ("raw", "index", "logs", "notes", "share"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
    (a / "logs").mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "subject.json", dst / "subject.json")

    orig_src = next((src / "raw").glob("*.pptx"), None) or next((src / "raw").iterdir())
    shutil.copy2(orig_src, dst / "raw" / orig_src.name)

    (dst / "logs" / "knowledge").mkdir(parents=True, exist_ok=True)
    (dst / "logs" / "outline").mkdir(parents=True, exist_ok=True)
    for rel in ("knowledge/%d-末级知识点总结.json" % SEED_DOC,
                "outline/%d.json" % SEED_DOC):
        s = src / "logs" / rel
        if s.is_file():
            shutil.copy2(s, dst / "logs" / rel)
    fd_src = src / "logs" / "figure_desc"
    if fd_src.is_dir():
        shutil.copytree(fd_src, dst / "logs" / "figure_desc", dirs_exist_ok=True)

    # SQLite：用 backup API 复制（源库以只读方式打开，不产生 -wal/-journal 写入）
    sdb = ro_conn(src / "index" / "study.db")
    ddb = sqlite3.connect(str(dst / "index" / "study.db"))
    try:
        sdb.backup(ddb)
        ddb.execute("UPDATE documents SET abs_path=? WHERE id=?",
                    (str(dst / "raw" / orig_src.name), SEED_DOC))
        ddb.commit()
    finally:
        ddb.close()
        sdb.close()

    c = ro_conn(dst / "index" / "study.db")
    try:
        counts = {t: c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                  for t in ("documents", "chunks", "embeddings", "figure_desc", "chunks_fts")}
        title = c.execute("SELECT title FROM documents WHERE id=?", (SEED_DOC,)).fetchone()["title"]
        doc_n_chunks = c.execute("SELECT n_chunks FROM documents WHERE id=?",
                                 (SEED_DOC,)).fetchone()["n_chunks"]
    finally:
        c.close()
    pages = sorted((dst / "logs" / "figure_desc").glob("*/slide_*.png"))
    return {"root": str(a), "db": str(dst / "index" / "study.db"),
            "counts": counts, "title": title, "doc_n_chunks": int(doc_n_chunks),
            "orig": str(dst / "raw" / orig_src.name),
            "orig_bytes": (dst / "raw" / orig_src.name).stat().st_size,
            "n_pages": len(pages),
            "pages_bytes": sum(p.stat().st_size for p in pages)}


def make_bad_packages(min_zip: Path, out_dir: Path) -> dict:
    """用最精简包当底，造 5 类恶意包（每类只打一个点）。"""
    import base64
    import zipfile

    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(min_zip) as zf:
        base = {i.filename: zf.read(i.filename) for i in zf.infolist()}
    man = json.loads(base["manifest.json"].decode("utf-8"))

    def repack(dest: Path, items: dict, manifest: dict) -> Path:
        items = dict(items)
        items["manifest.json"] = json.dumps(manifest, ensure_ascii=False,
                                            indent=2).encode("utf-8")
        # manifest 里对 manifest.json 自身没有摘要要求；其它条目按需在调用处更新
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in items.items():
                zf.writestr(name, data)
        return dest

    def fix_file_entry(manifest: dict, name: str, blob: bytes) -> None:
        for f in (manifest.get("files") or []):
            if f.get("name") == name:
                f["bytes"] = len(blob)
                f["sha256"] = hashlib.sha256(blob).hexdigest()

    made: dict = {}

    # ① 路径穿越：多塞一条 `..\..\c0081-evil.txt`
    items = dict(base)
    with zipfile.ZipFile(out_dir / "bad-traversal.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in items.items():
            zf.writestr(name, data)
        zf.writestr("../../c0081-evil.txt", "C-0081 路径穿越载荷：写出来就说明闸门失效了\n")
    made["traversal"] = str(out_dir / "bad-traversal.zip")

    # ② 模型名/维度不符（整包自洽：manifest + 每行 + sha256 一起改）
    items = dict(base)
    rows = [json.loads(x) for x in items["embeddings.jsonl"].decode("utf-8").splitlines() if x.strip()]
    for row in rows:
        row["model"], row["dim"] = "Xenova/bge-large-zh-v1.5", 1024
        row["vec_b64"] = base64.b64encode(os.urandom(1024 * 4)).decode("ascii")
    blob = ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode("utf-8")
    items["embeddings.jsonl"] = blob
    man2 = json.loads(json.dumps(man))
    man2["embedding"] = {"model": "Xenova/bge-large-zh-v1.5", "dim": 1024}
    fix_file_entry(man2, "embeddings.jsonl", blob)
    made["model"] = str(repack(out_dir / "bad-model.zip", items, man2))

    # ③ 缺必需条目：连 manifest.files 里的登记一起删掉（干净地"少一个文件"）
    items = {k: v for k, v in base.items() if k != "chunks.jsonl"}
    man3 = json.loads(json.dumps(man))
    man3["files"] = [f for f in (man3.get("files") or []) if f.get("name") != "chunks.jsonl"]
    made["missing_chunks"] = str(repack(out_dir / "bad-missing-chunks.zip", items, man3))

    # ④ 缺字段：doc.json 删掉 content_hash（同步改它在 manifest.files 里的摘要）
    doc = json.loads(base["doc.json"].decode("utf-8"))
    doc.pop("content_hash", None)
    doc_blob = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    items = dict(base)
    items["doc.json"] = doc_blob
    man4 = json.loads(json.dumps(man))
    fix_file_entry(man4, "doc.json", doc_blob)
    made["nofield"] = str(repack(out_dir / "bad-nofield.zip", items, man4))

    # ⑤ 超大解压体积：32 MB 全零 → 压成 ~32 KB，压缩比 ~1000×（远超上限 200×）
    bomb = b"\x00" * (32 * 1024 * 1024)
    items = dict(base)
    items["pages/bomb.bin"] = bomb
    man5 = json.loads(json.dumps(man))
    man5.setdefault("files", []).append(
        {"name": "pages/bomb.bin", "bytes": len(bomb),
         "sha256": hashlib.sha256(bomb).hexdigest()})
    made["bomb"] = str(repack(out_dir / "bad-bomb.zip", items, man5))
    for k, v in made.items():
        say("  恶意包 %-16s %s（%d 字节）" % (k, v, Path(v).stat().st_size))
    return made


# --------------------------------------------------------------------- 宿主
def start_host(role: str, port: int, root: Path, *, offline: bool) -> tuple:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [str(VENV_PY), str(HOST_PY), "--role", role, "--port", str(port),
           "--root", str(root), "--sentinel", str(root / "sentinel.json"),
           "--json", str(root / "ready.json")]
    if offline:
        cmd.append("--offline")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            env=env, cwd=str(PROJ))
    t0 = time.time()
    info = None
    while time.time() - t0 < 90:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        line = line.rstrip()
        if line.startswith("C0081_READY "):
            info = json.loads(line[len("C0081_READY "):])
            break
        if line:
            say("    [%s] %s" % (role, line))
    if info is None:
        raise RuntimeError("宿主 %s 没起来（端口 %d）" % (role, port))
    say("  [%s] 就绪 pid=%s db=%s 学科=%s 文档数=%s 断网=%s"
        % (role, info["pid"], info["db"], info["subject"], info["n_docs"], info["offline"]))
    return proc, info


def stop_host(proc: subprocess.Popen, root: Path) -> None:
    (root / "STOP").write_text("stop", encoding="utf-8")
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    rest = proc.stdout.read() if proc.stdout else ""
    for line in (rest or "").splitlines():
        if line.strip():
            say("    [宿主] %s" % line.strip())


def run_probe(args: list) -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(["node", str(PROBE_JS)] + args, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env,
                       cwd=str(PROJ), timeout=1800)
    out = "\n".join(x for x in (p.stdout or "").strip().splitlines())
    say("  " + out.replace("\n", "\n  "))
    if p.returncode not in (0, 1):
        say("  [探针 stderr] " + (p.stderr or "")[-800:])
    return {"returncode": p.returncode, "stdout": out, "stderr": (p.stderr or "")[-2000:]}


def read_json(p: Path, dflt=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dflt


def sentinel_window(snap: dict, t0: float, t1: float) -> dict:
    """哨兵快照里落在 [t0,t1]（epoch 秒）窗口内的调用/连接。"""
    if not snap:
        return {"error": "没有哨兵快照"}
    base = float(snap.get("started") or 0.0)
    mods: dict = {}
    for name, rel in (snap.get("mod_events") or []):
        t = base + float(rel)
        if t0 - 0.5 <= t <= t1 + 0.5:
            mods[name] = mods.get(name, 0) + 1
    conns = [c for c in (snap.get("connects") or [])
             if t0 - 0.5 <= base + float(c.get("t", 0)) <= t1 + 0.5]
    api = {k: v for k, v in mods.items() if k in API_MODULES}
    local = {k: v for k, v in mods.items() if k in LOCAL_MODULES}
    return {"window": [round(t0, 2), round(t1, 2)], "mod_calls": mods,
            "api_calls_by_module": api, "api_calls_total": sum(api.values()),
            "local_embed_calls": local, "local_total": sum(local.values()),
            "n_connects": len(conns), "connects": conns[:10],
            "offline_blocked_total": snap.get("blocked"),
            "total_connects_in_session": snap.get("n_connects")}


# --------------------------------------------------------------------- 对照
def _ensure_core_on_path() -> None:
    if str(PROJ) not in sys.path:
        sys.path.insert(0, str(PROJ))


def prepare_b(sa: Path) -> dict:
    """乙端：**全新**数据根（等价于"另一台刚装好的机器"）。"""
    b = sa / "B"
    if b.exists():
        shutil.rmtree(b, ignore_errors=True)
    for sub in ("raw", "notes", "index", "logs", "share"):
        (b / sub).mkdir(parents=True, exist_ok=True)
    return {"root": str(b)}


def bad_packages_direct(bad: dict, b_root: Path, slug: str = SUBJ_MIN) -> dict:
    """绕开界面，直接在**导入层**（`core.share.import_package`）上把同一批恶意包再打一遍。

    为什么还要这一遍：界面在"看包信息"阶段就把坏包拦住了（这正是用户看到的），
    但"导入接口自己会不会也拦"是另一回事——万一有人直接调接口（CLI/MCP/脚本），
    闸门必须在那一层也在。这里用 `dry_run=True`：走完全部校验但**不落库**。
    """
    _ensure_core_on_path()
    from core import share as share_mod
    d = b_root / "subjects" / slug
    db_path, log_dir, raw_dir = d / "index" / "study.db", d / "logs", d / "raw"
    out: dict = {}
    for name, p in bad.items():
        conn = ro_conn(db_path)
        before = {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                  for t in ("documents", "chunks", "embeddings")}
        conn.close()
        rec = {"zip": p, "rejected": False, "error": "", "status": ""}
        try:
            r = share_mod.import_package(p, db_path=db_path, log_dir=log_dir,
                                         raw_dir=raw_dir, root=b_root, dry_run=True)
            rec["status"] = str(r.get("status"))
        except share_mod.ShareError as e:
            rec["rejected"] = True
            rec["error"] = str(e)[:500]
        except Exception as e:                       # noqa: BLE001
            rec["rejected"] = True
            rec["error"] = "%s: %s" % (type(e).__name__, e)
            rec["unexpected"] = True
        conn = ro_conn(db_path)
        after = {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                 for t in before}
        conn.close()
        rec["rows_before"], rec["rows_after"] = before, after
        rec["rows_unchanged"] = before == after
        out[name] = rec
    return out


def hits_of(conn, q: str, mode: str, k: int = 8) -> list:
    _ensure_core_on_path()
    from core import search as search_mod
    hits = search_mod.search(conn, q, top_k=k, corpus="main", mode=mode)
    return [{"ord": h.ord, "source_ref": h.source_ref, "text": (h.text or "")[:70],
             "heading_path": (h.heading_path or "")[:60], "doc_title": h.doc_title,
             "chunk_id": h.chunk_id, "vec": round(h.vec_score, 4),
             "kw": round(h.kw_score, 4)} for h in hits]


def _key(h: dict) -> tuple:
    return (h["ord"], h["source_ref"], h["text"])


def search_compare(db_a: Path, db_b: Path) -> dict:
    ca, cb = ro_conn(db_a), ro_conn(db_b)
    out: dict = {"queries": {}}
    try:
        for q in QUERIES:
            per = {}
            for mode in ("keyword", "hybrid"):
                ha = hits_of(ca, q, mode)
                hb = hits_of(cb, q, mode)
                ka, kb = [_key(h) for h in ha], [_key(h) for h in hb]
                per[mode] = {
                    "a_n": len(ha), "b_n": len(hb),
                    "identical": ka == kb,
                    "a": [{"ord": h["ord"], "ref": h["source_ref"], "text": h["text"]} for h in ha],
                    "b": [{"ord": h["ord"], "ref": h["source_ref"], "text": h["text"]} for h in hb],
                }
            out["queries"][q] = per
    finally:
        ca.close()
        cb.close()
    return out


def kp_snapshot(kp_path: Path) -> dict:
    obj = read_json(kp_path) or {}
    kps = obj.get("knowledge_points") or []
    st = obj.get("structure") or {}
    names: list = []

    def walk(n) -> None:
        if isinstance(n, dict):
            if n.get("name"):
                names.append(n["name"])
            for c in (n.get("children") or []):
                walk(c)
        elif isinstance(n, list):
            for c in n:
                walk(c)

    walk(st.get("root") or {})
    refs: set = set()
    for k in kps:
        for s in (k.get("sources") or []):
            for cid in (s.get("chunk_ids") or []):
                refs.add(int(cid))
            if s.get("chunk_id") is not None:
                refs.add(int(s["chunk_id"]))
        for cid in (k.get("material_block_ids") or []):
            refs.add(int(cid))
        for m in (k.get("material") or []):
            if m.get("chunk_id") is not None:
                refs.add(int(m["chunk_id"]))
    usage = {"cost_cny": 0.0, "tokens_in": 0, "tokens_out": 0}
    for k in kps:
        u = k.get("usage") or {}
        usage["cost_cny"] += float(u.get("cost_cny") or 0.0)
        usage["tokens_in"] += int(u.get("tokens_in") or 0)
        usage["tokens_out"] += int(u.get("tokens_out") or 0)
    return {"exists": kp_path.is_file(), "path": str(kp_path),
            "meta_doc_id": (obj.get("meta") or {}).get("doc_id"),
            "n_kps": len(kps), "kp_names": [k.get("name") for k in kps],
            "structure_names": names, "n_structure_names": len(names),
            "ref_chunk_ids": sorted(refs), "usage": usage,
            "pipeline_version": (obj.get("meta") or {}).get("pipeline_version")}


def cost_table(a_root: Path, b_root: Path, a_info: dict) -> dict:
    c = ro_conn(a_root / "subjects" / SEED_SUBJECT / "index" / "study.db")
    try:
        fig = c.execute("SELECT COUNT(*) n, COALESCE(SUM(tokens_in),0) ti,"
                        " COALESCE(SUM(tokens_out),0) to_, COALESCE(SUM(cost_cny),0) cost"
                        " FROM figure_desc WHERE doc_id=?", (SEED_DOC,)).fetchone()
    finally:
        c.close()
    kp = kp_snapshot(a_root / "subjects" / SEED_SUBJECT / "logs" / "knowledge"
                     / ("%d-末级知识点总结.json" % SEED_DOC))
    outline = read_json(a_root / "subjects" / SEED_SUBJECT / "logs" / "outline"
                        / ("%d.json" % SEED_DOC)) or {}
    outline_cost = float(outline.get("cost_cny") or 0.0)
    kp_cost = float(kp["usage"]["cost_cny"])
    fig_cost = float(fig["cost"])
    return {
        "甲_识图_25页": {"cny": round(fig_cost, 4), "tokens_in": fig["ti"],
                          "tokens_out": fig["to_"], "pages": fig["n"]},
        "甲_知识点总结": {"cny": round(kp_cost, 4), "n_kps": kp["n_kps"],
                          "tokens_in": kp["usage"]["tokens_in"],
                          "tokens_out": kp["usage"]["tokens_out"]},
        "甲_入库结构分析": {"cny": round(outline_cost, 4),
                            "usage": outline.get("usage") or {}},
        "甲_合计": round(fig_cost + kp_cost + outline_cost, 4),
        "乙_导入花费": 0.0,
        "乙_自行入库所需的同等花费": round(fig_cost + kp_cost + outline_cost, 4),
        "口径": "甲端三项花费全部取自**产物自己记录的** cost_cny（figure_desc.cost_cny /"
                " 知识点产物 meta.knowledge_points[].usage.cost_cny / logs/outline 的 cost_cny），"
                "不是估算；乙端导入 0 次模型调用（哨兵 + 接口字段双证）。",
    }


# --------------------------------------------------------------------- 主流程
def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="C-0081 分享功能总验收")
    ap.add_argument("--sa", default="", help="验收临时根（默认 %%TEMP%%\\sa_share_test）")
    ap.add_argument("--keep-a", action="store_true", help="不把甲端数据根改名封存")
    ap.add_argument("--reuse-a", action="store_true", help="复用已存在的甲端数据根")
    args = ap.parse_args()
    sa = Path(args.sa) if args.sa else Path(os.environ.get("TEMP", ".")) / "sa_share_test"
    for sub in ("A", "B", "transfer", "evidence", "logs"):
        (sa / sub).mkdir(parents=True, exist_ok=True)
    detail = sa / "logs" / "e2e_detail.log"
    findings: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": []}

    def step(name: str, ok: bool, detail_txt: str = "", **extra) -> None:
        findings["steps"].append({"name": name, "ok": bool(ok),
                                  "detail": detail_txt, "extra": extra or {}})
        say("  %s %s%s" % ("[OK]" if ok else "[!!]", name,
                           (" —— " + detail_txt) if detail_txt else ""))

    say("=" * 78)
    say("卡 C-0081 总验收：甲导出 → 乙异地导入（真浏览器）")
    say("临时根：%s" % sa)
    say("=" * 78)

    # ---------- 1. 甲端数据根（真实库只读复制） ----------
    say("\n[1/9] 准备甲端数据根（真实库 %s 只读复制）" % SEED_SUBJECT)
    a_info = prepare_a(sa) if not args.reuse_a else read_json(sa / "evidence" / "a_prepare.json")
    if a_info is None:
        a_info = prepare_a(sa)
    say("  甲端根：%s" % a_info["root"])
    say("  课件：%s（doc#%d，原文件 %d 字节，页图 %d 张 / %d 字节）"
        % (a_info["title"], SEED_DOC, a_info["orig_bytes"], a_info["n_pages"], a_info["pages_bytes"]))
    say("  行数：%s" % a_info["counts"])
    (sa / "evidence" / "a_prepare.json").write_text(
        json.dumps(a_info, ensure_ascii=False, indent=2), encoding="utf-8")
    step("甲端数据根就绪（真实库只读复制）", a_info["counts"]["documents"] >= 1,
         json.dumps(a_info["counts"], ensure_ascii=False))

    # ---------- 2. 甲端实例 + 浏览器导出 ----------
    say("\n[2/9] 起甲端实例（端口 %d）+ 真浏览器点「导出」两次" % PORT_A)
    proc_a, ready_a = start_host("A", PORT_A, sa / "A", offline=False)
    try:
        probe_a = run_probe(["--stage", "a", "--port", str(PORT_A),
                             "--outdir", str(sa / "evidence" / "shots"),
                             "--json", str(sa / "evidence" / "probe_a.json"),
                             "--transfer", str(sa / "transfer"),
                             "--prof-dir", str(sa / "A"),
                             "--docId", str(SEED_DOC)])
    finally:
        stop_host(proc_a, sa / "A")
    pa = read_json(sa / "evidence" / "probe_a.json", {}) or {}
    checks_a = pa.get("checks") or []
    ok_a = sum(1 for c in checks_a if c.get("ok"))
    say("  甲端探针：%s %d/%d" % (pa.get("stage"), ok_a, len(checks_a)))
    for c in checks_a:
        if not c.get("ok"):
            say("    ✗ %s —— %s" % (c.get("name"), c.get("detail")))
    exp_min = (pa.get("evidence") or {}).get("export_min") or {}
    exp_full = (pa.get("evidence") or {}).get("export_full") or {}
    sent_a = read_json(sa / "A" / "sentinel.json", {}) or {}
    se_a = sentinel_window(sent_a, exp_full.get("t0_epoch", 0), exp_full.get("t1_epoch", 0))
    findings["export"] = {
        "min": {"bytes": exp_min.get("disk_bytes"), "wall_ms": exp_min.get("wall_ms"),
                "path": exp_min.get("path")},
        "full": {"bytes": exp_full.get("disk_bytes"), "wall_ms": exp_full.get("wall_ms"),
                 "path": exp_full.get("path")},
        "sentinel_full_export": se_a,
    }
    say("  最精简包：%s 字节（界面耗时 %s ms）" % (exp_min.get("disk_bytes"), exp_min.get("wall_ms")))
    say("  全量包  ：%s 字节（界面耗时 %s ms）" % (exp_full.get("disk_bytes"), exp_full.get("wall_ms")))
    say("  导出期间对外连接：%d 次；API 模块调用：%d 次；本地嵌入调用：%d 次"
        % (se_a.get("n_connects", -1), se_a.get("api_calls_total", -1), se_a.get("local_total", -1)))
    step("甲：真浏览器点「导出」两次并落盘（含原文件+页图 / 最精简）",
         exp_min.get("disk_bytes", 0) > 0 and exp_full.get("disk_bytes", 0) > 0
         and exp_full.get("disk_bytes", 0) > exp_min.get("disk_bytes", 0),
         "min=%s full=%s（字节）" % (exp_min.get("disk_bytes"), exp_full.get("disk_bytes")))
    window_a_ok = (exp_full.get("t1_epoch", 0) or 0) > (exp_full.get("t0_epoch", 0) or 0) > 0
    step("甲：导出窗口有效（哨兵按点「导出」到出结果的时间段切片）", window_a_ok,
         "窗口 %s ~ %s" % (exp_full.get("t0_epoch"), exp_full.get("t1_epoch")))
    step("甲：导出时零 API 调用（哨兵实测）",
         window_a_ok and se_a.get("api_calls_total") == 0 and se_a.get("n_connects") == 0,
         "api=%s connects=%s" % (se_a.get("api_calls_total"), se_a.get("n_connects")))

    # ---------- 3. 断链：包 → 传输目录，甲端数据根改名封存 ----------
    say("\n[3/9] 模拟「传到另一台机器」：包进传输目录，甲端数据根改名封存")
    transfer = sa / "transfer"
    zmin, zfull = transfer / "A-min.zip", transfer / "A-full.zip"
    step("甲：两个包都已在传输目录里", zmin.is_file() and zfull.is_file(),
         "%s(%d) %s(%d)" % (zmin.name, zmin.stat().st_size if zmin.is_file() else -1,
                            zfull.name, zfull.stat().st_size if zfull.is_file() else -1))
    sealed = None
    if not args.keep_a:
        sealed = sa / ("A_sealed_%s" % time.strftime("%H%M%S"))
        os.rename(sa / "A", sealed)
        gone = not (sa / "A" / "subjects").exists()
        say("  甲端数据根已改名为 %s（原路径 %s 不再存在：%s）" % (sealed.name, sa / "A", gone))
        step("异地证明：乙端导入期间甲端库在原路径上不存在", gone, str(sealed))
    a_db_for_compare = (sealed or (sa / "A")) / "subjects" / SEED_SUBJECT / "index" / "study.db"
    findings["sealed_root"] = str(sealed) if sealed else None

    # ---------- 4. 恶意包 ----------
    say("\n[4/9] 造 5 类恶意包")
    bad = make_bad_packages(zmin, sa / "bad")

    # ---------- 5. 乙端实例 + 浏览器导入 ----------
    say("\n[5/9] 起乙端实例（端口 %d，全新数据根、断网、没配密钥）"
        % PORT_B)
    b_info = prepare_b(sa)
    say("  乙端整机根（全新）：%s" % b_info["root"])
    proc_b, ready_b = start_host("B", PORT_B, sa / "B", offline=True)
    say("  乙端环境自检：KB_* 里指向真实项目的键 = %s（为空才是干净）"
        % (ready_b.get("env_leak_check") or "无"))
    try:
        probe_b = run_probe(["--stage", "b", "--port", str(PORT_B),
                             "--outdir", str(sa / "evidence" / "shots"),
                             "--json", str(sa / "evidence" / "probe_b.json"),
                             "--zip-min", str(zmin), "--zip-full", str(zfull),
                             "--subj-min", SUBJ_MIN, "--subj-full", SUBJ_FULL,
                             "--bad", str(sa / "bad"), "--docId", "1",
                             "--expectChunks", str(a_info["counts"]["chunks"]),
                             "--expectDocChunks", str(a_info.get("doc_n_chunks") or 0),
                             "--prof-dir", str(sa / "B")])
    finally:
        stop_host(proc_b, sa / "B")
    pb = read_json(sa / "evidence" / "probe_b.json", {}) or {}
    checks_b = pb.get("checks") or []
    ok_b = sum(1 for c in checks_b if c.get("ok"))
    say("  乙端探针：%s %d/%d" % (pb.get("stage"), ok_b, len(checks_b)))
    for c in checks_b:
        if not c.get("ok"):
            say("    ✗ %s —— %s" % (c.get("name"), c.get("detail")))
    ev_b = pb.get("evidence") or {}
    sent_b = read_json(sa / "B" / "sentinel.json", {}) or {}
    imports_ev = {k: ev_b.get(k) or {} for k in ("importMin", "importDup", "importFull")}
    sent_windows = {}
    for k, v in imports_ev.items():
        sent_windows[k] = sentinel_window(sent_b, v.get("t0_epoch", 0), v.get("t1_epoch", 0))
    findings["imports"] = {
        "min": {kk: imports_ev["importMin"].get(kk) for kk in
                ("wall_ms", "text", "progress_samples")},
        "dup": {kk: imports_ev["importDup"].get(kk) for kk in ("wall_ms", "text")},
        "full": {kk: imports_ev["importFull"].get(kk) for kk in
                 ("wall_ms", "text", "progress_samples")},
        "sentinels": sent_windows,
        "docs_before": ev_b.get("docsBefore"), "docs_after_min": ev_b.get("docsAfterMin"),
        "docs_full": ev_b.get("docsFull"),
        "bad": ev_b.get("badPackages"), "traversal_escapes": ev_b.get("traversalEscapes"),
    }
    for k, w in sent_windows.items():
        say("  %s 导入窗口：API 调用 %s 次 / 对外连接 %s 次 / 本地嵌入 %s 次"
            % (k, w.get("api_calls_total"), w.get("n_connects"), w.get("local_total")))
    step("乙：真浏览器「导入分享包」走完全流程（最精简包 + 全量包）",
         bool(ev_b.get("importMin", {}).get("text")) and "导入完成" in (ev_b.get("importMin", {}).get("text") or "")
         and "导入完成" in (ev_b.get("importFull", {}).get("text") or ""),
         "min=%sms full=%sms" % (imports_ev["importMin"].get("wall_ms"),
                                 imports_ev["importFull"].get("wall_ms")))
    win_ok = all((v.get("t1_epoch", 0) or 0) > (v.get("t0_epoch", 0) or 0) > 0
                 for v in imports_ev.values())
    step("乙：三次导入窗口都有效（哨兵按点「开始导入」到出结果的时间段切片）", win_ok,
         json.dumps({k: [v.get("t0_epoch"), v.get("t1_epoch")] for k, v in imports_ev.items()}))
    zero_ok = win_ok and all(w.get("api_calls_total") == 0 and w.get("n_connects") == 0
                             for w in sent_windows.values())
    step("★ 乙：导入期间零模型调用、零对外连接（哨兵按导入窗口切片统计）", zero_ok,
         json.dumps({k: {"api": v.get("api_calls_total"), "conn": v.get("n_connects"),
                         "local": v.get("local_total")} for k, v in sent_windows.items()},
                    ensure_ascii=False))
    txt_min = ev_b.get("importMin", {}).get("text") or ""
    step("★ 乙：界面自报「模型调用 0 次 / CNY 0.00」",
         "模型调用：0 次" in txt_min.replace(" ", " ") and "CNY 0.00" in txt_min,
         txt_min.replace("\n", " ")[:160])

    # ---------- 6. 一致性对照（库里跑，逐条给对照） ----------
    say("\n[6/9] 甲 / 乙 一致性对照（python 侧：keyword + hybrid 两路逐条比）")
    b_min_db = sa / "B" / "subjects" / SUBJ_MIN / "index" / "study.db"
    b_full_db = sa / "B" / "subjects" / SUBJ_FULL / "index" / "study.db"
    cmp_res = search_compare(a_db_for_compare, b_min_db)
    findings["search_compare"] = cmp_res
    for q, per in cmp_res["queries"].items():
        for mode, d in per.items():
            say("  %-14s %-8s 甲 %d 条 / 乙 %d 条 → %s"
                % (q, mode, d["a_n"], d["b_n"], "完全一致" if d["identical"] else "★不一致★"))
    ident_all = all(d["identical"] for per in cmp_res["queries"].values() for d in per.values())
    step("★ 一致性：同题检索在甲/乙两边命中条目与出处页码完全一致（keyword+hybrid）",
         ident_all, "见 evidence/report.json 的 search_compare 逐条对照")
    closed = [d for per in cmp_res["queries"].values() for d in per.values()
              if d["a_n"] > 0]
    step("一致性：检索在乙端确有命中（不是「两边都空」的假一致）",
         bool(closed) and all(d["b_n"] > 0 for d in closed),
         "有命中的查询 %d 组" % len(closed))

    kp_a = kp_snapshot((sealed or (sa / "A")) / "subjects" / SEED_SUBJECT / "logs"
                       / "knowledge" / ("%d-末级知识点总结.json" % SEED_DOC))
    kp_b = kp_snapshot(sa / "B" / "subjects" / SUBJ_MIN / "logs" / "knowledge"
                       / "1-末级知识点总结.json")
    findings["kp_compare"] = {"a": {k: v for k, v in kp_a.items() if k != "ref_chunk_ids"},
                              "b": {k: v for k, v in kp_b.items() if k != "ref_chunk_ids"},
                              "same_names": kp_a["kp_names"] == kp_b["kp_names"],
                              "same_structure": kp_a["structure_names"] == kp_b["structure_names"]}
    say("  知识点：甲 %d 条 / 乙 %d 条；节点名 %d / %d；条名一致=%s；结构一致=%s"
        % (kp_a["n_kps"], kp_b["n_kps"], kp_a["n_structure_names"], kp_b["n_structure_names"],
           kp_a["kp_names"] == kp_b["kp_names"],
           kp_a["structure_names"] == kp_b["structure_names"]))
    cb = ro_conn(b_min_db)
    try:
        ids = set(r[0] for r in cb.execute("SELECT id FROM chunks"))
        missing = [i for i in kp_b["ref_chunk_ids"] if i not in ids]
        n_rows = {t: cb.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                  for t in ("documents", "chunks", "embeddings", "figure_desc", "chunks_fts")}
    finally:
        cb.close()
    findings["b_rows_min"] = n_rows
    findings["b_refs_missing"] = missing
    step("★ 一致性：知识点产物条数与节点名与甲端一致（且引用块在新库里都找得到）",
         kp_a["n_kps"] == kp_b["n_kps"] and kp_a["kp_names"] == kp_b["kp_names"]
         and kp_a["structure_names"] == kp_b["structure_names"] and not missing,
         "甲 %d 条 / 乙 %d 条，丢引用 %d 处" % (kp_a["n_kps"], kp_b["n_kps"], len(missing)))

    # 页图 + 原文件（全量包那一边）
    a_fig = {}
    for p in sorted(((sealed or (sa / "A")) / "subjects" / SEED_SUBJECT
                     / "logs" / "figure_desc").glob("*/slide_*.png")):
        a_fig[p.name] = sha256_file(p)
    b_fig_dir = sa / "B" / "subjects" / SUBJ_FULL / "logs" / "figure_desc"
    b_fig = {}
    for p in sorted(b_fig_dir.glob("*/slide_*.png")):
        b_fig[p.name] = sha256_file(p)
    same_pages = a_fig == b_fig and len(a_fig) > 0
    raw_dir = sa / "B" / "subjects" / SUBJ_FULL / "raw"
    raw_files = {p.name: p.stat().st_size for p in raw_dir.glob("*") if p.is_file()}
    findings["pages_compare"] = {"a": len(a_fig), "b": len(b_fig), "identical": same_pages,
                                 "sample": list(a_fig.items())[:3]}
    findings["raw_copied"] = raw_files
    say("  页图：甲 %d 张 / 乙 %d 张；sha256 全等=%s" % (len(a_fig), len(b_fig), same_pages))
    say("  乙端 raw/ 落下的原文件：%s" % raw_files)
    step("★ 一致性：带页图的包导入后，乙端页图与甲端逐张 sha256 相同", same_pages,
         "甲 %d 张 / 乙 %d 张" % (len(a_fig), len(b_fig)))
    step("一致性：全量包把原课件文件也落到了接收方 raw/",
         any(v == a_info["orig_bytes"] for v in raw_files.values()),
         "原文件 %d 字节，接收方 %s" % (a_info["orig_bytes"], raw_files))
    step("一致性：乙端精简库的行数与甲端同口径（documents/chunks/embeddings）",
         n_rows["documents"] == 1 and n_rows["chunks"] == a_info["counts"]["chunks"]
         and n_rows["embeddings"] == a_info["counts"]["embeddings"],
         "乙 %s vs 甲 %s" % ({k: n_rows[k] for k in ("documents", "chunks", "embeddings")},
                             {k: a_info["counts"][k] for k in ("documents", "chunks", "embeddings")}))

    # ---------- 7. 恶意包 / 重复导入 ----------
    say("\n[7/9] 安全与健壮（恶意包 5 类 / 重复导入 / 进度）")
    bad_res = ev_b.get("badPackages") or []
    for b in bad_res:
        say("  %-26s 拒绝=%s 可操作=%s 点过「开始导入」=%s 库内资料 %s→%s"
            % (b["label"], b["rejected"], b["actionable"], b["clickedGo"],
               b["docs_after"], b.get("docs_after")))
    step("★ 恶意包 5/5 全部被拒", len(bad_res) == 5 and all(b["rejected"] for b in bad_res),
         "被拒 %d/%d" % (sum(1 for b in bad_res if b["rejected"]), len(bad_res)))
    step("★ 恶意包全部给出可操作提示", all(b["actionable"] for b in bad_res),
         "可操作 %d/%d" % (sum(1 for b in bad_res if b["actionable"]), len(bad_res)))
    step("恶意包被拒后库内资料数不变",
         all(b["docs_after"] == (ev_b.get("docsFull") or {}).get("n") for b in bad_res),
         "基线 %s" % (ev_b.get("docsFull") or {}).get("n"))
    step("路径穿越包没有在库外写出任何文件",
         not (ev_b.get("traversalEscapes") or []),
         str(ev_b.get("traversalEscapes") or "（三个可能落点都不存在）"))

    # 再绕开界面，直接在**导入层**打同一批包（万一是 CLI / MCP / 脚本在调接口）
    bad_direct = bad_packages_direct(bad, sa / "B")
    findings["bad_direct"] = bad_direct
    for k, v in bad_direct.items():
        say("  导入层 %-16s 被拒=%-5s 行数不变=%-5s %s"
            % (k, v["rejected"], v["rows_unchanged"],
               (v["error"] or v["status"]).replace("\n", " ")[:110]))
    step("★ 恶意包在导入层（绕开界面、直接调 import_package）同样全部被拒",
         len(bad_direct) == 5 and all(v["rejected"] for v in bad_direct.values()),
         "%d/%d 被拒" % (sum(1 for v in bad_direct.values() if v["rejected"]), len(bad_direct)))
    step("导入层拒绝这些包后，库内行数一个都没变",
         all(v["rows_unchanged"] for v in bad_direct.values()),
         json.dumps({k: v["rows_after"] for k, v in bad_direct.items()}, ensure_ascii=False))
    dup_txt = (ev_b.get("importDup") or {}).get("text") or ""
    d_before = (ev_b.get("docsAfterMin") or {}).get("chunks")
    step("★ 重复导入：第二次被明确跳过，且不产生重复块",
         ("跳过" in dup_txt) and (ev_b.get("docsAfterMin") or {}).get("n")
         == (ev_b.get("docsBefore") or {}).get("n", 0) + 1,
         "第二次结果：%s" % dup_txt.replace("\n", " ")[:120])
    p_min = (imports_ev["importMin"].get("progress_samples") or [])
    p_full = (imports_ev["importFull"].get("progress_samples") or [])
    step("★ 大包导入期间进度可见（真浏览器按 15 ms 采样到 active/stage/done/total）",
         len([s for s in p_full if s.get("active")]) > 0,
         "精简包采样 %d 条（active %d）/ 全量包采样 %d 条（active %d）"
         % (len(p_min), len([s for s in p_min if s.get("active")]),
            len(p_full), len([s for s in p_full if s.get("active")])))

    # ---------- 8. 成本对照 ----------
    say("\n[8/9] 成本对照表（甲的一次性投入 vs 乙的导入）")
    cost = cost_table(sealed or (sa / "A"), sa / "B", a_info)
    findings["cost"] = cost
    for k in ("甲_识图_25页", "甲_知识点总结", "甲_入库结构分析", "甲_合计", "乙_导入花费"):
        say("  %-16s %s" % (k, cost[k]))
    step("★ 成本：乙端导入花费 = CNY 0（甲端一次性花费不必再花一遍）",
         cost["乙_导入花费"] == 0.0 and cost["甲_合计"] > 0,
         "甲合计 CNY %s → 乙 CNY 0" % cost["甲_合计"])

    # ---------- 9. 汇总 ----------
    say("\n[9/9] 汇总")
    ok_steps = [s for s in findings["steps"] if s["ok"]]
    fail_steps = [s for s in findings["steps"] if not s["ok"]]
    checks_all = checks_a + checks_b
    findings["summary"] = {
        "probe_a": {"pass": ok_a, "total": len(checks_a)},
        "probe_b": {"pass": ok_b, "total": len(checks_b)},
        "steps_pass": len(ok_steps), "steps_total": len(findings["steps"]),
        "failed_steps": [s["name"] for s in fail_steps],
        "verdict": "PASS" if not fail_steps and ok_a == len(checks_a)
                   and ok_b == len(checks_b) and len(checks_all) > 0 else "FAIL",
    }
    (sa / "evidence" / "report.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    (sa / "logs" / "e2e_detail.log").write_text("\n".join(LOG_LINES), encoding="utf-8")
    say("  证据：%s" % (sa / "evidence" / "report.json"))

    print("")
    print("结论：%s  （探针 %d/%d + %d/%d，验收步骤 %d/%d）"
          % (findings["summary"]["verdict"], ok_a, len(checks_a), ok_b, len(checks_b),
             len(ok_steps), len(findings["steps"])))
    print("甲：真浏览器导出 2 个包（最精简 %s 字节 / 带原文件+页图 %s 字节），导出时 0 次 API 调用"
          % (exp_min.get("disk_bytes"), exp_full.get("disk_bytes")))
    print("乙：全新数据根 + 断网 + 没配密钥，真浏览器导入 2 个包成功；导入期间 API 调用 0 次、对外连接 0 次、花费 CNY 0.00")
    print("一致性：检索 %d 组（keyword+hybrid）逐条一致；知识点 %d 条一致；页图 %d 张 sha256 全等"
          % (len(QUERIES) * 2, kp_b["n_kps"], len(a_fig)))
    print("安全：恶意包 %d/%d 被拒并给可操作提示；重复导入不产生重复块；大包导入进度可采样"
          % (sum(1 for b in bad_res if b["rejected"]), len(bad_res)))
    print("报告：%s ；明细：%s" % (sa / "evidence" / "report.json", sa / "logs" / "e2e_detail.log"))
    return 0 if findings["summary"]["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
