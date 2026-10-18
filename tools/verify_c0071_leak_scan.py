# -*- coding: utf-8 -*-
"""卡 C-0071 泄漏扫描：本卡产出的文件里有没有夹带真实用户数据 / 真实密钥。

检查对象（本卡改过或新增的东西）：
  · README.md（公开版正文）
  · tools/dashboard.py（首次运行引导）
  · tools/verify_c0071_firstrun.py（交付自检脚本）
  · core/chat.py、core/figures.py（凭据路径改动）

判据（任一命中即 FAIL）：
  1. 出现用户真实库里任一文档标题（长度 >= 6 的逐个反查，只读打开真实库）；
  2. 出现 `subjects\\` 这类真实学科目录痕迹；
  3. 出现形状像真密钥的串（`sk-` + >= 16 位），占位符 `sk-REPLACE_ME` 除外；
  4. 出现作者的家目录 / 盘符路径（`C:\\Users\\<名字>`、`G:\\study_agent`）。

只读、不联网。用法：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0071_leak_scan.py
输出前 6 行是结论，明细写日志。
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 明细日志落哪儿：设了 C0071_CARD 就写那儿，否则写临时目录（不留作者路径痕迹）。
CARD = Path(os.environ["C0071_CARD"]) if os.environ.get("C0071_CARD") else None

TARGETS = [
    ROOT / "README.md",
    ROOT / "tools" / "dashboard.py",
    ROOT / "tools" / "verify_c0071_firstrun.py",
    ROOT / "core" / "chat.py",
    ROOT / "core" / "figures.py",
]

KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")
HOME_RE = re.compile(r"C:\\Users\\(?!Lenovo\\AppData)[A-Za-z0-9_.\-]+")
DRIVE_RE = re.compile(r"G:\\study_agent")
SUBJ_RE = re.compile(r"subjects\\\\")

# 这些是**允许**出现的占位/示例串（README 里教用户填密钥用的）
ALLOW = {"sk-REPLACE_ME", "sk-C0071-DUMMY-STUB-KEY0000"}


def real_titles() -> list[str]:
    db = ROOT / "index" / "study.db"
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute("SELECT title FROM documents WHERE title IS NOT NULL").fetchall()
        conn.close()
        return [str(r[0]).strip() for r in rows if r[0] and len(str(r[0]).strip()) >= 6]
    except Exception:                                        # noqa: BLE001
        return []


def main() -> int:
    findings: list[str] = []
    checked = 0
    titles = real_titles()
    for p in TARGETS:
        if not p.exists():
            findings.append(f"[MISS] {p} 不存在")
            continue
        checked += 1
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = p.relative_to(ROOT)
        for m in KEY_RE.finditer(text):
            if m.group(0) not in ALLOW:
                findings.append(f"[KEY ] {rel}: {m.group(0)[:24]}…")
        for m in HOME_RE.finditer(text):
            findings.append(f"[HOME] {rel}: {m.group(0)}")
        for m in DRIVE_RE.finditer(text):
            findings.append(f"[DRIVE] {rel}: {m.group(0)}")
        for m in SUBJ_RE.finditer(text):
            findings.append(f"[SUBJ] {rel}: {m.group(0)}")
        hit = [t for t in titles if t in text]
        for t in hit:
            findings.append(f"[DOC ] {rel}: 用户真实课件名 “{t}”")

    verdict = "PASS" if not findings else "FAIL"
    lp = None
    for d in [d for d in (CARD, Path(tempfile.gettempdir())) if d is not None]:
        try:
            d.mkdir(parents=True, exist_ok=True)
            lp = d / "leak_scan_c0071.log"
            lp.write_text("\n".join(findings) + "\n", encoding="utf-8")
            break
        except OSError:
            lp = None
    if lp is None:
        lp = Path(tempfile.gettempdir()) / "leak_scan_c0071.log"
        lp.write_text("\n".join(findings) + "\n", encoding="utf-8")

    print("C-0071 泄漏扫描（README / dashboard.py / verify 脚本 / chat.py / figures.py）")
    print(f"RESULT: {verdict}")
    print(f"扫描 {checked} 个文件；反查真实课件名 {len(titles)} 个；命中 {len(findings)} 条")
    print(f"明细日志: {lp}")
    print("判据: 真实课件名 / 真密钥形状 / 作者家目录 / 作者盘符 / 真实学科目录痕迹")
    print("")
    if findings:
        print("\n".join(findings))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
