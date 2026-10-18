# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-16-8ce9fa0e   (project study_agent; keep this line)
"""入库进度：一个极简的**进程内**状态，供看板轮询显示进度条。

为什么不做成通用的任务队列：看板是**单进程 + 锁内串行**执行入库的
（见 `tools/dashboard.py::api_ingest`），同一时刻只有一个入库任务在跑，
所以一个全局字典就够——不需要任务 id、不需要多任务隔离、不需要持久化。

命令行不需要它：那边本来就是同步打印，进度直接看得见。
这里只是给"点一下按钮然后盯着网页"的场景补上反馈。

用法（`core/ingest.py` 已经接好，一般不用直接调）::

    progress.begin("2镇静催眠药.pptx")
    progress.stage("识别图片")
    progress.tick(3, total=26)         # 第 3/26 页
    progress.end()                     # 出错时 progress.end("failed", error=...)
"""
from __future__ import annotations

import threading
import time

# 入库的五个阶段，按先后顺序。进度百分比按"阶段序号 + 阶段内完成度"算，
# 不做时间预估——识图每页 25 秒、嵌入是纯 CPU、复用缓存时又快得没影，
# 按时间估出来的 ETA 只会骗人。
STAGES = ("提取文字", "识别图片", "分析结构", "切块嵌入", "写入库")

_lock = threading.Lock()
_state: dict = {
    "active": False,      # 是否有入库任务在跑
    "label": "",          # 正在处理哪个文件
    "stage": "",          # 当前阶段名
    "stage_index": 0,     # 当前阶段序号（0 起）
    "done": 0,            # 阶段内已完成
    "total": 0,           # 阶段内总数（0 = 该阶段没有细分进度）
    "note": "",           # 阶段内的补充说明（如"复用 12 页"）
    "status": "",         # running / done / failed
    "error": "",
    "started_at": 0.0,
    "elapsed": 0.0,
    "files_done": 0,      # 批量入库时已完成几个文件
    "files_total": 0,
}


def begin(label: str, files_total: int = 1) -> None:
    """开始处理一个文件。"""
    with _lock:
        _state.update({
            "active": True, "label": label, "stage": STAGES[0],
            "stage_index": 0, "done": 0, "total": 0, "note": "",
            "status": "running", "error": "",
            "started_at": time.time(), "elapsed": 0.0,
            "files_done": 0, "files_total": max(1, int(files_total)),
        })


def stage(name: str, note: str = "") -> None:
    """切到某个阶段（阶段内进度归零）。"""
    with _lock:
        idx = STAGES.index(name) if name in STAGES else _state["stage_index"]
        _state.update({"stage": name, "stage_index": idx,
                       "done": 0, "total": 0, "note": note})


def tick(done: int, total: int = 0, note: str = "") -> None:
    """报告阶段内的进度（如识图跑到第几页）。"""
    with _lock:
        _state["done"] = int(done)
        if total:
            _state["total"] = int(total)
        if note:
            _state["note"] = note


def end(status: str = "done", error: str = "") -> None:
    """结束（成功或失败都要调，否则看板会一直显示"进行中"）。"""
    with _lock:
        _state.update({
            "active": False, "status": status, "error": error,
            "stage": "", "note": "",
            "elapsed": max(0.0, time.time() - (_state["started_at"] or time.time())),
        })


def file_done(n: int) -> None:
    """批量入库：第 n 个文件处理完了。"""
    with _lock:
        _state["files_done"] = int(n)


def snapshot() -> dict:
    """当前进度快照（给看板轮询用），带算好的百分比。"""
    with _lock:
        s = dict(_state)
    # 百分比 = 已完成的阶段数 + 当前阶段内的完成度。
    # 阶段内没有细分进度（total=0）时按"刚开始这一阶段"算，
    # 宁可显示得保守一点，也不要跳到 99% 然后卡住不动。
    frac = (s["done"] / s["total"]) if s["total"] else 0.0
    frac = min(1.0, max(0.0, frac))
    n = len(STAGES)
    s["percent"] = int(((s["stage_index"] + frac) * 100) / n) if s["active"] else (
        100 if s["status"] == "done" else 0)
    if s["active"]:
        s["elapsed"] = max(0.0, time.time() - (s["started_at"] or time.time()))
    s["stages"] = list(STAGES)
    return s


def reset() -> None:
    """清空（测试用）。"""
    with _lock:
        _state.update({
            "active": False, "label": "", "stage": "", "stage_index": 0,
            "done": 0, "total": 0, "note": "", "status": "", "error": "",
            "started_at": 0.0, "elapsed": 0.0,
            "files_done": 0, "files_total": 0,
        })