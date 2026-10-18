# -*- coding: utf-8 -*-
"""卡 C-0102：把看板跑在**临时数据根**上的启动器（验收专用，看板自身不引用它）。

血统：`tools\\c0100_tmp_launch.py` / `tools\\c0101_tmp_launch.py`（同一套隔离手法）。
为什么必须有它：`subjects.restore()` 会按 `<项目根>\\logs\\subjects.json` 的 `current`
把 config 的 5 条路径重绑到**真实学科库**；而本卡要真浏览器验证知识点窗口里的
「教材补充」框。这里把「注册表位置」与「学科根」两处都指到临时目录，于是：

    restore() 读不到注册表 → 回 default 学科
    → `_DEFAULT_PATHS` = 本进程 import config 时的初值 = 上面设的 KB_* 环境变量
    → raw/notes/index/logs 全部落在临时目录里

★ 与 C-0100/C-0101 的一处**关键差别**：这里 **不覆盖 `cfg.ROOT`**。
   `core.teach.resolve_textbook_pdf()` 的安全闸判据是"教材 PDF 必须落在
   `config.ROOT` 之内"，而真教材在 `G:\\study_agent\\raw\\...`；把 ROOT 指到临时
   数据根会让每一页 `/api/teach-page` 都被 403 挡掉，"打开教材页"这条就没法验了。
   ROOT 保持项目根 ⇒ 教材 PDF 只以 **PyMuPDF 只读**方式打开；
   库仍是**副本**（写入只落副本），logs/notes/raw 也都在临时数据根里。

用法：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0102_tmp_launch.py \\
        <临时数据根> <端口> [--copy-db <真库>] [--logs-from <真logs>] \\
        [--drop-textbook] [--nomatch-fixture]
"""
from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRUE_LOGS = ROOT / "logs"

# 造 no_match 用的脱靶知识点：词项与药理学教材**零交集**。踩过两次坑，记在这里：
#   ① 中文写（"…在药理学教材里不存在"）→ `_terms()` 切出「在药理学教」「教材」等
#      2~6 字窗口，命中教材前言，status 又回到 ok；
#   ② 英文写但带了 the/for/and/are/target/card 这类常用词 → 命中 p.398 的
#      "moleculartargetedtherapy"。
# `_ASCII_TERM = [A-Za-z][A-Za-z0-9\-_]{2,}` 是**整词**匹配（不切窗口），所以只留
# 四个纯专名即可：教材（中文 PDF）里不会出现 Zariski / etale / cohomology /
# Grothendieck —— 一个词项都不命中，才走得到 `no_match`。
NOMATCH_KP = {
    "id": "KP-C0102-NOMATCH",
    "doc_id": 103,
    "name": "Zariski etale cohomology",
    "summary": "Zariski etale Grothendieck cohomology",
    "points": ["Zariski etale cohomology", "Grothendieck"],
    "pages": [],
    "node_id": "",
    "node_name": "Zariski etale cohomology",
    "node_path": [],
    "level": 3,
    "sources": [],
    "sources_rejected": [],
    "insufficient": False,
    "quality": "",
    "material": [],
    "material_chars": 0,
    "n_material_blocks": 0,
    "n_body_blocks": 0,
    "n_fig_blocks": 0,
    "usage": {},
    "material_block_ids": [],
    "fixture": "C-0102 no_match",
}


def copy_db(src: Path, dst: Path) -> None:
    """真库 → 副本：用 sqlite 的 backup API 做**一致性快照**（只读源库）。

    比 `copyfile` 稳：真库可能开着 WAL，主文件里未必有最新事务；backup 走的是
    一次真读事务的快照。源连接 `mode=ro`，**不写真库一个字节**。
    """
    src_conn = sqlite3.connect("file:%s?mode=ro" % src.as_posix(), uri=True)
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tmp", help="临时数据根（会被建出来）")
    ap.add_argument("port", type=int)
    ap.add_argument("--copy-db", default="", help="真库路径（只读源；复制成临时库）")
    ap.add_argument("--logs-from", default="", help="真 logs 路径：只复制 knowledge/ 与 outline/")
    ap.add_argument("--drop-textbook", action="store_true",
                    help="在**副本**库里把 role='textbook' 改成 courseware（验 no_textbook 分支）")
    ap.add_argument("--nomatch-fixture", action="store_true",
                    help="往**副本**产物里追加一条脱靶知识点（验 no_match 分支）")
    a = ap.parse_args()
    tmp = Path(a.tmp).resolve()
    for sub in ("raw", "notes", "index", "logs"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    dst = tmp / "index" / "study.db"

    if a.copy_db:
        src = Path(a.copy_db).resolve()
        if not src.exists():
            print("C0102-TMP-FAIL 源库不存在：%s" % src)
            return 2
        copy_db(src, dst)
        print("[C-0102 临时实例] 真库 → 副本（backup API，只读源）：%s → %s（%.1f MB）"
              % (src, dst, dst.stat().st_size / 1024 / 1024))

    if a.logs_from:
        src = Path(a.logs_from).resolve()
        for sub in ("knowledge", "outline"):
            s = src / sub
            if s.is_dir():
                shutil.copytree(s, tmp / "logs" / sub, dirs_exist_ok=True)
                print("[C-0102 临时实例] 复制产物 %s → %s" % (s, tmp / "logs" / sub))

    if a.drop_textbook:
        conn = sqlite3.connect(str(dst))
        try:
            n = conn.execute("UPDATE documents SET role='courseware' "
                             "WHERE role='textbook'").rowcount
            conn.commit()
        finally:
            conn.close()
        print("[C-0102 临时实例] 副本库里 %d 份教材的 role → courseware（验 no_textbook）" % n)

    if a.nomatch_fixture:
        p = tmp / "logs" / "knowledge" / "103-末级知识点总结.json"
        if not p.exists():
            print("C0102-TMP-FAIL 没有 %s，先 --logs-from 真 logs" % p)
            return 2
        doc = json.loads(p.read_text(encoding="utf-8"))
        kps = doc.setdefault("knowledge_points", [])
        kps[:] = [k for k in kps if k.get("id") != NOMATCH_KP["id"]]
        kps.append(dict(NOMATCH_KP))
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print("[C-0102 临时实例] 产物里追加脱靶知识点 %s（验 no_match）" % NOMATCH_KP["id"])

    # 必须在 import core.config 之前设好：config 的路径常量是模块级快照。
    os.environ["KB_RAW"] = str(tmp / "raw")
    os.environ["KB_NOTES"] = str(tmp / "notes")
    os.environ["KB_INDEX"] = str(tmp / "index")
    os.environ["KB_DB"] = str(dst)
    os.environ["KB_LOG"] = str(tmp / "logs")
    os.environ["KB_SETTINGS_FILE"] = str(tmp / "config.local.json")

    sys.path.insert(0, str(ROOT))
    import core.config as cfg                    # noqa: E402
    import core.subjects as subjects_mod         # noqa: E402

    # ★ 只改学科注册表的位置；`cfg.ROOT` 保持项目根（教材 PDF 的安全闸要它，见文件头）
    subjects_mod.registry_path = lambda: tmp / "logs" / "subjects.json"   # type: ignore[assignment]
    subjects_mod.subjects_root = lambda: tmp / "subjects"                 # type: ignore[assignment]

    print("[C-0102 临时实例] 数据根 = %s" % tmp)
    print("[C-0102 临时实例] db=%s（存在=%s）" % (cfg.DB_PATH, Path(cfg.DB_PATH).exists()))
    print("[C-0102 临时实例] logs=%s | ROOT=%s（不改：教材页安全闸判据）"
          % (cfg.LOG_DIR, cfg.ROOT))
    print("[C-0102 临时实例] 端口 = %d" % a.port)
    if not cfg.SETTINGS_FILE.exists():
        cfg.SETTINGS_FILE.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
    sys.stdout.flush()

    sys.argv = ["dashboard.py", "--host", "127.0.0.1", "--port", str(a.port), "--no-open"]
    runpy.run_path(str(ROOT / "tools" / "dashboard.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
