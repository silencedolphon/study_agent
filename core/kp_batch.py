# -*- coding: utf-8 -*-
# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-10-f7fba510   (project study_agent; keep this line)
"""批量整理知识点（卡 C-0038）——**多份课件并发**跑 `core/kp_build.py` 那条管线。

人要求：在「知识点」页签能一次勾选多份课件整理知识点，并且**多管线同时跑**以提高效率。

## 为什么不复用 `core/progress.py`

`core/progress.py` 是**入库**用的全局单例（一个进程一份状态），它的注释里写得很清楚：
「同一时刻只有一个入库任务在跑，所以一个全局字典就够」。批量整理恰好打破这个前提——
N 份课件同时在跑，谁写谁覆盖，进度条会来回跳。

所以这个模块自带一套 **per-task 进度**：每份任务一个状态字典（`_STATE["tasks"]`），
`snapshot()` 把它们聚合成「总体 N/M + 每份状态」，**一个字段都不写进 `core/progress.py`**，
也就不可能影响正在跑的入库进度。

## 并发安全

- **线程池**：`concurrent.futures.ThreadPoolExecutor(max_workers)`，上限 `KP_BATCH_MAX_WORKERS=4`。
  整理是**网络等待型**（每条一次模型调用，单条约 10~30 秒），线程池足够；
  没必要上多进程，那还要处理进程间状态同步。
- **真库只读**：每份任务各自在 `kp_build.read_chunks()` 里**新建只读连接**、读完即关，
  没有长事务、没有写库（详见 `kp_build` 模块头部）。
- **各写各的产物**：`logs/knowledge/<doc_id>-末级知识点总结.json`，文件名带 doc_id，
  N 份并发不会互相覆盖；落盘走 `kp_build._save()` 的「先写 .tmp 再 replace」。
- **失败隔离**：每份任务在**自己的线程里**跑，异常在这一份内被捕获记账，
  其余各份照跑（`_run_one` 的 try/except）。
- **成本保护**：每份有自己的 `max_cost` 硬上限；批任务另有**合计**上限
  `total_max_cost`（到了就把还没开始的份取消掉，正在跑的那份靠自己的上限收尾）。

## 接口形状（供后续与 C-0037 的批量执行器统一）

    从 core import kp_batch
    kp_batch.plan([7, 6])                       # 预估（每份 + 合计），不调模型
    kp_batch.start([7, 6], max_workers=2)       # 起调度线程，立刻返回
    kp_batch.snapshot()                         # 聚合进度（总体 + 每份）
    kp_batch.is_running() / kp_batch.cancel()   # 状态与取消

⚠ C-0037（批量入库 + 并发执行器）在本卡施工时**尚未交付**（全项目当时只有
`core/chat.py` 里一处 `ThreadPoolExecutor(max_workers=1)`），所以这里是自实现的。
接口刻意压到最小：`plan/start/snapshot/is_running/cancel` 五个函数，
将来要与入库执行器合并时，替换的是内部实现，不是这份契约。
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import kp_build

# ---------------------------------------------------------------- 常量
KP_BATCH_MAX_WORKERS = 4        # 并发上限：再多也不会更快（瓶颈是模型的响应时间）
KP_BATCH_DEFAULT_WORKERS = 2    # 默认保守值：2 条并发实测稳定，不触发限流
KP_BATCH_CONFIRM_CNY = 2.0      # 预估合计超过这个数就要人二次确认（前端弹确认框）
# 阶段名直接沿用管线自己的四个，前端进度条与单份那条**同一套**渲染
BATCH_STAGES = tuple(kp_build.KP_STAGES)

# 每份任务的状态机
ST_WAITING = "waiting"
ST_RUNNING = "running"
ST_DONE = "done"
ST_FAILED = "failed"
ST_SKIPPED = "skipped"
ST_CANCELLED = "cancelled"

_LOCK = threading.RLock()
_STATE: dict = {
    "active": False,
    "status": "",            # "" / done / failed / cancelled
    "error": "",
    "started_at": 0.0,
    "finished_at": 0.0,
    "elapsed": 0.0,
    "max_workers": KP_BATCH_DEFAULT_WORKERS,
    "est_cost_cny": 0.0,
    "total_max_cost": 0.0,
    "cost_cny": 0.0,
    "cancel": False,
    "skipped": [],           # 被"跳过已完成"规则跳过的 doc_id
    # [重建] 原 `_STATE` 里应有 label：`start()` 用 `_STATE.update({... "label": ...})`
    # 写它，`snapshot()` 要读它（进度条上那行"批量整理知识点：N 份 · 并发 M"）。
    # 缺这个键时 update 能成功、但 reset() 之后的首次读取会 KeyError。
    "label": "",
    "tasks": [],
    # 卡 C-0123：停滞看门狗是否已经强制收尾（一旦为真，`_scheduler` 的收尾
    # 不许再把 status 改回 "done" —— 那会把"卡死"报成"完成"）。
    "stall_forced": False,
}

# 重建注（卡 C-0117）：`LOG_TAIL` 随文件一起丢失，下面是**字节码真值** ——
# `kp_batch.pyc` 模块级 STORE_NAME 第 90 行 `LOG_TAIL = 40`。
# 它是每份任务在进度里保留的日志行数上限（`_log_locked` 超了就丢最旧的）。
LOG_TAIL = 40

# ---------------------------------------------------------------- 停滞兜底（卡 C-0123）
#
# 现场：用户 8756 看板 2026-10-15 08:42 起的批量整理，4 个模型调用（TCP 连到
# api.deepseek.com）**16 分钟一个字节都没回**，而这份进度快照一直报
# `active=true / n_running=2 / elapsed=1078`，两份停在 kp_done=14 与 5
# —— 界面上就是"知识点整理卡住不动"，且 `is_running()` 永远挡住后续整理。
#
# `core/kp_build.py` 那一层已经补了硬墙（单次调用上限 + 判停线，见那里的
# "挂起兜底"小节）。这里再加**最后一道**：整批**完全无进展**超过判停线时，
# 强制把还在跑的那几份判失败、把 `active` 放下来，让界面能复位、人能重跑
# （产物里已完成的条数一条不丢，重跑就是续跑）。
#
# ⚠ 判据是"**无进展**"而不是"总时长"：慢任务（大课件几十条）每次完成一条都会
# 改变指纹，永远不会误伤。默认 **1800 秒（30 分钟）**——开工时实测到一段
# "15 分 14 秒零进展、随后自己活过来"的真实过程（doc#2 于 08:58:10 跑完 20/20），
# 所以这条线必须明显长于 15 分钟，否则会把"网关排队慢"误判成"卡死"。
BATCH_STALL_TIMEOUT_ENV = "KP_BATCH_STALL_TIMEOUT"


def batch_stall_timeout() -> float:
    """整批无进展的判停线（秒）；0 = 关掉这道兜底。"""
    raw = os.environ.get(BATCH_STALL_TIMEOUT_ENV)
    if raw is None or str(raw).strip() == "":
        return 1800.0
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return 1800.0
    return v if v > 0 else 0.0


def _out_name(path) -> str:
    """产物文件名（**永不抛异常**）。

    `Path("")` 在 Windows 上等于 `Path(".")`；它的 `.name` 取出来是空串（不炸），
    但 `with_suffix()` / `resolve()` 一类会抛
    `ValueError: WindowsPath('.') has an empty name`。卡 C-0120 的报错 B 就是从
    "空串进了 `Path()`"开始的，所以这里对空值一律返回空串：宁可让上游看到
    "没有文件名"，也不让异常在几十次模型调用**之后**才爆出来。
    """
    p = str(path or "").strip()
    if not p or p in (".", ".."):
        return ""
    try:
        return Path(p).name
    except (ValueError, OSError):   # pragma: no cover - 防御分支
        return ""


def done_state(est: dict, prev: dict) -> tuple[bool, str]:
    """这份课件的产物算不算「已完成」——(是否完成, 说明)。

    ⚠ [重建] 函数名与参数顺序以**真值清单**为准：字节码里是 `done_state(est, prev)`
    （`_work/strings/kp_batch.funcs.txt` L94, args=2, vars: est, prev, p_total, n_leaf），
    由 `plan()` 调用。此处原先误命名为 `_should_skip(prev, est)`，已改正。
    另注：上一行那句"每份任务的日志尾部"的注释与本节无关（拼接断裂残留），请忽略。

    「跳过已完成」的判据要严一点，否则会**误跳过**（少花钱但白干）：
      1. 产物存在，且是本版管线（`meta.pipeline_version`）产出的；
      2. **页数一致**：产物是按 N 页生成的，课件现在还是 N 页（重入库后页数会变）；
      3. 末级节点**全覆盖**：`n_done_pages >= n_leaf_pages`。
    三条都满足才跳过；任何一条不满足都照跑（本版产物可断点续跑，不会重复花钱）。
    """
    if not prev.get("exists"):
        return False, "还没有产物"
    if not prev.get("is_current_version"):
        return False, "产物不是本版管线（%s）产出的" % (prev.get("pipeline_version") or "未知版本")
    # ⚠ 比的是**末级节点数**，不是 outline 的总页数：产物里的 `meta.pages_total`
    # 就是生成时的末级页数（`kp_build._meta` 的 `kp_total = len(leaf_pages)`）。
    # 拿总页数比会永远判"没完成"——有父节点页的课件（如 doc#67：19 页 / 11 个末级）
    # 就会每次都重跑一遍，白花钱。
    p_total = int(prev.get("pages_total") or 0)
    n_leaf = int(est.get("n_leaf_pages") or 0)
    if p_total != n_leaf:
        return False, ("产物是按 %d 个末级节点生成的，现在这份是 %d 个"
                       % (p_total, n_leaf))
    if int(prev.get("n_done_pages") or 0) < n_leaf:
        return False, "只覆盖 %d/%d 个末级节点" % (prev.get("n_done_pages") or 0, n_leaf)
    return True, "已完成 %d 条（覆盖 %d 页）" % (int(prev.get("n_kps") or 0),
                                                int(prev.get("n_done_pages") or 0))


def plan(doc_ids, *, skip_done: bool = True, force: bool = False) -> dict:
    """批量预估：每份的条数/成本 + 合计 + 跳过判定。**不调模型**。

    每份的预估口径与单份完全一样（都来自 `kp_build.estimate()`，是**上界**：
    按每页一条算；合并掉的页不会单独调用模型，真实花费只会更少）。
    """
    items = []
    run_items = []
    skip_items = []
    est_sum = 0.0
    for raw in (doc_ids or []):
        try:
            did = int(raw)
        except (TypeError, ValueError):
            continue
        if did <= 0:
            continue
        try:
            est = kp_build.estimate(did)
        except Exception as e:  # noqa: BLE001
            items.append({"doc_id": did, "ok": False, "skip": True,
                          "error": "%s: %s" % (type(e).__name__, e)})
            continue
        if not est.get("ok"):
            items.append({"doc_id": did, "ok": False, "skip": True,
                          "error": est.get("error") or "不可整理"})
            continue
        # [重建] 恢复版在此处**直接跳空**（L139 `continue` → L144 `task["percent"]`，
        # 函数体被截断），而 `_work/strings/kp_batch.funcs.txt` 的 `plan` 变量表里
        # 明确有 `e, prev` ⇒ 原实现这里是"跑一次 done_state、按 skip_done/force 分流"。
        # 按真值目录的字段名补全（`items` / `run_items` / `skip_items` / `est_sum`
        # 都由 `plan` 的变量表证实存在）。
        try:
            prev = kp_build.prev_info(did)
        except Exception as e:  # noqa: BLE001
            prev = {"exists": False, "error": "%s: %s" % (type(e).__name__, e)}
        done, why = done_state(est, prev)
        # [C-0120 修复] 这一组字段**必须**在这里补齐 —— `_blank_task()` 逐个读它们
        # （`item["n_leaf_pages"]` / `item["n_pages"]` / `item["kp_min"]` /
        # `item["kp_max"]` / `item["est_cost_cny"]` / `item["out_path"]` /
        # `item["out_name"]`）。缺了**不会报错**，只会静默退化成 0 与空串，
        # 而空串进 `Path()` 就是 `Path('.')`：
        #     `_run_one()` 的 `out = Path(task["out_path"])`
        #   → `kp_build.build(out_path=".")` → `_save()` 的
        #     `out.with_suffix(out.suffix + ".tmp")`
        #   → `ValueError: WindowsPath('.') has an empty name`
        # 崩点在**最后落盘**那一刻（C-0120 实测 traceback：build L5221 →
        # build_v2 L5005 → persist L4950 → _save L1403），也就是模型调用
        # **已经全部跑完**才失败：钱花了、产物没落地。这正是看板「批量整理
        # 知识点」0/1 失败、每行显示「undefined 页 / 末级 undefined」的根因。
        # 字段名取自 v1.1 打包字节码真值（`_work/strings/kp_batch.strings.txt`
        # 的 `[plan]` 段含 out_path/out_name/est_rule/n_pages/n_leaf_pages/
        # kp_min/kp_max/est_calls；`[_blank_task]` 段含 out_path/out_name/
        # kp_total/cost_cny），与 `kp_build.estimate()` 的返回键同名。
        out_path = str(est.get("out_path") or "").strip()
        if not out_path or out_path in (".", ".."):
            # 兜底：宁可用管线自己的默认产物路径，也不让空名路径往下走。
            out_path = str(kp_build.out_file(did))
        entry = {"doc_id": did, "ok": True, "skip": bool(done and skip_done and not force),
                 "calls": est.get("est_calls"), "pages": est.get("n_leaf_pages"),
                 "est_cost_cny": est.get("est_cost_cny"),
                 "done": done, "why": why,
                 "title": est.get("title") or prev.get("title") or ("文档 %d" % did),
                 "n_pages": int(est.get("n_pages") or 0),
                 "n_leaf_pages": int(est.get("n_leaf_pages") or 0),
                 "kp_min": int(est.get("kp_min") or 0),
                 "kp_max": int(est.get("kp_max") or 0),
                 "est_calls": int(est.get("est_calls") or 0),
                 "out_path": out_path,
                 "out_name": _out_name(out_path),
                 "est_rule": str(est.get("est_rule") or "")}
        items.append(entry)
        if entry["skip"]:
            skip_items.append(did)
        else:
            run_items.append(entry)
            try:
                est_sum += float(est.get("est_cost_cny") or 0.0)
            except (TypeError, ValueError):
                pass

    # [重建] `plan()` 的 return 在恢复版里整个丢了（函数体在循环内就断掉，
    # 直接接上下一个 def）。而 `start()` 里 `p = plan(...)` 之后立刻用
    # `p["items"] / p["n_skip"] / p["est_cost_cny"]` ⇒ 不补就是 TypeError。
    # 字段名由 `start()` 的用法与 `plan` 的变量表（items/run_items/skip_items/
    # est_sum）共同确定。
    return {
        "items": items,
        "n_total": len(items),
        "n_run": len(run_items),
        "n_skip": len(skip_items),
        "skipped": list(skip_items),
        "run": list(run_items),
        "est_cost_cny": round(est_sum, 6),
        "over_confirm": bool(est_sum > KP_BATCH_CONFIRM_CNY),
        "confirm_limit_cny": KP_BATCH_CONFIRM_CNY,
    }


def start(doc_ids, *, max_workers: int = KP_BATCH_DEFAULT_WORKERS,
          skip_done: bool = True, force: bool = False,
          per_doc_max_cost: float = 0.8, total_max_cost: float = 0.0,
          confirm: bool = False) -> dict:
    """起一个批任务：N 份课件按 `max_workers` 并发跑。**立刻返回**，进度看 `snapshot()`。

    - 一次只允许一个批任务（`is_running()`）；调度线程 daemon，进程退出不留孤儿。
    - `skip_done=True` 时先按 `done_state()` 过滤掉已完成的份（不花钱）。
    - 预估合计 > `KP_BATCH_CONFIRM_CNY` 时必须 `confirm=True`（人二次确认），
      否则直接拒绝启动——这是**花钱的刹车**，不靠前端自觉。
    """
    with _LOCK:
        if _STATE["active"]:
            return {"started": False, "error": "已有一个批量整理任务在跑（同一时间只允许一个）"}
    try:
        workers = int(max_workers)
    except (TypeError, ValueError):
        workers = KP_BATCH_DEFAULT_WORKERS
    workers = max(1, min(workers, KP_BATCH_MAX_WORKERS))

    p = plan(doc_ids, skip_done=skip_done, force=force)
    run_items = [x for x in p["items"] if x.get("ok") and not x.get("skip")]
    bad_items = [x for x in p["items"] if not x.get("ok")]
    if not run_items:
        return {"started": False,
                "error": ("没有要跑的课件：%d 份已在跳过名单里，%d 份不可整理"
                          % (p["n_skip"], len(bad_items))),
                "skipped": p["skipped"], "plan": p}
    if p["est_cost_cny"] > KP_BATCH_CONFIRM_CNY and not confirm:
        return {"started": False, "need_confirm": True, "plan": p,
                "error": ("预估合计 ¥%.4f 超过 ¥%.2f：需要二次确认（confirm=true）"
                          % (p["est_cost_cny"], KP_BATCH_CONFIRM_CNY))}

    tasks = [_blank_task(x) for x in run_items]
    with _LOCK:
        _STATE.update({
            "active": True, "status": "", "error": "",
            "started_at": time.time(), "finished_at": 0.0, "elapsed": 0.0,
            "max_workers": workers, "est_cost_cny": float(p["est_cost_cny"]),
            "total_max_cost": float(total_max_cost or 0.0), "cost_cny": 0.0,
            "cancel": False, "skipped": list(p["skipped"]),
            "tasks": tasks, "stall_forced": False,
            "label": "批量整理知识点：%d 份 · 并发 %d" % (len(tasks), workers),
        })

    def _watchdog() -> None:
        """卡 C-0123 兜底：整批**完全无进展**超过判停线就强制收尾。

        指纹 = 每份的 (status, stage_index, kp_done, cost_cny, 日志行数)：
        正常任务每完成一条都会变，所以慢任务不会被误伤；只有"一个字节都不动"
        （模型接口挂起，用户 2026-10-15 08:42 那次）才会命中。

        收尾动作：还在跑的份判 `failed` 并写明原因；`active` 放下来 ⇒
        看板进度条复位、`is_running()` 不再挡住后续整理；**产物里已完成的条数
        一条不丢**（那是每条一落盘的），重跑即续跑。
        """
        limit = batch_stall_timeout()
        if limit <= 0:
            return
        last_sig = None
        last_t = time.time()
        while True:
            time.sleep(1.0)
            with _LOCK:
                if not _STATE["active"] or _STATE.get("stall_forced"):
                    return
                sig = tuple((t.get("doc_id"), t.get("status"), t.get("stage_index"),
                             t.get("kp_done"), t.get("cost_cny"),
                             len(t.get("log") or []))
                            for t in _STATE["tasks"])
                alive = any(t.get("status") == ST_RUNNING for t in _STATE["tasks"])
            if sig != last_sig:
                last_sig, last_t = sig, time.time()
                continue
            if not alive or (time.time() - last_t) < limit:
                continue
            now = time.time()
            with _LOCK:
                if not _STATE["active"] or _STATE.get("stall_forced"):
                    return
                msg = ("★停滞：整批 %.0f 秒没有任何进展（模型调用挂起，连接活着但不回包）；"
                       "已强制收尾 —— 正在跑的份判失败，产物里已完成的条数不丢，"
                       "直接重跑即续跑（卡 C-0123 兜底）" % limit)
                for t in _STATE["tasks"]:
                    if t.get("status") == ST_RUNNING:
                        _log_locked(t, msg)
                        t.update(status=ST_FAILED, error=msg, finished_at=now,
                                 status_note="停滞（模型接口挂起）")
                        t["elapsed"] = round(now - (t.get("started_at") or now), 1)
                        t["percent"] = _task_percent(t)
                _STATE.update({
                    "active": False, "status": "failed", "error": msg,
                    "finished_at": now, "stall_forced": True,
                    "elapsed": round(now - (_STATE["started_at"] or now), 1),
                    "cost_cny": round(sum(float(t.get("cost_cny") or 0.0)
                                          for t in _STATE["tasks"]), 6),
                })
            return

    def _scheduler() -> None:
        try:
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="kp-batch") as pool:
                list(pool.map(
                    lambda t: _run_one(t, force=force,
                                       per_doc_max_cost=per_doc_max_cost),
                    tasks))
        except Exception as e:  # noqa: BLE001
            with _LOCK:
                _STATE["error"] = "%s: %s" % (type(e).__name__, e)
        finally:
            with _LOCK:
                n = len(_STATE["tasks"])
                n_fail = sum(1 for t in _STATE["tasks"] if t["status"] == ST_FAILED)
                n_cancel = sum(1 for t in _STATE["tasks"] if t["status"] == ST_CANCELLED)
                _STATE["active"] = False
                # 卡 C-0123：看门狗已经按"停滞"定过案（active/finished_at/error
                # 都写好了），这里**不许**再把 status 改回 "done" —— 那等于把
                # 一次卡死报成"完成"。此时只补一句"调度器终于退出来了"。
                if _STATE.get("stall_forced"):
                    _log_stall_return()
                    return
                _STATE["finished_at"] = time.time()
                _STATE["elapsed"] = round(_STATE["finished_at"] - _STATE["started_at"], 1)
                _STATE["cost_cny"] = round(sum(float(t.get("cost_cny") or 0.0)
                                               for t in _STATE["tasks"]), 6)
                if _STATE["cancel"] and n_cancel:
                    _STATE["status"] = "cancelled"
                elif n_fail:
                    _STATE["status"] = "failed"
                else:
                    _STATE["status"] = "done"

    def _log_stall_return() -> None:
        """看门狗已定案后，迟到的调度器线程只留一行痕（**调用方必须已持锁**）。"""
        for t in _STATE["tasks"]:
            if t.get("status") == ST_FAILED and "停滞" in str(t.get("status_note") or ""):
                _log_locked(t, "（调度器此后才退出：它的线程池一直在等挂起的调用）")
                return

    th = threading.Thread(target=_scheduler, name="kp-batch-sched", daemon=True)
    th.start()
    # 卡 C-0123：停滞看门狗（整批无进展 → 强制收尾，见 `_watchdog` 的说明）。
    threading.Thread(target=_watchdog, name="kp-batch-watchdog", daemon=True).start()
    return {
        "started": True,
        "n_run": len(tasks),
        "n_skip": p["n_skip"],
        "skipped": p["skipped"],
        "max_workers": workers,
        "est_cost_cny": p["est_cost_cny"],
        "total_max_cost": float(total_max_cost or 0.0),
        "doc_ids": [t["doc_id"] for t in tasks],
        "progress": snapshot(),
        "hint": ("完成后去「资料」页 → 课件思维导图 → 点节点看总结；"
                 "产物在 logs/knowledge/<doc_id>-末级知识点总结.json"),
    }


def cancel() -> dict:
    """取消：**还没开始**的份直接作废；正在跑的那份让它跑完当前调用后收尾。

    不硬杀线程——那一份的产物已经逐条落盘，硬杀只会留下"跑了半条"的错觉；
    真正的钱是每次模型调用，杀线程也退不回来。
    """
    with _LOCK:
        if not _STATE["active"]:
            return {"cancelled": False, "error": "当前没有批量任务在跑"}
        _STATE["cancel"] = True
        n = sum(1 for t in _STATE["tasks"] if t["status"] == ST_WAITING)
        return {"cancelled": True, "pending": n,
                "note": "正在跑的份会跑完当前这一条，未开始的 %d 份已作废" % n}


def reset() -> None:
    """清空状态（测试用；不在跑的时候才允许）。"""
    with _LOCK:
        if _STATE["active"]:
            return
        _STATE.update({
            "active": False, "status": "", "error": "", "started_at": 0.0,
            "finished_at": 0.0, "elapsed": 0.0,
            "max_workers": KP_BATCH_DEFAULT_WORKERS, "est_cost_cny": 0.0,
            "total_max_cost": 0.0, "cost_cny": 0.0, "cancel": False,
            "skipped": [], "tasks": [], "label": "", "stall_forced": False,
        })


def is_running() -> bool:
    """有没有批任务在跑（看板用它禁用"开始"按钮、并决定是否轮询进度）。"""
    with _LOCK:
        return bool(_STATE.get("active"))


# ================================================================ 内部辅助
# 重建注（卡 C-0117）：下面这一组（以及后面的 `_run_one`）原本随文件丢失 ——
# 恢复版与第一轮反编译都没有它们，`start()` 一调 `_blank_task`/`_run_one` 就
# `NameError`（批量整理知识点整条链路不可用）。本次用 **v1.1 打包字节码真值**
# `StudyDashboard.exe_extracted\PYZ.pyz_extracted\core\kp_batch.pyc` 补齐：
# 函数名、参数名、局部变量名、常量、控制流与字节码逐条对齐，
# 并由 `diagnostics/parity_check.py` 把原版字节码**真的执行起来**做对拍。


def _blank_task(item: dict) -> dict:
    """造一份任务的初始状态字典（字段与 `snapshot()` 的逐份输出对齐）。

    [C-0120] `out_path` 在这里就**不许**是空名值：空串进 `Path()` 会退化成
    `Path('.')`，一路带到 `kp_build._save()` 的 `with_suffix()` 才炸
    （见 `plan()` 的注释）。缺字段/空值时回退到管线自己的默认产物路径
    （`kp_build.out_file()`），与单份那条路的产物位置完全一致。
    """
    doc_id = int(item["doc_id"])
    out_path = str(item.get("out_path") or "").strip()
    if not out_path or out_path in (".", ".."):
        out_path = str(kp_build.out_file(doc_id))
    return {
        "doc_id": doc_id,
        "title": item.get("title") or "",
        "status": ST_WAITING,
        "stage": "",
        "stage_index": 0,
        "note": "",
        "kp_done": 0,
        "kp_total": int(item.get("n_leaf_pages") or 0),
        "n_leaf_pages": int(item.get("n_leaf_pages") or 0),
        "n_pages": int(item.get("n_pages") or 0),
        "est_kp_min": int(item.get("kp_min") or 0),
        "est_kp_max": int(item.get("kp_max") or 0),
        "est_cost_cny": float(item.get("est_cost_cny") or 0.0),
        "cost_cny": 0.0,
        "out_path": out_path,
        "out_name": str(item.get("out_name") or "") or _out_name(out_path),
        "backup": "",
        "error": "",
        "status_note": "",
        "log": [],
        "started_at": 0.0,
        "finished_at": 0.0,
        "elapsed": 0.0,
        "percent": 0,
        "kp_actual": 0,
        "pages_covered": 0,
    }


def _task_of(doc_id: int):
    """按 doc_id 取任务（同一批里 doc_id 唯一）。"""
    for t in _STATE["tasks"]:
        if int(t["doc_id"]) == int(doc_id):
            return t
    return None


def _apply_event(task: dict, ev: dict) -> None:
    """把管线回调的事件写进**这一份**任务的状态（不碰别的任务）。"""
    if not isinstance(ev, dict):
        return
    kind = ev.get("ev")
    with _LOCK:
        if kind == "stage":
            task["stage"] = str(ev.get("stage") or "")
            task["stage_index"] = int(ev.get("stage_index") or 0)
            if ev.get("note"):
                task["note"] = str(ev["note"])
        elif kind == "tick":
            task["kp_done"] = int(ev.get("kp_done") or 0)
            if ev.get("kp_total"):
                task["kp_total"] = int(ev["kp_total"])
            task["cost_cny"] = round(float(ev.get("cost_cny") or 0.0), 6)
            if ev.get("note"):
                task["note"] = str(ev["note"])
            task["percent"] = _task_percent(task)
        elif kind == "begin":
            # 重建注：`kp_total` 的三级回退 —— 事件里有就用事件的，否则沿用本份
            # 已有的（`_blank_task` 从预估里带的），都没有才算 0。
            task["kp_total"] = int(ev.get("kp_total") or task["kp_total"] or 0)
            task["kp_done"] = int(ev.get("kp_done") or 0)
        elif kind == "log":
            _log_locked(task, str(ev.get("msg") or ""))
    # 重建注：合计花费的累加在**锁外**做（`_bump_global` 自己再加锁），
    # 免得同一把 RLock 里嵌套进出。
    if kind == "tick":
        _bump_global(task)


def _task_percent(task: dict) -> int:
    """份内完成度：阶段序号 + 阶段内完成度（口径与 `kp_build.progress_snapshot` 一致）。"""
    if task["status"] in (ST_DONE, ST_SKIPPED):
        return 100
    if task["status"] in (ST_FAILED, ST_CANCELLED):
        return int(task.get("percent") or 0)
    n = max(1, len(BATCH_STAGES))
    tot = int(task.get("kp_total") or 0)
    done = int(task.get("kp_done") or 0)
    frac = (done / tot) if tot else 0.0
    frac = min(1.0, max(0.0, frac))
    idx = min(int(task.get("stage_index") or 0), n - 1)
    return int((idx + frac) * 100 / n)


def _log_locked(task: dict, msg: str) -> None:
    """往这一份的日志尾巴里追加一行（**调用方必须已持锁**）。"""
    if not msg:
        return
    line = time.strftime("%H:%M:%S ") + msg
    buf = task.setdefault("log", [])
    buf.append(line)
    if len(buf) > LOG_TAIL:
        del buf[:len(buf) - LOG_TAIL]


def _log(task: dict, msg: str) -> None:
    """加锁版的 `_log_locked`（`kp_build.build` 的 `log=` 回调）。"""
    with _LOCK:
        _log_locked(task, msg)


def _bump_global(task: dict) -> None:
    """合计花费 = 各份之和；超过合计上限就把**还没开始的**份取消掉。"""
    with _LOCK:
        _STATE["cost_cny"] = round(sum(t["cost_cny"] for t in _STATE["tasks"]), 6)
        cap = float(_STATE.get("total_max_cost") or 0.0)
        if cap and _STATE["cost_cny"] >= cap and not _STATE["cancel"]:
            _STATE["cancel"] = True
            _STATE["error"] = ("合计花费 CNY%.4f 已达上限 CNY%.2f：未开始的任务已取消"
                               % (_STATE["cost_cny"], cap))


def _task_out(task: dict) -> dict:
    """一份任务的对外快照：**整体透出**，只把内部的 `log` 改名成 `log_tail`。

    [C-0120 修复] 这里原来是**手写的字段白名单**，把 `_blank_task()` 明明记着的
    `n_pages` / `n_leaf_pages` / `kp_total` / `kp_done` / `kp_actual` /
    `pages_covered` / `elapsed` / `status_note` 全丢了 ⇒ 前端 `kpbTaskRow()`
    读 `t.n_pages` / `t.n_leaf_pages` 拿到 undefined，界面显示
    「undefined 页 / 末级 undefined」。

    真值（v1.1 打包字节码）不是白名单：`snapshot` 的局部变量是
    `t, k, v, d`（`_work/strings/kp_batch.funcs.txt::L313`），常量里 `log_tail`
    与 `log` 成对出现（`kp_batch.strings.txt` 的 `[snapshot]` 段）—— 原实现就是
    "逐键拷贝 + `log` → `log_tail`"。按真值改回来：白名单会静默丢字段，而
    "静默丢字段"正是这一整张卡的病根。
    """
    d = {}
    for k, v in task.items():
        d["log_tail" if k == "log" else k] = v
    return d


def snapshot() -> dict:
    """聚合进度快照：总体（N/M、cost、elapsed）+ 每份任务的状态。

    [重建] 原函数体随文件末尾一起丢失。这里按 `_STATE` 既有的字段与
    `start()` 写入的内容补全，并**一律用 `.get` 防御**——快照函数本身绝不能
    因为某个键缺失而抛异常（它是轮询接口，一抛就是白屏）。
    注：`_blank_task` / `_task_percent` / `_bump_global` / `_run_one` 四个
    内部函数在恢复版里也丢了，见文末 TODO。
    """
    with _LOCK:
        tasks = list(_STATE.get("tasks") or [])
        n = len(tasks)
        done = sum(1 for t in tasks if t.get("status") == ST_DONE)
        failed = sum(1 for t in tasks if t.get("status") == ST_FAILED)
        cancelled = sum(1 for t in tasks if t.get("status") == ST_CANCELLED)
        waiting = sum(1 for t in tasks if t.get("status") == ST_WAITING)
        running = sum(1 for t in tasks if t.get("status") == ST_RUNNING)
        started = float(_STATE.get("started_at") or 0.0)
        elapsed = float(_STATE.get("elapsed") or 0.0)
        if _STATE.get("active") and started:
            elapsed = round(time.time() - started, 1)
        return {
            "kind": "kp_batch",
            "active": bool(_STATE.get("active")),
            "status": _STATE.get("status") or "",
            "error": _STATE.get("error") or "",
            "label": _STATE.get("label") or (
                "批量整理知识点：%d 份 · 并发 %d"
                % (n, int(_STATE.get("max_workers") or KP_BATCH_DEFAULT_WORKERS))),
            "stages": list(BATCH_STAGES),
            # [C-0120] 真值 `[snapshot]` 段有 `stage_total` 常量（阶段总数），
            # 补齐；`stages` 是本项目既有的对外口径，保留不动。
            "stage_total": len(BATCH_STAGES),
            "n_total": n,
            "n_done": done,
            "n_failed": failed,
            "n_cancelled": cancelled,
            "n_waiting": waiting,
            "n_running": running,
            "percent": int(round(100.0 * (done + failed + cancelled) / n)) if n else 0,
            "cost_cny": round(float(_STATE.get("cost_cny") or 0.0), 6),
            "est_cost_cny": round(float(_STATE.get("est_cost_cny") or 0.0), 6),
            "total_max_cost": float(_STATE.get("total_max_cost") or 0.0),
            "cancel": bool(_STATE.get("cancel")),
            "elapsed": elapsed,
            "skipped": list(_STATE.get("skipped") or []),
            "max_workers": int(_STATE.get("max_workers") or KP_BATCH_DEFAULT_WORKERS),
            # 每份任务的进度（看板逐行渲染）。整体透出 —— 见 `_task_out()`：
            # 白名单会静默丢掉 `n_pages`/`n_leaf_pages`，前端就成了
            # 「undefined 页 / 末级 undefined」。
            "tasks": [_task_out(t) for t in tasks],
        }


# ================================================================ 单份执行
# 重建注（卡 C-0117）：原本这里只留了一段「TODO（重建未完）」的说明，函数体全丢。
# 现已按字节码真值补齐：`_blank_task` / `_task_percent` / `_bump_global` 等在
# 上面「内部辅助」区，`_run_one` 在下面。补齐依据不再需要了，但留一句给后来人 ——
# **别再靠"读调用点猜语义"**：`kp_batch.pyc`（v1.1 打包字节码）里有完整的
# 函数名、局部变量名、常量与行号，照着它还原本最快也最准。


def _run_one(task: dict, force: bool, per_doc_max_cost: float) -> None:
    """跑一份课件。**这份里的任何异常都不许影响别的份**（失败隔离）。"""
    doc_id = int(task["doc_id"])
    with _LOCK:
        # 重建注：调度器可能在这份**还没开始**时就把整批取消了（合计花费到顶）。
        # 这里再挡一道，免得取消之后还去花钱。
        if _STATE["cancel"]:
            task.update(status=ST_CANCELLED,
                        error="批次已取消（尚未开始）",
                        finished_at=time.time(),
                        status_note="已取消")
            return
        task.update(status=ST_RUNNING,
                    stage=BATCH_STAGES[0],
                    stage_index=0,
                    note="读取 logs/outline 与真库块",
                    started_at=time.time())

    # [C-0120] 空名路径兜底：`Path("")` 在 Windows 上就是 `Path('.')`，它的
    # `with_suffix()` 抛 `ValueError: WindowsPath('.') has an empty name`，
    # 而那个调用点在 `kp_build._save()` —— **模型调用全部跑完之后**才炸。
    # 不能等它炸：没有文件名的值一律换回管线默认产物路径。
    out_path = str(task.get("out_path") or "").strip()
    if not out_path or out_path in (".", ".."):
        out_path = str(kp_build.out_file(doc_id))
        task["out_path"] = out_path
        task["out_name"] = _out_name(out_path)
    out = Path(out_path)
    backup = ""

    try:
        prev = kp_build.prev_info(doc_id)
        # 重建注：要不要先备份旧产物，取决于"会不会覆盖它"——
        # force 必然覆盖；非 force 但旧产物**不能续跑**时也会从头重写，同样要备份。
        # `resumable` 为真则是在原产物上续跑，不能动它。
        if prev["exists"] and (force or not prev["resumable"]):
            bak = out.with_name(out.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(out, bak)
            backup = str(bak)
    except OSError as e:
        with _LOCK:
            task.update(status=ST_FAILED,
                        error="备份旧产物失败，为不覆盖它已中止：%s" % e,
                        finished_at=time.time(),
                        status_note="失败")
        return

    with _LOCK:
        task["backup"] = backup

    def on_event(ev: dict) -> None:
        _apply_event(task, ev)

    try:
        rep = kp_build.build(doc_id, out_path=str(out), max_cost=per_doc_max_cost,
                             force=force, progress_on=False, on_event=on_event,
                             log=lambda m: _log(task, m))
    except Exception as e:                                   # noqa: BLE001
        # 重建注：这一份炸了只记账，不让异常穿出去 —— 线程池里其它份还在跑，
        # `start()._scheduler` 靠 `snapshot()` 的状态收尾。
        with _LOCK:
            _log_locked(task, f"★异常：{type(e).__name__}: {e}")
            task.update(status=ST_FAILED,
                        error=f"{type(e).__name__}: {e}",
                        finished_at=time.time(),
                        status_note="失败")
            task["elapsed"] = round(
                time.time() - (task["started_at"] or time.time()), 1)
            task["percent"] = _task_percent(task)
        return

    meta = (rep or {}).get("meta") or {}
    status = str((rep or {}).get("status") or "")
    err = str((rep or {}).get("error") or "")
    with _LOCK:
        task["cost_cny"] = round(
            float(meta.get("cost_cny_all_calls") or task.get("cost_cny") or 0.0), 6)
        task["kp_actual"] = int(meta.get("kp_actual") or 0)
        task["pages_covered"] = len(meta.get("pages_covered") or [])
        # 重建注：真实产出优先；管线没报数时才沿用过程中的 tick 计数。
        task["kp_done"] = task["kp_actual"] or task["kp_done"]
        task["finished_at"] = time.time()
        task["elapsed"] = round(
            task["finished_at"] - (task["started_at"] or time.time()), 1)
        # 重建注：三种收尾都算"跑完了"，但语义不同 ——
        #   · rep 不 ok            → 失败（把管线给的 error 原样透出）
        #   · status == "failed"   → 管线中止（同样算失败）
        #   · status == "stopped"  → 到花费上限，**算完成但可续跑**（不是错误）
        if not (rep or {}).get("ok"):
            task.update(status=ST_FAILED,
                        error=err or "整理失败",
                        status_note="失败")
        elif status == "failed":
            task.update(status=ST_FAILED,
                        error=err or "管线中止",
                        status_note="失败")
        elif status == "stopped":
            task.update(status=ST_DONE,
                        status_note="已到花费上限，剩余页可续跑")
        else:
            task.update(status=ST_DONE,
                        status_note="完成")
        task["percent"] = _task_percent(task)
    _bump_global(task)