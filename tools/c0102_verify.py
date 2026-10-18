# -*- coding: utf-8 -*-
"""卡 C-0102 验收脚本：知识点窗口的「教材补充」框（静态 + 函数级真跑）。

**它不做浏览器探针**（那要起临时实例，超过 30 秒预算，见同目录 `c0102_probe_ui.py`）。
这里验的是"交付物本身长什么样"，全部在本机静态完成，25 秒内跑完：

  1. `tools/dashboard.py` 裸 LF = 0（CRLF 保持），且 Python 侧能编译；
  2. 新函数在：`mmTbHtml` / `mmTbOpenPage` / `mmTbPlace` / `mmLoadTextbook`
     （验收卡面要求"文件类断言：新函数名"）；
  3. `mmRenderKp` 里真的有「要点」框（`#mmkp-ptsbox`）与「教材补充」框
     （`#mmkp-tbbox`），且在同一 `.kpcols` 里；**异步调用**在 `evPopMount` 之后；
  4. 既有内容一个不少：`#mmkp-deck` / `#mmkp-prev` / `#mmkp-next` / `#mmkp-search` /
     `#mmkp-note` / `#mmkp-notes` / `#mmkp-more` / `#mmkp-slide`；
  5. CSS：`.kpcols` / `.kpcol` / `@media (max-width:700px)` 堆叠 /
     `.evpop:has(#mmkp-body)` 宽度，且 `<style>` 大括号配平；
  6. ★ **把 `mmTbHtml` 抠出来交给 node 真跑五个 status**（ok / no_textbook /
     no_match / no_kp / error）——断言每个分支都出中文文案、ok 分支带
     `data-tbpage` 与教材标题、其余分支带 `disabled` 按钮；
  7. 整段 `<script>` 交 `node --check` 做语法校验（模板字符串里一个反引号写错
     就是白屏，静态就能挡住）；
  8. 越界检查：与改前快照 `dashboard.py.c0102-bak` 逐函数比对，
     `viewDocs` / `bindUpload` / `rows` / `fileRows` / `batchCard` / `openKpsHelp` /
     `openKpsPreview`（卡②③④与预习面板）**一个字符都没动**。

用法（**绝对路径，不依赖 cwd**）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0102_verify.py
输出**前 6 行内**固定出现 `C0102-PASS` 或 `C0102-FAIL`；退出码 0 = 全部断言通过。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"G:\study_agent")
DASH = ROOT / "tools" / "dashboard.py"
BAK = ROOT / "tools" / "dashboard.py.c0102-bak"

FAILS: list = []
OUT: list = []


def check(ok, label, detail=""):
    if not ok:
        FAILS.append(label + ((" | " + str(detail)) if detail else ""))
    OUT.append(("  ok  " if ok else "  NG  ") + label
               + ((" | " + str(detail)[:200]) if detail else ""))
    return bool(ok)


def grab_function(js: str, name: str) -> str:
    """按函数名抠出源码（从 `function name(` / `async function name(` 到配对的大括号）。"""
    m = re.search(r"(?:^|\n)((?:async\s+)?function\s+%s\s*\()" % re.escape(name), js)
    if not m:
        return ""
    start = m.start(1)
    i = js.find("{", m.end(1) - 1)
    if i < 0:
        return ""
    depth = 0
    j = i
    while j < len(js):
        c = js[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return js[start:j + 1]
        j += 1
    return ""


TB_RUNNER = r"""
// 只抠 mmTbHtml：纯函数，唯一依赖是 esc。五个 status 全跑一遍，把 HTML 交回去断言。
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
__MMTBHTML__
const cases = [
  {status:'ok', message:'教材「药理学 第6版 刘晓东」里命中 2 段（p.95 起），按词项与章名自动匹配，仅供参考，请以原文为准。',
   textbook:{doc_id:91, title:'药理学 第6版 刘晓东', n_chunks:882, pages:452,
             page_url:'/api/teach-page?doc_id=91&page={page}',
             passages:[{chunk_id:11, page:95, page_last:99, page_label:'p.95–p.99',
                        page_exact:false, chapter:'第3章', heading_path:'药理学 › 第3章',
                        score:101.6, why:['命中「DNA」','章名匹配：第3章'],
                        text:'核苷酸是核酸的基本组成单位。', text_truncated:true}],
             terms:[{term:'DNA', df:3, weight:9.1}], truncated:true,
             note:'起始页只是大致位置。'}},
  {status:'no_textbook', message:'当前学科库里没有教材类资料（role=textbook 的 PDF），教材补充框暂时没有内容可挂。把教材 PDF 放进 raw 并入库即可。', textbook:{}},
  {status:'no_match', message:'教材里没有出现这条知识点的任何关键词（可能这份教材与它不同源）。', textbook:{title:'药理学 第6版 刘晓东', pages:452}},
  {status:'no_kp', message:'文档 103 里没有末级知识点「KP-X」。', textbook:{}},
  {status:'error', message:'教材补充读取失败：boom', textbook:{}}
];
console.log(JSON.stringify(cases.map(c => mmTbHtml(c))));
"""


def main() -> int:
    t0 = time.time()
    raw = io.open(str(DASH), "r", encoding="utf-8", newline="").read()
    js = ""
    m = re.search(r"<script>([\s\S]*?)</script>", raw)
    if m:
        js = m.group(1)

    # ---------- 1 行尾 / 编译 ----------
    bare = raw.count("\n") - raw.count("\r\n")
    check(bare == 0, "dashboard.py 裸 LF = 0（CRLF 保持）", "裸 LF=%d 行数=%d" % (bare, raw.count("\r\n")))
    rc = subprocess.run([sys.executable, "-X", "utf8", "-m", "py_compile", str(DASH)],
                        capture_output=True, text=True, encoding="utf-8")
    check(rc.returncode == 0, "dashboard.py Python 侧能编译", (rc.stderr or "")[:200])

    # ---------- 2 新函数 ----------
    fns = {}
    for nm in ("mmTbHtml", "mmTbOpenPage", "mmTbPlace", "mmLoadTextbook", "mmRenderKp"):
        fns[nm] = grab_function(js, nm)
        check(bool(fns[nm]), "新/改函数在：%s()" % nm, "抠到 %d 字符" % len(fns[nm]))
    render = fns["mmRenderKp"]
    tb = fns["mmTbHtml"]
    op = fns["mmTbOpenPage"]

    # ---------- 3 两栏 + 异步 ----------
    check('class="kpcols"' in render and 'id="mmkp-ptsbox"' in render
          and 'id="mmkp-tbbox"' in render,
          "mmRenderKp 里有「要点」框 + 「教材补充」框（.kpcols 两栏）")
    check(render.index('id="mmkp-ptsbox"') < render.index('id="mmkp-tbbox"')
          and render.index('id="mmkp-tbbox"') - render.index('id="mmkp-ptsbox"') < 900,
          "两个框相邻（同一段 HTML 里紧挨着）")
    check("mmkp-tbstatus" in render and "正在查教材" in render,
          "教材补充框有加载占位（不阻塞首屏）")
    check("mmLoadTextbook(d);" in render
          and render.index("evPopMount(el, html);") < render.index("mmLoadTextbook(d);"),
          "mmLoadTextbook(d) 在 evPopMount 之后调用（异步补进来，不挡首屏）")
    check("await mmLoadTextbook" not in render, "mmRenderKp 没有 await 教材请求")

    # ---------- 4 既有内容一个不少 ----------
    keep = ["mmkp-deck", "mmkp-prev", "mmkp-next", "mmkp-search", "mmkp-note",
            "mmkp-notes", "mmkp-more", "mmkp-slide", "mmkp-pages", "mmkp-body"]
    miss = [k for k in keep if k not in render]
    check(not miss, "知识点窗口既有内容一个不少", "缺=%s" % miss)

    # ---------- 5 四个 status 分支 + CSS ----------
    for lit in ("'ok'", "'no_textbook'", "'no_match'", "'no_kp'"):
        check(lit in tb, "mmTbHtml 认 %s 分支" % lit)
    check("error" in tb, "mmTbHtml 有 error 兜底分支")
    check("page_url" in op and "{page}" in op and "window.open" in op,
          "「打开教材页」用后端 page_url 模板 + window.open（不自己拼 URL）")
    check("disabled" in tb and "title=" in tb,
          "无页码时按钮置灰并写明原因")
    check(".kpcols{" in raw and ".kpcol{" in raw and "@media (max-width:700px)" in raw,
          "CSS 有两栏与窄屏堆叠规则")
    check(".evpop:has(#mmkp-body)" in raw, "CSS 用 :has(#mmkp-body) 只给知识点窗口加宽")
    css = ""
    mst = re.search(r"<style>([\s\S]*?)</style>", raw)
    if mst:
        css = mst.group(1)
    check(bool(css) and css.count("{") == css.count("}"),
          "内联 CSS 大括号配平", "{=%d }=%d" % (css.count("{"), css.count("}")))

    # ---------- 6 mmTbHtml 五分支真跑（node） ----------
    node = shutil.which("node")
    if check(bool(node), "找得到 node（函数级真跑要用）", node):
        src = TB_RUNNER.replace("__MMTBHTML__", tb)
        fd, path = tempfile.mkstemp(suffix=".js", prefix="c0102_tbhtml_")
        os.close(fd)
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(src)
            r = subprocess.run([node, path], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=40)
            outs = []
            if r.returncode == 0 and (r.stdout or "").strip():
                outs = json.loads(r.stdout.strip().splitlines()[-1])
            check(r.returncode == 0 and len(outs) == 5,
                  "node 真跑 mmTbHtml 五个 status（0 依赖、纯函数）",
                  "exit=%s err=%s" % (r.returncode, (r.stderr or "")[:160]))
            if len(outs) == 5:
                okh, ntb, nmt, nkp, err = outs
                check('data-tbpage="95"' in okh and "打开教材页 p.95" in okh,
                      "ok 分支：出现带页码的「打开教材页」按钮")
                check("药理学 第6版 刘晓东" in okh and "命中 2 段（p.95 起）" in okh,
                      "ok 分支：教材标题 + 带页码的命中说明")
                check("自动匹配" in okh and "请以教材原文为准" in okh,
                      "ok 分支：标注「自动匹配 / 以原文为准」")
                check("p.95–p.99" in okh and "核苷酸是核酸的基本组成单位。" in okh,
                      "ok 分支：passages 原文段落与页码标签")
                check("命中词项" in okh and "DNA" in okh and "[object Object]" not in okh,
                      "ok 分支：terms 渲染成词（不是 [object Object]）")
                check("disabled" in ntb and "没有教材类资料" in ntb,
                      "no_textbook 分支：明确文案 + 置灰按钮")
                check("disabled" in nmt and "没有出现这条知识点的任何关键词" in nmt,
                      "no_match 分支：明确文案 + 置灰按钮")
                check("disabled" in nkp and "没有末级知识点" in nkp,
                      "no_kp 分支：明确文案 + 置灰按钮")
                check("disabled" in err and "教材补充读取失败" in err,
                      "error 分支：明确文案 + 置灰按钮")
                check(all(("class=\"kptbwarn\"" in h) for h in (ntb, nmt, nkp, err)),
                      "四个非 ok 分支都用醒目的 .kptbwarn 呈现（不是静默空白）")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    # ---------- 7 整段 JS 语法 ----------
    if node and js:
        fd, path = tempfile.mkstemp(suffix=".js", prefix="c0102_script_")
        os.close(fd)
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(js)
            r = subprocess.run([node, "--check", path], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=40)
            check(r.returncode == 0, "整段 <script> 过 node --check",
                  (r.stderr or "")[:200])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    # ---------- 8 越界：卡②③④与预习面板的函数一个字符都没动 ----------
    if check(BAK.exists(), "改前快照在（越界检查要用）", str(BAK)):
        old = io.open(str(BAK), "r", encoding="utf-8", newline="").read()
        m2 = re.search(r"<script>([\s\S]*?)</script>", old)
        ojs = m2.group(1) if m2 else ""
        touched = []
        for nm in ("viewDocs", "bindUpload", "rows", "fileRows", "batchCard",
                   "openKpsHelp", "openKpsPreview", "mmLegend"):
            a, b = grab_function(ojs, nm), grab_function(js, nm)
            if not a:
                continue
            if a != b:
                touched.append(nm)
        check(not touched, "卡②③④与预习面板的函数体未被改动", "被改=%s" % touched)

    dt = time.time() - t0
    # ★ 结论行必须落在输出的**前 6 行**内：所以逐条明细先攒着，最后统一打印。
    if FAILS:
        print("C0102-FAIL 有 %d 条未过（共 %d 条断言，%.1fs）" % (len(FAILS), len(OUT), dt))
    else:
        print("C0102-PASS 全部 %d 条断言通过（%.1fs）" % (len(OUT), dt))
    print("覆盖：行尾/编译 · 新函数 · 两栏布局与异步 · 既有内容 · CSS · "
          "mmTbHtml 五分支 node 真跑 · 整段 JS 语法 · 卡②③④与预习面板未动")
    for f in FAILS[:3]:
        print("  NG %s" % f[:200])
    print("-" * 62)
    print("逐条明细：")
    for line in OUT:
        print(line)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
