"""Inspect or apply a local visual-cache repair; default is read-only."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.visual_repair import repair_database  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="恢复被误标记的本地图示描述，不调用远程模型")
    parser.add_argument("--db", required=True)
    parser.add_argument("--doc-id", required=True, action="append", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", help="实际写入前的独立 SQLite 快照；--apply 时必填")
    args = parser.parse_args()
    try:
        result = repair_database(args.db, args.doc_id, apply=args.apply, backup_path=args.backup)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
