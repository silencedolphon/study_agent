# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
"""卡 C-0080 验证宿主：起一个**完全隔离**的看板实例，供 CDP 探针在真浏览器里点。

为什么不能直接拿 8756 那个看板验：
  · 本卡要验的「导出 → 导入」会**写库**。用户的真实库里有 6 个学科、
    几万条切块，在它上面跑导入就是污染真实数据；
  · 所以这里把 `KB_RAW/KB_NOTES/KB_INDEX/KB_DB/KB_LOG` 全部指到
    `.c0080tmp/`（在项目根下，`documents.path` 的相对路径才有意义），
    并且把 `subjects.subjects_root()` 换掉，让「导入到哪个学科库」的下拉
    只看见临时造的两个库。

对真实库的唯一接触是**只读导出**一份种子包（`core.share.export_doc` 全程只
SELECT，见 core/share.py）。种子包含原课件文件，用于让"包含原课件文件"这个
勾选框在界面上真的可用。

跑法（临时端口，绝不碰 8756）：
    venv\\Scripts\\python.exe tools\\verify_c0080_share_ui.py --port 8793 --fresh

它打印一行 `C0080_READY {...}`（纯 ASCII JSON）后常驻；把 `.c0080tmp/STOP`
写出来它就会清理本次在 share\\ 里新增的包并退出。
"""
from __future__ import annotations

import argparse
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
import zipfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
TMP = PROJ / ".c0080tmp"
SHARE_DIR = PROJ / "share"

# 种子来源：真实库里**最小的一份**课件（只读用）。挑它是因为导出快、包小，
# 而它带着 25 页识图描述与知识点总结产物——正好覆盖本卡要展示的字段。
SEED_SLUG = "药物化学"
SEED_DOC = 1
FAKE_SLUG = "演示第二库"


def _env_setup() -> None:
    """★ 必须在 `import core.config` **之前**调用。

    `core/config.py` 的路径一律在模块导入时从环境变量取值，晚了就晚了。
    """
    pairs = (("KB_RAW", TMP / "raw"), ("KB_NOTES", TMP / "notes"),
             ("KB_INDEX", TMP / "index"), ("KB_DB", TMP / "index" / "study.db"),
             ("KB_LOG", TMP / "logs"))
    for key, val in pairs:
        os.environ[key] = str(val)
    os.environ["PYTHONIOENCODING"] = "utf-8"


def _prepare_dirs(fresh: bool) -> None:
    if fresh and TMP.exists():
        shutil.rmtree(TMP, ignore_errors=True)
    for sub in ("raw", "index", "notes", "logs", "seed", "subjects"):
        (TMP / sub).mkdir(parents=True, exist_ok=True)
    # 造一个"第二个学科库"，用来验「导入到哪个学科库」的下拉真的能换库
    d = TMP / "subjects" / FAKE_SLUG
    for sub in ("raw", "index", "notes", "logs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "subject.json").write_text(json.dumps(
        {"name": FAKE_SLUG, "created_at": "2026-10-10T00:00:00", "note": "C-0080 验证用"},
        ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_pngs(stem: str, n: int = 25) -> int:
    """给临时库造出识图页图，让「包含识图页图」这个勾选框在界面上可用。"""
    from core import share as share_mod
    pdir = TMP / "logs" / "figure_desc" / share_mod.safe_dirname(stem)
    pdir.mkdir(parents=True, exist_ok=True)
    blob = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1180
    for i in range(1, n + 1):
        (pdir / ("slide_%d.png" % i)).write_bytes(blob)
    return n


def _repack(src: Path, dest: Path, *, swap_embedding=None, break_chunks=False) -> None:
    """按需改一个包的副本，造出「模型不一致」与「包损坏」两种失败路径。

    造「模型不一致」必须**整包自洽**：manifest.embedding、embeddings.jsonl 每行的
    model/dim、向量字节数（dim*4）与 manifest.files 里的 sha256 **一起**改。
    少改一样，包就会先被结构闸拦下——那时验到的是"包坏了"，不是"模型不一致"，
    而本卡要验的恰恰是后者那句「包用的是 X，你当前是 Y」。
    """
    with zipfile.ZipFile(src) as zf:
        items = {i.filename: zf.read(i.filename) for i in zf.infolist()}
    man = json.loads(items["manifest.json"].decode("utf-8"))
    if swap_embedding is not None:
        model, dim = swap_embedding
        rows = [json.loads(x) for x in
                items["embeddings.jsonl"].decode("utf-8").splitlines() if x.strip()]
        for row in rows:
            row["model"], row["dim"] = model, dim
            # ★ 必须用**不可压缩**的随机字节：换成全零向量的话，embeddings.jsonl
            #   的压缩比会到 400+ 倍，先被 C-0079 的"zip 炸弹"闸拦下
            #   （实测报"压缩比 430 倍，超过上限 200 倍"）——那样验到的是另一条闸。
            row["vec_b64"] = base64.b64encode(os.urandom(dim * 4)).decode("ascii")
        blob = ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode("utf-8")
        items["embeddings.jsonl"] = blob
        man["embedding"] = {"model": model, "dim": dim}
        for f in (man.get("files") or []):
            if f.get("name") == "embeddings.jsonl":
                f["bytes"] = len(blob)
                f["sha256"] = hashlib.sha256(blob).hexdigest()
    if break_chunks:
        # 故意只改内容、**不**更新 manifest 里的 sha256 ⇒ 触发"包被改过"那条闸
        lines = [x for x in items["chunks.jsonl"].decode("utf-8").splitlines() if x.strip()]
        if len(lines) > 1:
            obj = json.loads(lines[1])
            obj["text"] = str(obj.get("text") or "") + "（被 C-0080 验证改坏）"
            lines[1] = json.dumps(obj, ensure_ascii=False)
        items["chunks.jsonl"] = ("\n".join(lines) + "\n").encode("utf-8")
    items["manifest.json"] = json.dumps(man, ensure_ascii=False, indent=2).encode("utf-8")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in items.items():
            zf.writestr(name, data)


def _share_snapshot() -> set[str]:
    if not SHARE_DIR.is_dir():
        return set()
    return {p.name for p in SHARE_DIR.glob("*.zip")}


def _share_cleanup(before: set[str]) -> list[str]:
    """删掉本次验证在 share\\ 里新增的包（不动验证之前就在那儿的任何文件）。"""
    removed: list[str] = []
    if not SHARE_DIR.is_dir():
        return removed
    for p in sorted(SHARE_DIR.glob("*.zip")):
        if p.name in before:
            continue
        try:
            p.unlink()
            removed.append(p.name)
        except OSError as e:
            removed.append("%s（删不掉：%s）" % (p.name, e))
    try:
        if not any(SHARE_DIR.iterdir()):
            SHARE_DIR.rmdir()
            removed.append("（share\\ 已空，目录一并删掉）")
    except OSError:
        pass
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="C-0080 隔离看板实例")
    ap.add_argument("--port", type=int, default=8793)
    ap.add_argument("--fresh", action="store_true", help="重建 .c0080tmp/")
    ap.add_argument("--json", default="", help="把就绪信息同时写进这个文件")
    args = ap.parse_args()

    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", args.port)) == 0:
            print("端口 %d 已被占用，换一个" % args.port, file=sys.stderr)
            return 2

    before_share = _share_snapshot()
    _prepare_dirs(args.fresh)
    _env_setup()
    sys.path.insert(0, str(PROJ))

    from core import share as share_mod            # noqa: E402（在 env 之后）
    from core import subjects as subjects_mod      # noqa: E402

    seed_db = PROJ / "subjects" / SEED_SLUG / "index" / "study.db"
    seed_log = PROJ / "subjects" / SEED_SLUG / "logs"
    seed_raw = PROJ / "subjects" / SEED_SLUG / "raw"
    if not seed_db.is_file():
        print("找不到种子库：%s" % seed_db, file=sys.stderr)
        return 3

    # ---- 1. 只读真实库 → 种子包（带原课件文件，界面上的勾选框才有意义）----
    seed_zip = TMP / "seed" / "seed-medchem.zip"
    r = share_mod.export_doc(SEED_DOC, seed_zip, db_path=seed_db, log_dir=seed_log,
                             raw_dir=seed_raw, root=PROJ, with_original=True,
                             exported_by="C-0080 验证", note="种子（只读真实库导出）")
    print("[种子] %s（%d 字节）" % (r["zip"], r["bytes"]), flush=True)

    # ---- 2. 两个失败路径的包：模型不一致 / 包损坏；外加一个不是 zip 的文件 ----
    bad_model = TMP / "seed" / "bad-model.zip"
    _repack(seed_zip, bad_model,
            swap_embedding=("Xenova/bge-large-zh-v1.5", 1024))
    broken = TMP / "seed" / "broken.zip"
    _repack(seed_zip, broken, break_chunks=True)
    notzip = TMP / "seed" / "notzip.zip"
    notzip.write_bytes("这不是一个 zip 文件（C-0080 验证用）".encode("utf-8"))

    # ---- 3. 把种子导进临时库：这样资料页上有"已入库资料"，导出按钮才有对象 ----
    imp = share_mod.import_package(str(seed_zip), db_path=TMP / "index" / "study.db",
                                  log_dir=TMP / "logs", raw_dir=TMP / "raw", root=PROJ)
    n_pages = _seed_pngs(Path(str(imp["file"])).stem) if imp["file"] else 0
    print("[临时库] 已入库 doc#%s %s（块 %d / 向量 %d / 页图 %d）"
          % (imp["doc_id"], imp["title"], imp["counts"]["chunks"],
             imp["counts"]["embeddings"], n_pages), flush=True)

    # ---- 4. 加载看板模块（不调 main()，所以不会去 restore 用户注册表）----
    spec = importlib.util.spec_from_file_location(
        "dash_c0080", PROJ / "tools" / "dashboard.py")
    dash = importlib.util.module_from_spec(spec)
    sys.modules["dash_c0080"] = dash
    spec.loader.exec_module(dash)                 # noqa: E402

    # ★ 只改"学科目录在哪"这一件事：真实注册表、真实学科目录一个都不碰。
    subjects_mod.subjects_root = lambda: TMP / "subjects"
    dash.config.ensure_dirs()

    srv = dash.ThreadingHTTPServer(("127.0.0.1", args.port), dash.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    info = {
        "ok": True, "port": args.port, "pid": os.getpid(),
        "tmp": str(TMP), "db": str(TMP / "index" / "study.db"),
        "share_dir": str(SHARE_DIR),
        "seed_zip": str(seed_zip), "bad_model_zip": str(bad_model),
        "broken_zip": str(broken), "notzip_zip": str(notzip),
        "doc_id": int(imp["doc_id"]), "title": imp["title"],
        "chunks": int(imp["counts"]["chunks"]),
        "embeddings": int(imp["counts"]["embeddings"]),
        "pages": n_pages, "fake_subject": FAKE_SLUG,
        "share_before": sorted(before_share),
    }
    line = "C0080_READY " + json.dumps(info, ensure_ascii=True)
    print(line, flush=True)
    if args.json:
        Path(args.json).write_text(json.dumps(info, ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    stop = TMP / "STOP"
    if stop.exists():
        stop.unlink()
    print("[就绪] http://127.0.0.1:%d/  （写出 %s 即退出并清理）" % (args.port, stop),
          flush=True)
    try:
        while not stop.exists():
            time.sleep(0.4)
    except KeyboardInterrupt:
        pass
    srv.shutdown()
    removed = _share_cleanup(before_share)
    print("[清理] share\\ 里本次新增并已删除：%s" % (removed or "无"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
