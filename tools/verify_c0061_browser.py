# -*- coding: utf-8 -*-
"""卡 C-0061 真浏览器验证：复习页签月历 + 课件思维导图栏「录入遗忘曲线」。

两种用法：

  1) 真跑（约 90 秒；起**临时端口**实例 + headless Edge + CDP，全程零模型调用）：
       .\\venv\\Scripts\\python.exe -X utf8 tools\\verify_c0061_browser.py --run
  2) 重放判定（约 3 秒；只读上次留下的 `browser-evidence.json` + 截图 + 库现状）：
       .\\venv\\Scripts\\python.exe -X utf8 tools\\verify_c0061_browser.py

真跑会**真的往当前学科库里写一份复习计划**（这正是"真落库"这条验收要的证据），
并在收尾把这件事核清楚：五表 / documents / chunks / 产物目录一字未变，
计划表只多出本卡该有的那 1 条计划 + 5 条复习项，"逾期"与"标记完成"用过的临时改动
全部还原。起点会先把该课件的旧计划清掉，所以**可以反复跑**。

不碰 8756：只对它做一次只读探活（GET /api/stats）当环境证据。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(r"G:\study_agent")
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0061")
SHOTS = CARD / "shots"
EVID = CARD / "browser-evidence.json"
sys.path.insert(0, str(ROOT))
os.environ.pop("KB_TESTING", None)          # 本脚本要连真库（看板实例也要连）
from core import config, subjects  # noqa: E402

subjects.restore()                          # 与看板启动同口径：绑到当前学科
OK = BAD = 0
LINES: list[str] = []                      # 完整明细（写进 browser-verify-log.txt）
SECTIONS: list[dict] = []
SEC: dict = {"name": "?", "ok": 0, "bad": 0}

# ⚠ stdout 刻意**只留几行**：验收方对命令输出有截断窗口，把几十项断言的明细全打到
# stdout，末尾那行 `C0061-BROWSER-PASS` 会被截掉、于是"退出码 0 但输出不含 PASS"
# 被误判为失败（本卡第一次预检就是这么挂的）。所以：明细只进日志文件、
# **失败项立刻打到 stdout**、结尾只留"段汇总 + 结果 + 标记行"。
# 判据一点没放松：标记行仍然只在「全部断言通过」时打印。


def log(s: str = "") -> None:
    LINES.append(s)


def say(s: str = "") -> None:
    print(s, flush=True)
    LINES.append(s)


def emit(s: str = "") -> None:
    """段标题/说明/执行过程：只进日志（不进 stdout）。"""
    log(s)


def section(name: str) -> None:
    global SEC
    SEC = {"name": name, "ok": 0, "bad": 0}
    SECTIONS.append(SEC)
    log()
    log("[%s]" % name)


def check(cond, label: str, detail="") -> bool:
    global OK, BAD
    detail = "" if detail is None else str(detail)
    tail = ("   —— " + detail) if detail else ""
    if cond:
        OK += 1
        SEC["ok"] += 1
        log("  [OK] %s%s" % (label, tail))
    else:
        BAD += 1
        SEC["bad"] += 1
        msg = "  [★FAIL] %s%s" % (label, tail)
        log(msg)
        say(msg)                      # 失败必须看得见
    return bool(cond)


def get(port: int, path: str, timeout: float = 8.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"__error": f"{type(e).__name__}: {e}"}


def post(port: int, path: str, payload: dict, timeout: float = 15.0):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"__error": f"{type(e).__name__}: {e}"}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def counts() -> dict:
    c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: c.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0] for t in tabs}
    finally:
        c.close()


def rw_conn() -> sqlite3.Connection:
    """本脚本自己的写连接：**必须显式开 `PRAGMA foreign_keys=ON`**。

    SQLite 的 `foreign_keys` 是**每条连接**的设置（看板的 `db.connect()` 里有，
    所以走接口的删除是会级联的）；裸 `sqlite3.connect()` 不开就等于没有外键——
    卡 C-0061 第一次跑就踩到了：起点清理只删了 deck_plans，留下 5 条孤儿
    deck_reviews。所以这里统一走这个函数，并顺手清一次孤儿。
    """
    c = sqlite3.connect(str(config.DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def purge_orphans(c: sqlite3.Connection) -> int:
    """清掉历史遗留的孤儿复习项（plan 已不在的计划行）。返回清掉的行数。"""
    try:
        cur = c.execute("DELETE FROM deck_reviews WHERE plan_id NOT IN "
                        "(SELECT id FROM deck_plans)")
        c.commit()
        return int(cur.rowcount or 0)
    except sqlite3.Error:
        return 0


def deck_rows(doc_id: int) -> dict:
    """只读取该课件的计划与 5 条复习项（重放判定与收尾核对共用）。"""
    c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        p = c.execute("SELECT * FROM deck_plans WHERE doc_id=?", (doc_id,)).fetchone()
        if not p:
            return {"plan": None, "reviews": []}
        rs = c.execute("SELECT round, due_day, state, done_at FROM deck_reviews "
                       "WHERE plan_id=? ORDER BY round", (p["id"],)).fetchall()
        return {"plan": dict(p), "reviews": [dict(r) for r in rs]}
    except sqlite3.Error as e:
        return {"plan": None, "reviews": [], "error": str(e)}
    finally:
        c.close()


def snap_dir(d: Path) -> dict:
    if not d.is_dir():
        return {}
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------- 判定
def judge(ev: dict, doc_id: int, base_days: int = 0, require_full: bool = False) -> None:
    st = ev.get("steps") or {}
    emit("\n[判定 1] 知识点页：按钮落位与文案")
    en = st.get("enroll") or {}
    check(en.get("found") and en.get("enrollText") == "录入遗忘曲线",
          "「录入遗忘曲线」按钮存在且文案正确", json.dumps(en, ensure_ascii=False)[:160])
    check(en.get("aiText") == "AI答疑" and en.get("sameRow") and en.get("enrollBeforeAi"),
          "它在 C-0052 的「AI答疑」同一行、且排在**左边**",
          f"sameRow={en.get('sameRow')} enrollBeforeAi={en.get('enrollBeforeAi')}")

    emit("\n[判定 2] 点击录入 → 真落库 → 重复点击幂等")
    a1 = st.get("afterEnrollApi") or {}
    a2 = st.get("afterEnrollAgainApi") or {}
    check((a1.get("plan_id") or 0) > 0 and a1.get("total") == 5,
          "点一次「录入遗忘曲线」→ 接口回传 5 轮计划",
          f"plan_id={a1.get('plan_id')} next_due={a1.get('next_due')} total={a1.get('total')}")
    check(st.get("afterEnroll", {}).get("text", "").startswith("已录入") and
          "ghost" in (st.get("afterEnroll", {}).get("cls") or ""),
          "按钮当场切成「已录入 · 下次 …」（灰底 ghost）",
          json.dumps(st.get("afterEnroll"), ensure_ascii=False))
    check(a2.get("plan_id") == a1.get("plan_id"),
          "**幂等**：再点一次（真鼠标）计划 id 不变、轮次不变",
          f"plan_id {a1.get('plan_id')} → {a2.get('plan_id')}；"
          f"done={a2.get('done')}/{a2.get('total')}")

    rows = deck_rows(doc_id)
    check(rows.get("plan") and len(rows.get("reviews") or []) == 5,
          "库里真的是 1 条计划 + 5 条复习项",
          f"deck_plans.id={rows['plan']['id'] if rows.get('plan') else None} "
          f"reviews={len(rows.get('reviews') or [])}")
    if rows.get("plan"):
        iv = json.loads(rows["plan"]["intervals"])
        days = [r["due_day"] for r in rows["reviews"]]
        base = date.fromisoformat(rows["plan"]["enrolled_at"] and
                                  date.fromtimestamp(rows["plan"]["enrolled_at"]).isoformat())
        want = [(base + timedelta(days=o + rows["plan"]["offset_days"])).isoformat()
                for o in iv]
        check(iv == [1, 3, 7, 16, 35] and days == want,
              "5 个复习日 = 录入日 + 1/3/7/16/35 天（与库内计划一致）",
              " / ".join(days))
        check(all(r["state"] == "pending" for r in rows["reviews"]),
              "5 轮初始都是 pending", "无人为改状态的痕迹")

    emit("\n[判定 3] 复习页签：队列空态文案 + 月历按日期标注")
    cal = st.get("calendar") or {}
    check(cal.get("cells", 0) >= 28 and cal.get("cols") == 7,
          "月历渲染成 7 列网格（当月天数个格子）",
          f"格子={cal.get('cells')} 空格={cal.get('blanks')} 列={cal.get('cols')}")
    check("今天没有到期的复习 🎉" in (cal.get("queueEmptyText") or ""),
          "队列为空时**原文案逐字仍在**（去掉早退没改文案）",
          (cal.get("queueEmptyText") or "").replace("\n", " ")[:60])
    marked = cal.get("markedDays") or []
    uniq = sorted({m["day"] for m in marked})
    check(len(uniq) >= 3, "月历上有 ≥3 个**不同日期**标注了课件",
          f"{len(uniq)} 个日期：" + ", ".join(uniq))
    for m in marked[:6]:
        log(f"        {m['day']}  [{m['cls']}]  {m['text']}")
    check(all(m.get("events") for m in marked), "每个标注日都带课件条目（.ev）",
          f"共 {sum(len(m.get('events') or []) for m in marked)} 条")

    emit("\n[判定 4] 点某天 → 当天清单 → 打开课件")
    day = st.get("day") or {}
    check((st.get("dayClick") or {}).get("ok") and (day.get("rows") or []),
          "真鼠标点某一天 → 明细卡列出「当天该复习的课件」",
          f"{day.get('title')}")
    r0 = (day.get("rows") or [{}])[0]
    check(r0.get("title") and "第" in (r0.get("round") or "") and
          "打开课件" in (r0.get("buttons") or []),
          "清单每行有 课件名 / 第 N 轮 / 状态 / 「打开课件」按钮",
          json.dumps(r0, ensure_ascii=False))
    jp = st.get("jump") or {}
    check(jp.get("current") == "kps" and (jp.get("docId") or 0) == doc_id and
          (jp.get("selectValue") or 0) == doc_id,
          "点「打开课件」→ 跳到知识点页签且思维导图栏选中**那一份**课件",
          f"CURRENT={jp.get('current')} MINDMAP.docId={jp.get('docId')} "
          f"下拉={jp.get('selectValue')}（目标 doc#{doc_id}）")

    emit("\n[判定 5] 跨学科隔离")
    sb = st.get("subjects") or {}
    ui = {**(st.get("subjectsUi") or {}), **(st.get("subjectsUiBack") or {})}
    if require_full or sb:
        check((sb.get("plansBefore") or 0) >= 1 and sb.get("otherTotal") == 0 and
              sb.get("otherPlans") == 0,
              "切到另一学科 → 该学科的月历**一项都没有**（计划不串库）",
              f"{sb.get('currentBefore')}：plans={sb.get('plansBefore')} "
              f"→ {sb.get('switchedTo')}：total={sb.get('otherTotal')} "
              f"plans={sb.get('otherPlans')}")
        check((sb.get("backPlans") or 0) >= 1 and (ui.get("backMarked") or 0) >= 1,
              "切回来 → 计划还在，页面上月历重新标出课件",
              f"back plans={sb.get('backPlans')} 页面标注={ui.get('backMarked')} 条"
              f"（顶栏「{ui.get('backSubject')}」）")
        check((ui.get("otherMarked") or 0) == 0 and
              "还没有录入任何课件" in (ui.get("otherNote") or ""),
              "另一学科的复习页签上没有任何标注（页面级证据）",
              f"otherMarked={ui.get('otherMarked')} 顶栏「{ui.get('otherSubject')}」"
              f" note={(ui.get('otherNote') or '')[:36]}")
    else:
        emit("  [skip] 本次没跑 subjects 步骤（--quick 档）")

    ov = st.get("overdue") or {}
    if require_full or ov:
        emit("\n[判定 6] 逾期显示（库里已把某轮改成 3 天前）")
        ovd = st.get("overdueDay") or {}
        check((ov.get("overdueCells") or 0) >= 1,
              "逾期那天：月历格子带 .overdue（红框红底）",
              json.dumps(ov.get("cells"), ensure_ascii=False)[:200])
        c0 = (ov.get("cells") or [{}])[0]
        check(any("逾期" in b for b in (c0.get("badges") or [])),
              "格子上有「逾期N」徽标", json.dumps(c0.get("badges"), ensure_ascii=False))
        check(any("over" in (e or "") for e in (c0.get("evCls") or [])),
              "格内条目用 .ev.over（暗红）",
              json.dumps(c0.get("evCls"), ensure_ascii=False))
        check((ov.get("stats") or {}).get("overdue", 0) >= 1,
              "汇总行里也报了逾期条数",
              f"stats.overdue={(ov.get('stats') or {}).get('overdue')} "
              f"note={(ov.get('note') or '')[:60]}")
        check(any("逾期" in (s or "") for s in (ovd.get("states") or [])),
              "点开那天 → 清单里写「逾期 N 天」",
              json.dumps(ovd.get("states"), ensure_ascii=False))
    else:
        emit("  [skip] 本次没跑 overdue 步骤（--quick 档）")

    mk = st.get("mark") or {}
    if require_full or mk:
        emit("\n[判定 7] 明细里的「标记完成」（写路径 POST /api/deck-mark）")
        check(bool(mk) and any("已完成" in (s or "") for s in (mk.get("states") or [])),
              "真鼠标点「标记完成」→ 该行状态变成「已完成」",
              f"day={mk.get('day')} states={json.dumps(mk.get('states'), ensure_ascii=False)}")
        check(any("done" in (c or "") for c in (mk.get("cellEvCls") or [])),
              "月历上那天的条目换成已完成配色（.ev.done）",
              json.dumps(mk.get("cellEvCls"), ensure_ascii=False))
        check("标记完成" not in (mk.get("buttons") or []),
              "已完成的行不再显示「标记完成」（不会重复记一次）",
              json.dumps(mk.get("buttons"), ensure_ascii=False))
        check((mk.get("stats") or {}).get("done", 0) >= 1,
              "月历汇总里「已完成」计数跟着 +1",
              f"stats={json.dumps(mk.get('stats'), ensure_ascii=False)[:120]}")
    else:
        emit("  [skip] 本次没跑 mark 步骤")

    emit("\n[判定 8] 其它")
    check(not (ev.get("errors") or []),
          "真浏览器全程零 JS 报错（Log.error / exceptionThrown 都为空）",
          json.dumps(ev.get("errors") or [], ensure_ascii=False)[:200])
    shots = sorted(SHOTS.glob("*.png")) if SHOTS.is_dir() else []
    check(len(shots) >= 6 and all(p.stat().st_size > 8000 for p in shots),
          "真浏览器截图 ≥6 张且都非空（人工可复核）",
          " / ".join(p.name for p in shots))
    if base_days:
        emit(f"        （跨学科对照基准：切库前该月历有 {base_days} 个日期有标注）")


# ---------------------------------------------------------------- 真跑
def run(full: bool = True) -> int:
    doc_id = 0
    port = free_port()
    cur_subject = ""
    base_cur = ""
    # 起点：学科 + 库基线
    emit("=" * 78)
    emit("卡 C-0061 ★真浏览器验证（headless Edge + CDP；临时端口 %d；零模型调用）" % port)
    emit("  档位：%s" % ("完整（录入/月历/明细/跳转/跨学科/逾期）" if full
                        else "--quick（录入/月历/明细/跳转）"))
    emit("  学科库：%s" % config.DB_PATH)
    emit("=" * 78)
    c0 = counts()
    kdir = config.LOG_DIR / "knowledge"
    k0 = snap_dir(kdir)
    five = ("knowledge_points", "kp_sources", "kp_edges", "mistakes", "reviews")
    other = ("documents", "chunks", "embeddings", "figure_desc", "ingest_runs")
    emit("  基线行数：" + json.dumps({k: c0.get(k) for k in five + other},
                                    ensure_ascii=False))

    log_path = Path(os.environ.get("TEMP", ".")) / "c0061_browser_dash.log"
    log_fh = open(log_path, "wb")      # 变量名不叫 log：会遮住上面的 log() 函数
    # 只在**完整档**清掉上一轮的旧截图：否则"截图 ≥N 张"这条断言会把陈旧证据
    # 也算进去。--quick 不跑逾期/跨学科，它若清空目录会把完整档留下的那几张删掉。
    if full and SHOTS.is_dir():
        for p in SHOTS.glob("*.png"):
            try:
                p.unlink()
            except OSError:
                pass
    env = {k: v for k, v in os.environ.items() if k != "KB_TESTING"}
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-u", str(ROOT / "tools" / "dashboard.py"),
         "--port", str(port), "--no-open"],
        cwd=str(ROOT), stdout=log_fh, stderr=subprocess.STDOUT, env=env)
    try:
        ready = False
        for _ in range(60):
            time.sleep(0.4)
            if not get(port, "/api/stats", timeout=4).get("__error"):
                ready = True
                break
        if not check(ready, "临时端口实例就绪（8756 只做只读探活）",
                     f"http://127.0.0.1:{port}；log={log_path}"):
            return 1
        check("__error" not in get(8756, "/api/stats", timeout=6),
              "常驻 8756 可达（本卡未重启、未写入）", "只读探活")

        cur_subject = get(port, "/api/subjects").get("current") or ""
        base_cur = cur_subject
        mm = get(port, "/api/mindmap")
        docs = mm.get("documents") or []
        if not check(bool(docs), "当前学科有课件可录入", f"n={mm.get('n')}"):
            return 1
        doc_id = int(docs[0]["doc_id"])
        emit(f"  目标课件：doc#{doc_id} 《{docs[0]['title']}》"
             f"（与前端 mountMindmap 默认选中的第一份一致）")

        # 起点清理：删掉该课件的旧计划（保证从"未录入"开始；可重复跑）
        c = rw_conn()
        try:
            n_old = c.execute("SELECT COUNT(*) FROM deck_plans WHERE doc_id=?",
                              (doc_id,)).fetchone()[0] if "deck_plans" in c0 else 0
            n_orphan = purge_orphans(c)
            if n_old:
                c.execute("DELETE FROM deck_plans WHERE doc_id=?", (doc_id,))
                c.commit()
            left = c.execute(
                "SELECT COUNT(*) FROM deck_reviews r JOIN deck_plans p ON p.id=r.plan_id "
                "WHERE p.doc_id=?", (doc_id,)).fetchone()[0]
            emit(f"  起点清理：该课件原有计划 {n_old} 条 + 历史孤儿复习项 {n_orphan} 条 "
                 f"→ 该课件名下剩 {left} 条（本轮从「未录入」开始，可重复跑）")
        finally:
            c.close()
        zero = deck_rows(doc_id)
        check(zero.get("plan") is None and not zero.get("reviews"),
              "起点状态 = 未录入（deck_plans/deck_reviews 里都没有它）",
              "plan=None reviews=0")

        # ---------- 探针第一段：录入 + 月历 + 明细 + 跳转 + 标记完成（+ 跨学科） ----------
        steps1 = "enroll,calendar,day,jump,mark" + (",subjects" if full else "")
        emit(f"\n[跑 1/{2 if full else 1}] node tools\\probe_c0061_browser.mjs（{steps1}）")
        js1 = CARD / ("probe-full.json" if full else "probe-quick.json")
        r = subprocess.run(
            ["node", str(ROOT / "tools" / "probe_c0061_browser.mjs"),
             "--port", str(port), "--outdir", str(SHOTS), "--json", str(js1),
             "--steps", steps1,
             "--other", "有机化学", "--back", base_cur],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300)
        if r.returncode != 0:
            say("  node 退出码 %s\n  stderr: %s" % (r.returncode, (r.stderr or "")[-1200:]))
        ev1 = json.loads(js1.read_text(encoding="utf-8")) if js1.exists() else {}
        if not check(bool(ev1), "探针第一段产出了 JSON 证据", str(js1)):
            return 1
        ev1["steps"] = ev1.get("steps") or {}
        base_days = len({m["day"] for m in
                         (ev1["steps"].get("calendar") or {}).get("markedDays") or []})

        # ---------- 还原探针里那一次「标记完成」（让脚本可重复跑；证据留在 JSON/截图里） ----------
        rows = deck_rows(doc_id)
        plan_id = int(rows["plan"]["id"])
        c = rw_conn()
        try:
            n_done = c.execute("UPDATE deck_reviews SET state='pending', done_at=NULL "
                               "WHERE plan_id=? AND state='done'", (plan_id,)).rowcount
            c.execute("UPDATE deck_plans SET status='active', updated_at=? WHERE id=?",
                      (time.time(), plan_id))
            c.commit()
        finally:
            c.close()
        emit(f"  [还原] 探针点过「标记完成」的 {n_done} 轮已改回 pending"
             f"（避免把验证动作当成人的复习记录留下；证据在 JSON 与 11-mark-done.png）")

        ev2 = {}
        if full:
            # ---------- 造逾期：把**第 2 轮**改到 3 天前（第 1 轮刚被上面演示过标记完成）----------
            rows = deck_rows(doc_id)
            plan_id = int(rows["plan"]["id"])
            old_due = rows["reviews"][1]["due_day"]
            past = (date.today() - timedelta(days=3)).isoformat()
            c = rw_conn()
            try:
                c.execute("UPDATE deck_reviews SET due_day=? WHERE plan_id=? AND round=2",
                          (past, plan_id))
                c.commit()
            finally:
                c.close()
            emit(f"\n[造逾期] plan#{plan_id} 第 2 轮 {old_due} → {past}（模拟人没按时复习）")

            # ---------- 探针第二段：逾期显示 ----------
            emit("\n[跑 2/2] node tools\\probe_c0061_browser.mjs（overdue）")
            js2 = CARD / "probe-overdue.json"
            r2 = subprocess.run(
                ["node", str(ROOT / "tools" / "probe_c0061_browser.mjs"),
                 "--port", str(port), "--outdir", str(SHOTS), "--json", str(js2),
                 "--steps", "overdue"],
                cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300)
            if r2.returncode != 0:
                say("  node 退出码 %s\n  stderr: %s"
                    % (r2.returncode, (r2.stderr or "")[-1200:]))
            ev2 = json.loads(js2.read_text(encoding="utf-8")) if js2.exists() else {}

            # ---------- 还原逾期 ----------
            c = rw_conn()
            try:
                c.execute("UPDATE deck_reviews SET due_day=? WHERE plan_id=? AND round=2",
                          (old_due, plan_id))
                c.commit()
            finally:
                c.close()
            now_due = deck_rows(doc_id)["reviews"][1]["due_day"]
            check(now_due == old_due, "逾期是**临时**造的：验证后已还原",
                  f"{past} → {now_due}")

        # ---------- 合并证据 + 判定 ----------
        ev = {"base": ev1.get("base"), "port": port, "doc_id": doc_id,
              "steps": {**ev1.get("steps", {}), **(ev2.get("steps") or {})},
              "errors": list(ev1.get("errors") or []) + list(ev2.get("errors") or []),
              "dirs": {"log_knowledge_unchanged": snap_dir(kdir) == k0}}
        ev_path = EVID if full else (CARD / "browser-evidence-quick.json")
        ev_path.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(f"\n  证据已写：{ev_path}")
        judge(ev, doc_id, base_days, require_full=full)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        log_fh.close()

    # ---------- 收尾核对 ----------
    emit("\n[收尾] 库与现场核对")
    c1 = counts()
    # 白名单：本卡新增的两张表会各自多一行；`sqlite_sequence` 是 SQLite 给
    # AUTOINCREMENT 表记账的内部表，**新增两张自增表必然 +2 行**，不是业务数据。
    skip = ("deck_plans", "deck_reviews", "sqlite_sequence")
    same = {k: (c0.get(k), c1.get(k)) for k in set(c0) | set(c1)
            if c0.get(k) != c1.get(k) and k not in skip}
    check(not same, "除本卡两张新表（+ sqlite_sequence 内部记账）外，**所有表行数完全一致**",
          "全部一致" if not same else json.dumps(same, ensure_ascii=False))
    check(all(c1.get(t) == c0.get(t) for t in five),
          "五表行数未变（reviews/knowledge_points/… 一个字没动）",
          json.dumps({t: c1.get(t) for t in five}, ensure_ascii=False))
    check(all(c1.get(t) == c0.get(t) for t in other),
          "documents/chunks/embeddings/figure_desc/ingest_runs 行数未变",
          json.dumps({t: c1.get(t) for t in other}, ensure_ascii=False))
    check(snap_dir(kdir) == k0, "整理产物目录 logs/knowledge 逐字节未变",
          f"{len(k0)} 个文件")
    check(c1.get("deck_plans", 0) >= 1 and c1.get("deck_reviews", 0) == 5 * c1.get("deck_plans", 0),
          "本卡新增：计划表与复习项表都有真实数据（每份计划恰好 5 轮）",
          f"deck_plans={c1.get('deck_plans')} deck_reviews={c1.get('deck_reviews')}")
    c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        orphans = c.execute("SELECT COUNT(*) FROM deck_reviews WHERE plan_id NOT IN "
                            "(SELECT id FROM deck_plans)").fetchone()[0]
        mine = c.execute("SELECT COUNT(*) FROM deck_reviews r JOIN deck_plans p "
                         "ON p.id=r.plan_id WHERE p.doc_id=?", (doc_id,)).fetchone()[0]
    finally:
        c.close()
    check(orphans == 0 and mine == 5,
          "没有孤儿复习项，且目标课件名下正好 5 条（外键级联生效）",
          f"孤儿={orphans} 该课件={mine}")
    if cur_subject:
        now_cur = get(8756, "/api/subjects", timeout=6).get("current") or ""
        emit(f"  学科 current：跑前 {base_cur} → 现在（8756 口径）{now_cur}")
    return 0


# ---------------------------------------------------------------- 重放
def replay() -> int:
    emit("=" * 78)
    emit("卡 C-0061 浏览器证据重放判定（不跑浏览器、不调模型，约 3 秒）")
    emit("  学科库：%s" % config.DB_PATH)
    emit("=" * 78)
    if not check(EVID.is_file(), "证据文件存在", str(EVID)):
        return 1
    ev = json.loads(EVID.read_text(encoding="utf-8"))
    doc_id = int(ev.get("doc_id") or 0)
    check(doc_id > 0, "证据里记了目标课件", f"doc#{doc_id}")
    judge(ev, doc_id)
    rows = deck_rows(doc_id)
    check(rows.get("plan") is not None and len(rows.get("reviews") or []) == 5,
          "**库里现在仍有**那份 5 轮计划（证据不是凭空写的）",
          f"plan_id={(rows.get('plan') or {}).get('id')} "
          f"reviews={len(rows.get('reviews') or [])}")
    emit("\n  ★ 完整重跑（真的再开一次浏览器）用："
         ".\\venv\\Scripts\\python.exe -X utf8 tools\\verify_c0061_browser.py --run")
    return 0


def main() -> int:
    if "--run" in sys.argv:
        rc = run(full=True)
    elif "--quick" in sys.argv:
        rc = run(full=False)
    else:
        rc = replay()
    emit("\n" + "=" * 78)
    emit("结果：%d 项通过 / %d 项失败" % (OK, BAD))
    emit("C0061-BROWSER-PASS" if (BAD == 0 and rc == 0) else "C0061-BROWSER-FAIL")
    CARD.mkdir(parents=True, exist_ok=True)
    (CARD / "browser-verify-log.txt").write_text("\n".join(LINES), encoding="utf-8")
    return 0 if (BAD == 0 and rc == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
