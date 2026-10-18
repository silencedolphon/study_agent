# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-26-c0074vf   (project study_agent; keep this line)
"""卡 C-0074 验收：对外用户文档《使用说明.md》的必查项。

**为什么要有这个脚本**：这份文档是要随 exe 发给外部用户的，它一旦漏了
「已知限制」或混进了本机真实数据，是靠人眼很难兜住的。所以把三件事做成
可执行断言：

  ① **章节齐全**：这是什么 / 怎么开始 / 支持格式 / 已知限制 / 数据与备份卸载
     / 常见问题；
  ② **四条限制如实列出**：Office 依赖、联网且自费、首启动慢、仅 Windows；
  ③ **不含真实数据与密钥**：
     · 形状类 —— `sk-` 密钥、本机 `C:\\Users\\<用户名>` 路径、内部运维痕迹；
     · 真实数据类 —— 拿**本机库里真实的学科名与课件标题**去比对（动态取，
       不把任何真实名字硬编码进本脚本，否则脚本自己就成了泄漏源）。

用法（cmd / pwsh 通吃）：
    <python> tools\\verify_c0074_docs.py [--doc <路径>]
退出码 0 = 全部通过；1 = 有项不达标（明细逐条打印）。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _setup_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                   # noqa: BLE001
            pass


_setup_output()

# ---- ① 章节：标题里必须出现的关键词 -------------------------------------
REQUIRED_SECTIONS = [
    ("这是什么", ("这是什么",)),
    ("怎么开始用（双击 exe → 填密钥 → 入库）",
     ("第一次使用", "三步上手")),
    ("支持的文件格式", ("支持的文件格式",)),
    ("已知限制", ("已知限制",)),
    ("数据位置 / 备份 / 卸载", ("备份", "卸载")),
    ("常见问题", ("常见问题",)),
]

# ---- ② 四条已知限制：各自的判别关键词 ------------------------------------
REQUIRED_LIMITS = [
    ("① Office 依赖（pptx 页图渲染）", ("Office", "PowerPoint"),
     r"(pptx|PPT).{0,80}(需要|只能).{0,40}(Office|PowerPoint)|"
     r"(Office|PowerPoint).{0,60}(pptx|整页)"),
    ("② AI 需联网且费用自付", ("联网", "自付"),
     r"联网[\s\S]{0,200}(计费|费用|付费|自付)|"
     r"(费用|计费).{0,40}(自付|自付|自己承担|自行承担)"),
    ("③ 首次启动可能较慢", ("首次启动", "慢"),
     r"首次启动[\s\S]{0,120}(慢|等待|解包)"),
    ("④ 仅 Windows", ("Windows",),
     r"仅支持[\s\S]{0,20}Windows|Windows 10\s*/\s*11|"
     r"只提供\s*\*{0,2}Windows"),
]

# ---- ③ 泄漏：形状类 ------------------------------------------------------
SHAPE_RULES = [
    ("`sk-` 形状的真实密钥",
     re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("本机用户目录绝对路径（含用户名）",
     re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'`|)]+")),
    ("内部运维痕迹（卡目录 / 大管家会话）",
     re.compile(r"steward-lite|\.dsh[\\/]+consoles|cards[\\/]+C-\d+")),
    ("赋值形式的密钥",
     re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{24,}")),
]

# 真实数据类：这些是**占位/通用词**，命中了也不算违规（否则必然误报）。
_NEUTRAL = {
    "raw", "notes", "index", "logs", "subjects", "core", "tools", "docs",
    "README", "StudyDashboard", "study", "agent", "test", "tmp", "backup",
}


def real_names() -> list[str]:
    """从**本机真实库**取"绝不该出现在对外文档里"的名字。

    取不到（干净环境 / 没建库）就返回空表，只跑形状类规则。
    """
    names: list[str] = []

    subdir = ROOT / "subjects"
    if subdir.is_dir():
        for p in subdir.iterdir():
            if p.is_dir() and p.name not in _NEUTRAL:
                names.append(p.name)

    db = ROOT / "index" / "study.db"
    if db.is_file():
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
            for row in con.execute("SELECT path FROM documents LIMIT 500"):
                raw = str(row[0] or "")
                if not raw:
                    continue
                stem = Path(raw.replace("\\", "/")).stem
                if len(stem) >= 4 and stem not in _NEUTRAL:
                    names.append(stem)
            con.close()
        except Exception:                                   # noqa: BLE001
            pass

    out, seen = [], set()
    for n in names:
        n = n.strip()
        if len(n) >= 3 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", default=str(ROOT / "使用说明.md"))
    args = ap.parse_args()

    doc = Path(args.doc)
    fails: list[str] = []
    notes: list[str] = []

    # 0) 存在且非空
    if not doc.is_file():
        print("结论：FAIL —— 找不到文档 %s" % doc)
        print("0/1 项通过")
        return 1
    text = doc.read_text(encoding="utf-8", errors="replace")
    if len(text.strip()) < 500:
        print("结论：FAIL —— 文档过短（%d 字符），像是占位文件" % len(text))
        print("0/1 项通过")
        return 1

    # 1) 章节
    for label, keys in REQUIRED_SECTIONS:
        if not any(k in text for k in keys):
            fails.append("缺章节：%s（关键词 %s）" % (label, "/".join(keys)))

    # 2) 四条限制
    for label, keys, pat in REQUIRED_LIMITS:
        if not (all(k in text for k in keys) and re.search(pat, text)):
            fails.append("已知限制未如实写明：%s" % label)

    # 3) 泄漏
    for label, rx in SHAPE_RULES:
        m = rx.search(text)
        if m:
            fails.append("疑似泄漏（%s）：%s" % (label, m.group(0)[:60]))

    names = real_names()
    hit = sorted({n for n in names if n in text})
    if hit:
        # ⚠ 只报数量与长度，**不回显命中的名字**——那本身就是泄漏。
        fails.append("文档里出现了本机真实数据名 %d 处（最长 %d 字符），必须清掉"
                     % (len(hit), max(len(h) for h in hit)))
    notes.append("真实数据比对样本 %d 个（学科名 + 课件标题）" % len(names))

    total = len(REQUIRED_SECTIONS) + len(REQUIRED_LIMITS) + len(SHAPE_RULES) + 1
    ok = total - len(fails)

    # 结论行走在前 6 行
    print("结论：%s —— %d/%d 项通过" % ("PASS" if not fails else "FAIL", ok, total))
    print("文档：%s（%d 字符）" % (doc, len(text)))
    print("必查项：章节 %d · 已知限制 %d · 泄漏规则 %d · 真实数据比对 1"
          % (len(REQUIRED_SECTIONS), len(REQUIRED_LIMITS), len(SHAPE_RULES)))
    print("真实数据比对：%s" % "；".join(notes))
    if fails:
        print("不达标明细（%d 条）：" % len(fails))
        for f in fails:
            print("  · %s" % f)
    else:
        print("明细：章节齐全；四条限制逐条在文；无密钥形状 / 本机路径 / 内部痕迹 / 真实数据名。")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
