# -*- coding: utf-8 -*-
"""C-0090 实测防覆盖（卡面红线「坑 1」）：重跑**之后**，通用入口必须挡住教材产物。

方案文档 §四·补 第 1 条点名的最危险的坑：人在「教材入库」上做好的按章切块，
会被下一次「入库全部待处理文件」用旧切块抹掉。C-0089 的对策是给教材产物打
`documents.extra.chunk_scheme="textbook-v1"`，通用入口遇到就摘出去。

本脚本**不打桩、不 mock、不直接调函数**——起一个**临时端口**的看板实例
（绝不碰用户的 8756），用真实 HTTP 打这两个入口：

  ① `POST /api/ingest`            —— 「单份入库」/「入库全部待处理文件」背后那条路
  ② `POST /api/ingest/batch/plan` —— 批量入库的预估（只读，不写库）

并给出**前后指纹**证明库一个字节都没动（块数 / 块 id 集合 / heading_path 分布）。

⚠ 刻意**不用空 paths 打 /api/ingest**（那等于"入库全部待处理文件"的真实执行）：
raw/ 下有几份**从未入库**的文件（如 `药理/27章-甲状腺激素及抗甲状腺药.pdf`），
全量真跑会把它们**新增进用户的库**——那是本卡红线（不要动别的东西）。
所以"全部待处理文件"这条入口用**批量预估**（只读）来实测，语义完全一致。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_guard_test.py
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

DB = ROOT / "index" / "study.db"
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0090")
PORT = 8799
REL = "药理/药理学 第6版 刘晓东.pdf"
DOC_ID = 91


def fingerprint() -> dict:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    ids = [int(r[0]) for r in conn.execute(
        "SELECT id FROM chunks WHERE doc_id=? ORDER BY id", (DOC_ID,))]
    hps = [tuple(r) for r in conn.execute(
        "SELECT heading_path, COUNT(*) FROM chunks WHERE doc_id=? "
        "GROUP BY heading_path ORDER BY heading_path", (DOC_ID,))]
    doc = conn.execute("SELECT n_chunks, extra FROM documents WHERE id=?",
                       (DOC_ID,)).fetchone()
    n_all = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    conn.close()
    return {
        "n_chunks": len(ids),
        "id_sha1": hashlib.sha1(",".join(map(str, ids)).encode()).hexdigest(),
        "heading_paths": [{"hp": h, "n": n} for h, n in hps],
        "doc_n_chunks": int(doc["n_chunks"]) if doc else None,
        "doc_extra": (doc["extra"] if doc else None),
        "n_documents": n_all,
    }


def http(path: str, payload: dict, timeout: float = 120.0):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"_http_error": e.read().decode("utf-8", "replace")}
    except Exception as e:                       # noqa: BLE001
        return 0, {"_error": f"{type(e).__name__}: {e}"}


def pending_files() -> list[str]:
    """raw/ 下全部文件的 raw 相对路径（= 前端「入库全部待处理文件」给的 d.rel）。"""
    from core import ingest as ingest_mod
    out = []
    for f in ingest_mod.iter_files([Path(ROOT / "raw")]):
        out.append(str(f.relative_to(ROOT / "raw")).replace("\\", "/"))
    return out


def main() -> int:
    res: dict = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "port": PORT,
                 "steps": []}
    before = fingerprint()
    res["before"] = before
    extra = json.loads(before["doc_extra"] or "{}")
    res["chunk_scheme"] = extra.get("chunk_scheme")
    print(f"doc#91 chunk_scheme = {extra.get('chunk_scheme')!r}  "
          f"块数={before['n_chunks']} heading_path {len(before['heading_paths'])} 种")

    srv = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(ROOT / "tools" / "dashboard.py"),
         "--port", str(PORT), "--no-open"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    res["server_pid"] = srv.pid
    up = False
    t0 = time.time()
    while time.time() - t0 < 25:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/api/stats", timeout=2) as r:
                if r.status == 200:
                    up = True
                    break
        except Exception:                        # noqa: BLE001
            time.sleep(0.6)
    res["server_up"] = up
    print(f"临时看板 127.0.0.1:{PORT} 就绪 = {up}（{time.time() - t0:.1f}s）")
    if not up:
        srv.kill()
        res["error"] = "临时看板没起来"
        (CARD / "guard_result.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    try:
        # ---- ① 通用单份入库：应当被**拒绝**（targets 全被摘走）----
        st, body = http("/api/ingest", {"paths": [REL], "no_figures": True})
        res["steps"].append({"name": "POST /api/ingest {paths:[doc#91]}",
                             "http": st, "resp": body})
        blocked = bool(body.get("textbook_locked"))
        err = str(body.get("error") or "")
        print(f"\n① /api/ingest  -> http={st} error={err[:80]!r}")
        print(f"   textbook_locked={json.dumps(body.get('textbook_locked'), ensure_ascii=False)[:220]}")
        res["ingest_blocked"] = bool(blocked and err)
        res["ingest_hint_has_howto"] = ("教材入库" in (err + json.dumps(
            body.get("textbook_locked"), ensure_ascii=False)))

        # ---- ② 批量入库预估（「入库全部待处理文件」的只读那一步）----
        pend = pending_files()
        res["pending_n"] = len(pend)
        res["pending_has_doc91"] = REL in pend
        st2, body2 = http("/api/ingest/batch/plan",
                          {"paths": pend, "no_figures": True})
        res["steps"].append({"name": "POST /api/ingest/batch/plan {raw 全部}",
                             "http": st2, "resp": body2})
        locked2 = body2.get("textbook_locked") or []
        files2 = body2.get("files") or body2.get("items") or []
        res["batch_blocked_n"] = len(locked2)
        res["batch_blocked_paths"] = [x.get("path") for x in locked2]
        res["batch_plan_files_n"] = len(files2)
        res["batch_plan_contains_doc91"] = any(
            REL in json.dumps(x, ensure_ascii=False) for x in files2)
        print(f"\n② /api/ingest/batch/plan（raw 全部 {len(pend)} 份）-> http={st2}")
        print(f"   textbook_locked = {json.dumps(locked2, ensure_ascii=False)[:220]}")
        print(f"   计划的文件数 = {len(files2)}，其中含 doc#91 = "
              f"{res['batch_plan_contains_doc91']}")
    finally:
        srv.kill()
        try:
            srv.wait(timeout=5)
        except Exception:                        # noqa: BLE001
            pass

    after = fingerprint()
    res["after"] = after
    res["unchanged"] = {
        "n_chunks": before["n_chunks"] == after["n_chunks"],
        "id_sha1": before["id_sha1"] == after["id_sha1"],
        "heading_paths": before["heading_paths"] == after["heading_paths"],
        "n_documents": before["n_documents"] == after["n_documents"],
    }
    res["ok"] = (res.get("ingest_blocked") and res.get("batch_blocked_n", 0) >= 1
                 and not res["batch_plan_contains_doc91"]
                 and all(res["unchanged"].values()))
    print(f"\n库指纹未变：{res['unchanged']}")
    print(f"防覆盖实测 ok = {res['ok']}")
    CARD.mkdir(parents=True, exist_ok=True)
    (CARD / "guard_result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写入 {CARD / 'guard_result.json'}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
