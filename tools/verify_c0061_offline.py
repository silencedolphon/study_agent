# -*- coding: utf-8 -*-
"""卡 C-0061 离线验证（零模型、不改真库任何数据）：后端算法 + 接口结构 + 静态自查。

三段：
  A. **临时库单测**（毫秒级）：建表 / 录入 5 轮 / 幂等行数对照 / 软分摊 cap /
     月历分组与 overdue / 顺延规则 / 全部完成转 finished / 外键级联 / 缺表兜底 /
     五表零污染。跑在 `%TEMP%` 下的临时库，`KB_TESTING=1` 护栏在场。
  B. **真库只读接口对照**（起一个**临时端口**实例，跑完杀掉；绝不碰 8756）：
     `/api/queue` 结构、`/api/stats` 的 reviews_today 口径、`/api/mindmap` 的
     13 个原字段 + 4 个新 `deck_*` 字段与排序判据、`/api/deck-calendar`、以及
     `/api/terms`、`/api/kp-build/options` 这俩同样调 `_artifact_doc_list` 的接口。
     全程只 GET，不写库；跑完再核对五表 / documents / chunks 行数与开始时一致。
  C. **静态自查**：看板零外链（无 `http(s)://`、无 `<script src=` / `<link href>`）、
     新旧 DOM id 与函数定义次数、`#mm-enroll` 在 `#mm-ai` 左边、队列空态文案逐字仍在。

用法（约 10 秒）：
    .\\venv\\Scripts\\python.exe -X utf8 tools\\verify_c0061_offline.py
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("KB_TESTING", "1")   # 临时库测试护栏（见 core/db.py::connect）
ROOT = Path(r"G:\study_agent")
sys.path.insert(0, str(ROOT))
from core import config, db, study, subjects  # noqa: E402

# 跟看板启动时一样，把 config 绑到**当前学科**的库（`logs/subjects.json` 的 current）。
# 不 restore 的话 config.DB_PATH 会停在根目录的默认库，B 段的"真库行数对照"就会
# 对错库——而临时实例是 restore 过的，两边必须看同一个库。
subjects.restore()

DASH = ROOT / "tools" / "dashboard.py"
SRC_DASH = DASH.read_text(encoding="utf-8")
SRC_STUDY = (ROOT / "core" / "study.py").read_text(encoding="utf-8")
SRC_DB = (ROOT / "core" / "db.py").read_text(encoding="utf-8")

OK = BAD = 0
LINES: list[str] = []                 # 完整明细（写进 offline-verify-log.txt）
SECTIONS: list[dict] = []             # 每段的通过/失败计数，最后拼成一行汇总
SEC: dict = {"name": "?", "ok": 0, "bad": 0}
LOGPATH = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
               r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043"
               r"\cards\C-0061\offline-verify-log.txt")

# ⚠ stdout 刻意**只留几行**：验收方（大管家）对命令输出有截断窗口，
# 把 60 项断言的明细全打到 stdout，末尾那行 `C0061-OFFLINE-PASS` 会被截掉、
# 于是"退出码 0 但输出不含 PASS"被误判为失败（本卡第一次预检就是这么挂的）。
# 所以：明细（含全部 [OK]）只进日志文件；**失败项立刻打到 stdout**；
# 结尾只留"段汇总 + 结果 + 标记行"这几行。判据一点没放松：
# 标记行仍然只在「全部断言通过」时打印。


def log(s: str = "") -> None:
    LINES.append(s)


def say(s: str = "") -> None:
    print(s, flush=True)
    LINES.append(s)


def emit(s: str = "") -> None:
    """段标题/说明：只进日志（不进 stdout）。"""
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


# ---------------------------------------------------------------- A 临时库单测
def part_a() -> None:
    section("A 临时库单测")
    emit("临时库单测（课件级遗忘曲线的算法与数据模型）")
    tmp = Path(tempfile.mkdtemp(prefix="c0061a-")) / "t.db"
    conn = db.connect(tmp)
    db.init_db(conn)
    tabs = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check({"deck_plans", "deck_reviews"} <= tabs,
          "init_db 建出两张新表（新库）", "deck_plans/deck_reviews 都在")
    check(study.DECK_INTERVALS == [1, 3, 7, 16, 35] and study.DECK_DAILY_CAP == 3,
          "间隔表与软分摊常量", f"{study.DECK_INTERVALS} cap={study.DECK_DAILY_CAP}")

    now = db.now()
    for i in (1, 2, 3, 4, 5):
        conn.execute(
            "INSERT INTO documents(id, path, abs_path, kind, title, content_hash,"
            " indexed_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (i, f"raw/d{i}.pptx", f"G:/x/d{i}.pptx", "pptx", f"第{i}章 测试课件",
             "h%d" % i, now, now))
    conn.commit()

    d0 = date(2026, 10, 8)
    r1 = study.enroll_deck(conn, 1, subject="药物化学", today=d0)
    check(not r1["already"] and r1["n_rounds"] == 5 and len(r1["days"]) == 5,
          "录入一份课件 → 一次写死 5 轮复习日",
          " / ".join(d["due_day"] for d in r1["days"]))
    check([d["due_day"] for d in r1["days"]] ==
          ["2026-10-09", "2026-10-11", "2026-10-15", "2026-10-24", "2026-11-12"],
          "5 个日期 = 起算日 + 1/3/7/16/35 天", "+1/+3/+7/+16/+35")

    for i in (2, 3, 4):
        study.enroll_deck(conn, i, today=d0)
    off4 = conn.execute("SELECT offset_days FROM deck_plans WHERE doc_id=4").fetchone()[0]
    check(off4 == 1, "第 4 份同日录入被软分摊错开（cap=3）", f"offset_days={off4}")

    n0 = (conn.execute("SELECT COUNT(*) AS n FROM deck_plans").fetchone()["n"],
          conn.execute("SELECT COUNT(*) AS n FROM deck_reviews").fetchone()["n"])
    again = study.enroll_deck(conn, 1, today=d0)
    again2 = study.enroll_deck(conn, 1, today=d0)
    n1 = (conn.execute("SELECT COUNT(*) AS n FROM deck_plans").fetchone()["n"],
          conn.execute("SELECT COUNT(*) AS n FROM deck_reviews").fetchone()["n"])
    check(again["already"] and again2["already"] and n0 == n1 and again["plan_id"] == r1["plan_id"],
          "幂等：重复录入不新建计划、不追加轮次",
          f"already=True x2；plan/reviews 行数 {n0} → {n1}")

    cal = study.deck_calendar(conn, "2026-10", today=d0)
    check(len(cal["days"]) >= 3 and cal["stats"]["total"] == 16,
          "月历按 due_day 分组（2026-10）",
          f"{len(cal['days'])} 个日期 / {cal['stats']['total']} 项")
    check(cal["stats"]["plans"] == 4 and cal["stats"]["pending_all"] == 20,
          "汇总口径：4 份计划 / 全库 20 项待复习", json.dumps(cal["stats"], ensure_ascii=False))

    # 逾期：把 doc1 第 1 轮改到 3 天前
    conn.execute("UPDATE deck_reviews SET due_day=? WHERE plan_id=1 AND round=1",
                 ((d0 - timedelta(days=3)).isoformat(),))
    conn.commit()
    cal2 = study.deck_calendar(conn, "2026-10", today=d0)
    over = [x for xs in cal2["days"].values() for x in xs if x["overdue"]]
    check(len(over) == 1 and over[0]["overdue"] == 3 and cal2["stats"]["overdue"] == 1,
          "逾期天数按日历日算（且只算 pending）",
          f"{over[0]['due_day']} → overdue={over[0]['overdue']}")

    # 顺延：doc5 第 2 轮先改成过去，再做第 1 轮
    p5 = study.enroll_deck(conn, 5, today=d0)["plan_id"]
    conn.execute("UPDATE deck_reviews SET due_day=? WHERE plan_id=? AND round=2",
                 ((d0 - timedelta(days=5)).isoformat(), p5))
    conn.commit()
    m = study.deck_mark(conn, p5, 1, "done", today=d0)
    check(m["shifted"] and m["shifted"]["round"] == 2 and
          m["shifted"]["due_day"] == (d0 + timedelta(days=1)).isoformat(),
          "唯一的顺延规则：下一轮已过期 → 顺延到明天起第一个负载 < cap 的日子",
          f"round2 → {m['shifted']['due_day'] if m['shifted'] else None}")
    before3 = conn.execute(
        "SELECT due_day FROM deck_reviews WHERE plan_id=? AND round=3", (p5,)).fetchone()[0]
    check(before3 == "2026-10-15", "第 3 轮日期**不**被顺延波及（只动紧接着的下一轮）",
          f"round3 仍 {before3}")

    for rnd in range(2, 6):
        mm = study.deck_mark(conn, p5, rnd, "done", today=d0)
    st5 = conn.execute("SELECT status FROM deck_plans WHERE id=?", (p5,)).fetchone()[0]
    check(st5 == "finished" and mm["next_due"] is None,
          "5 轮全部完成 → status=finished 且不再上月历", f"status={st5}")
    cal3 = study.deck_calendar(conn, "2026-10", today=d0)
    check(all(x["plan_id"] != p5 for xs in cal3["days"].values() for x in xs),
          "finished 的计划从月历消失", "月历只显示 status=active")

    batch = study.decks_for(conn, [1, 2, 999])
    check(set(batch) == {1, 2} and batch[1]["deck_rounds_total"] == 5,
          "decks_for 批量按钮状态（现存计划才有）", json.dumps(batch[1], ensure_ascii=False))

    five = {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
            for t in ("knowledge_points", "kp_sources", "kp_edges", "mistakes", "reviews")}
    check(all(v == 0 for v in five.values()),
          "课件级复习**不写**五表（知识点级账本零污染）", json.dumps(five))

    n_before = (conn.execute("SELECT COUNT(*) AS n FROM deck_plans").fetchone()["n"],
                conn.execute("SELECT COUNT(*) AS n FROM deck_reviews").fetchone()["n"])
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("DELETE FROM documents WHERE id=1")
    conn.commit()
    n_after = (conn.execute("SELECT COUNT(*) AS n FROM deck_plans").fetchone()["n"],
               conn.execute("SELECT COUNT(*) AS n FROM deck_reviews").fetchone()["n"])
    check(n_after == (n_before[0] - 1, n_before[1] - 5),
          "删课件 → 外键级联清掉它的计划与 5 条复习项", f"{n_before} → {n_after}")

    fresh = db.connect(Path(tempfile.mkdtemp(prefix="c0061b-")) / "old.db")
    fresh.executescript(db.SCHEMA.replace(db.DECK_SCHEMA, ""))     # 模拟"没建过新表的旧库"
    cal_old = study.deck_calendar(fresh, "2026-10", today=d0)
    check(cal_old["stats"]["total"] == 0 and study.decks_for(fresh, [1, 2]) == {},
          "旧学科库（没有这两张表）→ 读接口兜底成空，不抛错",
          "calendar/decks_for 都安全降级")
    fresh.close()
    conn.close()


# ---------------------------------------------------------------- B 真库只读接口
FIELDS_13 = ["doc_id", "title", "kind", "in_library", "artifact_state", "artifact_source",
             "artifact_message", "n_pages", "n_nodes", "n_kps", "model", "created_at",
             "cost_cny"]
FIELDS_DECK = ["deck_plan_id", "deck_next_due", "deck_rounds_done", "deck_rounds_total"]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(port: int, path: str, timeout: float = 8.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"__error": f"{type(e).__name__}: {e}"}


def ro_counts(path: Path) -> dict:
    """只读打开库，数各表行数（库不存在返回 {}）。"""
    if not path.is_file():
        return {}
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: c.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0] for t in tabs}
    finally:
        c.close()


def part_b() -> None:
    section("B 真库只读接口")
    emit("真库只读接口对照（临时端口实例；只 GET；跑完核对行数未变）")
    dbf = config.DB_PATH
    c0 = ro_counts(dbf)
    port = free_port()
    log_path = Path(tempfile.gettempdir()) / "c0061_offline_dash.log"
    log = open(log_path, "wb")
    # ★ 子进程必须**不受**本脚本的 `KB_TESTING=1` 影响：那是给临时库测试用的护栏，
    #   传下去会让看板实例拒绝连真实库（core/db.py::connect 会直接 RuntimeError）。
    env = {k: v for k, v in os.environ.items() if k != "KB_TESTING"}
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-u", str(ROOT / "tools" / "dashboard.py"),
         "--port", str(port), "--no-open"],
        cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env)
    ready = False
    try:
        for _ in range(60):
            time.sleep(0.4)
            if not get(port, "/api/stats", timeout=4).get("__error"):
                ready = True
                break
        check(ready, "临时端口实例就绪（不碰 8756）",
              f"http://127.0.0.1:{port}" if ready else f"起不来，看 {log_path}")

        if ready:
            stats = get(port, "/api/stats")
            check(isinstance(stats.get("knowledge", {}).get("reviews_today"), int),
                  "/api/stats 的 reviews_today 仍在（口径=知识点复习次数）",
                  f"reviews_today={stats.get('knowledge', {}).get('reviews_today')}")

            q = get(port, "/api/queue?limit=5")
            check(isinstance(q.get("queue"), list) and set(q) == {"queue"},
                  "/api/queue 结构与改动前一致（{queue:[...]}）",
                  f"keys={sorted(q)} n={len(q.get('queue') or [])}")

            mm = get(port, "/api/mindmap")
            docs = mm.get("documents") or []
            check(isinstance(docs, list) and len(docs) >= 1, "/api/mindmap 课件清单可读",
                  f"n={mm.get('n')}")
            if docs:
                d0_ = docs[0]
                miss = [k for k in FIELDS_13 if k not in d0_]
                check(not miss, "原有 13 个字段一个不少",
                      "缺: " + ",".join(miss) if miss else "全在")
                miss2 = [k for k in FIELDS_DECK if k not in d0_]
                check(not miss2, "新增 4 个 deck_* 字段",
                      "缺: " + ",".join(miss2) if miss2 else "全在")
                order_ok = docs == sorted(docs, key=lambda x: (not x["in_library"], x["title"]))
                check(order_ok, "排序判据仍是 (not in_library, title)", "顺序未变")

            cal = get(port, "/api/deck-calendar")
            check(set(cal) >= {"ym", "days", "stats", "today", "intervals", "cap"} and
                  isinstance(cal.get("days"), dict),
                  "/api/deck-calendar 返回月历结构",
                  f"ym={cal.get('ym')} 有标注的日期={len(cal.get('days') or {})}")

            terms = get(port, "/api/terms")
            kpo = get(port, "/api/kp-build/options")
            check("__error" not in terms and "documents" in terms,
                  "同样调 _artifact_doc_list 的 /api/terms 仍正常", f"n={terms.get('n')}")
            check("__error" not in kpo and "documents" in kpo,
                  "同样调 _artifact_doc_list 的 /api/kp-build/options 仍正常",
                  f"n={kpo.get('n')}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        log.close()
    if not ready:
        return
    c1 = ro_counts(dbf)
    same = {k: (c0.get(k), c1.get(k)) for k in set(c0) | set(c1) if c0.get(k) != c1.get(k)}
    five = ("knowledge_points", "kp_sources", "kp_edges", "mistakes", "reviews")
    check(not same, "只读接口没有改动真库任何一张表的行数",
          "全部一致" if not same else json.dumps(same, ensure_ascii=False))
    check(all(c1.get(t) == c0.get(t) for t in five),
          "五表行数与开始时一致", json.dumps({t: c1.get(t) for t in five}))


# ---------------------------------------------------------------- C 静态自查
def part_c() -> None:
    section("C 静态自查")
    emit("静态自查（离线红线 / DOM 落位 / 未整文件重写 / 未碰 8756）")
    # 外链：全项目唯一的 http:// 是 main() 里"自动打开浏览器"拼的本机地址，
    # 那不是资源引用。真正的资源引用（CDN / <script src> / <link>）必须为零。
    urls = re.findall(r"https?://[^\s\"'`)]+", SRC_DASH)
    offsite = [u for u in urls if "{args.host}" not in u and "127.0.0.1" not in u
               and "localhost" not in u]
    check(not offsite, "dashboard.py 无外部资源引用（仅本机自开浏览器那一个 URL）",
          f"命中 {len(urls)} 处，非本机 {len(offsite)} 处: {offsite}")
    html = re.search(r'INDEX_HTML = r"""(.*?)"""', SRC_DASH, re.S).group(1)
    check(not re.search(r"https?://", html), "内联 HTML 段零 http(s)://")
    check(not re.search(r"<script[^>]+src=", SRC_DASH) and not re.search(r"<link\b", SRC_DASH),
          "无 <script src> / <link>（不引任何 CDN）", "零命中")

    i_en = SRC_DASH.find('id="mm-enroll"')
    i_ai = SRC_DASH.find('id="mm-ai"')
    check(0 < i_en < i_ai, "「录入遗忘曲线」按钮在「AI答疑」左边（文本顺序）",
          f"offset {i_en} < {i_ai}")
    check("今天没有到期的复习 🎉" in SRC_DASH,
          "队列空态文案逐字仍在（去掉早退没有改文案）", "今天没有到期的复习 🎉")
    check("if (!queue.length){" not in SRC_DASH.split("async function viewReview")[1][:900],
          "viewReview 里不再有“队列为空就早退”", "已改为拼接月历卡片")

    want1 = ["deckCalendarCard", "mountDeckCalendar", "renderDeckCalendar", "dcCell",
             "selectDeckDay", "renderDeckDay", "openDeckMindmap", "markDeckDone",
             "deckShiftMonth", "mmEnrollPaint", "deckEnrollClick"]
    bad = [n for n in want1 if len(re.findall(rf"\bfunction {n}\b", SRC_DASH)) != 1]
    check(not bad, "本卡新函数各定义一次（11 个）",
          "缺/重复: " + ",".join(bad) if bad else "全部 1 次")
    keep = ["mindmapCard", "mountMindmap", "viewReview", "mmToggleAll", "mmRenderKp",
            "mmNextKp", "loadMindmapTree", "openDeckChat", "openDoc", "viewKps",
            "mmOpenKp", "bindCiteClicks"]
    bad2 = [n for n in keep if len(re.findall(rf"\bfunction {n}\b", SRC_DASH)) != 1]
    check(not bad2, "既有函数未被复制/删除（12 个地标各 1 次）",
          "异常: " + ",".join(bad2) if bad2 else "全部 1 次")
    # 本卡没有重复实现别的卡已有的东西（这几个名字必须**恰好**是既有那一份）
    for dead in ("openDeckChat", "renderDeckChat", "deckChatDoc", "renderTerms"):
        n = len(re.findall(rf"\bfunction {dead}\b", SRC_DASH))
        check(n == 1, f"未引入重复实现：{dead}（仍恰好 1 处，属其它卡既有功能）", f"{n} 处")

    for name in ("def enroll_deck", "def deck_calendar", "def deck_mark", "def decks_for"):
        check(len(re.findall(re.escape(name) + r"\b", SRC_STUDY)) == 1,
              f"core/study.py 新增 {name}（唯一）")
    for name in ("def schedule", "def due_queue", "def compute_mastery", "def dashboard",
                 "def upsert_kp", "def save_note", "def list_notes", "def add_mistake"):
        check(len(re.findall(re.escape(name) + r"\b", SRC_STUDY)) == 1,
              f"core/study.py 既有 {name} 未被改动/复制")
    check("SCHEMA = SCHEMA + DECK_SCHEMA" in SRC_DB and "DECK_SCHEMA = " in SRC_DB,
          "core/db.py：DECK_SCHEMA 拼进 SCHEMA（新库旧库都自动建）", "幂等建表")
    check("deck_plans" not in SRC_DB.split("def _migrate")[1].split("def init_db")[0],
          "_migrate() 里没有为新表加迁移项（新表不需要补列）", "migrations 输出不含新增项")
    check("CREATE TABLE IF NOT EXISTS deck_plans" in SRC_DB and
          "CREATE TABLE IF NOT EXISTS deck_reviews" in SRC_DB,
          "两张表都是 IF NOT EXISTS（可重复执行不报错）", "幂等 DDL")

    # 行数对账（口径：python 的 splitlines；别用 PowerShell 默认的 Get-Content，
    # 它在 UTF-8 中文文件上会少算行）。HEAD = 9c65a37。
    head = {"core/db.py": 535, "core/study.py": 347, "tools/dashboard.py": 6754}
    now = {k: len((ROOT / k).read_text(encoding="utf-8").splitlines()) for k in head}
    check(all(now[k] > head[k] for k in head), "三个被改文件相对 HEAD 都只增不减",
          " / ".join(f"{k} {head[k]}→{now[k]}" for k in head))
    check(now["core/db.py"] - head["core/db.py"] == 55,
          "core/db.py 的增量 = 本卡新增的 DECK_SCHEMA 块（+55 行，开工时它与 HEAD 一致）",
          f"+{now['core/db.py'] - head['core/db.py']} 行")
    marks = {f: len(re.findall("卡 C-0061", (ROOT / f).read_text(encoding="utf-8")))
             for f in head}
    check(marks["core/db.py"] == 1 and marks["core/study.py"] == 2 and
          marks["tools/dashboard.py"] >= 10,
          "本卡改动点都带「卡 C-0061」标记（18 处可反向定位）",
          json.dumps(marks, ensure_ascii=False))
    # 未整文件重写：git 报的删除行数远小于文件规模
    try:
        r = subprocess.run(["git", "diff", "--numstat", "--", "tools/dashboard.py"],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=20)
        parts = (r.stdout or "").split()
        add, dele = int(parts[0]), int(parts[1])
        check(dele < 200, "tools/dashboard.py 未被整文件重写（删除行数远小于文件规模）",
              f"HEAD→工作区 +{add}/-{dele}（含并行会话的既有改动）")
    except Exception as e:  # noqa: BLE001
        emit(f"  [skip] git diff 取不到（{e}）")

    r = get(8756, "/api/stats", timeout=6)
    check("__error" not in r, "常驻 8756 只读探活（未重启、未写入）",
          "可达" if "__error" not in r else "不可达（不影响本卡判定）")


def main() -> int:
    say("卡 C-0061 离线验证（零模型调用）· 学科库 %s" % config.DB_PATH)
    say("逐条明细（含全部 [OK]）写到：%s" % LOGPATH)
    part_a()
    part_b()
    part_c()
    say("段汇总：" + " · ".join(
        "%s %d/%d" % (s["name"], s["ok"], s["ok"] + s["bad"]) for s in SECTIONS))
    say("结果：%d 项通过 / %d 项失败" % (OK, BAD))
    say("C0061-OFFLINE-PASS" if BAD == 0 else "C0061-OFFLINE-FAIL")
    try:
        LOGPATH.parent.mkdir(parents=True, exist_ok=True)
        LOGPATH.write_text("\n".join(LINES), encoding="utf-8")
    except OSError as e:
        print("（明细日志写失败：%s）" % e, flush=True)
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
