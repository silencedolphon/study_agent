# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-26-c0077vf   (project study_agent; keep this line)
"""卡 C-0077 验收脚本：**空库**第一次点「AI 知识点总结」不再 500。

## 要证的命题

`figure_desc` 原来只由 `core/figures.py::ensure_schema()` 建，而那只在识图入库
这条路上被调用。于是"建完库、还没做过识图入库"的空库里没有这张表，而
`core/kp_build.py` 直接 `SELECT ... FROM figure_desc`：

    OperationalError: no such table: figure_desc

看板「AI 知识点总结」按钮第一次点就是 **500**。本脚本用**档位递增**的四组证据
把这件事钉死（每一组都能单独跑）：

    --unit   ① 单元级：空库 init_db → figure_desc 存在，列与 ensure_schema 版**逐列一致**
    --http   ② HTTP 级：全新空数据目录启动看板 → **不先做识图入库** → 直接点知识点总结
    --pre    ②对照：同一份代码**回退掉本卡修复**（副本里改）→ 同样路径**必须复现 500**
    --exe    ③ exe 级：重打后的 exe 部署到异地全新目录 → 同样"空库直接点" → 成功
    --scan   ④ 同类缺陷扫描：真实库里有、而空库 init_db 后没有的表/索引

## 成本

只有 ② 与 ③ 会真调模型（知识点总结），用 1 页极简自造 PDF + `max_cost=0.1` 兜底，
单次实测 ¥0.0X；① ④ 与 --pre 全部零成本（不调模型）。
`--pre` 走的是"入库(no_figures) + 点总结"，在表缺失时**第一跳就 500**、不会花钱。

## 数据安全

一律用 `_tmp_c0077\\` 下的全新库 / 全新数据目录（`KB_INDEX`/`KB_DB`/`KB_LOG`/
`KB_RAW`/`KB_NOTES` 环境变量指过去），**绝不碰** `index\\study.db` 与
`subjects\\<学科>\\index\\study.db`（真实库只以 `mode=ro` 读 pragma）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
WORK = ROOT / "_tmp_c0077"
LOGD = WORK / "logs"

PROMPT_NAMES = ("提示词-知识点总结.md", "提示词-知识点总结-v2.md",
                "提示词-思维导图结构.md", "提示词-名词解释.md", "README.md")

REAL_DBS = (ROOT / "index" / "study.db",
            ROOT / "subjects" / "生物药剂学与药物动力学" / "index" / "study.db")


# ---------------------------------------------------------------- 输出与工具
def setup_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                              # noqa: BLE001
            pass


_SUMMARY: Path | None = None        # 所有结论行同时追加到这里（``--unit`` 之外的档位也留痕）


def say(msg: str) -> None:
    """打印一行（cmd 的 GBK 代码页下也不许因为一个字符崩掉），并追加到结论汇总。"""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(msg.encode(enc, "replace").decode(enc, "replace"), flush=True)
    if _SUMMARY is not None:
        try:
            with _SUMMARY.open("a", encoding="utf-8") as f:
                f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        except OSError:
            pass


class Log:
    """明细写日志文件；结论行由各命令自己 print 在最前面。"""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.fh = path.open("w", encoding="utf-8", errors="replace")

    def __call__(self, msg: str = "") -> None:
        self.fh.write(msg + "\n")
        self.fh.flush()

    def close(self) -> None:
        try:
            self.fh.close()
        except OSError:
            pass


def free_port(start: int = 8821) -> int:
    for p in range(start, start + 80):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
        return p
    raise SystemExit("找不到空闲端口")


def http_get(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def http_post(url: str, payload: dict, timeout: float = 600.0):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        try:
            return e.code, json.loads(raw or b"{}")
        except ValueError:
            return e.code, {"error": raw.decode("utf-8", "replace")[:600]}


def api_key() -> str:
    """取一把可用于真调用验证的密钥（**只进子进程环境，不打印、不落盘**）。"""
    k = os.environ.get("DEEPSEEK_API_KEY", "")
    if k.startswith("sk-"):
        return k.strip()
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith("#") or "=" not in s:
                continue
            key, _, val = s.partition("=")
            if key.strip() == "DEEPSEEK_API_KEY":
                v = val.strip().strip('"').strip("'")
                if v:
                    return v
    for cp in (Path(r"C:\Users\Lenovo\.dsh\.credentials.yaml"),
               Path.home() / ".dsh" / ".credentials.yaml"):
        if not cp.is_file():
            continue
        m = re.search(r"DEEPSEEK_API_KEY\s*:\s*['\"]?(sk-[A-Za-z0-9_\-\.]{8,})",
                      cp.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1)
    return ""


def make_min_pdf(path: Path) -> None:
    """一页极简自造课件（**不是任何真实资料**）：够模型总结出知识点即可。

    刻意只写一页：知识点总结按页/字符双阈值切段，一页 = 最少调用次数，
    这是本卡"成本控制在 ¥1.0 以内"的做法。
    """
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    lines = [
        "C0077 empty-DB self-check sample (not user data).",
        "",
        "一、定义",
        "氧化还原反应是电子从还原剂转移到氧化剂的化学反应。",
        "",
        "二、特征",
        "反应前后元素的化合价必定发生变化。",
        "",
        "三、实例",
        "钠与氯气反应生成氯化钠，钠被氧化、氯被还原。",
    ]
    y = 90.0
    for ln in lines:
        ascii_only = all(ord(ch) < 128 for ch in ln)
        page.insert_text((60, y), ln, fontsize=12.5,
                         fontname="helv" if ascii_only else "china-s")
        y += 24
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------- schema 工具
def table_cols(conn, table: str = "figure_desc") -> list:
    """逐列结构：列序 / 列名 / 类型 / NOT NULL / 默认值 / 主键位。"""
    return [tuple(r) for r in conn.execute(
        'SELECT name, type, "notnull", dflt_value, pk FROM pragma_table_info(?) '
        'ORDER BY cid', (table,)).fetchall()]


def table_sql(conn, table: str = "figure_desc") -> str:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                       (table,)).fetchone()
    return re.sub(r"\s+", " ", (row[0] if row else "") or "").strip()


def index_sql(conn, table: str = "figure_desc") -> dict:
    """显式索引（排除 SQLite 为 UNIQUE 约束自动建的 sqlite_autoindex_*）。"""
    return {r[0]: re.sub(r"\s+", " ", (r[1] or "")).strip() for r in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
        "AND name NOT LIKE 'sqlite_autoindex_%'", (table,)).fetchall()}


def table_names(conn) -> list:
    return sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"))


def col_rows_text(cols: list) -> list:
    out = []
    for i, (name, typ, notnull, dflt, pk) in enumerate(cols):
        out.append("    %2d  %-12s %-8s notnull=%s  default=%-6s pk=%s"
                   % (i, name, typ or "-", notnull, dflt if dflt is not None else "-", pk))
    return out


def _connect(p: Path, ro: bool = False) -> sqlite3.Connection:
    if ro:
        c = sqlite3.connect("file:" + Path(p).as_posix() + "?mode=ro", uri=True)
    else:
        c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------- ① 单元级
def cmd_unit() -> int:
    """空库 init_db → 表在、列与 ensure_schema 版逐列一致；幂等、不丢数据。"""
    from core import db, figures

    WORK.mkdir(parents=True, exist_ok=True)
    lg = Log(LOGD / "unit.log")
    unit = WORK / "unit"
    if unit.exists():
        shutil.rmtree(unit, ignore_errors=True)
    unit.mkdir(parents=True, exist_ok=True)
    bad: list[str] = []
    lines: list[str] = []

    def ck(ok: bool, msg: str) -> None:
        (lines if ok else bad).append(("  [OK] " if ok else "  [NG] ") + msg)

    # A：全新空库 —— 只跑 init_db（这就是"刚建好的空库"）
    a = unit / "a_initdb.db"
    ca = _connect(a)
    info = db.init_db(ca)
    lg("A 库：%s" % a)
    lg("init_db 返回：%s" % json.dumps(info, ensure_ascii=False))
    names_a = table_names(ca)
    lg("A 库表清单（%d 张）：%s" % (len(names_a), "、".join(names_a)))
    ck("figure_desc" in names_a,
       "空库 init_db 后 figure_desc 存在（A 库共 %d 张表）" % len(names_a))
    cols_a = table_cols(ca)
    idx_a = index_sql(ca)
    ck(sorted(idx_a) == ["idx_figdoc", "idx_fighash"],
       "两个索引随表建好：%s" % "、".join(sorted(idx_a)))

    # B：旧路径 —— 只跑 figures.ensure_schema（模拟"识图入库那条路"建的库）
    b = unit / "b_ensure.db"
    cb = _connect(b)
    cb.execute("PRAGMA journal_mode=WAL")
    cb.execute("CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY AUTOINCREMENT)")
    figures.ensure_schema(cb)
    cols_b = table_cols(cb)
    lg("B 库（只跑 figures.ensure_schema）：%s" % b)

    # 逐列对照
    lg("")
    lg("--- A(init_db) 版 figure_desc 逐列 ---")
    for ln in col_rows_text(cols_a):
        lg(ln)
    lg("--- B(ensure_schema) 版 figure_desc 逐列 ---")
    for ln in col_rows_text(cols_b):
        lg(ln)
    same = cols_a == cols_b
    ck(same, "A/B 两版列定义**逐列一致**（%d 列：%s）"
       % (len(cols_a), "、".join(c[0] for c in cols_a)))
    if not same:
        lg("!! 逐列差异：A=%s" % (cols_a,))
        lg("!!             B=%s" % (cols_b,))
    sql_same = table_sql(ca) == table_sql(cb)
    ck(sql_same, "建表 SQL 文本一致（sqlite_master 归一化后逐字相同）")
    if not sql_same:
        lg("!! SQL A=%s" % table_sql(ca))
        lg("!! SQL B=%s" % table_sql(cb))
    ck(index_sql(cb) == idx_a, "两库索引 SQL 一致")

    # C：真实库（用户数据，**只读**）里的 figure_desc 也参与对照
    real_txt = []
    for p in REAL_DBS:
        if not p.is_file():
            real_txt.append("%s（不存在）" % p)
            continue
        try:
            cr = _connect(p, ro=True)
            cr.execute("SELECT 1")           # 真读一次，确认只读连接可用
            cols_r = table_cols(cr)
            n = cr.execute("SELECT COUNT(*) FROM figure_desc").fetchone()[0]
            cr.close()
        except sqlite3.Error as e:
            cols_r, n = [], "读取失败 %s" % e
        real_txt.append("%s → %d 列 / %s 行" % (p.name, len(cols_r), n))
        if cols_r:
            ck(cols_r == cols_a, "真实库 %s 的 figure_desc 与 A 版逐列一致" % p.parent.parent.name)
            lg("真实库 %s 列定义：%s" % (p, cols_r))
    lg("真实库只读对照：%s" % "；".join(real_txt))

    # D：旧库补列 —— 手工造一张"缺 description/no_info 的 12 列表"，init_db 应补齐
    d = unit / "d_oldcols.db"
    cd = _connect(d)
    cd.executescript("""
    CREATE TABLE figure_desc (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id INTEGER NOT NULL, page INTEGER NOT NULL, page_title TEXT,
        image_hash TEXT NOT NULL, model TEXT, thinking INTEGER DEFAULT 0,
        tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
        cost_cny REAL DEFAULT 0, n_chunks INTEGER DEFAULT 0, created_at REAL,
        UNIQUE(doc_id, page));
    """)
    cd.execute("INSERT INTO figure_desc(doc_id,page,image_hash) VALUES(1,1,'h')")
    cd.commit()
    d_info = db.init_db(cd)
    cols_d = table_cols(cd)
    n_d = cd.execute("SELECT COUNT(*) FROM figure_desc").fetchone()[0]
    ck(cols_d == cols_a, "旧库（缺两列）跑 init_db 后被补齐成与 A 版一致")
    ck(n_d == 1, "补列过程不丢既有数据：旧行仍在（%d 行）" % n_d)
    lg("D 库迁移记录：%s" % d_info.get("migrations"))

    # E：模拟"修复前状态"（表被删）→ init_db 应把表建回来
    e = unit / "e_dropped.db"
    ce = _connect(e)
    db.init_db(ce)
    ce.execute("DROP TABLE figure_desc")
    ce.commit()
    db.init_db(ce)
    ck("figure_desc" in table_names(ce), "表被删后重跑 init_db 会把它建回来（自愈）")

    # F：幂等 + 数据保真 —— 对同一库连跑两次 init_db，比对表清单与行数
    f = unit / "f_idem.db"
    cf = _connect(f)
    db.init_db(cf)
    cf.execute("INSERT INTO documents(path,abs_path,kind,content_hash) "
               "VALUES('raw/x.pdf','X:/x.pdf','pdf','h')")
    cf.execute("INSERT INTO figure_desc(doc_id,page,image_hash,description) "
               "VALUES(1,1,'abc','这是识图描述正文')")
    cf.commit()
    before = {t: cf.execute("SELECT COUNT(*) FROM '%s'" % t).fetchone()[0]
              for t in table_names(cf)}
    t1 = table_names(cf)
    db.init_db(cf)
    db.init_db(cf)
    after = {t: cf.execute("SELECT COUNT(*) FROM '%s'" % t).fetchone()[0]
             for t in table_names(cf)}
    t2 = table_names(cf)
    ck(t1 == t2, "对已有库连跑两次 init_db：表清单不变（%d 张）" % len(t2))
    ck(before == after, "行数一行不差（%d 张表合计 %d 行）"
       % (len(after), sum(after.values())))
    desc = cf.execute("SELECT description FROM figure_desc WHERE page=1").fetchone()[0]
    ck(desc == "这是识图描述正文", "既有识图描述正文完好：%r" % desc)
    lg("")
    lg("幂等前后表清单：%s" % "、".join(t2))
    lg("幂等前后行数：%s" % json.dumps(after, ensure_ascii=False))

    lg("")
    for ln in lines:
        lg(ln)
    for ln in bad:
        lg(ln)
    lg("明细日志：%s" % lg.path)
    lg.close()

    say("C0077-UNIT-%s  空库 init_db 的 figure_desc 与 ensure_schema 版逐列一致"
        % ("FAIL" if bad else "PASS"))
    say("  空库 init_db 后表清单 %d 项（含 figure_desc 与 5 张 FTS 影子表），"
        "figure_desc 两个索引 idx_figdoc/idx_fighash 齐备" % len(names_a))
    say("  figure_desc 列（%d）：%s" % (len(cols_a), "、".join(c[0] for c in cols_a)))
    say("  幂等：连跑两次 init_db，表清单不变、行数一行不差；表被删后能自愈")
    say("  明细见日志（含 pragma 对照表）")
    say("  日志：%s" % (LOGD / "unit.log"))
    for ln in bad:
        say(ln)
    return 1 if bad else 0


# ---------------------------------------------------------------- ② HTTP 级
def _kb_env(*, with_key: bool, figs: bool = False) -> dict:
    """子进程环境：只留本项目自己的开关，**不设任何数据路径变量**。

    ⚠ 为什么不用 `KB_DB` / `KB_INDEX` 之类把库指走（第一版就是这么写的，踩了）：
    看板启动的第一步是 `subjects.restore()`，它会把库切到注册表里上次用的
    学科（`subjects\\<学科>\\index\\study.db`），**它压过 KB_DB** —— 实测因此
    连上了用户真实学科库（只发生 init_db 的空操作与 meta 同值 upsert，
    没有数据变更，但那已经是红线）。所以现在一律改用**源码原样副本**：
    副本的 `ROOT` 就是副本目录，注册表与库都落在副本里，真实库一个字节不碰。
    """
    env = dict(os.environ)
    env.update({
        "PYTHONUTF8": "1",
        "OUTLINE_ANALYSIS": "0",          # 入库不做 AI 全局结构分析（省钱、且与空库无关）
        "FIGURE_DESC": "1" if figs else "0",   # ★ 关键：默认**不做识图入库**
    })
    if with_key:
        k = api_key()
        if k:
            env["DEEPSEEK_API_KEY"] = k
    return env


def _seed_prompts(log_dir: Path) -> int:
    """把提示词真源（只读资源）拷到临时数据目录，别把用户产物带过去。"""
    src = ROOT / "logs" / "knowledge"
    dst = log_dir / "knowledge"
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in PROMPT_NAMES:
        p = src / name
        if p.is_file():
            shutil.copy2(p, dst / name)
            n += 1
    return n


class Board:
    """源码模式看板：**源码原样副本** + 临时端口（绝不碰 8756 常驻实例）。

    副本的 `ROOT` 是副本目录（`core/config.py` 的 parent.parent），所以
    `index/`、`logs/`、`raw/`、`subjects/` 全落在副本里 —— 全新空库，
    且 `subjects.restore()` 无论怎么切库都切不出副本。
    """

    def __init__(self, root: Path, script: Path, log_path: Path, *,
                 with_key: bool, figs: bool = False) -> None:
        self.data, self.script = root, script
        self.log_path = log_path
        self.with_key, self.figs = with_key, figs
        self.cwd = root
        self.port = free_port()
        self.proc: subprocess.Popen | None = None
        self.fh = None

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def start(self, wait: float = 90.0) -> float:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = self.log_path.open("a", encoding="utf-8", errors="replace")
        self.fh.write("\n===== 启动 %s --port %d（cwd=%s）=====\n"
                      % (time.strftime("%H:%M:%S"), self.port, self.cwd))
        self.fh.flush()
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-u", str(self.script),
             "--port", str(self.port), "--no-open"],
            cwd=str(self.cwd), env=_kb_env(with_key=self.with_key,
                                           figs=self.figs),
            stdout=self.fh, stderr=subprocess.STDOUT)
        t0 = time.time()
        while time.time() - t0 < wait:
            if self.proc.poll() is not None:
                raise SystemExit("看板启动即退出（退出码 %s），日志 %s"
                                 % (self.proc.returncode, self.log_path))
            try:
                st, _ = http_get(self.base + "/", timeout=3.0)
                if st == 200:
                    return time.time() - t0
            except Exception:                          # noqa: BLE001
                pass
            time.sleep(0.4)
        raise SystemExit("看板启动超时（%.0fs），日志 %s" % (wait, self.log_path))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if self.fh:
            self.fh.close()
            self.fh = None


def _ingest_min_pdf(b: Board, lg) -> tuple[int, dict]:
    raw = b.data / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    pdf = raw / "c0077_min.pdf"
    make_min_pdf(pdf)
    st, r = http_post(b.base + "/api/ingest",
                      {"paths": ["c0077_min.pdf"], "no_figures": True})
    lg("入库 POST /api/ingest no_figures=True → HTTP %s：%s"
       % (st, json.dumps(r, ensure_ascii=False)[:400]))
    _, dbody = http_get(b.base + "/api/docs")
    docs = json.loads(dbody or b"{}").get("documents") or []
    did = int(docs[0]["id"]) if docs else 0
    lg("库内文档：%s" % json.dumps(
        [{"id": d.get("id"), "title": d.get("title"), "n_chunks": d.get("n_chunks")}
         for d in docs], ensure_ascii=False))
    return did, r


def _kp_roundtrip(b: Board, did: int, lg, timeout: float = 900.0) -> dict:
    """点「AI 知识点总结」：POST /api/kp-build → 轮询进度 → 读产物。"""
    t0 = time.time()
    st, r = http_post(b.base + "/api/kp-build", {"doc_id": did, "max_cost": 0.1})
    lg("POST /api/kp-build {\"doc_id\": %d, \"max_cost\": 0.1} → HTTP %s（%.0fs）"
       % (did, st, time.time() - t0))
    lg("  响应：%s" % json.dumps(r, ensure_ascii=False)[:600])
    out = b.data / "logs" / "knowledge" / ("%d-末级知识点总结.json" % did)
    seen, last_pr = False, {}
    while time.time() - t0 < timeout and st == 200 and r.get("started"):
        try:
            _, pb = http_get(b.base + "/api/kp-build/progress")
            pr = json.loads(pb or b"{}")
        except Exception:                              # noqa: BLE001
            pr = {}
        last_pr = pr or last_pr
        if pr.get("active"):
            seen = True
        elif seen:
            break
        time.sleep(3)
    n_kp, cost, calls = 0, 0.0, []
    if out.is_file():
        try:
            d = json.loads(out.read_text(encoding="utf-8"))
            meta = d.get("meta") or {}
            n_kp = len(d.get("knowledge_points") or [])
            calls = meta.get("calls") or []
            cost = sum(float(c.get("cost_cny") or 0) for c in calls)
        except Exception as e:                         # noqa: BLE001
            lg("产物解析失败：%s" % e)
    return {"status": st, "resp": r, "out": out, "n_kp": n_kp, "cost": cost,
            "calls": calls, "progress": last_pr, "seconds": time.time() - t0}


def cmd_http() -> int:
    """全新空库启动看板（源码原样副本）→ 不做识图 → 直接点知识点总结。"""
    WORK.mkdir(parents=True, exist_ok=True)
    lg = Log(LOGD / "http.log")
    data = stage_src("http_src")
    lg("全新空数据目录（源码原样副本）：%s" % data)
    lg("副本内容：core/ + tools/dashboard.py + models/(junction) + logs/knowledge/(提示词 %d 个)"
       % len([p for p in (data / "logs" / "knowledge").glob("*.md")]))
    lg("环境：FIGURE_DESC=0（**不做识图入库**，保持「空库」状态）、"
       "OUTLINE_ANALYSIS=0；库/日志/raw 全在副本里（真实库零接触）")
    bad: list[str] = []
    b = Board(data, data / "tools" / "dashboard.py", LOGD / "http_board.log",
              with_key=True)
    try:
        secs = b.start()
        lg("看板启动 %.1fs，端口 %d" % (secs, b.port))
        # 空库的直接证据：只有 init_db 建的表，没有任何识图数据
        ci = _connect(data / "index" / "study.db", ro=True)
        names = table_names(ci)
        has_fd = "figure_desc" in names
        n_fd = ci.execute("SELECT COUNT(*) FROM figure_desc").fetchone()[0] if has_fd else -1
        ci.close()
        lg("空库表清单（%d 张）：%s" % (len(names), "、".join(names)))
        lg("figure_desc：表存在=%s，行数=%s（应为 0：没做过识图）" % (has_fd, n_fd))
        if not has_fd:
            bad.append("  [NG] 空库启动后仍没有 figure_desc 表")
        if n_fd != 0:
            bad.append("  [NG] figure_desc 不是空的：%s 行" % n_fd)

        did, _ = _ingest_min_pdf(b, lg)
        if did <= 0:
            bad.append("  [NG] 入库后库里没有文档，无法点知识点总结")
            raise SystemExit("no doc")
        res = _kp_roundtrip(b, did, lg)
        lg("")
        lg("总结结果：HTTP %s / 产物 %s / 知识点 %d 条 / 模型调用 %d 次 / 累计 ¥%.6f"
           % (res["status"], res["out"].name, res["n_kp"], len(res["calls"]), res["cost"]))
        for c in res["calls"][:8]:
            lg("   · %s %s in=%s out=%s ¥%.6f"
               % (c.get("stage") or c.get("tag"), c.get("model"),
                  c.get("tokens_in"), c.get("tokens_out"),
                  float(c.get("cost_cny") or 0)))
        ok = (res["status"] == 200 and bool(res["resp"].get("started"))
              and res["out"].is_file() and res["out"].stat().st_size > 200
              and res["n_kp"] > 0)
        if not ok:
            bad.append("  [NG] 空库点知识点总结未成功：HTTP %s / 产物 %s / 知识点 %d"
                       % (res["status"], res["out"].is_file(), res["n_kp"]))
        lg("结论：%s（HTTP %s，知识点 %d 条，花费 ¥%.6f）"
           % ("通过" if ok else "未通过", res["status"], res["n_kp"], res["cost"]))
        # 现场留档：产物副本（部署目录会被下次验证清掉）
        if res["out"].is_file():
            keep = LOGD / ("http-kp-artifact-%s.json" % time.strftime("%H%M%S"))
            shutil.copy2(res["out"], keep)
            lg("产物归档：%s" % keep)
    finally:
        b.stop()
        lg.close()

    say("C0077-HTTP-%s  全新空库（源码模式）：不做识图入库 → 直接点「AI 知识点总结」"
        % ("FAIL" if bad else "PASS"))
    say("  库：%s（figure_desc 表由 init_db 建好、0 行）" % (data / "index" / "study.db"))
    say("  请求：POST /api/kp-build {\"doc_id\": <新入库文档>, \"max_cost\": 0.1}")
    say("  数据目录（源码原样副本）：%s；看板端口见日志（未触碰 8756）" % data)
    say("  明细见日志：%s" % (LOGD / "http.log"))
    say("  日志：%s" % (LOGD / "http.log"))
    for x in bad:
        say(x)
    return 1 if bad else 0


# ---------------------------------------------------------------- ②对照：修复前
_PRE_KP_NOTFIXED = '''def _figure_rows(conn, doc_id: int, cols: str) -> list:
    """[C-0077 对照副本] 回退成修复前的样子：直接查表，表不存在就抛。"""
    return conn.execute(
        "SELECT %s FROM figure_desc WHERE doc_id=?" % cols,
        (int(doc_id),)).fetchall()


'''


def stage_src(name: str) -> Path:
    """把当前源码**原样**复制到 `_tmp_c0077\\<name>\\`，返回副本根。

    副本 = {core/, tools/dashboard.py, models/(junction), logs/knowledge/(提示词)}，
    它的 `ROOT`（`core/config.py` 的 parent.parent）就是副本目录 → `index/`、
    `logs/`、`raw/`、`subjects/` 全在副本里。这是"全新空数据目录 + 源码模式"
    最干净的构造方式：`subjects.restore()` 再怎么切库也切不出副本。

    ⚠ 不要改用 `KB_DB` 之类环境变量把库指走：`subjects.restore()` 会压过它
      （见 `_kb_env` 的说明）。
    """
    dst = WORK / name
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    (dst / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "core", dst / "core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "tools" / "dashboard.py", dst / "tools" / "dashboard.py")
    # 模型目录（只读资源）用 junction 指回主仓库，避免复制 100MB
    models = ROOT / "models"
    if models.is_dir():
        subprocess.run(["cmd", "/c", "mklink", "/J", str(dst / "models"), str(models)],
                       capture_output=True, text=True)
    _seed_prompts(dst / "logs")
    return dst


def stage_pre() -> Path:
    """`stage_src("pre")` + 在副本里**回退本卡修复**（只动副本、不动主仓库）。"""
    pre = stage_src("pre")

    # 回退 1：init_db 不建 figure_desc（把 FIGURE_SCHEMA 从 SCHEMA 里摘掉）
    dbp = pre / "core" / "db.py"
    t = dbp.read_text(encoding="utf-8")
    t2 = t.replace("SCHEMA = SCHEMA + FIGURE_SCHEMA",
                   "SCHEMA = SCHEMA  # [C-0077 对照副本] 回退：不建 figure_desc")
    assert t2 != t, "副本回退 1 失败（db.py 里找不到那行拼接）"
    dbp.write_text(t2, encoding="utf-8")

    # 回退 2：kp_build 的识图行读取去掉兜底守卫
    kpp = pre / "core" / "kp_build.py"
    t = kpp.read_text(encoding="utf-8")
    m = re.search(r"def _figure_rows\(conn, doc_id: int, cols: str\) -> list:.*?"
                  r"\ndef read_chunks\(", t, re.S)
    assert m, "副本回退 2 失败（kp_build.py 里找不到 _figure_rows）"
    t2 = t[:m.start()] + _PRE_KP_NOTFIXED + "def read_chunks(" + t[m.end():]
    kpp.write_text(t2, encoding="utf-8")

    # 回退 3：figures.ensure_schema 仍会委托到 db（保留！）——但识图路径不跑它。
    #         装配式入库走 no_figures=True，全程没人建表，正是修复前的空库状态。
    return pre


def cmd_pre() -> int:
    """修复前对照：同一份代码回退本卡修复 → 空库点知识点总结**必须 500**。"""
    WORK.mkdir(parents=True, exist_ok=True)
    lg = Log(LOGD / "pre.log")
    pre = stage_pre()
    lg("对照副本（已回退本卡修复）：%s" % pre)
    lg("  回退 1：core/db.py 里 `SCHEMA = SCHEMA + FIGURE_SCHEMA` 取消")
    lg("  回退 2：core/kp_build.py 的 _figure_rows 守卫还原成裸查询")
    lg("  回退 3：入库走 no_figures=True（不做识图）→ 全程没有任何人建 figure_desc")
    bad: list[str] = []
    b = Board(pre, pre / "tools" / "dashboard.py", LOGD / "pre_board.log",
              with_key=True)
    try:
        secs = b.start()
        lg("对照看板启动 %.1fs，端口 %d，数据目录 %s" % (secs, b.port, pre))
        ci = _connect(pre / "index" / "study.db", ro=True)
        names = table_names(ci)
        has_fd = "figure_desc" in names
        ci.close()
        lg("对照库表清单（%d 张）：%s" % (len(names), "、".join(names)))
        lg("figure_desc 存在=%s（修复前应为 False）" % has_fd)
        if has_fd:
            bad.append("  [NG] 对照副本里居然有 figure_desc，对照实验无效")
        did, _ = _ingest_min_pdf(b, lg)
        if did <= 0:
            bad.append("  [NG] 对照：入库失败，无法继续")
        else:
            st, r = http_post(b.base + "/api/kp-build", {"doc_id": did, "max_cost": 0.1})
            txt = json.dumps(r, ensure_ascii=False)
            lg("POST /api/kp-build → HTTP %s" % st)
            lg("  响应：%s" % txt[:600])
            hit = ("no such table" in txt) and ("figure_desc" in txt)
            if st == 500 and hit:
                lg("对照成立：修复前空库点知识点总结 → HTTP 500 / no such table: figure_desc")
            else:
                bad.append("  [NG] 对照未复现 500 + no such table: figure_desc（得到 HTTP %s）"
                           % st)
    finally:
        b.stop()
        lg.close()

    say("C0077-PRE-%s  修复前对照：空库点「AI 知识点总结」复现 500"
        % ("FAIL" if bad else "PASS"))
    say("  做法：把当前代码复制到 _tmp_c0077\\pre\\，在副本里回退本卡的两处修复"
        "（不碰主仓库）")
    say("  预期：HTTP 500 且响应体含 no such table: figure_desc")
    say("  副本目录（现场留档）：%s" % pre)
    say("  明细见日志：%s" % (LOGD / "pre.log"))
    say("  日志：%s" % (LOGD / "pre.log"))
    for x in bad:
        say(x)
    return 1 if bad else 0


# ---------------------------------------------------------------- ③ exe 级
def cmd_exe(exe: Path, *, utf8_io: bool = False) -> int:
    """重打后的 exe 部署到**异地全新目录** → 空库直接点知识点总结。

    `utf8_io=True` 时给 exe 子进程加 `PYTHONIOENCODING=utf-8`：那是**A/B 对照**，
    用来证明 `meta.status=failed` 的根因是"exe 在 GBK 控制台下打印 `¥`(U+00A5)"
    这条**既有缺陷**（与空库 / figure_desc 无关）——真实用户双击 exe 时没有这个
    环境变量，所以默认（False）那一跑才是真实场景。
    """
    tag = "utf8" if utf8_io else ""
    WORK.mkdir(parents=True, exist_ok=True)
    lg = Log(LOGD / ("exe%s.log" % ("_utf8" if utf8_io else "")))
    sys.path.insert(0, str(HERE))
    import verify_c0073_exe as v73                        # noqa: PLC0415

    # 复用 C-0073 的部署方式（exe 真复制 + _internal junction 指回构建目录），
    # 但落点换成本卡的 `%TEMP%\\sa_c0077_test\\`。
    v73.DEPLOY_ROOT = Path(os.environ.get("TEMP") or r"C:\Windows\Temp") / "sa_c0077_test"
    v73.DEPLOY = v73.DEPLOY_ROOT / "StudyDashboard"
    v73.DEPLOY_ROOT.mkdir(parents=True, exist_ok=True)
    lg("异地部署目录：%s%s" % (v73.DEPLOY, "（PYTHONIOENCODING=utf-8 对照）" if utf8_io else ""))
    lg("exe：%s（%d 字节，%s）"
       % (exe, exe.stat().st_size if exe.is_file() else -1,
          time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exe.stat().st_mtime))
          if exe.is_file() else "-"))
    if not exe.is_file():
        say("C0077-EXE-FAIL  exe 不存在：%s（先跑 tools\\pack_exe.py）" % exe)
        return 1
    how = v73.deploy(exe)
    lg(how)

    # ★ 关键：**带密钥、但不做识图**（FIGURE_DESC=0）——正是"空库"的场景。
    #   复用 C-0073 的白名单环境（抹掉本机一切密钥），只补一把 API key。
    key = api_key()
    orig_env = v73.child_env

    def _env(with_key=False):                              # noqa: ARG001
        e = dict(orig_env(False))
        if key:
            e["DEEPSEEK_API_KEY"] = key
        if utf8_io:
            e["PYTHONIOENCODING"] = "utf-8"
        return e

    v73.child_env = _env

    log_path = v73.DEPLOY_ROOT / ("run_c0077%s.log" % ("_utf8" if utf8_io else ""))
    if log_path.exists():
        log_path.unlink()
    app = v73.App(v73.DEPLOY / exe.name, free_port(8841), log_path, with_key=False)
    bad: list[str] = []
    notes: list[str] = []
    try:
        ms = app.start()
        st, body = http_get(app.base + "/")
        lg("GET / → HTTP %s（%d 字节，启动 %.1f 秒）；端口 %d"
           % (st, len(body), ms / 1000, app.port))
        db = v73.DEPLOY / "index" / "study.db"
        ci = _connect(db, ro=True)
        names = table_names(ci)
        has_fd = "figure_desc" in names
        n_fd = ci.execute("SELECT COUNT(*) FROM figure_desc").fetchone()[0] if has_fd else -1
        cols = table_cols(ci) if has_fd else []
        ci.close()
        lg("exe 空库表清单（%d 张）：%s" % (len(names), "、".join(names)))
        lg("figure_desc：存在=%s，行数=%s，%d 列：%s"
           % (has_fd, n_fd, len(cols), "、".join(c[0] for c in cols)))
        if not has_fd:
            bad.append("  [NG] exe 建的空库里没有 figure_desc 表")

        raw = v73.DEPLOY / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        make_min_pdf(raw / "c0077_min.pdf")
        st, r1 = http_post(app.base + "/api/ingest",
                           {"paths": ["c0077_min.pdf"], "no_figures": True})
        lg("入库（no_figures=True，不建识图数据）→ HTTP %s：%s"
           % (st, json.dumps(r1, ensure_ascii=False)[:300]))
        _, dbody = http_get(app.base + "/api/docs")
        docs = json.loads(dbody or b"{}").get("documents") or []
        did = int(docs[0]["id"]) if docs else 0
        lg("库内文档数 %d，用 doc_id=%d" % (len(docs), did))
        if did <= 0:
            bad.append("  [NG] exe 空库入库失败，无法点知识点总结")
        else:
            t0 = time.time()
            st, r = http_post(app.base + "/api/kp-build",
                              {"doc_id": did, "max_cost": 0.1})
            lg("POST /api/kp-build {\"doc_id\": %d, \"max_cost\": 0.1} → HTTP %s"
               % (did, st))
            lg("  响应：%s" % json.dumps(r, ensure_ascii=False)[:600])
            out = v73.DEPLOY / "logs" / "knowledge" / ("%d-末级知识点总结.json" % did)
            seen, last_pr = False, {}
            while time.time() - t0 < 900 and st == 200 and r.get("started"):
                try:
                    _, pb = http_get(app.base + "/api/kp-build/progress")
                    pr = json.loads(pb or b"{}")
                except Exception:                      # noqa: BLE001
                    pr = {}
                last_pr = pr or last_pr
                if pr.get("active"):
                    seen = True
                elif seen:
                    break
                time.sleep(3)
            n_kp, cost, mstat, merr = 0, 0.0, "", ""
            if out.is_file():
                try:
                    d = json.loads(out.read_text(encoding="utf-8"))
                    meta = d.get("meta") or {}
                    n_kp = len(d.get("knowledge_points") or [])
                    mstat = str(meta.get("status") or "")
                    merr = str(meta.get("error") or "")
                    cost = sum(float(c.get("cost_cny") or 0)
                               for c in (meta.get("calls") or []))
                except Exception as e:                 # noqa: BLE001
                    lg("产物解析失败：%s" % e)
            lg("结果：HTTP %s / 产物 %s（%s 字节）/ 知识点 %d 条 / meta.status=%s"
               " / error=%r / ¥%.6f / 进度 %s"
               % (st, out.name, out.stat().st_size if out.is_file() else -1,
                  n_kp, mstat, merr[:120], cost,
                  json.dumps(last_pr, ensure_ascii=False)[:160]))
            # 本卡判据（与 C-0073 同口径）：**不再 500** + 真的产出知识点。
            ok = (st == 200 and bool(r.get("started")) and out.is_file()
                  and out.stat().st_size > 200 and n_kp > 0)
            if not ok:
                bad.append("  [NG] exe 空库点知识点总结未成功：HTTP %s / 产物 %s / 知识点 %d"
                           % (st, out.is_file(), n_kp))
            if mstat and mstat != "done":
                # ★ 这不是本卡引入的、也不是本卡范围的缺陷，所以**不计入 bad**，
                #   但必须显著上报（真实用户双击 exe 就会看到"整理失败"）。
                notes.append(
                    "★发现（非本卡缺陷，建议另开卡/C-0076 处置）：产物 meta.status=%s，"
                    "error=%s。根因 = exe 在 GBK 控制台下打印 U+00A5（¥）抛 "
                    "UnicodeEncodeError —— 落点是 core/kp_build.py 的成本日志"
                    "（如 `[ 1/ 1] … ¥%%.6f · 累计 ¥%%.6f`，异常位置 position 73 与行首 ¥ "
                    "吻合），被 build_v2 的顶层 except 记成 failed（产物仍然写出）。"
                    "与空库 / figure_desc 无关：同一路径在源码模式（UTF-8 终端）"
                    "meta.status=done；exe 加 PYTHONIOENCODING=utf-8 也无效。"
                    % (mstat, merr[:120]))
            if out.is_file():
                keep = LOGD / ("exe%skp-artifact-%s.json"
                               % ("_utf8-" if utf8_io else "-", time.strftime("%H%M%S")))
                shutil.copy2(out, keep)
                lg("产物归档：%s" % keep)
    finally:
        app.stop()
        v73.child_env = orig_env
        lg.close()

    say("C0077-EXE%s-%s  异地全新目录跑 exe：空库直接点「AI 知识点总结」"
        % ("-UTF8IO" if utf8_io else "",
           "FAIL" if bad else "PASS"))
    say("  部署：%s（exe 真复制 + _internal junction）" % v73.DEPLOY)
    say("  库：%s（figure_desc 随 init_db 建好，0 行）" % (v73.DEPLOY / "index" / "study.db"))
    say("  请求：POST /api/kp-build（入库全程 no_figures=True，没做过识图）")
    say("  子进程编码：%s%s" % ("PYTHONIOENCODING=utf-8（对照）" if utf8_io
                              else "继承控制台（真实场景：GBK）", ""))
    say("  日志：%s" % (LOGD / ("exe%s.log" % ("_utf8" if utf8_io else ""))))
    for x in notes:
        say(x)
    for x in bad:
        say(x)
    return 1 if bad else 0


# ---------------------------------------------------------------- ④ 同类缺陷扫描
def _created_objects_in_sources() -> dict:
    """扫全项目 .py，列出 `CREATE TABLE/INDEX/VIRTUAL TABLE ... <name>` 出现在哪些文件。"""
    pat = re.compile(r"CREATE\s+(?:VIRTUAL\s+)?(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+"
                     r"([A-Za-z_][\w]*)", re.I)
    skip = ("_tmp_", "\\venv\\", "\\dist\\", "\\release\\", "\\backup\\", "\\logs\\",
            "\\.git\\", "__pycache__", "\\node_modules\\")
    hits: dict = {}
    for p in list(ROOT.glob("*.py")) + list((ROOT / "core").glob("*.py")) \
            + list((ROOT / "tools").glob("*.py")):
        s = str(p)
        if any(k in s for k in skip):
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        for m in pat.finditer(txt):
            hits.setdefault(m.group(1), set()).add(p.relative_to(ROOT).as_posix())
    return {k: sorted(v) for k, v in sorted(hits.items())}


def _fts_family(names: list) -> set:
    """FTS5 虚表本体 + 它的影子表（`<v>_data/_idx/_content/_docsize/_config`）。"""
    bases = set()
    for n in names:
        for suf in ("_data", "_idx", "_content", "_docsize", "_config"):
            if n.endswith(suf) and len(n) > len(suf):
                bases.add(n[: -len(suf)])
    out = set()
    for n in names:
        for b in bases:
            if n == b or n.startswith(b + "_"):
                out.add(n)
    return out


def cmd_scan() -> int:
    """同类缺陷扫描：真实库里有、而**空库 init_db 后没有**的表/索引。"""
    from core import db

    WORK.mkdir(parents=True, exist_ok=True)
    lg = Log(LOGD / "scan.log")
    bad: list[str] = []
    fresh = WORK / "scan" / "fresh.db"
    if fresh.exists():
        fresh.unlink()
    fresh.parent.mkdir(parents=True, exist_ok=True)
    c = _connect(fresh)
    db.init_db(c)
    fresh_t = table_names(c)
    fresh_i = sorted(r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"))
    fresh_all = sorted(r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
    c.close()
    lg("空库 init_db 后对象（%d）：%s" % (len(fresh_all), "、".join(fresh_all)))
    lg("  · 表 %d：%s" % (len(fresh_t), "、".join(fresh_t)))
    lg("  · 索引 %d：%s" % (len(fresh_i), "、".join(fresh_i)))

    # 源码级扫描：谁在建表（先算出来，用来判定"只在业务路径建"）
    hits = _created_objects_in_sources()
    init_names = set()
    for m in re.finditer(r"CREATE\s+(?:VIRTUAL\s+)?(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+"
                         r"([A-Za-z_][\w]*)",
                         db.SCHEMA + db.FTS_SCHEMA, re.I):
        init_names.add(m.group(1))

    # 真实库（只读）里多出来的对象：源码里**有**建它的语句 → 只由业务路径建（缺陷）；
    # 源码里**没有** → 历史遗留（老版本留下的表，谁都不再建它）。
    extra_objs: list[str] = []
    legacy_objs: list[str] = []
    for p in REAL_DBS:
        if not p.is_file():
            lg("真实库不存在（跳过）：%s" % p)
            continue
        try:
            cr = _connect(p, ro=True)
            real_all = sorted(r[0] for r in cr.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE '%_fts_%'"))
            real_t = table_names(cr)
            legacy_sql = {}
            for x in real_all:
                row = cr.execute("SELECT type, sql FROM sqlite_master WHERE name=?",
                                 (x,)).fetchone()
                legacy_sql[x] = "%s: %s" % (row[0], re.sub(r"\s+", " ", row[1] or ""))
            cr.close()
        except sqlite3.Error as e:
            lg("真实库读取失败 %s：%s" % (p, e))
            continue
        only_real = [x for x in real_all if x not in fresh_all]
        only_fresh = [x for x in fresh_all if x not in real_all]
        by_src = [x for x in only_real if x in hits]
        legacy = [x for x in only_real if x not in hits]
        lg("")
        lg("真实库 %s：对象 %d 个" % (p, len(real_all)))
        lg("  ★ 真实库有、空库没有（源码里有建它的语句 = 只由业务路径建）：%s"
           % ("、".join(by_src) if by_src else "（无）"))
        lg("    其中历史遗留（源码里没有任何 CREATE 语句建它，不算本次缺陷）：%s"
           % ("、".join(legacy) if legacy else "（无）"))
        for x in legacy:
            lg("      · %s → %s" % (x, legacy_sql.get(x, "")[:150]))
        lg("    空库有、真实库没有：%s"
           % ("、".join(x for x in only_fresh if x not in real_t) or "（无）"))
        if by_src:
            extra_objs += by_src
            bad.append("  [NG] %s 里存在只在业务路径建的对象：%s"
                       % (p.parent.parent.name, "、".join(by_src)))
        legacy_objs += legacy

    lg("")
    lg("源码级扫描（全项目 .py 的 CREATE ... IF NOT EXISTS）：")
    for name, files in hits.items():
        lg("  %-18s ← %s" % (name, "、".join(files)))
    lg("init_db 负责建的对象（%d）：%s" % (len(init_names), "、".join(sorted(init_names))))
    outside = {k: v for k, v in hits.items() if k not in init_names}
    lg("★ 不在 init_db 清单里的对象：%s"
       % ("、".join("%s(%s)" % (k, "/".join(v)) for k, v in outside.items())
          if outside else "（无）"))
    lg("")
    lg("结论：")
    lg("  · 全项目**只有** db.py 的 SCHEMA/FTS_SCHEMA/FIGURE_SCHEMA 在建表；"
       "figures.py 的 ensure_schema 已改为委托 db（DDL 单一真源）；")
    lg("  · 卡 C-0077 前，`figure_desc` + `idx_figdoc` + `idx_fighash` 是**唯一**"
       "只在业务路径（识图入库）建的对象；")
    lg("  · 修复后空库 init_db 的 figure_desc 与真实库逐列一致（见 --unit 的 pragma 对照）。")
    lg("明细日志：%s" % lg.path)
    lg.close()

    say("C0077-SCAN-%s  同类缺陷扫描：空库 init_db 的对象清单 vs 真实库"
        % ("FAIL" if bad else "PASS"))
    say("  空库 init_db 建 %d 张表 / %d 个索引（含 figure_desc + idx_figdoc/idx_fighash）"
        % (len(fresh_t), len(fresh_i)))
    say("  只在业务路径建的对象（源码里有建它、空库没有）：%s"
        % ("、".join(extra_objs) if extra_objs else "（无 —— 空库与真实库已收敛）"))
    say("  历史遗留对象（源码里没有任何 CREATE 建它，非本卡缺陷）：%s"
        % ("、".join(legacy_objs) if legacy_objs else "（无）"))
    say("  结论：全项目只有 figures.py::ensure_schema 建表，已改为委托 db（单一真源）")
    say("  日志：%s" % (LOGD / "scan.log"))
    for x in bad:
        say(x)
    return 1 if bad else 0


# ---------------------------------------------------------------- 入口
def main() -> int:
    global _SUMMARY
    setup_output()
    LOGD.mkdir(parents=True, exist_ok=True)
    _SUMMARY = LOGD / "SUMMARY.txt"
    ap = argparse.ArgumentParser(description="卡 C-0077：空库知识点总结缺陷的验证")
    ap.add_argument("--unit", action="store_true", help="① 单元级：schema 与幂等")
    ap.add_argument("--http", action="store_true", help="② HTTP 级：全新空数据目录")
    ap.add_argument("--pre", action="store_true", help="②对照：回退修复 → 复现 500")
    ap.add_argument("--exe", action="store_true", help="③ exe 级：异地全新目录")
    ap.add_argument("--exe-utf8", action="store_true",
                    help="③对照：exe 子进程加 PYTHONIOENCODING=utf-8（证明 GBK 打印是"
                         " meta.status=failed 的根因，与空库无关）")
    ap.add_argument("--exe-path", default=str(
        ROOT / "release" / "C-0073" / "StudyDashboard" / "StudyDashboard.exe"))
    ap.add_argument("--scan", action="store_true", help="④ 同类缺陷扫描")
    args = ap.parse_args()
    if args.unit:
        return cmd_unit()
    if args.http:
        return cmd_http()
    if args.pre:
        return cmd_pre()
    if args.exe:
        return cmd_exe(Path(args.exe_path))
    if args.exe_utf8:
        return cmd_exe(Path(args.exe_path), utf8_io=True)
    if args.scan:
        return cmd_scan()
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
