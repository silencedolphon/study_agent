# -*- coding: utf-8 -*-
"""卡 C-0055 ★真浏览器验证（headless Edge + CDP）：问答页出处小窗里的"这一页"。

验什么（对照卡面验收标准逐条）：
  1. 真浏览器里点出处角标 → 小窗**真的显示出那一页的课件图**：
     抠 `<img>` 的 src/naturalWidth/naturalHeight，并 fetch 该 URL 记 HTTP 状态与字节数；
  2. 同一次：小窗里列出**该页归属的知识点**，点它能打开**知识点弹窗**；
     知识点 id 要**四方一致**：产物 `meta.coverage.page_owner` == 接口返回 ==
     前端 chip 的 data-kp == 弹窗 dataset.kp；
  3. 一页对应**多条**知识点时全部列出（真库里一页只属一条，所以造一份 fixture
     产物 doc#99998：同一条结构下两条知识点都覆盖 p.3）；
  4. 页码解析边界三种都不报错且给可理解提示：`p.N-M` 跨页、`p.N 图` 识图块、
     认不出页码的出处；
  5. 无图：真实文档的越界页（doc#4 只有 19 张图，问 p.99）→ "该页没有课件图缓存"；
  6. 真浏览器无 JS 报错；`check_dashboard_js.py` 通过；
  7. **零模型调用**（费用 0）：证据由真库 chunks 表组装后注入页面，
     只走"渲染 → 绑定 → 点开小窗"这条真实前端链路，不碰 /api/chat/stream；
  8. 改动面自查：与卡目录里的开工基线（`baseline/dashboard.before.py`）做 difflib，
     每个改动块前后 8 行窗口内**必须出现 "C-0055" 标记**（= 改动全部是本卡的），
     并逐一断言 K4/K5 区段（知识点弹窗/笔记/课件图/页签）的**函数体逐字节未变**。

用法（自起临时端口实例，跑完杀掉；不碰 8756）：
    venv\\Scripts\\python.exe tools\\verify_c0055_browser.py
    venv\\Scripts\\python.exe tools\\verify_c0055_browser.py --port 8899 --keep
"""
from __future__ import annotations

import argparse
import atexit
import difflib
import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config, subjects  # noqa: E402

# ★ 输出一律 UTF-8（**不依赖调用方加 `-X utf8`**）：Windows 上 Python 默认按
# locale（cp936）往管道写字节，而预检/CI 捕获 stdout 后按 UTF-8 解码——中文断言
# 行会变成乱码，判据里的中文字串就匹配不上（卡 C-0055 预检第一次的失败原因）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0055")
SHOTS = CARD / "shots"
DASH = ROOT / "tools" / "dashboard.py"
BASELINE = CARD / "baseline" / "dashboard.before.py"
# 本卡交付时生成的 diff（开工基线 → 本卡交付版）。它是**静态证据**：
# 之后别的卡再改同一个文件，也不会改变"本卡当初改了什么"这个事实。
DIFF = CARD / "dashboard.diff"
# fixture：真库里没有 doc#99998，产物只在验证期间存在，跑完（含异常）一律删掉
FIXTURE_DOC = 99998
FIXTURE = "99998-末级知识点总结.json"
# 同期**别的会话**正在重跑这份（C-0051：`run_kp.py --doc 5`），它随时可能变。
# 产物快照把它排除，否则"产物没被动过"的断言会被别人的写操作误伤。
EXTERNAL_BUSY = {"5-末级知识点总结.json"}
# 本卡**不许**碰的区段（别人的卡在改）：知识点弹窗内部 / 笔记 / 课件图 / 页签。
# 逐个抠函数体与开工基线比对，必须**逐字节未变**。
K4K5_FUNCS = ("mmShowSlide", "mmToggleDeck", "mmRenderKp", "mmOpenNode",
              "mmOpenKpList", "mmNoteEdit", "mmNoteSave", "mmNoteList",
              "mmNotePlace", "mountMindmap", "viewKps", "mmNodes")
# 这两个函数**被本卡动过**（都只为把"这次读哪份课件"传进去，见 mmOpenKp 的注释）：
#   mmOpenKp   ← 新增可选入参 docId（不传时行为与原来完全一致）
#   mmNextKp   ← 把 MMPOP.docId 传下去（否则跨文档时"下一知识点"会去别份课件里找）
TOUCHED = ("mmOpenKp", "mmNextKp")

OK = 0
BAD = 0
LINES: list[str] = []


def emit(s: str = "") -> None:
    print(s)
    LINES.append(s)


def check(cond, label: str, detail: str = "") -> bool:
    global OK, BAD
    if cond:
        OK += 1
        emit("  [OK] %s%s" % (label, ("  —— " + detail) if detail else ""))
    else:
        BAD += 1
        emit("  [★FAIL] %s%s" % (label, ("  —— " + detail) if detail else ""))
    return bool(cond)


def api(port: int, path: str, raw: bool = False):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=30) as r:
            body = r.read()
            return (r.status, body) if raw else json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s" % e.code} if not raw else (e.code, b"")
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)} if not raw else (0, b"")


# ---------------------------------------------------------------- 证据组装
def role_of(role: str, kind: str) -> str:
    role = (role or "").strip()
    if role in ("textbook", "courseware", "reference"):
        return role
    return "courseware" if (kind or "").lower() == "pptx" else "textbook"


def fetch_chunks(doc_id: int) -> list[dict]:
    """真库里的块（只读）。**不检索、不调模型**：直接按 ord 取。"""
    import sqlite3
    conn = sqlite3.connect("file:%s?mode=ro" % Path(config.DB_PATH).as_posix(),
                           uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT c.id AS chunk_id, c.doc_id, c.ord, c.source_ref, c.heading_path,"
            " c.text, d.title, d.kind, d.role FROM chunks c"
            " JOIN documents d ON d.id = c.doc_id WHERE c.doc_id=? ORDER BY c.ord",
            (doc_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def to_evidence(r: dict, idx: int = 1, source_ref: str | None = None) -> dict:
    from core.chat import ref_label
    doc_kind = role_of(r["role"], r["kind"])
    src = str(r["source_ref"] if source_ref is None else source_ref)
    head = str(r["heading_path"] or "")
    return {
        "idx": idx,
        "chunk_id": int(r["chunk_id"]),
        "doc_id": int(r["doc_id"]),
        "doc_title": str(r["title"] or ""),
        "doc_kind": doc_kind,
        "role_label": {"textbook": "教材", "courseware": "课件"}.get(doc_kind, "资料"),
        "is_figure": "图示说明" in head,
        "heading_path": head,
        "source_ref": src,
        "location": ref_label(doc_kind, src, r["kind"]),
        "label": "《%s》%s" % (r["title"], ref_label(doc_kind, src, r["kind"])),
        "text": str(r["text"] or "")[:900],
        "vec_score": 0.6123,
        "kw_score": 0.4411,
        "confidence": "ok",
    }


def pick(chunks: list[dict], pred) -> dict | None:
    for r in chunks:
        if pred(str(r["source_ref"] or "")):
            return r
    return None


# 三种出处形态（全库实测只有这三种；`p.N 图` 是识图块）
RE_PLAIN = re.compile(r"^p\.\d+$")
RE_FIGURE = re.compile(r"^p\.\d+\s*图$")
RE_SPAN = re.compile(r"^p\.\d+-\d+$")


def snap_artifacts(kdir: Path, skip: set[str]) -> dict:
    """产物目录的 sha256 快照（**排除 fixture 与正在被别的会话重跑的那份**）。

    本卡是纯只读的：跑完前后这份快照必须一模一样（顺带证明"零模型调用"——
    没有任何产物被重新生成过）。
    """
    out = {}
    for p in sorted(kdir.glob("*" + "-末级知识点总结.json")):
        if p.name in skip:
            continue
        try:
            out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def parse_hunks(diff_text: str) -> list:
    """从 unified diff 里抠出每个 hunk：`(基线起始行, 基线行数, 新起始行, 新行数, 文本)`。

    行号沿用 unified diff 的语义：`@@ -a,b +c,d @@` 指基线的 `a..a+b` 行。
    """
    out: list = []
    head: tuple | None = None
    buf: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            if head is not None:
                out.append(head + ("\n".join(buf),))
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            head = (int(m.group(1)), int(m.group(2) or 1),
                    int(m.group(3)), int(m.group(4) or 1)) if m else (0, 0, 0, 0)
            buf = [line]
        elif head is not None:
            buf.append(line)
    if head is not None:
        out.append(head + ("\n".join(buf),))
    return out


def func_block(src: str, name: str) -> str:
    """抠出一个顶层函数的源码块（到下一个顶层 `function ` 或分节注释为止）。

    用来做"K4/K5 区段逐字节未变"的断言——比我自己的眼睛可靠。
    ⚠ 名字必须**精确匹配到 `(`**：`mmOpenKp` 是 `mmOpenKpList` 的前缀，
    用 `str.find("\\nfunction mmOpenKp")` 会命中后者的定义（第一版就踩了）。
    """
    m = re.search(r"\n(?:async )?function %s\s*\(" % re.escape(name), src)
    if not m:
        return ""
    i = m.start()
    j = len(src)
    for pat in ("\nfunction ", "\nasync function ", "\n// ----------------"):
        k = src.find(pat, i + 10)
        if 0 <= k < j:
            j = k
    return src[i:j]


def make_fixture(kdir: Path, src_doc: int) -> tuple[Path | None, list[str]]:
    """造一份"一页挂两条知识点"的 fixture 产物（返回路径与两条 kp_id）。"""
    src = kdir / ("%d-末级知识点总结.json" % src_doc)
    if not src.exists():
        return None, []
    doc = json.loads(src.read_text(encoding="utf-8"))
    kps = [k for k in (doc.get("knowledge_points") or []) if isinstance(k, dict)]
    if len(kps) < 2:
        return None, []
    ids = []
    for k in kps[:2]:
        k["pages"] = [3]
        k["candidate_pages"] = [3]
        ids.append(str(k.get("id") or ""))
    meta = doc.setdefault("meta", {})
    meta["doc_id"] = FIXTURE_DOC
    meta["pipeline_version"] = "C-0049-v2"
    cov = meta.setdefault("coverage", {})
    cov["page_owner"] = {"3": ids[0]}
    cov["page_owner_note"] = ("卡 C-0055 验证 fixture：两条知识点都覆盖 p.3，"
                              "用来验『一页多条全部列出』")
    p = kdir / FIXTURE
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p, ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--doc", type=int, default=9)
    ap.add_argument("--keep", action="store_true", help="跑完不删 fixture（排障用）")
    ap.add_argument("--json", default=str(CARD / "browser_probe.json"))
    args = ap.parse_args()

    PY = ROOT / "venv" / "Scripts" / "python.exe"
    exe = str(PY) if PY.exists() else sys.executable

    proc = None
    if not args.port:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        args.port = s.getsockname()[1]
        s.close()
    blog = ROOT / "logs" / "_c0055_browser.out.log"
    blog.parent.mkdir(parents=True, exist_ok=True)
    fh = blog.open("wb")
    proc = subprocess.Popen(
        [exe, "-X", "utf8", "-u", str(DASH), "--port", str(args.port), "--no-open"],
        cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
    atexit.register(lambda: proc.terminate())
    for _ in range(100):
        time.sleep(0.4)
        if not api(args.port, "/api/stats").get("error"):
            break
    emit("  已自起临时实例：端口 %d（PID %s，解释器 %s）" % (args.port, proc.pid, exe))

    subjects.restore()
    kdir = Path(config.LOG_DIR) / "knowledge"
    SHOTS.mkdir(parents=True, exist_ok=True)
    emit("=" * 78)
    emit("卡 C-0055 ★真浏览器验证（headless Edge + CDP，端口 %d）" % args.port)
    emit("  学科 %s · 产物目录 %s" % (subjects.current(), kdir))
    emit("=" * 78)

    h = api(args.port, "/api/stats")
    if h.get("error"):
        emit("★临时实例 %d 打不通：%s" % (args.port, h["error"]))
        proc.terminate()
        return 2
    # 实例必须是**含本卡改动**的那份代码：后端路由在 / 的 HTML 里认不出，
    # 所以直接用新接口本身做探针（旧代码会 404）。
    probe = api(args.port, "/api/page-kps?doc_id=1&source_ref=p.1")
    if probe.get("error"):
        emit("★临时实例上没有 /api/page-kps（%s）——跑的不是本卡改过的代码？"
             % probe["error"])
        proc.terminate()
        return 2

    fixture_path, fixture_ids = make_fixture(kdir, args.doc)
    if fixture_path:
        atexit.register(lambda: fixture_path.unlink(missing_ok=True))
        emit("\n[0] fixture 产物 %s（两条知识点都覆盖 p.3：%s）"
             % (fixture_path.name, "、".join(fixture_ids)))
    # 产物快照（跑前）：本卡纯只读，跑完必须一模一样
    snap0 = snap_artifacts(kdir, {FIXTURE} | EXTERNAL_BUSY)

    # ---------------- 组装 cases（证据全部来自真库，零模型） ----------------
    chunks = fetch_chunks(args.doc)
    plain = pick(chunks, lambda s: RE_PLAIN.match(s))
    figure = pick(chunks, lambda s: RE_FIGURE.match(s))
    span = pick(chunks, lambda s: RE_SPAN.match(s))
    if not (plain and figure and span):
        emit("★doc#%d 缺少需要的块形态（plain/figure/span）" % args.doc)
        proc.terminate()
        return 2
    plain_page = int(plain["source_ref"][2:])
    other_chunks = fetch_chunks(4)          # doc#4 只有 19 张图，p.99 一定没图
    nograph = pick(other_chunks, lambda s: RE_PLAIN.match(s)) or plain

    cases = {
        "plain": {
            "question": "这一页讲的是什么？",
            "answer": "按资料，这一页的要点如下[1]。",
            "evidence": [to_evidence(plain)],
            "doc_id": args.doc, "page": plain_page,
        },
        "figure": {
            "question": "这一页的图说明了什么？",
            "answer": "图里画的是[1]。",
            "evidence": [to_evidence(figure)],
            "doc_id": args.doc,
        },
        "span": {
            "question": "这一段跨页的内容是什么？",
            "answer": "跨页的内容是[1]。",
            "evidence": [to_evidence(span)],
            "doc_id": args.doc,
        },
        "nopage": {
            "question": "这条出处在哪一页？",
            "answer": "这条出处没有页码[1]。",
            "evidence": [to_evidence(plain, source_ref="封面")],
            "doc_id": args.doc, "clickKp": False,
        },
        "nograph": {
            "question": "第 99 页讲什么？",
            "answer": "第 99 页的内容是[1]。",
            "evidence": [to_evidence(nograph, source_ref="p.99")],
            "doc_id": 4, "clickKp": False,
        },
        # 模型写了 [2] 但这一轮只有 1 条出处：小窗要如实说"未找到"，
        # 且**不该**为它去请求"这一页"（没有证据就没有 doc_id/source_ref）。
        "nocite": {
            "question": "这条出处在哪？",
            "answer": "按资料，答案是这样的[2]。",
            "evidence": [to_evidence(plain)],
            "doc_id": args.doc, "clickKp": False, "waitMs": 700,
        },
    }
    if fixture_ids:
        # fixture 的证据：把真块挂到 fixture 文档号上（**同一页两条知识点**）。
        # 只有 doc_id 换成 99998，其余字段照旧——前端拿的仍是 Evidence 的真实形态。
        mk_ev = to_evidence(plain, source_ref="p.3")
        mk_ev["doc_id"] = FIXTURE_DOC
        mk_ev["doc_title"] = "C-0055 fixture（一页挂两条知识点）"
        cases["multikp"] = {
            "question": "这一页有哪几条知识点？",
            "answer": "这一页有两条知识点[1]。",
            "evidence": [mk_ev],
            "doc_id": FIXTURE_DOC, "clickKp": False,
        }
    spec = {"order": list(cases), "cases": cases}
    cases_file = CARD / "cases_c0055.json"
    cases_file.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    emit("\n[1] 真跑浏览器（node tools\\probe_c0055_browser.mjs，%d 个 case）"
         % len(cases))
    r = subprocess.run(
        ["node", str(ROOT / "tools" / "probe_c0055_browser.mjs"),
         "--port", str(args.port), "--cases", str(cases_file),
         "--outdir", str(SHOTS), "--json", args.json],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=900)
    if r.returncode != 0:
        emit("  node 退出码 %s\n  stderr: %s" % (r.returncode, (r.stderr or "")[-1500:]))
    D = json.loads(Path(args.json).read_text(encoding="utf-8")) if Path(args.json).exists() else {}
    if not D:
        emit("★探针没有产出 JSON")
        proc.terminate()
        return 1
    C = D.get("cases") or {}
    emit("  探针挂载：%s" % D.get("mount"))

    # ---------------- 断言 ----------------
    emit("\n[2] 点角标 → 小窗里的这一页")
    p = C.get("plain") or {}
    check(bool(p.get("ok")), "点角标弹出了出处小窗", str(p.get("popupLoc", ""))[:60])
    check(p.get("hasExtra"), "小窗里有『这一页』区块（#evpop-extra）")
    img = next((i for i in (p.get("imgs") or []) if i.get("cls") == "evslideimg"), None)
    check(bool(img), "该页课件图 <img.evslideimg> 出现在小窗里",
          (img or {}).get("src", ""))
    check(bool(img and img.get("nw", 0) > 0 and img.get("nh", 0) > 0),
          "图**真的解码出来了**（naturalWidth×Height > 0）",
          "%s×%s" % ((img or {}).get("nw"), (img or {}).get("nh")))
    ih = p.get("imgHttp") or {}
    check(ih.get("status") == 200 and ih.get("type") == "image/png"
          and (ih.get("bytes") or 0) > 1000,
          "该图 URL HTTP 200 + image/png + 有字节",
          "%s %s bytes=%s" % (ih.get("url"), ih.get("status"), ih.get("bytes")))
    ex = p.get("extraRect") or [None, None]
    check(isinstance(ex[0], int) and ex[0] >= 0 and (ex[1] or 0) > 150,
          "『这一页』区块在小窗里占到了实际高度（图 + 知识点都在视野里）",
          "相对小窗顶部 +%s px，高 %s px" % (ex[0], ex[1]))

    emit("\n[3] 小窗里的该页知识点 → 点开知识点弹窗（四方一致）")
    art = json.loads((kdir / ("%d-末级知识点总结.json" % args.doc))
                     .read_text(encoding="utf-8"))
    owner = ((art.get("meta") or {}).get("coverage") or {}).get("page_owner") or {}
    want = str(owner.get(str(plain_page)) or "")
    be = api(args.port, "/api/page-kps?doc_id=%d&source_ref=%s"
             % (args.doc, urllib.parse.quote(str(plain["source_ref"]))))
    be_kp = (be.get("kps") or [{}])[0].get("kp_id", "")
    chips = p.get("kpChips") or []
    chip_kp = chips[0].get("kp") if chips else ""
    kc = p.get("kpClick") or {}
    check(bool(want), "产物 meta.coverage.page_owner[p.%d] 有归属知识点" % plain_page, want)
    check(be_kp == want, "接口 /api/page-kps 给的就是它", "%s vs %s" % (be_kp, want))
    check(chip_kp == want, "小窗里列出的 chip 就是它", chip_kp)
    check(kc.get("want") == want and kc.get("datasetKp") == want,
          "点 chip 打开的**知识点弹窗**就是这条", "dataset.kp=%s" % kc.get("datasetKp"))
    check(want and want in str(kc.get("loc", "")),
          "弹窗头部显示的知识点 id 与之一致", str(kc.get("loc", ""))[:60])
    check((kc.get("nPoints") or 0) > 0, "弹窗里真读出了要点（不是加载态/失败态）",
          "%s 条" % kc.get("nPoints"))
    off = kc.get("centerOffset") or [99, 99]
    check(abs(off[0]) <= 4 and abs(off[1]) <= 4,
          "知识点弹窗居中显示（与页签点节点的观感一致）", "偏移 %s px" % off)
    check(bool(kc.get("hasDeckBtn")) and bool(kc.get("hasNoteBtn")),
          "知识点弹窗的原功能都在（展开当页课件 / 记入笔记）")
    dk = kc.get("deck") or {}
    check(dk.get("opened") is True, "网状可达的另一半：点「展开当页课件」真展开了课件图区")
    check("/img/slide?doc_id=%d" % args.doc in str(dk.get("imgSrc"))
          and (dk.get("nw") or 0) > 0,
          "它放出的正是**出处那份课件**的页图（docId 改动自洽）",
          "%s %s×%s" % (dk.get("imgSrc"), dk.get("nw"), dk.get("nh")))

    emit("\n[4] 页码解析边界（三种都不报错 + 给可理解提示）")
    f = C.get("figure") or {}
    check(f.get("ok") and "识图块" in (f.get("extraText") or ""),
          "`p.N 图`（识图块）：解析出页号并在标题里标明",
          str((f.get("extraText") or "").split("\n")[0])[:50])
    check((f.get("imgHttp") or {}).get("status") == 200,
          "`p.N 图` 的页图照样取得回来", str((f.get("imgHttp") or {}).get("status")))
    sp = C.get("span") or {}
    check(len(sp.get("pagesChips") or []) >= 2,
          "`p.N-M` 跨页：给出整排页号", str([x.get("page") for x in (sp.get("pagesChips") or [])]))
    check(bool(sp.get("imgs")) and (sp.get("imgs") or [{}])[0].get("nw", 0) > 0,
          "跨页时默认显示第一页的图")
    np_ = C.get("nopage") or {}
    check(np_.get("ok") and "没有认得出的页码" in (np_.get("extraText") or ""),
          "认不出页码：不报错、给一句可理解的话",
          (np_.get("extraText") or "").replace("\n", " ")[:70])
    check(not np_.get("imgs"), "认不出页码时不放任何图（绝不给别的页充数）")
    nc = C.get("nocite") or {}
    check(nc.get("ok") and not nc.get("hasExtra")
          and "未找到第 2 条出处" in (nc.get("popupLoc") or ""),
          "角标指向不存在的出处：如实说「未找到」且不为它请求『这一页』",
          str(nc.get("popupLoc"))[:40])
    check(not nc.get("imgs"), "这时也不放任何图")

    emit("\n[5] 无图空态")
    ng = C.get("nograph") or {}
    check(ng.get("ok") and "该页没有课件图缓存" in (ng.get("extraText") or ""),
          "真实文档的越界页（doc#4 p.99）：明说没有图缓存",
          (ng.get("extraText") or "").replace("\n", " ")[:70])
    check(not ng.get("imgs"), "无图时不显示任何 <img>")

    emit("\n[6] 一页多条知识点：全部列出")
    mk = C.get("multikp") or {}
    mk_chips = mk.get("kpChips") or []
    check(len(mk_chips) >= 2, "同一页的两条知识点都列了出来",
          "、".join(x.get("kp", "") for x in mk_chips))
    check(set(fixture_ids) <= {x.get("kp") for x in mk_chips},
          "列出的正是 fixture 里那两条", "、".join(fixture_ids))
    mk_be = api(args.port, "/api/page-kps?doc_id=%d&source_ref=p.3" % FIXTURE_DOC)
    check(mk_be.get("n_kps") == 2 and mk_be.get("kp_state") == "v2",
          "接口对 fixture 也返回 2 条（kp_state=v2）",
          "n_kps=%s state=%s" % (mk_be.get("n_kps"), mk_be.get("kp_state")))
    check("该页没有课件图缓存" in (mk.get("extraText") or ""),
          "fixture 文档没有 figure_desc 目录 → 无图空态（同一屏里两条都对）")

    emit("\n[7] 浏览器与脚本卫生")
    check(not D.get("errors"), "真浏览器无 JS 报错",
          "；".join(D.get("errors") or [])[:200] or "0 条")
    check((D.get("closeCheck") or {}).get("display") == "none",
          "收尾 evPopClose 后小窗真的关掉了", str(D.get("closeCheck")))
    js = subprocess.run([exe, "-X", "utf8", str(ROOT / "tools" / "check_dashboard_js.py")],
                        cwd=str(ROOT), capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    check(js.returncode == 0 and "语法通过" in (js.stdout or ""),
          "check_dashboard_js.py 通过（内联 JS 语法 + 关键函数各 1 次）",
          (js.stdout or "").strip().splitlines()[-1] if js.stdout else "")

    emit("\n[8] 改动面自查（本卡 diff 自证 + 当前文件里功能仍在）")
    if not (BASELINE.exists() and DIFF.exists()):
        check(False, "开工基线与交付 diff 都在（baseline/dashboard.before.py、dashboard.diff）")
    else:
        # ⚠ 口径说明（卡 C-0055 第二次修订）：本卡交付后，**同期别的卡**（C-0053 改
        #   知识点页签顶部 + 版本分流）继续改同一个 dashboard.py。所以判"本卡碰了
        #   什么"**不能**用"当前文件 vs 开工基线"——那会把别人的改动算到本卡头上
        #   （第一版就是这么误报的：`viewKps` 变了、还多出 10 个无标记改动块）。
        #   改用**本卡自己的交付 diff**（`dashboard.diff`，静态不可篡改）做判据：
        #   它记录了"开工基线 → 本卡交付版"的全部改动，且不含后来别人的改动。
        old = BASELINE.read_text(encoding="utf-8")
        oldlines = old.splitlines()
        newlines = DASH.read_text(encoding="utf-8").splitlines()
        cur = "\n".join(newlines)
        diff_text = DIFF.read_text(encoding="utf-8")

        # (1) 本卡 diff 的每个 hunk 都要带 C-0055 标记（= 改动全是本卡的，逐处留痕）
        hunks = parse_hunks(diff_text)
        untagged = [h for h in hunks if "C-0055" not in h[4]]
        check(not untagged,
              "本卡 diff 的每个 hunk 都带 C-0055 标记（改动全是本卡的）",
              "hunk %d 个，未标记 %s" % (len(hunks), [(h[0], h[1]) for h in untagged][:3]))

        # (2) 本卡 diff **一点没碰** K4/K5 的函数体：hunk 的**基线行区间**与函数体
        #     区间不许相交（TOUCHED 的两个是本卡声明动过的，单独放行）。
        spans: dict[str, tuple[int, int]] = {}
        for n in K4K5_FUNCS + TOUCHED:
            b = func_block(old, n)
            if not b:
                continue
            i = old.find(b)
            # ⚠ 行号换算：`func_block` 返回的块**以块首行之前那个 `\n` 开头**，
            #   所以 `b.splitlines()[0]` 是个空串（不对应任何基线行），块的首行
            #   内容 = 第 `s` 行（s = i 之前的换行数 + 2），块的**最后一个有效行**
            #   = 第 `s + b.count("\n") - 1` 行 ⇒ 半开区间 `[s, s + b.count("\n"))`。
            #   （前两版分别写成 +1 与 +1/+1，各差一行，把边界上的 mmShowSlide /
            #    mmNoteList 误判成"与 hunk 相交"；已用"首行内容逐个核对"验证过。）
            s = old[:i].count("\n") + 2
            spans[n] = (s, s + b.count("\n"))
        missing = [n for n in K4K5_FUNCS + TOUCHED if n not in spans]
        check(not missing, "待比的 K4/K5 函数在基线里都找得到（名字没写错）",
              "找不到：%s" % missing if missing else "%d 个" % len(spans))
        hit = []
        for a, ln, _na, _nln, _t in hunks:
            lo, hi = a, a + max(ln, 1)   # hunk 覆盖的基线行区间 [lo, hi)
            for n, (s, e) in spans.items():
                if n in TOUCHED:
                    continue
                if lo < e and hi > s:
                    hit.append((n, lo, hi, s, e))
        check(not hit, "本卡 diff 没碰 K4/K5 的函数体（基线行区间不相交）",
              ("越界的：%s" % hit[:3]) if hit
              else "逐个比对了 %s" % "、".join(K4K5_FUNCS))
        touched_hit = sorted({n for a, ln, _na, _nln, _t in hunks
                              for n in TOUCHED
                              if a < spans.get(n, (0, 0))[1]
                              and a + max(ln, 1) > spans.get(n, (0, 0))[0]})
        check(touched_hit == sorted(TOUCHED),
              "本卡动过的 K4 函数就是声明的那两个（%s）" % "、".join(TOUCHED),
              "实际被动到的：%s" % touched_hit)

        # (3) 本卡 diff 的增删行数（未整文件重写）
        removed = sum(1 for x in diff_text.splitlines()
                      if x.startswith("-") and not x.startswith("---"))
        added = sum(1 for x in diff_text.splitlines()
                    if x.startswith("+") and not x.startswith("+++"))
        check(removed < 60 and added < 700,
              "本卡改动是局部的（只删了几行、只增了几百行，不是整文件重写）",
              "删 %d 行 / 增 %d 行（基线 %d 行）" % (removed, added, len(oldlines)))

        # (4) 当前 dashboard.py 里本卡的函数/容器**都还在且各 1 处**
        #     （后续卡改同一文件，但不能把本卡的东西删掉/改重）
        tokens = ("function evCiteExtras(", "function evCiteExtrasHtml(",
                  "function evCiteBind(", "function evCiteSlide(",
                  "function evCiteAlive(", 'id="evpop-extra"',
                  "async function mmOpenKp(kpId, name, el, keepDeck, docId)",
                  "def api_page_kps(", "def _ref_pages(", "def _page_kp_index(")
        wrong = ["%s×%d" % (t, cur.count(t)) for t in tokens if cur.count(t) != 1]
        check(not wrong, "当前 dashboard.py 里本卡的函数/容器都在且各 1 处",
              "异常：%s" % wrong if wrong else "%d 项" % len(tokens))

        # (5) 同期别的会话对同一文件的改动——**如实列出，不判失败**（供联合核验）
        sm2 = difflib.SequenceMatcher(None, oldlines, newlines, autojunk=False)
        foreign = []
        for tag, i1, i2, j1, j2 in sm2.get_opcodes():
            if tag == "equal" or (i1 >= i2 and j1 >= j2):
                continue
            win = "\n".join(newlines[max(0, j1 - 20):min(len(newlines), j2 + 20)])
            if "C-0055" not in win:
                foreign.append("%s@%d" % (tag, j1 + 1))
        emit("  · 同期别的会话对同一文件的改动（**不是本卡**，仅供联合核验）：%s"
             % ("、".join(str(x) for x in foreign[:8]) + ("…" if len(foreign) > 8 else "")
                if foreign else "无"))

    emit("\n[9] 全库逐份：页 → 知识点 与产物 page_owner 一致（v2 与 v3 都要读得出）")
    vers: dict[str, int] = {}
    mism: list[str] = []
    n_doc = 0
    for p in sorted(kdir.glob("*末级知识点总结.json")):
        head = p.name.split("-")[0]
        if p.name == FIXTURE or not head.isdigit():
            continue
        did = int(head)
        try:
            art_i = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        m_i = art_i.get("meta") or {}
        ver_i = str(m_i.get("pipeline_version") or "")
        owner_i = (m_i.get("coverage") or {}).get("page_owner") or {}
        vers[ver_i] = vers.get(ver_i, 0) + 1
        if not owner_i:
            continue
        n_doc += 1
        pg = sorted(int(x) for x in owner_i if str(x).lstrip("-").isdigit())[0]
        want_i = str(owner_i.get(str(pg)) or "")
        r_i = api(args.port, "/api/page-kps?doc_id=%d&page=%d" % (did, pg))
        got_i = [k.get("kp_id") for k in (r_i.get("kps") or [])]
        if want_i not in got_i or r_i.get("kp_state") not in ("v2", "v3"):
            mism.append("doc#%d(%s) p.%d → 期望 %s，实得 %s/%s"
                        % (did, ver_i, pg, want_i, r_i.get("kp_state"), got_i))
    check(not mism, "逐份对照：接口给出的该页知识点 == 产物 page_owner（%d 份）" % n_doc,
          "；".join(mism[:3]) if mism else "版本分布 %s" % json.dumps(vers, ensure_ascii=False))
    check("C-0052-v3" in vers,
          "其中包含 v3 产物（`C-0052-v3`：页签常量还没跟上，小窗读得出）",
          "v3 %d 份 / v2 %d 份（doc#5 正被同期 C-0051 会话重跑，读数以当时那份为准）"
          % (vers.get("C-0052-v3", 0), vers.get("C-0049-v2", 0)))

    emit("\n[10] 本卡纯只读：产物一个字节都没被动过（也等于零模型调用）")
    snap1 = snap_artifacts(kdir, {FIXTURE} | EXTERNAL_BUSY)
    diff_files = sorted(set(snap0) ^ set(snap1)) + \
        sorted(k for k in set(snap0) & set(snap1) if snap0[k] != snap1[k])
    check(not diff_files, "产物目录 sha256 跑前跑后一致（%d 份）" % len(snap0),
          "变了的：%s" % diff_files[:3] if diff_files else
          "排除 fixture 与被别的会话重跑的 %s" % "、".join(sorted(EXTERNAL_BUSY)))

    emit("\n[11] 截图")
    for k, v in (D.get("shots") or {}).items():
        emit("  %-9s %s" % (k, v))

    # ---------------- 清理 ----------------
    if fixture_path and not args.keep:
        try:
            fixture_path.unlink()
            emit("\n已删除 fixture 产物 %s" % fixture_path.name)
        except OSError as e:
            emit("★fixture 删除失败：%s" % e)

    emit("\n" + "=" * 78)
    emit("结果：%d 项通过，%d 项失败" % (OK, BAD))
    (CARD / "verify_report.txt").write_text("\n".join(LINES) + "\n", encoding="utf-8")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
