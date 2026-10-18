"""数据备份：把用户数据快照到 `_recovery/data-backup-<时间戳>/`。

背景：项目 2026-10-14 经历过硬盘物理损坏 + 数据无法恢复的教训；在做任何大改动
（重入库、迁移、升级）之前，先跑一次本脚本。

默认备份内容：
- 主库 `index/study.db`（config.DB_PATH）
- 全部学科库 `subjects/<slug>/index/study.db`
- 笔记目录 `notes/`（用户手写内容，最不可再生）
- 学科注册表 `logs/subjects.json`

用 sqlite3 的 backup API 而不是直接复制文件：看板可能正开着数据库，
直接 copy 可能拿到写了一半的页面（WAL 模式尤其危险）。

用法::

    venv\\Scripts\\python.exe -X utf8 tools\\backup_data.py
    venv\\Scripts\\python.exe -X utf8 tools\\backup_data.py --out D:\\backups\\study_agent
    venv\\Scripts\\python.exe -X utf8 tools\\backup_data.py --keep 10   # 只保留最近 10 份
    venv\\Scripts\\python.exe -X utf8 tools\\backup_data.py --json

退出码：0 = 全部成功；1 = 有失败。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402

STAMP = time.strftime("%Y%m%d-%H%M%S")


def _db_targets() -> list[tuple[str, Path]]:
    """(标签, 源库路径) —— 主库 + 全部学科库。"""
    out: list[tuple[str, Path]] = []
    main_db = Path(config.DB_PATH)
    if main_db.exists():
        out.append(("index__study.db", main_db))
    sub_root = ROOT / "subjects"
    if sub_root.is_dir():
        for p in sorted(sub_root.glob("*/index/study.db")):
            rel = p.relative_to(ROOT).as_posix().replace("/", "__")
            out.append((rel, p))
    return out


def backup_db(src: Path, dst: Path) -> None:
    src_conn = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def verify_db(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def prune(out_root: Path, keep: int) -> list[str]:
    """只保留最近 keep 份备份目录（按名字里的时间戳排序）。"""
    if keep <= 0:
        return []
    dirs = sorted([p for p in out_root.glob("data-backup-*") if p.is_dir()])
    removed = []
    for p in dirs[:-keep]:
        shutil.rmtree(p)
        removed.append(p.name)
    return removed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="study_agent 用户数据备份")
    ap.add_argument("--out", default="", help="备份根目录，默认 _recovery/")
    ap.add_argument("--keep", type=int, default=0, help="只保留最近 N 份（0=不清理）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    out_root = Path(args.out) if args.out else ROOT / "_recovery"
    dest = out_root / f"data-backup-{STAMP}"
    dest.mkdir(parents=True, exist_ok=True)

    report: dict = {"dest": str(dest), "databases": [], "files": [],
                    "removed": [], "failed": [], "bytes": 0}

    for label, src in _db_targets():
        target = dest / label
        try:
            backup_db(src, target)
            integ = verify_db(target)
            size = target.stat().st_size
            report["databases"].append({"label": label, "src": str(src),
                                        "size": size, "integrity": integ})
            report["bytes"] += size
            print(f"[OK  ] DB  {label}  {size / 1048576:.1f} MB  integrity={integ}")
        except Exception as e:  # noqa: BLE001
            report["failed"].append(f"{label}: {type(e).__name__}: {e}")
            print(f"[FAIL] DB  {label}  {type(e).__name__}: {e}")

    # 笔记（最不可再生）
    notes_src = Path(config.NOTES_DIR)
    if notes_src.is_dir():
        try:
            shutil.copytree(notes_src, dest / "notes", dirs_exist_ok=True)
            n = len(list((dest / "notes").glob("*")))
            size = sum(p.stat().st_size for p in (dest / "notes").glob("*") if p.is_file())
            report["files"].append({"label": "notes/", "n": n, "size": size})
            report["bytes"] += size
            print(f"[OK  ] 笔记 {n} 个文件  {size / 1048576:.2f} MB")
        except Exception as e:  # noqa: BLE001
            report["failed"].append(f"notes: {e}")
            print(f"[FAIL] 笔记 {e}")

    # 学科注册表
    reg = ROOT / "logs" / "subjects.json"
    if reg.exists():
        try:
            shutil.copy2(reg, dest / "subjects.json")
            report["files"].append({"label": "subjects.json", "size": reg.stat().st_size})
            report["bytes"] += reg.stat().st_size
            print(f"[OK  ] 学科注册表 subjects.json")
        except Exception as e:  # noqa: BLE001
            report["failed"].append(f"subjects.json: {e}")

    if args.keep:
        report["removed"] = prune(out_root, args.keep)
        for name in report["removed"]:
            print(f"[INFO] 清理旧备份 {name}")

    print(f"\n备份目录: {dest}")
    print(f"总大小: {report['bytes'] / 1048576:.1f} MB，失败 {len(report['failed'])} 项")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = not report["failed"]
    print("RESULT: PASS" if ok else "RESULT: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
