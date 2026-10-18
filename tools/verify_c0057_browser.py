# -*- coding: utf-8 -*-
"""卡 C-0057 ★真浏览器验证：课件答疑走独立管线（不检索 / 不用提示词 / 思考最强）。

流程：
  1. `subjects.restore()` 跟随看板的**当前学科**，快照现场（五表行数 /
     logs/knowledge 产物 sha256 / notes / 库文件 sha256）；
  2. 起一个**临时端口**实例（跑完杀掉；绝不碰 8756）；
  3. 跑 `tools/probe_c0057_browser.mjs`（真 Edge + CDP）：
     · 知识点页「AI答疑」→ 选**当前学科里最大的那份课件**（多半超预算）→ 真提问 →
       收回答 → 抠范围行与回答下方那行（模型 / 思考强度 / token / 是否截断）；
     · 同一次运行里再走一遍问答页的**知识库问答**（对照：仍有检索、证据、角标）；
     · 角标链路另外用页面自己的 `linkCites` + 合成消息自检一次（免钱、可单跑）；
  4. 解析两条链路**原始 SSE**（探针用 fetch 钩子抓的）：
     · 课件答疑：`scope` 里 pipeline=deck_direct、参数与截断实况；**没有 sources**；
     · 知识库问答：**有 sources**（检索到的证据条数）——对照证据；
  5. 记账：从 SSE 的 usage 算 token 与金额（口径同 core/kp_build.py）；
  6. 零副作用：五表仍 0 行、产物 json 逐字节未变、notes/ 未变、未碰 8756。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0057_browser.py
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0057_browser.py --skip-ai   # 不花钱
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(r"G:\study_agent")
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0057")
SHOTS = CARD / "shots"
sys.path.insert(0, str(ROOT))
from core import chat as chat_mod  # noqa: E402
from core import config, subjects  # noqa: E402

PRICE_IN, PRICE_OUT, FX = 0.15, 0.60, 7.1
OK = 0
BAD = 0
LINES: list[str] = []
COST: list[dict] = []


def emit(s: str = "") -> None:
    print(s, flush=True)
    LINES.append(s)


def check(cond, label: str, detail="") -> bool:
    global OK, BAD
    detail = "" if detail is None else str(detail)
    if cond:
        OK += 1
        emit("  [OK] %s%s" % (label, ("  —— " + detail) if detail else ""))
    else:
        BAD += 1
        emit("  [★FAIL] %s%s" % (label, ("  —— " + detail) if detail else ""))
    return bool(cond)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def snap(d: Path) -> dict:
    return {str(p.relative_to(d)): sha256(p)
            for p in sorted(d.rglob("*")) if p.is_file()} if d.is_dir() else {}


def cost_of(usage: dict) -> float:
    ti = float((usage or {}).get("prompt_tokens") or 0)
    to = float((usage or {}).get("completion_tokens") or 0)
    return round((ti / 1e6 * PRICE_IN + to / 1e6 * PRICE_OUT) * FX, 6)


def reasoning_tokens(usage: dict) -> int:
    return int(((usage or {}).get("completion_tokens_details") or {})


def get_json(port: int, path: str) -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


def parse_sse(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        try:
            out.append(json.loads(line[6:]))
        except ValueError:
            pass
    return out


def five_counts(conn) -> dict:
    return {t: int(conn.execute("SELECT COUNT(*) AS n FROM %s" % t).fetchone()["n"])
            for t in ("knowledge_points", "kp_sources", "kp_edges",
                      "mistakes", "reviews")}


def main() -> int:
    # 纯 ASCII 的机器可读结论行：中文在别的终端/管道里可能被转码，ASCII 不会。
    print("C0057 BROWSER CHECK: RUNNING", flush=True)
    rc = _main()
    print("C0057 BROWSER CHECK RESULT: %s (%d ok / %d bad)"
          % ("PASS" if rc == 0 else "FAIL", OK, BAD), flush=True)
    return rc


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", type=int, default=0,
                    help="目标课件；0 = 自动选当前学科里最大的那份（验截断）")
    ap.add_argument("--q", default="这份课件的重点是什么？分条列出。")
    ap.add_argument("--kbq", default="这份课件讲了哪些主要内容？")
    ap.add_argument("--skip-ai", action="store_true")
    ap.add_argument("--replay", action="store_true",
                    help="**不重跑浏览器、不花钱**：直接拿上一次探针产出的 "
                         "browser_probe.json 重新判定一遍（断言改过之后重核证据用）")
    args = ap.parse_args()

    # ★ 跟随看板的真实学科：看板启动时就是这么恢复的（`subjects.restore()`）。
    #   不这么做的话，脚本读根库、看板读学科库，两边看的根本不是同一批课件
    #   （本次实测踩到：当前学科是「药物化学」，根库里的 doc#89/91 在下拉里没有）。
    subjects.restore()
    slug = subjects.current()

    SHOTS.mkdir(parents=True, exist_ok=True)
    blog = config.LOG_DIR / "_c0057_browser_auto.out.log"
    blog.parent.mkdir(parents=True, exist_ok=True)
    jsout = CARD / "browser_probe.json"
    proc = None
    port = 0

    if args.replay:
        # ---- 重放模式：只读上一次的探针 JSON，重新判定（一个字节都不发出去）----
        if not jsout.exists():
            emit("★没有可重放的 %s" % jsout)
            return 2
        D = json.loads(jsout.read_text(encoding="utf-8"))
        args.doc = int(D.get("doc") or args.doc or 0)
        emit("=" * 78)
        emit("卡 C-0057 浏览器验证·**重放判定**（不重跑浏览器、不花钱）")
        emit("  证据来源：%s（上次真跑留下的探针产物）" % jsout)
        emit("  学科《%s》· 库 %s" % (slug, config.DB_PATH))
        emit("  目标课件 doc#%d · 重放的提问：%s" % (args.doc, D.get("q")))
        emit("=" * 78)
    else:
        # 选目标课件：当前学科库里 corpus=main 的最大一份（多半会超预算 → 验截断）
        if not args.doc:
            conn0 = ro_conn(config.DB_PATH)
            rows = conn0.execute(
                "SELECT id, title, n_chunks FROM documents WHERE COALESCE(corpus,'main')='main' "
                "ORDER BY n_chunks DESC").fetchall()
            for r in rows:
                c = chat_mod.build_deck_context(conn0, int(r["id"]))
                if c.chunks_total:
                    args.doc = int(r["id"])
                    emit("  （自动选课件：doc#%d《%s》%d 块，truncated=%s；候选前 %d 份）"
                         % (args.doc, (r["title"] or "")[:24], c.chunks_total,
                            c.truncated, min(len(rows), 5)))
                    break
            conn0.close()
        if not args.doc:
            emit("★当前学科（%s）的库里没有可用课件，无法验证" % slug)
            return 2

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        fh = blog.open("wb")
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-u", str(ROOT / "tools" / "dashboard.py"),
             "--port", str(port), "--no-open"],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
        atexit.register(lambda: proc.terminate())
        for _ in range(80):
            time.sleep(0.4)
            if not get_json(port, "/api/stats").get("error"):
                break

        emit("=" * 78)
        emit("卡 C-0057 ★真浏览器验证（headless Edge + CDP，临时端口 %d）" % port)
        emit("  学科《%s》· 库 %s" % (slug, config.DB_PATH))
        emit("  目标课件 doc#%d · 临时实例 PID %s · 常驻 8756 不碰" % (args.doc, proc.pid))
        emit("=" * 78)
        h = get_json(port, "/api/stats")
        if h.get("error"):
            emit("★临时实例 %d 打不通：%s" % (port, h["error"]))
            return 2
    ok8756 = not get_json(8756, "/api/stats").get("error")
    emit("  （只读探活 8756：%s）" % ("可达" if ok8756 else "不可达"))

    # 1) 现场快照（学科库的目录，不是根目录）
    emit("\n[1] 快照现场")
    kdir = config.LOG_DIR / "knowledge"
    odir = config.LOG_DIR / "outline"
    ndir = config.NOTES_DIR
    dbf = config.DB_PATH
    ksnap0, osnap0, nsnap0 = snap(kdir), snap(odir), snap(ndir)
    dbhash0 = sha256(dbf) if dbf.is_file() else ""
    conn = ro_conn(dbf)                   # ★ 只读：本脚本自己不给库添字节
    five0 = five_counts(conn)
    docs0 = int(conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"])
    chunks0 = int(conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"])
    runs0 = int(conn.execute("SELECT COUNT(*) AS n FROM ingest_runs").fetchone()["n"])
    cfg = chat_mod.deck_cfg()
    conn.close()
    emit("  五表 %s · documents %d · chunks %d" % (five0, docs0, chunks0))
    emit("  本管线配置（core/config.py 的 DECK_CHAT_*）：%s"
         % json.dumps({k: cfg[k] for k in ("model", "reasoning_effort", "thinking",
                                           "max_context_tokens", "max_tokens",
                                           "figure_share")}, ensure_ascii=False))

    # 2) 真跑浏览器（重放模式：这一步整个跳过，用上次留下的探针 JSON）
    if args.replay:
        emit("\n[2] 重放模式：**不跑浏览器、不调模型**，直接判定上次的探针产物")
        emit("    （这条路径只为「断言改过之后重核同一批证据」而存在，本身不产生新证据）")
    else:
        emit("\n[2] 真跑浏览器（node tools\\probe_c0057_browser.mjs）")
        r = subprocess.run(
            ["node", str(ROOT / "tools" / "probe_c0057_browser.mjs"),
             "--port", str(port), "--doc", str(args.doc),
             "--q", args.q, "--kbq", args.kbq,
             "--outdir", str(SHOTS), "--json", str(jsout),
             "--skipai", "1" if args.skip_ai else ""],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=900)
        if r.returncode != 0:
            emit("  node 退出码 %s\n  stderr: %s"
                 % (r.returncode, (r.stderr or "")[-1500:]))
        D = json.loads(jsout.read_text(encoding="utf-8")) if jsout.exists() else {}
        if not D:
            emit("★探针没有产出 JSON")
            if proc:
                proc.terminate()
            return 1
    st = D.get("steps") or {}

    # 3) 课件页：按钮旁的说明 + 弹窗说明
    emit("\n[3] 断言：① 课件页明确写着走的是**独立管线**")
    hint = st.get("hint") or {}
    check(bool(hint.get("ok")) and hint.get("btn") == "AI答疑",
          "「AI答疑」按钮还在", str(hint.get("btn")))
    check("独立管线" in str(hint.get("hint")) and "不检索" in str(hint.get("hint"))
          and "不用提示词" in str(hint.get("hint")),
          "★按钮旁那行写明：独立管线 / 不检索 / 不用提示词",
          str(hint.get("hint"))[:90])
    pd = st.get("pickDoc") or {}
    check(bool(pd.get("ok")) and int(pd.get("docId") or 0) == args.doc,
          "选中目标课件 doc#%d" % args.doc, str(pd.get("label"))[:50])
    ao = st.get("aiOpen") or {}
    check(bool(ao.get("ok")), "点「AI答疑」弹出了对话窗口", str(ao.get("reason") or ""))
    check("不检索" in str(ao.get("head")) and "不用提示词" in str(ao.get("head"))
          and "思考强度" in str(ao.get("head")),
          "★窗口说明里写明三条（不检索 / 不用提示词 / 思考强度最高）",
          str(ao.get("head"))[:140].replace("\n", " "))

    # 4) 真对话 + 参数行
    emit("\n[4] 断言：② 真提一个问题 → 收到回答 → 参数如实显示")
    ak = st.get("aiAsk") or {}
    if args.skip_ai:
        emit("  （--skip-ai：跳过真实调用）")
    else:
        check(bool(ak.get("ok")) and int(ak.get("nAnswerChars") or 0) > 40,
              "★真调模型并流式收到回答",
              "%s 字：%s" % (ak.get("nAnswerChars"),
    runs1 = int(conn.execute("SELECT COUNT(*) AS n FROM ingest_runs").fetchone()["n"])
    conn.close()
    check(all(v == 0 for v in five1.values()),
          "★五表仍全 0 行", json.dumps(five1))
    check(five1 == five0 and docs1 == docs0 and chunks1 == chunks0,
          "documents / chunks 计数与跑前一致",
          "%d/%d → %d/%d" % (docs0, chunks0, docs1, chunks1))
    check(runs1 == runs0, "ingest_runs 未新增（没跑过任何入库）",
          "%d → %d" % (runs0, runs1))
    # 库**文件字节**：如实报告，不当硬判据。原因：本脚本与临时实例都会开库，
    # sqlite 打开可写连接 / WAL checkpoint 都可能移动文件字节，与本卡代码无关
    # ——本卡的零副作用由"五表+计数不变"与"代码里只有 SELECT"（离线脚本证）保证。
    dbhash1 = sha256(dbf) if dbf.is_file() else ""
    emit("  库文件 sha256：跑前 %s… / 跑后 %s…（%s）"
         % (dbhash0[:16], dbhash1[:16],
            "一致" if dbhash0 == dbhash1 else
            "文件字节有移动——开库/WAL 的正常现象，业务表与计数分毫未动"))
    emit("  8756 探活结果（跑完再确认一次）：%s"
         % ("可达" if not get_json(8756, "/api/stats").get("error") else "不可达"))

    # 9) 花费
    emit("\n[9] 花费（deepseek-flash，口径同 core/kp_build.py："
         "$%.2f/1M 入 + $%.2f/1M 出，汇率 %.1f）" % (PRICE_IN, PRICE_OUT, FX))
    total = 0.0
    for c in COST:
        total += float(c["cny"])
        emit("  · %s：输入 %s / 输出 %s（思考 %s）tokens → ¥%.4f"
             % (c["where"], c.get("prompt_tokens"), c.get("completion_tokens"),
                reasoning_tokens(c), c["cny"]))
    if not COST:
        emit("  （本次 --skip-ai，没有真实调用）")
    emit("  本次浏览器验证合计 ¥%.4f" % total)
    check(total <= 0.30, "★花费 ≤ ¥0.30", "实测 ¥%.4f" % total)

    emit("\n" + "=" * 78)
    emit("结果：%d 通过 / %d 失败" % (OK, BAD))
    emit("=" * 78)
    (CARD / "browser_verify.log").write_text("\n".join(LINES) + "\n", encoding="utf-8")
    (CARD / "browser_verify.json").write_text(json.dumps(
        {"ok": OK, "bad": BAD, "port": port, "doc": args.doc,
         "cost": COST, "cost_total_cny": round(total, 6),
         "config": {k: cfg[k] for k in ("model", "reasoning_effort", "thinking",
                                        "max_context_tokens", "max_tokens",
                                        "figure_share")}},
                                        "figure_share")}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    proc.terminate()
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())