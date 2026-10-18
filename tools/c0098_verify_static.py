# -*- coding: utf-8 -*-
"""C-0098③ 交付自检（离线、绝对路径、不依赖 cwd、不连任何实例）。

跑法（25 秒内出结论）：
    G:\\study_agent\\venv\\Scripts\\python.exe G:\\study_agent\\tools\\c0098_verify_static.py

第一行固定是 `C0098-PASS` 或 `C0098-FAIL`，之后才是明细（输出里**没有连续空格**，
方便验收脚本直接做子串匹配）。检查项分三组：

  A. 本卡两处改动都在（行内 1+⋯ 的两个菜单函数 / 两处行内调用 / 批量入库弹窗内容）；
  B. 反面断言：D10 已删、批量卡片外壳不再回到 return、batchCard/bindBatch 签名未变、
     卡 C-0097② 的骨架（viewDocs / bindUpload / docsBatchOpen / docsMoreMenu / ds-bar）
     一个字没动、`$('#view').innerHTML` 的拼接顺序未变；
  C. 工程约束：整个文件仍是 CRLF（裸 LF = 0）、`<script>` 能过真实 JS 引擎（node --check）。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

DASH = r"G:\study_agent\tools\dashboard.py"
PROBE_TOOL = r"G:\study_agent\tools\c0098_docs_probe.py"
REPORT = (r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
          r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0098\report_C0098.md")

results = []


def chk(name, ok, detail=""):
    results.append((bool(ok), name, str(detail)[:160]))


def main():
    if not os.path.isfile(DASH):
        print("C0098-FAIL")
        print("dashboard.py 不存在：" + DASH)
        return 1
    src = io.open(DASH, "r", encoding="utf-8", newline="").read()
    lines = src.split("\r\n")

    # ---------- A. 本卡改动在不在 ----------
    for must in ['onclick="docsDocRowMenu(this)"', 'onclick="docsFileRowMenu(this)"',
                 "function docsDocRowMenu(el){", "function docsFileRowMenu(el){",
                 "window.docsDocRowMenu = docsDocRowMenu;",
                 "window.docsFileRowMenu = docsFileRowMenu;"]:
        chk("Z2 行内 1+⋯：" + must, must in src)
    for must in ['id="batch-list"', 'id="batch-workers"', 'id="batch-retry"',
                 'id="batch-dismiss"', 'id="batch-selinfo"', 'id="batch-tasks"',
                 'onclick="batchSelectAll()"', 'onclick="batchSelectNone()"',
                 'onclick="batchConfirm()"', 'onclick="batchRetry()"',
                 'onclick="batchDismiss()"', "⚠ 识图要花钱", "25 秒/页"]:
        chk("Z3 弹窗内容：" + must, must in src)

    # 花钱提示必须落在 batchCard 里（触发它的控件同屏，方案 §3.3-S1）
    bc = src.find("function batchCard(files){")
    bb = src.find("function bindBatch(files){")
    body = src[bc:bb] if (0 < bc < bb) else ""
    chk("S1 花钱告知在 batchCard 内", "⚠ 识图要花钱" in body)
    chk("batchCard 返回的是弹窗内容（不再是整张 .card 外壳）",
        'return `<div class="card"' not in body and "return `" in body)

    # ---------- B. 反面断言 / 边界 ----------
    # D10 已删：只允许出现在说明它被删掉的注释行里（本卡自己的施工注释）
    d10 = [i + 1 for i, l in enumerate(lines)
           if "仍是原来的" in l and not l.strip().startswith("//")]
    chk("D10 已删（非注释行找不到「仍是原来的」）", not d10, "命中行 %s" % d10)
    chk("batchCard 签名未变", "function batchCard(files){" in src)
    chk("bindBatch 签名未变", "function bindBatch(files){" in src)
    for must in ["DOCS_PEND = pendingFiles;", 'id="ds-bar"', "onclick=\"docsMoreMenu(this)\"",
                 "function docsBatchOpen(){", "modal(batchCard(DOCS_PEND));",
                 "bindBatch(DOCS_PEND);", "function docsMoreMenu(el){",
                 "function viewDocs(){", "function bindUpload(){"]:
        chk("卡 C-0097② 骨架仍在：" + must, must in src)
    # 拼接顺序：卡片在 $('#view').innerHTML 里的相对次序不许动
    order = ['<div class="row" id="docs-tabs">', "添加资料",
             "raw\\\\ 中的文件 · ", "已入库资料 · ", "${orphanNote}`"]
    pos = [src.find(k, src.find("$('#view').innerHTML")) for k in order]
    chk("$('#view').innerHTML 拼接顺序未变",
        all(p > 0 for p in pos) and pos == sorted(pos), str(pos))

    # 行内按钮数的静态下界：rows 段只应有 2 个 <button>，fileRows 段只应有 3 个
    rs = src.find("const rows = documents.map(d => {")
    re_ = src.find("const fileRows = rawFiles.map(f => {")
    rf = src.find("// 孤儿记录", re_)
    n_rows = src[rs:re_].count("<button")
    n_raw = src[re_:rf].count("<button")
    chk("rows 段 button 标签数 == 2（重建 + ⋯）", n_rows == 2, "实测 %d" % n_rows)
    chk("fileRows 段 button 标签数 == 3（重建/入库分支 + ⋯）", n_raw == 3, "实测 %d" % n_raw)
    # 旧的 5 个行内按钮名不许整片留在行生成段里
    seg = src[rs:rf]
    for gone in [">导出</button>", ">改标题</button>", ">改名</button>", ">移除</button>",
                 ">删文件</button>"]:
        chk("行生成段不再有常驻按钮：" + gone, gone not in seg)

    # ---------- C. 工程约束 ----------
    bare = src.count("\n") - src.count("\r\n")
    chk("CRLF 保持 / 裸 LF == 0", bare == 0, "裸 LF = %d" % bare)

    node = shutil.which("node")
    if node:
        head, tail = src.find("<script>"), src.rfind("</script>")
        fd, path = tempfile.mkstemp(suffix=".js", prefix="c0098_verify_")
        os.close(fd)
        try:
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(src[head + len("<script>"):tail])
            r = subprocess.run([node, "--check", path], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            chk("<script> 过 node --check（真实 JS 引擎）", r.returncode == 0,
                ((r.stdout or "") + (r.stderr or "")).strip()[:120])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    else:
        chk("<script> 过 node --check（真实 JS 引擎）", False, "找不到 node")

    chk("专项探针脚本在 tools 下", os.path.isfile(PROBE_TOOL))
    chk("report_C0098.md 在卡目录", os.path.isfile(REPORT))

    # ---------- 输出：结论行放最前 ----------
    ok = all(r[0] for r in results)
    bad = [r for r in results if not r[0]]
    out = ["C0098-PASS" if ok else "C0098-FAIL",
           "C-0098③ 静态自检：%d 项，通过 %d，失败 %d" % (len(results), len(results) - len(bad), len(bad)),
           "文件：" + DASH]
    for good, name, detail in results:
        if not good:
            out.append("FAIL " + name + ((" | " + detail) if detail else ""))
    if ok:
        for good, name, detail in results:
            out.append("ok " + name + ((" | " + detail) if detail else ""))
    text = "\n".join(out)
    assert "  " not in text, "输出里出现了连续空格"
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
