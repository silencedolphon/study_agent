# -*- coding: utf-8 -*-
"""C-0090 重跑前备份（卡面第 1 条）。

备份到**项目内**可追溯位置（不写 %TEMP%）：

    G:\\study_agent\\backup\\c0090-doc91-rerun-<时间戳>\\
        study.db                默认学科库（SQLite 一致性备份，不是裸拷贝）
        logs_knowledge\\         logs\\knowledge\\ 整棵树
        logs_subjects.json      重跑前的学科切换状态
        其他学科库指纹.json      六个学科库的 size/mtime/sha256/行数
        MANIFEST.json           清单 + 回滚命令

回滚（库）：把备份里的 study.db 拷回 index\\study.db（命令写在 MANIFEST 里）。

只读源库 + 客观复制，不改任何库、不碰 subjects\\ 下的 raw 文件。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "index" / "study.db"
KNOW = ROOT / "logs" / "knowledge"
SUBJ = ROOT / "logs" / "subjects.json"
SUBJECTS = ROOT / "subjects"
BACKUP_ROOT = ROOT / "backup"

TABLES = ("knowledge_points", "kp_sources", "mistakes", "reviews",
          "documents", "chunks", "embeddings")


def sha256(p: Path, limit: int = 0) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def db_fingerprint(path: Path) -> dict:
    """一个库的指纹：文件属性 + 三张关键表的行数（只读打开）。"""
    out = {"path": str(path)}
    try:
        st = path.stat()
        out.update({"size": st.st_size,
                    "mtime": st.st_mtime,
                    "mtime_iso": time.strftime("%Y-%m-%d %H:%M:%S",
                                               time.localtime(st.st_mtime))})
    except OSError as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for t in TABLES:
            try:
                out[t] = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            except sqlite3.Error:
                out[t] = None
        rows = conn.execute(
            "SELECT id, path, n_chunks, status, role FROM documents ORDER BY id"
        ).fetchall()
        out["documents_rows"] = [{"id": r[0], "path": r[1], "n_chunks": r[2],
                                  "status": r[3], "role": r[4]} for r in rows]
        conn.close()
    except sqlite3.Error as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def main() -> int:
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_ROOT / f"c0090-doc91-rerun-{ts}"
    dest.mkdir(parents=True, exist_ok=True)
    print(f"备份目录 = {dest}")

    manifest: dict = {"created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "created_ts": time.time(), "dest": str(dest),
                      "card": "C-0090", "reason": "doc#91 教材专线重跑前备份"}

    # ---- 1. 默认学科库（SQLite 在线备份 API：一致性快照，含未 checkpoint 的 WAL）----
    src = sqlite3.connect(str(DB))
    dst_file = dest / "study.db"
    dst = sqlite3.connect(str(dst_file))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    manifest["study_db"] = {
        "src": str(DB), "dst": str(dst_file),
        "src_size": DB.stat().st_size, "dst_size": dst_file.stat().st_size,
        "dst_sha256": sha256(dst_file),
        "method": "sqlite3 Connection.backup()（一致性快照）",
    }
    print(f"  study.db -> {dst_file}  "
          f"({manifest['study_db']['src_size']} -> "
          f"{manifest['study_db']['dst_size']} 字节)")

    # ---- 2. logs\knowledge\ ----
    if KNOW.exists():
        dstk = dest / "logs_knowledge"
        shutil.copytree(KNOW, dstk, dirs_exist_ok=True)
        files = sorted(p for p in dstk.rglob("*") if p.is_file())
        manifest["logs_knowledge"] = {
            "src": str(KNOW), "dst": str(dstk), "files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "names": [str(p.relative_to(dstk)) for p in files][:80],
        }
        print(f"  logs\\knowledge -> {dstk}（{len(files)} 个文件）")

    # ---- 3. 学科切换状态 ----
    if SUBJ.exists():
        shutil.copy2(SUBJ, dest / "logs_subjects.json")
        manifest["subjects_json"] = {
            "src": str(SUBJ), "dst": str(dest / "logs_subjects.json"),
            "content": json.loads(SUBJ.read_text(encoding="utf-8")),
        }
        cur = manifest["subjects_json"]["content"].get("current")
        print(f"  logs\\subjects.json -> current={cur!r}")

    # ---- 4. 其他学科库指纹（只读；用来证明"没被触碰"）----
    subs: dict = {}
    for sub in sorted(SUBJECTS.iterdir()):
        dbf = sub / "index" / "study.db"
        if dbf.exists():
            subs[sub.name] = db_fingerprint(dbf)
    manifest["other_subjects"] = subs
    print(f"  其他学科库指纹：{len(subs)} 个")

    # ---- 5. 默认库自身的重跑前指纹 ----
    manifest["before"] = db_fingerprint(DB)
    print(f"  重跑前四计数 = "
          f"{ {t: manifest['before'].get(t) for t in TABLES[:4]} }")

    # ---- 6. 回滚命令 ----
    manifest["rollback"] = {
        "db": (f'Copy-Item -LiteralPath "{dst_file}" '
               f'-Destination "{DB}" -Force'),
        "db_note": "先停掉正在跑的看板/入库进程再拷，否则文件被占用",
        "logs_knowledge": (f'Remove-Item -Recurse -Force "{KNOW}"; '
                           f'Copy-Item -Recurse -LiteralPath '
                           f'"{dest / "logs_knowledge"}" -Destination "{KNOW}"'),
        "verify": ('G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 '
                   'G:\\study_agent\\tools\\c0090_probe.py'),
    }

    (dest / "其他学科库指纹.json").write_text(
        json.dumps(subs, ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # 给 verify 脚本一个稳定入口：备份根下固定名 latest.txt
    (BACKUP_ROOT / "c0090_latest.txt").write_text(str(dest), encoding="utf-8")
    print(f"\nMANIFEST = {dest / 'MANIFEST.json'}")
    print(f"回滚命令 = {manifest['rollback']['db']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
