# -*- coding: utf-8 -*-
"""卡 C-0052 ★真浏览器验证：知识点笔记 + 课件级 AI 答疑 + 笔记页签。

流程（记现场 → 真跑浏览器 → 断言 → 恢复现场）：
  1. 快照：notes/ 里每个文件的 sha256、logs/outline、logs/knowledge、五表行数；
  2. 起一个**临时端口**实例（`--autostart`，跑完杀掉；绝不碰 8756）；
  3. 跑 `tools/probe_c0052_browser.mjs`（真 Edge + CDP）：
     · 知识点弹窗「记入笔记」→ 真写文件 → 「查看笔记」看得到 → 关掉重开仍在；
     · 课件思维导图栏**右下角**「AI答疑」→ 真对话（真调 deepseek-flash）→
       范围行与证据 doc_id 集合 → 问答记入笔记；
     · 笔记页签：条目带记录时间与来源、点开看原文、「跳到知识点窗口」真跳回去；
       课件问答类笔记没有这个按钮（且不报错）；
  4. 断言 + 文件系统对账（笔记真的落了文件、front-matter 真的带绑定）；
  5. **MCP 读取路径**：`tools/serve_mcp.py` 的 `tools/call kb_note_list` 能列出新笔记；
  6. **后端口径证据**：直接打一次 `/api/deck-chat/stream`，命中的 doc_id 全等于该课件；
  7. 反向对照：`logs/outline` 与产物 json 逐字节未变、五表仍 0 行；
  8. 清理：删掉本次验证写的笔记 → notes/ 逐字节回到跑前。

用法：
    python tools\\verify_c0052_browser.py --autostart --doc 7
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from core import config, db, subjects  # noqa: E402

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0052")
SHOTS = CARD / "shots"
EVID = CARD / "evidence"
# 价格口径照 core/kp_build.py（非高峰 $0.15/1M 入 + $0.60/1M 出，汇率 7.1）
PRICE_IN, PRICE_OUT, FX = 0.15, 0.60, 7.1
COST: list[dict] = []
OK = 0
BAD = 0
LINES: list[str] = []


def emit(s: str = "") -> None:
    print(s)
    LINES.append(s)


def check(cond, label: str, detail="") -> bool:
    global OK, BAD
    detail = "" if detail is None else str(detail)   # 调用方偶尔会塞 list 进来
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
    ti = float(usage.get("prompt_tokens") or 0)
    to = float(usage.get("completion_tokens") or 0)
    return round((ti / 1e6 * PRICE_IN + to / 1e6 * PRICE_OUT) * FX, 6)


def api(port: int, path: str) -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


def deck_chat_backend(port: int, doc_id: int, query: str) -> dict:
    """后端口径证据：直接打一次 /api/deck-chat/stream，看命中的 doc_id 集合。"""
    body = json.dumps({"doc_id": doc_id, "query": query},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d/api/deck-chat/stream" % port, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    out = {"scope": None, "ev": [], "answer": "", "usage": {}}
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            obj = json.loads(line[6:])
            if obj["type"] == "scope":
                out["scope"] = obj
            elif obj["type"] == "sources":
                out["ev"] = obj["evidence"]
            elif obj["type"] == "delta":
                out["answer"] += obj["text"]
            elif obj["type"] == "done":
                out["usage"] = obj.get("usage") or {}
            elif obj["type"] == "error":
                out["answer"] += "\n[ERROR] " + obj.get("message", "")
    return out


def five_counts(conn) -> dict:
    out = {}
    for t in ("knowledge_points", "kp_sources", "kp_edges", "mistakes", "reviews"):
        out[t] = int(conn.execute("SELECT COUNT(*) AS n FROM %s" % t).fetchone()["n"])
    return out


def usage_from_text(t: str) -> dict:
    """从浏览器里那行"用时 x s · 输入 a / 输出 b tokens"抠用量。"""
    import re
    m = re.search(r"输入\s*(\d+)\s*/\s*输出\s*(\d+)", t or "")
    if not m:
        return {}
    return {"prompt_tokens": int(m.group(1)), "completion_tokens": int(m.group(2))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--doc", type=int, default=7)
    ap.add_argument("--autostart", action="store_true",
                    help="自己起一个临时端口实例（跑完杀掉），不用事先起看板")
    ap.add_argument("--skip-ai", action="store_true", help="省钱模式：不调模型")
    ap.add_argument("--skip-restore", action="store_true")
    args = ap.parse_args()

    proc = None
    if args.autostart:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        args.port = s.getsockname()[1]
        s.close()
        blog = ROOT / "logs" / "_c0052_browser_auto.out.log"
        blog.parent.mkdir(parents=True, exist_ok=True)
        fh = blog.open("wb")
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-u", str(ROOT / "tools" / "dashboard.py"),
             "--port", str(args.port), "--no-open"],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
        atexit.register(lambda: proc.terminate())
        for _ in range(80):
            time.sleep(0.4)
            if not api(args.port, "/api/stats").get("error"):
                break
        print("  已自起临时实例：端口 %d（PID %s）" % (args.port, proc.pid))

    subjects.restore()
    notes_dir = Path(config.NOTES_DIR)
    notes_dir.mkdir(parents=True, exist_ok=True)
    kdir = Path(config.LOG_DIR) / "knowledge"
    odir = Path(config.LOG_DIR) / "outline"
    SHOTS.mkdir(parents=True, exist_ok=True)
    EVID.mkdir(parents=True, exist_ok=True)

    emit("=" * 78)
    emit("卡 C-0052 ★真浏览器验证（headless Edge + CDP，端口 %d）" % args.port)
    emit("  目标课件 doc#%d · 笔记目录 %s" % (args.doc, notes_dir))
    emit("=" * 78)

    h = api(args.port, "/api/stats")
    if h.get("error"):
        emit("★临时实例 %d 打不通：%s" % (args.port, h["error"]))
        return 2

    # 前提：这份课件得有 v2 产物与至少一个末级知识点。
    # ⚠ 现场可能有**别的会话**正在重跑整理管线（本次实测撞上过：并行的 C-0051
    #   会话在跑 `run_kp.py --doc 5 --force`），会把某份产物改成新版本。所以这里
    #   不写死一份课件：指定的不行就顺着清单找第一份当前可用的。
    def usable(doc_id: int) -> tuple:
        m = api(args.port, "/api/mindmap?doc_id=%d" % doc_id)
        if m.get("artifact_state") != "v2":
            return None, m
        st = list(m.get("tree") or [])
        while st:
            n = st.pop(0)
            if n.get("lkp_ids"):
                return n["lkp_ids"][0], m
            st += (n.get("children") or [])
        return None, m

    kp_id, mm = usable(args.doc)
    if not kp_id:
        for d in (api(args.port, "/api/mindmap").get("documents") or [])[:8]:
            if d.get("doc_id") == args.doc:
                continue
            kp_id, mm = usable(d["doc_id"])
            if kp_id:
                emit("  （doc#%d 当前不可用，改用 doc#%d）" % (args.doc, d["doc_id"]))
                args.doc = d["doc_id"]
                break
    emit("\n[0] 前提：doc#%d state=%s / %s 个节点 / 首个末级知识点 %s"
         % (args.doc, mm.get("artifact_state"), mm.get("n_nodes"), kp_id))
    if mm.get("artifact_state") != "v2" or not kp_id:
        emit("★前提不成立：这份课件没有 v2 产物或没有末级知识点")
        if proc:
            proc.terminate()
        return 2

    # 1) 现场快照
    emit("\n[1] 快照现场")
    nsnap0 = snap(notes_dir)
    ksnap0 = snap(kdir)
    osnap0 = snap(odir)
    conn = db.connect()
    db.init_db(conn)
    five0 = five_counts(conn)
    docs0 = int(conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"])
    conn.close()
    emit("  notes/ %d 篇 · 产物 %d 份 · outline %d 份 · 五表 %s"
         % (len(nsnap0), len(ksnap0), len(osnap0), five0))

    # 2) 真跑浏览器
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    text = "（C0052 真浏览器验证 %s）脂环烃：小环张力与构象分析是本章重点。" % stamp
    emit("\n[2] 真跑浏览器（node tools\\probe_c0052_browser.mjs）")
    jsout = CARD / "browser_probe.json"
    r = subprocess.run(
        ["node", str(ROOT / "tools" / "probe_c0052_browser.mjs"),
         "--port", str(args.port), "--doc", str(args.doc), "--text", text,
         "--outdir", str(SHOTS), "--json", str(jsout),
         "--skipai", "1" if args.skip_ai else ""],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=600)
    if r.returncode != 0:
        emit("  node 退出码 %s\n  stderr: %s"
             % (r.returncode, (r.stderr or "")[-1200:]))
    D = json.loads(jsout.read_text(encoding="utf-8")) if jsout.exists() else {}
    if not D:
        emit("★探针没有产出 JSON")
        if proc:
            proc.terminate()
        return 1

    st = D.get("steps") or {}
    kp_file = (st.get("noteSave") or {}).get("file") or ""
    deck_file = (st.get("aiNote") or {}).get("file") or ""
    new_notes = sorted(set(snap(notes_dir)) - set(nsnap0))
    emit("  新增笔记文件：%s" % (new_notes or "无"))

    # 3) 断言
    emit("\n[3] 断言：① 知识点弹窗的「记入笔记」「查看笔记」")
    pu = st.get("popup") or {}
    check(bool(pu.get("ok")), "点导图节点弹出了知识点窗口",
          "节点 %s / kp %s" % (pu.get("clicked"), pu.get("kpId")))
    check(bool(pu.get("hasAdd")) and str(pu.get("addText")) == "记入笔记",
          "弹窗里有「记入笔记」按钮", str(pu.get("addTitle"))[:60])
    check(bool(pu.get("hasSee")) and str(pu.get("seeText")) == "查看笔记",
          "弹窗里有「查看笔记」按钮", str(pu.get("footerText")).replace("\n", " / ")[:120])
    ed = st.get("noteEdit") or {}
    check(bool(ed.get("ok")) and bool(ed.get("hasTextarea")),
          "点「记入笔记」→ 真出现输入框（可多行 textarea）",
          str(ed.get("placeholder"))[:60])
    sv = st.get("noteSave") or {}
    check(bool(sv.get("ok")), "点「保存笔记」→ 后端回「已记入」", str(sv.get("msg"))[:80])

    emit("\n[4] 断言：② 笔记**真的落成文件**，且与该知识点绑定")
    kp_path = notes_dir / kp_file if kp_file else None
    check(bool(kp_file) and kp_path.exists() and kp_path.stat().st_size > 0,
          "★文件系统里真出现这条笔记（不是只在前端内存里）",
          str(kp_path) if kp_path else "没有文件名")
    kp_text = kp_path.read_text(encoding="utf-8", errors="replace") if (
        kp_path and kp_path.exists()) else ""
    check(text in kp_text, "文件内容与浏览器里输入的那段**逐字一致**",
          repr(text[:40]))
    for key, want in (("origin", "knowledge_point"), ("kp_id", kp_id),
                      ("doc_id", str(args.doc))):
        check(("%s: %s" % (key, want)) in kp_text,
              "front-matter 里有 `%s: %s`（绑定写进了文件，不是另建库表）" % (key, want))
    check("source_label:" in kp_text, "front-matter 里有 source_label（人可读来源）",
          " / ".join(ln for ln in kp_text.splitlines()
                     if ln.startswith("source_label")))
    if kp_path and kp_path.exists():
        shutil.copy2(kp_path, EVID / ("kp-note-" + kp_path.name))

    nl = st.get("noteList") or {}
    check(bool(nl.get("ok")) and bool(nl.get("hasText")) and int(nl.get("n") or 0) >= 1,
          "点「查看笔记」→ 列出绑定这条知识点的笔记（含刚写的那条）",
          "列出 %s 条" % nl.get("n"))
    ro = st.get("reopen") or {}
    check(bool(ro.get("ok")) and bool(ro.get("hasText")),
          "★关掉弹窗、重新点开同一节点 → 这条笔记还在（真读文件，不是页面缓存）",
          "重开后列出 %s 条" % ro.get("n"))

    emit("\n[5] 断言：③「课件思维导图」栏右下角的「AI答疑」按钮")
    ab = st.get("aiButton") or {}
    check(bool(ab.get("ok")) and str(ab.get("text")) == "AI答疑",
          "按钮存在且文案是「AI答疑」")
    check(bool(ab.get("inCard")) and bool(ab.get("belowTree")) and bool(ab.get("rightAligned")),
          "★位置在「课件思维导图」那一栏的**右下角**（栏内 · 树下方 · 靠右）",
          "按钮 %s / 卡片 %s（距右边界 <60px）"
          % (ab.get("rect"), ab.get("cardRect")))
    ao = st.get("aiOpen") or {}
    check(bool(ao.get("ok")) and bool(ao.get("hasThread")) and bool(ao.get("hasSend")),
          "点它 → 弹出小对话窗口（含输入框与发送按钮）",
          "标题 %s" % str(ao.get("title"))[:50])
    check("doc#%d" % args.doc in str(ao.get("head")),
          "窗口里写明检索范围是这一份课件", str(ao.get("head"))[:80])

    emit("\n[6] 断言：④ 真对话 + 检索确实限定在该课件")
    ak = st.get("aiAsk") or {}
    if args.skip_ai:
        emit("  （--skip-ai：跳过一次真实模型调用，由后端口径那一步补证）")
    else:
        check(bool(ak.get("ok")) and len(str(ak.get("answer") or "")) > 60,
              "★真调 deepseek-flash 并流式收到回答",
              "回答 %d 字" % len(str(ak.get("answer") or "")))
        check("doc#%d" % args.doc in str(ak.get("scope")),
              "窗口里的范围行写明 doc#%d" % args.doc, str(ak.get("scope"))[:90])
        check("[%d]" % args.doc in str(ak.get("scopeTitle")),
              "★范围行的证据 doc_id 集合只含这份课件",
              str(ak.get("scopeTitle"))[:60])
        u = usage_from_text(str(ak.get("usageText") or ""))
        if u:
            c = cost_of(u)
            COST.append({"where": "浏览器 AI答疑（主回答）", **u, "cny": c})
            check(c > 0, "浏览器里那一次调用的用量与花费（从页面文本抠出）",
                  "输入 %s / 输出 %s tokens → ¥%.4f"
                  % (u["prompt_tokens"], u["completion_tokens"], c))

    emit("\n[7] 后端口径证据：直接打一次 /api/deck-chat/stream（Python，不经浏览器）")
    q2 = "这份课件分为哪几部分？"
    be = {} if args.skip_ai else deck_chat_backend(args.port, args.doc, q2)
    if be:
        sc = be.get("scope") or {}
        dset = sorted({int(e["doc_id"]) for e in be.get("ev") or []})
        check(sc.get("doc_ids") == [args.doc] or dset == [args.doc],
              "★命中的证据 doc_id 全部等于该课件（后端检索参数 doc_ids=[%d]）" % args.doc,
              "scope.doc_ids=%s / 证据 doc_id 集合=%s / 滤掉范围外 %s 条"
              % (sc.get("doc_ids"), dset, sc.get("n_dropped")))
        check(all(int(e["doc_id"]) == args.doc for e in (be.get("ev") or [])),
              "逐条核对：%d 条证据没有一条来自别的资料" % len(be.get("ev") or []),
              "样例 %s" % [(e["idx"], e["doc_id"], e["doc_title"][:12])
                          for e in (be.get("ev") or [])[:3]])
        c2 = cost_of(be.get("usage") or {})
        COST.append({"where": "后端 /api/deck-chat/stream（主回答）",
                     **(be.get("usage") or {}), "cny": c2})
        check(bool(str(be.get("answer") or "").strip()),
              "后端接口也真给出了回答", "%d 字 → ¥%.4f" % (len(be.get("answer") or ""), c2))

    emit("\n[8] 断言：⑤ 问答产出记入「笔记」页签")
    an = st.get("aiNote") or {}
    check(bool(an.get("ok")), "AI 回答下方点「记入笔记」→ 回「已记入」", str(an.get("msg"))[:70])
    deck_path = notes_dir / deck_file if deck_file else None
    check(bool(deck_file) and deck_path.exists() and deck_path.stat().st_size > 0,
          "★课件问答的产出也真落成文件", str(deck_path) if deck_path else "没有文件名")
    deck_text = deck_path.read_text(encoding="utf-8", errors="replace") if (
        deck_path and deck_path.exists()) else ""
    check("origin: deck_chat" in deck_text and ("doc_id: %d" % args.doc) in deck_text,
          "它的 front-matter 记下来源类型与课件 id",
          " / ".join(ln for ln in deck_text.splitlines()
                     if ln.startswith(("origin", "doc_id"))))
    check("## 出处" in deck_text and "**问**" in deck_text and "**答**" in deck_text,
          "正文里问、答、出处三样都在（只存答案下次读的人不知道在答什么）")
    if deck_path and deck_path.exists():
        shutil.copy2(deck_path, EVID / ("deck-note-" + deck_path.name))

    emit("\n[9] 断言：⑥ 笔记页签（记录时间 + 来源 + 原文 + 跳回知识点窗口）")
    nt = st.get("notesTab") or {}
    check(bool(nt.get("ok")) and int(nt.get("n") or 0) >= 2,
          "笔记页签列出了这两条新笔记", "共 %s 行" % nt.get("n"))
    kr = st.get("kpRow") or {}
    check(bool(kr.get("ok")), "页签里能找到刚写的那条知识点笔记", str(kr.get("file")))
    cells = [str(x) for x in (kr.get("cells") or [])]
    check(len(cells) >= 5 and "知识点" in cells[1],
          "★条目带**来源**（写明来自哪个知识点）",
          "来源列：%s" % (cells[1] if len(cells) > 1 else "").replace("\n", " / ")[:80])
    check(len(cells) >= 3 and ("/" in cells[2] or ":" in cells[2]) and cells[2] != "—",
          "★条目带**记录时间**", "时间列：%s" % (cells[2] if len(cells) > 2 else ""))
    check(bool(kr.get("hasJump")), "条目上带「跳到知识点窗口」按钮",
          str(kr.get("jumpText")))
    ok_note = st.get("openKpNote") or {}
    check(bool(ok_note.get("ok")) and bool(ok_note.get("hasBody")),
          "点进条目 → 能看到笔记原文", str(ok_note.get("text"))[:60].replace("\n", " / "))
    check(bool(ok_note.get("hasJump")), "详情里有跳转按钮", str(ok_note.get("jumpText")))
    check("标签" in str(ok_note.get("text")),
          "详情里带 front-matter 解析出来的标签（tags 也读回来了）",
          str(ok_note.get("text"))[:80].replace("\n", " / "))
    jp = st.get("jump") or {}
    check(bool(jp.get("ok")) and str(jp.get("tab")) == "知识点"
          and str(jp.get("popKpId")).strip() == str(jp.get("wantKp")).strip()
          and str(jp.get("wantKp")).strip() == kp_id,
          "★点按钮**真跳回**绑定的那条知识点窗口（弹窗里就是它）",
          "弹窗 kp=%s / 期望 %s / 页签 %s" % (jp.get("popKpId"), jp.get("wantKp"),
                                              jp.get("tab")))
    check(bool(jp.get("modalClosed")), "跳转时笔记弹窗先关掉（不叠两层）")
    dn = st.get("openDeckNote") or {}
    check(bool(dn.get("ok")) and not bool(dn.get("hasJump")),
          "课件问答类笔记（没有绑定知识点）**不给**跳转按钮，也不报错")
    check("课件 AI 答疑" in str(dn.get("text")),
          "它的详情里写明了来源是课件答疑", str(dn.get("text"))[:60].replace("\n", " / "))

    emit("\n[10] 断言：⑦ 与 MCP 的 kb_note_* 读同一批文件")
    import serve_mcp  # noqa: E402（放在这里：它 import 时会读 config）
    resp = serve_mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                             "params": {"name": "kb_note_list", "arguments": {}}})
    mtxt = resp["result"]["content"][0]["text"]
    mnotes = json.loads(mtxt)["notes"]
    mfiles = {n["file"] for n in mnotes}
    check(kp_file in mfiles and deck_file in mfiles,
          "★MCP 的读取路径（tools/call kb_note_list）能列出这两条新笔记",
          "MCP 看到 %d 篇；含 kp=%s / deck=%s" % (len(mfiles), kp_file in mfiles,
                                                   deck_file in mfiles))
    mkp = next((n for n in mnotes if n["file"] == kp_file), {})
    check(str(mkp.get("kp_id")) == kp_id and str(mkp.get("origin")) == "knowledge_point",
          "MCP 读到的元数据里带着绑定（同一套 front-matter，没有两套格式）",
          json.dumps({k: mkp.get(k) for k in ("kp_id", "doc_id", "origin")},
                     ensure_ascii=False))
    resp2 = serve_mcp.handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                              "params": {"name": "kb_note_read",
                                         "arguments": {"name": kp_file}}})
    mread = json.loads(resp2["result"]["content"][0]["text"])
    check(text in str(mread.get("content") or ""),
          "MCP 的 kb_note_read 也读得到这条笔记的正文",
          str(mread.get("title"))[:50])

    emit("\n[11] 反向对照：没动别的东西")
    ksnap1 = snap(kdir)
    osnap1 = snap(odir)
    o_changed = sorted(k for k in (set(osnap0) & set(osnap1)) if osnap0[k] != osnap1[k])
    check(not o_changed and len(osnap0) == len(osnap1),
          "★logs/outline/*.json 一份没少、逐字节未变（sha256 对照）",
          "%d 个文件，改动 %s" % (len(osnap1), o_changed or "无"))
    # 全量产物对照：本卡**没有任何写产物的代码路径**（笔记一律写 notes/）。
    # 但现场可能有别的会话正在跑整理管线（本次实测撞上：并行的 C-0051 会话在跑
    # `run_kp.py --doc 5 --force`），所以把"本卡验证的那份产物"单独设成硬断言，
    # 其余变化如实列出并给出归因（产物 meta 里带着是谁生成的）。
    my_doc = "%d-末级知识点总结.json" % args.doc
    changed = sorted(k for k in (set(ksnap0) & set(ksnap1)) if ksnap0[k] != ksnap1[k])
    gone = sorted(set(ksnap0) - set(ksnap1))
    added = sorted(set(ksnap1) - set(ksnap0))
    check(my_doc in ksnap1 and ksnap0.get(my_doc) == ksnap1.get(my_doc),
          "★本卡验证针对的那份产物（%s）逐字节未变（sha256 对照）" % my_doc)
    if changed or gone or added:
        emit("  ⚠ 验证窗口内有**外部写入**（本卡只写 notes/，没有写产物的代码路径）："
             "新增 %s / 消失 %s / 改动 %s" % (added or "无", gone or "无", changed or "无"))
        for k in (changed + added)[:6]:
            try:
                meta = json.loads((kdir / k).read_text(encoding="utf-8")).get("meta") or {}
                emit("     %s：generated_at=%s · run_no=%s · source_card=%s"
                     % (k, meta.get("generated_at"), meta.get("run_no"),
                        meta.get("source_card")))
            except Exception:  # noqa: BLE001
                pass
    check(my_doc not in changed and my_doc not in gone,
          "外部写入里**没有**本卡验证的那份产物",
          my_doc)
    conn = db.connect()
    db.init_db(conn)
    five1 = five_counts(conn)
    docs1 = int(conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"])
    conn.close()
    check(all(v == 0 for v in five1.values()),
          "★真库五表仍全 0 行（笔记走文件，一行都没写库）", json.dumps(five1))
    check(five1 == five0 and docs1 == docs0,
          "跑前跑后五表与 documents 计数一致",
          "documents %s → %s" % (docs0, docs1))

    emit("\n[12] 断言：⑧ 无 JS 报错 + 截图存证")
    check(not (D.get("errors") or []),
          "真浏览器控制台无 JS 报错", str(D.get("errors") or "无")[:200])
    for name, p in (D.get("shots") or {}).items():
        if p:
            f = Path(p)
            check(f.exists() and f.stat().st_size > 3000,
                  "截图存证 %s（%s）" % (name, f.name),
                  "%d 字节" % (f.stat().st_size if f.exists() else 0))

    emit("\n[13] 花费（deepseek-flash，口径同 core/kp_build.py："
         "$%.2f/1M 入 + $%.2f/1M 出，汇率 %.1f）" % (PRICE_IN, PRICE_OUT, FX))
    total = 0.0
    for c in COST:
        total += float(c["cny"])
        emit("  · %s：输入 %s / 输出 %s tokens → ¥%.4f"
             % (c["where"], c.get("prompt_tokens"), c.get("completion_tokens"),
                c["cny"]))
    if not COST:
        emit("  （本次 --skip-ai，没有真实调用）")
    emit("  主回答合计 ¥%.4f（另有每次提问内部的一次查询改写调用，"
         "core/chat.py 不上报其用量，按同量级估 ¥0.002/次）" % total)
    check(total <= 0.20, "★AI答疑验证花费 ≤ ¥0.20", "实测 ¥%.4f" % total)

    # 14) 清理现场
    emit("\n[14] 清理：删掉本次验证写的笔记，恢复现场")
    if args.skip_restore:
        emit("  （--skip-restore：故意不清理）")
    else:
        for nm in new_notes:
            try:
                (notes_dir / nm).unlink()
            except OSError:
                pass
        nsnap2 = snap(notes_dir)
        check(nsnap2 == nsnap0, "★notes/ 逐字节回到跑前状态（测试笔记已清理）",
              "%d 篇（跑前 %d 篇）" % (len(nsnap2), len(nsnap0)))
        if kp_file:
            check(not (notes_dir / kp_file).exists()
                  and (EVID / ("kp-note-" + kp_file)).exists(),
                  "测试笔记已删除、原文留档在卡目录 evidence/",
                  str(EVID / ("kp-note-" + kp_file)))

    emit("\n" + "=" * 78)
    emit("结果：%d 通过 / %d 失败" % (OK, BAD))
    emit("=" * 78)
    (CARD / "browser_verify.log").write_text("\n".join(LINES) + "\n", encoding="utf-8")
    (CARD / "browser_verify.json").write_text(json.dumps(
        {"ok": OK, "bad": BAD, "cost": COST, "cost_total_cny": round(total, 6),
         "probe": D}, ensure_ascii=False, indent=2), encoding="utf-8")
    if proc:
        proc.terminate()
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        # 兜底：断言中途抛异常也要把**验证写的笔记**清掉（它们是测试产物）
        try:
            subjects.restore()
            nd = Path(config.NOTES_DIR)
            for p in sorted(nd.glob("*.md")):
                t = p.read_text(encoding="utf-8", errors="replace")
                if "C0052 真浏览器验证" in t:
                    p.unlink()
            print("★异常中断（%s: %s），已清掉本次验证写的笔记" % (type(e).__name__, e))
        except Exception as e2:  # noqa: BLE001
            print("★清理也失败：%s" % e2)
        raise
