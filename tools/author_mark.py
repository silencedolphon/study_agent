# -*- coding: utf-8 -*-
"""作者标记工具（卡：源码交付前打标）。

用途：给核心源码文件打上**版权头 + 唯一 File-ID**，用于
  ① 明示「这是有主的专有代码」以劝阻未授权发布；
  ② 留下**只有著作权人知道含义**的指纹（File-ID 清单另存，不随源码交付），
     日后若他人删掉版权头照抄，仍可用清单比对。

⚠ **本工具是著作权人侧的工具，不要随源码交付包一起给别人**（它解释了指纹的含义）。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\author_mark.py            # dry-run，只打印将插入的内容
    venv\\Scripts\\python.exe -X utf8 tools\\author_mark.py --apply    # 真写
    venv\\Scripts\\python.exe -X utf8 tools\\author_mark.py --apply --fingerprints out.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

AUTHOR = "潜声海豚"
EMAIL = "2227663973@qq.com"
YEAR = "2026"
PROJECT = "study_agent"

# 核心产品文件（人拍板：只标核心，不标历史验证/探针脚本）
TARGETS = [
    "core/__init__.py", "core/chat.py", "core/chunk.py", "core/config.py",
    "core/db.py", "core/embed.py", "core/extract.py", "core/figures.py",
    "core/ingest.py", "core/kp_batch.py", "core/kp_build.py", "core/layout.py",
    "core/multippt.py", "core/outline.py", "core/pipeline_pool.py",
    "core/progress.py", "core/search.py", "core/study.py", "core/subjects.py",
    "core/tables.py",
    "tools/dashboard.py",
    "README.md",
    "看板.cmd",
]

MARKER_KEY = "File-ID:"


def sha8(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


def body_of(text: str, n_head_lines: int | None = None) -> str:
    """去掉本工具已插入的头部后的正文（用于算指纹，保证重复跑幂等）。"""
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and ("Copyright (c) %s" % YEAR) in lines[i]:
        i += 1
    while i < len(lines) and (
        lines[i].startswith("# ") or lines[i].startswith("REM ")
        or lines[i].strip().startswith("<!--")
    ):
        i += 1
    return "".join(lines[i:])


def build_block(path: Path, text: str, idx: int) -> tuple:
    """按文件类型生成标记块；返回 (块文本, file_id, 正文哈希)。"""
    body = body_of(text)
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    fid = "SA-%02d-%s" % (idx, sha8(body.encode("utf-8")))
    suffix = path.suffix.lower()

    if suffix == ".py":
        block = (
            "# Copyright (c) {y} {a} <{e}>. All rights reserved.\n"
            "# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；\n"
            "# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。\n"
            "# Unauthorized copying, distribution or publication is strictly prohibited.\n"
            "# {k} {f}   (project {p}; keep this line)\n"
        ).format(y=YEAR, a=AUTHOR, e=EMAIL, k=MARKER_KEY, f=fid, p=PROJECT)
    elif suffix in (".cmd", ".bat"):
        # ⚠ cmd.exe 用系统 OEM 代码页（本机 GBK）解析 .cmd —— 这个文件实测 0 个非 ASCII 字节，
        # 所以版权头一律 ASCII，署名用邮箱（中文名只出现在 NOTICE / README 里）。
        block = (
            "REM Copyright (c) {y} <{e}>. All rights reserved.\n"
            "REM Proprietary - authorized testing only. No copying, distribution\n"
            "REM or publication without written permission.\n"
            "REM {k} {f}   (project {p}; keep this line)\n"
        ).format(y=YEAR, e=EMAIL, k=MARKER_KEY, f=fid, p=PROJECT)
    elif suffix in (".md", ".markdown"):
        block = (
            "<!-- Copyright (c) {y} {a} <{e}>. All rights reserved. -->\n"
            "<!-- 本源码为著作权人私有作品：仅供经书面授权的测试使用；未经许可，\n"
            "     不得复制、分发、公开、发布或用于任何商业用途。 -->\n"
            "<!-- {k} {f}   (project {p}; keep this line) -->\n"
        ).format(y=YEAR, a=AUTHOR, e=EMAIL, k=MARKER_KEY, f=fid, p=PROJECT)
    else:
        raise SystemExit("不支持的文件类型：%s" % path)
    # ⚠ 行尾符必须**跟随原文件**。本工具第一版用 Path.write_text() 写回，
    # 而它在本机（Windows）默认把 \n 转成 \r\n —— 结果 23 个文件全成了"整文件重写"
    # （git diff 13000+ 行）。项目原文件是**纯 LF**，这里显式保持。
    nl = "\r\n" if "\r\n" in text else "\n"
    if nl != "\n":
        block = block.replace("\n", nl)
    return block, fid, body_hash


def insert_pos(text: str) -> int:
    """插入位置：shebang / 编码声明 / 已有 docstring 之前（注释不影响 __doc__）。"""
    lines = text.splitlines(keepends=True)
    i = 0
    if i < len(lines) and lines[i].startswith("#!"):
        i += 1
    if i < len(lines) and "coding" in lines[i] and lines[i].lstrip().startswith("#"):
        i += 1
    return sum(len(x) for x in lines[:i])


def main() -> int:
    ap = argparse.ArgumentParser(description="给核心源码打版权头 + File-ID")
    ap.add_argument("--apply", action="store_true", help="真写（默认只 dry-run）")
    ap.add_argument("--fingerprints", default="", help="指纹清单 JSON 输出路径")
    args = ap.parse_args()

    print("著作权人：%s <%s>　年份：%s　文件数：%d"
          % (AUTHOR, EMAIL, YEAR, len(TARGETS)))
    print("模式：%s\n" % ("APPLY（真写）" if args.apply else "DRY-RUN（不写）"))

    prints = []
    changed = skipped = 0
    for idx, rel in enumerate(TARGETS, start=1):
        p = ROOT / rel
        if not p.exists():
            print("  [跳过] %s（不存在）" % rel)
            continue
        with p.open("r", encoding="utf-8", newline="") as f:
            text = f.read()
        if MARKER_KEY in text.split("\n\n")[0][:600]:
            print("  [已标记] %s" % rel)
            skipped += 1
            continue
        block, fid, body_hash = build_block(p, text, idx)
        pos = insert_pos(text)
        new_text = text[:pos] + block + text[pos:]
        prints.append({"path": rel, "file_id": fid, "body_sha256": body_hash,
                       "marked_sha256": hashlib.sha256(new_text.encode("utf-8")).hexdigest(),
                       "bytes_before": len(text.encode("utf-8")),
                       "bytes_after": len(new_text.encode("utf-8"))})
        print("  [标记] %-24s %s  (+%d 字节)"
              % (rel, fid, len(new_text.encode("utf-8")) - len(text.encode("utf-8"))))
        if args.apply:
            with p.open("w", encoding="utf-8", newline="") as f:
                f.write(new_text)
            changed += 1

    print("\n共 %d 个待标记、%d 个已标记%s" % (len(prints), skipped,
                                          "、" if args.apply else "（dry-run 未写盘）"))
    if args.apply:
        print("实际写入 %d 个文件" % changed)
    if args.fingerprints:
        out = Path(args.fingerprints)
        out.write_text(json.dumps(
            {"author": AUTHOR, "email": EMAIL, "year": YEAR, "project": PROJECT,
             "note": "File-ID 清单：仅著作权人持有，勿随源码交付",
             "files": prints}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("指纹清单已写：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
