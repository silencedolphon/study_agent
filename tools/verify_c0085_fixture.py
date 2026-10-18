# -*- coding: utf-8 -*-
"""卡 C-0085 验证夹具：在**临时数据根**里造一份"可安全重命名"的资料集。

为什么必须造：重命名会**动真实文件与数据库**（改 raw 里的文件名、改 documents.path、
搬 logs/figure_desc 下的目录），绝不能在真实学科库上试。但"造"也得造得像真的——
所以这里不是凭空捏数据，而是**从真库搬**：

  · `documents` 那两行的字段逐列照抄（含真实 content_hash、真实 path 形态
    `raw\\05. Med Chem.pptx`）⇒ 改完能对着真形态核对；
  · 整理产物**拷真库那一份**（`logs/knowledge/103-末级知识点总结.json`，含完整的
    `structure.root / raw_root / nodes / knowledge_points`）⇒ 四处名字、节点 id 与
    知识点关联都是真的，不是空壳（空壳验不出"关联断没断"）；
  · 页图目录里的 `slide_*.png` 也**拷真图**（只拷、不动源）⇒ 改完目录名后能真的数出图。

三份被测资料（都在临时库里，各有各的用途）：
  A `med`      ：doc#103 `raw\\05. Med Chem.pptx` —— 有 v2 产物 + 有页图目录，
                 四类重命名里的②③④都拿它验；
  B `plain`    ：doc#73 `raw\\6肾上腺素受体激动药.pptx` —— **没有** v2 产物
                 （验"没有产物时改产物标题被明确拒绝，而不是报 500"）；
  C `unindexed`：`raw\\未入库的讲义.pdf` —— **不在库**（验"未入库文件改名只动磁盘"）。
  另加一份 `longname`：文件名主干 **超过 60 字符**，页图目录名是
  `share.safe_dirname(stem)` 截断后的形态 —— 验"看板按截断名也能找到页图"
  （卡 C-0085 顺手补齐的缺口，此前这种课件即使没改过名也永远取不到页图）。

学科侧：临时数据根里自带一个学科 `subjects/临时学科/`（含 subject.json 与空库），
用来验"学科库重命名只改 subject.json 的 name、slug 目录与路径一个字不动"。

作为**模块**被 `tools/verify_c0085_rename.py` 调用；单独跑
（`python tools\\verify_c0085_fixture.py`）只是把夹具造出来留着看，便于人工核对。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "index" / "study.db"
REAL_LOG = ROOT / "logs"

MED_ID = 103              # 真库里有 v2 产物与页图目录的那份
PLAIN_ID = 73             # 真库里在、但没有 v2 产物
MED_REL = r"raw\05. Med Chem.pptx"
PLAIN_REL = r"raw\6肾上腺素受体激动药.pptx"
UNINDEXED_REL = r"raw\未入库的讲义.pdf"
MED_DECK = "05. Med Chem"          # = _safe_dirname(Path(MED_REL).stem)
KP_SUFFIX = "-末级知识点总结.json"

# 文件名主干刻意超过 60 字符：写入侧 `figures._safe_dirname` 会截断到 60，
# 于是目录名 ≠ stem —— 正是"按 stem 找不到页图"那个缺口的触发条件。
LONG_STEM = ("全国普通高等中医药院校药学类专业十三五规划教材第二轮规划教材"
             "药理学思维导图与学习指导超长文件名测试用例追加更多字符让主干超过六十个字符上限")
LONG_REL = "raw\\" + LONG_STEM + ".pptx"
LONG_ID = 104


def _rows(db: Path, sql: str, args: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _insert_dicts(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        cols = list(r.keys())
        conn.execute("INSERT INTO %s (%s) VALUES (%s)"
                     % (table, ",".join(cols), ",".join("?" * len(cols))),
                     [r[c] for c in cols])
        n += 1
    return n


def _copy_deck(src_stem: str, dst_dir: Path, limit: int = 3) -> int:
    """从真库的页图目录拷几个**真 png** 过来（只拷，真库一个字节不改）。"""
    src = REAL_LOG / "figure_desc" / src_stem
    if not src.is_dir():
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.glob("slide_*.png"))[:limit]:
        shutil.copy2(p, dst_dir / p.name)
        n += 1
    return n


def _fake_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n c0085 fixture")


def prepare(dataroot: Path, *, verbose: bool = True) -> dict:
    """在 `dataroot` 下造出完整的临时数据根 + 资料夹具，返回 manifest。"""
    dataroot = Path(dataroot)
    if dataroot.exists():
        shutil.rmtree(dataroot, ignore_errors=True)
    raw = dataroot / "raw"
    logs = dataroot / "logs"
    kdir = logs / "knowledge"
    froot = logs / "figure_desc"
    for d in (raw, kdir, froot, dataroot / "index", dataroot / "notes",
              dataroot / "subjects"):
        d.mkdir(parents=True, exist_ok=True)

    log = (lambda s: print("  " + s)) if verbose else (lambda s: None)

    # ---------- 1. 临时库：照真库建表 + 搬两行 documents ----------
    db_dst = dataroot / "index" / "study.db"
    sys.path.insert(0, str(ROOT))
    import core.db as dbmod  # noqa: E402
    conn = dbmod.connect(db_dst)
    dbmod.init_db(conn)

    want = [MED_ID, PLAIN_ID]
    docs = _rows(REAL_DB, "SELECT * FROM documents WHERE id IN (%s)"
                 % ",".join("?" * len(want)), tuple(want))
    got = {int(d["id"]) for d in docs}
    missing = [i for i in want if i not in got]
    if missing:
        raise RuntimeError("真库里找不到这些 doc_id：%s" % missing)
    # abs_path 指向真库 —— 必须改写成临时数据根下的路径，否则夹具自带一条
    # "abs_path 指着别处"的脏数据，会把"改名同步 abs_path"这条验糊。
    for d in docs:
        rel = str(d["path"]).replace("/", "\\")
        if rel.lower().startswith("raw\\"):
            rel = rel[4:]
        d["abs_path"] = str(raw / rel)
    # 长名那份也入库：它的页图目录名是**截断名**（≠ stem），用来验
    # 「看板按截断名也能找到页图」（卡 C-0085 顺手补的缺口）。
    tpl = dict(docs[0])
    tpl.update({"id": LONG_ID, "path": LONG_REL,
                "abs_path": str(raw / (LONG_STEM + ".pptx")),
                "title": "长文件名测试（页图目录名被截断）", "n_chunks": 0,
                "content_hash": "c0085-long-name"})
    docs.append(tpl)
    _insert_dicts(conn, "documents", docs)
    conn.commit()
    conn.close()
    log("临时库建表完成，搬入 documents %d 行：%s"
        % (len(docs), [int(d["id"]) for d in docs]))

    # ---------- 2. raw 原文件（要有真实字节，验"文件真的改名了"） ----------
    raw_files: dict[str, str] = {}
    med_p = raw / "05. Med Chem.pptx"
    med_p.write_bytes(b"C0085 fixture med bytes\n" * 8)
    raw_files["med"] = str(med_p)
    plain_p = raw / "6肾上腺素受体激动药.pptx"
    plain_p.write_bytes(b"C0085 fixture plain bytes\n" * 8)
    raw_files["plain"] = str(plain_p)
    long_p = raw / (LONG_STEM + ".pptx")
    long_p.write_bytes(b"C0085 fixture long bytes\n" * 8)
    raw_files["longname"] = str(long_p)
    un_p = raw / "未入库的讲义.pdf"
    un_p.write_bytes(b"C0085 fixture unindexed bytes\n" * 8)
    raw_files["unindexed"] = str(un_p)
    log("raw 原文件 %d 个" % len(raw_files))

    # content_hash 改成**这些假字节的真实 sha256**：否则 `/api/raw` 会把三份都报成
    # 「内容有变」（真库拷来的 hash 对不上），而"改名不改内容"这条断言就被噪声淹了。
    conn = dbmod.connect(db_dst)
    for did, p in ((MED_ID, med_p), (PLAIN_ID, plain_p), (LONG_ID, long_p)):
        conn.execute("UPDATE documents SET content_hash=?, size_bytes=? WHERE id=?",
                     (dbmod.sha256_file(p), p.stat().st_size, did))
    conn.commit()
    conn.close()

    # ---------- 3. 页图目录（真图优先，缺了就用最小 PNG 头） ----------
    med_deck = froot / MED_DECK
    n_med = _copy_deck(MED_DECK, med_deck, 3)
    if n_med == 0:
        med_deck.mkdir(parents=True, exist_ok=True)
        for i in (1, 2, 3):
            _fake_png(med_deck / ("slide_%02d.png" % i))
            (med_deck / ("slide_%02d.md" % i)).write_text("# 夹具页\n", encoding="utf-8")
            n_med += 1
    # 长名那份：目录名按**写入侧的真实规则**截断（share.safe_dirname == figures._safe_dirname）
    import core.share as share_mod  # noqa: E402
    long_deck_name = share_mod.safe_dirname(LONG_STEM)
    long_deck = froot / long_deck_name
    long_deck.mkdir(parents=True, exist_ok=True)
    for i in (1, 2):
        _fake_png(long_deck / ("slide_%02d.png" % i))
    log("页图目录：%s（%d 张）/ %s（2 张，目录名已按 60 字截断）"
        % (MED_DECK, n_med, long_deck_name))

    # ---------- 4. 整理产物：拷真库那一份（含完整 structure） ----------
    real_art = REAL_LOG / "knowledge" / ("%d%s" % (MED_ID, KP_SUFFIX))
    if not real_art.is_file():
        raise RuntimeError("真库产物不存在，夹具不成立：%s" % real_art)
    art_dst = kdir / ("%d%s" % (MED_ID, KP_SUFFIX))
    doc = json.loads(real_art.read_text(encoding="utf-8"))
    st = doc.get("structure") if isinstance(doc.get("structure"), dict) else {}
    if not isinstance(st.get("root"), dict):
        raise RuntimeError("真库产物里没有 structure.root，夹具不成立")
    art_dst.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def _spots(d: dict) -> dict:
        s = d.get("structure") or {}
        nodes = s.get("nodes") or []
        return {"root": (s.get("root") or {}).get("name"),
                "raw_root": (s.get("raw_root") or {}).get("name"),
                "node0": (nodes[0] or {}).get("name") if nodes else None,
                "deck": (d.get("meta") or {}).get("deck")}

    n_kp = len(doc.get("knowledge_points") or [])
    log("产物拷贝完成：knowledge_points=%d，四处名字=%s"
        % (n_kp, json.dumps(_spots(doc), ensure_ascii=False)))

    # ---------- 5. 一个学科（验"学科库重命名只改 name"） ----------
    slug = "临时学科"
    sdir = dataroot / "subjects" / slug
    for sub in ("raw", "notes", "index", "logs"):
        (sdir / sub).mkdir(parents=True, exist_ok=True)
    sc = dbmod.connect(sdir / "index" / "study.db")
    dbmod.init_db(sc)
    sc.close()
    (sdir / "subject.json").write_text(json.dumps({
        "name": "临时学科", "slug": slug, "created_at": "2026-10-12T00:00:00",
        "note": "卡 C-0085 夹具", "layout": {
            "RAW_DIR": str(sdir / "raw"), "NOTES_DIR": str(sdir / "notes"),
            "INDEX_DIR": str(sdir / "index"), "DB_PATH": str(sdir / "index" / "study.db"),
            "LOG_DIR": str(sdir / "logs")},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    # ⚠ **刻意不写注册表**（`logs/subjects.json`）：看板启动时 `subjects.restore()`
    #   读不到注册表 → 回 default 学科 → 5 条路径就是 KB_* 环境变量那套（本数据根），
    #   夹具文档才落在看板看得见的地方。注册表留给"改名"这一步去创建——
    #   那正是要验的东西之一（改名后 current 不漂）。
    log("学科：subjects/%s（subject.json；注册表刻意不写，让 restore 回 default）" % slug)

    man = {
        "dataroot": str(dataroot),
        "db": str(db_dst),
        "raw_dir": str(raw),
        "log_dir": str(logs),
        "med_id": MED_ID, "med_rel": MED_REL, "med_deck": MED_DECK,
        "plain_id": PLAIN_ID, "plain_rel": PLAIN_REL,
        "unindexed_rel": UNINDEXED_REL,
        "long_rel": LONG_REL, "long_stem": LONG_STEM, "long_id": LONG_ID,
        "long_deck_name": long_deck_name,
        "subject_slug": slug,
        "raw_files": raw_files,
        "artifact": str(art_dst),
        "artifact_spots": _spots(doc),
        "artifact_kp_n": n_kp,
        "artifact_nodes_n": len((doc.get("structure") or {}).get("nodes") or []),
        "deck_dir": str(med_deck),
    }
    (dataroot / "fixture_manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    return man


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    dr = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_tmp_c0085" / "fixture_data"
    print(json.dumps(prepare(dr), ensure_ascii=False, indent=2))
