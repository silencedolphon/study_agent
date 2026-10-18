# -*- coding: utf-8 -*-
"""卡 C-0084 验证夹具：在**临时数据根**里造一份"可直接真删"的完整资料。

为什么必须造：真删是不可逆的，绝不能在真实学科库上试。但"造"也得造得像真的，
否则验不出东西——所以这里不是凭空捏数据，而是**从真库搬**：

  · `documents` / `chunks` / `embeddings` / `figure_desc` 四张表里那几份资料的
    行，逐列照抄进临时库（含真实向量 blob）→ 删的时候真的走外键 CASCADE、
    真的触发 FTS5 的删除触发器，不是"看起来像"；
  · 知识点侧（`knowledge_points` / `kp_sources` / `reviews` / `kp_edges` /
    `mistakes`）真库里是 0 行，所以按同样的口径**插一份**：一个孤儿知识点
    （只挂被删资料的切块）与一个幸存知识点（同时挂被删资料与保留资料的切块），
    用来分辨"该清的清了 / 不该清的没动"；
  · 四处文件系统产物按 `core/kp_build.py` 与 `core/figures.py` 的**真实命名规则**
    在临时 `logs/` 下生成（末级知识点总结 + .bak-*、名词解释、outline + .bak-*、
    页图目录 slide_*.png/.md）——一个字节都不写进真库的 logs/。

三份被测资料：
  A `del_file`  ：真库里有、产物齐 → 「连原文件一起删」
  B `keep_file` ：真库里有、产物齐 → 「保留原文件」（验 raw 不被删）
  C `ghost`     ：**不在库**、只有产物 → 验"历史幽灵条目也能一次清干净"

作为**模块**被 `tools/verify_c0084_purge.py` 调用；单独跑（python tools\\verify_c0084_fixture.py）
只是把夹具造出来留着看，便于人工核对。
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

# 被测三份（id 必须与真库一致，产物文件名与页图目录名都由 id / path 推出来）
DEL_FILE_ID = 55          # raw\药理\抗心衰药.pdf（真库里在）
KEEP_FILE_ID = 64         # raw\药理\2镇静催眠药.pptx（真库里在）
GHOST_ID = 62             # 不在库（真库里有它的 outline 与页图目录，正是"幽灵"）
GHOST_PATH = r"raw\药理\抗精神病药.pptx"   # 幽灵那份当年入库时的 path（用来推页图目录名）

# 内嵌 fixture 的另一份文档：作为"幸存切块"的宿主，保证有个知识点不该被清
KEEP2_ID = 56             # raw\药理\6镇痛药.pptx

PK = "-末级知识点总结.json"
TERMS = "-名词解释.json"


def _rows(db: Path, sql: str, args: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _insert_dicts(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    """按**列名**插入（表结构两边一致时表序无关），返回插入行数。"""
    n = 0
    for r in rows:
        cols = list(r.keys())
        conn.execute("INSERT INTO %s (%s) VALUES (%s)"
                     % (table, ",".join(cols), ",".join("?" * len(cols))),
                     [r[c] for c in cols])
        n += 1
    return n


def _mk(path: Path, text: str = "{}\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _deck_files_from_real(stem: str, dst_dir: Path, limit: int) -> int:
    """从真库的页图目录**拷几个真 png**过来（只拷，不动源；真库一个字节不改）。

    拷真的而不是造空文件：删除时要按白名单判形态、要统计字节数，
    空文件与真图在"删干净了没"这件事上没有区别，但真图能顺带验出大小统计没写反。
    """
    src = REAL_LOG / "figure_desc" / stem
    if not src.is_dir():
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.glob("slide_*.png"))[:limit]:
        shutil.copy2(p, dst_dir / p.name)
        n += 1
        if n >= limit:
            break
    return n


def prepare(dataroot: Path, *, verbose: bool = True) -> dict:
    """在 `dataroot` 下造出完整的临时数据根与 library fixture，返回 manifest。"""
    dataroot = Path(dataroot)
    if dataroot.exists():
        shutil.rmtree(dataroot, ignore_errors=True)
    raw = dataroot / "raw"
    logs = dataroot / "logs"
    kdir = logs / "knowledge"
    odir = logs / "outline"
    froot = logs / "figure_desc"
    for d in (raw, kdir, odir, froot, dataroot / "index", dataroot / "notes"):
        d.mkdir(parents=True, exist_ok=True)

    log = (lambda s: print("  " + s)) if verbose else (lambda s: None)

    # ---------- 1. 临时库：照真库建表 ----------
    db_dst = dataroot / "index" / "study.db"
    sys.path.insert(0, str(ROOT))
    import core.db as dbmod  # noqa: E402
    conn = dbmod.connect(db_dst)
    dbmod.init_db(conn)
    log("临时库建表完成：%s" % db_dst.name)

    want = [DEL_FILE_ID, KEEP_FILE_ID, KEEP2_ID]
    docs = _rows(REAL_DB, "SELECT * FROM documents WHERE id IN (%s)"
                 % ",".join("?" * len(want)), tuple(want))
    got = {int(d["id"]) for d in docs}
    missing = [i for i in want if i not in got]
    if missing:
        raise RuntimeError("真库里找不到这些 doc_id：%s" % missing)

    # 幽灵那份**刻意不入库**：真库里没有 id=62 这一行，磁盘上却留着它的产物——
    # 这正是"有产物但已不在库"的历史状态（`_artifact_doc_list` 会把它列进页签、
    # title 退化成「文档 N」）。删它时不该报错、不该中止整批，也不能删掉
    # 别的资料；它的 path 只用来推导页图目录名（这一点见下面 ghost_doc 的用途）。
    ghost_doc = dict(docs[0])
    ghost_doc.update({"id": GHOST_ID, "path": GHOST_PATH, "title": "抗精神病药（幽灵）",
                      "n_chunks": 0, "content_hash": "c0084-ghost"})
    ghost_in_library = False

    _insert_dicts(conn, "documents", docs)
    ids = [int(d["id"]) for d in docs]
    ph = ",".join("?" * len(ids))
    n_chunks = _insert_dicts(conn, "chunks", _rows(
        REAL_DB, "SELECT * FROM chunks WHERE doc_id IN (%s) ORDER BY id" % ph, tuple(ids)))
    n_emb = _insert_dicts(conn, "embeddings", _rows(
        REAL_DB, """SELECT e.* FROM embeddings e JOIN chunks c ON c.id=e.chunk_id
                    WHERE c.doc_id IN (%s)""" % ph, tuple(ids)))
    n_fig = _insert_dicts(conn, "figure_desc", _rows(
        REAL_DB, "SELECT * FROM figure_desc WHERE doc_id IN (%s)" % ph, tuple(ids)))
    log("搬入 documents=%d chunks=%d embeddings=%d figure_desc=%d"
        % (len(docs), n_chunks, n_emb, n_fig))

    # ---------- 2. 知识点侧：一个孤儿 + 一个幸存 ----------
    now = 1791795932.0
    chunk_of = {}
    for r in conn.execute("SELECT id, doc_id FROM chunks WHERE doc_id IN (%s)" % ph,
                          tuple(ids)):
        chunk_of.setdefault(int(r["doc_id"]), []).append(int(r["id"]))
    for i in ids:
        # 幽灵那份（GHOST_ID）故意**没有切块**——它代表"资料早就不在库、
        # 只剩磁盘产物"的历史状态，正是要验的清理对象之一，不该有切块。
        if i == GHOST_ID:
            continue
        if not chunk_of.get(i):
            raise RuntimeError("doc#%d 没有切块，夹具不成立" % i)

    c_del = chunk_of[DEL_FILE_ID][0]        # 只属被删资料 A
    c_keep = chunk_of[KEEP2_ID][0]          # 只属保留资料
    kps = [
        (9001, "C0084 孤儿知识点（只挂被删资料的切块）", 1),
        (9002, "C0084 幸存知识点（同时挂被删资料与保留资料）", 1),
        (9003, "C0084 手工知识点（从未有过出处，绝不能被清）", 0),
    ]
    for kid, name, had in kps:
        conn.execute("""INSERT INTO knowledge_points
            (id, name, summary, subject, mastery, ease, interval_days, reps, lapses,
             due_at, last_review, had_sources, created_at, updated_at)
            VALUES (?,?,?,?,0,2.5,0,0,0,?,NULL,?,?,?)""",
                     (kid, name, "夹具", "药物化学", now + 86400, had, now, now))
    kp_src = [(9001, c_del), (9002, c_del), (9002, c_keep)]
    for kp_id, cid in kp_src:
        conn.execute("INSERT INTO kp_sources (kp_id, chunk_id, note) VALUES (?,?,?)",
                     (kp_id, cid, "夹具"))
    conn.execute("""INSERT INTO reviews (kp_id, grade, correct, question, answer, ts)
                    VALUES (9001, 4, 1, '夹具题', '夹具答', ?)""", (now,))
    conn.execute("""INSERT INTO reviews (kp_id, grade, correct, question, answer, ts)
                    VALUES (9002, 5, 1, '夹具题2', '夹具答2', ?)""", (now,))
    conn.execute("""INSERT INTO kp_edges (src_id, dst_id, kind) VALUES (9001, 9002, 'prereq')""")
    conn.execute("""INSERT INTO kp_edges (src_id, dst_id, kind) VALUES (9002, 9003, 'related')""")
    conn.execute("""INSERT INTO mistakes (kp_id, doc_id, question, my_answer, right_answer,
                    reason, created_at) VALUES (9001, ?, '夹具错题', '我答', '正答', '概念混淆', ?)""",
                 (DEL_FILE_ID, now))
    conn.commit()
    log("知识点侧：3 个知识点 / 3 条出处 / 2 条复习 / 2 条依赖边 / 1 道错题")

    before = {t: int(conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0])
              for t in ("documents", "chunks", "embeddings", "figure_desc", "kp_sources",
                        "knowledge_points", "reviews", "kp_edges", "mistakes")}
    conn.close()

    # ---------- 3. raw 原文件（要有真实字节，验"真删掉了"） ----------
    raw_files = {}
    for did, rel in ((DEL_FILE_ID, "药理\\抗心衰药.pdf"),
                     (KEEP_FILE_ID, "药理\\2镇静催眠药.pptx"),
                     (KEEP2_ID, "药理\\6镇痛药.pptx")):
        p = raw / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(("C0084 fixture raw bytes for doc#%d\n" % did).encode("utf-8") * 8)
        raw_files[did] = p

    # ---------- 4. 四处文件系统产物 ----------
    arts: dict[int, dict] = {}
    deck_plan = {DEL_FILE_ID: ("抗心衰药", 3),
                 KEEP_FILE_ID: ("2镇静催眠药", 4),
                 GHOST_ID: ("抗精神病药", 2)}

    def _mk_artifacts(did: int, *, n_backup: int, with_terms: bool,
                      outline_backup: bool, deck: tuple | None,
                      deck_extra: str = "") -> dict:
        out = {"knowledge": [], "outline": [], "deck_dir": None, "deck_files": []}
        p = kdir / ("%d%s" % (did, PK))
        _mk(p, json.dumps({"meta": {"pipeline_version": "C-0049-v2"}, "structure": {"root": {}},
                           "knowledge_points": []}, ensure_ascii=False))
        out["knowledge"].append(p)
        for i in range(n_backup):
            b = p.with_name(p.name + ".bak-2026101%d-00000%d" % (i, i))
            _mk(b, "{}\n")
            out["knowledge"].append(b)
        if with_terms:
            t = kdir / ("%d%s" % (did, TERMS))
            _mk(t, json.dumps({"meta": {"terms_filter": {"mode": "fixture"}},
                               "terms": [{"term": "夹具名词", "def": "夹具解释"}]},
                              ensure_ascii=False))
            out["knowledge"].append(t)
        o = odir / ("%d.json" % did)
        _mk(o, json.dumps({"pages": {}}, ensure_ascii=False))
        out["outline"].append(o)
        if outline_backup:
            ob = o.with_name(o.name + ".bak-20261012-000001")
            _mk(ob, "{}\n")
            out["outline"].append(ob)
        if deck:
            stem, n = deck
            d = froot / stem
            n_copied = _deck_files_from_real(stem, d, n)
            if n_copied == 0:      # 真库没有这个目录就造几张，形态与真的一致
                d.mkdir(parents=True, exist_ok=True)
                for page in range(1, n + 1):
                    (d / ("slide_%02d.png" % page)).write_bytes(b"\x89PNG\r\n\x1a\n fixture")
                    (d / ("slide_%02d.md" % page)).write_text("# 夹具页\n", encoding="utf-8")
            else:
                for page in range(1, n + 1):
                    (d / ("slide_%02d.md" % page)).write_text("# 夹具页\n", encoding="utf-8")
            (d / "_summary.json").write_text("{}\n", encoding="utf-8")
            if deck_extra:
                (d / deck_extra).write_text("这一份不是系统生成的，删除时必须保留\n",
                                            encoding="utf-8")
            out["deck_dir"] = d
            out["deck_files"] = sorted(x.name for x in d.iterdir())
        return out

    # A（连原文件一起删）：页图目录里**只有系统生成的文件** → 目录本身也该消失；
    # B（保留原文件）  ：目录里额外放一份人工文件 → 目录必须**保留**（不删别人的东西）。
    arts[DEL_FILE_ID] = _mk_artifacts(DEL_FILE_ID, n_backup=2, with_terms=True,
                                      outline_backup=True, deck=deck_plan[DEL_FILE_ID])
    arts[KEEP_FILE_ID] = _mk_artifacts(KEEP_FILE_ID, n_backup=1, with_terms=True,
                                       outline_backup=False, deck=deck_plan[KEEP_FILE_ID],
                                       deck_extra="手工放置.txt")
    arts[GHOST_ID] = _mk_artifacts(GHOST_ID, n_backup=1, with_terms=False,
                                   outline_backup=False, deck=deck_plan[GHOST_ID])
    # 旁观者（KEEP2，全程不在任何一次删除的 doc_ids 里）：给它也造一份产物，
    # 用来验"删别的资料时，这一份的产物一件都没被动过"。
    arts[KEEP2_ID] = _mk_artifacts(KEEP2_ID, n_backup=1, with_terms=False,
                                   outline_backup=False, deck=None)
    log("产物：knowledge %d 个 / outline %d 个 / 页图目录 %d 个"
        % (sum(len(v["knowledge"]) for v in arts.values()),
           sum(len(v["outline"]) for v in arts.values()),
           sum(1 for v in arts.values() if v["deck_dir"])))

    man = {
        "dataroot": str(dataroot),
        "db": str(db_dst),
        "raw_dir": str(raw),
        "log_dir": str(logs),
        "del_file_id": DEL_FILE_ID,
        "keep_file_id": KEEP_FILE_ID,
        "ghost_id": GHOST_ID,
        "ghost_in_library": ghost_in_library,
        "ghost_path": GHOST_PATH,
        "keep2_id": KEEP2_ID,
        "raw_files": {str(k): str(v) for k, v in raw_files.items()},
        "artifacts": {str(k): {"knowledge": [str(x) for x in v["knowledge"]],
                               "outline": [str(x) for x in v["outline"]],
                               "deck_dir": str(v["deck_dir"]) if v["deck_dir"] else "",
                               "deck_files": v["deck_files"]} for k, v in arts.items()},
        "before": before,
        "kp_orphan": 9001,
        "kp_survivor": 9002,
        "kp_manual": 9003,
    }
    (dataroot / "fixture_manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    return man


if __name__ == "__main__":
    dr = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_tmp_c0084" / "fixture_data"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    m = prepare(dr)
    print(json.dumps(m, ensure_ascii=False, indent=2))
