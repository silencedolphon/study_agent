# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-15-7cc306f6   (project study_agent; keep this line)
"""批量入库的**并发执行器**：多份课件同时跑入库管线。

## 为什么需要它

看板原来的「入库全部待处理文件」是**串行**的：一次一份、跑完再跑下一份。
而入库里最慢的两段——整页识图（逐页调视觉模型，实测约 25 秒/页）与嵌入
（纯 CPU）——对同一份资料是**先后**关系，对多份资料却是**互相独立**的。
十几份课件串行跑就是几小时，并发之后这段时间按并发数缩短。

## 三条硬约束（本模块的全部设计都围绕它们）

1. **写库要安全**。SQLite 允许"多读者 + 单写者"，多线程同时写会拿到
   `database is locked`。所以：
   - 每个任务**自己 `db.connect()` 新建连接**，绝不共享 sqlite3 连接
     （sqlite3 的连接对象默认不允许跨线程使用，共享还会互相踩事务）；
   - 连接上开 WAL + `busy_timeout`，写者排队等锁而不是立刻报错；
   - `isolation_level="IMMEDIATE"`：让隐式事务以 `BEGIN IMMEDIATE` 开始。
     这一点不是可有可无——WAL 下的 **deferred** 事务是"先读后写"，若读完之后
     别的连接提交过写，升级写锁会直接返回 `SQLITE_BUSY`（`SQLITE_BUSY_SNAPSHOT`），
     而且 **busy_timeout 对它不生效**，重试也没用。提前拿写锁从根上避开这个坑。
   - 建表/迁移（`init_db`）只在主控线程做一次，不在并发任务里各做一遍。

2. **进度要分离**。`core/progress.py` 是**全局单例快照**（看板单份入库的进度
   就靠它），多份并行会互相覆盖：A 的阶段名被 B 冲掉、百分比在两份之间跳。
   本模块的方案是 **不动机器，只换接线**：
   - 用一把 `threading.local` 的"任务上下文"把进度调用**按线程**分流：
     线程绑定了任务 → 写进该任务自己的记录；没绑定 → 原样转发给
     `core/progress`。
   - 于是单份入库 / 「入库全部待处理文件」的进度显示**一个字节都没变**
     （它们的线程没有任务上下文，走的是原来的全局单例）；
     而并发批量期间，全局快照保持"未激活"，不会被批量任务污染。
   - `core/progress.py` **没有被修改**（本卡硬约束：不改 core 既有文件行为）。

3. **失败要隔离、并发要有上限**。单个任务抛异常只标记该任务失败，
   不影响其它任务；并发数默认保守（2），可 1–4，可用环境变量覆盖。
   识图要花钱且可能触发限流，一上来开 8 是拿钱和时间换故障。

## 谁在用

`tools/dashboard.py` 的 `/api/ingest/batch*` 三个接口。命令行不需要它
（`tools/reimport_all.py` 仍是串行、可续跑的形态，本卡不动它的契约）。

## 取消（卡 C-0040）

`cancel(batch_id=None) -> dict`：未开始（pending）的份当场作废且不再启动，
正在跑的份**不硬杀**、跑完当前这一份文件后自然收尾。幂等，取消不存在的批次
也不抛异常。看板侧还没接按钮（那是下一张卡），本模块只负责把接口做出来。
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import time
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, db, extract
from . import ingest as ingest_mod
from . import progress as _global_progress

# ---- 并发上限 ----
#
# 默认 2 是**保守值**，不是性能最优值：入库会逐页调视觉模型，并发越高越容易
# 撞上服务端的速率限制；而限流一旦发生，重试与退避反而比串行更慢。
# 上限 4 是因为再往上收益已经被两件事吃掉：
#   - 嵌入是 CPU 密集（onnxruntime 内部本来就吃满多核），并发只会互相抢核；
#   - 每个任务的连接都要排队拿 SQLite 写锁，写者越多等得越久。
DEFAULT_WORKERS = 2
MIN_WORKERS = 1
MAX_WORKERS = 4
_WORKERS_ENV = "KB_INGEST_WORKERS"

# 写锁等待上限（毫秒）。**不再自己写一个数**，直接引用 `core/db.py` 的常量：
# 两边各写一个 30 秒，改了一处忘了另一处就会出现"批量连接等 30 秒、
# 单份连接等 5 秒"这种口径分叉（卡 C-0040 把它收成一处）。
#
# 它等的是"别的连接持有写锁"这件事。**它挡不住的是"自己持锁太久"**：
# 撞锁方最多等 30 秒，持锁方只要超过 30 秒，对方必然报 `database is locked`。
# 所以真正的修复是让写事务变短（见 `core/figures.py` 的逐页提交），
# 而不是把这个数往上调——调大只会让并发入库整体变慢，治不了根。
BUSY_TIMEOUT_MS = db.BUSY_TIMEOUT_MS

# 识图单页成本口径（人民币）。取自本库历史实测：
#   8 页 ¥0.1656（≈0.0207/页）、78 页 ¥1.3345（≈0.0171/页）。
# 预估算的是**上界**（按每页都要调模型算，命中缓存的页不会产生费用），
# 所以取偏大的 0.02。
EST_COST_PER_PAGE_CNY = 0.02
# 全局结构分析（core/outline.py）每份课件的实测成本（46 页约 ¥0.01）。
EST_COST_PER_OUTLINE_CNY = 0.01


def default_workers() -> int:
    """默认并发数：环境变量 `KB_INGEST_WORKERS` 优先，非法值退回 2。"""
    try:
        return clamp_workers(int(os.environ.get(_WORKERS_ENV, DEFAULT_WORKERS)))
    except (TypeError, ValueError):
        return DEFAULT_WORKERS


def clamp_workers(n) -> int:
    """把并发数夹到 1..MAX_WORKERS，非法值退回默认。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return DEFAULT_WORKERS
    return max(MIN_WORKERS, min(MAX_WORKERS, n))


# ---------------------------------------------------------------- 进度分流
#
# 这是"进度分离"的全部实现：一个线程局部变量 + 一个透明代理。
# 代理装在 `core` 包的 `progress` 名字上（`core/ingest.py`、`core/figures.py`
# 都是 `from . import progress` 或函数内延迟导入，装的正是这个位置），
# 所以入库管线里的每一次 `progress.stage(...)` / `progress.tick(...)`
# 都会先经过这里。

_local = threading.local()


def _current_task() -> dict | None:
    return getattr(_local, "task", None)


class _ProgressRouter:
    """把进度调用分流到"当前线程的任务"，没有任务就转发给全局单例。

    没有定义的方法/属性一律**委托给 `core.progress` 真模块**（`__getattr__`），
    所以它对调用方是透明的：`STAGES`、`snapshot()`、`reset()` 全都还是原来的
    行为，拿到的东西还是原来那个全局快照。
    """

    def __getattr__(self, name):
        # 只在类/实例上都找不到时才会走到这里——即"我没接管的名字"。
        return getattr(_global_progress, name)

    # ---- 接管的五个动作 ----
    def begin(self, label: str, files_total: int = 1) -> None:
        task = _current_task()
        if task is None:
            return _global_progress.begin(label, files_total=files_total)
        task.update({
            "status": "running", "label": label, "stage": _global_progress.STAGES[0],
            "stage_index": 0, "done": 0, "total": 0, "note": "",
            "percent": 0, "error": "",
            "started_at": task.get("started_at") or time.time(),
        })

    def stage(self, name: str, note: str = "") -> None:
        task = _current_task()
        if task is None:
            return _global_progress.stage(name, note)
        stages = _global_progress.STAGES
        # 不在五个标准阶段里（如"切分多页幻灯片"）时沿用上一个阶段序号——
        # 与 `core/progress.py::stage()` 的处理完全一致。
        idx = stages.index(name) if name in stages else task.get("stage_index", 0)
        task.update({"stage": name, "stage_index": idx, "done": 0, "total": 0,
                     "note": note, "percent": _percent(idx, 0, 0)})

    def tick(self, done: int, total: int = 0, note: str = "") -> None:
        task = _current_task()
        if task is None:
            return _global_progress.tick(done, total=total, note=note)
        task["done"] = int(done)
        if total:
            task["total"] = int(total)
        if note:
            task["note"] = note
        task["percent"] = _percent(task.get("stage_index", 0),
                                   task.get("done", 0), task.get("total", 0))

    def end(self, status: str = "done", error: str = "") -> None:
        task = _current_task()
        if task is None:
            return _global_progress.end(status, error)
        task.update({
            "status": "failed" if status == "failed" else "done",
            "error": error or task.get("error", ""),
            "stage": "", "note": "",
            "ended_at": time.time(),
            "percent": 100 if status != "failed" else task.get("percent", 0),
        })

    def file_done(self, n: int) -> None:
        task = _current_task()
        if task is None:
            return _global_progress.file_done(n)
        task["files_done"] = int(n)


_ROUTER = _ProgressRouter()
_install_lock = threading.Lock()
_installed = False


def install() -> bool:
    """把分流代理接到 `core` 包的 `progress` 名字上（幂等，只生效一次）。

    为什么连包属性一起换：`core/figures.py` 里是**函数内**延迟导入
    （`from . import progress as _pg`），只替换 `core.ingest.progress`
    接不住它的逐页 tick，识图阶段的进度就只能停在阶段起点。
    """
    global _installed
    with _install_lock:
        if _installed:
            return False
        import sys
        pkg = sys.modules.get("core")
        if pkg is not None:
            pkg.progress = _ROUTER
        ingest_mod.progress = _ROUTER
        _installed = True
        return True


def is_installed() -> bool:
    return _installed


def _percent(stage_index: int, done: int, total: int) -> int:
    """百分比 = 已完成阶段 + 当前阶段内完成度（与 `core/progress.py` 同口径）。"""
    frac = (done / total) if total else 0.0
    frac = min(1.0, max(0.0, frac))
    n = len(_global_progress.STAGES)
    return int(((stage_index + frac) * 100) / n)


# ---------------------------------------------------------------- 连接


def task_connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """给一个并发任务建**独立**连接，并把并发写库需要的 PRAGMA 都设好。

    `db.connect(..., immediate=True)` 负责两件事（卡 C-0040 收口到那一处）：
    - `PRAGMA busy_timeout`：写者排队等锁而不是立刻报错；
    - `isolation_level="IMMEDIATE"`：让隐式事务以 `BEGIN IMMEDIATE` 开始，
      避开 WAL 下 deferred 事务升级写锁时那个 busy_timeout 管不了的
      `SQLITE_BUSY_SNAPSHOT`。

    ⚠ **提前拿写锁同时也意味着"提前开始持锁"**：`isolation_level="IMMEDIATE"`
    之后，只要有一条写语句没 commit，写锁就一直在这个连接手里。
    所以凡是写库的代码都必须"写完就提交"，不能跨着慢操作（识图、模型调用）
    养着一个未决事务——那正是卡 C-0040 修的故障。
    """
    conn = db.connect(db_path, immediate=True)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() != "wal":
            # 库级设置，只有还不是 WAL 时才改，避免并发下反复切换 journal 模式。
            conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass  # 拿不到就算了：连接本身能用，写竞争交给 busy_timeout
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ---------------------------------------------------------------- 预估


def _quick_pages(path: Path, kind: str) -> int:
    """快速数页数（只读元信息，不解压正文、不调模型）。

    - pdf 走 pymupdf 的 page_count（几十毫秒）
    - pptx 直接数 zip 里的 `ppt/slides/slideN.xml`（零解析开销）
    - 其它类型没有"页"的概念，返回 0（它们也不会走识图）
    """
    try:
        if kind == "pdf":
            import pymupdf
            with pymupdf.open(str(path)) as d:
                return int(d.page_count)
        if kind == "pptx":
            with zipfile.ZipFile(path) as z:
                return sum(1 for n in z.namelist()
                           if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
    except Exception:  # noqa: BLE001
        return 0
    return 0


def _resolve(rel_or_path: str | Path) -> Path:
    """把"相对 raw/ 的路径"或绝对路径统一成绝对路径（与看板同一口径）。"""
    p = Path(str(rel_or_path))
    if p.is_absolute():
        return p
    return Path(config.RAW_DIR) / str(rel_or_path).replace("/", os.sep)


def plan_files(paths: list[str], *, force: bool = False,
               with_figures: bool | None = None,
               workers: int | None = None,
               conn: sqlite3.Connection | None = None) -> dict:
    """批量入库的**预估**：待处理几份、要识图几页、大概花多少钱。

    只读文件元信息与库里的登记信息，**不调模型、不解压正文**，所以可以放心
    在确认框里实时算一次。预估算的是上界（命中识图缓存的页不花钱）。
    """
    files = ingest_mod.iter_files([_resolve(p) for p in paths])
    if with_figures is None:
        with_figures = bool(getattr(config, "FIGURE_DESC", 1))

    own = conn is None
    if own:
        conn = db.connect()
    indexed: dict[str, dict] = {}
    try:
        for r in conn.execute(
                "SELECT path, size_bytes, content_hash, n_chunks FROM documents"):
            indexed[_norm_rel(r["path"])] = dict(r)
    finally:
        if own:
            conn.close()

    items: list[dict] = []
    pages_total = 0
    calls_total = 0
    skip_total = 0
    for f in files:
        kind = extract.detect_kind(f)
        # 旧版 Office（.doc/.ppt/.xls）不是"不支持"：入库时会先由本机 Office
        # 转成 OOXML 再按普通文件走（见 core/extract.py::ensure_ooxml）。
        # 只有真正不认识的格式（unknown）才算不支持。
        supported = kind != "unknown"
        try:
            st = f.stat()
        except OSError:
            continue
        rel = ingest_mod._rel(f)  # noqa: SLF001 — 与入库写库用的是同一个口径
        hit = indexed.get(_norm_rel(rel))
        will_skip = bool(hit and not force
                         and hit.get("size_bytes") == st.st_size
                         and int(hit.get("n_chunks") or 0) > 0)
        legacy_note = ""
        if kind == "legacy":
            # 转换产物与源文件同目录并存（.ppt + .pptx）。若产物已入库、
            # 且不比源文件旧，这一份就无需再跑一遍转换。
            dest = extract.legacy_dest(f)
            dest_hit = (indexed.get(_norm_rel(ingest_mod._rel(dest)))
                        if dest.is_file() else None)
            if dest_hit and not force and int(dest_hit.get("n_chunks") or 0) > 0:
                try:
                    fresh = dest.stat().st_mtime >= st.st_mtime
                except OSError:
                    fresh = False
                if fresh:
                    will_skip = True
                    legacy_note = f"已转换为 {dest.name} 并入库（源文件保留）"
            if not legacy_note:
                legacy_note = (f"旧格式：入库时自动转换为 {dest.name}"
                               f"（需本机 Office；源文件保留）")
        pages = _quick_pages(f, kind) if supported else 0
        needs_fig = bool(with_figures and supported and kind in ("pdf", "pptx"))
        calls = pages if needs_fig else 0
        cost = calls * EST_COST_PER_PAGE_CNY
        if not will_skip and supported and kind in ("pdf", "pptx"):
            cost += EST_COST_PER_OUTLINE_CNY
        items.append({
            "rel": rel, "name": f.name, "kind": kind, "supported": supported,
            "size": st.st_size, "pages": pages, "est_calls": calls,
            "est_cost_cny": round(cost, 4), "will_skip": will_skip,
            "note": legacy_note,
        })
        if supported and not will_skip:
            pages_total += pages
            calls_total += calls

    return {
        "files": items,
        "n_files": len(items),
        "n_todo": sum(1 for i in items if i["supported"] and not i["will_skip"]),
        "n_skip": sum(1 for i in items if i["will_skip"]),
        "n_unsupported": sum(1 for i in items if not i["supported"]),
        "pages_total": pages_total,
        "est_calls": calls_total,
        "est_cost_cny": round(sum(i["est_cost_cny"] for i in items), 4),
        "workers": clamp_workers(workers if workers is not None else default_workers()),
        "max_workers": MAX_WORKERS,
        "with_figures": bool(with_figures),
        "est_rule": ("预估是**上界**：按每个待处理文件的最多页数都要调一次视觉模型算；"
                     f"命中识图缓存的页不产生费用。单页口径 ¥{EST_COST_PER_PAGE_CNY}"
                     "（取自本库历史实测）。"),
    }


def _norm_rel(rel: str) -> str:
    """把文档路径统一成"相对项目根、正斜杠"的形态，便于与 rel 对齐。"""
    return str(rel).replace("\\", "/").lstrip("./")


# ---------------------------------------------------------------- 批次


_batches: dict[str, dict] = {}
_batch_lock = threading.Lock()
_active: str | None = None
_seq = 0


def _new_task(rel: str, abs_path: str) -> dict:
    return {
        "rel": rel, "abs": abs_path, "name": Path(abs_path).name,
        "status": "pending", "label": Path(abs_path).name,
        "stage": "", "stage_index": 0, "done": 0, "total": 0, "note": "",
        "percent": 0, "error": "", "pipeline_status": "",
        "started_at": 0.0, "ended_at": 0.0, "seconds": 0.0,
        "chunks": 0, "cost_cny": 0.0, "warnings": [],
    }


def is_running() -> bool:
    with _batch_lock:
        b = _batches.get(_active or "")
        return bool(b and b.get("running"))


def active_batch_id() -> str | None:
    with _batch_lock:
        b = _batches.get(_active or "")
        return _active if (b and b.get("running")) else None


def _batch_still_busy(b: dict) -> bool:
    """批次是否**还占着执行器**（卡 C-0040）。

    只看 `running` 不够：取消之后没有正在跑的份时会把 `running` 立刻置 false
    （用户视角"已经停了"），可调度线程 `_run_batch` 还在收尾。这段时间里若放行
    新批次，就会出现两个 `_run_batch` 线程、两套线程池同时写库——正是本卡要
    消灭的东西。所以"是否还能提交新批次"必须同时看 `cancelling and not drained`。
    """
    return bool(b.get("running") or (b.get("cancelling") and not b.get("drained")))


def submit_batch(paths: list[str], *, force: bool = False, with_ocr: bool = True,
                 with_figures: bool | None = None, workers: int | None = None,
                 db_path: Path | str | None = None) -> dict:
    """提交一批入库任务，**立刻返回** batch_id（真正跑在后台线程里）。

    同一时间只允许一个批次在跑：并发数已经由 `workers` 控制，再叠加批次
    就等于绕过了并发上限（识图限流与写锁排队都会失控）。
    """
    global _active, _seq
    files = ingest_mod.iter_files([_resolve(p) for p in paths])
    if not files:
        return {"error": "没有可入库的文件（路径不存在或都是临时文件）"}

    with _batch_lock:
        cur = _batches.get(_active or "")
        if cur and _batch_still_busy(cur):
            return {"error": f"已有一个批量入库在跑（{cur['id']}，"
                             f"{cur['finished']}/{cur['total']}）；请等它结束",
                    "batch_id": cur["id"], "running": True}
        _seq += 1
        bid = f"b{int(time.time())}-{_seq}"
        batch = {
            "id": bid, "created_at": time.time(), "started_at": 0.0,
            "ended_at": 0.0, "running": True,
            # 取消相关的三个字段（卡 C-0040）：
            #   cancelling —— 收到过取消请求（一旦为真不再复位）；
            #   cancelled  —— 被作废的份数（进 finished 计数，percent 因此能到 100）；
            #   drained    —— 调度线程确认已收尾，可以放行下一个批次。
            "cancelling": False, "cancelled": 0, "drained": False,
            "workers": clamp_workers(workers if workers is not None
                                     else default_workers()),
            "force": bool(force), "with_ocr": bool(with_ocr),
            "with_figures": with_figures, "db_path": str(db_path or config.DB_PATH),
            "total": len(files), "finished": 0, "succeeded": 0, "failed": 0,
            "skipped": 0, "unsupported": 0, "chunks": 0, "cost_cny": 0.0,
            "warmup_error": "",
            "tasks": [_new_task(ingest_mod._rel(f), str(f)) for f in files],  # noqa: SLF001
        }
        _batches[bid] = batch
        _active = bid
        # 只保留最近 8 个批次，避免长跑的看板进程里字典无限增长
        for old in sorted(_batches, key=lambda k: _batches[k]["created_at"])[:-8]:
            if old != _active:
                _batches.pop(old, None)

    th = threading.Thread(target=_run_batch, args=(bid,), name=f"ingest-batch-{bid}",
                          daemon=True)
    th.start()
    return {"batch_id": bid, "total": len(files), "workers": batch["workers"],
            "paths": [t["rel"] for t in batch["tasks"]]}


def _run_batch(bid: str) -> None:
    """调度线程：装好进度分流 → 预热嵌入模型 → 按并发数跑完所有任务。"""
    batch = _batches[bid]
    install()
    batch["started_at"] = time.time()
    try:
        # 嵌入模型是进程内单例（`core/embed.py::get_embedder`），并发构造会
        # 白加载两份（几百 MB 内存）。在主控线程先热一次，任务里拿到的就是同一个。
        try:
            from .embed import get_embedder
            get_embedder()
        except Exception as e:  # noqa: BLE001
            batch["warmup_error"] = f"{type(e).__name__}: {e}"
        # 建表/迁移只在主控线程做一次：并发任务里各做一遍 DDL 才是真的危险。
        try:
            c = db.connect(batch["db_path"] or None)
            try:
                db.init_db(c)
            finally:
                c.close()
        except Exception as e:  # noqa: BLE001
            batch["warmup_error"] = (batch["warmup_error"] + " | " if batch["warmup_error"]
                                     else "") + f"init_db: {type(e).__name__}: {e}"

        with ThreadPoolExecutor(max_workers=batch["workers"],
                                thread_name_prefix=f"ing-{bid}") as ex:
            futures = [ex.submit(_run_one, bid, t) for t in batch["tasks"]]
            for fu in futures:
                try:
                    fu.result()
                except Exception:  # noqa: BLE001 — 任务内部已兜底，这里只防漏网
                    pass
    finally:
        # 取消时 `running` 可能已经被 `cancel()` 提前置 false（见那里的说明），
        # 这里如实覆盖成"收尾完成"的时刻，并打下 drained 供下一个批次放行。
        # 持锁更新：`cancel()` / `snapshot()` / `submit_batch` 都在锁内读这几个字段。
        with _batch_lock:
            batch["drained"] = True
            batch["running"] = False
            batch["ended_at"] = time.time()
            batch["elapsed"] = batch["ended_at"] - batch["started_at"]


def _mark_task_cancelled(batch: dict, task: dict, why: str) -> None:
    """把一份还没开始的任务作废（**调用方必须已持有 `_batch_lock`**）。

    计数在这里加，是因为被作废的份永远不会进 `_run_one` 的 finally，
    不加就永远不到 100%（`percent = finished / total`）。
    """
    now = time.time()
    task.update({"status": "cancelled", "stage": "cancelled", "error": "",
                 "note": why, "ended_at": now,
                 "seconds": round(now - (task.get("started_at") or now), 2)})
    batch["cancelled"] = int(batch.get("cancelled") or 0) + 1
    batch["finished"] = int(batch.get("finished") or 0) + 1


def cancel(batch_id: str | None = None) -> dict:
    """取消一个批次：**未开始**的份作废，**正在跑**的份跑完当前文件后收尾。

    接口契约（卡 C-0040；下一张卡 C-0041 的看板按钮依赖它）：

    - `pending` → `cancelled`，且**不再启动**。两处保证：`cancel()` 当场标记，
      `_run_one` 开头再挡一道（线程池已经在排队的份仍会被唤醒，但一看状态就
      直接返回，不进管线）。
    - 正在跑的份**不硬杀**。它们手里有活的 SQLite 连接、未提交的写事务、
      写了一半的文档；硬杀线程只会留下半条记录，而且已经花掉的识图钱也退不
      回来（`core/kp_batch.py::cancel` 是同一个理由）。它们跑完当前这一份
      文件后自然收尾，不会再有新的启动。
    - **幂等**：重复取消、取消不存在的批次都不抛异常。第二次取消时 pending
      已经是 0，所以 `cancelled` 返回 0。
    - `running`（批次级）：取消那一刻若**没有**任何份在跑，立即置 false
      （`batch.progress` 当场显示已停）；若还有份在跑就保持 true，
      等最后一个份收尾时由 `_run_batch` 置 false —— 这期间
      `cancelling=true`、`drained` 仍为 false，`_batch_still_busy()` 会继续
      拦住新批次，不会出现两套线程池并发写库。

    返回 `{"batch_id", "cancelled", "pending", "running", "found", ...}`：
    `cancelled` = 本次被作废的份数，`pending` / `running` = 调用那一刻的计数。
    `batch_id` 省略时取当前批次（与 `snapshot()` 同口径）。
    """
    with _batch_lock:
        bid = batch_id or _active
        batch = _batches.get(bid or "")
        if not batch:
            return {"batch_id": bid or "", "cancelled": 0, "pending": 0,
                    "running": 0, "found": False, "already_cancelled": False,
                    "note": "批次不存在（看板重启后内存里的批次会丢）"}
        pend = [t for t in batch["tasks"] if t["status"] == "pending"]
        running = [t for t in batch["tasks"] if t["status"] == "running"]
        already = bool(batch.get("cancelling"))
        batch["cancelling"] = True
        for t in pend:
            _mark_task_cancelled(batch, t, "批次已取消（尚未开始）")
        now = time.time()
        if not running and batch.get("running"):
            batch["running"] = False
            batch["ended_at"] = now
            batch["elapsed"] = max(0.0, now - (batch.get("started_at") or now))
        if pend:
            note = "未开始的 %d 份已作废；正在跑的 %d 份会跑完当前文件" % (
                len(pend), len(running))
        elif running:
            note = "没有待启动的份；正在跑的 %d 份会跑完当前文件" % len(running)
        else:
            note = "批次已结束，没有可取消的份" if not already else "该批次之前已取消过"
        return {"batch_id": batch["id"], "cancelled": len(pend),
                "pending": len(pend), "running": len(running),
                "found": True, "already_cancelled": already,
                "finished": batch["finished"], "total": batch["total"],
                "note": note}


@contextlib.contextmanager
def _model_slot(kind: str = "ingest"):
    """取一个**进程级模型调用槽**（卡 C-0063，与整理管线共享同一把闸门）。

    延迟 import `core.kp_build` 是有意的：`pipeline_pool` 自己会拉起
    `ingest → embed`（onnxruntime，几百 MB），而 `kp_build` 只依赖
    `config / outline / progress`（轻量）。闸门定义在 `kp_build` 里是为了让
    整理管线不必 import 入库这套重依赖，这里在真正要跑任务时才 import 回来。

    [C-0119 修复] 取槽按**对象实际支持的协议**分派：是上下文管理器就用
    `with`，否则退回用 `for` 驱动生成器。两种形态语义完全等价（都在进入时
    `sem.acquire()`、退出时 `sem.release()`，闸门统计仍由 kp_build 的原实现
    记账），所以分派是安全的，不会再出现"哪一边被修好、另一边就炸"。

    历史（这个垫片为什么会有两种形态）：
      · C-0108：`kp_build.model_slot` 的 `@contextlib.contextmanager`
        被重建错位挂到了前一个函数 `configure_model_gate` 头上（AST 实测：
        `configure_model_gate` 带装饰器、`model_slot` 不带），它那时返回
        **裸生成器** ⇒ 直接 `with kp_build.model_slot(...)` 必抛
        `TypeError: 'generator' object does not support the context manager
        protocol`。当时 `core/kp_build.py` 被明令禁止改动，于是这里在调用侧
        用 `for … : yield` 代替 `with`（生成器一旦被真正创建就会执行
        `sem.acquire()`，循环退出时 `.close()` 触发它的 `finally` 把槽还回去）。
      · C-0111：把装饰器修回 `model_slot` 头上之后，它返回的是
        `_GeneratorContextManager`，上面那个 `for` 反而**每次都抛**
        `TypeError: '_GeneratorContextManager' object is not iterable`。
        位置就在 `_run_one` 进入的第一行 `with _model_slot("ingest"):`，
        所以看板「批量入库」的每一份都是**用时 0 秒**、成功 0 / 失败 N
        （实测 2 份全 failed，task["error"] 就是这条 TypeError）。
    """
    from . import kp_build
    slot = kp_build.model_slot(kind)
    if hasattr(slot, "__enter__") and hasattr(slot, "__exit__"):
        with slot:              # 正常形态：kp_build 侧带 @contextlib.contextmanager
            yield
    else:                       # 兜底：装饰器若再次错位，它会退化成裸生成器
        for _ in slot:
            yield


def _run_one(bid: str, task: dict) -> None:
    """跑一份文件。**任何异常都只标记这一份失败，绝不外抛。**"""
    batch = _batches[bid]
    with _batch_lock:
        # 取消的两道闸（卡 C-0040）。放在 try 之前，所以早退的份不会走到
        # 下面的 finally 去重复计数：
        #   1) `cancel()` 已经把它标成 cancelled 并计过数了 → 什么都不做；
        #   2) 取消请求刚到、这一份还没来得及被标记（线程池已在排队）→ 当场作废。
        if task["status"] == "cancelled":
            return
        if batch.get("cancelling"):
            _mark_task_cancelled(batch, task, "批次已取消（尚未开始）")
            return
        task["status"] = "running"
        task["started_at"] = time.time()
    _local.task = task          # ← 进度分流的开关：本线程的进度都归这份文件
    conn = None
    try:
        # 卡 C-0063：与整理管线**共享同一把进程级模型闸门**（`core/kp_build.py`
        # 的"并发闸门"小节）。这里按**份**持有一个槽——逐页识图的调用在
        # `core/figures.py` 里，那是另一张卡的范围，本卡不动那个文件，所以口径
        # 取"一份入库任务 = 一个在飞的模型调用"。持槽期间内部**不再取槽**，
        # 不存在"持槽等槽"的死锁（见 model_slot 的说明）。
        with _model_slot("ingest"):
            conn = task_connect(batch["db_path"] or None)
            rep = ingest_mod.ingest_paths(
                conn, [Path(task["abs"])], force=batch["force"],
                with_ocr=batch["with_ocr"], verbose=False,
                with_figures=batch["with_figures"])
        detail = rep.details[0] if rep.details else {}
        st = str(detail.get("status") or "failed")
        task["pipeline_status"] = st
        task["chunks"] = int(detail.get("chunks") or 0)
        task["warnings"] = list(detail.get("warnings") or [])[:6]
        figs = detail.get("figures") or {}
        task["cost_cny"] = round(float(figs.get("cost_cny") or 0.0)
                                 + float(detail.get("outline_cost") or 0.0), 4)
        task["reason"] = detail.get("reason") or ""
        if st in ("added", "updated", "partial"):
            task["status"] = "done"
            task["percent"] = 100
        elif st == "skipped":
            task["status"] = "skipped"
            task["percent"] = 100
        elif st == "unsupported":
            task["status"] = "unsupported"
            task["percent"] = 100
        else:
            task["status"] = "failed"
            task["error"] = detail.get("reason") or "入库失败"
    except Exception as e:  # noqa: BLE001
        task["status"] = "failed"
        task["error"] = f"{type(e).__name__}: {e}"
        task["traceback"] = traceback.format_exc()[-1500:]
    finally:
        _local.task = None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        task["ended_at"] = time.time()
        task["seconds"] = round(task["ended_at"] - task["started_at"], 2)
        if not task["stage"]:
            task["stage"] = task["status"]
        with _batch_lock:
            batch["finished"] += 1
            key = {"done": "succeeded", "skipped": "skipped",
                   "unsupported": "unsupported"}.get(task["status"], "failed")
            batch[key] += 1
            batch["chunks"] += int(task.get("chunks") or 0)
            batch["cost_cny"] = round(batch["cost_cny"] + float(task.get("cost_cny") or 0), 4)


def snapshot(batch_id: str | None = None) -> dict:
    """批次进度快照（看板轮询用）。

    字段与 `/api/ingest/progress` **刻意保持大体同构**（total/finished/percent/
    stages…），前端的进度渲染风格因此可以直接复用；多出来的是 `tasks`：
    每份文件各自的阶段、百分比、用时与失败原因。
    """
    with _batch_lock:
        bid = batch_id or _active
        batch = _batches.get(bid or "")
        if not batch:
            return {"batch_id": "", "running": False, "total": 0, "finished": 0,
                    "succeeded": 0, "failed": 0, "skipped": 0, "unsupported": 0,
                    "cancelled": 0, "cancelling": False,
                    "percent": 0, "tasks": [], "stages": list(_global_progress.STAGES),
                    "elapsed": 0.0, "workers": default_workers(),
                    "max_workers": MAX_WORKERS, "cost_cny": 0.0, "chunks": 0,
                    "warmup_error": "", "force": False}
        b = dict(batch)
        b["tasks"] = [dict(t) for t in batch["tasks"]]
        # 对外一律叫 `batch_id`：内部记录用 `id`，但前端与 `/api/*` 的口径是
        # `batch_id`（`submit_batch` 的返回、`retry_failed` 的入参都这么叫）。
        # 少这一行，前端拿到的就是 undefined，面板会一直空着。
        b["batch_id"] = b["id"]
        b["stages"] = list(_global_progress.STAGES)
        b["max_workers"] = MAX_WORKERS
        if b["running"] and b.get("started_at"):
            b["elapsed"] = max(0.0, time.time() - b["started_at"])
        else:
            b["elapsed"] = max(0.0, b.get("ended_at", 0.0) - b.get("started_at", 0.0))
    b["percent"] = int(100 * b["finished"] / b["total"]) if b["total"] else 0
    b["running_files"] = [t["name"] for t in b["tasks"] if t["status"] == "running"]
    b["failed_files"] = [t["rel"] for t in b["tasks"] if t["status"] == "failed"]
    # 被取消的份单列出来：`retry_failed` 只重投 failed，取消的份刻意不在其中
    # （它们不是失败，是用户主动叫停的）。
    b["cancelled_files"] = [t["rel"] for t in b["tasks"] if t["status"] == "cancelled"]
    return b


def retry_failed(batch_id: str, *, workers: int | None = None,
                 db_path: Path | str | None = None) -> dict:
    """把某个批次里**失败的那几份**重新提交为一个新批次。"""
    with _batch_lock:
        batch = _batches.get(batch_id)
        if not batch:
            return {"error": f"批次 {batch_id} 不存在（看板重启后内存里的批次会丢）"}
        if _batch_still_busy(batch):
            return {"error": f"批次 {batch_id} 还在跑，等它结束再重试失败项"}
        # 用**绝对路径**重投。task["rel"] 是"相对项目根"（raw\a\b.pdf），
        # 而 submit_batch 的入参口径是"相对 raw/"——直接拿 rel 重投会被
        # `_resolve` 拼成 raw\raw\...（看板里踩过同一个坑，见 dashboard.py
        # 的 `reindexOne` 注释）。
        paths = [t["abs"] for t in batch["tasks"] if t["status"] == "failed"]
        force = batch.get("force")
        wf = batch.get("with_figures")
        woc = batch.get("with_ocr")
        dbf = db_path or batch.get("db_path")
    if not paths:
        return {"error": "该批次没有失败项"}
    return submit_batch(paths, force=force, with_ocr=woc, with_figures=wf,
                        workers=workers if workers is not None
                        else batch.get("workers"), db_path=dbf)