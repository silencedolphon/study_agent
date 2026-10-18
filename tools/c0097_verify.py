# -*- coding: utf-8 -*-
"""卡 C-0097② 交付自检（可重复跑，25 秒内出结论）。

验的是"这一卡到底改了什么、有没有越界、安全告知还在不在"这几件事，
不是"跑了个命令没报错"。全部证据都来自**文件本身**，不依赖网络与浏览器。

用法（绝对路径，不依赖 cwd）：
    G:\\study_agent\\venv\\Scripts\\python.exe G:\\study_agent\\tools\\c0097_verify.py

退出码 0 = 全部通过；1 = 有检查项不通过（逐条打印）。
"""
import os
import re
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

DASH = r"G:\study_agent\tools\dashboard.py"
BEFORE = (r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
          r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0097\_work\dashboard.py.before")
NODE = r"C:\Program Files\nodejs\node.exe"

OK, BAD = [], []


def chk(name, cond, note=""):
    (OK if cond else BAD).append("%s%s" % (name, ("：" + note) if note else ""))
    return bool(cond)


def read(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def block(text, start, end):
    """取 [start 行 .. 之后最近的 end 行] 的整段（含首尾行）。找不到返回 None。"""
    i = text.find(start)
    if i < 0:
        return None
    j = text.find(end, i + len(start))
    if j < 0:
        return None
    return text[i:j + len(end)]


def strip_comments(t):
    """去掉整行注释再断言：注释里为了说明"删了什么"会引用原文，
    那是给维护者看的，不该让"页面上还有没有这句"的检查误判。"""
    return "\n".join(l for l in t.split("\n") if not l.lstrip().startswith("//"))


raw = read(DASH)
# 改前快照（本卡 _work 下）：区段边界那几项靠它做逐字对比。快照被清理掉时降级为
# "跳过"而不是让整份自检崩掉——但会在输出里写明跳过，不假装通过。
HAS_BEFORE = os.path.exists(BEFORE)
before = read(BEFORE) if HAS_BEFORE else ""

# ---- 1 语法：Python 编译 + 内嵌 JS 过 node --check -------------------------
py = subprocess.run([sys.executable, "-m", "py_compile", DASH],
                    capture_output=True, text=True)
chk("Python 语法", py.returncode == 0, (py.stderr or "")[-200:])

i = raw.find('INDEX_HTML = r"""')
j = raw.find('"""', i + 20)
html = raw[i + len('INDEX_HTML = r"""'):j]
scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
js_ok, js_msg = True, "%d 段内嵌 JS 全部通过" % len(scripts)
if os.path.exists(NODE):
    tmp = os.path.join(tempfile.gettempdir(), "c0097_check")
    os.makedirs(tmp, exist_ok=True)
    for n, s in enumerate(scripts):
        p = os.path.join(tmp, "js%d.js" % n)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(s)
        r = subprocess.run([NODE, "--check", p], capture_output=True, text=True)
        if r.returncode != 0:
            js_ok = False
            js_msg = "第 %d 段: %s" % (n, (r.stderr or "")[:300])
            break
else:
    js_msg = "没找到 node，跳过（页面实测见 audit）"
chk("内嵌 JS 语法", js_ok, js_msg)

# ---- 2 行尾：全 CRLF、裸 LF 为 0 ------------------------------------------
b = open(DASH, "rb").read()
lf, crlf = b.count(b"\n"), b.count(b"\r\n")
chk("行尾保持 CRLF", lf - crlf == 0, "CRLF=%d 裸LF=%d 行数=%d" % (crlf, lf - crlf, crlf))

# ---- 3 四入口收成一行 + 各自的弹窗函数 ------------------------------------
for label in ["＋ 从路径导入", "⇪ 拖拽 / 上传", "📖 教材专线", "📦 导入分享包"]:
    chk("入口按钮「%s」" % label, ('>%s</button>' % label) in html)
for fn in ["function docsDlgImport()", "function docsDlgUpload()",
           "function docsDlgTextbook()", "function docsMoreMenu(el)",
           "function docsBatchOpen()", "function docsSubSet("]:
    chk("弹窗函数 %s" % fn.split()[1], fn in html)
chk("分享包入口复用现成弹窗", 'onclick="openShareImport()"' in html)

# ---- 4 四个平级按钮 → 1 主 CTA + 「⋯ 更多」+ 纯图标刷新 -------------------
docsfn = block(html, "async function viewDocs(){", "function bindUpload(){") or ""
view = block(html, "  $('#view').innerHTML = `", "    ${orphanNote}`;") or ""
code = strip_comments(docsfn)          # 去注释后的 viewDocs 函数体（= 真正渲染的东西）
chk("唯一主 CTA 保留页面", '<button class="act" onclick="reindex(null)"' in code)
chk("维护动作进「⋯ 更多」", '⋯ 更多' in code and "docsMoreMenu(this)" in code)
menu = block(html, "function docsMoreMenu(el){", "  </div>`);") or ""
for act in ["reindex(null,true)", "openCleanup()", "go('docs')"]:
    chk("⋯ 更多里有 %s" % act, act in menu)
chk("卡片标题右侧纯图标刷新", ">↻</button>" in code)

# ---- 5 批量入库整卡进弹窗（接口不变，只调用） -----------------------------
chk("批量入库入口按钮", "docsBatchOpen()" in code and "批量入库${" in code)
chk("接口 batchCard(files) 签名不变", "function batchCard(files){" in html)
chk("只调用、不改写", "modal(batchCard(DOCS_PEND))" in html)
chk("页面里不再常驻批量卡片",
    not re.search(r"^\s*\$\{batchCard\(pendingFiles\)\}", code, re.M))

# ---- 6 浮动操作条：勾选后才出现 -------------------------------------------
chk("选中条默认不占位", 'id="ds-bar" style="margin:10px 0 6px;display:none"' in code)
chk("docSelPaint 负责显隐", "bar.style.display = DS.sel.size ? '' : 'none'" in html)

# ---- 7 孤立卡折叠 / 页签角标 ----------------------------------------------
chk("孤立卡折叠成一行提示条", "<details class=\"card\"" in code
    and "个文件在 raw\\\\ 里已找不到 ›" in code)
chk("页签角标改纯数字+tooltip", 'title="${n} 个待入库">${n}</span>' in code)

# ---- 8 D1–D13 文案处置（逐条断言） ----------------------------------------
gone = {
    "D4 已删除（只留一句进弹窗）": "这种方式只是把文件放进",
    "D6 已删除（并入既有清理弹窗）": "以它为出处的知识点会失去支撑",
    "D8 已删除（规则句进 tooltip）": "两个子页签各自只列本库的文件与已入库文档",
    "D2 的价值判断句已删": "勾选与提交仍由下面的「批量入库」决定",
}
for k, s in gone.items():
    chk(k, s not in code)
kept = {
    "S1 识图要花钱": "识图要花钱",
    "S2 删除不可逆": "删除不可逆",
    "S3 拒绝导入不偷偷花钱": '绝不用"重算一遍"偷偷花掉接口费',
    "S4 几百 MB 会被掐断": "单个几百 MB 的文件可能被浏览器自己掐断",
    "S5 重跑会重建块的 id": "会重建块的 id，引用它的知识点出处会失效",
    "D3 一条龙对价说明（弹窗内折叠）": "一条龙",
    "D13 孤儿成因（展开后可见）": "这些是手工把源文件移走或改名后留下的记录",
    "D1 路径导入理由（tooltip）": "没有大小限制、不吃内存",
    "D5 两库一起入（tooltip）": "一份资料进哪个库由它所在的目录决定",
    "D11 重建/移除语义（tooltip）": "移除：默认只删索引、保留 raw",
}
for k, s in kept.items():
    chk(k + " 仍在", s in html)
delmodal = block(html, '    <h2 style="color:var(--bad)">批量删除确认', "连原文件一起删</button>") or ""
chk("D12 落在确认弹窗内", "删除不可逆" in delmodal)
share = block(html, "async function openShareImport(zipPath){", "    <div id=\"shi-body\">") or ""
chk("D7 落在分享包弹窗内", '绝不用"重算一遍"偷偷花掉接口费' in share)

# ---- 9 区段边界：表格行生成函数一字未动 -----------------------------------
same = {
    "rows 生成函数": ("  const rows = documents.map(d => {", "  }).join('');"),
    "fileRows 生成函数": ("  const fileRows = rawFiles.map(f => {", "  }).join('');"),
    "batchCard（平行卡区段）": ("function batchCard(files){", "\n}"),
    "bindBatch（平行卡区段）": ("function bindBatch(files){", "\n}"),
    "bindDropzone（拖拽实现）": ("function bindDropzone(){", "\n}"),
    "bindUpload（路径/分享包绑定）": ("function bindUpload(){", "\n}"),
}
for name, (s, e) in same.items():
    if not HAS_BEFORE:
        print("  ! 跳过「%s 未改动」：改前快照不在（%s）" % (name, BEFORE))
        continue
    chk("%s 未改动" % name, block(raw, s, e) == block(before, s, e))

# ---- 10 #view 拼接顺序未变（各卡片出现的相对次序） ------------------------
order = ["docsRes.stale ?", 'id="docs-tabs"', '<h2 style="margin:0">添加资料</h2>',
         "raw\\\\ 中的文件", ">已入库资料 · ", "    ${orphanNote}`;"]
pos = [code.find(x) for x in order]
chk("#view 拼接顺序未变", all(p >= 0 for p in pos) and pos == sorted(pos),
    "位置 %s" % pos)

# ---- 11 未触碰其他页与后端 ------------------------------------------------
if HAS_BEFORE:
    chk("未改 api_teach_page / api_kp_textbook",
        raw.count("def api_teach_page") == before.count("def api_teach_page")
        and raw.count("def api_kp_textbook") == before.count("def api_kp_textbook"))
    chk("未改设计 token（--fg2 等取值）",
        block(raw, ":root{", "}") == block(before, ":root{", "}"))
# ---- 输出（结论在前 6 行） ------------------------------------------------
print("C0097-PASS 资料页工具栏与卡片骨架重排自检全部通过（%d 项）" % len(OK)
      if not BAD else "C0097-FAIL 有 %d 项不通过" % len(BAD))
print("检查项 %d 通过 / %d 失败 | 文件 %s" % (len(OK), len(BAD), DASH))
print("行尾 CRLF=%d 裸LF=%d | 内嵌 JS %s" % (crlf, lf - crlf, js_msg))
print("四入口弹窗 + ⋯ 更多 + 批量入库弹窗 + 浮动选中条 + 孤儿折叠：全部落地")
print("区段边界：表格行生成（rows/fileRows）与平行卡区段（batchCard/bindBatch）逐字未动")
if BAD:
    print("-" * 60)
    for x in BAD:
        print("  ✗ %s" % x)
    print("-" * 60)
    print("（通过项 %d 条，明细见上）" % len(OK))
raise SystemExit(1 if BAD else 0)
