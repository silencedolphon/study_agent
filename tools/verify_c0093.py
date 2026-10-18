#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。     
# File-ID: SA-93-c0093vf   (project study_agent; keep this line)
r"""卡 C-0093 验证（快档）：教材挂载后端 —— 知识点→教材原文 + 教材按需单页渲染。

要证的六件事（对应卡面验收标准）：
  1. **知识点 → 教材原文**：真知识点（默认库 doc#73 的 KP-N03-1）能取到
     `material[].chunk_id` → `chunks.text` 的原文，并带上页码 / `source_ref` / `cite`；
  2. **按需单页渲染**：`core.teach.page_png()` 现渲染教材某一页（第一次 miss 的真实
     耗时记在结论里），同一页再来一次走缓存（毫秒级）；
  3. **★ 安全边界**：越界页码 / 非教材文档 / 不存在的 doc / 非法 doc_id 全部被拒，
     且**拒绝文案可操作**（写明"该教材共 452 页"这类数字）；
  4. **★ 目录穿越**：在**临时库**里塞进"role=textbook 但 abs_path 指向资料库之外"
     的记录，必须被 403 拒（证明路径不是照抄库里的值，而是二次校验过的）；
  5. **挂接就位**：`tools/dashboard.py` 里两条路由（`/api/teach-page`、`/api/kp-textbook`）
     都在全局 `_lock` **之前**，且前端知识点窗口的既有标识（`mmRenderKp`、
     `#mmkp-deck`、`evPopShow` …）一个都没动（本卡不改前端 UI）；
  6. **零模型**：整条链路不 import 任何模型客户端（源码级断言：`core/teach.py` 里
     没有 chat/vision/embed 的调用点）。

隔离与安全：**只读**真库（默认库=药理），一个字都不写；库写入只发生在
`_tmp_c0093/verify_kb/` 这个临时库里（`KB_TESTING=1` 护栏此时不适用——我们连的是
自己刚建的临时库，不是真库）。不联网、不调模型、不花接口费、不动 8756 端口。

跑法（绝对路径，不依赖 cwd）：
    G:\study_agent\venv\Scripts\python.exe -X utf8 G:\study_agent\tools\verify_c0093.py

输出：**只有 6 行**，首行含 `C0093-PASS` 或 `C0093-FAIL`；明细写日志文件。
退出码 0（PASS）/ 2（FAIL）。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # tools/ -> 项目根
T0 = time.time()
WORK = ROOT / "_tmp_c0093"
LOG_PATH = WORK / "verify_c0093.log"
WORK.mkdir(parents=True, exist_ok=True)

# ⚠ 必须在 import core.config **之前**把路径钉到"默认库（药理）"：
# 看板可能正切在别的学科库上，而只有默认库里有教材（实测 6 个学科库都没有
# role=textbook 的 PDF）。这样本脚本的结论与"当前切到哪个库"无关，可复跑。
os.environ["KB_INDEX"] = str(ROOT / "index")
os.environ["KB_DB"] = str(ROOT / "index" / "study.db")
os.environ["KB_LOG"] = str(ROOT / "logs")
os.environ["KB_RAW"] = str(ROOT / "raw")
os.environ["KB_NOTES"] = str(ROOT / "notes")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.config as config          # noqa: E402
from core import db, teach            # noqa: E402

_log = open(LOG_PATH, "w", encoding="utf-8")


def P(*a) -> None:
    print(*a, file=_log)


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[bool, str]] = []

    def ok(self, cond, label: str, detail: str = "") -> bool:
        self.rows.append((bool(cond), label))
        P(f"[{'PASS' if cond else 'FAIL'}] {label}"
          + (f"  —— {detail}" if detail else ""))
        return bool(cond)

    @property
    def n_pass(self) -> int:
        return sum(1 for ok, _ in self.rows if ok)

    @property
    def n_total(self) -> int:
        return len(self.rows)

    @property
    def all_pass(self) -> bool:
        return all(ok for ok, _ in self.rows)


C = Checks()
SUMMARY: list[str] = []


def _reject(fn, *a, **kw):
    """跑一个应当被拒的调用，回 (code, message)；没被拒就回 (0, '未被拒绝')。"""
    try:
        fn(*a, **kw)
        return 0, "未被拒绝"
    except teach.TeachError as e:
        return int(e.code), e.message
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


# ---------------- ① 真库：教材、知识点 → 教材原文 ----------------

P(f"== C-0093 验证  {time.strftime('%Y-%m-%d %H:%M:%S')} ==")
P(f"ROOT={config.ROOT}\nDB={config.DB_PATH}\nLOG={config.LOG_DIR}")

conn = db.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

tbs = teach.textbook_docs(conn)
C.ok(bool(tbs), "当前（默认）库里能识别出教材类 PDF",
     f"{len(tbs)} 本：{[ (t['id'], t['title']) for t in tbs ]}")
tb = tbs[0] if tbs else None
if tb:
    real, info = teach.resolve_textbook_pdf(conn, tb["id"])
    pages = teach.pdf_page_count(real)
    C.ok(real.is_file() and pages > 0,
         f"教材源文件可解析：doc#{tb['id']}《{tb['title']}》",
         f"{pages} 页 / {real.stat().st_size/1e6:.1f} MB / {real}")
else:
    C.ok(False, "教材源文件可解析", "库里没有 role=textbook 的 PDF")

# 真实知识点：默认库产物 73-末级知识点总结.json 的 KP-N03-1
kp = None
for p in sorted((Path(config.LOG_DIR) / "knowledge").glob("73-*.json")):
    raw = json.loads(p.read_text(encoding="utf-8"))
    for item in (raw.get("knowledge_points") or []):
        if str(item.get("id")) == "KP-N03-1":
            kp = item
            break
C.ok(kp is not None, "读到真产物里的知识点（73-…json / KP-N03-1）",
     f"name={kp.get('name') if kp else '-'}")

if kp is not None:
    t1 = time.perf_counter()
    res = teach.kp_textbook(conn, 73, "KP-N03-1", kp=kp, limit=3)
    ms = (time.perf_counter() - t1) * 1000
    blocks = (res.get("material") or {}).get("blocks") or []
    tbx = res.get("textbook") or {}
    psg = tbx.get("passages") or []
    C.ok(bool(blocks) and all(b.get("text") for b in blocks),
         "① 知识点 → material[].chunk_id → chunks.text 取到原文",
         f"{len(blocks)} 块；首块 chunk#{blocks[0]['chunk_id']} "
         f"cite={blocks[0].get('cite')}" if blocks else "0 块")
    C.ok(bool(psg) and psg[0].get("page") and psg[0].get("source_ref"),
         "① 教材补充段落带页码 / source_ref / 章名 / 命中理由",
         f"{len(psg)} 段，最佳 {psg[0]['page_label']} 《{psg[0]['chapter']}》 "
         f"score={psg[0]['score']}（{ms:.0f} ms）" if psg else "0 段")

# ---------------- ② 单页渲染（零模型，首次真实耗时） ----------------

if tb:
    test_page = min(pages, 121)
    cpath = teach.page_cache_path(tb["id"], test_page, teach.TEACH_DPI)
    if cpath.is_file():
        cpath.unlink()                     # 清掉这一页的缓存，测真实的"首次"
    t1 = time.perf_counter()
    got1 = teach.page_png(conn, tb["id"], test_page)
    cold_ms = (time.perf_counter() - t1) * 1000
    t2 = time.perf_counter()
    got2 = teach.page_png(conn, tb["id"], test_page)
    warm_ms = (time.perf_counter() - t2) * 1000
    png_ok = (got1["png"][:8] == b"\x89PNG\r\n\x1a\n" and len(got1["png"]) > 20000
              and got1["png"] == got2["png"])
    C.ok(png_ok and got1["cache"] == "miss" and got2["cache"] == "hit",
         "② 教材单页按需渲染（PyMuPDF，零模型）+ 同页第二次走缓存",
         f"p.{test_page}：首次 {cold_ms:.0f} ms / {len(got1['png'])/1024:.0f} KB "
         f"→ 再次 {warm_ms:.0f} ms（cache={got2['cache']}）")
    SUMMARY.append(f"单页渲染 p.{test_page} 首次 {cold_ms:.0f} ms、缓存命中 {warm_ms:.0f} ms")
    missing = teach.page_cache_path(tb["id"], test_page, teach.TEACH_DPI)
    C.ok(missing.is_file(), "页图落当前学科目录（logs/teach_pages/<doc_id>/）",
         str(missing))

# ---------------- ③ 安全边界：越界 / 非教材 / 不存在 / 非法入参 ----------------

if tb:
    cases = [
        ((tb["id"], 0), 400, "页码 0"),
        ((tb["id"], pages + 1), 400, "页码越界"),
        ((tb["id"], -1), 400, "负页码"),
        (("../../windows/win.ini", 1), 400, "把路径当 doc_id"),
        ((tb["id"], "abc"), 400, "页码不是数字"),
        ((999999, 1), 404, "不存在的 doc_id"),
    ]
    # 找一份**非教材**的 PDF（课件）来试"只放行教材类 PDF"这条红线
    row = conn.execute(
        "SELECT id, title FROM documents WHERE COALESCE(role,'courseware')<>'textbook' "
        "AND kind='pdf' LIMIT 1").fetchone()
    if row is not None:
        cases.append(((int(row["id"]), 1), 403,
                      f"课件（doc#{row['id']}《{row['title']}》）"))
    results = []
    good = True
    for args, want, why in cases:
        code, msg = _reject(teach.page_png, conn, *args)
        good = good and code == want
        results.append(f"{why}→{code}{'' if code == want else f'(期望{want})'}")
    C.ok(good, "③ 安全边界：越界 / 非教材 / 不存在 / 非法入参全部被拒",
         "；".join(results))
    code, msg = _reject(teach.page_png, conn, tb["id"], pages + 1)
    C.ok(code == 400 and str(pages) in msg,
         "③ 越界拒绝文案**可操作**（写明教材总页数）", msg)

conn.close()

# ---------------- ④ 目录穿越：临时库里的恶意记录 ----------------

tmp_root = WORK / "verify_kb"
if tmp_root.exists():
    shutil.rmtree(tmp_root, ignore_errors=True)
for sub in ("raw", "notes", "index", "logs"):
    (tmp_root / sub).mkdir(parents=True, exist_ok=True)
outside = WORK / "outside_kb"                 # 临时资料库**之外**的目录
outside.mkdir(parents=True, exist_ok=True)
evil_pdf = outside / "evil.pdf"
evil_pdf.write_bytes(b"%PDF-1.4\n% fake\n")

old_root, old_db, old_log = config.ROOT, config.DB_PATH, config.LOG_DIR
try:
    config.ROOT = tmp_root
    config.DB_PATH = tmp_root / "index" / "study.db"
    config.LOG_DIR = tmp_root / "logs"
    tconn = db.connect(config.DB_PATH)
    db.init_db(tconn)
    good_pdf = tmp_root / "raw" / "教材样例.pdf"
    good_pdf.write_bytes(b"%PDF-1.4\n% fake\n")
    rows = [
        # (path, abs_path, kind, role, 期望码, 说明)
        ("raw/邪恶.pdf", str(evil_pdf), "pdf", "textbook", 403,
         "库内路径指向资料库之外（目录穿越）"),
        ("raw/伪装.pdf", str(tmp_root / "raw" / "missing.pdf"), "pdf", "textbook", 404,
         "文件不存在（路径在库内）"),
        ("raw/说明.txt", str(tmp_root / "raw" / "说明.txt"), "pdf", "textbook", 403,
         "后缀不是 .pdf"),
        ("raw/课件.pdf", str(good_pdf), "pdf", "courseware", 403,
         "role 是课件（只放行教材类 PDF）"),
        ("raw/教材.pptx", str(good_pdf), "pptx", "textbook", 403,
         "kind 不是 pdf"),
    ]
    (tmp_root / "raw" / "说明.txt").write_text("x", encoding="utf-8")
    ids = {}
    for i, (rel, abs_, kind, role, _want, _why) in enumerate(rows):
        cur = tconn.execute(
            "INSERT INTO documents(path, abs_path, kind, title, content_hash, role) "
            "VALUES(?,?,?,?,?,?)", (rel, abs_, kind, f"用例{i}", f"h{i}", role))
        ids[i] = int(cur.lastrowid)
    tconn.commit()
    # 再插一条**合法**教材，证明临时库这条链路本身是通的（不是"一律拒绝"）
    cur = tconn.execute(
        "INSERT INTO documents(path, abs_path, kind, title, content_hash, role) "
        "VALUES(?,?,?,?,?,?)",
        ("raw/教材样例.pdf", str(good_pdf), "pdf", "合法教材", "h9", "textbook"))
    good_id = int(cur.lastrowid)
    tconn.commit()

    det = []
    good = True
    for i, (_rel, _abs, _kind, _role, want, why) in enumerate(rows):
        code, msg = _reject(teach.resolve_textbook_pdf, tconn, ids[i])
        good = good and code == want
        det.append(f"{why}→{code}{'' if code == want else f'(期望{want})'}")
    C.ok(good, "④ ★ 目录穿越：库内 abs_path 逃出资料库目录 → 403（临时库实测）",
         "；".join(det))
    ok_code, ok_msg = _reject(teach.resolve_textbook_pdf, tconn, good_id)
    C.ok(ok_code == 0,
         "④ 临时库里的合法教材记录**没被误伤**（闸门不是一律拒绝）",
         f"resolve 通过={ok_code == 0}（{ok_msg}）")
    tconn.close()
finally:
    config.ROOT, config.DB_PATH, config.LOG_DIR = old_root, old_db, old_log

# ---------------- ④b 多学科：换一个"学科根"，链路照样通（页图落那个学科） ----------------
#
# 现实（实测 2026-10-14）：6 个真实学科库（分析化学/有机化学/生物制药工艺学/生物技术制药/
# 生物药剂学与药物动力学/药物化学）里**一个教材都没有**（role=textbook 的 PDF = 0 本），
# 教材只在默认库（药理）里。所以"多学科正确"只能用一个**真建的临时学科根**来证：
# 换根之后教材识别、单页渲染、知识点→教材原文三件事都要照样工作，且页图落在**那个学科**的
# logs 下（不是默认库的）。这里造的是真 PDF（PyMuPDF 写 3 页），不是打桩。

lab = WORK / "subject_lab"
if lab.exists():
    shutil.rmtree(lab, ignore_errors=True)
for sub in ("raw", "notes", "index", "logs/knowledge"):
    (lab / sub).mkdir(parents=True, exist_ok=True)
try:
    import pymupdf
    lab_pdf = lab / "raw" / "药物化学 第3版 教材.pdf"
    d = pymupdf.open()
    pages_txt = ["第3章 药物的体内代谢", "第一节 相反应", "第二节 相反应"]
    for i, t in enumerate(pages_txt):
        pg = d.new_page()
        pg.insert_text((60, 90), f"p.{i+1}", fontsize=12)
        pg.insert_text((60, 130), t, fontsize=15)
    d.save(str(lab_pdf))
    d.close()
    old2 = (config.ROOT, config.DB_PATH, config.LOG_DIR, config.RAW_DIR,
            config.INDEX_DIR, config.NOTES_DIR)
    config.ROOT = lab
    config.DB_PATH = lab / "index" / "study.db"
    config.LOG_DIR = lab / "logs"
    config.RAW_DIR = lab / "raw"
    config.INDEX_DIR = lab / "index"
    config.NOTES_DIR = lab / "notes"
    try:
        lconn = db.connect(config.DB_PATH)
        db.init_db(lconn)
        lconn.execute(
            "INSERT INTO documents(path, abs_path, kind, title, content_hash, role,"
            " corpus, n_chunks) VALUES(?,?,?,?,?,?,?,?)",
            ("raw/药物化学 第3版 教材.pdf", str(lab_pdf), "pdf", "药物化学 第3版 教材",
             "h1", "textbook", "main", 3))
        lid = int(lconn.execute("SELECT id FROM documents").fetchone()["id"])
        bodies = ["第3章 药物的体内代谢\n药物代谢又称生物转化，是药物在体内经酶催化发生的化学结构改变。",
                  "第一节 相反应\n氧化、还原、水解属于I相反应，主要由细胞色素P450酶系催化。",
                  "第二节 相反应\n葡萄糖醛酸结合、硫酸结合属于II相反应，多数产物失去活性。"]
        for i, txt in enumerate(bodies):
            lconn.execute(
                "INSERT INTO chunks(doc_id, ord, text, heading_path, source_ref,"
                " char_len) VALUES(?,?,?,?,?,?)",
                (lid, i, txt, "药物化学 第3版 教材 > 第3章 药物的体内代谢",
                 f"p.{i+1}", len(txt)))
        lconn.commit()
        seen = teach.textbook_docs(lconn)
        lgot = teach.page_png(lconn, lid, 2)
        lab_kp = {"id": "K-1", "doc_id": lid, "name": "药物的体内代谢与I相反应",
                  "points": ["药物代谢又称生物转化", "I相反应包括氧化、还原、水解"],
                  "material": [{"chunk_id": 1, "kind": "正文块", "ref": "p.1",
                                "page": 1, "chars": 30, "pages": [1]}]}
        lres = teach.kp_textbook(lconn, lid, "K-1", kp=lab_kp, limit=2)
        lconn.close()
    finally:
        (config.ROOT, config.DB_PATH, config.LOG_DIR, config.RAW_DIR,
         config.INDEX_DIR, config.NOTES_DIR) = old2
    in_lab = Path(lgot["cache_path"]).is_relative_to(lab)
    lps = (lres.get("textbook") or {}).get("passages") or []
    C.ok(len(seen) == 1 and lgot["png"][:8] == b"\x89PNG\r\n\x1a\n" and in_lab
         and lres["status"] == "ok" and lps,
         "④b 多学科：换一个学科根后，教材识别 / 单页渲染 / 知识点→教材原文 全通",
         f"临时学科库 doc#{lid}《{seen[0]['title']}》：{len(lgot['png'])} 字节 PNG、"
         f"页图落该学科 logs={in_lab}；匹配 {len(lps)} 段"
         f"{('，最佳 ' + lps[0]['page_label']) if lps else ''}")
except Exception as e:  # noqa: BLE001
    C.ok(False, "④b 多学科：换一个学科根后链路照常", f"{type(e).__name__}: {e}")

# ---------------- ⑤ 挂接就位 + 前端零改动 + 零模型 ----------------

dash = (ROOT / "tools" / "dashboard.py").read_text(encoding="utf-8")
teach_src = (ROOT / "core" / "teach.py").read_text(encoding="utf-8")
hooked = all(s in dash for s in (
    "from core import teach as teach_mod",
    'if path == "/api/teach-page":',
    'if path == "/api/kp-textbook":',
    "def _teach_page(self",
    "def api_kp_textbook(",
))
# 两条新路由都必须在全局 `_lock` **之前**（照 /img/slide 的既有纪律）
i_route = dash.index('if path == "/api/teach-page":')
i_kptb = dash.index('if path == "/api/kp-textbook":')
i_lock = dash.index("            with _lock:")
C.ok(hooked and i_route < i_lock and i_kptb < i_lock,
     "⑤ 两条路由已挂接，且都在全局 _lock 之前（长任务不阻塞）",
     f"teach-page@{i_route} kp-textbook@{i_kptb} < _lock@{i_lock}")

front = all(s in dash for s in (
    "mmRenderKp", "#mmkp-deck", "evPopShow", "mmNodes", "async function mmOpen",
))
C.ok(front, "⑤ 前端知识点窗口的既有标识原样在（本卡不改前端 UI）",
     "mmRenderKp / #mmkp-deck / evPopShow / mmNodes / mmOpen 命中")

banned = [s for s in ("chat.", "vision", "embed", "openai", "requests.",
                      "httpx", "get_embedder")
          if s in teach_src]
C.ok(not banned, "⑥ 零模型：core/teach.py 里没有任何模型/网络调用点",
     f"扫描结果={banned or '干净'}（PyMuPDF 渲染 + 词项打分）")

# ---------------- 结论 ----------------

SUMMARY.insert(0, f"教材 doc#{tb['id'] if tb else '-'}"
                  f"《{tb['title'] if tb else '-'}》{pages if tb else 0} 页")
verdict = "C0093-PASS" if C.all_pass else "C0093-FAIL"
P(f"\n{verdict} {C.n_pass}/{C.n_total}")
lines = [
    f"{verdict} 教材挂载后端（知识点→教材原文 + 按需单页渲染）{C.n_pass}/{C.n_total} 项通过",
    f"1) {SUMMARY[0] if SUMMARY else '教材'}；知识点→教材原文 OK（material 原文 + 段落带页码/cite）",
    f"2) {SUMMARY[1] if len(SUMMARY) > 1 else '单页渲染 OK'}（PyMuPDF，零模型调用）",
    "3) 安全：越界页码 / 非教材文档 / 不存在 doc / 把路径当 doc_id 全部被拒，文案含总页数",
    "4) ★ 临时库：目录穿越 abs_path 逃出资料库 → 403；换学科根后教材识别/渲染/匹配全通（页图落该学科 logs）",
    f"5) 路由已挂接且在全局 _lock 之前；前端 mmRenderKp / #mmkp-deck 零改动；明细 {LOG_PATH}",
]
_log.close()
for ln in lines:
    print(ln)
raise SystemExit(0 if C.all_pass else 2)
