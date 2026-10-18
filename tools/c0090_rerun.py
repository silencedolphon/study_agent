# -*- coding: utf-8 -*-
"""C-0090 用**教材专线**重跑 doc#91（卡面第 2 条）。

走的入口是 C-0089 的 `dashboard.api_ingest_textbook()` —— 也就是资料栏
「教材入库」按钮背后那个函数本体（HTTP 路由 `/api/ingest/textbook` 只是
`with _INGEST_LOCK: return api_ingest_textbook(...)` 的一层壳）。
唯一差别：**不经过 HTTP 层**——452 页本地 OCR 实测以小时计，同步 HTTP
请求等不起，而且脚本化跑出来的回执 JSON 更适合做对照证据。

`force=True` 是必需项：不传的话内容哈希没变会直接 `skipped`。
`with_figures=False`（入口默认值）：教材专线走本地免费 OCR，不调视觉大模型。

用法：
    set PYTHONIOENCODING=utf-8
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_rerun.py --probe 3
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_rerun.py --run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0090")
REL = "药理/药理学 第6版 刘晓东.pdf"          # raw/ 下的相对路径（doc#91）


def probe(n: int) -> int:
    """只测 OCR 速度，不写库：给"要不要现在重跑"一个实测数字。"""
    from core import config, extract

    p = Path(config.RAW_DIR) / Path(*REL.split("/"))
    print(f"OCR 测速：{p}")
    print(f"  OCR_DPI={config.OCR_DPI}  OCR_TABLE={getattr(config, 'OCR_TABLE', '?')}")
    t0 = time.perf_counter()
    eng = extract._get_ocr()
    t1 = time.perf_counter()
    print(f"  引擎加载 {t1 - t0:.1f} 秒  engine={'OK' if eng else 'None'}")
    if eng is None:
        return 2
    pages = list(range(1, n + 1))
    t2 = time.perf_counter()
    res = extract.ocr_pdf_pages(p, pages)
    t3 = time.perf_counter()
    dt = t3 - t2
    nblk = len(res.blocks)
    print(f"  {n} 页 OCR 用时 {dt:.1f} 秒 => {dt / max(n, 1):.1f} 秒/页")
    print(f"  块数={nblk} 警告={res.warnings[:2]}")
    est = dt / max(n, 1) * 452
    print(f"  ⇒ 452 页预估 {est / 60:.0f} 分钟（{est / 3600:.2f} 小时）")
    (CARD / "probe_ocr_speed.json").write_text(json.dumps({
        "pages": n, "seconds": round(dt, 1),
        "sec_per_page": round(dt / max(n, 1), 2),
        "engine_load_sec": round(t1 - t0, 1),
        "est_452_minutes": round(est / 60, 1),
        "blocks": nblk, "warnings": res.warnings[:5],
        "dpi": config.OCR_DPI, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def run() -> int:
    import dashboard as D
    from core import config

    t0 = time.perf_counter()
    print(f"教材专线重跑：paths=[{REL!r}] force=True with_figures=False")
    print(f"起始 {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    # 与路由 `/api/ingest/textbook` 同一条配置口径（路由里也是先 activate 再进函数）
    info = config.activate("ingest")
    print(f"config.activate('ingest') = {info}", flush=True)
    try:
        out = D.api_ingest_textbook(paths=[REL], force=True)
    except Exception as e:                       # noqa: BLE001
        import traceback
        out = {"error": f"{type(e).__name__}: {e}",
               "traceback": traceback.format_exc()}
    dt = time.perf_counter() - t0
    out["_seconds"] = round(dt, 1)
    out["_minutes"] = round(dt / 60, 1)
    out["_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    CARD.mkdir(parents=True, exist_ok=True)
    dst = CARD / "rerun_receipt.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n耗时 {dt / 60:.1f} 分钟")
    print(f"回执 -> {dst}")
    print(json.dumps(out, ensure_ascii=False, indent=2)[:4000])
    return 0 if not out.get("error") else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", type=int, default=0, help="只测 N 页 OCR 速度")
    ap.add_argument("--run", action="store_true", help="真正重跑")
    a = ap.parse_args()
    if a.probe:
        return probe(a.probe)
    if a.run:
        return run()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
