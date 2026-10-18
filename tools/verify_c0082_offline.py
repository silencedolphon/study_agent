# -*- coding: utf-8 -*-
"""卡 C-0082 离线自检：**笔记只显示人写的内容** + **知识点窗口改笔记**。

不花钱、不用浏览器（真浏览器那一条在 `tools/verify_c0082_browser.py`）。
一条命令复核完整条链路：

  1. `note_body()` 的五种边界（无 fm / 有 fm / 标题≠fm.title / 正文含「## 出处」/
     出处段后有多余内容）；
  2. 老笔记（无 front-matter）**回落整篇原文**——拿真文件走一遍接口；
  3. `GET /api/note` 同时给 `content`（原文，逐字未变）与 `body`（剥离后）；
  4. `POST /api/note/update` 改正文后：front-matter 与「## 出处」段**逐字未变**、
     `updated:` 就地更新、只有正文区变（附 unified diff 证据）、行尾仍是 CRLF；
  5. >4000 字的笔记：`/api/notes/by_kp` 的 `body` 会截断（并标 `body_truncated`），
     而 **`GET /api/note` 的 body 是全文**——按前端流程回写，正文尾部不丢；
     顺带做一次**反面对照**：拿截断的 body 回写会把尾部砍掉（证明这条警告是真的）；
  6. 路径穿越一律拒：`../../x.md`、`.hidden.md`、`a/b.md`、`x.txt`、空名；
  7. `mtime` 冲突返回 **409**（模拟"别处刚改过这篇笔记"）；
  8. 回归：`tools/verify_c0052_quick.py` 仍全过。

**不碰真笔记**：全程只读写本次自建的三篇 `C0082自检-*.md`，跑完删掉并核对
笔记目录回到跑前（跑前先快照文件名 → sha256）。看板实例起在**临时端口**，
绝不碰 8756；跑完杀掉。

用法（cmd 与 pwsh 下都能跑）：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0082_offline.py
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0082_offline.py --port 8793
"""
from __future__ import annotations

import argparse
import atexit
import difflib
import hashlib
import json
import re
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
sys.path.insert(0, str(ROOT / "tools"))
from core import config, subjects, study  # noqa: E402

LOGP = ROOT / "logs" / "_c0082_verify.log"
PREFIX = "C0082自检-"
OK = 0
BAD = 0
FAILS: list[str] = []
_LOG = None
MINE: list[Path] = []

# 控制台编码兜底：脚本在 cmd（GBK）与 pwsh（UTF-8）下都要能跑，
# 中文打不出来时退成替换字符，**不能因为一个编码异常把自检自己搞崩**。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass


def log(line: str = "") -> None:
    """明细一律进日志文件（UTF-8），控制台只留结论。"""
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
        print(txt, flush=True)          # 失败项立刻打 stdout（卡的要求）
    return bool(cond)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_note(path: Path, text: str) -> None:
    """按 notes/ 的既有习惯写：CRLF（`save_note` 在 Windows 上就是这个行尾）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\r\n", "\n").replace("\n", "\r\n"))


def raw_read(p: Path) -> str:
    with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def api(port: int, path: str, payload=None, timeout: int = 25):
    """打一次接口：返回 (status, json)。4xx/5xx 也照常返回状态码，不抛。"""
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"error": body[:200]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def cleanup() -> None:
    for p in MINE:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- 样本
def crlf(s: str) -> str:
    """把一段文本整成 CRLF——notes/ 下的文件就是这个行尾（`save_note` 写的）。"""
    return s.replace("\r\n", "\n").replace("\n", "\r\n")


BODY_A = ("这是人自己写的正文第一行。\n"
          "第二行：价格口径 ¥0.15/1M，字节对自检用。\n"
          "第三行：正文里出现「## 出处」这四个字也不该被误切。")
FM_A = crlf("---\n"
            "title: C0082自检笔记\n"
            "kind: note\n"
            "created: 1700000000\n"
            "updated: 1700000000\n"
            'tags: ["知识点笔记"]\n'
            "origin: knowledge_point\n"
            "kp_id: KP-C0082-SELFTEST\n"
            "doc_id: 7\n"
            "source_label: 自检课件 · 知识点 C0082\n"
            "---\n")
REF_A = crlf("\n## 出处\n\n- 自检教材.pdf › 3.1 › p.12\n- doc#7\n")
TEXT_A = FM_A + crlf("\n# C0082自检笔记\n\n") + crlf(BODY_A) + crlf("\n") + REF_A
TEXT_OLD = crlf("# 旧式自检笔记\n\n没有 front-matter 的老写法，整篇都是人写的。\n")
LONG_TAIL = "★正文尾部标记★"
TEXT_LONG = (crlf(FM_A).replace("C0082自检笔记", "C0082自检超长笔记")
             + crlf("\n# C0082自检超长笔记\n\n")
             + crlf("这是一段用来把正文撑过 4000 字的填充内容。\n" * 300)
             + LONG_TAIL + crlf("\n") + REF_A)


def main() -> int:
    global _LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0,
                    help="默认自动挑一个空闲端口")
    ap.add_argument("--no-autostart", action="store_true",
                    help="用已经起好的实例（不自己起）")
    args = ap.parse_args()

    LOGP.parent.mkdir(parents=True, exist_ok=True)
    _LOG = LOGP.open("w", encoding="utf-8")
    proc = None
    fh = None
    started = time.strftime("%Y-%m-%d %H:%M:%S")

    # 起一个**临时端口**的实例（绝不碰 8756）
    if not args.no_autostart:
        if not args.port:
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            args.port = s.getsockname()[1]
            s.close()
        blog = ROOT / "logs" / "_c0082_offline_auto.out.log"
        fh = blog.open("wb")
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-u",
             str(ROOT / "tools" / "dashboard.py"),
             "--port", str(args.port), "--no-open"],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
        atexit.register(lambda: proc.terminate())
        for _ in range(80):
            time.sleep(0.4)
            if api(args.port, "/api/stats")[0] == 200:
                break
    port = args.port

    print("C-0082 offline verify: START (port=%d, %s)" % (port, started), flush=True)
    log("=" * 78)
    log("卡 C-0082 离线自检 · 端口 %d · 起于 %s" % (port, started))
    log("=" * 78)

    st, _ = api(port, "/api/stats")
    if st != 200:
        print("Verdict=fail", flush=True)
        print("temporary instance on port %d is unreachable (status=%s)" % (port, st),
              flush=True)
        print("log=%s" % LOGP, flush=True)
        return 2

    subjects.restore()
    notes = Path(config.NOTES_DIR)
    notes.mkdir(parents=True, exist_ok=True)
    before = {p.name: sha256(p) for p in sorted(notes.glob("*.md"))}
    log("笔记目录 %s（跑前 %d 篇）" % (notes, len(before)))

    p_a = notes / (PREFIX + "笔记.md")
    p_old = notes / (PREFIX + "老笔记.md")
    p_long = notes / (PREFIX + "超长笔记.md")
    MINE.extend([p_a, p_old, p_long])
    atexit.register(cleanup)
    for p in MINE:
        if p.exists():
            p.unlink()
    write_note(p_a, TEXT_A)
    write_note(p_old, TEXT_OLD)
    write_note(p_long, TEXT_LONG)

    # ---------- [1] note_body 五种边界 ----------
    log("\n[1] note_body 的五种边界")
    body_a = study.note_body(TEXT_A)
    check(body_a == crlf(BODY_A), "有 fm：剥掉 front-matter / 自动标题 /「## 出处」段",
          repr(body_a)[:70])
    check(study.note_body(TEXT_OLD) == TEXT_OLD, "无 fm：整篇原文照旧（逐字）")
    t_notitle = TEXT_A.replace("# C0082自检笔记\r\n", "# 我自己改过的标题\r\n", 1)
    check("我自己改过的标题" in study.note_body(t_notitle)
          and crlf(BODY_A) in study.note_body(t_notitle),
          "标题 ≠ fm.title：标题留着不删，正文照剥")
    t_inner = (FM_A + crlf("\n# C0082自检笔记\n\n人写的正文。\n\n## 出处\n\n"
                           "- 这行是正文里自己写的\n\n后面还有正文。\n"))
    check("后面还有正文。" in study.note_body(t_inner),
          "正文含「## 出处」但后面还接着内容：不切")
    t_tail = (FM_A + crlf("\n# C0082自检笔记\n\n正文。\n\n## 出处\n\n"
                          "- 教材 p.1\n\n多出来的东西\n"))
    check("多出来的东西" in study.note_body(t_tail),
          "出处段后有多余内容：不切")
    check(study.note_body(TEXT_A.replace(REF_A, "\r\n")) == crlf(BODY_A),
          "完全没有出处段：照样剥（不报错）")

    # ---------- [2] 老笔记回落（真文件 + 真接口） ----------
    log("\n[2] 老笔记（无 front-matter）回落整篇原文")
    # 接口读文件走的是 `Path.read_text()`（universal newlines）：`content` 的
    # 语义历来是"原文（行尾规范化成 LF）"，磁盘上是 CRLF。对账按接口的口径来。
    st, d = api(port, "/api/note?name=" + urllib.parse.quote(p_old.name, safe=""))
    check(st == 200 and not d.get("error"), "接口读得到这篇老笔记", st)
    check(d.get("body") == TEXT_OLD.replace("\r\n", "\n"),
          "body 与文件原文**逐字相同**（含末尾换行）", repr(d.get("body"))[:60])
    check(d.get("body") == d.get("content"), "content 与 body 一致（无壳可剥）")

    # ---------- [3] content 是原文、body 是剥离后 ----------
    log("\n[3] GET /api/note：content = 原文全文，body = 剥离后")
    st, d = api(port, "/api/note?name=" + urllib.parse.quote(p_a.name, safe=""))
    check(st == 200 and not d.get("error"), "接口读得到", st)
    check(d.get("content") == p_a.read_text(encoding="utf-8")
          and p_a.read_bytes().count(b"\r\n") > 0,
          "content 与磁盘原文**逐字相同**（行尾按接口口径规范化）",
          "len=%d 磁盘是 CRLF" % len(d.get("content") or ""))
    check(d.get("body") == BODY_A, "body 是剥离后的正文", repr(d.get("body"))[:60])
    got_body = (d.get("body") or "")
    body_lines = got_body.split("\n")
    check(not got_body.startswith("---")
          and "# C0082自检笔记" not in body_lines
          and "## 出处" not in [l.strip() for l in body_lines],
          "body 里没有 front-matter / 自动标题 /「## 出处」段"
          "（正文自己写出这四个字不算）")
    check("也不该被误切" in got_body,
          "正文里自己写的「## 出处」字样被完整保留（没被误切）")
    check((d.get("note") or {}).get("kp_id") == "KP-C0082-SELFTEST",
          "note 元数据照旧带 kp_id（跳回知识点靠它）")

    # ---------- [4] POST /api/note/update：只换正文 ----------
    log("\n[4] 改正文：front-matter 与「## 出处」段逐字未变")
    mtime = p_a.stat().st_mtime
    new_body = "改过之后的正文第一行。\n第二行：¥ 还是那个 ¥。"
    st, r = api(port, "/api/note/update",
                {"file": p_a.name, "body": new_body, "mtime": mtime})
    check(st == 200 and not r.get("error"), "改笔记的接口通了", "%s %s" % (st, r))
    after = raw_read(p_a)
    fm_before = TEXT_A[:TEXT_A.index("\r\n---", 3) + 5]
    fm_after = after[:after.index("\r\n---", 3) + 5]
    norm = lambda s: re.sub(r"updated: \d+", "updated: <新>", s)  # noqa: E731
    check(norm(fm_after) == norm(fm_before),
          "front-matter 逐字未变（只有 updated 的值变了）", repr(fm_after[:80]))
    check("## 出处\r\n\r\n- 自检教材.pdf › 3.1 › p.12\r\n- doc#7\r\n" in after,
          "「## 出处」段逐字未变")
    check(("updated: 1700000000" not in after) and ("updated: " in after),
          "updated: 就地更新成一个新值",
          [l for l in after.split("\r\n") if l.startswith("updated:")])
    for key in ("created: 1700000000", "kp_id: KP-C0082-SELFTEST", "doc_id: 7",
                "origin: knowledge_point", 'tags: ["知识点笔记"]'):
        check(key in after, "`%s` 一字未动" % key)
    check(new_body.replace("\n", "\r\n") in after
          and "这是人自己写的正文第一行。" not in after,
          "正文区确实换成了新内容")
    check("# C0082自检笔记\r\n\r\n" + new_body.replace("\n", "\r\n") in after,
          "标题与正文之间的空行还在（没贴一起）")
    check(after.count("\n") == after.count("\r\n"),
          "整篇行尾仍是 CRLF（没有混进裸 LF）",
          "LF=%d CRLF=%d" % (after.count("\n"), after.count("\r\n")))
    check(b"\xc2\xa5" in p_a.read_bytes(),
          "「¥」的字节仍是 C2 A5（编码没被改写）")
    diff = list(difflib.unified_diff(
        TEXT_A.split("\r\n"), after.split("\r\n"),
        fromfile="改前", tofile="改后", lineterm=""))
    log("  --- 改前/改后 unified diff（只有正文区与 updated 行）---")
    for line in diff:
        log("  " + line)
    changed = [l for l in diff
               if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    # 逐条盯住"不该变的"：fm 的每个键、出处段的每一行，都不许出现在 diff 里。
    # （正文自己写出「## 出处」这四个字是允许的——那正是它该被保留的原因）
    forbidden = ["title:", "kind:", "created:", "tags:", "origin:", "kp_id:",
                 "doc_id:", "source_label:", "- 自检教材.pdf", "- doc#7"]
    hit = [l for l in changed if any(f in l for f in forbidden)]
    hit += [l for l in changed if l[1:].strip() == "## 出处"]
    check(not hit, "diff 里没有 front-matter / 出处段的改动", hit or "%d 行变化" % len(changed))
    check(len(changed) <= 8, "diff 很小：只动了正文与 updated",
          "%d 行" % len(changed))
    check(sum(1 for l in changed if l.startswith("+")) == 3
          and sum(1 for l in changed if l.startswith("-")) == 4,
          "diff 明细：删 4 行加 3 行（旧正文 3 行 + updated，换成新正文 2 行 + updated）",
          "%d+/%d-" % (sum(1 for l in changed if l.startswith("+")),
                       sum(1 for l in changed if l.startswith("-"))))

    # 改完再读一次：body 应当等于刚提交的正文
    st, d2 = api(port, "/api/note?name=" + urllib.parse.quote(p_a.name, safe=""))
    check(d2.get("body") == new_body, "再读回来，body 就是刚提交的正文",
          repr(d2.get("body"))[:60])

    # ---------- [5] >4000 字：编辑必须取全文 ----------
    log("\n[5] 超过 4000 字的笔记：正文尾部不被截断砍掉")
    st, by = api(port, "/api/notes/by_kp?kp_id=KP-C0082-SELFTEST&doc_id=7")
    item = next((n for n in (by.get("notes") or []) if n["file"] == p_long.name), None)
    check(item is not None, "小窗反查列表里有这篇超长笔记")
    if item:
        check(item.get("body_truncated") is True, "小窗的 body 标了截断",
              "len=%d" % len(item.get("body") or ""))
        check(len(item.get("body") or "") == 4000, "小窗的 body 正好截到 4000",
              len(item.get("body") or ""))
    long_raw = raw_read(p_long)
    check(len(long_raw) > 4000, "这篇笔记原文确实超过 4000 字", len(long_raw))
    check(LONG_TAIL in long_raw, "原文里有尾部标记（用来验它还在不在）")

    # 正路：按前端的流程先取全文，再回写
    st, full = api(port, "/api/note?name=" + urllib.parse.quote(p_long.name, safe=""))
    full_body = full.get("body") or ""
    check(len(full_body) > 4000, "GET /api/note 的 body 是**全文**（不截断）",
          len(full_body))
    check(LONG_TAIL in full_body, "全文里带着尾部标记")
    keep = full_body + "\n新增的一行：编辑不该砍掉我后面的正文。"
    st, r = api(port, "/api/note/update",
                {"file": p_long.name, "body": keep,
                 "mtime": p_long.stat().st_mtime})
    check(st == 200 and not r.get("error"), "用全文回写成功", "%s" % st)
    check(LONG_TAIL in raw_read(p_long),
          "★尾部标记仍在（正文尾部没被 4000 砍掉）")
    check("新增的一行：编辑不该砍掉我后面的正文。" in raw_read(p_long),
          "新增的那一行也落盘了")

    # 反面对照：拿**小窗里截断过的 body** 回写 → 尾部必然丢（证明警告是真的）
    trunc = (item or {}).get("body") or ""
    st, r = api(port, "/api/note/update",
                {"file": p_long.name, "body": trunc,
                 "mtime": p_long.stat().st_mtime})
    check(st == 200, "（反面对照）截断 body 也能写进去", st)
    check(LONG_TAIL not in raw_read(p_long),
          "（反面对照）尾部标记被砍掉了 → 所以前端必须先取全文", "预期如此")

    # ---------- [6] 路径穿越一律拒 ----------
    log("\n[6] 路径穿越 / 非法文件名一律拒")
    outside = notes.parent / "C0082穿越.md"
    hidden = notes / ".C0082隐藏.md"
    for name in ["../../C0082穿越.md", "..\\..\\C0082穿越.md", ".C0082隐藏.md",
                 "sub/C0082子目录.md", "C0082自检-笔记.txt", "", "   ",
                 str(outside), "C0082自检-笔记.md/../../x.md"]:
        st, r = api(port, "/api/note/update",
                    {"file": name, "body": "x", "mtime": 0})
        check(st == 400 and bool(r.get("error")),
              "拒绝 %r" % (name,), "%s %s" % (st, r.get("error")))
    check(not outside.exists() and not hidden.exists(),
          "库外与隐藏文件**都没有被创建出来**")

    # ---------- [7] mtime 冲突 → 409 ----------
    log("\n[7] mtime 冲突返回 409")
    st, r = api(port, "/api/note/update",
                {"file": p_a.name, "body": "并发覆盖测试。",
                 "mtime": p_a.stat().st_mtime - 120})
    check(st == 409, "陈旧 mtime → HTTP 409", "%s %s" % (st, r.get("error")))
    check(bool(r.get("conflict")) is True, "返回体里标了 conflict")
    check(new_body.replace("\n", "\r\n") in raw_read(p_a),
          "被拦下的那次**没有写进文件**")
    # 真的在别处改一下文件（模拟 MCP / 另一个看板实例），旧 mtime 同样要被拦
    with open(p_a, "a", encoding="utf-8", newline="") as f:
        f.write("别处追加的一行。\r\n")
    st, r = api(port, "/api/note/update",
                {"file": p_a.name, "body": "并发覆盖测试。", "mtime": mtime})
    check(st == 409, "文件被别处改过后，旧 mtime → 409", "%s" % st)
    check("别处追加的一行。" in raw_read(p_a), "别处写的内容还在（没被盖掉）")
    st, r = api(port, "/api/note/update",
                {"file": p_a.name, "body": new_body,
                 "mtime": p_a.stat().st_mtime})
    check(st == 200, "拿最新 mtime 重试 → 成功", "%s" % st)
    check("别处追加的一行。" not in raw_read(p_a),
          "这次正文被换掉了（因为人确认过要改）")

    # ---------- [8] 回归 ----------
    log("\n[8] 回归：tools/verify_c0052_quick.py")
    r = subprocess.run([sys.executable, "-X", "utf8",
                        str(ROOT / "tools" / "verify_c0052_quick.py")],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    log(r.stdout or "")
    if r.stderr:
        log("  [stderr] " + (r.stderr or "")[:1000])
    check(r.returncode == 0 and "全部通过" in (r.stdout or ""),
          "C-0052 快速自检仍全过", "exit=%s" % r.returncode)

    # ---------- 清理与对账 ----------
    log("\n[9] 清理：笔记目录回到跑前")
    cleanup()
    after_names = {p.name: sha256(p) for p in sorted(notes.glob("*.md"))}
    check(after_names == before,
          "笔记目录与跑前**逐字节一致**（%d 篇）" % len(before),
          "跑后 %d 篇" % len(after_names))
    check(not any(n.startswith(PREFIX) for n in after_names),
          "本次自建的测试笔记已全部删除")

    if proc:
        proc.terminate()
    if fh:
        fh.close()

    # ---------- 结论（控制台前几行） ----------
    print("Verdict=%s" % ("pass" if not BAD else "fail"), flush=True)
    print("OK=%d BAD=%d" % (OK, BAD), flush=True)
    if FAILS:
        print("failed: %s" % " | ".join(FAILS[:6]), flush=True)
    print("log=%s" % LOGP, flush=True)
    print("notes=%s" % notes, flush=True)
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
