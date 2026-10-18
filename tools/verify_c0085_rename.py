#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""卡 C-0085 交付验证：重命名（学科库 / 入库后的原文件名 / 课件标题 / 产物标题）。

一句话验的是：**四类重命名各自闭环——改完之后，该变的都变了、该连带的副本一处不落、
该不动的一个字节没动，而且撞上正在跑的入库/整理时会被拒绝。**

跑法（工作目录＝项目根，cmd 与 pwsh 都能跑）：
    .\\venv\\Scripts\\python.exe tools\\verify_c0085_rename.py --quick   快速自检（约 20 秒）
    .\\venv\\Scripts\\python.exe tools\\verify_c0085_rename.py           整轮（额外起真浏览器）

两种模式验的是**同一套后端判据**，整轮多一条：真浏览器里走一遍页面点击。

整轮做的事情：
  1. 静态证据：四个接口 / 四条路由 / 四类改名函数都在，前端入口都在；
     老路径（单份移除、批量真删、清除产物）**一个符号都没被我碰**；
  2. 造夹具（`verify_c0085_fixture.py`：从真库搬 documents 行 + 真产物 JSON + 真页图，
     长文件名那份的页图目录名是真截断名）；
  3. 真起一个看板实例（**临时数据根 + 临时端口**，注册表与学科根都指到临时目录），
     先自证"它确实在操作临时数据根"再动手；
  4. 四类重命名逐条验（每一类都走 dry_run → 缺 confirm 拒绝 → 有任务在跑拒绝(409) →
     confirm 真做 → 逐项核对磁盘与库）；
  5. 回归：不改名时那批只读接口逐条 200 且关键字段没变；
  6. 整轮额外：headless Edge 走页面（学科菜单改名 / 资料页改名 / 改标题 / 导图页改产物标题）。

stdout 只打结论（≤6 行），明细写 `_tmp_c0085\\verify.log`；失败项立刻打 stdout。
"""
from __future__ import annotations

import argparse
import hashlib
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
SUBJ_SRC = ROOT / "core" / "subjects.py"
TMPROOT = ROOT / "_tmp_c0085"
DATAROOT = TMPROOT / ("verify_data_%d" % os.getpid())
LOGPATH = TMPROOT / "verify.log"
QUICK_LOGPATH = TMPROOT / "verify_quick.log"
PY = ROOT / "venv" / "Scripts" / "python.exe"
NODE = shutil.which("node") or "node"

# ⚠ 必须在 import core.* 之前把路径钉到临时数据根：`core.config` 的路径常量是
#   模块级快照，导完再改就晚了（`_DEFAULT_PATHS` 会记成真库）。
for _k, _v in (("KB_RAW", "raw"), ("KB_NOTES", "notes"), ("KB_INDEX", "index"),
               ("KB_LOG", "logs")):
    os.environ[_k] = str(DATAROOT / _v)
os.environ["KB_DB"] = str(DATAROOT / "index" / "study.db")

sys.path.insert(0, str(ROOT))
import core.config as cfg                       # noqa: E402
cfg.ROOT = DATAROOT                             # 与 c0085_tmp_launch.py 同构
from core import ingest as ingest_mod           # noqa: E402
from core import db as dbmod                    # noqa: E402

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


# ---------------------------------------------------------------- 小工具
def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def q(db: Path, sql: str, args: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def load_fixture_module():
    p = ROOT / "tools" / "verify_c0085_fixture.py"
    spec = importlib.util.spec_from_file_location("verify_c0085_fixture", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
        except Exception:  # noqa: BLE001
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


def get_bytes(port: int, path: str, timeout: int = 60):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:  # noqa: BLE001
        return 0, b""


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_ready(port: int, proc: subprocess.Popen, timeout: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False
        st, _ = get_json(port, "/api/stats", timeout=5)
        if st == 200:
            return True
        time.sleep(0.4)
    return False


# ---------------------------------------------------------------- 1. 静态证据
def static_checks() -> None:
    src = SRC.read_text(encoding="utf-8")
    subj = SUBJ_SRC.read_text(encoding="utf-8")

    for anchor, why in (
            ("def api_rename_raw(conn, payload: dict) -> dict:", "原文件重命名接口"),
            ("def api_rename_doc_title(conn, payload: dict) -> dict:", "课件标题重命名接口"),
            ("def api_rename_artifact_title(conn, payload: dict) -> dict:", "产物标题重命名接口"),
            ("def api_subjects_rename(payload: dict) -> dict:", "学科库重命名接口"),
            ('if u.path == "/api/rename/raw":', "原文件重命名路由"),
            ('if u.path == "/api/doc/rename":', "课件标题重命名路由"),
            ('if u.path == "/api/artifact/rename":', "产物标题重命名路由"),
            ('"/api/subjects/rename"', "学科重命名路由"),
            ("def _rename_running_gate() -> dict | None:", "与运行中任务互斥的守卫"),
            ("def _clean_new_name(name, *, field: str = \"名字\",", "名字校验"),
            ("def _artifact_title_spots(raw: dict) -> dict:", "产物四处落点"),
            ("def _deck_name_candidates(stem: str) -> tuple[str, ...]:",
             "页图目录名候选（顺手补的截断名缺口）"),
            ("def rename(slug: str, name: str) -> dict:", "subjects.rename"),
            ("def _default_name() -> str:", "默认学科名字（注册表 default_name）"),
            ('"default_name": default_name,', "注册表新增一格")):
        check("静态：%s 在（%s）" % (why, anchor), anchor in src or anchor in subj)

    check("静态：页图目录推导**两条路**都用候选表（读图与删图同一口径）",
          src.count("for name in _deck_name_candidates(stem):") == 2
          and "stem + \"_slides\"" not in src.split("def _deck_dir_for")[1]
          .split("def _slide_files")[0])

    body = src[src.index("def api_rename_raw("):src.index("def api_rename_doc_title(")]
    check("静态：原文件改名三步齐全（磁盘 → 库 → 页图目录）且失败会回滚",
          "os.replace(src, dst)" in body
          and "UPDATE documents SET path=?, abs_path=?" in body
          and "d.rename(nd)" in body
          and "逆序回滚" in body)
    check("静态：原文件改名**不允许改扩展名**（入库类型由它决定）",
          "new_suffix.casefold() != old_suffix.casefold()" in body)

    art = src[src.index("def api_rename_artifact_title("):src.index("def cleanup_orphans(")]
    for spot in ('root["name"] = clean', 'rr["name"] = clean',
                 'nodes[0]["name"] = clean', 'meta["deck"] = clean'):
        check("静态：产物标题四处同步改（%s）" % spot, spot in art)
    check("静态：产物标题落盘方式与 kp_build._save 一致（tmp + os.replace + indent=1）",
          'json.dump(raw, f, ensure_ascii=False, indent=1)' in art
          and "os.replace(tmp, p)" in art and "shutil.copy2(p, bak)" in art)

    # 回归：老路径一个符号都没被碰
    one = src[src.index("def api_delete_doc(conn, doc_id: int"):src.index("def api_delete_raw(")]
    check("回归：单份移除（api_delete_doc）里没有我的新符号",
          "_rename_" not in one and "_clean_new_name" not in one)
    kpclear = src[src.index("def api_kp_clear(payload: dict) -> dict:"):
                  src.index("# ---------------- 资料管理：浏览 raw")]
    check("回归：清除整理产物（api_kp_clear）里没有我的新符号",
          "_rename_" not in kpclear and "_artifact_title_spots" not in kpclear)

    js = src[src.index("<script>"):src.index("</script>")]
    for anchor, why in (
            ('id="subj-ren"', "学科菜单里的「重命名学科…」入口"),
            ("function subjRenameDialog(slug)", "学科改名弹窗"),
            ("async function subjRenameGo()", "学科改名提交"),
            ("async function renameRawFile(docId, rel, oldName)", "原文件改名弹窗"),
            ("async function renameDocTitle(docId, oldTitle)", "课件标题改名弹窗"),
            ("async function renameArtifactTitle(docId, oldName)", "产物标题改名弹窗"),
            ("window.renameRawFile=renameRawFile;", "window 绑定（onclick 字符串里要用）"),
            ('/api/subjects/rename', "前端调学科改名接口"),
            ('/api/rename/raw', "前端调原文件改名接口"),
            ('/api/doc/rename', "前端调课件标题改名接口"),
            ('/api/artifact/rename', "前端调产物标题改名接口"),
            ('id="mm-ren-deck"', "导图页的「改课件标题」按钮"),
            ('id="mm-ren-art"', "导图页的「改产物标题」按钮")):
        check("前端：%s 在（%s）" % (why, anchor), anchor in js)


# ---------------------------------------------------------------- 2~5. 主体
def live_checks(port: int, man: dict) -> None:  # noqa: C901
    db = Path(man["db"])
    raw = Path(man["raw_dir"])
    logs = Path(man["log_dir"])
    froot = logs / "figure_desc"
    kdir = logs / "knowledge"
    MED = int(man["med_id"])
    PLAIN = int(man["plain_id"])
    LONG = int(man["long_id"])
    SLUG = str(man["subject_slug"])

    def row_of(doc_id: int) -> dict:
        rs = q(db, "SELECT * FROM documents WHERE id=?", (doc_id,))
        return rs[0] if rs else {}

    def n_docs() -> int:
        return int(q(db, "SELECT COUNT(*) AS n FROM documents")[0]["n"])

    def hook(label: str) -> dict:
        st, d = post_json(port, "/api/_test/running", {"label": label})
        return d

    # ---------- 0. 先自证：看板确实在临时数据根上 ----------
    st, d = post_json(port, "/api/_test/paths", {})
    paths = (d or {}).get("paths") or {}
    check("看板实例跑在**临时数据根**上（不是真库）",
          Path(paths.get("ROOT", "")).resolve() == DATAROOT.resolve()
          and Path(paths.get("RAW_DIR", "")).resolve() == raw.resolve(),
          json.dumps(paths, ensure_ascii=False)[:220])
    st, subj0 = get_json(port, "/api/subjects")
    check("学科快照可读：当前是 default（夹具没写注册表）",
          st == 200 and subj0.get("current") == "default"
          and any(s["slug"] == SLUG for s in subj0.get("subjects") or []),
          "current=%s" % subj0.get("current"))

    # ================================================================ ① 学科库改名
    st, r1 = post_json(port, "/api/subjects/rename",
                       {"slug": SLUG, "name": "临时学科-改名后"})
    subj_json = dataroot_subject_json(man)
    reg = dataroot_registry(man)
    layout_before = json.loads((Path(man["dataroot"]) / "subjects" / SLUG
                                / "subject.json").read_text(encoding="utf-8"))
    check("① 学科改名：接口 200 且回执写明「目录未动」",
          st == 200 and r1.get("ok") and (r1.get("renamed") or {}).get("moved_dir") is False,
          "status=%s renamed=%s" % (st, json.dumps(r1.get("renamed"), ensure_ascii=False)))
    check("① 学科改名：subject.json 的 name 改了、slug 与 layout 五条路径**逐字未变**",
          subj_json.get("name") == "临时学科-改名后"
          and subj_json.get("slug") == SLUG
          and subj_json.get("layout") == layout_before.get("layout"),
          json.dumps({"name": subj_json.get("name"), "slug": subj_json.get("slug")},
                     ensure_ascii=False))
    check("① 学科改名：slug **目录名**没变、也没有新建一个「改名后」的目录",
          (Path(man["dataroot"]) / "subjects" / SLUG).is_dir()
          and not (Path(man["dataroot"]) / "subjects" / "临时学科-改名后").exists())
    check("① 学科改名：注册表 current 不漂（仍 default），副本 name 已同步",
          str(reg.get("current")) == "default"
          and any(x["slug"] == SLUG and x["name"] == "临时学科-改名后"
                  for x in reg.get("subjects") or []),
          json.dumps({"current": reg.get("current"),
                      "subjects": reg.get("subjects")}, ensure_ascii=False)[:300])
    st, subj1 = get_json(port, "/api/subjects")
    got = [s for s in (subj1.get("subjects") or []) if s["slug"] == SLUG]
    check("① 学科改名：/api/subjects 里显示新名，dir 仍是原目录",
          st == 200 and got and got[0]["name"] == "临时学科-改名后"
          and Path(got[0]["dir"]).name == SLUG,
          json.dumps(got, ensure_ascii=False)[:220])
    check("① 学科改名：清进程内缓存的回执在（cleared 四项齐全）",
          isinstance(r1.get("cleared"), dict)
          and set(("knowledge", "lkp", "slide", "kp_opt")) <= set(r1["cleared"]),
          json.dumps(r1.get("cleared"), ensure_ascii=False))

    # 改**显示名与自己相同**：不该被"已存在同名学科"挡住（exclude_slug 的作用）
    st, r_same = post_json(port, "/api/subjects/rename",
                           {"slug": SLUG, "name": "临时学科-改名后"})
    check("① 学科改名：改成与自己相同的名字不算撞名（不被自己挡住）",
          st == 200 and r_same.get("ok"), "status=%s err=%s" % (st, r_same.get("error")))
    # 撞**别人**的名字：必须拒绝
    st, r_dup = post_json(port, "/api/subjects/rename",
                          {"slug": SLUG, "name": "默认"})
    check("① 学科改名：撞上默认学科的名字 → 400 拒绝（原话可读）",
          st == 400 and "同名" in str(r_dup.get("error")),
          "status=%s err=%s" % (st, r_dup.get("error")))
    # 默认学科也能改名（人的默认库资料最多）
    st, r_def = post_json(port, "/api/subjects/rename",
                          {"slug": "default", "name": "默认库-改名测试"})
    reg = dataroot_registry(man)
    st2, subj2 = get_json(port, "/api/subjects")
    d0 = [s for s in (subj2.get("subjects") or []) if s["slug"] == "default"]
    check("① 默认学科也能改名（落在注册表 default_name 一格，dir 不动）",
          st == 200 and reg.get("default_name") == "默认库-改名测试"
          and d0 and d0[0]["name"] == "默认库-改名测试"
          and Path(d0[0]["dir"]).resolve() == Path(man["dataroot"]).resolve(),
          json.dumps({"default_name": reg.get("default_name"),
                      "entry": d0[0] if d0 else None}, ensure_ascii=False)[:260])
    st, r401 = post_json(port, "/api/subjects/rename", {"slug": "不存在的学科", "name": "x"})
    check("① 学科改名：不存在的 slug → 400（不是 500）",
          st == 400 and "不存在" in str(r401.get("error")), "status=%s" % st)

    # ================================================================ ② 原文件改名
    med_file = raw / "05. Med Chem.pptx"
    new_name = "05. Med Chem（改名）.pptx"
    new_file = raw / new_name
    old_deck = froot / man["med_deck"]
    new_deck = froot / "05. Med Chem（改名）"
    doc_before = row_of(MED)
    hash_before = sha256(med_file)
    n_before = n_docs()
    n_deck_png = len(list(old_deck.glob("slide_*.png")))
    deck_files_before = sorted(x.name for x in old_deck.iterdir())

    st, pv = post_json(port, "/api/rename/raw",
                       {"rel": "05. Med Chem.pptx", "new_name": new_name, "dry_run": True})
    check("② 原文件改名：dry_run 预览给出「改前 / 改后」三处对照",
          st == 200 and pv.get("dry_run")
          and pv["file"]["to"].endswith(new_name)
          and pv["db"]["before"]["path"] == doc_before["path"]
          and pv["db"]["after"]["path"] == "raw\\" + new_name
          and pv["figure_desc"] and pv["figure_desc"][0]["to"].endswith("05. Med Chem（改名）"),
          json.dumps({"file": pv.get("file"), "db": pv.get("db"),
                      "decks": pv.get("figure_desc")}, ensure_ascii=False)[:300])
    check("② dry_run 只算不动（磁盘、库、页图目录三处都没变）",
          med_file.is_file() and not new_file.exists() and old_deck.is_dir()
          and row_of(MED)["path"] == doc_before["path"])

    st, nc = post_json(port, "/api/rename/raw",
                       {"rel": "05. Med Chem.pptx", "new_name": new_name})
    check("② 缺 confirm → need_confirm，且一个字节都没动",
          st == 200 and nc.get("need_confirm") is True
          and med_file.is_file() and not new_file.exists()
          and row_of(MED)["path"] == doc_before["path"],
          "status=%s need_confirm=%s" % (st, nc.get("need_confirm")))

    hook("入库")
    st, busy = post_json(port, "/api/rename/raw",
                         {"rel": "05. Med Chem.pptx", "new_name": new_name, "confirm": True})
    check("② 有任务在跑（入库）→ 拒绝改名，且回执是可操作的提示",
          st == 409 and "有任务正在跑" in str(busy.get("error"))
          and med_file.is_file() and not new_file.exists(),
          "status=%s err=%s" % (st, busy.get("error")))
    hook("")

    st, done = post_json(port, "/api/rename/raw",
                         {"rel": "05. Med Chem.pptx", "new_name": new_name, "confirm": True})
    check("② 原文件改名：confirm 后真做（回执 ok + 三处连带结果）",
          st == 200 and done.get("ok") and done.get("dry_run") is False,
          "status=%s err=%s" % (st, done.get("error")))
    check("② 磁盘：旧文件名消失、新文件名在，**内容一个字节没变**",
          (not med_file.exists()) and new_file.is_file() and sha256(new_file) == hash_before)
    doc_after = row_of(MED)
    check("② 库：documents.path / abs_path 同步更新（给改前后对照）",
          doc_after["path"] == "raw\\" + new_name
          and Path(doc_after["abs_path"]) == new_file,
          "path: %s → %s" % (doc_before["path"], doc_after["path"]))
    check("② 库：content_hash 未变（按内容判重，重入不会重复计费）",
          doc_after["content_hash"] == doc_before["content_hash"])
    check("② 库：没有多出/少掉 doc 行，doc_id 也没变",
          n_docs() == n_before and doc_after["id"] == MED,
          "n=%d→%d" % (n_before, n_docs()))
    # ★ 重入判据：ingest 认人靠 `WHERE path=?`（core/ingest.py::_ensure_doc_row），
    #   它先用 `_rel()` 把磁盘路径算成"相对项目根"。这两者一致 ⇒ 命中既有行、不新建。
    rel_now = ingest_mod._rel(new_file)
    hit = q(db, "SELECT id FROM documents WHERE path=?", (rel_now,))
    check("② 重入不会新建 doc 行（ingest._rel(磁盘) == documents.path，按 path 认人命中）",
          rel_now == doc_after["path"] and len(hit) == 1 and int(hit[0]["id"]) == MED,
          "rel=%s hits=%s" % (rel_now, [h["id"] for h in hit]))
    check("② 页图目录跟着改名（否则界面页图会全丢）",
          (not old_deck.exists()) and new_deck.is_dir()
          and len(list(new_deck.glob("slide_*.png"))) == n_deck_png,
          "旧存在=%s 新 png=%d" % (old_deck.exists(), len(list(new_deck.glob("slide_*.png")))))
    check("② 页图目录里的文件一个没少（旧目录的清单原样搬到新目录名下了）",
          sorted(x.name for x in new_deck.iterdir()) == deck_files_before,
          "before=%s after=%s" % (deck_files_before[:4],
                                  sorted(x.name for x in new_deck.iterdir())[:4]))
    st, imgs = get_bytes(port, "/img/slide?doc_id=%d&page=1" % MED)
    check("② 改名后 /img/slide 仍能取到页图（HTTP 200 + 有字节）",
          st == 200 and len(imgs) > 0, "status=%s bytes=%d" % (st, len(imgs)))
    st, mi = get_json(port, "/api/mindmap")
    it = [x for x in (mi.get("documents") or []) if x.get("doc_id") == MED]
    check("② 改名后页签仍认得这份课件（/api/mindmap 里标题与页数正常）",
          st == 200 and it and it[0].get("title") == doc_after["title"]
          and int(it[0].get("n_pages") or 0) > 0,
          json.dumps(it[0], ensure_ascii=False)[:220] if it else "not found")
    st, rl = get_json(port, "/api/raw")
    files = {f["rel"]: f for f in (rl.get("files") or [])}
    check("② /api/raw 里新 rel 已对上这条 doc、旧 rel 消失、也不是孤儿",
          st == 200 and (files.get(new_name) or {}).get("doc_id") == MED
          and (files.get(new_name) or {}).get("state") == "indexed"
          and "05. Med Chem.pptx" not in files
          and not [o for o in (rl.get("orphans") or []) if o.get("id") == MED],
          "doc_id=%s state=%s" % ((files.get(new_name) or {}).get("doc_id"),
                                  (files.get(new_name) or {}).get("state")))

    # 三条拒绝（安全边界）
    st, e1 = post_json(port, "/api/rename/raw",
                       {"rel": new_name, "new_name": "改名成txt.txt", "confirm": True})
    check("② 拒绝改扩展名（入库类型会失效）", st == 200 and "扩展名" in str(e1.get("error")),
          str(e1.get("error")))
    st, e2 = post_json(port, "/api/rename/raw",
                       {"rel": new_name, "new_name": "子目录\\跑出去.pptx", "confirm": True})
    check("② 拒绝带路径片段的新名（不许顺手把文件搬走）",
          st == 200 and ("路径" in str(e2.get("error")) or "文件名" in str(e2.get("error"))),
          str(e2.get("error")))
    st, e3 = post_json(port, "/api/rename/raw",
                       {"rel": new_name, "new_name": "6肾上腺素受体激动药.pptx",
                        "confirm": True})
    check("② 拒绝覆盖同名文件（目标已存在）", st == 200 and "已存在" in str(e3.get("error")),
          str(e3.get("error")))

    # 未入库的文件：只动磁盘
    st, e4 = post_json(port, "/api/rename/raw",
                       {"rel": "未入库的讲义.pdf", "new_name": "未入库的讲义-改名.pdf",
                        "confirm": True})
    check("② 未入库的文件也能改名（只动磁盘、库行数不变）",
          st == 200 and e4.get("ok") and (raw / "未入库的讲义-改名.pdf").is_file()
          and not (raw / "未入库的讲义.pdf").exists() and n_docs() == n_before,
          "status=%s err=%s" % (st, e4.get("error")))

    # 长文件名（页图目录名是被截断的清洗名）——顺手补的缺口
    st, b = get_bytes(port, "/img/slide?doc_id=%d&page=1" % LONG)
    check("② 长文件名课件（目录名按 60 字截断）也能取到页图（本轮补的缺口）",
          st == 200 and len(b) > 0,
          "status=%s bytes=%d｜目录名=%s" % (st, len(b), man["long_deck_name"][:20] + "…"))

    # ================================================================ ③ 课件标题
    art_p = kdir / ("%d-末级知识点总结.json" % MED)
    art_hash_before = sha256(art_p)
    st, tp = post_json(port, "/api/doc/rename",
                       {"doc_id": MED, "title": old_title_of(doc_after),
                        "dry_run": True})
    check("③ 课件标题：dry_run 预览给出改前改后 + 产物里的当前根名",
          st == 200 and tp.get("dry_run") and "from" in (tp.get("title") or {})
          and tp.get("artifact_deck") == "Medicinal Chemistry"
          and tp.get("artifact_will_change") is False,
          json.dumps(tp, ensure_ascii=False)[:260])
    st, nc3 = post_json(port, "/api/doc/rename",
                        {"doc_id": MED, "title": "药物化学 第五章（改名）"})
    check("③ 缺 confirm → need_confirm（库里标题没动）",
          st == 200 and nc3.get("need_confirm") is True
          and row_of(MED)["title"] == doc_after["title"])
    hook("知识点整理")
    st, busy3 = post_json(port, "/api/doc/rename",
                          {"doc_id": MED, "title": "药物化学 第五章（改名）",
                           "confirm": True})
    check("③ 有任务在跑（知识点整理）→ 拒绝改课件标题（409）",
          st == 409 and "有任务正在跑" in str(busy3.get("error")), "status=%s" % st)
    hook("")
    st, t3 = post_json(port, "/api/doc/rename",
                       {"doc_id": MED, "title": "药物化学 第五章（改名）", "confirm": True})
    check("③ 课件标题：confirm 后库里标题变了（/api/docs 也显示新值）",
          st == 200 and t3.get("ok") and row_of(MED)["title"] == "药物化学 第五章（改名）")
    st, docs2 = get_json(port, "/api/docs")
    d3 = [x for x in (docs2.get("documents") or []) if x["id"] == MED]
    check("③ 列表接口显示新标题（刷新即见，不需要重启）",
          st == 200 and d3 and d3[0]["title"] == "药物化学 第五章（改名）",
          json.dumps(d3[0] if d3 else None, ensure_ascii=False)[:200])
    check("③ 产物文件**一个字节都没动**（改标题不重跑整理）",
          sha256(art_p) == art_hash_before)
    st, e5 = post_json(port, "/api/doc/rename", {"doc_id": MED, "title": "   ",
                                                 "confirm": True})
    check("③ 空标题被拒（原话可读，不是 500）", st == 200 and "不能为空" in str(e5.get("error")),
          str(e5.get("error")))
    st, e6 = post_json(port, "/api/doc/rename", {"doc_id": 999999, "title": "x",
                                                 "confirm": True})
    check("③ 不存在的 doc_id → 明确拒绝（不是 500）",
          st == 200 and "不存在" in str(e6.get("error")), str(e6.get("error")))

    # ================================================================ ④ 产物标题
    before_doc = json.loads(art_p.read_text(encoding="utf-8"))
    before_kp_ids = [k.get("id") for k in (before_doc.get("knowledge_points") or [])]
    before_nodes = [(n.get("id"), tuple(n.get("kp_ids") or []), n.get("parent_id"))
                    for n in ((before_doc.get("structure") or {}).get("nodes") or [])]
    before_owner = dict((before_doc.get("structure") or {}).get("page_owner") or {})

    st, ap = post_json(port, "/api/artifact/rename",
                       {"doc_id": MED, "name": "占位", "dry_run": True})
    check("④ 产物标题：dry_run 列出四处当前值",
          st == 200 and ap.get("dry_run")
          and all((ap.get("spots_before") or {}).get(k) == "Medicinal Chemistry"
                  for k in ("structure.root.name", "structure.raw_root.name",
                            "structure.nodes[0].name", "meta.deck")),
          json.dumps(ap.get("spots_before"), ensure_ascii=False))
    st, nc4 = post_json(port, "/api/artifact/rename",
                        {"doc_id": MED, "name": "Medicinal Chemistry（改名）"})
    check("④ 缺 confirm → need_confirm（产物文件未动）",
          st == 200 and nc4.get("need_confirm") is True and sha256(art_p) == art_hash_before)
    hook("批量整理")
    st, busy4 = post_json(port, "/api/artifact/rename",
                          {"doc_id": MED, "name": "Medicinal Chemistry（改名）",
                           "confirm": True})
    check("④ 有任务在跑（批量整理）→ 拒绝改产物标题（409）",
          st == 409 and "有任务正在跑" in str(busy4.get("error")), "status=%s" % st)
    hook("")
    st, a4 = post_json(port, "/api/artifact/rename",
                       {"doc_id": MED, "name": "Medicinal Chemistry（改名）",
                        "confirm": True})
    check("④ 产物标题：confirm 后四处同步改（changed 列出四处）",
          st == 200 and a4.get("ok") and len(a4.get("changed") or []) == 4
          and all(a4["spots_after"][k] == "Medicinal Chemistry（改名）"
                  for k in ("structure.root.name", "structure.raw_root.name",
                            "structure.nodes[0].name", "meta.deck")),
          json.dumps({"changed": a4.get("changed"), "after": a4.get("spots_after")},
                     ensure_ascii=False)[:300])
    after_doc = json.loads(art_p.read_text(encoding="utf-8"))
    after_kp_ids = [k.get("id") for k in (after_doc.get("knowledge_points") or [])]
    after_nodes = [(n.get("id"), tuple(n.get("kp_ids") or []), n.get("parent_id"))
                   for n in ((after_doc.get("structure") or {}).get("nodes") or [])]
    after_owner = dict((after_doc.get("structure") or {}).get("page_owner") or {})
    check("④ 知识点关联**一处没断**：kp id 列表、节点 id/kp_ids/parent_id、页归属逐字不变",
          after_kp_ids == before_kp_ids and after_nodes == before_nodes
          and after_owner == before_owner
          and len(after_kp_ids) == int(man["artifact_kp_n"]) and len(after_nodes) > 0,
          "kp=%d nodes=%d" % (len(after_kp_ids), len(after_nodes)))
    check("④ 产物文件名没变（由 doc_id 拼，改标题不需要改文件名）",
          art_p.is_file() and art_p.name == "%d-末级知识点总结.json" % MED)
    bak = sorted(kdir.glob("%d-末级知识点总结.json.bak-rename-*" % MED))
    check("④ 改之前自动备份了旧产物（.bak-rename-<时间戳>）", len(bak) == 1,
          str(bak[0].name) if bak else "没有备份")
    if bak:
        old = json.loads(bak[0].read_text(encoding="utf-8"))
        check("④ 备份里还是旧名字（可回退的证据）",
              ((old.get("structure") or {}).get("root") or {}).get("name")
              == "Medicinal Chemistry")
    st, mm = get_json(port, "/api/mindmap?doc_id=%d" % MED)
    tree = (mm.get("tree") or [])
    check("④ 页签渲染出来的根名就是新名（缓存没把旧值顶回来）",
          st == 200 and tree and tree[0].get("name") == "Medicinal Chemistry（改名）",
          json.dumps(tree[:1], ensure_ascii=False)[:200])
    st, e7 = post_json(port, "/api/artifact/rename",
                       {"doc_id": PLAIN, "name": "没有产物也想改", "confirm": True})
    check("④ 没有 v2 产物的课件 → 明确拒绝并说明原因（不是 500）",
          st == 200 and "没有可改名的整理产物" in str(e7.get("error")),
          str(e7.get("error"))[:160])

    # ================================================================ ⑤ 缓存与回归
    for tag, resp in (("学科", r1), ("原文件", done), ("课件标题", t3), ("产物标题", a4)):
        c = resp.get("cleared") if isinstance(resp, dict) else None
        check("⑤ %s改名后清进程内缓存（cleared 四项齐全）" % tag,
              isinstance(c, dict) and set(("knowledge", "lkp", "slide", "kp_opt")) <= set(c),
              json.dumps(c, ensure_ascii=False))

    for path, why in (("/api/stats", "总览"), ("/api/docs", "资料清单"), ("/api/raw", "raw 清单"),
                      ("/api/mindmap", "页签课件清单"), ("/api/terms", "名词解释页签"),
                      ("/api/subjects", "学科快照"), ("/api/kp-build/options", "一键整理选项")):
        st, _ = get_json(port, path)
        check("回归：%s（%s）仍然 200" % (path, why), st == 200, "status=%s" % st)
    st, mi2 = get_json(port, "/api/mindmap")
    it2 = [x for x in (mi2.get("documents") or []) if x.get("doc_id") == MED]
    check("回归：页签里这份课件的节点数与产物一致（没被改名弄丢节点）",
          it2 and int(it2[0].get("n_nodes") or 0) == int(man["artifact_nodes_n"]),
          "n_nodes=%s 期望=%s" % (it2[0].get("n_nodes") if it2 else None,
                                  man["artifact_nodes_n"]))


def old_title_of(row: dict) -> str:
    return str(row.get("title") or "")


def dataroot_subject_json(man: dict) -> dict:
    p = Path(man["dataroot"]) / "subjects" / str(man["subject_slug"]) / "subject.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def dataroot_registry(man: dict) -> dict:
    p = Path(man["dataroot"]) / "logs" / "subjects.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- 6. 真浏览器
def browser_checks(port: int, man: dict) -> None:
    mjs = ROOT / "tools" / "probe_c0085_rename.mjs"
    if not mjs.is_file():
        check("整轮：真浏览器探针脚本在", False, str(mjs))
        return
    jsout = TMPROOT / "browser_result.json"
    outdir = TMPROOT / "shots"
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [NODE, str(mjs), "--port", str(port), "--data", str(man["dataroot"]),
           "--json", str(jsout), "--outdir", str(outdir)]
    log("\n$ " + " ".join(cmd))
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        check("整轮：真浏览器探针在 10 分钟内跑完", False, "超时")
        return
    tail = (r.stdout or "")[-4000:]
    log(tail)
    if r.stderr:
        log("stderr: " + (r.stderr or "")[-2000:])
    try:
        data = json.loads(jsout.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        check("整轮：真浏览器探针产出结果 JSON", False, "rc=%s" % r.returncode)
        return
    for c in data.get("checks") or []:
        check("[浏览器] " + str(c.get("name")), bool(c.get("ok")), str(c.get("detail") or ""))
    check("整轮：浏览器全过程零 JS 错误", not (data.get("errors") or []),
          json.dumps((data.get("errors") or [])[:3], ensure_ascii=False))


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="不起真浏览器（只验后端 + HTTP）")
    ap.add_argument("--keep", action="store_true", help="保留临时数据根与日志")
    ap.add_argument("--json", default="", help="把逐项结果写成 JSON 的路径")
    a = ap.parse_args()

    TMPROOT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    static_checks()

    fixture = load_fixture_module()
    man = fixture.prepare(DATAROOT, verbose=False)
    log("夹具就绪：%s" % man["dataroot"])

    port = free_port()
    env = dict(os.environ)
    env["C0085_TEST_HOOK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [str(PY), str(ROOT / "tools" / "c0085_tmp_launch.py"), str(DATAROOT), str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    log("临时看板：http://127.0.0.1:%d（数据根=%s）" % (port, DATAROOT))
    try:
        ready = wait_ready(port, proc)
        check("临时看板实例就绪（临时数据根 + 临时端口，不动 8756）", ready,
              "port=%d" % port)
        if ready:
            live_checks(port, man)
            if not a.quick:
                browser_checks(port, man)
            else:
                log("（--quick：跳过真浏览器；整轮请去掉 --quick）")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    ok = sum(1 for r in RESULTS if r[0])
    bad = [r for r in RESULTS if not r[0]]
    took = time.time() - t0
    logpath = QUICK_LOGPATH if a.quick else LOGPATH
    header = [
        "卡 C-0085 重命名验证（%s）：%d/%d 通过，用时 %.1fs"
        % ("quick" if a.quick else "整轮", ok, len(RESULTS), took),
        "数据根（临时，跑完%s）：%s" % ("保留" if a.keep else "清理", DATAROOT),
        "明细日志：%s" % logpath,
    ]
    if bad:
        header.append("失败项（前 3 条）：")
        for r in bad[:3]:
            header.append("  · %s | %s" % (r[1], r[2][:160]))
    else:
        header.append("四类重命名全部闭环：学科库 / 原文件 / 课件标题 / 产物标题")
    text = "\n".join(header) + "\n\n" + "\n".join(LOG_LINES) + "\n"
    logpath.write_text(text, encoding="utf-8")
    print("\n".join(header), flush=True)

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"ok": not bad, "n_ok": ok, "n_total": len(RESULTS), "elapsed": round(took, 1),
             "dataroot": str(DATAROOT),
             "checks": [{"name": n, "ok": o, "detail": d} for o, n, d in RESULTS]},
            ensure_ascii=False, indent=2), encoding="utf-8")

    if not a.keep:
        shutil.rmtree(DATAROOT, ignore_errors=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
