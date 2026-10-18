# -*- coding: utf-8 -*-
"""卡 C-0082 ★真浏览器验证（headless Edge + CDP）：笔记只显示人写的内容 + 小窗里改笔记。

离线自检（`tools/verify_c0082_offline.py`）验的是接口与文件；这一条验的是
**人在浏览器里看到/点到的东西**：

  1. 「笔记」页签点开一条笔记：正文区里**没有** front-matter、没有自动生成的
     一级标题、没有「## 出处」段——只有人自己写进去的那几行；
  2. 「知识点」页签 → 点知识点 → 弹窗里「查看笔记」列出它 → 点「编辑正文」
     进编辑态（列表收起、拿到的是**全文**）→ 改成新正文 → 保存成功 →
     列表就地刷成新正文；
  3. 对账：磁盘上那篇笔记的正文真的变了，而 front-matter 与「## 出处」段
     逐字未变（改前改后 sha256 分区比对）。

**不碰真笔记**：只写一篇本次自建的 `C0082浏览器自检-*.md`，跑完删掉并核对
笔记目录回到跑前。实例起在**临时端口**，绝不碰 8756；跑完杀掉。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0082_browser.py
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import re
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
from core import config, subjects  # noqa: E402

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0082")
SHOTS = CARD / "shots"
LOGP = ROOT / "logs" / "_c0082_browser.log"
NEWBODY_FILE = ROOT / "logs" / "_c0082_newbody.txt"

OK = 0
BAD = 0
FAILS: list[str] = []
_LOG = None
MADE: list[Path] = []

# 控制台编码兜底（同 verify_c0082_offline.py）：cmd 下是 GBK、pwsh 下是 UTF-8，
# 中文打不出来时退成替换字符，别让自检自己崩在编码上。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

OLD_BODY = "这是人自己写的旧正文第一行。\n第二行：改之前的样子。"
NEW_BODY = ("C0082 真浏览器改过的正文第一行。\n"
            "第二行：这条是从知识点窗口里改的。\n"
            "第三行：价格口径 ¥0.15/1M。")


def log(line: str = "") -> None:
    if _LOG:
        _LOG.write(line + "\n")
        _LOG.flush()


def check(cond, label: str, detail="") -> bool:
    global OK, BAD
    detail = "" if detail is None else str(detail)
    txt = "  [%s] %s%s" % ("OK" if cond else "★FAIL", label,
                           ("  —— " + detail) if detail else "")
    log(txt)
    if cond:
        OK += 1
    else:
        BAD += 1
        FAILS.append(label)
        print(txt, flush=True)
    return bool(cond)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def raw_read(p: Path) -> str:
    with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def api(port: int, path: str, timeout: int = 25):
    try:
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:200]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def cleanup() -> None:
    for p in MADE:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
    try:
        if NEWBODY_FILE.exists():
            NEWBODY_FILE.unlink()
    except OSError:
        pass


def usable(port: int, doc_id: int):
    """这份课件是不是"当前可用"（v2 产物 + 至少一个末级知识点）？"""
    st, m = api(port, "/api/mindmap?doc_id=%d" % doc_id)
    if st != 200 or m.get("artifact_state") != "v2":
        return None, m
    stack = list(m.get("tree") or [])
    while stack:
        n = stack.pop(0)
        if n.get("lkp_ids"):
            return n["lkp_ids"][0], m
        stack += (n.get("children") or [])
    return None, m


def main() -> int:
    global _LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--doc", type=int, default=0, help="默认自动挑一份可用的课件")
    args = ap.parse_args()

    LOGP.parent.mkdir(parents=True, exist_ok=True)
    _LOG = LOGP.open("w", encoding="utf-8")
    SHOTS.mkdir(parents=True, exist_ok=True)

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    args.port = args.port or s.getsockname()[1]
    s.close()
    blog = ROOT / "logs" / "_c0082_browser_auto.out.log"
    fh = blog.open("wb")
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-u", str(ROOT / "tools" / "dashboard.py"),
         "--port", str(args.port), "--no-open"],
        cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
    atexit.register(lambda: proc.terminate())
    atexit.register(cleanup)
    for _ in range(80):
        time.sleep(0.4)
        if api(args.port, "/api/stats")[0] == 200:
            break

    print("C-0082 browser verify: START (port=%d)" % args.port, flush=True)
    log("=" * 78)
    log("卡 C-0082 ★真浏览器验证（headless Edge + CDP）· 端口 %d" % args.port)
    log("=" * 78)

    st, _ = api(args.port, "/api/stats")
    if st != 200:
        print("Verdict=fail", flush=True)
        print("temporary instance on port %d is unreachable" % args.port, flush=True)
        print("log=%s" % LOGP, flush=True)
        return 2

    # 挑一份当前可用的课件（现场可能有别的会话在重跑整理管线，别写死）
    doc_id, kp_id, mm = args.doc, None, {}
    if doc_id:
        kp_id, mm = usable(args.port, doc_id)
    if not kp_id:
        st, allmm = api(args.port, "/api/mindmap")
        for d in (allmm.get("documents") or []):
            cand = d.get("doc_id")
            if not cand:
                continue
            kp_id, mm = usable(args.port, cand)
            if kp_id:
                doc_id = cand
                break
    log("[0] 前提：doc#%s state=%s 首个末级知识点 %s"
        % (doc_id, (mm or {}).get("artifact_state"), kp_id))
    if not kp_id:
        print("Verdict=fail", flush=True)
        print("no usable doc with a leaf knowledge point was found", flush=True)
        print("log=%s" % LOGP, flush=True)
        return 2

    subjects.restore()
    notes = Path(config.NOTES_DIR)
    notes.mkdir(parents=True, exist_ok=True)
    before = {p.name: sha256(p) for p in sorted(notes.glob("*.md"))}
    log("笔记目录 %s（跑前 %d 篇）" % (notes, len(before)))

    stamp = time.strftime("%m%d-%H%M%S")
    title = "C0082浏览器自检-%s" % stamp
    p_note = notes / ("%s.md" % title)
    MADE.append(p_note)
    fm = ("---\r\n"
          "title: %s\r\n"
          "kind: note\r\n"
          "created: %.0f\r\n"
          "updated: %.0f\r\n"
          'tags: ["知识点笔记"]\r\n'
          "origin: knowledge_point\r\n"
          "kp_id: %s\r\n"
          "doc_id: %d\r\n"
          "source_label: 自检课件 · 知识点 %s\r\n"
          "---\r\n" % (title, time.time(), time.time(), kp_id, doc_id, kp_id))
    ref = "\r\n## 出处\r\n\r\n- 自检出处 p.1\r\n- doc#%d\r\n" % doc_id
    text_before = fm + ("\r\n# %s\r\n\r\n" % title) + OLD_BODY.replace("\n", "\r\n") + ref
    with open(p_note, "w", encoding="utf-8", newline="") as f:
        f.write(text_before)
    NEWBODY_FILE.write_text(NEW_BODY, encoding="utf-8")
    log("  自建笔记 %s（绑定 kp_id=%s doc_id=%s）" % (p_note.name, kp_id, doc_id))

    # ---------- 真跑浏览器 ----------
    jsout = CARD / "browser_probe.json"
    log("\n[1] 真跑浏览器（node tools\\probe_c0082_browser.mjs）")
    r = subprocess.run(
        ["node", str(ROOT / "tools" / "probe_c0082_browser.mjs"),
         "--port", str(args.port), "--doc", str(doc_id), "--note", p_note.name,
         "--newbody-file", str(NEWBODY_FILE),
         "--outdir", str(SHOTS), "--json", str(jsout)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=900)
    log("  node 退出码 %s" % r.returncode)
    if r.stdout:
        log("  stdout: " + r.stdout.strip()[:1500])
    if r.stderr:
        log("  stderr: " + (r.stderr or "").strip()[-1500:])
    if not jsout.exists():
        print("Verdict=fail", flush=True)
        print("browser probe produced no JSON (node exit=%s)" % r.returncode, flush=True)
        print("log=%s" % LOGP, flush=True)
        return 1
    D = json.loads(jsout.read_text(encoding="utf-8"))
    stp = D.get("steps") or {}

    # ---------- [2] 笔记页签：只显示人写的内容 ----------
    log("\n[2] 「笔记」页签：只显示我自己写进去的内容")
    lst = stp.get("list") or {}
    check(lst.get("found") is True, "笔记页签里列出了这条笔记",
          "共 %s 条" % lst.get("total"))
    op = stp.get("opened") or {}
    check(op.get("ok") is True, "点开后正文区渲染出来了", op.get("reason"))
    shown = (op.get("text") or "").strip()
    check(shown == OLD_BODY, "正文区**逐字**就是人写的那两行",
          json.dumps(shown, ensure_ascii=False)[:90])
    check(op.get("hasFmMarker") is not True, "没有 front-matter 的 `---`")
    check(op.get("hasHeading") is not True, "没有自动生成的一级标题 `# …`")
    check(op.get("hasRefHead") is not True, "没有「## 出处」段")
    check("kp_id" not in shown and "source_label" not in shown,
          "正文区里看不到任何 front-matter 键")

    # ---------- [3] 知识点窗口：改笔记 ----------
    log("\n[3] 知识点窗口：查看笔记 → 编辑正文 → 保存")
    pop = stp.get("popup") or {}
    check(pop.get("ok") is True, "知识点弹窗打开了，且有笔记区", pop.get("reason"))
    check(pop.get("hasEditBox") is True and pop.get("hasListBox") is True,
          "「记入笔记」与「查看笔记」两个容器都在")
    ol = stp.get("openList") or {}
    check(ol.get("ok") is True, "「查看笔记」列出了这条知识点的笔记", ol.get("reason"))
    check(ol.get("mineFound") is True, "列表里有本次那条笔记")
    check(ol.get("hasEditBtn") is True, "这条笔记上有「编辑正文」按钮",
          ol.get("editBtnText"))
    check((ol.get("pre") or "").strip() == OLD_BODY,
          "小窗列表里显示的也是**剥离后**的正文（不是原文）",
          json.dumps((ol.get("pre") or "").strip(), ensure_ascii=False)[:90])

    ee = stp.get("enterEdit") or {}
    check(ee.get("ok") is True, "点「编辑正文」进了编辑态", ee.get("reason"))
    check(ee.get("listHidden") is True and ee.get("listEmptied") is True,
          "两个容器互斥：开编辑时列表已收起并清空")
    check((ee.get("value") or "").strip() == OLD_BODY,
          "编辑框里装的是剥离后的正文（不是整篇原文）",
          json.dumps((ee.get("value") or "").strip(), ensure_ascii=False)[:90])
    check(ee.get("saveText") == "保存修改", "保存按钮文案对", ee.get("saveText"))

    sv = stp.get("saved") or {}
    check(sv.get("ok") is True, "改完点保存：列表就地刷成了新正文", sv.get("reason"))
    check(sv.get("editClosed") is True, "保存后编辑态收起")
    check(NEW_BODY.split("\n")[0] in (sv.get("preText") or ""),
          "列表里显示的是刚改的新正文",
          json.dumps((sv.get("preText") or "")[:60], ensure_ascii=False))

    # ---------- [4] 磁盘对账 ----------
    log("\n[4] 磁盘对账：正文真的改了，fm 与「## 出处」段逐字未变")
    after = raw_read(p_note)
    check(NEW_BODY.replace("\n", "\r\n") in after, "磁盘上的正文区=浏览器里提交的新正文")
    check(OLD_BODY.split("\n")[0] not in after, "旧正文已被换掉")
    fm_before = text_before[:text_before.index("\r\n---", 3) + 5]
    fm_after = after[:after.index("\r\n---", 3) + 5]
    norm = lambda x: re.sub(r"updated: \d+", "updated: <新>", x)  # noqa: E731
    check(norm(fm_after) == norm(fm_before),
          "front-matter 逐字未变（只有 updated 值变）")
    check("## 出处\r\n\r\n- 自检出处 p.1\r\n- doc#%d\r\n" % doc_id in after,
          "「## 出处」段逐字未变")
    check("kp_id: %s" % kp_id in after and "doc_id: %d" % doc_id in after,
          "绑定知识点/课件的两个键没丢（「跳回知识点」还能用）")
    check(after.count("\n") == after.count("\r\n"), "整篇行尾仍是 CRLF")

    errs = D.get("errors") or []
    check(len(errs) == 0, "页面没有 JS 报错", errs[:2])

    # ---------- 清理 ----------
    log("\n[5] 清理：笔记目录回到跑前")
    cleanup()
    after_names = {p.name: sha256(p) for p in sorted(notes.glob("*.md"))}
    check(after_names == before, "笔记目录与跑前**逐字节一致**（%d 篇）" % len(before),
          "跑后 %d 篇" % len(after_names))

    proc.terminate()
    fh.close()

    print("Verdict=%s" % ("pass" if not BAD else "fail"), flush=True)
    print("OK=%d BAD=%d" % (OK, BAD), flush=True)
    if FAILS:
        print("failed: %s" % " | ".join(FAILS[:6]), flush=True)
    print("log=%s" % LOGP, flush=True)
    print("shots=%s" % SHOTS, flush=True)
    log("\n结论：OK=%d BAD=%d %s" % (OK, BAD, "全部通过" if not BAD else "有失败"))
    _LOG.close()
    return 0 if not BAD else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print("Verdict=fail", flush=True)
        print("crash: %s: %s" % (type(e).__name__, e), flush=True)
        import traceback
        if _LOG:
            traceback.print_exc(file=_LOG)
        raise SystemExit(3)
