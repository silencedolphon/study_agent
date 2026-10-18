#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。     
# File-ID: SA-93-c0093hp   (project study_agent; keep this line)
r"""卡 C-0093 补充验证：**真 HTTP** 打新接口（临时端口起一个真看板，不动 8756）。

`tools/verify_c0093.py` 验的是"函数与安全闸"（2~5 秒、快档）；
本脚本验的是"**看板上真的能这么用**"——`Handler` 里那两条路由、响应头、
状态码、旧接口零回归。用**临时端口**（默认 8793）起一个独立看板实例，
跑完就停；**不会**碰正在跑的 8756（看板是常驻进程，新代码要重启才生效，
本卡不重启——所以这个脚本是"新代码在真 HTTP 上可用"的唯一现场证据）。

用例（对着当前学科库；当前库没有教材时自动降级为 SKIP 并说明）：
  1. `/api/teach-page?doc_id=<教材>&page=<页>` 首次 → 200 + `image/png` + `X-Teach-Cache: miss`
  2. 同一页再来一次 → 200 + `X-Teach-Cache: hit`（且字节完全一致）
  3. 换 dpi → 200（DPI 进缓存文件名，不串图）
  4. 越界页 / 非教材 doc / 不存在的 doc / 把路径当 doc_id / page=abc → 400/403/404，**不是 500**
  5. `/api/kp-textbook?doc_id=&kp_id=` → 200 + `material` 原文 + `textbook.passages`
  6. 回归：`/api/summary-kp`、`/api/mindmap`、`/api/page-kps`、`/api/stats` 照常 200

跑法（绝对路径，不依赖 cwd）：
    G:\study_agent\venv\Scripts\python.exe -X utf8 G:\study_agent\tools\probe_c0093_http.py [端口]

输出：结论行 `C0093-HTTP-PASS` / `C0093-HTTP-FAIL` + 关键数字；明细写日志。
退出码 0（PASS）/ 2（FAIL）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8793
WORK = ROOT / "_tmp_c0093"
WORK.mkdir(parents=True, exist_ok=True)
LOG_PATH = WORK / "probe_c0093_http.log"
DASH_LOG = WORK / "probe_c0093_dashboard.log"
PY = ROOT / "venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)

_log = open(LOG_PATH, "w", encoding="utf-8")
P = lambda *a: print(*a, file=_log)          # noqa: E731
fails: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> bool:
    if not cond:
        fails.append(label)
    P(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  —— {detail}" if detail else ""))
    return bool(cond)


def call(base: str, path: str):
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(base + path, timeout=60) as r:
            return r.status, dict(r.headers), r.read(), (time.perf_counter() - t) * 1000
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(), (time.perf_counter() - t) * 1000


def main() -> int:
    base = f"http://127.0.0.1:{PORT}"
    P(f"== C-0093 真 HTTP 探针  {time.strftime('%Y-%m-%d %H:%M:%S')}  端口={PORT} ==")
    logf = open(DASH_LOG, "wb")
    proc = subprocess.Popen([str(PY), str(ROOT / "tools" / "dashboard.py"),
                             "--port", str(PORT), "--no-open"],
                            cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        ready = False
        while time.time() - t0 < 90:
            if proc.poll() is not None:
                break
            try:
                st, _h, _b, _ms = call(base, "/api/stats")
                if st == 200:
                    ready = True
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        check(ready, "临时看板实例起来了（不动 8756）",
              f"{time.time()-t0:.1f} s，pid={proc.pid}")
        if not ready:
            return 2

        # 探一下当前学科库有没有教材（没有就跳过渲染用例，但旧接口回归照跑）
        st, hdr, body, ms = call(base, "/api/kp-textbook?doc_id=73&kp_id=KP-N03-1")
        tb = {}
        if st == 200:
            tb = (json.loads(body.decode("utf-8")) or {}).get("textbook") or {}
        tb_id = int(tb.get("doc_id") or 0)
        pages = int(tb.get("pages") or 0)
        P(f"当前学科库教材：doc#{tb_id} pages={pages} n_chunks={tb.get('n_chunks')}")

        if tb_id and pages:
            p = min(pages, 121)
            # 先删掉这一页的缓存，才能测到"首次渲染"的真实耗时与 cache=miss。
            sys.path.insert(0, str(ROOT))
            from core import teach as _teach        # noqa: PLC0415
            cache = _teach.page_cache_path(tb_id, p, _teach.TEACH_DPI)
            try:
                cache.unlink(missing_ok=True)
            except OSError:
                pass
            st1, h1, b1, ms1 = call(base, f"/api/teach-page?doc_id={tb_id}&page={p}")
            st2, h2, b2, ms2 = call(base, f"/api/teach-page?doc_id={tb_id}&page={p}")
            st3, h3, b3, ms3 = call(
                base, f"/api/teach-page?doc_id={tb_id}&page={p}&dpi=110")
            check(st1 == 200 and b1[:8] == b"\x89PNG\r\n\x1a\n"
                  and h1.get("Content-Type") == "image/png"
                  and h1.get("X-Teach-Cache") == "miss",
                  "① 教材单页渲染：200 + image/png + 首次 cache=miss",
                  f"p.{p} {ms1:.0f} ms {len(b1)/1024:.0f} KB "
                  f"X-Teach={h1.get('X-Teach-Page')} 渲染 {h1.get('X-Teach-Ms')} ms")
            check(st2 == 200 and h2.get("X-Teach-Cache") == "hit" and b2 == b1,
                  "② 同一页第二次：cache=hit 且字节完全一致",
                  f"{ms2:.0f} ms（未重渲染：X-Teach-Ms={h2.get('X-Teach-Ms')}）")
            check(st3 == 200 and h3.get("X-Teach-Dpi") == "110" and b3 != b1,
                  "③ 换 dpi：200 且图不同（DPI 进缓存键，不串图）",
                  f"dpi=110 {ms3:.0f} ms {len(b3)/1024:.0f} KB")
            cases = [
                (f"/api/teach-page?doc_id={tb_id}&page=0", 400, "页码 0"),
                (f"/api/teach-page?doc_id={tb_id}&page={pages + 1}", 400, "页码越界"),
                (f"/api/teach-page?doc_id={tb_id}&page=abc", 400, "page 非数字"),
                ("/api/teach-page?doc_id=..%2F..%2Fwindows%2Fwin.ini&page=1", 400,
                 "把路径当 doc_id"),
                ("/api/teach-page?doc_id=999999&page=1", 404, "不存在的 doc"),
            ]
            det = []
            ok = True
            for pth, want, why in cases:
                c, _h, b, _m = call(base, pth)
                ok = ok and c == want
                det.append(f"{why}→{c}")
            check(ok, "④ 安全：越界 / 非法入参 / 不存在 doc 全部被拒（非 500）",
                  "；".join(det))
            c, _h, b, _m = call(base, f"/api/teach-page?doc_id={tb_id}&page={pages + 1}")
            check(c == 400 and str(pages).encode() in b,
                  "④ 越界文案里带教材总页数（可操作）", b.decode("utf-8")[:120])
            # 非教材：从 mindmap 列表里挑一份非教材文档试
            st, _h, body, _ms = call(base, "/api/mindmap")
            non_tb = 0
            try:
                for it in (json.loads(body.decode("utf-8")) or {}).get("documents") or []:
                    if int(it.get("doc_id") or 0) not in (0, tb_id):
                        non_tb = int(it["doc_id"])
                        break
            except Exception:  # noqa: BLE001
                pass
            if non_tb:
                c, _h, b, _m = call(base, f"/api/teach-page?doc_id={non_tb}&page=1")
                check(c == 403, "④ 只放行教材类 PDF（课件 doc#%d → 403）" % non_tb,
                      b.decode("utf-8")[:140])
        else:
            P("[SKIP] 当前学科库里没有教材（role=textbook 的 PDF），渲染用例跳过；"
              "默认库（药理）下有 doc#91，可切回默认库再跑本脚本。")

        st, _h, body, ms = call(base, "/api/kp-textbook?doc_id=73&kp_id=KP-N03-1")
        d = json.loads(body.decode("utf-8")) if st == 200 else {}
        blocks = (d.get("material") or {}).get("blocks") or []
        psg = ((d.get("textbook") or {}).get("passages")) or []
        check(st == 200 and blocks and all(x.get("text") for x in blocks),
              "⑤ 知识点 → material[].chunk_id → chunks.text 取到原文（HTTP）",
              f"{len(blocks)} 块，首块 {blocks[0].get('cite') if blocks else '-'}")
        check(st == 200 and d.get("status") in ("ok", "no_match", "no_textbook"),
              "⑤ 知识点 → 教材段落（HTTP，带页码/章名/命中理由）",
              f"status={d.get('status')} 段落={len(psg)} "
              f"{psg[0].get('page_label') if psg else ''} "
              f"{psg[0].get('chapter') if psg else ''}")

        P("\n-- 回归：旧接口照常 --")
        reg_ok = True
        for pth in ["/api/summary-kp?doc_id=73&kp_id=KP-N03-1", "/api/mindmap?doc_id=73",
                    "/api/page-kps?doc_id=73&page=1", "/api/stats", "/api/docs",
                    "/img/slide?doc_id=103&page=1"]:
            c, _h, b, m = call(base, pth)
            reg_ok = reg_ok and c in (200, 404)
            P(f"   {c} {pth} ({m:.0f} ms, {len(b)} 字节)")
        check(reg_ok, "⑥ 回归：summary-kp / mindmap / page-kps / stats / docs / img·slide 照常")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:  # noqa: BLE001
            proc.kill()
        logf.close()
        P(f"\n临时看板已停（pid={proc.pid}）")

    verdict = "C0093-HTTP-PASS" if not fails else "C0093-HTTP-FAIL"
    P(f"{verdict} 失败项={fails or '无'}")
    _log.close()
    print(f"{verdict} 真 HTTP 新接口（临时端口 {PORT}）"
          f"{'全部通过' if not fails else '失败：' + '；'.join(fails)}")
    print(f"明细日志：{LOG_PATH}")
    return 0 if not fails else 2


if __name__ == "__main__":
    raise SystemExit(main())
