# -*- coding: utf-8 -*-
"""C-0099（界面改造·卡④：知识点页与知识点窗口清理）的交付自检。

两段证据：
  A. **源码静态断言**（`tools/dashboard.py`）——新函数在不在、卡号/CLI 有没有从
     "会渲染到界面"的行里清掉、Z5 的 `<details>` / prev-next 结构有没有被碰、
     图例与「录入遗忘曲线」在不在、CRLF 有没有破坏。
     静态扫描**只扫会渲染的文本**：整行/行尾 `//` 注释、`/* */` 块注释、`<!-- -->`
     里的卡号不算（那是给人看的开发注释，不是界面文案）。
  B. **真浏览器证据断言**（`tools/_audit_c0099/kps-after.json`）——由
     `tools/audit_c0099.py` 用真 headless Edge + CDP 抓下来的知识点页数据：
     正文字数（两个口径）、按钮数、页高、图例、知识点窗口底部元素、三个弹窗/菜单。

跑法（绝对路径，不依赖 cwd）：
    G:\\study_agent\\venv\\Scripts\\python.exe G:\\study_agent\\tools\\verify_c0099.py
结论行固定以 `C0099-PASS` / `C0099-FAIL` 开头，且在前 6 行内。
无第三方依赖；只读文件，不联网、不写库、不碰 8756。
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

DASH = r"G:\study_agent\tools\dashboard.py"
AFTER = r"G:\study_agent\tools\_audit_c0099\kps-after.json"
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_audit_c0099")
CARD_NO = re.compile(r"C-0\d\d")


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8"), raw


def render_lines(body):
    """留下"会渲染到界面"的文本：剥掉行尾 `//`、`/* */` 块注释与 `<!-- -->`。"""
    out, in_block, in_html = [], False, False
    for ln in body.split("\n"):
        s = ln
        if in_block:
            if "*/" in s:
                in_block = False
                s = s.split("*/", 1)[1]
            else:
                continue
        if in_html:
            if "-->" in s:
                in_html = False
                s = s.split("-->", 1)[1]
            else:
                continue
        while "<!--" in s:
            pre, _, rest = s.partition("<!--")
            if "-->" in rest:
                s = pre + rest.split("-->", 1)[1]
            else:
                s = pre
                in_html = True
                break
        while "/*" in s:
            pre, _, rest = s.partition("/*")
            if "*/" in rest:
                s = pre + rest.split("*/", 1)[1]
            else:
                s = pre
                in_block = True
                break
        st = s.strip()
        if not st or st.startswith("//"):
            continue
        if "//" in st and "http" not in st:
            st = st.split("//", 1)[0]
        out.append(st)
    return out


def slice_between(src, a, b):
    i = src.index(a)
    j = src.index(b, i)
    return src[i:j]


def code_only(seg):
    """剥掉 Python `#` 注释后剩下的代码（断言"文案里没有"时不能被注释误伤）。"""
    out = []
    for ln in seg.split("\n"):
        st = ln.strip()
        if st.startswith("#"):
            continue
        out.append(ln.split("#", 1)[0] if "#" in ln else ln)
    return "\n".join(out)


def main():
    fails, notes = [], []

    # ---------------- A. 源码静态 ----------------
    src, raw = read_text(DASH)
    crlf = raw.count(b"\r\n")
    bare_lf = raw.count(b"\n") - crlf
    if crlf and bare_lf == 0:
        notes.append("CRLF=%d bareLF=0" % crlf)
    else:
        fails.append("换行符：CRLF=%d 裸LF=%d（要求 CRLF 保持、裸 LF 为 0）" % (crlf, bare_lf))

    # 只扫 `INDEX_HTML` 这个字符串里"会渲染"的行（Python 侧代码/注释不在里面）
    i0 = src.index('INDEX_HTML = r"""') + len('INDEX_HTML = r"""')
    i1 = src.index('"""', i0)
    lines = render_lines(src[i0:i1])
    bad_card = [s for s in lines if CARD_NO.search(s)]
    bad_cli = [s for s in lines if "terms_build" in s or "kp_build.py" in s]
    if not bad_card:
        notes.append("渲染文本无卡号 0 处")
    else:
        fails.append("渲染文本里仍有卡号 %d 处：%s" % (len(bad_card), bad_card[:3]))
    if not bad_cli:
        notes.append("渲染文本无 CLI 0 处")
    else:
        fails.append("渲染文本里仍有命令行 %d 处：%s" % (len(bad_cli), bad_cli[:3]))

    must = {
        "openKpsHelp 帮助弹窗": "function openKpsHelp(",
        "kpBuildNotesHtml 260字折叠": "function kpBuildNotesHtml(",
        "mmMoreMenu 更多菜单": "function mmMoreMenu(",
        "kpBadgePaint 入口角标": "function kpBadgePaint(",
        "mmRenameDeck 改名动作": "function mmRenameDeck(",
        "mmRenameArt 改名动作": "function mmRenameArt(",
        "批量整理弹窗内的?折叠": 'id="kpb-help"',
        "导图图例": 'id="mm-legend"',
        "视图控制 ⊞": 'id="mm-expand" title="展开全部节点">⊞',
        "视图控制 ⊟": 'id="mm-collapse" title="折叠全部节点">⊟',
        "更多入口 ⋯": 'id="mm-more"',
        "录入遗忘曲线仍可见": '<button class="act" id="mm-enroll">录入遗忘曲线</button>',
        "Z5 details 结构": 'details class="kpmore" id="mmkp-more"',
        "Z5 prev 逻辑": "mmPrevKp()",
        "Z5 next 逻辑": "mmNextKp()",
        "Z5 evPop 定位未自造": "evPopMount(el, html);",
        "窗口底部 记入笔记": 'id="mmkp-note"',
        "窗口底部 查看笔记": 'id="mmkp-notes"',
        "窗口底部 展开当页课件": 'id="mmkp-deck"',
        "窗口底部 笔记条数": 'id="mmkp-notecount"',
        "窗口底部 上一知识点": 'id="mmkp-prev"',
        "窗口底部 下一知识点": 'id="mmkp-next"',
    }
    miss = [k for k, v in must.items() if v not in src]
    if not miss:
        notes.append("关键结构 %d 项全在" % len(must))
    else:
        fails.append("缺少关键结构：%s" % miss)

    # 图例四要素必须齐全（◆ / 已有总结 / ◇ / 待生成）
    legend_ok = all(t in src for t in ("◆", "已有总结", "◇", "待生成"))
    if legend_ok:
        notes.append("图例四要素齐")
    else:
        fails.append("导图图例不完整（需要 ◆/已有总结/◇/待生成）")

    # 检索按钮：从"文字按钮"降为图标 + tooltip
    if 'id="mmkp-search"' in src and ">🔍</button>" in src:
        notes.append("检索按钮已图标化")
    else:
        fails.append("知识点窗口的检索按钮没有降为 🔍 图标")
    if ">在资料库中检索此主题<" in src:
        fails.append("源码里还留着「在资料库中检索此主题」的文字按钮")

    # 内部产物路径不该再出现在**常驻页面正文**里（K1 的理由：路径是内部信息）。
    # 弹窗内的"写入…"行与知识点窗口"待生成"屏的排障行属保留项，见报告。
    page_body = "\n".join(render_lines("".join(slice_between(src, a, b) for a, b in (
        ("function kpBuildCard(", "function kpBuildNotesHtml("),
        ("function termsCard(", "function termsBodyHtml("),
        ("function mindmapCard(", "async function mountMindmap("),
        ("async function viewKps(", "// ---------------- 批量整理知识点"),
    ))))
    if "logs/knowledge/" in page_body:
        fails.append("知识点页常驻正文里仍有内部产物路径")
    else:
        notes.append("常驻正文无产物路径")
    # 260 字说明必须**只在**弹窗/帮助里（卡片上不能再有）
    if "整理 = 用模型读" in slice_between(src, "function kpBuildCard(",
                                          "function kpBuildNotesHtml("):
        fails.append("260 字说明还留在批量整理卡片上（K1 未落地）")
    else:
        notes.append("260 字说明已从卡片撤下")
    n_help = src.count('onclick="openKpsHelp()"')
    if n_help >= 2:
        notes.append("帮助入口 %d 处" % n_help)
    else:
        fails.append("「? 本页怎么用」入口少于 2 处（表格卡片 / 批量整理卡片）")
    # K7：命令行不许写给 GUI 用户（后端那句空态话）
    if "terms_build" in code_only(slice_between(src, "def _terms_state", "def _terms_payload")):
        fails.append("名词解释空态里仍写着命令行（K7 未落地）")
    else:
        notes.append("K7 命令行已删")

    # ---------------- B. 真浏览器证据 ----------------
    if not os.path.exists(AFTER):
        fails.append("缺少真浏览器证据文件：%s（先跑 tools/audit_c0099.py）" % AFTER)
        kps = None
    else:
        with open(AFTER, encoding="utf-8") as f:
            ev = json.load(f)
        kps = ev.get("kps") or {}
        before = ev.get("before") or {}
        win = (ev.get("kp_window") or {}).get("window") or {}
        helpd = ev.get("help_modal") or {}
        more = ev.get("more_menu") or {}
        kpb = (ev.get("kpb_modal") or {}).get("kpb") or {}

        ns = kps.get("bodyNoSelectLen")
        bns = before.get("bodyNoSelectLen")
        if isinstance(ns, int) and ns <= 1200:
            notes.append("可见正文 %s→%s 字 ≤1200" % (bns, ns))
        else:
            fails.append("可见正文口径未达标：bodyNoSelectLen=%s（要求 ≤1200）" % ns)
        if isinstance(kps.get("bodyLen"), int) and isinstance(before.get("bodyLen"), int) \
                and kps["bodyLen"] < before["bodyLen"]:
            notes.append("innerText 口径 %s→%s 字" % (before.get("bodyLen"), kps["bodyLen"]))
        if not kps.get("cardNos"):
            notes.append("页面可见文本无卡号")
        else:
            fails.append("页面可见文本仍有卡号：%s" % kps.get("cardNos"))
        if not kps.get("cli"):
            notes.append("页面可见文本无命令行")
        else:
            fails.append("页面可见文本仍有命令行：%s" % kps.get("cli"))
        lg = kps.get("legend") or ""
        if kps.get("legendShown") and "◆" in lg and "已有总结" in lg and "◇" in lg \
                and "待生成" in lg:
            notes.append("图例在页面上可见")
        else:
            fails.append("页面上看不到导图图例：%r" % lg)
        if (kps.get("enroll") or {}).get("t") == "录入遗忘曲线":
            notes.append("录入遗忘曲线仍可见")
        else:
            fails.append("「录入遗忘曲线」不在页面上")
        pf = win.get("pf") or []
        if len(pf) >= 8 and win.get("hasPrev") and win.get("hasNext") and win.get("hasDetails") \
                and win.get("hasNoteBtn") and win.get("hasNotesBtn") and win.get("hasDeckBtn"):
            notes.append("知识点窗口底部元素 %d 个全在" % len(pf))
        else:
            fails.append("知识点窗口底部元素缺失：pf=%d prev=%s next=%s details=%s" % (
                len(pf), win.get("hasPrev"), win.get("hasNext"), win.get("hasDetails")))
        sb = win.get("searchBtn") or {}
        if sb.get("t") == "🔍" and sb.get("title"):
            notes.append("窗口内检索按钮 = 图标 + tooltip")
        else:
            fails.append("窗口内检索按钮形状不对：%r" % sb)
        if helpd.get("open") and helpd.get("sections"):
            notes.append("帮助弹窗可打开（%d 节）" % len(helpd.get("sections")))
        else:
            fails.append("「? 本页怎么用」帮助弹窗打不开：%r" % (helpd.get("error") or helpd))
        if kpb.get("open") and kpb.get("hasQ"):
            notes.append("批量整理弹窗内有 ? 折叠（%s 字）" % kpb.get("qLen"))
        else:
            fails.append("批量整理弹窗内没有 ? 折叠：%r" % kpb)
        items = [x.get("t") for x in (more.get("items") or [])]
        if more.get("entryFound") and len(items) >= 3:
            notes.append("⋯ 更多菜单 %d 项：%s" % (len(items), "、".join(items)))
        else:
            fails.append("⋯ 更多菜单不可用：%r" % more)
        if (ev.get("kpb_modal") or {}).get("kpbEscClosed"):
            notes.append("弹窗 Esc 关闭路径可用")

    # ---------------- 结论 ----------------
    ok = not fails
    head = "C0099-PASS" if ok else "C0099-FAIL"
    stats = ""
    if kps:
        b = (ev.get("before") or {})
        stats = ("KPS 正文(innerText 口径) %s→%s 字 | 可见正文(剔除下拉 option) %s→%s 字 | "
                 "按钮 %s→%s | 页高 %s→%s"
                 % (b.get("bodyLen"), kps.get("bodyLen"), b.get("bodyNoSelectLen"),
                    kps.get("bodyNoSelectLen"), b.get("buttons"), len(kps.get("buttons") or []),
                    b.get("scrollHeight"), kps.get("scrollHeight")))
    print("%s 静态+证据 %d 项通过 / %d 项不通过" % (head, len(notes), len(fails)))
    if stats:
        print(stats)
    print("静态：%s" % "；".join(notes[:6]))
    for f in fails[:6]:
        print("不通过：%s" % f)
    print("证据文件：%s" % AFTER)
    print("（结论标记 %s，退出码 %d）" % (head, 0 if ok else 1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
