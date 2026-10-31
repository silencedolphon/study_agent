#!/usr/bin/env python3
"""只读基准：章级材料快速路径 vs 旧路径（A/B，零模型调用）。

用法（在项目根目录）：
  venv\\Scripts\\python.exe -X utf8 tools\\bench_pipeline.py --doc 50 --chapters 1,6,20,39 --runs 5

两个回退开关（默认均走快速路径）：
  PREVIEW_CHAPTER_FAST=0      preview.read_chapter_ch 回退整本读取
  TEXTBOOK_KP_FAST_PLAN=0     textbook_kp._plan 回退整表扫描

本脚本只读 SQLite（mode=ro）与已有产物，不写真实库/notes/logs，不调用模型。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import kp_build, preview, textbook_kp  # noqa: E402


def bench(fn, n: int, warm: int = 1) -> dict:
    for _ in range(warm):
        fn()
    xs = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t) * 1000)
    return {"n": n, "median_ms": round(statistics.median(xs), 2),
            "min_ms": round(min(xs), 2), "max_ms": round(max(xs), 2)}


def collect(doc: int, chapters: list[int], n: int) -> dict:
    return {
        "locate_chapter": {str(c): bench(lambda c=c: preview.locate_chapter(doc, c), n)
                           for c in chapters},
        "read_chapter_ch": {str(c): bench(lambda c=c: preview.read_chapter_ch(doc, c), n)
                            for c in chapters},
        "estimate_preview": {str(c): bench(lambda c=c: preview.estimate_preview(doc, c), n)
                             for c in chapters},
        "textbook_plan": {str(c): bench(lambda c=c: textbook_kp._plan(doc, c), n)
                          for c in chapters},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", type=int, default=50)
    ap.add_argument("--chapters", default="1,6,20,39")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    chapters = [int(x) for x in args.chapters.split(",") if x.strip()]

    env_old = {"PREVIEW_CHAPTER_FAST": "0", "TEXTBOOK_KP_FAST_PLAN": "0"}
    saved = {k: os.environ.get(k) for k in env_old}
    try:
        for k, v in env_old.items():
            os.environ[k] = v
        old = collect(args.doc, chapters, args.runs)
        for k, v in env_old.items():
            os.environ[k] = "1"
        new = collect(args.doc, chapters, args.runs)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    out = {"doc": args.doc, "chapters": chapters, "runs": args.runs,
           "old": old, "new": new}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
