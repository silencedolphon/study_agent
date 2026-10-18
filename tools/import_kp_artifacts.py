#!/usr/bin/env python
# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
"""整理产物 → 知识点表 一键导入 · 命令行壳（M0-3 / M0-6b）。

逻辑内核在 `core/kp_import.py`（纯函数 `run()`，供 dashboard 直接调用）；
本文件只负责：解析命令行 → 调 `core.kp_import.run()` → 打印摘要 / 决定退出码。
参数、默认值、输出与退出码与重构前**完全一致**。

## 这个工具解决什么问题

`core/kp_build.py` 的「整理知识点」只写
`logs/knowledge/<doc_id>-末级知识点总结.json`，从不写数据库；于是「整理完看不见、
复习队列永远空」。本工具把产物离线解析进 `knowledge_points` + `kp_sources`：
零 AI、零网络、幂等（重复导入不重复建卡）。

## 用法

```powershell
# 默认 dry-run：只预览，不写库（sqlite 只读连接，连 WAL 都不动）
venv\\Scripts\\python.exe -X utf8 tools\\import_kp_artifacts.py

# 真写库（默认 config.DB_PATH；测试一律用副本）
venv\\Scripts\\python.exe -X utf8 tools\\import_kp_artifacts.py --apply

# 指定副本库 / 产物目录 / 只导一份 / 机器可读输出
venv\\Scripts\\python.exe -X utf8 tools\\import_kp_artifacts.py --apply ^
    --db _work\\m0\\import\\real-copy.db --artifacts-dir logs\\knowledge --doc-id 41 --json
```

退出码：0 = 跑完（单份产物坏只记 `failed_files` 并继续；单条知识点失败也继续）；
2 = 前置不满足（库不存在 / 产物目录不存在 / 缺表 / 参数不合法）。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

# tools/ 下脚本统一从项目根 import core（与 tools/study.py 同一条路）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, kp_import  # noqa: E402

PY = "venv\\Scripts\\python.exe"


def _human(stats: dict) -> str:
    """人读摘要。逻辑内核只返回 dict，展示格式留在这里（与重构前逐字一致）。"""
    is_apply = stats["mode"] == "apply"
    mode = "apply（真写库）" if is_apply else "dry-run（预览，不写库）"
    created = stats["kps_created"] if is_apply else stats["kps_would_create"]
    verb = "已创建" if is_apply else "将创建"
    src_done = stats["kp_sources_added"] if is_apply else stats["kp_sources_would_add"]
    lines = [
        f"整理产物 → 知识点表导入 · {mode}",
        f"  数据库  : {stats['db']}",
        f"  产物目录: {stats['artifacts_dir']}",
        f"  扫描 {stats['scanned_files']} 个产物文件，解析成功 {stats['parsed_files']}，失败 {len(stats['failed_files'])}",
        f"  命中库内文档 {stats['docs_matched']} 份；待导入知识点 {stats['kps_found']} 条",
        f"  {verb} {created}；已存在跳过 {stats['kps_existing']}；名称冲突跳过 {stats['kps_name_conflict']}；无效 {stats['kps_invalid']}",
        f"  出处 kp_sources {'已写入' if is_apply else '将写入'} {src_done} 条；无出处知识点 {stats['kps_without_sources']} 条",
        f"  名称避让 {stats['renamed']} 条；耗时 {stats['elapsed_ms']} ms",
    ]
    if stats["docs_missing_in_db"]:
        lines.append(f"  产物对应文档已不在库（跳过）: {stats['docs_missing_in_db']}")
    for f in stats["failed_files"]:
        lines.append(f"  ✗ {f['file']}: {f['error']}")
    if stats["name_conflicts_sample"]:
        lines.append("  名称冲突样例: " + "、".join(
            f"{x['name']}(doc{x['doc_id']})" for x in stats["name_conflicts_sample"]))
    for f in stats["record_failures"][:10]:
        lines.append(f"  ✗ {f['key']}: {f['error']}")
    if not is_apply:
        lines.append("（预览不写库；确认无误后加 --apply）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把 logs/knowledge 整理产物导入 knowledge_points + kp_sources（零 AI、幂等）")
    ap.add_argument("--apply", action="store_true",
                    help="真写库；不带此参数 = dry-run 只预览（默认）")
    ap.add_argument("--dry-run", action="store_true", help="显式指定预览（默认行为）")
    ap.add_argument("--db", default=str(config.DB_PATH),
                    help=f"目标 SQLite 库（默认 {config.DB_PATH}；测试请用副本）")
    ap.add_argument("--artifacts-dir", default=str(Path(config.LOG_DIR) / "knowledge"),
                    help="整理产物目录（默认 logs/knowledge）")
    ap.add_argument("--doc-id", default="",
                    help="只导入指定 doc_id（可逗号分隔多个，如 41,42）")
    ap.add_argument("--max-sources-per-kp", type=int, default=kp_import.DEFAULT_MAX_SOURCES,
                    help=f"单条知识点最多挂几条出处（默认 {kp_import.DEFAULT_MAX_SOURCES}）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON 摘要")
    args = ap.parse_args(argv)

    # 参数校验的错误文案与重构前保持一致（退出码 2）
    try:
        doc_ids = [int(x) for x in
                   re.split(r"[,，\s]+", str(args.doc_id or "")) if x.strip()]
    except ValueError:
        print("--doc-id 需要整数（可逗号分隔）", file=sys.stderr)
        return 2
    if args.apply and args.dry_run:
        print("--apply 与 --dry-run 不能同时使用", file=sys.stderr)
        return 2

    try:
        stats = kp_import.run(
            db_path=args.db,
            artifacts_dir=args.artifacts_dir,
            doc_id=doc_ids,
            apply=bool(args.apply),
            max_sources_per_kp=args.max_sources_per_kp,
        )
    except kp_import.KpImportError as e:      # 前置条件不满足 → 退出码 2
        print(f"错误: {e}", file=sys.stderr)
        return 2
    except sqlite3.Error as e:                # 数据库损坏等 → 退出码 2
        print(f"数据库错误: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(_human(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
