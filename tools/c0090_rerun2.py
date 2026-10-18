# -*- coding: utf-8 -*-
"""C-0090 重跑（第二版）：分批 OCR + 断点续跑 + 崩溃定位。

## 为什么要有第二版

第一版（`tools/c0090_rerun.py`）在 2026-10-13 **22:25:05 崩掉**：
Windows 事件日志 = `python.exe` / `ntdll.dll` / **0xc0000005（访问冲突）**，
崩溃转储落在 `%LOCALAPPDATA%\\CrashDumps\\python.exe.5184.dmp`（12.8 MB）。
**不是内存不足**（物理内存 23.8 GB、页面文件峰值仅 61 MB），是某个**原生扩展**
（onnxruntime / opencv / pymupdf / sqlite3）里的访问冲突。库因为事务没提交而
**一字未改**（doc#91 仍是 867 块），但整整 127 分钟的 OCR 全部白费——
原生崩溃会把 stdout 缓冲区一起带走，日志里一个字都没留下。

## 本版做了什么（不改 core 一行，不改重跑语义）

1. **分批调 `extract.ocr_pdf_pages`**（默认每 40 页一批），每批返回就 pickle 落盘
   `_tmp_c0090/ocr_batch_*.pkl`。崩在第 N 批时，前 N-1 批重跑时直接秒加载。

   **为什么分批是等价的**（不是"为了省时间走捷径"）：`core/tables.py` 的
   `recover_pages` 与 `recover_overviews` 都是
   `for pno, img, boxes in pages:` 的**逐页独立**循环——`detect_grid(img)`、
   `assign_cells(kept, grid)`、`table_lines(...)` 只吃**该页自己的图与框**，
   没有任何跨页状态。`ocr_pdf_pages` 也是按 `sorted(one_dim)` 逐页产出块。
   所以分批与一次性调用**逐页结果相同**，合并规则是确定的：
   块按页序拼接、`meta['table_pages']` 取并集、`meta['table_overviews']` 合并 dict、
   `warnings` 按总数重新生成一条（与一次性调用的那一条逐字同形）。
   附带好处：`collected` 里常驻的整页图从 452 张降到 40 张（内存峰值 ≈1/11）。

2. **`faulthandler` 写进日志文件**：原生崩溃时会打印 **Python 层调用栈**，
   直接把崩溃点定位到具体函数。这正是第一版缺的东西。

3. **阶段日志 + RSS 监控**：OCR / 章识别 / 切块 / 嵌入各段的起止时间与内存。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_rerun2.py
    set C0090_OCR_BATCH=40        # 可选，默认 40；设 0 = 一次跑完（第一版的行为）
"""
from __future__ import annotations

import ctypes
import faulthandler
import json
import os
import pickle
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0090")
TMP = ROOT / "_tmp_c0090"
LOG = ROOT / "logs" / "c0090_rerun2.log"
REL = "药理/药理学 第6版 刘晓东.pdf"
BATCH = int(os.environ.get("C0090_OCR_BATCH", "40") or 0)

PHASE = {"name": "启动", "t0": time.time()}
_STOP = threading.Event()
_logf = None


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _logf is not None:
        try:
            _logf.write(line + "\n")
            _logf.flush()
        except Exception:                        # noqa: BLE001
            pass


def set_phase(name: str) -> None:
    PHASE["name"] = name
    PHASE["t0"] = time.time()
    log(f"--- 阶段：{name} ---")


class _PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def rss_mb() -> float:
    try:
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(pmc), pmc.cb)
        return pmc.WorkingSetSize / 1048576.0
    except Exception:                            # noqa: BLE001
        return -1.0


def monitor() -> None:
    while not _STOP.wait(60):
        log(f"  [mon] 阶段={PHASE['name']} 已 "
            f"{(time.time() - PHASE['t0']) / 60:.1f} 分钟 RSS={rss_mb():.0f}MB")


# --------------------------------------------------------------------------
# OCR 分批 + 断点续跑
# --------------------------------------------------------------------------
def _merge(parts: list) -> object:
    """把若干批 `ExtractResult` 合并成一份（规则见模块 docstring）。"""
    from core.extract import ExtractResult

    out = ExtractResult()
    tpages: set[int] = set()
    tover: dict[int, str] = {}
    warned_tables = False
    for p in parts:
        out.blocks.extend(p.blocks)
        for w in p.warnings:
            if "页按表格还原" in w:
                warned_tables = True          # 汇总后统一生成一条
            else:
                out.warnings.append(w)
        for k, v in (p.meta or {}).items():
            if k == "table_pages":
                tpages.update(int(x) for x in (v or []))
            elif k == "table_overviews":
                tover.update(v or {})
            else:
                out.meta[k] = v
    if parts and ("table_pages" in (parts[0].meta or {}) or warned_tables):
        out.meta["table_pages"] = sorted(tpages)
        out.meta["table_overviews"] = tover
    if warned_tables and tpages:
        out.warnings.append(
            f"{len(tpages)} 页按表格还原（{'/'.join(str(p) for p in sorted(tpages))}）")
    return out


def install_ocr_hook() -> None:
    from core import extract

    orig = extract.ocr_pdf_pages

    def run_one(path, pages, dpi, tag: str):
        cf = TMP / f"ocr_batch_{tag}.pkl"
        if cf.exists():
            try:
                res = pickle.loads(cf.read_bytes())
                log(f"  复用已落盘批次 {tag}（{cf.stat().st_size / 1048576:.1f}MB，"
                    f"{len(res.blocks)} 块）")
                return res
            except Exception as e:               # noqa: BLE001
                log(f"  批次 {tag} 缓存损坏（{type(e).__name__}: {e}），重跑")
        t0 = time.time()
        res = orig(path, pages, dpi)
        dt = time.time() - t0
        try:
            TMP.mkdir(parents=True, exist_ok=True)
            cf.write_bytes(pickle.dumps(res))
            log(f"  批次 {tag} 完成：{len(res.blocks)} 块 / {dt / 60:.1f} 分钟"
                f"（{dt / max(len(pages), 1):.2f} 秒/页）已落盘 "
                f"{cf.stat().st_size / 1048576:.1f}MB")
        except Exception as e:                   # noqa: BLE001
            log(f"  ⚠ 批次 {tag} 落盘失败：{type(e).__name__}: {e}")
        return res

    def wrapped(path, pages, dpi=None):
        pages = list(pages)
        if BATCH <= 0 or len(pages) <= BATCH:
            set_phase(f"OCR 一次性（{len(pages)} 页）")
            t0 = time.time()
            res = orig(path, pages, dpi)
            log(f"OCR 完成（一次性）：{len(res.blocks)} 块 / "
                f"{(time.time() - t0) / 60:.1f} 分钟，RSS={rss_mb():.0f}MB")
            return res
        set_phase(f"OCR 分批（{len(pages)} 页 × 每批 {BATCH}）")
        parts = []
        t_all = time.time()
        for i in range(0, len(pages), BATCH):
            chunk = pages[i:i + BATCH]
            tag = f"{i // BATCH:03d}_p{chunk[0]}-{chunk[-1]}"
            log(f"批次 {tag} 开始（第 {i + 1}~{i + len(chunk)} 页 / "
                f"共 {len(pages)}）RSS={rss_mb():.0f}MB")
            PHASE["t0"] = time.time()
            parts.append(run_one(path, chunk, dpi, tag))
        res = _merge(parts)
        log(f"OCR 全部分批完成：{len(res.blocks)} 块 / "
            f"{(time.time() - t_all) / 60:.1f} 分钟，"
            f"表格页 {len(res.meta.get('table_pages') or [])} 页，RSS={rss_mb():.0f}MB")
        return res

    extract.ocr_pdf_pages = wrapped
    log(f"已挂上 OCR 分批钩子（每批 {BATCH or '不分批'} 页，缓存目录 {TMP}）")


def install_stage_hooks() -> None:
    from core import ingest as ing
    from core import textbook as tb
    from core import chunk as ck
    from core import embed as em

    o_det = tb.detect_chapters

    def det(*a, **k):
        set_phase("章识别 detect_chapters")
        r = o_det(*a, **k)
        log(f"章识别完成：{len(r.body)} 章，fallback={r.fallback}，"
            f"warnings={list(r.warnings)[:3]}")
        return r

    tb.detect_chapters = det

    o_cut = tb.cut_by_chapter

    def cut(*a, **k):
        set_phase("按章切块 cut_by_chapter")
        r = o_cut(*a, **k)
        log(f"切块完成：{len(r)} 块")
        return r

    tb.cut_by_chapter = cut

    o_cb = ck.chunk_blocks

    def cb(*a, **k):
        set_phase("通用切块 chunk_blocks")
        r = o_cb(*a, **k)
        log(f"chunk_blocks 返回 {len(r)} 块")
        return r

    ck.chunk_blocks = cb
    ing.chunk_mod.chunk_blocks = cb

    o_emb = em.Embedder.embed_documents

    def emb(self, texts, batch_size=16, progress=False):
        set_phase(f"嵌入 {len(texts)} 块")
        t0 = time.time()
        r = o_emb(self, texts, batch_size=batch_size, progress=False)
        log(f"嵌入完成 {len(texts)} 块 / {time.time() - t0:.1f} 秒")
        return r

    em.Embedder.embed_documents = emb
    log("已挂上切块/嵌入阶段钩子")


def main() -> int:
    global _logf
    LOG.parent.mkdir(parents=True, exist_ok=True)
    _logf = open(LOG, "w", encoding="utf-8", buffering=1)
    faulthandler.enable(file=_logf, all_threads=True)
    log("=" * 72)
    log(f"C-0090 重跑（第二版）pid={os.getpid()}  OCR 每批 {BATCH} 页")
    log(f"日志：{LOG}")

    import dashboard as D
    from core import config

    info = config.activate("ingest")
    log(f"config.activate('ingest') = {info}")
    log(f"OCR_DPI={config.OCR_DPI} OCR_TABLE={getattr(config, 'OCR_TABLE', '?')} "
        f"RSS={rss_mb():.0f}MB")

    install_ocr_hook()
    install_stage_hooks()
    threading.Thread(target=monitor, daemon=True).start()

    set_phase("教材专线入库 api_ingest_textbook")
    t0 = time.time()
    try:
        out = D.api_ingest_textbook(paths=[REL], force=True)
    except BaseException as e:                   # noqa: BLE001
        import traceback
        log(f"api_ingest_textbook 抛出 {type(e).__name__}: {e}")
        log(traceback.format_exc())
        out = {"error": f"{type(e).__name__}: {e}",
               "traceback": traceback.format_exc()}
    dt = time.time() - t0
    _STOP.set()

    out["_seconds"] = round(dt, 1)
    out["_minutes"] = round(dt / 60, 1)
    out["_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    out["_ocr_batch"] = BATCH
    out["_pid"] = os.getpid()
    CARD.mkdir(parents=True, exist_ok=True)
    (CARD / "rerun_receipt.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"耗时 {dt / 60:.1f} 分钟；回执 -> {CARD / 'rerun_receipt.json'}")
    log(json.dumps(out, ensure_ascii=False)[:1500])
    log("正常结束")
    return 0 if not out.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
