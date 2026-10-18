# C-0054 只读取证：各库 documents x figure_desc 交叉统计
# 只读打开（mode=ro），绝不写库
import sqlite3, sys, os, json
from pathlib import Path

LIBS = {
    "默认库": r"G:\study_agent\index\study.db",
    "有机化学": r"G:\study_agent\subjects\有机化学\index\study.db",
    "分析化学": r"G:\study_agent\subjects\分析化学\index\study.db",
}
LOGROOTS = {
    "默认库": Path(r"G:\study_agent\logs\figure_desc"),
    "有机化学": Path(r"G:\study_agent\subjects\有机化学\logs\figure_desc"),
    "分析化学": Path(r"G:\study_agent\subjects\分析化学\logs\figure_desc"),
}

import re
def safe_dirname(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name)[:60]

for label, db in LIBS.items():
    print("=" * 78)
    print(f"## {label}  {db}")
    if not os.path.exists(db):
        print("  (库不存在)")
        continue
    uri = "file:" + db.replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)")]
    print("  documents 列:", cols)
    have_fig = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='figure_desc'"
    ).fetchone()
    figmap = {}
    if have_fig:
        for r in conn.execute(
            "SELECT doc_id, COUNT(*) n, SUM(COALESCE(no_info,0)) ni,"
            " SUM(CASE WHEN COALESCE(description,'')<>'' THEN 1 ELSE 0 END) d,"
            " MAX(page) maxp FROM figure_desc GROUP BY doc_id"):
            figmap[int(r["doc_id"])] = dict(r)
    rows = conn.execute("SELECT * FROM documents ORDER BY id").fetchall()
    print(f"  文档总数: {len(rows)}")
    nopdf = []
    for r in rows:
        d = dict(r)
        fid = int(d["id"])
        f = figmap.get(fid)
        stem = Path(str(d.get("path") or "").replace("\\", "/")).stem
        diskdir = LOGROOTS[label] / safe_dirname(stem)
        npng = len(list(diskdir.glob("slide_*.png"))) if diskdir.is_dir() else -1
        kind = d.get("kind") or ""
        flag = ""
        if kind in ("pdf", "pptx") and not f:
            flag = "  <<< 无识图记录"
            nopdf.append((fid, kind, str(d.get("path")), npng))
        print(f"  id={fid:>3} kind={kind:<6} n_chunks={d.get('n_chunks'):>4} "
              f"status={str(d.get('status')):<8} fig页={f['n'] if f else 0:>3} "
              f"no_info={f['ni'] if f else 0:>3} 描述非空={f['d'] if f else 0:>3} "
              f"图最大页={f['maxp'] if f else 0:>3} 磁盘png={npng:>3} | {stem[:44]}{flag}")
    print(f"  ---- pdf/pptx 且无 figure_desc 记录的文档: {len(nopdf)} 份")
    for fid, kind, p, npng in nopdf:
        print(f"       id={fid} kind={kind} 磁盘png={npng} :: {p}")
    conn.close()
